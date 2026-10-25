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
    # Shared DB (bot reads/writes concurrently); wait on a busy lock instead of
    # failing the write instantly. Matters most during the ID-capture pass, which
    # writes between slow device taps.
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db() -> None:
    """Create the SHARED schema · the miner is the single owner of the scan +
    member-identity tables (the bot's utils/db.js owns its bot-only tables and
    never creates these). The CREATE statements always reflect the CURRENT
    shape: when the schema changes, run the ALTER once against guild.db and
    fold the column into the CREATE here · no migration trail replayed on load.

    guilds/warbands are bot-owned (Discord role/membership management is a bot
    concern) · created by utils/db.js, not here. members.warband_id below still
    references warbands(id) since it's the same physical guild.db file; the
    scraper only reads that table (_resolve_warband) and never creates it."""
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                scraped_at   TEXT NOT NULL,
                member_count INTEGER NOT NULL
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
                last_scanned_at TEXT,
                ingame_id       INTEGER,
                title           TEXT
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
                last_active         TEXT,  -- NULL when the OCR'd time garbled · the member
                last_seen_approx    TEXT,  -- is still captured (name-anchored); time is nice-to-have
                combat_power        TEXT NOT NULL,
                combat_power_value  REAL,
                activeness          INTEGER NOT NULL,
                warband             TEXT NOT NULL DEFAULT '',
                warband_id          INTEGER REFERENCES warbands(id),
                title               TEXT NOT NULL DEFAULT '',
                season_id           INTEGER REFERENCES ally_seasons(id)
            );

            CREATE INDEX IF NOT EXISTS idx_ms_snapshot ON member_snapshots(snapshot_id);
            CREATE INDEX IF NOT EXISTS idx_ms_name     ON member_snapshots(name);
            CREATE INDEX IF NOT EXISTS idx_ms_member   ON member_snapshots(member_id);

            CREATE TABLE IF NOT EXISTS name_corrections (
                ocr_name     TEXT PRIMARY KEY,
                correct_name TEXT NOT NULL,
                source       TEXT NOT NULL DEFAULT 'ocr'
            );

            CREATE TABLE IF NOT EXISTS dream_realm_bosses (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                season     INTEGER,
                sort_order INTEGER,
                UNIQUE(name, season)
            );

            CREATE TABLE IF NOT EXISTS dream_realm_boss_aliases (
                alias   TEXT PRIMARY KEY,
                boss_id INTEGER NOT NULL REFERENCES dream_realm_bosses(id)
            );

            INSERT OR IGNORE INTO dream_realm_boss_aliases (alias, boss_id)
                SELECT 'croaker', id FROM dream_realm_bosses WHERE name = 'King Croaker';

            CREATE TABLE IF NOT EXISTS dream_realm_scores (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                member_id   INTEGER NOT NULL REFERENCES members(id),
                boss_id     INTEGER REFERENCES dream_realm_bosses(id),
                boss_name   TEXT NOT NULL,
                scan_date   TEXT NOT NULL,
                rank        INTEGER,
                score       TEXT,
                tier        TEXT,
                scanned_at  TEXT NOT NULL,
                UNIQUE(member_id, scan_date)
            );

            CREATE INDEX IF NOT EXISTS idx_drs_date ON dream_realm_scores(scan_date);

            CREATE TABLE IF NOT EXISTS afk_stage_rankings (
                member_id   INTEGER NOT NULL REFERENCES members(id),
                season      INTEGER NOT NULL,
                phase       INTEGER NOT NULL,
                rank        INTEGER,
                progress    TEXT,
                scanned_at  TEXT NOT NULL,
                PRIMARY KEY (member_id, season, phase)
            );

            CREATE TABLE IF NOT EXISTS arena_rankings (
                member_id   INTEGER PRIMARY KEY REFERENCES members(id),
                rank        INTEGER,
                points      INTEGER,
                tier        TEXT,
                scanned_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS supreme_arena_rankings (
                member_id    INTEGER NOT NULL REFERENCES members(id),
                period_start TEXT NOT NULL,
                rank         INTEGER,
                scanned_at   TEXT NOT NULL,
                PRIMARY KEY (member_id, period_start)
            );

            CREATE TABLE IF NOT EXISTS guild_duel_rankings (
                member_id    INTEGER NOT NULL REFERENCES members(id),
                period_start TEXT NOT NULL,
                rank         INTEGER,
                crests       INTEGER,
                scanned_at   TEXT NOT NULL,
                damage       INTEGER,
                damage_scanned_at TEXT,
                PRIMARY KEY (member_id, period_start)
            );

            CREATE TABLE IF NOT EXISTS honor_duel_rankings (
                member_id    INTEGER PRIMARY KEY REFERENCES members(id),
                rank         INTEGER,
                honor_points INTEGER,
                scanned_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS arcane_lab_rankings (
                member_id   INTEGER PRIMARY KEY REFERENCES members(id),
                rank        INTEGER,
                difficulty  INTEGER,
                floor       INTEGER,
                points      INTEGER,
                scanned_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS clashfronts_signups (
                member_id  INTEGER PRIMARY KEY REFERENCES members(id),
                selected   INTEGER NOT NULL DEFAULT 0,
                scanned_at TEXT NOT NULL
            );
        """)
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


def _la_pair(last_active: str | None, scraped_at: datetime) -> tuple[str | None, str | None]:
    """(last_active, last_seen_approx) to store · a garbled/unread time nulls BOTH.
    Capturing the member is what matters; the last-active timestamp is nice-to-have,
    so we store NULL rather than fabricate an 'Unknown' / seen-now value."""
    if not last_active or last_active == "Unknown":
        return None, None
    return last_active, _parse_last_seen(last_active, scraped_at).isoformat()


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


def log_scan_timing(mode: str, status: str, duration_ms: int, run_started_at: str) -> None:
    """Writes directly into the bot-owned scan_timings table (utils/db.js owns
    the CREATE, created before any scan ever runs) -- same immediate-write
    pattern as every other save_*() here, so a crash mid-run never loses the
    timing of modes that already finished. One shared guild.db, so either side
    can write whichever table needs it; this one just happens to be defined in
    the bot repo's schema."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO scan_timings (mode, status, duration_ms, run_started_at) VALUES (?, ?, ?, ?)",
            (mode, status, duration_ms, run_started_at),
        )
        conn.commit()


def save_snapshot(members: list[Member], pending_names: set[str] | None = None) -> tuple[int, int]:
    scraped_at = _utcnow()
    scraped_at_str = scraped_at.isoformat()
    week_start = _current_week_start()
    pending_names = pending_names or set()

    with _connect() as conn:
        season_id = _active_season_id(conn)
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
                               warband = ?, warband_id = ?, title = ?, season_id = ?
                           WHERE snapshot_id = ? AND member_id = ?""",
                        (
                            m.name,
                            *_la_pair(m.last_active, scraped_at),
                            m.combat_power,
                            _parse_power_value(m.combat_power),
                            m.activeness,
                            m.warband,
                            m.warband_id,
                            m.title,
                            season_id,
                            snapshot_id,
                            member_id,
                        ),
                    )
                else:
                    conn.execute(
                        """INSERT INTO member_snapshots
                           (snapshot_id, member_id, name, last_active, last_seen_approx,
                            combat_power, combat_power_value, activeness, warband, warband_id, title, season_id)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            snapshot_id,
                            member_id,
                            m.name,
                            *_la_pair(m.last_active, scraped_at),
                            m.combat_power,
                            _parse_power_value(m.combat_power),
                            m.activeness,
                            m.warband,
                            m.warband_id,
                            m.title,
                            season_id,
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
                    combat_power, combat_power_value, activeness, warband, warband_id, title, season_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        snapshot_id,
                        member_id,
                        m.name,
                        *_la_pair(m.last_active, scraped_at),
                        m.combat_power,
                        _parse_power_value(m.combat_power),
                        m.activeness,
                        m.warband,
                        m.warband_id,
                        m.title,
                        season_id,
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

        # Sync each member's current title from this scan — only when read
        # (non-blank), so an unreadable title never wipes a known one.
        # Mirrors the warband_id sync above exactly; title has no separate
        # _id FK since it's a fixed 8-word vocabulary, not an admin-managed table.
        conn.execute(
            """UPDATE members SET title = (
                   SELECT ms.title FROM member_snapshots ms
                   WHERE ms.member_id = members.id AND ms.snapshot_id = ? AND ms.title != '')
               WHERE id IN (
                   SELECT member_id FROM member_snapshots
                   WHERE snapshot_id = ? AND title != '')""",
            (snapshot_id, snapshot_id),
        )
        # Blank fallback — fill this scan's unread titles from the member's
        # known current title so any future title-based view stays continuous.
        conn.execute(
            """UPDATE member_snapshots
               SET title = (SELECT title FROM members WHERE members.id = member_snapshots.member_id)
               WHERE snapshot_id = ?
                 AND title = ''
                 AND (SELECT title FROM members WHERE members.id = member_snapshots.member_id) IS NOT NULL""",
            (snapshot_id,),
        )

    return snapshot_id, actual_count


# --- In-game User ID helpers ---


def members_needing_ingame_id() -> list[tuple[int, str]]:
    """(id, ingame_name) for members read in the latest scan that have no stored
    in-game User ID yet. active=1 is latest-scan-only, so this is exactly the
    on-screen roster · the ID-capture pass only taps members it can reach.
    Treats NULL or empty-string as missing -- see members_missing_ingame_id()
    for why (a manually-cleared field lands as '', not NULL)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, ingame_name FROM members "
            "WHERE active = 1 AND (ingame_id IS NULL OR ingame_id = '')"
        ).fetchall()
    return [(r["id"], r["ingame_name"]) for r in rows]


def set_ingame_id(member_id: int, ingame_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE members SET ingame_id = ? WHERE id = ?", (ingame_id, member_id))


def member_by_ingame_id(ingame_id: int) -> int | None:
    """Reverse lookup for the ID-assisted resolution fallback: once a member's
    in-game User ID is on file, it never changes, so it out-ranks a fuzzy name
    match that OCR garbled this round."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM members WHERE ingame_id = ?", (ingame_id,)
        ).fetchone()
    return row["id"] if row else None


def members_missing_ingame_id(member_ids: list[int]) -> set[int]:
    """Subset of member_ids that have no stored in-game User ID yet -- used by
    ranking-mode scans to opportunistically backfill IDs for rows that already
    resolved by name, while the row is still on screen for a follow-up tap.
    Treats NULL or empty-string as missing (a stray '' from a manual DB edit
    would otherwise be silently invisible to IS NULL forever)."""
    if not member_ids:
        return set()
    with _connect() as conn:
        placeholders = ",".join("?" * len(member_ids))
        rows = conn.execute(
            f"SELECT id FROM members WHERE id IN ({placeholders}) "
            "AND (ingame_id IS NULL OR ingame_id = '')",
            member_ids,
        ).fetchall()
    return {r["id"] for r in rows}


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


# Letters of the scripts real guild names use (Latin + CJK ideographs/ext + kana +
# Hangul). A decorative tag glyph that OCRs as Greek (ψ/φ) or a symbol falls outside
# these and is dropped; one that OCRs as a Latin letter (ψ→i/j) survives this filter
# but is split off as a short token by _normalize_core's longest-token rule.
_NAME_CHAR_RE = re.compile(
    r"[a-z0-9一-鿿㐀-䶿぀-ヿ가-힯]"
)
# Whitespace + the bracket forms guild tags are wrapped in ('「ψ」'); used to split a
# read into tag-vs-name tokens. OCR also misreads the brackets, but a delimiter
# (bracket or space) almost always survives between the 1-2 char tag and the name.
_TOKEN_SPLIT_RE = re.compile(r"[\s「-】\[\]()<>]+")


def _clean_token(tok: str) -> str:
    """Keep only name-script letters/digits in a token, lowercased · drops symbols
    in place (so 'sc∅rpi∅n175' → 'scrpin175' rather than splitting on the ∅)."""
    return "".join(ch for ch in _NAME_CHAR_RE.findall(tok.lower()))


def _normalize_core(name: str) -> str:
    """Reduce an OCR'd name to its comparable core via the LONGEST name token.

    Guild members wear a decorative '「ψ」' tag whose glyph v5 reads wildly (ψ, 山, φ,
    i, j, or dropped) and whose brackets also garble. Character-class filtering can't
    remove a tag that lands as a Latin letter, but the tag is always a 1-2 char
    fragment split off by a bracket or space, while the real name is the longest
    token. So: split on whitespace+brackets, clean symbols within each token, and
    return the longest survivor. 'ψ」Hira'→'hira', '山」谢霆锋'→'谢霆锋', 'i 」 arcanist'→
    'arcanist'. Returns '' only when nothing name-like remains · callers MUST skip the
    normalized tier on empty cores (rare now that CJK survives) to avoid false fusion."""
    tokens = [_clean_token(t) for t in _TOKEN_SPLIT_RE.split(name)]
    tokens = [t for t in tokens if t]
    return max(tokens, key=len) if tokens else ""


def _normalized_roster(roster: set[str]) -> dict[str, str]:
    """Map each roster name's normalized core to its canonical spelling. Empty
    cores (pure non-ASCII names) are excluded so they can't collide."""
    idx: dict[str, str] = {}
    for r in roster:
        core = _normalize_core(r)
        if core and core not in idx:
            idx[core] = r
    return idx


def _normalized_match(name: str, norm_idx: dict[str, str]) -> str | None:
    """Match by decoration-stripped core. Skips when the candidate's core is
    empty so pure-CJK / symbol-only reads never match via this tier. Only runs
    after exact match has already failed, so any hit is a genuine correction."""
    core = _normalize_core(name)
    if not core:
        return None
    return norm_idx.get(core)


def _strip_merged_tag(name: str) -> str:
    """Strip a trailing server-tag shape OCR sometimes merges onto a ranking
    screen name ("Kit" + "s282" -> "Kits282"). Only an [Ss]?ddd(d) suffix
    counts — a bare [A-Za-z] prefix was tried and too broad (ate the last
    letter of names like UCQ: UCQ282 -> UC instead of UCQ)."""
    stripped = re.sub(r"\s*[sS]?\d{3,4}$", "", name).strip()
    stripped = re.sub(r"\s*[sS]$", "", stripped).strip()
    return stripped


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


class RosterResolver:
    """Single source of truth for OCR-name -> canonical roster name resolution,
    shared by validate_names (guild scan), resolve_names (mode scans), and the
    ID-capture pass. Chain: alias/correction -> exact -> normalized-core (strips
    decorative tags, 'ψ」Hira' -> 'Hira') -> fuzzy@0.86, over the active roster +
    corrections table. Indexes are built once at construction · build one resolver
    and reuse it across many reads. learn=True records new normalized/fuzzy hits
    back into the corrections table (and logs them) so the next scan resolves them
    by alias; learn=False is read-only (the ID-capture pass)."""

    def __init__(self, learn: bool = False):
        self._corrections = get_corrections()
        self._roster = _get_active_roster()
        self._norm_idx = _normalized_roster(self._roster)
        self._learn = learn

    def resolve(self, name: str) -> str | None:
        canonical = self._resolve_once(name)
        if canonical:
            return canonical
        # Ranking-screen OCR sometimes merges a player's server tag onto their
        # name in one text block ("Kit" + "s282" -> "Kits282"). Try the raw
        # name first (above) so real names ending in digits/s ("Scorpion175",
        # "Crysallis") resolve untouched · only fall back to stripping a
        # trailing merged-tag-shaped suffix if the raw name didn't resolve.
        stripped = _strip_merged_tag(name)
        if stripped != name:
            return self._resolve_once(stripped)
        return None

    def _resolve_once(self, name: str) -> str | None:
        if name.lower() in self._corrections:
            return self._corrections[name.lower()]
        exact = _roster_exact(name, self._roster)
        if exact:
            return exact
        norm = _normalized_match(name, self._norm_idx)
        if norm:
            self._record(name, norm, "matched core")
            return norm
        fuzzy = _fuzzy_match_known(name, self._roster, threshold=0.86)
        if fuzzy:
            self._record(name, fuzzy, "matched roster")
        return fuzzy

    def _record(self, original: str, canonical: str, how: str) -> None:
        if self._learn:
            print(f"  Auto-corrected '{original}' -> '{canonical}' ({how})")
            save_correction(original, canonical)


def make_roster_resolver():
    """Read-only resolver callable (name -> canonical or None) for the ID-capture
    pass · identifies a card the same way the scans do, without side effects."""
    return RosterResolver(learn=False).resolve


def identity_key(resolver: "RosterResolver", name: str) -> tuple[str, bool]:
    """Cluster key for resolve-then-vote: (key, resolved). If the read resolves to a
    canonical member, key is that canonical name and resolved=True · otherwise key is
    the longest-token core (so noisy variants of an unknown name still cluster) and
    resolved=False. Votes accumulate per true identity, so a name truncated in one
    frame is outvoted by the frames that read it cleanly."""
    canonical = resolver.resolve(name)
    if canonical:
        return canonical, True
    return (_normalize_core(name) or name.lower()), False


def validate_names(members: list[Member]) -> tuple[list[Member], list[str]]:
    """Resolve each OCR'd name into the canonical active roster · never blocks on input.

    Returns (members, uncertain_names). uncertain_names are reads that matched no
    existing member (alias / exact / fuzzy) · they are accepted as-is, the member is
    created with pending=1 by save_snapshot, and they are surfaced via REVIEW_NAMES
    so they can be approved or merged later.
    """
    resolver = RosterResolver(learn=True)
    uncertain: list[str] = []
    for m in members:
        canonical = resolver.resolve(m.name)
        if canonical:
            m.name = canonical
        else:
            uncertain.append(m.name)  # unknown read — created pending by save_snapshot
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


# --- Game-mode ranking scans ---


def names_for_ids(ids: list[int]) -> dict[int, str]:
    """Map member ids to their canonical ingame_name · for logging which member
    an OCR read resolved to."""
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    with _connect() as conn:
        return {r["id"]: r["ingame_name"] for r in conn.execute(
            f"SELECT id, ingame_name FROM members WHERE id IN ({placeholders})", ids
        ).fetchall()}


def resolve_names(names: list[str]) -> tuple[dict[str, int], list[str]]:
    """Resolve OCR'd names from ranking scans into member ids · alias → exact →
    fuzzy@0.86 against the same roster validate_names() uses. Returns
    ({ocr_name: member_id}, unmatched). Unmatched names are NOT created as
    members (the roster scan owns membership) · callers skip those rows and
    surface them via REVIEW_NAMES."""
    resolver = RosterResolver(learn=True)
    with _connect() as conn:
        # pending=1 excluded: an unapproved/unreviewed row's exact name is itself
        # an unverified OCR read (possibly garbage, e.g. a single stray fragment
        # like "to") -- treating it as an authoritative exact-match target let a
        # bogus pending row silently "match" a later scan's equally-garbled read
        # of a totally different, real member's card, confirmed live 2026-08-24
        # (make_id_fallback_hook then wrote a real captured ingame_id onto that
        # bogus row, actively worsening what should have stayed a harmless,
        # reviewable duplicate). A real member missed by the latest guild scan
        # (pending=0, just not in _get_active_roster()) still matches here.
        id_by_name = {r["ingame_name"].lower(): r["id"]
                      for r in conn.execute(
                          "SELECT id, ingame_name FROM members WHERE pending = 0").fetchall()}
    resolved: dict[str, int] = {}
    unmatched: list[str] = []
    for raw in names:
        canonical = resolver.resolve(raw)
        if canonical is None and raw.lower() in id_by_name:
            canonical = raw  # known member outside the latest snapshot
        if canonical and canonical.lower() in id_by_name:
            resolved[raw] = id_by_name[canonical.lower()]
        else:
            unmatched.append(raw)
    return resolved, unmatched


def _active_season_id(conn: sqlite3.Connection) -> int | None:
    """Return the id of the currently active ally_season, or None if not set."""
    try:
        row = conn.execute(
            "SELECT id FROM ally_seasons WHERE active = 1 LIMIT 1"
        ).fetchone()
        return row["id"] if row else None
    except Exception:
        return None


def _resolve_boss(conn: sqlite3.Connection, text: str) -> tuple[str, int | None]:
    """Resolve an OCR'd Dream Realm boss name: alias → exact → fuzzy (best match).
    Never inserts a new row — all bosses must be pre-loaded in dream_realm_bosses."""
    if not text:
        return "", None
    rows = conn.execute("SELECT id, name FROM dream_realm_bosses").fetchall()
    low = text.lower()
    alias_row = conn.execute(
        "SELECT boss_id FROM dream_realm_boss_aliases WHERE alias = ?", (low,)
    ).fetchone()
    if alias_row:
        matched = next((r for r in rows if r["id"] == alias_row["boss_id"]), None)
        if matched:
            return matched["name"], matched["id"]
    for r in rows:
        if r["name"].lower() == low:
            return r["name"], r["id"]
    best, score = None, 0.0
    for r in rows:
        s = SequenceMatcher(None, low, r["name"].lower()).ratio()
        if s > score:
            best, score = r, s
    if best:
        if score < 0.8:
            print(f"REVIEW_BOSS: OCR read '{text}' · snapped to '{best['name']}' (score={score:.2f}) · add alias if wrong")
        return best["name"], best["id"]
    return "", None


def get_boss_for_date(today_iso: str, today_boss_id: int,
                      scan_date: str) -> tuple[str, int | None]:
    """Return (name, id) of the boss on scan_date, computed from today's boss
    position in the cycle. Returns ('', None) if sort_order not yet set."""
    from datetime import date as _date
    days_back = (_date.fromisoformat(today_iso) - _date.fromisoformat(scan_date)).days
    if days_back <= 0:
        return "", None
    with _connect() as conn:
        today_row = conn.execute(
            "SELECT sort_order, season FROM dream_realm_bosses WHERE id = ?",
            (today_boss_id,),
        ).fetchone()
        if not today_row or today_row["sort_order"] is None:
            return "", None
        season = today_row["season"]
        cycle = conn.execute(
            "SELECT COUNT(*) AS n FROM dream_realm_bosses "
            "WHERE season = ? AND sort_order IS NOT NULL",
            (season,),
        ).fetchone()["n"]
        if cycle == 0:
            return "", None
        target_order = (today_row["sort_order"] - 1 - days_back) % cycle + 1
        row = conn.execute(
            "SELECT id, name FROM dream_realm_bosses "
            "WHERE season = ? AND sort_order = ?",
            (season, target_order),
        ).fetchone()
        if row:
            return row["name"], row["id"]
    return "", None


def get_missing_dream_realm_days(max_back: int = 3) -> list[str]:
    """UTC game days (yesterday back to -max_back) with no saved scores yet,
    newest first. Today's in-progress board is never captured."""
    today = _utcnow().date()
    days = [(today - timedelta(days=i)).isoformat() for i in range(1, max_back + 1)]
    with _connect() as conn:
        have = {r["scan_date"] for r in conn.execute(
            "SELECT DISTINCT scan_date FROM dream_realm_scores WHERE scan_date >= ?",
            (days[-1],)).fetchall()}
    return [d for d in days if d not in have]


def save_dream_realm(entries: list[dict], scan_date: str, boss_name: str,
                     boss_id: int | None = None) -> int:
    scanned_at = _utcnow().isoformat()
    with _connect() as conn:
        if boss_id is None:
            boss_name, boss_id = _resolve_boss(conn, boss_name)
        conn.executemany(
            """INSERT OR REPLACE INTO dream_realm_scores
               (member_id, boss_id, boss_name, scan_date, rank, score, tier, scanned_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [(e["member_id"], boss_id, boss_name, scan_date,
              e.get("rank"), e.get("score"), e.get("tier"), scanned_at) for e in entries])
    return len(entries)


def save_afk_stages(entries: list[dict], season: int, phase: int) -> int:
    scanned_at = _utcnow().isoformat()
    with _connect() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO afk_stage_rankings
               (member_id, season, phase, rank, progress, scanned_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [(e["member_id"], season, phase, e.get("rank"), e.get("progress"), scanned_at)
             for e in entries])
    return len(entries)


def save_arena(entries: list[dict]) -> int:
    scanned_at = _utcnow().isoformat()
    with _connect() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO arena_rankings (member_id, rank, points, tier, scanned_at)
               VALUES (?, ?, ?, ?, ?)""",
            [(e["member_id"], e.get("rank"), e.get("points"), e.get("tier"), scanned_at)
             for e in entries])
    return len(entries)


def current_supreme_period() -> str:
    """Returns the period_start (most recent Wednesday, ISO date) for 'now' ·
    always resolves, regardless of weekday. Wed itself is period start (0 days
    back); Tue is the last day of the PREVIOUS period (6 days back)."""
    now = _utcnow()
    days_since_wednesday = (now.weekday() + 7 - 2) % 7
    wednesday = (now - timedelta(days=days_since_wednesday)).date()
    return wednesday.isoformat()


def guild_duel_period_key(now: datetime) -> str:
    """Returns the period_start (ISO date) of the week Battle Report is
    currently showing · pure function, no DB access, so the boundary math can
    be asserted directly against known dates instead of only observed live on
    one day. Battle Report always displays the week that just ended, stable
    for the entire following week (confirmed live: still showed last week's
    result on a Tuesday with the new week's Prep Phase already running) and
    rolling over exactly at Monday 00:00 UTC (confirmed, no lag). That means
    the period is one week BEHIND the most recent Monday, not the most recent
    Monday itself · e.g. on Mon Jul 20 (rollover day), Battle Report has just
    switched to showing the week starting Jul 13, not Jul 20."""
    days_since_monday = (now.weekday() + 7 - 0) % 7
    most_recent_monday = (now - timedelta(days=days_since_monday)).date()
    period_start = most_recent_monday - timedelta(days=7)
    return period_start.isoformat()


def guild_duel_damage_period_key(now: datetime) -> str:
    """period_start for a weekend damage scan: the most recent Monday on or
    before now · the same date guild_duel_period_key resolves to once the
    Battle Report for that week is readable, so both scans land on one row."""
    return (now - timedelta(days=now.weekday())).date().isoformat()


def guild_duel_damage_window_open(now: datetime) -> bool:
    """Damage is only readable live, Saturday through Sunday UTC. Monday 00:00
    UTC resets the screen to the new week at zero, so a later scan must not
    write."""
    return now.weekday() in (5, 6)


def get_guild_duel_period(force: bool = False) -> str | None:
    """Returns the period_start to scan for, or None to skip.

    Battle Report always shows the just-finished week's totals, stable for
    the entire following week (confirmed live, no rollover lag at Monday
    00:00 UTC) · so there's no narrow scan window like Supreme Arena's
    Mon/Tue gate. The only gate is 'has this period already been saved' ·
    a missed or failed run safely retries on every later scrape this week
    until it succeeds, then goes quiet once rows exist. force=True bypasses
    that gate (manual re-scan) · save_guild_duel is INSERT OR REPLACE keyed
    on (member_id, period_start), so re-saving the same period overwrites
    cleanly instead of duplicating."""
    period = guild_duel_period_key(_utcnow())
    if force:
        return period
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM guild_duel_rankings WHERE period_start = ? AND crests IS NOT NULL LIMIT 1",
            (period,)
        ).fetchone()
    if row:
        return None
    return period


