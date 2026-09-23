# Season-Scoped Guild Growth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Daniel compare RiffRaff's total guild power at the start vs. end of an ally season (or start vs. right now, for a season still in progress) via two new `/guild` Discord commands.

**Architecture:** Add a `season_id` column to `member_snapshots` (the miner's per-member-per-scan power table) that stamps every future scan with whichever `ally_seasons` row is currently `active`. Backfill existing rows to season 1. Two new bot-side subcommands resolve a season's first/last (or first/most-recent) tagged snapshot and render a per-member power-delta list, reusing the existing `/guild power`/`/guild growth` formatting conventions.

**Tech Stack:** Python 3 + sqlite3 (miner, `AFKDataMining/src/db.py`) · Node.js + discord.js v14 + better-sqlite3 (bot, `DiscordBotAfkJ/slash-commands/guild.js`) · `node --test` for bot-side unit tests · no Python test suite exists in this repo (manual verification is the established convention here).

**Spec:** `AFKDataMining/docs/superpowers/specs/2026-09-23-season-scoped-guild-growth-design.md`

## Global Constraints

- Scope is `member_snapshots` ONLY. Do not touch `arena_rankings`, `honor_duel_rankings`, `arcane_lab_rankings`, `supreme_arena_rankings`, `guild_duel_rankings`, or `clashfronts_signups` — explicitly excluded in the spec.
- `season_id` is a nullable `INTEGER REFERENCES ally_seasons(id)` — matches `dream_realm_bosses.season`'s pattern, NOT `afk_stage_rankings.season`'s bare-integer pattern (they mean different things).
- Backfill target is a flat `season_id = 1` for every existing row — no attempt to infer historical season per scan date.
- Schema changes in this repo follow "ALTER once directly against guild.db, then fold the column into the CREATE TABLE statement in `init_db()` — no migration trail replayed on every load" (confirmed: `db.py` currently has zero `ALTER TABLE` statements in `init_db()`; every past schema change was applied once by hand and folded into the CREATE). Do not write a `try/except ALTER` block that runs on every `init_db()` call — that is the bot-side (`admin/server.js`) convention, not this repo's.
- New bot commands are separate top-level `/guild` subcommands (`seasongrowth`, `seasongrowthplus`) — NOT nested under the existing `/guild growth` subcommand, since Discord allows only one level of subcommand nesting and `growth` is already a plain (unrelated) subcommand.
- A member with no snapshot at the season's start renders as `NEW · **power**`, not a dropped row and not a `0 → power` delta.

---

## Task 1: Schema change + backfill (AFKDataMining)

**Files:**
- Modify: `AFKDataMining/src/db.py:71-84` (the `CREATE TABLE member_snapshots` block, inside `init_db()`)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `member_snapshots.season_id` column, usable by Task 2's `save_snapshot()` changes and Task 4's bot-side queries.

- [ ] **Step 1: Apply the ALTER directly against the live database**

Run this once, from a Python shell or a one-off script, against the real `guild.db` (not inside `init_db()` — this repo folds schema changes into the CREATE statement after applying them once by hand, per the Global Constraints above):

```python
import sqlite3
conn = sqlite3.connect("guild.db")
conn.execute("ALTER TABLE member_snapshots ADD COLUMN season_id INTEGER REFERENCES ally_seasons(id)")
conn.execute("UPDATE member_snapshots SET season_id = 1 WHERE season_id IS NULL")
conn.commit()
conn.close()
```

- [ ] **Step 2: Verify the backfill**

Run:
```python
import sqlite3
conn = sqlite3.connect("guild.db")
conn.row_factory = sqlite3.Row
total = conn.execute("SELECT COUNT(*) AS n FROM member_snapshots").fetchone()["n"]
tagged = conn.execute("SELECT COUNT(*) AS n FROM member_snapshots WHERE season_id = 1").fetchone()["n"]
untagged = conn.execute("SELECT COUNT(*) AS n FROM member_snapshots WHERE season_id IS NULL").fetchone()["n"]
print(f"total={total} tagged_season_1={tagged} untagged={untagged}")
```
Expected: `untagged=0` and `tagged_season_1` equals `total` (every pre-existing row backfilled).

- [ ] **Step 3: Fold the column into the CREATE TABLE statement**

In `AFKDataMining/src/db.py`, update the `member_snapshots` CREATE block (currently lines 71-84):

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
                title               TEXT NOT NULL DEFAULT '',
                season_id           INTEGER REFERENCES ally_seasons(id)
            );
