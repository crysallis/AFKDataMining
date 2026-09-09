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
from nav import navigate_to_mode, find_template, find_template_all, _t, TEMPLATES_DIR
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

# How many single-swipe scrolls to try per climb attempt before giving up
# and treating it as a stuck/broken state (never a burst: a burst of
# scroll_down() calls was confirmed live 2026-09-08 to fling the list ~27
# ranks past everything in between, the same over-scroll pattern already
# documented for Arcane Lab). Measured live: ~4 ranks gained per swipe, and
# a drill-in resets the list to roughly rank 4-8 -- worst case within a
# ~70-row scan needs ~(70-4)/4 = 17 swipes, so 30 gives real headroom.
MAX_CLIMB_SWIPES = 30

# Hard ceiling on total swipes across the WHOLE scan, independent of any
# per-climb budget -- confirmed live 2026-09-08 a bug in an earlier version
# of this loop (fixed, see git history) walked the list repeatedly without
# this guard, reaching "rank 275" on a scroll before being killed manually.
# Sized for a real ~70-row scan (70 * ~17 swipes/climb =~ 1200 worst case
# would be too generous here -- if a run needs anywhere near that many
# swipes something is already wrong, and letting it run ~70 minutes before
# tripping defeats the point of having a ceiling at all. 400 covers a
# genuinely working scan through rank ~70 with real slack, while still
# aborting a broken run in ~18 minutes instead of over an hour.
MAX_TOTAL_SWIPES = 400

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


def _rank_rows_in_band(img) -> list[tuple[int, int]]:
    """(y, rank) pairs from _scan_rank_column, restricted to the safe tap
    band, PLUS a template-match fallback for ranks 1-3's ornate badges --
    same fallback parse_rank_rows itself uses (common.py:154-167), needed
    here because _scan_rank_column's plain digit-strip OCR misses rank 1's
    gold crown glyph almost every time (parse_rank_rows's own comment:
    "tested: 1/20 via OCR"). Without this, a scan whose first target is
    rank 1 climbs forever looking for a rank that OCR will essentially
    never produce -- confirmed live 2026-09-08, the actual cause of a
    runaway scroll that walked the entire list searching for it."""
    hits = [(sy, rv) for sy, rv in _scan_rank_column(img) if ROW_TAP_Y_MIN <= sy <= ROW_TAP_Y_MAX]
    seen_ranks = {rv for _sy, rv in hits}
    for rv in (1, 2, 3):
        if rv in seen_ranks:
            continue
        tmpl = TEMPLATES_DIR / f"rank_{rv}_badge.png"
        if not tmpl.exists():
            continue
        for _cx, cy in find_template_all(img, tmpl, threshold=0.75):
            if ROW_TAP_Y_MIN <= cy <= ROW_TAP_Y_MAX:
                hits.append((cy, rv))
                break
    return hits


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
    total_swipes = 0
    highest_tapped = 0

    while highest_tapped < max_rank and total_swipes < MAX_TOTAL_SWIPES:
        # Target the LOWEST untapped rank currently visible in the safe tap
        # band -- not a specific frontier number. This is self-healing
        # against any one rank that's permanently unreadable (rank 1's gold
        # crown glyph, or a row clipped at a card boundary): if that rank
        # never resolves, the next-lowest untapped rank is simply tapped
        # instead, rather than the whole scan getting stuck hunting for one
        # specific number that may never appear. An earlier version of this
        # loop targeted a fixed "next expected rank" and got stuck exactly
        # this way live 2026-09-08 -- confirmed rank 1's crown glyph is
        # essentially unreadable via plain OCR, so the climb walked the
        # entire ~500-row list searching for a rank number that (without
        # the badge-template fallback _rank_rows_in_band now includes)
        # would never show up, reaching "rank 275" before being killed.
        target_rank = None
        target_y = None
        for sy, rv in sorted(_rank_rows_in_band(screenshot())):
            if rv not in tapped_ranks:
                target_rank, target_y = rv, sy
                break

        if target_rank is None:
            # Nothing untapped visible -- climb via single verified swipes
            # (never a burst: confirmed live 2026-09-08 a burst of
            # scroll_down() calls flings the list ~27 ranks past everything
            # in between, the same over-scroll pattern already documented
            # for Arcane Lab) until something new appears or the climb
            # budget runs out.
            found_during_climb = False
            for _swipe in range(MAX_CLIMB_SWIPES):
                if total_swipes >= MAX_TOTAL_SWIPES:
                    break
                scroll_down()
                total_swipes += 1
                time.sleep(1.2)
                mark_progress()  # real device work, keep the watchdog quiet
                for sy, rv in sorted(_rank_rows_in_band(screenshot())):
                    if rv not in tapped_ranks:
                        target_rank, target_y = rv, sy
                        found_during_climb = True
                        break
                if found_during_climb:
                    break

            if not found_during_climb:
                # Climb exhausted without finding anything new -- could be
                # the genuine end of the list, or the scan got stranded
                # somewhere unrecognizable. Re-navigate (a fresh, reliable
                # reset to the top of the list -- never scroll_to_top() or
                # scroll_up() here, both confirmed unsafe on ranking lists,
                # see gotcha-arcane-lab-list-collapses-on-overscroll) rather
                # than continuing to scroll_down() blindly from an unknown
                # position; if even that produces nothing new, the list has
                # genuinely been fully covered and the scan ends.
                print(f"PEARL_BAZAAR: climb exhausted ({MAX_CLIMB_SWIPES} swipes) with no "
                      f"new rank found (highest tapped so far: {highest_tapped}), "
                      f"re-navigating to recover.")
                try:
                    navigate_to_mode("supreme_arena")
                except RuntimeError:
                    pass
                for sy, rv in sorted(_rank_rows_in_band(screenshot())):
                    if rv not in tapped_ranks:
                        target_rank, target_y = rv, sy
                        break
                if target_rank is None:
                    print("PEARL_BAZAAR: nothing new after re-navigating either -- "
                          "ending scan.")
                    break

        tapped_ranks.add(target_rank)
        highest_tapped = max(highest_tapped, target_rank)

        tap(RANK_CARD_AVATAR_X, target_y + RANK_CARD_AVATAR_Y_OFFSET)
        time.sleep(1.5)
        user_id = read_user_id(screenshot())
        mark_progress()

        _capture_pearl_bazaar_row(target_rank, user_id, counters)

    print(f"PEARL_BAZAAR: drilled {counters['drilled']} rows, saved {counters['saved']}.")
    return "success"
