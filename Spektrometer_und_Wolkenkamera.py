import sys
import os
import struct, time, csv, threading
from datetime import datetime

# --- HARDWARE-IMPORTE & PRÜFUNG ---
try:
    import serial
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    import numpy as np
    from picamera2 import Picamera2
except ImportError as e:
    print(f"\n! FEHLER: Modul fehlt: {e}")
    print("Bitte installieren mit: pip install pyserial matplotlib numpy picamera2")
    sys.exit()

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

# --- PFADE ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CAM_BASE_DIR = os.path.join(SCRIPT_DIR, "Wolkenbilder")
DARK_BASE_DIR = os.path.join(SCRIPT_DIR, "Dunkelmessungen")

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
    def __init__(self, root):
        self.root = root
        self.root.title("PJG Spectrometer & Cloud-Cam v8.7 Expert")
        self.root.geometry("1250x850")
        
        # Status & Hardware
        self.ser = None
        self.picam = None
        self.connected = False
        self.is_measuring = False
        self.last_trigger_minute = -1
        
        # Spektrometer-Daten
        self.start_wl, self.num_points = 380, 401 
        self.dark_reference = []

        self.create_widgets()
        self.init_hardware()

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

    def create_widgets(self):
        # Top Control Panel
        ctrl_frame = tk.Frame(self.root, pady=10)
        ctrl_frame.pack(side=tk.TOP, fill=tk.X, padx=15)
        
        # Bereich: Belichtung
        exp_f = tk.LabelFrame(ctrl_frame, text=" Belichtung & Sync-Steuerung ", padx=15, pady=10)
        exp_f.pack(side=tk.LEFT, padx=5)
        self.mode_var = tk.StringVar(value="auto")
        tk.Radiobutton(exp_f, text="Auto-Belichtung", variable=self.mode_var, value="auto").grid(row=0, column=0, sticky="w")
        tk.Radiobutton(exp_f, text="Manuell (ms):", variable=self.mode_var, value="manual").grid(row=1, column=0, sticky="w")
        self.entry_ms = tk.Entry(exp_f, width=8, justify="center"); self.entry_ms.insert(0, "500")
        self.entry_ms.grid(row=1, column=1, padx=5)

        # Bereich: CIE
        opt_f = tk.LabelFrame(ctrl_frame, text=" CIE Standard ", padx=15, pady=10)
        opt_f.pack(side=tk.LEFT, padx=5)
        self.cie_var = tk.StringVar(value="CIE1931-2° (0x00)")
        self.cie_combo = ttk.Combobox(opt_f, textvariable=self.cie_var, state="readonly", width=18, 
                                      values=["CIE1931-2° (0x00)", "CIE2015-2° (0x02)", "CIE-10° (0x03)"])
        self.cie_combo.pack(pady=2)
        tk.Button(opt_f, text="Sync Hardware", command=self.apply_hardware).pack(fill=tk.X)

        # Bereich: Aktionen
        act_f = tk.Frame(ctrl_frame, padx=10)
        act_f.pack(side=tk.LEFT, fill=tk.Y)
        self.btn_start = tk.Button(act_f, text="START LOGGING", command=self.toggle_measurement, 
                                   bg="#28a745", fg="white", font=("Arial", 10, "bold"), width=20, height=2)
        self.btn_start.pack(pady=2)
        tk.Button(act_f, text="Dunkel-Abgleich", command=self.start_dark_thread, width=20).pack(pady=2)

        # Hauptbereich: Grafik & Log
        main_container = tk.Frame(self.root)
        main_container.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)
        
        # Matplotlib Figure
        self.fig = Figure(figsize=(7, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_xlabel("Wellenlänge [nm]")
        self.ax.set_ylabel("Intensität [W/m²]")
        self.ax.set_title("Spektrale Strahlungsverteilung (Echtzeit)")
        self.ax.grid(True, linestyle='--', alpha=0.6)
        
        self.line, = self.ax.plot([], [], color='#d9534f', lw=1.5)
        self.canvas = FigureCanvasTkAgg(self.fig, master=main_container)
        self.canvas.get_tk_widget().pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Log Bereich
        self.log_area = scrolledtext.ScrolledText(main_container, width=50, state='disabled', 
                                                  bg="#1e1e1e", fg="#e0e0e0", font=("Consolas", 9))
        self.log_area.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))

    def init_hardware(self):
        # 1. Spektrometer
        try:
            self.ser = serial.Serial('/dev/ttyACM0', 115200, timeout=1.5)
            time.sleep(2)
            self.ser.write(self.build_packet(0x0F))
            res = self.ser.read(100)
            if res and len(res) >= 13:
                s, e = struct.unpack('<HH', res[6:10])
                self.start_wl, self.num_points = s, e - s + 1
                self.connected = True
                self.log(f"Spektrometer erkannt: {s}nm - {e}nm", "green")
        except: self.log("Hardware: Spektrometer nicht gefunden.", "red")

        # 2. Kamera
        try:
            self.picam = Picamera2()
            self.picam.configure(self.picam.create_still_configuration())
            self.picam.start()
            self.log("Hardware: Kamera bereit.", "green")
        except: self.log("Hardware: Kamera-Initialisierung fehlgeschlagen.", "red")

    def build_packet(self, cmd, data=None):
        if data is None: data = []
        l = 9 + len(data)
        p = bytes([0xCC, 0x01, l&0xFF, (l>>8)&0xFF, (l>>16)&0xFF, cmd] + data)
        return p + bytes([sum(p)&0xFF, 0x0D, 0x0A])

    def apply_hardware(self):
        if not self.connected: return
        try:
            cie_map = {"CIE1931-2° (0x00)": 0x00, "CIE2015-2° (0x02)": 0x02, "CIE-10° (0x03)": 0x03}
            self.ser.write(self.build_packet(0x36, [cie_map.get(self.cie_var.get(), 0x00)]))
            is_auto = (self.mode_var.get() == "auto")
            self.ser.write(self.build_packet(0x0A, [0x01 if is_auto else 0x00]))
            if not is_auto:
                t_us = int(float(self.entry_ms.get()) * 1000)
                self.ser.write(self.build_packet(0x0C, list(struct.pack('<I', t_us))))
            self.log("Hardware-Synchronisation erfolgreich.", "yellow")
        except: pass

    def toggle_measurement(self):
        if not self.is_measuring:
            self.apply_hardware()
            self.is_measuring = True
            self.btn_start.config(text="STOPP LOGGING", bg="#dc3545")
            self.log(">>> SYNC-MODUS AKTIVIERT (Jede volle Minute)", "cyan")
            threading.Thread(target=self.main_sync_loop, daemon=True).start()
        else:
            self.is_measuring = False
            self.btn_start.config(text="START LOGGING", bg="#28a745")

    def main_sync_loop(self):
        """Präzises Warten auf Sekunde 00 und paralleler Start der Hardware."""
        while self.is_measuring:
            now = datetime.now()
            if now.second == 0 and now.minute != self.last_trigger_minute:
                self.last_trigger_minute = now.minute
                
                ts_folder = now.strftime("%Y-%m-%d")
                ts_file = now.strftime("%H%M%S")
                
                self.log(f"--- Trigger: {now.strftime('%H:%M')}:00 ---", "yellow")
                
                # Threads für simultanen Start
                t1 = threading.Thread(target=self.capture_photo, args=(ts_folder, ts_file))
                t2 = threading.Thread(target=self.capture_spectrum, args=(ts_folder, ts_file))
                
                t1.start()
                t2.start()
                
                time.sleep(50) # Schonen der CPU bis zur nächsten Minute
            time.sleep(0.05)

    def capture_photo(self, folder, filename):
        if not self.picam: return
        try:
            path = os.path.join(CAM_BASE_DIR, folder)
            os.makedirs(path, exist_ok=True)
            full_fn = f"Wolkenbild_{filename}.png"
            self.picam.capture_file(os.path.join(path, full_fn))
            self.log(f"Kamera: {full_fn} gesichert.", "cyan")
        except Exception as e: self.log(f"Fehler Kamera: {e}", "red")

    def capture_spectrum(self, folder, filename):
        if not self.connected: return
        try:
            self.ser.reset_input_buffer()
            self.ser.write(self.build_packet(0x32))
            data, start_t = b"", time.time()
            while (time.time() - start_t) < 12:
                if self.ser.in_waiting:
                    data += self.ser.read(self.ser.in_waiting)
                    if len(data) >= (213 + self.num_points*2 + 3): break
                time.sleep(0.01)
            
            if len(data) >= 213:
                n = struct.unpack('<h', data[211:213])[0]
                raw = struct.unpack('<' + 'H'*self.num_points, data[213:213+self.num_points*2])
                scan = [v / (10**n) for v in raw]
                final = [max(0, v - self.dark_reference[i]) if self.dark_reference else v for i, v in enumerate(scan)]
                
                self.root.after(0, lambda: self.update_plot(final))
                
                path = os.path.join(SCRIPT_DIR, f"Messungen_{folder}")
                os.makedirs(path, exist_ok=True)
                full_fn = f"m_{filename}.csv"
                
                floats = struct.unpack('<' + 'f'*50, data[11:211])
                with open(os.path.join(path, full_fn), 'w', newline='') as f:
                    w = csv.writer(f, delimiter=';')
                    for name, val in zip(PARAM_NAMES, floats): w.writerow([name, f"{val:.4f}"])
                    w.writerow(["--- SPEKTRUM (nm; W/m2) ---"])
                    for i, v in enumerate(final): w.writerow([self.start_wl+i, f"{v:.6f}"])
                self.log(f"Spektrum: {full_fn} gesichert.", "green")
        except Exception as e: self.log(f"Fehler Spektrometer: {e}", "red")

    def update_plot(self, data):
        x_vals = [self.start_wl + i for i in range(len(data))]
        self.line.set_data(x_vals, data)
        self.ax.relim(); self.ax.autoscale_view(); self.canvas.draw()

    def start_dark_thread(self):
        threading.Thread(target=self.perform_dark_calib, daemon=True).start()

    def perform_dark_calib(self):
        messagebox.showinfo("Dunkelabgleich", "Sensor bitte lichtdicht abdecken!")
        self.log("Dunkelabgleich: Referenzmessung läuft...", "cyan")
        all_s = []
        now = datetime.now()
        ts_f = now.strftime("%Y-%m-%d"); ts_t = now.strftime("%H%M%S")

        try:
            for i in range(3):
                self.ser.reset_input_buffer()
                self.ser.write(self.build_packet(0x32))
                time.sleep(2.5)
                res = self.ser.read(2500)
                if len(res) > 213:
                    n = struct.unpack('<h', res[211:213])[0]
                    raw = struct.unpack('<' + 'H'*self.num_points, res[213:213+self.num_points*2])
                    all_s.append([v / (10**n) for v in raw])
                    self.log(f" Dunkel-Probe {i+1}/3 aufgenommen.")

            if len(all_s) == 3:
                self.dark_reference = [sum(col)/3 for col in zip(*all_s)]
                
                # Speichern des Dunkelspektrums
                os.makedirs(DARK_BASE_DIR, exist_ok=True)
                dark_fn = f"dark_{ts_f}_{ts_t}.csv"
                with open(os.path.join(DARK_BASE_DIR, dark_fn), 'w', newline='') as f:
                    w = csv.writer(f, delimiter=';')
                    w.writerow(["Zeitpunkt", now.strftime("%Y-%m-%d %H:%M:%S")])
                    w.writerow(["Wellenlaenge", "Dunkelwert (W/m2)"])
                    for i, v in enumerate(self.dark_reference):
                        w.writerow([self.start_wl + i, f"{v:.6f}"])
                
                self.log(f"Dunkel-CSV archiviert: {dark_fn}", "green")
                messagebox.showinfo("Erfolg", "Dunkelabgleich abgeschlossen.")
        except Exception as e: self.log(f"Dunkel-Fehler: {e}", "red")

if __name__ == "__main__":
    root = tk.Tk(); app = SpectrometerApp(root); root.mainloop()