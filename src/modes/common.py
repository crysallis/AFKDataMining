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
from db import resolve_names, names_for_ids

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
                    extra_skip: set[str] | None = None,
                    list_y_min: int = 0,
                    value_re: "re.Pattern | None" = None) -> list[RankRow]:
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

    # Auto-detect the winner podium: the top-3 are drawn as heroes side-by-side,
    # so their name (and guild-tag) blocks share a y-band but spread wide across
    # x · list rows instead stack name + guild tag at the same x. The podium's
    # decorative 1/2/3 badges otherwise steal ranks 1-3 from the real list. Start
    # the parse just below the lowest such band. (Combined with any caller hint.)
    # Only the upper screen · the footer tabs ('Server/District Rankings') are
    # also a wide band, but at the bottom; including them would clip the list.
    _podium_zone = img.shape[0] * 0.45
    _name_like = [(cx, cy) for cx, cy, t in blocks
                  if cx > rank_x_max and cy < _podium_zone
                  and not NUM_RE.match(t.replace(",", "")) and t.lower() not in skip]
    _podium_y = 0
    for cx, cy in _name_like:
        band_xs = [ox for ox, oy in _name_like if abs(oy - cy) <= 50]
        if len(band_xs) >= 2 and (max(band_xs) - min(band_xs)) > 350:
            _podium_y = max(_podium_y, cy)
    if _podium_y:
        list_y_min = max(list_y_min, _podium_y + 40)

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
    # Caller can force the list to start below a fixed header (e.g. Dream Realm's
    # winner podium + date bar), so the top-3 podium can't steal ranks 1-3.
    col_y_min = max(col_y_min, list_y_min)
    # Scan the FULL rank strip (from the top) for stable glyph detection, then
    # exclude the podium by filtering on y in Python. Cropping the image at
    # col_y_min shifts the 3x resize grid and can make a borderline ornate glyph
    # vanish at certain offsets — the gold top-1 badge read fine at y_min 0/600/700
    # but disappeared at exactly 614, silently dropping the #1 player's rank.
    # Deduplicate by rank value: keep only the topmost (lowest y) occurrence of
    # each rank number. "31" misread as "3" would otherwise assign rank 3 to a
    # member whose actual rank is 31.
    _seen_ranks: set[int] = set()
    column_ranks: dict[int, int] = {}
    for sy, rv in _scan_rank_column(img, y_min=0):
        if sy < col_y_min:
            continue
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
        if cy < list_y_min:  # above the list (podium / header) — never a member row
            continue
        candidate = re.sub(r"^[\W_]+", "", t)
        candidate = re.sub(r"\s*[sS]?\d{3,4}$", "", candidate).strip()
        candidate = re.sub(r"\s*[sS]$", "", candidate).strip()
        # Single-character reads ('市', '金') are OCR noise from UI bleed, never
        # real player names · drop them so they don't reach REVIEW_NAMES.
        if not candidate or len(candidate) <= 1 or candidate.lower() in skip:
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
        # A row is a real card if it has numeric points, a rank badge from the
        # column scan, OR (when value_re is given) a right-side value matching it.
        # AFK Stages always shows a Phase Progress value ('Apex N' text or a
        # number), so value_re makes that the card signal even if the rank read
        # misses · the rank fallback also covers points-free modes (Supreme Arena).
        value_hit = bool(value_re) and any(
            ecx > 700 and abs(ecy - cy) < card_tol and value_re.search(t)
            for ecx, ecy, t in blocks)
        if not nearby_pts and rank_val is None and not value_hit:
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


def _vote_name(names: list[str]) -> str:
    """Cluster name variants by fuzzy similarity; return the most common spelling
    in the largest cluster."""
    from collections import Counter
    clusters: list[list[str]] = []
    for name in names:
        for cluster in clusters:
            if any(SequenceMatcher(None, name.lower(), c.lower()).ratio() >= 0.75
                   for c in cluster):
                cluster.append(name)
                break
        else:
            clusters.append([name])
    if not clusters:
        return ''
    largest = max(clusters, key=len)
    return Counter(largest).most_common(1)[0][0]


