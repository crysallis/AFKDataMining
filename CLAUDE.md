# Data Miner · AFK Journey Guild Scraper

Python · ADB (Android Debug Bridge) · OpenCV · RapidOCR (onnxruntime) · SQLite

Scrapes guild roster data from AFK Journey running in BlueStacks, saves to SQLite.
Companion bot at `C:\vscode\DiscordBotAfkJ`.
See global context at `C:\Users\crysa\.claude\CLAUDE.md`.
See `README.md` for setup/run commands and `--mode` flags; see `ARCHITECTURE.md` for
mode-scan voting and rank-reading internals.

## Session Start

Project knowledge lives in the mempalace `afkdatamining` wing (rooms: status, src,
decisions, gotchas, pending). `mempalace wake-up` (global CLAUDE.md step 1) loads recent
context; `mempalace_search` pulls specifics on demand — e.g. search the `gotchas` room
before touching scraper stdout/logging, the `src` room for mode-scan parser internals.
Companion bot knowledge is in the `discordbotafkj` wing.

Give a brief what's-done / what's-pending summary before starting the task.

## CRITICAL: Template Capture
**Always use `src/capture_template.py` to capture templates via ADB screencap.**
Never screenshot the BlueStacks window directly.
BlueStacks internal resolution is always 1080x1920 regardless of window size.
Window screenshots are scaled/different pixel space · templates will not match.

## Key Files
| File | Purpose |
|---|---|
| `src/scraper.py` | Main entry point · navigates game, captures screen, runs OCR · CLI flags for guild scan + each mode (see README.md) |
| `src/nav.py` | Screen navigation via ADB + template matching · screen-aware routing back to guild home |
| `src/device.py` | ADB interface (screenshot, tap, swipe, scroll) · leak-free `_shell`, retry/restart logic |
| `src/ocr.py` | Shared OCR wrapper (RapidOCR PP-OCRv5, v3 fallback) |
| `src/parser.py` | Extracts structured `Member` data from OCR results · `NAME_X_MIN`, `PURE_POWER_RE`, `TOTAL_RE` live here |
| `src/db.py` | DB helpers · owns the shared schema (members, snapshots, member_snapshots, name_corrections, member_name_history, dream_realm_bosses, dream_realm_boss_aliases, dream_realm_scores, afk_stage_rankings, arena_rankings, supreme_arena_rankings, honor_duel_rankings, arcane_lab_rankings, clashfronts_signups, guild_duel_rankings) · `guilds`/`warbands` are bot-owned (see below) · schema changes: ALTER guild.db once, fold into the CREATE · `validate_names()` returns `tuple[list[Member], list[str]]` · `RosterResolver.resolve()` tries the raw OCR'd name first, falling back to `_strip_merged_tag()` only if that fails (see Gotchas) |
| `src/capture_template.py` | ADB screencap → crop → save template |
| `src/import_history.py` | One-time historical import script (10 snapshots, 10/10/2025–5/3/2026) |
| `src/modes/common.py` | Shared vote plumbing for the ranking-mode scans (multi-frame consensus voting) · `RankRow.texts` collects any non-numeric text near a row beyond name/rank/points, including a second line under the name |
| `src/modes/dream_realm.py`, `arena.py`, `afk_stages.py`, `supreme_arena.py`, `honor_duel.py`, `arcane_lab.py`, `guild_duel.py` | Per-mode ranking scans · read every ranking card across many frames, resolve by majority vote |
| `src/modes/manual_scan.py` | Parses a user-submitted Supreme Arena screenshot (arbitrary resolution/aspect ratio) for off-server members; invoked by the bot's `/sa-manual` |
| `src/modes/clashfronts.py` | Scans the Clashfronts "Manage Sign-Ups" roster (not a Rankings screen) · name + selected/unselected checkmark per row, full-list overwrite · detects and skips the post-signup deployment map |
| `src/templates/` | OpenCV template images · must be captured via ADB |

## Database
Shared with bot at `C:\vscode\AFKDataMining\guild.db`.

Warbands are normalized: `warbands` table (canonical) + `warband_id` FK on `members` (current
warband) and `member_snapshots` (per-scan). `_resolve_warband()` snaps OCR reads to a warband id
(alias → exact → fuzzy); blank/unread warbands fall back to the member's known current warband.
`members.warband` text is kept in sync for display. Edit `WARBAND_ALIASES` in `db.py` for OCR
misreads the scraper should recognize.

