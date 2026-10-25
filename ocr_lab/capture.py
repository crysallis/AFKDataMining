"""Grab live BlueStacks frames for the OCR lab · never drives the game, you scroll by hand.

    venv/Scripts/python.exe ocr_lab/capture.py --data <dir> [--count 8] [--interval 4]
"""
import argparse
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from device import screenshot  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--count", type=int, default=8)
    ap.add_argument("--interval", type=float, default=4.0)
    args = ap.parse_args()

    frames = Path(args.data) / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    start = len(list(frames.glob("frame_*.png")))
    for i in range(args.count):
        img = screenshot()
        path = frames / f"frame_{start + i:02d}.png"
        cv2.imwrite(str(path), img)
        print(f"saved {path.name} {img.shape[1]}x{img.shape[0]}", flush=True)
        if i < args.count - 1:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