def get_guild_duel_missing(period: str) -> list[str]:
    """Active, non-pending members with no guild_duel_rankings row for this
    period · covers rows saved by the scan and any added by hand."""
    with _connect() as conn:
        return [r["ingame_name"] for r in conn.execute(
            """SELECT m.ingame_name FROM members m
               WHERE m.active = 1 AND m.pending = 0
                 AND NOT EXISTS (SELECT 1 FROM guild_duel_rankings g
                                 WHERE g.member_id = m.id AND g.period_start = ?
                                   AND g.crests IS NOT NULL)
               ORDER BY m.ingame_name COLLATE NOCASE""",
            (period,)).fetchall()]


def get_supreme_period() -> str | None:
    """Returns the period_start to scan for, or None to skip the LIVE scan.

    Scan window: Monday and Tuesday UTC only (results are final after Mon 00:00).
    Skips if a live scan for this period was already saved today (once-per-day
    guard). Manual submissions (save_supreme_arena(..., force=True)) bypass this
    entirely · they don't compete for the daily slot and can't suppress it."""
    now = _utcnow()
    if now.weekday() not in (0, 1):
        return None
    period = current_supreme_period()
    today = now.date().isoformat()
    with _connect() as conn:
        row = conn.execute(
            "SELECT scanned_at FROM supreme_arena_rankings WHERE period_start = ? LIMIT 1",
            (period,)
        ).fetchone()
    if row and row["scanned_at"][:10] == today:
        return None
    return period


