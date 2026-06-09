import re
import sqlite3
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from parser import Member

DB_PATH = Path(__file__).parent.parent / "guild.db"


def _utcnow() -> datetime:
    """Naive UTC now. datetime.utcnow() is deprecated; .replace(tzinfo=None)
    keeps stored timestamps byte-identical to the existing naive-ISO format."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create the SHARED schema · the miner is the single owner of the scan +
    member-identity tables (the bot's utils/db.js owns its bot-only tables and
    never creates these). The CREATE statements always reflect the CURRENT
    shape: when the schema changes, run the ALTER once against guild.db and
    fold the column into the CREATE here · no migration trail replayed on load."""
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                scraped_at   TEXT NOT NULL,
                member_count INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS warbands (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                sort_order  INTEGER NOT NULL DEFAULT 0,
                archived    INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS members (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ingame_name     TEXT NOT NULL UNIQUE,
                discord_id      TEXT UNIQUE,
                discord_name    TEXT,
                first_seen      TEXT NOT NULL,
                notes           TEXT,
                active          INTEGER NOT NULL DEFAULT 0,
                pending         INTEGER NOT NULL DEFAULT 0,
                warband_id      INTEGER REFERENCES warbands(id),
                last_scanned_at TEXT
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
                warband             TEXT NOT NULL DEFAULT '',
                warband_id          INTEGER REFERENCES warbands(id)
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
        # Seed known warbands (idempotent)
        for i, name in enumerate(SEED_WARBANDS):
            conn.execute("INSERT OR IGNORE INTO warbands (name, sort_order) VALUES (?, ?)", (name, i))
        conn.commit()


def _parse_last_seen(last_active: str, scraped_at: datetime) -> datetime:
    if last_active.lower() == "online":
        return scraped_at
    m = re.match(r"(\d*)([smhd])\s*ago", last_active, re.IGNORECASE)
    if not m or not m.group(1):
        return scraped_at
    value = int(m.group(1))
    unit = m.group(2).lower()
    delta = {"s": timedelta(seconds=value), "m": timedelta(minutes=value),
             "h": timedelta(hours=value),   "d": timedelta(days=value)}[unit]
    return scraped_at - delta


SEED_WARBANDS = ("RKF RiffRaff", "RKF Kings", "Sobaquitos")
# Hard OCR misreads fuzzy matching can't catch · keys are lowercase → canonical name
WARBAND_ALIASES = {"dkekinos": "RKF Kings"}


def _resolve_warband(conn: sqlite3.Connection, text: str) -> tuple[str, int | None]:
    """Resolve an OCR'd warband to (canonical_name, warband_id) using the warbands
    table: alias → exact → fuzzy (0.8). Unknown reads keep their text with id=None so
    a genuinely new in-game warband surfaces for the admin to add rather than guessing."""
    if not text:
        return "", None
    rows = conn.execute("SELECT id, name FROM warbands WHERE archived = 0").fetchall()
    known = {r["name"].lower(): (r["id"], r["name"]) for r in rows}
    low = text.lower()
    if low in WARBAND_ALIASES:
        low = WARBAND_ALIASES[low].lower()
    if low in known:
        wid, name = known[low]
        return name, wid
    best, score = None, 0.0
    for k, (wid, name) in known.items():
        r = SequenceMatcher(None, low, k).ratio()
        if r > score:
            best, score = (name, wid), r
    if best and score >= 0.8:
        return best
    return text, None


def _parse_power_value(power: str) -> float:
    m = re.match(r"([\d.]+)([KM])", power, re.IGNORECASE)
    if not m:
        return 0.0
    value = float(m.group(1))
    return value * 1_000_000 if m.group(2).upper() == "M" else value * 1_000


def _get_or_create_member(conn: sqlite3.Connection, name: str, first_seen: str, pending: int = 0) -> int:
    row = conn.execute("SELECT id FROM members WHERE ingame_name = ?", (name,)).fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO members (ingame_name, first_seen, pending) VALUES (?, ?, ?)",
        (name, first_seen, pending),
    )
    return cur.lastrowid


def _current_week_start() -> str:
    """Monday 00:00 UTC as ISO string — equals Sunday 8 PM EDT / 7 PM EST."""
    now = _utcnow()
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday.isoformat()


def save_snapshot(members: list[Member], pending_names: set[str] | None = None) -> tuple[int, int]:
    scraped_at = _utcnow()
    scraped_at_str = scraped_at.isoformat()
    week_start = _current_week_start()
    pending_names = pending_names or set()

    with _connect() as conn:
        for m in members:
            m.warband, m.warband_id = _resolve_warband(conn, m.warband)

        # Resolve every member to an id once, so we know exactly who was read in
        # THIS scan (not the week-union of member_snapshots rows).
        member_ids = [
            _get_or_create_member(conn, m.name, scraped_at_str, 1 if m.name in pending_names else 0)
            for m in members
        ]
        # Deduplicate here so member_count reflects unique members, not raw OCR reads.
        # Two OCR reads of the same person (different spellings, same DB id after
        # validate_names correction) would otherwise inflate the count.
        current_ids = list(dict.fromkeys(member_ids))
        actual_count = len(current_ids)

        existing = conn.execute(
            "SELECT id FROM snapshots WHERE scraped_at >= ? ORDER BY id DESC LIMIT 1",
            (week_start,),
        ).fetchone()

        if existing:
            snapshot_id = existing[0]
            conn.execute(
                "UPDATE snapshots SET scraped_at = ?, member_count = ? WHERE id = ?",
                (scraped_at_str, actual_count, snapshot_id),
            )
            for m, member_id in zip(members, member_ids):
                row = conn.execute(
                    "SELECT id FROM member_snapshots WHERE snapshot_id = ? AND member_id = ?",
                    (snapshot_id, member_id),
                ).fetchone()
                if row:
                    conn.execute(
                        """UPDATE member_snapshots
                           SET name = ?, last_active = ?, last_seen_approx = ?,
                               combat_power = ?, combat_power_value = ?, activeness = ?,
                               warband = ?, warband_id = ?
                           WHERE snapshot_id = ? AND member_id = ?""",
                        (
                            m.name,
                            m.last_active,
                            _parse_last_seen(m.last_active, scraped_at).isoformat(),
                            m.combat_power,
                            _parse_power_value(m.combat_power),
                            m.activeness,
                            m.warband,
                            m.warband_id,
                            snapshot_id,
                            member_id,
                        ),
                    )
                else:
                    conn.execute(
                        """INSERT INTO member_snapshots
                           (snapshot_id, member_id, name, last_active, last_seen_approx,
                            combat_power, combat_power_value, activeness, warband, warband_id)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                            m.warband_id,
                        ),
                    )
        else:
            cur = conn.execute(
                "INSERT INTO snapshots (scraped_at, member_count) VALUES (?, ?)",
                (scraped_at_str, actual_count),
            )
            snapshot_id = cur.lastrowid
            conn.executemany(
                """INSERT INTO member_snapshots
                   (snapshot_id, member_id, name, last_active, last_seen_approx,
                    combat_power, combat_power_value, activeness, warband, warband_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
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
                        m.warband_id,
                    )
                    for m, member_id in zip(members, member_ids)
                ],
            )

        # Sync active flag — only members read in THIS scan are active (latest-scan
        # only). A member who left shows inactive on the next scan; one found again
        # auto-reactivates. last_scanned_at records when each was last actually read.
        if current_ids:
            placeholders = ','.join('?' * len(current_ids))
            conn.execute(
                f'UPDATE members SET last_scanned_at = ? WHERE id IN ({placeholders})',
                [scraped_at_str, *current_ids],
            )
            conn.execute(f'UPDATE members SET active = 1 WHERE id IN ({placeholders})', current_ids)
            conn.execute(f'UPDATE members SET active = 0 WHERE id NOT IN ({placeholders})', current_ids)

        # Sync each member's current warband from this scan — only when read (non-null),
        # so a blank/unreadable warband never wipes a known one (manual overrides persist).
        conn.execute(
            """UPDATE members SET warband_id = (
                   SELECT ms.warband_id FROM member_snapshots ms
                   WHERE ms.member_id = members.id AND ms.snapshot_id = ? AND ms.warband_id IS NOT NULL)
               WHERE id IN (
                   SELECT member_id FROM member_snapshots
                   WHERE snapshot_id = ? AND warband_id IS NOT NULL)""",
            (snapshot_id, snapshot_id),
        )
        # Blank fallback — fill this scan's unread warbands from the member's known current
        # warband so /guild views stay continuous instead of dropping people to "no warband".
        conn.execute(
            """UPDATE member_snapshots
               SET warband_id = (SELECT warband_id FROM members WHERE members.id = member_snapshots.member_id),
                   warband    = COALESCE((SELECT w.name FROM warbands w
                                          JOIN members mm ON mm.warband_id = w.id
                                          WHERE mm.id = member_snapshots.member_id), '')
               WHERE snapshot_id = ?
                 AND (warband_id IS NULL OR warband = '')
                 AND (SELECT warband_id FROM members WHERE members.id = member_snapshots.member_id) IS NOT NULL""",
            (snapshot_id,),
        )

    return snapshot_id, actual_count


