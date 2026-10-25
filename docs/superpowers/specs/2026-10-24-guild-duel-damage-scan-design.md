# Guild Duel damage scan (Duel Merit Rankings), phase 1: collection only

## Why

Guild Duel members earn crests Mon-Fri and spend them on the boss Sat-Sun
(200 crests per hit). Spending matters more than earning, since unspent crests
are wasted. The existing Battle Report scan reads crests EARNED, but only after
Monday 00:00 UTC, and by then damage done and crests spent are gone from the
game. Damage can only be read live, on the weekend, before the reset.

Eventually the guild wants a soft "worth a conversation" signal: crests earned
divided by 200 gives the hits a member had, total damage divided by that gives
damage per hit, and a member far below the guild median that week probably did
not spend their crests (or needs help with team formation). It is a data point,
not a penalty.

This document covers only getting the damage data into the database. The
calculation, the `/guildduel low` criterion, its admin threshold and any stats
display are deliberately later, after the guild has seen real collected data.

## Goals

- A new scan mode reads each of OUR guild's members' total damage from the
  "Total Duel Merit Rankings" screen and stores it on that week's Guild Duel row.
- The Monday Battle Report scan later fills crests and rank on the SAME row,
  without losing the damage, and still runs.
- Nothing that reads `guild_duel_rankings` today breaks or lies because a row
  can now exist with damage but no crests.

## Non-goals

- Per-hit damage, any flagging, `/guildduel low` changes, admin thresholds,
  stats-site damage display. All later.
- Crests spent as a stored number. The screen only shows it for the viewer;
  damage is the stand-in.
- Recovering any past week. Damage for finished weeks is gone from the game.
- Any work around the pinned own-row bar beyond ignoring it as a data row.

## Anti-goals (a failure even if it technically works)

- A scan run after the Monday 00:00 UTC reset saving zeros for the new week as
  if they were real damage.
- The Monday scan silently never running because Sunday already created rows.
- A damage-only row hiding a member from "missing crests" lists.

## The screen (confirmed live, 2026-10-24)

