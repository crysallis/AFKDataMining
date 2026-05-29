import re
from dataclasses import dataclass

TIME_RE = re.compile(r"^(\d+[smhd]\s*ago|online)$", re.IGNORECASE)
POWER_RE = re.compile(r"([\d.]+)\s*([KM])", re.IGNORECASE)
SKIP_NAMES = {"Friends", "Guild Announcement", "Me", "Fellowship", "Activeness", "Descending", "Warband"}


@dataclass
class Member:
    name: str
    last_active: str
    combat_power: str
    activeness: int
    warband: str = ''


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
            if t not in SKIP_NAMES
            and not t.startswith("Guild Member")
            and not TIME_RE.match(t)
            and not POWER_RE.search(t)
        ]
        if not name_candidates:
            continue
        # Strip leading non-alphanumeric (gender icon OCR artifacts)
        name = re.sub(r"^[^a-zA-Z0-9]+", "", name_candidates[0])
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
