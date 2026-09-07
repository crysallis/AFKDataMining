"""Clashfronts sign-up/selection scan · single kept scan (full-list overwrite).

Unlike every other mode, the target is not a Rankings screen but the
'Manage Sign-Ups' roster: every row is a member who signed up (guild-wide,
already filtered -- no apply_guild_filter() needed), and a green/grey
checkmark per row shows whether they've been selected/approved. No rank
numbers appear on this screen at all, so parse_rank_rows doesn't apply --
row detection here is name-block + template-matched checkmark only."""
import re

from ocr import block_center
from db import init_db, save_clashfronts_signups
from nav import (navigate_to_clashfronts_signups, press_back,
                 ClashfrontsDeployScreen, ClashfrontsNotActive)
from modes.common import (NUM_RE, SKIP_LABELS, scroll_scan, resolve_entries,
                          detect_tiers, tier_for_row, _warband_skip_set)

# Name column sits left of the power/formations/approve columns.
_NAME_X_MAX = 450
_ROW_TOL = 60
# Power values on this screen ("114M", "99,282K") -- never a name.
_POWER_RE = re.compile(r"^[\d,.]+\s*[KM]?$", re.IGNORECASE)
_HEADER_SKIP = {"power", "formations", "approve", "quick setup"}
# The title ('M' drop-cap + 'anage Sign-Ups'), subtitle, and '30/30
# participants' counter are static screen chrome above the list header row
# (header itself ~y=433) and never scroll -- only the list below it does.
_LIST_Y_MIN = 460


def _parse(img, ocr_results) -> list[dict]:
    skip = SKIP_LABELS | _HEADER_SKIP | _warband_skip_set()
    tiers = detect_tiers(img, "clashfronts")

    rows = []
    for box, text, _ in ocr_results:
        t = text.strip()
        if not t:
            continue
        cx, cy = block_center(box)
        if cx > _NAME_X_MAX or cy < _LIST_Y_MIN:
            continue
        cleaned = re.sub(r"^[\W_]+", "", t)
        if not cleaned or cleaned.lower() in skip:
            continue
        if _POWER_RE.match(cleaned.replace(",", "")) or NUM_RE.match(cleaned.replace(",", "")):
            continue
        # tier is None when this row's checkmark wasn't matched in this frame
        # (e.g. the floating scroll-nav button briefly occluding the bottom
        # row) -- abstain rather than collapse to False, so an occluded green
        # check doesn't outvote the clean reads of the same row from other
        # scroll stops. _vote_entries already drops None values per field.
        tier = tier_for_row(cy, tiers, tol=_ROW_TOL)
        selected = {"selected": True, "not_selected": False}.get(tier)
        rows.append({"name": cleaned, "selected": selected, "y": cy})

    rows.sort(key=lambda r: r["y"])
    for r in rows:
        del r["y"]
    return rows


def scan() -> None:
    init_db()
    try:
        navigate_to_clashfronts_signups()
    except ClashfrontsDeployScreen:
        press_back()
        print("CLASHFRONTS: sign-ups closed (deployment screen), skipping.")
        return "skipped"
    except ClashfrontsNotActive:
        print("CLASHFRONTS: card not found on Battle Modes, likely not active this cycle. Skipping.")
        return "skipped"
    entries = scroll_scan(_parse, "Clashfronts")
    rows = resolve_entries(entries, "Clashfronts")
    saved = save_clashfronts_signups(rows)
    selected_count = sum(1 for r in rows if r.get("selected"))
    print(f"CLASHFRONTS: saved {saved} signups ({selected_count} selected).")
    return "success"
