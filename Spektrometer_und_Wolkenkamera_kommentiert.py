# Standard-Bibliotheken
import sys  # Ermöglicht Zugriff auf Systemfunktionen
import os   # Funktionen für Datei- und Verzeichnisoperationen
import struct, time, csv, threading # Binärdaten, Zeitfunktionen, CSV-Verarbeitung, Threading
from datetime import datetime   # Datum und Zeit

# HARDWARE-IMPORTE & PRÜFUNG
try:
    import serial   # Serielle Kommunikation
    import matplotlib.pyplot as plt # Plotten von Daten
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg # Bettet Diagramme in die GUI ein
    from matplotlib.figure import Figure    # Matplotlib Figure-Objekt
    import numpy as np  # Numerische Operationen
    from picamera2 import Picamera2 # Kamera-Steuerung
except ImportError as e:    # Fehlermeldung bei fehlenden Bibliotheken
    print(f"\n! FEHLER: Modul fehlt: {e}")
    print("Bitte installieren mit: pip install pyserial matplotlib numpy picamera2")
    sys.exit()  # Beendet das Programm

import tkinter as tk    # Standard GUI-Bibliothek
from tkinter import ttk, messagebox, scrolledtext   # Erweiterte GUI-Komponenten (Menüs, Dialoge, Scrolltext)

# Erstellung der Ordner für Speicherung
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__)) # Verzeichnis des Skripts
CAM_BASE_DIR = os.path.join(SCRIPT_DIR, "Wolkenbilder") # Basisverzeichnis für Wolkenbilder
DARK_BASE_DIR = os.path.join(SCRIPT_DIR, "Dunkelmessungen") # Basisverzeichnis für Dunkelmessungen

# Liste der Parameter, die das Spektrometer zurückgibt
PARAM_NAMES = [
    "X", "Y", "Z", "x", "y", "u", "v", "u_prime", "v_prime",
    "Tc_CCT", "Nit", "r_ratio", "g_ratio", "b_ratio", "DUV", "Ra",
    "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10",
    "R11", "R12", "R13", "R14", "R15",
    "Lp", "HW", "Ld", "purity", "SP", "SDCM", "k", "lux", "Ee", "fc",
    "CQS", "GAI_EES", "GAI_BB_8", "GAI_BB_15", "EML", "M_EDI",
    "Red_Ee", "Nir_EeA", "Nir_EeB"
]

