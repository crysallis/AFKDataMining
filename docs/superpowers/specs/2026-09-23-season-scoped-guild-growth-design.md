# Season-scoped guild growth

## Problem

Daniel wants to see guild power growth across a season boundary (RiffRaff's
Dream Realm rotation just crossed its first season, 2026-09-22). Nothing in
`guild.db` currently ties a `member_snapshots` row to a real-world season —
`ally_seasons` (the table `/season` and the admin panel manage) exists purely
as a name + `active` flag, with no historical record of when a season
started or ended, and no scan table references it at all except
`dream_realm_bosses.season` (already an `ally_seasons.id` FK) and
`afk_stage_rankings.season` (an unrelated bare integer — AFK Stage's own
in-game season/phase numbering, not ally-season aligned).

## Scope decisions (from brainstorming)

The original ask covered stamping season onto every ranking table
(`arena_rankings`, `supreme_arena_rankings`, `guild_duel_rankings`,
`honor_duel_rankings`, `arcane_lab_rankings`, `clashfronts_signups`). Scope
was cut down during design:

- `arena_rankings`, `honor_duel_rankings`, `arcane_lab_rankings` are
  single-row-per-member tables (`member_id` is the PK, overwritten every
  scan). Adding `season_id` there would only ever reflect the *current*
  season's snapshot — the previous season's final standing is destroyed on
  the next write. Real season history would need a primary-key change
  (`(member_id, season_id)`), which is out of scope for this pass.
- `supreme_arena_rankings` and `guild_duel_rankings` already key on
  `(member_id, period_start)` so they *could* take a plain `season_id`
  column cleanly — but Daniel confirmed growth tracking only needs
  `member_snapshots`; these two are excluded to keep the change minimal.
- `clashfronts_signups` is single-row-per-member (same shape problem as the
  first group) and isn't a growth metric anyway — excluded.

**Net scope: `member_snapshots` only.**

## Design

### Schema (`AFKDataMining/src/db.py`)

Add one column:

```sql
ALTER TABLE member_snapshots ADD COLUMN season_id INTEGER REFERENCES ally_seasons(id);
```

Guarded the same way other post-initial-schema ALTERs in this codebase are
(e.g. `dream_realm_bosses.sort_order` in `DiscordBotAfkJ/admin/server.js:1295`
— `try { ... } except { }` around the ALTER, safe to run every startup since
a second `ALTER ADD COLUMN` on an existing column just raises and is
swallowed). Add it in `db.py`'s `init_db()` near the other ALTER guards, and
fold the column into the `CREATE TABLE member_snapshots` statement per this
repo's schema convention (ALTER once, then fold into the owner's CREATE — no
migration trail).

**Backfill:** one-time, same guarded block —

```sql
UPDATE member_snapshots SET season_id = 1 WHERE season_id IS NULL;
```

`ally_seasons.id = 1` is "Crown of Ashes," the season active through the
2026-09-22 rollover. Confirmed live: `member_snapshots` currently has zero
rows with any season data, so this backfill is total (every existing row
gets season_id = 1), not selective.

### Stamping every future scan

`save_snapshot()` (`db.py:280-`) is the only writer of `member_snapshots`.
It has two write paths depending on whether a row already exists for the
current calendar week (`snapshots`/`member_snapshots` collapse to one row
per week, not one row per scan — confirmed reading the existing
UPDATE-if-exists / INSERT-if-not branch at lines 302-356):

- The UPDATE branch (existing week, existing member) — line 320.
- The INSERT branch, both the "existing week, new member" single insert
  (line 340) and the "new week" bulk `executemany` insert (line 364).

All three writes get `season_id` added to their column list and values,
resolved once per `save_snapshot()` call via a new small helper mirroring
`_active_season_id()` (already used by Dream Realm, `db.py:809`):

```python
def _active_season_id(conn) -> int | None:
    # already exists, reused as-is
```

Stamping happens on **every** write, insert or update — so if the active
season changes mid-week (edge case, but possible right at a rollover), the
row's `season_id` reflects whatever was active at the moment of the most
recent scan that touched it, not whatever was active when the week's row was
first created. This matches the "every snapshot, always" decision from
brainstorming and avoids a stale season_id sitting on a row that's actually
been re-scanned under a new season.

### Bot-side commands (`DiscordBotAfkJ/slash-commands/guild.js`)

Two new top-level subcommands on `/guild` (not nested under the existing
`/guild growth`, since Discord only allows one level of subcommand nesting
and `growth` is already a plain subcommand — scan-to-scan power growth,
unrelated to this feature and left untouched):

