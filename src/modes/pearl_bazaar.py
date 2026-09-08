"""Pearl Bazaar drill-in scan. Walks the (unfiltered) Supreme Arena rankings
list, ranks 1-100, and for every row taps into that player's profile to
check for the Pearl Bazaar icon. Where present, captures today's selling
price and remaining daily selling cap. No guild filter -- most of the top
100 are other guilds' members. Only the latest run's data has any value
(remaining cap changes minute to minute), so the table is wiped at the
START of this scan, not the end."""
import re
import time

import cv2

from db import _connect, wipe_pearl_bazaar_prices, save_pearl_bazaar_row
from device import screenshot, tap, back, scroll_down, mark_progress
from nav import navigate_to_mode, find_template, _t, TEMPLATES_DIR
from ocr import ocr_image, block_center
from modes.common import (_scan_rank_column, read_user_id,
                          RANK_CARD_AVATAR_X, RANK_CARD_AVATAR_Y_OFFSET)

# Safe y-band for tapping a row's avatar -- a row whose card is clipped at
# the very top or bottom of the screen (like rank 38 in a live capture,
# 2026-09-08) only has its top half on screen, and a tap can land below the
# real card into dead space or the nav-back arrow. Every successful tap in
# live probes this session landed on a row comfortably inside this range.
ROW_TAP_Y_MIN = 700
ROW_TAP_Y_MAX = 1400

# How many single-swipe scrolls to try per climb attempt before giving up on
# ever finding the next target rank. Measured live 2026-09-08: ~4 ranks
# gained per swipe, and a drill-in resets the list to roughly rank 4-8 --
# so climbing back to rank 100 from there needs at most ~(100-4)/4 = 24
# swipes. 30 gives headroom without risking a runaway climb past the end
# of a shorter list on a bad reading.
MAX_CLIMB_SWIPES = 30

# End the whole scan once this many consecutive climbs fail to reach a new
# highest rank at all -- distinguishes "reached the real end of the list"
# from "stuck mid-climb", which MAX_CLIMB_SWIPES alone can't do since a
# slow-but-working climb and a genuinely-ended list both eventually stop
# producing progress within one climb's swipe budget.
MAX_CONSECUTIVE_CLIMB_FAILURES = 2

PRICE_RE = re.compile(r"\b(\d{3})\b")
CAP_RE = re.compile(r"Total\s*Daily\s*Selling\s*Cap\D*(\d+)", re.IGNORECASE)

# The Pearl Bazaar screen has a scrolling marquee/chat line below the price+cap
# block (e.g. "Pearl Price: Fishbo x903; Available Slot: x200. Hurry in!") that
# can contain other 3-digit numbers unrelated to this seller's own price --
# confirmed live 2026-09-07, "Fishbo" is just another scanned player's name
# showing up in that feed, not a screen field. Cropping the OCR pass to end
# just below "Total Daily Selling Cap" excludes that marquee entirely, so
# PRICE_RE never sees it. Measured live against a 1080x1920 capture -- the
# price+cap block sits well above this line; re-verify if the game's layout
# ever changes.
PRICE_CAP_REGION_Y_MAX = 1440

# The player's own Fishbo (currency) balance is a 3-digit number pinned at
# the top-right of the screen (measured live 2026-09-08 at (935,35), same
# shape/x-range every time) -- an unscoped PRICE_RE match found this FIRST on
# every single row of a live scan, since OCR returns screen-top blocks before
# the price panel further down, producing the same wrong "price" (the coin
# balance) for every seller regardless of their real price. The real price
# sits directly under the "Today's Price" label matched by
# pearl_bazaar_price_label (measured live: label y=1026, price y=1130 --
# ~104px below, same x). A tight y-window anchored on the label (not just
# "below the label", which would also catch "Daily Selling Cap per Visitor:
# 20" at y=1341) keeps this to the one real price block.
PRICE_Y_OFFSET_MIN = 50
PRICE_Y_OFFSET_MAX = 160