**`guilds` and `warbands` are owned by the bot** (`DiscordBotAfkJ/utils/db.js`), not the miner —
Discord role/membership management is a bot concern. The miner only reads `warbands` here
(`_resolve_warband()`) against the same physical `guild.db` file; it never creates or seeds the
table. Add new warbands via the bot's admin panel, not by editing `db.py`.

### Power Value Convention
- Stored text: `"86329K"` (verbatim game display)
- Stored numeric: `float(86329 * 1000)` = 86329000.0
- Game shows `86329K` or `102M` depending on magnitude · store the K number as-is

## validate_names() Return Value
Returns `tuple[list[Member], list[str]]` · resolves each read into the canonical active
roster (alias → exact → fuzzy@0.86) so noisy OCR never creates duplicate members. The second
element is names that matched nothing · `save_snapshot(members, pending_names=...)` inserts those
with `pending = 1`, and the scraper prints `REVIEW_NAMES: name1, name2` so the bot posts a Discord
warning. **Non-interactive** · no `input()` prompt (old EOFError path removed). Approve/merge
pending members via the bot's `/review` or admin Members tab.

## Navigation
- Scraper navigates screen-aware: detects current screen, routes back to guild home
- 2.5s sleep between nav steps, 20 poll iterations max
- Saves debug screenshot on nav failure
- ensure_resolution() enforces 1080x1920 before scraping

## Scrolling & Parsing (tuned constants — don't naively change)
- **`scroll_down()` swipe `(540,1400 → 540,778, 2200ms)`** advances ~3 of the 4
  visible member cards (card pitch ~249px). The deliberate 1-card overlap means
  every card lands fully in some frame; OCR dedup (`_fuzzy_key`) drops the repeat.
  A bigger swipe drifts via fling momentum and accumulates off-grid (~10 swipes),
  re-introducing half-card straddle misses. Tuned on-device — re-measure pitch if
  the card layout changes.
- **`TOTAL_RE` targets group(1)** of `Guild Member (88/90)` = the CURRENT count,
  not group(2) capacity (90). Loosened pattern tolerates OCR mangling of the
  parens/slash. Lets the scrape detect completion ("All members captured").
- **`PURE_POWER_RE`** (anchored) guards the name slot: a candidate is a power
  value only if it STARTS as one. The old `POWER_RE.search()` substring match
  dropped real names containing digits+K/M (e.g. `Ramz78k` matched `78k`).
- **`NAME_X_MIN = 130`** (in `parser.py`, used by both `parse_members` and
  `parse_member_anchors`) excludes member title badges (Confidant, Warmaster,
  Envoy, Riff, Raff, Sage, ... — an open-ended, growing list) from being read
  as names. Titles render as their own OCR block far-left over the avatar
  (left-edge x measured 51-84 across multiple live captures), ~40-47px above
  the real name row. Before this fix only the `90 <= x` lower bound stood
  between a title and being accepted as a name — "Warmaster" at left_x=84
  cleared it by 6px and confirmed-live produced a phantom member carrying
  the real member's own power/activeness (grabbed from the row ~95px below,
  same offset `parse_members` uses to find the real power row). Because the
  title list keeps growing, this is a **structural** (position-based) fix,
  not a name-based skip list like `SKIP_NAMES` — don't add new titles there
  instead of relying on this bound. 130 sits ~45px above the highest measured
  title and ~105px below the closest measured real name (`"iat"`, a 3-char
  name, left_x=235) — biased toward the title side of the gap since an
  unmeasured edge case (a name whose gender-icon glyph merges into the same
  OCR block, further left than a clean split) is more likely on the name
  side, and dropping a real member is worse than a title phantom.

## AFK Stages Phase Navigation
Phase 2 dropped in-game (2026-07-04). Rankings screen has a "Phase N" tab bar
(same position/icon as Dream Realm's date bar — `dream_realm_right.png`
reused for paging once Phase 3 exists and pushes Phase 1 off-screen).
`_select_phase(phase)` in `afk_stages.py` OCR-taps the target tab, tolerant of
"Phase 1" misreading as "Phase l" (lowercase L) — same class of bug as
`_norm_time`'s l/1 fix, confirmed live.

