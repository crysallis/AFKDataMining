"""Shared scaffolding for game-mode ranking scans: row parsing for ranked
lists, the open-ended scroll loop, tier-icon detection, and the save plumbing
that resolves OCR names into member ids."""
import logging
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import cv2
import numpy as np
from device import screenshot, scroll_down, screen_changed
from nav import TEMPLATES_DIR, find_template_all
from ocr import ocr_image, block_center, engine, get_engine_v5
from db import resolve_names

# Ranked lists have no known total (1 to all 90 members may place), so the
# scroll loop ends only on two consecutive unchanged frames.
MAX_SCROLLS = 60

RANK_RE = re.compile(r"^\d{1,4}$")
NUM_RE = re.compile(r"^[\d,]+$")
# Ranking-screen UI labels that must never be taken as a player name.
SKIP_LABELS = {"rank", "player", "points", "score", "name", "ranking", "guild",
               "filter", "rewards", "my rank", "season", "honor points",
               "unranked", "district"}


@dataclass
class RankRow:
    rank: int | None
    name: str
    numbers: list[int] = field(default_factory=list)  # numeric blocks right of the name
    texts: list[str] = field(default_factory=list)    # extra non-numeric blocks (difficulty etc.)
    y: int = 0


def fuzzy_key(name: str, seen_lower: set[str], threshold: float = 0.88) -> str | None:
    key = name.lower()
    if key in seen_lower:
        return key
    for existing in seen_lower:
        if SequenceMatcher(None, key, existing).ratio() >= threshold:
            return existing
    return None


