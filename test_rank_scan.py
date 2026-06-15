"""Stress-test rank detection on the current screen (no scrolling).
Run while the DR rankings list is visible in the game.
Usage: python test_rank_scan.py [N=20]
"""
import sys, time
sys.path.insert(0, "src")

import cv2
from pathlib import Path
from device import screenshot
from modes.common import _scan_rank_column, _clahe
from nav import TEMPLATES_DIR, find_template_all

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20

rank1_ocr = 0
rank1_tmpl = 0

for i in range(N):
    img = screenshot()
    hits = _scan_rank_column(img)
    ocr_ranks = {rv for _, rv in hits}

    via_ocr = 1 in ocr_ranks
    via_tmpl = False

    if not via_ocr:
        tmpl = TEMPLATES_DIR / "rank_1_badge.png"
        if tmpl.exists():
            for _, by in find_template_all(img, tmpl, threshold=0.75):
                if by > 200:   # rough floor — above the list section
                    via_tmpl = True
                    break

    rank1_ocr  += via_ocr
    rank1_tmpl += via_tmpl

    how = "OCR  " if via_ocr else ("TMPL " if via_tmpl else "MISS ")
    top = sorted(ocr_ranks)[:6]
    print(f"  [{i+1:2d}] rank1={how}  ocr ranks: {top}{'...' if len(ocr_ranks) > 6 else ''}")
    time.sleep(0.3)

total = rank1_ocr + rank1_tmpl
print(f"\nRank #1 via OCR:      {rank1_ocr}/{N}")
print(f"Rank #1 via template: {rank1_tmpl}/{N}")
print(f"Total detected:       {total}/{N} ({total/N*100:.0f}%)")
