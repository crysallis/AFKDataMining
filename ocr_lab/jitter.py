"""Stress test · shifts one frame vertically (a stand-in for the scan's varying scroll
offsets) and prints how each variant reads the rows at every offset.

    venv/Scripts/python.exe ocr_lab/jitter.py --data <dir> --frame frame_04.png --variants prod,cyrillic
"""
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lab import VARIANTS, name_conf, run_ocr, RosterResolver, rows_from_blocks, rows_to_entries  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--frame", required=True)
    ap.add_argument("--variants", default="prod,cyrillic")
    ap.add_argument("--offsets", default="-60,-45,-30,-15,0,15,30,45,60")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    img = cv2.imread(str(Path(args.data) / "frames" / args.frame))
    resolver = RosterResolver(learn=False)
    h, w = img.shape[:2]
    for vname in args.variants.split(","):
        spec, pre, scale = VARIANTS[vname]
        print(f"== {vname}")
        for dy in map(int, args.offsets.split(",")):
            M = np.float32([[1, 0, 0], [0, 1, dy]])
            shifted = cv2.warpAffine(img, M, (w, h), borderMode=cv2.BORDER_REPLICATE)
            res = run_ocr(shifted, spec, pre, scale)
            cells = []
            for e in rows_to_entries(rows_from_blocks(res)):
                c = name_conf(res, e["name"])
                m = resolver.resolve(e["name"])
                cells.append(f"{e['damage'] // 10**9}B {e['name']!r} {c:.2f}{'' if m else ' UNMATCHED'}")
            print(f"  dy={dy:+4d}  " + " | ".join(cells))


if __name__ == "__main__":
    main()
