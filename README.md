# AFKDataMining

Automated guild data scraper for AFK Journey. Connects to BlueStacks via ADB, navigates to the guild member list, extracts member data using OCR, and saves weekly snapshots to a shared SQLite database.

---

## Prerequisites

- **BlueStacks** running AFK Journey with ADB enabled (Settings > Advanced > Android Debug Bridge)
- **ADB** installed and on your PATH (`adb --version` should work)
- **Python 3.11+**
- A Python virtual environment with dependencies installed (see Setup)

---

## Setup

```powershell
# Create and activate the venv
python -m venv venv
.\venv\Scripts\Activate

# Install dependencies
pip install -r requirements.txt
```

### BlueStacks ADB connection

BlueStacks exposes ADB on localhost port 5555 by default. Verify with:

```powershell
adb connect 127.0.0.1:5555
adb devices
# Should show: 127.0.0.1:5555   device
```

---

## Running a scan

```powershell
.\venv\Scripts\python.exe src\scraper.py
```

The scraper will:
1. Navigate from the current screen to the guild member list automatically
2. Scroll through all members, capturing each via OCR
3. Run up to two additional passes to fill in missing data
4. Resolve each OCR'd name into the canonical member roster (alias → exact → fuzzy match); reads it can't confidently match are flagged `pending` for review via the bot's `/review`
5. Save or update this week's snapshot in `guild.db`

The scan runs non-interactively — it never prompts for input — and is resilient to ADB stalls via a wall-clock watchdog, so a hung `screencap` can't freeze it.

Alternatively, trigger a scan from Discord using `/scan` (authorized user only).

### Game-mode ranking scans

Beyond the roster scrape, the scraper can capture per-member rankings for seven game
modes, plus Clashfronts sign-up/selection tracking. Each is enabled with a flag
(with no flags, only the roster scan runs):

```powershell
.\venv\Scripts\python.exe src\scraper.py --arena --honor-duel
# flags: --dream-realm --afk-stages --arena --supreme-arena --honor-duel --arcane-lab --clashfronts --guild-duel
```

The ranking scans read every ranking card across many frames and resolve
the final value by majority vote, so they survive OCR noise and animated rows.
`--clashfronts` instead scans the "Manage Sign-Ups" roster (not a rankings
screen) and records who's signed up and who's been selected/approved; once
sign-ups close it lands on the deployment map instead, which the scan detects
(via the "Deploy" button) and skips cleanly. `--guild-duel` scans the Guild
Duel "Total Crest Rankings" screen, which — unlike every other mode — mixes
both guilds in the same list with no in-game filter; each row's own guild tag
is checked instead, and only "RKF RiffRaff" rows are kept. After the scan it
lists any active members it missed; add them with `python src/add_guild_duel.py
"<name>" <rank> <crests>`. The separate `--guild-duel-damage` mode (Saturday-Sunday UTC only) saves each member's boss damage onto the same weekly row. It skips rows cut off by the pinned own-row bar or a list edge, reads a settled screen, merges duplicate misreads by damage value, and reads Cyrillic names in Cyrillic (details in `CLAUDE.md`). Results go to
per-mode tables in `guild.db` and feed the bot's `/member` (rankings) or
`/clashfronts` (sign-ups) commands. See `ARCHITECTURE.md` for the voting and
rank-reading details.

---

## Output

Data is saved to `guild.db` (SQLite) in the project root. The database is shared with MeerBot (the Discord bot), which reads from it to serve slash commands.

Snapshots follow a **weekly upsert** model — one snapshot per week, updated in place on each scan. The week boundary is **Monday 00:00 UTC** (Sunday 8PM EDT / 7PM EST), matching the AFK Journey activeness reset.

---

## Template images

Screen navigation relies on PNG reference images stored in `src/templates/`. If the game UI changes or BlueStacks resolution changes, these may need to be recaptured using `src/capture_template.py`.

```powershell
# Capture a full screenshot for reference
.\venv\Scripts\python.exe src\capture_template.py

# Capture a named template crop
.\venv\Scripts\python.exe src\capture_template.py guild_button 760 1810 800 1850
```

---

## Project structure

```
AFKDataMining/
    src/
        scraper.py          Entry point. Orchestrates roster + mode scans.
        nav.py              Screen navigation via ADB + template matching.
        device.py           ADB interface (screenshot, tap, swipe, scroll).
        ocr.py              Shared OCR (RapidOCR PP-OCRv5, with v3 fallback).
        parser.py           Extracts structured Member data from OCR results.
        modes/              Six game-mode ranking scans + shared vote plumbing.
        db.py               SQLite schema, weekly upsert, name correction logic.
        import_history.py   One-time historical snapshot import.
        capture_template.py Utility to capture template PNGs from live screen.
        capture_ids.py      ADB pass: taps each member avatar, OCRs User ID popup,
                            stores ingame_id in DB. Runs after main scan for members
                            with no stored ID.
        templates/          Reference PNG files for template matching.
    test_rank_scan.py       Stress tester: takes N live ADB frames, reports rank #1
                            detection rate split OCR vs template match.
    guild.db                SQLite database (shared with MeerBot).
    requirements.txt        Python dependencies.
```

---

## Dependencies

| Package | Purpose |
|---|---|
| `rapidocr-onnxruntime` | OCR engine for reading member data from screenshots |
| `opencv-python` | Screenshot decoding, template matching for navigation |
| `numpy` | Image array operations |
| `adbutils` | ADB device management |
