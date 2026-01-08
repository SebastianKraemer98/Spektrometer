from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import messagebox

from app.controller import Controller
from devices.spectrometer import SpectrometerClient
from io.io_csv import CsvWriter
from app.controller import Controller
from ui.ui_tk import AppUI

try:
    from devices.camera import CloudCamera
except Exception:
    CloudCamera = None 


def main():
    base_dir = Path(__file__).resolve().parent

    spec = SpectrometerClient(port="/dev/ttyACM0")
    try:
        s, e = spec.connect()
    except Exception as ex:
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror("Hardware-Fehler", f"Spektrometer nicht gefunden / nicht lesbar:\n{ex}")
        r.destroy()
        return

    camera = None
    if CloudCamera is not None:
        try:
            camera = CloudCamera()
        except Exception:
            camera = None

    csvw = CsvWriter(base_dir=base_dir)
    controller = Controller(spec=spec, csvw=csvw, base_dir=base_dir, camera=camera)

    root = tk.Tk()
    ui = AppUI(root, controller, spec)

    ui.log(f"Spektrometer erkannt: {s}nm - {e}nm", "green")
    if camera:
        ui.log("Hardware: Kamera bereit.", "green")
    else:
        ui.log("Hardware: Kamera nicht verfügbar (läuft ohne).", "yellow")

    root.mainloop()


if __name__ == "__main__":
    main()
