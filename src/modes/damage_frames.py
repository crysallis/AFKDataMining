"""Keeps the screenshot behind a damage read whose name did not resolve, so the cause
of a misread can be seen instead of guessed. Frames are written to a git-ignored folder
(they show member names) with file names built only from the time and the damage value,
never from the member name."""
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2

MAX_AGE_DAYS = 14


class FrameKeeper:
    def __init__(self, out_dir: Path | None = None, cap: int = 20):
        self.out_dir = Path(out_dir) if out_dir is not None else Path(__file__).resolve().parents[2] / "debug_unmatched"
        self.cap = cap
        self._frames: dict[int, object] = {}
        self._entries: list[tuple[str, int, int]] = []

    def remember(self, img, name: str, damage: int) -> None:
        key = id(img)
        if key not in self._frames:
            if len(self._frames) >= self.cap:
                return
            self._frames[key] = img
        self._entries.append((name, damage, key))

    def write_for(self, unmatched: list[tuple[str, int]], fmt_damage=str) -> dict[tuple[str, int], str]:
        self._prune()
        wanted = set(unmatched)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        written: dict[tuple[str, int], str] = {}
        for name, damage, key in self._entries:
            pair = (name, damage)
            if pair not in wanted or pair in written:
                continue
            fname = f"{stamp}_{fmt_damage(damage)}_{len(written)}.png"
            try:
                self.out_dir.mkdir(parents=True, exist_ok=True)
                if not cv2.imwrite(str(self.out_dir / fname), self._frames[key]):
                    continue
            except OSError:
                continue
            written[pair] = f"{self.out_dir.name}/{fname}"
        return written

    def _prune(self) -> None:
        if not self.out_dir.exists():
            return
        cutoff = time.time() - MAX_AGE_DAYS * 86400
        try:
            for f in self.out_dir.glob("*.png"):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                except OSError:
                    continue
        except OSError:
            return