- **`/guild seasongrowth season:<name>`** — start = the earliest
  `member_snapshots` row (by parent `snapshots.scraped_at`) tagged with the
  chosen season's id; end = the latest such row. Both fixed in the past,
  for a season that has already ended (or any point-in-time slice of one
  that's ongoing, though `seasonplus` is the intended tool for "now").
- **`/guild seasongrowthplus season:<name>`** — same start (earliest
  snapshot tagged with that season), but end = the single most recent
  snapshot overall (`MAX(scraped_at)` across all of `snapshots`, no season
  filter). Lets Daniel check the *current* season's growth-so-far without
  waiting for it to end.

Both share one query shape, modeled directly on the existing `handleGrowth`
(`guild.js:264-305`, currently powering `/guild growth`'s top-5 scan-to-scan
list) — same `prevPower -> **currentPower** (+delta)` line format, same
`fmtPower`/`pickColor` helpers, same embed style as `/guild power`. Two
differences from `handleGrowth`:

- **All active members**, ranked by growth descending — not top-5. Matches
  `/guild power`'s full-roster style (per brainstorming decision).
- A member with no snapshot at the season's start (joined mid-season) shows
  `NEW · **currentPower**` instead of a `prev -> current` delta — a new,
  purpose-built check (`LEFT JOIN` the start snapshot, `NULL` means no data),
  not a reuse of the existing `badge()`/`newMemberIds()` helper, since that
  helper's "🆕" means "joined within the current calendar week," a different
  and unrelated condition.

`season:` autocompletes from `ally_seasons.name`, same pattern as the
existing season-name autocomplete in `slash-commands/season.js`.

### Query sketch

```sql
-- start snapshot id for a season
SELECT s.id FROM snapshots s
JOIN member_snapshots ms ON ms.snapshot_id = s.id
WHERE ms.season_id = ?
ORDER BY s.scraped_at ASC LIMIT 1;

-- end snapshot id: season-scoped (seasongrowth) vs. latest overall (seasongrowth-plus)
SELECT s.id FROM snapshots s
JOIN member_snapshots ms ON ms.snapshot_id = s.id
WHERE ms.season_id = ?              -- omit this WHERE entirely for seasongrowth-plus
ORDER BY s.scraped_at DESC LIMIT 1;
```

Then the same per-member join shape as `handleGrowth`, with the start join
changed to `LEFT JOIN` so a missing row surfaces as `NULL` (→ "NEW") instead
of silently excluding the member.

## Out of scope

- `arena_rankings`, `honor_duel_rankings`, `arcane_lab_rankings`,
  `supreme_arena_rankings`, `guild_duel_rankings`, `clashfronts_signups` —
  no season stamping on any of these (see Scope decisions above).
- Retroactively inferring which season was active on each historical scan
  date — not attempted; `ally_seasons.active` has never been timestamped on
  flip, so this isn't reliably possible. All pre-existing rows get the flat
  `season_id = 1` backfill.
- The separately-tracked, already-pinned Dream Realm season-crossover
  backfill gap (`get_boss_for_date` season-boundary awareness) — unrelated
  table, unrelated bug, not touched by this spec.
- Any change to `afk_stage_rankings.season` or reconciling its meaning with
  `ally_seasons` — explicitly a different concept (AFK Stage's own in-game
  season/phase numbering), left alone.
- A combined guild-total-power embed (single before/after number for the
  whole guild rather than a per-member list) — the per-member ranked-list
  format was explicitly chosen ("like guild power, user based power to
  power") over an aggregate-only view.

## Testing

No existing automated test suite covers the Python miner's `db.py` schema
functions directly (per this repo's existing testing conventions — manual
verification is the norm for schema/scraper changes). Manual verification
plan:
1. After the ALTER + backfill run, confirm every existing `member_snapshots`
   row shows `season_id = 1` via a direct query.
2. Run a live `/scan`, confirm the new week's `member_snapshots` rows (both
   the insert and, on a second same-week scan, the update path) carry the
   currently-active `ally_seasons.id`.
3. Flip the active season via `/season activate`, run another scan, confirm
   the newly-touched rows pick up the new season_id while older rows keep
   their original value.
4. `/guild seasongrowth season:<old season>` against the two seasons above —
   confirm start/end resolve to the expected snapshot dates and the
   per-member list matches hand-checked power deltas for a couple of known
   members.
5. `/guild seasongrowth-plus season:<current season>` — confirm end resolves
   to the most recent snapshot regardless of season tag.
6. A member who joined after the season's start snapshot — confirm they
   render as `NEW · power` rather than being silently dropped or crashing
   the query.
