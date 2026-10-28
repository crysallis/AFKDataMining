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

**Tier icon templates carry their own row background (2026-10-28).** A tier template
(`templates/<mode>/tier_*.png`) is a rectangle, so it includes the row colour it was
captured on (white row, or the tan highlight behind the viewer's own row). Plain colour
matching then scores the same icon ~0.70 on any other background, under the 0.75 cut,
and `dream_realm.py` saved those rows as `common` (an all-epic screen read one epic and
the rest common). `detect_tiers(..., saturation=True)` matches on the HSV saturation
channel instead (icon high, every row background low), which scored 0.93-0.95 on all
five rows; the hue check still separates same-shape tiers (epic vs hard). Only Dream
Realm passes it so far; arena and clashfronts are unchanged. Rows already saved as
`common` are not corrected. `test_tier_background.py` pins it with synthetic frames.

## Key Files
| File | Purpose |
|---|---|
| `src/scraper.py` | Main entry point · navigates game, captures screen, runs OCR · CLI flags for guild scan + each mode (see README.md) |
| `src/nav.py` | Screen navigation via ADB + template matching · screen-aware routing back to guild home |
| `src/device.py` | ADB interface (screenshot, tap, swipe, scroll) · leak-free `_shell`, retry/restart logic |
| `src/ocr.py` | Shared OCR wrapper (RapidOCR PP-OCRv5, v3 fallback) |
| `src/parser.py` | Extracts structured `Member` data from OCR results · `NAME_X_MIN`, `PURE_POWER_RE`, `TOTAL_RE` live here |
| `src/db.py` | DB helpers · owns the shared schema (members, snapshots, member_snapshots, name_corrections, member_name_history, dream_realm_bosses, dream_realm_boss_aliases, dream_realm_scores, afk_stage_rankings, arena_rankings, supreme_arena_rankings, honor_duel_rankings, arcane_lab_rankings, clashfronts_signups, guild_duel_rankings) · `guilds`/`warbands` are bot-owned (see below) · schema changes: ALTER guild.db once, fold into the CREATE · `validate_names()` returns `tuple[list[Member], list[str]]` · `RosterResolver.resolve()` tries the raw OCR'd name first, falling back to `_strip_merged_tag()` only if that fails (see Gotchas) · `resolve_names()`'s exact-match fallback excludes `pending=1` rows (see ID-Assisted Name Resolution) · `member_by_ingame_id()`/`members_missing_ingame_id()` support that same feature, both treating `ingame_id = ''` the same as `NULL` |
| `src/capture_template.py` | ADB screencap → crop → save template |
| `src/capture_ids.py` | Standalone safety-net pass over the guild list for any member still missing an `ingame_id` after the main scan's `make_id_fallback_hook` already ran · unconditional early exit when nobody needs one · calls `mark_progress()` on real work now (didn't before — see ID-Assisted Name Resolution) |
| `src/import_history.py` | One-time historical import script (10 snapshots, 10/10/2025–5/3/2026) |
| `src/modes/common.py` | Shared vote plumbing for the ranking-mode scans (multi-frame consensus voting) · `RankRow.texts` collects any non-numeric text near a row beyond name/rank/points, including a second line under the name · `scroll_scan()`'s `on_stop` hook + `make_id_fallback_hook()` (see ID-Assisted Name Resolution) |
| `src/modes/dream_realm.py`, `arena.py`, `afk_stages.py`, `supreme_arena.py`, `honor_duel.py`, `arcane_lab.py`, `guild_duel.py`, `guild_duel_damage.py` | Per-mode ranking scans · read every ranking card across many frames, resolve by majority vote · all but `guild_duel.py` and `guild_duel_damage.py` wired to `make_id_fallback_hook()` |
| `src/add_guild_duel.py` | CLI to add/correct one Guild Duel row by hand, or `--missing` to list active members the scan didn't capture · same logic as the bot's `/guildduel` |
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
  `parse_member_anchors`) excludes member title badges (a member with no readable title logs `No title matched` only once the card is confirmed as a real member, so header text like `Active Players:89` and power misreads stay quiet) (Confidant, Warmaster,
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
  `STALL_SECONDS` (120) — kills adb and exits rather than hanging. This only
  catches a wedged ADB *transport* — see the game-progress watchdog below for
  the separate case where ADB stays responsive but the game itself is stuck.
- **AdGuard must exclude `C:\platform-tools\adb.exe`** — its loopback filtering
  throttles the multi-MB screencap transfers and was the root cause of scans
  degrading from 2-3 min to 20+ min. Do NOT exclude `HD-Player.exe` (unblocks
  BlueStacks ads).

## Game-progress watchdog
A second, independent heartbeat from the ADB one above: ADB can keep
responding (screenshots and taps all succeed) while the game itself is
genuinely stuck — e.g. a client-side hang where Back stops producing an
expected screen transition. That's exactly what caused a real incident
(2026-08-17): `navigate_home` had no direct way to recognize the overview map
(only three landmarks: guild_members, guild_home, the Exit-game popup after
Back), so when Back stopped producing that popup, it looped through
`max_attempts` and got endlessly re-tried by the outer navigation loops —
with the ADB-stall watchdog never tripping, since every ADB call kept
succeeding.

- `device.py` `mark_progress()`/`seconds_since_progress()`: a second
  heartbeat, updated only on confirmed game-state advances (a nav landmark
  reached, a scroll finding new rows) — never on a bare successful ADB call.
- `nav.py` `_is_at_overview()` — a direct `battle_modes_btn` check, added as
  a fourth `navigate_home` landmark alongside the three above, so reaching
  the overview no longer depends solely on the Back→popup round-trip.
- `scraper.py` `_start_game_progress_watchdog` — restarts the game if no
  confirmed progress for `NO_PROGRESS_SECONDS` (300): `device.restart_game()`
  (`am force-stop` + relaunch via package `com.farlightgames.igame.gp`), then
  `nav.wait_for_boot()` — a fresh launch lands on the "Tap to begin your
  journey" title screen, not a scannable state, so this taps through it (and
  any popup/announcement chain after) until `_is_at_overview` confirms
  arrival. Then hard-exits, same shape as the ADB-stall watchdog (this script
  runs fresh per `/scan`) — leaves the game in a real scannable state for the
  *next* invocation rather than stuck at the title screen.
