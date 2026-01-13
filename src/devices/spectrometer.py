from __future__ import annotations

import struct
import threading
import time
from typing import Dict, List, Tuple

from domain.models import SpectrumMeasurement, SpectrometerSettings

try:
    import serial  
except ImportError:
    serial = None 


CMD_GET_WL_RANGE = 0x0F
CMD_SET_CIE_MODE = 0x36
CMD_SET_EXPOSURE_MODE = 0x0A
CMD_SET_EXPOSURE_TIME = 0x0C
CMD_SINGLE_SHOT = 0x32

PKT_HDR = b"\xCC\x01"
PKT_TAIL = b"\x0D\x0A"

PARAM_START = 11
PARAM_END = 211          # 50 floats = 200 bytes
EXPONENT_START = 211     # int16
SPECTRUM_START = 213     # uint16 * num_points

PARAM_NAMES = [
    "X", "Y", "Z", "x", "y", "u", "v", "u_prime", "v_prime",
    "Tc_CCT", "Nit", "r_ratio", "g_ratio", "b_ratio", "DUV", "Ra",
    "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10",
    "R11", "R12", "R13", "R14", "R15",
    "Lp", "HW", "Ld", "purity", "SP", "SDCM", "k", "lux", "Ee", "fc",
    "CQS", "GAI_EES", "GAI_BB_8", "GAI_BB_15", "EML", "M_EDI",
    "Red_Ee", "Nir_EeA", "Nir_EeB"
]

CIE_MAP = {
    "CIE1931-2° (0x00)": 0x00,
    "CIE2015-2° (0x02)": 0x02,
    "CIE-10° (0x03)": 0x03,
}


class SpectrometerClient:
    def __init__(self, port: str = "/dev/ttyACM0", baudrate: int = 115200, timeout_s: float = 1.5):
        if serial is None:
            raise RuntimeError("pyserial not installed. Install with: pip install pyserial")

        self.port = port
        self.baudrate = baudrate
        self.timeout_s = timeout_s

        self._ser = None
        self._lock = threading.Lock()

        self.start_wl_nm: int = 380
        self.num_points: int = 401
        self.connected: bool = False

    def connect(self) -> Tuple[int, int]:
        self._ser = serial.Serial(self.port, self.baudrate, timeout=self.timeout_s)
        time.sleep(2.0)  # zeit zum booten
        s, e = self.read_wavelength_range()
        self.connected = True
        return s, e

    def close(self) -> None:
        try:
            if self._ser and self._ser.is_open:
                self._ser.close()
        finally:
            self.connected = False

    def apply_settings(self, settings: SpectrometerSettings) -> None:
        if not self.connected:
            raise RuntimeError("Spectrometer not connected")
        with self._lock:
            self._write(self._build_packet(CMD_SET_CIE_MODE, bytes([settings.cie_mode])))
            self._write(self._build_packet(CMD_SET_EXPOSURE_MODE, bytes([0x01 if settings.auto_exposure else 0x00])))
            if not settings.auto_exposure:
                if settings.exposure_us is None or settings.exposure_us <= 0:
                    raise ValueError("Manual exposure requires a positive exposure time")
                self._write(self._build_packet(CMD_SET_EXPOSURE_TIME, struct.pack("<I", settings.exposure_us)))

    def read_single_measurement(self, timeout_s: float = 12.0) -> SpectrumMeasurement:
        if not self.connected:
            raise RuntimeError("Spectrometer not connected")

        min_needed = SPECTRUM_START + self.num_points * 2 + 3

        with self._lock:
            self._ser.reset_input_buffer()  
            self._write(self._build_packet(CMD_SINGLE_SHOT))

            packet = self._read_framed_packet(timeout_s=timeout_s, min_needed=min_needed)

        return parse_measurement_packet(packet, start_wl_nm=self.start_wl_nm, num_points=self.num_points)

    def read_wavelength_range(self) -> Tuple[int, int]:
        with self._lock:
            self._ser.reset_input_buffer()  
            self._write(self._build_packet(CMD_GET_WL_RANGE))
            res = self._ser.read(100)  

        if not res or len(res) < 13:
            raise TimeoutError("No wavelength-range response")

        s, e = struct.unpack("<HH", res[6:10])
        self.start_wl_nm = int(s)
        self.num_points = int(e - s + 1)
        return int(s), int(e)


    # Herlferfunktionen
    def _write(self, data: bytes) -> None:
        self._ser.write(data)  

    def _build_packet(self, cmd: int, payload: bytes = b"") -> bytes:
        length = 9 + len(payload)
        p = bytes([0xCC, 0x01, length & 0xFF, (length >> 8) & 0xFF, (length >> 16) & 0xFF, cmd]) + payload
        checksum = bytes([sum(p) & 0xFF])
        return p + checksum + PKT_TAIL

    def _read_framed_packet(self, timeout_s: float, min_needed: int) -> bytes:
        buf = bytearray()
        t0 = time.time()

        while time.time() - t0 < timeout_s:
            waiting = self._ser.in_waiting  
            if waiting:
                buf.extend(self._ser.read(waiting)) 

                start = buf.find(PKT_HDR)
                if start == -1:
                    if len(buf) > 1:
                        buf[:] = buf[-1:]
                    continue

                if start > 0:
                    del buf[:start]

                if len(buf) < 5:
                    continue

                length = buf[2] | (buf[3] << 8) | (buf[4] << 16)

                target_len = length if length >= min_needed else min_needed

                if len(buf) >= target_len:
                    packet = bytes(buf[:target_len])

                    if packet.endswith(PKT_TAIL):
                        return packet
                    if len(packet) >= min_needed:
                        return packet

            time.sleep(0.01)

        raise TimeoutError(f"Timeout reading measurement packet (got {len(buf)} bytes)")


def parse_measurement_packet(packet: bytes, start_wl_nm: int, num_points: int) -> SpectrumMeasurement:
    if len(packet) < SPECTRUM_START + num_points * 2:
        raise ValueError(f"Packet too short for spectrum: {len(packet)} bytes")

    n = struct.unpack("<h", packet[EXPONENT_START:EXPONENT_START + 2])[0]

    param_block = packet[PARAM_START:PARAM_END]
    floats = struct.unpack("<" + "f" * 50, param_block)
    params: Dict[str, float] = {name: float(val) for name, val in zip(PARAM_NAMES, floats)}

    raw = struct.unpack("<" + "H" * num_points, packet[SPECTRUM_START:SPECTRUM_START + num_points * 2])
    scale = 10 ** n
    intensities: List[float] = [v / scale for v in raw]

    from datetime import datetime
    return SpectrumMeasurement(
        timestamp=datetime.now(),
        start_wl_nm=start_wl_nm,
        intensities=intensities,
        parameters=params,
        scale_exponent=int(n),
    )
