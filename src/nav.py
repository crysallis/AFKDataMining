"""Navigation helpers for AFK Journey guild scraping."""
import logging
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np

from device import DEVICE, screenshot, tap

TEMPLATES_DIR = Path(__file__).parent / "templates"

# Hardcoded fallback coordinates (1080×1920).
# Used when the matching template PNG is missing.
_GUILD_BTN_XY = (780, 1830)
_GUILD_BANNER_XY = (120, 57)


def press_back(sleep: float = 0.8) -> None:
    subprocess.run(
        ["adb", "-s", DEVICE, "shell", "input", "keyevent", "4"],
        capture_output=True,
    )
    time.sleep(sleep)


def find_template(
    screen: np.ndarray,
    template_path: Path,
    threshold: float = 0.80,
) -> tuple[int, int] | None:
    """Return (cx, cy) of best template match, or None if below threshold."""
    tmpl = cv2.imread(str(template_path))
    if tmpl is None:
        return None
    result = cv2.matchTemplate(screen, tmpl, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val >= threshold:
        h, w = tmpl.shape[:2]
        return (max_loc[0] + w // 2, max_loc[1] + h // 2)
    return None


def _t(name: str) -> Path:
    return TEMPLATES_DIR / f"{name}.png"


def _is_at_overview(screen: np.ndarray) -> bool:
    """World or Homestead overview: look for the D-pad joystick."""
    return find_template(screen, _t("overview_joystick"), threshold=0.75) is not None


def _is_at_guild_home(screen: np.ndarray) -> bool:
    """Guild home screen: look for the Admin button (top-right)."""
    return find_template(screen, _t("guild_home_indicator"), threshold=0.80) is not None


def _is_at_guild_members(screen: np.ndarray) -> bool:
    """Guild members list: look for an element unique to that screen."""
    return find_template(screen, _t("guild_members_indicator"), threshold=0.80) is not None


def _tap_ui_back(screen: np.ndarray) -> bool:
    """Tap an on-screen back button if one is visible. Returns True if tapped."""
    for name in ('back_dark', 'back_light'):
        pos = find_template(screen, _t(name), threshold=0.75)
        if pos:
            tap(*pos)
            logging.debug("Tapped UI back button '%s' at %s.", name, pos)
            return True
    return False


def navigate_home(max_attempts: int = 20) -> str | None:
    """Press Back until any known screen is detected.

    Checks after every back-press — returns 'guild_members', 'guild_home',
    or 'overview' as soon as one is found. Returns None if max_attempts
    is exhausted without finding a known screen.
    """
    for attempt in range(max_attempts):
        screen = screenshot()
        if _is_at_guild_members(screen):
            logging.debug("Found guild_members after %d back-press(es).", attempt)
            return 'guild_members'
        if _is_at_guild_home(screen):
            logging.debug("Found guild_home after %d back-press(es).", attempt)
            return 'guild_home'
        if _is_at_overview(screen):
            logging.debug("Found overview after %d back-press(es).", attempt)
            return 'overview'
        if not _tap_ui_back(screen):
            press_back()

    logging.error("Could not reach a known screen after %d attempts.", max_attempts)
    cv2.imwrite(str(TEMPLATES_DIR.parent / "debug_overview_fail.png"), screenshot())
    return None


def navigate_to_guild_members() -> None:
    """Navigate from any screen to the guild member list."""
    logging.info("Navigating to guild members list.")

    where = navigate_home()
    if where is None:
        raise RuntimeError("Could not reach a known screen after repeated back-presses.")

    if where == 'guild_members':
        logging.info("Already at guild members list.")
        return

    if where == 'overview':
        # Tap Guild in the bottom nav bar
        screen = screenshot()
        pos = find_template(screen, _t("guild_button"), threshold=0.75)
        tap(*(pos or _GUILD_BTN_XY))
        logging.debug("Tapped Guild button at %s.", pos or _GUILD_BTN_XY)
        time.sleep(2.5)

        # Wait for guild home screen
        for _ in range(20):
            screen = screenshot()
            if _is_at_guild_home(screen):
                break
            time.sleep(0.5)
        else:
            cv2.imwrite(str(TEMPLATES_DIR.parent / "debug_guild_home_fail.png"), screenshot())
            raise RuntimeError("Guild home screen not detected after tapping Guild.")

    # At guild home — tap the guild level banner to open members list
    screen = screenshot()
    pos = find_template(screen, _t("guild_banner"), threshold=0.75)
    tap(*(pos or _GUILD_BANNER_XY))
    logging.debug("Tapped guild banner at %s.", pos or _GUILD_BANNER_XY)
    time.sleep(1.5)

    # Confirm we're on the members list
    for _ in range(15):
        screen = screenshot()
        if _is_at_guild_members(screen):
            logging.info("Guild members list reached.")
            return
        time.sleep(0.5)
    raise RuntimeError("Guild members list not detected after tapping banner.")
