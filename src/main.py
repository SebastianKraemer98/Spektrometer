from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import messagebox

from app.controller import Controller
from app.config import load_config
from devices.spectrometer import SpectrometerClient
from data_io.io_csv import CsvWriter
from app.controller import Controller
from ui.ui_tk import AppUI

try:
    from devices.camera import CloudCamera
except Exception:
    CloudCamera = None 


def main():
    project_dir = Path(__file__).resolve().parent
    cfg = load_config(project_dir / "config.json")
    data_dir = cfg.data_dir_path(project_dir)

    spec = SpectrometerClient(
        port=cfg.spectrometer.port,
        baudrate=cfg.spectrometer.baudrate,
    )
    try:
        s, e = spec.connect()
    except Exception as ex:
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror("Hardware-Fehler", f"Spektrometer nicht gefunden / nicht lesbar:\n{ex}")
        r.destroy()
        return

    camera = None
    if cfg.camera.enabled and CloudCamera is not None:
        try:
            camera = CloudCamera()
        except Exception:
            camera = None

    csvw = CsvWriter(
        base_dir=data_dir,
        measurement_dir_pattern=cfg.storage.measurement_dir_pattern,
        csv_delimiter=cfg.storage.csv_delimiter,
        )
    
    controller = Controller(
        spec=spec,
        csvw=csvw,
        data_dir=data_dir,
        camera=camera,
        cloud_images_dirname=cfg.storage.cloud_image_dir,
        dark_dirname=cfg.storage.dark_dirname,
        dark_samples=cfg.dark.samples,
        dark_sample_delay_s=cfg.dark.sample_delay_s,
        dark_measurement_timeout_s=cfg.dark.measurement_timeout_s,
    )
    
    
    root = tk.Tk()
    ui = AppUI(root, controller, spec, ui_cfg=cfg.ui)

    ui.log(f"Spektrometer erkannt: {s}nm - {e}nm", "green")
    if camera:
        ui.log("Hardware: Kamera bereit.", "green")
    else:
        ui.log("Hardware: Kamera nicht verfügbar (läuft ohne).", "yellow")

    root.mainloop()


if __name__ == "__main__":
    main()
