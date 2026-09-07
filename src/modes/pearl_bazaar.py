"""Pearl Bazaar drill-in scan. Walks the (unfiltered) Supreme Arena rankings
list, ranks 1-100, and for every row taps into that player's profile to
check for the Pearl Bazaar icon. Where present, captures today's selling
price and remaining daily selling cap. No guild filter -- most of the top
100 are other guilds' members. Only the latest run's data has any value
(remaining cap changes minute to minute), so the table is wiped at the
START of this scan, not the end."""
import re
import time

from db import _connect, wipe_pearl_bazaar_prices, save_pearl_bazaar_row
from device import screenshot, tap, back
from nav import navigate_to_mode, find_template, _t
from ocr import ocr_image
from modes.common import parse_rank_rows, scroll_scan, make_drill_in_hook

PRICE_RE = re.compile(r"\b(\d{3})\b")
CAP_RE = re.compile(r"Total\s*Daily\s*Selling\s*Cap\D*(\d+)", re.IGNORECASE)


def _parse(img, ocr_results) -> list[dict]:
    return [{"name": row.name, "rank": row.rank} for row in parse_rank_rows(img, ocr_results)]


def _make_row_capturer(counters: dict):
    """Wraps _capture_pearl_bazaar_row's actual work, closing over a shared
    counters dict (drilled/saved) that scan() reads after scroll_scan
    finishes -- the row callback itself is called by make_drill_in_hook with
    only (rank, user_id) and no return value scan() could otherwise
    aggregate."""

    def _capture_pearl_bazaar_row(rank: int, user_id: int | None) -> None:
        counters["drilled"] += 1

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
        time.sleep(1.2)

        price_screen = screenshot()
        if not find_template(price_screen, _t("pearl_bazaar_price_label"), threshold=0.80):
            print(f"PEARL_BAZAAR: rank {rank} (ID {user_id}) Pearl screen didn't load, skipping.")
            back()
            return

        price = None
        cap_remaining = None
        for _box, text, _conf in ocr_image(price_screen):
            cap_match = CAP_RE.search(text)
            if cap_match:
                cap_remaining = int(cap_match.group(1))
                continue
            if price is None:
                m = PRICE_RE.search(text)
                if m:
                    price = int(m.group(1))

        # Exactly one Back from the pricing screen returns straight to the rank
        # list -- confirmed live, the game skips the intermediate profile screen.
        back()

        if price is None or cap_remaining is None:
            print(f"PEARL_BAZAAR: rank {rank} (ID {user_id}) price/cap not read, skipping.")
            return

        save_pearl_bazaar_row(rank, user_id, price, cap_remaining)
        counters["saved"] += 1
        print(f"PEARL_BAZAAR: rank {rank} (ID {user_id}) price {price}, {cap_remaining} left to sell.")

    return _capture_pearl_bazaar_row


def scan() -> str:
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
    scroll_scan(_parse, "Pearl Bazaar",
                on_stop=make_drill_in_hook(_make_row_capturer(counters)))

    print(f"PEARL_BAZAAR: drilled {counters['drilled']} rows, saved {counters['saved']}.")
    return "success"
