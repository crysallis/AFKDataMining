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
    logging.debug("match %-22s %.3f / %.2f %s",
                  template_path.stem, max_val, threshold,
                  "HIT" if max_val >= threshold else "")
    if max_val >= threshold:
        h, w = tmpl.shape[:2]
        return (max_loc[0] + w // 2, max_loc[1] + h // 2)
    return None


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
    """If a known blocking popup is visible (e.g. 'Exit game?'), tap to dismiss it.
    Returns True if something was dismissed. The exit dialog also closes on Back,
    so even without a template the back-press fallback clears it."""
    for name in _POPUP_DISMISS:
        pos = find_template(screen, _t(name), threshold=0.80)
        if pos:
            tap(*pos)
            logging.info("Dismissed popup via '%s' at %s.", name, pos)
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
        exit_pos = _find_exit_popup(screen)
        if exit_pos:
            tap(*exit_pos)  # dismiss "Exit game?" (No) → lands on the overview
            logging.info("Reached root via Exit dialog after %d back-press(es); dismissed → overview.", attempt)
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