A phase stays fully scannable/guild-filterable after being superseded, but its
card format changes: cleared members show `Cleared` + a date + a time instead
of a live `Phase Progress` number. Apex 1000 is the final stage, so `Cleared`
is recorded as that fixed value — the date/time itself isn't stored.

Business rule: once a new phase starts, the previous phase gets scanned
**one final time**, then only the current phase from then on — not every
phase every scan. Detection signal is the CURRENT phase's row-count being
zero for this season (not the previous phase's rows existing — those persist
forever, so their presence can't distinguish "not yet backfilled" from
"already backfilled"). Self-healing: a scan that dies mid-backfill leaves the
current phase at 0 rows, so it retries next run. The backfill scan passes
`reentry_fn=None` — `make_reentry_fn`'s second pass re-taps `rankings_btn`,
which lands back on the *current* phase tab by default, and would otherwise
silently mix current-phase reads into the backfilled phase's data.

## ADB reliability
- `device.py` `_shell` is leak-free: synchronous, context-managed connections;
  retry 2 → restart adb server (server_kill, psutil kill fallback) → 2 more.
- A no-progress watchdog in `scraper.py` aborts if no successful ADB call for
  `STALL_SECONDS` (120) — kills adb and exits rather than hanging.
- **AdGuard must exclude `C:\platform-tools\adb.exe`** — its loopback filtering
  throttles the multi-MB screencap transfers and was the root cause of scans
  degrading from 2-3 min to 20+ min. Do NOT exclude `HD-Player.exe` (unblocks
  BlueStacks ads).

## Dead Ends · Do Not Pursue

- **Qwen2-VL** · requires CUDA (NVIDIA) or MPS (Apple). AMD RX 9070 XT has neither · do not investigate. AdbAutoPlayer's installed app also cannot run it on this hardware (falls back to same RapidOCR we already use).
- **AdbAutoPlayer as a better OCR engine** · on this hardware they run PP-OCRv4 via rapidocr_onnxruntime, same family as ours. Their reliability advantage is **consensus voting** (collect all frames, vote per rank) not the engine. Port the voting approach, not the engine.
- **AdGuard: excluding `HD-Player.exe`** · unblocks BlueStacks ads but does NOT fix ADB throttling. Always exclude `C:\platform-tools\adb.exe` instead.

## Manual Scan (off-server members)
Some guild members play on a different server/district and never appear in the
live scan's guild-filtered ranking list. `/sa-manual` in the bot lets any member
submit a Supreme Arena screenshot (any resolution/aspect ratio — phone
screenshots, not ADB captures) and have it parsed the same way.

- `src/modes/manual_scan.py` · standalone entrypoint (`python -m modes.manual_scan <image_path>`),
  not part of `scraper.py`'s ADB-driven CLI. No BlueStacks/ADB dependency.
- `parse_manual_rows()` anchors purely on relative OCR-block geometry (nearest
  rank-digit block to each name block; podium excluded via the same wide-x-spread
  heuristic `parse_rank_rows` uses) — no fixed pixel constants, unlike the live
  scanner's `_scan_rank_column` (tuned to BlueStacks' 1080-wide captures, and
  confirmed to silently return zero rows on a wider/letterboxed image).
  Verified against a 1080x2404 and a letterboxed 2560x1440 (partial Traditional
  Chinese UI) screenshot — do not merge this logic back into `parse_rank_rows`.
- Unmatched names are reported (`MANUAL_REVIEW:` stdout line) but never written —
  membership/creation still belongs to the roster scan, same rule as
  `resolve_entries()`. Bot pings crysallis in `COMMAND_LOG_CHANNEL_ID` with the
  image when any name needs review.
- `save_supreme_arena(entries, force=True)` bypasses the live scan's Mon/Tue +
  once-per-day gate, writing to `current_supreme_period()` (the period-key
  math alone, refactored out of `get_supreme_period()`) regardless of weekday.
  `INSERT OR REPLACE` on `(member_id, period_start)` means a manual write and a
  same-day live scan never conflict or suppress each other — last write wins.