- Modeled on AdbAutoPlayer's (`C:\vscode\AdbAutoPlayer`, a sibling repo kept
  around as a reference, not a dependency) separate activity-timeout watchdog
  + `restart_game` pattern — see its `game/_lifecycle_mixin.py` and
  `util/execute.py`'s watchdog thread for the original design this was
  adapted from.

## Dead Ends · Do Not Pursue

- **Qwen2-VL via CUDA/MPS** · superseded 2026-09-23, see below. The CUDA-or-MPS-only framing was true when written but AMD shipped a native ROCm-on-Windows preview (7.2.1, RDNA4-supported) since then.
- **Qwen2-VL via ROCm (tested 2026-09-23, do not pursue further right now — but see caveat)** · installed torch 2.9.1+rocm7.2.1 in an isolated venv (Python 3.12 via `uv`, deleted after testing) — real GPU compute confirmed (fp16 matmul executes, `torch.version.hip` populated, RX 9070 XT correctly reports 15.9GB VRAM), and AdbAutoPlayer's actual `has_sufficient_vram()` gate logic returns True on this hardware. ROCm-on-Windows itself is not the problem — it works.
  **Caveat on the test itself:** what was actually tested (a single Qwen2-VL call asked to transcribe every row on a full activeness screen) is a *harder, broader* task than what AdbAutoPlayer's changelog describes using it for — a supplemental pass to catch specifically the Hangul/Cyrillic names their own PP-OCRv5 detects zero data for, not a wholesale replacement pass. Their choice to use Qwen2-VL isn't undermined by this test; the test wasn't a faithful reproduction of their actual call pattern. Result: transcribed a Cyrillic name RapidOCR mangled into pure garbage (`БоссМразей` → RapidOCR's `EoccMpa3ei` vs Qwen's `BossМразей`, partially right) but **silently dropped one of 4 rows** despite an explicit "include ALL rows" instruction, and took **41.7s per call** (cold model, one screen crop). VRAM usage was fine (4.3GB peak).
  The 41.7s figure also isn't automatically disqualifying the way it reads in isolation — AdbAutoPlayer is a semi-autonomous farming bot that already spends minutes per screen on template matching/swipes, so tens of seconds for a name it'd otherwise get zero data for is a different cost-benefit than the same latency added to our `/scan` slash command, which a person is actively watching.
  Separately: tried to get the real AdbAutoPlayer.exe install (`C:\Users\crysa\AppData\Local\AdbAutoPlayer`) to use ROCm torch instead of its default CUDA-only path, to benchmark their actual app rather than a standalone script. Blocked — `_torch_metadata()` in `_guild_scan_setup.py` checks `"+cu" in version_string` to decide if a satisfactory torch is already installed; a ROCm build's version string (`+rocm7.2.1`) never matches, so `_ensure_qwen2vl_packages()` (called at the start of every guild scan) always decides it needs to reinstall CUDA torch over whatever's in the extras folder, undoing a manual ROCm seed every run. Fixing this means patching their app, which we chose not to do.
  Net: our OCR pipeline is a different shape than theirs (we need one clean pass over a whole paginated list per `/scan`, not a background farming loop that can afford per-name retries), so this isn't a fair apples-to-apples "should we switch" call yet. Worth a real revisit if: (a) a properly-scoped test — Qwen2-VL called only on rows RapidOCR already flagged as low-confidence/undetected, matching their actual usage — still shows good accuracy, or (b) AdbAutoPlayer's project adds ROCm-awareness to `_torch_metadata()` so their app can be benchmarked directly on this hardware.
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
- **Not-active detection (2026-08-23)**: separate from the deploy screen
  above — when the Clashfronts card never appears on Battle Modes at all
  (event genuinely not running this cycle), `navigate_to_clashfronts_signups()`
  now raises `ClashfrontsNotActive` instead of a bare `RuntimeError`, only
  when *every* flow attempt's failure point was specifically "card not
  found" (a `card_never_found` flag flips permanently false on any other
  failure step, so a mixed bag of failure reasons still falls through to
  the generic error + debug screenshot). `clashfronts.py` catches it and
  prints a clean skip line — no `press_back()` needed since nav never left
  Battle Modes on this path.

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

