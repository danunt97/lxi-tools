"""
Demo mode: a simulated fleet with ~2 years of history, and a simulated
"battery on the adapter" so the whole app can be explored without hardware.

The fleet is generated deterministically and fed through FleetDB.add_reading,
so trends and alerts are produced by exactly the same code as in live mode.
"""

import datetime as _dt
import math
import random

from fleet import BATTERY_TYPES, iso, utcnow

DAY = _dt.timedelta(days=1)

# key, name, type, e-serial, location, comment, age_days (since manufacture),
# Ah per week, start imbalance, imbalance drift mV/year, overheats/year,
# lowvolt events/year, low-voltage charge events (days before now),
# reading interval (days), stop readings N days ago
PROFILES = [
    dict(key="a01", name="A-01", type=306, serial=412877, loc="Werkstatt · Regal 1",
         comment="Bohrhammer-Set", age=1100, ahw=9, imb0=6, drift=2, oh=0.3, lv=1,
         lvc=[], every=35, stop=0),
    dict(key="a02", name="A-02", type=306, serial=412901, loc="Werkstatt · Regal 1",
         comment="", age=1080, ahw=7, imb0=8, drift=2.5, oh=0, lv=0.5,
         lvc=[], every=42, stop=0),
    dict(key="b01", name="B-01", type=108, serial=88213, loc="Transporter 1",
         comment="Kreissäge / Flex – viel Last", age=900, ahw=34, imb0=9, drift=7, oh=3, lv=4,
         lvc=[], every=28, stop=0),
    dict(key="b02", name="B-02", type=107, serial=90544, loc="Transporter 1",
         comment="Wird oft heiß eingesetzt", age=820, ahw=26, imb0=12, drift=10, oh=8, lv=6,
         lvc=[], every=30, stop=0),
    dict(key="c01", name="C-01", type=37, serial=118342, loc="Büro · Akkuschrauber-Koffer",
         comment="Kaum genutzt", age=2100, ahw=1.2, imb0=5, drift=1.5, oh=0, lv=0.2,
         lvc=[], every=60, stop=0),
    dict(key="c02", name="C-02", type=38, serial=230119, loc="Baustelle Schmidtstraße",
         comment="Lag 3 Monate leer im Container", age=1500, ahw=4.5, imb0=12, drift=11,
         oh=1, lv=3, lvc=[170, 40], every=30, stop=0),
    dict(key="f01", name="F-01", type=384, serial=7731, loc="Transporter 2",
         comment="Neu – Forge", age=95, ahw=40, imb0=4, drift=3, oh=0.5, lv=1,
         lvc=[], every=14, stop=0),
    dict(key="d01", name="D-01", type=106, serial=55410, loc="Lager",
         comment="Reserve", age=1300, ahw=5, imb0=7, drift=3, oh=0.2, lv=0.5,
         lvc=[], every=45, stop=210),
]

HISTORY_DAYS = 730


class Sim:
    """State of one simulated pack; advance() moves it forward in time."""

    def __init__(self, p, now, rng):
        self.p = p
        self.rng = rng
        self.cap = BATTERY_TYPES[p["type"]][0]
        self.manufactured = (now - p["age"] * DAY).replace(hour=0, minute=0, second=0)
        self.first_charge = self.manufactured + rng.randint(10, 60) * DAY
        self.t = self.first_charge
        self.ah = 0.0
        self.overheat = 0
        self.lowvolt = 0
        self.overcurrent = 0
        self.to_empty = 0
        self.extra_imb = 0.0
        self.lvc_days = [now - d * DAY for d in p["lvc"]]
        self.last_charge = self.first_charge
        self.note = ""

    def advance(self, to):
        days = max(0.0, (to - self.t).total_seconds() / 86400.0)
        if days <= 0:
            return
        r, p = self.rng, self.p
        weeks = days / 7.0
        self.ah += p["ahw"] * weeks * r.uniform(0.6, 1.4)
        yrs = days / 365.25
        self.overheat += _poisson(r, p["oh"] * yrs)
        self.lowvolt += _poisson(r, p["lv"] * yrs)
        self.overcurrent += _poisson(r, p["oh"] * 0.3 * yrs)
        self.to_empty += _poisson(r, p["lv"] * 0.6 * yrs)
        self.t = to
        self.last_charge = to - r.uniform(0.2, 6) * DAY

    def snapshot(self):
        p, r = self.p, self.rng
        yrs = (self.t - self.first_charge).days / 365.25
        lvc = sum(1 for d in self.lvc_days if d <= self.t)
        # imbalance drifts with age, jumps after deep-discharge charges
        imb = p["imb0"] + p["drift"] * yrs ** 1.3 + lvc * 14 + self.extra_imb + r.uniform(-3, 3)
        imb = max(2, round(imb))
        soc_v = r.uniform(3.62, 4.08)
        offs = [r.uniform(0, 1) for _ in range(5)]
        lo, hi = min(offs), max(offs)
        offs = [(o - lo) / ((hi - lo) or 1) for o in offs]
        cells = [round(soc_v + (o - 0.5) * imb / 1000.0, 3) for o in offs]
        imb_real = round((max(cells) - min(cells)) * 1000)
        charges = round(self.ah / self.cap * 1.25) + 1
        redlink = round(charges * 0.92)
        das = round(self.ah * 3600)
        return {
            "ok": True,
            "eserial": f"{p['type']}-{p['serial']}",
            "type_code": p["type"],
            "serial_num": p["serial"],
            "serial": f"Type: {p['type']:3d}, Serial: {p['serial']}",
            "manufactured": iso(self.manufactured),
            "first_charge": iso(self.first_charge),
            "last_charge": iso(self.last_charge),
            "last_tool_use": iso(self.t - r.uniform(0.5, 9) * DAY),
            "note": self.note,
            "cells": cells,
            "pack_v": round(sum(cells), 2),
            "temp": round(r.uniform(17, 27), 1),
            "imbalance_mv": imb_real,
            "counters": {
                "total_charges": charges, "redlink_charges": redlink,
                "dumb_charges": charges - redlink, "discharge_as": das,
                "discharge_ws": round(das * 18.2), "overheat": self.overheat,
                "overcurrent": self.overcurrent, "lowvolt": self.lowvolt,
                "lowvolt_bounce": self.lowvolt // 3, "discharge_to_empty": self.to_empty,
                "lowv_charges": lvc, "days": (self.t - self.first_charge).days,
                "charge_time_s": round(charges * 2400), "tool_time_s": round(das / 16),
            },
            "raw": {},
        }


