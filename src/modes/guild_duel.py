"""Guild Duel ranking scan · rank + crests. This ranking screen mixes both
guilds in the same Mon-Fri contest (no in-game filter available), so each row
carries its own guild tag under the member name ("RKF RiffRaff") that must be
checked per row — rows tagged for the other guild are dropped before name
resolution, not left for resolve_names() to sort out."""
from difflib import SequenceMatcher

from db import init_db, current_guild_duel_period, save_guild_duel
from nav import navigate_to_mode
from modes.common import parse_rank_rows, scroll_scan, resolve_entries

GUILD_TAG = "rkf riffraff"
GUILD_TAG_THRESHOLD = 0.75


def _is_our_guild(texts: list[str]) -> bool:
    """Lenient tag check: substring match, or fuzzy@0.75 against GUILD_TAG,
    tolerating OCR noise on the (small, gray) tag text without dropping a real
    member. Checks every extra text block on the row, since the tag may land
    split across blocks or alongside other row text."""
    for t in texts:
        low = t.lower().strip()
        if GUILD_TAG in low or SequenceMatcher(None, low, GUILD_TAG).ratio() >= GUILD_TAG_THRESHOLD:
            return True
    return False


def _parse(img, ocr_results) -> list[dict]:
    return [
        {"name": row.name, "rank": row.rank,
         "crests": row.numbers[0] if row.numbers else None}
        for row in parse_rank_rows(img, ocr_results)
        if _is_our_guild(row.texts)
    ]


def _reentry() -> bool:
    """Second-pass reentry, same as make_reentry_fn() but without the in-game
    guild-filter tap — there is no such filter on this screen (both guilds are
    always listed together; _is_our_guild() does the filtering per row)."""
    from nav import press_back, _wait_until_stable, find_template, _t
    from device import tap, screenshot
    press_back()
    _wait_until_stable()
    pos = find_template(screenshot(), _t("rankings_btn"), threshold=0.75)
    if not pos:
        print("GUILD_DUEL: rankings_btn not found on reentry, skipping pass 2.")
        return False
    tap(*pos)
    _wait_until_stable()
    return True


def scan() -> None:
    init_db()
    navigate_to_mode("guild_duel")
    entries = scroll_scan(_parse, "Guild Duel", reentry_fn=_reentry)
    rows = resolve_entries(entries, "Guild Duel")
    period = current_guild_duel_period()
    saved = save_guild_duel(rows)
    print(f"GUILD_DUEL: saved {saved} entries for period {period}.")
