import time
import numpy as np
import cv2
import adbutils
import psutil

DEVICE = "127.0.0.1:5555"
EXPECTED_WIDTH   = 1080
EXPECTED_HEIGHT  = 1920
EXPECTED_DENSITY = 240

_client: adbutils.AdbClient | None = None
_device: adbutils.AdbDevice | None = None

_last_activity = time.monotonic()  # heartbeat: updated on every successful ADB call


def seconds_since_activity() -> float:
    """Seconds since the last successful ADB command (the scan's progress heartbeat)."""
    return time.monotonic() - _last_activity


def mark_activity() -> None:
    """Reset the activity heartbeat (call once at scan start)."""
    global _last_activity
    _last_activity = time.monotonic()


def _get_device() -> adbutils.AdbDevice:
    global _client, _device
    if _client is None:
        _client = adbutils.AdbClient(host="127.0.0.1", port=5037)
        _client.server_version()  # starts ADB server if not running
    if _device is None:
        _client.connect(DEVICE)
        _device = _client.device(DEVICE)
    return _device


def _kill_adb_process() -> None:
    """Kill the adb.exe process at the OS level via psutil.

    server_kill() travels through adbutils' own socket, which can itself be
    wedged; terminating the process from outside cannot hang and always frees
    it. Mirrors AdbAutoPlayer's _kill_adb_process fallback."""
    killed = False
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "").lower()
        if name in ("adb", "adb.exe"):
            try:
                proc.terminate()
                proc.wait(timeout=3)
                killed = True
            except psutil.NoSuchProcess:
                killed = True
            except psutil.TimeoutExpired:
                proc.kill()
                killed = True
            except psutil.AccessDenied:
                print("[ADB] Access denied killing adb.exe.")
    if killed:
        print("[ADB] adb.exe process killed.")


kill_adb_process = _kill_adb_process  # public alias for the scan watchdog


def _restart_adb_server() -> None:
    """Restart the adb server and drop the cached client/device.

    Graceful server_kill() first, OS-level process kill as fallback. The next
    _get_device() rebuilds the client and device from scratch."""
    global _client, _device
    print("[ADB] Restarting adb server...")
    try:
        if _client:
            _client.server_kill()
    except Exception:
        _kill_adb_process()
    _client = None
    _device = None
    time.sleep(1.0)


def _shell(cmd, stream=False, timeout=10.0):
    """Run a shell command with retry, modeled on AdbAutoPlayer's @adb_retry.

    Calls run synchronously and every connection is context-managed, so a failed
    or timed-out call closes its socket (and its guest-side screencap process)
    instead of leaking. Leaked connections were the cause of the screencap
    death-spiral: each abandoned screencap kept loading the guest, slowing the
    next until everything timed out.

    Flow: 2 attempts, then restart the adb server + recreate the device, then 2
    more. adbutils' own per-read socket timeout bounds a stalled call."""
    global _client, _device, _last_activity
    last_exc = None
    for attempt in range(4):
        if attempt == 2:
            _restart_adb_server()
        try:
            d = _get_device()
            with d.shell(cmd, stream=True, timeout=timeout) as conn:
                enc = None if stream else "utf-8"
                result = conn.read_until_close(encoding=enc)
            _last_activity = time.monotonic()  # heartbeat: a call succeeded
            return result
        except Exception as e:
            last_exc = e
            print(f"[ADB] shell {cmd!r} attempt {attempt+1} failed: {e}")
            # Drop BOTH client and device: a wedged client reused for connect()
            # has no timeout and will hang. Rebuild from scratch next attempt.
            _client = None
            _device = None
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


def back() -> None:
    """Android Back (keyevent 4), routed through the retrying ADB layer."""
    _shell("input keyevent 4", timeout=5.0)


def swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 500) -> None:
    _shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}", timeout=5.0)


def scroll_down() -> None:
    # Advance ~3 member cards per swipe (card pitch ~249px). The 4th card
    # overlaps into the next frame, so no card is ever cut in both adjacent
    # screenshots — dedup drops the repeat. A single bigger swipe drifts via
    # fling momentum (~11-20% overshoot) and accumulates off-grid by ~10 swipes;
    # this ~620px drag over 2200ms lines up every time. Tuned by hand on-device.
    # The 1-card overlap + dedup absorbs any residual fling drift under load.
    swipe(540, 1400, 540, 778, 2200)


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
