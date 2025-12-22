import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import serial, struct, time, csv, os, threading
from datetime import datetime
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import numpy as np

# --- KONFIGURATION ---
PORT = '/dev/ttyACM0' 
BAUD = 115200

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
        self.root.title("PJG Spectrometer - Expert Lab v7.9")
        self.root.geometry("1150x800")
        self.ser = None
        self.start_wl, self.num_points = 0, 0
        self.dark_reference = []
        self.is_measuring = False
        self.connected = False
        self.hw_id_string = "N/A"

        self.create_widgets()
        self.init_serial()

    def build_packet(self, cmd, data=None):
        if data is None: data = []
        l = 9 + len(data)
        p = bytes([0xCC, 0x01, l&0xFF, (l>>8)&0xFF, (l>>16)&0xFF, cmd] + data)
        return p + bytes([sum(p)&0xFF, 0x0D, 0x0A])

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
        # Steuerungsleiste
        ctrl_frame = tk.Frame(self.root, pady=10)
        ctrl_frame.pack(side=tk.TOP, fill=tk.X, padx=15)
        
        # Zusammengefasster Bereich 1 & 2: Belichtung
        exp_f = tk.LabelFrame(ctrl_frame, text=" Belichtung (Modus & Zeit) ", padx=15, pady=10)
        exp_f.pack(side=tk.LEFT, padx=5)
        
        self.mode_var = tk.StringVar(value="auto")
        tk.Radiobutton(exp_f, text="Auto (0x01)", variable=self.mode_var, value="auto").grid(row=0, column=0, sticky="w")
        tk.Radiobutton(exp_f, text="Manuell (ms):", variable=self.mode_var, value="manual").grid(row=1, column=0, sticky="w")
        
        self.entry_ms = tk.Entry(exp_f, width=8, font=("Arial", 10), justify="center")
        self.entry_ms.insert(0, "500")
        self.entry_ms.grid(row=1, column=1, padx=5)

        # Bereich 3: System / CIE
        opt_f = tk.LabelFrame(ctrl_frame, text=" CIE & Sync ", padx=15, pady=10)
        opt_f.pack(side=tk.LEFT, padx=5)
        self.cie_var = tk.StringVar(value="CIE1931-2° (0x00)")
        self.cie_combo = ttk.Combobox(opt_f, textvariable=self.cie_var, state="readonly", width=20,
                                      values=["CIE1931-2° (0x00)", "CIE2015-2° (0x02)", "CIE-10° (0x03)"])
        self.cie_combo.pack(pady=2)
        tk.Button(opt_f, text="Sync Hardware", command=self.apply_hardware, bg="#f8f9fa").pack(fill=tk.X)

        # Bereich 4: Aktionen
        act_f = tk.Frame(ctrl_frame, padx=10)
        act_f.pack(side=tk.LEFT, fill=tk.Y)
        self.btn_start = tk.Button(act_f, text="START LOGGING", command=self.toggle_measurement, bg="#28a745", fg="white", font=("Arial", 10, "bold"), width=16, height=2)
        self.btn_start.pack(pady=2)
        tk.Button(act_f, text="Dunkel-Abgleich", command=self.start_dark_thread, width=16).pack(pady=2)

        # Hauptcontainer (Graph & Log)
        main_container = tk.Frame(self.root)
        main_container.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)

        self.fig = Figure(figsize=(6, 4), dpi=100); self.ax = self.fig.add_subplot(111)
        self.ax.set_title("Spektralanalyse (Relativ)"); self.ax.grid(True, alpha=0.3)
        self.line, = self.ax.plot([], [], color='#d9534f', lw=1.5)
        self.canvas = FigureCanvasTkAgg(self.fig, master=main_container)
        self.canvas.get_tk_widget().pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.log_area = scrolledtext.ScrolledText(main_container, width=55, state='disabled', bg="#1e1e1e", fg="#e0e0e0", font=("Consolas", 9))
        self.log_area.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))

        # Status
        self.status_bar = tk.Frame(self.root, bd=1, relief=tk.SUNKEN)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        self.lbl_status = tk.Label(self.status_bar, text="Bereit", anchor="w")
        self.lbl_status.pack(side=tk.LEFT, padx=10)
        self.lbl_hw_info = tk.Label(self.status_bar, text="Hardware: N/A", anchor="e")
        self.lbl_hw_info.pack(side=tk.RIGHT, padx=10)

    def log_proc(self, msg, success=True):
        color = "green" if success else "red"
        self.log(msg, color)

    def init_serial(self):
        try:
            self.ser = serial.Serial(PORT, BAUD, timeout=1.5)
            time.sleep(2)
            self.log(">>> Systemstart...", "cyan")
            self.ser.write(self.build_packet(0x0F))
            res = self.ser.read(100)
            if res and len(res) >= 13:
                s, e = struct.unpack('<HH', res[6:10])
                self.start_wl, self.num_points = s, e - s + 1
                self.connected = True
                self.log(f"Hardware gefunden: {s}-{e}nm.", "green")
                self.get_device_id()
        except Exception as e: self.log(f"FEHLER: Port nicht verfügbar.", "red")

    def get_device_id(self):
        if not self.connected: return
        self.ser.write(bytes([0xCC, 0x01, 0x0A, 0x00, 0x00, 0x08, 0x18, 0xF7, 0x0D, 0x0A]))
        time.sleep(0.4)
        raw = self.ser.read(64)
        if len(raw) >= 30:
            self.hw_id_string = "".join([chr(b) for b in raw[6:30] if 32 <= b <= 126]).strip()
            self.lbl_hw_info.config(text=f"ID: {self.hw_id_string}")
            self.log(f"Geräte-ID: {self.hw_id_string}", "green")

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
                self.log(f"Sync: Manueller Modus ({t_us} µs) gesetzt.", "yellow")
            else:
                self.log("Sync: Automatischer Modus (0x01) gesetzt.", "yellow")
        except Exception as e: self.log(f"Fehler bei Sync: {e}", "red")

    def toggle_measurement(self):
        if not self.is_measuring:
            self.apply_hardware()
            self.is_measuring = True
            self.btn_start.config(text="STOPP LOGGING", bg="#dc3545")
            self.log(">>> Logging GESTARTET (Intervall: 60s)", "cyan")
            threading.Thread(target=self.measurement_loop, daemon=True).start()
        else:
            self.is_measuring = False
            self.btn_start.config(text="START LOGGING", bg="#28a745")
            self.log(">>> Logging BEENDET.", "yellow")

    def measurement_loop(self):
        while self.is_measuring:
            now = datetime.now()
            if now.second == 0:
                self.log(f"Messzyklus gestartet ({now.strftime('%H:%M')})...")
                expected = 213 + (self.num_points * 2) + 3
                self.ser.reset_input_buffer()
                self.ser.write(self.build_packet(0x32))
                
                data, start_t = b"", time.time()
                while (time.time() - start_t) < 15:
                    if self.ser.in_waiting:
                        data += self.ser.read(self.ser.in_waiting)
                        if len(data) >= expected: break
                    time.sleep(0.01)
                
                if len(data) >= expected:
                    # Skalierung aus Paket extrahieren
                    n = struct.unpack('<h', data[211:213])[0]
                    raw = struct.unpack('<' + 'H'*self.num_points, data[213:213+self.num_points*2])
                    scan = [v / (10**n) for v in raw]
                    
                    # Dunkelabgleich & Plot
                    final = [max(0, v - self.dark_reference[i]) if self.dark_reference else v for i, v in enumerate(scan)]
                    self.root.after(0, lambda: self.update_plot(final))
                    
                    # CSV Archivierung
                    folder = f"Messungen_{now.strftime('%Y-%m-%d')}"
                    os.makedirs(folder, exist_ok=True)
                    fn = f"m_{now.strftime('%H%M%S')}.csv"
                    
                    # Fotometrische Werte extrahieren (Floats)
                    floats = struct.unpack('<' + 'f'*50, data[11:211])
                    with open(os.path.join(folder, fn), 'w', newline='') as f:
                        w = csv.writer(f, delimiter=';')
                        for name, val in zip(PARAM_NAMES, floats): w.writerow([name, f"{val:.4f}"])
                        w.writerow(["--- SPEKTRUM ---"])
                        for i, v in enumerate(final): w.writerow([self.start_wl+i, f"{v:.6f}"])
                    
                    self.log(f"Speicherung abgeschlossen: {fn}", "green")
                else:
                    self.log("FEHLER: Gerät hat das Datenpaket nicht vollständig gesendet.", "red")
                time.sleep(55)
            time.sleep(0.1)

    def update_plot(self, data):
        self.line.set_data([self.start_wl + i for i in range(len(data))], data)
        self.ax.relim(); self.ax.autoscale_view(); self.canvas.draw()

    def start_dark_thread(self):
        threading.Thread(target=self.perform_dark_calib, daemon=True).start()

    def perform_dark_calib(self):
        messagebox.showinfo("Dunkelabgleich", "Sensor bitte lichtdicht abdecken!")
        self.log("Dunkelabgleich: Erfasse 3 Referenzen...", "cyan")
        all_s = []
        for i in range(3):
            self.log(f"Scan {i+1}/3 läuft...")
            self.ser.reset_input_buffer()
            self.ser.write(self.build_packet(0x32))
            time.sleep(2.5)
            res = self.ser.read(2500)
            if len(res) > 213:
                n = struct.unpack('<h', res[211:213])[0]
                raw = struct.unpack('<' + 'H'*self.num_points, res[213:213+self.num_points*2])
                all_s.append([v / (10**n) for v in raw])
        
        if len(all_s) == 3:
            self.dark_reference = [sum(col)/3 for col in zip(*all_s)]
            self.log("Dunkelabgleich erfolgreich abgeschlossen.", "green")
        else:
            self.log("Fehler: Dunkelabgleich unvollständig.", "red")

if __name__ == "__main__":
    root = tk.Tk(); app = SpectrometerApp(root); root.mainloop()