```

- [ ] **Step 4: Verify a fresh init_db() run is a no-op against the already-altered DB**

Run:
```bash
cd C:/vscode/AFKDataMining
python -c "from src.db import init_db; init_db(); print('ok')"
```
Expected: prints `ok` with no errors (SQLite's `CREATE TABLE IF NOT EXISTS` silently no-ops since the table already exists with the new column — this step confirms the fold-in didn't introduce a syntax error, not that it re-applies the column).

- [ ] **Step 5: Commit**

```bash
cd C:/vscode/AFKDataMining
git add src/db.py
git commit -m "feat: add season_id column to member_snapshots"
```

---

## Task 2: Stamp season_id on every scan write (AFKDataMining)

**Files:**
- Modify: `AFKDataMining/src/db.py:280-` (`save_snapshot()` — all three write paths: the UPDATE branch at line ~320, the single-row INSERT at line ~340, and the bulk `executemany` INSERT at line ~364)

**Interfaces:**
- Consumes: `_active_season_id(conn)` — already exists at `db.py:809`, returns `int | None` (the id of the `ally_seasons` row where `active = 1`, or `None` if none is set).
- Produces: every `member_snapshots` row written from this point forward carries the season that was active at the moment of that specific write (insert or update).

- [ ] **Step 1: Resolve the active season once per save_snapshot() call**

In `save_snapshot()` (`db.py:280`), immediately after the existing `with _connect() as conn:` line (~286), add:

```python
        season_id = _active_season_id(conn)
```

- [ ] **Step 2: Add season_id to the UPDATE branch**

Around line 319-337, the existing UPDATE statement is:

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

Change it to:

```python
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
```

- [ ] **Step 3: Add season_id to the single-row INSERT branch**

The existing INSERT (currently ~line 339-356):

```python
                else:
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

Change it to:

```python
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
```

- [ ] **Step 4: Add season_id to the bulk executemany INSERT (new-week path)**

The existing bulk insert (currently ~line 363-381) is:

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

Change it to:

```python
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
```

- [ ] **Step 5: Manual verification — live scan picks up the active season**

