# Data Miner · AFK Journey Guild Scraper

Python · ADB (Android Debug Bridge) · OpenCV · RapidOCR (onnxruntime) · SQLite

Scrapes guild roster data from AFK Journey running in BlueStacks, saves to SQLite.
Companion bot at `C:\vscode\DiscordBotAfkJ`.
See global context at `C:\Users\crysa\.claude\CLAUDE.md`.

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
| `src/scraper.py` | Main entry point · navigates game, captures screen, runs OCR |
| `src/db.py` | DB helpers · owns the shared schema (members, snapshots, member_snapshots, warbands, name_corrections, member_name_history) · bot creates only its bot-only tables · schema changes: ALTER guild.db once, fold into the CREATE · `validate_names()` returns `tuple[list[Member], list[str]]` |
| `src/capture_template.py` | ADB screencap → crop → save template |
| `src/import_history.py` | One-time historical import script (10 snapshots, 10/10/2025–5/3/2026) |
| `src/templates/` | OpenCV template images · must be captured via ADB |

## Database
Shared with bot at `C:\vscode\AFKDataMining\guild.db`.

Warbands are normalized: `warbands` table (canonical) + `warband_id` FK on `members` (current
warband) and `member_snapshots` (per-scan). `_resolve_warband()` snaps OCR reads to a warband id
(alias → exact → fuzzy); blank/unread warbands fall back to the member's known current warband.
`members.warband` text is kept in sync for display. Edit `SEED_WARBANDS`/`WARBAND_ALIASES` in
`db.py` for new warbands the scraper should recognize (or add via the bot admin panel).

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

## Historical Data
10 snapshots imported covering 10/10/2025 through 5/3/2026.
`first_seen` for all members corrected to MIN(scraped_at) after import.
`scripts/sync-join-dates.js` (in bot repo) can update first_seen from Discord server join dates for linked members.
