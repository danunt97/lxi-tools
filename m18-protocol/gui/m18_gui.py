#!/usr/bin/env python3
"""
M18 Akku-Verwaltung - local web app to read Milwaukee M18 packs (via m18.py
and a 3.3 V USB-serial adapter), keep every read-out in a SQLite database,
track aging per pack, print QR labels and produce reports.

Usage:
    python gui/m18_gui.py                  # live mode, choose the port in the UI
    python gui/m18_gui.py --port COM5      # connect on startup
    python gui/m18_gui.py --demo           # simulated fleet, no hardware needed
    python gui/m18_gui.py --host 0.0.0.0   # reachable from phones on the LAN (QR scan)
    python gui/m18_gui.py --db pfad/zur.db # use another database file

Then open http://127.0.0.1:8018 (opened automatically unless --no-browser).

Live mode needs pyserial (pip install pyserial). Everything else, including
demo mode, uses only the Python standard library.
"""

import argparse
import contextlib
import csv
import datetime as _dt
import io
import json
import os
import re
import secrets
import socket
import sys
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
STATIC = os.path.join(HERE, "static")
sys.path.insert(0, HERE)
sys.path.insert(1, PARENT)  # m18.py, m18_bridge.py

import fleet  # noqa: E402
from fleet import FleetDB, iso, utcnow  # noqa: E402
from qrgen import QrCode  # noqa: E402

DEFAULT_DB = os.path.join(PARENT, "data", "m18_fleet.db")


# --------------------------------------------------------------------------
# Register decoding (live mode)
# --------------------------------------------------------------------------
def _date(v):
    """datetime from m18.py -> ISO string, ignoring unset (epoch-ish) dates."""
    if isinstance(v, _dt.datetime):
        if v.year < 2005:
            return None
        return iso(v if v.tzinfo else v.replace(tzinfo=_dt.timezone.utc))
    return None


def _int(v):
    return v if isinstance(v, int) else None


def _hhmmss(v):
    if not isinstance(v, str) or ":" not in v:
        return None
    try:
        h, m, s = (int(x) for x in v.split(":"))
        return h * 3600 + m * 60 + s
    except ValueError:
        return None


def decode_registers(by_addr):
    """Turn {address: value} from M18.read_id(output='array') into a snapshot."""
    g = by_addr.get
    sn = str(g(0x0004) or "")
    nums = re.findall(r"\d+", sn)
    type_code = int(nums[0]) if len(nums) >= 1 else None
    serial_num = int(nums[1]) if len(nums) >= 2 else None
    eserial = f"{type_code}-{serial_num}" if serial_num is not None else None

    cells_mv = [c for c in (g(0x400A) or []) if isinstance(c, int)]
    cells = [round(c / 1000.0, 3) for c in cells_mv]
    imbalance = (max(cells_mv) - min(cells_mv)) if cells_mv else None

    # Forge packs report 0x401F, others 0x4014; the unused one reads as 0.
    temp = None
    for addr in (0x401F, 0x4014):
        with contextlib.suppress(TypeError, ValueError):
            v = float(g(addr))
            if v != 0.0:
                temp = round(v, 1)
                break

    tool_time = 0
    have_hist = False
    for addr in range(0x903A, 0x9062, 2):  # discharge histogram 10A .. 200A+
        if isinstance(g(addr), int):
            tool_time += g(addr)
            have_hist = True

    note = str(g(0x0023) or "").strip('"').rstrip("-").strip()
    return {
        "ok": eserial is not None,
        "eserial": eserial,
        "type_code": type_code,
        "serial_num": serial_num,
        "serial": sn,
        "cell_type": _int(g(0x0000)),
        "manufactured": _date(g(0x0011)),
        "first_charge": _date(g(0x0015)) or _date(g(0x9000)),
        "last_charge": _date(g(0x0019)) or _date(g(0x9008)),
        "last_tool_use": _date(g(0x9004)),
        "note": note,
        "cells": cells,
        "pack_v": round(sum(cells), 2) if cells else None,
        "temp": temp,
        "imbalance_mv": imbalance,
        "counters": {
            "total_charges": _int(g(0x901A)),
            "redlink_charges": _int(g(0x9020)),
            "dumb_charges": _int(g(0x901E)),
            "discharge_as": _int(g(0x9012)),
            "discharge_ws": _int(g(0x9016)),
            "overheat": _int(g(0x9032)),
            "overcurrent": _int(g(0x9034)),
            "lowvolt": _int(g(0x9036)),
            "lowvolt_bounce": _int(g(0x9038)),
            "discharge_to_empty": _int(g(0x9030)),
            "lowv_charges": _int(g(0x902E)),
            "days": _int(g(0x9010)),
            "charge_time_s": _hhmmss(g(0x9024)),
            "tool_time_s": tool_time if have_hist else None,
        },
        "raw": {f"0x{a:04X}": (_date(v) if isinstance(v, _dt.datetime) else v)
                for a, v in by_addr.items()},
    }


