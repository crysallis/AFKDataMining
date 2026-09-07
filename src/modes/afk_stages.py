"""AFK Stages ranking scan · rank + phase progress ("896" or "Apex 14").
Keyed by (season_id, phase) · each season has up to 3 phases.

A phase stops being the current one once the next phase starts, but stays
scannable/guild-filterable indefinitely after that (confirmed live) -- its
cards switch from a live 'Phase Progress' number to 'Cleared <date> <time>'
for anyone who finished it. Apex 1000 is the final stage, so a cleared phase
is recorded as that fixed value (the clear date/time itself isn't stored --
only that they cleared it, same as every other phase's numeric progress)."""
import re

from db import init_db, save_afk_stages
from device import screenshot, tap
from nav import (navigate_to_overview, _tap_to_reach, _is_at_battle_modes,
                 MODE_CARD_LABEL, _ocr_tap_text, _wait_until_stable,
                 find_template, TEMPLATES_DIR, apply_guild_filter, _t)
from ocr import ocr_image, block_center
from modes.common import parse_rank_rows, scroll_scan, resolve_entries, make_id_fallback_hook

# Tolerate l/1 OCR confusion the same way _norm_time already does elsewhere
# ("Phase l" misread instead of "Phase 1") -- seen intermittently live.
PHASE_RE = re.compile(r"Phase\s*([123lI])", re.IGNORECASE)
APEX_RE = re.compile(r"Apex\s*\d+", re.IGNORECASE)
CLEARED_RE = re.compile(r"^Cleared$", re.IGNORECASE)
FINAL_APEX = "Apex 1000"
# Every AFK Stages card shows either a live 'Phase Progress' value ('Apex N' or
# a bare stage number) or, once the phase has ended, 'Cleared' · either is the
# real-card signal so a row survives even if its rank badge OCR misses.
PROGRESS_RE = re.compile(r"Apex\s*\d+|^\d[\d,]*$|^Cleared$", re.IGNORECASE)


def _norm_phase_digit(d: str) -> int:
    return 1 if d.lower() in ("l", "i") else int(d)


def _detect_phase(ocr_results) -> int | None:
    for _, text, _ in ocr_results:
        m = PHASE_RE.search(text)
        if m:
            return _norm_phase_digit(m.group(1))
    return None


def _select_phase(phase: int, attempts: int = 6) -> bool:
    """Tap the 'Phase N' tab on the rankings screen. Both tabs are visible at
    once today (only 2 phases exist) so no paging is needed yet; the RIGHT
    arrow (dream_realm_right.png, same icon/position, confirmed live) is wired
    in for when Phase 3 pushes Phase 1 off-screen, mirroring Dream Realm's
    _select_day()."""
    import time
    target = f"phase {phase}"
    for attempt in range(attempts):
        for box, text, _conf in ocr_image(screenshot()):
            norm = re.sub(r"\s+", " ", text.strip().lower())
            norm = re.sub(r"phase\s*[lI]\b", "phase 1", norm)
            if norm == target:
                cx, cy = block_center(box)
                tap(cx, cy)
                _wait_until_stable()
                return True
        pos = find_template(screenshot(), _t("dream_realm_right"), threshold=0.75)
        if pos is None:
            return False
        tap(*pos)
        time.sleep(0.8)
    return False


def _parse(img, ocr_results) -> list[dict]:
    out = []
    for row in parse_rank_rows(img, ocr_results, value_re=PROGRESS_RE):
        progress = None
        for t in row.texts:
            if CLEARED_RE.match(t.strip()):
                progress = FINAL_APEX
                break
            m = APEX_RE.search(t)
            if m:
                progress = re.sub(r"\s+", " ", m.group(0)).title()
                break
        if progress is None and row.numbers:
            progress = str(row.numbers[-1])
        out.append({"name": row.name, "rank": row.rank, "progress": progress})
    return out


def scan() -> None:
    init_db()
    from db import _connect, _active_season_id
    with _connect() as conn:
        season_id = _active_season_id(conn)
    if season_id is None:
        print("AFK_STAGES: no active season in DB, aborting.")
        return "failed"

    # Navigate to AFK Stage main screen (card tap only) to read current phase
    def _to_afk_stage_main() -> bool:
        if not navigate_to_overview():
            return False
        if not _tap_to_reach(
            lambda s: find_template(s, TEMPLATES_DIR / "battle_modes_btn.png", threshold=0.75),
            _is_at_battle_modes, "battle_modes", fallback_xy=None,
        ):
            return False
        if not _ocr_tap_text(ocr_image, MODE_CARD_LABEL["afk_stages"],
                             "card:AFK Stage", scroll_between=True, attempts=5):
            return False
        _wait_until_stable()
        return True

    if not _to_afk_stage_main():
        print("AFK_STAGES: could not reach AFK Stage main screen.")
        return "failed"

    phase = _detect_phase(ocr_image(screenshot()))
    if phase is None:
        print("AFK_STAGES: could not detect phase, aborting.")
        return "failed"
    print(f"AFK_STAGES: season_id={season_id}, phase={phase}")

    # Tap rankings button to enter the ranking list
    rankings_pos = find_template(screenshot(), TEMPLATES_DIR / "rankings_btn.png", threshold=0.75)
    if not rankings_pos:
        print("AFK_STAGES: rankings button not found.")
        return "failed"
    tap(*rankings_pos)
    _wait_until_stable()

    def _scan_current_tab(p: int) -> str:
        """Scans whatever phase tab is currently selected on the rankings
        screen. reentry_fn=None: default frames_per_stop=2 already gives
        per-stop vote redundancy without a second pass. The ID fallback hook
        rides along on this same scroll (see make_id_fallback_hook) instead of
        needing its own re-navigation, so it never risks landing on the wrong
        phase tab the way a separate reentry pass would."""
        if not apply_guild_filter():
            print(f"AFK_STAGES: guild filter failed for phase {p}, skipping.")
            return "failed"
        entries = scroll_scan(_parse, f"AFK Stages phase {p}", reentry_fn=None,
                              on_stop=make_id_fallback_hook(f"AFK Stages phase {p}"))
        rows = resolve_entries(entries, f"AFK Stages phase {p}")
        saved = save_afk_stages(rows, season_id, p)
        print(f"AFK_STAGES: saved {saved} entries for season_id={season_id} phase={p}.")
        return "success"

    # One-time final scan of the phase that just ended: if the CURRENT phase
    # has no rows yet for this season, this is the first scan since it started,
    # so the previous phase (now settled, still fully scannable/filterable)
    # gets one last capture before we stop tracking it. Detecting on the
    # CURRENT phase's absence (not the previous phase's presence) is required --
    # the previous phase's rows never go away, so their presence can't signal
    # "already backfilled". Self-healing: if this dies mid-run, phase stays
    # at 0 rows and retries next scan (INSERT OR REPLACE makes re-saving safe).
    with _connect() as conn:
        current_phase_scanned = conn.execute(
            "SELECT 1 FROM afk_stage_rankings WHERE season = ? AND phase = ? LIMIT 1",
            (season_id, phase),
        ).fetchone() is not None

    if phase > 1 and not current_phase_scanned:
        prev_phase = phase - 1
        print(f"AFK_STAGES: phase {phase} just started, backfilling phase {prev_phase} one last time.")
        if _select_phase(prev_phase):
            _scan_current_tab(prev_phase)
            _select_phase(phase)
        else:
            print(f"AFK_STAGES: could not select phase {prev_phase} tab, skipping backfill.")

    return _scan_current_tab(phase)
