"""Honor Duel ranking scan · rank + honor points, single kept scan (overwrite)."""
from db import init_db, save_honor_duel
from nav import navigate_to_mode, apply_guild_filter
from modes.common import parse_rank_rows, scroll_scan, resolve_entries, make_reentry_fn


def _parse(img, ocr_results) -> list[dict]:
    return [
        {"name": row.name, "rank": row.rank,
         "points": row.numbers[0] if row.numbers else None}
        for row in parse_rank_rows(img, ocr_results)
    ]


def scan() -> None:
    init_db()
    navigate_to_mode("honor_duel")
    if not apply_guild_filter():
        print("HONOR_DUEL: guild filter failed, aborting.")
        return
    entries = scroll_scan(_parse, "Honor Duel", reentry_fn=make_reentry_fn("HONOR_DUEL"))
    rows = resolve_entries(entries, "Honor Duel")
    saved = save_honor_duel(rows)
    print(f"HONOR_DUEL: saved {saved} entries.")
