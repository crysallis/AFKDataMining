"""Parse a user-submitted Supreme Arena screenshot (arbitrary resolution,
aspect ratio, or letterboxing -- e.g. an off-server member's own capture).

Unlike the live ADB scan, this never touches BlueStacks: it OCRs a single
static image, so there's no scroll/vote redundancy and no fixed on-screen
geometry to anchor on. Anchoring is purely relative between OCR blocks
(nearest rank-digit block to each name block, podium excluded by the same
wide-x-spread heuristic parse_rank_rows uses) so it survives resolutions the
live scanner has never been tuned against.
"""
import re

import cv2

from ocr import ocr_image, block_center
from db import resolve_names, save_supreme_arena

RANK_RE = re.compile(r"^\d{1,4}$")
# Trailing server tag glued or spaced onto the name, e.g. "Vantaj S55", "McLagusS55".
TAG_RE = re.compile(r"\s*[sS]\d{2,4}$")


def _podium_y_cutoff(blocks: list[tuple[int, int, str]]) -> int:
    """Same technique as parse_rank_rows: the top-3 podium draws name/tag blocks
    spread wide across x at a shared y-band; real list rows stack narrowly at one
    x. Returns the y just below the lowest such band, or 0 if no podium found."""
    name_like = [(cx, cy) for cx, cy, t in blocks if not RANK_RE.match(t)]
    podium_y = 0
    for cx, cy in name_like:
        band_xs = [ox for ox, oy in name_like if abs(oy - cy) <= 50]
        if len(band_xs) >= 2 and (max(band_xs) - min(band_xs)) > 350:
            podium_y = max(podium_y, cy)
    return podium_y + 40 if podium_y else 0


def parse_manual_rows(img, ocr_results: list) -> list[dict]:
    """[{'rank': int|None, 'name': str}, ...] · one per list row found, top to
    bottom. rank is None if that row's badge never OCR'd (e.g. an ornate rank-1
    crown) -- the row is still kept so the member isn't silently dropped."""
    blocks = []
    for box, text, _ in ocr_results:
        t = text.strip()
        if t:
            cx, cy = block_center(box)
            blocks.append((cx, cy, t))

    list_y_min = _podium_y_cutoff(blocks)

    rank_blocks = [(cx, cy, int(t)) for cx, cy, t in blocks
                   if RANK_RE.match(t) and cy >= list_y_min]
    name_blocks = [(cx, cy, t) for cx, cy, t in blocks
                   if not RANK_RE.match(t) and cy >= list_y_min
                   and "riffraff" not in t.lower().replace(" ", "")]

    rows = []
    used_ranks: set[int] = set()
    for cx, cy, raw in sorted(name_blocks, key=lambda b: b[1]):
        name = TAG_RE.sub("", raw).strip()
        if not name:
            continue
        best_i, best_dy = None, 999999
        for i, (_, rcy, _) in enumerate(rank_blocks):
            if i in used_ranks:
                continue
            dy = abs(rcy - cy)
            if dy < best_dy:
                best_i, best_dy = i, dy
        rank = None
        if best_i is not None and best_dy < 80:
            used_ranks.add(best_i)
            rank = rank_blocks[best_i][2]
        rows.append({"rank": rank, "name": name, "y": cy})

    rows.sort(key=lambda r: r["y"])
    for r in rows:
        del r["y"]
    return rows


def scan_image(path: str) -> dict:
    """Runs OCR + parsing + name resolution + DB save for one screenshot.
    Never creates new members -- unmatched reads are reported, not written,
    so a single garbled frame can't create a bogus roster entry.

    Returns {'saved': [{'name','rank','member_id'}], 'unmatched': [str]}."""
    img = cv2.imread(path)
    if img is None:
        raise ValueError(f"Could not read image: {path}")

    ocr_results = ocr_image(img)
    rows = parse_manual_rows(img, ocr_results)

    resolved, unmatched = resolve_names([r["name"] for r in rows])
    saved_entries = []
    for r in rows:
        member_id = resolved.get(r["name"])
        if member_id is not None:
            saved_entries.append({"member_id": member_id, "rank": r["rank"],
                                   "name": r["name"]})

    save_supreme_arena(
        [{"member_id": e["member_id"], "rank": e["rank"]} for e in saved_entries],
        force=True,
    )

    return {"saved": saved_entries, "unmatched": unmatched}


if __name__ == "__main__":
    import sys
    from db import init_db, names_for_ids

    if len(sys.argv) != 2:
        print("Usage: python manual_scan.py <image_path>")
        sys.exit(1)

    init_db()
    result = scan_image(sys.argv[1])
    canonical = names_for_ids([e["member_id"] for e in result["saved"]])
    for e in result["saved"]:
        name = canonical.get(e["member_id"], e["name"])
        rank_str = e["rank"] if e["rank"] is not None else "?"
        print(f"MANUAL_OK: rank={rank_str} name={name} member_id={e['member_id']}")
    for raw in result["unmatched"]:
        print(f"MANUAL_REVIEW: {raw}")
    print(f"MANUAL_SUMMARY: saved={len(result['saved'])} review={len(result['unmatched'])}")
