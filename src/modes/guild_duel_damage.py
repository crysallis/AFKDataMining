"""Guild Duel damage scan · total damage per member from the weekend "Total Duel
Merit Rankings" screen, saved onto the same weekly row the Monday Battle Report
scan later fills with crests. The list mixes both guilds (rows tagged for the
other guild are dropped) and repeats the top 3 under a hero podium that is
never read. The viewer's own pinned bar carries no damage value when they have
not attacked, so it drops out as a non-data row."""
import re

from modes.guild_duel import _is_our_guild

# Podium sits above the first list row (~y725 on a 1080x1920 capture).
LIST_Y_MIN = 700

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
