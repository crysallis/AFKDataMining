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
from device import screenshot, scroll_down, screen_changed, mark_progress, tap, back
from nav import TEMPLATES_DIR, find_template_all
from ocr import ocr_image, block_center, engine, get_engine_v5
from db import (resolve_names, names_for_ids, RosterResolver, identity_key,
                member_by_ingame_id, members_missing_ingame_id, set_ingame_id,
                save_correction)

# "User ID: 18939236" in the profile popup. \D* skips the label/colon so the
# captured digits are the ID, not the "Server: S282" number elsewhere on screen.
# Shared with capture_ids.py's guild-list ID capture pass.
USER_ID_RE = re.compile(r"User\s*ID\D*(\d+)", re.IGNORECASE)


def read_user_id(img) -> int | None:
    for _, text, _ in ocr_image(img):
        m = USER_ID_RE.search(text)
        if m:
            return int(m.group(1))
    return None

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

    # Template-match the ornate top-3 badges for any rank OCR missed.
    # Only fires when a rank is absent from _seen_ranks; ranks 2/3 are
    # reliably found by OCR so in practice this only activates for rank #1
    # whose gold crown glyph is consistently unreadable (tested: 1/20 via OCR).
    for _rv in (1, 2, 3):
        if _rv in _seen_ranks:
            continue
        _tmpl = TEMPLATES_DIR / f"rank_{_rv}_badge.png"
        if not _tmpl.exists():
            continue
        for _, _by in find_template_all(img, _tmpl, threshold=0.75):
            if _by >= col_y_min:
                _seen_ranks.add(_rv)
                column_ranks[_by] = _rv
                break

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
                # A merged server tag ("Kits282") is left untouched here —
                # RosterResolver.resolve() tries the raw name first and only
                # falls back to stripping a trailing tag-shaped suffix if the
                # raw name doesn't resolve, so real names ending in digits/s
                # ("Scorpion175", "Crysallis") aren't mangled before matching.
                candidate = re.sub(r"^[\W_]+", "", t).strip()
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
        # A merged server tag ("Kits282") is left untouched here —
        # RosterResolver.resolve() tries the raw name first and only falls
        # back to stripping a trailing tag-shaped suffix if the raw name
        # doesn't resolve, so real names ending in digits/s ("Scorpion175",
        # "Crysallis") aren't mangled before they ever reach the resolver.
        candidate = re.sub(r"^[\W_]+", "", t).strip()
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
        # Collect non-numeric text blocks near the row: right-side info (e.g.
        # "6934M" score in Dream Realm, x>700) as well as a second text line
        # under the name itself (e.g. Guild Duel's per-row guild tag, which
        # sits in the name's own x-range one line below it, ~56-57px down on
        # a 1080x1920 capture). Excludes the name's own block by coordinates.
        extra_texts = [t for ecx, ecy, t in blocks
                       if ecx > rank_x_max and (ecx, ecy) != (cx, cy)
                       and abs(ecy - cy) < card_tol
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
        for _, cy in find_template_all(img, path, threshold=threshold, hue_tol=10):
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
    """Consolidate raw frame observations into one entry per player, resolve-then-vote:
    each observation's name is resolved to a canonical identity FIRST, then grouped by
    that identity, so a name garbled or truncated in one frame is outvoted by the
    frames that read it cleanly. Numeric fields (rank, points, ...) are voted per
    identity — grouping by identity (not rank) avoids duplicate entries when rank OCR
    bleeds values from adjacent fields. Unresolved reads group by their normalized core
    and keep a voted raw name (resolve_entries drops them). Drops players seen fewer
    than min_obs times."""
    from collections import Counter
    resolver = RosterResolver(learn=False)

    groups: dict[str, list[dict]] = {}
    resolved_key: dict[str, bool] = {}
    for obs in observations:
        key, resolved = identity_key(resolver, obs['name'])
        groups.setdefault(key, []).append(obs)
        resolved_key[key] = resolved

    result: list[dict] = []
    for key, group in groups.items():
        if len(group) < min_obs:
            logging.debug("%s: '%s' seen %d time(s), dropping.",
                          label, key, len(group))
            continue
        name = key if resolved_key[key] else _vote_name([o['name'] for o in group])
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
                reentry_fn=None, frames_per_stop: int = 2, scroll_fn=None,
                require_new: bool = True, no_change_limit: int = 2,
                on_stop=None) -> list[dict]:
    """Open-ended scroll pass for ranked lists. All raw observations from every
    frame are accumulated then consolidated via majority vote per rank.

    parse_fn(img, ocr_results) -> list[dict] each with at least 'name'.
    frames_per_stop: OCR this many temporally-spread frames at each scroll stop
    before scrolling. >=2 gives the vote redundancy that survives animated rows
    (e.g. the podium flickering the ornate top-1 glyph out of OCR) without a
    second full pass — so the reentry pass is skipped when multi-sampling.
    reentry_fn: re-opens the ranking list for a second full pass; only used when
    frames_per_stop < 2 (single-frame mode), where per-stop redundancy is absent.
    scroll_fn: overrides the default device.scroll_down() swipe -- used by
    screens whose visible list area doesn't match the full Rankings screen
    the default swipe was tuned for (e.g. Guild Duel's Battle Report popup).
    require_new: when False, disables the no-new-keys early stop -- for modes
    whose parse_fn drops rows post-OCR (e.g. Guild Duel filtering out the other
    guild), a stop with zero *kept* rows doesn't mean the list ended, so only
    screen_changed (real scroll progress) is trusted to end the scan.
    no_change_limit: consecutive unchanged-screen stops before ending the scan.
    on_stop(stop_img, frame_entries): optional hook called once per scroll stop,
    right after that stop's frames_per_stop reads are collected and BEFORE
    scrolling to the next stop -- the row is still on screen. Used by the ID
    fallback (see resolve_id_fallback_hook) to tap a row for its permanent
    in-game User ID while it's reachable, without a second pass: reusing the
    scroll that's already happening avoids needing a separate re-navigation
    (which broke Arcane Lab's list by over-scrolling to relocate a row after
    the fact -- see project memory). Every existing caller that doesn't pass
    this is unaffected."""
    all_observations: list[dict] = []

    def _one_pass() -> None:
        seen_keys: set = set()
        no_new = 0
        no_change = 0
        scrolls = 0
        while scrolls < max_scrolls:
            new = 0
            last_img = None
            stop_entries: list[dict] = []
            for f in range(max(1, frames_per_stop)):
                if f:
                    time.sleep(0.4)
                last_img = screenshot()
                frame_entries = parse_fn(last_img, ocr_image(last_img))
                all_observations.extend(frame_entries)
                stop_entries.extend(frame_entries)
                for e in frame_entries:
                    k = e.get('rank') if e.get('rank') is not None else e.get('name', '').lower()
                    if k and k not in seen_keys:
                        seen_keys.add(k)
                        new += 1
            logging.info("%s: stop · %d frame(s), %d new keys, %d obs total.",
                         label, max(1, frames_per_stop), new, len(all_observations))
            if new:
                mark_progress()
            if on_stop is not None and stop_entries:
                on_stop(last_img, stop_entries)
            if require_new:
                if new == 0:
                    no_new += 1
                    if no_new >= 5:
                        break
                else:
                    no_new = 0
            (scroll_fn or scroll_down)()
            scrolls += 1
            time.sleep(1.2)
            curr_img = screenshot()
            if not screen_changed(last_img, curr_img):
                no_change += 1
                if no_change >= no_change_limit:
                    break
            else:
                no_change = 0
                mark_progress()
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


