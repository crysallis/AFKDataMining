"""Navigation helpers for AFK Journey guild scraping.

Resilience model:
  - Every tap that should change screens goes through `_tap_to_reach`, which
    re-taps (up to N times) until the expected screen appears, detects taps that
    didn't register (screen unchanged), and dismisses blocking popups first.
  - `navigate_to_guild_members` retries the whole path, re-homing between tries,
    so a transient stall or a popup self-corrects instead of aborting the scan.
  - Template match scores are logged (DEBUG) so we can see how confident — or
    marginal — each identification is.
"""
import logging
import time
from difflib import SequenceMatcher
from pathlib import Path

import cv2
import numpy as np

from device import screenshot, tap, back, screen_changed

TEMPLATES_DIR = Path(__file__).parent / "templates"

# Hardcoded fallback coordinates (1080×1920), used when a template PNG is missing
# or below threshold.
_GUILD_BTN_XY = (780, 1830)
_GUILD_BANNER_XY = (120, 57)

# Templates that, if visible, mean a modal/popup is blocking a forward tap.
# `popup_cancel` (the "Exit game?" No/stay button) doubles as the root landmark
# in navigate_home. Add more here (captured via capture_template.py) as needed.
_POPUP_DISMISS = ("popup_cancel",)

_TEMPLATE_CACHE: dict[str, np.ndarray | None] = {}


def _t(name: str) -> Path:
    return TEMPLATES_DIR / f"{name}.png"


def _load_template(template_path: Path) -> np.ndarray | None:
    """Load (and cache) a template PNG. Warns once if it's missing."""
    key = str(template_path)
    if key not in _TEMPLATE_CACHE:
        img = cv2.imread(key)
        if img is None:
            logging.warning("Template not found: %s", template_path.name)
        _TEMPLATE_CACHE[key] = img
    return _TEMPLATE_CACHE[key]


