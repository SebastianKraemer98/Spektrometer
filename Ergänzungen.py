import sys
import os
import struct, time, csv, threading
from datetime import datetime

# --- HARDWARE-IMPORTE ---
try:
    import serial
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    import numpy as np
    from picamera2 import Picamera2
    from PIL import Image, ImageTk 
except ImportError as e:
    print(f"\n! FEHLER: Modul fehlt: {e}")
    sys.exit()

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

# --- PFADE ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CAM_BASE_DIR = os.path.join(SCRIPT_DIR, "Wolkenbilder")

PARAM_NAMES = ["X", "Y", "Z", "x", "y", "u", "v", "u_prime", "v_prime", "Tc_CCT", "Nit", "lux", "Ee"] # Gekürzt für Übersicht

class SpectrometerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PJG Spectrometer & Cloud-Cam v9.5 Extreme")
        self.root.geometry("1350x850")
        
        self.ser = None
        self.picam = None
        self.connected = False
        self.is_measuring = False
        self.last_trigger_minute = -1
        self.start_wl, self.num_points = 380, 401 
        self.dark_reference = []

        self.create_widgets()
        self.init_hardware()
        
        # Start der getrennten Live-Threads
        threading.Thread(target=self.live_camera_worker, daemon=True).start()
        threading.Thread(target=self.live_spectrometer_worker, daemon=True).start()

    def log(self, msg, color="white"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_area.config(state='normal')
        self.log_area.insert(tk.END, f"[{ts}] {msg}\n", color)
        self.log_area.tag_config("green", foreground="#44ff44")
        self.log_area.tag_config("cyan", foreground="#44ffff")
        self.log_area.config(state='disabled')
        self.log_area.see(tk.END)

    def create_widgets(self):
        ctrl_frame = tk.Frame(self.root, pady=10)
        ctrl_frame.pack(side=tk.TOP, fill=tk.X, padx=15)
        
        # Einstellungen
        exp_f = tk.LabelFrame(ctrl_frame, text=" Steuerung ", padx=10, pady=5)
        exp_f.pack(side=tk.LEFT, padx=5)
        self.mode_var = tk.StringVar(value="auto")
        tk.Radiobutton(exp_f, text="Auto", variable=self.mode_var, value="auto").grid(row=0, column=0)
        self.entry_ms = tk.Entry(exp_f, width=5); self.entry_ms.insert(0, "500")
        self.entry_ms.grid(row=0, column=1)
        tk.Label(exp_f, text="Intervall (Min):").grid(row=0, column=2, padx=5)
        self.entry_interval = tk.Entry(exp_f, width=4); self.entry_interval.insert(0, "1")
        self.entry_interval.grid(row=0, column=3)

        # Modus
        disp_f = tk.LabelFrame(ctrl_frame, text=" Modus ", padx=10, pady=5)
        disp_f.pack(side=tk.LEFT, padx=5)
        self.display_mode = tk.StringVar(value="logging")
        tk.Radiobutton(disp_f, text="LIVE-MODUS", variable=self.display_mode, value="live").pack(side=tk.LEFT)
        tk.Radiobutton(disp_f, text="Logging", variable=self.display_mode, value="logging").pack(side=tk.LEFT)

        tk.Button(ctrl_frame, text="Sync Hardware", command=self.apply_hardware).pack(side=tk.LEFT, padx=10)
        
        self.btn_start = tk.Button(ctrl_frame, text="START LOGGING", command=self.toggle_measurement, bg="#28a745", fg="white", width=15)
        self.btn_start.pack(side=tk.LEFT, padx=5)

        # Main Layout
        main_container = tk.Frame(self.root)
        main_container.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)
        
        # Grafik
        left_side = tk.Frame(main_container)
        left_side.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.fig = Figure(figsize=(5, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_ylim(0, 1) # Initialer Scale
        self.line, = self.ax.plot([], [], color='#d9534f', lw=1.5)
        self.canvas = FigureCanvasTkAgg(self.fig, master=left_side)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # Rechts: Kamera & Log
        self.right_side = tk.Frame(main_container, width=450)
        self.right_side.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        self.right_side.pack_propagate(False)

        self.img_label = tk.Label(self.right_side, bg="black", height=300)
        self.img_label.pack(side=tk.TOP, fill=tk.X)

        self.log_area = scrolledtext.ScrolledText(self.right_side, bg="#1e1e1e", fg="#e0e0e0", font=("Consolas", 9), state='disabled')
        self.log_area.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(10,0))

    def init_hardware(self):
        try:
            self.ser = serial.Serial('/dev/ttyACM0', 115200, timeout=0.01)
            time.sleep(1)
            self.connected = True
            self.log("Spektrometer verbunden.", "green")
        except: self.log("Spektrometer nicht gefunden.", "red")

        try:
            self.picam = Picamera2()
            self.picam.configure(self.picam.create_still_configuration())
            self.picam.start()
            self.log("Kamera bereit.", "green")
        except: self.log("Kamera Fehler.", "red")

    def apply_hardware(self):
        if not self.connected: return
        is_auto = (self.mode_var.get() == "auto")
        self.ser.write(self.build_packet(0x0A, [0x01 if is_auto else 0x00]))
        self.log("Sync gesendet.", "cyan")

    def build_packet(self, cmd, data=None):
        if data is None: data = []
        l = 9 + len(data)
        p = bytes([0xCC, 0x01, l&0xFF, (l>>8)&0xFF, (l>>16)&0xFF, cmd] + data)
        return p + bytes([sum(p)&0xFF, 0x0D, 0x0A])

    # --- LIVE WORKERS (HINTERGRUND) ---
    def live_camera_worker(self):
        while True:
            if self.display_mode.get() == "live" and self.picam and not self.is_measuring:
                try:
                    frame = self.picam.capture_array()
                    img = Image.fromarray(frame)
                    img = img.resize((450, 300), Image.Resampling.NEAREST)
                    photo = ImageTk.PhotoImage(img)
                    self.root.after(0, self.update_img_gui, photo)
                except: pass
            time.sleep(0.03) # ~30 FPS Ziel

    def live_spectrometer_worker(self):
        while True:
            if self.display_mode.get() == "live" and self.connected and not self.is_measuring:
                self.fetch_spectrum_data(save=False)
            time.sleep(0.05)

    def update_img_gui(self, photo):
        self.img_label.config(image=photo)
        self.img_label.image = photo

    def fetch_spectrum_data(self, save=False, folder=None, filename=None):
        try:
            self.ser.reset_input_buffer()
            self.ser.write(self.build_packet(0x32))
            
            data = b""
            start_t = time.time()
            # Warten auf komplettes Paket (ca. 1000 Bytes)
            while (time.time() - start_t) < 0.5:
                if self.ser.in_waiting:
                    data += self.ser.read(self.ser.in_waiting)
                    if len(data) >= (213 + self.num_points*2): break
            
            if len(data) >= 213 + self.num_points*2:
                n = struct.unpack('<h', data[211:213])[0]
                raw = struct.unpack('<' + 'H'*self.num_points, data[213:213+self.num_points*2])
                final = [v / (10**n) for v in raw]
                
                self.root.after(0, lambda: self.update_plot(final))
                
                if save:
                    # Hier CSV Logik (analog zu vorher)
                    pass
        except: pass

    def update_plot(self, data):
        x = [self.start_wl + i for i in range(len(data))]
        self.line.set_data(x, data)
        self.ax.relim()
        self.ax.autoscale_view()
        self.canvas.draw_idle()

    def toggle_measurement(self):
        if not self.is_measuring:
            self.is_measuring = True
            self.btn_start.config(text="STOPP", bg="#dc3545")
            threading.Thread(target=self.logging_loop, daemon=True).start()
        else:
            self.is_measuring = False
            self.btn_start.config(text="START LOGGING", bg="#28a745")

    def logging_loop(self):
        while self.is_measuring:
            now = datetime.now()
            interv = int(self.entry_interval.get())
            if now.second == 0 and now.minute % interv == 0:
                self.log(f"Trigger {now.minute}min", "cyan")
                # Foto und Spektrum speichern...
                time.sleep(55)
            time.sleep(0.1)

if __name__ == "__main__":
    root = tk.Tk(); app = SpectrometerApp(root); root.mainloop()