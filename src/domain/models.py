from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional


class SpectrometerSettings:
    __slots__ = ("cie_mode", "auto_exposure", "exposure_us")

    def __init__(self, cie_mode: int, auto_exposure: bool, exposure_us: Optional[int]):
        object.__setattr__(self, "cie_mode", cie_mode)
        object.__setattr__(self, "auto_exposure", auto_exposure)
        object.__setattr__(self, "exposure_us", exposure_us)

    def __setattr__(self, name, value):
        raise AttributeError(f"{self.__class__.__name__} is frozen")


class SpectrumMeasurement:
    __slots__ = (
        "timestamp",
        "start_wl_nm",
        "intensities",
        "parameters",
        "scale_exponent",
    )

    def __init__(
        self,
        timestamp: datetime,
        start_wl_nm: int,
        intensities: List[float],
        parameters: Dict[str, float],
        scale_exponent: int,
    ):
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "start_wl_nm", start_wl_nm)
        object.__setattr__(self, "intensities", intensities)
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "scale_exponent", scale_exponent)

    def __setattr__(self, name, value):
        raise AttributeError(f"{self.__class__.__name__} is frozen")

 