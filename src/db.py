import re
import sqlite3
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from parser import Member

DB_PATH = Path(__file__).parent.parent / "guild.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                scraped_at  TEXT NOT NULL,
                member_count INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS members (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ingame_name   TEXT NOT NULL UNIQUE,
                discord_id    TEXT UNIQUE,
                discord_name  TEXT,
                first_seen    TEXT NOT NULL,
                notes         TEXT
            );

            CREATE TABLE IF NOT EXISTS member_name_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                member_id   INTEGER NOT NULL REFERENCES members(id),
                old_name    TEXT NOT NULL,
                new_name    TEXT NOT NULL,
                changed_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS member_snapshots (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id         INTEGER NOT NULL REFERENCES snapshots(id),
                member_id           INTEGER REFERENCES members(id),
                name                TEXT NOT NULL,
                last_active         TEXT NOT NULL,
                last_seen_approx    TEXT,
                combat_power        TEXT NOT NULL,
                combat_power_value  REAL,
                activeness          INTEGER NOT NULL,
                warband             TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_ms_snapshot ON member_snapshots(snapshot_id);
            CREATE INDEX IF NOT EXISTS idx_ms_name     ON member_snapshots(name);
            CREATE INDEX IF NOT EXISTS idx_ms_member   ON member_snapshots(member_id);

            CREATE TABLE IF NOT EXISTS name_corrections (
                ocr_name     TEXT PRIMARY KEY,
                correct_name TEXT NOT NULL,
                source       TEXT NOT NULL DEFAULT 'ocr'
            );
        """)
        # Migrations
        try:
            conn.execute("ALTER TABLE member_snapshots ADD COLUMN warband TEXT NOT NULL DEFAULT ''")
            conn.commit()
        except Exception as e:
            if "duplicate column" not in str(e):
                print(f"[DB migration] {e}")


def _parse_last_seen(last_active: str, scraped_at: datetime) -> datetime:
    if last_active.lower() == "online":
        return scraped_at
    m = re.match(r"(\d+)([smhd])\s*ago", last_active, re.IGNORECASE)
    if not m:
        return scraped_at
    value = int(m.group(1))
    unit = m.group(2).lower()
    delta = {"s": timedelta(seconds=value), "m": timedelta(minutes=value),
             "h": timedelta(hours=value),   "d": timedelta(days=value)}[unit]
    return scraped_at - delta


def _parse_power_value(power: str) -> float:
    m = re.match(r"([\d.]+)([KM])", power, re.IGNORECASE)
    if not m:
        return 0.0
    value = float(m.group(1))
    return value * 1_000_000 if m.group(2).upper() == "M" else value * 1_000


def _get_or_create_member(conn: sqlite3.Connection, name: str, first_seen: str) -> int:
    row = conn.execute("SELECT id FROM members WHERE ingame_name = ?", (name,)).fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO members (ingame_name, first_seen) VALUES (?, ?)",
        (name, first_seen),
    )
    return cur.lastrowid


def _current_week_start() -> str:
    """Monday 00:00 UTC as ISO string — equals Sunday 8 PM EDT / 7 PM EST."""
    now = datetime.utcnow()
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday.isoformat()


def save_snapshot(members: list[Member]) -> int:
    scraped_at = datetime.utcnow()
    scraped_at_str = scraped_at.isoformat()
    week_start = _current_week_start()

    with _connect() as conn:
        existing = conn.execute(
            "SELECT id FROM snapshots WHERE scraped_at >= ? ORDER BY id DESC LIMIT 1",
            (week_start,),
        ).fetchone()

        if existing:
            snapshot_id = existing[0]
            conn.execute(
                "UPDATE snapshots SET scraped_at = ?, member_count = ? WHERE id = ?",
                (scraped_at_str, len(members), snapshot_id),
            )
            for m in members:
                member_id = _get_or_create_member(conn, m.name, scraped_at_str)
                row = conn.execute(
                    "SELECT id FROM member_snapshots WHERE snapshot_id = ? AND member_id = ?",
                    (snapshot_id, member_id),
                ).fetchone()
                if row:
                    conn.execute(
                        """UPDATE member_snapshots
                           SET name = ?, last_active = ?, last_seen_approx = ?,
                               combat_power = ?, combat_power_value = ?, activeness = ?, warband = ?
                           WHERE snapshot_id = ? AND member_id = ?""",
                        (
                            m.name,
                            m.last_active,
                            _parse_last_seen(m.last_active, scraped_at).isoformat(),
                            m.combat_power,
                            _parse_power_value(m.combat_power),
                            m.activeness,
                            m.warband,
                            snapshot_id,
                            member_id,
                        ),
                    )
                else:
                    conn.execute(
                        """INSERT INTO member_snapshots
                           (snapshot_id, member_id, name, last_active, last_seen_approx,
                            combat_power, combat_power_value, activeness, warband)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            snapshot_id,
                            member_id,
                            m.name,
                            m.last_active,
                            _parse_last_seen(m.last_active, scraped_at).isoformat(),
                            m.combat_power,
                            _parse_power_value(m.combat_power),
                            m.activeness,
                            m.warband,
                        ),
                    )
        else:
            cur = conn.execute(
                "INSERT INTO snapshots (scraped_at, member_count) VALUES (?, ?)",
                (scraped_at_str, len(members)),
            )
            snapshot_id = cur.lastrowid
            conn.executemany(
                """INSERT INTO member_snapshots
                   (snapshot_id, member_id, name, last_active, last_seen_approx,
                    combat_power, combat_power_value, activeness, warband)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        snapshot_id,
                        _get_or_create_member(conn, m.name, scraped_at_str),
                        m.name,
                        m.last_active,
                        _parse_last_seen(m.last_active, scraped_at).isoformat(),
                        m.combat_power,
                        _parse_power_value(m.combat_power),
                        m.activeness,
                        m.warband,
                    )
                    for m in members
                ],
            )

        # Sync active flag — members in this scan are active, everyone else is not
        scanned_ids = [
            r[0] for r in conn.execute(
                'SELECT member_id FROM member_snapshots WHERE snapshot_id = ? AND member_id IS NOT NULL',
                (snapshot_id,)
            ).fetchall()
        ]
        if scanned_ids:
            placeholders = ','.join('?' * len(scanned_ids))
            conn.execute(f'UPDATE members SET active = 1 WHERE id IN ({placeholders})', scanned_ids)
            conn.execute(f'UPDATE members SET active = 0 WHERE id NOT IN ({placeholders})', scanned_ids)

    return snapshot_id


# --- Name correction helpers ---

AMBIGUOUS = re.compile(r"[1lLIi0O]")


def get_corrections() -> dict[str, str]:
    with _connect() as conn:
        rows = conn.execute("SELECT ocr_name, correct_name FROM name_corrections").fetchall()
    # Keys stored and looked up as lowercase so case variants all resolve correctly
    return {r["ocr_name"].lower(): r["correct_name"] for r in rows}


def save_correction(ocr_name: str, correct_name: str, source: str = "ocr") -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO name_corrections (ocr_name, correct_name, source) VALUES (?, ?, ?)",
            (ocr_name.lower(), correct_name, source),
        )


def apply_corrections(members: list[Member]) -> list[Member]:
    corrections = get_corrections()
    for m in members:
        m.name = corrections.get(m.name, m.name)
    return members


def _get_known_names() -> set[str]:
    with _connect() as conn:
        rows = conn.execute("SELECT DISTINCT name FROM member_snapshots").fetchall()
    return {r["name"] for r in rows}


def _fuzzy_match_known(name: str, known: set[str], threshold: float = 0.88) -> str | None:
    best_score = 0.0
    best_match = None
    for known_name in known:
        score = SequenceMatcher(None, name.lower(), known_name.lower()).ratio()
        if score > best_score:
            best_score = score
            best_match = known_name
    if best_score >= threshold and best_match != name:
        return best_match
    return None


def validate_names(members: list[Member]) -> tuple[list[Member], list[str]]:
    """Returns (members, uncertain_names).

    uncertain_names contains OCR'd names that had ambiguous characters, no
    history match, and no stdin available to prompt · saved as-is and should
    be reviewed with /rename.
    """
    corrections = get_corrections()
    known_names = _get_known_names()
    uncertain: list[str] = []

    for m in members:
        original = m.name

        # Step 1: known correction (case-insensitive lookup)
        if original.lower() in corrections:
            m.name = corrections[original.lower()]
            continue

        # Step 2: no ambiguous chars — accept as-is
        if not AMBIGUOUS.search(original):
            continue

        # Step 3: fuzzy match against DB history
        match = _fuzzy_match_known(original, known_names)
        if match:
            print(f"  Auto-corrected '{original}' -> '{match}' (matched history)")
            save_correction(original, match)
            m.name = match
            continue

        # Step 4: unknown — ask once (skipped silently when running non-interactively)
        print(f"\n  New name with ambiguous characters: '{original}'")
        try:
            answer = input("  Enter correct name (or press Enter to accept): ").strip()
        except EOFError:
            answer = ""
        correct = answer if answer else original
        save_correction(original, correct)
        m.name = correct
        if not answer:
            uncertain.append(correct)

    return members, uncertain


# --- Query helpers for the bot ---

def get_latest_members() -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute("""
            SELECT ms.*
            FROM member_snapshots ms
            WHERE ms.snapshot_id = (SELECT MAX(id) FROM snapshots)
            ORDER BY ms.activeness DESC
        """).fetchall()


def get_inactive_members(days: int = 3) -> list[sqlite3.Row]:
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with _connect() as conn:
        return conn.execute("""
            SELECT ms.*
            FROM member_snapshots ms
            WHERE ms.snapshot_id = (SELECT MAX(id) FROM snapshots)
              AND ms.last_seen_approx < ?
            ORDER BY ms.last_seen_approx ASC
        """, (cutoff,)).fetchall()


def get_low_activeness(threshold: int = 700) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute("""
            SELECT ms.*
            FROM member_snapshots ms
            WHERE ms.snapshot_id = (SELECT MAX(id) FROM snapshots)
              AND ms.activeness < ?
            ORDER BY ms.activeness ASC
        """, (threshold,)).fetchall()


def get_power_history(name: str) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute("""
            SELECT s.scraped_at, ms.combat_power, ms.combat_power_value, ms.activeness
            FROM member_snapshots ms
            JOIN snapshots s ON s.id = ms.snapshot_id
            WHERE ms.name = ?
            ORDER BY s.scraped_at ASC
        """, (name,)).fetchall()
