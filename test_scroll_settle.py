"""Settle-before-reading option on scroll_scan, and the damage swipe constant.
Run: venv/Scripts/python.exe test_scroll_settle.py
Plain-script convention (no pytest in this repo). Fake screenshots and clocks only;
the one DB-touching case uses a temp database, never the real guild.db.
"""
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, "src")

import numpy as np

import db
from device import GUILD_DUEL_DAMAGE_SWIPE
import modes.common as common
from modes.common import wait_until_settled, scroll_scan

failures = 0
total = 0


def check(desc, actual, expected):
    global failures, total
    total += 1
    ok = actual == expected
    print(f"[{'PASS' if ok else 'FAIL'}] {desc}: got {actual!r}, expected {expected!r}")
    if not ok:
        failures += 1


def img(value, outside=None):
    a = np.full((20, 20, 3), value, dtype=np.uint8)
    if outside is not None:
        a[10:, :] = outside
    return a


class Clock:
    def __init__(self):
        self.t = 0.0

    def sleep(self, s):
        self.t += s

    def now(self):
        return self.t


REGION = (slice(0, 10), slice(0, 10))


def feed(frames):
    it = iter(frames)
    calls = {"n": 0}

    def shot():
        calls["n"] += 1
        return next(it)
    return shot, calls


c = Clock()
shot, calls = feed([img(0), img(50), img(100), img(150), img(150)])
last, ok = wait_until_settled(REGION, screenshot_fn=shot, sleep_fn=c.sleep, clock_fn=c.now)
check("settles once two consecutive frames match", ok, True)
check("settle stops polling at the matching frame", calls["n"], 5)
check("settle returns the last frame", int(last[0, 0, 0]), 150)

c = Clock()
shot, calls = feed([img(7), img(7)])
_, ok = wait_until_settled(REGION, screenshot_fn=shot, sleep_fn=c.sleep, clock_fn=c.now)
check("an already still screen settles on the second frame", (ok, calls["n"]), (True, 2))

c = Clock()
counter = {"v": 0}


def always_moving():
    counter["v"] += 40
    return img(counter["v"] % 250)


last, ok = wait_until_settled(REGION, screenshot_fn=always_moving, sleep_fn=c.sleep, clock_fn=c.now)
check("a screen that never settles gives up at the cap and reports it", ok, False)
check("the cap bounds the polling (6s at 0.3s steps)", counter["v"] // 40 <= 23, True)

c = Clock()
shot, calls = feed([img(0, outside=0), img(0, outside=200)])
_, ok = wait_until_settled(REGION, screenshot_fn=shot, sleep_fn=c.sleep, clock_fn=c.now)
check("a change outside the region does not count as motion", (ok, calls["n"]), (True, 2))

check("the damage swipe keeps its tuned values", GUILD_DUEL_DAMAGE_SWIPE, (540, 1450, 540, 890, 2000))

_tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
db.DB_PATH = Path(_tmp.name) / "test_guild.db"
db.init_db()


def run_scan(settle_region, settle_result=None):
    sleeps, settles = [], []
    still = np.zeros((20, 20, 3), dtype=np.uint8)
    saved = (common.screenshot, common.ocr_image, common.time.sleep, common.wait_until_settled)
    common.screenshot = lambda: still
    common.ocr_image = lambda im: []
    common.time.sleep = lambda s: sleeps.append(s)
    common.wait_until_settled = lambda region, **kw: (settles.append(region), settle_result or (still, True))[1]
    try:
        scroll_scan(lambda im, res: [], "test", max_scrolls=1, frames_per_stop=1,
                    scroll_fn=lambda: None, require_new=False, no_change_limit=1,
                    settle_region=settle_region)
    finally:
        common.screenshot, common.ocr_image, common.time.sleep, common.wait_until_settled = saved
    return sleeps, settles


sleeps, settles = run_scan(None)
check("default scroll_scan keeps its fixed 1.2s pause", 1.2 in sleeps, True)
check("default scroll_scan never calls the settle poll", settles, [])

sleeps, settles = run_scan(REGION)
check("with a settle region the 1.2s pause is replaced", 1.2 in sleeps, False)
check("with a settle region the poll runs once per scroll with that region", settles, [REGION])

sleeps, settles = run_scan(REGION, settle_result=(np.zeros((20, 20, 3), dtype=np.uint8), False))
check("a hit settle cap still reads on and ends cleanly", settles, [REGION])

if failures:
    print(f"\n{failures}/{total} cases failed.")
    sys.exit(1)
print(f"\nAll {total} cases passed.")
