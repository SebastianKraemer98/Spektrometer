"""
PJG Spectrometer & Cloud-Cam – GUI-Programm (Tkinter)

Was kann das Programm?
- Spektrometer per USB-Serial automatisch finden (Port-Scan + Handshake)
- Spektrum anzeigen (Plot) und optional speichern (Logging-Modus)
- Kamera-Livebild anzeigen
- Live-Modus: Spektrum + Kamera laufen dauerhaft (ohne Speicherung)
- Logging-Modus: Spektrum + Foto werden in festem Minutenintervall gespeichert
- Dunkelabgleich (Dark Reference) zur Rauschminderung für Spektrum
- Kamera-Einstellungen über GUI setzen (AE/Manuell, Exposure, Gain, AWB, Bildlook)

Wichtige Architektur-Ideen:
- Tkinter GUI läuft im Hauptthread.
- Hardwarezugriffe laufen in Threads (damit die GUI nicht „einfriert“).
- Locks (cam_lock, spec_lock) verhindern gleichzeitige Zugriffe auf Kamera/Spektrometer.
"""

## Import von Bibliotheken -------------------------------------------------------------------------------------------------------------------------

import sys
import os
import struct
import time
import csv
import threading
from datetime import datetime

try:
    import serial
    from serial.tools import list_ports
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    import numpy as np
    from picamera2 import Picamera2
    from PIL import Image, ImageTk  # Livebild
except ImportError as e:
    print(f"\n! FEHLER: Modul fehlt: {e}")
    print("Bitte installieren mit: pip install pyserial matplotlib numpy picamera2 pillow")
    sys.exit()

import tkinter as tk
from tkinter import messagebox, scrolledtext, filedialog

## Erstellen von Default-Speicherpfaden und Ordnern ------------------------------------------------------------------------------------------------

# Ordner des Skripts (Basis, falls der Nutzer nichts anderes auswählt)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Standard-Unterordner für Kamera-Fotos und Dunkelmessungen
CAM_BASE_DIR = os.path.join(SCRIPT_DIR, "Wolkenbilder")
DARK_BASE_DIR = os.path.join(SCRIPT_DIR, "Dunkelmessungen")

# Sicherstellen, dass Standardordner existieren
os.makedirs(CAM_BASE_DIR, exist_ok=True)
os.makedirs(DARK_BASE_DIR, exist_ok=True)

# Namen der 50 Float-Parameter, die aus dem Spektrometer-Paket gelesen werden
PARAM_NAMES = [
    "X", "Y", "Z", "x", "y", "u", "v", "u_prime", "v_prime",
    "Tc_CCT", "Nit", "r_ratio", "g_ratio", "b_ratio", "DUV", "Ra",
    "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10",
    "R11", "R12", "R13", "R14", "R15",
    "Lp", "HW", "Ld", "purity", "SP", "SDCM", "k", "lux", "Ee", "fc",
    "CQS", "GAI_EES", "GAI_BB_8", "GAI_BB_15", "EML", "M_EDI",
    "Red_Ee", "Nir_EeA", "Nir_EeB"
]


