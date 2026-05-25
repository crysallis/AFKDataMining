"""
One-time import of historical guild data.
Clears all existing snapshots then inserts 10 historical scans.
Run BEFORE doing a fresh /scan to build chart history.

    venv\Scripts\python.exe src\import_history.py
"""
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "guild.db"

# (name, combat_power_value, date MM/DD/YYYY)
RAW = [
    ("Lilliana",     86329, "10/10/2025"),
    ("PersianQueen", 68360, "10/10/2025"),
    ("Royalmus",     73516, "10/10/2025"),
    ("Simmysazn",    70711, "10/10/2025"),
    ("Aiderian",     74586, "10/10/2025"),
    ("Binky",        72791, "10/10/2025"),
    ("Caernafon",    84307, "10/10/2025"),
    ("Carbon",       79124, "10/10/2025"),
    ("Crysallis",    79034, "10/10/2025"),
    ("Hellboy",      71031, "10/10/2025"),
    ("Kit",          72035, "10/10/2025"),
    ("Krazy",        69182, "10/10/2025"),
    ("Liam",         80244, "10/10/2025"),
    ("MonkeyAFK",    67929, "10/10/2025"),
    ("Newbie",       67970, "10/10/2025"),
    ("Nightpyre",    77612, "10/10/2025"),
    ("OniEliteZ",    73248, "10/10/2025"),
    ("Onuh",         79034, "10/10/2025"),
    ("Sandokai",     66157, "10/10/2025"),
    ("TashaaMaree",  72278, "10/10/2025"),
    ("Tatsuya",      69415, "10/10/2025"),
    ("UCQ",          71021, "10/10/2025"),
    ("Unbound",      75708, "10/10/2025"),
    ("Wanderer",     66365, "10/10/2025"),
    ("Yuegui",       71237, "10/10/2025"),

    ("Lilliana",     90570, "11/20/2025"),
    ("PersianQueen", 71843, "11/20/2025"),
    ("Royalmus",     76919, "11/20/2025"),
    ("Simmysazn",    75042, "11/20/2025"),
    ("Aiderian",     77799, "11/20/2025"),
    ("Binky",        76747, "11/20/2025"),
    ("Caernafon",    88302, "11/20/2025"),
    ("Carbon",       82635, "11/20/2025"),
    ("ChaShao",      77543, "11/20/2025"),
    ("Crysallis",    82084, "11/20/2025"),
    ("Hellboy",      74693, "11/20/2025"),
    ("Kit",          75254, "11/20/2025"),
    ("Krazy",        73669, "11/20/2025"),
    ("Liam",         83942, "11/20/2025"),
    ("Lilith",       73618, "11/20/2025"),
    ("MonkeyAFK",    70193, "11/20/2025"),
    ("Newbie",       72845, "11/20/2025"),
    ("Nightpyre",    81570, "11/20/2025"),
    ("OniEliteZ",    76088, "11/20/2025"),
    ("Onuh",         82331, "11/20/2025"),
    ("Sandokai",     70363, "11/20/2025"),
    ("TashaaMaree",  75160, "11/20/2025"),
    ("Tatsuya",      73000, "11/20/2025"),
    ("UCQ",          74862, "11/20/2025"),
    ("Unbound",      78214, "11/20/2025"),
    ("Wanderer",     69334, "11/20/2025"),
    ("Yuegui",       75105, "11/20/2025"),

    ("Lilliana",     93781, "12/11/2025"),
    ("PersianQueen", 74241, "12/11/2025"),
    ("Royalmus",     78950, "12/11/2025"),
    ("Simmysazn",    76681, "12/11/2025"),
    ("Aiderian",     79748, "12/11/2025"),
    ("Binky",        80071, "12/11/2025"),
    ("Caernafon",    90972, "12/11/2025"),
    ("Carbon",       84947, "12/11/2025"),
    ("ChaShao",      79468, "12/11/2025"),
    ("Crysallis",    84609, "12/11/2025"),
    ("Hellboy",      77151, "12/11/2025"),
    ("Iseryn",       79370, "12/11/2025"),
    ("Kit",          77568, "12/11/2025"),
    ("Krazy",        75553, "12/11/2025"),
    ("Liam",         85962, "12/11/2025"),
    ("Lilith",       75766, "12/11/2025"),
    ("MonkeyAFK",    72619, "12/11/2025"),
    ("Newbie",       74596, "12/11/2025"),
    ("Nightpyre",    83539, "12/11/2025"),
    ("OniEliteZ",    77908, "12/11/2025"),
    ("Onuh",         84308, "12/11/2025"),
    ("Sandokai",     72649, "12/11/2025"),
    ("TashaaMaree",  77207, "12/11/2025"),
    ("Tatsuya",      75906, "12/11/2025"),
    ("UCQ",          78286, "12/11/2025"),
    ("Unbound",      79473, "12/11/2025"),
    ("Wanderer",     73191, "12/11/2025"),
    ("Yuegui",       77328, "12/11/2025"),

    ("Lilliana",     95272, "12/25/2025"),
    ("PersianQueen", 75465, "12/25/2025"),
    ("Royalmus",     80603, "12/25/2025"),
    ("Simmysazn",    78430, "12/25/2025"),
    ("Aiderian",     80794, "12/25/2025"),
    ("Binky",        81322, "12/25/2025"),
    ("Caernafon",    92200, "12/25/2025"),
    ("Carbon",       85826, "12/25/2025"),
    ("ChaShao",      80786, "12/25/2025"),
    ("Crysallis",    85826, "12/25/2025"),
    ("Garahel",      74940, "12/25/2025"),
    ("Hellboy",      78012, "12/25/2025"),
    ("Iseryn",       80704, "12/25/2025"),
    ("Kit",          78264, "12/25/2025"),
    ("Krazy",        77742, "12/25/2025"),
    ("Liam",         87954, "12/25/2025"),
    ("Lilith",       77430, "12/25/2025"),
    ("MonkeyAFK",    73539, "12/25/2025"),
    ("Newbie",       76333, "12/25/2025"),
    ("Nightpyre",    84590, "12/25/2025"),
    ("OniEliteZ",    79709, "12/25/2025"),
    ("Onuh",         85399, "12/25/2025"),
    ("Sandokai",     74312, "12/25/2025"),
    ("TashaaMaree",  77852, "12/25/2025"),
    ("Tatsuya",      77210, "12/25/2025"),
    ("UCQ",          79722, "12/25/2025"),
    ("Unbound",      80603, "12/25/2025"),
    ("Wanderer",     75572, "12/25/2025"),
    ("Yuegui",       78782, "12/25/2025"),

    ("Lilliana",    100000, "2/6/2026"),
    ("PersianQueen", 79412, "2/6/2026"),
    ("Royalmus",     84035, "2/6/2026"),
    ("Simmysazn",    81006, "2/6/2026"),
    ("Aiderian",     85162, "2/6/2026"),
    ("Binky",        85275, "2/6/2026"),
    ("Caernafon",    98189, "2/6/2026"),
    ("Carbon",       91103, "2/6/2026"),
    ("ChaShao",      85818, "2/6/2026"),
    ("Crysallis",    91064, "2/6/2026"),
    ("Garahel",      80898, "2/6/2026"),
    ("Hellboy",      80819, "2/6/2026"),
    ("Iseryn",       85217, "2/6/2026"),
    ("Jinbei",       73867, "2/6/2026"),
    ("Kit",          83487, "2/6/2026"),
    ("Krazy",        81763, "2/6/2026"),
    ("Liam",         92691, "2/6/2026"),
    ("Lilith",       80075, "2/6/2026"),
    ("MonkeyAFK",    76490, "2/6/2026"),
    ("Newbie",       80900, "2/6/2026"),
    ("Nightpyre",    89273, "2/6/2026"),
    ("OniEliteZ",    83517, "2/6/2026"),
    ("Onuh",         89369, "2/6/2026"),
    ("Sandokai",     78601, "2/6/2026"),
    ("TashaaMaree",  82766, "2/6/2026"),
    ("Tatsuya",      81442, "2/6/2026"),
    ("UCQ",          83984, "2/6/2026"),
    ("Unbound",      86311, "2/6/2026"),
    ("Wanderer",     79961, "2/6/2026"),
    ("Yuegui",       83433, "2/6/2026"),

    ("Lilliana",    101000, "2/20/2026"),
    ("Royalmus",     85087, "2/20/2026"),
    ("Simmysazn",    83379, "2/20/2026"),
    ("PersianQueen", 81027, "2/20/2026"),
    ("Caernafon",    99829, "2/20/2026"),
    ("Liam",         94044, "2/20/2026"),
    ("Carbon",       92642, "2/20/2026"),
    ("Crysallis",    92580, "2/20/2026"),
    ("Onuh",         90900, "2/20/2026"),
    ("Nightpyre",    90636, "2/20/2026"),
    ("Unbound",      87651, "2/20/2026"),
    ("ChaShao",      87355, "2/20/2026"),
    ("Iseryn",       86974, "2/20/2026"),
    ("Aiderian",     86895, "2/20/2026"),
    ("Yuegui",       85945, "2/20/2026"),
    ("Binky",        85625, "2/20/2026"),
    ("Kit",          84971, "2/20/2026"),
    ("OniEliteZ",    84808, "2/20/2026"),
    ("UCQ",          84519, "2/20/2026"),
    ("TashaaMaree",  84338, "2/20/2026"),
    ("Krazy",        83304, "2/20/2026"),
    ("Tatsuya",      82334, "2/20/2026"),
    ("Hellboy",      82218, "2/20/2026"),
    ("Garahel",      81940, "2/20/2026"),
    ("Newbie",       81831, "2/20/2026"),
    ("Wanderer",     81433, "2/20/2026"),
    ("Lilith",       80591, "2/20/2026"),
    ("Sandokai",     79860, "2/20/2026"),
    ("MonkeyAFK",    77963, "2/20/2026"),
    ("Jinbei",       77432, "2/20/2026"),

    ("Lilliana",    102000, "3/13/2026"),
    ("Royalmus",     86890, "3/13/2026"),
    ("Simmysazn",    84656, "3/13/2026"),
    ("PersianQueen", 81240, "3/13/2026"),
    ("Caernafon",   102000, "3/13/2026"),
    ("Liam",         95468, "3/13/2026"),
    ("Crysallis",    94178, "3/13/2026"),
    ("Carbon",       93920, "3/13/2026"),
    ("Onuh",         92489, "3/13/2026"),
    ("Nightpyre",    91964, "3/13/2026"),
    ("ChaShao",      88956, "3/13/2026"),
    ("Unbound",      88755, "3/13/2026"),
    ("Iseryn",       88410, "3/13/2026"),
    ("Aiderian",     88005, "3/13/2026"),
    ("Binky",        87794, "3/13/2026"),
    ("Yuegui",       87466, "3/13/2026"),
    ("Kit",          86612, "3/13/2026"),
    ("UCQ",          86521, "3/13/2026"),
    ("TashaaMaree",  86280, "3/13/2026"),
    ("OniEliteZ",    85682, "3/13/2026"),
    ("Krazy",        84370, "3/13/2026"),
    ("Tatsuya",      84298, "3/13/2026"),
    ("Hellboy",      83723, "3/13/2026"),
    ("Garahel",      82828, "3/13/2026"),
    ("Newbie",       82635, "3/13/2026"),
    ("Wanderer",     82633, "3/13/2026"),
    ("Lilith",       82578, "3/13/2026"),
    ("Sandokai",     81140, "3/13/2026"),
    ("Jinbei",       80706, "3/13/2026"),
    ("MonkeyAFK",    79209, "3/13/2026"),

    ("Lilliana",    102000, "3/26/2026"),
    ("Simmysazn",    85003, "3/26/2026"),
    ("Caernafon",   103000, "3/26/2026"),
    ("Liam",         96487, "3/26/2026"),
    ("Crysallis",    95822, "3/26/2026"),
    ("Carbon",       95227, "3/26/2026"),
    ("Nightpyre",    93565, "3/26/2026"),
    ("Onuh",         93520, "3/26/2026"),
    ("ChaShao",      90076, "3/26/2026"),
    ("Iseryn",       89828, "3/26/2026"),
    ("Aiderian",     89581, "3/26/2026"),
    ("Unbound",      89206, "3/26/2026"),
    ("Yuegui",       88905, "3/26/2026"),
    ("Binky",        88275, "3/26/2026"),
    ("UCQ",          87692, "3/26/2026"),
    ("TashaaMaree",  87689, "3/26/2026"),
    ("Kit",          87663, "3/26/2026"),
    ("OniEliteZ",    86479, "3/26/2026"),
    ("Tatsuya",      85185, "3/26/2026"),
    ("Krazy",        84950, "3/26/2026"),
    ("Newbie",       84115, "3/26/2026"),
    ("Garahel",      84101, "3/26/2026"),
    ("Hellboy",      83974, "3/26/2026"),
    ("Wanderer",     82987, "3/26/2026"),
    ("Lilith",       82887, "3/26/2026"),
    ("Jinbei",       82623, "3/26/2026"),
    ("Goldlock101",  82597, "3/26/2026"),
    ("Sandokai",     81571, "3/26/2026"),

    ("Lilliana",    103000, "4/16/2026"),
    ("Caernafon",   105000, "4/16/2026"),
    ("Vantaj",       99500, "4/16/2026"),
    ("Liam",         98422, "4/16/2026"),
    ("Crysallis",    97633, "4/16/2026"),
    ("Carbon",       97220, "4/16/2026"),
    ("Nightpyre",    96264, "4/16/2026"),
    ("Onuh",         94613, "4/16/2026"),
    ("Unbound",      91393, "4/16/2026"),
    ("Iseryn",       91197, "4/16/2026"),
    ("Aiderian",     90986, "4/16/2026"),
    ("Yuegui",       90882, "4/16/2026"),
    ("ChaShao",      90852, "4/16/2026"),
    ("Binky",        90263, "4/16/2026"),
    ("Kit",          89690, "4/16/2026"),
    ("TashaaMaree",  89455, "4/16/2026"),
    ("UCQ",          88674, "4/16/2026"),
    ("OniEliteZ",    87526, "4/16/2026"),
    ("Krazy",        86646, "4/16/2026"),
    ("Tatsuya",      86539, "4/16/2026"),
    ("Newbie",       85888, "4/16/2026"),
    ("Garahel",      85713, "4/16/2026"),
    ("Hellboy",      85349, "4/16/2026"),
    ("Lilith",       85156, "4/16/2026"),
    ("Jinbei",       84407, "4/16/2026"),
    ("Goldlock101",  83798, "4/16/2026"),
    ("Wanderer",     83138, "4/16/2026"),
    ("Sandokai",     82890, "4/16/2026"),
    ("MonkeyAFK",    81929, "4/16/2026"),
    ("Palo",         73494, "4/16/2026"),

    ("Caernafon",   105000, "5/3/2026"),
    ("Vantaj",      100000, "5/3/2026"),
    ("Liam",         98709, "5/3/2026"),
    ("Crysallis",    98229, "5/3/2026"),
    ("Carbon",       97605, "5/3/2026"),
    ("Nightpyre",    96926, "5/3/2026"),
    ("Onuh",         95203, "5/3/2026"),
    ("Unbound",      92045, "5/3/2026"),
    ("Iseryn",       91674, "5/3/2026"),
    ("Aiderian",     91633, "5/3/2026"),
    ("Yuegui",       91300, "5/3/2026"),
    ("ChaShao",      91065, "5/3/2026"),
    ("Binky",        90766, "5/3/2026"),
    ("TashaaMaree",  90683, "5/3/2026"),
    ("Kit",          90658, "5/3/2026"),
    ("UCQ",          89544, "5/3/2026"),
    ("OniEliteZ",    89091, "5/3/2026"),
    ("Tatsuya",      87657, "5/3/2026"),
    ("Krazy",        87034, "5/3/2026"),
    ("Hellboy",      86595, "5/3/2026"),
    ("Newbie",       86011, "5/3/2026"),
    ("Lilith",       85878, "5/3/2026"),
    ("Garahel",      85830, "5/3/2026"),
    ("Goldlock101",  85064, "5/3/2026"),
    ("Jinbei",       84580, "5/3/2026"),
    ("Wanderer",     84356, "5/3/2026"),
    ("Sandokai",     82991, "5/3/2026"),
    ("MonkeyAFK",    82778, "5/3/2026"),
    ("McLagus",      82053, "5/3/2026"),
    ("Palo",         74111, "5/3/2026"),
]


