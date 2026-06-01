import re
from dataclasses import dataclass
from difflib import SequenceMatcher

TIME_RE = re.compile(r"^(\d+[smhd]\s*ago|online)$", re.IGNORECASE)
POWER_RE = re.compile(r"([\d.]+)\s*([KM])", re.IGNORECASE)
# Anchored power check for the NAME slot only: a candidate is a power value just
# when it STARTS as one (e.g. "95708K", "111M", "95708K (Base)"). Using POWER_RE's
# bare .search() here wrongly rejected real names that merely contain digits+K/M,
# e.g. "Ramz78k" (matched "78k") — silently dropping that member from every scan.
PURE_POWER_RE = re.compile(r"^[\d.]+\s*[KM]\b", re.IGNORECASE)
SKIP_NAMES = {"Friends", "Guild Announcement", "Me", "Fellowship", "Activeness", "Descending", "Warband"}
SKIP_LOWER = {s.lower() for s in SKIP_NAMES}


def _is_skip_label(t: str) -> bool:
    """True for known emblem/UI labels, tolerant of OCR noise around them
    (e.g. the 'Friends' emblem misread as '1Friends' or 'Friends.') and of
    clipped characters (e.g. the 'F' dropped → 'riends'). Exact match first,
    then a gated fuzzy fallback (len >= 4, ratio >= 0.8) so a mangled emblem
    label is skipped without risking real names (shortest roster names score
    well below 0.5 against any label)."""
    cleaned = re.sub(r"^[^a-zA-Z]+|[^a-zA-Z]+$", "", t).lower()
    if cleaned in SKIP_LOWER:
        return True
    if len(cleaned) >= 4:
        return any(SequenceMatcher(None, cleaned, s).ratio() >= 0.8 for s in SKIP_LOWER)
    return False


@dataclass
class Member:
    name: str
    last_active: str
    combat_power: str
    activeness: int
    warband: str = ''
    warband_id: int | None = None


def _parse_blocks(ocr_results: list) -> list[tuple[int, int, str]]:
    blocks = []
    for box, text, _ in ocr_results:
        x = int(box[0][0])
        y = int(box[0][1])
        blocks.append((x, y, text.strip()))
    return blocks


def _find_near_y(blocks, target_y, y_tolerance, x_min=0, x_max=9999):
    return [
        (x, y, t) for x, y, t in blocks
        if abs(y - target_y) <= y_tolerance and x_min <= x <= x_max
    ]


def parse_members(ocr_results: list) -> list[Member]:
    blocks = _parse_blocks(ocr_results)
    timestamps = [(x, y, t) for x, y, t in blocks if TIME_RE.match(t)]

    members = []
    for ts_x, ts_y, ts_text in timestamps:
        # Name: left side, within 40px of timestamp y
        name_candidates = [
            t for _, _, t in _find_near_y(blocks, ts_y, 40, x_min=90, x_max=500)
            if not _is_skip_label(t)
            and not t.startswith("Guild Member")
            and not TIME_RE.match(t)
            and not PURE_POWER_RE.match(t)
        ]
        if not name_candidates:
            continue
        # Strip leading symbol junk (gender-icon OCR artifacts, decorative
        # brackets). [\W_] is Unicode-aware so it keeps any-script letters —
        # e.g. CJK names like 「ψ」谢霆锋 survive instead of being wiped to ''.
        name = re.sub(r"^[\W_]+", "", name_candidates[0])
        if not name:
            continue
        # Normalize common OCR digit/letter confusion: l→1 and O→0 when surrounded by digits
        name = re.sub(r"(?<=\d)l(?=\d|$)", "1", name)
        name = re.sub(r"(?<=\d)O(?=\d|$)", "0", name)

        # Power / warband / activeness row ~95px below timestamp
        power_row = _find_near_y(blocks, ts_y + 95, 50)

        combat_power = ""
        activeness = 0
        warband = ""

        for x, y, t in power_row:
            if x < 450:
                m = POWER_RE.search(t)
                if m:
                    combat_power = m.group(1) + m.group(2).upper()
            elif x > 600:
                nums = re.findall(r'\d+', t)
                if nums:
                    activeness = int(nums[0])

            # Warband: middle-region text, not power, not a pure number, not a known header/artifact
            if (not warband
                    and 200 <= x <= 680
                    and not POWER_RE.search(t)
                    and not re.match(r'^\d+$', t)
                    and not t.startswith('(')
                    and len(t) >= 2
                    and t not in SKIP_NAMES):
                warband = t

        members.append(Member(
            name=name,
            last_active=ts_text,
            combat_power=combat_power,
            activeness=activeness,
            warband=warband,
        ))

    return members
