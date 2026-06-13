"""Arena ranking scan · rank, points, tier (colored badge via template
matching). Always ongoing, so this is a pure overwrite · no history kept."""
from db import init_db, save_arena
from nav import navigate_to_mode, apply_guild_filter
from modes.common import (parse_rank_rows, scroll_scan, resolve_entries,
                           detect_tiers, tier_for_row, make_reentry_fn)


def _parse(img, ocr_results) -> list[dict]:
    tiers = detect_tiers(img, "arena")
    return [
        {"name": row.name, "rank": row.rank,
         "points": row.numbers[0] if row.numbers else None,
         "tier": tier_for_row(row.y, tiers)}
        for row in parse_rank_rows(img, ocr_results)
    ]


def scan() -> None:
    init_db()
    navigate_to_mode("arena")
    if not apply_guild_filter():
        print("ARENA: guild filter failed, aborting.")
        return
    entries = scroll_scan(_parse, "Arena", reentry_fn=make_reentry_fn("ARENA"))
    rows = resolve_entries(entries, "Arena")
    saved = save_arena(rows)
    print(f"ARENA: saved {saved} entries.")
