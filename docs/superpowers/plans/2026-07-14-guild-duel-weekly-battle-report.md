# Guild Duel Weekly Battle Report Scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the daily Guild Duel "Total Crest Rankings" scan with a once-weekly scan of the "Battle Report" screen, which shows the previous week's finalized per-member crest totals.

**Architecture:** A pure period-key function (`guild_duel_period_key`) computes the previous-Monday period from any given `now`, tested directly with assertions. A DB-gated wrapper (`get_guild_duel_period`) uses it plus a "does this period already have rows" check to decide whether to scan at all — mirroring `get_supreme_period()`'s shape. `guild_duel.py`'s `scan()` checks that gate *before* doing any navigation, so a skip day costs nothing. Navigation adds one new function in `nav.py` that reuses the existing `navigate_to_mode("guild_duel")` path and then taps into the Battle Report popup. Parsing reuses `parse_rank_rows()` with a tuned `list_y_min` and a Battle-Report-specific scroll increment.

**Tech Stack:** Python, ADB via `adbutils` (`device.py`), OpenCV template matching + RapidOCR (`nav.py`, `ocr.py`), SQLite (`db.py`). No test framework in this repo — device/nav/OCR logic is verified live against the running game, not via pytest (see Global Constraints). Pure date-math gets a standalone assertion script since it's the one piece live verification structurally can't cover (can't observe multiple `now` values in one live run).

## Global Constraints

- No em dashes anywhere in code, comments, docs, or commit messages — use `·` or `...` instead.
- No unnecessary comments in code; comments only for non-obvious WHY (hidden constraints, workarounds, invariants).
- This codebase has no pytest suite for nav/OCR/scan logic — those are verified by running the real scraper against the live device/game and observing printed output (`REVIEW_NAMES:`, `MODE_FAILED:`, mode-specific status lines) or querying `guild.db` directly, not by writing unit tests. Only the pure period-key date math gets a real assertion-based test, per the design's period-key risk.
- Schema changes: ALTER `guild.db` once, then fold the column/table change into the `CREATE TABLE IF NOT EXISTS` in `db.py` — no migration trail replayed on load. (This plan makes no schema changes; `guild_duel_rankings` table shape is unchanged.)
- Python venv is at `c:\vscode\AFKDataMining\venv\Scripts\python.exe` — always invoke scripts through it, not bare `python`.
- Template capture only via `src/capture_template.py` against a live ADB screencap (BlueStacks internal resolution 1080x1920) — never a Windows window screenshot.
- `CLAUDE.md` and mempalace (`afkdatamining` wing) must be updated as part of "done" for this feature (existing project convention: "complete" includes docs, not just working code).

---

## File Structure

- **Modify `src/db.py`**: add `guild_duel_period_key(now)` (pure), `get_guild_duel_period()` (gated wrapper, replaces `current_guild_duel_period()`), and change `save_guild_duel()` to use the gate. Remove `current_guild_duel_period()`.
- **Modify `src/nav.py`**: add `navigate_to_guild_duel_battle_report(flow_attempts=3)`.
- **Modify `src/modes/guild_duel.py`**: check the period gate before navigating; navigate via the new Battle Report function; tune `list_y_min` for the reward-banner exclusion; add a Battle-Report-specific scroll step; drop the custom `_reentry()` in favor of multi-sample `frames_per_stop` (matches how Supreme Arena/other modes already rely on `scroll_scan`'s default redundancy without a hand-rolled reentry, except where a mode truly needs one).
- **Create `test_guild_duel_period.py`** (repo root, matching `test_rank_scan.py`'s existing ad-hoc top-level placement): standalone assertion script for `guild_duel_period_key()`.
- **Modify `CLAUDE.md`**: update the Guild Duel section to describe the new weekly Battle Report scan, replacing the daily-scan description.
- **Migration**: one-off DB cleanup + manual scan run, not a code file — documented as its own task with exact SQL and verification query.

---

### Task 1: Pure period-key function + standalone test

**Files:**
- Modify: `src/db.py` (add `guild_duel_period_key`, remove nothing yet — `current_guild_duel_period` removal happens in Task 2 once nothing calls it)
- Create: `test_guild_duel_period.py`

**Interfaces:**
- Produces: `guild_duel_period_key(now: datetime) -> str` — pure function, no DB access, no `_utcnow()` call inside. Takes a naive UTC `datetime`, returns the ISO date string of the most recent Monday on/before `now` (Monday itself returns its own date).

- [ ] **Step 1: Add `guild_duel_period_key` to `src/db.py`**

Find `current_guild_duel_period()` (currently at approximately line 857-865):

```python
def current_guild_duel_period() -> str:
    """Returns the period_start (most recent Monday, ISO date) for 'now' ·
    always resolves, regardless of weekday. Crests accumulate Mon-Fri; no scan
    window gating (unlike Supreme Arena) since any day's read is useful.
    Anchored to Monday (weekday index 0) instead of Wednesday (index 2)."""
    now = _utcnow()
    days_since_monday = (now.weekday() + 7 - 0) % 7
    monday = (now - timedelta(days=days_since_monday)).date()
    return monday.isoformat()
```

Add a new pure function directly above it (leave `current_guild_duel_period()` in place for now, it's still referenced by `guild_duel.py` until Task 3):

```python
def guild_duel_period_key(now: datetime) -> str:
    """Returns the period_start (ISO date) of the most recent Monday on/before
    `now` · pure function, no DB access, so the boundary math can be asserted
    directly against known dates instead of only observed live on one day.
    Battle Report shows the week that just ended for the entire following
    week (confirmed live: still showed last week's result on a Tuesday with
    the new week's Prep Phase already running) and rolls over exactly at
    Monday 00:00 UTC (confirmed, no lag) · so 'the previous Monday relative
    to now' is always the correct period whenever this is called on/after
    that Monday."""
    days_since_monday = (now.weekday() + 7 - 0) % 7
    monday = (now - timedelta(days=days_since_monday)).date()
    return monday.isoformat()
```

- [ ] **Step 2: Write the assertion test**

Create `test_guild_duel_period.py`:

```python
"""Assert guild_duel_period_key's Monday-boundary math against known dates.
Run: venv/Scripts/python.exe test_guild_duel_period.py
No pytest in this repo (nav/OCR logic is verified live) · this is the one
exception, since date-math boundaries can't be observed by running the
scraper once on one real day.
"""
import sys
from datetime import datetime
sys.path.insert(0, "src")

from db import guild_duel_period_key

cases = [
    # (now, expected_period_start, description)
    (datetime(2026, 7, 20), "2026-07-13", "Mon Jul 20 -> Jul 13 (that day's own Monday)"),
    (datetime(2026, 7, 14), "2026-07-06", "Tue Jul 14 -> Jul 6 (matches live Battle Report capture)"),
    (datetime(2026, 7, 19), "2026-07-06", "Sun Jul 19 -> Jul 6 (still last week, right before rollover)"),
    (datetime(2026, 7, 20, 0, 0, 1), "2026-07-13", "Mon Jul 20 00:00:01 -> Jul 13 (just after rollover)"),
    (datetime(2026, 7, 17), "2026-07-13", "Fri Jul 17 -> Jul 13 (mid-week)"),
]

failures = 0
for now, expected, desc in cases:
    actual = guild_duel_period_key(now)
    ok = actual == expected
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {desc}: got {actual}, expected {expected}")
    if not ok:
        failures += 1

if failures:
    print(f"\n{failures}/{len(cases)} cases failed.")
    sys.exit(1)
print(f"\nAll {len(cases)} cases passed.")
```

- [ ] **Step 3: Run it and verify all cases pass**

Run: `cd c:\vscode\AFKDataMining && venv/Scripts/python.exe test_guild_duel_period.py`

Expected output:
```
[PASS] Mon Jul 20 -> Jul 13 (that day's own Monday): got 2026-07-13, expected 2026-07-13
[PASS] Tue Jul 14 -> Jul 6 (matches live Battle Report capture): got 2026-07-06, expected 2026-07-06
[PASS] Sun Jul 19 -> Jul 6 (still last week, right before rollover): got 2026-07-06, expected 2026-07-06
[PASS] Mon Jul 20 00:00:01 -> Jul 13 (just after rollover): got 2026-07-13, expected 2026-07-13
[PASS] Fri Jul 17 -> Jul 13 (mid-week): got 2026-07-13, expected 2026-07-13

All 5 cases passed.
```

If any FAIL, fix `guild_duel_period_key` before proceeding — this is the load-bearing boundary logic for the whole feature.

- [ ] **Step 4: Commit**

```bash
git add src/db.py test_guild_duel_period.py
git commit -m "feat: add pure guild_duel_period_key with boundary tests"
```

---

### Task 2: Gated `get_guild_duel_period()` + wire `save_guild_duel()`

**Files:**
- Modify: `src/db.py`

**Interfaces:**
- Consumes: `guild_duel_period_key(now)` from Task 1, `_utcnow()` (existing), `_connect()` (existing).
- Produces: `get_guild_duel_period() -> str | None` — returns the period to scan, or `None` if it's already been scanned (any day this week) and should be skipped. Replaces `current_guild_duel_period()` (which is removed in this task, its two remaining references being `guild_duel.py`'s `_reentry` docstring — no code reference — and `save_guild_duel`, both fixed here or in Task 3).

- [ ] **Step 1: Add `get_guild_duel_period()` and update `save_guild_duel()`**

Replace the existing `current_guild_duel_period()` function entirely with:

```python
def get_guild_duel_period() -> str | None:
    """Returns the period_start to scan for, or None to skip.

    Battle Report always shows the just-finished week's totals, stable for
    the entire following week (confirmed live, no rollover lag at Monday
    00:00 UTC) · so there's no narrow scan window like Supreme Arena's
    Mon/Tue gate. The only gate is 'has this period already been saved' ·
    a missed or failed run safely retries on every later scrape this week
    until it succeeds, then goes quiet once rows exist."""
    period = guild_duel_period_key(_utcnow())
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM guild_duel_rankings WHERE period_start = ? LIMIT 1",
            (period,)
        ).fetchone()
    if row:
        return None
    return period
```

Update `save_guild_duel()` (currently at approximately line 906-914) from:

```python
def save_guild_duel(entries: list[dict]) -> int:
    period = current_guild_duel_period()
    scanned_at = _utcnow().isoformat()
    with _connect() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO guild_duel_rankings
               (member_id, period_start, rank, crests, scanned_at) VALUES (?, ?, ?, ?, ?)""",
            [(e["member_id"], period, e.get("rank"), e.get("crests"), scanned_at) for e in entries])
    return len(entries)
```

to:

```python
def save_guild_duel(entries: list[dict], period: str) -> int:
    scanned_at = _utcnow().isoformat()
    with _connect() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO guild_duel_rankings
               (member_id, period_start, rank, crests, scanned_at) VALUES (?, ?, ?, ?, ?)""",
            [(e["member_id"], period, e.get("rank"), e.get("crests"), scanned_at) for e in entries])
    return len(entries)
```

`period` is now a required parameter (passed in by the caller, which already computed it via the gate check) rather than recomputed inside `save_guild_duel` — this avoids a second, separately-timed call to `guild_duel_period_key(_utcnow())` racing across a UTC-midnight boundary between the gate check and the save.

- [ ] **Step 2: Verify no other callers of `current_guild_duel_period()` remain**

Run: `cd c:\vscode\AFKDataMining && grep -rn "current_guild_duel_period" src/`

Expected: no matches (Task 3 updates `guild_duel.py`'s only reference, but confirm here first — if `guild_duel.py` still references it, that's expected until Task 3 runs; note it and continue).

- [ ] **Step 3: Commit**

```bash
git add src/db.py
git commit -m "feat: gate guild_duel scan on weekly period, replacing daily period key"
```

---

### Task 3: Battle Report navigation

**Files:**
- Modify: `src/nav.py`

**Interfaces:**
- Consumes: `navigate_to_mode` (existing, in this file), `_ocr_tap_text` (existing, in this file), `_wait_until_stable` (existing, in this file).
- Produces: `navigate_to_guild_duel_battle_report(flow_attempts: int = 3) -> None` — raises `RuntimeError` on failure (same convention as `navigate_to_mode`), returns normally once the Battle Report popup is open and settled.

- [ ] **Step 1: Add the navigation function**

Add to `src/nav.py`, after `navigate_to_clashfronts_signups` (around line 498, before `apply_guild_filter`):

```python
def navigate_to_guild_duel_battle_report(flow_attempts: int = 3) -> None:
    """Navigate from any screen to the Guild Duel 'Battle Report' popup.

    Route: navigate_to_mode('guild_duel') (existing path -> lands on the
    'Elite Duelers' tab) -> OCR-tap 'Battle Report' (top-right button, found
    by text since button art may vary by season like the mode cards do).
    Unlike navigate_to_mode, there is no rankings_btn/afk_stages_trophy tap
    here -- Guild Duel's ranking data now lives behind this button instead,
    reached from the same mode-card landing screen."""
    from ocr import ocr_image
    logging.info("Navigating to Guild Duel Battle Report.")

    for attempt in range(1, flow_attempts + 1):
        try:
            navigate_to_mode("guild_duel")
        except RuntimeError:
            logging.warning("guild_duel mode screen not reached (flow attempt %d/%d).",
                             attempt, flow_attempts)
            continue

        if not _ocr_tap_text(ocr_image, "Battle Report", "guild_duel:battle_report", attempts=4):
            logging.warning("'Battle Report' button not found (flow attempt %d), re-homing.", attempt)
            continue
        _wait_until_stable()

        logging.info("Arrived at Guild Duel Battle Report.")
        return

    cv2.imwrite(str(TEMPLATES_DIR.parent / "debug_nav_fail_guild_duel_battle_report.png"), screenshot())
    raise RuntimeError(f"Could not reach Guild Duel Battle Report after {flow_attempts} attempts.")
```

- [ ] **Step 2: Verify live**

Run: `cd c:\vscode\AFKDataMining && venv/Scripts/python.exe -c "import sys; sys.path.insert(0, 'src'); from nav import navigate_to_guild_duel_battle_report; navigate_to_guild_duel_battle_report(); print('OK')"`

With the game open in BlueStacks (any starting screen). Expected: prints `OK` with no traceback, and the Battle Report Victory-style popup is visible on the device. If it fails, check `src/debug_nav_fail_guild_duel_battle_report.png` (written on failure) to see what screen it got stuck on.

- [ ] **Step 3: Commit**

```bash
git add src/nav.py
git commit -m "feat: add navigation to Guild Duel Battle Report screen"
```

---

### Task 4: Parsing tuned for the Battle Report card + Battle-Report-specific scroll

**Files:**
- Modify: `src/modes/guild_duel.py`
- Modify: `src/device.py` (new scroll function)

**Interfaces:**
- Consumes: `parse_rank_rows` (`modes/common.py`, existing), `scroll_scan` (`modes/common.py`, existing), `resolve_entries` (`modes/common.py`, existing), `navigate_to_guild_duel_battle_report` (Task 3), `get_guild_duel_period` / `save_guild_duel` (Task 2), `swipe` (`device.py`, existing).
- Produces: `guild_duel_scroll_down() -> None` in `device.py`. Rewritten `_parse`, `scan` in `guild_duel.py`.

- [ ] **Step 1: Add a Battle-Report-specific scroll function to `src/device.py`**

Add directly after the existing `scroll_down()` (around line 174):

```python
def guild_duel_scroll_down() -> None:
    """Scroll increment for the Guild Duel Battle Report popup card, whose
    visible list area is shorter than the full Rankings screen scroll_down()
    was tuned for -- that swipe overshoots this smaller viewport and skips
    rows. Starting point: half the distance and duration of scroll_down()'s
    620px/2200ms, for roughly double the stops over the same content. Tune
    on-device against the real card (measure row pitch, confirm via voted
    entry counts) before relying on this for a live scan."""
    swipe(540, 1300, 540, 990, 1100)
```

- [ ] **Step 2: Rewrite `src/modes/guild_duel.py`**

Replace the full file contents:

```python
"""Guild Duel Battle Report scan · rank + crests, read once a week from the
previous week's finalized results. This screen mixes both guilds in the same
Mon-Fri contest (no in-game filter available, same as the old daily Rankings
screen), so each row carries its own guild tag under the member name ("RKF
RiffRaff") that must be checked per row -- rows tagged for the other guild
are dropped before name resolution, not left for resolve_names() to sort
out. The viewer's own row is sticky (pinned at a fixed position regardless
of scroll), which occludes whatever real row would be there -- confirmed
unavoidable even scrolling by hand, so that one member's row is accepted as
unrecoverable rather than engineered around."""
from difflib import SequenceMatcher

from db import init_db, get_guild_duel_period, save_guild_duel
from device import guild_duel_scroll_down
from nav import navigate_to_guild_duel_battle_report
from modes.common import parse_rank_rows, scroll_scan, resolve_entries

GUILD_TAG = "rkf riffraff"
GUILD_TAG_THRESHOLD = 0.75

# Reward banner (Victory/Tier Rewards icons and their numeric values, e.g.
# 1200/500/600/250/6) sits above the member list, roughly y700-850 on a
# 1080x1920 capture -- excluded so those numbers are never read as row data.
LIST_Y_MIN = 850


def _is_our_guild(texts: list[str]) -> bool:
    """Lenient tag check: substring match, or fuzzy@0.75 against GUILD_TAG,
    tolerating OCR noise on the (small, gray) tag text without dropping a real
    member. Checks every extra text block on the row, since the tag may land
    split across blocks or alongside other row text."""
    for t in texts:
        low = t.lower().strip()
        if GUILD_TAG in low or SequenceMatcher(None, low, GUILD_TAG).ratio() >= GUILD_TAG_THRESHOLD:
            return True
    return False


def _parse(img, ocr_results) -> list[dict]:
    return [
        {"name": row.name, "rank": row.rank,
         "crests": row.numbers[0] if row.numbers else None}
        for row in parse_rank_rows(img, ocr_results, list_y_min=LIST_Y_MIN)
        if _is_our_guild(row.texts)
    ]


def scan() -> None:
    init_db()
    period = get_guild_duel_period()
    if period is None:
        print("GUILD_DUEL: skipping (this week's period already scanned).")
        return

    navigate_to_guild_duel_battle_report()
    entries = scroll_scan(_parse, "Guild Duel", scroll_fn=guild_duel_scroll_down)
    rows = resolve_entries(entries, "Guild Duel")
    saved = save_guild_duel(rows, period)
    print(f"GUILD_DUEL: saved {saved} entries for period {period}.")
```

Note: `scroll_scan()` in `modes/common.py` does not currently accept a `scroll_fn` parameter -- it calls the module-level `scroll_down` imported from `device`. Step 3 adds that parameter.

- [ ] **Step 3: Add `scroll_fn` parameter to `scroll_scan()` in `src/modes/common.py`**

Find the `scroll_scan` signature (around line 397):

```python
def scroll_scan(parse_fn, label: str, max_scrolls: int = MAX_SCROLLS,
                reentry_fn=None, frames_per_stop: int = 2) -> list[dict]:
```

Change to:

```python
def scroll_scan(parse_fn, label: str, max_scrolls: int = MAX_SCROLLS,
                reentry_fn=None, frames_per_stop: int = 2, scroll_fn=None) -> list[dict]:
```

Update the docstring's final line (`reentry_fn: ...`) by adding directly after it:

```python
    scroll_fn: overrides the default device.scroll_down() swipe -- used by
    screens whose visible list area doesn't match the full Rankings screen
    the default swipe was tuned for (e.g. Guild Duel's Battle Report popup).
```

Find the scroll call inside `_one_pass()` (around line 438):

```python
            scroll_down()
```

Change to:

```python
            (scroll_fn or scroll_down)()
```

- [ ] **Step 4: Verify live**

With the game open on the Battle Report popup (or any screen -- `scan()` navigates itself):

Run: `cd c:\vscode\AFKDataMining && venv/Scripts/python.exe -c "
import sys; sys.path.insert(0, 'src')
import logging; logging.basicConfig(level=logging.INFO)
from modes.guild_duel import scan
scan()
"`

Expected: log lines showing the navigation reaching Battle Report, `Guild Duel: stop ·` lines as it scrolls, ending with `GUILD_DUEL: saved N entries for period 2026-07-06.` (or `2026-07-13` etc. depending on the day this is actually run). Watch the device: confirm the reward-banner numbers (1200, 500, 600, 250, 6) do NOT appear as `crests` values in the `Guild Duel: matched` log line printed by `resolve_entries` -- if they do, `LIST_Y_MIN` needs to be raised.

Then check the row count and a couple of values against what's visible on screen:

Run: `cd c:\vscode\AFKDataMining && venv/Scripts/python.exe -c "
import sys; sys.path.insert(0, 'src')
from db import _connect
with _connect() as conn:
    for r in conn.execute('SELECT period_start, rank, crests FROM guild_duel_rankings ORDER BY period_start DESC, crests DESC LIMIT 10'):
        print(dict(r))
"`

Expected: rows with sane crest values matching what's visible in the Battle Report card (e.g. Ekkateryna 6650, Jinbei 6310 from the earlier live capture, if this period matches).

- [ ] **Step 5: Commit**

```bash
git add src/modes/guild_duel.py src/modes/common.py src/device.py
git commit -m "feat: scan Guild Duel Battle Report instead of daily rankings"
```

---

### Task 5: Wire `--guild-duel` no-op-safe daily invocation (verify only, likely no code change)

**Files:**
- Read-only check: `src/scraper.py`

**Interfaces:**
- Consumes: `modes.guild_duel.scan()` (Task 4) via `importlib.import_module(f"modes.{mode}").scan()`.

- [ ] **Step 1: Confirm `scraper.py` needs no changes**

Read `src/scraper.py` lines 142-161 (`MODES` tuple and `run_modes`). Confirm `"guild_duel"` is still listed in `MODES` and `run_modes` still calls `.scan()` generically via `importlib` with a broad `except Exception` around each mode. Since `guild_duel.scan()` (Task 4) now returns immediately after printing a skip line when `get_guild_duel_period()` is `None`, no change is needed here -- the existing daily invocation becomes a fast no-op on 6 of 7 days.

- [ ] **Step 2: Run the full CLI flag once to confirm end-to-end wiring**

Run: `cd c:\vscode\AFKDataMining && venv/Scripts/python.exe src/scraper.py --guild-duel`

Expected: either `GUILD_DUEL: skipping (this week's period already scanned).` (if Task 4's manual run already saved this week) or a full scan + `GUILD_DUEL: saved N entries for period ...` line, with no `MODE_FAILED: guild_duel` line.

No commit -- this task makes no code changes, only confirms Task 4's work is correctly wired into the existing CLI.

---

### Task 6: Migration -- clear and re-seed the current week's data

**Files:**
- None (direct DB operation + one scan run).

- [ ] **Step 1: Check current row count for the period being replaced**

Run: `cd c:\vscode\AFKDataMining && venv/Scripts/python.exe -c "
import sys; sys.path.insert(0, 'src')
from db import _connect
with _connect() as conn:
    n = conn.execute(\"SELECT COUNT(*) c FROM guild_duel_rankings WHERE period_start = '2026-07-06'\").fetchone()['c']
    print(f'{n} existing rows for period 2026-07-06')
"`

Note the count for comparison after re-seeding.

- [ ] **Step 2: Delete the daily-scan data for the current period**

Run: `cd c:\vscode\AFKDataMining && venv/Scripts/python.exe -c "
import sys; sys.path.insert(0, 'src')
from db import _connect
with _connect() as conn:
    cur = conn.execute(\"DELETE FROM guild_duel_rankings WHERE period_start = '2026-07-06'\")
    conn.commit()
    print(f'Deleted {cur.rowcount} rows.')
"`

Expected: `Deleted N rows.` where N matches (or is close to) Step 1's count.

- [ ] **Step 3: Re-seed via a manual scan run**

Run: `cd c:\vscode\AFKDataMining && venv/Scripts/python.exe src/scraper.py --guild-duel`

With the game open and BlueStacks running. Expected: `GUILD_DUEL: saved N entries for period 2026-07-06.` (period must read `2026-07-06` -- if it reads a different date, stop and check `guild_duel_period_key`/`get_guild_duel_period` before proceeding further, since that would mean the gate computed the wrong week).

- [ ] **Step 4: Verify the re-seeded data looks sane**

Run: `cd c:\vscode\AFKDataMining && venv/Scripts/python.exe -c "
import sys; sys.path.insert(0, 'src')
from db import _connect
with _connect() as conn:
    n = conn.execute(\"SELECT COUNT(*) c FROM guild_duel_rankings WHERE period_start = '2026-07-06'\").fetchone()['c']
    print(f'{n} rows for period 2026-07-06')
    for r in conn.execute(\"SELECT rank, crests FROM guild_duel_rankings WHERE period_start = '2026-07-06' ORDER BY crests DESC LIMIT 5\"):
        print(dict(r))
"`

Expected: a row count in the range of active RiffRaff Guild Duel participants (roughly 20-50, based on the live capture showing ranks into the 20s+ before the sticky-row occlusion point), and the top crest values matching what's visible on the live Battle Report screen (e.g. Ekkateryna ~6650, Jinbei ~6310 if unchanged since the earlier capture).

No commit -- this is a one-off data operation, not a code change. Note in the next session/handoff that this migration ran and its result (row count) so it isn't repeated.

---

### Task 7: Documentation

**Files:**
- Modify: `CLAUDE.md` (Guild Duel section)

- [ ] **Step 1: Replace the Guild Duel section in `CLAUDE.md`**

Find the `## Guild Duel (weekly crest tracking)` section. Add a new subsection at the end of it (after the existing "Found and fixed a pre-existing name-truncation bug..." paragraph, before the next `##` heading), documenting the redesign:

```markdown
### Weekly Battle Report scan (replaces daily scan)

The scan no longer reads the live daily "Guild Duel Total Crest Rankings"
screen. It now reads the "Battle Report" screen (Elite Duelers tab -> tap
"Battle Report", top-right) once a week, which shows the previous week's
FINALIZED per-member crest totals -- confirmed live to stay stable and
correct for the entire following week (rolls over exactly at Monday 00:00
UTC, no lag).

- `get_guild_duel_period()` (`db.py`) gates the scan: computes the period via
  the pure `guild_duel_period_key(now)` (previous Monday relative to `now`,
  matching the existing `period_start` convention of keying to the week
  being measured, not the day scanned) and returns `None` if that period
  already has rows -- so the daily `--guild-duel` CLI invocation becomes a
  fast no-op 6 days out of 7, and a missed/failed Monday run self-heals by
  retrying on the next day's scrape until it succeeds.
- `navigate_to_guild_duel_battle_report()` (`nav.py`) reuses the existing
  `navigate_to_mode("guild_duel")` path (lands on Elite Duelers) then
  OCR-taps "Battle Report" (text-matched, not template-matched, same
  reasoning as Clashfronts' "ManageSign" button -- art may vary by season).
- The Battle Report card has a reward-icon banner above the list (numeric
  values like 1200/500/600/250/6) that `list_y_min=850` excludes from being
  read as row data, and no hero/podium section (unlike Dream Realm/Arena).
- Uses its own `guild_duel_scroll_down()` (`device.py`) instead of the
  shared `scroll_down()` -- the Battle Report popup's visible list area is
  shorter than the full Rankings screen the default swipe was tuned for.
- **Known accepted gap**: the viewer's own row is sticky (pinned at a fixed
  position regardless of scroll position), occluding whatever real member
  row would otherwise be there in every sampled frame. Confirmed unavoidable
  even scrolling manually by hand (the row drops back into place the instant
  the scroll gesture ends) -- accepted as an unrecoverable limitation, not
  engineered around with a completeness check.
- Migration: the daily-scan data for the transition week (period_start
  2026-07-06) was deleted and re-seeded from a real Battle Report scan
  rather than left mixed with daily partial reads.
```

- [ ] **Step 2: Update mempalace**

Use `mempalace_add_drawer` (wing `afkdatamining`, room `decisions`) to record that the Guild Duel weekly Battle Report redesign shipped, referencing this plan file and the migration result (row count) from Task 6 Step 4. Mark the existing `pending` room entry (filed during brainstorming, `drawer_afkdatamining_pending_5514cb400f1046188c8c37db`) as resolved/superseded if the mempalace tooling supports updating/removing drawers, or add a new `decisions` entry noting it's done.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document Guild Duel weekly Battle Report scan"
```

---

## Self-Review Notes

**Spec coverage:** Navigation (Task 3), parsing/list_y_min/scroll (Task 4), period key + gate (Tasks 1-2), scraper wiring (Task 5, verify-only per spec's "no changes" call), migration (Task 6), docs (Task 7, per Global Constraints' "done means docs updated") -- all spec sections have a task.

**Placeholder scan:** No TODOs. The two "tune on-device" notes (`LIST_Y_MIN = 850`, `guild_duel_scroll_down`'s swipe distance) are starting values with explicit live-verification steps (Task 4 Steps 4) that direct the implementer to adjust them if wrong, not unresolved placeholders -- this matches the codebase's own established pattern (e.g. `NAME_X_MIN = 130` in `parser.py`, `scroll_down()`'s swipe constants) of shipping a reasoned starting constant plus a live-tune loop rather than blocking on impossible-to-know-in-advance pixel values.

**Type consistency:** `save_guild_duel(entries, period)` signature change (Task 2) is reflected in its only call site update (Task 4's `scan()`). `scroll_scan(..., scroll_fn=None)` (Task 4 Step 3) matches its usage in Task 4 Step 2 (`scroll_fn=guild_duel_scroll_down`). `guild_duel_period_key(now)` (Task 1) is consumed by `get_guild_duel_period()` (Task 2) with matching signature (`_utcnow()` passed in).
