"""Arcane Labyrinth ranking scan · rank, difficulty, floor, points · single
kept scan (overwrite)."""
from db import init_db, save_arcane_lab
from nav import navigate_to_mode, apply_guild_filter
from ocr import block_center
from modes.common import (parse_rank_rows, scroll_scan, resolve_entries,
                           NUM_RE, make_reentry_fn)

_COLS = ("difficulty", "floor", "points")
_COL_TOL = 80


def _column_values(ocr_results, row_y: int) -> dict[str, int]:
    """Map each numeric block in this card's y-band to its column by matching
    the number's x-center to the nearest header label ('Difficulty'/'Floor'/
    'Points') in the same band."""
    headers: dict[str, int] = {}
    numbers: list[tuple[int, int]] = []
    for box, text, _ in ocr_results:
        cx, cy = block_center(box)
        if abs(cy - row_y) > _COL_TOL:
            continue
        t = text.strip()
        low = t.lower()
        if low in _COLS:
            headers[low] = cx
        elif NUM_RE.match(t.replace(",", "")):
            numbers.append((cx, int(t.replace(",", ""))))

    out: dict[str, int] = {}
    if not headers:
        return out
    for cx, val in numbers:
        col = min(headers, key=lambda c: abs(headers[c] - cx))
        if col not in out:
            out[col] = val
    return out


def _parse(img, ocr_results) -> list[dict]:
    out = []
    for row in parse_rank_rows(img, ocr_results):
        cols = _column_values(ocr_results, row.y)
        out.append({"name": row.name, "rank": row.rank,
                    "difficulty": cols.get("difficulty"),
                    "floor": cols.get("floor"),
                    "points": cols.get("points")})
    return out


def scan() -> None:
    init_db()
    navigate_to_mode("arcane_lab")
    if not apply_guild_filter():
        print("ARCANE_LAB: guild filter failed, aborting.")
        return
    entries = scroll_scan(_parse, "Arcane Lab", reentry_fn=make_reentry_fn("ARCANE_LAB"))
    rows = resolve_entries(entries, "Arcane Lab")
    saved = save_arcane_lab(rows)
    print(f"ARCANE_LAB: saved {saved} entries.")
