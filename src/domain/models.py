from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional


@dataclass(frozen=True)
class SpectrometerSettings:
    cie_mode: int
    auto_exposure: bool
    exposure_us: Optional[int]  # only used if manual


@dataclass(frozen=True)
class SpectrumMeasurement:
    timestamp: datetime
    start_wl_nm: int
    intensities: List[float]
    parameters: Dict[str, float]
    scale_exponent: int
