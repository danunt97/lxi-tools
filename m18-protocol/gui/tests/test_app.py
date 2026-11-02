"""Tests for the battery-management GUI (stdlib only).

    python -m unittest discover -s gui/tests -v
"""

import datetime as dt
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
GUI = os.path.dirname(HERE)
sys.path.insert(0, GUI)

import fleet  # noqa: E402
import m18_gui  # noqa: E402
from demo import DemoFleet  # noqa: E402
from fleet import FleetDB  # noqa: E402
from qrgen import QrCode  # noqa: E402


def fake_register_dump(cells_mv=(4012, 4010, 4015, 4009, 4011), overheat=2, lowv_charges=0):
    """What M18.read_id(output='array') returns, keyed by address."""
    utc = dt.timezone.utc
    return {
        0x0000: 5,
        0x0004: "Type: 306, Serial: 412877",
        0x0011: dt.datetime(2022, 3, 14, tzinfo=utc),
        0x0015: dt.datetime(1970, 1, 1, tzinfo=utc),   # unset on non-Forge packs
        0x0019: dt.datetime(1970, 1, 1, tzinfo=utc),
        0x0023: '"A-07----------------"',
        0x400A: list(cells_mv),
        0x4014: 23.4,
        0x401F: None,
        0x9000: dt.datetime(2022, 4, 2, tzinfo=utc),
        0x9004: dt.datetime(2026, 10, 20, tzinfo=utc),
        0x9008: dt.datetime(2026, 10, 28, tzinfo=utc),
        0x9010: 1670,
        0x9012: 3_600_000,          # 1000 Ah
        0x9016: 65_000_000,
        0x901A: 260, 0x901E: 20, 0x9020: 240,
        0x9024: "312:10:05",
        0x902E: lowv_charges, 0x9030: 4, 0x9032: overheat, 0x9034: 1, 0x9036: 6, 0x9038: 2,
        0x903A: 1000, 0x903C: 500, 0x9060: 10,
    }


class TestDecode(unittest.TestCase):
    def test_decode_registers(self):
        s = m18_gui.decode_registers(fake_register_dump())
        self.assertTrue(s["ok"])
        self.assertEqual(s["eserial"], "306-412877")
        self.assertEqual(s["type_code"], 306)
        self.assertEqual(s["cells"], [4.012, 4.01, 4.015, 4.009, 4.011])
        self.assertEqual(s["imbalance_mv"], 6)
        self.assertAlmostEqual(s["pack_v"], 20.06)
        self.assertEqual(s["temp"], 23.4)
        self.assertEqual(s["note"], "A-07")
        self.assertEqual(s["manufactured"], "2022-03-14T00:00:00Z")
        self.assertEqual(s["first_charge"], "2022-04-02T00:00:00Z")  # falls back to 0x9000
        self.assertEqual(s["counters"]["charge_time_s"], 312 * 3600 + 10 * 60 + 5)
        self.assertEqual(s["counters"]["tool_time_s"], 1510)
        self.assertEqual(s["counters"]["overheat"], 2)
        json.dumps(s["raw"])  # must be JSON serialisable

    def test_missing_serial(self):
        self.assertFalse(m18_gui.decode_registers({})["ok"])


class TestFleet(unittest.TestCase):
    def setUp(self):
        self.db = FleetDB(":memory:")

    def test_health_and_alerts(self):
        s1 = m18_gui.decode_registers(fake_register_dump())
        bat = self.db.create_battery(s1, {"name": "A-07", "location": "Werkstatt"})
        self.assertEqual(bat["capacity_ah"], 5.0)
        t0 = fleet.utcnow() - dt.timedelta(days=60)
        r1, a1 = self.db.add_reading(bat["id"], s1, ts=t0)
        self.assertEqual(a1, [])
        self.assertTrue(0 < r1["health"] < 100)

        # pack got worse: imbalance up, overheat + deep-discharge charge
        s2 = m18_gui.decode_registers(fake_register_dump(
            cells_mv=(4012, 3990, 4015, 3900, 4011), overheat=4, lowv_charges=1))
        r2, a2 = self.db.add_reading(bat["id"], s2, ts=t0 + dt.timedelta(days=30))
        kinds = {a["kind"]: a["severity"] for a in a2}
        self.assertEqual(kinds.get("imbalance"), "crit")      # 115 mV
        self.assertEqual(kinds.get("overheat"), "warn")
        self.assertEqual(kinds.get("lowv_charge"), "crit")
        self.assertEqual(kinds.get("health_drop"), "crit")
        self.assertLess(r2["health"], r1["health"])

        lst = self.db.list_batteries()
        self.assertEqual(lst[0]["status"], "critical")
        self.assertEqual(lst[0]["alerts_open"]["crit"], 3)
        self.db.ack_all(bat["id"])
        self.assertEqual(self.db.alerts(open_only=True), [])

    def test_resolve(self):
        s = m18_gui.decode_registers(fake_register_dump())
        self.db.create_battery(s, {"name": "A-07", "case_serial": "J45A12345"})
        for q in ("306-412877", "http://pc:8018/b/306-412877", "412877", "a-07", "J45A12345"):
            self.assertEqual(self.db.resolve(q)["eserial"], "306-412877", q)
        self.assertIsNone(self.db.resolve("nope"))

    def test_trend(self):
        now = fleet.utcnow()
        pts = [(now - dt.timedelta(days=365 - i * 30), 90 - i * 2) for i in range(12)]
        t = fleet.trend(pts)
        self.assertAlmostEqual(t["slope_per_year"], -24.3, delta=0.5)
        self.assertIsNotNone(t["projected_date"])


