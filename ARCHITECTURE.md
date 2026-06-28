# AFKDataMining · Architecture

## Overview

The scraper is a pipeline of five distinct layers that transform a live game screen into structured database rows:

```text
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

The same `device.py`/`nav.py`/`ocr.py` foundation also powers a parallel
subsystem: the six **game-mode ranking scans** under `modes/`, which use
read-many-and-vote consolidation instead of the roster scrape's first-read dedup.
See [Game-mode ranking scans](#game-mode-ranking-scans-modes) below.

---

## Layer 1 · device.py (ADB Interface)

All communication with BlueStacks goes through ADB over TCP at `127.0.0.1:5555`.

### Resolution enforcement

`ensure_resolution()` runs at the start of every scan before any navigation:

```python
def ensure_resolution() -> None:
    size    = adb shell wm size     # e.g. "Physical size: 1080x1920"
    density = adb shell wm density  # e.g. "Physical density: 240"
    if current != "1080x1920":
        adb shell wm size 1080x1920
    if "240" not in density:
        adb shell wm density 240
    # sleeps 2.0s if either was changed
```

This is critical because ADB window manager size and density are independent of the BlueStacks window size on the desktop. Resizing the BlueStacks window does not change the internal resolution · but other things (BlueStacks settings, display profile changes) can. If the resolution drifts, all template match coordinates become wrong. Enforcing it on every scan ensures templates always match.

### ADB transport · adbutils + wall-clock watchdog

All shell commands go through `_shell()`, which uses a persistent **adbutils** client/device
(`127.0.0.1:5555`) rather than spawning `adb` subprocesses. Each call is wrapped in
`_run_with_deadline()` — a worker thread joined with a hard wall-clock timeout:

```python
def _shell(cmd, stream=False, timeout=10.0):
    for attempt in range(4):
        if attempt == 2:
            _reconnect()                      # kill + restart ADB server, re-connect
        try:
            return _run_with_deadline(call, timeout + 5.0)
        except Exception:
            _device = None                    # drop the (possibly hung) handle
            time.sleep(0.5)
```

This matters because adbutils' own socket `timeout` does **not** reliably interrupt a stalled
`screencap` stream — right after a reboot, or when a game update raises the render load on the
guild screen, a capture can block indefinitely. The watchdog guarantees the scraper never
hard-freezes: a hung call is abandoned, the dead handle dropped, and the next attempt reconnects
(escalating to a full ADB server restart on attempt 3).

### Screenshot capture

```python
def screenshot() -> np.ndarray:
    png_bytes = _shell("screencap -p", stream=True, timeout=15.0)
```

`screencap -p` streams raw PNG bytes over the adbutils connection (no temp file on the device).
The bytes go through two conversions:

1. `np.frombuffer(png_bytes, dtype=np.uint8)` · flat byte array that OpenCV can read
2. `cv2.imdecode(arr, cv2.IMREAD_COLOR)` · decoded BGR image as a numpy ndarray

If ADB returns empty bytes or `cv2.imdecode` returns `None` (corrupt/truncated data during a
screen transition), `screenshot()` raises and the `_shell` retry/reconnect loop handles it —
rather than passing garbage downstream and crashing the OpenCV internals.

### Input simulation

Taps and scrolls go through `_shell()` just like screenshots, so they get the same adbutils watchdog and retry logic:

```python
def swipe(x1, y1, x2, y2, duration_ms=500):
    _shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}", timeout=5.0)