def fmt_power(val: int) -> str:
    # val is exactly what the game showed as K · store it verbatim so
    # _parse_power_value can derive the correct numeric value from the text
    return f"{val}K"


def to_iso(date_str: str) -> str:
    return datetime.strptime(date_str, "%m/%d/%Y").strftime("%Y-%m-%dT00:00:00")


def main() -> None:
    # Deduplicate: if the same name appears twice on the same date keep the higher value
    seen: dict[tuple[str, str], int] = {}
    for name, power, date in RAW:
        key = (name.lower(), date)
        if key not in seen or power > seen[key]:
            seen[key] = power

    by_date: dict[str, list[tuple[str, int]]] = defaultdict(list)
    # Reconstruct with canonical casing from first appearance
    canonical: dict[str, str] = {}
    for name, power, date in RAW:
        lname = name.lower()
        if lname not in canonical:
            canonical[lname] = name
        key = (lname, date)
        if seen.get(key) == power:
            by_date[to_iso(date)].append((canonical[lname], power))
            seen.pop(key)  # consume so duplicates don't re-insert

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    print("Clearing existing snapshot data...")
    conn.execute("DELETE FROM member_snapshots")
    conn.execute("DELETE FROM snapshots")
    conn.commit()

    def get_or_create_member(name: str, first_seen: str) -> int:
        row = conn.execute(
            "SELECT id FROM members WHERE lower(ingame_name) = lower(?)", (name,)
        ).fetchone()
        if row:
            return row[0]
        cur = conn.execute(
            "INSERT INTO members (ingame_name, first_seen) VALUES (?, ?)",
            (name, first_seen),
        )
        return cur.lastrowid

    for date_iso in sorted(by_date.keys()):
        members_on_date = by_date[date_iso]
        cur = conn.execute(
            "INSERT INTO snapshots (scraped_at, member_count) VALUES (?, ?)",
            (date_iso, len(members_on_date)),
        )
        snapshot_id = cur.lastrowid

        for name, power in members_on_date:
            member_id = get_or_create_member(name, date_iso)
            conn.execute(
                """INSERT INTO member_snapshots
                       (snapshot_id, member_id, name, last_active, last_seen_approx,
                        combat_power, combat_power_value, activeness)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (snapshot_id, member_id, name, "Unknown",
                 date_iso, fmt_power(power), float(power * 1000), 0),
            )

        conn.commit()
        print(f"  {date_iso[:10]} · {len(members_on_date)} members")

    conn.close()
    print("\nDone. Run /scan now to add today's live data as snapshot 11.")


if __name__ == "__main__":
    main()
