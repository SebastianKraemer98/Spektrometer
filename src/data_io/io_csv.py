from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from domain.models import SpectrumMeasurement
from devices.spectrometer import PARAM_NAMES


class CsvWriter:
    def __init__(self, 
                base_dir: Path,
                measurement_dir_pattern: str = "Messungen_{day}",
                csv_delimiter: str = ";",
                ):
        self.base_dir = base_dir
        self.measurement_dir_pattern = measurement_dir_pattern
        self.csv_delimiter = csv_delimiter

    def measurement_path(self, day: str, ts: datetime) -> Path:
        out_dir = self.base_dir / self.measurement_dir_pattern.format(day=day)
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / f"m_{ts:%H%M%S}.csv"

    def write_measurement(self, path: Path, m: SpectrumMeasurement, corrected: Optional[List[float]] = None) -> None:
        data = corrected if corrected is not None else m.intensities
        with path.open("w", newline="") as f:
            w = csv.writer(f, delimiter=self.csv_delimiter)
            for k in PARAM_NAMES:
                if k in m.parameters:
                    w.writerow([k, f"{m.parameters[k]:.4f}"])
            w.writerow(["--- SPEKTRUM (nm; W/m2) ---"])
            for i, v in enumerate(data):
                w.writerow([m.start_wl_nm + i, f"{v:.6f}"])

    def write_dark(self, path: Path, start_wl_nm: int, dark: List[float], ts: datetime) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as f:
            w = csv.writer(f, delimiter=self.csv_delimiter)
            w.writerow(["Zeitpunkt", ts.strftime("%Y-%m-%d %H:%M:%S")])
            w.writerow(["Wellenlaenge", "Dunkelwert (W/m2)"])
            for i, v in enumerate(dark):
                w.writerow([start_wl_nm + i, f"{v:.6f}"])