class TestQr(unittest.TestCase):
    def test_structure(self):
        qr = QrCode.encode("http://127.0.0.1:8018/b/306-412877", ecl="M")
        self.assertEqual(qr.size, qr.version * 4 + 17)
        self.assertTrue(qr.modules[0][0] and qr.modules[6][6])        # finder corner
        self.assertTrue(qr.modules[qr.size - 8][8])                   # dark module
        self.assertTrue(qr.to_png().startswith(b"\x89PNG"))
        self.assertIn("<svg", qr.to_svg())

    def test_too_long(self):
        with self.assertRaises(ValueError):
            QrCode.encode("x" * 3000, ecl="H")


class TestHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        db = FleetDB(os.path.join(cls.tmp, "t.db"))
        app = m18_gui.App(db, demo=DemoFleet(db))
        m18_gui.Handler.app = app
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), m18_gui.Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def req(self, path, data=None, method=None):
        body = json.dumps(data).encode() if data is not None else None
        r = urllib.request.Request(self.base + path, data=body, method=method,
                                   headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r) as resp:
            raw = resp.read()
            return json.loads(raw) if resp.headers.get_content_type() == "application/json" else raw

    def test_pages(self):
        for p in ("/", "/fleet", "/b/107-90544", "/read", "/label/107-90544", "/report/107-90544",
                  "/static/app.js", "/qr/107-90544.svg", "/qr/107-90544.png", "/export/fleet.csv",
                  "/export/battery/107-90544.csv", "/export/database"):
            with urllib.request.urlopen(self.base + p) as r:
                self.assertEqual(r.status, 200, p)

    def test_read_save_flow(self):
        n0 = len(self.req("/api/batteries/108-88213")["readings"])
        self.req("/api/connect", {"port": "DEMO"})
        self.req("/api/demo/plug", {"key": "b01"})
        r = self.req("/api/read", {})
        self.assertTrue(r["ok"])
        self.assertEqual(r["battery"]["eserial"], "108-88213")
        s = self.req("/api/save", {"token": r["token"], "fields": {"location": "Halle 2"}})
        self.assertTrue(s["ok"])
        d = self.req("/api/batteries/108-88213")
        self.assertEqual(len(d["readings"]), n0 + 1)
        self.assertEqual(d["location"], "Halle 2")
        # a token can only be used once
        self.assertFalse(self.req("/api/save", {"token": r["token"]})["ok"])

    def test_new_battery(self):
        self.req("/api/connect", {"port": "DEMO"})
        self.req("/api/demo/plug", {"key": "new"})
        r = self.req("/api/read", {})
        self.assertIsNone(r["battery"])
        s = self.req("/api/save", {"token": r["token"], "fields": {"name": "N-01", "location": "Lager"}})
        self.assertTrue(s["created"])
        self.assertEqual(self.req("/api/resolve?q=N-01")["battery"]["eserial"], r["eserial"])

    def test_edit_and_settings(self):
        self.req("/api/batteries/306-412901", {"comment": "neu kommentiert", "retired": True})
        d = self.req("/api/batteries/306-412901")
        self.assertEqual(d["comment"], "neu kommentiert")
        self.assertEqual(d["retired"], 1)
        self.req("/api/settings", {"base_url": "http://werkstatt:8018/"})
        self.assertEqual(self.req("/api/batteries/306-412901")["qr_url"],
                         "http://werkstatt:8018/b/306-412901")
        self.req("/api/settings", {"base_url": ""})


if __name__ == "__main__":
    unittest.main()
