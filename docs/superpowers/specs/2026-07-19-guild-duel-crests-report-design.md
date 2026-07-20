# Guild Duel Crests Report (stats site)

## Problem

`guild_duel_rankings` has been populated by the weekly Battle Report scan
(see `2026-07-14-guild-duel-weekly-battle-report-design.md`) but nothing
surfaces it. The RiffRaff stats site (`DiscordBotAfkJ/stats/`) needs a new
tab showing member crest counts over time, plus a list of who did and didn't
earn crests in a given week.

This spec covers only the reporting/UI side. It lives in this repo's spec
folder because that's where the Guild Duel feature's design history already
is, but the implementation is entirely in `DiscordBotAfkJ/stats/`.

## Existing conventions (from research)

The stats site (`DiscordBotAfkJ/stats/`) is a Vite-built vanilla-JS SPA:
Express API (`server.js`) + one `src/charts/<name>.js` module per tab +
Chart.js v4 for charts + a hand-rolled `.data-table` CSS class for lists. No
server-side templating. Tabs are wired in three places: `index.html` (button
+ `<section>`), `main.js` (import + `activateTab` branch), `server.js` (API
route).

Two existing reports are the closest templates:
- **`power.js`** — per-member multi-select line chart with `spanGaps: true`,
  warband filter, "current user" defaults. Model for the chart half.
- **`supremeArena.js`** — same `(member_id, period_start)` shape as
  `guild_duel_rankings`, period-aware table + per-member history chart. Model
  for the period-selector and table half.

Every existing report query does an **INNER JOIN** `ranking_table JOIN
members`, so absent members simply never appear. There is no existing
LEFT JOIN "who's missing" pattern anywhere in the codebase — this spec
introduces the first one.

## Design

### Backend — `stats/server.js`

New route, following the existing route conventions (try/catch, 500 on
error, `active=1 AND pending=0` for roster scoping):

```
GET /api/guild-duel
```

Returns:
```js
{
  periods: string[],   // distinct period_start, ascending
  rows: [              // one row per (member, period) that HAS a ranking row
    { member_id, ingame_name, warband_id, warband_name, period_start, crests }
  ],
  absent: [             // one row per (member, period) that has NO ranking row
    { member_id, ingame_name, warband_id, warband_name, period_start }
  ]
}
```

Query shape:
```sql
-- rows (mirrors supremeArena's supArena query, crests instead of rank)
SELECT m.id as member_id, m.ingame_name, m.warband_id, w.name as warband_name,
       g.period_start, g.crests
FROM guild_duel_rankings g
JOIN members m ON m.id = g.member_id
LEFT JOIN warbands w ON w.id = m.warband_id
ORDER BY g.period_start, g.crests DESC

-- absent — new pattern, computed for every known period, not just latest
SELECT m.id as member_id, m.ingame_name, m.warband_id, w.name as warband_name,
       p.period_start
FROM members m
CROSS JOIN (SELECT DISTINCT period_start FROM guild_duel_rankings) p
LEFT JOIN warbands w ON w.id = m.warband_id
LEFT JOIN guild_duel_rankings g ON g.member_id = m.id AND g.period_start = p.period_start
WHERE m.active = 1 AND m.pending = 0 AND g.member_id IS NULL
ORDER BY p.period_start, m.ingame_name
```

`periods` list comes from `SELECT DISTINCT period_start FROM
guild_duel_rankings ORDER BY period_start`. Computing `absent` for every
period server-side (rather than per-request-period) means switching the
period selector on the frontend needs no extra round-trip — same "fetch
once, reshape client-side" pattern as `power.js`/`supremeArena.js`.

A member who is active but has zero rows in `guild_duel_rankings` for *any*
period (e.g. joined after Guild Duel scanning started) will appear in
`absent` for every period — this is intentional per the "treat the same"
decision below, not a bug to special-case.

### Frontend — `stats/src/charts/guildDuel.js` (new)

Modeled on `supremeArena.js`'s `initSupArena`/`renderTable` structure, with
`power.js`'s chart-building for the trend chart.

State shape built from the fetch, same `memberMap` pattern as
`supremeArena.js`:
```js
memberMap: id -> { id, name, warbandId, warbandName, crestsByPeriod: { period_start -> crests } }
periods: string[]        // ascending, from API
absentByPeriod: Map<period_start, Set<member_id>>
```

**Controls:**
- Warband filter `<select>` — same convention as `power.js`/`supremeArena.js`,
  applies to both the chart and the data list.
- Period `<select>` — options are each `period_start` (most recent first,
  selected by default) plus a leading `"All Time"` option.

**Chart** (`chart-guildduel`, canvas): per-member multi-select line chart,
x = periods, y = crests. Copy `power.js`'s `buildDropdown`/checkbox-multiselect
wholesale (same "All members" + pinned current-user + individual toggles
behavior) rather than `supremeArena.js`'s single-select, since the design
call was per-member trend lines with multi-select like Power. `spanGaps: true`
per dataset so a member's line just skips periods with no data. No `reverse`
on the y-axis (unlike rank charts) since higher crests is better, displayed
top-up naturally.

**Data list** (two `.data-table`s side by side, "Had Crests" / "No Crests"),
driven by the period selector:

- **Single period selected:** "Had Crests" = rows for that period from
  `memberMap`, sorted by crests desc, warband-filtered. "No Crests" =
  `absentByPeriod.get(period)`, sorted alphabetically, warband-filtered.
- **"All Time" selected:** "Had Crests" = every member with at least one
  period in `crestsByPeriod`, value = sum of `crestsByPeriod` across all
  periods, sorted desc. "No Crests" = members who are in `absentByPeriod`
  for *every* period in `periods` (i.e. `crestsByPeriod` is empty) —
  computed client-side as `[...memberMap.values()].filter(m =>
  Object.keys(m.crestsByPeriod).length === 0)`, warband-filtered. This
  matches the "total crests + ever-zero" design decision.

No "vs prev period" delta column — that's a rank-report convention
(lower is better, so a delta needs a sign flip) and doesn't map cleanly onto
a raw crest count; skipped for this first version.

### New/edited files

| File | Change |
|---|---|
| `DiscordBotAfkJ/stats/server.js` | Add `GET /api/guild-duel` route |
| `DiscordBotAfkJ/stats/src/charts/guildDuel.js` | New file, `initGuildDuel(me)` |
| `DiscordBotAfkJ/stats/src/index.html` | New tab button (`data-tab="guildduel"`) + `<section id="tab-guildduel">` with warband/period selects, chart canvas, two table containers |
| `DiscordBotAfkJ/stats/src/main.js` | Import `initGuildDuel`, add `activateTab` branch |

No changes needed in `AFKDataMining` (this repo) — `guild_duel_rankings`
schema and the scan that populates it already exist and are unchanged by
this work.

## Testing

- No existing test suite covers the `stats/` frontend or its Express routes
  (confirmed no `stats/**/*.test.js` in the research pass). Match that:
  no new automated tests for this feature, consistent with sibling reports
  (Power, Supreme Arena) which also ship without tests.
- Manual verification: load the stats site, open the new tab, confirm the
  chart renders with real data, switch periods and confirm both lists update
  and warband filter applies to both, switch to "All Time" and confirm sums
  and the ever-zero list look right against a manual spot-check of the DB.

## Out of scope

- "Duel merit" end-of-event stat — shape unknown until a full cycle
  completes (per `CLAUDE.md`), not touched here.
- Distinguishing "joined after period started" from "earned zero crests" in
  the absent list — explicitly rejected in favor of treating both the same.
- A "vs prev period" delta column on the data list.