# --------------------------------------------------------------------------
# Hardware access
# --------------------------------------------------------------------------
class Hardware:
    """Serial connection to the pack via m18.py (live mode)."""

    def __init__(self, log):
        self.log = log
        self.m = None
        self.m18 = None
        self.port = None
        self.lock = threading.Lock()

    def list_ports(self):
        try:
            from serial.tools import list_ports
        except ImportError:
            self.log("pyserial fehlt – bitte 'pip install pyserial' ausführen")
            return []
        import m18_bridge
        return [{"device": p.device,
                 "desc": f"{p.manufacturer or ''} {p.description or ''}".strip(),
                 "esp": m18_bridge.is_bridge_usb_port(p)}
                for p in list_ports.comports()]

    def connect(self, port):
        if not port:
            return {"ok": False, "error": "Kein Port gewählt"}
        with self.lock:
            try:
                if self.m18 is None:
                    sys.path.insert(0, PARENT)
                    import m18
                    self.m18 = m18
                if self.m is not None:
                    with contextlib.suppress(Exception):
                        self.m.port.close()
                import m18_bridge
                if port.startswith("esp:") or m18_bridge.is_network_target(port):
                    # ESP32 bridge on USB ("esp:COM7") or Wi-Fi ("m18-bridge.local")
                    self.m = m18_bridge.M18Bridge(port[4:] if port.startswith("esp:") else port)
                else:
                    if self.m18.Serial is None:
                        raise RuntimeError("pyserial fehlt – bitte 'pip install pyserial' ausführen")
                    self.m = self.m18.M18(port)
                self.port = port
                ok = self.m.reset()
                self.m.idle()
                self.log(f"Verbunden mit {port} – Reset {'ACK' if ok else 'ohne ACK'}")
                return {"ok": True, "ack": bool(ok)}
            except Exception as e:
                self.m = None
                self.port = None
                self.log(f"Verbinden fehlgeschlagen: {e}")
                return {"ok": False, "error": str(e)}

    def disconnect(self):
        with self.lock:
            if self.m is not None:
                with contextlib.suppress(Exception):
                    self.m.idle()
                    self.m.port.close()
            self.m = None
            self.port = None
        self.log("Getrennt")
        return {"ok": True}

    def read(self):
        if self.m is None:
            return {"ok": False, "error": "Nicht verbunden"}
        with self.lock:
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    arr = self.m.read_id(output="array")
                with contextlib.suppress(Exception):
                    self.m.idle()
                if not arr:
                    msg = buf.getvalue().strip().splitlines()
                    raise RuntimeError(msg[-1] if msg else "keine Antwort vom Akku")
                by_addr = {self.m18.data_id[idx][0]: val for idx, val in arr}
                snap = decode_registers(by_addr)
                if not snap["ok"]:
                    raise RuntimeError("Seriennummer nicht lesbar – Akku richtig angeschlossen?")
                answered = sum(1 for _, v in arr if v is not None)
                self.log(f"Ausgelesen: {snap['eserial']}, {answered}/{len(arr)} Register, "
                         f"{snap['pack_v']} V")
                return snap
            except Exception as e:
                with contextlib.suppress(Exception):
                    self.m.idle()
                self.log(f"Auslesen fehlgeschlagen: {e}")
                return {"ok": False, "error": str(e)}

    def write_note(self, text):
        if self.m is None:
            return {"ok": False, "error": "Nicht verbunden"}
        with self.lock:
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.m.write_message(text[:20])
                    self.m.idle()
                self.log(f"Notiz geschrieben: {text[:20]!r}")
                return {"ok": True}
            except Exception as e:
                return {"ok": False, "error": str(e)}

    def idle(self):
        if self.m is None:
            return {"ok": False, "error": "Nicht verbunden"}
        with self.lock:
            with contextlib.suppress(Exception):
                self.m.idle()
        self.log("TX auf Low (Idle)")
        return {"ok": True}


