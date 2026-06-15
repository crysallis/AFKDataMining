import argparse
import importlib
import logging
import os
import re
import sys
import threading
from pathlib import Path
from device import (screenshot, scroll_to_top,
                    ensure_resolution, seconds_since_activity, mark_activity, kill_adb_process)
from nav import navigate_to_guild_members
from parser import parse_members, Member
from db import init_db, save_snapshot, validate_names
from capture_ids import capture_ingame_ids

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

# Import AFTER the logging/Tee setup above so RapidOCR's init logs are captured
# (both pull in the ocr module, which constructs RapidOCR at import time).
from ocr import ocr_image as _ocr  # noqa: E402
from modes.common import scroll_scan  # noqa: E402


def _get_total_members(ocr_results) -> int:
    for _, text, _ in ocr_results:
        m = TOTAL_RE.search(text)
        if m:
            return int(m.group(1))  # current member count, not capacity
    return 90  # fallback if the header isn't read this frame


def _guild_parse(img, ocr_results) -> list[dict]:
    """parse_fn for the shared scroll_scan · the guild scan's 'what': parse member
    cards into dicts. Empty/zero/Unknown fields become None so _vote_entries skips
    them when voting (an unread power/warband never outvotes a real one). img is
    unused (parse_members works from the OCR results), kept for the parse_fn contract."""
    out = []
    for m in parse_members(ocr_results):
        out.append({
            "name": m.name,
            "last_active": m.last_active if m.last_active not in ("Unknown", "") else None,
            "combat_power": m.combat_power or None,
            "activeness": m.activeness or None,
            "warband": m.warband or None,
        })
    return out


def _entry_to_member(e: dict) -> Member:
    """Voted entry dict -> Member, restoring the empty/zero/Unknown defaults."""
    return Member(
        name=e["name"],
        last_active=e.get("last_active") or "Unknown",
        combat_power=e.get("combat_power") or "",
        activeness=e.get("activeness") or 0,
        warband=e.get("warband") or "",
    )


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
        scroll_to_top()
        target = _get_total_members(_ocr(screenshot()))
        print(f"Target: {target} members")

        # Same shared driver the mode scans use (the 'how'): multi-sample frames per
        # scroll stop, then resolve-then-vote per identity. _guild_parse is the 'what'.
        entries = scroll_scan(_guild_parse, "Guild", reentry_fn=None)
        all_members = [_entry_to_member(e) for e in entries]
        print(f"Voted: {len(all_members)} members.")
        for m in all_members:
            print(f"  {m.name} | {m.last_active} | {m.combat_power} | {m.warband} | {m.activeness}")

        all_members, uncertain = validate_names(all_members)
        snapshot_id, actual_count = save_snapshot(all_members, pending_names=set(uncertain))
        print(f"Saved to DB as snapshot #{snapshot_id}.")
        if uncertain:
            print(f"REVIEW_NAMES: {', '.join(uncertain)}")
        capture_ingame_ids()
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
