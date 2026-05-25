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
4. Validate and correct OCR'd names against known history
5. Save or update this week's snapshot in `guild.db`

Alternatively, trigger a scan from Discord using `/scan` (authorized user only).

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
        scraper.py          Entry point. Orchestrates the full scrape.
        nav.py              Screen navigation via ADB + template matching.
        device.py           ADB interface (screenshot, tap, swipe, scroll).
        ocr.py              OCR wrapper around RapidOCR.
        parser.py           Extracts structured Member data from OCR results.
        db.py               SQLite schema, weekly upsert, name correction logic.
        capture_template.py Utility to capture template PNGs from live screen.
        templates/          Reference PNG files for template matching.
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