class App:
    def __init__(self, db, demo=None, public_host="127.0.0.1"):
        self.db = db
        self.demo = demo
        self.public_host = public_host
        self.log_lines = []
        self.hw = None if demo else Hardware(self.log)
        self.demo_connected = False
        self.pending = {}  # token -> snapshot read but not yet saved
        self.pending_lock = threading.Lock()

    def log(self, msg):
        ts = _dt.datetime.now().strftime("%H:%M:%S")
        self.log_lines.append(f"{ts} {msg}")
        self.log_lines = self.log_lines[-200:]

    # status / connection -------------------------------------------------
    def status(self):
        if self.demo:
            return {"connected": self.demo_connected, "port": "DEMO" if self.demo_connected else None,
                    "demo": True, "plugged": self.demo.plugged}
        return {"connected": self.hw.port is not None, "port": self.hw.port, "demo": False}

    def ports(self):
        if self.demo:
            return [{"device": "DEMO", "desc": "Demo-Adapter (simuliert)"}]
        return self.hw.list_ports()

    def connect(self, port):
        if self.demo:
            self.demo_connected = True
            self.log("Verbunden (Demo)")
            return {"ok": True}
        return self.hw.connect(port)

    def disconnect(self):
        if self.demo:
            self.demo_connected = False
            self.log("Getrennt (Demo)")
            return {"ok": True}
        return self.hw.disconnect()

    # reading -------------------------------------------------------------
    def live_read(self):
        if self.demo:
            if not self.demo_connected:
                return {"ok": False, "error": "Nicht verbunden"}
            snap = self.demo.read()
            self.log(f"Ausgelesen (Demo): {snap['eserial']}, {snap['pack_v']} V")
        else:
            snap = self.hw.read()
        if not snap.get("ok"):
            return snap
        bat = self.db.get_battery(snap["eserial"])
        token = secrets.token_hex(8)
        with self.pending_lock:
            self.pending = {token: (snap, utcnow())}  # only the latest read is saveable
        out = {k: v for k, v in snap.items() if k != "raw"}
        out["token"] = token
        out["battery"] = bat
        out["model"] = fleet.battery_type(snap.get("type_code"))[1]
        out["capacity_ah"] = fleet.battery_type(snap.get("type_code"))[0]
        out["preview"] = self.db.preview(snap, bat)
        return out

    def save_pending(self, token, fields):
        with self.pending_lock:
            item = self.pending.pop(token, None)
        if not item:
            return {"ok": False, "error": "Messung nicht mehr verfügbar – bitte erneut auslesen"}
        snap, ts = item
        bat = self.db.get_battery(snap["eserial"])
        created = False
        if bat is None:
            bat = self.db.create_battery(snap, fields or {})
            created = True
        elif fields:
            bat = self.db.update_battery(bat["id"], fields)
        reading, alerts = self.db.add_reading(bat["id"], snap, ts=ts,
                                              source="demo" if self.demo else "live")
        self.log(f"Gespeichert: {bat['name'] or bat['eserial']} – Zustand {reading['health']}"
                 + (f", {len(alerts)} Warnung(en)" if alerts else ""))
        return {"ok": True, "battery": bat, "created": created, "reading": reading,
                "alerts": alerts}

    def write_note(self, text):
        if self.demo:
            self.demo.write_note(text)
            self.log(f"Notiz geschrieben (Demo): {text[:20]!r}")
            return {"ok": True}
        return self.hw.write_note(text)

    def idle(self):
        if self.demo:
            self.log("Idle (Demo)")
            return {"ok": True}
        return self.hw.idle()

    def disconnect_on_exit(self):
        if self.hw:
            self.hw.disconnect()

    # settings ------------------------------------------------------------
    def base_url(self, req_host=None):
        url = (self.db.get_setting("base_url") or "").strip().rstrip("/")
        if url:
            return url
        return f"http://{req_host}" if req_host else "http://127.0.0.1:8018"

    def settings(self, req_host):
        return {
            "base_url": self.db.get_setting("base_url") or "",
            "effective_base_url": self.base_url(req_host),
            "suggested_base_url": f"http://{lan_ip()}:{req_host.rsplit(':', 1)[-1]}"
            if req_host and ":" in req_host else None,
            "listen_host": self.public_host,
            "company": self.db.get_setting("company") or "",
            "db_path": self.db.path,
            "demo": bool(self.demo),
            "thresholds": fleet.THRESHOLDS,
        }


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------
READING_FIELDS = [
    ("ts", "Zeitpunkt (UTC)"), ("health", "Zustand %"), ("pack_v", "Pack V"),
    ("temp_c", "Temp °C"), ("imbalance_mv", "Imbalance mV"),
]
COUNTER_LABELS = [
    ("total_charges", "Ladungen gesamt"), ("redlink_charges", "Redlink-Ladungen"),
    ("dumb_charges", "Einfache Ladungen"), ("discharge_ah", "Entladung Ah"),
    ("discharge_wh", "Entladung Wh"), ("efc", "Äquiv. Vollzyklen"),
    ("overheat", "Überhitzung"), ("overcurrent", "Überstrom"), ("lowvolt", "Unterspannung"),
    ("lowv_charges", "Ladung Zelle<2,5V"), ("discharge_to_empty", "Leer gefahren"),
    ("days", "Tage seit Erstladung"),
]


