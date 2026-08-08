# In-Game Title Badge Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture each guild member's in-game role title (Riff, Raff, Fellowship, Confidant, Immortal, Envoy, Warmaster, Sage) during the existing guild-roster scan, canonicalize it against the fixed 8-word vocabulary, and store both a per-scan history value and a current-state value — mirroring the existing `warband`/`warband_id` pattern.

**Architecture:** The OCR engine already reads these title badges as their own text block on every guild scan — `parser.py`'s `NAME_X_MIN = 130` constant exists specifically to exclude them from name detection (they render at x≈45-111, ~34-46px above each member's name row; see the constant's comment for the 2026-07-04 phantom-member bug this constant fixed). This plan adds a *new* lookup that claims exactly the space `NAME_X_MIN` already rejects — it does not touch `NAME_X_MIN`, `parse_member_anchors()`, or any existing name-detection logic. Canonicalization happens inline in `parser.py` (not in `db.py` like `_resolve_warband()`) because the title vocabulary is fixed/closed and hardcoded, not an open-ended admin-managed table — there is nothing to look up. `member_snapshots.title` stores the per-scan canonical read (history); `members.title` stores the current value, synced forward after each scan exactly like `members.warband_id`.

**Tech Stack:** Python, sqlite3, RapidOCR (via existing `ocr.py`/`parser.py`), `difflib.SequenceMatcher` (already used elsewhere in this codebase for fuzzy text matching).

## Global Constraints

- Do not modify `NAME_X_MIN`, `parse_member_anchors()`, or `_is_name_block()` — verified working against 3 live captures across all title styles (see evidence below); any change risks reintroducing the 2026-07-04 phantom-member bug.
- Canonicalize to one of the 8 known titles *inside* `parse_members()`, before the value leaves `parser.py` — never vote on raw/uncanonicalized OCR text (a per-frame misread like `[mmortal` must resolve to `Immortal` before voting, or 3 frames reading the same person can split their vote across spellings).
- `Riff` and `Raff` are edit-distance 1 apart — canonicalization must either match one of them exactly or flag the read as ambiguous; never let a fuzzy-match silently pick between the two.
- Every member always has exactly one of the 8 titles (confirmed by the user) — a canonicalization miss is always an anomaly to log, never a valid "no title" state.
- Follow this repo's existing schema convention: `ALTER TABLE` once against the live `guild.db`, then fold the column into the `CREATE TABLE IF NOT EXISTS` in `db.py` — no migration-runner code.
- This repo's test convention is manual smoke-test scripts run against a real captured image (see `src/test_ocr.py`), not pytest — follow that same pattern, do not introduce a new test framework.

