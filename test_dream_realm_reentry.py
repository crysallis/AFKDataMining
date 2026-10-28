"""Dream Realm re-enters the rankings between days.
Run: venv/Scripts/python.exe test_dream_realm_reentry.py
Plain-script convention (no pytest in this repo). All device and OCR calls are faked.
Switching the date tab keeps the previous day's scroll position, so a day scanned
after another one started partway down the list and lost its top ranks.
"""
import sys
sys.path.insert(0, "src")

import modes.dream_realm as dr

failures = 0
total = 0


def check(name, cond):
    global failures, total
    total += 1
    if not cond:
        failures += 1
        print(f"FAIL: {name}")


def patched(**attrs):
    saved = {k: getattr(dr, k) for k in attrs}
    for k, v in attrs.items():
        setattr(dr, k, v)
    return saved


def restore(saved):
    for k, v in saved.items():
        setattr(dr, k, v)


def test_reenter_presses_back_and_taps_rankings():
    log = []
    saved = patched(
        press_back=lambda: log.append("back"),
        _wait_until_stable=lambda *a, **k: log.append("wait"),
        screenshot=lambda: "frame",
        find_template=lambda img, path, threshold=0.8: (500, 140),
        tap=lambda x, y: log.append(("tap", x, y)),
    )
    try:
        result = dr._reenter_rankings()
    finally:
        restore(saved)
    check("reenter returns True when the rankings button is found", result is True)
    check("reenter backs out, then taps the rankings button",
          log[0] == "back" and ("tap", 500, 140) in log and log.index("back") < log.index(("tap", 500, 140)))


def test_reenter_fails_when_rankings_button_missing():
    saved = patched(
        press_back=lambda: None,
        _wait_until_stable=lambda *a, **k: None,
        screenshot=lambda: "frame",
        find_template=lambda img, path, threshold=0.8: None,
        tap=lambda x, y: None,
    )
    try:
        result = dr._reenter_rankings()
    finally:
        restore(saved)
    check("reenter returns False when the rankings button is missing", result is False)


def run_capture(missing, reenter_ok=True, failing_day=None):
    log = []

    def select(day):
        log.append(f"select {day}")
        return True

    def reenter():
        log.append("reenter")
        return reenter_ok

    saved = patched(
        _select_day=select,
        apply_guild_filter=lambda: log.append("filter") or True,
        scroll_scan=lambda *a, **k: log.append("scan") or [],
        resolve_entries=lambda entries, label: [],
        save_dream_realm=lambda rows, day, boss, boss_id: log.append(f"save {day}") or 0,
        get_boss_for_date=lambda today, today_boss, day: ("Boss", 1),
        make_id_fallback_hook=lambda label: None,
        _reenter_rankings=reenter,
    )
    try:
        dr._capture_days(missing, "2026-10-28", 1, "Boss")
    finally:
        restore(saved)
    return log


def test_every_day_after_the_first_re_enters_before_selecting():
    log = run_capture(["2026-10-27", "2026-10-26", "2026-10-25"])
    check("first day is selected with no re-entry before it", log[0] == "select 2026-10-27")
    check("re-entry happens before each later day is selected",
          log.count("reenter") == 2
          and log.index("reenter") < log.index("select 2026-10-26")
          and [l for l in log if l == "reenter" or l.startswith("select")]
          == ["select 2026-10-27", "reenter", "select 2026-10-26", "reenter", "select 2026-10-25"])
    check("each day is filtered and scanned after it is selected",
          log.count("filter") == 3 and log.count("scan") == 3)


def test_a_failed_re_entry_stops_the_run():
    log = run_capture(["2026-10-27", "2026-10-26", "2026-10-25"], reenter_ok=False)
    check("a failed re-entry never scans a mid-list board", "select 2026-10-26" not in log and log.count("scan") == 1)
    check("a failed re-entry is not retried, since pressing back again would leave the screen",
          log.count("reenter") == 1 and "select 2026-10-25" not in log)


test_reenter_presses_back_and_taps_rankings()
test_reenter_fails_when_rankings_button_missing()
test_every_day_after_the_first_re_enters_before_selecting()
test_a_failed_re_entry_stops_the_run()

print(f"{total - failures}/{total} passed")
sys.exit(1 if failures else 0)
