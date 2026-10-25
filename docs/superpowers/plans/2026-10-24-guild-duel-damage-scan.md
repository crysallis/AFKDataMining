# Guild Duel damage scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collect each guild member's weekend boss damage from the "Total Duel Merit Rankings" screen into the existing weekly Guild Duel rows, without disturbing the Monday crests scan or anything that reads those rows.

**Architecture:** Add `damage` and `damage_scanned_at` columns to `guild_duel_rankings` (miner owns the schema). A new miner scan mode writes damage with an UPSERT that touches only those columns. The existing crests save becomes an UPSERT too, the Monday gate and the "missing" lookups key on `crests IS NOT NULL`, and every bot or stats reader tolerates a damage-only row. The new mode is wired like Guild Duel and ships off by default.

**Tech Stack:** Python (miner, plain-script tests run with `venv/Scripts/python.exe`, no pytest), Node `node --test` (bot), better-sqlite3, one shared `guild.db`.

**Spec:** `docs/superpowers/specs/2026-10-24-guild-duel-damage-scan-design.md` (miner repo). Tasks 1-4 are in `C:\vscode\AFKDataMining`, tasks 5-7 in `C:\vscode\DiscordBotAfkJ`.

## Global Constraints

- No em dashes anywhere (use `·` or `...`). No unnecessary comments in code.
- Damage is stored as an integer in raw units: `567B` is `567000000000`.
- The new scan only runs Saturday 00:00 UTC through Sunday 23:59:59 UTC, unless forced.
- Period key for the new scan is the most recent Monday on or before now (UTC), which equals what `guild_duel_period_key` returns one week later.
- Crests scan stays Battle Report based and unchanged apart from its save and gate.
- Phase one only: no per-hit math, no `/guildduel low` change, no admin threshold, no stats damage display.
- Both repos are public: no screenshots or real member names in commits, tests or fixtures (use made-up names).
- Tests must never touch the real `guild.db` rows (temp DB or in-memory; the one exception in Task 7 uses prefixed fixtures cleaned up by exact id).
- The user runs every `pm2 restart`; never run one.
- Schema change: ALTER once against `guild.db`, then fold into the owner's CREATE.

## Review Focus

- A scan run after Monday 00:00 UTC must refuse and write nothing (Task 1 window tests, Task 4 live check).
- An OCR damage text with no suffix or garbage must drop the row, never save a 0 (Task 3).
- A damage-only week must not stop the Monday crests scan and must not hide anyone from the missing list (Task 2).
- A damage-only row must not create a blank period on the stats page or change a member's `/member` Guild Duel line (Task 6).
- Merging two members with a row for the same week must not lose damage or crests held only by the dropped row (Task 7).

---

### Task 1: Schema, period key and window gate (miner)

**Files:**
- Modify: `src/db.py` (the `guild_duel_rankings` CREATE near line 154; two new pure functions beside `guild_duel_period_key` near line 948)
- Create: `test_guild_duel_damage.py` (plain script in the style of `test_guild_duel_period.py`)

**Interfaces:**
- Produces: `guild_duel_damage_period_key(now: datetime) -> str` and `guild_duel_damage_window_open(now: datetime) -> bool` in `db.py`; columns `damage INTEGER`, `damage_scanned_at TEXT` on `guild_duel_rankings`.

- [ ] **Step 1: Commit the spec and plan as-is** (`git add docs/superpowers`, message `docs: guild duel damage scan spec and plan`).
- [ ] **Step 2: Write the failing tests** in `test_guild_duel_damage.py`, printing PASS/FAIL per case and exiting non-zero on any failure:
  - `guild_duel_damage_period_key`: Sat `datetime(2026,10,24)` returns `"2026-10-19"`; Sun `datetime(2026,10,25,23,59,59)` returns `"2026-10-19"`; Mon `datetime(2026,10,26)` returns `"2026-10-26"`; and `guild_duel_period_key(datetime(2026,10,27)) == guild_duel_damage_period_key(datetime(2026,10,24))`.
  - `guild_duel_damage_window_open`: Fri `datetime(2026,10,23,23,59,59)` is `False`; Sat `datetime(2026,10,24,0,0,0)` is `True`; Sun `datetime(2026,10,25,23,59,59)` is `True`; Mon `datetime(2026,10,26,0,0,0)` is `False`.
