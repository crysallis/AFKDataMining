"""Pearl Bazaar drill-in scan. Walks the (unfiltered) Supreme Arena rankings
list, ranks 1-100, and for every row taps into that player's profile to
check for the Pearl Bazaar icon. Where present, captures today's selling
price and remaining daily selling cap. No guild filter -- most of the top
100 are other guilds' members. Only the latest run's data has any value
(remaining cap changes minute to minute), so the table is wiped at the
START of this scan, not the end."""
import re

from db import wipe_pearl_bazaar_prices, save_pearl_bazaar_row
from device import screenshot, tap, back
from nav import navigate_to_mode, find_template, _t
from ocr import ocr_image
from modes.common import parse_rank_rows, scroll_scan, make_drill_in_hook

PRICE_RE = re.compile(r"\b(\d{3})\b")
CAP_RE = re.compile(r"Total\s*Daily\s*Selling\s*Cap\D*(\d+)", re.IGNORECASE)


def _parse(img, ocr_results) -> list[dict]:
    return [{"name": row.name, "rank": row.rank} for row in parse_rank_rows(img, ocr_results)]


def _capture_pearl_bazaar_row(rank: int, user_id: int | None) -> None:
    if user_id is None:
        print(f"PEARL_BAZAAR: rank {rank} User ID not read, skipping.")
        back()
        return

    icon_pos = find_template(screenshot(), _t("pearl_bazaar_icon"), threshold=0.80)
    if not icon_pos:
        # Expected and normal -- not everyone participates in this event.
        back()
        return

    tap(*icon_pos)
    import time
    time.sleep(1.2)

    price_screen = screenshot()
    if not find_template(price_screen, _t("pearl_bazaar_price_label"), threshold=0.80):
        print(f"PEARL_BAZAAR: rank {rank} (ID {user_id}) Pearl screen didn't load, skipping.")
        back()
        return

    price = None
    cap_remaining = None
    for _box, text, _conf in ocr_image(price_screen):
        if price is None:
            m = PRICE_RE.search(text)
            if m:
                price = int(m.group(1))
        m = CAP_RE.search(text)
        if m:
            cap_remaining = int(m.group(1))

    # Exactly one Back from the pricing screen returns straight to the rank
    # list -- confirmed live, the game skips the intermediate profile screen.
    back()

    if price is None or cap_remaining is None:
        print(f"PEARL_BAZAAR: rank {rank} (ID {user_id}) price/cap not read, skipping.")
        return

    save_pearl_bazaar_row(rank, user_id, price, cap_remaining)
    print(f"PEARL_BAZAAR: rank {rank} (ID {user_id}) price {price}, {cap_remaining} left to sell.")


def scan() -> str:
    try:
        navigate_to_mode("supreme_arena")
    except RuntimeError:
        if find_template(screenshot(), _t("supreme_arena_inactive"), threshold=0.80):
            print("PEARL_BAZAAR: Supreme Arena inactive, skipping.")
            return "skipped"
        raise

    from device import tap as _tap
    daily_calc = find_template(screenshot(), _t("supreme_arena_daily_calc"), threshold=0.80)
    if daily_calc:
        _tap(*daily_calc)

    wipe_pearl_bazaar_prices()

    scroll_scan(_parse, "Pearl Bazaar",
                on_stop=make_drill_in_hook(_capture_pearl_bazaar_row))

    print("PEARL_BAZAAR: scan complete.")
    return "success"