RANK_CARD_AVATAR_X = 215          # avatar tap -- measured live against Arcane Lab's
RANK_CARD_AVATAR_Y_OFFSET = 12    # Sandokai row (2026-08-24): name-block anchor at
                                   # (414,783), avatar portrait center at ~(215,795).
                                   # Original placeholder (120, 55) was confirmed
                                   # wrong live ("way too high") -- cards share the
                                   # same basic size across ranking modes, so this
                                   # single measured constant should cover all of
                                   # them, but still worth confirming on another mode.


def make_id_fallback_hook(label: str, avatar_x: int = RANK_CARD_AVATAR_X,
                          avatar_y_offset: int = RANK_CARD_AVATAR_Y_OFFSET):
    """Builds an on_stop hook (pass to scroll_scan) that taps a row for its
    permanent in-game User ID while it's still on screen -- once per row, right
    after that stop's frames_per_stop reads settle a name for it, before
    scrolling on. Two cases trigger a tap: the row's name doesn't resolve to
    any known member, or it does but that member has no ingame_id on file yet
    (opportunistic backfill).

    avatar_x/avatar_y_offset default to the ranking-card constants (measured
    live against Arcane Lab) -- pass capture_ids.py's guild-list AVATAR_X/
    AVATAR_Y_OFFSET (120/55) when wiring this into the guild scan, since that
    card layout is different and already tuned/proven in production there.

    On the guild scan specifically, a genuinely brand-new (never-seen) member
    will ALSO fail the ID lookup below (they're not in members yet either
    way) and correctly fall through to today's pending-member flow -- there's
    no cheap way to tell "new member" from "existing member, badly garbled
    name" before tapping, so every unmatched name gets one tap attempt; a
    real new member just costs a few wasted seconds confirming that.

    A resolved-by-ID name is taught back via save_correction(), NOT by
    injecting member_id into the entry dict -- _vote_entries/resolve_entries
    group observations by fuzzy name text, with no member_id concept at all,
    so there's no clean seam to inject a resolved identity downstream.
    save_correction() writes the OCR-name -> canonical-name mapping into
    name_corrections, which RosterResolver checks first (before exact/fuzzy);
    since _vote_entries builds its OWN resolver at the very end of this same
    scroll_scan call, a correction saved mid-scroll is picked up automatically
    by this run's own final vote, with zero special-casing needed.

    For guild-filtered ranking modes and the guild scan itself (every row on
    screen is already a confirmed RiffRaff member or a legitimate roster
    candidate) -- NOT Guild Duel, whose unfiltered ~180-row mixed-guild list
    would mean chasing far more other-guild misses than this is worth; that
    mode keeps plain resolve_entries() with no hook.

    Deliberately reuses the scroll already happening in scroll_scan instead of
    a separate follow-up pass: an earlier version did a second pass with its
    own re-navigation, which broke Arcane Lab live (over-scrolling to relocate
    a row collapsed its list, stranding the target off-screen mid-pass) -- see
    project memory. Tapping in-place while the row is already visible sidesteps
    that whole class of problem."""
    tapped: set[str] = set()

    def on_stop(stop_img, stop_frame_entries: list[dict]) -> None:
        seen_names = {e["name"] for e in stop_frame_entries}
        # resolve_names() is the same lookup resolve_entries() uses at the end
        # of the scan -- checking match status here (not via a separate
        # resolver) keeps "does this OCR name match a known member" consistent
        # with what the final result will actually show.
        resolved_map, _ = resolve_names(list(seen_names))

        for ocr_name in seen_names:
            if ocr_name in tapped:
                continue
            member_id = resolved_map.get(ocr_name)
            if member_id is not None and not members_missing_ingame_id([member_id]):
                continue  # already matched and already has an ID -- nothing to do
            tapped.add(ocr_name)

            row_y = None
            for box, text, _conf in ocr_image(stop_img):
                cx, cy = block_center(box)
                if SequenceMatcher(None, text.strip().lower(), ocr_name.lower()).ratio() >= 0.88:
                    row_y = cy
                    break
            if row_y is None:
                continue

            tap(avatar_x, row_y + avatar_y_offset)
            time.sleep(1.5)
            user_id = read_user_id(screenshot())
            back()
            time.sleep(1.0)
            mark_progress()  # real device work happened, regardless of outcome below --
                              # capture_ingame_ids() had this exact gap (300s watchdog
                              # fired mid-capture, confirmed live 2026-08-24) since it
                              # never called mark_progress() from inside its tap loop
            if user_id is None:
                print(f"  {label}: '{ocr_name}' User ID not read, leaving as-is.")
                continue

            if member_id is None:
                by_id = member_by_ingame_id(user_id)
                if by_id is None:
                    print(f"  {label}: '{ocr_name}' ID {user_id} matches no known member.")
                    continue
                canonical_name = names_for_ids([by_id]).get(by_id, ocr_name)
                if canonical_name != ocr_name:
                    save_correction(ocr_name, canonical_name, source="ingame_id")
                print(f"  {label}: '{ocr_name}' resolved via ID {user_id} -> {canonical_name}.")
            else:
                set_ingame_id(member_id, user_id)
                print(f"  {label}: '{ocr_name}' ID {user_id} stored.")

    return on_stop


