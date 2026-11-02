#!/usr/bin/env python3
"""
Use an ESP32 "M18 bridge" (esp32-bridge/) instead of a USB-serial adapter.

The ESP32 talks to the battery; this module talks to the ESP32 - over USB
(serial port, 115200 baud) or over Wi-Fi (HTTP) - and exposes it as an
`m18.M18` object, so read_id(), health(), write_message() and the web GUI work
unchanged.

    python m18_bridge.py --bridge COM7                 # ESP32 on USB
    python m18_bridge.py --bridge m18-bridge.local     # ESP32 over Wi-Fi
    python m18_bridge.py --bridge 192.168.1.50 --health
    python m18_bridge.py --discover                    # find bridges on the network

In Python:
    from m18_bridge import M18Bridge
    m = M18Bridge("http://m18-bridge.local")
    m.health()
"""

import base64
import datetime
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

import m18


class BridgeError(Exception):
    pass


class BridgeAuthError(BridgeError):
    """Password missing or wrong (HTTP 401) - never retried."""


def is_network_target(target):
    t = target.strip().lower()
    return t.startswith(("http://", "https://")) or t.endswith(".local") or \
        (t.count(".") == 3 and t.replace(".", "").replace(":", "").isdigit())


BUSY_WAIT_S = 30          # the bridge answers "ERR BUSY" while it reads the battery itself


class _LinkBase:
    """Retry policy shared by USB and Wi-Fi.

    Every bridge command is idempotent (reads, reset/idle, writing the same note
    byte again), so transient failures - a lost Wi-Fi packet, a USB hiccup, the
    board rebooting - are retried instead of aborting a 15-second read-out.
    """
    retries = 2

    def exec(self, lines, timeout=None):
        lines = [ln.strip() for ln in lines if ln.strip()]
        busy_until = time.time() + BUSY_WAIT_S
        attempt = 0
        while True:
            try:
                out = self._exec(lines, timeout)
            except BridgeAuthError:
                raise
            except BridgeError:
                attempt += 1
                if attempt > self.retries:
                    raise
                time.sleep(0.3 * attempt)
                self._recover()
                continue
            if any(o.startswith("ERR BUSY") for o in out) and time.time() < busy_until:
                time.sleep(1.0)
                continue
            return out

    def _recover(self):
        pass


class SerialLink(_LinkBase):
    """ESP32 connected by USB: one text command per line, one reply per line."""

    def __init__(self, device, timeout=6.0):
        self.device = device
        self.timeout = timeout
        self.ser = None
        self._open()
        self._wait_ready()

    def _open(self):
        import serial  # pyserial
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
        s = serial.Serial()
        s.port = self.device
        s.baudrate = 115200
        s.timeout = 0.2
        s.write_timeout = 2
        # Classic ESP32 boards reset when DTR/RTS toggle; keep them released.
        try:
            s.dtr = False
            s.rts = False
        except (OSError, ValueError, serial.SerialException):
            pass
        s.open()
        self.ser = s

    def _wait_ready(self, seconds=5.0):
        # The board may have just rebooted: retry PING for a few seconds.
        deadline = time.time() + seconds
        last = None
        while time.time() < deadline:
            try:
                last = self._exec(["PING"], timeout=0.8)[0]
                if last.startswith("OK M18BRIDGE"):
                    return
            except BridgeError as e:
                last = str(e)
        raise BridgeError(f"Keine M18-Brücke an {self.device} (Antwort: {last!r})")

    def _recover(self):
        # USB re-enumerated (board reset, cable wiggle): reopen the port and resync.
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                self._open()
                self._wait_ready(3.0)
                return
            except Exception:
                time.sleep(0.5)

    def _exec(self, lines, timeout=None):
        import serial
        timeout = timeout or self.timeout
        replies = []
        try:
            self.ser.reset_input_buffer()
            for line in lines:
                self.ser.write((line + "\n").encode("utf-8"))
                self.ser.flush()
                deadline = time.time() + timeout
                while True:
                    raw = self.ser.readline().decode("utf-8", "replace").strip()
                    if raw.startswith(("OK", "ERR")):
                        replies.append(raw)
                        break
                    if time.time() > deadline:
                        raise BridgeError(f"Zeitüberschreitung bei {line!r}")
        except (serial.SerialException, OSError) as e:
            raise BridgeError(f"USB-Verbindung zur Brücke unterbrochen: {e}") from e
        return replies

    def close(self):
        if self.ser is not None:
            self.ser.close()