- [ ] **Step 3: Run** `venv/Scripts/python.exe test_guild_duel_damage.py`. Expected: FAIL (ImportError).
- [ ] **Step 4: Implement** both functions in `db.py` as pure functions (no DB access), and add the two columns to the CREATE statement.
- [ ] **Step 5: Add the columns to the live database once:** `ALTER TABLE guild_duel_rankings ADD COLUMN damage INTEGER` and `... ADD COLUMN damage_scanned_at TEXT`, run through the miner's `_connect()`. Check with `PRAGMA table_info(guild_duel_rankings)`.
- [ ] **Step 6: Run** the test script. Expected: all cases PASS.
- [ ] **Step 7: Commit** (`feat: guild duel damage columns, period key and weekend window`).

### Task 2: Saves and gates tolerate damage-only rows (miner)

**Files:**
- Modify: `src/db.py` (`save_guild_duel` near line 1063, `get_guild_duel_period` near 965, `get_guild_duel_missing` near 990; new `save_guild_duel_damage`)
- Test: `test_guild_duel_damage.py` (add DB-backed cases)

**Interfaces:**
- Consumes: Task 1 columns.
- Produces: `save_guild_duel_damage(entries: list[dict], period: str) -> int` where each entry has `member_id` and `damage`; `save_guild_duel` keeps its signature but upserts only `rank`, `crests`, `scanned_at`.