def make_verified_scroll_down(max_retries: int = 10):
    """Builds a scroll_fn (pass to scroll_scan) that re-swipes if the list
    didn't actually advance, instead of trusting a single fixed-distance
    scroll_down() to always move the list by the same amount.

    Built for make_drill_in_hook's use case: confirmed live 2026-09-08
    (Daniel, across several ranking modes, not just Pearl Bazaar) that
    backing out of a row drilled in far enough down the list snaps the list
    back up slightly -- the deeper into the list, the more this snap-back
    eats into scroll_down()'s fixed ~620px throw, until net progress per
    scroll approaches zero. A live Pearl Bazaar run showed this exactly:
    obs total climbed steadily every stop (OCR was reading fine) while new
    keys sat at 0 for 5+ consecutive stops -- the same already-seen rows
    were being re-read because the list had barely moved, not because
    anything had broken.

    max_retries defaults high (10, not 3) because Daniel confirmed live
    the snap-back itself grows with depth -- "you'll have to scroll a lot
    each time and more the farther you get in the list" -- so a low fixed
    cap would work near the top of a ~100-row list and then silently run
    out exactly where the problem is worst. A retry that gives up early
    looks identical to one that never ran: both leave new keys at 0.

    Verifies progress via the MAXIMUM rank visible in _scan_rank_column's
    raw output, not the topmost -- the pinned top-3 podium badges are
    always present and always topmost, so a topmost-based check would
    never detect real movement. Max rank strictly increases as the list
    scrolls forward and is podium-immune with no exclusion zone needed."""
    def scroll_fn() -> None:
        before = screenshot()
        before_max = max((rv for _sy, rv in _scan_rank_column(before)), default=0)

        for _attempt in range(max_retries):
            scroll_down()
            time.sleep(1.2)
            after = screenshot()
            after_max = max((rv for _sy, rv in _scan_rank_column(after)), default=0)
            if after_max > before_max:
                return
            logging.warning("verified_scroll: no rank progress (%d -> %d), retrying.",
                            before_max, after_max)
            before_max = after_max

        logging.warning("verified_scroll: exhausted %d retries with no rank progress "
                        "(stuck at %d).", max_retries, before_max)

    return scroll_fn