### Weekly Battle Report scan (replaces daily scan)

The scan no longer reads the live daily "Guild Duel Total Crest Rankings"
screen. It now reads the "Battle Report" screen once a week, which shows the
previous week's FINALIZED per-member crest totals — confirmed live to stay
stable and correct for the entire following week (rolls over exactly at
Monday 00:00 UTC, no lag).

- Nav path: Battle Modes → "Guild Mode" tab (Battle Modes now opens on a
  "Routines" tab; the tab label wraps, OCR returns just the "Guild" fragment,
  so that is tried first with the whole string as fallback · other modes stay
  on Routines and are unaffected) → Guild Duel card → lands on a "Preparation Phase"
  team-matchup tab by default (no Battle Report button there) → OCR-tap the
  "Elite Duelers" bottom-nav tab → OCR-tap "Battle Report" (top-right).
  `navigate_to_guild_duel_battle_report()` (`nav.py`) drives this directly
  rather than reusing `navigate_to_mode()`, since that helper's generic
  "tap Rankings" step overshoots past Elite Duelers onto the OLD daily
  rankings screen instead. The "Battle Report" button's wrapped two-line
  label OCRs as separate "Battle" / "Report" blocks (confirmed live,
  ratio 0.63 against the joined string — below the 0.82 match threshold),
  same class of issue as the "ManageSign" split below — the tap target is
  the single word "Battle".
- `get_guild_duel_period()` (`db.py`) gates the scan: computes the period via
  the pure `guild_duel_period_key(now)` — the Monday of the week Battle
  Report is currently displaying, which is one week BEHIND the most recent
  calendar Monday (e.g. on the Monday 00:00 UTC rollover itself, Battle
  Report has just switched to showing the PRIOR week, not the day's own
  Monday) — and returns `None` if that period already has a row with crests (a weekend damage-only row does not count). The daily
  `--guild-duel` CLI invocation becomes a fast no-op most days, and a
  missed/failed Monday run self-heals by retrying on the next day's scrape
  until it succeeds. `guild_duel_period_key`'s boundary math is covered by
  a standalone assertion script (`test_guild_duel_period.py`, repo root) —
  the first implementation had an off-by-one-week bug (returned the current
  week instead of the prior one) that only the multi-date assertions caught,
  not a single day's live run.
- The Battle Report card has a reward-icon banner above the list (numeric
  values measured at y=761-762 on a 1080x1920 capture) that
  `list_y_min=850` excludes from being read as row data (first real row
  measured at y=908) — and no hero/podium section (unlike Dream Realm/Arena).