def _capture_pearl_bazaar_row(rank: int, user_id: int | None, counters: dict) -> None:
    """Drills into one already-tapped-open profile: checks for the Pearl
    Bazaar icon, and if present, taps in for price+cap. counters tracks
    drilled/saved for scan()'s summary line.

    Every branch that reaches the Pearl icon check has ALREADY opened the
    price screen or is about to -- confirmed live 2026-09-08 that doing so
    resets the rank list's scroll position back near the top, regardless of
    how deep the drilled row was. scan()'s climb loop is what re-locates the
    next target rank afterward; this function's own job ends at getting back
    to the plain rank list via back()."""
    counters["drilled"] += 1

    if user_id is None:
        # Debug capture -- this path is ambiguous between "opened a real
        # profile but OCR missed the ID text" (one back() is correct) and
        # "the tap missed the row/banner entirely" (a back() here isn't
        # undoing anything, and can cascade further up the nav stack than
        # intended) -- confirmed live 2026-09-08 this happens and the scan
        # goes completely dark afterward. Same debug_*.png convention as
        # nav.py's own failure captures.
        cv2.imwrite(str(TEMPLATES_DIR.parent / f"debug_pearl_bazaar_no_id_rank{rank}.png"),
                    screenshot())
        print(f"PEARL_BAZAAR: rank {rank} User ID not read, skipping.")
        back()
        time.sleep(1.0)
        return

    icon_pos = find_template(screenshot(), _t("pearl_bazaar_icon"), threshold=0.80)
    if not icon_pos:
        # Expected and normal -- not everyone participates in this event.
        back()
        time.sleep(1.0)
        return

    tap(*icon_pos)
    # The price panel animates in noticeably after the rest of the Pearl
    # Bazaar screen -- confirmed live 2026-09-08: at 1.2s the shopkeeper
    # background and "Daily Selling Cap" panel were already rendered but
    # "Today's Price" was still blank, causing a false "screen didn't
    # load" skip on every single row of a live scan. 2.5s consistently
    # showed the fully-rendered price panel in the same test.
    time.sleep(2.5)

    price_screen = screenshot()
    label_pos = find_template(price_screen, _t("pearl_bazaar_price_label"), threshold=0.80)
    if not label_pos:
        print(f"PEARL_BAZAAR: rank {rank} (ID {user_id}) Pearl screen didn't load, skipping.")
        back()
        time.sleep(1.0)
        return
    _label_x, label_y = label_pos
    price_y_min = label_y + PRICE_Y_OFFSET_MIN
    price_y_max = label_y + PRICE_Y_OFFSET_MAX

    price = None
    cap_remaining = None
    cropped_screen = price_screen[:PRICE_CAP_REGION_Y_MAX, :]
    for box, text, _conf in ocr_image(cropped_screen):
        cap_match = CAP_RE.search(text)
        if cap_match:
            cap_remaining = int(cap_match.group(1))
            continue
        if price is None and price_y_min <= block_center(box)[1] <= price_y_max:
            m = PRICE_RE.search(text)
            if m:
                price = int(m.group(1))

    # Exactly one Back from the pricing screen returns straight to the rank
    # list -- confirmed live, the game skips the intermediate profile screen.
    # The transition itself isn't instant, though: without a wait here the
    # very next row's tap can fire before the list has finished settling
    # back into view -- confirmed live 2026-09-08, this caused a cascade of
    # bad taps in a live run that eventually landed on the game's world
    # overview screen (several navigation levels above the rankings list).
    back()
    time.sleep(1.0)

    if price is None or cap_remaining is None:
        print(f"PEARL_BAZAAR: rank {rank} (ID {user_id}) price/cap not read, skipping.")
        return

    save_pearl_bazaar_row(rank, user_id, price, cap_remaining)
    counters["saved"] += 1
    print(f"PEARL_BAZAAR: rank {rank} (ID {user_id}) price {price}, {cap_remaining} left to sell.")


