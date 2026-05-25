import subprocess
import time
import numpy as np
import cv2


DEVICE = "127.0.0.1:5555"
EXPECTED_WIDTH   = 1080
EXPECTED_HEIGHT  = 1920
EXPECTED_DENSITY = 240


def ensure_resolution() -> None:
    def _get(cmd):
        r = subprocess.run(["adb", "-s", DEVICE, "shell", "wm"] + cmd, capture_output=True, text=True)
        return r.stdout.strip()

    size = _get(["size"])
    density = _get(["density"])

    current = size.split(":")[-1].strip()   # "1080x1920"
    expected = f"{EXPECTED_WIDTH}x{EXPECTED_HEIGHT}"

    needs_size    = current != expected
    needs_density = str(EXPECTED_DENSITY) not in density

    if needs_size:
        print(f"[Resolution] Resetting size {current} -> {expected}")
        subprocess.run(["adb", "-s", DEVICE, "shell", "wm", "size", expected])
    if needs_density:
        print(f"[Resolution] Resetting density -> {EXPECTED_DENSITY}")
        subprocess.run(["adb", "-s", DEVICE, "shell", "wm", "density", str(EXPECTED_DENSITY)])

    if needs_size or needs_density:
        time.sleep(2.0)
        print("[Resolution] Done.")
    else:
        print(f"[Resolution] OK ({current} @ {EXPECTED_DENSITY}dpi)")


def screenshot() -> np.ndarray:
    for attempt in range(3):
        result = subprocess.run(
            ["adb", "-s", DEVICE, "exec-out", "screencap", "-p"],
            capture_output=True,
        )
        png_bytes = result.stdout
        if not png_bytes:
            time.sleep(0.5)
            continue
        arr = np.frombuffer(png_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is not None:
            return img
        time.sleep(0.5)
    raise RuntimeError("screenshot() failed after 3 attempts — ADB returned empty or corrupt data")


def tap(x: int, y: int) -> None:
    subprocess.run(["adb", "-s", DEVICE, "shell", "input", "tap", str(x), str(y)])


def swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 500) -> None:
    subprocess.run([
        "adb", "-s", DEVICE, "shell", "input", "swipe",
        str(x1), str(y1), str(x2), str(y2), str(duration_ms),
    ])


def scroll_down() -> None:
    swipe(540, 1100, 540, 730, 500)


def scroll_down_small() -> None:
    swipe(540, 1050, 540, 800, 500)


def screen_changed(prev: np.ndarray, curr: np.ndarray, threshold: float = 1.5) -> bool:
    return _img_diff(prev, curr) >= threshold


def scroll_up() -> None:
    swipe(540, 700, 540, 1400, 300)


def _img_diff(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16))))


def scroll_to_top(max_scrolls: int = 20, stable_threshold: float = 1.5) -> None:
    print("Scrolling to top...")
    prev = screenshot()
    no_change = 0
    for _ in range(max_scrolls):
        scroll_up()
        time.sleep(0.8)
        curr = screenshot()
        if _img_diff(prev, curr) < stable_threshold:
            no_change += 1
            if no_change >= 2:
                print("  At top.")
                return
        else:
            no_change = 0
        prev = curr
    print("  Reached scroll limit.")
