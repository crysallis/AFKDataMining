import subprocess
import time
import numpy as np
import cv2


DEVICE = "127.0.0.1:5555"


def screenshot() -> np.ndarray:
    result = subprocess.run(
        ["adb", "-s", DEVICE, "exec-out", "screencap", "-p"],
        capture_output=True,
    )
    png_bytes = result.stdout
    arr = np.frombuffer(png_bytes, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


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
