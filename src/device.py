import threading
import time
import numpy as np
import cv2
import adbutils

DEVICE = "127.0.0.1:5555"
EXPECTED_WIDTH   = 1080
EXPECTED_HEIGHT  = 1920
EXPECTED_DENSITY = 240

_client: adbutils.AdbClient | None = None
_device: adbutils.AdbDevice | None = None


def _get_device() -> adbutils.AdbDevice:
    global _client, _device
    if _client is None:
        _client = adbutils.AdbClient(host="127.0.0.1", port=5037)
        _client.server_version()  # starts ADB server if not running
    if _device is None:
        _client.connect(DEVICE)
        _device = _client.device(DEVICE)
    return _device


def _reconnect() -> None:
    global _client, _device
    print("[ADB] Reconnecting...")
    try:
        if _client:
            _client.server_kill()
    except Exception:
        pass
    time.sleep(1.0)
    _client = adbutils.AdbClient(host="127.0.0.1", port=5037)
    _client.server_version()  # starts ADB server if not running
    _client.connect(DEVICE)
    _device = _client.device(DEVICE)
    print("[ADB] Reconnected.")


def _run_with_deadline(fn, deadline: float):
    """Run fn() in a worker thread, raising TimeoutError if it blocks past
    `deadline` seconds. adbutils' socket timeout does not reliably interrupt a
    stalled screencap stream, so this wall-clock watchdog is the real guard.
    A timed-out worker is abandoned (daemon) and its dead socket discarded."""
    box: dict = {}

    def worker():
        try:
            box["value"] = fn()
        except Exception as e:  # noqa: BLE001
            box["error"] = e

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(deadline)
    if t.is_alive():
        raise TimeoutError(f"call exceeded {deadline}s wall-clock")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _shell(cmd, stream=False, timeout=10.0):
    """Run a shell command with a hard wall-clock timeout + retry/reconnect."""
    global _device
    last_exc = None
    for attempt in range(4):
        if attempt == 2:
            _reconnect()
        try:
            d = _get_device()

            def call():
                if stream:
                    with d.shell(cmd, stream=True, timeout=timeout) as conn:
                        return conn.read_until_close(encoding=None)
                return d.shell(cmd, timeout=timeout)

            return _run_with_deadline(call, timeout + 5.0)
        except Exception as e:
            last_exc = e
            print(f"[ADB] shell {cmd!r} attempt {attempt+1} failed: {e}")
            _device = None  # drop the (possibly hung) handle so next attempt reconnects
            time.sleep(0.5)
    raise RuntimeError(f"ADB shell failed after 4 attempts: {last_exc}")


def ensure_resolution() -> None:
    size = _shell("wm size")
    density = _shell("wm density")

    current = size.split(":")[-1].strip()
    expected = f"{EXPECTED_WIDTH}x{EXPECTED_HEIGHT}"

    needs_size    = current != expected
    needs_density = str(EXPECTED_DENSITY) not in density

    if needs_size:
        print(f"[Resolution] Resetting size {current} -> {expected}")
        _shell(f"wm size {expected}")
    if needs_density:
        print(f"[Resolution] Resetting density -> {EXPECTED_DENSITY}")
        _shell(f"wm density {EXPECTED_DENSITY}")

    if needs_size or needs_density:
        time.sleep(2.0)
        print("[Resolution] Done.")
    else:
        print(f"[Resolution] OK ({current} @ {EXPECTED_DENSITY}dpi)")


def screenshot() -> np.ndarray:
    png_bytes = _shell("screencap -p", stream=True, timeout=15.0)
    if not png_bytes:
        raise RuntimeError("screencap returned empty bytes")
    arr = np.frombuffer(png_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"cv2.imdecode failed on {len(png_bytes)} bytes")
    return img


def tap(x: int, y: int) -> None:
    _shell(f"input tap {x} {y}", timeout=5.0)


def swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 500) -> None:
    _shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}", timeout=5.0)


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
