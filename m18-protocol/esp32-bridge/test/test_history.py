"""
Offline measurements stored on the bridge -> import into the PC database,
and the optional bridge password. Runs against the real C++ bridge core
(sim_bridge.cpp: m18_proto/m18_reader/m18_history/m18_commands + simulated pack).

    python -m unittest esp32-bridge/test/test_history.py -v
"""

import base64
import datetime as dt
import json
import os
import sys
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, ROOT, os.path.join(ROOT, "gui")]

import fleet  # noqa: E402
import m18  # noqa: E402
import m18_bridge  # noqa: E402
import m18_gui  # noqa: E402
from bridge_import import import_from_bridge, import_records, snapshot_from_record  # noqa: E402
from test_bridge import GXX, SimProcess, build_sim  # noqa: E402


def start_shim(sim, password=None):
    """HTTP front-end like the ESP32 (POST /cmd), optionally with Basic auth."""
    want = "Basic " + base64.b64encode(f"m18:{password}".encode()).decode() if password else None

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"])).decode()
            if want and self.headers.get("Authorization") != want:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="M18-Bruecke"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            out = ("\n".join(sim.exec([ln for ln in body.splitlines() if ln.strip()])) + "\n").encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


@unittest.skipUnless(GXX, "g++ not available")
class TestHistory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = build_sim()

    def setUp(self):
        self.sim = SimProcess(self.exe)
        self.srv, self.url = start_shim(self.sim)
        self.db = fleet.FleetDB(":memory:")

    def tearDown(self):
        self.srv.shutdown()
        self.sim.close()

    def test_offline_record_equals_live_reading(self):
        m = m18_bridge.M18Bridge(self.url)
        rec_id = m.scan()["history_id"]
        rec = m.history_get(rec_id)
        offline = snapshot_from_record(rec)
        m.reset()
        live = m18_gui.decode_registers({m18.data_id[i][0]: v for i, v in m.read_id(output="array")})
        for k in ("eserial", "type_code", "cells", "pack_v", "temp", "imbalance_mv", "manufactured", "note"):
            self.assertEqual(offline[k], live[k], k)
        self.assertEqual(offline["counters"], live["counters"])
        self.assertEqual(len(offline["raw"]), len(live["raw"]))

    def test_sync_into_database(self):
        m = m18_bridge.M18Bridge(self.url)                  # sets the bridge clock (TIME)
        self.assertEqual(m.history_stats()["count"], 0)
        m.scan()
        m.scan()                                            # same pack, unchanged -> duplicate on import
        metas = m.history_list()
        self.assertEqual(len(metas), 2)
        self.assertEqual(metas[0]["loc"], "Kunde Meier")
        self.assertGreater(metas[0]["ts"], 1700000000)

        res = import_from_bridge(self.db, m)
        self.assertEqual((res["imported"], res["duplicates"], res["created"]), (1, 1, 1), res)
        self.assertEqual(res["errors"], [])
        bat = self.db.get_battery("306-412877")
        self.assertEqual(bat["location"], "Kunde Meier")
        self.assertEqual(bat["comment"], "Testmessung")
        rs = self.db.readings(bat["id"])
        self.assertEqual(len(rs), 1)
        self.assertEqual(rs[0]["source"], "esp")
        self.assertEqual(rs[0]["cells"], [4.012, 4.01, 4.015, 4.009, 4.011])
        self.assertEqual(rs[0]["counters"]["total_charges"], 260)
        ts = fleet.parse_ts(rs[0]["ts"])
        self.assertLess(abs((fleet.utcnow() - ts).total_seconds()), 300)

        # both records are acknowledged on the bridge: nothing new, re-import is a no-op
        self.assertEqual(m.history_list(new_only=True), [])
        self.assertEqual(m.history_stats()["unsynced"], 0)
        res2 = import_from_bridge(self.db, m)
        self.assertEqual(res2["imported"] + res2["duplicates"], 0)

    def test_time_from_uptime_without_clock(self):
        m = m18_bridge.M18Bridge(self.url, sync_time=False)  # bridge has no clock (offline, no NTP)
        m.scan()
        meta = m.history_list()[0]
        self.assertEqual(meta["ts"], 0)
        res = import_from_bridge(self.db, m)
        item = res["items"][0]
        self.assertEqual(item["time_source"], "uptime")       # same boot: dated by uptime difference
        ts = fleet.parse_ts(item["ts"])
        age = (fleet.utcnow() - ts).total_seconds()
        self.assertTrue(60 < age < 120, age)                  # sim: uptime 200 s, record at 123 s

    def test_file_import_and_gui_endpoints(self):
        m = m18_bridge.M18Bridge(self.url)
        m.scan()
        rec = m.history_get(m.history_list()[0]["id"])
        export = {"format": "m18-bridge-export", "version": 1, "records": [rec]}
        res = import_records(self.db, json.loads(json.dumps(export))["records"])
        self.assertEqual(res["imported"], 1)
        self.assertEqual(import_records(self.db, [rec])["duplicates"], 1)   # idempotent

        # GUI backend: connect to the bridge, list and import via HTTP endpoints
        db2 = fleet.FleetDB(":memory:")
        app = m18_gui.App(db2)
        m18_gui.Handler.app = app
        gsrv = ThreadingHTTPServer(("127.0.0.1", 0), m18_gui.Handler)
        threading.Thread(target=gsrv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{gsrv.server_address[1]}"

        def call(path, data=None):
            req = urllib.request.Request(base + path, data=None if data is None else json.dumps(data).encode(),
                                         method="POST" if data is not None else "GET",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())
        try:
            self.assertTrue(call("/api/connect", {"port": self.url})["bridge"])
            hist = call("/api/bridge/history")
            self.assertEqual(len(hist["items"]), 1)
            r = call("/api/bridge/import", {"ids": [hist["items"][0]["id"]]})
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["imported"], 1)
            self.assertEqual(call("/api/bridge/history")["items"], [])
            r = call("/api/import/bridge-file", export)
            self.assertEqual(r["duplicates"], 1)
            self.assertEqual(len(call("/api/batteries/306-412877")["readings"]), 1)
        finally:
            app.hw.disconnect()
            gsrv.shutdown()


@unittest.skipUnless(GXX, "g++ not available")
class TestPassword(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = build_sim()

    def test_basic_auth(self):
        sim = SimProcess(self.exe)
        srv, url = start_shim(sim, password="geheim123")
        try:
            with self.assertRaises(m18_bridge.BridgeAuthError):
                m18_bridge.M18Bridge(url)
            with self.assertRaises(m18_bridge.BridgeAuthError):
                m18_bridge.M18Bridge(url, password="falsch")
            self.assertTrue(m18_bridge.M18Bridge(url, password="geheim123").reset())
            host = url.replace("http://", "")
            self.assertTrue(m18_bridge.M18Bridge(f"http://m18:geheim123@{host}").reset())   # URL credentials

            # GUI: wrong/no password -> clear message with "auth" flag; stored after success
            app = m18_gui.App(fleet.FleetDB(":memory:"))
            r = app.connect(url)
            self.assertFalse(r["ok"])
            self.assertTrue(r["auth"])
            self.assertIn("Passwort", r["error"])
            self.assertTrue(app.connect(url, "geheim123")["ok"])
            app.disconnect()
            self.assertTrue(app.connect(url)["ok"])                  # remembered password
            app.disconnect()
        finally:
            srv.shutdown()
            sim.close()


if __name__ == "__main__":
    unittest.main()
