"""
End-to-end test of the ESP32 bridge without hardware.

The bridge firmware's protocol core (m18_proto.h) is compiled for the PC
together with a simulated battery (sim_bridge.cpp). m18_bridge.py then talks
to it exactly like to a real ESP32:
  - over a pseudo-terminal (= ESP32 on USB, needs pyserial), and
  - over HTTP (= ESP32 on Wi-Fi).

    python -m unittest esp32-bridge/test/test_bridge.py -v
"""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [ROOT, os.path.join(ROOT, "gui")]

import fleet  # noqa: E402
import m18  # noqa: E402
import m18_bridge  # noqa: E402
import m18_gui  # noqa: E402

GXX = shutil.which("g++")


def build_sim():
    out = os.path.join(tempfile.mkdtemp(), "sim_bridge")
    subprocess.check_call([GXX, "-std=c++17", "-O1", "-I", os.path.join(HERE, "..", "m18_bridge"),
                           os.path.join(HERE, "sim_bridge.cpp"), "-o", out])
    return out


class SimProcess:
    """sim_bridge serve: bridge line protocol on stdin/stdout."""

    def __init__(self, exe, hist_dir=None):
        self.hist_dir = hist_dir or tempfile.mkdtemp(prefix="m18hist")
        self.p = subprocess.Popen([exe, "serve", self.hist_dir], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  text=True, bufsize=1, encoding="utf-8")
        self.p.stdout.readline()  # READY banner
        self.lock = threading.Lock()

    def exec(self, lines):
        with self.lock:
            out = []
            for line in lines:
                self.p.stdin.write(line + "\n")
                self.p.stdin.flush()
                out.append(self.p.stdout.readline().strip())
            return out

    def close(self):
        self.p.kill()


@unittest.skipUnless(GXX, "g++ not available")
class TestBridge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = build_sim()

    def check_m18(self, m):
        self.assertTrue(m.reset())
        arr = m.read_id(output="array")
        self.assertEqual(len(arr), len(m18.data_id))
        by_addr = {m18.data_id[i][0]: v for i, v in arr}
        snap = m18_gui.decode_registers(by_addr)
        self.assertEqual(snap["eserial"], "306-412877")
        self.assertEqual(snap["cells"], [4.012, 4.01, 4.015, 4.009, 4.011])
        self.assertEqual(snap["counters"]["total_charges"], 260)
        self.assertAlmostEqual(snap["temp"], 23.0, delta=0.5)   # 0x401F = 0 -> falls back to 0x4014
        self.assertEqual(snap["counters"]["overheat"], 2)
        self.assertEqual(snap["manufactured"][:10], "2022-03-14")
        # Direct read-out on the bridge (SCAN) must match the PC's own decoding
        sc = m.scan()
        self.assertTrue(sc["ok"], sc)
        self.assertEqual(sc["eserial"], snap["eserial"])
        self.assertEqual(sc["cells_mv"], [round(v * 1000) for v in snap["cells"]])
        self.assertEqual(sc["imbalance_mv"], snap["imbalance_mv"])
        for k in ("total_charges", "overheat", "lowvolt", "discharge_as", "days", "tool_time_s"):
            self.assertEqual(sc["counters"][k], snap["counters"][k], k)
        now = fleet.utcnow()                              # M18Bridge set the bridge clock (TIME) on connect
        health, parts = fleet.health_estimate(snap, None, now)
        self.assertEqual(sc["health"], health)
        self.assertEqual([p["label"] for p in sc["health_parts"]], [p["label"] for p in parts])
        m.write_message("TEST-1")
        self.assertEqual(m.read_id([7], output="array")[0][1], '"TEST-1--------------"')
        m.idle()

    def test_http(self):
        sim = SimProcess(self.exe)

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"])).decode()
                out = ("\n".join(sim.exec([ln for ln in body.splitlines() if ln.strip()])) + "\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        target = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            self.check_m18(m18_bridge.M18Bridge(target))

            # The web GUI backend with the bridge as "port"
            logs = []
            hw = m18_gui.Hardware(logs.append)
            self.assertTrue(hw.connect(target)["ok"], logs)
            snap = hw.read()
            self.assertTrue(snap["ok"], logs)
            self.assertEqual(snap["eserial"], "306-412877")
            self.assertTrue(hw.write_note("A-07")["ok"])
            hw.disconnect()
        finally:
            srv.shutdown()
            sim.close()

    def test_http_flaky(self):
        """Wi-Fi hiccups (dropped connections) and a busy bridge are retried transparently."""
        sim = SimProcess(self.exe)
        state = {"n": 0, "busy": 2}

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"])).decode()
                state["n"] += 1
                if state["n"] % 5 == 0:                 # drop every 5th request on the floor
                    self.connection.close()
                    return
                lines = [ln for ln in body.splitlines() if ln.strip()]
                if state["busy"] > 0 and lines[0].startswith("RESET"):
                    state["busy"] -= 1
                    out = "ERR BUSY (Akku wird gerade ausgelesen)\n".encode()
                else:
                    out = ("\n".join(sim.exec(lines)) + "\n").encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            m = m18_bridge.M18Bridge(f"http://127.0.0.1:{srv.server_address[1]}")
            self.assertTrue(m.reset())                       # after two "ERR BUSY"
            self.assertEqual(state["busy"], 0)
            arr = m.read_id(output="array")
            answered = sum(1 for _, v in arr if v is not None)
            self.assertEqual(answered, len(m18.data_id))     # nothing lost despite drops
            self.assertGreater(state["n"], 200)
        finally:
            srv.shutdown()
            sim.close()

    def test_usb_serial(self):
        try:
            import serial  # noqa: F401
        except ImportError:
            self.skipTest("pyserial not installed")
        master, slave = os.openpty()
        p = subprocess.Popen([self.exe, "serve"], stdin=master, stdout=master)
        try:
            m = m18_bridge.M18Bridge(os.ttyname(slave))
            self.check_m18(m)
            m.port.close()
        finally:
            p.kill()
            os.close(master)
            os.close(slave)

    def test_discovery(self):
        import socket
        srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            srv.bind(("127.0.0.1", m18_bridge.DISCOVERY_PORT))
        except OSError:
            self.skipTest("UDP port 18018 busy")

        def responder():
            data, addr = srv.recvfrom(64)
            if data.startswith(b"M18BRIDGE?"):
                srv.sendto(b"M18BRIDGE 1.1 host=m18-bridge-ab12.local ip=192.168.1.50 http=80 "
                           b"mac=AA:BB:CC:DD:AB:12\n", addr)
        t = threading.Thread(target=responder, daemon=True)
        t.start()
        found = m18_bridge.discover(timeout=1.0, targets=["127.0.0.1"])
        srv.close()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["host"], "m18-bridge-ab12.local")
        self.assertEqual(found[0]["ip"], "192.168.1.50")
        self.assertEqual(found[0]["version"], "1.1")

    def test_target_detection(self):
        for t in ("m18-bridge.local", "192.168.1.50", "http://x:80"):
            self.assertTrue(m18_bridge.is_network_target(t), t)
        for t in ("COM7", "/dev/ttyUSB0", "/dev/cu.usbserial-110"):
            self.assertFalse(m18_bridge.is_network_target(t), t)


if __name__ == "__main__":
    unittest.main()
