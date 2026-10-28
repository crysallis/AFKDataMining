"""Tier icons are found on any row background (white rows, tan own-row highlight).
Run: venv/Scripts/python.exe test_tier_background.py
Plain-script convention (no pytest in this repo). Synthetic frames built from the
committed tier templates, so no real screenshot is needed.
"""
import sys
sys.path.insert(0, "src")

import cv2
import numpy as np

from nav import TEMPLATES_DIR
from modes.common import detect_tiers

failures = 0
total = 0

BACKGROUNDS = {"white": (243, 245, 247), "tan": (152, 168, 183)}
ROW_YS = [300, 700, 1100]
ICON_X = 800


def check(name, cond):
    global failures, total
    total += 1
    if not cond:
        failures += 1
        print(f"FAIL: {name}")


def icon_pixels(tier):
    tmpl = cv2.imread(str(TEMPLATES_DIR / "dream_realm" / f"tier_{tier}.png"))
    sat = cv2.cvtColor(tmpl, cv2.COLOR_BGR2HSV)[:, :, 1]
    return tmpl, sat > 90


def frame_with_icon(tier, bg_name):
    frame = np.zeros((1400, 1080, 3), np.uint8)
    frame[:] = BACKGROUNDS[bg_name]
    tmpl, mask = icon_pixels(tier)
    h, w = tmpl.shape[:2]
    for y in ROW_YS:
        region = frame[y:y + h, ICON_X:ICON_X + w]
        region[mask] = tmpl[mask]
    return frame, [y + h // 2 for y in ROW_YS]


for tier in ("epic", "hard", "hell"):
    for bg in BACKGROUNDS:
        frame, centers = frame_with_icon(tier, bg)
        hits = detect_tiers(frame, "dream_realm", threshold=0.75, saturation=True)
        found = sorted(cy for cy, name in hits if name == tier)
        check(f"{tier} on {bg}: every row detected", found == centers)
        check(f"{tier} on {bg}: no other tier reported",
              all(name == tier for _, name in hits))

print(f"{total - failures}/{total} passed")
sys.exit(1 if failures else 0)
