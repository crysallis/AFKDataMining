"""Guild Duel damage scan: period key, weekend window gate, saves and parsing.
Run: venv/Scripts/python.exe test_guild_duel_damage.py
Plain-script convention (no pytest in this repo). DB-backed cases use a temp
database file, never the real guild.db.
"""
import sys
from datetime import datetime
sys.path.insert(0, "src")

import tempfile
from pathlib import Path

import db
from db import (guild_duel_period_key, guild_duel_damage_period_key,
                guild_duel_damage_window_open, save_guild_duel,
                save_guild_duel_damage, get_guild_duel_period,
                get_guild_duel_missing)

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

_tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
db.DB_PATH = Path(_tmp.name) / "test_guild.db"
db.init_db()
with db._connect() as _conn:
    _conn.execute("CREATE TABLE IF NOT EXISTS warbands (id INTEGER PRIMARY KEY, name TEXT)")


def _member(name):
    with db._connect() as conn:
        mid = conn.execute(
            "INSERT INTO members (ingame_name, first_seen, pending, active) VALUES (?, ?, 0, 1)",
            (name, "2026-01-01")).lastrowid
    return mid


def _row(member_id, period):
    with db._connect() as conn:
        r = conn.execute(
            "SELECT * FROM guild_duel_rankings WHERE member_id = ? AND period_start = ?",
            (member_id, period)).fetchone()
    return dict(r) if r else None


P = "2026-10-19"
aaa, bbb = _member("Aaa"), _member("Bbb")

save_guild_duel_damage([{"member_id": aaa, "damage": 567_000_000_000}], P)
save_guild_duel([{"member_id": aaa, "rank": 3, "crests": 4000}], P)
r = _row(aaa, P)
check("damage then crests keeps damage", r["damage"], 567_000_000_000)
check("damage then crests sets rank and crests", (r["rank"], r["crests"]), (3, 4000))
check("damage_scanned_at is stamped", bool(r["damage_scanned_at"]), True)

save_guild_duel([{"member_id": bbb, "rank": 5, "crests": 3000}], P)
before = _row(bbb, P)
save_guild_duel_damage([{"member_id": bbb, "damage": 100_000_000_000}], P)
after = _row(bbb, P)
check("crests then damage keeps rank, crests and scanned_at",
      (after["rank"], after["crests"], after["scanned_at"]),
      (before["rank"], before["crests"], before["scanned_at"]))
check("crests then damage sets damage", after["damage"], 100_000_000_000)

save_guild_duel_damage([{"member_id": bbb, "damage": 200_000_000_000}], P)
again = _row(bbb, P)
check("second damage save overwrites only damage",
      (again["damage"], again["crests"], again["scanned_at"]),
      (200_000_000_000, before["crests"], before["scanned_at"]))

P2 = "2026-10-26"
real_utcnow = db._utcnow
db._utcnow = lambda: datetime(2026, 11, 3)
save_guild_duel_damage([{"member_id": aaa, "damage": 1_000_000_000}], P2)
check("damage-only week does not block the Monday scan", get_guild_duel_period(), P2)
check("damage-only member is still listed missing for crests",
      get_guild_duel_missing(P2), ["Aaa", "Bbb"])
save_guild_duel([{"member_id": aaa, "rank": 1, "crests": 5000}], P2)
check("a week with crests is scanned", get_guild_duel_period(), None)
check("force bypasses the scanned gate", get_guild_duel_period(force=True), P2)
check("member with crests is no longer missing", get_guild_duel_missing(P2), ["Bbb"])
db._utcnow = real_utcnow

from modes.common import RankRow
from modes.guild_duel_damage import parse_damage, rows_to_entries

for text, expected in [
    ("567B", 567_000_000_000),
    ("1.2T", 1_200_000_000_000),
    ("850M", 850_000_000),
    ("12,345K", 12_345_000),
    (" 567 b ", 567_000_000_000),
    ("567", None),
    ("Total Damage", None),
    ("", None),
    ("B", None),
]:
    check(f"parse_damage({text!r})", parse_damage(text), expected)

ours = RankRow(rank=2, name="Aaa", texts=["Total Damage", "567B", "RKF RiffRaff"], y=900)
theirs = RankRow(rank=1, name="Zzz", texts=["Total Damage", "999B", "OtherGuild"], y=800)
pinned = RankRow(rank=None, name="Bbb", texts=["Unranked", "RKF RiffRaff"], y=1650)
garbled = RankRow(rank=7, name="Ccc", texts=["Total Damage", "5678", "RKF RiffRaff"], y=1300)
check("rows_to_entries keeps our guild rows, drops the opponent, the pinned bar and unparseable damage",
      rows_to_entries([theirs, ours, pinned, garbled]),
      [{"name": "Aaa", "damage": 567_000_000_000}])

from modes.guild_duel_damage import rows_from_blocks


def blk(x, y, text):
    return ([[x - 20, y - 10], [x + 20, y - 10], [x + 20, y + 10], [x - 20, y + 10]], text, 0.99)


