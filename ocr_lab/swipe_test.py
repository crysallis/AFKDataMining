"""Measures how long the Guild Duel Damage list keeps moving after the scan's own swipe.

Runs the real guild_duel_damage_scroll_down(), then screenshots back to back and reports,
per frame: seconds since the swipe returned, pixel change vs the previous frame (list
region only), and each row's name / vertical position / OCR confidence. An idle baseline
is taken first so animated avatars aren't mistaken for motion.

    venv/Scripts/python.exe ocr_lab/swipe_test.py --data <dir> [--frames 10]
"""
import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from device import guild_duel_damage_scroll_down, screenshot  # noqa: E402
from lab import name_conf, run_ocr, rows_from_blocks, rows_to_entries  # noqa: E402

LIST = (slice(725, 1560), slice(0, 1080))


def region_diff(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(np.abs(a[LIST].astype(np.int16) - b[LIST].astype(np.int16))))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--frames", type=int, default=10)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    out = Path(args.data) / "swipe_frames"
    out.mkdir(parents=True, exist_ok=True)

    idle = [screenshot() for _ in range(4)]
    base = [region_diff(idle[i], idle[i + 1]) for i in range(3)]
    print(f"idle baseline diff between consecutive frames: {[round(x, 2) for x in base]}")

    guild_duel_damage_scroll_down()
    t0 = time.monotonic()
    shots = []
    for _ in range(args.frames):
        img = screenshot()
        shots.append((time.monotonic() - t0, img))

    print(f"\n{'t(s)':>6} {'diff':>6}   rows read (name @y conf)")
    prev = None
    for i, (t, img) in enumerate(shots):
        cv2.imwrite(str(out / f"swipe_{i:02d}.png"), img)
        d = region_diff(prev, img) if prev is not None else float("nan")
        res = run_ocr(img, None, "prod", 1.0)
        cells = []
        for row in rows_from_blocks(res):
            c = name_conf(res, row.name)
            cells.append(f"{row.name}@{row.y}" + (f" {c:.2f}" if c is not None else ""))
        print(f"{t:6.2f} {d:6.2f}   " + " | ".join(cells), flush=True)
        prev = img


if __name__ == "__main__":
    main()