def _counter_values(r, cap):
    c = r.get("counters") or {}
    das, dws = c.get("discharge_as"), c.get("discharge_ws")
    v = dict(c)
    v["discharge_ah"] = round(das / 3600, 1) if das is not None else None
    v["discharge_wh"] = round(dws / 3600, 1) if dws is not None else None
    v["efc"] = round(das / 3600 / cap, 1) if das is not None and cap else None
    return v


def _fmt(v, excel_de):
    if v is None:
        return ""
    if excel_de and isinstance(v, float):
        return str(v).replace(".", ",")
    return v


def csv_battery(db, b, excel_de):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";" if excel_de else ",")
    rs = db.readings(b["id"])
    ncells = max([len(r["cells"]) for r in rs] + [5])
    w.writerow(["E-Seriennr.", "Name"] + [h for _, h in READING_FIELDS]
               + [f"Zelle {i + 1} V" for i in range(ncells)] + [h for _, h in COUNTER_LABELS]
               + ["Quelle"])
    for r in rs:
        cv = _counter_values(r, b["capacity_ah"])
        row = [b["eserial"], b["name"]] + [r.get(k) for k, _ in READING_FIELDS]
        row += [(r["cells"][i] if i < len(r["cells"]) else None) for i in range(ncells)]
        row += [cv.get(k) for k, _ in COUNTER_LABELS] + [r["source"]]
        w.writerow([_fmt(x, excel_de) for x in row])
    return buf.getvalue()


def csv_fleet(db, excel_de):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";" if excel_de else ",")
    w.writerow(["E-Seriennr.", "Name", "Modell", "Nennkapazität Ah", "Gehäuse-Seriennr.",
                "Standort", "Kommentar", "Status", "Ausgemustert", "Messungen",
                "Letzte Messung (UTC)", "Tage seit Messung", "Trend %/Jahr", "Prognose < 60 %",
                "Offene Warnungen"] + [h for _, h in READING_FIELDS[1:]]
              + [h for _, h in COUNTER_LABELS])
    for b in db.list_batteries():
        lr = b["latest"] or {}
        cv = _counter_values(lr, b["capacity_ah"]) if lr else {}
        t = b["trend"] or {}
        row = [b["eserial"], b["name"], b["model"], b["capacity_ah"], b["case_serial"],
               b["location"], b["comment"], b["status"], "ja" if b["retired"] else "",
               len(b["health_series"]), lr.get("ts"), b["days_since_reading"],
               t.get("slope_per_year"), (t.get("projected_date") or "")[:10],
               sum(b["alerts_open"].values())]
        row += [lr.get(k) for k, _ in READING_FIELDS[1:]] + [cv.get(k) for k, _ in COUNTER_LABELS]
        w.writerow([_fmt(x, excel_de) for x in row])
    return buf.getvalue()


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
PAGES = {"label": "label.html", "report": "report.html"}
CTYPES = {"html": "text/html; charset=utf-8", "css": "text/css; charset=utf-8",
          "js": "application/javascript; charset=utf-8", "svg": "image/svg+xml",
          "png": "image/png", "ico": "image/x-icon", "json": "application/json"}


