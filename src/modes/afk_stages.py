"""AFK Stages ranking scan · rank + phase progress ("896" or "Apex 14").
Keyed by (season_id, phase) · each season has up to 3 phases."""
import re

from db import init_db, save_afk_stages
from device import screenshot, tap
from nav import (navigate_to_overview, _tap_to_reach, _is_at_battle_modes,
                 MODE_CARD_LABEL, _ocr_tap_text, _wait_until_stable,
                 find_template, TEMPLATES_DIR, apply_guild_filter)
from ocr import ocr_image, block_center
from modes.common import parse_rank_rows, scroll_scan, resolve_entries, make_reentry_fn

PHASE_RE = re.compile(r"Phase\s*([123])", re.IGNORECASE)
APEX_RE = re.compile(r"Apex\s*\d+", re.IGNORECASE)


def _detect_phase(ocr_results) -> int | None:
    for _, text, _ in ocr_results:
        m = PHASE_RE.search(text)
        if m:
            return int(m.group(1))
    return None


def _parse(img, ocr_results) -> list[dict]:
    out = []
    for row in parse_rank_rows(img, ocr_results):
        progress = None
        for t in row.texts:
            m = APEX_RE.search(t)
            if m:
                progress = re.sub(r"\s+", " ", m.group(0)).title()
                break
        if progress is None and row.numbers:
            progress = str(row.numbers[-1])
        out.append({"name": row.name, "rank": row.rank, "progress": progress})
    return out


def scan() -> None:
    init_db()
    from db import _connect, _active_season_id
    with _connect() as conn:
        season_id = _active_season_id(conn)
    if season_id is None:
        print("AFK_STAGES: no active season in DB, aborting.")
        return

    # Navigate to AFK Stage main screen (card tap only) to read current phase
    def _to_afk_stage_main() -> bool:
        if not navigate_to_overview():
            return False
        if not _tap_to_reach(
            lambda s: find_template(s, TEMPLATES_DIR / "battle_modes_btn.png", threshold=0.75),
            _is_at_battle_modes, "battle_modes", fallback_xy=None,
        ):
            return False
        if not _ocr_tap_text(ocr_image, MODE_CARD_LABEL["afk_stages"],
                             "card:AFK Stage", scroll_between=True, attempts=5):
            return False
        _wait_until_stable()
        return True

    if not _to_afk_stage_main():
        print("AFK_STAGES: could not reach AFK Stage main screen.")
        return

    phase = _detect_phase(ocr_image(screenshot()))
    if phase is None:
        print("AFK_STAGES: could not detect phase, aborting.")
        return
    print(f"AFK_STAGES: season_id={season_id}, phase={phase}")

    # Tap rankings button to enter the ranking list
    rankings_pos = find_template(screenshot(), TEMPLATES_DIR / "rankings_btn.png", threshold=0.75)
    if not rankings_pos:
        print("AFK_STAGES: rankings button not found.")
        return
    tap(*rankings_pos)
    _wait_until_stable()

    if not apply_guild_filter():
        print("AFK_STAGES: guild filter failed, aborting.")
        return

    entries = scroll_scan(_parse, "AFK Stages", reentry_fn=make_reentry_fn("AFK_STAGES"))
    rows = resolve_entries(entries, "AFK Stages")
    saved = save_afk_stages(rows, season_id, phase)
    print(f"AFK_STAGES: saved {saved} entries for season_id={season_id} phase={phase}.")
