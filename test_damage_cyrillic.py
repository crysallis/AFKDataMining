"""Cyrillic second pass for damage-scan names, and the name-core range change.
Run: venv/Scripts/python.exe test_damage_cyrillic.py
Plain-script convention (no pytest in this repo). Fake reader only, no model needed.
"""
import sys
sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np

from modes.damage_cyrillic import cyrillic_ratio, accept_cyrillic, reread_blocks
from db import _normalize_core, _clean_token, _normalized_roster, _normalized_match

failures = 0
total = 0


def check(desc, actual, expected):
    global failures, total
    total += 1
    ok = actual == expected
    print(f"[{'PASS' if ok else 'FAIL'}] {desc}: got {actual!r}, expected {expected!r}")
    if not ok:
        failures += 1


check("ratio of a Cyrillic name is 1.0", cyrillic_ratio("Найхэ"), 1.0)
check("ratio of a Latin name is 0.0", cyrillic_ratio("Kit"), 0.0)
check("ratio of a CJK name is 0.0", cyrillic_ratio("谢霆锋"), 0.0)
check("ratio of an empty string is 0.0", cyrillic_ratio(""), 0.0)

check("accept Найхэ at the measured 0.87", accept_cyrillic("Найхэ", 0.87), True)
check("accept БоссМразей at the measured 0.95", accept_cyrillic("БоссМразей", 0.95), True)
check("reject Cyrillic below the confidence floor", accept_cyrillic("Найхэ", 0.7), False)
check("reject a Latin name even at full confidence", accept_cyrillic("Kit", 1.0), False)
check("reject an empty read", accept_cyrillic("", 0.0), False)
check("reject a mostly Latin read with a stray Cyrillic letter", accept_cyrillic("McШ", 0.95), False)

img = np.zeros((1920, 1080, 3), dtype=np.uint8)


def blk(cx, cy, text, conf=0.9):
    return ([[cx - 20, cy - 10], [cx + 20, cy - 10], [cx + 20, cy + 10], [cx - 20, cy + 10]], text, conf)


seen = []


calls = {"n": 0}
answers = [("Найхэ", 0.87), ("Kit", 0.99), ("", 0.0), ("RKFRiffRaff", 0.97)]


def scripted_reader(crop):
    calls["n"] += 1
    seen.append(crop.shape)
    return answers[calls["n"] - 1]


seen.clear()
blocks = [blk(400, 1000, "Hanxa", 0.89), blk(400, 1186, "Kit", 0.99), blk(400, 1372, "谢霆锋", 1.0),
          blk(450, 1016, "RKF RiffRaff", 0.98), blk(945, 1000, "171B", 0.99)]
out = reread_blocks(img, blocks, (250, 700), 700, reader=scripted_reader)
check("an accepted Cyrillic read replaces the name text", out[0][1], "Найхэ")
check("the replaced block keeps its box and the original confidence", (out[0][0], out[0][2]), (blocks[0][0], 0.89))
check("a Latin result leaves the block untouched", out[1], blocks[1])
check("an empty result (CJK name) leaves the block untouched", out[2], blocks[2])
check("a Latin guild tag is left untouched", out[3], blocks[3])
check("a block outside the name column is never re-read", out[4], blocks[4])
check("only blocks in the name column are re-read", calls["n"], 4)
check("the crop is the box plus 8 px on every side", seen[0], (36, 56, 3))

above = [blk(400, 650, "Podium", 0.99)]
calls["n"] = 0
check("a block above the list is never re-read", reread_blocks(img, above, (250, 700), 700, reader=scripted_reader), above)
check("no reader call happened above the list", calls["n"], 0)

check("a decorated Cyrillic read reduces to its name core", _normalize_core("ψ」Найхэ"), "найхэ")
check("a decorated Latin read still reduces to its name core", _normalize_core("「ψ」Hira"), "hira")
check("a Greek tag glyph is still dropped", _clean_token("ψ"), "")
roster = {"Kit", "谢霆锋", "Найхэ", "МакШейк", "MonkeyAFK", "Scorpion175", "XIII'th", "NaizaB"}
index = _normalized_roster(roster)
check("Cyrillic roster names now have a core in the index", "найхэ" in index and "макшейк" in index, True)
check("a decorated Cyrillic read matches the Cyrillic roster name", _normalized_match("ψ」Найхэ", index), "Найхэ")
check("every existing Latin and CJK roster name still maps to itself",
      sorted(n for n in roster if _normalized_match(n, index) != n), [])

import modes.guild_duel_damage as gdd


def card(dy, name, damage):
    return [blk(400, dy - 41, name, 0.89), blk(945, dy, damage, 0.99), blk(450, dy + 16, "RKF RiffRaff", 0.98)]


def swap_hanxa(im, results, name_x_range, y_min):
    return [(b, "Найхэ" if t == "Hanxa" else t, c) for b, t, c in results]


real_reread = gdd.reread_blocks
gdd.reread_blocks = swap_hanxa
light = np.full((1920, 1080, 3), (234, 241, 245), dtype=np.uint8)
try:
    parsed = gdd._parse(light, card(1000, "Hanxa", "171B"))
finally:
    gdd.reread_blocks = real_reread
check("_parse re-reads names before pairing rows, so the entry carries the Cyrillic name",
      [(e["name"], e["damage"]) for e in parsed], [("Найхэ", 171_000_000_000)])

if failures:
    print(f"\n{failures}/{total} cases failed.")
    sys.exit(1)
print(f"\nAll {total} cases passed.")
