import argparse
import importlib
import logging
import os
import re
import sys
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path
from device import (screenshot, scroll_down, scroll_to_top, screen_changed,
                    ensure_resolution, seconds_since_activity, mark_activity, kill_adb_process)
from nav import navigate_to_guild_members
from parser import parse_members, Member
from db import init_db, save_snapshot, validate_names

STALL_SECONDS = 120  # abort if no successful ADB call for this long (true hang, not slow)

for _std in (sys.__stdout__, sys.__stderr__):
    try:
        _std.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

LOG_PATH = Path(__file__).parent.parent / "scraper.log"

_log_fh = open(LOG_PATH, 'w', encoding='utf-8', buffering=1)

class _Tee:
    def __init__(self, *streams): self.streams = streams
    def write(self, data):
        for s in self.streams: s.write(data)
    def flush(self):
        for s in self.streams: s.flush()
    def fileno(self): return self.streams[0].fileno()

sys.stdout = _Tee(sys.__stdout__, _log_fh)
sys.stderr = _Tee(sys.__stderr__, _log_fh)

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)-7s %(message)s',
    datefmt='%H:%M:%S',
    handlers=[logging.StreamHandler(sys.__stdout__), logging.FileHandler(LOG_PATH, mode='a', encoding='utf-8')],
)

# Header reads "Guild Member (88/90)" = current/capacity. We want the CURRENT
# count (group 1), not capacity (group 2). Loose: OCR mangles the parens and
# spaces the slash, so accept "Guild Member 88 / 90", "GuildMember(88/90)", etc.
TOTAL_RE = re.compile(r"Guild\s*Member\D*(\d+)\s*/\s*(\d+)", re.IGNORECASE)

# Import AFTER the logging/Tee setup above so RapidOCR's init logs are captured.
from ocr import ocr_image as _ocr  # noqa: E402


def _get_total_members(ocr_results) -> int:
    for _, text, _ in ocr_results:
        m = TOTAL_RE.search(text)
        if m:
            return int(m.group(1))  # current member count, not capacity
    return 90  # fallback if the header isn't read this frame


def _vote_members(observations: list) -> list:
    """Consolidate raw Member observations by fuzzy name cluster. For each
    cluster, votes on the best power, activeness, warband, and last_active
    by preferring non-null/non-zero values via most-common count."""
    from collections import Counter
    clusters: list[list] = []
    for m in observations:
        for group in clusters:
            if any(SequenceMatcher(None, m.name.lower(), g.name.lower()).ratio() >= 0.88
                   for g in group):
                group.append(m)
                break
        else:
            clusters.append([m])

    result = []
    for group in clusters:
        name = Counter(m.name for m in group).most_common(1)[0][0]

        powers = [m.combat_power for m in group if m.combat_power]
        power = Counter(powers).most_common(1)[0][0] if powers else ''

        activenesses = [m.activeness for m in group if m.activeness > 0]
        activeness = Counter(activenesses).most_common(1)[0][0] if activenesses else 0

        warbands = [m.warband for m in group if m.warband]
        warband = Counter(warbands).most_common(1)[0][0] if warbands else ''

        last_actives = [m.last_active for m in group if m.last_active not in ('Unknown', '')]
        last_active = Counter(last_actives).most_common(1)[0][0] if last_actives else 'Unknown'

        result.append(Member(name=name, last_active=last_active,
                             combat_power=power, activeness=activeness, warband=warband))
    return result