Run:
```python
import sqlite3
conn = sqlite3.connect("guild.db")
conn.row_factory = sqlite3.Row
row = conn.execute("SELECT id FROM ally_seasons WHERE active = 1").fetchone()
print("active season id:", row["id"] if row else None)
```
Then run a real `/scan` (or the equivalent scraper entry point used for manual testing in this repo). After it completes:
```python
row = conn.execute("SELECT season_id, COUNT(*) AS n FROM member_snapshots GROUP BY season_id ORDER BY season_id").fetchall()
for r in row: print(r["season_id"], r["n"])
```
Expected: the most-recently-touched rows (this week's snapshot) show `season_id` matching the active season id printed above.

- [ ] **Step 6: Commit**

```bash
cd C:/vscode/AFKDataMining
git add src/db.py
git commit -m "feat: stamp active season_id on every member_snapshots write"
```

---

## Task 3: Season start/end snapshot resolution + per-member delta helper (DiscordBotAfkJ)

**Files:**
- Modify: `DiscordBotAfkJ/slash-commands/guild.js` (add helper functions near the existing `getLatestSnapshot`/`getPrevSnapshotId` at lines 12-18)
- Test: `DiscordBotAfkJ/slash-commands/guild.test.js` (new file — `guild.js` currently has no test file; this introduces one, following the `require('dotenv').config()` + `node:test` pattern used by every other test in this repo, e.g. `utils/db.test.js`)

**Interfaces:**
- Consumes: `db` (better-sqlite3 connection) from `../utils/db`, already imported at the top of `guild.js`.
- Produces:
  - `getSeasonSnapshotRange(seasonId)` → `{ startId: number, endId: number } | null` (season-bounded: first and last snapshot tagged with that season; `null` if no rows are tagged with that season).
  - `getSeasonToNowRange(seasonId)` → `{ startId: number, endId: number } | null` (open-ended: first snapshot tagged with that season, to the single most recent snapshot overall; `null` if no rows are tagged with that season, or if there is no snapshot at all).
  - Both are consumed by Task 4's command handlers.

- [ ] **Step 1: Write the failing tests**

Create `DiscordBotAfkJ/slash-commands/guild.test.js`:

```javascript
require('dotenv').config();
const test = require('node:test');
const assert = require('node:assert/strict');
const db = require('../utils/db');
const { getSeasonSnapshotRange, getSeasonToNowRange } = require('./guild');

function makeSeason(name) {
    const r = db.prepare('INSERT INTO ally_seasons (name, active) VALUES (?, 0)').run(name);
    return r.lastInsertRowid;
}

function makeSnapshot(scrapedAt) {
    const r = db.prepare('INSERT INTO snapshots (scraped_at, member_count) VALUES (?, 0)').run(scrapedAt, 0);
    return r.lastInsertRowid;
}

function tagMemberSnapshot(snapshotId, seasonId) {
    // member_id NULL is fine here -- these tests only exercise season_id resolution,
    // not member-level joins (Task 4's tests cover the member-level query).
    db.prepare(`INSERT INTO member_snapshots
        (snapshot_id, member_id, name, combat_power, combat_power_value, activeness, season_id)
        VALUES (?, NULL, 'test', '1K', 1000, 0, ?)`).run(snapshotId, seasonId);
}

test('getSeasonSnapshotRange returns first and last snapshot tagged with a season', () => {
    const seasonId = makeSeason('__test_season_range');
    const s1 = makeSnapshot('2026-01-01T00:00:00');
    const s2 = makeSnapshot('2026-01-08T00:00:00');
    const s3 = makeSnapshot('2026-01-15T00:00:00');
    tagMemberSnapshot(s1, seasonId);
    tagMemberSnapshot(s2, seasonId);
    tagMemberSnapshot(s3, seasonId);

    const range = getSeasonSnapshotRange(seasonId);
    assert.deepEqual(range, { startId: s1, endId: s3 });
});

test('getSeasonSnapshotRange returns null for a season with no tagged snapshots', () => {
    const seasonId = makeSeason('__test_season_empty');
    assert.equal(getSeasonSnapshotRange(seasonId), null);
});

test('getSeasonToNowRange uses the season start but the single most recent snapshot overall', () => {
    const seasonId = makeSeason('__test_season_plus');
    const otherSeasonId = makeSeason('__test_season_plus_other');
    const s1 = makeSnapshot('2026-02-01T00:00:00');
    const s2 = makeSnapshot('2026-02-08T00:00:00');
    const s3Later = makeSnapshot('2026-02-15T00:00:00'); // most recent overall, different season
    tagMemberSnapshot(s1, seasonId);
    tagMemberSnapshot(s2, seasonId);
    tagMemberSnapshot(s3Later, otherSeasonId);

    const range = getSeasonToNowRange(seasonId);
    assert.deepEqual(range, { startId: s1, endId: s3Later });
});

test('getSeasonToNowRange returns null for a season with no tagged snapshots', () => {
    const seasonId = makeSeason('__test_season_plus_empty');
    assert.equal(getSeasonToNowRange(seasonId), null);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd DiscordBotAfkJ && node --test slash-commands/guild.test.js`
Expected: FAIL — `getSeasonSnapshotRange is not a function` (not yet exported from `guild.js`).

- [ ] **Step 3: Implement the two helpers and export them**

In `DiscordBotAfkJ/slash-commands/guild.js`, add after `getPrevSnapshotId` (currently ending at line 18):

```javascript
function getSeasonSnapshotRange(seasonId) {
    const start = db.prepare(`
        SELECT s.id FROM snapshots s
        JOIN member_snapshots ms ON ms.snapshot_id = s.id
        WHERE ms.season_id = ?
        ORDER BY s.scraped_at ASC LIMIT 1
    `).get(seasonId);
    const end = db.prepare(`
        SELECT s.id FROM snapshots s
        JOIN member_snapshots ms ON ms.snapshot_id = s.id
        WHERE ms.season_id = ?
        ORDER BY s.scraped_at DESC LIMIT 1
    `).get(seasonId);
    if (!start || !end) return null;
    return { startId: start.id, endId: end.id };
}

function getSeasonToNowRange(seasonId) {
    const start = db.prepare(`
        SELECT s.id FROM snapshots s
        JOIN member_snapshots ms ON ms.snapshot_id = s.id
        WHERE ms.season_id = ?
        ORDER BY s.scraped_at ASC LIMIT 1
    `).get(seasonId);
    if (!start) return null;
    const end = db.prepare('SELECT id FROM snapshots ORDER BY scraped_at DESC LIMIT 1').get();
    if (!end) return null;
    return { startId: start.id, endId: end.id };
}
```

At the bottom of the file, find the existing `module.exports = { ... }` block (currently starting at line 69, containing `data`, `autocomplete`, `execute`). Add the two new functions as additional named exports alongside it — since `module.exports` is currently a single object literal assignment, change it to also attach these two functions after the object is defined:

```javascript
module.exports.getSeasonSnapshotRange = getSeasonSnapshotRange;
module.exports.getSeasonToNowRange = getSeasonToNowRange;
```//placed immediately after the closing `};` of the existing `module.exports = { data, autocomplete, execute }` block.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd DiscordBotAfkJ && node --test slash-commands/guild.test.js`
Expected: all 4 tests PASS.

- [ ] **Step 5: Clean up test data**

Add a cleanup block so repeated test runs don't accumulate rows (matches the project's `gotcha-test-cleanup-needs-afterEach-and-prefix` convention — fixture rows use a `__test_` name prefix and must be deleted after each run, not just before):

