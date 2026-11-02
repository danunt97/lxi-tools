"""
Battery fleet database (SQLite, stdlib only) and aging analysis.

A *snapshot* is one decoded read-out of a pack (see m18_gui.decode_registers
or demo.py). Every saved snapshot becomes a row in `readings`; on save it is
compared with the previous reading of the same pack and alerts are raised
when the pack got noticeably worse.

The pack does not report a state-of-health. The "health" value used here is
an *estimate* built from wear indicators the pack does log (energy throughput
relative to nominal capacity, cell imbalance, abuse events, calendar age).
Every deduction is stored next to the score so the UI can show why.
"""

import datetime as _dt
import json
import sqlite3
import threading

SCHEMA_VERSION = 1

# type code (register 0x0004, first two bytes) -> (nominal Ah, model, cells)
# Same table as M18.health() in m18.py.
BATTERY_TYPES = {
    36: (1.5, "1.5Ah CP", "5s1p 18650"),
    37: (2.0, "2Ah CP", "5s1p 18650"),
    38: (3.0, "3Ah XC", "5s2p 18650"),
    39: (4.0, "4Ah XC", "5s2p 18650"),
    40: (5.0, "5Ah XC (<= 2018)", "5s2p 18650"),
    165: (5.0, "5Ah XC (2019-2021)", "5s2p 18650"),
    306: (5.0, "5Ah XC (2021-2023)", "5s2p 18650"),
    424: (5.0, "5Ah XC (>= 2023)", "5s2p 18650"),
    46: (6.0, "6Ah XC", "5s2p 18650"),
    47: (9.0, "9Ah HD", "5s3p 18650"),
    104: (3.0, "3Ah HO", "5s1p 21700"),
    150: (5.5, "5.5Ah HO (EU)", "5s2p 21700"),
    106: (6.0, "6Ah HO", "5s2p 21700"),
    107: (8.0, "8Ah HO", "5s2p 21700"),
    108: (12.0, "12Ah HO", "5s3p 21700"),
    383: (8.0, "8Ah Forge", "5s2p 21700 tabless"),
    384: (12.0, "12Ah Forge", "5s3p 21700 tabless"),
}

# Counter columns stored per reading (snapshot["counters"][name]).
COUNTERS = [
    "total_charges", "redlink_charges", "dumb_charges", "discharge_as",
    "discharge_ws", "overheat", "overcurrent", "lowvolt", "lowvolt_bounce",
    "discharge_to_empty", "lowv_charges", "days", "charge_time_s", "tool_time_s",
]

# --------------------------------------------------------------------------
# Thresholds (kept in one place so they are easy to tune)
# --------------------------------------------------------------------------
HEALTH_GOOD = 80
HEALTH_WEAK = 60
HEALTH_DROP_WARN = 5
HEALTH_DROP_CRIT = 10
IMB_WARN_MV = 50
IMB_CRIT_MV = 100
IMB_RISE_WARN_MV = 25
CELL_LOW_WARN_V = 3.0
CELL_LOW_CRIT_V = 2.5
STALE_DAYS = 90

THRESHOLDS = {
    "health_good": HEALTH_GOOD, "health_weak": HEALTH_WEAK,
    "health_drop_warn": HEALTH_DROP_WARN, "health_drop_crit": HEALTH_DROP_CRIT,
    "imb_warn_mv": IMB_WARN_MV, "imb_crit_mv": IMB_CRIT_MV,
    "imb_rise_warn_mv": IMB_RISE_WARN_MV,
    "cell_low_warn_v": CELL_LOW_WARN_V, "cell_low_crit_v": CELL_LOW_CRIT_V,
    "stale_days": STALE_DAYS,
}


def utcnow():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def iso(dt):
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s):
    if not s:
        return None
    s = str(s).strip().replace("Z", "+00:00")
    try:
        d = _dt.datetime.fromisoformat(s)
    except ValueError:
        try:
            d = _dt.datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d


def battery_type(code):
    try:
        code = int(code)
    except (TypeError, ValueError):
        return (None, "Unbekannt", "")
    return BATTERY_TYPES.get(code, (None, f"Unbekannt (Typ {code})", ""))


