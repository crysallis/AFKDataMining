"""Dream Realm scan · per-day boss scores (rank, score, tier badge).

The screen defaults to TODAY's in-progress board, which is never captured.
The date bar holds today + 3 past days as `M/DD` text, 3 visible at a time,
with small arrows that page the strip. Day selection is text-driven: OCR the
date labels and tap the matching block, paging left when the target is not
visible. Each day has its own boss, so the boss name is re-read per day."""
import logging
import re
import time
from datetime import date

from db import init_db, get_missing_dream_realm_days, save_dream_realm, get_boss_for_date
from device import screenshot, tap
from nav import navigate_to_mode, apply_guild_filter, find_template, _t, _wait_until_stable
from ocr import ocr_image, block_center
from modes.common import parse_rank_rows, scroll_scan, resolve_entries, detect_tiers, tier_for_row

_SCORE_RE = re.compile(r"^[\d,.]+\s*[KMB]$", re.IGNORECASE)


def _find_score(texts: list[str]) -> str | None:
    """Find the raw score string (e.g. '54875M', '7600K') from the texts list."""
    for t in texts:
        if _SCORE_RE.match(t.replace(",", "").strip()):
            return t.strip()
    return None

DATE_RE = re.compile(r"^\d{1,2}/\d{1,2}$")
# Date bar sits below the hero section (~y=650-700 in a 1080x1920 screen).
# Using a generous upper bound — date format M/DD is specific enough to avoid
# false matches from other screen elements.
_DATE_BAR_Y_MAX = 900
# Boss name appears at the very top of the Dream Realm main screen (y≈62).
_BOSS_NAME_Y = (0, 200)


def _norm_date_text(t: str) -> str:
    t = re.sub(r"\s+", "", t)
    return t.replace("O", "0").replace("o", "0").replace("l", "1").replace("I", "1")


def _date_label(day_iso: str) -> str:
    d = date.fromisoformat(day_iso)
    return f"{d.month}/{d.day}"


def _day_number(text: str) -> str:
    """Extract the day part from a possibly-mangled M/DD OCR read.
    OCR often misreads the month digit ('6' → '$', '8' → '&' etc.) but
    reliably reads the day number after the slash."""
    clean = re.sub(r"[^\d/]", "", text)  # keep only digits and slash
    parts = clean.split("/")
    return parts[-1] if parts and parts[-1].isdigit() else ""


def _select_day(day_iso: str, attempts: int = 6) -> bool:
    """Tap the target date tab. Dates run newest-left to oldest-right.
    Try to tap directly first (most days are visible without scrolling).
    If not visible, tap the RIGHT arrow to reveal older dates."""
    d = date.fromisoformat(day_iso)
    target_day = str(d.day)   # "11" from "2026-06-11"
    target_month = str(d.month)  # "6"

    for attempt in range(attempts):
        visible: list[str] = []
        for box, text, _conf in ocr_image(screenshot()):
            cx, cy = block_center(box)
            if cy > _DATE_BAR_Y_MAX:
                continue
            norm = _norm_date_text(text)
            if DATE_RE.match(norm):
                visible.append(norm)
            # Primary: exact match on normalized text (e.g. "6/11")
            # Fallback: match on day number after "/" — handles month misreads ("$/11" → day "11")
            day_part = _day_number(text)
            if norm == f"{target_month}/{target_day}" or (day_part == target_day and "/" in text):
                logging.info("Found date tabs %s; selecting %s at (%d,%d).",
                             visible or "[]", _date_label(day_iso), cx, cy)
                tap(cx, cy)
                _wait_until_stable()
                return True
        if visible:
            logging.info("Date %s not in visible tabs %s; paging right.",
                         _date_label(day_iso), visible)
        # Date not visible — tap RIGHT arrow to reveal older dates
        pos = find_template(screenshot(), _t("dream_realm_right"), threshold=0.75)
        if pos is None:
            print(f"  Dream Realm: date {_date_label(day_iso)} not visible and no right-arrow found.")
            return False
        tap(*pos)
        time.sleep(0.8)
    return False


_TIMER_RE = re.compile(r"\d+\s*[hms:]\s*\d", re.IGNORECASE)


