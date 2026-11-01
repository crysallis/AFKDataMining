"""ocr_image must run with the text-angle classifier off.
Run: venv/Scripts/python.exe test_ocr_no_angle_classifier.py
Plain-script convention (no pytest in this repo). Fake engine only, no model or device.

The game UI never renders rotated text, and with the classifier on it flipped a
Clashfronts row ("XIII'th") to 180 degrees and read it as "4.IIX", so the row was
dropped as unmatched. Measured on 29 saved frames: off never lost or changed a real name.
"""
import sys
sys.path.insert(0, "src")

import ocr

failures = 0
total = 0


def check(desc, actual, expected):
    global failures, total
    total += 1
    ok = actual == expected
    print(f"[{'PASS' if ok else 'FAIL'}] {desc}: got {actual!r}, expected {expected!r}")
    if not ok:
        failures += 1


class FakeResult:
    boxes = [[(0, 0), (10, 0), (10, 5), (0, 5)]]
    txts = ["Name"]
    scores = [0.9]


class FakeEngine:
    def __init__(self):
        self.calls = []

    def __call__(self, img, **kwargs):
        self.calls.append(kwargs)
        return FakeResult()


fake = FakeEngine()
ocr._engine_v5 = fake
try:
    import numpy as np
    out = ocr.ocr_image(np.zeros((20, 20, 3), dtype=np.uint8))
finally:
    ocr._engine_v5 = None

check("v5 called once", len(fake.calls), 1)
check("angle classifier is off", fake.calls[0].get("use_cls"), False)
check("results still returned", [t for _, t, _ in out], ["Name"])

print(f"\n{total - failures}/{total} passed")
sys.exit(1 if failures else 0)
