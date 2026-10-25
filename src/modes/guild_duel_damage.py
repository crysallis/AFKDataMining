"""Guild Duel damage scan · total damage per member from the weekend "Total Duel
Merit Rankings" screen, saved onto the same weekly row the Monday Battle Report
scan later fills with crests. The list mixes both guilds (rows tagged for the
other guild are dropped) and repeats the top 3 under a hero podium that is
never read. The viewer's own pinned bar carries no damage value when they have
not attacked, so it drops out as a non-data row."""
import re

from db import (init_db, _utcnow, RosterResolver, guild_duel_damage_period_key,
                guild_duel_damage_window_open, save_guild_duel_damage,
                count_guild_duel_damage)
from device import guild_duel_damage_scroll_down
from nav import navigate_to_guild_duel_merit_rankings
from ocr import block_center
from modes.common import RankRow, scroll_scan, resolve_entries
from modes.damage_clip import WINDOW, filter_clear_rows
from modes.damage_dedupe import drop_duplicate_reads
from modes.damage_frames import FrameKeeper
from modes.guild_duel import _is_our_guild

# Podium sits above the first list row (~y725 on a 1080x1920 capture).
LIST_Y_MIN = 700

# Card geometry on a 1080x1920 capture: the damage value sits ~41px below the
# name's center, the guild tag ~57px below it, damage at the far right, name
# and tag left-aligned in the middle band.
DAMAGE_X_MIN = 800
NAME_X_RANGE = (250, 700)
NAME_ABOVE_DAMAGE = (20, 65)
NAME_ABOVE_DAMAGE_TYPICAL = 41
TAG_BELOW_DAMAGE = (0, 50)

_DAMAGE_RE = re.compile(r"^([\d.]+)([KMBT])$", re.IGNORECASE)
_MULTIPLIERS = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000, "T": 1_000_000_000_000}


def parse_damage(text: str) -> int | None:
    """'567B' -> 567_000_000_000 · converted once here so every later
    calculation can use the number directly. None when there is no suffix or
    the text is not a damage value at all."""
    m = _DAMAGE_RE.match(text.replace(",", "").replace(" ", ""))
    if not m:
        return None
    try:
        mantissa = float(m.group(1))
    except ValueError:
        return None
    return round(mantissa * _MULTIPLIERS[m.group(2).upper()])


def format_damage(n: int) -> str:
    """567000000000 -> '567B' · the screen's own style, for log lines a person
    scans against the in-game list."""
    for size, suffix in ((10**12, "T"), (10**9, "B"), (10**6, "M")):
        if n >= size:
            return f"{n / size:g}{suffix}"
    return str(n)


def unmatched_lines(entries: list[dict], rows: list[dict],
                    frames: dict | None = None) -> list[str]:
    """One line per entry whose name resolve_entries could not match, with the
    damage seen beside it so the person can be found by value on the in-game
    list (then fixed by a name correction or a rescan). Sent through /scan
    because it carries the GUILD_DUEL_DAMAGE prefix."""
    matched = {r["name"] for r in rows}
    frames = frames or {}
    lines = []
    for e in entries:
        if e["name"] in matched:
            continue
        line = f"GUILD_DUEL_DAMAGE: unmatched '{e['name']}' · saw {format_damage(e['damage'])}"
        path = frames.get((e["name"], e["damage"]))
        lines.append(f"{line} · frame {path}" if path else line)
    return lines