class SpectrometerApp:  # Hauptanwendungsklasse für das Spektrometer und die Wolkenkamera
    def __init__(self, root):   # Initialisierung der GUI und Hardware
        self.root = root    # Hauptfenster
        self.root.title("PJG Spectrometer & Cloud-Cam v8.7 Expert") # Fenstertitel
        self.root.geometry("1250x850")  # Fenstergröße
        
        # Status & Hardware
        self.ser = None # Serielle Schnittstelle
        self.picam = None   # Kamera Schnittstelle
        self.connected = False  # Verbindungsstatus Spektrometer
        self.is_measuring = False   # Messmodus-Status (läuft die Messung?)
        self.last_trigger_minute = -1   # Merkt sich die letzte Trigger-Minute
        
        # Spektrometer-Daten
        self.start_wl, self.num_points = 380, 401   # Start-Wellenlänge und Anzahl der Messpunkte
        self.dark_reference = []    # Liste für Dunkelreferenzwerte

        self.create_widgets()   # GUI-Elemente erstellen
        self.init_hardware()    # Hardware initialisieren

    def log(self, msg, color="white"):  # Protokollierungsfunktion für die GUI
        ts = datetime.now().strftime("%H:%M:%S")    # Zeitstempel
        self.log_area.config(state='normal')    # Log-Bereich aktivieren
        self.log_area.insert(tk.END, f"[{ts}] {msg}\n", color)  # Nachricht einfügen
        # Farbkonfiguration
        self.log_area.tag_config("green", foreground="#44ff44")
        self.log_area.tag_config("yellow", foreground="#ffff44")
        self.log_area.tag_config("red", foreground="#ff4444")
        self.log_area.tag_config("cyan", foreground="#44ffff")
        self.log_area.config(state='disabled')  # Log-Bereich deaktivieren
        self.log_area.see(tk.END)   # Zum Ende scrollen

    def create_widgets(self):   # GUI-Elemente erstellen
        # oberer Steuerbereich
        ctrl_frame = tk.Frame(self.root, pady=10)   # Rahmen für Steuerungselemente
        ctrl_frame.pack(side=tk.TOP, fill=tk.X, padx=15)    # Packen des Rahmens in das Hauptfenster
        
        # Bereich: Belichtung
        exp_f = tk.LabelFrame(ctrl_frame, text=" Belichtung & Sync-Steuerung ", padx=15, pady=10)   # Rahmen für Belichtungseinstellungen
        exp_f.pack(side=tk.LEFT, padx=5)    # Positionierung des Rahmens
        self.mode_var = tk.StringVar(value="auto")  # Variable für Belichtungsmodus (standardmäßig "auto")
        tk.Radiobutton(exp_f, text="Auto-Belichtung", variable=self.mode_var, value="auto").grid(row=0, column=0, sticky="w")   # Radiobutton für Auto-Belichtung
        tk.Radiobutton(exp_f, text="Manuell (ms):", variable=self.mode_var, value="manual").grid(row=1, column=0, sticky="w")   # Radiobutton für manuelle Belichtung
        self.entry_ms = tk.Entry(exp_f, width=8, justify="center"); self.entry_ms.insert(0, "500")  # Eingabefeld für manuelle Belichtungszeit (Standard 500ms)
        self.entry_ms.grid(row=1, column=1, padx=5) # Positionierung des Eingabefelds

        # Bereich: CIE (Farbstandard)
        opt_f = tk.LabelFrame(ctrl_frame, text=" CIE Standard ", padx=15, pady=10)  # Rahmen für CIE-Auswahl
        opt_f.pack(side=tk.LEFT, padx=5)    # Positionierung des Rahmens
        self.cie_var = tk.StringVar(value="CIE1931-2° (0x00)")  # Variable für CIE-Auswahl
        self.cie_combo = ttk.Combobox(opt_f, textvariable=self.cie_var, state="readonly", width=18, 
                                      values=["CIE1931-2° (0x00)", "CIE2015-2° (0x02)", "CIE-10° (0x03)"])  # Dropdown-Menü für CIE-Auswahl
        self.cie_combo.pack(pady=2) # Positionierung des Dropdown-Menüs
        tk.Button(opt_f, text="Sync Hardware", command=self.apply_hardware).pack(fill=tk.X) # Button zum Anwenden der Hardware-Einstellungen

        # Bereich: Aktionen (Start/Stop, Dunkelabgleich)
        act_f = tk.Frame(ctrl_frame, padx=10)   # Rahmen für Aktions-Buttons
        act_f.pack(side=tk.LEFT, fill=tk.Y) # Positionierung des Rahmens
        self.btn_start = tk.Button(act_f, text="START LOGGING", command=self.toggle_measurement, 
                                   bg="#28a745", fg="white", font=("Arial", 10, "bold"), width=20, height=2)    # Start/Stop Button
        self.btn_start.pack(pady=2) # Positionierung des Start/Stop Buttons
        tk.Button(act_f, text="Dunkel-Abgleich", command=self.start_dark_thread, width=20).pack(pady=2) # Button für Dunkelabgleich

        # Hauptbereich: Grafik & Log
        main_container = tk.Frame(self.root)    # Hauptcontainer für Grafik und Log
        main_container.pack(fill=tk.BOTH, expand=True, padx=15, pady=5) # Positionierung des Hauptcontainers
        
        # Matplotlib Figure
        self.fig = Figure(figsize=(7, 4), dpi=100)  # Grafik-Objekt erstellen
        self.ax = self.fig.add_subplot(111) # Subplot hinzufügen
        self.ax.set_xlabel("Wellenlänge [nm]")  # x-Achsenbeschriftung
        self.ax.set_ylabel("Intensität [W/m²]") # y-Achsenbeschriftung
        self.ax.set_title("Spektrale Strahlungsverteilung (Echtzeit)")  # Diagrammtitel
        self.ax.grid(True, linestyle='--', alpha=0.6)   # Gitternetz im Diagramm
        
        self.line, = self.ax.plot([], [], color='#d9534f', lw=1.5)  # Initiale leere Linie im Diagramm
        self.canvas = FigureCanvasTkAgg(self.fig, master=main_container)    # Verbindet die Grafik mit dem Frame
        self.canvas.get_tk_widget().pack(side=tk.LEFT, fill=tk.BOTH, expand=True)  # Positionierung der Grafik

        # Log Bereich auf der rechten Seite
        self.log_area = scrolledtext.ScrolledText(main_container, width=50, state='disabled', 
                                                  bg="#1e1e1e", fg="#e0e0e0", font=("Consolas", 9)) # Erstellt das Log-Textfeld mit Scrollfunktion
        self.log_area.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))  # Positionierung des Log-Textfelds

    def init_hardware(self):    # Initialisierung der Hardware-Komponenten
        # 1. USB-Spektrometer initialisieren
        try:
            self.ser = serial.Serial('/dev/ttyACM0', 115200, timeout=1.5)   # Serielle Verbindung zum Spektrometer herstellen (115200 Baudrate)
            time.sleep(2)   # Wartezeit für die Initialisierung
            self.ser.write(self.build_packet(0x0F)) # Abfrage der Geräteinformationen (Wellenlängenbereich)
            res = self.ser.read(100)    # Liest bis zu 100 Bytes als Antwort
            if res and len(res) >= 13:  # Prüft, ob eine Antwort empfangen wurde und ob sie lang genug ist (mindestens 13 Bytes)
                s, e = struct.unpack('<HH', res[6:10])  # Start- und Endwellenlänge aus den Binärdaten extrahieren (Position 6-9)
                self.start_wl, self.num_points = s, e - s + 1   # Speichert die Startwellenlänge und berechnet die Anzahl der Messpunkte
                self.connected = True   # Verbindungsstatus als erfolgreich setzen
                self.log(f"Spektrometer erkannt: {s}nm - {e}nm", "green")   # Erfolgsmeldung
        except: self.log("Hardware: Spektrometer nicht gefunden.", "red")   # Fehlermeldung

        # 2. Raspberry Pi Kamera initialisieren
        try:
            self.picam = Picamera2()    # Picamera2-Objekt erstellen
            self.picam.configure(self.picam.create_still_configuration())   # Kamera konfigurieren (Foto-Modus)
            self.picam.start()  # Startet den Kamerastream
            self.log("Hardware: Kamera bereit.", "green")   # Erfolgsmeldung
        except: self.log("Hardware: Kamera-Initialisierung fehlgeschlagen.", "red") # Fehlermeldung

    def build_packet(self, cmd, data=None): # Baut ein Steuerpaket für das Spektrometer
        if data is None: data = []  # Falls keine Daten übergeben wurden, leere Liste verwenden
        l = 9 + len(data)   # Paketlänge berechnen (9 Byte Header + Datenlänge)
        p = bytes([0xCC, 0x01, l&0xFF, (l>>8)&0xFF, (l>>16)&0xFF, cmd] + data)  # Erstellt die ersten Bytes des Pakets
        return p + bytes([sum(p)&0xFF, 0x0D, 0x0A]) # Prüfsumme und End-Zeichen hinzufügen

    def apply_hardware(self):   # Sendet die aktuellen Einstellungen an das Spektrometer
        if not self.connected: return   # Falls das Spektrometer nicht verbunden ist, abbrechen
        try:
            cie_map = {"CIE1931-2° (0x00)": 0x00, "CIE2015-2° (0x02)": 0x02, "CIE-10° (0x03)": 0x03}    # Wörterbuch für CIE-Auswahl
            self.ser.write(self.build_packet(0x36, [cie_map.get(self.cie_var.get(), 0x00)]))    # Sendet die CIE-Einstellung
            is_auto = (self.mode_var.get() == "auto")   # Belichtungsmodus prüfen
            self.ser.write(self.build_packet(0x0A, [0x01 if is_auto else 0x00]))    # Belichtungsmodus senden (0x01=Auto, 0x00=Manuell)
            if not is_auto: # wenn manuelle Belichtung ausgewählt
                t_us = int(float(self.entry_ms.get()) * 1000)   # ms in µs umrechnen
                self.ser.write(self.build_packet(0x0C, list(struct.pack('<I', t_us))))  # Belichtungszeit senden (4 Byte Binärformat)
            self.log("Hardware-Synchronisation erfolgreich.", "yellow")  # Bestätigung der Synchronisation
        except: pass    # Falls ein Fehler auftritt, ignorieren

    def toggle_measurement(self):   # Startet oder stoppt den Messvorgang
        if not self.is_measuring:   # Wenn die Messung nicht läuft
            self.apply_hardware()   # Synchronisiere die Hardware-Einstellungen
            self.is_measuring = True    # Messmodus aktivieren
            self.btn_start.config(text="STOPP LOGGING", bg="#dc3545")   # Button-Text und Farbe ändern
            self.log(">>> SYNC-MODUS AKTIVIERT (Jede volle Minute)", "cyan")    # Info-Log-Eintrag
            threading.Thread(target=self.main_sync_loop, daemon=True).start()   # Starte den Synchronisations-Thread zur Überwachung der Zeit
        else:   # Wenn die Messung läuft
            self.is_measuring = False   # Messmodus deaktivieren
            self.btn_start.config(text="START LOGGING", bg="#28a745")   # Button-Text und Farbe zurücksetzen

    def main_sync_loop(self):   # Hauptschleife für die zeitgesteuerte Messung
        """Präzises Warten auf Sekunde 00 und paralleler Start der Hardware."""
        while self.is_measuring:    # Solange der Messmodus aktiv ist
            now = datetime.now()    # Aktuelle Zeit abrufen
            if now.second == 0 and now.minute != self.last_trigger_minute:  # Prüft, ob es Sekunde 00 ist und ob diese noch nicht gemessen wurde
                self.last_trigger_minute = now.minute   # Merke die aktuelle gemessene Minute
                
                ts_folder = now.strftime("%Y-%m-%d")    # Ordnername basierend auf dem Datum
                ts_file = now.strftime("%H%M%S")    # Dateiname basierend auf der Uhrzeit
                
                self.log(f"--- Trigger: {now.strftime('%H:%M')}:00 ---", "yellow")  # Log-Eintrag für Trigger
                
                # Threads für simultanen Start (Kamera & Spektrometer)
                t1 = threading.Thread(target=self.capture_photo, args=(ts_folder, ts_file)) # Kamera-Thread
                t2 = threading.Thread(target=self.capture_spectrum, args=(ts_folder, ts_file))  # Spektrometer-Thread
                
                t1.start()  # Startet die Kamera-Aufnahme im Hintergrund
                t2.start()  # Startet die Spektrummessung im Hintergrund
                
                time.sleep(50) # Schonen der CPU bis zur nächsten Minute
            time.sleep(0.05)    # Falls nicht Sekunde 00 ist, warte kurz bis zum nächsten Check

    def capture_photo(self, folder, filename):  # Nimmt ein Foto mit der Kamera auf und speichert es
        if not self.picam: return   # Nur wenn die Kamera verbunden undinitialisiert ist
        try:
            path = os.path.join(CAM_BASE_DIR, folder)   # Verzeichnispfad erstellen
            os.makedirs(path, exist_ok=True)    # Verzeichnis erstellen, falls es nicht existiert
            full_fn = f"Wolkenbild_{filename}.png"  # Dateiname erstellen
            self.picam.capture_file(os.path.join(path, full_fn))    # Foto aufnehmen und unter dem Pfad speichern
            self.log(f"Kamera: {full_fn} gesichert.", "cyan")   # Erfolgsmeldung, wenn das Foto gespeichert wurde
        except Exception as e: self.log(f"Fehler Kamera: {e}", "red")   # Fehlermeldung, wenn etwas schiefgeht

    def capture_spectrum(self, folder, filename):   # Nimmt ein Spektrum mit dem Spektrometer auf und speichert es
        if not self.connected: return   # Abbruchen, wenn das Spektrometer nicht verbunden ist
        try:
            self.ser.reset_input_buffer()   # Löscht alle vorherigen Daten im Eingabepuffer
            self.ser.write(self.build_packet(0x32)) # Befehl zur Spektrummessung senden
            data, start_t = b"", time.time()    # Initialisiert leere Daten und merkt sich die Startzeit
            while (time.time() - start_t) < 12: # Warteschleife für die Antwort (max. 12 Sekunden)
                if self.ser.in_waiting: # Wenn Daten verfügbar sind
                    data += self.ser.read(self.ser.in_waiting)  # Alle verfügbaren Daten einlesen und an data anhängen
                    if len(data) >= (213 + self.num_points*2 + 3): break  # Prüfen, ob das gesamte Paket empfangen wurde
                time.sleep(0.01)    # Kurze Pause
            
            if len(data) >= 213:    # Wenn mindestens der Header empfangen wurde
                n = struct.unpack('<h', data[211:213])[0]   # Exponent (Skalierungsfaktor) extrahieren
                raw = struct.unpack('<' + 'H'*self.num_points, data[213:213+self.num_points*2]) # Extrahiert die Roh-Spektralwerte als 2-Byte-Integer
                scan = [v / (10**n) for v in raw]   # Skaliert die Rohwerte basierend auf dem Exponenten
                final = [max(0, v - self.dark_reference[i]) if self.dark_reference else v for i, v in enumerate(scan)]  # Zieht Dunkelwerte ab (falls vorhanden) und stellt sicher, dass keine negativen Werte entstehen
                
                self.root.after(0, lambda: self.update_plot(final)) # Aktualisiert die Grafik im Hauptthread
                
                path = os.path.join(SCRIPT_DIR, f"Messungen_{folder}")  # Verzeichnispfad für den Messordner erstellen
                os.makedirs(path, exist_ok=True)    # Ordner erzeugen, falls er nicht existiert
                full_fn = f"m_{filename}.csv"   # CSV-Dateiname für die Messung erstellen
                
                floats = struct.unpack('<' + 'f'*50, data[11:211])  # Extrahiert die 50 Parameterwerte als 4-Byte-Floats
                with open(os.path.join(path, full_fn), 'w', newline='') as f:   # Öffnet die CSV-Datei zum Schreiben
                    w = csv.writer(f, delimiter=';')    # CSV-Schreiber mit Semikolon als Trennzeichen
                    for name, val in zip(PARAM_NAMES, floats): w.writerow([name, f"{val:.4f}"]) # Schreibt jeden Parameternamen und -wert in eine Zeile der CSV
                    w.writerow(["--- SPEKTRUM (nm; W/m2) ---"]) # Trennzeile für die Spektraldaten
                    for i, v in enumerate(final): w.writerow([self.start_wl+i, f"{v:.6f}"]) # Schreibt die Wellenlänge und den entsprechenden Messwert in die CSV
                self.log(f"Spektrum: {full_fn} gesichert.", "green")    # Erfolgsmeldung, wenn das Spektrum gespeichert wurde
        except Exception as e: self.log(f"Fehler Spektrometer: {e}", "red") # Fehlermeldung, wenn etwas schiefgeht

    def update_plot(self, data):    # Aktualisiert die Grafik mit neuen Spektraldaten
        x_vals = [self.start_wl + i for i in range(len(data))]  # Liste der X-Achsenwerte (Wellenlängen)
        self.line.set_data(x_vals, data) # Aktualisiert die Daten der bereits existierenden Linie im Diagramm
        self.ax.relim(); self.ax.autoscale_view(); self.canvas.draw()   # Achsen neu skalieren und Grafik neu zeichnen

    def start_dark_thread(self):    # Startet den Dunkelabgleich in einem separaten Thread
        threading.Thread(target=self.perform_dark_calib, daemon=True).start()   # Startet den Dunkelabgleich in einem Hintergrund-Thread

    def perform_dark_calib(self):   # Führt den Dunkelabgleich durch und speichert die Referenzwerte
        messagebox.showinfo("Dunkelabgleich", "Sensor bitte lichtdicht abdecken!")  # Hinweis-Dialog
        self.log("Dunkelabgleich: Referenzmessung läuft...", "cyan")    # Log-Eintrag für Dunkelabgleich
        all_s = []  # Initialisiert eine leere Liste um alle Dunkelspektren zu speichern
        now = datetime.now()    # Aktuelle Zeit für Dateinamen
        ts_f = now.strftime("%Y-%m-%d"); ts_t = now.strftime("%H%M%S")  # Formatiert Datum und Zeit für Dateinamen

        try:
            for i in range(3):  # Nimmt 3 Dunkelspektren auf
                self.ser.reset_input_buffer()   # Leert den seriellen Eingabepuffer, um keine alten Daten zu haben
                self.ser.write(self.build_packet(0x32)) # Befehl zur Messung an das Spektrometer senden
                time.sleep(2.5) # Wartezeit für die Messung
                res = self.ser.read(2500)   # Liest die Antwortdaten (bis zu 2500 Bytes)
                if len(res) > 213:  # Prüft, ob gültige Daten empfangen wurden (mindestens 213 Bytes)
                    n = struct.unpack('<h', res[211:213])[0]    # Exponent (Skalierungsfaktor) extrahieren
                    raw = struct.unpack('<' + 'H'*self.num_points, res[213:213+self.num_points*2])  # Extrahiert die Roh-Spektralwerte als 2-Byte-Integer
                    all_s.append([v / (10**n) for v in raw])    # Skaliert die Rohwerte basierend auf dem Exponenten und speichert sie 
                    self.log(f" Dunkel-Probe {i+1}/3 aufgenommen.") # Log-Eintrag für jedes abgespeicherte Dunkelspektrum

            if len(all_s) == 3: # Wenn alle 3 Dunkelspektren aufgenommen wurden
                self.dark_reference = [sum(col)/3 for col in zip(*all_s)]   # Berechnet für jede Wellenlänge den Durchschnittswert der 3 Messungen
                
                # Speichern des Dunkelspektrums
                os.makedirs(DARK_BASE_DIR, exist_ok=True)   # Erstellt das Verzeichnis für Dunkelmessungen, falls es noch nicht existiert
                dark_fn = f"dark_{ts_f}_{ts_t}.csv" # Dateiname für die Dunkel-CSV-Datei
                with open(os.path.join(DARK_BASE_DIR, dark_fn), 'w', newline='') as f:  # Öffnet die CSV-Datei zum Schreiben
                    w = csv.writer(f, delimiter=';')    # CSV-Schreiber mit Semikolon als Trennzeichen
                    w.writerow(["Zeitpunkt", now.strftime("%Y-%m-%d %H:%M:%S")])    # Schreibt den Zeitpunkt der Dunkelmessung in die Kopfzeile der CSV
                    w.writerow(["Wellenlaenge", "Dunkelwert (W/m2)"])   # Schreibt die Spaltenüberschriften
                    for i, v in enumerate(self.dark_reference): # Für jede Wellenlänge
                        w.writerow([self.start_wl + i, f"{v:.6f}"]) # Schreibt die Wellenlänge und den entsprechenden Dunkelwert in die CSV
                
                self.log(f"Dunkel-CSV archiviert: {dark_fn}", "green")  # Erfolgsmeldung für das Speichern der Dunkel-CSV
                messagebox.showinfo("Erfolg", "Dunkelabgleich abgeschlossen.")  # Erfolgsmeldung in einem Popup-Fenster
        except Exception as e: self.log(f"Dunkel-Fehler: {e}", "red")   # Fehlermeldung bei Problemen während des Dunkelabgleichs

if __name__ == "__main__":  # Prüft, ob das Skript direkt ausgeführt wird (und nicht nur als Modul importiert wird)
    root = tk.Tk(); app = SpectrometerApp(root); root.mainloop()    # Erstellt das Hauptfenster und startet die GUI-Schleife