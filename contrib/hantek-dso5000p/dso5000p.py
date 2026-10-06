#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dso5000p - command line tool for Hantek DSO5000P oscilloscopes
(DSO5072P, DSO5102P, DSO5202P) over USB.

The scope speaks the "Das Oszi" protocol over a vendor specific bulk
interface (VID:PID 049f:505a, OUT endpoint 0x02, IN endpoint 0x81).

Every packet, in both directions:

    byte 0      0x53 (0x43 for the debug/shell packet)
    byte 1..2   length, little endian, counts command byte + payload + checksum
    byte 3      command (replies carry command | 0x80)
    byte 4..    payload
    last byte   checksum: low byte of the sum of all preceding bytes

Protocol knowledge collected from:
  * https://elinux.org/Das_Oszi_Protocol
  * https://github.com/titos-carrasco/DSO5102P-Python  (MIT)
  * https://github.com/crt0512/xdso                     (settings, scaling, quirks)

Only dependency is pyusb (pip install pyusb). matplotlib is optional and
only needed for the "live" and "plot" commands.
"""

import argparse
import csv
import datetime
import struct
import sys
import time
import zlib

VID = 0x049F
PID = 0x505A
EP_OUT = 0x02
EP_IN = 0x81
INTERFACE = 0

MAGIC = 0x53
MAGIC_DEBUG = 0x43

CMD_ECHO = 0x00
CMD_READ_SETTINGS = 0x01
CMD_READ_SAMPLES = 0x02
CMD_READ_FILE = 0x10
CMD_CONTROL = 0x12
CMD_KEY = 0x13
CMD_SCREENSHOT = 0x20
CMD_SYSTEM_TIME = 0x21

FLAG_EMPTY = 0x00
FLAG_MORE = 0x01

SCREEN_W = 800
SCREEN_H = 480

SAMPLES = 3200          # per channel, always
H_DIVS = 16             # 3200 samples span 16 divisions of 200 samples
SAMPLES_PER_DIV = SAMPLES // H_DIVS
COUNTS_PER_DIV = 25.6   # adc counts per vertical division
CENTRE_COUNT = 128.0    # count that sits on the graticule centre line

# The scope ignores commands that arrive less than ~15 ms after the previous
# reply. 20 ms is reliable.
CMD_GAP = 0.020
READ_LEN = 16 * 1024    # one bulk read returns one whole protocol packet
SKIP_LIMIT = 8          # stale replies to skip before giving up

T_SHORT = 400           # ms
T_SAMPLES = 700
T_SCREEN = 4000
T_DRAIN = 300

# Settings blob layout (208 bytes), from the scope's own protocol.inf.
SETTINGS_LAYOUT = [
    ("VERT-CH1-DISP", 1), ("VERT-CH1-VB", 1), ("VERT-CH1-COUP", 1),
    ("VERT-CH1-20MHZ", 1), ("VERT-CH1-FINE", 1), ("VERT-CH1-PROBE", 1),
    ("VERT-CH1-RPHASE", 1), ("VERT-CH1-CNT-FINE", 1), ("VERT-CH1-POS", 2),
    ("VERT-CH2-DISP", 1), ("VERT-CH2-VB", 1), ("VERT-CH2-COUP", 1),
    ("VERT-CH2-20MHZ", 1), ("VERT-CH2-FINE", 1), ("VERT-CH2-PROBE", 1),
    ("VERT-CH2-RPHASE", 1), ("VERT-CH2-CNT-FINE", 1), ("VERT-CH2-POS", 2),
    ("TRIG-STATE", 1), ("TRIG-TYPE", 1), ("TRIG-SRC", 1), ("TRIG-MODE", 1),
    ("TRIG-COUP", 1), ("TRIG-VPOS", 2), ("TRIG-FREQUENCY", 8),
    ("TRIG-HOLDTIME-MIN", 8), ("TRIG-HOLDTIME-MAX", 8), ("TRIG-HOLDTIME", 8),
    ("TRIG-EDGE-SLOPE", 1), ("TRIG-VIDEO-NEG", 1), ("TRIG-VIDEO-PAL", 1),
    ("TRIG-VIDEO-SYN", 1), ("TRIG-VIDEO-LINE", 2), ("TRIG-PULSE-NEG", 1),
    ("TRIG-PULSE-WHEN", 1), ("TRIG-PULSE-TIME", 8), ("TRIG-SLOPE-SET", 1),
    ("TRIG-SLOPE-WIN", 1), ("TRIG-SLOPE-WHEN", 1), ("TRIG-SLOPE-V1", 2),
    ("TRIG-SLOPE-V2", 2), ("TRIG-SLOPE-TIME", 8),
    ("TRIG-SWAP-CH1-TYPE", 1), ("TRIG-SWAP-CH1-MODE", 1),
    ("TRIG-SWAP-CH1-COUP", 1), ("TRIG-SWAP-CH1-EDGE-SLOPE", 1),
    ("TRIG-SWAP-CH1-VIDEO-NEG", 1), ("TRIG-SWAP-CH1-VIDEO-PAL", 1),
    ("TRIG-SWAP-CH1-VIDEO-SYN", 1), ("TRIG-SWAP-CH1-VIDEO-LINE", 2),
    ("TRIG-SWAP-CH1-PULSE-NEG", 1), ("TRIG-SWAP-CH1-PULSE-WHEN", 1),
    ("TRIG-SWAP-CH1-PULSE-TIME", 1), ("TRIG-SWAP-CH1-SLOPE-SET", 1),
    ("TRIG-SWAP-CH1-SLOPE-WIN", 1), ("TRIG-SWAP-CH1-SLOPE-WHEN", 1),
    ("TRIG-SWAP-CH1-SLOPE-V1", 2), ("TRIG-SWAP-CH1-SLOPE-V2", 2),
    ("TRIG-SWAP-CH1-SLOPE-TIME", 8),
    ("TRIG-SWAP-CH2-TYPE", 1), ("TRIG-SWAP-CH2-MODE", 1),
    ("TRIG-SWAP-CH2-COUP", 1), ("TRIG-SWAP-CH2-EDGE-SLOPE", 1),
    ("TRIG-SWAP-CH2-VIDEO-NEG", 1), ("TRIG-SWAP-CH2-VIDEO-PAL", 1),
    ("TRIG-SWAP-CH2-VIDEO-SYN", 1), ("TRIG-SWAP-CH2-VIDEO-LINE", 2),
    ("TRIG-SWAP-CH2-PULSE-NEG", 1), ("TRIG-SWAP-CH2-PULSE-WHEN", 1),
    ("TRIG-SWAP-CH2-PULSE-TIME", 8), ("TRIG-SWAP-CH2-SLOPE-SET", 1),
    ("TRIG-SWAP-CH2-SLOPE-WIN", 1), ("TRIG-SWAP-CH2-SLOPE-WHEN", 1),
    ("TRIG-SWAP-CH2-SLOPE-V1", 2), ("TRIG-SWAP-CH2-SLOPE-V2", 2),
    ("TRIG-SWAP-CH2-SLOPE-TIME", 8),
    ("TRIG-OVERTIME-NEG", 1), ("TRIG-OVERTIME-TIME", 8),
    ("HORIZ-TB", 1), ("HORIZ-WIN-TB", 1), ("HORIZ-WIN-STATE", 1),
    ("HORIZ-TRIGTIME", 8),
    ("MATH-DISP", 1), ("MATH-MODE", 1), ("MATH-FFT-SRC", 1),
    ("MATH-FFT-WIN", 1), ("MATH-FFT-FACTOR", 1), ("MATH-FFT-DB", 1),
    ("DISPLAY-MODE", 1), ("DISPLAY-PERSIST", 1), ("DISPLAY-FORMAT", 1),
    ("DISPLAY-CONTRAST", 1), ("DISPLAY-MAXCONTRAST", 1),
    ("DISPLAY-GRID-KIND", 1), ("DISPLAY-GRID-BRIGHT", 1),
    ("DISPLAY-MAXGRID-BRIGHT", 1),
    ("ACQURIE-MODE", 1), ("ACQURIE-AVG-CNT", 1), ("ACQURIE-TYPE", 1),
    ("ACQURIE-STORE-DEPTH", 1),
    ("MEASURE-ITEM1-SRC", 1), ("MEASURE-ITEM1", 1),
    ("MEASURE-ITEM2-SRC", 1), ("MEASURE-ITEM2", 1),
    ("MEASURE-ITEM3-SRC", 1), ("MEASURE-ITEM3", 1),
    ("MEASURE-ITEM4-SRC", 1), ("MEASURE-ITEM4", 1),
    ("MEASURE-ITEM5-SRC", 1), ("MEASURE-ITEM5", 1),
    ("MEASURE-ITEM6-SRC", 1), ("MEASURE-ITEM6", 1),
    ("MEASURE-ITEM7-SRC", 1), ("MEASURE-ITEM7", 1),
    ("MEASURE-ITEM8-SRC", 1), ("MEASURE-ITEM8", 1),
    ("CONTROL-TYPE", 1), ("CONTROL-MENUID", 1), ("CONTROL-DISP-MENU", 1),
]
SETTINGS_LEN = 208

# Front panel keys. The key code is the index in this list
# (the scope's keyprotocol.inf), with a human friendly alias.
KEYS = [
    ("FN-0-KEY", "f0"), ("FN-1-KEY", "f1"), ("FN-2-KEY", "f2"),
    ("FN-3-KEY", "f3"), ("FN-4-KEY", "f4"), ("FN-5-KEY", "f5"),
    ("FN-6-KEY", "f6"), ("FN-7-KEY", "f7"),
    ("FN-MLEFT-KEY", "knob-left"), ("FN-MRIGHT-KEY", "knob-right"),
    ("FN-MZERO-KEY", "knob-push"),
    ("MENU-SR-KEY", "save-recall"), ("MENU-MEASURE-KEY", "measure"),
    ("MENU-ACQUIRE-KEY", "acquire"), ("MENU-UTILITY-KEY", "utility"),
    ("MENU-CURSOR-KEY", "cursor"), ("MENU-DISPLAY-KEY", "display"),
    ("CT-AUTOSET-KEY", "autoset"), ("CT-SINGLESEQ-KEY", "single"),
    ("CT-RS-KEY", "runstop"), ("CT-HELP-KEY", "help"),
    ("CT-DS-KEY", "default-setup"), ("CT-STU-KEY", "save-to-usb"),
    ("VT-MATH-MENU-KEY", "math"),
    ("VT-CH1-MENU-KEY", "ch1"), ("VT-CH1-PSUB-KEY", "ch1-pos-down"),
    ("VT-CH1-PADD-KEY", "ch1-pos-up"), ("VT-CH1-PZERO-KEY", "ch1-pos-zero"),
    ("VT-CH1-VBSUB-KEY", "ch1-volts-down"), ("VT-CH1-VBADD-KEY", "ch1-volts-up"),
    ("VT-CH2-MENU-KEY", "ch2"), ("VT-CH2-PSUB-KEY", "ch2-pos-down"),
    ("VT-CH2-PADD-KEY", "ch2-pos-up"), ("VT-CH2-PZERO-KEY", "ch2-pos-zero"),
    ("VT-CH2-VBSUB-KEY", "ch2-volts-down"), ("VT-CH2-VBADD-KEY", "ch2-volts-up"),
    ("HZ-MENU-KEY", "horiz"), ("HZ-PSUB-KEY", "horiz-pos-down"),
    ("HZ-PADD-KEY", "horiz-pos-up"), ("HZ-PZERO-KEY", "horiz-pos-zero"),
    ("HZ-TBSUB-KEY", "time-down"), ("HZ-TBADD-KEY", "time-up"),
    ("TG-MENU-KEY", "trig"), ("TG-PSUB-KEY", "trig-level-down"),
    ("TG-PADD-KEY", "trig-level-up"), ("TG-PZERO-KEY", "trig-level-zero"),
    ("TG-PHALF-KEY", "trig-50"), ("TG-FORCE-KEY", "force"),
    ("TG-PROBECHECK-KEY", "probe-check"),
]

VOLTS_DIV = [1e-3, 2e-3, 5e-3, 10e-3, 20e-3, 50e-3, 100e-3, 200e-3, 500e-3,
             1.0, 2.0, 5.0, 10.0]
PROBE_MULT = [1, 10, 100, 1000]
TIME_DIV = [2e-9, 5e-9, 10e-9, 20e-9, 50e-9, 100e-9, 200e-9, 500e-9,
            1e-6, 2e-6, 5e-6, 10e-6, 20e-6, 50e-6, 100e-6, 200e-6, 500e-6,
            1e-3, 2e-3, 5e-3, 10e-3, 20e-3, 50e-3, 100e-3, 200e-3, 500e-3,
            1.0, 2.0, 5.0, 10.0, 20.0, 50.0]

COUPLING = ["DC", "AC", "GND"]
TRIG_TYPE = ["Edge", "Video", "Pulse", "Slope", "Overtime", "Swap"]
TRIG_MODE = ["Auto", "Normal", "Single"]
TRIG_SRC = ["CH1", "CH2", "EXT", "EXT/5", "AC Line"]
TRIG_SLOPE = ["Rising", "Falling"]
ACQ_MODE = ["Sample", "Peak Detect", "Average"]


class ScopeError(Exception):
    pass


# --------------------------------------------------------------------------
# framing

def checksum(data):
    return sum(data) & 0xFF


def encode(cmd, payload=b"", magic=MAGIC):
    length = len(payload) + 2
    packet = bytes([magic, length & 0xFF, (length >> 8) & 0xFF, cmd]) + bytes(payload)
    return packet + bytes([checksum(packet)])


def decode(raw):
    """Split a raw packet into (command, payload)."""
    raw = bytes(raw)
    if len(raw) < 5:
        raise ScopeError("short packet (%d bytes)" % len(raw))
    if raw[0] not in (MAGIC, MAGIC_DEBUG):
        raise ScopeError("bad magic byte 0x%02x" % raw[0])
    if checksum(raw[:-1]) != raw[-1]:
        raise ScopeError("bad checksum")
    return raw[3], raw[4:-1]


# --------------------------------------------------------------------------
# settings

def _signed16(v):
    return v - 0x10000 if v > 0x7FFF else v


def _lookup(table, idx):
    return table[idx] if 0 <= idx < len(table) else None


class Settings:
    """The 208 byte front panel state, decoded."""

    def __init__(self, payload):
        if len(payload) < SETTINGS_LEN:
            raise ScopeError("settings: expected %d bytes, got %d"
                             % (SETTINGS_LEN, len(payload)))
        self.raw = {}
        offset = 0
        for name, size in SETTINGS_LAYOUT:
            self.raw[name] = int.from_bytes(payload[offset:offset + size], "little")
            offset += size

    def channel(self, ch):
        """ch is 1 or 2"""
        p = "VERT-CH%d-" % ch
        vb = self.raw[p + "VB"]
        probe = self.raw[p + "PROBE"]
        vdiv = _lookup(VOLTS_DIV, vb)
        mult = _lookup(PROBE_MULT, probe)
        return {
            "enabled": self.raw[p + "DISP"] != 0,
            "volts_div": vdiv * mult if vdiv is not None and mult is not None else None,
            "probe": mult,
            "coupling": _lookup(COUPLING, self.raw[p + "COUP"]),
            "position": _signed16(self.raw[p + "POS"]),
            "bw_limit": self.raw[p + "20MHZ"] != 0,
        }

    @property
    def running(self):
        return self.raw["TRIG-STATE"] != 0

    @property
    def seconds_div(self):
        return _lookup(TIME_DIV, self.raw["HORIZ-TB"])

    @property
    def sample_interval(self):
        s = self.seconds_div
        return s / SAMPLES_PER_DIV if s is not None else None

    @property
    def trigger(self):
        return {
            "type": _lookup(TRIG_TYPE, self.raw["TRIG-TYPE"]),
            "mode": _lookup(TRIG_MODE, self.raw["TRIG-MODE"]),
            "source": _lookup(TRIG_SRC, self.raw["TRIG-SRC"]),
            "slope": _lookup(TRIG_SLOPE, self.raw["TRIG-EDGE-SLOPE"]),
            "level": _signed16(self.raw["TRIG-VPOS"]),
            "frequency_hz": self.raw["TRIG-FREQUENCY"] / 1000.0,  # milli hertz
        }

    @property
    def acquire_mode(self):
        return _lookup(ACQ_MODE, self.raw["ACQURIE-MODE"])


def settings_blob(raw):
    """Inverse of Settings(): build the 208 byte blob from {name: value}."""
    blob = bytearray(SETTINGS_LEN)
    offset = 0
    for name, size in SETTINGS_LAYOUT:
        v = raw.get(name, 0) & ((1 << (8 * size)) - 1)
        blob[offset:offset + size] = v.to_bytes(size, "little")
        offset += size
    return bytes(blob)


def counts_to_volts(counts, channel_settings):
    """Convert sample counts (already 0x80 flipped) to volts at the probe tip.

    The scope bakes the channel's vertical position into the samples, so it is
    taken back off here."""
    vdiv = channel_settings["volts_div"]
    if not vdiv:
        raise ScopeError("unknown volts/div setting")
    pos = channel_settings["position"]
    return [(c - CENTRE_COUNT - pos) / COUNTS_PER_DIV * vdiv for c in counts]


# --------------------------------------------------------------------------
# images

def rgb565_to_rgb(raw):
    out = bytearray(len(raw) // 2 * 3)
    j = 0
    for (v,) in struct.iter_unpack("<H", raw[:len(raw) // 2 * 2]):
        out[j] = ((v >> 11) & 0x1F) << 3
        out[j + 1] = ((v >> 5) & 0x3F) << 2
        out[j + 2] = (v & 0x1F) << 3
        j += 3
    return bytes(out)


def png_bytes(width, height, rgb, level=6):
    """Minimal PNG encoder, so no Pillow is needed."""
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    stride = width * 3
    rows = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, level))
            + chunk(b"IEND", b""))


def write_png(path, width, height, rgb):
    with open(path, "wb") as f:
        f.write(png_bytes(width, height, rgb))


# --------------------------------------------------------------------------
# device

class UsbTransport:
    """Thin pyusb wrapper. Kept separate so tests can swap in a fake."""

    def __init__(self):
        try:
            import usb.core
            import usb.util
        except ImportError:
            raise ScopeError("pyusb fehlt: pip install pyusb")
        self._usb = usb
        try:
            dev = usb.core.find(idVendor=VID, idProduct=PID)
        except usb.core.NoBackendError:
            # Windows: libusb DLL shipped by the libusb-package wheel
            try:
                import libusb_package
            except ImportError:
                raise ScopeError("keine libusb gefunden: pip install libusb-package")
            dev = usb.core.find(idVendor=VID, idProduct=PID,
                                backend=libusb_package.get_libusb1_backend())
        if dev is None:
            raise ScopeError("kein DSO5000P gefunden (USB %04x:%04x). Eingesteckt und "
                             "eingeschaltet? Unter Windows: WinUSB-Treiber (Zadig) "
                             "installiert? Siehe README." % (VID, PID))
        try:
            if dev.is_kernel_driver_active(INTERFACE):
                # Linux binds cdc_subset (usb0 network device) to the scope.
                dev.detach_kernel_driver(INTERFACE)
        except (NotImplementedError, usb.core.USBError):
            pass  # not supported on Windows/macOS
        try:
            try:
                dev.get_active_configuration()
            except usb.core.USBError:
                dev.set_configuration()
        except NotImplementedError:
            # libusb on Windows can only open devices bound to WinUSB/libusbK
            raise ScopeError(
                "Scope gefunden, aber Windows benutzt noch den Hantek-Treiber.\n"
                "  Mit Zadig auf WinUSB umstellen: Options -> List All Devices, Geraet\n"
                "  049F 505A waehlen (bei 'Interface 0'/'Interface 1' das Interface 0),\n"
                "  WinUSB einstellen, Replace Driver, danach Scope neu anstecken.")
        try:
            usb.util.claim_interface(dev, INTERFACE)
        except usb.core.USBError as e:
            if getattr(e, "errno", None) == 13:
                raise ScopeError("keine Berechtigung fuer das USB-Geraet. udev-Regel "
                                 "installieren (siehe README) oder mit sudo starten.")
            raise ScopeError("USB-Interface belegt (%s). Laeuft noch ein anderes "
                             "Programm, das auf das Scope zugreift?" % e)
        # The detached kernel driver leaves the data toggle out of sync, which
        # makes the scope swallow our first command. Clearing halt resyncs it.
        for ep in (EP_IN, EP_OUT):
            try:
                dev.clear_halt(ep)
            except usb.core.USBError:
                pass
        self.dev = dev

    def write(self, data, timeout_ms):
        self.dev.write(EP_OUT, data, timeout_ms)

    def read(self, timeout_ms):
        try:
            return bytes(self.dev.read(EP_IN, READ_LEN, timeout_ms))
        except self._usb.core.USBTimeoutError:
            return None
        except self._usb.core.USBError as e:
            if getattr(e, "errno", None) == 110 or "timed out" in str(e).lower():
                return None
            raise

    def close(self):
        try:
            self._usb.util.release_interface(self.dev, INTERFACE)
            self._usb.util.dispose_resources(self.dev)
        except self._usb.core.USBError:
            pass


class DSO5000P:
    def __init__(self, transport=None):
        self.t = transport if transport is not None else UsbTransport()
        self._last_io = 0.0
        self.drain()
        self._wake()

    # -- low level -------------------------------------------------------

    def _send(self, cmd, payload=b"", magic=MAGIC):
        wait = CMD_GAP - (time.monotonic() - self._last_io)
        if wait > 0:
            time.sleep(wait)
        self.t.write(encode(cmd, payload, magic), T_SHORT)
        self._last_io = time.monotonic()

    def _read(self, timeout_ms):
        raw = self.t.read(timeout_ms)
        self._last_io = time.monotonic()
        if raw is None:
            raise ScopeError("Zeitueberschreitung, Scope antwortet nicht")
        return decode(raw)

    def _reply(self, cmd, timeout_ms=T_SHORT):
        want = cmd | 0x80
        for _ in range(SKIP_LIMIT):
            got, payload = self._read(timeout_ms)
            if got == want:
                return payload
        raise ScopeError("scope out of sync (no reply to 0x%02x)" % cmd)

    def _multi(self, cmd, timeout_ms, header):
        """Collect a multi packet reply. header = bytes of preamble after the flag."""
        want = cmd | 0x80
        out = bytearray()
        skipped = 0
        while True:
            got, payload = self._read(timeout_ms)
            if got != want:
                skipped += 1
                if skipped > SKIP_LIMIT:
                    raise ScopeError("scope out of sync")
                continue
            if not payload:
                break
            flag = payload[0]
            if flag == FLAG_MORE:
                out += payload[1 + header:]
            elif flag == FLAG_EMPTY and cmd == CMD_READ_SAMPLES:
                continue  # acquisition stopped, keep reading until end flag
            else:
                break
        return bytes(out)

    def drain(self):
        while self.t.read(T_DRAIN) is not None:
            pass

    def _wake(self):
        # A freshly claimed scope often eats the first command or three.
        last = None
        for _ in range(12):
            try:
                self.settings()
                return
            except ScopeError as e:
                last = e
        raise ScopeError("scope does not answer: %s" % last)

    # -- commands --------------------------------------------------------

    def echo(self, data):
        self._send(CMD_ECHO, data)
        return self._reply(CMD_ECHO)

    def settings(self):
        self._send(CMD_READ_SETTINGS)
        return Settings(self._reply(CMD_READ_SETTINGS))

    def samples(self, ch):
        """Raw sample counts for channel 1 or 2 (0x80 flipped, 128 = centre).
        Empty while acquisition is stopped."""
        self._send(CMD_READ_SAMPLES, bytes([0x01, ch - 1]))
        data = self._multi(CMD_READ_SAMPLES, T_SAMPLES, header=1)
        return [b ^ 0x80 for b in data]

    def screenshot(self):
        """Returns 800x480 RGB bytes."""
        self._send(CMD_SCREENSHOT)
        raw = self._multi(CMD_SCREENSHOT, T_SCREEN, header=0)
        want = SCREEN_W * SCREEN_H * 2
        if len(raw) != want:
            raise ScopeError("screenshot truncated: %d of %d bytes" % (len(raw), want))
        return rgb565_to_rgb(raw)

    def system_time(self):
        self._send(CMD_SYSTEM_TIME)
        p = self._reply(CMD_SYSTEM_TIME)
        if len(p) < 7:
            raise ScopeError("short time reply")
        year = p[0] | (p[1] << 8)
        return "%04d-%02d-%02d %02d:%02d:%02d" % (year, p[2], p[3], p[4], p[5], p[6])

    def lock_panel(self, locked):
        self._send(CMD_CONTROL, bytes([0x01, 1 if locked else 0]))
        self._reply(CMD_CONTROL)

    def press(self, code):
        self._send(CMD_KEY, bytes([code, 0x01]))
        try:
            self._read(T_SHORT)  # ack carries nothing and sometimes never comes
        except ScopeError:
            pass

    def read_file(self, path):
        self._send(CMD_READ_FILE, b"\x00" + path.encode())
        return self._multi(CMD_READ_FILE, T_SAMPLES, header=0)

    def waveform(self, ch, settings=None):
        """(times, volts) for a channel, or None if it is off / stopped."""
        s = settings or self.settings()
        cs = s.channel(ch)
        if not cs["enabled"]:
            return None
        counts = self.samples(ch)
        if not counts:
            return None
        dt = s.sample_interval or 0.0
        return [i * dt for i in range(len(counts))], counts_to_volts(counts, cs)

    def close(self):
        self.t.close()


# --------------------------------------------------------------------------
# simulated scope, for --demo and tests

class SimTransport:
    """Behaves like a DSO5102P on the wire: CH1 1 kHz sine, CH2 500 Hz square.
    Reacts to the front panel keys for V/div, position, time/div, run/stop."""

    def __init__(self):
        import math
        import random
        self._math = math
        self._rand = random.Random(1)
        self.raw = {
            "VERT-CH1-DISP": 1, "VERT-CH1-VB": 9, "VERT-CH1-POS": 25,
            "VERT-CH2-DISP": 1, "VERT-CH2-VB": 10, "VERT-CH2-POS": -60 & 0xFFFF,
            "TRIG-STATE": 1, "TRIG-MODE": 0, "HORIZ-TB": 15,
            "TRIG-FREQUENCY": 1000000,
        }
        self.locked = False
        self.out = []
        self.phase = 0.0

    # signals in volts at time t
    def _signal(self, ch, t):
        m = self._math
        if ch == 1:
            return 1.5 * m.sin(2 * m.pi * 1000 * t + self.phase) + self._rand.gauss(0, 0.01)
        return (3.3 if (t * 500 + self.phase / (2 * m.pi)) % 1 < 0.5 else 0.0) + self._rand.gauss(0, 0.005)

    def _counts(self, ch):
        s = Settings(settings_blob(self.raw))
        cs = s.channel(ch)
        dt = s.sample_interval
        out = []
        for i in range(SAMPLES):
            c = CENTRE_COUNT + cs["position"] + self._signal(ch, (i - SAMPLES / 2) * dt) / cs["volts_div"] * COUNTS_PER_DIV
            out.append(max(0, min(255, int(round(c)))))
        return out

    def _render(self):
        """Fake screen: graticule plus both traces, RGB565."""
        w, h = SCREEN_W, SCREEN_H
        px = bytearray(b"\x00\x00" * (w * h))
        x0, y0, gw, gh = 0, 40, 800, 400

        def put(x, y, col):
            if 0 <= x < w and 0 <= y < h:
                struct.pack_into("<H", px, 2 * (y * w + x), col)
        for i in range(17):
            for y in range(y0, y0 + gh, 4):
                put(x0 + i * gw // 16, y, 0x4208)
        for j in range(9):
            for x in range(x0, x0 + gw, 4):
                put(x, y0 + j * gh // 8, 0x4208)
        for ch, col in ((1, 0xFFE0), (2, 0x07FF)):
            if not self.raw["VERT-CH%d-DISP" % ch]:
                continue
            for i, c in enumerate(self._counts(ch)[::4]):
                put(x0 + i, int(y0 + gh / 2 - (c - CENTRE_COUNT) * gh / 8 / COUNTS_PER_DIV), col)
        return bytes(px)

    def _key(self, code):
        name = KEYS[code][0]
        r = self.raw
        step = {"VBADD": 1, "VBSUB": -1}
        for ch in (1, 2):
            p = "VT-CH%d-" % ch
            if name.startswith(p):
                k = name[len(p):-4]
                if k in step:
                    r["VERT-CH%d-VB" % ch] = max(0, min(len(VOLTS_DIV) - 1, r["VERT-CH%d-VB" % ch] + step[k]))
                elif k in ("PADD", "PSUB"):
                    pos = _signed16(r["VERT-CH%d-POS" % ch]) + (4 if k == "PADD" else -4)
                    r["VERT-CH%d-POS" % ch] = max(-200, min(200, pos)) & 0xFFFF
                elif k == "PZERO":
                    r["VERT-CH%d-POS" % ch] = 0
                elif k == "MENU":
                    r["VERT-CH%d-DISP" % ch] ^= 1
        if name == "HZ-TBADD-KEY":
            r["HORIZ-TB"] = min(len(TIME_DIV) - 1, r["HORIZ-TB"] + 1)
        elif name == "HZ-TBSUB-KEY":
            r["HORIZ-TB"] = max(0, r["HORIZ-TB"] - 1)
        elif name == "CT-RS-KEY":
            r["TRIG-STATE"] ^= 1
        elif name == "CT-AUTOSET-KEY":
            r.update({"VERT-CH1-VB": 9, "VERT-CH2-VB": 10, "HORIZ-TB": 15, "TRIG-STATE": 1})

    def write(self, data, _timeout_ms):
        cmd, payload = decode(data)
        q = self.out
        rep = lambda p: q.append(encode(cmd | 0x80, p))
        if cmd == CMD_READ_SETTINGS:
            rep(settings_blob(self.raw))
        elif cmd == CMD_ECHO:
            rep(payload)
        elif cmd == CMD_SYSTEM_TIME:
            n = datetime.datetime.now()
            rep(struct.pack("<H", n.year) + bytes([n.month, n.day, n.hour, n.minute, n.second]))
        elif cmd == CMD_READ_SAMPLES:
            ch = payload[1] + 1
            if self.raw["TRIG-STATE"] and self.raw["VERT-CH%d-DISP" % ch]:
                self.phase = self._rand.gauss(0, 0.02)
                wire = bytes(c ^ 0x80 for c in self._counts(ch))
                rep(bytes([0x00]) + struct.pack("<I", len(wire))[:3])
                for i in range(0, len(wire), 1200):
                    rep(bytes([0x01, ch - 1]) + wire[i:i + 1200])
            rep(bytes([0x02, ch - 1]))
        elif cmd == CMD_SCREENSHOT:
            raw = self._render()
            for i in range(0, len(raw), 10000):
                rep(b"\x01" + raw[i:i + 10000])
            rep(b"\x02")
        elif cmd == CMD_KEY:
            self._key(payload[0])
            rep(b"")
        elif cmd == CMD_CONTROL:
            if payload[0] == 1:
                self.locked = bool(payload[1])
            rep(b"")
        elif cmd == CMD_READ_FILE:
            rep(b"\x01demo")
            rep(b"\x02")

    def read(self, _timeout_ms):
        return self.out.pop(0) if self.out else None

    def close(self):
        pass


# --------------------------------------------------------------------------
# helpers for the CLI

def eng(value, unit, digits=3):
    if value is None:
        return "--"
    if value == 0:
        return "0" + unit
    for scale, prefix in ((1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""),
                          (1e-3, "m"), (1e-6, "u"), (1e-9, "n")):
        if abs(value) >= scale * 0.9995:
            return "%.*g%s%s" % (digits, value / scale, prefix, unit)
    return "%.*g%s" % (digits, value, unit)


def find_key(name):
    n = name.strip().lower()
    for code, (inf_name, alias) in enumerate(KEYS):
        if n in (alias, inf_name.lower()):
            return code
    if n.isdigit() and int(n) < len(KEYS):
        return int(n)
    raise ScopeError("unknown key '%s' (see: dso5000p.py keys)" % name)


def stats(volts):
    n = len(volts)
    lo, hi = min(volts), max(volts)
    mean = sum(volts) / n
    rms = (sum(v * v for v in volts) / n) ** 0.5
    return {"min": lo, "max": hi, "pkpk": hi - lo, "mean": mean, "rms": rms}


def frequency(volts, dt):
    """Frequency from rising crossings of the mid level (with hysteresis).
    None if fewer than two full periods are visible."""
    if not volts or not dt:
        return None
    lo, hi = min(volts), max(volts)
    if hi - lo < 1e-9:
        return None
    mid = (hi + lo) / 2
    hyst = (hi - lo) * 0.1
    armed = False
    crossings = []
    for i, v in enumerate(volts):
        if v < mid - hyst:
            armed = True
        elif armed and v > mid + hyst:
            crossings.append(i)
            armed = False
    if len(crossings) < 3:
        return None
    period = (crossings[-1] - crossings[0]) / (len(crossings) - 1) * dt
    return 1.0 / period if period > 0 else None


def clipped(counts):
    """True if the trace runs off the top or bottom of the ADC range."""
    return bool(counts) and (min(counts) <= 1 or max(counts) >= 254)


def stamp():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def capture(dso, channels):
    s = dso.settings()
    if not s.running:
        print("Hinweis: Scope ist gestoppt (RUN/STOP), es kommen evtl. keine Daten.",
              file=sys.stderr)
    waves = {}
    for ch in channels:
        w = dso.waveform(ch, s)
        if w is None:
            print("CH%d: aus oder keine Daten" % ch, file=sys.stderr)
        else:
            waves[ch] = w
    return s, waves


def write_csv(path, waves):
    chans = sorted(waves)
    n = min(len(waves[c][1]) for c in chans)
    times = waves[chans[0]][0]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_s"] + ["ch%d_V" % c for c in chans])
        for i in range(n):
            w.writerow(["%.9g" % times[i]] + ["%.6g" % waves[c][1][i] for c in chans])
    return n


# --------------------------------------------------------------------------
# commands

def cmd_info(dso, args):
    s = dso.settings()
    print("Scope-Zeit : %s" % dso.system_time())
    print("Status     : %s" % ("RUN" if s.running else "STOP"))
    print("Zeitbasis  : %s/div  (Abtastabstand %s)"
          % (eng(s.seconds_div, "s"), eng(s.sample_interval, "s")))
    print("Erfassung  : %s" % s.acquire_mode)
    t = s.trigger
    print("Trigger    : %s %s, Quelle %s, Flanke %s, Frequenz %s"
          % (t["type"], t["mode"], t["source"], t["slope"], eng(t["frequency_hz"], "Hz", 5)))
    for ch in (1, 2):
        c = s.channel(ch)
        if c["enabled"]:
            print("CH%d        : %s/div, %s, Tastkopf x%s, Position %d%s"
                  % (ch, eng(c["volts_div"], "V"), c["coupling"], c["probe"],
                     c["position"], ", 20MHz BW" if c["bw_limit"] else ""))
        else:
            print("CH%d        : aus" % ch)
    if args.raw:
        for k, v in s.raw.items():
            print("  %-26s %d" % (k, v))


def cmd_screenshot(dso, args):
    path = args.output or "dso-%s.png" % stamp()
    print("Screenshot wird geladen (ca. 1 s) ...", file=sys.stderr)
    write_png(path, SCREEN_W, SCREEN_H, dso.screenshot())
    print(path)


def cmd_capture(dso, args):
    s, waves = capture(dso, args.channel)
    if not waves:
        raise ScopeError("keine Kurvendaten erhalten")
    path = args.output or "dso-%s.csv" % stamp()
    n = write_csv(path, waves)
    print("%d Punkte gespeichert: %s" % (n, path))
    for ch, (_, v) in sorted(waves.items()):
        st = stats(v)
        print("CH%d: Vpp %s  Vmin %s  Vmax %s  Mittel %s  RMS %s"
              % (ch, eng(st["pkpk"], "V"), eng(st["min"], "V"), eng(st["max"], "V"),
                 eng(st["mean"], "V"), eng(st["rms"], "V")))
    if s.trigger["frequency_hz"]:
        print("Frequenz (Scope-Zaehler): %s" % eng(s.trigger["frequency_hz"], "Hz", 5))


def _pyplot():
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        raise ScopeError("matplotlib fehlt: pip install matplotlib")
    return plt


def _setup_axes(ax, s):
    ax.set_xlabel("Zeit [s]")
    ax.set_ylabel("Spannung [V]")
    ax.grid(True, alpha=0.4)
    ax.set_title("DSO5000P  %s/div" % eng(s.seconds_div, "s"))


COLORS = {1: "#d4a000", 2: "#1f77b4"}


def cmd_plot(dso, args):
    plt = _pyplot()
    s, waves = capture(dso, args.channel)
    if not waves:
        raise ScopeError("keine Kurvendaten erhalten")
    fig, ax = plt.subplots(figsize=(11, 5))
    for ch, (t, v) in sorted(waves.items()):
        ax.plot(t, v, color=COLORS[ch], lw=1, label="CH%d" % ch)
    _setup_axes(ax, s)
    ax.legend()
    if args.output:
        fig.savefig(args.output, dpi=120, bbox_inches="tight")
        print(args.output)
    else:
        plt.show()


def cmd_live(dso, args):
    plt = _pyplot()
    plt.ion()
    fig, ax = plt.subplots(figsize=(11, 5))
    lines = {ch: ax.plot([], [], color=COLORS[ch], lw=1, label="CH%d" % ch)[0]
             for ch in args.channel}
    ax.legend(loc="upper right")
    print("Live-Ansicht, Fenster schliessen oder Strg+C zum Beenden.", file=sys.stderr)
    try:
        while plt.fignum_exists(fig.number):
            try:
                s, waves = dso.settings(), {}
                for ch in args.channel:
                    w = dso.waveform(ch, s)
                    if w:
                        waves[ch] = w
            except ScopeError as e:
                print("Fehler: %s, versuche weiter ..." % e, file=sys.stderr)
                dso.drain()
                plt.pause(0.2)
                continue
            for ch, line in lines.items():
                if ch in waves:
                    line.set_data(*waves[ch])
                else:
                    line.set_data([], [])
            _setup_axes(ax, s)
            ax.relim()
            ax.autoscale_view()
            info = []
            for ch, (_, v) in sorted(waves.items()):
                st = stats(v)
                info.append("CH%d Vpp=%s Mittel=%s" % (ch, eng(st["pkpk"], "V"), eng(st["mean"], "V")))
            if s.trigger["frequency_hz"]:
                info.append("f=%s" % eng(s.trigger["frequency_hz"], "Hz", 5))
            if not s.running:
                info.append("[STOP]")
            ax.set_title("DSO5000P  %s/div   %s" % (eng(s.seconds_div, "s"), "   ".join(info)),
                         fontsize=9)
            plt.pause(args.interval)
    except KeyboardInterrupt:
        pass


def cmd_key(dso, args):
    for name in args.keys:
        dso.press(find_key(name))
        time.sleep(0.15)


def cmd_keys(_dso, _args):
    for code, (inf_name, alias) in enumerate(KEYS):
        print("%2d  %-16s %s" % (code, alias, inf_name))


def cmd_lock(dso, args):
    dso.lock_panel(args.state == "on")


def cmd_getfile(dso, args):
    data = dso.read_file(args.path)
    out = args.output or args.path.rstrip("/").split("/")[-1] or "file.bin"
    with open(out, "wb") as f:
        f.write(data)
    print("%d Bytes -> %s" % (len(data), out))


def cmd_monitor(dso, args):
    """Log measurements to CSV at a fixed interval (data logger)."""
    path = args.output or "dso-log-%s.csv" % stamp()
    print("Logge nach %s, Strg+C zum Beenden." % path, file=sys.stderr)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        head = ["timestamp", "frequency_Hz"]
        for ch in args.channel:
            head += ["ch%d_%s" % (ch, k) for k in ("pkpk", "min", "max", "mean", "rms")]
        w.writerow(head)
        try:
            while True:
                t0 = time.monotonic()
                try:
                    s, waves = capture(dso, args.channel)
                except ScopeError as e:
                    print("Fehler: %s" % e, file=sys.stderr)
                    dso.drain()
                    continue
                row = [datetime.datetime.now().isoformat(timespec="seconds"),
                       "%.6g" % s.trigger["frequency_hz"]]
                for ch in args.channel:
                    if ch in waves:
                        st = stats(waves[ch][1])
                        row += ["%.6g" % st[k] for k in ("pkpk", "min", "max", "mean", "rms")]
                    else:
                        row += [""] * 5
                w.writerow(row)
                f.flush()
                print(", ".join(row))
                time.sleep(max(0.0, args.interval - (time.monotonic() - t0)))
        except KeyboardInterrupt:
            pass


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="dso5000p.py",
        description="Hantek DSO5072P/5102P/5202P per USB: Screenshots, Kurven als CSV, "
                    "Live-Ansicht, Messwert-Logger, Fernbedienung.")
    p.add_argument("--demo", action="store_true",
                   help="simuliertes Scope statt echtem Geraet (zum Ausprobieren)")
    sub = p.add_subparsers(dest="command", required=True)

    def chan_arg(sp):
        sp.add_argument("-c", "--channel", type=int, nargs="+", choices=(1, 2),
                        default=[1, 2], help="Kanal/Kanaele (Standard: 1 2)")

    sp = sub.add_parser("info", help="Einstellungen des Scopes anzeigen")
    sp.add_argument("--raw", action="store_true", help="alle Rohwerte ausgeben")
    sp.set_defaults(func=cmd_info)

    sp = sub.add_parser("screenshot", help="Bildschirmfoto als PNG speichern")
    sp.add_argument("-o", "--output")
    sp.set_defaults(func=cmd_screenshot)

    sp = sub.add_parser("capture", help="Kurvendaten (Volt) als CSV speichern")
    chan_arg(sp)
    sp.add_argument("-o", "--output")
    sp.set_defaults(func=cmd_capture)

    sp = sub.add_parser("plot", help="Kurve einmal holen und anzeigen/als Bild speichern")
    chan_arg(sp)
    sp.add_argument("-o", "--output", help="Bilddatei statt Fenster (png/svg/pdf)")
    sp.set_defaults(func=cmd_plot)

    sp = sub.add_parser("live", help="Live-Ansicht am PC (matplotlib)")
    chan_arg(sp)
    sp.add_argument("-i", "--interval", type=float, default=0.05,
                    help="Pause zwischen Updates in s (Standard 0.05)")
    sp.set_defaults(func=cmd_live)

    sp = sub.add_parser("monitor", help="Messwerte periodisch in CSV loggen")
    chan_arg(sp)
    sp.add_argument("-i", "--interval", type=float, default=1.0,
                    help="Intervall in s (Standard 1)")
    sp.add_argument("-o", "--output")
    sp.set_defaults(func=cmd_monitor)

    sp = sub.add_parser("key", help="Taste(n) am Scope druecken, z.B. 'key autoset'")
    sp.add_argument("keys", nargs="+")
    sp.set_defaults(func=cmd_key)

    sp = sub.add_parser("keys", help="alle Tastennamen auflisten")
    sp.set_defaults(func=cmd_keys, offline=True)

    sp = sub.add_parser("lock", help="Bedienfeld sperren/entsperren")
    sp.add_argument("state", choices=("on", "off"))
    sp.set_defaults(func=cmd_lock)

    sp = sub.add_parser("getfile", help="Datei vom Scope-Dateisystem lesen")
    sp.add_argument("path")
    sp.add_argument("-o", "--output")
    sp.set_defaults(func=cmd_getfile)

    args = p.parse_args(argv)
    if hasattr(args, "channel"):
        args.channel = sorted(set(args.channel))

    dso = None
    try:
        if not getattr(args, "offline", False):
            dso = DSO5000P(SimTransport() if args.demo else None)
        args.func(dso, args)
    except ScopeError as e:
        print("Fehler: %s" % e, file=sys.stderr)
        return 1
    except Exception as e:  # USB errors etc.: one line instead of a traceback
        print("Fehler: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        return 1
    finally:
        if dso is not None:
            dso.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