def _vote_entries(observations: list[dict], label: str,
                  min_obs: int = 1) -> list[dict]:
    """Consolidate raw frame observations into one entry per player via majority
    vote. Groups by name cluster first — rank OCR can bleed values from adjacent
    numeric fields (e.g. difficulty in Arcane Lab), so grouping by rank first
    would create duplicate entries for the same player. Name is more stable.
    Drops players seen fewer than min_obs times."""
    from collections import Counter

    name_groups: list[list[dict]] = []
    for obs in observations:
        for group in name_groups:
            if any(SequenceMatcher(None, obs['name'].lower(), o['name'].lower()).ratio() >= 0.75
                   for o in group):
                group.append(obs)
                break
        else:
            name_groups.append([obs])

    result: list[dict] = []
    for group in name_groups:
        if len(group) < min_obs:
            logging.debug("%s: '%s' seen %d time(s), dropping.",
                          label, group[0]['name'], len(group))
            continue
        name = _vote_name([o['name'] for o in group])
        if not name:
            continue
        entry: dict = {'name': name}
        for field in group[0]:
            if field == 'name':
                continue
            values = [o[field] for o in group if o.get(field) is not None]
            entry[field] = Counter(values).most_common(1)[0][0] if values else None
        result.append(entry)

    result.sort(key=lambda r: (r.get('rank') is None, r.get('rank') or 0))
    logging.info("%s: voted %d obs → %d entries.", label, len(observations), len(result))
    return result


def scroll_scan(parse_fn, label: str, max_scrolls: int = MAX_SCROLLS,
                reentry_fn=None, frames_per_stop: int = 2) -> list[dict]:
    """Open-ended scroll pass for ranked lists. All raw observations from every
    frame are accumulated then consolidated via majority vote per rank.

    parse_fn(img, ocr_results) -> list[dict] each with at least 'name'.
    frames_per_stop: OCR this many temporally-spread frames at each scroll stop
    before scrolling. >=2 gives the vote redundancy that survives animated rows
    (e.g. the podium flickering the ornate top-1 glyph out of OCR) without a
    second full pass — so the reentry pass is skipped when multi-sampling.
    reentry_fn: re-opens the ranking list for a second full pass; only used when
    frames_per_stop < 2 (single-frame mode), where per-stop redundancy is absent."""
    all_observations: list[dict] = []

    def _one_pass() -> None:
        seen_keys: set = set()
        no_new = 0
        no_change = 0
        scrolls = 0
        while scrolls < max_scrolls:
            new = 0
            last_img = None
            for f in range(max(1, frames_per_stop)):
                if f:
                    time.sleep(0.4)
                last_img = screenshot()
                frame_entries = parse_fn(last_img, ocr_image(last_img))
                all_observations.extend(frame_entries)
                for e in frame_entries:
                    k = e.get('rank') if e.get('rank') is not None else e.get('name', '').lower()
                    if k and k not in seen_keys:
                        seen_keys.add(k)
                        new += 1
            logging.info("%s: stop · %d frame(s), %d new keys, %d obs total.",
                         label, max(1, frames_per_stop), new, len(all_observations))
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
            if not screen_changed(last_img, curr_img):
                no_change += 1
                if no_change >= 2:
                    break
            else:
                no_change = 0
        if scrolls >= max_scrolls:
            logging.warning("%s: hit scroll limit (%d).", label, max_scrolls)

    _one_pass()

    if reentry_fn and frames_per_stop < 2 and all_observations:
        ok = reentry_fn()
        if ok is not False:
            logging.info("%s: second pass for more observations.", label)
            _one_pass()

    return _vote_entries(all_observations, label)


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
    canonical = names_for_ids([r["member_id"] for r in rows])
    matched = sorted(rows, key=lambda r: (r.get("rank") is None, r.get("rank") or 0))

    def _fmt(r: dict) -> str:
        name = canonical.get(r["member_id"], r["name"])
        tag = f" (read: {r['name']})" if r["name"] != name else ""
        return f"#{r.get('rank') or '?'} {name}{tag}"

    logging.info("%s: matched %d/%d entries: %s", label, len(rows), len(entries),
                 ", ".join(_fmt(r) for r in matched))
    return rows