def make_drill_in_hook(row_callback, avatar_x: int = RANK_CARD_AVATAR_X,
                       avatar_y_offset: int = RANK_CARD_AVATAR_Y_OFFSET):
    """Builds an on_stop hook (pass to scroll_scan) that taps EVERY row's
    banner while it's still on screen, reads that row's permanent in-game
    User ID from the profile popup, then hands off to row_callback(rank,
    user_id) for any further mode-specific drill-in (e.g. Pearl Bazaar
    tapping a second icon) before backing out.

    Unlike make_id_fallback_hook, this fires unconditionally for every row
    (no roster-match short-circuit) since it's meant for unfiltered,
    unguilded lists where there's no resolved member name to check against.
    row_callback owns everything past reading the User ID -- this hook only
    guarantees the tap/OCR sequence for the profile step and that
    row_callback runs exactly once per row while it's on screen, honoring
    the same reuse-the-scroll-in-progress approach that avoided Arcane
    Lab's over-scroll bug (see make_id_fallback_hook's docstring / project
    memory) -- no second pass, no re-navigation.

    row_callback is responsible for its own further taps/backs (e.g. Pearl
    icon -> price screen -> back) and must leave the screen back at the
    rank list before returning, matching this hook's own contract to
    scroll_scan.

    Uses each entry's own 'y' (as returned by parse_rank_rows, already
    podium-excluded via its list_y_min guard) only for the FIRST tap of a
    stop. That re-derivation approach was tried first and confirmed BROKEN
    live 2026-09-08: the podium header (top-3 hero cards) is pinned on
    screen and never scrolls out of frame, so its warband-tag/name text
    blocks are present on every single stop -- a fuzzy match against a low
    rank's name (itself sometimes wrongly parsed as a warband tag by
    parse_rank_rows -- a separate, pre-existing bug in that parser, not
    fixed here) could land on the podium's copy of that text instead of the
    real list row, silently tapping the same wrong profile for every
    affected rank. Trusting the parser's own y instead of a name-based
    lookup sidesteps that whole class of problem.

    But 'y' still goes stale mid-stop for every row after the first: this
    game's rank list snaps back up a bit whenever you back out of a row
    drilled in deep enough down the list -- confirmed live 2026-09-08
    against Pearl Bazaar and other ranking modes alike, not something a
    single wait can settle out of. A stop with multiple new rows (common at
    frames_per_stop=2) would tap the 2nd+ rows at coordinates that no
    longer match their real on-screen position once the 1st row's drill-in
    has run, producing exactly the corruption seen live: rank 24's DB row
    carrying rank 7's real price because the tap for "rank 24" landed on
    row 7's now-shifted card. Fixed by re-scanning the rank column fresh
    (_scan_rank_column) after every row_callback returns, using that live
    reading -- not the original stop's stale batch -- to find each
    subsequent row's real y. A rank that can't be found on the current
    screen (scrolled off after the snap-back) is skipped, not guessed at.

    Re-scanning with _scan_rank_column alone isn't enough: unlike
    parse_rank_rows, it has no podium exclusion or per-rank dedup of its
    own (its only filter is y-proximity, not rank value) -- a decorative
    top-1/2/3 badge sharing a rank number with a real list row further down
    would silently overwrite it in a naive {rank: y} dict. _rescan_ranks
    below reapplies both: a podium floor derived from the lowest y already
    known-good in this stop (parse_rank_rows already excluded the podium
    from every 'y' it handed us), and keep-topmost dedup, matching
    parse_rank_rows's own approach line for line."""
    tapped_ranks: set[int] = set()

    def _rescan_ranks(img, floor_y: int) -> dict[int, int]:
        by_rank: dict[int, int] = {}
        for sy, rv in sorted(_scan_rank_column(img, y_min=0)):
            if sy < floor_y:
                continue
            if rv not in by_rank:
                by_rank[rv] = sy
        return by_rank

    def on_stop(stop_img, stop_frame_entries: list[dict]) -> None:
        live_ranks = {entry['rank']: entry['y'] for entry in stop_frame_entries
                     if entry.get('rank') is not None and entry.get('y') is not None}
        pending = sorted(r for r in live_ranks if r not in tapped_ranks)
        if not pending:
            return
        floor_y = min(live_ranks.values()) - 40

        for rank in pending:
            if rank in tapped_ranks:
                continue
            row_y = live_ranks.get(rank)
            if row_y is None:
                continue
            tapped_ranks.add(rank)

            tap(avatar_x, row_y + avatar_y_offset)
            time.sleep(1.5)
            user_id = read_user_id(screenshot())
            mark_progress()  # real device work happened regardless of outcome

            row_callback(rank, user_id)

            live_ranks = _rescan_ranks(screenshot(), floor_y)

    return on_stop