**Evidence gathered before writing this plan** (3 live ADB captures via `src/capture_test.py` against the current RKF RiffRaff roster, OCR'd with the existing `ocr_image()`):

| Title | OCR x | y-offset above name |
|---|---|---|
| Fellowship | 44-45 | 37-43 |
| Confidant | 53 | 37-38 |
| Riff | 111 | 46 |
| Raff | 69 | 36 |
| Immortal | 82 (OCR'd as `[mmortal`) | 34 |
| Envoy | 85 | 43 |
| Warmaster | 82 | 45 |

(Sage not captured — only 1 holder guild-wide; the vocabulary/anomaly-log approach below will surface it on first read if its geometry ever differs from the other 7.) All 7 observed titles sit at x ≤ 111, comfortably under `NAME_X_MIN = 130`; y-offset is consistently 34-46px above the paired name block. The "Friends" badge (unrelated, always ignorable per user) OCRs at x≈231-240, well outside this band, so no extra filtering is needed to exclude it.

---

### Task 1: Canonicalize + extract title in `parser.py`

**Files:**
- Modify: `src/parser.py`
- Test: `src/test_ocr.py` (extend existing manual smoke-test script)

**Interfaces:**
- Consumes: `NAME_X_MIN` (existing constant, `src/parser.py:32`), `_find_near_y()` (existing helper, `src/parser.py:69-73`), `_parse_blocks()` (existing helper, `src/parser.py:60-66`)
- Produces: `Member.title: str` field (default `''`), `_canonicalize_title(text: str) -> str | None` function (returns one of the 8 canonical titles, or `None` if no confident match), `TITLES` frozenset constant

- [ ] **Step 1: Add the `TITLES` vocabulary constant and `_canonicalize_title()` function**

Add near the top of `src/parser.py`, after the existing `WARBAND`-adjacent constants (around line 32, right after `NAME_X_MIN = 130`):

```python
# Fixed, closed vocabulary of in-game role titles (unlike warbands, which are
# open-ended and admin-managed via the warbands table) -- every member always
# has exactly one of these 8, so canonicalization happens here in parser.py,
# not via a DB lookup like _resolve_warband(). Riff/Raff are edit-distance 1
# apart, so they require an exact (case-insensitive) match; the other 6 tolerate
# a fuzzy match for common OCR noise (e.g. "Immortal" misread as "[mmortal").
TITLES = frozenset({"Riff", "Raff", "Fellowship", "Confidant", "Immortal", "Envoy", "Warmaster", "Sage"})
_TITLES_LOWER = {t.lower(): t for t in TITLES}
_AMBIGUOUS_LOWER = {"riff", "raff"}


def _canonicalize_title(text: str) -> str | None:
    """Resolve an OCR'd title-badge block to one of the 8 known titles, or None
    if no confident match. Strips leading OCR junk (matches _clean_name's
    approach) before matching. Riff/Raff must match exactly (case-insensitive)
    -- their edit distance is 1, so fuzzy matching between them is unsafe;
    the other 6 titles tolerate a fuzzy match (ratio >= 0.8) for OCR noise."""
    cleaned = re.sub(r"^[\W_]+|[\W_]+$", "", text)
    if not cleaned:
        return None
    low = cleaned.lower()

    if low in _AMBIGUOUS_LOWER:
        return _TITLES_LOWER[low]
    if low in _TITLES_LOWER:
        return _TITLES_LOWER[low]

    best, score = None, 0.0
    for tl, canonical in _TITLES_LOWER.items():
        if tl in _AMBIGUOUS_LOWER:
            continue  # never fuzzy-match into Riff/Raff -- exact match only
        r = SequenceMatcher(None, low, tl).ratio()
        if r > score:
            best, score = canonical, r
    if best and score >= 0.8:
        return best
    return None
```

- [ ] **Step 2: Add `title` field to the `Member` dataclass**

In `src/parser.py`, modify the existing `Member` dataclass (currently lines 50-57):

```python
@dataclass
class Member:
    name: str
    last_active: str
    combat_power: str
    activeness: int
    warband: str = ''
    warband_id: int | None = None
    title: str = ''
```

- [ ] **Step 3: Extract the title in `parse_members()`**

In `src/parser.py`, inside `parse_members()` (currently lines 116-181), add the title lookup right after the `last_active` block (after line 141, before the `# Power / warband / activeness row` comment on line 143):

```python
        # Title badge: a block left of the name column (the space NAME_X_MIN
        # excludes), 30-50px above the name row. Every member always has
        # exactly one of the 8 known titles -- a miss here is logged as an
        # anomaly, never treated as a valid blank (mirrors how last_active
        # falls back to "Unknown" rather than silently dropping the member).
        title = ''
        for x, y, t in _find_near_y(blocks, ny - 40, 15, x_min=0, x_max=NAME_X_MIN):
            canonical = _canonicalize_title(t)
            if canonical:
                title = canonical
                break
        if not title:
            import logging
            logging.getLogger(__name__).warning(
                "No title matched for member %r near y=%d -- OCR miss or new title style", name, ny)
```

Then add `title=title` to the `Member(...)` construction at the end of the loop (currently lines 173-179):

```python
        members.append(Member(
            name=name,
            last_active=last_active,
            combat_power=combat_power,
            activeness=activeness,
            warband=warband,
            title=title,
        ))
```

- [ ] **Step 4: Manual verification against a live capture**

Run the existing smoke-test script against one of the captures gathered for this plan (or a fresh one via `python src/capture_test.py`, which writes `test_capture.png` to the repo root):

```
cd C:\vscode\AFKDataMining
venv\Scripts\python.exe src\test_ocr.py test_capture.png
```

Extend `src/test_ocr.py`'s print loop (currently line 27) to include title:

```python
    print(f"  name={m.name!r}  last_active={m.last_active!r}  power={m.combat_power!r}  activeness={m.activeness}  warband={m.warband!r}  title={m.title!r}")
```

Expected: every member printed has a non-empty `title` matching one of the 8 known values, and it matches what's visible in the screenshot for that member. If any member shows `title=''`, check stderr/logs for the "No title matched" warning and inspect that row's OCR blocks directly (`src/test_ocr.py`'s "Raw OCR blocks" section already prints every block with its x/y).

- [ ] **Step 5: Commit**

```bash
cd C:\vscode\AFKDataMining
git add src/parser.py src/test_ocr.py
git commit -m "feat: extract and canonicalize in-game title badges

Every guild member has one of 8 fixed titles (Riff, Raff, Fellowship,
Confidant, Immortal, Envoy, Warmaster, Sage) rendered as a badge above
their avatar. The OCR already reads this block -- NAME_X_MIN exists
specifically to exclude it from name detection. This adds a lookup
that claims exactly that excluded space, canonicalizes against the
fixed vocabulary (Riff/Raff require exact match, edit-distance 1;
the other 6 tolerate fuzzy matching for OCR noise), and logs an
anomaly on miss since every member always has a title."
```

---

### Task 2: Vote-safe propagation through the scan pipeline

**Files:**
- Modify: `src/scraper.py`

**Interfaces:**
- Consumes: `Member.title` (from Task 1)
- Produces: `title` key flows through `_guild_parse()`'s dict output and `_entry_to_member()`'s reconstruction, matching the existing `warband` field's treatment exactly

- [ ] **Step 1: Add `title` to `_guild_parse()`'s dict conversion**

In `src/scraper.py`, modify `_guild_parse()` (currently lines 65-79):

```python
def _guild_parse(img, ocr_results) -> list[dict]:
    """parse_fn for the shared scroll_scan · the guild scan's 'what': parse member
    cards into dicts. Empty/zero/Unknown fields become None so _vote_entries skips
    them when voting (an unread power/warband never outvotes a real one). img is
    unused (parse_members works from the OCR results), kept for the parse_fn contract."""
    out = []
    for m in parse_members(ocr_results):
        out.append({
            "name": m.name,
            "last_active": m.last_active if m.last_active not in ("Unknown", "") else None,
            "combat_power": m.combat_power or None,
            "activeness": m.activeness or None,
            "warband": m.warband or None,
            "title": m.title or None,
        })
    return out
```

- [ ] **Step 2: Add `title` to `_entry_to_member()`'s reconstruction**

In `src/scraper.py`, modify `_entry_to_member()` (currently lines 82-90):

```python
def _entry_to_member(e: dict) -> Member:
    """Voted entry dict -> Member, restoring the empty/zero/Unknown defaults."""
    return Member(
        name=e["name"],
        last_active=e.get("last_active") or "Unknown",
        combat_power=e.get("combat_power") or "",
        activeness=e.get("activeness") or 0,
        warband=e.get("warband") or "",
        title=e.get("title") or "",
    )
```

- [ ] **Step 3: Verify `_vote_entries()` needs no changes**

Read `src/modes/common.py`'s `_vote_entries()` (around line 355). Confirm it operates generically on dict keys (Counter-based per-field majority vote) with no hardcoded field list — `title` should be voted on automatically once present in the dicts from Task 2 Step 1, the same way `warband` already is. This step is a read-only confirmation; if `_vote_entries()` turns out to have a hardcoded field list (it does not, per the codebase read done before writing this plan, but confirm before proceeding), that would require an additional change not covered by this plan — stop and report back rather than guessing.

- [ ] **Step 4: Manual verification**

Run a real scan end-to-end is out of scope for this manual step (that requires the full ADB/BlueStacks scroll-scan flow) — instead, confirm via code inspection that both dict conversions (Step 1 and Step 2) are symmetric: every key `_guild_parse()` produces has a matching `.get()` in `_entry_to_member()`, and the None-on-empty / restore-default convention matches `warband`'s exactly. Diff the two functions side by side and confirm `title` and `warband` are treated identically in both directions.

- [ ] **Step 5: Commit**

```bash
cd C:\vscode\AFKDataMining
git add src/scraper.py
git commit -m "feat: propagate title through the scan vote/dict pipeline

Mirrors warband's existing treatment exactly: None when unread (so an
unread title never outvotes a real one in _vote_entries), restored to
empty string default in _entry_to_member. No changes needed to
_vote_entries itself -- it's already generic over dict keys."
```

---

### Task 3: Schema — `member_snapshots.title` and `members.title`

**Files:**
- Modify: `src/db.py`

**Interfaces:**
- Consumes: `Member.title` (from Task 1/2)
- Produces: `member_snapshots.title TEXT NOT NULL DEFAULT ''` column (history), `members.title TEXT` column (current state, synced forward)

- [ ] **Step 1: Run the one-time `ALTER TABLE` against the live `guild.db`**

This is a live, shared database (the bot reads/writes it concurrently) — run this once, directly, not as application code:

```
cd C:\vscode\AFKDataMining
venv\Scripts\python.exe -c "
import sqlite3
conn = sqlite3.connect('guild.db')
conn.execute('ALTER TABLE member_snapshots ADD COLUMN title TEXT NOT NULL DEFAULT \'\'')
conn.execute('ALTER TABLE members ADD COLUMN title TEXT')
conn.commit()
print('Done.')
"
```

Verify:
```
venv\Scripts\python.exe -c "
import sqlite3
conn = sqlite3.connect('guild.db')
print([r[1] for r in conn.execute('PRAGMA table_info(member_snapshots)')])
print([r[1] for r in conn.execute('PRAGMA table_info(members)')])
"
```

Expected: `title` appears in both column lists.

- [ ] **Step 2: Fold the columns into the `CREATE TABLE IF NOT EXISTS` statements**

In `src/db.py`, modify `init_db()`'s `members` table CREATE (currently lines 48-60):

```python
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
```

And the `member_snapshots` table CREATE (currently lines 70-82):

```python
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
                title               TEXT NOT NULL DEFAULT ''
            );
```

- [ ] **Step 3: Write `title` into both `save_snapshot()` write branches**

In `src/db.py`'s `save_snapshot()`, three SQL statements currently write `warband`/`warband_id` and need `title` added alongside:

The UPDATE branch (currently lines 302-319):
```python
                    conn.execute(
                        """UPDATE member_snapshots
                           SET name = ?, last_active = ?, last_seen_approx = ?,
                               combat_power = ?, combat_power_value = ?, activeness = ?,
                               warband = ?, warband_id = ?, title = ?
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
                            snapshot_id,
                            member_id,
                        ),
                    )
```

The first INSERT branch (currently lines 321-337):
```python
                    conn.execute(
                        """INSERT INTO member_snapshots
                           (snapshot_id, member_id, name, last_active, last_seen_approx,
                            combat_power, combat_power_value, activeness, warband, warband_id, title)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                        ),
                    )
```

The bulk `executemany` INSERT branch (currently lines 344-360, the `else:` fallback when no snapshot exists yet this week) — apply the same column/placeholder addition:
```python
            conn.executemany(
                """INSERT INTO member_snapshots
                   (snapshot_id, member_id, name, last_active, last_seen_approx,
                    combat_power, combat_power_value, activeness, warband, warband_id, title)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    )
                    for m, member_id in zip(members, member_ids)
                ],
            )
```

- [ ] **Step 4: Sync `members.title` forward, mirroring the `warband_id` sync**

In `src/db.py`'s `save_snapshot()`, right after the existing warband sync block (currently lines 377-399, the `UPDATE members SET warband_id = ...` and its blank-fallback), add an equivalent title sync:

```python
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
```

- [ ] **Step 5: Manual verification**

Run a full scan (or replay `save_snapshot()` against parsed output from a test capture) and confirm via direct query:

```
venv\Scripts\python.exe -c "
import sqlite3
conn = sqlite3.connect('guild.db')
conn.row_factory = sqlite3.Row
rows = conn.execute('SELECT ingame_name, title FROM members WHERE title IS NOT NULL LIMIT 10').fetchall()
for r in rows:
    print(dict(r))
"
```

Expected: title values are all one of the 8 canonical strings (exact casing: `Riff`, `Raff`, `Fellowship`, `Confidant`, `Immortal`, `Envoy`, `Warmaster`, `Sage`), no blank/garbled entries for members that were actually read this scan.

- [ ] **Step 6: Commit**

```bash
cd C:\vscode\AFKDataMining
git add src/db.py
git commit -m "feat: store title in member_snapshots (history) and members (current)

Mirrors the warband/warband_id split exactly: member_snapshots.title
is the per-scan free-text history, members.title is synced forward
after each scan (only on non-blank reads, so an unread title never
wipes a known one) for O(1) 'what's their title right now' lookups
without a latest-snapshot join. ALTER TABLE already run once against
the live guild.db; this folds both columns into the CREATE statements
per this repo's schema-ownership convention."
```

---

## Explicitly Out of Scope (Phase 2)

The "title says Confidant but discord_id is set" sync-report / contradiction-check feature is **not** part of this plan. Only one direction of that comparison was specified by the user (Confidant means "not on the Discord server" — a member with that title who is nonetheless linked is worth flagging); the reverse direction (any other title implying Discord presence) was never confirmed and should not be inferred or built without asking first. That logic belongs in the bot repo (`C:\vscode\DiscordBotAfkJ`), reading `members.title` from the shared `guild.db` this plan populates, once its exact semantics are confirmed with the user.
