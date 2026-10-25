"""Row filter for the Guild Duel Damage list · keeps a row only when its whole card is
visible: inside the list window and not overlapped by the viewer's own row card (the
pinned bar at the top or bottom, or an ordinary row while the viewer's rank is on
screen). Rows cut by the bar or a list edge read as junk, so they are skipped and read
in full at another scroll stop."""
import numpy as np

WINDOW = (710, 1733)
EDGE_TOL = 6

CARD_ABOVE_DAMAGE = 104
CARD_BELOW_DAMAGE = 58

OWN_STRIP_X = (420, 760)
OWN_SCAN_Y = (690, 1760)
OWN_DARK_MAX = 195
OWN_CARD_HEIGHT = (150, 175)


def find_own_card(img: np.ndarray) -> tuple[int, int] | None:
    """Top and bottom y of the viewer's own row card, found as one dark run in the
    strip between the name and damage columns. None unless exactly one run has a
    card-sized height."""
    y0, y1 = OWN_SCAN_Y
    strip = img[y0:y1, OWN_STRIP_X[0]:OWN_STRIP_X[1]].astype(np.float32).mean(axis=2)
    dark = np.median(strip, axis=1) < OWN_DARK_MAX
    runs = []
    start = None
    for i, is_dark in enumerate(dark):
        if is_dark and start is None:
            start = i
        elif not is_dark and start is not None:
            runs.append((start + y0, i - 1 + y0))
            start = None
    if start is not None:
        runs.append((start + y0, len(dark) - 1 + y0))
    low, high = OWN_CARD_HEIGHT
    cards = [(a, b) for a, b in runs if low <= b - a + 1 <= high]
    return cards[0] if len(cards) == 1 else None


def row_card(damage_y: int) -> tuple[int, int]:
    return damage_y - CARD_ABOVE_DAMAGE, damage_y + CARD_BELOW_DAMAGE


def is_clear(card: tuple[int, int], own: tuple[int, int] | None,
             window: tuple[int, int] = WINDOW, tol: int = EDGE_TOL) -> bool:
    top, bottom = card
    if top < window[0] - tol or bottom > window[1] + tol:
        return False
    if own is None:
        return True
    if abs(top - own[0]) <= tol and abs(bottom - own[1]) <= tol:
        return True
    return not (top < own[1] - tol and bottom > own[0] + tol)


def filter_with_own(rows: list, own: tuple[int, int] | None) -> tuple[list, int]:
    kept = [r for r in rows if is_clear(row_card(r.y), own)]
    return kept, len(rows) - len(kept)


def filter_clear_rows(img: np.ndarray, rows: list) -> tuple[list, int]:
    return filter_with_own(rows, find_own_card(img))
