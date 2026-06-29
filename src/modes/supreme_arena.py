"""Supreme Arena ranking scan · rank only. Active Wednesday 00:00 UTC through
Monday 00:00 UTC; off Monday + Tuesday (UTC). Overwrites within the current
period (keyed by that week's Wednesday); a new period starts a new record set."""
from db import init_db, get_supreme_period, save_supreme_arena
from device import screenshot
from nav import navigate_to_mode, apply_guild_filter, find_template, _t, _wait_until_stable
from modes.common import parse_rank_rows, scroll_scan, resolve_entries, make_reentry_fn


def _parse(img, ocr_results) -> list[dict]:
    return [{"name": row.name, "rank": row.rank} for row in parse_rank_rows(img, ocr_results)]


def scan() -> None:
    init_db()
    period = get_supreme_period()
    if period is None:
        print("SUPREME_ARENA: skipping (not Mon/Tue UTC, or already scanned today).")
        return
    try:
        navigate_to_mode("supreme_arena")
    except RuntimeError:
        if find_template(screenshot(), _t("supreme_arena_inactive"), threshold=0.80):
            print("SUPREME_ARENA: inactive screen detected, skipping.")
            return
        raise

    # "Daily Calculation" popup — appears once per day on first load.
    # Tap anywhere on it to dismiss. Template captured as supreme_arena_daily_calc.png.
    from device import tap
    daily_calc = find_template(screenshot(), _t("supreme_arena_daily_calc"), threshold=0.80)
    if daily_calc:
        tap(*daily_calc)
        _wait_until_stable()

    if not apply_guild_filter():
        print("SUPREME_ARENA: guild filter failed, aborting.")
        return
    entries = scroll_scan(_parse, "Supreme Arena", reentry_fn=make_reentry_fn("SUPREME_ARENA"))
    rows = resolve_entries(entries, "Supreme Arena")
    saved = save_supreme_arena(rows)
    print(f"SUPREME_ARENA: saved {saved} entries for period {period}.")