# --- Name correction helpers ---


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


def _get_active_roster() -> set[str]:
    """Canonical names to resolve OCR reads into · the set of members seen in the
    latest weekly snapshot. Deliberately NOT `active = 1`: the active flag is now
    latest-scan-only, so using it would shrink the match pool after a lossy scan
    and turn a missed-then-misread member into a spurious pending duplicate."""
    with _connect() as conn:
        rows = conn.execute("""
            SELECT DISTINCT m.ingame_name
            FROM members m
            JOIN member_snapshots ms ON ms.member_id = m.id
            WHERE ms.snapshot_id = (SELECT MAX(id) FROM snapshots)
        """).fetchall()
    return {r["ingame_name"] for r in rows}


def _roster_exact(name: str, roster: set[str]) -> str | None:
    low = name.lower()
    for r in roster:
        if r.lower() == low:
            return r
    return None


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
    """Resolve each OCR'd name into the canonical active roster · never blocks on input.

    Returns (members, uncertain_names). uncertain_names are reads that matched no
    existing member (alias / exact / fuzzy) · they are accepted as-is, the member is
    created with pending=1 by save_snapshot, and they are surfaced via REVIEW_NAMES
    so they can be approved or merged later.
    """
    corrections = get_corrections()
    roster = _get_active_roster()
    uncertain: list[str] = []

    for m in members:
        original = m.name

        # 1. Known alias (case-insensitive)
        if original.lower() in corrections:
            m.name = corrections[original.lower()]
            continue

        # 2. Exact roster match (case-insensitive)
        exact = _roster_exact(original, roster)
        if exact:
            m.name = exact
            continue

        # 3. Fuzzy match against the active roster
        match = _fuzzy_match_known(original, roster, threshold=0.86)
        if match:
            print(f"  Auto-corrected '{original}' -> '{match}' (matched roster)")
            save_correction(original, match)
            m.name = match
            continue

        # 4. Unknown read — accept as-is, flag pending + for review
        uncertain.append(original)

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
    cutoff = (_utcnow() - timedelta(days=days)).isoformat()
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
