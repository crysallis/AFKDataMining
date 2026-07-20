# Dream Realm: tier-aware stats report

## Problem

The Dream Realm tab in the stats site (`DiscordBotAfkJ/stats/`) mixes
difficulty tiers together as if scores were directly comparable. On a given
boss/day, different members fight at different difficulty tiers (Hell,
Endless, etc. — chosen per-member, not fixed per-day), and each tier
rescales scoring differently. A player who advances from Hell to Endless
sees their raw score *drop* even though they objectively improved — e.g.
`Crysallis` went `epic 143B → hell 830B → endless 179B` across three scans,
which is tier progression the whole way, not a decline.

Confirmed live in `guild.db` (Snow Stomper, 2026-07-15): Endless scores
range ~116B-200B, Hell scores range ~1228B-2065B — an Endless bar at 200B
would visually vanish next to a Hell bar at 2065B on a shared axis, despite
Endless being the harder tier and correctly ranked above Hell already.

This is a presentation-layer bug only. Two things are already correct
upstream and untouched by this spec:
- `dream_realm_scores.tier` (`AFKDataMining/src/db.py:110-121`) already
  stores the actual difficulty tier per row (`hard`, `epic`, `hell`,
  `endless`, `common`), captured live via `detect_tiers()`/`tier_for_row()`
  in `src/modes/dream_realm.py`.
- `dream_realm_scores.rank` is already computed correctly across tiers by
  the game itself / the scan (confirmed: Endless members rank above Hell
  members on the same boss/day despite lower raw scores).

The bug is entirely in `DiscordBotAfkJ/stats/src/charts/dreamrealm.js`:
1. The bar chart plots raw `score` values for every member on one shared
   numeric x-axis, so a max Hell score dwarfs a max Endless score.
2. `tierColor()` (`dreamrealm.js:154-158`) only maps `hard`/`epic`, falling
   through to `--common` (blue) for anything else — so `hell` and `endless`
   currently render as the *same color* as `common`, indistinguishable.
3. The "vs prev" delta column diffs raw scores with no tier-change check,
   producing a misleading large negative delta whenever a member advances a
   tier between scans.

## Scope note

This is a separate, unrelated problem from the Guild Duel crests report
(`2026-07-19-guild-duel-crests-report-design.md`, currently also awaiting
review) — different file, different bug class (correctness of an existing
report vs. building a new one). Tracked as its own spec/plan so the two
don't get tangled into one implementation pass.

## Design

All changes are in `DiscordBotAfkJ/stats/`:
- `shared/theme.css` — add `--hell` / `--endless` color vars to both theme
  blocks (dark ~line 12-14, light ~line 42-44), alongside the existing
  `--hard`/`--epic`/`--common`.
- `shared/components.css` — add `.tier-hell` / `.tier-endless` badge classes
  (~line 120-122), same pattern as the existing `.tier-hard`/`.tier-epic`/
  `.tier-common`.
- `stats/src/charts/dreamrealm.js` — the actual fix, detailed below.

### Grouping (table)

For a given boss + day, group rows by `tier` instead of one flat list.
Order groups worst-to-best: `common < hard < epic < hell < endless` (any
tier value outside this known set — future new tiers — sorts after
`endless`, so an unrecognized tier is visible rather than silently dropped).
Only render a group if it has at least one row that day (an all-Endless
guild on a given day has no `common`/`hard` section at all).

Within a group, sort by `score` descending (not the global `rank` field —
`rank` is nullable on OCR miss per schema, and within a single tier score
order and rank order agree anyway, so sorting by score sidesteps a null
edge case for free). `rank`'s existing global value is still shown as a
column/badge for context, just not used to order rows.

Each tier group renders as its own labeled sub-table (heading = tier name,
styled via the tier's badge color), reusing the existing `.data-table`
markup per group rather than inventing a new component.

### Chart (bar chart, one per tier)

Replace the single shared-axis bar chart with one small bar chart per
rendered tier group (per the "separate mini-chart per tier" decision) —
each with its own x-axis scale, so bar length is only ever meaningful
within its own tier. Reuses the existing Chart.js bar-chart config from
`dreamrealm.js`, just instantiated once per tier group instead of once per
boss/day, with a `<canvas>` + heading per tier. Only tiers with rows that
day get a canvas — no empty charts.

### Delta ("vs prev") column

Compare each member's current-scan tier to their tier on the previous scan
of the same boss:
- **Same tier**: show the existing numeric +/- delta (unchanged behavior).
- **Different tier** (including no previous scan at all): show no numeric
  delta — the numbers aren't comparable. Reuse the existing `new` badge
  styling (`delta-neu`) for "no previous scan," and add a distinct label
  (e.g. "tier changed") for "previous scan exists but was a different
  tier," so the two null-delta cases stay visually distinguishable.

### Color mapping

Extend `tierColor()` (`dreamrealm.js:154-158`) with `hell` → `var(--hell)`
and `endless` → `var(--endless)` branches, matching the existing
`hard`/`epic` pattern. Fallback for unrecognized tier values stays
`--common`, same as today.

## Out of scope

- Re-deriving or backfilling `rank` — untouched, already correct.
- Any change to the scan/miner side (`src/modes/dream_realm.py`,
  `src/db.py`) — `tier` is already captured correctly at write time.
- A cross-tier "normalized" score (e.g. converting all tiers to one
  comparable unit) — not requested; grouping and per-tier scaling is
  sufficient per the design decisions above.

## Testing

No existing automated test suite covers `stats/` frontend code (matches the
Guild Duel spec's testing section). Manual verification: load the Dream
Realm tab, select Snow Stomper / 2026-07-15 (the confirmed live example
with both Hell and Endless rows), confirm the table splits into an Endless
group above a Hell group, confirm two separate bar charts render at
different scales, confirm Hell and Endless badges render in distinct
colors, and confirm Crysallis's Hell→Endless scan-to-scan transition shows
no misleading numeric delta.
