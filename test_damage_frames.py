"""Frame keeper for unmatched damage reads: caps, safe file names, pruning, wiring.
Run: venv/Scripts/python.exe test_damage_frames.py
Plain-script convention (no pytest in this repo). Temp directories only.
"""
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, "src")

import cv2
import numpy as np

from modes.damage_frames import FrameKeeper
from modes.guild_duel_damage import _parse, _state, unmatched_lines, format_damage

failures = 0
total = 0


def check(desc, actual, expected):
    global failures, total
    total += 1
    ok = actual == expected
    print(f"[{'PASS' if ok else 'FAIL'}] {desc}: got {actual!r}, expected {expected!r}")
    if not ok:
        failures += 1


def frame(v=0):
    return np.full((40, 60, 3), v, dtype=np.uint8)


tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
out = Path(tmp.name) / "debug_unmatched"

k = FrameKeeper(out_dir=out, cap=2)
a, b, c = frame(10), frame(20), frame(30)
k.remember(a, "X/Y", 313_000_000_000)
k.remember(a, "Other", 313_000_000_000)
k.remember(b, "Найхэ", 171_000_000_000)
k.remember(c, "Late", 5_000_000_000)
written = k.write_for([("X/Y", 313_000_000_000), ("Other", 313_000_000_000),
                       ("Найхэ", 171_000_000_000), ("Late", 5_000_000_000),
                       ("Never seen", 1)], fmt_damage=format_damage)
check("two entries from one frame count as one frame", ("X/Y", 313_000_000_000) in written and ("Other", 313_000_000_000) in written, True)
check("a third distinct frame past the cap is ignored", ("Late", 5_000_000_000) in written, False)
check("a pair that was never remembered is skipped", ("Never seen", 1) in written, False)

path = written[("X/Y", 313_000_000_000)]
fname = path.split("/")[-1]
check("the returned path names the folder and the file", path.startswith("debug_unmatched/"), True)
check("the file exists and is a readable image", cv2.imread(str(out / fname)) is not None, True)
check("the file name carries the damage value", "313B" in fname, True)
names_in_files = [n for p in written.values() for n in ("Other", "Найхэ", "X/Y", "Late") if n in p.split("/")[-1]]
check("no file name contains a member name", names_in_files, [])
check("each written pair gets its own file", (len(written), len(set(written.values()))), (3, 3))

old = out / "old_frame.png"
cv2.imwrite(str(old), frame(1))
stale = time.time() - 15 * 86400
os.utime(old, (stale, stale))
fresh = out / "fresh_frame.png"
cv2.imwrite(str(fresh), frame(2))
FrameKeeper(out_dir=out, cap=2).write_for([])
check("files older than 14 days are pruned", old.exists(), False)
check("recent files are kept", fresh.exists(), True)

entries = [{"name": "Junk", "damage": 313_000_000_000}, {"name": "Kit", "damage": 171_000_000_000}]
rows = [{"name": "Kit", "damage": 171_000_000_000}]
check("unmatched_lines is unchanged without frames",
      unmatched_lines(entries, rows), ["GUILD_DUEL_DAMAGE: unmatched 'Junk' · saw 313B"])
check("unmatched_lines adds the frame path when one exists",
      unmatched_lines(entries, rows, {("Junk", 313_000_000_000): "debug_unmatched/a.png"}),
      ["GUILD_DUEL_DAMAGE: unmatched 'Junk' · saw 313B · frame debug_unmatched/a.png"])
check("unmatched_lines skips the suffix for pairs with no frame",
      unmatched_lines(entries, rows, {("Other", 1): "debug_unmatched/b.png"}),
      ["GUILD_DUEL_DAMAGE: unmatched 'Junk' · saw 313B"])


def blk(x, y, text):
    return ([[x - 20, y - 10], [x + 20, y - 10], [x + 20, y + 10], [x - 20, y + 10]], text, 0.99)


def card(dy, name, damage):
    return [blk(400, dy - 41, name), blk(945, dy, damage), blk(450, dy + 16, "RKF RiffRaff")]


img = np.full((1920, 1080, 3), (234, 241, 245), dtype=np.uint8)
parse_keeper = FrameKeeper(out_dir=Path(tmp.name) / "parse", cap=5)
_state["keeper"] = parse_keeper
_state["resolver"] = SimpleNamespace(resolve=lambda n: None if n == "Junk" else n)
_parse(img, card(1000, "Junk", "313B") + card(1186, "Kit", "171B"))
got = parse_keeper.write_for([("Junk", 313_000_000_000), ("Kit", 171_000_000_000)], fmt_damage=format_damage)
check("_parse remembers the frame behind an unresolved name", ("Junk", 313_000_000_000) in got, True)
check("_parse does not remember frames for names that resolve", ("Kit", 171_000_000_000) in got, False)
_state["keeper"] = None
_state["resolver"] = None

blocker = Path(tmp.name) / "not_a_folder"
blocker.write_text("x")
broken = FrameKeeper(out_dir=blocker / "sub", cap=2)
broken.remember(frame(5), "Junk", 1)
check("write_for never raises when the folder cannot be created", broken.write_for([("Junk", 1)]), {})

locked_dir = Path(tmp.name) / "locked"
locked_dir.mkdir()
locked = locked_dir / "old.png"
cv2.imwrite(str(locked), frame(1))
os.utime(locked, (stale, stale))
real_unlink = Path.unlink


def deny(self, *a, **k):
    raise PermissionError("file is open elsewhere")


Path.unlink = deny
raised = False
try:
    FrameKeeper(out_dir=locked_dir, cap=2).write_for([])
except Exception:
    raised = True
finally:
    Path.unlink = real_unlink
check("write_for never raises when an old frame cannot be deleted", raised, False)

check("the default folder is anchored at the repo root, not the working directory",
      FrameKeeper().out_dir, Path(__file__).resolve().parent / "debug_unmatched")

if failures:
    print(f"\n{failures}/{total} cases failed.")
    sys.exit(1)
print(f"\nAll {total} cases passed.")
