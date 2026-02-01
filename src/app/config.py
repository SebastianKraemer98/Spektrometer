from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

@dataclass(frozen=True)
class SpectrometerConfig:
        port: str='/dev/ttyACM0'
        baudrate: int = 115200
        #timout_s: float = 1.5
        
        
@dataclass(frozen=True)
class CloudCamera:
    enabled: bool = True
    
    
@dataclass(frozen=True)
class StorageConfig:
    data_dir: Optional[str] = None
    
    measurement_dir_pattern: str = "Messungen_{day}" # {day} = YYYY-MM-DD
    cloud_image_dir: str = "Wolkenbilder"
    dark_dirname: str = "Dunkelmessungen"
    
    csv_delimiter: str = ";" 
    
@dataclass(frozen=True)
class DarkConfig:
    samples: int = 3
    sample_delay_s: float = 0.2
    measurement_timeout_s: float = 12.0
    

@dataclass(frozen=True)
class UIConfig:
    window_title: str = "Messstation"
    default_exposure_mode: str = "auto"  # "auto" | "manual"
    default_exposure_ms: float = 500.0
    # eventuell mehr Eisntellungen für die UI
    

@dataclass(frozen=True)
class AppConfig:
    spectrometer: SpectrometerConfig = field(default_factory=SpectrometerConfig)
    camera: CloudCamera = field(default_factory=CloudCamera)
    storage: StorageConfig = field(default_factory=StorageConfig)
    dark: DarkConfig = field(default_factory=DarkConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    
    def data_dir_path(self, project_dir):
        d = self.storage.data_dir
        if not d:
            return project_dir
        p = Path(d)
        return p if p.is_absolute() else project_dir / p
    
    @staticmethod
    def from_dict(d):
        spec_d = d.get("spectrometer", {})
        cam_d = d.get("camera", {})
        stor_d = d.get("storage", {})
        dark_d = d.get("dark", {})
        ui_d = d.get("ui", {})
        
        cfg = AppConfig(
            spectrometer=SpectrometerConfig(
                port=str(spec_d.get("port", SpectrometerConfig.port)),
                baudrate=int(spec_d.get("baudrate", SpectrometerConfig.baudrate)),
            ),
            camera=CloudCamera(
                enabled=bool(cam_d.get("enabled", CloudCamera.enabled)),
            ),
            storage=StorageConfig(
                data_dir=stor_d.get("data_dir", StorageConfig.data_dir),
                measurement_dir_pattern=str(stor_d.get("measurement_dir_pattern", StorageConfig.measurement_dir_pattern)),
                cloud_image_dir=str(stor_d.get("cloud_image_dir", StorageConfig.cloud_image_dir)),
                dark_dirname=str(stor_d.get("dark_dirname", StorageConfig.dark_dirname)),
                csv_delimiter=str(stor_d.get("csv_delimiter", StorageConfig.csv_delimiter)),
            ),
            dark=DarkConfig(
                samples=int(dark_d.get("samples", DarkConfig.samples)),
                sample_delay_s=float(dark_d.get("sample_delay_s", DarkConfig.sample_delay_s)),
                measurement_timeout_s=float(dark_d.get("measurement_timeout_s", DarkConfig.measurement_timeout_s)),
            ),
            ui=UIConfig(
                window_title=str(ui_d.get("window_title", UIConfig.window_title)),
                default_exposure_mode=str(ui_d.get("default_exposure_mode", UIConfig.default_exposure_mode)),
                default_exposure_ms=float(ui_d.get("default_exposure_ms", UIConfig.default_exposure_ms)),
            ),
        )
        _validate(cfg)
        return cfg
    
def _validate(cfg: AppConfig) -> None:
    if cfg.storage.csv_delimiter == "":
        raise ValueError("storage.csv_delimiter must not be empty")

    if cfg.dark.samples <= 0:
        raise ValueError("dark.samples must be >= 1")
    if cfg.dark.sample_delay_s < 0:
        raise ValueError("dark.sample_delay_s must be >= 0")
    if cfg.dark.measurement_timeout_s <= 0:
        raise ValueError("dark.measurement_timeout_s must be > 0")

    if cfg.ui.default_exposure_mode not in ("auto", "manual"):
        raise ValueError('ui.default_exposure_mode must be "auto" or "manual"')
    if cfg.ui.default_exposure_ms <= 0:
        raise ValueError("ui.default_exposure_ms must be > 0")
    
def load_config(path):
    if not path.exists():
        return AppConfig()
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw) if raw.strip() else {}
    if not isinstance(data, dict):
        raise ValueError("Config file must contain a JSON object")
    return AppConfig.from_dict(data)


