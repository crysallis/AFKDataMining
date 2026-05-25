# AFKDataMining · Architecture

## Overview

The scraper is a pipeline of five distinct layers that transform a live game screen into structured database rows:

```
BlueStacks (ADB)
    |
    | raw PNG bytes over TCP
    v
device.py           Screenshot capture, tap/swipe input
    |
    | numpy ndarray (BGR image)
    v
nav.py              Template matching navigation to guild member list
    |
    | (game is now on the right screen)
    v
scraper.py          Scroll loop, deduplication, pass orchestration
    |
    | OCR results per frame
    v
parser.py           Structured Member extraction from OCR text
    |
    | list[Member]
    v
db.py               Weekly upsert into SQLite
```

---

## Layer 1 · device.py (ADB Interface)

All communication with BlueStacks goes through ADB over TCP at `127.0.0.1:5555`.

### Screenshot capture

```python
def screenshot() -> np.ndarray:
    result = subprocess.run(
        ["adb", "-s", DEVICE, "exec-out", "screencap", "-p"],
        capture_output=True,
    )
```

`exec-out screencap -p` streams raw PNG bytes directly over the ADB connection without writing a temp file on the device. This is faster than `adb pull` and avoids leaving files on the emulator.

The bytes go through two conversions:
1. `np.frombuffer(png_bytes, dtype=np.uint8)` · flat byte array that OpenCV can read
2. `cv2.imdecode(arr, cv2.IMREAD_COLOR)` · decoded BGR image as a numpy ndarray

**Resilience:** `screenshot()` retries up to 3 times with a 0.5s pause between attempts. If ADB returns empty bytes or `cv2.imdecode` returns `None` (corrupt/truncated data during a screen transition), the retry catches it rather than passing garbage to downstream consumers and causing a native C++ crash.

### Input simulation

Taps and scrolls are sent as ADB shell commands:

```python
def swipe(x1, y1, x2, y2, duration_ms):
    subprocess.run(["adb", "shell", "input", "swipe", x1, y1, x2, y2, duration_ms])
```

Scroll direction is controlled by swipe direction: down-to-up swipes scroll the list up (toward top), up-to-down swipes scroll down. Two scroll speeds exist: `scroll_down()` (larger jump) for the first pass and `scroll_down_small()` for the cleanup pass.

### Change detection

`screen_changed()` computes the mean absolute pixel difference between two frames:

```python
def _img_diff(a, b):
    return float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16))))
```

A threshold of 1.5 mean pixel difference determines whether the screen actually moved. This is used in scroll loops to detect when the list has reached the top or bottom — two consecutive unchanged frames signals the end.

---

## Layer 2 · nav.py (Navigation)

Before scraping can begin, the game must be on the guild member list screen. `nav.py` automates the full navigation path using **OpenCV template matching**.

### Template matching

```python
def find_template(screen, template_path, threshold=0.75):
    template = cv2.imread(str(template_path))
    result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val >= threshold:
        h, w = template.shape[:2]
        return (max_loc[0] + w // 2, max_loc[1] + h // 2)
    return None
```

`TM_CCOEFF_NORMED` returns a correlation score from -1 to 1. A score >= 0.75 is treated as a match. The center of the matched region is returned as tap coordinates.

### Navigation path

The full path from any screen to the guild member list:

```
Any screen
    -> press BACK (Android keyevent 4) until home screen detected
    -> tap guild_button (or fallback coordinates 780, 1830)
    -> wait for guild_home_indicator to appear
    -> tap guild_banner (the Members entry)
    -> wait for guild_members_indicator to appear
```

Each step polls up to 10 times with 0.5s delays before failing. If template matching fails to find a UI element, hardcoded fallback coordinates are used — this handles cases where the template confidence is below threshold but the element is still in its expected position.

### Templates

| File | What it detects |
|---|---|
| `overview_joystick.png` | Home/overworld screen (the joystick HUD) |
| `guild_button.png` | Guild entry in the overworld UI |
| `guild_home_indicator.png` | Guild home screen (the admin/settings icon) |
| `guild_banner.png` | The Members row in the guild home list |
| `guild_members_indicator.png` | The column headers on the member list |

---

## Layer 3 · scraper.py (Scroll Orchestration)

### Pass structure

The scraper runs up to three passes:

**Pass 1 · `scroll_down` (large scroll)**
Scrolls through the entire list top-to-bottom. Captures as many members as possible.

**Pass 2 · `scroll_down_small` (small scroll)**
Only runs if the member count is below the expected total. Slower scroll catches members missed due to partial visibility at frame boundaries.

**Cleanup pass**
Targets members where `activeness == 0`. Activeness is displayed on one side of the card; if a card was only partially visible during a scroll, power may have been captured but activeness missed. This pass re-scans until all members have activeness filled or the list stops moving.