def rows_from_blocks(ocr_results: list) -> list[RankRow]:
    """One row per damage value, paired with the name block just above it and
    the guild tag just below it, by position. Rank is never needed (and rank 1
    is an ornate glyph OCR does not read, so a rank-keyed parser silently drops
    the top row). A pinned own-row bar pairs with its own damage, never with
    the row it covers."""
    blocks = [(*block_center(box), text.strip()) for box, text, _conf in ocr_results]
    rows = []
    for dx, dy, dtext in blocks:
        if dx < DAMAGE_X_MIN or dy < LIST_Y_MIN or parse_damage(dtext) is None:
            continue
        names = [(abs((dy - cy) - NAME_ABOVE_DAMAGE_TYPICAL), text) for cx, cy, text in blocks
                 if text and NAME_X_RANGE[0] <= cx <= NAME_X_RANGE[1]
                 and NAME_ABOVE_DAMAGE[0] <= dy - cy <= NAME_ABOVE_DAMAGE[1]]
        if not names:
            continue
        tags = [text for cx, cy, text in blocks
                if NAME_X_RANGE[0] <= cx <= NAME_X_RANGE[1]
                and TAG_BELOW_DAMAGE[0] <= cy - dy <= TAG_BELOW_DAMAGE[1]]
        rows.append(RankRow(rank=None, name=min(names)[1], texts=tags + [dtext], y=dy))
    return rows


def rows_to_entries(rows: list) -> list[dict]:
    entries = []
    for row in rows:
        if not _is_our_guild(row.texts):
            continue
        damage = next((d for d in map(parse_damage, row.texts) if d is not None), None)
        if damage is None:
            continue
        entries.append({"name": row.name, "damage": damage})
    return entries


SETTLE_REGION = (slice(WINDOW[0], WINDOW[1]), slice(0, 1080))

clip_stats = {"dropped": 0}
_state = {"keeper": None, "resolver": None}


def _parse(img, ocr_results) -> list[dict]:
    rows, dropped = filter_clear_rows(img, rows_from_blocks(ocr_results))
    clip_stats["dropped"] += dropped
    entries = rows_to_entries(rows)
    if _state["keeper"] is not None:
        for e in entries:
            if _state["resolver"].resolve(e["name"]) is None:
                _state["keeper"].remember(img, e["name"], e["damage"])
    return entries


def save_gate(window_open: bool, saving: int, previous: int) -> tuple[bool, list[str]]:
    """Last check before the scan writes. A scan that ran past Monday 00:00 UTC read
    the reset screen, so nothing is saved; fewer members saved than an earlier scan
    of the same week only warns, since a row dropped as clipped that no other stop
    read in full would otherwise be silent."""
    if not window_open:
        return False, ["GUILD_DUEL_DAMAGE: window closed during the scan, not saving."]
    if previous and saving < previous:
        return True, [f"GUILD_DUEL_DAMAGE: warning, saved {saving} members, previous scan saved {previous}."]
    return True, []


def scan(force: bool = False) -> str:
    init_db()
    now = _utcnow()
    if not force and not guild_duel_damage_window_open(now):
        print("GUILD_DUEL_DAMAGE: skipping (outside the Saturday-Sunday UTC window).")
        return "skipped"
    period = guild_duel_damage_period_key(now)
    clip_stats["dropped"] = 0
    _state["keeper"] = FrameKeeper()
    _state["resolver"] = RosterResolver(learn=False)

    navigate_to_guild_duel_merit_rankings()
    entries = scroll_scan(_parse, "Guild Duel Damage", scroll_fn=guild_duel_damage_scroll_down,
                          require_new=False, no_change_limit=4, max_scrolls=70,
                          frames_per_stop=1, settle_region=SETTLE_REGION)
    print(f"GUILD_DUEL_DAMAGE: skipped {clip_stats['dropped']} row reads hidden by the pinned bar or list edge.")
    entries, merge_lines = drop_duplicate_reads(entries, fmt_damage=format_damage)
    for line in merge_lines:
        print(line)
    rows = resolve_entries(entries, "Guild Duel Damage")
    matched_names = {r["name"] for r in rows}
    frames = _state["keeper"].write_for(
        [(e["name"], e["damage"]) for e in entries if e["name"] not in matched_names],
        fmt_damage=format_damage)
    for line in unmatched_lines(entries, rows, frames):
        print(line)
    should_save, notes = save_gate(force or guild_duel_damage_window_open(_utcnow()),
                                   len(rows), count_guild_duel_damage(period))
    for note in notes:
        print(note)
    if not should_save:
        return "skipped"
    saved = save_guild_duel_damage(rows, period)
    print(f"GUILD_DUEL_DAMAGE: saved {saved} entries for period {period}.")
    return "success"