## Clashfronts (sign-up/selection tracking)
New guild event (recurring, ~3 weeks apart, exact cadence still unclear).
Members sign up guild-wide; leadership selects/approves a subset (30 slots
seen so far) via the in-game "Manage Sign-Ups" screen — one screen, one row
per signed-up member, a green (selected) or grey (unselected) checkmark per
row. No rank numbers on this screen at all.

- Nav is NOT `navigate_to_mode()` — that function always ends by tapping a
  Rankings-style control, wrong destination here. `navigate_to_clashfronts_signups()`
  in `nav.py` reuses the card-tap/OCR-scroll portion but OCR-taps `"ManageSign"`
  instead (the wrapped two-line button "Manage Sign-Ups" OCRs as `"ManageSign"` +
  `"Ups"` in separate blocks — targeting "Manage" alone only scores 0.75 fuzzy-match
  against `_ocr_tap_text`'s 0.82 threshold, found live before it shipped).
- No `apply_guild_filter()` — this screen is inherently guild-only already.
- `parse_rank_rows`/`_scan_rank_column` don't apply (no rank numbers) — `clashfronts.py`
  has its own name-block parser, checkmark state read via the existing
  `detect_tiers()`/`tier_for_row()` template-match pattern (`templates/clashfronts/tier_selected.png` /
  `tier_not_selected.png`, captured live — hue_tol=10 cleanly separates green/grey).
- `clashfronts_signups` table: single kept scan, **full-list overwrite**
  (`save_clashfronts_signups()` does `DELETE` then re-`INSERT`, not
  `INSERT OR REPLACE` merge) — a member absent from this scan's rows must NOT
  linger as "signed up" from a stale previous scan.
- **Checkmark misread bug found and fixed**: the floating scroll-nav button
  (a circular down-arrow that overlays the bottom-most visible row) corrupts
  that row's checkmark template match in whichever frame it lands there.
  `tier_for_row` returns `None` for an unreadable checkmark, but `_parse`
  originally collapsed that to `False` (`tier == "selected"`) — an occluded
  GREEN check therefore cast a confident False vote that competed with (and
  sometimes won against) the clean True reads of the same row from other
  scroll stops. Caught via the game's own "30/30 participants" counter as a
  ground-truth invariant: a full scan only produced 26 `selected=True` rows,
  with the 4 missing ones appearing as isolated `False`s inside an otherwise
  unbroken True run — a monotonicity violation, since selected members always
  sort before unselected ones. Fixed by making `_parse` emit `None` (abstain)
  for an unreadable checkmark instead of `False` — `_vote_entries` already
  drops `None` values per-field, so occluded frames stop competing and the
  clean reads decide. Re-verified: raw voted entries now show exactly 30
  `True` with a clean True→False boundary, matching 30/30 exactly.
- Verified live end-to-end after the fix: 69/70 raw voted entries resolved
  and saved (30 selected, 39 not-selected, matching 30/30 exactly), 1
  unmatched correctly dropped via `REVIEW_NAMES` (not written, zero pending
  members created — resolves against the real `members` table exactly like
  every other mode scan).
- `capture_template.py` had a latent bug fixed alongside this: `cv2.imwrite`'s
  return value was never checked, so a nested-path template name (e.g.
  `clashfronts/tier_selected`) silently saved nothing when the subdirectory
  didn't exist yet, while still printing "Saved". Now uses
  `mkdir(parents=True)` and checks the write succeeded.
- **Post-signup deployment screen**: once sign-ups close, tapping the
  Clashfronts card leads straight to the battle deployment map ("Round 1", a
  "Deploy" button) instead of "Manage Sign-Ups" — nothing to scan there.
  `navigate_to_clashfronts_signups()` (in `nav.py`) checks for this screen
  (OCR for "Deploy" button text, no template needed) the moment the
  "ManageSign" button isn't found, and raises the dedicated
  `ClashfrontsDeployScreen` exception immediately rather than exhausting all
  `flow_attempts` retries first — a plain "wrong screen" miss still falls
  through to the normal re-home-and-retry loop. `clashfronts.py`'s `scan()`
  catches that specific exception, presses back, and exits cleanly with a
  `CLASHFRONTS:` status line instead of surfacing `MODE_FAILED`. Originally
  this check lived only in `clashfronts.py`'s except-block (after retrying 3
  full nav cycles for nothing) — moved into the retry loop itself so the
  deploy screen is detected on the first miss. Same shape as
  `supreme_arena.py`'s inactive-screen handling, but text-detected rather than
  template-matched since "Deploy" is plain button text.

