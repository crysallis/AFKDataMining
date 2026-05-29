# Data Miner · AFK Journey Guild Scraper

Python · ADB (Android Debug Bridge) · OpenCV · pytesseract · SQLite

Scrapes guild roster data from AFK Journey running in BlueStacks, saves to SQLite.
Companion bot at `C:\vscode\DiscordBotAfkJ`.
See global context at `C:\Users\crysa\.claude\CLAUDE.md`.

## CRITICAL: Template Capture
**Always use `src/capture_template.py` to capture templates via ADB screencap.**
Never screenshot the BlueStacks window directly.
BlueStacks internal resolution is always 1080x1920 regardless of window size.
Window screenshots are scaled/different pixel space · templates will not match.

## Key Files
| File | Purpose |
|---|---|
| `src/scraper.py` | Main entry point · navigates game, captures screen, runs OCR |
| `src/db.py` | DB helpers · `validate_names()` returns `tuple[list[Member], list[str]]` |
| `src/capture_template.py` | ADB screencap → crop → save template |
| `src/import_history.py` | One-time historical import script (10 snapshots, 10/10/2025–5/3/2026) |
| `src/templates/` | OpenCV template images · must be captured via ADB |

## Database
Shared with bot at `C:\vscode\AFKDataMining\guild.db`.

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

## Historical Data
10 snapshots imported covering 10/10/2025 through 5/3/2026.
`first_seen` for all members corrected to MIN(scraped_at) after import.
`scripts/sync-join-dates.js` (in bot repo) can update first_seen from Discord server join dates for linked members.
