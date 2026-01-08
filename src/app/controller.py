from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from queue import Queue
from typing import List, Optional, Tuple

from domain.models import SpectrometerSettings
from devices.spectrometer import SpectrometerClient
from io.io_csv import CsvWriter

try:
    import numpy as np  
except ImportError:
    np = None 

try:
    from devices.camera import CloudCamera
except Exception:
    CloudCamera = None  


class Controller:
    def __init__(self, spec: SpectrometerClient, csvw: CsvWriter, base_dir: Path, camera = None):
        self.spec = spec
        self.csvw = csvw
        self.base_dir = base_dir
        self.camera = camera

        self.stop_event = threading.Event()
        self.events: "Queue[Tuple[str, object]]" = Queue()

        self.dark_reference: List[float] = []
        self._loop_thread: Optional[threading.Thread] = None

    def start(self, settings: SpectrometerSettings) -> None:
        self.spec.apply_settings(settings)
        self.stop_event.clear()
        self._loop_thread = threading.Thread(target=self._loop_full_minute, daemon=True)
        self._loop_thread.start()
        self.events.put(("log", ("cyan", ">>> SYNC-MODUS AKTIVIERT (Jede volle Minute)")))

    def stop(self) -> None:
        self.stop_event.set()
        self.events.put(("log", ("yellow", "Stop angefordert.")))

    def calibrate_dark(self) -> None:
        threading.Thread(target=self._dark_worker, daemon=True).start()

    def _loop_full_minute(self) -> None:
        last_trigger_minute = -1
        while not self.stop_event.is_set():
            now = datetime.now()

            next_minute = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
            wait_s = (next_minute - now).total_seconds()

            if wait_s > 0:
                self.stop_event.wait(wait_s)
            if self.stop_event.is_set():
                break

            ts = datetime.now().replace(microsecond=0)
            if ts.minute == last_trigger_minute:
                continue
            last_trigger_minute = ts.minute

            day = ts.strftime("%Y-%m-%d")
            self.events.put(("log", ("yellow", f"--- Trigger: {ts:%H:%M}:00 ---")))

            # run photo + spectrum in parallel
            threading.Thread(target=self._photo_worker, args=(day, ts), daemon=True).start()
            threading.Thread(target=self._spectrum_worker, args=(day, ts), daemon=True).start()

    def _photo_worker(self, day: str, ts: datetime) -> None:
        if not self.camera:
            return
        try:
            out_dir = self.base_dir / "Wolkenbilder" / day
            path = self.camera.capture_png(out_dir, ts)
            self.events.put(("log", ("cyan", f"Kamera: {path.name} gesichert.")))
        except Exception as e:
            self.events.put(("log", ("red", f"Fehler Kamera: {e}")))

    def _spectrum_worker(self, day: str, ts: datetime) -> None:
        try:
            m = self.spec.read_single_measurement()
            corrected = None
            if self.dark_reference:
                if len(self.dark_reference) == len(m.intensities):
                    corrected = [max(0.0, v - d) for v, d in zip(m.intensities, self.dark_reference)]
                else:
                    self.events.put(("log", ("yellow", "Warnung: Dark-Referenz hat falsche Länge, wird ignoriert.")))

            out_path = self.csvw.measurement_path(day, ts)
            self.csvw.write_measurement(out_path, m, corrected=corrected)

            self.events.put(("plot", corrected if corrected is not None else m.intensities))
            self.events.put(("log", ("green", f"Spektrum: {out_path.name} gesichert.")))
        except Exception as e:
            self.events.put(("log", ("red", f"Fehler Spektrometer: {e}")))

    def _dark_worker(self) -> None:
        try:
            self.events.put(("log", ("cyan", "Dunkelabgleich: Referenzmessung läuft...")))
            scans: List[List[float]] = []
            for i in range(3):
                m = self.spec.read_single_measurement(timeout_s=12.0)
                scans.append(m.intensities)
                self.events.put(("log", ("cyan", f" Dunkel-Probe {i+1}/3 aufgenommen.")))
                time.sleep(0.2)

            if np is not None:
                avg = (np.mean(np.array(scans), axis=0)).tolist()
            else:
                n = len(scans[0])
                avg = [sum(s[i] for s in scans) / 3.0 for i in range(n)]

            self.dark_reference = [float(x) for x in avg]

            dark_dir = self.base_dir / "Dunkelmessungen"
            fn = dark_dir / f"dark_{datetime.now():%Y-%m-%d_%H%M%S}.csv"
            self.csvw.write_dark(fn, self.spec.start_wl_nm, self.dark_reference, datetime.now())

            self.events.put(("log", ("green", f"Dunkel-CSV archiviert: {fn.name}")))
            self.events.put(("dark_done", None))
        except Exception as e:
            self.events.put(("log", ("red", f"Dunkel-Fehler: {e}")))