class Handler(BaseHTTPRequestHandler):
    app = None  # set in main()
    server_version = "M18Fleet/2"

    # helpers --------------------------------------------------------------
    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, default=str, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _download(self, data, filename, ctype):
        if isinstance(data, str):
            data = data.encode("utf-8-sig")
        self._send(200, data, ctype, {
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})

    def _json_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw or b"{}")
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def log_message(self, *a):
        pass

    def _file(self, name):
        fp = os.path.normpath(os.path.join(STATIC, name))
        if not fp.startswith(STATIC + os.sep) or not os.path.isfile(fp):
            return self._send(404, {"error": "not found"})
        with open(fp, "rb") as f:
            self._send(200, f.read(), CTYPES.get(fp.rsplit(".", 1)[-1], "application/octet-stream"))

    def _battery_or_404(self, ident):
        b = self.app.db.get_battery(unquote(ident))
        if not b:
            self._send(404, {"ok": False, "error": "Akku nicht gefunden"})
        return b

    # routing --------------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        app, db = self.app, self.app.db
        url = urlparse(self.path)
        path, qs = url.path, parse_qs(url.query)
        parts = [p for p in path.split("/") if p]
        host = self.headers.get("Host")
        try:
            if path.startswith("/static/"):
                return self._file(path[len("/static/"):])
            if path == "/favicon.ico":
                return self._file("favicon.svg")
            if not parts or parts[0] in ("fleet", "b", "read", "alerts", "settings"):
                return self._file("index.html")
            if parts[0] in PAGES:
                return self._file(PAGES[parts[0]])

            if parts[0] == "qr" and len(parts) == 2:
                name = unquote(parts[1])
                ident, ext = (name.rsplit(".", 1) + ["svg"])[:2]
                b = self._battery_or_404(ident)
                if not b:
                    return
                qr = QrCode.encode(f"{app.base_url(host)}/b/{b['eserial']}", ecl="M")
                if ext == "png":
                    scale = max(2, min(40, int(qs.get("scale", ["10"])[0])))
                    border = max(0, min(8, int(qs.get("border", ["2"])[0])))
                    return self._send(200, qr.to_png(scale=scale, border=border), "image/png")
                return self._send(200, qr.to_svg(border=max(0, min(8, int(qs.get("border", ["4"])[0])))),
                                  "image/svg+xml")

            if parts[0] == "export":
                excel = qs.get("format", ["excel"])[0] != "intl"
                stamp = _dt.date.today().isoformat()
                if parts[1:] == ["fleet.csv"]:
                    return self._download(csv_fleet(db, excel), f"m18-flotte-{stamp}.csv",
                                          "text/csv; charset=utf-8")
                if len(parts) == 3 and parts[1] == "battery":
                    b = self._battery_or_404(parts[2].removesuffix(".csv"))
                    if b:
                        return self._download(csv_battery(db, b, excel),
                                              f"m18-{b['eserial']}-{stamp}.csv",
                                              "text/csv; charset=utf-8")
                    return
                if parts[1:] == ["database"]:
                    return self._download(db.backup_bytes(), f"m18-flotte-{stamp}.db",
                                          "application/x-sqlite3")

            if parts[0] != "api":
                return self._send(404, {"error": "not found"})
            r = parts[1:]
            if r == ["status"]:
                return self._send(200, app.status())
            if r == ["ports"]:
                return self._send(200, {"ports": app.ports(), "demo": bool(app.demo)})
            if r == ["log"]:
                return self._send(200, {"lines": app.log_lines[-80:]})
            if r == ["settings"]:
                return self._send(200, app.settings(host))
            if r == ["batteries"]:
                return self._send(200, {"batteries": db.list_batteries(),
                                        "locations": db.locations()})
            if len(r) == 2 and r[0] == "batteries":
                d = db.detail(unquote(r[1]))
                if not d:
                    return self._send(404, {"ok": False, "error": "Akku nicht gefunden"})
                d["qr_url"] = f"{app.base_url(host)}/b/{d['eserial']}"
                d["company"] = db.get_setting("company") or ""
                return self._send(200, d)
            if r == ["alerts"]:
                open_only = qs.get("open", ["0"])[0] == "1"
                return self._send(200, {"alerts": db.alerts(open_only=open_only)})
            if r == ["resolve"]:
                b = db.resolve(qs.get("q", [""])[0])
                return self._send(200, {"battery": b})
            if r == ["discover"]:
                if app.demo:
                    return self._send(200, {"bridges": []})
                import m18_bridge
                return self._send(200, {"bridges": m18_bridge.discover()})
            if r == ["demo"]:
                if not app.demo:
                    return self._send(200, {"demo": False})
                return self._send(200, {"demo": True, "options": app.demo.options(),
                                        "plugged": app.demo.plugged})
            return self._send(404, {"error": "unknown endpoint"})
        except Exception as e:  # keep the server alive, report the error
            app.log(f"Fehler bei {path}: {e}")
            return self._send(500, {"ok": False, "error": str(e)})

    def do_POST(self):
        app, db = self.app, self.app.db
        path = urlparse(self.path).path
        r = [p for p in path.split("/") if p][1:] if path.startswith("/api/") else None
        data = self._json_body()
        try:
            if r == ["connect"]:
                return self._send(200, app.connect(data.get("port")))
            if r == ["disconnect"]:
                return self._send(200, app.disconnect())
            if r == ["idle"]:
                return self._send(200, app.idle())
            if r == ["note"]:
                return self._send(200, app.write_note(str(data.get("text", ""))))
            if r == ["read"]:
                return self._send(200, app.live_read())
            if r == ["save"]:
                return self._send(200, app.save_pending(data.get("token"), data.get("fields")))
            if r == ["settings"]:
                for k in ("base_url", "company"):
                    if k in data:
                        db.set_setting(k, str(data[k]).strip())
                return self._send(200, {"ok": True, **app.settings(self.headers.get("Host"))})
            if r == ["demo", "plug"] and app.demo:
                return self._send(200, {"ok": True, "plugged": app.demo.plug(data.get("key"))})
            if r == ["alerts", "ack_all"]:
                db.ack_all(data.get("battery_id"))
                return self._send(200, {"ok": True})
            if r and len(r) == 3 and r[0] == "alerts" and r[2] in ("ack", "unack"):
                db.ack_alert(int(r[1]), r[2] == "ack")
                return self._send(200, {"ok": True})
            if r and len(r) == 2 and r[0] == "batteries":
                b = self._battery_or_404(r[1])
                if b:
                    return self._send(200, {"ok": True,
                                            "battery": db.update_battery(b["id"], data)})
                return
            return self._send(404, {"error": "unknown endpoint"})
        except Exception as e:
            app.log(f"Fehler bei {path}: {e}")
            return self._send(500, {"ok": False, "error": str(e)})

    def do_DELETE(self):
        db = self.app.db
        r = [p for p in urlparse(self.path).path.split("/") if p][1:]
        if len(r) == 2 and r[0] == "batteries":
            b = self._battery_or_404(r[1])
            if b:
                db.delete_battery(b["id"])
                self.app.log(f"Akku gelöscht: {b['name'] or b['eserial']}")
                return self._send(200, {"ok": True})
            return
        if len(r) == 2 and r[0] == "readings":
            db.delete_reading(int(r[1]))
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "unknown endpoint"})