# --------------------------------------------------------------------------
# Health estimate
# --------------------------------------------------------------------------
def health_estimate(snap, capacity_ah=None, now=None):
    """Return (score 0-100, [ {label, penalty, detail}, ... ])."""
    c = snap.get("counters") or {}
    if capacity_ah is None:
        capacity_ah = battery_type(snap.get("type_code"))[0]
    parts = []

    def add(label, penalty, detail):
        parts.append({"label": label, "penalty": round(max(0.0, penalty), 1), "detail": detail})

    das = c.get("discharge_as")
    if das is not None and capacity_ah:
        efc = das / 3600.0 / capacity_ah
        add("Zyklenverschleiß", min(45.0, efc * 0.04), f"{efc:.0f} äquivalente Vollzyklen")
    imb = snap.get("imbalance_mv")
    if imb is not None:
        add("Zell-Imbalance", min(40.0, max(0, imb - 20) * 0.35), f"{imb} mV Spreizung")
    lvc = c.get("lowv_charges")
    if lvc:
        add("Ladung mit Zelle < 2,5 V", min(30.0, lvc * 10), f"{lvc}× tiefentladen geladen")
    oh = c.get("overheat")
    if oh:
        add("Überhitzung", min(15.0, oh * 1.5), f"{oh} Ereignisse")
    man = parse_ts(snap.get("manufactured"))
    if man:
        now = now or utcnow()
        years = max(0.0, (now - man).days / 365.25)
        add("Kalenderalter", min(15.0, years * 2), f"{years:.1f} Jahre")
    if not parts:
        return None, []
    score = round(max(0.0, 100 - sum(p["penalty"] for p in parts)))
    return score, parts


def status_of(health, open_crit=0):
    if health is None:
        return "unknown"
    if open_crit:
        return "critical"
    if health >= HEALTH_GOOD:
        return "good"
    if health >= HEALTH_WEAK:
        return "watch"
    return "weak"


# --------------------------------------------------------------------------
# Aging: compare a new reading with the previous one
# --------------------------------------------------------------------------
def detect_changes(prev, new):
    """Return a list of alerts {severity, kind, message} for `new`.

    `prev` / `new` are reading dicts as returned by FleetDB (or a preview
    with the same keys). `prev` may be None for the first reading.
    """
    alerts = []

    def a(sev, kind, msg):
        alerts.append({"severity": sev, "kind": kind, "message": msg})

    cells = new.get("cells") or []
    if cells:
        lo = min(cells)
        if lo < CELL_LOW_CRIT_V:
            a("crit", "cell_low", f"Zelle bei {lo:.2f} V – tiefentladen (< {CELL_LOW_CRIT_V} V)")
        elif lo < CELL_LOW_WARN_V:
            a("warn", "cell_low", f"Zelle bei {lo:.2f} V – sehr niedrig (< {CELL_LOW_WARN_V} V)")

    imb = new.get("imbalance_mv")
    pimb = prev.get("imbalance_mv") if prev else None
    if imb is not None:
        if imb >= IMB_CRIT_MV and (pimb is None or pimb < IMB_CRIT_MV):
            a("crit", "imbalance", f"Zell-Imbalance {imb} mV (≥ {IMB_CRIT_MV} mV)")
        elif imb >= IMB_WARN_MV and (pimb is None or pimb < IMB_WARN_MV):
            a("warn", "imbalance", f"Zell-Imbalance {imb} mV (≥ {IMB_WARN_MV} mV)")
        elif pimb is not None and imb - pimb >= IMB_RISE_WARN_MV:
            a("warn", "imbalance_rise", f"Zell-Imbalance stieg von {pimb} auf {imb} mV")

    if prev is None:
        return alerts

    h, ph = new.get("health"), prev.get("health")
    if h is not None and ph is not None:
        drop = ph - h
        if drop >= HEALTH_DROP_CRIT:
            a("crit", "health_drop", f"Zustand fiel um {drop} Punkte ({ph} → {h})")
        elif drop >= HEALTH_DROP_WARN:
            a("warn", "health_drop", f"Zustand fiel um {drop} Punkte ({ph} → {h})")

    c, pc = new.get("counters") or {}, prev.get("counters") or {}

    def delta(k):
        if c.get(k) is None or pc.get(k) is None:
            return 0
        return c[k] - pc[k]

    d = delta("lowv_charges")
    if d > 0:
        a("crit", "lowv_charge", f"{d}× neu geladen, obwohl eine Zelle < 2,5 V war")
    d = delta("overheat")
    if d > 0:
        a("warn", "overheat", f"{d} neue Überhitzungs-Ereignisse (gesamt {c.get('overheat')})")
    d = delta("lowvolt")
    if d > 0:
        a("warn", "lowvolt", f"{d} neue Unterspannungs-Ereignisse (gesamt {c.get('lowvolt')})")
    d = delta("overcurrent")
    if d > 0:
        a("info", "overcurrent", f"{d} neue Überstrom-Ereignisse")
    return alerts


