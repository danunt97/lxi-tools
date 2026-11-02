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

import json
import socket
import time
import urllib.error
import urllib.request

import m18


class BridgeError(Exception):
    pass


def is_network_target(target):
    t = target.strip().lower()
    return t.startswith(("http://", "https://")) or t.endswith(".local") or \
        (t.count(".") == 3 and t.replace(".", "").replace(":", "").isdigit())


class SerialLink:
    """ESP32 connected by USB: one text command per line, one reply per line."""

    def __init__(self, device, timeout=6.0):
        import serial  # pyserial
        self.device = device
        self.timeout = timeout
        s = serial.Serial()
        s.port = device
        s.baudrate = 115200
        s.timeout = 0.2
        # Most ESP32 dev boards reset when DTR/RTS toggle; keep them released.
        try:
            s.dtr = False
            s.rts = False
        except (OSError, ValueError, serial.SerialException):
            pass
        s.open()
        self.ser = s
        self._wait_ready()

    def _wait_ready(self):
        # The board may have just rebooted: retry PING for a few seconds.
        deadline = time.time() + 4.0
        last = None
        while time.time() < deadline:
            try:
                last = self.exec(["PING"], timeout=0.8)[0]
                if last.startswith("OK M18BRIDGE"):
                    return
            except BridgeError as e:
                last = str(e)
        raise BridgeError(f"Keine M18-Brücke an {self.device} (Antwort: {last!r})")

    def exec(self, lines, timeout=None):
        timeout = timeout or self.timeout
        self.ser.reset_input_buffer()
        replies = []
        for line in lines:
            self.ser.write((line.strip() + "\n").encode("ascii"))
            self.ser.flush()
            deadline = time.time() + timeout
            while True:
                raw = self.ser.readline().decode("utf-8", "replace").strip()
                if raw.startswith(("OK", "ERR")):
                    replies.append(raw)
                    break
                if time.time() > deadline:
                    raise BridgeError(f"Zeitüberschreitung bei {line!r}")
        return replies

    def close(self):
        self.ser.close()


class HttpLink:
    """ESP32 on the network: POST /cmd with one command per line."""

    def __init__(self, target, timeout=10.0):
        t = target.strip().rstrip("/")
        if not t.startswith(("http://", "https://")):
            t = "http://" + t
        self.base = t
        self.device = t
        self.timeout = timeout
        r = self.exec(["PING"])[0]
        if not r.startswith("OK M18BRIDGE"):
            raise BridgeError(f"{t} ist keine M18-Brücke (Antwort: {r!r})")

    def exec(self, lines, timeout=None):
        body = ("\n".join(line.strip() for line in lines) + "\n").encode("ascii")
        req = urllib.request.Request(self.base + "/cmd", data=body, method="POST",
                                     headers={"Content-Type": "text/plain"})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                out = resp.read().decode("utf-8", "replace").splitlines()
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


def open_link(target):
    return HttpLink(target) if is_network_target(target) else SerialLink(target)


class M18Bridge(m18.M18):
    """m18.M18 that sends its battery commands through the ESP32 bridge."""

    def __init__(self, target):
        self.link = open_link(target)
        self.port = self.link          # callers use m.port.close()
        self.target = self.link.device
        self.idle()

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
    args = ap.parse_args()
    if args.discover or not args.bridge:
        found = discover()
        for b in found:
            print(f"{b.get('host', '?'):28} {b['ip']:15} Firmware {b['version']}")
        if not found:
            print("Keine M18-Brücke im Netzwerk gefunden.")
        raise SystemExit(0)
    m = M18Bridge(args.bridge)
    print(f"Verbunden mit M18-Brücke {m.target}: {m.info()}")
    if args.health:
        m.health()
        m.idle()
    else:
        code.interact(banner="M18-Brücke: z. B. m.health(), m.read_id(), m.write_message('A-07')",
                      local={"m": m, "M18Bridge": M18Bridge})