def _read_boss_name(ocr_results) -> str:
    """Read the boss name from the Dream Realm main screen OCR.
    Skips UI labels, countdown timers ('Results in 19h:20m:30s'), and date tabs."""
    skip_words = {"dream", "realm", "rankings", "server", "district", "ranking",
                  "guild", "members", "filter", "season", "results", "formations",
                  "store", "history", "monsters", "battle", "rewards"}
    best = ""
    best_y = 9999
    for box, text, _conf in ocr_results:
        _, cy = block_center(box)
        t = text.strip()
        if not t or cy > _BOSS_NAME_Y[1]:
            continue
        if DATE_RE.match(_norm_date_text(t)):
            continue
        if any(w in t.lower() for w in skip_words):
            continue
        if _TIMER_RE.search(t):
            continue
        # Prefer topmost block — boss name is always at the top of the screen
        if cy < best_y:
            best = t
            best_y = cy
    return best


def _parse(img, ocr_results) -> list[dict]:
    tiers = detect_tiers(img, "dream_realm")
    rows = []
    for row in parse_rank_rows(img, ocr_results):
        score = _find_score(row.texts)
        rows.append({"name": row.name, "rank": row.rank, "score": score,
                     "tier": tier_for_row(row.y, tiers) or "common"})
    return rows


def scan() -> None:
    init_db()
    missing = get_missing_dream_realm_days()
    if not missing:
        print("DREAM_REALM: all recent days already captured, nothing to do.")
        return

    # Navigate to Dream Realm MAIN SCREEN (card tap only, not Rankings yet)
    # so we can read the boss name before entering the ranking list.
    from nav import (navigate_to_overview, _tap_to_reach, _is_at_battle_modes,
                     MODE_CARD_LABEL, _ocr_tap_text, _wait_until_stable,
                     find_template, TEMPLATES_DIR)
    from ocr import ocr_image as _ocr

    def _to_dream_realm_main() -> bool:
        if not navigate_to_overview():
            return False
        if not _tap_to_reach(
            lambda s: find_template(s, TEMPLATES_DIR / "battle_modes_btn.png", threshold=0.75),
            _is_at_battle_modes, "battle_modes", fallback_xy=None,
        ):
            return False
        if not _ocr_tap_text(_ocr, MODE_CARD_LABEL["dream_realm"],
                             "card:Dream Realm", scroll_between=True, attempts=5):
            return False
        _wait_until_stable()
        return True

    if not _to_dream_realm_main():
        print("DREAM_REALM: could not reach Dream Realm main screen.")
        return

    # Boss name is visible on the Dream Realm main screen
    boss_name = _read_boss_name(_ocr(screenshot()))
    if boss_name:
        print(f"DREAM_REALM: boss = {boss_name}")
    else:
        print("DREAM_REALM: boss name not read, saving with blank.")

    # Tap Rankings to enter the ranked list
    rankings_pos = find_template(screenshot(), TEMPLATES_DIR / "rankings_btn.png", threshold=0.75)
    if not rankings_pos:
        print("DREAM_REALM: rankings button not found.")
        return
    tap(*rankings_pos)
    _wait_until_stable()

    # Resolve today's boss_id so we can compute past days' bosses from the cycle
    from db import _connect, _resolve_boss as _rb
    from datetime import datetime, timezone
    today_iso = datetime.now(timezone.utc).date().isoformat()
    today_boss_id: int | None = None
    if boss_name:
        with _connect() as _c:
            _, today_boss_id = _rb(_c, boss_name)

    logging.info("Dream Realm: %d day(s) to capture: %s",
                 len(missing), [_date_label(d) for d in missing])
    for day in missing:
        logging.info("Processing date: %s", _date_label(day))
        # Compute which boss was on this past day from the rotation cycle
        if today_boss_id is not None:
            day_boss_name, day_boss_id = get_boss_for_date(today_iso, today_boss_id, day)
        else:
            day_boss_name, day_boss_id = "", None

        if not _select_day(day):
            print(f"DREAM_REALM: could not select {_date_label(day)}, skipping.")
            continue
        if not apply_guild_filter():
            print(f"DREAM_REALM: guild filter failed for {day}, skipping.")
            continue
        entries = scroll_scan(_parse, f"Dream Realm {_date_label(day)}")
        rows = resolve_entries(entries, "Dream Realm")
        saved = save_dream_realm(rows, day, day_boss_name or boss_name, day_boss_id)
        display_boss = day_boss_name or boss_name or "unknown"
        print(f"DREAM_REALM: saved {saved} entries for {day} (boss: {display_boss}).")