def _poisson(rng, lam):
    if lam <= 0:
        return 0
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


class DemoFleet:
    """Seeds a FleetDB and simulates the pack currently on the adapter."""

    NEW_KEY = "new"

    def __init__(self, db, seed=18):
        self.db = db
        self.now = utcnow()
        self.rng = random.Random(seed)
        self.sims = {}
        self.plugged = "b02"
        self._new_sim = None
        self._seed()

    def _seed(self):
        now = self.now
        start = now - HISTORY_DAYS * DAY
        for p in PROFILES:
            sim = Sim(p, now, random.Random(p["serial"]))
            self.sims[p["key"]] = sim
            first = max(start, sim.first_charge + 3 * DAY)
            end = now - p["stop"] * DAY - 2 * DAY
            t = first
            bat = None
            while t <= end:
                sim.advance(t)
                snap = sim.snapshot()
                if bat is None:
                    bat = self.db.create_battery(snap, {"name": p["name"], "location": p["loc"],
                                                        "comment": p["comment"]}, ts=t)
                self.db.add_reading(bat["id"], snap, ts=t, source="demo")
                t += (p["every"] + sim.rng.randint(-6, 6)) * DAY
        # Older alerts count as handled; keep the last ~75 days open.
        self.db.ack_all(before_ts=iso(now - 75 * DAY))
        if self.db.get_setting("demo_seeded") is None:
            self.db.set_setting("demo_seeded", iso(now))

    def options(self):
        out = []
        for p in PROFILES:
            out.append({"key": p["key"], "label": f"{p['name']} · {BATTERY_TYPES[p['type']][1]}"
                                                  f" ({p['type']}-{p['serial']})"})
        out.append({"key": self.NEW_KEY, "label": "Neuer, unbekannter Akku (5Ah XC)"})
        return out

    def plug(self, key):
        if key == self.NEW_KEY or key in self.sims:
            self.plugged = key
            if key == self.NEW_KEY:
                self._new_sim = None
        return self.plugged

    def read(self):
        """Simulate reading the plugged-in pack a few weeks after the last read."""
        if self.plugged == self.NEW_KEY:
            if self._new_sim is None or self.db.get_battery(self._new_sim_eserial()):
                serial = self.rng.randint(300000, 999999)
                p = dict(key="new", name="", type=424, serial=serial, loc="", comment="",
                         age=self.rng.randint(30, 400), ahw=8, imb0=7, drift=2, oh=0.2, lv=0.5,
                         lvc=[], every=30, stop=0)
                self._new_sim = Sim(p, utcnow(), random.Random(serial))
            sim = self._new_sim
        else:
            sim = self.sims[self.plugged]
        # Compress time: each demo read = 3-8 weeks of use, so trends move.
        sim.advance(sim.t + self.rng.randint(21, 56) * DAY)
        if sim.p["key"] == "c02":
            sim.extra_imb += 12  # this one degrades visibly with every read
        snap = sim.snapshot()
        return snap

    def _new_sim_eserial(self):
        p = self._new_sim.p
        return f"{p['type']}-{p['serial']}"

    def write_note(self, text):
        sim = self._new_sim if self.plugged == self.NEW_KEY else self.sims.get(self.plugged)
        if sim:
            sim.note = text[:20]