def save_supreme_arena(entries: list[dict], force: bool = False) -> int:
    """force=True (manual/off-server submissions): writes for the CURRENT period
    regardless of weekday, bypassing the live-scan's Mon/Tue + once-per-day gate
    so a manual run never blocks that day's live scan from also saving."""
    period = current_supreme_period() if force else get_supreme_period()
    if period is None:
        return 0
    scanned_at = _utcnow().isoformat()
    with _connect() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO supreme_arena_rankings
               (member_id, period_start, rank, scanned_at) VALUES (?, ?, ?, ?)""",
            [(e["member_id"], period, e.get("rank"), scanned_at) for e in entries])
    return len(entries)


def wipe_pearl_bazaar_prices() -> None:
    """Clears the previous run's rows at the START of a new run (not the end)
    -- only the latest run ever matters (remaining cap changes minute to
    minute), and a crash mid-run should leave this run's partial-but-fresh
    data rather than stale data from last time."""
    with _connect() as conn:
        conn.execute("DELETE FROM pearl_bazaar_prices")


def save_pearl_bazaar_row(rank: int, ingame_id: int, price: int, selling_cap_remaining: int) -> None:
    """Writes one row immediately as it's captured -- same immediate-write
    pattern as every other save_*() here, so a crash partway through the
    100-row drill-in never loses rows already captured this run."""
    scanned_at = _utcnow().isoformat()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO pearl_bazaar_prices
               (rank, ingame_id, price, selling_cap_remaining, scanned_at)
               VALUES (?, ?, ?, ?, ?)""",
            (rank, ingame_id, price, selling_cap_remaining, scanned_at))


