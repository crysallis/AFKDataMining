import re
from dataclasses import dataclass
from difflib import SequenceMatcher

TIME_RE = re.compile(r"^(\d*[smhd]\s*ago|online)$", re.IGNORECASE)

def _norm_time(t: str) -> str:
    """Fix common OCR digit/letter confusion in timestamps: 'l' and 'I' are
    often read instead of '1' (e.g. 'lh ago', 'Id ago' → '1h ago', '1d ago')."""
    return re.sub(r'^[lI](?=[smhd])', '1', t, flags=re.IGNORECASE)
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


def _clean_name(t: str) -> str:
    """Strip leading symbol junk (gender-icon OCR artifacts, decorative brackets)
    and fix digit/letter confusion. [\\W_] is Unicode-aware so it keeps any-script
    letters — CJK names like 「ψ」谢霆锋 survive instead of being wiped to ''.
    Returns '' if nothing usable remains (e.g. an icon that OCR'd as its own block)."""
    name = re.sub(r"^[\W_]+", "", t)
    if not name:
        return ""
    # Normalize common OCR digit/letter confusion: l→1 and O→0 when surrounded by digits
    name = re.sub(r"(?<=\d)l(?=\d|$)", "1", name)
    name = re.sub(r"(?<=\d)O(?=\d|$)", "0", name)
    return name


def _is_name_block(t: str) -> bool:
    """True for a left-region text block that could be a member name · excludes
    the UI header, timestamps, power values, and known emblem/UI labels."""
    return (not _is_skip_label(t)
            and not t.startswith("Guild Member")
            and not TIME_RE.match(_norm_time(t))
            and not PURE_POWER_RE.match(t))


def parse_member_anchors(ocr_results: list) -> list[tuple[str, int]]:
    """(name, y) per visible member card · anchors on the NAME block itself (left
    region) rather than the last-active timestamp. The capture pass only needs
    name + y to tap the far-left avatar, so it must not depend on the timestamp:
    a row whose time OCRs as e.g. '2lh ago' (1 read as l) fails TIME_RE and would
    otherwise yield no anchor. Names are returned raw · the caller resolves them."""
    blocks = _parse_blocks(ocr_results)
    anchors = []
    for x, y, t in blocks:
        if not (90 <= x <= 500) or not _is_name_block(t):
            continue
        name = _clean_name(t)
        if name:
            anchors.append((name, y))
    return anchors


def parse_members(ocr_results: list) -> list[Member]:
    """Parse guild member cards, anchored on the NAME. A member's identity — the
    fact they're in the list — is the primary signal; last_active, power, warband
    and activeness are secondary attributes located relative to the name. Anchoring
    on the name (not the last-active timestamp) means a garbled time ('2lh ago')
    no longer drops an otherwise-readable member · last_active just falls back to
    'Unknown' (nulled at save time). A name block is confirmed a real card only if
    a secondary signal (power or timestamp) sits with it, so header text / a power
    value misread with a leading letter never become phantom members."""
    blocks = _parse_blocks(ocr_results)

    members = []
    for nx, ny, nt in blocks:
        if not (90 <= nx <= 500) or not _is_name_block(nt):
            continue
        name = _clean_name(nt)
        if not name:
            continue

        # last_active: a time-like block on the same row, right side (optional)
        last_active = "Unknown"
        for _, _, t in _find_near_y(blocks, ny, 40, x_min=600):
            normt = _norm_time(t)
            if TIME_RE.match(normt):
                last_active = normt
                break

        # Power / warband / activeness row ~95px below the name row
        power_row = _find_near_y(blocks, ny + 95, 50)
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

        # Confirm it's a real card: a name with neither power nor timestamp nearby
        # is OCR noise (header line, leading-letter power misread) — never a member.
        if not combat_power and last_active == "Unknown":
            continue

        members.append(Member(
            name=name,
            last_active=last_active,
            combat_power=combat_power,
            activeness=activeness,
            warband=warband,
        ))

    return members