class SpectrometerApp:
    """
    Hauptklasse der Anwendung.
    Enthält:
    - GUI-Aufbau (create_widgets)
    - Hardware-Init (init_hardware)
    - Livebild (Kamera)
    - Spektrum lesen / anzeigen / speichern
    - Logging-Modus + Live-Modus
    - Dunkelabgleich
    - Kamera-Einstellungen anwenden
    """

    def __init__(self, root):
        self.root = root
        self.root.title("PJG Spectrometer & Cloud-Cam v8.7 Expert")
        self.root.geometry("1250x850")

        # --------------------------
        # Hardware-Handles / Status
        # --------------------------
        self.ser = None      # serial.Serial-Objekt für Spektrometer
        self.picam = None    # Picamera2-Objekt
        self.connected = False  # True, wenn Spektrometer gefunden/handshake ok

        # --------------------------
        # Modus-Flags
        # --------------------------
        self.is_measuring = False   # Logging Modus aktiv?
        self.is_live_mode = False   # Live Modus aktiv?
        self.last_trigger_minute = -1  # Schutz, damit Trigger nur einmal pro Minute passiert

        # --------------------------
        # Spektrometer-Daten
        # --------------------------
        self.start_wl, self.num_points = 380, 401  # wird beim Handshake überschrieben
        self.dark_reference = []  # Liste gleicher Länge wie num_points

        # --------------------------
        # Speicherpfad (GUI steuerbar)
        # --------------------------
        self.base_dir_var = tk.StringVar(value=SCRIPT_DIR)  # GUI-Entry
        self.base_dir = SCRIPT_DIR
        self.cam_base_dir = CAM_BASE_DIR
        self.dark_base_dir = DARK_BASE_DIR

        # --------------------------
        # Kamera-Live-View und Konfigurationen
        # --------------------------
        self.video_config = None
        self.still_config = None
        self.live_enabled = False          # Kamera-Livebild läuft?
        self.live_job = None               # Tkinter after()-Job-ID
        self.tk_live_img = None            # Referenz auf PhotoImage (sonst wird es „gegc“)

        # --------------------------
        # Locks (Thread-Sicherheit)
        # --------------------------
        self.cam_lock = threading.Lock()   # verhindert parallelen Zugriff auf Kamera
        self.spec_lock = threading.Lock()  # verhindert parallelen Zugriff auf Spektrometer

        # Helfer für Fotoaufnahme (Livebild pausieren)
        self._live_was_running_before_capture = False
        self._live_spec_thread = None

        # --------------------------
        # Kamera-Einstellungen (GUI Variablen)
        # --------------------------
        self.cam_mode_var = tk.StringVar(value="auto")       # "auto" / "manual"
        self.cam_exposure_us_var = tk.StringVar(value="10000")  # Exposure in µs
        self.cam_gain_var = tk.StringVar(value="1.0")           # AnalogueGain
        self.cam_awb_var = tk.BooleanVar(value=True)            # Auto White Balance

        # „Bildlook“-Parameter (werden als Controls gesetzt, wenn unterstützt)
        self.cam_brightness_var = tk.StringVar(value="0.0")
        self.cam_contrast_var   = tk.StringVar(value="1.0")
        self.cam_saturation_var = tk.StringVar(value="1.0")
        self.cam_sharpness_var  = tk.StringVar(value="1.0")

        # GUI erzeugen + Hardware initialisieren
        self.create_widgets()
        self.init_hardware()

    # ======================================================================
    # Logging-Ausgabe in die Log-Textbox (mit Farben über Tags)
    # ======================================================================
    def log(self, msg, color="white"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_area.config(state='normal')
        self.log_area.insert(tk.END, f"[{ts}] {msg}\n", color)
        self.log_area.tag_config("green", foreground="#44ff44")
        self.log_area.tag_config("yellow", foreground="#ffff44")
        self.log_area.tag_config("red", foreground="#ff4444")
        self.log_area.tag_config("cyan", foreground="#44ffff")
        self.log_area.config(state='disabled')
        self.log_area.see(tk.END)

    # ======================================================================
    # Speicherpfad: Ordner wählen / übernehmen
    # ======================================================================
    def browse_base_dir(self):
        """Öffnet Dialog zum Auswählen eines Basis-Ordners."""
        path = filedialog.askdirectory(initialdir=self.base_dir_var.get() or SCRIPT_DIR)
        if path:
            self.base_dir_var.set(path)

    def set_base_dir(self):
        """
        Übernimmt den in der GUI eingestellten Basisordner.
        Erstellt dort die Unterordner:
        - Wolkenbilder
        - Dunkelmessungen
        """
        new_base = (self.base_dir_var.get() or "").strip()
        if not new_base:
            messagebox.showerror("Fehler", "Bitte einen gültigen Speicherpfad eingeben.")
            return

        new_base = os.path.abspath(new_base)

        try:
            self.base_dir = new_base
            self.cam_base_dir = os.path.join(self.base_dir, "Wolkenbilder")
            self.dark_base_dir = os.path.join(self.base_dir, "Dunkelmessungen")

            os.makedirs(self.cam_base_dir, exist_ok=True)
            os.makedirs(self.dark_base_dir, exist_ok=True)

            self.log(f"Speicherpfad gesetzt: {self.base_dir}", "yellow")
        except Exception as e:
            messagebox.showerror("Fehler", f"Ordner konnten nicht erstellt werden:\n{e}")
            self.log(f"Fehler Speicherpfad: {e}", "red")

    # ======================================================================
    # GUI-Aufbau
    # ======================================================================
    def create_widgets(self):
        """
        Baut die komplette Oberfläche.
        Oben: Einstellungen (Spektrometer / Kamera / Logging / Live)
        Unten: Plot (Spektrum) + Kamera-Livebild + Log-Ausgabe
        """
        # Top Control Panel
        ctrl_frame = tk.Frame(self.root, pady=10)
        ctrl_frame.pack(side=tk.TOP, fill=tk.X, padx=15)

        # --------------------------
        # Speicherpfad-Gruppe
        # --------------------------
        path_f = tk.LabelFrame(ctrl_frame, text=" Speicherpfad ", padx=15, pady=10)
        path_f.pack(side=tk.LEFT, padx=5)

        tk.Label(path_f, text="Basis:").grid(row=0, column=0, sticky="w")
        self.entry_base = tk.Entry(path_f, textvariable=self.base_dir_var, width=28)
        self.entry_base.grid(row=0, column=1, padx=5)

        tk.Button(path_f, text="...", width=3, command=self.browse_base_dir).grid(row=0, column=2)
        tk.Button(path_f, text="Übernehmen", command=self.set_base_dir).grid(
            row=1, column=0, columnspan=3, sticky="ew", pady=(6, 0)
        )

        # --------------------------
        # Spektrometer-Einstellungen (ohne CIE)
        # --------------------------
        spec_settings_f = tk.LabelFrame(ctrl_frame, text=" Spektrometer-Einstellungen", padx=15, pady=10)
        spec_settings_f.pack(side=tk.LEFT, padx=5)

        # Integrationszeit: Auto vs Manuell
        exp_f = tk.LabelFrame(spec_settings_f, text=" Integrationszeit ", padx=12, pady=8)
        exp_f.grid(row=0, column=0, sticky="nw", padx=(0, 10))

        self.mode_var = tk.StringVar(value="auto")
        tk.Radiobutton(exp_f, text="Auto-Belichtung", variable=self.mode_var, value="auto").grid(row=0, column=0, sticky="w")
        tk.Radiobutton(exp_f, text="Manuell (ms):", variable=self.mode_var, value="manual").grid(row=1, column=0, sticky="w")

        self.entry_ms = tk.Entry(exp_f, width=8, justify="center")
        self.entry_ms.insert(0, "500")
        self.entry_ms.grid(row=1, column=1, padx=5)

        # Dunkelabgleich-Button
        dark_f = tk.LabelFrame(spec_settings_f, text=" Dunkelabgleich ", padx=12, pady=8)
        dark_f.grid(row=0, column=1, sticky="ne")

        tk.Button(dark_f, text="Dunkel-Abgleich", command=self.start_dark_thread, width=18).pack(pady=(0, 4))
        tk.Label(
            dark_f,
            text="Führt eine Dunkelmessung zur Rauschminderung durch.",
            fg="gray", justify="left"
        ).pack(anchor="w")

        # Spektrometer-Einstellungen anwenden
        tk.Button(
            spec_settings_f,
            text="Wende Spektrometeränderungen an",
            command=self.apply_hardware
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        tk.Label(
            spec_settings_f,
            text="Hinweis: Übernimmt Integrationszeit ins Spektrometer.",
            fg="gray"
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))

        # --------------------------
        # Kamera-Einstellungen
        # --------------------------
        cam_settings_f = tk.LabelFrame(ctrl_frame, text=" Kamera-Einstellungen ", padx=15, pady=10)
        cam_settings_f.pack(side=tk.LEFT, padx=5)

        # Belichtung: Auto vs Manuell + Exposure + Gain
        cam_exp_f = tk.LabelFrame(cam_settings_f, text=" Belichtung ", padx=12, pady=8)
        cam_exp_f.grid(row=0, column=0, sticky="nw", padx=(0, 10))

        tk.Radiobutton(cam_exp_f, text="Auto (AE)", variable=self.cam_mode_var, value="auto").grid(row=0, column=0, sticky="w")
        tk.Radiobutton(cam_exp_f, text="Manuell (µs):", variable=self.cam_mode_var, value="manual").grid(row=1, column=0, sticky="w")

        self.entry_cam_exposure = tk.Entry(cam_exp_f, width=10, justify="center", textvariable=self.cam_exposure_us_var)
        self.entry_cam_exposure.grid(row=1, column=1, padx=5)

        tk.Label(cam_exp_f, text="Gain:", fg="gray").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.entry_cam_gain = tk.Entry(cam_exp_f, width=10, justify="center", textvariable=self.cam_gain_var)
        self.entry_cam_gain.grid(row=2, column=1, padx=5, pady=(6, 0))

        # Bildlook: AWB + Brightness/Contrast/Saturation/Sharpness
        cam_img_f = tk.LabelFrame(cam_settings_f, text=" Bildlook ", padx=12, pady=8)
        cam_img_f.grid(row=0, column=1, sticky="ne")

        tk.Checkbutton(cam_img_f, text="Auto White Balance (AWB)", variable=self.cam_awb_var).grid(row=0, column=0, columnspan=2, sticky="w")

        tk.Label(cam_img_f, text="Brightness:", fg="gray").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.entry_cam_brightness = tk.Entry(cam_img_f, width=8, justify="center", textvariable=self.cam_brightness_var)
        self.entry_cam_brightness.grid(row=1, column=1, pady=(6, 0))

        tk.Label(cam_img_f, text="Contrast:", fg="gray").grid(row=2, column=0, sticky="w")
        self.entry_cam_contrast = tk.Entry(cam_img_f, width=8, justify="center", textvariable=self.cam_contrast_var)
        self.entry_cam_contrast.grid(row=2, column=1)

        tk.Label(cam_img_f, text="Saturation:", fg="gray").grid(row=3, column=0, sticky="w")
        self.entry_cam_saturation = tk.Entry(cam_img_f, width=8, justify="center", textvariable=self.cam_saturation_var)
        self.entry_cam_saturation.grid(row=3, column=1)

        tk.Label(cam_img_f, text="Sharpness:", fg="gray").grid(row=4, column=0, sticky="w")
        self.entry_cam_sharpness = tk.Entry(cam_img_f, width=8, justify="center", textvariable=self.cam_sharpness_var)
        self.entry_cam_sharpness.grid(row=4, column=1)

        # Kamera-Einstellungen anwenden
        tk.Button(
            cam_settings_f,
            text="Wende Kameraänderungen an",
            command=self.apply_camera_settings
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        tk.Label(
            cam_settings_f,
            text="Hinweis: Es werden nur Controls gesetzt, die deine Kamera unterstützt.",
            fg="gray"
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))

        # --------------------------
        # Logging Modus
        # --------------------------
        trig_f = tk.LabelFrame(ctrl_frame, text=" Logging Modus ", padx=12, pady=10)
        trig_f.pack(side=tk.LEFT, padx=5, fill=tk.Y)

        tk.Label(trig_f, text="Messintervall (min):").grid(row=0, column=0, sticky="w")
        self.entry_interval = tk.Entry(trig_f, width=8, justify="center")
        self.entry_interval.insert(0, "1")
        self.entry_interval.grid(row=0, column=1, padx=(8, 0), sticky="w")

        tk.Label(
            trig_f,
            text="Triggerungsintervall: Aufnahme von Wolkenkamera + Spektrometer bei jeder vollen Minute\n"
                 "(z.B. 5 = 00,05,10,... | 15 = 00,15,30,45). Muss ein Teiler von 60 sein.",
            fg="gray",
            justify="left"
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 6))

        self.btn_start = tk.Button(
            trig_f,
            text="START LOGGING",
            command=self.toggle_measurement,
            bg="#28a745",
            fg="white",
            font=("Arial", 10, "bold"),
            width=20,
            height=2
        )
        self.btn_start.grid(row=2, column=0, columnspan=2, pady=(2, 6), sticky="ew")

        # --------------------------
        # Live Modus
        # --------------------------
        live_f = tk.LabelFrame(ctrl_frame, text=" Live Modus ", padx=12, pady=10)
        live_f.pack(side=tk.LEFT, padx=5, fill=tk.Y)

        tk.Label(
            live_f,
            text="Zeigt Spektrum + Kamera dauerhaft live.\nKeine Speicherung.",
            fg="gray",
            justify="left"
        ).pack(anchor="w")

        self.btn_live = tk.Button(
            live_f,
            text="START LIVE",
            command=self.toggle_live_mode,
            bg="#007bff",
            fg="white",
            font=("Arial", 10, "bold"),
            width=20,
            height=2
        )
        self.btn_live.pack(pady=(6, 0), fill="x")

        # ======================================================================
        # Hauptbereich: Plot | Livebild | Log
        # ======================================================================
        main_container = tk.Frame(self.root)
        main_container.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)

        main_container.columnconfigure(0, weight=1, uniform="vis")
        main_container.columnconfigure(1, weight=1, uniform="vis")
        main_container.columnconfigure(2, weight=0)
        main_container.rowconfigure(0, weight=1)

        # ---- Plot (links): Spektrum
        plot_frame = tk.Frame(main_container)
        plot_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        self.fig = Figure(figsize=(7, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_xlabel("Wellenlänge [nm]")
        self.ax.set_ylabel("Intensität [W/m²]")
        self.ax.set_title("Spektrale Strahlungsverteilung (Echtzeit)")
        self.ax.grid(True, linestyle='--', alpha=0.6)

        self.line, = self.ax.plot([], [], color='#d9534f', lw=1.5)
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # ---- Live Kamera (mitte)
        cam_frame = tk.LabelFrame(main_container, text=" Live Kamera ", padx=6, pady=6)
        cam_frame.grid(row=0, column=1, sticky="nsew", padx=(0, 10))

        self.cam_label = tk.Label(cam_frame, bg="black")
        self.cam_label.pack(fill=tk.BOTH, expand=True)

        # ---- Log (rechts)
        self.log_area = scrolledtext.ScrolledText(
            main_container,
            width=50,
            state='disabled',
            bg="#1e1e1e",
            fg="#e0e0e0",
            font=("Consolas", 9)
        )
        self.log_area.grid(row=0, column=2, sticky="ns")

    # ======================================================================
    # Logging-Intervall prüfen (muss Teiler von 60 sein)
    # ======================================================================
    def get_interval_minutes(self):
        """
        Liest das Intervall aus dem GUI-Feld.
        Regeln:
        - mindestens 1 Minute
        - 60 muss durch Intervall teilbar sein (z.B. 1,2,3,4,5,6,10,12,15,20,30,60)
        """
        try:
            v = int((self.entry_interval.get() or "1").strip())
        except Exception:
            v = 1
        v = max(1, v)

        if 60 % v != 0:
            raise ValueError(
                f"Ungültiges Intervall: {v} min. Für vergleichbare Zeiten muss 60 durch das Intervall teilbar sein "
                f"(erlaubt z.B. 1,2,3,4,5,6,10,12,15,20,30,60)."
            )
        return v

    def validate_interval_or_show_error(self) -> bool:
        """Validiert Intervall und zeigt ggf. Messagebox + Log an."""
        try:
            _ = self.get_interval_minutes()
            return True
        except ValueError as e:
            messagebox.showerror("Intervall-Fehler", str(e))
            self.log(str(e), "red")
            return False

    # ======================================================================
    # Stop Logging between 22:00 and 6:00 
    # ======================================================================

    def is_quiet_hours(self, now: datetime) -> bool:
        """
        True = innerhalb Sperrzeit (22:00–06:00) -> kein Logging
        """
        h = now.hour
        return (h >= 22) or (h < 6)

    # ======================================================================
    # Spektrometer: Port finden (Auto-Scan)
    # ======================================================================
    def find_spectrometer_port(self, baud=115200, timeout=1.5):
        """
        Sucht nach Spektrometer an ttyACM/ttyUSB Ports.
        Vorgehen:
        - Ports scannen
        - Port öffnen
        - Handshake-Paket senden (0x0F)
        - Antwort prüfen und Spektralbereich (Start/Ende) auslesen
        """
        ports = [p.device for p in list_ports.comports()
                 if ("ttyACM" in p.device) or ("ttyUSB" in p.device)]

        for dev in ports:
            ser = None
            try:
                ser = serial.Serial(dev, baud, timeout=timeout)
                time.sleep(2)  # warten, bis das Gerät „bereit“ ist

                ser.reset_input_buffer()
                ser.write(self.build_packet(0x0F))  # Handshake / Info
                res = ser.read(100)

                # Erwartung: mindestens 13 Bytes, und Start/Ende stecken an Position 6..10
                if res and len(res) >= 13:
                    s, e = struct.unpack('<HH', res[6:10])
                    if 200 <= s <= 1000 and 200 <= e <= 2000 and e >= s:
                        return dev, ser, s, e  # Port passt

                ser.close()
            except Exception:
                try:
                    if ser:
                        ser.close()
                except Exception:
                    pass

        return None, None, None, None

    # ======================================================================
    # Hardware initialisieren (Spektrometer + Kamera)
    # ======================================================================
    def init_hardware(self):
        """Verbindet Spektrometer und startet die Kamera mit Live-Video-Konfiguration."""
        # ---- Spektrometer
        try:
            dev, ser, s, e = self.find_spectrometer_port()
            if ser:
                self.ser = ser
                self.start_wl, self.num_points = s, e - s + 1
                self.connected = True
                self.log("Handshake: Spektrometer erkannt", "green")
                self.log(f"  Port: {dev}", "green")
                self.log(f"  Bereich: {s}nm - {e}nm", "green")
                self.log("Hardware: Spektrometer bereit.", "green")
            else:
                self.log("Hardware: Spektrometer nicht gefunden (kein passender Port).", "red")
        except Exception as ex:
            self.log(f"Hardware: Spektrometer-Scan Fehler: {ex}", "red")

        # ---- Kamera
        try:
            self.picam = Picamera2()

            # Video-Konfig für Livebild
            self.video_config = self.picam.create_video_configuration(
                main={"size": (640, 480), "format": "RGB888"}
            )
            # Still-Konfig für Fotos (höhere Qualität, je nach Kamera)
            self.still_config = self.picam.create_still_configuration()

            self.picam.configure(self.video_config)
            self.picam.start()
            self.log("Hardware: Kamera bereit.", "green")
        except Exception as e:
            self.picam = None
            self.log(f"Hardware: Kamera-Initialisierung fehlgeschlagen: {e}", "red")

    # ======================================================================
    # Spektrometer: Paket bauen (Protokoll CC 01 ... + checksum + CRLF)
    # ======================================================================
    def build_packet(self, cmd, data=None):
        """
        Baut ein Kommando-Paket für das Spektrometer.
        Format:
        - Header: 0xCC, 0x01
        - Länge (3 Byte, little endian)
        - cmd (1 Byte)
        - data (0..n Bytes)
        - checksum (summe aller bytes & 0xFF)
        - 0x0D, 0x0A (CR LF)
        """
        if data is None:
            data = []
        l = 9 + len(data)
        p = bytes([0xCC, 0x01, l & 0xFF, (l >> 8) & 0xFF, (l >> 16) & 0xFF, cmd] + data)
        return p + bytes([sum(p) & 0xFF, 0x0D, 0x0A])

    # ======================================================================
    # Spektrometer-Einstellungen anwenden (Integrationszeit Auto/Manuell)
    # ======================================================================
    def apply_hardware(self):
        """
        Überträgt Spektrometer-Einstellungen aus der GUI an das Gerät:
        - Auto-Belichtung ein/aus (cmd 0x0A)
        - Wenn manuell: Integrationszeit in µs setzen (cmd 0x0C)
        """
        if not self.connected:
            return
        try:
            with self.spec_lock:
                is_auto = (self.mode_var.get() == "auto")
                self.ser.write(self.build_packet(0x0A, [0x01 if is_auto else 0x00]))

                if not is_auto:
                    # GUI ist ms -> Spektrometer erwartet µs
                    t_us = int(float(self.entry_ms.get()) * 1000)
                    self.ser.write(self.build_packet(0x0C, list(struct.pack('<I', t_us))))

            self.log("Spektrometer: Einstellungen angewendet.", "yellow")
        except Exception:
            # bewusst still – du kannst hier ggf. genauer loggen
            pass

    # ======================================================================
    # Kamera: Controls abfragen + sicher setzen
    # ======================================================================
    def _get_cam_controls(self) -> dict:
        """Liest (falls vorhanden) die unterstützten camera_controls aus Picamera2."""
        try:
            return dict(getattr(self.picam, "camera_controls", {}) or {})
        except Exception:
            return {}

    def _safe_set_controls(self, controls: dict):
        """
        Setzt Kamera-Controls „sicher“:
        - Wenn Kamera eine Liste unterstützter Controls liefert: nur diese setzen
        - Nicht unterstützte Keys werden geloggt und übersprungen
        """
        if not self.picam:
            self.log("Kamera: nicht verfügbar.", "red")
            return

        available = self._get_cam_controls()

        # Falls wir keine Liste bekommen: versuchen wir es trotzdem „blind“
        if not available:
            try:
                self.picam.set_controls(controls)
                return
            except Exception as e:
                self.log(f"Kamera: set_controls fehlgeschlagen: {e}", "red")
                return

        filtered = {}
        skipped = []
        for k, v in controls.items():
            if k in available:
                filtered[k] = v
            else:
                skipped.append(k)

        if skipped:
            self.log(f"Kamera: Controls nicht unterstützt (übersprungen): {', '.join(skipped)}", "yellow")

        if not filtered:
            return

        try:
            self.picam.set_controls(filtered)
        except Exception as e:
            self.log(f"Kamera: Controls setzen fehlgeschlagen: {e}", "red")

    def apply_camera_settings(self):
        """
        Nimmt die Werte aus der GUI und setzt Kamera-Controls.
        Typisch (je nach Kamera):
        - AeEnable / ExposureTime / AnalogueGain
        - AwbEnable
        - Brightness / Contrast / Saturation / Sharpness
        """
        if not self.picam:
            self.log("Kamera: nicht verfügbar.", "red")
            return

        # kleine Helfer zum sicheren Parsen
        def _to_float(s, default):
            try:
                return float((s or "").strip())
            except Exception:
                return default

        def _to_int(s, default):
            try:
                return int(float((s or "").strip()))
            except Exception:
                return default

        mode = self.cam_mode_var.get()
        exposure_us = _to_int(self.cam_exposure_us_var.get(), 10000)
        gain = _to_float(self.cam_gain_var.get(), 1.0)
        awb = bool(self.cam_awb_var.get())

        brightness = _to_float(self.cam_brightness_var.get(), 0.0)
        contrast   = _to_float(self.cam_contrast_var.get(), 1.0)
        saturation = _to_float(self.cam_saturation_var.get(), 1.0)
        sharpness  = _to_float(self.cam_sharpness_var.get(), 1.0)

        controls = {}

        # Auto-Exposure an/aus + ggf. ExposureTime setzen
        if mode == "auto":
            controls["AeEnable"] = True
        else:
            controls["AeEnable"] = False
            controls["ExposureTime"] = max(1, exposure_us)

        # Gain / AWB / Bildlook
        controls["AnalogueGain"] = max(0.0, gain)
        controls["AwbEnable"] = awb
        controls["Brightness"] = brightness
        controls["Contrast"] = contrast
        controls["Saturation"] = saturation
        controls["Sharpness"] = sharpness

        # Lock: Livebild greift auch auf die Kamera zu
        with self.cam_lock:
            self._safe_set_controls(controls)

        self.log("Kamera: Einstellungen angewendet.", "yellow")

    # ======================================================================
    # Kamera-Livebild (Tkinter after()-Loop)
    # ======================================================================
    def start_live_view(self):
        """Startet das periodische Aktualisieren des Livebilds."""
        if not self.picam:
            self.log("Livebild: Kamera nicht verfügbar.", "red")
            return
        if self.live_enabled:
            return
        self.live_enabled = True
        self.update_live_view()

    def stop_live_view(self):
        """Stoppt das periodische Aktualisieren des Livebilds."""
        self.live_enabled = False
        if self.live_job is not None:
            try:
                self.root.after_cancel(self.live_job)
            except Exception:
                pass
            self.live_job = None

    def _frame_to_pil_rgb(self, frame: np.ndarray) -> Image.Image:
        """
        Konvertiert Numpy-Frame zu PIL Image.
        Hinweis: Bei manchen Setups ist das Kanal-Layout BGR statt RGB -> wir drehen Kanäle um.
        """
        if frame.ndim == 3 and frame.shape[2] == 3:
            frame = frame[:, :, ::-1]
        return Image.fromarray(frame)

    def update_live_view(self):
        """
        Holt ein Frame aus der Kamera und zeigt es im Label an.
        Läuft regelmäßig über self.root.after(...).
        """
        if not self.live_enabled or not self.picam:
            return

        # Falls z.B. ein Foto gerade exklusiven Zugriff hat: kurz warten
        if self.cam_lock.locked():
            self.live_job = self.root.after(100, self.update_live_view)
            return

        try:
            with self.cam_lock:
                frame = self.picam.capture_array("main")

            img = self._frame_to_pil_rgb(frame)

            # Zielgröße vom Label (responsive)
            w = max(1, self.cam_label.winfo_width())
            h = max(1, self.cam_label.winfo_height())

            # Bild proportional skalieren („contain“ + letterbox)
            img_ratio = img.width / img.height
            box_ratio = w / h

            if img_ratio > box_ratio:
                new_w = w
                new_h = int(w / img_ratio)
            else:
                new_h = h
                new_w = int(h * img_ratio)

            img = img.resize((max(1, new_w), max(1, new_h)), Image.LANCZOS)

            # Schwarzer Hintergrund + Bild zentrieren
            canvas = Image.new("RGB", (w, h), (0, 0, 0))
            x0 = (w - img.width) // 2
            y0 = (h - img.height) // 2
            canvas.paste(img, (x0, y0))

            # Wichtig: Referenz speichern (sonst wird es gelöscht)
            self.tk_live_img = ImageTk.PhotoImage(canvas)
            self.cam_label.configure(image=self.tk_live_img)

        except Exception as e:
            self.log(f"Livebild-Fehler: {e}", "red")

        # Nächste Aktualisierung
        self.live_job = self.root.after(150, self.update_live_view)

    # ======================================================================
    # Live Modus: dauerhaft Spektrum + Kamera (keine Speicherung)
    # ======================================================================
    def toggle_live_mode(self):
        """
        Schaltet den Live-Modus ein/aus.
        Regeln:
        - Live und Logging dürfen nicht gleichzeitig laufen.
        - Live startet: Logging wird ggf. gestoppt.
        - Live stoppt: Kamera-Livebild wird gestoppt (wenn Logging nicht läuft).
        """
        if not self.is_live_mode:
            # Live startet -> Logging muss aus
            if self.is_measuring:
                self.toggle_measurement()

            # Spektrometer-Einstellungen anwenden (Integrationszeit)
            self.apply_hardware()

            self.is_live_mode = True
            self.btn_live.config(text="STOP LIVE", bg="#6c757d")
            self.log(">>> LIVE MODUS AKTIVIERT (Spektrum + Kamera dauerhaft)", "cyan")

            # Kamera-Livebild starten
            self.start_live_view()

            # Spektrum in Thread auslesen (damit GUI nicht blockiert)
            self._live_spec_thread = threading.Thread(target=self.live_spectrum_loop, daemon=True)
            self._live_spec_thread.start()

        else:
            # Live stoppt
            self.is_live_mode = False
            self.btn_live.config(text="START LIVE", bg="#007bff")
            self.log(">>> LIVE MODUS GESTOPPT", "cyan")

            if not self.is_measuring:
                self.stop_live_view()

    def live_spectrum_loop(self):
        """
        Thread-Schleife für Live-Spektrum:
        - sendet Messkommando
        - liest Antwort
        - aktualisiert Plot
        """
        if not self.connected:
            self.root.after(0, lambda: self.log("Live-Spektrum: Spektrometer nicht verbunden.", "red"))
            self.root.after(0, lambda: self.btn_live.config(text="START LIVE", bg="#007bff"))
            self.is_live_mode = False
            return

        while self.is_live_mode:
            try:
                with self.spec_lock:
                    # Messung starten (cmd 0x32)
                    self.ser.reset_input_buffer()
                    self.ser.write(self.build_packet(0x32))

                    # Antwort sammeln (Timeout 12s)
                    data, start_t = b"", time.time()
                    while (time.time() - start_t) < 12 and self.is_live_mode:
                        if self.ser.in_waiting:
                            data += self.ser.read(self.ser.in_waiting)
                            # grob: Header + Spektrum + CRLF
                            if len(data) >= (213 + self.num_points * 2 + 3):
                                break
                        time.sleep(0.01)

                if not self.is_live_mode:
                    break

                # Daten auswerten
                if len(data) >= 213:
                    # Exponent n (Skalierung 10^n) steht bei 211..213
                    n = struct.unpack('<h', data[211:213])[0]

                    # Spektrumwerte (uint16) beginnen bei 213
                    raw = struct.unpack('<' + 'H' * self.num_points, data[213:213 + self.num_points * 2])
                    scan = [v / (10 ** n) for v in raw]

                    # Dark Reference abziehen (wenn vorhanden)
                    final = [max(0, v - self.dark_reference[i]) if self.dark_reference else v
                             for i, v in enumerate(scan)]

                    # Plot-Update muss im GUI-Thread passieren -> root.after(0,...)
                    self.root.after(0, lambda d=final: self.update_plot(d))

                # kurze Pause (reduziert CPU/Serienlast)
                time.sleep(0.15)

            except Exception as e:
                self.root.after(0, lambda: self.log(f"Live-Spektrum Fehler: {e}", "red"))
                time.sleep(0.5)

    # ======================================================================
    # Logging Modus: Spektrum + Foto zu festen Minuten speichern
    # ======================================================================
    def toggle_measurement(self):
        """
        Schaltet Logging-Modus ein/aus.
        Regeln:
        - Logging und Live dürfen nicht gleichzeitig laufen.
        - Start: Intervall prüfen, Einstellungen anwenden, Kamera-Livebild starten, Thread starten.
        - Stop: Flag setzen, Button zurück, Livebild stoppen (wenn Live-Modus nicht aktiv).
        """
        if not self.is_measuring:
            # Logging startet -> Live muss aus
            if self.is_live_mode:
                self.toggle_live_mode()

            if not self.validate_interval_or_show_error():
                return

            self.apply_hardware()

            self.is_measuring = True
            self.btn_start.config(text="STOPP LOGGING", bg="#dc3545")

            interval = self.get_interval_minutes()
            self.log(f">>> SYNC-MODUS AKTIVIERT (Alle {interval} Minute(n))", "cyan")

            # Kamera-Livebild auch im Logging anzeigen
            self.start_live_view()

            threading.Thread(target=self.main_sync_loop, daemon=True).start()

        else:
            self.is_measuring = False
            self.btn_start.config(text="START LOGGING", bg="#28a745")
            if not self.is_live_mode:
                self.stop_live_view()

    def main_sync_loop(self):
        """
        Thread-Schleife für Logging:
        - Prüft fortlaufend die Uhrzeit
        - Bei Sekunde==0 und Minute%Intervall==0: Trigger
        - Startet parallel Threads für Foto + Spektrum
        """
        while self.is_measuring:
            now = datetime.now()

            # Intervall kann im laufenden Betrieb geändert werden -> validieren
            try:
                interval = self.get_interval_minutes()
            except ValueError as e:
                self.root.after(0, lambda: messagebox.showerror("Intervall-Fehler", str(e)))
                self.log(str(e), "red")
                self.is_measuring = False
                self.root.after(0, lambda: self.btn_start.config(text="START LOGGING", bg="#28a745"))
                return

            # Trigger nur einmal pro passender Minute (aber nicht in der Sperrzeit 22–06 Uhr)
            if now.second == 0 and (now.minute % interval == 0) and now.minute != self.last_trigger_minute:
                if self.is_quiet_hours(now):
                    self.last_trigger_minute = now.minute  # verhindert Spam in derselben Minute
                    if now.minute % interval == 0:
                        self.log(f"--- Sperrzeit aktiv (22–06 Uhr): Logging übersprungen um {now.strftime('%H:%M')}:00 ---", "yellow")
                    time.sleep(1.2)  # kurz warten, damit second==0 nicht mehrfach feuert
                    continue
                
                self.last_trigger_minute = now.minute

                # Dateiname/Ordner: nach Datum und Uhrzeit
                ts_folder = now.strftime("%Y-%m-%d")
                ts_file = now.strftime("%H%M%S")

                self.log(f"--- Trigger: {now.strftime('%H:%M')}:00 (Intervall {interval}min) ---", "yellow")

                # Foto + Spektrum parallel, damit es schneller geht
                t1 = threading.Thread(target=self.capture_photo, args=(ts_folder, ts_file), daemon=True)
                t2 = threading.Thread(target=self.capture_spectrum, args=(ts_folder, ts_file), daemon=True)
                t1.start()
                t2.start()

                # „Schlaf“ damit nicht mehrfach getriggert wird (zusätzlich zur last_trigger_minute)
                time.sleep(50)

            time.sleep(0.05)

    # ======================================================================
    # Fotoaufnahme (Logging)
    # ======================================================================
    def _pause_live_for_capture(self):
        """Merkt sich, ob Livebild an war und stoppt es temporär."""
        self._live_was_running_before_capture = self.live_enabled
        if self._live_was_running_before_capture:
            self.stop_live_view()

    def _resume_live_after_capture(self):
        """Startet Livebild wieder, wenn es vorher lief und ein Modus aktiv ist."""
        if self._live_was_running_before_capture and (self.is_measuring or self.is_live_mode):
            self.start_live_view()
        self._live_was_running_before_capture = False

    def capture_photo(self, folder, filename):
        """
        Nimmt ein Foto auf und speichert es ab:
        - Ordner: <cam_base_dir>/<YYYY-MM-DD>/
        - Datei: Wolkenbild_<HHMMSS>.png

        Wichtig:
        - Picamera2 muss vor configure() gestoppt werden.
        - Danach wieder zurück auf Video-Konfiguration für Livebild.
        """
        if not self.picam:
            return

        with self.cam_lock:  # exklusiver Zugriff
            try:
                self._pause_live_for_capture()

                path = os.path.join(self.cam_base_dir, folder)
                os.makedirs(path, exist_ok=True)
                full_fn = f"Wolkenbild_{filename}.png"
                out_path = os.path.join(path, full_fn)

                # Kamera stoppen bevor configure/switch
                try:
                    self.picam.stop()
                except Exception:
                    pass

                # Still aufnehmen
                try:
                    if self.still_config is not None:
                        self.picam.configure(self.still_config)
                    self.picam.start()
                    self.picam.capture_file(out_path)
                finally:
                    # Zurück zum Live-Video
                    try:
                        self.picam.stop()
                    except Exception:
                        pass
                    try:
                        if self.video_config is not None:
                            self.picam.configure(self.video_config)
                        self.picam.start()
                    except Exception as e:
                        self.log(f"Kamera: Fehler beim Zurückschalten auf Live-Modus: {e}", "red")

                self.log(f"Kamera: {full_fn} gesichert.", "cyan")

            except Exception as e:
                self.log(f"Fehler Kamera: {e}", "red")
            finally:
                self._resume_live_after_capture()

    # ======================================================================
    # Spektrum aufnehmen (Logging -> CSV speichern)
    # ======================================================================
    def capture_spectrum(self, folder, filename):
        """
        Führt eine Spektrummessung aus und speichert eine CSV:
        - Ordner: <base_dir>/Messungen_<YYYY-MM-DD>/
        - Datei: m_<HHMMSS>.csv

        CSV enthält:
        - 50 Parameter (PARAM_NAMES)
        - Spektrum als Tabelle: nm; Wert
        """
        if not self.connected:
            return

        try:
            with self.spec_lock:
                self.ser.reset_input_buffer()
                self.ser.write(self.build_packet(0x32))

                data, start_t = b"", time.time()
                while (time.time() - start_t) < 12:
                    if self.ser.in_waiting:
                        data += self.ser.read(self.ser.in_waiting)
                        if len(data) >= (213 + self.num_points * 2 + 3):
                            break
                    time.sleep(0.01)

            if len(data) >= 213:
                n = struct.unpack('<h', data[211:213])[0]
                raw = struct.unpack('<' + 'H' * self.num_points, data[213:213 + self.num_points * 2])
                scan = [v / (10 ** n) for v in raw]
                final = [max(0, v - self.dark_reference[i]) if self.dark_reference else v
                         for i, v in enumerate(scan)]

                # Plot aktualisieren
                self.root.after(0, lambda: self.update_plot(final))

                # Speicherpfad
                path = os.path.join(self.base_dir, f"Messungen_{folder}")
                os.makedirs(path, exist_ok=True)
                full_fn = f"m_{filename}.csv"

                # 50 Float-Parameter sind im Paketbereich 11..211
                floats = struct.unpack('<' + 'f' * 50, data[11:211])

                with open(os.path.join(path, full_fn), 'w', newline='') as f:
                    w = csv.writer(f, delimiter=';')
                    for name, val in zip(PARAM_NAMES, floats):
                        w.writerow([name, f"{val:.4f}"])

                    w.writerow(["--- SPEKTRUM (nm; W/m2) ---"])
                    for i, v in enumerate(final):
                        w.writerow([self.start_wl + i, f"{v:.6f}"])

                self.log(f"Spektrum: {full_fn} gesichert.", "green")

        except Exception as e:
            self.log(f"Fehler Spektrometer: {e}", "red")

    # ======================================================================
    # Plot aktualisieren
    # ======================================================================
    def update_plot(self, data):
        """Setzt neue X/Y Daten in die Linie und skaliert Achsen automatisch."""
        x_vals = [self.start_wl + i for i in range(len(data))]
        self.line.set_data(x_vals, data)
        self.ax.relim()
        self.ax.autoscale_view()
        self.canvas.draw()

    # ======================================================================
    # Dunkelabgleich (Dark Reference)
    # ======================================================================
    def start_dark_thread(self):
        """Startet Dark Calibration in separatem Thread (GUI bleibt flüssig)."""
        threading.Thread(target=self.perform_dark_calib, daemon=True).start()

    def perform_dark_calib(self):
        """
        Führt 3 Dunkelmessungen aus:
        - Benutzer soll Sensor lichtdicht abdecken
        - 3 Messungen -> Mittelwert pro Wellenlänge
        - Ergebnis in self.dark_reference speichern
        - zusätzlich als CSV archivieren
        """
        messagebox.showinfo("Dunkelabgleich", "Sensor bitte lichtdicht abdecken!")
        self.log("Dunkelabgleich: Referenzmessung läuft...", "cyan")

        all_s = []
        now = datetime.now()
        ts_f = now.strftime("%Y-%m-%d")
        ts_t = now.strftime("%H%M%S")

        try:
            for i in range(3):
                with self.spec_lock:
                    self.ser.reset_input_buffer()
                    self.ser.write(self.build_packet(0x32))
                    time.sleep(2.5)
                    res = self.ser.read(2500)

                if len(res) > 213:
                    n = struct.unpack('<h', res[211:213])[0]
                    raw = struct.unpack('<' + 'H' * self.num_points, res[213:213 + self.num_points * 2])
                    all_s.append([v / (10 ** n) for v in raw])
                    self.log(f" Dunkel-Probe {i + 1}/3 aufgenommen.", "cyan")

            if len(all_s) == 3:
                # Mittelwert pro Wellenlänge
                self.dark_reference = [sum(col) / 3 for col in zip(*all_s)]

                os.makedirs(self.dark_base_dir, exist_ok=True)
                dark_fn = f"dark_{ts_f}_{ts_t}.csv"

                with open(os.path.join(self.dark_base_dir, dark_fn), 'w', newline='') as f:
                    w = csv.writer(f, delimiter=';')
                    w.writerow(["Zeitpunkt", now.strftime("%Y-%m-%d %H:%M:%S")])
                    w.writerow(["Wellenlaenge", "Dunkelwert (W/m2)"])
                    for i, v in enumerate(self.dark_reference):
                        w.writerow([self.start_wl + i, f"{v:.6f}"])

                self.log(f"Dunkel-CSV archiviert: {dark_fn}", "green")
                messagebox.showinfo("Erfolg", "Dunkelabgleich abgeschlossen.")

        except Exception as e:
            self.log(f"Dunkel-Fehler: {e}", "red")


# ======================================================================
# Programmstart
# ======================================================================
if __name__ == "__main__":
    root = tk.Tk()
    app = SpectrometerApp(root)
    root.mainloop()