def save_guild_duel(entries: list[dict], period: str) -> int:
    scanned_at = _utcnow().isoformat()
    with _connect() as conn:
        conn.executemany(
            """INSERT INTO guild_duel_rankings
               (member_id, period_start, rank, crests, scanned_at) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(member_id, period_start) DO UPDATE SET
                   rank = excluded.rank, crests = excluded.crests, scanned_at = excluded.scanned_at""",
            [(e["member_id"], period, e.get("rank"), e.get("crests"), scanned_at) for e in entries])
    return len(entries)


def save_guild_duel_damage(entries: list[dict], period: str) -> int:
    """Weekend damage read · touches only damage and damage_scanned_at on a
    conflict, so crests and rank from the Battle Report survive in either
    order. scanned_at is NOT NULL, so a brand new damage-only row borrows the
    same instant until the crests scan rewrites it."""
    scanned_at = _utcnow().isoformat()
    with _connect() as conn:
        conn.executemany(
            """INSERT INTO guild_duel_rankings
               (member_id, period_start, scanned_at, damage, damage_scanned_at) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(member_id, period_start) DO UPDATE SET
                   damage = excluded.damage, damage_scanned_at = excluded.damage_scanned_at""",
            [(e["member_id"], period, scanned_at, e["damage"], scanned_at) for e in entries])
    return len(entries)


