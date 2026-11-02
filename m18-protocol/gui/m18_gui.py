#!/usr/bin/env python3
"""
M18 Battery GUI - a small local web interface for reading and graphically
displaying Milwaukee M18 battery diagnostics.

It is a thin wrapper around the m18.py protocol implementation in the parent
folder. The heavy lifting (serial framing, register decoding) stays in m18.py;
this file only connects, pulls structured values via read_id(output="array"),
and serves them to a browser UI.

Usage:
    python3 gui/m18_gui.py                 # real mode, pick the port in the UI
    python3 gui/m18_gui.py --port /dev/ttyUSB0
    python3 gui/m18_gui.py --demo          # sample data, no hardware needed
    python3 gui/m18_gui.py --no-browser    # don't auto-open the browser

Then open http://127.0.0.1:8018 (opened automatically unless --no-browser).

Real mode needs the same dependencies as m18.py (pyserial, requests).
Demo mode needs nothing beyond the Python standard library.
"""

import argparse
import contextlib
import io
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
STATIC = os.path.join(HERE, "static")

# Register addresses we surface in the UI. Mapped by address so we never
# depend on the order of m18.data_id.
ADDR_CELL_TYPE   = 0x0000
ADDR_SERIAL      = 0x0004
ADDR_MANUFACTURE = 0x0011
ADDR_FIRST_CHG   = 0x0015
ADDR_LAST_CHG    = 0x0019
ADDR_NOTE        = 0x0023
ADDR_CELLS       = 0x400A
ADDR_TEMP_ADC    = 0x4014
ADDR_TEMP_DEC    = 0x401F
ADDR_DAYS        = 0x9010
ADDR_DISCHARGE_AS = 0x9012
ADDR_DISCHARGE_WS = 0x9016
ADDR_TOTAL_CHG   = 0x901A
ADDR_DUMB_CHG    = 0x901E
ADDR_REDLINK_CHG = 0x9020
ADDR_OVERHEAT    = 0x9032
ADDR_LOWVOLT     = 0x9036


