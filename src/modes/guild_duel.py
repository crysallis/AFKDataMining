"""Guild Duel Battle Report scan · rank + crests, read once a week from the
previous week's finalized results. This screen mixes both guilds in the same
Mon-Fri contest (no in-game filter available, same as the old daily Rankings
screen), so each row carries its own guild tag under the member name ("RKF
RiffRaff") that must be checked per row -- rows tagged for the other guild
are dropped before name resolution, not left for resolve_names() to sort
out. The viewer's own row is sticky (pinned at a fixed position regardless
of scroll), which occludes whatever real row would be there -- confirmed
unavoidable even scrolling by hand, so that one member's row is accepted as
unrecoverable rather than engineered around."""
import logging
from difflib import SequenceMatcher

from db import init_db, get_guild_duel_period, save_guild_duel
from device import guild_duel_scroll_down
from nav import navigate_to_guild_duel_battle_report
from modes.common import parse_rank_rows, scroll_scan, resolve_entries

GUILD_TAG = "rkf riffraff"
GUILD_TAG_THRESHOLD = 0.75

# Reward banner (Victory/Tier Rewards icons and their numeric values, e.g.
# 1200/500/600/250/6) sits above the member list, measured at y=761-762 on a
# 1080x1920 capture; the first real row starts at y=908 -- excluded so those
# reward numbers are never read as row data.
LIST_Y_MIN = 850


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
    rows = parse_rank_rows(img, ocr_results, list_y_min=LIST_Y_MIN)
    kept = [row for row in rows if _is_our_guild(row.texts)]
    logging.debug("Guild Duel: %d row(s) parsed, %d kept; dropped texts: %s",
                  len(rows), len(kept),
                  [row.texts for row in rows if row not in kept])
    return [
        {"name": row.name, "rank": row.rank,
         "crests": row.numbers[0] if row.numbers else None}
        for row in kept
    ]


def scan(force: bool = False) -> None:
    init_db()
    period = get_guild_duel_period(force=force)
    if period is None:
        print("GUILD_DUEL: skipping (this week's period already scanned).")
        return

    navigate_to_guild_duel_battle_report()
    entries = scroll_scan(_parse, "Guild Duel", scroll_fn=guild_duel_scroll_down,
                          require_new=False, no_change_limit=4, max_scrolls=90)
    rows = resolve_entries(entries, "Guild Duel")
    saved = save_guild_duel(rows, period)
    print(f"GUILD_DUEL: saved {saved} entries for period {period}.")