### Deduplication within a scan

Each frame produces multiple OCR hits for partially visible members. The same member may appear across dozens of frames.

```python
def _fuzzy_key(name, seen_lower, threshold=0.88):
    key = name.lower()
    if key in seen_lower:
        return key
    for existing in seen_lower:
        if SequenceMatcher(None, key, existing).ratio() >= threshold:
            return existing
    return None
```

`SequenceMatcher` from the standard library computes edit-distance similarity. A 0.88 threshold catches OCR noise on the same name (e.g. `Caernaf0n` vs `Caernafon`) without merging genuinely different names.

When a duplicate is found, the existing record is updated rather than replaced — preferring non-zero values for power and activeness, and the name variant with more digit characters (a heuristic for better OCR reads of names containing numbers).

### Stop condition

The scroll loop exits when:
- `len(all_members) >= total` (all expected members captured), or
- Two consecutive frames show no screen change (end of list reached)

Total member count is read from the "Guild Member (X/Y)" header text visible at the top of the list. It defaults to 30 if not detected.

---

## Layer 4 · parser.py (OCR Result Parsing)

The OCR engine returns a list of `(bounding_box, text, confidence)` tuples. `parse_members()` groups these into `Member` objects by matching expected patterns.

### Member dataclass

```python
@dataclass
class Member:
    name: str
    last_active: str    # raw string: "2h ago", "1d ago", "Online"
    combat_power: str   # raw string: "109.0M", "85.3K"
    activeness: int     # integer score
```

Parsing uses regex to identify each field type from the OCR text and associates nearby text blocks by their vertical position on screen.

---

## Layer 5 · db.py (SQLite + Weekly Upsert)

### Schema

```sql
snapshots           One row per week. scraped_at updated on each scan.
members             One row per player. discord_id links to Discord account.
member_snapshots    One row per member per snapshot. All scraped stats live here.
name_corrections    Maps OCR'd names to corrected names. Persists across scans.
member_name_history Audit log of /rename operations from the bot.
```

### Weekly upsert logic

The week boundary is **Monday 00:00 UTC**, computed fresh at scan time:

```python
def _current_week_start():
    now = datetime.utcnow()
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday.isoformat()
```

On each scan, `save_snapshot()` checks whether a snapshot already exists with `scraped_at >= current_week_start`. If yes, it UPDATEs that snapshot and all its member rows. If no, it INSERTs a new snapshot.

This means:
- Multiple scans per week accumulate into one record (the latest data wins)
- The previous week's snapshot remains untouched for growth comparisons
- `scraped_at` always reflects when the data was last collected

### Name correction pipeline

Names from OCR often contain misread characters, especially ambiguous glyphs like `1/l/I`, `0/O`. The correction pipeline runs in order:

1. **Known correction** · exact match (case-insensitive) in `name_corrections` table
2. **Clean name** · if the name contains no ambiguous characters, accept as-is
3. **Fuzzy history match** · compare against all known names in DB history using SequenceMatcher (threshold 0.88). If matched, save the correction for future scans.
4. **Interactive prompt** · shown only when running manually (stdin available). Skipped silently in non-interactive contexts (bot subprocess, stdin closed = EOFError caught).

### last_seen_approx

The game displays relative time strings ("2h ago", "1d ago", "Online"). These are converted to absolute UTC timestamps at scrape time:

```python
def _parse_last_seen(last_active, scraped_at):
    # "2h ago" -> scraped_at - timedelta(hours=2)
```

This absolute value is what the inactivity alert queries against, since relative strings can't be compared across scans.

---

## Data flow summary

```
/scan command in Discord
    -> bot calls execFile(python, [scraper.py])
    -> scraper.py: navigate_to_guild_members()
        -> nav.py: press BACK until home -> tap guild -> tap members
    -> scraper.py: _scroll_pass() x2 + cleanup pass
        -> device.py: screenshot() -> numpy ndarray
        -> RapidOCR: ndarray -> [(bbox, text, conf), ...]
        -> parser.py: OCR results -> [Member, ...]
        -> _process_screen(): deduplicate into all_members list
    -> db.py: validate_names(all_members)
        -> name_corrections lookup -> fuzzy match -> save corrections
    -> db.py: save_snapshot(all_members)
        -> weekly upsert -> guild.db updated
    -> bot: post-scan inactivity alert if anyone 3+ days inactive
```

---

## capture_template.py

Utility for maintaining the template PNG files. Run with no arguments to save a full screenshot as `screen_debug.png`. Run with a name and crop coordinates to save a template:

```powershell
python src\capture_template.py guild_button 760 1810 800 1850
# Saves src/templates/guild_button.png
```

Use this whenever a template match stops working — usually after a game UI update or BlueStacks resolution change.
