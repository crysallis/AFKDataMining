"""Assert guild_duel_period_key's Monday-boundary math against known dates.
Run: venv/Scripts/python.exe test_guild_duel_period.py
No pytest in this repo (nav/OCR logic is verified live) · this is the one
exception, since date-math boundaries can't be observed by running the
scraper once on one real day.
"""
import sys
from datetime import datetime
sys.path.insert(0, "src")

from db import guild_duel_period_key

cases = [
    # (now, expected_period_start, description)
    (datetime(2026, 7, 20), "2026-07-13", "Mon Jul 20 -> Jul 13 (that day's own Monday)"),
    (datetime(2026, 7, 14), "2026-07-06", "Tue Jul 14 -> Jul 6 (matches live Battle Report capture)"),
    (datetime(2026, 7, 19), "2026-07-06", "Sun Jul 19 -> Jul 6 (still last week, right before rollover)"),
    (datetime(2026, 7, 20, 0, 0, 1), "2026-07-13", "Mon Jul 20 00:00:01 -> Jul 13 (just after rollover)"),
    (datetime(2026, 7, 17), "2026-07-06", "Fri Jul 17 -> Jul 6 (mid-week, current week Jul 13-19 not finished yet)"),
]

failures = 0
for now, expected, desc in cases:
    actual = guild_duel_period_key(now)
    ok = actual == expected
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {desc}: got {actual}, expected {expected}")
    if not ok:
        failures += 1

if failures:
    print(f"\n{failures}/{len(cases)} cases failed.")
    sys.exit(1)
print(f"\nAll {len(cases)} cases passed.")