def scan(max_rank: int = 100) -> str:
    """max_rank caps how far down the rankings list this scan climbs --
    production default 100 (the SA list's practical size). Lower values are
    useful for a bounded manual test run without editing this function."""
    missing = [n for n in ("pearl_bazaar_icon", "pearl_bazaar_price_label") if not _t(n).exists()]
    if missing:
        print(f"PEARL_BAZAAR: missing template(s) {', '.join(missing)} -- "
              f"capture via capture_template.py before running. Aborting.")
        return "failed"

    try:
        navigate_to_mode("supreme_arena")
    except RuntimeError:
        if find_template(screenshot(), _t("supreme_arena_inactive"), threshold=0.80):
            print("PEARL_BAZAAR: Supreme Arena inactive, skipping.")
            return "skipped"
        raise

    daily_calc = find_template(screenshot(), _t("supreme_arena_daily_calc"), threshold=0.80)
    if daily_calc:
        tap(*daily_calc)

    with _connect() as conn:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pearl_bazaar_prices'"
        ).fetchone()
    if not table_exists:
        print("PEARL_BAZAAR: pearl_bazaar_prices table not found (bot-owned schema -- "
              "has the Discord bot process run at least once against this guild.db?). Aborting.")
        return "failed"

    wipe_pearl_bazaar_prices()

    counters = {"drilled": 0, "saved": 0}
    tapped_ranks: set[int] = set()
    next_rank = 1  # the frontier -- always target this specific rank next,
                   # never "whatever's lowest and untapped", so a rank that's
                   # permanently out of the safe tap band (clipped top/bottom
                   # row) gets skipped forward past instead of silently
                   # blocking every later rank from ever being targeted
    consecutive_climb_failures = 0

    while next_rank <= max_rank and consecutive_climb_failures < MAX_CONSECUTIVE_CLIMB_FAILURES:
        target_y = None
        for sy, rv in _scan_rank_column(screenshot()):
            if rv == next_rank and ROW_TAP_Y_MIN <= sy <= ROW_TAP_Y_MAX:
                target_y = sy
                break

        if target_y is None:
            # next_rank isn't visible (or is visible but clipped out of the
            # safe tap band) -- climb via single verified swipes (never a
            # burst: a burst of scroll_down() calls was confirmed live
            # 2026-09-08 to fling the list ~27 ranks past everything in
            # between, the same over-scroll pattern documented for Arcane
            # Lab) until next_rank appears in-band or the climb budget runs
            # out. If the climb passes next_rank's y entirely (visible
            # on-screen but never in-band, or misread), give up on that
            # specific rank and move the frontier forward rather than
            # looping on it forever.
            found_during_climb = False
            highest_seen = next_rank
            for _swipe in range(MAX_CLIMB_SWIPES):
                scroll_down()
                time.sleep(1.2)
                mark_progress()  # real device work, keep the watchdog quiet
                current = _scan_rank_column(screenshot())
                for sy, rv in current:
                    if rv > highest_seen:
                        highest_seen = rv
                    if rv == next_rank and ROW_TAP_Y_MIN <= sy <= ROW_TAP_Y_MAX:
                        target_y = sy
                        found_during_climb = True
                if found_during_climb:
                    break

            if not found_during_climb:
                if highest_seen > next_rank:
                    # The list moved past next_rank without ever showing it
                    # in a tappable position (e.g. permanently clipped at a
                    # card boundary) -- skip it, matching every other mode's
                    # skip-and-continue policy on a single bad row.
                    print(f"PEARL_BAZAAR: rank {next_rank} never reachable in the "
                          f"tap band, skipping.")
                    next_rank += 1
                    continue
                consecutive_climb_failures += 1
                print(f"PEARL_BAZAAR: climb exhausted ({MAX_CLIMB_SWIPES} swipes) looking "
                      f"for rank {next_rank} ({consecutive_climb_failures}/"
                      f"{MAX_CONSECUTIVE_CLIMB_FAILURES}) -- likely end of list.")
                continue

        consecutive_climb_failures = 0
        tapped_ranks.add(next_rank)

        tap(RANK_CARD_AVATAR_X, target_y + RANK_CARD_AVATAR_Y_OFFSET)
        time.sleep(1.5)
        user_id = read_user_id(screenshot())
        mark_progress()

        _capture_pearl_bazaar_row(next_rank, user_id, counters)
        next_rank += 1

    print(f"PEARL_BAZAAR: drilled {counters['drilled']} rows, saved {counters['saved']}.")
    return "success"