- [ ] **Step 1: Write the failing DB tests** against a temp database file (read the top of `db.py` for how the DB path is chosen and point it at a temp file before importing; call `init_db()`; create two members through the module's own helper). Cases:
  - `save_guild_duel_damage` then `save_guild_duel` for the same member and period leaves `damage` and `damage_scanned_at` intact and sets `rank`, `crests`.
  - `save_guild_duel` then `save_guild_duel_damage` leaves `rank`, `crests` and `scanned_at` intact.
  - Saving damage twice overwrites only damage.
  - With only a damage row for a period, `get_guild_duel_period` (not forced) returns that period's key; once a crests row exists it returns `None`; `force=True` always returns the key.
  - `get_guild_duel_missing(period)` lists a member whose only row is damage-only, and omits one who has crests.
- [ ] **Step 2: Run** the script. Expected: the new cases FAIL.
- [ ] **Step 3: Implement.** `save_guild_duel_damage` stamps `damage_scanned_at` with `_utcnow().isoformat()` and uses `INSERT ... ON CONFLICT(member_id, period_start) DO UPDATE SET damage, damage_scanned_at`; for a brand new row it sets `scanned_at` to the same instant only because the column is NOT NULL, and the upsert never rewrites `scanned_at` on conflict. Change `save_guild_duel` from `INSERT OR REPLACE` to the same ON CONFLICT shape setting only `rank`, `crests`, `scanned_at`. Change the gate query to `... AND crests IS NOT NULL` and the missing query's NOT EXISTS subquery likewise.
- [ ] **Step 4: Run** the script. Expected: all PASS. Also run `venv/Scripts/python.exe src/add_guild_duel.py --missing` and confirm it still prints a sensible list (it reads the changed lookup).
- [ ] **Step 5: Commit** (`feat: guild duel saves and gates tolerate damage-only rows`).

### Task 3: Damage parsing (miner)

**Files:**
- Create: `src/modes/guild_duel_damage.py` (parsing half only in this task)
- Test: `test_guild_duel_damage.py`

**Interfaces:**
- Consumes: `RankRow` (fields `rank`, `name`, `numbers`, `texts`, `y`) from `modes.common`; `_is_our_guild(texts)` from `modes.guild_duel`.
- Produces: `parse_damage(text: str) -> int | None` and `rows_to_entries(rows: list[RankRow]) -> list[dict]` returning `{"name", "damage"}`; constant `LIST_Y_MIN = 700` (starting value, tuned live in Task 4).

- [ ] **Step 1: Write the failing tests.**
  - `parse_damage`: `"567B"` is `567_000_000_000`; `"1.2T"` is `1_200_000_000_000`; `"850M"` is `850_000_000`; `"12,345K"` is `12_345_000`; `" 567 b "` is `567_000_000_000`; `"567"`, `"Total Damage"`, `""` and `"B"` are `None`.
  - `rows_to_entries` with made-up names: a row tagged `RKF RiffRaff` with texts `["Total Damage", "567B", "RKF RiffRaff"]` yields one entry with `damage` 567_000_000_000; a row tagged for the opponent is dropped; a row with our tag but no parseable damage text (the pinned "Unranked" bar) is dropped, not saved as 0.
- [ ] **Step 2: Run.** Expected: FAIL (module missing).
- [ ] **Step 3: Implement** both functions. `parse_damage` strips commas and spaces, takes a float mantissa and a K/M/B/T suffix (case-insensitive) and returns `round(mantissa * multiplier)`; `rows_to_entries` takes the first text block that parses as damage.
- [ ] **Step 4: Run.** Expected: all PASS.
- [ ] **Step 5: Commit** (`feat: guild duel damage parsing`).

### Task 4: Navigation, scan mode and wiring (miner, live-verified)

**Files:**
- Modify: `src/nav.py` (extract a shared helper from `navigate_to_guild_duel_battle_report` near line 565; add `navigate_to_guild_duel_merit_rankings`)
- Modify: `src/device.py` (add `guild_duel_damage_scroll_down()` beside `guild_duel_scroll_down`)
- Modify: `src/modes/guild_duel_damage.py` (add `scan`)
- Modify: `src/scraper.py` (`MODES` at line 186; `--force-rescan` handling at lines 217 and 235)

**Interfaces:**
- Consumes: Tasks 1-3 (`guild_duel_damage_period_key`, `guild_duel_damage_window_open`, `save_guild_duel_damage`, `_parse`/`rows_to_entries`).
- Produces: `_enter_guild_duel_card(ocr_image) -> bool` in `nav.py` (overview, Battle Modes, "Guild Mode" tab, Guild Duel card, each followed by the existing stable-wait; returns `False` on any miss so the caller re-homes); `navigate_to_guild_duel_merit_rankings(flow_attempts: int = 3) -> None`; `scan(force: bool = False) -> str` returning `"skipped"` or `"success"`; CLI flag `--guild-duel-damage`.

- [ ] **Step 1: Refactor `navigate_to_guild_duel_battle_report`** to call `_enter_guild_duel_card`, behavior unchanged.
- [ ] **Step 2: Verify the refactor live:** `venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'src'); from nav import navigate_to_guild_duel_battle_report as n; n()"`. Expected: log line `Arrived at Guild Duel Battle Report.`
- [ ] **Step 3: Implement `navigate_to_guild_duel_merit_rankings`:** call the helper, OCR-tap the Duel Merit panel (try the fragment `"Merit"` first, then `"Details"`, the way the Battle Report nav targets a single fragment), stable-wait, confirm arrival by OCR of the title text containing `"Merit"`; on failure write `debug_nav_fail_guild_duel_merit_rankings.png` and raise `RuntimeError`, same shape as the sibling function.
- [ ] **Step 4: Implement `scan`:** check the window gate first (unless `force`) and print `GUILD_DUEL_DAMAGE: skipping (outside the Saturday-Sunday UTC window).` returning `"skipped"`; otherwise navigate, `scroll_scan(_parse, "Guild Duel Damage", scroll_fn=guild_duel_damage_scroll_down, require_new=False, no_change_limit=4, max_scrolls=90)`, `resolve_entries`, save with `save_guild_duel_damage` under `guild_duel_damage_period_key(now)`, print `GUILD_DUEL_DAMAGE: saved N entries for period P.` and return `"success"`. Confirm by reading `resolve_entries` that it passes the `damage` key through; if it drops unknown keys, carry damage by name. `_parse` wraps `parse_rank_rows(img, ocr_results, list_y_min=LIST_Y_MIN)` then `rows_to_entries`.
- [ ] **Step 5: Wire the scraper:** append `"guild_duel_damage"` to `MODES`; pass `force=True` for both guild-duel modes when `--force-rescan` is set; update the flag's help text.
- [ ] **Step 6: Tune live.** With the emulator on any screen, run `venv/Scripts/python.exe src/scraper.py --guild-duel-damage --force-rescan`. Adjust `LIST_Y_MIN` and the scroll swipe (row pitch is about 186px) until the saved count matches the number of our-guild rows on screen and the last list row is captured. Compare saved damage for at least five members against the screen, including a row that was under the pinned bar at some stop.
- [ ] **Step 7: Confirm the gate live:** on a weekday run `venv/Scripts/python.exe src/scraper.py --guild-duel-damage` (no force). Expected: the skip line, no navigation, and a `scan_timings` row of status `skipped`.
- [ ] **Step 8: Commit** (`feat: guild duel damage scan mode`).

### Task 5: Bot scan toggle and `/scan` wiring

**Files:**
- Modify: `utils/botConfig.js` (new entry in `CONFIG_META`)
- Modify: `slash-commands/scan.js` (`MODE_FLAGS` at line 12; the output filter regex at line 201, hoisted to an exported `MODE_OUTPUT_RE`)
- Modify: `slash-commands/help.js`, `README.md`, `CLAUDE.md`
- Test: `slash-commands/scan.test.js` (new)

**Interfaces:**
- Produces: config key `SCAN_GUILD_DUEL_DAMAGE` (category `scan_modes`, default `'false'`, label `Guild Duel Damage Scan`); `module.exports.MODE_OUTPUT_RE` on the scan command module.

- [ ] **Step 1: Write the failing test:** `MODE_OUTPUT_RE` matches `"GUILD_DUEL_DAMAGE: saved 40 entries for period 2026-10-19."`, `"GUILD_DUEL: saved 5 entries"` and `"MODE_FAILED: guild_duel_damage - x"`, and does not match `"Mode scan: guild_duel_damage"`.
- [ ] **Step 2: Run** `node --test slash-commands/scan.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement:** add the config entry, the `SCAN_GUILD_DUEL_DAMAGE: "--guild-duel-damage"` flag mapping, hoist the regex adding a `GUILD_DUEL_DAMAGE` alternative, and export it.
- [ ] **Step 4: Docs** in the same task: one line each in help text, README and the CLAUDE.md scan-mode notes saying the mode exists, runs only Saturday and Sunday UTC before the reset, fills `damage` on the week's row, and is off by default (toggle in the admin panel Scan Modes tab).
- [ ] **Step 5: Run** `npm test`. Expected: all pass.
- [ ] **Step 6: Commit** (`feat: bot toggle and /scan wiring for the guild duel damage mode`).

### Task 6: Readers tolerate damage-only rows (bot and stats)

**Files:**
- Create: `utils/guildDuelRows.js`
- Modify: `slash-commands/guildduel.js` (`missingRows` near line 29, the `add` insert near line 113), `slash-commands/member.js` (the Guild Duel query near line 128), `stats/server.js` (`/api/guild-duel` near line 201)
- Test: `utils/guildDuelRows.test.js`

**Interfaces:**
- Produces (each takes a better-sqlite3 `db` first so tests can pass an in-memory one): `missingForPeriod(db, period) -> {ingame_name: string}[]`; `latestCrestsRow(db, memberId) -> {rank, crests} | undefined`; `upsertCrests(db, memberId, period, rank, crests, scannedAt) -> void` (sets only `rank`, `crests`, `scanned_at` on conflict); `crestReport(db) -> {periods, rows, absent}` (the existing stats shape, counting only rows with `crests IS NOT NULL`).

- [ ] **Step 1: Write the failing tests** with an in-memory db holding minimal `members`, `warbands` and `guild_duel_rankings` (with the damage columns), made-up names:
  - `missingForPeriod` lists a member whose only row is damage-only and omits one with crests.
  - `upsertCrests` on a row that already has damage keeps the damage.
  - `latestCrestsRow` returns the older crests row when a newer damage-only row exists.
  - `crestReport`: a period containing only damage-only rows is absent from `periods`; `rows` contain no null-crest entries; a member with a damage-only row for a period appears in `absent` for it.
  - `guildDuelLow.loadRows` ignores damage-only rows (give the in-memory db the extra tables that function reads: `snapshots`, `member_snapshots`, `member_afk`, `member_afk_history`).
- [ ] **Step 2: Run** `node --test utils/guildDuelRows.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement** the four functions by moving the existing SQL out of the three call sites and adding the `crests IS NOT NULL` conditions (and the upsert), then make the three call sites use them.
- [ ] **Step 4: Run** `npm test`. Expected: all pass. Also `cd stats && npm run build` is not needed (server-side change only).
- [ ] **Step 5: Docs:** note the damage-only-row rule in `CLAUDE.md` next to the `/guildduel` rows and the `guild_duel_rankings` table description.
- [ ] **Step 6: Commit** (`fix: guild duel readers tolerate damage-only rows`).

### Task 7: mergeMembers keeps both halves of a week's row (bot)

**Files:**
- Modify: `utils/db.js` (`mergeMembers`, before the multi-row collapse loop near line 656)
- Test: `utils/db.test.js`

**Interfaces:**
- Consumes: the `damage`, `damage_scanned_at`, `rank`, `crests` columns.
- Produces: no new exports; behavior change inside `mergeMembers`.

- [ ] **Step 1: Write the failing test** using the real db with fixture members named with a unique prefix (e.g. `__gdd_test_keep`, `__gdd_test_drop`) and cleanup in `afterEach` by exact member id and period (rows, then members): keeper has crests only for period P, dropped member has damage only for the same P; after `mergeMembers(keepId, dropId)` the keeper's P row has both crests and damage. Second case: the reverse.
- [ ] **Step 2: Run** `node --test utils/db.test.js`. Expected: FAIL (damage lost).
- [ ] **Step 3: Implement:** for `guild_duel_rankings`, before the existing delete-overlap-and-repoint pass, `UPDATE` the keeper's rows setting each of `rank`, `crests`, `damage`, `damage_scanned_at` to `COALESCE(keeper value, dropped member's value for the same period)`.
- [ ] **Step 4: Run** `npm test`. Expected: all pass, and confirm no fixture rows remain in the real db afterwards.
- [ ] **Step 5: Commit** (`fix: mergeMembers combines overlapping guild duel weeks`).

### Task 8: End-to-end check across the weekend and Monday (manual, time-gated)

- [ ] **Step 1 (Sat or Sun UTC, before reset):** enable `SCAN_GUILD_DUEL_DAMAGE` in the admin panel, restart the bot yourself, run `/scan`. Expected: a `GUILD_DUEL_DAMAGE: saved N entries` line and damage-only rows for this week's Monday.
- [ ] **Step 2 (Monday after 00:00 UTC):** run the normal Guild Duel scan. Expected: it runs (not skipped), and the same rows now hold crests, rank and damage.
- [ ] **Step 3:** run the damage mode on that Monday. Expected: skipped, nothing written for the new week.
- [ ] **Step 4:** check `/guildduel missing` and the stats Guild Duel tab: no blank period, no one wrongly missing.
- [ ] **Step 5:** update both repos' ARCHITECTURE.md and the miner's CLAUDE.md mode notes, then commit.
