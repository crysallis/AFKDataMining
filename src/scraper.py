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


def _fuzzy_key(name: str, seen_lower: set[str], threshold: float = 0.88) -> str | None:
    key = name.lower()
    if key in seen_lower:
        return key
    for existing in seen_lower:
        if SequenceMatcher(None, key, existing).ratio() >= threshold:
            return existing
    return None


def _process_screen(ocr_results, seen_lower, all_members) -> int:
    new_count = 0
    for m in parse_members(ocr_results):
        key = _fuzzy_key(m.name, seen_lower)
        if key is None:
            seen_lower.add(m.name.lower())
            all_members.append(m)
            new_count += 1
            print(f"  Found: {m.name} | {m.last_active} | {m.combat_power} | {m.warband} | {m.activeness}")
        else:
            for existing in all_members:
                if existing.name.lower() == key:
                    if not existing.combat_power and m.combat_power:
                        existing.combat_power = m.combat_power
                        print(f"  Updated power: {existing.name} = {m.combat_power}")
                    if existing.activeness == 0 and m.activeness > 0:
                        existing.activeness = m.activeness
                        print(f"  Updated activeness: {existing.name} = {m.activeness}")
                    if not existing.warband and m.warband:
                        existing.warband = m.warband
                        print(f"  Updated warband: {existing.name} = {m.warband}")
                    # Prefer name with more digits (better OCR read of numbers)
                    new_digits = sum(c.isdigit() for c in m.name)
                    old_digits = sum(c.isdigit() for c in existing.name)
                    if new_digits > old_digits:
                        existing.name = m.name
                    break
    return new_count


def _scroll_pass(scroll_fn, seen_lower, all_members, total, label, max_scrolls=60) -> None:
    scroll_to_top()
    no_change_count = 0
    scroll_count = 0
    prev_img = screenshot()

    while len(all_members) < total and scroll_count < max_scrolls:
        img = screenshot()
        results = _ocr(img)

        if not all_members:
            detected = _get_total_members(results)
            if detected:
                total = detected
                print(f"Total members: {total}")

        new = _process_screen(results, seen_lower, all_members)
        print(f"  Screen: {new} new, {len(all_members)}/{total} total")

        if len(all_members) >= total:
            print("All members captured.")
            return

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
    seen_lower: set[str] = set()
    all_members: list[Member] = []

    mark_activity()
    _done = threading.Event()
    _start_stall_watchdog(_done)

    try:
        init_db()
        ensure_resolution()
        navigate_to_guild_members()
        print("Starting scrape...")
        total = _get_total_members(_ocr(screenshot()))
        print(f"Target: {total} members")

        _scroll_pass(scroll_down, seen_lower, all_members, total, "Pass 1", max_scrolls=150)

        if len(all_members) < total:
            print(f"\nSecond pass (missing {total - len(all_members)})...")
            _scroll_pass(scroll_down, seen_lower, all_members, total, "Pass 2", max_scrolls=150)

        # Cleanup pass: fill any members still missing activeness
        incomplete = [m for m in all_members if m.activeness == 0]
        if incomplete:
            names = ", ".join(m.name for m in incomplete)
            print(f"\nCleanup pass (activeness=0: {names})...")
            scroll_to_top()
            no_change_count = 0
            prev_img = screenshot()
            while True:
                img = screenshot()
                _process_screen(_ocr(img), seen_lower, all_members)
                if not any(m.activeness == 0 for m in all_members):
                    print("All activeness filled.")
                    break
                scroll_down()
                time.sleep(1.2)
                curr_img = screenshot()
                if not screen_changed(prev_img, curr_img):
                    no_change_count += 1
                    if no_change_count >= 2:
                        print("Cleanup pass complete.")
                        break
                else:
                    no_change_count = 0
                prev_img = curr_img

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
    for _mode in MODES:
        cli.add_argument(f"--{_mode.replace('_', '-')}", action="store_true")
    args = cli.parse_args()

    members, actual_count = scrape_guild()
    print(f"\nDone. Captured {actual_count} members.")
    for m in members:
        print(f"  {m.name:<20} {m.last_active:<10} {m.combat_power:<10} {m.warband:<20} {m.activeness}")

    enabled = [m for m in MODES if getattr(args, m)]
    if enabled:
        run_modes(enabled)