top_of_list = [
    blk(300, 500, "Podium"), blk(900, 520, "999B"),
    blk(940, 773, "Total Damage"), blk(369, 785, "Aaa"), blk(947, 826, "567B"), blk(448, 842, "RKF RiffRaff"),
    blk(99, 989, "2"), blk(394, 971, "Zzz"), blk(945, 959, "Total Damage"), blk(948, 1012, "515B"), blk(425, 1028, "OtherGuild"),
    blk(365, 1159, "Bbb"), blk(941, 1144, "Total Damage"), blk(947, 1198, "274D"), blk(424, 1213, "RKF RiffRaff"),
]
check("rows_from_blocks pairs names with damage by position and needs no rank glyph (rank 1 has none)",
      [r.name for r in rows_from_blocks(top_of_list)], ["Aaa", "Zzz"])
check("rank-1 row survives the whole pipeline while the opponent and unparseable rows drop",
      rows_to_entries(rows_from_blocks(top_of_list)), [{"name": "Aaa", "damage": 567_000_000_000}])

under_the_bar = [
    blk(369, 1531, "Bjies"), blk(947, 1560, "274D"),
    blk(369, 1636, "Ccc"), blk(947, 1677, "17581M"), blk(448, 1692, "RKF RiffRaff"),
]
check("the pinned bar keeps its own damage and is not mixed with the row it covers",
      rows_to_entries(rows_from_blocks(under_the_bar)), [{"name": "Ccc", "damage": 17_581_000_000}])

from modes.guild_duel_damage import format_damage, unmatched_lines

for value, expected in [(567_000_000_000, "567B"), (17_581_000_000, "17.581B"),
                        (850_000_000, "850M"), (1_200_000_000_000, "1.2T"), (999, "999")]:
    check(f"format_damage({value})", format_damage(value), expected)

check("unmatched_lines shows the name and the damage seen for each unmatched entry",
      unmatched_lines(
          [{"name": "Aaa", "damage": 567_000_000_000}, {"name": "aliiaj", "damage": 365_000_000_000}],
          [{"name": "Aaa", "damage": 567_000_000_000, "member_id": 1}]),
      ["GUILD_DUEL_DAMAGE: unmatched 'aliiaj' · saw 365B"])
check("unmatched_lines is empty when every entry matched",
      unmatched_lines([{"name": "Aaa", "damage": 1}], [{"name": "Aaa", "damage": 1, "member_id": 1}]), [])

from scraper import force_for_mode

check("--force-rescan forces guild_duel", force_for_mode("guild_duel", True, False), True)
check("--force-damage alone does not force guild_duel", force_for_mode("guild_duel", False, True), False)
check("--force-rescan does NOT open the damage window", force_for_mode("guild_duel_damage", True, False), False)
check("--force-damage forces the damage scan", force_for_mode("guild_duel_damage", False, True), True)
check("no force flag reaches other modes", force_for_mode("arena", True, True), False)

from db import count_guild_duel_damage
from modes.guild_duel_damage import save_gate

P3 = "2026-11-02"
ccc, ddd, eee = _member("Ccc"), _member("Ddd"), _member("Eee")
check("count_guild_duel_damage is 0 for a week with no rows", count_guild_duel_damage(P3), 0)
save_guild_duel_damage([{"member_id": ccc, "damage": 5_000_000_000},
                        {"member_id": ddd, "damage": 6_000_000_000}], P3)
save_guild_duel([{"member_id": eee, "rank": 9, "crests": 1000}], P3)
check("count_guild_duel_damage counts damage rows only, not crests-only rows", count_guild_duel_damage(P3), 2)
check("count_guild_duel_damage is per week", count_guild_duel_damage("2026-10-19"), 2)

check("save_gate refuses to save when the window closed during the scan",
      save_gate(False, 71, 69),
      (False, ["GUILD_DUEL_DAMAGE: window closed during the scan, not saving."]))
check("save_gate saves quietly when the count is not lower", save_gate(True, 71, 69), (True, []))
check("save_gate saves quietly on the first scan of a week", save_gate(True, 60, 0), (True, []))
check("save_gate warns when fewer members were saved than before",
      save_gate(True, 60, 69),
      (True, ["GUILD_DUEL_DAMAGE: warning, saved 60 members, previous scan saved 69."]))

import io
from contextlib import redirect_stdout
import modes.guild_duel_damage as gdd

fff = _member("Fff")
P4 = gdd.guild_duel_damage_period_key(gdd._utcnow())
gdd.navigate_to_guild_duel_merit_rankings = lambda: None
gdd.scroll_scan = lambda *a, **k: [{"name": "Fff", "damage": 9_000_000_000}]


def run_scan(window_calls):
    seq = iter(window_calls)
    real_open = gdd.guild_duel_damage_window_open
    gdd.guild_duel_damage_window_open = lambda now: next(seq)
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            result = gdd.scan()
    finally:
        gdd.guild_duel_damage_window_open = real_open
    return result, buf.getvalue()


base = count_guild_duel_damage(P4)
result, out = run_scan([True, True])
check("scan inside the window saves the read member", (result, count_guild_duel_damage(P4)), ("success", base + 1))
check("scan prints how many row reads the filter skipped", "skipped 0 row reads hidden" in out, True)

with db._connect() as conn:
    conn.execute("DELETE FROM guild_duel_rankings WHERE member_id = ? AND period_start = ?", (fff, P4))
result, out = run_scan([True, False])
check("scan that crossed the reset refuses to save", (result, count_guild_duel_damage(P4)), ("skipped", base))
check("scan that crossed the reset says so", "window closed during the scan, not saving" in out, True)

if failures:
    print(f"\n{failures}/{total} cases failed.")
    sys.exit(1)
print(f"\nAll {total} cases passed.")
