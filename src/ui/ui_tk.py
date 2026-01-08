from __future__ import annotations

from datetime import datetime
from queue import Empty
from typing import List

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

from app.controller import Controller
from domain.models import SpectrometerSettings
from devices.spectrometer import CIE_MAP, SpectrometerClient

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


class AppUI:
    def __init__(self, root: tk.Tk, controller: Controller, spec: SpectrometerClient):
        self.root = root
        self.controller = controller
        self.spec = spec

        self.root.title("PJG Spectrometer & Cloud-Cam (Split Minimal Refactor)")
        self.root.geometry("1250x850")

        self.is_running = False

        self._build_widgets()
        self._configure_log_tags()

        self.root.after(50, self._poll_events)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_widgets(self) -> None:
        ctrl_frame = tk.Frame(self.root, pady=10)
        ctrl_frame.pack(side=tk.TOP, fill=tk.X, padx=15)

        exp_f = tk.LabelFrame(ctrl_frame, text=" Belichtung & Sync-Steuerung ", padx=15, pady=10)
        exp_f.pack(side=tk.LEFT, padx=5)

        self.mode_var = tk.StringVar(value="auto")
        tk.Radiobutton(exp_f, text="Auto-Belichtung", variable=self.mode_var, value="auto").grid(row=0, column=0, sticky="w")
        tk.Radiobutton(exp_f, text="Manuell (ms):", variable=self.mode_var, value="manual").grid(row=1, column=0, sticky="w")
        self.entry_ms = tk.Entry(exp_f, width=8, justify="center")
        self.entry_ms.insert(0, "500")
        self.entry_ms.grid(row=1, column=1, padx=5)

        opt_f = tk.LabelFrame(ctrl_frame, text=" CIE Standard ", padx=15, pady=10)
        opt_f.pack(side=tk.LEFT, padx=5)
        self.cie_var = tk.StringVar(value="CIE1931-2° (0x00)")
        self.cie_combo = ttk.Combobox(
            opt_f, textvariable=self.cie_var, state="readonly", width=18,
            values=list(CIE_MAP.keys())
        )
        self.cie_combo.pack(pady=2)
        tk.Button(opt_f, text="Sync Hardware", command=self._sync_hardware).pack(fill=tk.X)

        act_f = tk.Frame(ctrl_frame, padx=10)
        act_f.pack(side=tk.LEFT, fill=tk.Y)

        self.btn_start = tk.Button(
            act_f, text="START LOGGING", command=self._toggle,
            bg="#28a745", fg="white", font=("Arial", 10, "bold"),
            width=20, height=2
        )
        self.btn_start.pack(pady=2)

        tk.Button(act_f, text="Dunkel-Abgleich", command=self._dark, width=20).pack(pady=2)

        main_container = tk.Frame(self.root)
        main_container.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)

        self.fig = Figure(figsize=(7, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_xlabel("Wellenlänge [nm]")
        self.ax.set_ylabel("Intensität [W/m²]")
        self.ax.set_title("Spektrale Strahlungsverteilung (Echtzeit)")
        self.ax.grid(True, linestyle="--", alpha=0.6)

        self.line, = self.ax.plot([], [], lw=1.5)
        self.canvas = FigureCanvasTkAgg(self.fig, master=main_container)
        self.canvas.get_tk_widget().pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.log_area = scrolledtext.ScrolledText(
            main_container, width=50, state="disabled",
            bg="#1e1e1e", fg="#e0e0e0", font=("Consolas", 9)
        )
        self.log_area.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))

    def _configure_log_tags(self) -> None:
        self.log_area.tag_config("green", foreground="#44ff44")
        self.log_area.tag_config("yellow", foreground="#ffff44")
        self.log_area.tag_config("red", foreground="#ff4444")
        self.log_area.tag_config("cyan", foreground="#44ffff")
        self.log_area.tag_config("white", foreground="#e0e0e0")

    def log(self, msg: str, color: str = "white") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_area.config(state="normal")
        self.log_area.insert(tk.END, f"[{ts}] {msg}\n", color)
        self.log_area.config(state="disabled")
        self.log_area.see(tk.END)

    def _current_settings(self) -> SpectrometerSettings:
        cie_mode = CIE_MAP.get(self.cie_var.get(), 0x00)
        auto = (self.mode_var.get() == "auto")
        exposure_us = None
        if not auto:
            try:
                ms = float(self.entry_ms.get().strip())
                if ms <= 0:
                    raise ValueError
                exposure_us = int(ms * 1000)
            except Exception:
                raise ValueError("Belichtungszeit muss eine positive Zahl sein (ms).")
        return SpectrometerSettings(cie_mode=cie_mode, auto_exposure=auto, exposure_us=exposure_us)

    def _sync_hardware(self) -> None:
        try:
            settings = self._current_settings()
            self.spec.apply_settings(settings)
            self.log("Hardware-Synchronisation erfolgreich.", "yellow")
        except Exception as e:
            self.log(f"Hardware-Sync fehlgeschlagen: {e}", "red")

    def _toggle(self) -> None:
        if not self.is_running:
            try:
                settings = self._current_settings()
            except Exception as e:
                messagebox.showerror("Fehler", str(e))
                return

            self.controller.start(settings)
            self.is_running = True
            self.btn_start.config(text="STOPP LOGGING", bg="#dc3545")
        else:
            self.controller.stop()
            self.is_running = False
            self.btn_start.config(text="START LOGGING", bg="#28a745")

    def _dark(self) -> None:
        messagebox.showinfo("Dunkelabgleich", "Sensor bitte lichtdicht abdecken!")
        self.controller.calibrate_dark()

    def _update_plot(self, data: List[float]) -> None:
        x_vals = [self.spec.start_wl_nm + i for i in range(len(data))]
        self.line.set_data(x_vals, data)
        self.ax.relim()
        self.ax.autoscale_view()
        self.canvas.draw()

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.controller.events.get_nowait()
                if kind == "log":
                    color, msg = payload  
                    self.log(msg, color)
                elif kind == "plot":
                    self._update_plot(payload)  
                elif kind == "dark_done":
                    messagebox.showinfo("Erfolg", "Dunkelabgleich abgeschlossen.")
        except Empty:
            pass
        finally:
            self.root.after(50, self._poll_events)

    def _on_close(self) -> None:
        try:
            self.controller.stop()
        except Exception:
            pass
        try:
            self.spec.close()
        except Exception:
            pass
        try:
            if self.controller.camera:
                self.controller.camera.close()
        except Exception:
            pass
        self.root.destroy()