class Backend:
    """Holds the serial connection (if any) and exposes read operations."""

    def __init__(self, demo=False):
        self.demo = demo
        self.m = None            # m18.M18 instance in real mode
        self.m18 = None          # the m18 module (lazy import)
        self.port = None
        self.lock = threading.Lock()
        self.log_lines = []

    # -- logging ---------------------------------------------------------
    def log(self, msg):
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_lines.append(f"{ts} {msg}")
        self.log_lines = self.log_lines[-200:]

    # -- ports -----------------------------------------------------------
    def list_ports(self):
        if self.demo:
            return [{"device": "DEMO", "desc": "Demo mode (no hardware)"}]
        try:
            from serial.tools import list_ports
            out = []
            for p in list_ports.comports():
                out.append({"device": p.device,
                            "desc": f"{p.manufacturer or ''} {p.description or ''}".strip()})
            return out
        except Exception as e:
            self.log(f"port scan failed: {e}")
            return []

    # -- connect ---------------------------------------------------------
    def connect(self, port):
        if self.demo:
            self.port = port or "DEMO"
            self.log("connected (demo mode)")
            return {"ok": True, "demo": True}
        with self.lock:
            try:
                if self.m18 is None:
                    sys.path.insert(0, PARENT)
                    import m18
                    self.m18 = m18
                # Close any previous handle
                if self.m is not None:
                    with contextlib.suppress(Exception):
                        self.m.port.close()
                self.m = self.m18.M18(port)
                self.port = port
                ok = self.m.reset()
                self.log(f"connect {port} -> reset {'ACK' if ok else 'no ACK'}")
                return {"ok": True, "ack": bool(ok)}
            except Exception as e:
                self.log(f"connect failed: {e}")
                return {"ok": False, "error": str(e)}

    def disconnect(self):
        if self.m is not None:
            with contextlib.suppress(Exception):
                self.m.idle()
                self.m.port.close()
        self.m = None
        self.port = None
        self.log("disconnected")
        return {"ok": True}

    # -- read ------------------------------------------------------------
    def read(self):
        if self.demo:
            return _DEMO_SNAPSHOT
        if self.m is None:
            return {"ok": False, "error": "not connected"}
        with self.lock:
            try:
                arr = self.m.read_id(output="array")
                by_addr = {}
                for idx, val in (arr or []):
                    addr = self.m18.data_id[idx][0]
                    by_addr[addr] = val
                snap = self._decode(by_addr)
                self.log(f"read -> {len(arr or [])} registers, "
                         f"pack {snap['pack_v']:.1f} V")
                return snap
            except Exception as e:
                self.log(f"read failed: {e}")
                return {"ok": False, "error": str(e)}

    def _decode(self, a):
        def g(addr, default=None):
            return a.get(addr, default)

        cells = g(ADDR_CELLS) or []
        cells = [c for c in cells if isinstance(c, int)]
        cell_v = [round(c / 1000.0, 3) for c in cells]
        pack_v = round(sum(cell_v), 2)

        temp = g(ADDR_TEMP_DEC)
        if temp is None:
            temp = g(ADDR_TEMP_ADC)
        try:
            temp = float(temp)
        except (TypeError, ValueError):
            temp = None

        def as_int(addr):
            v = g(addr)
            return v if isinstance(v, int) else None

        # Health heuristic: cell imbalance + how full the pack is (3.0-4.2 V).
        health = None
        if cell_v:
            lo, hi = min(cell_v), max(cell_v)
            imbalance = round((hi - lo) * 1000)  # mV
            soc = max(0.0, min(1.0, (sum(cell_v) / len(cell_v) - 3.0) / 1.2))
            health = max(0, round(100 - imbalance * 0.5))
        else:
            imbalance = None
            soc = None

        return {
            "ok": True,
            "cell_type": as_int(ADDR_CELL_TYPE),
            "serial": _s(g(ADDR_SERIAL)),
            "manufactured": _s(g(ADDR_MANUFACTURE)),
            "first_charge": _s(g(ADDR_FIRST_CHG)),
            "last_charge": _s(g(ADDR_LAST_CHG)),
            "note": _s(g(ADDR_NOTE)).strip('"'),
            "cells": cell_v,
            "pack_v": pack_v,
            "temp": temp,
            "imbalance_mv": imbalance,
            "health": health,
            "counters": {
                "total_charges": as_int(ADDR_TOTAL_CHG),
                "redlink_charges": as_int(ADDR_REDLINK_CHG),
                "dumb_charges": as_int(ADDR_DUMB_CHG),
                "discharge_as": as_int(ADDR_DISCHARGE_AS),
                "discharge_ws": as_int(ADDR_DISCHARGE_WS),
                "overheat": as_int(ADDR_OVERHEAT),
                "lowvolt": as_int(ADDR_LOWVOLT),
                "days": as_int(ADDR_DAYS),
            },
        }

    # -- actions ---------------------------------------------------------
    def idle(self):
        if self.demo:
            self.log("idle (demo)")
            return {"ok": True}
        if self.m is None:
            return {"ok": False, "error": "not connected"}
        with self.lock:
            with contextlib.suppress(Exception):
                self.m.idle()
            self.log("TX set low (idle)")
            return {"ok": True}

    def write_note(self, text):
        if self.demo:
            self.log(f"write_note (demo): {text!r}")
            return {"ok": True}
        if self.m is None:
            return {"ok": False, "error": "not connected"}
        with self.lock:
            try:
                self.m.write_message(text[:20])
                self.log(f"wrote note: {text[:20]!r}")
                return {"ok": True}
            except Exception as e:
                return {"ok": False, "error": str(e)}

    def raw_text(self):
        """Capture the spreadsheet-style dump as plain text."""
        if self.demo:
            return {"ok": True, "text": _DEMO_RAW}
        if self.m is None:
            return {"ok": False, "error": "not connected"}
        with self.lock:
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    self.m.read_id(output="label")
                return {"ok": True, "text": buf.getvalue()}
            except Exception as e:
                return {"ok": False, "error": str(e), "text": buf.getvalue()}