def parse_rank_rows(img, ocr_results: list, card_tol: int = 85,
                    rank_x_max: int = 220,
                    extra_skip: set[str] | None = None) -> list[RankRow]:
    """Rank-number-anchored parser for ranking list screens.

    The large rank number on the far left of each card is the anchor, mirroring
    how parse_members() uses timestamps. For each rank anchor at (rank_x, rank_y):
      - name:    first clean text block within card_tol px and x > rank_x_max
      - numbers: all pure-numeric blocks within card_tol px (points, score, etc.)
      - texts:   remaining text blocks (server tag, warband line, labels) — kept
                 for mode-specific use, ignored by default

    card_tol covers the full card height so warband sub-rows are absorbed without
    becoming phantom member rows. Initial guess: 120px · tune with test_ocr.py."""
    skip = SKIP_LABELS | (extra_skip or _warband_skip_set())

    blocks = []
    for box, text, _ in ocr_results:
        t = text.strip()
        if t:
            cx, cy = block_center(box)
            blocks.append((cx, cy, t))

    # Compute list start y early so hero-section badge matches can be excluded.
    # Name blocks sit at x > rank_x_max; the minimum y of those is the list top.
    _early_name_ys = [cy for cx, cy, t in blocks
                      if cx > rank_x_max
                      and not NUM_RE.match(t.replace(",", ""))
                      and t.strip().lower() not in SKIP_LABELS]
    _list_y_min = max(0, min(_early_name_ys) - 80) if _early_name_ys else 0

    # Rank anchors are now handled entirely via the column scan (PP-OCRv5).
    # The main OCR (PP-OCRv3) splits multi-digit ranks like "31" into "3"+"1",
    # creating false anchors. The column scan reads the full number correctly.
    anchors: list[tuple[int, int, int]] = []  # kept empty — fallback path handles all

    # Points values live on the far right of each card (x > 700 on 1080px screens)
    point_blocks = []
    for cx, cy, t in blocks:
        cleaned = t.replace(",", "").strip()
        if cx > 700 and NUM_RE.match(cleaned):
            point_blocks.append((cx, cy, int(cleaned)))

    # Column scan: all rank numbers in one OCR pass on the rank strip.
    # Start below the hero/podium section so decorative rank badges at the
    # top don't create duplicate entries for the same rank value.
    name_ys = [cy for cx, cy, t in blocks if cx > rank_x_max
               and not NUM_RE.match(t.replace(",", ""))
               and t.lower() not in skip]
    col_y_min = max(0, min(name_ys) - 80) if name_ys else 0
    # Deduplicate by rank value: keep only the topmost (lowest y) occurrence of
    # each rank number. "31" misread as "3" would otherwise assign rank 3 to a
    # member whose actual rank is 31.
    _seen_ranks: set[int] = set()
    column_ranks: dict[int, int] = {}
    for sy, rv in _scan_rank_column(img, y_min=col_y_min):
        if rv not in _seen_ranks:
            _seen_ranks.add(rv)
            column_ranks[sy] = rv

    rows: list[RankRow] = []
    for rank_val, rank_cx, rank_cy in anchors:
        nearby = [(cx, cy, t) for cx, cy, t in blocks
                  if abs(cy - rank_cy) <= card_tol and cx > rank_cx]
        nearby.sort(key=lambda b: b[0])  # left to right

        name = ""
        numbers: list[int] = []
        texts: list[str] = []
        for cx, cy, t in nearby:
            cleaned = t.replace(",", "").strip()
            if NUM_RE.match(cleaned):
                numbers.append(int(cleaned))
            elif not name:
                candidate = re.sub(r"^[\W_]+", "", t)
                # Strip trailing server tag merged by OCR: "Kits282", "Kit282", "Kit s282"
                # Only accept S/s as optional prefix — [A-Za-z] was too broad and
                # ate the last letter of names like UCQ (UCQ282 → UC instead of UCQ).
                candidate = re.sub(r"\s*[sS]?\d{3,4}$", "", candidate).strip()
                candidate = re.sub(r"\s*[sS]$", "", candidate).strip()  # trailing lone S (truncated server tag)
                if candidate and candidate.lower() not in skip:
                    name = candidate
                elif candidate:
                    texts.append(candidate)
            else:
                texts.append(t)
        if name:
            rows.append(RankRow(rank=rank_val, name=name, numbers=numbers,
                                texts=texts, y=rank_cy))

    # Fallback: capture cards whose rank number wasn't OCR'd (low-contrast
    # badges in Arena etc.). Find unclaimed name+points pairs.
    claimed_ys = {r.y for r in rows}
    for cx, cy, t in sorted(blocks, key=lambda b: b[0]):  # left to right
        if cx <= rank_x_max or cx > 700:
            continue
        candidate = re.sub(r"^[\W_]+", "", t)
        candidate = re.sub(r"\s*[sS]?\d{3,4}$", "", candidate).strip()
        candidate = re.sub(r"\s*[sS]$", "", candidate).strip()
        if not candidate or candidate.lower() in skip:
            continue
        if NUM_RE.match(candidate.replace(",", "")):
            continue
        if any(abs(cy - cy2) < card_tol for cy2 in claimed_ys):
            continue
        # Skip the "My Rank: Unranked" slot — "Unranked" appears in the rank
        # badge column (x <= rank_x_max) next to the viewer's own name.
        if any(bt.lower() == "unranked" and bcx <= rank_x_max and abs(bcy - cy) < card_tol
               for bcx, bcy, bt in blocks):
            continue
        nearby_pts = [(pcx, pcy, pval) for pcx, pcy, pval in point_blocks
                      if abs(pcy - cy) < card_tol]
        # Find rank from column scan: nearest hit within card_tol
        rank_val = None
        for sy, rv in column_ranks.items():
            if abs(sy - cy) < card_tol:
                rank_val = rv
                break
        # Points are required as a "real card" signal — UNLESS this is a
        # points-free mode (Supreme Arena) where the column rank confirms the card.
        if not nearby_pts and rank_val is None:
            continue
        if not nearby_pts and point_blocks:
            continue
        val = max(nearby_pts, key=lambda p: p[0])[2] if nearby_pts else None
        # Collect x>700 non-numeric text blocks (e.g. "6934M" score in Dream Realm)
        extra_texts = [t for ecx, ecy, t in blocks
                       if ecx > 700 and abs(ecy - cy) < card_tol
                       and not NUM_RE.match(t.replace(",", "").strip())]
        rows.append(RankRow(rank=rank_val, name=candidate,
                            numbers=[val] if val else [], texts=extra_texts, y=cy))
        claimed_ys.add(cy)

    rows.sort(key=lambda r: r.y)
    return rows


_clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4))


def _scan_rank_column(img, y_min: int = 0) -> list[tuple[int, int]]:
    """OCR the entire rank-number column in one pass. Returns [(screen_y, rank)]
    sorted top-to-bottom. x=40-150 color + PP-OCRv5 reads all ranks including
    the calligraphic single-digit glyphs that PP-OCRv3/grayscale miss.
    Falls back to grayscale+CLAHE if v5 unavailable."""
    h, w = img.shape[:2]
    # x=40-150: rank 7's glyph sits at the left edge — 40 vs 50 captures it
    strip_color = img[y_min:h, 40:min(w, 150)]
    strip_gray  = img[y_min:h, 50:min(w, 130)]
    if strip_color.size == 0:
        return []

    color3 = cv2.resize(strip_color, (0, 0), fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    gray   = cv2.cvtColor(strip_gray, cv2.COLOR_BGR2GRAY)
    gray3  = cv2.resize(gray, (0, 0), fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    clahe3 = _clahe.apply(gray3)

    seen_ys: set[int] = set()
    hits: list[tuple[int, int]] = []

    v5 = get_engine_v5()
    engines = [(v5, color3)] if v5 else [(None, gray3), (None, clahe3)]

    for eng, variant in engines:
        result = eng(variant) if eng else None
        if eng:
            # PP-OCRv5 returns a result object with .boxes/.txts
            boxes = result.boxes if result and hasattr(result, 'boxes') else []
            txts = result.txts if result and hasattr(result, 'txts') else []
            pairs = [(boxes[i], txts[i]) for i in range(len(txts))
                     if i < len(boxes) and boxes[i] is not None]
        else:
            raw, _ = engine(variant)
            pairs = [(box, text) for box, text, _ in (raw or [])]

        for box, text in pairs:
            t = re.sub(r"\D", "", text.strip())
            if not t or not RANK_RE.match(t):
                continue
            cy_in_strip = int(box[0][1])
            screen_y = y_min + cy_in_strip // 3
            if not any(abs(screen_y - sy) < 60 for sy in seen_ys):
                seen_ys.add(screen_y)
                hits.append((screen_y, int(t)))

    hits.sort(key=lambda h: h[0])
    return hits


def detect_tiers(img, mode: str, threshold: float = 0.85) -> list[tuple[int, str]]:
    """Per-row tier icons: match every templates/{mode}/tier_*.png against the
    frame and return (cy, tier_name) hits. Returns [] until the tier templates
    are captured · scans still work, tier just stays NULL."""
    out: list[tuple[int, str]] = []
    tier_dir = TEMPLATES_DIR / mode
    if not tier_dir.is_dir():
        return out
    for path in sorted(tier_dir.glob("tier_*.png")):
        tier_name = path.stem.removeprefix("tier_")
        for _, cy in find_template_all(img, path, threshold=threshold):
            out.append((cy, tier_name))
    return out


def tier_for_row(row_y: int, tiers: list[tuple[int, str]], tol: int = 124) -> str | None:
    best, best_d = None, tol + 1
    for cy, name in tiers:
        d = abs(cy - row_y)
        if d < best_d:
            best, best_d = name, d
    return best


def _warband_skip_set() -> set[str]:
    """Lowercase warband names from DB · added to SKIP_LABELS so warbands never
    become phantom member names when a player's name is missed by OCR."""
    try:
        from db import _connect
        with _connect() as conn:
            rows = conn.execute("SELECT name FROM warbands").fetchall()
        return {r["name"].lower() for r in rows}
    except Exception:
        return set()


def scroll_scan(parse_fn, label: str, max_scrolls: int = MAX_SCROLLS,
                reentry_fn=None) -> list[dict]:
    """Open-ended scroll pass for ranked lists. parse_fn(img, ocr_results) ->
    list[dict] each with at least 'name'. Dedup across frames by fuzzy name.

    reentry_fn: optional callable that re-opens and re-filters the ranking list
    (back one screen → Rankings → filter). Called once after the first pass if
    any entries were found, to catch top-card OCR misses (e.g. gold-styled rank 1)."""
    seen_lower: set[str] = set()
    entries: list[dict] = []

    def _one_pass():
        no_change = 0
        no_new = 0      # consecutive frames with 0 new entries
        scrolls = 0
        prev_img = screenshot()
        new_this_pass = 0
        while scrolls < max_scrolls:
            img = screenshot()
            new = 0
            for e in parse_fn(img, ocr_image(img)):
                key = fuzzy_key(e["name"], seen_lower)
                if key is None:
                    seen_lower.add(e["name"].lower())
                    entries.append(e)
                    new += 1
                else:
                    for existing in entries:
                        if existing["name"].lower() == key:
                            for k, v in e.items():
                                if v is not None and existing.get(k) is None:
                                    existing[k] = v
                            break
                    new_this_pass += 1
            logging.info("%s: scan frame · %d new, %d total.", label, new, len(entries))
            # Stop if 5 consecutive frames produce nothing new — handles screens
            # with animated elements (timers) that prevent screen_changed from
            # detecting the end of the list.
            if new == 0:
                no_new += 1
                if no_new >= 5:
                    break
            else:
                no_new = 0
            scroll_down()
            scrolls += 1
            time.sleep(1.2)
            curr_img = screenshot()
            if not screen_changed(prev_img, curr_img):
                no_change += 1
                if no_change >= 2:
                    break
            else:
                no_change = 0
            prev_img = curr_img
        if scrolls >= max_scrolls:
            logging.warning("%s: hit scroll limit (%d).", label, max_scrolls)
        return new_this_pass

    _one_pass()

    if reentry_fn and entries:
        ok = reentry_fn()
        if ok is not False:
            logging.info("%s: starting second pass to fill OCR gaps.", label)
            new_pass2 = _one_pass()
            if new_pass2:
                logging.info("%s: pass 2 found %d additional entries.", label, new_pass2)

    return entries


def make_reentry_fn(mode_label: str):
    """Standard second-pass reentry for modes using rankings_btn.
    Navigates back one screen, re-taps rankings, re-applies guild filter."""
    def reentry() -> bool:
        from nav import press_back, _wait_until_stable, find_template, _t, apply_guild_filter
        from device import tap, screenshot
        press_back()
        _wait_until_stable()
        pos = find_template(screenshot(), _t("rankings_btn"), threshold=0.75)
        if not pos:
            print(f"{mode_label}: rankings_btn not found on reentry, skipping pass 2.")
            return False
        tap(*pos)
        _wait_until_stable()
        if not apply_guild_filter():
            print(f"{mode_label}: reentry filter failed, skipping pass 2.")
            return False
        return True
    return reentry


def resolve_entries(entries: list[dict], label: str) -> list[dict]:
    """Map each entry's OCR name to a member_id; unmatched entries are dropped
    (membership belongs to the roster scan) and surfaced via REVIEW_NAMES."""
    resolved, unmatched = resolve_names([e["name"] for e in entries])
    if unmatched:
        print(f"REVIEW_NAMES: {', '.join(unmatched)}")
    rows = []
    for e in entries:
        if e["name"] in resolved:
            rows.append({**e, "member_id": resolved[e["name"]]})
        else:
            print(f"  {label}: skipped unmatched '{e['name']}'")
    return rows
