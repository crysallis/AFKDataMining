import logging
import re
import sys
import time
from difflib import SequenceMatcher
from rapidocr_onnxruntime import RapidOCR
from device import screenshot, scroll_down, scroll_down_small, scroll_to_top, screen_changed, ensure_resolution
from nav import navigate_to_guild_members
from parser import parse_members, Member
from db import init_db, save_snapshot, validate_names

_log_fh = open('C:/vscode/AFKDataMining/scraper.log', 'w', encoding='utf-8', buffering=1)

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
    format='[%(name)s] %(message)s',
    handlers=[logging.StreamHandler(sys.__stdout__), logging.FileHandler('C:/vscode/AFKDataMining/scraper.log', mode='a', encoding='utf-8')],
)

TOTAL_RE = re.compile(r"Guild Member \((\d+)/(\d+)\)")

engine = RapidOCR()


def _ocr(img):
    results, _ = engine(img)
    return results or []


def _get_total_members(ocr_results) -> int:
    for _, text, _ in ocr_results:
        m = TOTAL_RE.search(text)
        if m:
            return int(m.group(2))
    return 90


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


def scrape_guild() -> list[Member]:
    seen_lower: set[str] = set()
    all_members: list[Member] = []

    init_db()
    ensure_resolution()
    navigate_to_guild_members()
    print("Starting scrape...")
    total = _get_total_members(_ocr(screenshot()))
    print(f"Target: {total} members")

    _scroll_pass(scroll_down, seen_lower, all_members, total, "Pass 1", max_scrolls=150)

    if len(all_members) < total:
        print(f"\nSecond pass (missing {total - len(all_members)})...")
        _scroll_pass(scroll_down_small, seen_lower, all_members, total, "Pass 2", max_scrolls=150)

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
            scroll_down_small()
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
    snapshot_id = save_snapshot(all_members)
    print(f"Saved to DB as snapshot #{snapshot_id}.")
    if uncertain:
        print(f"REVIEW_NAMES: {', '.join(uncertain)}")
    return all_members


if __name__ == "__main__":
    members = scrape_guild()
    print(f"\nDone. Captured {len(members)} members.")
    for m in members:
        print(f"  {m.name:<20} {m.last_active:<10} {m.combat_power:<10} {m.warband:<20} {m.activeness}")
