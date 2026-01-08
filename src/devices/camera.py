from __future__ import annotations

from datetime import datetime
from pathlib import Path

try:
    from picamera2 import Picamera2  # type: ignore
except ImportError:
    Picamera2 = None  # type: ignore


class CloudCamera:
    def __init__(self):
        if Picamera2 is None:
            raise RuntimeError("picamera2 not installed or not available on this system")
        self._cam = Picamera2()
        self._cam.configure(self._cam.create_still_configuration())
        self._cam.start()

    def close(self) -> None:
        try:
            self._cam.stop()
        except Exception:
            pass

    def capture_png(self, out_dir: Path, ts: datetime) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        fn = out_dir / f"Wolkenbild_{ts:%H%M%S}.png"
        self._cam.capture_file(str(fn))
        return fn
