"""Guild Duel damage scan: period key, weekend window gate, saves and parsing.
Run: venv/Scripts/python.exe test_guild_duel_damage.py
Plain-script convention (no pytest in this repo). DB-backed cases use a temp
database file, never the real guild.db.
"""
import sys
from datetime import datetime
sys.path.insert(0, "src")

from db import (guild_duel_period_key, guild_duel_damage_period_key,
                guild_duel_damage_window_open)

failures = 0
total = 0


def check(desc, actual, expected):
    global failures, total
    total += 1
    ok = actual == expected
    print(f"[{'PASS' if ok else 'FAIL'}] {desc}: got {actual!r}, expected {expected!r}")
    if not ok:
        failures += 1


check("Sat Oct 24 -> this week's Monday", guild_duel_damage_period_key(datetime(2026, 10, 24)), "2026-10-19")
check("Sun Oct 25 23:59:59 -> this week's Monday", guild_duel_damage_period_key(datetime(2026, 10, 25, 23, 59, 59)), "2026-10-19")
check("Mon Oct 26 -> the new week's Monday", guild_duel_damage_period_key(datetime(2026, 10, 26)), "2026-10-26")
check("Battle Report key next Tuesday matches the weekend key",
      guild_duel_period_key(datetime(2026, 10, 27)), guild_duel_damage_period_key(datetime(2026, 10, 24)))

check("Fri 23:59:59 window closed", guild_duel_damage_window_open(datetime(2026, 10, 23, 23, 59, 59)), False)
check("Sat 00:00:00 window open", guild_duel_damage_window_open(datetime(2026, 10, 24, 0, 0, 0)), True)
check("Sun 23:59:59 window open", guild_duel_damage_window_open(datetime(2026, 10, 25, 23, 59, 59)), True)
check("Mon 00:00:00 window closed", guild_duel_damage_window_open(datetime(2026, 10, 26, 0, 0, 0)), False)

if failures:
    print(f"\n{failures}/{total} cases failed.")
    sys.exit(1)
print(f"\nAll {total} cases passed.")
