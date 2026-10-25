"""Cyrillic second pass for the Guild Duel Damage scan · the main recognizer writes
Cyrillic names as Latin look-alikes (Найхэ as Hanxa), so each name-column block is
re-read with the Cyrillic recognizer and swapped in only when the result is mostly
Cyrillic letters at a confident score. Latin, CJK and empty results never replace the
original read, and confidence alone is never enough."""
import numpy as np

from ocr import read_cyrillic

MIN_RATIO = 0.6
MIN_CONF = 0.8
CROP_PAD = 8


def cyrillic_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum("Ѐ" <= c <= "ӿ" for c in letters) / len(letters)


def accept_cyrillic(text: str, conf: float, min_ratio: float = MIN_RATIO, min_conf: float = MIN_CONF) -> bool:
    return bool(text) and conf >= min_conf and cyrillic_ratio(text) >= min_ratio


def reread_blocks(img: np.ndarray, ocr_results: list, name_x_range: tuple[int, int], y_min: int,
                  reader=read_cyrillic, pad: int = CROP_PAD, resolve_fn=None) -> list:
    """resolve_fn(name) -> canonical member name or None. When given, a read that
    already resolves to a member is replaced only by a Cyrillic read that resolves
    to the same member, so a Cyrillic-named member still stored under Latin
    look-alikes cannot start failing to match."""
    height, width = img.shape[:2]
    out = []
    for box, text, conf in ocr_results:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        if not (name_x_range[0] <= cx <= name_x_range[1] and cy >= y_min):
            out.append((box, text, conf))
            continue
        crop = img[max(0, int(min(ys)) - pad):min(height, int(max(ys)) + pad),
                   max(0, int(min(xs)) - pad):min(width, int(max(xs)) + pad)]
        try:
            new_text, new_conf = reader(crop)
        except Exception:
            new_text, new_conf = "", 0.0
        replace = accept_cyrillic(new_text, new_conf)
        if replace and resolve_fn is not None:
            current = resolve_fn(text)
            replace = current is None or resolve_fn(new_text) == current
        out.append((box, new_text, conf) if replace else (box, text, conf))
    return out