def _s(v):
    """Stringify a decoded value (datetime, int, str, None) for JSON."""
    if v is None:
        return ""
    return str(v)


# --------------------------------------------------------------------------
# Demo data so the UI can be explored without a battery attached.
# --------------------------------------------------------------------------
_DEMO_SNAPSHOT = {
    "ok": True,
    "cell_type": 36,
    "serial": "Type:  36, Serial: 863604",
    "manufactured": "2022-03-14 00:00:00",
    "first_charge": "2022-04-02 00:00:00",
    "last_charge": "2026-10-28 00:00:00",
    "note": "drill-kit-2",
    "cells": [3.652, 3.648, 3.661, 3.655, 3.649],
    "pack_v": 18.27,
    "temp": 24.1,
    "imbalance_mv": 13,
    "health": 98,
    "counters": {
        "total_charges": 143, "redlink_charges": 131, "dumb_charges": 12,
        "discharge_as": 1750000, "discharge_ws": 486000,
        "overheat": 2, "lowvolt": 0, "days": 1324,
    },
}
_DEMO_RAW = ("2026-11-02 14:52:02\n"
             "ID  ADDR   LEN TYPE       LABEL                          VALUE\n"
             "  0 0x0000  2   uint   Cell type                        36\n"
             " 12 0x400A 10 cell_v   Cell voltages (mV)   1:3652 2:3648 ...\n"
             " 13 0x4014  2  adc_t   Temperature (C)                  24.1\n"
             " ... (connect a real battery to see every register)\n")


class Handler(BaseHTTPRequestHandler):
    backend = None  # set in main()

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass  # keep the console quiet

    def _static(self, path):
        if path == "/" or path == "":
            path = "/index.html"
        fp = os.path.normpath(os.path.join(STATIC, path.lstrip("/")))
        if not fp.startswith(STATIC) or not os.path.isfile(fp):
            return self._send(404, {"error": "not found"})
        ctype = {"html": "text/html", "css": "text/css",
                 "js": "application/javascript"}.get(fp.rsplit(".", 1)[-1],
                                                     "application/octet-stream")
        with open(fp, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_GET(self):
        b = self.backend
        if self.path == "/api/ports":
            return self._send(200, {"ports": b.list_ports(), "demo": b.demo})
        if self.path == "/api/read":
            return self._send(200, b.read())
        if self.path == "/api/raw":
            return self._send(200, b.raw_text())
        if self.path == "/api/log":
            return self._send(200, {"lines": b.log_lines[-60:]})
        if self.path == "/api/status":
            return self._send(200, {"connected": b.port is not None,
                                    "port": b.port, "demo": b.demo})
        return self._static(self.path)

    def do_POST(self):
        b = self.backend
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            data = {}
        if self.path == "/api/connect":
            return self._send(200, b.connect(data.get("port")))
        if self.path == "/api/disconnect":
            return self._send(200, b.disconnect())
        if self.path == "/api/idle":
            return self._send(200, b.idle())
        if self.path == "/api/note":
            return self._send(200, b.write_note(str(data.get("text", ""))))
        return self._send(404, {"error": "unknown endpoint"})


def main():
    ap = argparse.ArgumentParser(description="M18 Battery GUI")
    ap.add_argument("--port", help="serial port to connect on startup")
    ap.add_argument("--demo", action="store_true", help="use sample data, no hardware")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--http-port", type=int, default=8018)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    backend = Backend(demo=args.demo)
    if args.port:
        backend.connect(args.port)
    Handler.backend = backend

    httpd = ThreadingHTTPServer((args.host, args.http_port), Handler)
    url = f"http://{args.host}:{args.http_port}"
    mode = "DEMO" if args.demo else "live"
    print(f"M18 Battery GUI ({mode}) running at {url}")
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
        backend.disconnect()


if __name__ == "__main__":
    main()
