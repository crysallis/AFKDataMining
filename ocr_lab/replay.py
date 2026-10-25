"""Replays saved frames through the damage-mode parser with and without the row filter.

    venv/Scripts/python.exe ocr_lab/replay.py --data ocr_lab/data [--frames frame_06.png,frame_07.png]

Prints, per frame, each guild row read and whether the clipped-row filter keeps it.
"""
import argparse
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import ocr  # noqa: E402
from modes.damage_clip import filter_clear_rows, find_own_card, row_card  # noqa: E402
from modes.guild_duel_damage import rows_from_blocks, rows_to_entries, format_damage  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--frames", default=None)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    frames = sorted((Path(args.data) / "frames").glob("frame_*.png"))
    if args.frames:
        wanted = set(args.frames.split(","))
        frames = [f for f in frames if f.name in wanted]
    for path in frames:
        img = cv2.imread(str(path))
        rows = rows_from_blocks(ocr.ocr_image(img))
        kept, dropped = filter_clear_rows(img, rows)
        kept_ids = {id(r) for r in kept}
        print(f"{path.name}  own card {find_own_card(img)}  dropped {dropped}")
        for r in rows:
            entry = rows_to_entries([r])
            label = f"{r.name!r} {format_damage(entry[0]['damage'])}" if entry else f"{r.name!r} (not our guild)"
            print(f"   {'keep' if id(r) in kept_ids else 'DROP'}  dy={r.y} card={row_card(r.y)}  {label}")


if __name__ == "__main__":
    main()