def main():
    ap = argparse.ArgumentParser(description="M18 Akku-Verwaltung")
    ap.add_argument("--port", help="serieller Port, mit dem beim Start verbunden wird")
    ap.add_argument("--demo", action="store_true",
                    help="simulierte Akku-Flotte, keine Hardware nötig")
    ap.add_argument("--db", help=f"Datenbankdatei (Standard: {DEFAULT_DB}; "
                                 "im Demo-Modus eine temporäre Datei)")
    ap.add_argument("--host", default="127.0.0.1",
                    help="Adresse, auf der der Server lauscht (0.0.0.0 = im ganzen LAN)")
    ap.add_argument("--http-port", type=int, default=8018)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    if args.db:
        db_path = args.db
    elif args.demo:
        db_path = os.path.join(tempfile.mkdtemp(prefix="m18demo-"), "demo.db")
    else:
        db_path = DEFAULT_DB
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    db = FleetDB(db_path)

    demo = None
    if args.demo:
        from demo import DemoFleet
        if db.list_batteries():
            print("Hinweis: Datenbank enthält bereits Akkus – Demo-Daten werden ergänzt.")
        demo = DemoFleet(db)

    app = App(db, demo=demo, public_host=args.host)
    if args.port and not demo:
        app.connect(args.port)
    Handler.app = app

    httpd = ThreadingHTTPServer((args.host, args.http_port), Handler)
    shown = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host
    url = f"http://{shown}:{args.http_port}"
    print(f"M18 Akku-Verwaltung ({'DEMO' if demo else 'live'}) läuft auf {url}")
    print(f"Datenbank: {db_path}")
    if args.host in ("0.0.0.0", ""):
        print(f"Im LAN erreichbar unter http://{lan_ip()}:{args.http_port}")
    print("Beenden mit Strg+C.")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nBeende.")
        app.disconnect_on_exit()
        db.close()


if __name__ == "__main__":
    main()