```

This replaced the old `subprocess.run(["adb", ...])` approach during the ADB recovery rewrite. Routing through `_shell()` means a stalled swipe command is subject to the same wall-clock deadline and reconnect escalation as a hung screencap.

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

`navigate_to_guild_members()` is screen-aware and **self-correcting** · it checks the current state before each action and retries the whole path (re-homing between tries) so a stalled tap or a popup doesn't abort the scan:

```text
Repeat up to flow_attempts (3):
    navigate_home():
        already at guild members / guild home?  return that (don't back out)
        else press Back until the "Exit game?" dialog appears — that dialog is
        the reliable "we're at the overview/root" landmark — then dismiss it (No)
        and report 'overview'
    at members?    return
    at overview?   _tap_to_reach(guild_button -> guild_home)
    at guild home? _tap_to_reach(guild_banner -> guild_members)  -> return
    (any step fails -> re-home and try the whole path again)
On exhaustion: save debug_nav_fail.png and raise RuntimeError
```

`_tap_to_reach(locate, is_there, …)` is the core resilience primitive. For each of up to 5 attempts it: **dismisses any blocking popup first**, taps the located button (or a fallback coord), **waits for the screen to settle** (`_wait_until_stable` — polls until pixels stop changing, replacing fixed sleeps so variable load times don't cause false negatives), then checks for the target. If the screen **didn't change at all**, the tap likely never registered → it re-taps. This directly fixes the failure where the "Exit game?" dialog (from backing up too far) swallowed a Guild tap.

**Popup handling:** `_dismiss_popup()` looks for known modal templates (`popup_cancel`, `popup_close` — capture the "No"/stay button of the exit dialog). Missing templates are skipped gracefully; the dialog also closes on Back, so navigation still recovers without them.

**Match confidence** is now logged at DEBUG for every `find_template` call (`match <name> <score> / <threshold>`), so `scraper.log` shows exactly how confident — or marginal — each identification is. Use that to decide whether a template needs recapturing larger/cleaner.

If template matching can't find a UI element, hardcoded fallback coordinates are used · this handles cases where confidence is below threshold but the element is in its expected position. The back-press is routed through `device.py` (`back()`), so it gets the same ADB watchdog as every other command.

### Templates

| File | What it detects |
| --- | --- |
| `popup_cancel.png` | The "Exit game?" dialog's No/stay button · **navigation root landmark** (replaces the joystick) and used to dismiss the dialog |
| `guild_button.png` | Guild entry in the overworld UI |
| `guild_home_indicator.png` | Guild home screen (the admin/settings icon) |
| `guild_banner.png` | The Members row in the guild home list |
| `guild_members_indicator.png` | The column headers on the member list |

`overview_joystick.png` is no longer used — it matched marginally (~0.76 vs a 0.75 threshold) and appears on multiple screens, so it couldn't reliably identify the overview. The "Exit game?" dialog (`popup_cancel`, ~0.99) is the dependable root signal instead.

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

Total member count is read from the "Guild Member (X/Y)" header text visible at the top of the list. It defaults to 90 if not detected (the roster spans RiffRaff plus the sister warbands, not just the 30-member RiffRaff guild).

All scraper stdout/stderr is reconfigured to UTF-8 at startup so Unicode in-game names (e.g. `Mullikai 「ψ」`) print without crashing on the Windows cp1252 console.

---

## Layer 4 · parser.py (OCR Result Parsing)

The OCR engine returns a list of `(bounding_box, text, confidence)` tuples. `parse_members()` groups these into `Member` objects by matching expected patterns.

### Member dataclass

```python
@dataclass
class Member:
    name: str
    last_active: str | None  # raw string: "2h ago", "1d ago", "Online" · nullable
    combat_power: str        # raw string: "109.0M", "85.3K"
    activeness: int          # integer score
```

Parsing is **identity-first**: the name anchor is found first, then the timestamp is treated as optional. This means a member whose active-time field is missing or garbled still produces a valid `Member` row (with `last_active = None`) rather than being dropped. `member_snapshots.last_active` is nullable in the DB; `save_snapshot` writes `NULL` instead of crashing on unreadable timestamps.

`parse_member_anchors()` is a lighter variant used by `capture_ids.py` -- it returns `(name, anchor_y)` pairs without parsing the full card, so the ID-capture pass can locate cards by name without re-running the full member parse.

---

## Layer 5 · db.py (SQLite + Weekly Upsert)

### Schema

The miner is the single owner of this shared schema · the bot (`DiscordBotAfkJ/utils/db.js`)
reads and writes these tables but only creates its own bot-only tables (jobs, birthdays,
recruitment, ...). `init_db()` CREATE statements always reflect the current shape: schema
changes are ALTERed once against guild.db, then folded into the CREATE · no migration trail
replayed on load.

```sql
snapshots           One row per week. scraped_at updated on each scan.
members             One row per player (canonical roster). discord_id links to Discord.
                    active = present in latest scan. pending = 1 when a read could not be
                    confidently matched to an existing member (awaiting bot /review).
member_snapshots    One row per member per snapshot. Stats incl. warband (text) + warband_id.
warbands            Canonical warband list (id, name UNIQUE, sort_order, archived).
                    members.warband_id = current warband; member_snapshots.warband_id = per-scan.
name_corrections    Maps OCR'd names to canonical names. Persists across scans.
member_name_history Audit log of /rename and merge operations from the bot.
```

Warband reads are resolved into the `warbands` table by `_resolve_warband()` (alias → exact →
fuzzy), mirroring the member-name resolution. A blank/unreadable warband falls back to the
member's known current warband so scans never drop people to "no warband".

### Weekly upsert logic

The week boundary is **Monday 00:00 UTC**, computed fresh at scan time:

```python
def _current_week_start():
    now = _utcnow()  # naive UTC via datetime.now(timezone.utc).replace(tzinfo=None)
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday.isoformat()
```

On each scan, `save_snapshot()` checks whether a snapshot already exists with `scraped_at >= current_week_start`. If yes, it UPDATEs that snapshot and all its member rows. If no, it INSERTs a new snapshot.

This means:

- Multiple scans per week accumulate into one record (the latest data wins)
- The previous week's snapshot remains untouched for growth comparisons
- `scraped_at` always reflects when the data was last collected

### Name resolution pipeline · `validate_names()`

OCR reads drift between scans (especially with the warband text now sitting next to names), so
each read is resolved *into* the canonical active roster rather than trusted verbatim — this is
what prevents a noisy read from creating a duplicate member row. The pipeline runs in order, per
name, and **never blocks on input** (the old interactive prompt is gone):

1. **Alias** · exact match (case-insensitive) in `name_corrections` → use canonical name
2. **Exact roster** · matches a current `members` row where `active = 1` → use that name
3. **Fuzzy roster** · `SequenceMatcher` vs the active roster (threshold 0.86). On a hit, the
   alias is saved to `name_corrections` so it resolves instantly next scan.
4. **Unmatched** · accepted as-is, added to the `uncertain` list. `save_snapshot()` creates the
   member with `pending = 1`, and `scraper.py` prints a `REVIEW_NAMES:` line that the bot turns
   into a Discord warning. The member is then approved or merged via the bot's `/review`.

Because resolution targets the canonical roster (not raw snapshot history), confident matches
reuse the existing `member_id` and no duplicate row is ever created.

### last_seen_approx

The game displays relative time strings ("2h ago", "1d ago", "Online"). These are converted to absolute UTC timestamps at scrape time:

```python
def _parse_last_seen(last_active, scraped_at):
    # "2h ago" -> scraped_at - timedelta(hours=2)
```

This absolute value is what the inactivity alert queries against, since relative strings can't be compared across scans.

---

## Data flow summary

```text
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
        -> resolve into roster: alias -> exact -> fuzzy -> else flag pending
    -> db.py: save_snapshot(all_members, pending_names)
        -> weekly upsert (pending reads inserted with pending=1) -> guild.db updated
    -> bot: post-scan inactivity alert if anyone 3+ days inactive
```

---

## Game-mode ranking scans (`modes/`)

Separate from the guild roster scrape above, the scraper also captures per-member
**rankings** for six game modes: `dream_realm`, `afk_stages`, `arena`,
`supreme_arena`, `honor_duel`, `arcane_lab`. Each lives in `src/modes/<mode>.py`
with a `scan()` entry point and is run via `scraper.py` CLI flags
(`--arena`, `--honor-duel`, ...) or the bot's `/scan` (each gated by a
`SCAN_*` bot-config key, toggled in the admin Scan Modes tab). With no flags the
scraper runs only the roster scan; passing mode flags runs just those modes (add
`--guild` to also run the roster scan in the same invocation).

Shared scaffolding is in `modes/common.py`:

- **`parse_rank_rows()`** · rank-number-anchored card parser. The large rank
  number on the left of each card is the anchor; the name, points/score, and
  any extra text blocks are gathered by vertical proximity. Ranks come from
  `_scan_rank_column()`, a single OCR pass over the left rank strip using
  PP-OCRv5 (it reads the calligraphic single-digit glyphs that grayscale/v3
  miss). After the OCR pass, any rank **absent** from `_seen_ranks` is filled
  by **template matching** against `rank_1_badge.png`, `rank_2_badge.png`,
  `rank_3_badge.png`. In practice this almost always means rank #1: the gold
  crown glyph OCRs successfully only ~1-5% of the time (measured over 100
  trials) but matches the template at ~100%. Hits above `col_y_min` are
  discarded to prevent the animated podium from producing false positives.
- **`scroll_scan()`** · the open-ended scroll loop. Unlike the roster scrape's
  first-read-wins dedup, mode scans **collect every observation** from every
  frame and consolidate them by majority vote afterward (`_vote_entries()`):
  observations are clustered by fuzzy name similarity, then each field (rank,
  points, ...) is decided by `Counter.most_common`. `None` reads are dropped
  from the vote, so a single clean frame is enough.
- **`resolve_entries()`** · maps each voted OCR name to a `member_id` via the
  same roster matcher (`alias -> exact -> normalized-core -> fuzzy@0.86`).
  Unmatched names are **dropped**, not created · membership belongs to the
  roster scan. Unmatched reads surface as `REVIEW_NAMES:` for the bot.

### Why voting (not re-passes)

The reliability win over naive scanning is **read-many-and-vote**, not a better
OCR engine. `scroll_scan` samples `frames_per_stop=2` temporally-spread frames at
each scroll stop (0.4s apart) and votes across all of them. This replaced an
earlier second full re-navigated pass: same vote redundancy, no rescroll, and
samples spread in time so they survive animated rows. The reentry second pass now
runs only in single-frame mode (`frames_per_stop=1`).

This matters because the winner **podium** (top-3 drawn as animated heroes)
flickers the ornate gold top-1 rank glyph in and out of OCR · measured ~13% of
live frames miss it. One missed frame no longer loses the rank, and the podium's
decorative badges are excluded from the list parse by a y-filter: the rank strip
is scanned from the top for stable detection, then hits above the list's start
(`col_y_min`) are discarded in Python rather than cropping the image (cropping
shifts the 3x resize grid and can make the borderline glyph vanish at certain
offsets).

### Per-mode notes

- **dream_realm** · multi-day, multi-boss; has its own date-tab navigation
  instead of the standard reentry. One `dream_realm_scores` row per member per
  boss per scan day.
- **afk_stages** · progress is text (`Apex N`), not numeric points, so the parser
  is given a `value_re` to recognize the card; season comes from the DB, phase
  from the on-screen badge.
- **arcane_lab** · three numeric columns (difficulty / floor / points) assigned
  by nearest header x-position. The `Difficulty` and `Floor` column header
  strings are explicitly skipped as name candidates so they can't be mistaken
  for member names on the first visible frame.
- **honor_duel** · the viewer's own "My Rank: Unranked" slot is skipped so it
  can't steal the adjacent card's rank.
- **supreme_arena** · off Mon/Tue UTC; dismisses the Daily Calculation popup.

Results are written to per-mode tables (`*_rankings`, `dream_realm_scores`) keyed
by `member_id` + `scanned_at`; the bot reads the latest scan per member for
`/member`. Each mode prints a `<MODE>:`-prefixed status line to stdout that the
bot parses and posts to Discord.

---

## capture_ids.py

A separate ADB pass that populates `members.ingame_id` (the in-game "User ID") for any member that doesn't yet have one. Runs after the main roster scan.

**Flow:**

1. Query `members_needing_ingame_id()` -- members where `ingame_id IS NULL`.
2. Scroll through the guild member list (same scroll loop as the main scan).
3. At each stop, OCR `FRAMES_PER_STOP=2` spread frames and collect `(name, anchor_y)` pairs from `parse_member_anchors()`.
4. Resolve each name through the same roster matcher (`corrections → exact → fuzzy`) used by the main scan.
5. For each target found on screen: `tap(AVATAR_X, anchor_y + AVATAR_Y_OFFSET)` to open the profile popup, OCR the popup for the `User ID: <number>` string, call `set_ingame_id()`, then `back()` to return to the list.
6. Stop when all targets are found, or the list ends (two consecutive no-change frames).

The two-frame-per-stop approach mirrors the mode scans: a card whose name the OCR flubs in one frame is often clean in the next. The multi-sample union means the tap decision uses the best available read.

If a member's ID is not captured in one pass (the profile popup didn't render the ID in time), the script prints a warning and leaves `ingame_id` NULL so the next scan retries it.

---

## capture_template.py

Utility for maintaining the template PNG files. Run with no arguments to save a full screenshot as `screen_debug.png`. Run with a name and crop coordinates to save a template:

```powershell
python src\capture_template.py guild_button 760 1810 800 1850
# Saves src/templates/guild_button.png
```

Use this whenever a template match stops working · usually after a game UI update or BlueStacks display profile change.

### Critical: always use this script to capture templates

Never take a Windows screenshot of the BlueStacks window and crop it manually. The BlueStacks window can be any size on the desktop, but the game always runs internally at 1080x1920. `capture_template.py` pulls from ADB's internal screencap buffer at the true resolution · coordinates from a desktop screenshot of a resized window will be wrong and will cause template matches to fail silently.

Workflow for recapturing a template:

1. Navigate the game to the screen that shows the element you want to capture.
2. Run `python src\capture_template.py` (no args) to save `screen_debug.png`.
3. Open `screen_debug.png` in any image editor · hover the cursor over the element corners to read pixel coordinates from the status bar.
4. Run `python src\capture_template.py <name> <x1> <y1> <x2> <y2>` with those coordinates.
5. Open the saved template PNG and confirm it contains the correct UI element.