```javascript
test.after(() => {
    db.prepare("DELETE FROM member_snapshots WHERE name = 'test' AND snapshot_id IN (SELECT id FROM snapshots WHERE scraped_at LIKE '2026-01-%' OR scraped_at LIKE '2026-02-%')").run();
    db.prepare("DELETE FROM snapshots WHERE scraped_at LIKE '2026-01-%' OR scraped_at LIKE '2026-02-%'").run();
    db.prepare("DELETE FROM ally_seasons WHERE name LIKE '__test_%'").run();
});
```

Add this `test.after` block near the top of `guild.test.js`, right after the `tagMemberSnapshot` helper function.

- [ ] **Step 6: Run the full test suite to confirm no regressions**

Run: `cd DiscordBotAfkJ && npm test`
Expected: all existing tests still PASS, plus the 4 new ones (165+4 total).

- [ ] **Step 7: Commit**

```bash
cd DiscordBotAfkJ
git add slash-commands/guild.js slash-commands/guild.test.js
git commit -m "feat: add season snapshot range resolution to guild.js"
```

---

## Task 4: /guild seasongrowth and /guild seasongrowthplus commands (DiscordBotAfkJ)

**Files:**
- Modify: `DiscordBotAfkJ/slash-commands/guild.js` (add two subcommands to the `SlashCommandBuilder` at lines 69-116, add autocomplete routing at lines 118-124, add dispatch cases at lines 139-150, add two new handler functions)

**Interfaces:**
- Consumes: `getSeasonSnapshotRange(seasonId)` and `getSeasonToNowRange(seasonId)` from Task 3 (same file, already in scope — no import needed). `fmtPower(val)`, `pickColor()`, `snapshotDate(snapshot)` — all already exist in this file.
- Produces: two new live Discord subcommands. Nothing downstream depends on these.

- [ ] **Step 1: Add the two subcommands to the SlashCommandBuilder**

In `DiscordBotAfkJ/slash-commands/guild.js`, the `data: new SlashCommandBuilder()` chain currently ends with:

```javascript
        .addSubcommand(s => s
            .setName('unlinked')
            .setDescription('Active members not yet linked to a Discord account')),

    async autocomplete(interaction) {
```

Change it to:

```javascript
        .addSubcommand(s => s
            .setName('unlinked')
            .setDescription('Active members not yet linked to a Discord account'))
        .addSubcommand(s => s
            .setName('seasongrowth')
            .setDescription('Guild power growth from a season\'s start to its end')
            .addStringOption(o => o.setName('season').setDescription('Season name').setRequired(true).setAutocomplete(true)))
        .addSubcommand(s => s
            .setName('seasongrowthplus')
            .setDescription('Guild power growth from a season\'s start to right now')
            .addStringOption(o => o.setName('season').setDescription('Season name').setRequired(true).setAutocomplete(true))),

    async autocomplete(interaction) {
```