def _collect_pass(scroll_fn, observations: list, total_ref: list,
                  label: str, max_scrolls: int = 150) -> None:
    """Single scroll pass that appends every parsed Member to observations.
    total_ref is a one-element list so the detected total can be written back."""
    scroll_to_top()
    seen_names: set[str] = set()
    no_change_count = 0
    scroll_count = 0
    prev_img = screenshot()

    while scroll_count < max_scrolls:
        img = screenshot()
        results = _ocr(img)

        if not observations:
            detected = _get_total_members(results)
            if detected:
                total_ref[0] = detected
                print(f"Total members: {total_ref[0]}")

        members = parse_members(results)
        observations.extend(members)

        new = sum(1 for m in members
                  if m.name.lower() not in seen_names and not seen_names.add(m.name.lower()))  # type: ignore[func-returns-value]
        print(f"  {label} frame: {new} new names, {len(seen_names)}/{total_ref[0]} seen")

        scroll_fn()
        scroll_count += 1
        time.sleep(1.2)

        curr_img = screenshot()
        if not screen_changed(prev_img, curr_img):
            no_change_count += 1
            if no_change_count >= 2:
                print(f"{label} complete.")
                return
        else:
            no_change_count = 0
        prev_img = curr_img

    if scroll_count >= max_scrolls:
        print(f"{label} hit scroll limit ({max_scrolls}), stopping.")


def _start_stall_watchdog(done: threading.Event) -> None:
    """Abort the scan if no successful ADB call happens for STALL_SECONDS.

    A successful ADB command (device._shell) is the progress heartbeat; a true
    hang produces none, so the heartbeat goes stale. On stall, kill adb to
    unblock anything wedged and hard-exit (no partial data to save mid-scan)."""
    def loop():
        while not done.wait(5):
            if seconds_since_activity() > STALL_SECONDS:
                logging.error("Watchdog: no ADB progress for %ds - aborting.", STALL_SECONDS)
                kill_adb_process()
                os._exit(1)
    threading.Thread(target=loop, daemon=True).start()
    logging.info("Watchdog active: aborts if no ADB progress for %ds.", STALL_SECONDS)


def scrape_guild() -> tuple[list[Member], int]:
    mark_activity()
    _done = threading.Event()
    _start_stall_watchdog(_done)

    try:
        init_db()
        ensure_resolution()
        navigate_to_guild_members()
        print("Starting scrape...")
        total_ref = [_get_total_members(_ocr(screenshot()))]
        print(f"Target: {total_ref[0]} members")

        observations: list[Member] = []
        _collect_pass(scroll_down, observations, total_ref, "Pass 1")

        logging.info("Voting on %d raw observations...", len(observations))
        all_members = _vote_members(observations)
        print(f"Voted: {len(all_members)} members from {len(observations)} observations.")
        for m in all_members:
            print(f"  {m.name} | {m.last_active} | {m.combat_power} | {m.warband} | {m.activeness}")

        all_members, uncertain = validate_names(all_members)
        snapshot_id, actual_count = save_snapshot(all_members, pending_names=set(uncertain))
        print(f"Saved to DB as snapshot #{snapshot_id}.")
        if uncertain:
            print(f"REVIEW_NAMES: {', '.join(uncertain)}")
        return all_members, actual_count
    finally:
        _done.set()


MODES = ("dream_realm", "afk_stages", "arena", "supreme_arena", "honor_duel", "arcane_lab")


def run_modes(enabled: list[str]) -> None:
    """Run each enabled game-mode ranking scan after the guild scan, with its
    own stall watchdog. One mode failing never blocks the rest · failures are
    printed as MODE_FAILED lines the bot can surface."""
    mark_activity()
    done = threading.Event()
    _start_stall_watchdog(done)
    try:
        for mode in enabled:
            print(f"\nMode scan: {mode}")
            try:
                importlib.import_module(f"modes.{mode}").scan()
            except Exception as e:
                print(f"MODE_FAILED: {mode} - {e}")
                logging.exception("Mode scan %s failed", mode)
    finally:
        done.set()


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("--guild", action="store_true", help="Run the guild roster scan")
    for _mode in MODES:
        cli.add_argument(f"--{_mode.replace('_', '-')}", action="store_true")
    args = cli.parse_args()

    enabled = [m for m in MODES if getattr(args, m)]

    if args.guild or not enabled:
        members, actual_count = scrape_guild()
        print(f"\nDone. Captured {actual_count} members.")
        for m in members:
            print(f"  {m.name:<20} {m.last_active:<10} {m.combat_power:<10} {m.warband:<20} {m.activeness}")

    if enabled:
        run_modes(enabled)