def find_template(
    screen: np.ndarray,
    template_path: Path,
    threshold: float = 0.80,
) -> tuple[int, int] | None:
    """Return (cx, cy) of best template match, or None if below threshold.
    Logs the match score (DEBUG) so confidence is visible in scraper.log."""
    tmpl = _load_template(template_path)
    if tmpl is None:
        return None
    result = cv2.matchTemplate(screen, tmpl, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val >= threshold:
        h, w = tmpl.shape[:2]
        center = (max_loc[0] + w // 2, max_loc[1] + h // 2)
        logging.debug("match %-22s %.3f / %.2f HIT at (%d,%d)",
                      template_path.stem, max_val, threshold, *center)
        return center
    logging.debug("match %-22s %.3f / %.2f", template_path.stem, max_val, threshold)
    return None


def find_template_all(
    screen: np.ndarray,
    template_path: Path,
    threshold: float = 0.85,
    max_matches: int = 50,
) -> list[tuple[int, int]]:
    """All (cx, cy) matches above threshold, greedy peak-picking with the
    template's own footprint suppressed after each hit so one icon doesn't
    report a cloud of near-duplicate centers. Used for per-row tier icons."""
    tmpl = _load_template(template_path)
    if tmpl is None:
        return []
    result = cv2.matchTemplate(screen, tmpl, cv2.TM_CCOEFF_NORMED)
    h, w = tmpl.shape[:2]
    out: list[tuple[int, int]] = []
    while len(out) < max_matches:
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val < threshold:
            break
        out.append((max_loc[0] + w // 2, max_loc[1] + h // 2))
        y0, y1 = max(0, max_loc[1] - h // 2), max_loc[1] + h // 2 + 1
        x0, x1 = max(0, max_loc[0] - w // 2), max_loc[0] + w // 2 + 1
        result[y0:y1, x0:x1] = -1.0
    return out


def press_back(sleep: float = 0.8) -> None:
    back()
    time.sleep(sleep)


def _wait_until_stable(timeout: float = 4.0, poll: float = 0.4) -> np.ndarray:
    """Poll screenshots until the screen stops changing (load animation settles),
    or `timeout` elapses. Returns the last screenshot. Replaces fixed sleeps so
    variable load times don't cause false 'not found' results."""
    prev = screenshot()
    waited = 0.0
    while waited < timeout:
        time.sleep(poll)
        waited += poll
        cur = screenshot()
        if not screen_changed(prev, cur):
            return cur
        prev = cur
    return prev


def _find_exit_popup(screen: np.ndarray) -> tuple[int, int] | None:
    """Locate the 'Exit game?' dialog's dismiss (No/stay) button.

    This dialog is our reliable 'at the root / main screen' landmark: it only
    appears when Back is pressed at a top-level screen, and it matches ~0.99 —
    far more reliable than the overview joystick (marginal, and also shown on
    other screens like guild home, so it can't identify the overview anyway)."""
    return find_template(screen, _t("popup_cancel"), threshold=0.80)


def _is_at_guild_home(screen: np.ndarray) -> bool:
    """Guild home screen: look for the Admin button (top-right)."""
    return find_template(screen, _t("guild_home_indicator"), threshold=0.80) is not None


def _is_at_guild_members(screen: np.ndarray) -> bool:
    """Guild members list: look for an element unique to that screen."""
    return find_template(screen, _t("guild_members_indicator"), threshold=0.80) is not None


def _tap_ui_back(screen: np.ndarray) -> bool:
    """Tap an on-screen back button if one is visible. Returns True if tapped."""
    for name in ("back_dark", "back_light"):
        pos = find_template(screen, _t(name), threshold=0.75)
        if pos:
            tap(*pos)
            logging.debug("Tapped UI back button '%s' at %s.", name, pos)
            return True
    return False


def _dismiss_popup(screen: np.ndarray) -> bool:
    """If a known blocking popup is visible (e.g. 'Exit game?'), dismiss it with
    Back — this game's exit dialog closes on Back, not on a tap on the button.
    Returns True if one was dismissed."""
    for name in _POPUP_DISMISS:
        if find_template(screen, _t(name), threshold=0.80):
            logging.info("Popup '%s' visible — dismissing via Back.", name)
            press_back()
            _wait_until_stable(timeout=2.0)
            return True
    return False


def _tap_to_reach(locate, is_there, label: str, fallback_xy, attempts: int = 5) -> bool:
    """Tap toward a target screen, re-tapping until we get there.

    locate(screen)   -> (x, y) of the button to tap, or None
    is_there(screen) -> True once we've arrived
    Handles: popups (dismiss first), taps that don't register (screen unchanged),
    and slow page loads (waits for the screen to settle after each tap).
    """
    for attempt in range(1, attempts + 1):
        screen = screenshot()
        if is_there(screen):
            return True
        if _dismiss_popup(screen):
            continue  # re-evaluate from a clean screen before tapping
        pos = locate(screen) or fallback_xy
        if pos is None:
            logging.error("%s: no template match and no fallback XY — capture the template first.", label)
            return False
        before = screen
        tap(*pos)
        logging.info("%s: tapped %s (attempt %d/%d).", label, pos, attempt, attempts)
        after = _wait_until_stable()
        if is_there(after):
            logging.info("%s: reached.", label)
            return True
        if not screen_changed(before, after):
            logging.warning("%s: screen didn't change — tap may not have registered, retrying.", label)
        else:
            logging.warning("%s: screen changed but target not reached, retrying.", label)
    logging.error("%s: not reached after %d attempts.", label, attempts)
    return False


def navigate_home(max_attempts: int = 25) -> str | None:
    """Back up to a main screen, using the 'Exit game?' dialog as the root
    landmark instead of the (marginal, ambiguous) overview joystick.

    Each step: if we're already at a guild screen, return it (don't back out).
    Otherwise press Back — and when the Exit dialog appears (we've reached the
    overview), dismiss it (No) and report 'overview'. Returns
    'guild_members' | 'guild_home' | 'overview', or None if exhausted."""
    for attempt in range(max_attempts):
        screen = screenshot()
        if _is_at_guild_members(screen):
            logging.debug("Found guild_members after %d back-press(es).", attempt)
            return "guild_members"
        if _is_at_guild_home(screen):
            logging.debug("Found guild_home after %d back-press(es).", attempt)
            return "guild_home"
        if _find_exit_popup(screen):
            press_back()  # one more Back clears the "Exit game?" dialog → overview
            logging.info("Reached root via Exit dialog after %d back-press(es); cleared with Back → overview.", attempt)
            _wait_until_stable(timeout=2.0)
            return "overview"
        if not _tap_ui_back(screen):
            press_back()

    logging.error("Could not reach a main screen (no Exit dialog seen) after %d attempts.", max_attempts)
    cv2.imwrite(str(TEMPLATES_DIR.parent / "debug_nav_fail.png"), screenshot())
    return None


def navigate_to_guild_members(flow_attempts: int = 3) -> None:
    """Navigate from any screen to the guild member list, retrying the whole
    path (re-homing each time) so a stalled tap or popup self-corrects."""
    logging.info("Navigating to guild members list.")

    for attempt in range(1, flow_attempts + 1):
        where = navigate_home()
        if where is None:
            logging.warning("Home not reached (flow attempt %d/%d).", attempt, flow_attempts)
            continue
        if where == "guild_members":
            logging.info("Already at guild members list.")
            return

        if where == "overview":
            reached_home = _tap_to_reach(
                lambda s: find_template(s, _t("guild_button"), threshold=0.75),
                _is_at_guild_home, "guild_home", _GUILD_BTN_XY,
            )
            if not reached_home:
                logging.warning("Couldn't reach guild home (flow attempt %d), re-homing.", attempt)
                continue

        # At guild home — open the members list via the guild banner.
        if _tap_to_reach(
            lambda s: find_template(s, _t("guild_banner"), threshold=0.75),
            _is_at_guild_members, "guild_members", _GUILD_BANNER_XY,
        ):
            return
        logging.warning("Couldn't reach members list (flow attempt %d), re-homing.", attempt)

    cv2.imwrite(str(TEMPLATES_DIR.parent / "debug_nav_fail.png"), screenshot())
    raise RuntimeError(f"Could not reach guild members list after {flow_attempts} attempts.")


# --- Game-mode navigation (ranking scans) ---
#
# Route for every mode:
#   overview → tap battle_modes_btn (template) → OCR-find mode card label →
#   tap card → tap rankings_btn (template) or afk_stages_trophy (AFK Stage) →
#   arrived at ranking list
#
# Only 5 small icon templates needed (vs. one template per step):
#   battle_modes_btn.png  · bottom-nav diamond icon
#   rankings_btn.png      · Rankings icon (5 modes)
#   afk_stages_trophy.png · trophy icon (AFK Stage only)
#   filter_icon.png       · filter funnel (all modes, no text)
#   dream_realm_arrow_left.png · date-bar left-page arrow (Dream Realm only)

# Human-readable OCR label on the Battle Modes card for each mode.
MODE_CARD_LABEL: dict[str, str] = {
    "dream_realm":   "Dream Realm",
    "afk_stages":    "AFK Stage",
    "arena":         "Arena",
    "supreme_arena": "Supreme Arena",
    "honor_duel":    "Honor Duel",
    "arcane_lab":    "Labyrinth",
}

# All modes use the same rankings circle icon (rankings_btn.png).
_TROPHY_MODES: set[str] = set()


def _ocr_tap_text(
    ocr_fn,
    target: str,
    label: str,
    threshold: float = 0.75,
    attempts: int = 5,
    scroll_between: bool = False,
) -> bool:
    """OCR the screen, find a text block matching target (case-insensitive,
    fuzzy-tolerant), and tap it. Retries up to `attempts` times; optionally
    scrolls down between tries to reveal off-screen labels."""
    from device import scroll_down
    for attempt in range(1, attempts + 1):
        for box, text, _conf in ocr_fn(screenshot()):
            from ocr import block_center
            cx, cy = block_center(box)
            ratio = SequenceMatcher(None, text.strip().lower(), target.lower()).ratio()
            if ratio >= 0.82:
                tap(cx, cy)
                logging.info("%s: tapped '%s' (matched '%s', ratio=%.2f, attempt %d).",
                             label, text.strip(), target, ratio, attempt)
                return True
        if attempt < attempts:
            if scroll_between:
                scroll_down()
                time.sleep(1.0)
            else:
                time.sleep(0.5)
    logging.warning("%s: text '%s' not found after %d attempts.", label, target, attempts)
    return False


def navigate_to_overview(max_attempts: int = 25) -> bool:
    """Back all the way up to the overview, confirmed via the 'Exit game?'
    dialog landmark · mode routes always start from a clean overview."""
    for attempt in range(max_attempts):
        screen = screenshot()
        if _find_exit_popup(screen):
            press_back()
            logging.info("Reached overview after %d back-press(es).", attempt)
            _wait_until_stable(timeout=2.0)
            return True
        if not _tap_ui_back(screen):
            press_back()
    logging.error("Could not reach overview after %d attempts.", max_attempts)
    return False


def _is_at_battle_modes(screen: np.ndarray) -> bool:
    """Battle Modes card grid: look for any known mode card label via OCR
    rather than a template, since the card art changes with seasons."""
    from ocr import ocr_image
    for _, text, _ in ocr_image(screen):
        for label in MODE_CARD_LABEL.values():
            if SequenceMatcher(None, text.strip().lower(), label.lower()).ratio() >= 0.85:
                return True
    return False


def navigate_to_mode(mode: str, flow_attempts: int = 3) -> None:
    """Navigate from any screen to a game mode's ranking list.

    Route:
      overview → battle_modes_btn → OCR card label (scroll if needed) →
      rankings_btn / afk_stages_trophy
    Re-homes and retries the whole path on failure, same as navigate_to_guild_members."""
    from ocr import ocr_image
    card_label = MODE_CARD_LABEL[mode]
    ranking_tmpl = "afk_stages_trophy" if mode in _TROPHY_MODES else "rankings_btn"
    logging.info("Navigating to %s ranking.", mode)

    for attempt in range(1, flow_attempts + 1):
        if not navigate_to_overview():
            logging.warning("Overview not reached (flow attempt %d/%d).", attempt, flow_attempts)
            continue

        # overview → Battle Modes
        if not _tap_to_reach(
            lambda s: find_template(s, _t("battle_modes_btn"), threshold=0.75),
            _is_at_battle_modes, "battle_modes", fallback_xy=None,
        ):
            logging.warning("Battle Modes not reached (flow attempt %d), re-homing.", attempt)
            continue

        # Battle Modes → mode card (scroll down to find it if off-screen)
        if not _ocr_tap_text(
            ocr_image, card_label, f"card:{card_label}",
            scroll_between=True, attempts=5,
        ):
            logging.warning("Card '%s' not found (flow attempt %d), re-homing.", card_label, attempt)
            continue
        _wait_until_stable()

        # mode main screen → ranking list.
        # Try template first, then OCR "Rankings" text as fallback (handles
        # modes where the button style differs from the captured template).
        ranking_visible = find_template(screenshot(), _t(ranking_tmpl), threshold=0.75) is not None
        if ranking_visible:
            if not _tap_to_reach(
                lambda s, t=ranking_tmpl: find_template(s, _t(t), threshold=0.75),
                lambda s, t=ranking_tmpl: find_template(s, _t(t), threshold=0.75) is None,
                f"{mode}:{ranking_tmpl}", fallback_xy=None,
            ):
                logging.warning("Ranking button tap failed for %s (flow attempt %d).", mode, attempt)
                continue
        else:
            # Template didn't match — try OCR text tap for "Rankings"
            from ocr import ocr_image
            if not _ocr_tap_text(ocr_image, "Rankings", f"{mode}:rankings_text", attempts=3):
                logging.warning("Rankings button not found by template or OCR for %s (flow attempt %d).", mode, attempt)
                continue
            _wait_until_stable()

        _wait_until_stable()
        logging.info("Arrived at %s ranking list.", mode)
        return

    cv2.imwrite(str(TEMPLATES_DIR.parent / f"debug_nav_fail_{mode}.png"), screenshot())
    raise RuntimeError(f"Could not reach {mode} ranking after {flow_attempts} attempts.")


def apply_guild_filter() -> bool:
    """Tap the filter icon → wait for popup → OCR-tap 'Guild Members' → wait
    for popup to close. Shared by all modes. Returns False if either step
    fails (saves a debug screenshot) · callers decide whether to abort."""
    from ocr import ocr_image
    screen = screenshot()
    pos = find_template(screen, _t("filter_icon"), threshold=0.75)
    if pos is None:
        logging.error("filter_icon not found.")
        cv2.imwrite(str(TEMPLATES_DIR.parent / "debug_filter_fail.png"), screen)
        return False
    logging.info("Setting filter to Guild Members: filter icon at %s, tapping.", pos)
    tap(*pos)
    _wait_until_stable()

    if not _ocr_tap_text(ocr_image, "Guild Members", "guild_filter_option", attempts=4):
        logging.error("'Guild Members' option not found in filter popup.")
        cv2.imwrite(str(TEMPLATES_DIR.parent / "debug_filter_fail.png"), screenshot())
        return False

    _wait_until_stable()
    logging.info("Guild filter applied.")
    return True
