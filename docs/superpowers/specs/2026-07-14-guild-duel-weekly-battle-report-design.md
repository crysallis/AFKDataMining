# Guild Duel: weekly Battle Report scan replaces daily scan

## Problem

The current Guild Duel scan (`src/modes/guild_duel.py`) runs daily against the
"Guild Duel Total Crest Rankings" screen — a live, mid-week, in-progress view.
This should instead scan once a week, after the week's crests are finalized,
reading the **Battle Report** screen instead, which shows the finished
previous week's per-member crest totals.

## Screen discovered (live capture, 2026-07-14)

Nav path: Home → Battle Modes → Guild Duel card → lands on "Elite Duelers" tab
(same destination `navigate_to_mode("guild_duel")` already reaches) → tap
"Battle Report" button (top-right, OCR text) → opens a scrollable "Victory"
popup card.

Card layout, confirmed live:
- Reward banner (Victory Rewards / Tier Rewards icons, numeric values like
  1200/500/600/250/6) sits above the list, roughly y700-850. Not row data —
  must be excluded via `list_y_min`.
- List rows below that: rank number (left), avatar, name, guild tag
  ("RKF RiffRaff", one line under the name), crest icon + number (far
  right). Same shape as `parse_rank_rows()` already handles for the daily
  screen.
- No hero/podium section — ranks 1-3 are plain list rows here, unlike Dream
  Realm/Arena. `parse_rank_rows()`'s auto podium-detection must be verified
  not to misfire on this screen (there's no wide-spread name band to trigger
  it, so it's expected to no-op, but confirm live).
- The viewer's own row (e.g. "Crysallis", rank 5) is **sticky** — pinned at a
  fixed position at the bottom of the visible card area regardless of scroll
  position. This is confirmed to occlude whatever real row would otherwise be
  there (rank 4 was covered in the initial capture). Manually confirmed
  unavoidable even by hand (scrolling and holding still drops the sticky row
  back into place on release) — **accepted as a known gap**, not something
  this scan works around. The member normally at that scroll slot is
  unrecoverable by this method.
- Battle Report shows the **just-finished** week's results for the entire
  following week (confirmed: showing "Preparation Phase, 2d 23h" on Tuesday
  while Battle Report still had last week's finalized Victory data). It rolls
  over to the new "last week" precisely at Monday. This means any scan that
  runs on or after Monday 00:00 UTC during that week reads a stable, correct,
  finalized result — no risk of reading a mid-week partial state.

## Navigation changes (`src/nav.py`)

New `navigate_to_guild_duel_battle_report(flow_attempts: int = 3) -> None`:
- Calls `navigate_to_mode("guild_duel")` to reach the Elite Duelers tab (existing
  path, unchanged).
- OCR-taps "Battle Report" (top-right button, ratio-matched like other
  `_ocr_tap_text` targets).
- `_wait_until_stable()` after the tap, same pattern as other nav functions.

No new templates needed — button is found via OCR text like Clashfronts'
"ManageSign" tap, not template match (button art may vary by season the same
way mode cards do).

## Parsing changes (`src/modes/guild_duel.py`)

- `_parse()` keeps using `parse_rank_rows()`, passing a `list_y_min` tuned to
  start below the reward banner (measured ~y850 on the 1080x1920 capture;
  exact value to be confirmed/tuned on-device same as other modes).
- `_is_our_guild()` filtering is **kept** (not removed) — screen composition
  when the opposing guild might also appear on Battle Report is unconfirmed,
  and the check is cheap insurance already proven correct on the daily
  screen.
- Scroll behavior: the Battle Report card's visible list area is shorter than
  the full Rankings screen `scroll_down()` was tuned for. A gentler,
  Battle-Report-specific scroll (smaller swipe distance and/or more scroll
  steps between OCR stops) is needed so the existing swipe doesn't overshoot
  and skip rows in the smaller viewport. Exact swipe distance to be tuned
  live the same way `scroll_down()` was originally tuned (measure card pitch
  on-device, confirm via dedup/vote behavior).
- `_reentry()` — the existing custom reentry (press_back, re-tap
  rankings_btn) is replaced with a Battle-Report-specific reentry, or simply
  dropped in favor of `frames_per_stop >= 2` multi-sampling (existing
  `scroll_scan()` default), since a full second pass matters less for a
  screen already sampled more densely.

## Timing / period logic (`src/db.py`)

New `get_guild_duel_period() -> str | None`, modeled on
`get_supreme_period()`:
- Gate: only returns a period on/after Monday 00:00 UTC (`now.weekday() == 0`
  is the earliest trigger day, but unlike Supreme Arena's narrow Mon/Tue
  window, this stays valid the *entire* week since Battle Report doesn't roll
  over again until the following Monday — so the gate is "weekday >= 0" i.e.
  always true, and the real gate is the "already scanned" check below).
- Period key: the **previous** Monday relative to "now" (the week that just
  finished and that Battle Report is currently showing) — matches the
  existing `period_start` convention where the key is the start of the week
  being measured, not the day scanned.
- Self-healing catch-up: returns `None` if that period already has rows in
  `guild_duel_rankings` (any scan, not just same-day) — so a missed Monday
  run, or a failed one, safely retries on every subsequent day's scrape until
  it succeeds, then goes quiet for the rest of the week.
- `current_guild_duel_period()` is removed — nothing needs "the live
  in-progress period" concept anymore, only "the period to scan now, if any."

`save_guild_duel()` changes from unconditional write to:
```
period = get_guild_duel_period()
if period is None:
    return 0
```
mirroring `save_supreme_arena()`'s gated path (no `force=True` equivalent
needed — there's no manual-submission workflow for this mode).

`scan()` in `guild_duel.py` should check the gate **before** doing the nav +
scroll work (not just before the save), so a skip day doesn't spend
minutes scanning for nothing — model on how `afk_stages.py`'s phase-backfill
detection checks row-count before deciding whether to even navigate.

## Scraper wiring (`src/scraper.py`)

No changes. `--guild-duel` still exists and still calls `guild_duel.scan()`
via `MODES`; it's safe (and expected) to invoke it on every daily scrape run
— it becomes a fast no-op skip on non-Monday days or once the week's period
is already saved.

## Migration

Before this ships:
1. Delete all existing rows in `guild_duel_rankings` for the current period
   (`period_start = '2026-07-06'`) — the daily-scan data accumulated for the
   week that just ended is being replaced by the authoritative Battle Report
   read, not merged with it.
2. Run the new scan once manually to populate `2026-07-06` from the Battle
   Report screen currently showing (this is literally "last week" as of
   today, 2026-07-14, a Tuesday in the new week's Preparation Phase).

No historical backfill beyond that — prior weeks' daily-scan data stays as-is
since Battle Report can't retroactively show weeks further back than "last
week."

## Out of scope

- The eventual end-of-event "duel merit" stat — still unknown shape, still
  not tracked (per existing CLAUDE.md note).
- Any completeness/invariant check against the sticky-row occlusion gap —
  confirmed unrecoverable even manually; not engineered around.