- Uses its own `guild_duel_scroll_down()` (`device.py`) instead of the
  shared `scroll_down()` — the Battle Report popup's visible list area is
  shorter than the full Rankings screen the default swipe was tuned for.
- **Smallest list window of any scan (2026-10-26)**: about 485px visible (y 850
  to the viewer's pinned own-row bar at ~1335) against a 163px card, so the
  content may move at most ~320px per stop or rows pass without ever being fully
  visible. The old 310px swipe moved it ~425px, so rows landed half under the
  pinned bar (guild tag cut, dropped by `_is_our_guild`) or half under the
  banner (name cut, misread), and Caernafon (ranked 4-6) was missed most weeks.
  Now a 180px swipe (~245px, three live measurements within 5px, ~1.5 rows),
  one frame per stop with `settle_region`, `max_scrolls=160` (the list is ~177
  rows, ~120 stops). A scan takes ~22 minutes, up from ~12. A `--force-rescan`
  only overwrites rows it reads: members who left the guild since keep their old
  row (other guilds' rows are dropped by the tag check).
- **Stop-condition fix (2026-08-24)**: `scroll_scan`'s default early-stop
  (`require_new`: break after 5 consecutive stops with zero *new* keys)
  conflated "no rows kept this stop" with "list ended" for this mode
  specifically — a stop full of other-guild rows produces zero kept rows
  after `_is_our_guild()` filtering, which looked identical to genuinely
  reaching the end. Confirmed live: a real scan stopped after only 6 stops
  of a ~180-row list, saving just the viewer's own sticky row. Fixed by
  giving `scroll_scan` `require_new`/`no_change_limit`/`max_scrolls`
  params; Guild Duel passes `require_new=False` (trust only real screen
  change via `no_change_limit=4`, more tolerant than the shared default of
  2 since the swipe distance is itself unconfirmed) and `max_scrolls=90`
  (1.5x the ~60-scroll math for a 180-row list at ~3 cards/scroll, to
  absorb misfires/duplicate reads). `mark_progress()` now also fires on
  real scroll change (not just new kept rows), since a long other-guild
  stretch would otherwise starve the 300s game-progress watchdog.
- Single pass only. After the save, `scan()` prints `GUILD_DUEL: missing N
  active member(s): ...` (`get_guild_duel_missing(period)` in `db.py`: active,
  non-pending members with no row with crests for the period, counting hand-added rows) ·
  the bot's `/scan` reply forwards any `GUILD_DUEL:` line. No automatic second
  pass: the sticky viewer row is unrecoverable, so a retry could never close
  that gap. Fill gaps with `python src/add_guild_duel.py "<name>" <rank> <crests>`
  (`--missing` lists, `--period` overrides) or the bot's `/guildduel add`.
- `--force-rescan` CLI flag (`scraper.py`) bypasses `get_guild_duel_period`'s
  already-saved-this-week gate for a manual re-scan; `save_guild_duel` is an
  UPSERT on `(member_id, period_start)` that rewrites only `rank`, `crests` and `scanned_at`, so re-saving overwrites
  cleanly and a weekend `damage` value survives.
- **Known accepted gap**: the viewer's own row is sticky (pinned at a fixed
  position regardless of scroll position), occluding whatever real member
  row would otherwise be there in every sampled frame. Confirmed unavoidable
  even scrolling manually by hand (the row drops back into place the instant
  the scroll gesture ends) — accepted as an unrecoverable limitation, not
  engineered around with a completeness check.
- Migration: the daily-scan data for the transition week (period_start
  2026-07-06, 83 rows) was deleted and re-seeded from a real Battle Report
  scan (81 rows; top crest values matched what was visible on screen) rather
  than left mixed with daily partial reads.
- `_is_our_guild()` per-row guild-tag filtering (see above) is unchanged and
  still applies on this screen.

## Weekend damage scan (`guild_duel_damage`)

- Reads each member's total boss damage from the live "Total Duel Merit Rankings" list and saves it as `damage` (a raw integer, `567B` is 567000000000) plus `damage_scanned_at` on that week's `guild_duel_rankings` row. The Monday Battle Report scan fills `crests` and `rank` on the same row, so a row can be damage-only, crests-only or both. Anything meaning "has crests" filters `crests IS NOT NULL` (`get_guild_duel_period`, `get_guild_duel_missing`), and both saves are UPSERTs that touch only their own columns.
- Window: Saturday 00:00 to Sunday 23:59:59 UTC only (`guild_duel_damage_window_open`). Monday 00:00 UTC resets the screen to zero, so outside the window the mode skips and writes nothing (`--force-damage` overrides; `--force-rescan` deliberately does not, so redoing crests can never open the damage window). The period key is this week's Monday (`guild_duel_damage_period_key`), the same date `guild_duel_period_key` returns once the Battle Report is readable.
- Route: shared `_enter_guild_duel_card()` (Battle Modes, Guild Mode tab, Guild Duel card), then, during the battle phase, the card lands in the guild hall: tap the in-world "Guild Duel" sign, then "Details" on the boss screen. Never taps Elite Duelers. `navigate_to_guild_duel_battle_report` uses the same helper but cannot be live-checked during the battle phase (no Elite Duelers tab on the hall landing); verify it on a Monday.
- Parser: `rows_from_blocks()` pairs each damage value with the name above it and the guild tag below it by position and does NOT use `parse_rank_rows`: rank 1 is an ornate glyph OCR never reads, so a rank-keyed parser silently drops the top row. The list mixes both guilds, so rows drop on the guild tag. The pinned own-row bar pairs with its own damage, never the row it covers. Card geometry constants at the top of `modes/guild_duel_damage.py` were measured on a 1080x1920 capture.
- Scroll: `guild_duel_damage_scroll_down()` (swipe distance is the named constant `device.GUILD_DUEL_DAMAGE_SWIPE`, tunable), about 3 rows per swipe. The list keeps moving about 3.4 s after a swipe returns (measured), so the scan passes `settle_region` to `scroll_scan`, which replaces the fixed 1.2 s pause with `wait_until_settled` (poll until two list-region screenshots differ by under 0.5, cap 6 s, read anyway with a warning) and reads one frame per stop (`frames_per_stop=1`; a second frame of a settled screen is the same image). The list ends around stop 45 (it was stop 35 on an earlier, shorter list). The end-of-list check used to compare whole frames, and the animated podium and background alone differ by 1.5 to 1.6 (its threshold), so it never fired and the scan idled to `max_scrolls=70`, about 27 wasted stops (roughly 230 s) in the 2026-10-25 log; with `settle_region` set it now compares the list region only (idle diff 0.00), so the scan ends after `no_change_limit=4` unchanged stops past the end. `require_new=True` was rejected on purpose: it stops after 5 stops with no new names and would save nothing in a week where the opponent holds the first 15 rows.
- Unmatched names print `REVIEW_NAMES` and are not saved (membership belongs to the roster scan). The damage scan also prints `GUILD_DUEL_DAMAGE: unmatched '<name>' · saw <damage>` for each one (forwarded to the `/scan` reply) so the person can be found by value on the in-game list. **Follow-up (deferred by the user): do the same for every other scan mode** · `resolve_entries` in `modes/common.py` prints only the unmatched name, so the value that scan read is lost; extending it to show each mode's read value would let you find the row on screen and then rescan or fix it by hand. Tests: `test_guild_duel_damage.py` (repo root, plain script, temp DB). Bot side: off by default (`SCAN_GUILD_DUEL_DAMAGE`). Design and plan: `docs/superpowers/specs/2026-10-24-guild-duel-damage-scan-design.md` and the matching plan.
- **Clipped rows** (`modes/damage_clip.py`): the viewer's own row is a pinned bar at the top (after the scroll passes their rank), at the bottom (before it), or an ordinary row while their rank is on screen. A row sliding under the bar or off a list edge is cut in half and reads as junk in every engine (`McShake` with its top hidden reads `IviCJiiaKe`), so `_parse` keeps a row only when its whole card (`damage_y - 104` to `damage_y + 58`) sits inside the list window (y 710 to 1733) and does not overlap the own-row card. The own card is found as one 150 to 175 px dark run (median brightness under 195) in x 420 to 760; if it is not found the edge check still applies. Each row is fully clear at some stop (clear window about 850 px against a 560 px swipe), so skipped reads are read in full later; the scan prints how many were skipped.
- **Duplicate reads** (`modes/damage_dedupe.py`): runs before `resolve_entries`. An unresolved read with exactly the same damage as a resolved member and a clearly similar name (score at least 0.7, at least 0.15 over the runner-up, after folding Cyrillic and `IvI`-as-`M` look-alikes and allowing a 1 to 2 character leading tag) is dropped as a duplicate with a printed `GUILD_DUEL_DAMAGE: merged 'X' into 'Y' (same 311B)` line, so it never reaches `REVIEW_NAMES`. Damage alone never merges: values are rounded to billions and collide (three members tied at the same value in one week).
- **Cyrillic names** (`modes/damage_cyrillic.py`): production reads with the Chinese/English recognizer, which writes Cyrillic names as Latin look-alikes. Name-column blocks are re-read with the PP-OCRv5 Cyrillic recognizer (`ocr.read_cyrillic`, lazy) and replaced only when the result is mostly Cyrillic letters (60 percent or more) at confidence 0.8 or higher; Latin, CJK and empty results never replace. A read that already resolves to a roster member is replaced only by a Cyrillic read that resolves to the same member (`resolve_fn`), so a Cyrillic-named member still stored under Latin look-alikes cannot start failing to match; an unresolved read is replaced so the real name shows up in the review list. Members renamed to their Cyrillic names so far: `Найхэ`, `БоссМразей`, `Йона` (look-alike spellings kept as `name_corrections` aliases). `db._NAME_CHAR_RE` gained the Cyrillic range, so Cyrillic names now join `_normalized_roster`.
- **Debug frames** (`modes/damage_frames.py`): the screenshot behind any read that did not resolve is written to `debug_unmatched/` (git-ignored, member names are on screen; names are `<UTC time>_<damage>_<n>.png`, never the member name; pruned after 14 days, at most 20 frames per scan) and the unmatched line ends with `· frame <path>`.
- **Reset guard**: `save_gate()` re-checks the weekend window just before saving. A scan that ran past Monday 00:00 UTC read the reset screen and saves nothing (prints `window closed during the scan, not saving`); saving fewer members than an earlier scan of the same week only warns. Start the final weekend scan with time to spare (it takes about 10 minutes).
- Tests: `test_damage_clip.py`, `test_damage_frames.py`, `test_scroll_settle.py`, `test_damage_dedupe.py`, `test_damage_cyrillic.py`, `test_guild_duel_damage.py`, `test_parser_title_warning.py` (the roster parser's missing-title warning). Lab for replaying saved frames and comparing OCR variants: `ocr_lab/` (scripts committed, `ocr_lab/data/` ignored). Design: `docs/superpowers/specs/2026-10-25-damage-scan-clipped-rows-design.md`.

## ID-Assisted Name Resolution (2026-08-24)

A member's in-game User ID (`members.ingame_id`, captured via a tap on their
profile avatar → "User ID:" text in the popup) is permanent once known — it
never changes, unlike a display name OCR can garble differently every scan.
`make_id_fallback_hook()` (`modes/common.py`) uses that: for the 6
guild-filtered ranking modes (Arcane Lab, Dream Realm, AFK Stages, Arena,
Supreme Arena, Honor Duel) plus the guild scan itself, a row whose OCR'd name
fails to match anyone — or matches someone with no `ingame_id` on file yet —
gets tapped for its ID while still on screen, either resolving the row to its
real identity or backfilling the missing ID.

- **Not Guild Duel or Clashfronts**: Guild Duel's unfiltered ~180-row
  mixed-guild list would mean chasing far more other-guild misses than the
  tap cost is worth; both keep plain `resolve_entries()` with no hook.
- **Rides along on the scroll `scroll_scan` already does, per stop** — not a
  separate follow-up pass. An earlier design *did* do a second pass with its
  own re-navigation (needed to reset scroll position); that broke Arcane Lab
  live — `device.scroll_to_top()`'s up-swipe collapses that mode's list when
  it over-scrolls past the top, stranding the target row off-screen
  mid-pass. Tapping in-place while the row is already visible sidesteps that
  whole class of problem, and turned out faster overall despite the extra
  tap (no full re-navigation needed).
- `scroll_scan` gained an `on_stop(stop_img, stop_frame_entries)` hook,
  called once per scroll stop (right after that stop's `frames_per_stop`
  reads are collected, before scrolling on) — every existing caller that
  doesn't pass it is unaffected. Deliberately uses the SAME 2-frame stop
  vote as the rest of the scan for match/no-match decisions, not a stronger
  redundancy — a row rarely reappears after scrolling past.
- A resolved-by-ID identity is taught back via `save_correction()`
  (`ocr_name` → `canonical_name` in `name_corrections`), NOT by injecting
  `member_id` into the entry dict — `_vote_entries`/`resolve_entries` group
  observations by fuzzy name text with no `member_id` concept, so there's no
  clean seam to inject a resolved identity downstream. Since `_vote_entries`
  builds its own fresh resolver at the very end of the SAME `scroll_scan`
  call, a correction saved mid-scroll is picked up automatically by that
  run's own final vote, with zero special-casing needed.
- Guild scan wiring (`scraper.py`) passes `capture_ids.py`'s already-tuned
  `AVATAR_X`/`AVATAR_Y_OFFSET` (120/55, guild-list card layout) rather than
  the ranking-card constants — `make_id_fallback_hook()` takes them as
  optional params, defaulting to the ranking-card values. On a genuinely
  brand-new (never-seen) member, the ID lookup will also fail (they're not
  in `members` yet either way) and correctly falls through to today's
  pending-member flow — there's no cheap way to tell "new member" from
  "existing member, badly garbled name" before tapping, so every unmatched
  guild-scan name gets one tap attempt.
- `RANK_CARD_AVATAR_X`/`RANK_CARD_AVATAR_Y_OFFSET` (`modes/common.py`, `215`/
  `12`) are measured live against Arcane Lab's Rankings screen (name-block
  anchor at `(414,783)`, avatar portrait center at `~(215,795)`) — an
  initial placeholder guess (`120`/`55`, copied from the guild-list layout)
  was confirmed wrong live ("way too high"). Cards share the same basic size
  across ranking modes, so one measured constant should cover all of them.
