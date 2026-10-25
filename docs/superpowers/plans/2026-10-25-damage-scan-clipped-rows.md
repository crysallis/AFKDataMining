# Damage scan: clipped rows, settled reads, duplicate recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the Guild Duel Damage scan from reading partly hidden rows and moving screens, keep the screenshot behind any unmatched read, then recover duplicate misreads by damage value and read Cyrillic names in Cyrillic.

**Architecture:** Three small pure modules next to the mode (`damage_clip`, `damage_dedupe`, `damage_cyrillic`) plus a frame keeper, wired into `modes/guild_duel_damage.py`. One opt-in argument on the shared `scroll_scan` and one character range in `db.py` are the only shared-code changes. Built in three stages with a live-scan checkpoint after stage 1.

**Tech Stack:** Python 3.14 in `venv`, OpenCV, NumPy, RapidOCR (PP-OCRv5), SQLite. Plain-script tests (`check(desc, actual, expected)`), no pytest.

**Spec:** `docs/superpowers/specs/2026-10-25-damage-scan-clipped-rows-design.md` (read it first; all measured numbers below come from it).

## Global Constraints

- Work in `C:\vscode\AFKDataMining`, commit directly to `main`, never `--no-verify`.
- Run tests with `venv/Scripts/python.exe test_<name>.py` from the repo root. A test file ends with `sys.exit(1)` on any failure; gate every commit on the exit code itself (`python test_x.py && git commit ...`), never on a grep of the output.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` after a blank line.
- No em dashes anywhere (use `·`, `-` or `...`). No unnecessary comments in code.
- Tests that touch the database use a temp file via `db.DB_PATH`, never the real `guild.db` (see `test_guild_duel_damage.py`). Clean up by exact id, never by date range.
- Every existing caller of `scroll_scan` must behave exactly as before (new argument defaults off).
- Member names appear on the screenshots: nothing under `ocr_lab/data/` or `debug_unmatched/` is ever committed.
- Geometry (1080x1920): list window y 723 to 1733; own-row card is one dark run 150 to 175 px tall in strip x 420 to 760 with median brightness under 195; row card = `[dy - 104, dy + 58]` around the damage centre `dy`; tolerance about 6 px.

## Review Focus

- Own-row card not found on a frame: edge check still applies, warning printed once, scan continues (Task 2).
- A row exactly on the window edge or touching the own card within tolerance (Task 2).
- Rank 1 and rows above the viewer's rank are kept while the bar is at the bottom or absent (Task 2).
- Settle never stabilises: cap hit, read anyway, warning, no hang (Task 4).
- Two same-value members both similar to an unmatched read: no merge; a similar name with a different value: no merge (Task 7).
- Cyrillic pass leaves Latin, CJK and empty results untouched (Task 8).

---

### Task 1: Housekeeping, commit the lab and the ignores

**Files:**
- Modify: `.gitignore` (already has `ocr_lab/data/` and `debug_unmatched/` appended)
- Add: `ocr_lab/capture.py`, `ocr_lab/lab.py`, `ocr_lab/jitter.py`, `ocr_lab/swipe_test.py`

- [ ] **Step 1: Verify the ignores work.** Run `git check-ignore -v ocr_lab/data/frames/frame_00.png debug_unmatched/x.png`. Expected: both reported as ignored.
- [ ] **Step 2: Commit.** `git add .gitignore ocr_lab/*.py` then commit `chore: add OCR lab scripts and ignore private frames`. Confirm with `git status --short` that no image is staged.

### Task 2: Row filter module

**Files:**
- Create: `src/modes/damage_clip.py`
- Test: `test_damage_clip.py`

**Interfaces:**
- Produces:
  - `WINDOW = (723, 1733)`, `EDGE_TOL = 6`
  - `find_own_card(img: np.ndarray) -> tuple[int, int] | None` (top, bottom y of the own-row card, `None` if not exactly one plausible run)
  - `row_card(damage_y: int) -> tuple[int, int]` (`(dy - 104, dy + 58)`)
  - `is_clear(card: tuple[int, int], own: tuple[int, int] | None, window: tuple[int, int] = WINDOW, tol: int = EDGE_TOL) -> bool`
  - `filter_clear_rows(img, rows: list[RankRow]) -> tuple[list[RankRow], int]` (kept rows, count dropped; `row.y` is the damage centre)

- [ ] **Step 1: Write the failing tests** in `test_damage_clip.py` (plain-script, `check()` helper copied from `test_guild_duel_damage.py`). Build 1920x1080 BGR images: light background `(234, 241, 245)`, own-row card painted as a dark band `(131, 140, 161)` over x 20..1060. Cases:
  - `find_own_card` returns `(723, 884)` for a band at 723-884, `(1572, 1733)` at 1572-1733, `(900, 1062)` at 900-1062, within 1 px; `None` for an image with no band; `None` for a 60 px band (too short); `None` for two plausible bands.
  - `is_clear((726, 888), None)` is True; `is_clear((1700 - 104, 1700 + 58), None)` is False (bottom edge); `is_clear((696, 858), (723, 884))` is False (top edge); a card overlapping the own band is False; a card starting at the own band's bottom (`(885, 1047)` with own `(723, 884)`) is True; a card equal to the own card within tolerance is True.
  - `filter_clear_rows` with rows at `y = 827, 1013, 1199` and a middle own band at 900-1062: the row at 1013 (the own card itself) is kept, rows at 827 and 1199 kept, none dropped; with a top-pinned band and a row at `y = 928` (name hidden under it), that row is dropped and the count is 1.
  - No own card found and a row at `y = 1700`: dropped by the edge rule, others kept (fails open).
- [ ] **Step 2: Run to verify failure.** `venv/Scripts/python.exe test_damage_clip.py` Expected: ImportError / FAIL.
- [ ] **Step 3: Implement `src/modes/damage_clip.py`.** `find_own_card` takes the per-row median brightness over x 420 to 760 for y 690 to 1760, groups rows under 195 into runs, keeps runs 150 to 175 px tall, returns the run only if exactly one. `is_clear` per the spec rule: inside the window within tolerance, and either overlapping the own card by no more than tolerance or equal to it. `filter_clear_rows` calls `find_own_card` once per frame.
- [ ] **Step 4: Run to verify pass.** Same command, Expected: `All N cases passed.`
- [ ] **Step 5: Commit** `feat: row filter for clipped damage-scan rows`.

### Task 3: Wire the filter into the damage mode, counter, replay

**Files:**
- Modify: `src/modes/guild_duel_damage.py` (`_parse`, `scan`)
- Modify: `test_damage_clip.py`
- Create: `ocr_lab/replay.py`

**Interfaces:**
- Consumes: `filter_clear_rows` (Task 2).
- Produces: module-level `clip_stats: dict` with key `"dropped"`; `_parse(img, ocr_results) -> list[dict]` now drops clipped rows.

- [ ] **Step 1: Write the failing test** in `test_damage_clip.py`: synthetic image with a top-pinned own band at 723-884; hand-built OCR blocks (box = 4 points around a centre) for a clear row (name at `(400, dy-41)`, damage `'207B'` at `(945, dy)`, tag `'RKF RiffRaff'` at `(450, dy+16)`) with `dy = 1100`, and a clipped row (`dy = 928`, name `'Tarehiiei'`). Assert `_parse(img, blocks)` returns only the clear row, and `clip_stats["dropped"]` is 1 after a reset.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.** `_parse` pairs rows with `rows_from_blocks`, filters with `filter_clear_rows(img, rows)`, adds the dropped count to `clip_stats`, then `rows_to_entries`. `scan` resets `clip_stats` at the start and prints `GUILD_DUEL_DAMAGE: skipped N rows hidden by the pinned bar or list edge.` after the scroll.
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Create `ocr_lab/replay.py`** (args `--data <dir>`): for each `frame_*.png` print entries from `rows_to_entries(rows_from_blocks(ocr))` versus `_parse(img, ocr)`, marking dropped rows. Run it on `ocr_lab/data`. Expected: `frame_06` drops the `Tare_Ameli` read (`Tarehiiei`), keeps `Matikhv`, `Eudemonios`, `Hira`; `frame_07` keeps `Jinbei`, `Caernafon`, `Crysallis`; `frame_08` keeps `Wrex`, `Crysallis`, and drops nothing that is clear.
- [ ] **Step 6: Commit** `feat: damage mode drops rows hidden by the bar or list edge`.

### Task 4: Settle option on `scroll_scan`

**Files:**
- Modify: `src/modes/common.py` (`scroll_scan`, new `wait_until_settled`)
- Modify: `src/device.py` (`guild_duel_damage_scroll_down` reads a named constant)
- Test: `test_scroll_settle.py`

**Interfaces:**
- Produces:
  - `wait_until_settled(region: tuple[slice, slice], threshold: float = 0.5, interval: float = 0.3, cap: float = 6.0, screenshot_fn=screenshot, sleep_fn=time.sleep, clock_fn=time.monotonic) -> tuple[np.ndarray, bool]` (returns the last screenshot and whether it settled)
  - `scroll_scan(..., settle_region: tuple[slice, slice] | None = None)` (None = old behavior)
  - `device.GUILD_DUEL_DAMAGE_SWIPE = (540, 1450, 540, 890, 2000)`

- [ ] **Step 1: Write the failing tests** in `test_scroll_settle.py` with fake screenshot and clock functions: settles after 3 differing frames then 2 identical ones (returns `(img, True)` after 5 calls); never stabilises (frames always differ) returns `(last, False)` once the fake clock passes the cap and makes no more than a bounded number of calls; identical first two frames settle immediately. Region slicing: a difference outside the region does not count. `scroll_scan` with `settle_region=None` (monkeypatch `common.screenshot`, `common.ocr_image`, `common.time.sleep`, and make `common.wait_until_settled` raise): runs one stop, records a `sleep(1.2)`, never calls the settle function. With a region (settle monkeypatched to return an image): no `sleep(1.2)`, exactly one settle call per scroll. `device.GUILD_DUEL_DAMAGE_SWIPE == (540, 1450, 540, 890, 2000)`.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.** In `scroll_scan`, after the swipe and `scrolls += 1`: if `settle_region` is None keep `time.sleep(1.2); curr_img = screenshot()`; otherwise `curr_img, ok = wait_until_settled(settle_region)` and `logging.warning` when not `ok` (read anyway). The `screen_changed(last_img, curr_img)` end-of-list check and its 1.5 threshold are untouched. Difference measure: mean absolute difference over the region, as `_img_diff` does.
- [ ] **Step 4: Run to verify pass**, plus `venv/Scripts/python.exe test_guild_duel_damage.py`.
- [ ] **Step 5: Commit** `feat: opt-in settle wait in scroll_scan`.

### Task 5: Frame keeper and unmatched-line file names

**Files:**
- Create: `src/modes/damage_frames.py`
- Modify: `src/modes/guild_duel_damage.py` (`_parse`, `unmatched_lines`, `scan`)
- Test: `test_damage_frames.py`

**Interfaces:**
- Produces: `class FrameKeeper(out_dir: Path = Path("debug_unmatched"), cap: int = 20)` with `remember(img, name: str, damage: int) -> None` (ignores calls past the cap), `write_for(unmatched: list[tuple[str, int]]) -> dict[tuple[str, int], str]` (writes PNGs named `<UTC timestamp>_<damage via format_damage>_<n>.png`, returns the relative path per `(name, damage)`, prunes files older than 14 days). `unmatched_lines(entries, rows, frames: dict | None = None)` appends ` · frame <path>` when a file exists for that entry.

- [ ] **Step 1: Write the failing tests** in a temp dir: cap respected (21st `remember` ignored); `write_for` writes a readable PNG, the file name contains no part of the member name (use a name with `/` and Cyrillic), unknown pairs are skipped, files older than 14 days are removed; `unmatched_lines` output unchanged when `frames` is `None` and has the suffix when given.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.** In `_parse`, a module-level keeper and a `RosterResolver(learn=False)` built once per scan remember the frame for each kept entry whose name does not resolve. `scan` writes frames for the final unmatched names and passes the mapping to `unmatched_lines`.
- [ ] **Step 4: Run to verify pass** (also `test_guild_duel_damage.py`, which covers `unmatched_lines`).
- [ ] **Step 5: Commit** `feat: keep the screenshot behind unmatched damage reads`.

### Task 6: Wire stage 1 into `scan` and the live checkpoint

**Files:**
- Modify: `src/modes/guild_duel_damage.py` (`scan`)
- Modify: `src/db.py` (new `count_guild_duel_damage(period: str) -> int`)
- Test: `test_guild_duel_damage.py`

**Interfaces:**
- Produces: `count_guild_duel_damage(period)` counts `guild_duel_rankings` rows with `damage IS NOT NULL` for the period.

- [ ] **Step 1: Failing test** for `count_guild_duel_damage` against the temp DB (0 before, 2 after saving two damage entries, a crests-only row not counted).
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.** `scan` calls `scroll_scan(..., frames_per_stop=1, settle_region=(slice(723, 1733), slice(0, 1080)), scroll_fn=guild_duel_damage_scroll_down, ...)`, reads the previous count before saving, and prints `GUILD_DUEL_DAMAGE: warning, saved N members, previous scan saved M.` when `N < M` (never fails the scan). Just before saving it re-checks `guild_duel_damage_window_open(_utcnow())`; if the window closed during the scan (a scan that crossed Monday 00:00 UTC read the reset screen), it prints `GUILD_DUEL_DAMAGE: window closed during the scan, not saving.` and returns `"skipped"` without writing anything.
- [ ] **Step 4: Run all four test files**; all must exit 0.
- [ ] **Step 5: Commit** `feat: damage scan reads a settled screen and warns on fewer saved members`.
- [ ] **Step 6: CHECKPOINT (needs the user).** Ask the user to run a full damage scan (BlueStacks on the rankings, window open). Expected: no `TareAimei`-style fragments, matched count not below 69, the skipped-rows line printed, the warning line absent, added time about 2.5 minutes. Paste the output; record what is still unmatched and any single-read errors. If single-read errors remain, shorten `GUILD_DUEL_DAMAGE_SWIPE` (for example 450 px) and rerun. Do not start Task 7 until the user has seen the result.

### Task 7: Value tiebreaker

**Files:**
- Create: `src/modes/damage_dedupe.py`
- Modify: `src/modes/guild_duel_damage.py` (`scan`)
- Test: `test_damage_dedupe.py`

**Interfaces:**
- Produces:
  - `fold_name(s: str) -> str`
  - `name_similarity(read: str, canonical: str) -> float` (best `SequenceMatcher` ratio over the folded read, its space/bracket tokens, and each with 1 or 2 leading characters removed)
  - `find_duplicate_reads(entries: list[dict], resolved: dict[str, int], canonical: dict[int, str], floor: float = 0.7, margin: float = 0.15) -> list[tuple[dict, str, float]]`
  - `drop_duplicate_reads(entries, resolve_fn=resolve_names, names_fn=names_for_ids) -> tuple[list[dict], list[str]]` (kept entries, printable `GUILD_DUEL_DAMAGE: merged 'X' into 'Y' (same 311B)` lines)

- [ ] **Step 1: Write the failing tests** with fake resolver and names functions. Folding: lower-case, strip accents, a three-character `M` look-alike (`i`, `l`, `|` or `1`, then `v`, then `i`, `l`, `|` or `1`, as in `IvI`) to `m`, Cyrillic н to h, d to b, drop non-letters, do NOT fold l or 1 to i on their own. Merges: `UTNaizab` and `INaizaD` (311B) into `NaizaB`; `TareAimei` (202B) into `Tare_Ameli`; `EoccMpa3eñ` (244B) into `БоссМразей`; `IvICSnaKe` (19B, scored 0.71 without the `IvI` rule and 0.86 with it) into `McShake`. Stay unmatched: `rarehiiici`; `ψ Kamz/ok` and `φJ Kailiz/ ok` with `Araleth`, `XIII'th`, `Ramz78k` all at 166B (assert `φJ Kailiz/ ok` is NOT merged into `XIII'th`); a similar name with a different damage value; two same-value members both within 0.15 of each other and above 0.7 (no merge); no member changes (the function returns data only).
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.** Pure functions; the merged lines carry the `GUILD_DUEL_DAMAGE:` prefix so `/scan` forwards them. `scan` calls `drop_duplicate_reads` BEFORE `resolve_entries`, which then runs on the kept entries only (so merged names never reach its `REVIEW_NAMES` line).
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `feat: merge duplicate damage reads by value and name similarity`.

### Task 8: Cyrillic second pass and name-core range

**Files:**
- Modify: `src/ocr.py` (additive: `get_engine_cyrillic()`, `read_cyrillic(crop) -> tuple[str, float]`)
- Create: `src/modes/damage_cyrillic.py`
- Modify: `src/modes/guild_duel_damage.py` (`_parse` calls the pass first)
- Modify: `src/db.py` (`_NAME_CHAR_RE` gains `Ѐ-ӿ`)
- Test: `test_damage_cyrillic.py`

**Interfaces:**
- Produces:
  - `cyrillic_ratio(text: str) -> float` (fraction of letters in U+0400 to U+04FF; 0.0 for no letters)
  - `accept_cyrillic(text: str, conf: float, min_ratio: float = 0.6, min_conf: float = 0.8) -> bool`
  - `reread_blocks(img, ocr_results, name_x_range: tuple[int, int], y_min: int, reader=read_cyrillic) -> list` (same shape as `ocr_results`; replaces the text of a block whose cx is inside `name_x_range`, cy at least `y_min`, and whose crop (box bounds plus 8 px, clamped) re-reads as accepted Cyrillic; `_parse` passes `NAME_X_RANGE` and `LIST_Y_MIN`, which avoids importing the mode module from here)
  - `ocr.read_cyrillic(crop) -> tuple[str, float]` (PP-OCRv5 Cyrillic recognizer, `use_det=False, use_cls=False`; `("", 0.0)` when nothing is read)

- [ ] **Step 1: Write the failing tests.** Pure rules: ratio of `Найхэ` is 1.0, of `Kit` 0.0, of `谢霆锋` 0.0, of an empty string 0.0; accept `("Найхэ", 0.87)` and `("БоссМразей", 0.95)`; reject `("Найхэ", 0.7)`, `("Kit", 1.0)`, `("", 0.0)`. `reread_blocks` with a fake reader: an accepted block is replaced and keeps its box and confidence; Latin, CJK and empty results stay untouched; a block outside the name x-range is never passed to the reader. Name core: `_normalize_core("ψ」Найхэ") == "найхэ"`, `_normalize_core("「ψ」Hira") == "hira"`, `_clean_token("ψ") == ""`; `_normalized_roster({"Kit", "谢霆锋", "Найхэ"})` contains all three cores and `_normalized_match` of `ψ」Найхэ` returns `Найхэ`; every name in a sample Latin and CJK roster still maps to itself.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.** `get_engine_cyrillic` mirrors `get_engine_v5` (lazy, `False` sentinel on failure, Rec `PPOCRV5` `CYRILLIC` `MOBILE`, Det `PPOCRV4`). `reread_blocks` uses the injected reader so tests need no model.
- [ ] **Step 4: Run to verify pass**; then `ocr_lab/replay.py` with the Cyrillic pass on `frame_00` and `frame_03`. Expected: `Найхэ` and `БоссМразей` read, `Kit`, `MonkeyAFK`, `谢霆锋` untouched.
- [ ] **Step 5: Commit** `feat: Cyrillic second pass for damage-scan names`.

### Task 9: Docs, memory, final run

**Files:**
- Modify: `CLAUDE.md`, `ARCHITECTURE.md` (damage scan section)
- Modify: `C:\Users\crysa\.claude\projects\c--vscode-DiscordBotAfkJ\memory\project_ocr-name-misread-investigation.md`

- [ ] **Step 1:** Update the miner `CLAUDE.md` and `ARCHITECTURE.md` damage-scan sections: the row filter, settle option, frame keeper and `debug_unmatched/`, tiebreaker and its thresholds, Cyrillic pass, `GUILD_DUEL_DAMAGE_SWIPE`. The bot repo needs no change (the new lines reuse the `GUILD_DUEL_DAMAGE:` prefix).
- [ ] **Step 2:** Run every test file; all exit 0. Commit `docs: damage scan clipped-row handling`.
- [ ] **Step 3: Final live scan with the user.** Expected: zero unmatched duplicates of the earlier kinds, matched count not below the previous scan, merged lines (if any) look right, Cyrillic members resolve natively, time added within budget. Update the memory note from "deferred" to shipped with the measured results.
