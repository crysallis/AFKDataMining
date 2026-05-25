"""
Capture a template PNG from the live game screen.

Usage:
    python src/capture_template.py <name> <x1> <y1> <x2> <y2>

    Saves src/templates/<name>.png as a crop of the current ADB screenshot.

Run with NO arguments to dump the full screenshot to src/screen_debug.png
so you can open it and read off pixel coordinates.

──────────────────────────────────────────────────────────────────────────────
CAPTURE GUIDE  (1080×1920 resolution, estimated coords — verify via debug run)

  === Navigate to World or Homestead overview first ===
  python src/capture_template.py overview_joystick  420 1430 580 1555
  python src/capture_template.py guild_button        695 1785 865 1875

  === Navigate to Guild home screen first ===
  python src/capture_template.py guild_home_indicator  900  20 1075  90
  python src/capture_template.py guild_banner           10  20  240   95

  === Navigate to Guild members list first ===
  (take debug screenshot, pick a stable element, then run):
  python src/capture_template.py guild_members_indicator  <x1> <y1> <x2> <y2>
──────────────────────────────────────────────────────────────────────────────
"""
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).parent))
from device import screenshot

TEMPLATES_DIR = Path(__file__).parent / "templates"


def main() -> None:
    screen = screenshot()

    if len(sys.argv) == 1:
        out = Path(__file__).parent / "screen_debug.png"
        cv2.imwrite(str(out), screen)
        h, w = screen.shape[:2]
        print(f"Screenshot saved to {out}  ({w}×{h})")
        print("Open it in an image editor to read off pixel coordinates.")
        return

    if len(sys.argv) != 6:
        print(__doc__)
        sys.exit(1)

    name = sys.argv[1]
    x1, y1, x2, y2 = int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])

    crop = screen[y1:y2, x1:x2]
    if crop.size == 0:
        print(f"Error: empty crop for ({x1},{y1})-({x2},{y2}). Check coordinates.")
        sys.exit(1)

    TEMPLATES_DIR.mkdir(exist_ok=True)
    out = TEMPLATES_DIR / f"{name}.png"
    cv2.imwrite(str(out), crop)
    print(f"Saved '{name}' ({x2 - x1}×{y2 - y1} px)  →  {out}")


if __name__ == "__main__":
    main()