- `capture_ingame_ids()` (the older, separate standalone pass over the guild
  list, still run after `scrape_guild()`'s main scroll as a safety net) has
  an unconditional early exit — `if not members_needing_ingame_id(): return`
  — so it costs nothing once the hook has already covered everyone during
  the first pass. This existed before tonight; what changed is that it's
  now reliable (see the `ingame_id = ''` bug below).

### Two bugs found via live testing while building this

- **`ingame_id` stored as `''` (empty string) is invisible to `IS NULL`
  checks** — happened from a manual DB edit that cleared the field (most
  DB tools write `''`, not `NULL`, on "clear"). `members_needing_ingame_id()`
  and `members_missing_ingame_id()` both now check
  `(ingame_id IS NULL OR ingame_id = '')`. Confirmed live twice: once via
  `capture_ingame_ids()` silently skipping two members whose IDs had been
  manually cleared for testing, printing "All members have an in-game ID."
- **`resolve_names()`'s exact-match fallback matched unreviewed pending
  rows**, confirmed live 2026-08-24: a single-frame OCR garble ("to") of
  NickNackN's card got created as a normal `pending=1` row by
  `validate_names()` (expected, correct behavior for a genuinely unmatched
  read). A *later*, unrelated scan happened to garble NickNackN's card the
  *same* way again — `resolve_names()`'s `raw.lower() in id_by_name`
  fallback (originally written for a legitimate case: a real member missed
  by the latest guild scan but still visible on a mode screen, so not in
  `_get_active_roster()`) didn't distinguish that bogus pending row from a
  real one, matched the second garbled read straight to the first bogus
  row, and the new ID-fallback hook then wrote NickNackN's real, freshly-
  captured `ingame_id` onto the bogus row — silently creating a duplicate
  that now *looked* legitimate instead of the harmless, reviewable
  duplicate it should have stayed. `resolve_names()` now excludes
  `pending=1` from its fallback's candidate pool (`id_by_name`). The
  bogus row was merged into the real member by hand (mirroring
  `DiscordBotAfkJ/utils/db.js`'s `mergeMembers` steps for the tables that
  actually had data) since the bot's own `mergeMembers` was blocked by an
  unrelated `better-sqlite3` Node-ABI mismatch in that terminal session.

## Historical Data
10 snapshots imported covering 10/10/2025 through 5/3/2026.
`first_seen` for all members corrected to MIN(scraped_at) after import.
`scripts/sync-join-dates.js` (in bot repo) can update first_seen from Discord server join dates for linked members.