- [ ] **Step 2: Route autocomplete for the new `season` option**

The existing `autocomplete` handler (lines 118-124) only handles the `warband` option (implicitly — it doesn't check the option name at all, since `warband` is currently the only autocompleted option in this command). Replace it with a routed version:

```javascript
    async autocomplete(interaction) {
        const focused = interaction.options.getFocused(true);
        const query = focused.value.toLowerCase();
        if (focused.name === 'season') {
            const seasons = db.prepare('SELECT name FROM ally_seasons ORDER BY id DESC').all()
                .map(r => r.name)
                .filter(n => n.toLowerCase().includes(query))
                .slice(0, 25);
            await interaction.respond(seasons.map(n => ({ name: n, value: n })));
            return;
        }
        const filtered = db.getWarbands()
            .filter(w => w.name.toLowerCase().includes(query))
            .slice(0, 25);
        await interaction.respond(filtered.map(w => ({ name: w.name, value: w.name })));
    },
```

- [ ] **Step 3: Add dispatch cases**

In the `execute` function's `switch (sub)` block (lines 139-150), the `unlinked` case is already special-cased earlier (line 129-132, before the `getLatestSnapshot()` call) because it doesn't need a snapshot. The two new subcommands also don't need `getLatestSnapshot()` (they resolve their own snapshot range internally), so add them to that same early-return block:

```javascript
        if (sub === 'unlinked') {
            await handleUnlinked(interaction);
            return;
        }
        if (sub === 'seasongrowth' || sub === 'seasongrowthplus') {
            await handleSeasonGrowth(interaction, sub === 'seasongrowthplus');
            return;
        }
```

Insert this immediately after the existing `unlinked` block (currently lines 129-132), before the `const snapshot = getLatestSnapshot();` line.

- [ ] **Step 4: Implement handleSeasonGrowth**

Add this function near `handleGrowth` (after it, e.g. following line 305):

```javascript
async function handleSeasonGrowth(interaction, toNow) {
    const seasonName = interaction.options.getString('season');
    const season = db.prepare('SELECT id, name FROM ally_seasons WHERE name = ?').get(seasonName);
    if (!season) {
        return interaction.reply({ content: `No season named **${seasonName}**.`, flags: MessageFlags.Ephemeral });
    }

    const range = toNow ? getSeasonToNowRange(season.id) : getSeasonSnapshotRange(season.id);
    if (!range) {
        return interaction.reply({ content: `No scan data found for **${season.name}**.`, flags: MessageFlags.Ephemeral });
    }

    const rows = db.prepare(`
        SELECT m.id AS member_id, m.ingame_name AS name,
               ms2.combat_power_value AS current_power,
               ms1.combat_power_value AS prev_power
        FROM member_snapshots ms2
        JOIN members m ON m.id = ms2.member_id AND m.active = 1
        LEFT JOIN member_snapshots ms1 ON ms1.member_id = ms2.member_id AND ms1.snapshot_id = ?
        WHERE ms2.snapshot_id = ?
        ORDER BY (ms2.combat_power_value - COALESCE(ms1.combat_power_value, 0)) DESC
    `).all(range.startId, range.endId);

    const lines = rows.map((r, i) => {
        const rank = `\`${String(i + 1).padStart(2)}.\` **${r.name}**`;
        if (r.prev_power == null) {
            return `${rank} · NEW · **${fmtPower(r.current_power)}**`;
        }
        const g = r.current_power - r.prev_power;
        const growthStr = g > 0 ? `+${fmtPower(g)}` : g < 0 ? `-${fmtPower(Math.abs(g))}` : '+0';
        return `${rank} · ${fmtPower(r.prev_power)} → **${fmtPower(r.current_power)}** (${growthStr})`;
    });

    const title = toNow
        ? `📈 ${season.name} · Growth (start → now)`
        : `📈 ${season.name} · Growth (start → end)`;

    await interaction.reply({ embeds: [
        new EmbedBuilder()
            .setTitle(title)
            .setDescription(lines.join('\n') || 'No active members found.')
            .setColor(pickColor()),
    ]});
}
```

- [ ] **Step 5: Manual verification — live command test**

Deploy to the test bot (per this project's standing test-build convention, or Daniel's private bot-chatter channel on the real bot for a single-command change — confirm which with Daniel before running):

```
pm2 restart meerbot-test
```

In Discord, run `/guild seasongrowth season:<a season name>` and `/guild seasongrowthplus season:<a season name>`. Confirm:
1. Autocomplete on the `season` option shows real season names from `ally_seasons`.
2. The embed renders a ranked list, highest growth first.
3. A member who joined after the season's start snapshot shows `NEW · power`, not a delta.
4. `seasongrowthplus` against the currently active season shows an end power matching the most recent `/guild power` output.
5. An invalid/nonexistent season name returns the "No season named" error, not a crash.

- [ ] **Step 6: Commit**

```bash
cd DiscordBotAfkJ
git add slash-commands/guild.js
git commit -m "feat: add /guild seasongrowth and /guild seasongrowthplus commands"
```

---

## Task 5: Docs sync

**Files:**
- Modify: `DiscordBotAfkJ/CLAUDE.md` (Slash Commands table)
- Modify: `DiscordBotAfkJ/README.md` (if it lists `/guild` subcommands — check first)
- Modify: `DiscordBotAfkJ/docs/bot-guide.md` (Ask MeerBot's grounding doc — check if `/guild` subcommands are enumerated there)
- Modify: `AFKDataMining/CLAUDE.md` (if `member_snapshots` schema is documented there — check first)

Per this project's standing convention (`feedback-docs-sync-on-commit` memory: "update help.js, CLAUDE.md (project + global) with every commit") and `feedback-doc-roles-readme-arch-claudemd` (README = what it does, ARCHITECTURE = how it works, CLAUDE.md = Claude's own source of truth — check all three, not just CLAUDE.md).

- [ ] **Step 1: Check which docs currently mention /guild subcommands or member_snapshots schema**

Run:
```bash
cd DiscordBotAfkJ
grep -rln "guild chart\|guild growth\|guild power\|member_snapshots" CLAUDE.md README.md docs/bot-guide.md 2>/dev/null
grep -rln "member_snapshots" ../AFKDataMining/CLAUDE.md ../AFKDataMining/README.md ../AFKDataMining/ARCHITECTURE.md 2>/dev/null
```

- [ ] **Step 2: Add the two new subcommands to DiscordBotAfkJ/CLAUDE.md's Slash Commands table**

Find the `/guild chart` row in the Slash Commands table and add two new rows immediately after it:

```
| `/guild seasongrowth season:` | Full active-member power growth from a season's first tagged scan to its last · ranked by growth descending · members with no start-of-season data show as NEW |
| `/guild seasongrowthplus season:` | Same as `seasongrowth` but end = the most recent scan overall, for a season still in progress |
```

- [ ] **Step 3: Add season_id to the member_snapshots schema note in AFKDataMining's CLAUDE.md, if that table is documented there**

If Step 1's grep found `member_snapshots` mentioned in `AFKDataMining/CLAUDE.md`, add a note there: `season_id` (nullable, FK → `ally_seasons.id`, stamped with the currently-active season on every write from `save_snapshot()`; pre-existing rows backfilled to season 1 on 2026-09-23).

- [ ] **Step 4: Update README.md and docs/bot-guide.md only if Step 1 found existing /guild command documentation there**

If either file lists `/guild` subcommands (README's feature list, or bot-guide.md's command summary used by Ask MeerBot's DM handler), add the same two-line summary as Step 2. If neither file enumerates individual `/guild` subcommands today, skip this step — don't introduce a new documentation section that wasn't there before.

- [ ] **Step 5: Commit**

```bash
cd DiscordBotAfkJ
git add CLAUDE.md README.md docs/bot-guide.md 2>/dev/null
git commit -m "docs: document /guild seasongrowth and seasongrowthplus commands"
cd ../AFKDataMining
git add CLAUDE.md 2>/dev/null
git commit -m "docs: document member_snapshots.season_id" --allow-empty-message 2>/dev/null || true
```

(The AFKDataMining commit only happens if Step 3 actually changed that file — if `git add` stages nothing, skip the commit rather than creating an empty one.)
