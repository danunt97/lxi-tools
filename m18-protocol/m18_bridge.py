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

In Python:
    from m18_bridge import M18Bridge
    m = M18Bridge("http://m18-bridge.local")
    m.health()
"""

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
                raw = self.ser.readline().decode("ascii", "replace").strip()
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
                out = resp.read().decode("ascii", "replace").splitlines()
        except (urllib.error.URLError, OSError) as e:
            raise BridgeError(f"M18-Brücke {self.base} nicht erreichbar: {e}") from e
        out = [o.strip() for o in out if o.strip()]
        if len(out) != len(lines):
            raise BridgeError(f"Unerwartete Antwort der Brücke: {out!r}")
        return out

    def close(self):
        pass


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

    # -- not available through the bridge -----------------------------------
    def send(self, command):
        raise NotImplementedError("Über die ESP32-Brücke nicht verfügbar")

    def read_response(self, size):
        raise NotImplementedError("Über die ESP32-Brücke nicht verfügbar")


if __name__ == "__main__":
    import argparse
    import code

    ap = argparse.ArgumentParser(description="M18 über ESP32-Brücke")
    ap.add_argument("--bridge", required=True,
                    help="serieller Port (z. B. COM7, /dev/ttyUSB0) oder Netzwerkadresse "
                         "(m18-bridge.local, 192.168.1.50, http://...)")
    ap.add_argument("--health", action="store_true", help="Health-Report ausgeben und beenden")
    args = ap.parse_args()
    m = M18Bridge(args.bridge)
    print(f"Verbunden mit M18-Brücke {m.target}: {m.info()}")
    if args.health:
        m.health()
        m.idle()
    else:
        code.interact(banner="M18-Brücke: z. B. m.health(), m.read_id(), m.write_message('A-07')",
                      local={"m": m, "M18Bridge": M18Bridge})