def save_honor_duel(entries: list[dict]) -> int:
    scanned_at = _utcnow().isoformat()
    with _connect() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO honor_duel_rankings
               (member_id, rank, honor_points, scanned_at) VALUES (?, ?, ?, ?)""",
            [(e["member_id"], e.get("rank"), e.get("points"), scanned_at) for e in entries])
    return len(entries)


def save_arcane_lab(entries: list[dict]) -> int:
    scanned_at = _utcnow().isoformat()
    with _connect() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO arcane_lab_rankings
               (member_id, rank, difficulty, floor, points, scanned_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [(e["member_id"], e.get("rank"), e.get("difficulty"), e.get("floor"),
              e.get("points"), scanned_at) for e in entries])
    return len(entries)


def save_clashfronts_signups(entries: list[dict]) -> int:
    """Full-list replace: a member absent from this scan's entries has left the
    sign-up list since the last scan (unlike the other mode tables, this isn't
    an append/overwrite-by-key history -- membership in the table IS the
    'signed up' signal, so a stale row would wrongly keep showing them as
    signed up)."""
    scanned_at = _utcnow().isoformat()
    with _connect() as conn:
        conn.execute("DELETE FROM clashfronts_signups")
        conn.executemany(
            """INSERT INTO clashfronts_signups (member_id, selected, scanned_at)
               VALUES (?, ?, ?)""",
            [(e["member_id"], 1 if e.get("selected") else 0, scanned_at) for e in entries])
        conn.commit()
    return len(entries)