## Guild Duel (weekly crest tracking)
New recurring guild-vs-guild event (weekly). Members earn "crests" Mon-Fri;
Sat-Sun the guild spends them attacking a boss. An end-of-event "duel merit"
stat is expected eventually but its shape is unknown until a full cycle
completes — not tracked yet, intentionally out of scope.

- Ranking screen ("Guild Duel Total Crest Rankings") is laid out like every
  other mode's Rankings screen (podium + gray-numbered list, rank left,
  numeric value far right) — `parse_rank_rows()`/`scroll_scan()` needed no
  changes for the layout itself.
- **No in-game guild filter exists on this screen** — unlike every other
  mode, both guilds' contributors are listed together, since this is a
  guild-vs-guild event and the Rankings screen shows both sides. Each row
  carries its own guild tag directly under the member's name (e.g.
  `"RKF RiffRaff"`, coincidentally the same string as the `RKF RiffRaff`
  *warband* name in `SEED_WARBANDS` — unrelated collision, harmless since
  `_warband_skip_set()` already keeps warband names out of name detection).
  `guild_duel.py`'s `_is_our_guild()` checks that tag (lenient
  substring/fuzzy@0.75 match against `"rkf riffraff"`, tolerant of OCR noise
  on the tag) and drops any row that isn't ours *before* name resolution —
  deliberately not relying on `resolve_names()`'s fuzzy@0.86 roster match to
  sort out which guild a row belongs to, since a similarly-named member of
  the other guild could otherwise fuzzy-match onto one of ours and
  misattribute their crests.
- Getting the guild tag into reach of that check required a shared-code fix:
  `parse_rank_rows()`'s fallback path only collected extra text blocks at
  `x > 700` (tuned for right-side info like Dream Realm's score), but the
  guild tag sits in the name's own x-range, one line below it. Widened to
  `x > rank_x_max` (any non-numeric block near the row, excluding the name's
  own block) — verified safe for the two other consumers of `row.texts`
  (`dream_realm.py`, `afk_stages.py`), since both pattern-match specific text
  shapes rather than taking the first available block.
- `current_guild_duel_period()` anchors to the most recent Monday (mirrors
  `current_supreme_period()`'s Wednesday-anchor formula) but applies **no**
  scan-window gating — Supreme Arena's Mon/Tue-only + once-per-day guard is
  specific to that mode's result-finality window; Guild Duel crests can be
  usefully scanned any day Mon-Fri, so `save_guild_duel()` always writes via
  `INSERT OR REPLACE` against the current period.
- **Found and fixed a pre-existing name-truncation bug while verifying this
  live**: `parse_rank_rows` used to strip what it guessed was an OCR-merged
  server tag off the end of every name (`"Kits282"` → `"Kit"`), via a regex
  matching a trailing `[Ss]?\d{3,4}` shape. That guess is unresolvable from
  the string alone — it also silently truncated real names ending in digits
  or "s" (`"Scorpion175"` → `"Scorpion"`, unmatched despite an existing
  `name_corrections` entry keyed on the untruncated name; `"Crysallis"` →
  `"Crysalli"`, luckily still fuzzy-matched so it wasn't visibly broken).
  Fixed by moving the strip into `RosterResolver.resolve()` as a fallback: try
  the raw OCR'd name against the full alias/exact/normalized-core/fuzzy chain
  first, only fall back to `_strip_merged_tag()` if the raw name doesn't
  resolve. Preserves the original merged-tag case (`"Kits282"` fails raw →
  strips to `"Kit"` → resolves) while no longer mangling real names before
  they ever reach the resolver. Verified live: 47/47 names matched on the
  next scan (up from 45/46), zero regressions.

## Historical Data
10 snapshots imported covering 10/10/2025 through 5/3/2026.
`first_seen` for all members corrected to MIN(scraped_at) after import.
`scripts/sync-join-dates.js` (in bot repo) can update first_seen from Discord server join dates for linked members.