def trend(points, target=HEALTH_WEAK, window_days=540):
    """Linear fit of health over time.

    points: [(datetime, health), ...] sorted ascending.
    Returns {slope_per_year, projected_date (ISO) or None, n} or None.
    """
    pts = [(t, h) for t, h in points if h is not None]
    if len(pts) < 3:
        return None
    last = pts[-1][0]
    pts = [(t, h) for t, h in pts if (last - t).days <= window_days]
    if len(pts) < 3:
        return None
    t0 = pts[0][0]
    if (last - t0).days < 90:  # too short a span for a meaningful slope
        return None
    xs = [(t - t0).total_seconds() / 86400.0 for t, _ in pts]
    ys = [h for _, h in pts]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx  # per day
    icpt = my - slope * mx
    out = {"slope_per_year": round(slope * 365.25, 1), "n": n, "projected_date": None,
           "fit": [[iso(t0), round(icpt, 2)], [iso(last), round(icpt + slope * xs[-1], 2)]]}
    cur = ys[-1]  # project from the latest actual value, not the fitted one
    if cur <= target:
        out["projected_date"] = iso(last)
    elif slope < -1e-4:
        days_left = (cur - target) / -slope
        if days_left < 365 * 25:
            proj = last + _dt.timedelta(days=days_left)
            out["projected_date"] = iso(proj)
            out["projection"] = [[iso(last), cur], [iso(proj), target]]
    return out


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS batteries (
    id            INTEGER PRIMARY KEY,
    eserial       TEXT NOT NULL UNIQUE,
    type_code     INTEGER,
    capacity_ah   REAL,
    model         TEXT,
    name          TEXT NOT NULL DEFAULT '',
    case_serial   TEXT NOT NULL DEFAULT '',
    location      TEXT NOT NULL DEFAULT '',
    comment       TEXT NOT NULL DEFAULT '',
    manufactured  TEXT,
    retired       INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS readings (
    id            INTEGER PRIMARY KEY,
    battery_id    INTEGER NOT NULL REFERENCES batteries(id) ON DELETE CASCADE,
    ts            TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'live',
    pack_v        REAL,
    temp_c        REAL,
    imbalance_mv  INTEGER,
    health        INTEGER,
    health_parts  TEXT,
    cells         TEXT,
    note          TEXT,
    first_charge  TEXT,
    last_charge   TEXT,
    last_tool_use TEXT,
    total_charges INTEGER, redlink_charges INTEGER, dumb_charges INTEGER,
    discharge_as  INTEGER, discharge_ws INTEGER,
    overheat      INTEGER, overcurrent INTEGER, lowvolt INTEGER,
    lowvolt_bounce INTEGER, discharge_to_empty INTEGER, lowv_charges INTEGER,
    days          INTEGER, charge_time_s INTEGER, tool_time_s INTEGER,
    raw           TEXT
);
CREATE INDEX IF NOT EXISTS idx_readings_bat_ts ON readings(battery_id, ts);
CREATE TABLE IF NOT EXISTS alerts (
    id            INTEGER PRIMARY KEY,
    battery_id    INTEGER NOT NULL REFERENCES batteries(id) ON DELETE CASCADE,
    reading_id    INTEGER REFERENCES readings(id) ON DELETE CASCADE,
    ts            TEXT NOT NULL,
    severity      TEXT NOT NULL,
    kind          TEXT NOT NULL,
    message       TEXT NOT NULL,
    acknowledged  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_alerts_bat ON alerts(battery_id, acknowledged);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

EDITABLE = ("name", "case_serial", "location", "comment", "retired", "capacity_ah")


class FleetDB:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def _migrate(self):
        with self.lock, self.con:
            ver = self.con.execute("PRAGMA user_version").fetchone()[0]
            if ver < 1:
                self.con.executescript(_SCHEMA)
                self.con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            # future: if ver < 2: ALTER TABLE ...

    def close(self):
        with self.lock:
            self.con.close()

    def backup_bytes(self):
        """Consistent copy of the database file (for download)."""
        import os
        import tempfile
        with self.lock:
            fd, tmp = tempfile.mkstemp(suffix=".db")
            os.close(fd)
            try:
                dst = sqlite3.connect(tmp)
                self.con.backup(dst)
                dst.close()
                with open(tmp, "rb") as f:
                    return f.read()
            finally:
                os.unlink(tmp)

    # ------------------------------------------------------------ settings
    def get_setting(self, key, default=None):
        with self.lock:
            r = self.con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    def set_setting(self, key, value):
        with self.lock, self.con:
            self.con.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                             "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    # ----------------------------------------------------------- batteries
    @staticmethod
    def _bat(row):
        return dict(row) if row else None

    def get_battery(self, ident):
        """Look up by numeric id or e-serial."""
        with self.lock:
            if isinstance(ident, int) or (isinstance(ident, str) and ident.isdigit()):
                r = self.con.execute("SELECT * FROM batteries WHERE id=?", (int(ident),)).fetchone()
                if r:
                    return self._bat(r)
            r = self.con.execute("SELECT * FROM batteries WHERE eserial=?", (str(ident),)).fetchone()
        return self._bat(r)

    def create_battery(self, snap, fields=None, ts=None):
        fields = fields or {}
        cap, model, _ = battery_type(snap.get("type_code"))
        now = iso(ts or utcnow())
        with self.lock, self.con:
            cur = self.con.execute(
                "INSERT INTO batteries(eserial,type_code,capacity_ah,model,name,case_serial,"
                "location,comment,manufactured,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (snap["eserial"], snap.get("type_code"), fields.get("capacity_ah") or cap, model,
                 fields.get("name", "").strip(), fields.get("case_serial", "").strip(),
                 fields.get("location", "").strip(), fields.get("comment", "").strip(),
                 snap.get("manufactured") or None, now, now))
            return self.get_battery(cur.lastrowid)

    def update_battery(self, bid, fields):
        sets, vals = [], []
        for k in EDITABLE:
            if k in fields:
                v = fields[k]
                if k == "retired":
                    v = 1 if v else 0
                elif k == "capacity_ah":
                    v = float(v) if v not in (None, "") else None
                else:
                    v = str(v).strip()
                sets.append(f"{k}=?")
                vals.append(v)
        if not sets:
            return self.get_battery(bid)
        sets.append("updated_at=?")
        vals += [iso(utcnow()), bid]
        with self.lock, self.con:
            self.con.execute(f"UPDATE batteries SET {', '.join(sets)} WHERE id=?", vals)
        return self.get_battery(bid)

    def delete_battery(self, bid):
        with self.lock, self.con:
            self.con.execute("DELETE FROM batteries WHERE id=?", (bid,))

    def list_batteries(self, include_retired=True):
        with self.lock:
            bats = [dict(r) for r in self.con.execute(
                "SELECT * FROM batteries ORDER BY name COLLATE NOCASE, eserial")]
            latest = {r["battery_id"]: self._reading(r) for r in self.con.execute(
                "SELECT r.* FROM readings r JOIN (SELECT battery_id, MAX(ts) mts FROM readings "
                "GROUP BY battery_id) m ON r.battery_id=m.battery_id AND r.ts=m.mts")}
            counts = {}
            for r in self.con.execute(
                    "SELECT battery_id, severity, COUNT(*) n FROM alerts WHERE acknowledged=0 "
                    "GROUP BY battery_id, severity"):
                counts.setdefault(r["battery_id"], {})[r["severity"]] = r["n"]
            series = {}
            for r in self.con.execute("SELECT battery_id, ts, health FROM readings ORDER BY ts"):
                series.setdefault(r["battery_id"], []).append([r["ts"], r["health"]])
        now = utcnow()
        out = []
        for b in bats:
            if b["retired"] and not include_retired:
                continue
            lr = latest.get(b["id"])
            ac = counts.get(b["id"], {})
            b["latest"] = lr
            b["alerts_open"] = ac
            b["health_series"] = series.get(b["id"], [])[-24:]
            b["status"] = status_of(lr["health"] if lr else None, ac.get("crit", 0))
            last = parse_ts(lr["ts"]) if lr else None
            b["days_since_reading"] = (now - last).days if last else None
            b["stale"] = b["days_since_reading"] is None or b["days_since_reading"] > STALE_DAYS
            pts = [(parse_ts(t), h) for t, h in series.get(b["id"], [])]
            b["trend"] = trend(pts)
            out.append(b)
        return out

    def locations(self):
        with self.lock:
            return [r[0] for r in self.con.execute(
                "SELECT DISTINCT location FROM batteries WHERE location<>'' ORDER BY location")]

    def resolve(self, q):
        """Find a battery from free text: e-serial, a scanned QR URL, name or case serial."""
        q = (q or "").strip()
        if not q:
            return None
        if "/b/" in q:
            q = q.rsplit("/b/", 1)[1].split("?")[0].split("#")[0].strip("/")
        b = self.get_battery(q)
        if b:
            return b
        with self.lock:
            r = self.con.execute(
                "SELECT * FROM batteries WHERE eserial LIKE ? OR name=? COLLATE NOCASE "
                "OR case_serial=? COLLATE NOCASE ORDER BY id LIMIT 1",
                (f"%-{q}", q, q)).fetchone()
        return self._bat(r)

    # ------------------------------------------------------------ readings
    @staticmethod
    def _reading(row):
        if row is None:
            return None
        d = dict(row)
        d["cells"] = json.loads(d["cells"]) if d.get("cells") else []
        d["health_parts"] = json.loads(d["health_parts"]) if d.get("health_parts") else []
        d["counters"] = {k: d.pop(k) for k in COUNTERS}
        d.pop("raw", None)
        return d

    def readings(self, bid, with_raw=False):
        with self.lock:
            rows = self.con.execute("SELECT * FROM readings WHERE battery_id=? ORDER BY ts",
                                    (bid,)).fetchall()
        out = []
        for r in rows:
            d = self._reading(r)
            if with_raw:
                d["raw"] = json.loads(r["raw"]) if r["raw"] else {}
            out.append(d)
        return out

    def last_reading(self, bid, before_ts=None):
        with self.lock:
            if before_ts:
                r = self.con.execute("SELECT * FROM readings WHERE battery_id=? AND ts<? "
                                     "ORDER BY ts DESC LIMIT 1", (bid, before_ts)).fetchone()
            else:
                r = self.con.execute("SELECT * FROM readings WHERE battery_id=? "
                                     "ORDER BY ts DESC LIMIT 1", (bid,)).fetchone()
        return self._reading(r)

    def preview(self, snap, battery=None, now=None):
        """Evaluate a snapshot without saving: health, comparison, alerts."""
        cap = battery["capacity_ah"] if battery else None
        health, parts = health_estimate(snap, cap, now)
        new = {"health": health, "health_parts": parts, "cells": snap.get("cells") or [],
               "imbalance_mv": snap.get("imbalance_mv"), "counters": snap.get("counters") or {}}
        prev = self.last_reading(battery["id"]) if battery else None
        return {"health": health, "health_parts": parts, "previous": prev,
                "alerts": detect_changes(prev, new)}

    def add_reading(self, bid, snap, ts=None, source="live"):
        bat = self.get_battery(bid)
        ts = iso(ts or utcnow())
        health, parts = health_estimate(snap, bat["capacity_ah"], parse_ts(ts))
        c = snap.get("counters") or {}
        prev = self.last_reading(bid, before_ts=ts)
        new = {"health": health, "cells": snap.get("cells") or [],
               "imbalance_mv": snap.get("imbalance_mv"), "counters": c}
        found = detect_changes(prev, new)
        cols = ["battery_id", "ts", "source", "pack_v", "temp_c", "imbalance_mv", "health",
                "health_parts", "cells", "note", "first_charge", "last_charge",
                "last_tool_use"] + COUNTERS + ["raw"]
        vals = [bid, ts, source, snap.get("pack_v"), snap.get("temp"), snap.get("imbalance_mv"),
                health, json.dumps(parts), json.dumps(snap.get("cells") or []), snap.get("note"),
                snap.get("first_charge") or None, snap.get("last_charge") or None,
                snap.get("last_tool_use") or None] + [c.get(k) for k in COUNTERS] + \
               [json.dumps(snap.get("raw") or {}, default=str)]
        with self.lock, self.con:
            cur = self.con.execute(
                f"INSERT INTO readings({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals)
            rid = cur.lastrowid
            for a in found:
                self.con.execute("INSERT INTO alerts(battery_id,reading_id,ts,severity,kind,message)"
                                 " VALUES(?,?,?,?,?,?)",
                                 (bid, rid, ts, a["severity"], a["kind"], a["message"]))
            if snap.get("manufactured") and not bat.get("manufactured"):
                self.con.execute("UPDATE batteries SET manufactured=? WHERE id=?",
                                 (snap["manufactured"], bid))
        with self.lock:
            r = self.con.execute("SELECT * FROM readings WHERE id=?", (rid,)).fetchone()
        return self._reading(r), found

    def delete_reading(self, rid):
        with self.lock, self.con:
            self.con.execute("DELETE FROM readings WHERE id=?", (rid,))

    # -------------------------------------------------------------- alerts
    def alerts(self, bid=None, open_only=False, limit=500):
        q = ("SELECT a.*, b.eserial, b.name FROM alerts a JOIN batteries b ON b.id=a.battery_id "
             "WHERE 1=1")
        args = []
        if bid is not None:
            q += " AND a.battery_id=?"
            args.append(bid)
        if open_only:
            q += " AND a.acknowledged=0"
        q += (" ORDER BY a.acknowledged, CASE a.severity WHEN 'crit' THEN 0 WHEN 'warn' THEN 1 "
              "ELSE 2 END, a.ts DESC LIMIT ?")
        args.append(limit)
        with self.lock:
            return [dict(r) for r in self.con.execute(q, args)]

    def ack_alert(self, aid, value=True):
        with self.lock, self.con:
            self.con.execute("UPDATE alerts SET acknowledged=? WHERE id=?", (1 if value else 0, aid))

    def ack_all(self, bid=None, before_ts=None):
        q, args = "UPDATE alerts SET acknowledged=1 WHERE acknowledged=0", []
        if bid is not None:
            q += " AND battery_id=?"
            args.append(bid)
        if before_ts:
            q += " AND ts<?"
            args.append(before_ts)
        with self.lock, self.con:
            self.con.execute(q, args)

    # ---------------------------------------------------------------- detail
    def detail(self, ident):
        b = self.get_battery(ident)
        if not b:
            return None
        rs = self.readings(b["id"])
        al = self.alerts(b["id"])
        crit = sum(1 for a in al if not a["acknowledged"] and a["severity"] == "crit")
        last = rs[-1] if rs else None
        b["status"] = status_of(last["health"] if last else None, crit)
        b["readings"] = rs
        b["alerts"] = al
        b["trend"] = trend([(parse_ts(r["ts"]), r["health"]) for r in rs])
        if len(rs) >= 2:
            first = rs[0]
            b["since_first"] = {
                "days": (parse_ts(last["ts"]) - parse_ts(first["ts"])).days,
                "health": _sub(last["health"], first["health"]),
                "imbalance_mv": _sub(last["imbalance_mv"], first["imbalance_mv"]),
                "discharge_ah": _sub(_ah(last), _ah(first)),
            }
        return b


def _sub(a, b):
    if a is None or b is None:
        return None
    return round(a - b, 1)


def _ah(r):
    v = (r.get("counters") or {}).get("discharge_as")
    return None if v is None else v / 3600.0