Path: same start as the Battle Report scan (overview, Battle Modes, "Guild
Mode" tab, Guild Duel card). During the weekend battle phase the card lands on
the boss screen (header "Challenge", boss name, a "Tallies in Nh Nm"
countdown, a Battle button showing "N/200", N being the viewer's crests); on
weekdays it lands on the "Preparation Phase" tab instead, which has no such
panel. The Elite Duelers tab is NOT tapped. At the bottom left is a
"Duel Merit Rankings" panel with a "Details" button. Tapping it opens
"Total Duel Merit Rankings".

That screen (1080x1920):
- A top-3 hero podium (names plus guild tags) above the list, roughly y0-700.
  Ranks 1-3 are ALSO repeated as normal rows in the list below, so the podium
  is ignored entirely, like the other podium modes: nothing is read above the
  list (`list_y_min` below the podium, backed by `parse_rank_rows`' podium
  detection) and ranks 1-3 come from their list rows.
- List rows from about y725, pitch about 186px: rank number, avatar, name, a
  guild tag line under the name, and on the right "Total Damage" with a value
  such as `567B` (billions, abbreviated, whole numbers).
- The list mixes BOTH guilds (our tag is "RKF RiffRaff", the opponent has
  its own). Ranks are across both guilds, so our members are interleaved.
- The viewer's own row is NOT permanently pinned. It shows as a bar at the
  bottom only until the scroll passes the viewer's real position in the list,
  and then the real row appears in line. When the viewer has no damage they
  have no real row, so the bar reads "Unranked" with no value, and it is not a
  data row. Unlike the Battle Report screen, per the user the bottom-most row
  of the list is always visible, so no member is permanently hidden under the
  bar: a row that slides under it at one stop is read at the next stop through
  the usual overlap between stops.
- A member with no attacks yet is not in the list at all.

## Design

### Database (owned by the miner's `db.py`, mirrored in the bot's `utils/db.js` only if it creates the table there)

`guild_duel_rankings` gains:
- `damage INTEGER` : total damage in raw units (`567B` stored as 567000000000;
  fits SQLite's 64-bit integer and JS Number). Nullable.
- `damage_scanned_at TEXT` : when the damage was last read. Nullable.

`rank` and `crests` are already nullable. A row may now have damage only
(Sunday), crests only (today), or both (after Monday).

Schema change: ALTER once against guild.db, then fold into the owner's CREATE
(project convention, no migration trail).

### Period key

The Sunday scan writes to THIS week's Monday: the most recent Monday on or
before now (UTC). The Monday Battle Report scan already resolves to "the
Monday before the one just passed", which is the same date, so both land on
one row. New pure function `guild_duel_damage_period_key(now)` beside
`guild_duel_period_key`, with a unit test on boundary dates.

### Scan window gate

The new mode only runs Saturday 00:00 UTC through Sunday 23:59:59 UTC.
Outside that window it prints a skip line and does nothing, so it can never
write the new week's post-reset zeros. Inside the window a later scan
overwrites the earlier damage (damage is a running total, the latest is right).
A `force` option bypasses the window for manual use, same as `guild_duel`.

### Save functions (miner `db.py`)

- New `save_guild_duel_damage(rows, period)`: UPSERT keyed on
  (member_id, period_start). Sets `damage` and `damage_scanned_at` only.
  Never touches `rank`, `crests` or `scanned_at`.
- `save_guild_duel` (existing, Battle Report): change from `INSERT OR REPLACE`
  to an UPSERT that sets only `rank`, `crests`, `scanned_at`, so damage
  survives. Rows are still keyed on (member_id, period_start).
- `get_guild_duel_period` (the Monday gate): "already scanned" must mean the
  period has at least one row with `crests IS NOT NULL`, not any row.
  Otherwise Sunday's damage rows would suppress the Monday scan forever.
- `get_guild_duel_missing`: a member is missing when they have no row with
  crests for the period (not merely no row).

### Scan mode (miner)

New `src/modes/guild_duel_damage.py`, structured like `guild_duel.py`:
1. Check the window gate first (before any navigation, like the existing
   modes do) and return "skipped" if closed.
2. New nav function `navigate_to_guild_duel_merit_rankings()` in `nav.py`:
   the shared "reach the Guild Duel card" steps (factored out of
   `navigate_to_guild_duel_battle_report`, which then calls the same helper),
   OCR-tap the "Duel Merit" panel /
   "Details" (ratio-matched like other `_ocr_tap_text` targets, not a
   template), `_wait_until_stable()`, and confirm arrival by OCR of the
   "Duel Merit Rankings" title.
3. Parse with `parse_rank_rows` using a `list_y_min` below the podium (its
   podium auto-detection should handle the wide hero band; confirm live) and
   the same guild-tag filter as `guild_duel._is_our_guild`, since the opponent
   guild is in the list. Drop the pinned own-row bar as a data row (its rank
   reads "Unranked" and it carries no damage value), and confirm live that the
   final row of the list is readable at the end of the scroll. The damage value is not a pure integer (`567B`), and
   `parse_rank_rows` only collects pure-numeric blocks, so the damage value
   must be parsed from the row's extra text blocks with a K/M/B/T suffix
   parser. Reuse an existing suffix parser if the miner has one (power values
   use `86329K` style); otherwise add one with tests.
4. Scroll with a step tuned live to this list (row pitch about 186px), through
   `scroll_scan` with `resolve_entries`, like Guild Duel.
5. Save through `save_guild_duel_damage`, print a `GUILD_DUEL_DAMAGE:` result
   line (the bot's /scan output filter keys on a mode prefix).

### Wiring a new mode (follow the Guild Duel precedent exactly)

- Miner `scraper.py`: add `guild_duel_damage` to `MODES`, a
  `--guild-duel-damage` flag, and pass `force` the way `guild_duel` does.
- Bot `utils/botConfig.js`: new scan-mode toggle `SCAN_GUILD_DUEL_DAMAGE`
  (category `scan_modes`, default `false`).
- Bot `slash-commands/scan.js`: map the toggle to `--guild-duel-damage` and add
  the `GUILD_DUEL_DAMAGE` prefix to the output filter regex.
- Both repos' docs updated (help text, README, CLAUDE.md, ARCHITECTURE).

### Everything that reads `guild_duel_rankings` must tolerate a damage-only row

- Bot `/guildduel missing` (`slash-commands/guildduel.js`): missing means no
  row with crests.
- Bot `/guildduel add`: currently `INSERT OR REPLACE`, which would wipe
  damage. Change to the same UPSERT shape (rank, crests, scanned_at only).
- Miner `src/add_guild_duel.py`: same change.
- Bot `/member` (`slash-commands/member.js`, Guild Duel line): select the row
  with crests, not simply any row for the member.
- Stats `server.js` `/api/guild-duel`: `rows` and the `periods` list must only
  count rows with crests (a Sunday damage-only row must not create an empty
  period in the selector), and `absent` must key on "no row with crests".
- `utils/guildDuelLow.js` `loadRows` already filters `crests IS NOT NULL`
  (no change; add a test that a damage-only row is ignored).
- `utils/db.js` `mergeMembers`: for a period where BOTH members have a row it
  deletes the dropped member's whole row (keeper wins), which would lose the
  damage (or the crests) held only by the dropped row. For this table it must
  first fill the keeper's NULL fields (`rank`, `crests`, `damage`,
  `damage_scanned_at`) from the dropped member's row for that period, then
  run the existing collapse unchanged.

## Testing

- Unit: `guild_duel_damage_period_key` and the window gate across Fri/Sat/Sun/
  Mon boundaries (model on `test_guild_duel_period.py`); the suffix parser
  (`567B`, `1.2T`, `850M`, garbage); upsert preserves the other fields in both
  directions (damage then crests, crests then damage); the Monday gate with a
  damage-only week; each reader above with a damage-only row.
- Live, on the device: scan once on the weekend and compare the saved damage
  for several members against the screen; run the Monday scan afterwards and
  confirm crests land on the same rows and damage is intact; confirm a
  Monday-morning run of the new mode refuses.

## Known gaps (accepted)

- Damage is whole billions, so a very small total shows coarsely. Fine for
  spotting outliers way out, which is the goal.
- A member who has not attacked has no row on this screen, so "no damage" is
  indistinguishable from "missed the scan". The later calculation must treat
  the two differently only if the scan is known to have run that week.

## Left to the builder

Exact `list_y_min`, scroll swipe distance, the OCR tap text for the Duel Merit
panel, and the tolerance for the guild-tag match are tuning done live on the
device, as the Battle Report scan was.

## Calls made by the author (not the user), for review

1. Store damage as an integer in raw units, not text plus float (confirmed by
   the user: convert once at scan time so every later calculation can use the
   number directly).
2. A separate `damage_scanned_at`, so the crest scan's `scanned_at` keeps
   meaning "when crests were read".
3. Window is Saturday through Sunday UTC; later scans overwrite earlier ones.
4. The Monday gate changes to "has crests" instead of "has any row".
5. The new mode ships off by default behind its own `SCAN_GUILD_DUEL_DAMAGE`
   toggle.
