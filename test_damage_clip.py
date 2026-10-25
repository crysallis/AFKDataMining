"""Damage scan row filter: own-row card detection and clipped-row dropping.
Run: venv/Scripts/python.exe test_damage_clip.py
Plain-script convention (no pytest in this repo). Synthetic images only.
"""
import sys
from types import SimpleNamespace
sys.path.insert(0, "src")

import numpy as np

from modes.damage_clip import find_own_card, row_card, is_clear, filter_clear_rows

failures = 0
total = 0


def check(desc, actual, expected):
    global failures, total
    total += 1
    ok = actual == expected
    print(f"[{'PASS' if ok else 'FAIL'}] {desc}: got {actual!r}, expected {expected!r}")
    if not ok:
        failures += 1


def frame(*bands):
    img = np.full((1920, 1080, 3), (234, 241, 245), dtype=np.uint8)
    for a, b in bands:
        img[a:b + 1, 20:1060] = (131, 140, 161)
    return img


def near(found, expected, tol=1):
    return found is not None and abs(found[0] - expected[0]) <= tol and abs(found[1] - expected[1]) <= tol


check("find_own_card finds a top-pinned card", near(find_own_card(frame((723, 884))), (723, 884)), True)
check("find_own_card finds a bottom-pinned card", near(find_own_card(frame((1572, 1733))), (1572, 1733)), True)
check("find_own_card finds a card on screen in the middle", near(find_own_card(frame((900, 1062))), (900, 1062)), True)
check("find_own_card returns None with no dark band", find_own_card(frame()), None)
check("find_own_card ignores a band that is too short", find_own_card(frame((900, 959))), None)
check("find_own_card returns None when two plausible bands exist", find_own_card(frame((700, 861), (1000, 1161))), None)

check("row_card is 104 above and 58 below the damage centre", row_card(1004), (900, 1062))

check("is_clear: a card inside the window with no own card", is_clear((726, 888), None), True)
check("is_clear: a card running off the bottom edge", is_clear((1596, 1758), None), False)
check("is_clear: a card cut by the top edge", is_clear((696, 858), (723, 884)), False)
check("is_clear: a card overlapping the own card", is_clear((800, 962), (900, 1062)), False)
check("is_clear: a card starting where the pinned-top card ends", is_clear((885, 1047), (723, 884)), True)
check("is_clear: a card ending where the pinned-bottom card starts", is_clear((1385, 1547), (1572, 1733)), True)
check("is_clear: the own card itself", is_clear((900, 1062), (900, 1062)), True)
check("is_clear: the own card within tolerance", is_clear((902, 1064), (900, 1062)), True)


def rows(*ys):
    return [SimpleNamespace(y=y, name=f"r{y}") for y in ys]


kept, dropped = filter_clear_rows(frame((900, 1062)), rows(827, 1004, 1190))
check("middle state keeps the own row and both neighbours", [r.y for r in kept], [827, 1004, 1190])
check("middle state drops nothing", dropped, 0)

kept, dropped = filter_clear_rows(frame((723, 884)), rows(827, 928, 1110))
check("top-pinned state drops the row hidden under the bar", [r.y for r in kept], [827, 1110])
check("top-pinned state counts the dropped row", dropped, 1)

kept, dropped = filter_clear_rows(frame((1572, 1733)), rows(826, 1012, 1398, 1550))
check("bottom-pinned state keeps rank 1 and drops the row cut by the bar", [r.y for r in kept], [826, 1012, 1398])
check("bottom-pinned state counts the dropped row", dropped, 1)

kept, dropped = filter_clear_rows(frame(), rows(1190, 1700))
check("no own card found: the edge rule still applies and the scan fails open", [r.y for r in kept], [1190])
check("no own card found: the edge-dropped row is counted", dropped, 1)

if failures:
    print(f"\n{failures}/{total} cases failed.")
    sys.exit(1)
print(f"\nAll {total} cases passed.")