class HttpLink(_LinkBase):
    """ESP32 on the network: POST /cmd with one command per line."""

    def __init__(self, target, timeout=10.0, password=None):
        t = target.strip().rstrip("/")
        if not t.startswith(("http://", "https://")):
            t = "http://" + t
        # credentials may be part of the URL: http://m18:secret@host
        parts = urllib.parse.urlsplit(t)
        if parts.password is not None and not password:
            password = urllib.parse.unquote(parts.password)
        if parts.username is not None or parts.password is not None:
            host = parts.hostname + (f":{parts.port}" if parts.port else "")
            t = urllib.parse.urlunsplit((parts.scheme, host, parts.path, "", ""))
        self.base = t
        self.device = t
        self.timeout = timeout
        self.auth = None
        if password:
            self.auth = "Basic " + base64.b64encode(f"m18:{password}".encode()).decode()
        r = self.exec(["PING"])[0]
        if not r.startswith("OK M18BRIDGE"):
            raise BridgeError(f"{t} ist keine M18-Brücke (Antwort: {r!r})")

    def _exec(self, lines, timeout=None):
        body = ("\n".join(lines) + "\n").encode("utf-8")
        headers = {"Content-Type": "text/plain"}
        if self.auth:
            headers["Authorization"] = self.auth
        req = urllib.request.Request(self.base + "/cmd", data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                out = resp.read().decode("utf-8", "replace").splitlines()
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise BridgeAuthError("Die Brücke ist passwortgeschützt – Passwort fehlt oder ist falsch") from e
            raise BridgeError(f"M18-Brücke {self.base}: HTTP {e.code}") from e
        except (urllib.error.URLError, OSError) as e:
            raise BridgeError(f"M18-Brücke {self.base} nicht erreichbar: {e}") from e
        out = [o.strip() for o in out if o.strip()]
        if len(out) != len(lines):
            raise BridgeError(f"Unerwartete Antwort der Brücke: {out!r}")
        return out

    def close(self):
        pass


DISCOVERY_PORT = 18018
ESPRESSIF_USB_VID = 0x303A   # native USB of ESP32-S2/S3/C3


def discover(timeout=1.5, targets=None):
    """Find bridges on the local network (UDP broadcast "M18BRIDGE?" on port 18018).

    Returns [{"host": "m18-bridge-ab12.local", "ip": "192.168.1.50", "version": "1.1", ...}].
    """
    targets = targets or ["255.255.255.255"] + _subnet_broadcasts()
    found = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.bind(("", 0))
        for t in targets:
            try:
                s.sendto(b"M18BRIDGE?", (t, DISCOVERY_PORT))
            except OSError:
                pass
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                break
            s.settimeout(left)
            try:
                data, addr = s.recvfrom(512)
            except (socket.timeout, OSError):
                break
            text = data.decode("ascii", "replace").strip()
            if not text.startswith("M18BRIDGE "):
                continue
            parts = text.split()
            info = {"version": parts[1] if len(parts) > 1 else ""}
            for p in parts[2:]:
                if "=" in p:
                    k, v = p.split("=", 1)
                    info[k] = v
            info["ip"] = info.get("ip") or addr[0]
            info["reply_from"] = addr[0]
            found[info.get("mac") or addr[0]] = info
    finally:
        s.close()
    return sorted(found.values(), key=lambda i: i.get("host", ""))


def _subnet_broadcasts():
    """x.y.z.255 of the local IPv4 addresses (helps on Windows with several adapters)."""
    out = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                out.add(ip.rsplit(".", 1)[0] + ".255")
    except OSError:
        pass
    try:
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.connect(("10.255.255.255", 1))
        out.add(u.getsockname()[0].rsplit(".", 1)[0] + ".255")
        u.close()
    except OSError:
        pass
    return sorted(out)


def is_bridge_usb_port(p):
    """True for serial ports that look like an ESP32-S3 (native USB)."""
    return getattr(p, "vid", None) == ESPRESSIF_USB_VID


def open_link(target, password=None):
    if is_network_target(target):
        return HttpLink(target, password=password)
    return SerialLink(target)


def registers_from_memory(mem):
    """Raw register blocks stored by the bridge ({"0000": "0005", "400A": "0FAC..."})
    -> {address: value} exactly like m18.M18.read_id(output="array") returns them,
    so readings taken offline decode through the same code as live ones."""
    byte = {}
    for start, hexdata in (mem or {}).items():
        a = int(start, 16)
        for i, b in enumerate(bytes.fromhex(hexdata)):
            byte[a + i] = b
    out = {}
    for addr, length, dtype, _label in m18.data_id:
        if not all((addr + i) in byte for i in range(length)):
            out[addr] = None
            continue
        data = bytes(byte[addr + i] for i in range(length))
        if dtype == "uint":
            v = int.from_bytes(data, "big")
        elif dtype == "date":
            v = datetime.datetime.fromtimestamp(int.from_bytes(data, "big"), tz=datetime.timezone.utc)
        elif dtype == "hhmmss":
            mm, ss = divmod(int.from_bytes(data, "big"), 60)
            hh, mm = divmod(mm, 60)
            v = f"{hh}:{mm:02d}:{ss:02d}"
        elif dtype == "ascii":
            v = '"' + data.decode("utf-8", "replace") + '"'
        elif dtype == "sn":
            v = f"Type: {int.from_bytes(data[0:2], 'big'):3d}, Serial: {int.from_bytes(data[2:5], 'big'):d}"
        elif dtype == "adc_t":
            v = m18.M18.calculate_temperature(None, int.from_bytes(data, "big"))
        elif dtype == "dec_t":
            v = f"{data[0] + data[1] / 256:.2f}"
        elif dtype == "cell_v":
            v = [int.from_bytes(data[i:i + 2], "big") for i in range(0, 10, 2)]
        else:
            v = int.from_bytes(data, "big")
        out[addr] = v
    return out


class M18Bridge(m18.M18):
    """m18.M18 that sends its battery commands through the ESP32 bridge."""

    def __init__(self, target, password=None, sync_time=True):
        self.link = open_link(target, password)
        self.port = self.link          # callers use m.port.close()
        self.target = self.link.device
        self.idle()
        if sync_time:                  # offline bridges get the PC's clock for their records
            try:
                self._one(f"TIME {int(time.time())}")
            except BridgeError:
                pass

    def _one(self, line):
        return self.link.exec([line])[0]

    # -- primitives used by read_id(), health(), write_message(), read_all() ----
    def reset(self):
        self.ACC = 4
        r = self._one("RESET")
        if self.PRINT_RX:
            print(f"Bridge: {r}")
        return r == "OK ACK"

    def idle(self):
        self._one("IDLE")

    def high(self):
        self._one("HIGH")

    def cmd(self, a, b, c, length, command=0x01):
        if command != 0x01:
            raise NotImplementedError("Über die ESP32-Brücke sind nur Lesebefehle möglich")
        r = self._one(f"READ 0x{a:02X}{b:02X} {c}")
        if self.PRINT_TX:
            print(f"Bridge: READ 0x{a:02X}{b:02X} {c} -> {r}")
        if not r.startswith("OK "):
            return bytearray()      # m18.py treats this as "------" (no answer)
        return bytearray.fromhex(r[3:])

    def wcmd(self, a, b, c, length):
        r = self._one(f"NOTE 0x{a:02X}{b:02X} {c}")
        if not r.startswith("OK"):
            raise BridgeError(r)
        return bytearray.fromhex(r[3:]) if len(r) > 3 else bytearray()

    def info(self):
        return self._one("INFO")

    def scan(self):
        """Let the bridge read and decode the battery itself; returns a dict."""
        r = self.link.exec(["SCAN"], timeout=30)[0]
        return json.loads(r[r.index("{"):]) if "{" in r else {"ok": False, "error": r}

    # -- measurement history stored on the bridge ---------------------------
    def _json(self, line, timeout=None):
        r = self.link.exec([line], timeout=timeout)[0]
        if not r.startswith("OK "):
            raise BridgeError(r)
        return json.loads(r[3:])

    def history_stats(self):
        return self._json("HSTAT")

    def history_list(self, new_only=True):
        """All record metas (oldest first)."""
        items, after = [], 0
        for _ in range(200):
            page = self._json(f"HLIST {after} 100 {'new' if new_only else 'all'}")
            items += page["items"]
            after = page["next"]
            if not after:
                break
        return items

    def history_get(self, rid):
        return self._json(f"HGET {int(rid)}", timeout=15)

    def history_ack(self, rid):
        r = self._one(f"HACK {int(rid)}")
        if not r.startswith("OK"):
            raise BridgeError(r)

    def boot_info(self):
        """{"boot":..., "uptime_ms":...} - lets the PC date records taken without a clock."""
        info = json.loads(self.info()[3:])
        return {"uptime_ms": int(info.get("uptime_s", 0)) * 1000, "boot": info.get("boot")}

    # -- not available through the bridge -----------------------------------
    def send(self, command):
        raise NotImplementedError("Über die ESP32-Brücke nicht verfügbar")

    def read_response(self, size):
        raise NotImplementedError("Über die ESP32-Brücke nicht verfügbar")


if __name__ == "__main__":
    import argparse
    import code

    ap = argparse.ArgumentParser(description="M18 über ESP32-Brücke")
    ap.add_argument("--discover", action="store_true", help="Brücken im WLAN suchen und beenden")
    ap.add_argument("--bridge",
                    help="serieller Port (z. B. COM7, /dev/ttyUSB0) oder Netzwerkadresse "
                         "(m18-bridge.local, 192.168.1.50, http://...)")
    ap.add_argument("--health", action="store_true", help="Health-Report ausgeben und beenden")
    ap.add_argument("--password", help="Passwort der Brücke (falls gesetzt)")
    ap.add_argument("--sync", action="store_true",
                    help="auf der Brücke gespeicherte Messungen in die Datenbank übernehmen")
    ap.add_argument("--db", help="Datenbank für --sync (Standard: data/m18_fleet.db)")
    args = ap.parse_args()
    if args.discover or not args.bridge:
        found = discover()
        for b in found:
            print(f"{b.get('host', '?'):28} {b['ip']:15} Firmware {b['version']}")
        if not found:
            print("Keine M18-Brücke im Netzwerk gefunden.")
        raise SystemExit(0)
    m = M18Bridge(args.bridge, password=args.password)
    if args.sync:
        import os
        import sys
        here = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, os.path.join(here, "gui"))
        from bridge_import import import_from_bridge
        from fleet import FleetDB
        db = FleetDB(args.db or os.path.join(here, "data", "m18_fleet.db"))
        res = import_from_bridge(db, m)
        print(f"{res['imported']} übernommen, {res['created']} neue Akkus, {res['duplicates']} schon vorhanden, "
              f"{len(res['errors'])} Fehler")
        for e in res["errors"]:
            print("  Fehler:", e)
        raise SystemExit(0)
    print(f"Verbunden mit M18-Brücke {m.target}: {m.info()}")
    if args.health:
        m.health()
        m.idle()
    else:
        code.interact(banner="M18-Brücke: z. B. m.health(), m.read_id(), m.write_message('A-07')",
                      local={"m": m, "M18Bridge": M18Bridge})
