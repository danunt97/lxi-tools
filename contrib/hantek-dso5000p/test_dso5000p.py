#!/usr/bin/env python3
"""Tests for dso5000p.py against a simulated scope. Run: python3 -m unittest"""

import csv
import os
import struct
import tempfile
import unittest
import zlib

import dso5000p as d


def settings_blob(**fields):
    blob = bytearray(d.SETTINGS_LEN)
    offset = 0
    offsets = {}
    for name, size in d.SETTINGS_LAYOUT:
        offsets[name] = (offset, size)
        offset += size
    for key, value in fields.items():
        name = key.replace("_", "-")
        o, size = offsets[name]
        blob[o:o + size] = (value & ((1 << (8 * size)) - 1)).to_bytes(size, "little")
    return bytes(blob)


def reply(cmd, payload):
    return d.encode(cmd | 0x80, payload)


class FakeScope:
    """Answers like a DSO5102P, including its quirks."""

    def __init__(self, settings, ch_counts, screen=None, eat_first=1):
        self.settings = settings
        self.ch_counts = ch_counts      # {1: [counts], 2: [...]} as the user sees them
        self.screen = screen
        self.queue = [b"stale-junk-from-last-run".ljust(8, b"\0")]
        self.eat = eat_first            # scope swallows the first command(s)
        self.written = []
        self.closed = False

    def write(self, data, _timeout):
        self.written.append(bytes(data))
        if self.eat:
            self.eat -= 1
            return
        cmd, payload = d.decode(data)
        q = self.queue
        if cmd == d.CMD_READ_SETTINGS:
            q.append(reply(cmd, self.settings))
        elif cmd == d.CMD_ECHO:
            q.append(reply(cmd, payload))
        elif cmd == d.CMD_SYSTEM_TIME:
            q.append(reply(cmd, bytes([0xE2, 0x07, 5, 6, 7, 8, 9])))
        elif cmd == d.CMD_READ_SAMPLES:
            ch = payload[1] + 1
            wire = bytes(c ^ 0x80 for c in self.ch_counts.get(ch, []))
            q.append(reply(cmd, bytes([0x00, len(wire) & 0xFF, len(wire) >> 8, 0])))
            for i in range(0, len(wire), 1000):
                q.append(reply(cmd, bytes([0x01, ch - 1]) + wire[i:i + 1000]))
            q.append(reply(cmd, bytes([0x02, ch - 1])))
        elif cmd == d.CMD_SCREENSHOT:
            for i in range(0, len(self.screen), 10000):
                q.append(reply(cmd, b"\x01" + self.screen[i:i + 10000]))
            q.append(reply(cmd, b"\x02"))
        elif cmd == d.CMD_CONTROL:
            q.append(reply(cmd, b""))
        elif cmd == d.CMD_KEY:
            q.append(reply(cmd, b""))
        elif cmd == d.CMD_READ_FILE:
            body = b"hello from " + payload[1:]
            q.append(reply(cmd, b"\x01" + body))
            q.append(reply(cmd, b"\x02"))

    def read(self, _timeout):
        return self.queue.pop(0) if self.queue else None

    def close(self):
        self.closed = True


def make_scope(**kw):
    s = settings_blob(VERT_CH1_DISP=1, VERT_CH1_VB=9, VERT_CH1_PROBE=1,  # 1V/div x10
                      VERT_CH1_POS=-20, VERT_CH2_DISP=1, VERT_CH2_VB=6,    # 100mV/div
                      TRIG_STATE=1, HORIZ_TB=15,                           # 200us/div
                      TRIG_FREQUENCY=1_000_000)                            # 1 kHz
    # ch1: square wave between +/- 1 div around the position
    ch1 = [128 - 20 + (26 if (i // 100) % 2 else -26) for i in range(d.SAMPLES)]
    ch2 = [128] * d.SAMPLES
    return FakeScope(s, {1: ch1, 2: ch2}, **kw)


class Framing(unittest.TestCase):
    def test_encode_settings_request(self):
        self.assertEqual(d.encode(d.CMD_READ_SETTINGS), bytes([0x53, 0x02, 0x00, 0x01, 0x56]))

    def test_roundtrip_and_checksum(self):
        p = d.encode(d.CMD_KEY, bytes([19, 1]))
        self.assertEqual(d.decode(p), (d.CMD_KEY, bytes([19, 1])))
        bad = p[:-1] + bytes([(p[-1] + 1) & 0xFF])
        with self.assertRaises(d.ScopeError):
            d.decode(bad)
        with self.assertRaises(d.ScopeError):
            d.decode(b"\x42\0\0\0\0")


class Decoding(unittest.TestCase):
    def test_settings(self):
        s = d.Settings(make_scope().settings)
        c1 = s.channel(1)
        self.assertEqual(c1["volts_div"], 10.0)
        self.assertEqual(c1["probe"], 10)
        self.assertEqual(c1["position"], -20)
        self.assertAlmostEqual(s.channel(2)["volts_div"], 0.1)
        self.assertEqual(s.seconds_div, 200e-6)
        self.assertAlmostEqual(s.sample_interval, 1e-6)
        self.assertEqual(s.trigger["frequency_hz"], 1000.0)
        self.assertTrue(s.running)

    def test_settings_too_short(self):
        with self.assertRaises(d.ScopeError):
            d.Settings(b"\0" * 10)

    def test_volts(self):
        cs = {"volts_div": 2.0, "position": 10}
        v = d.counts_to_volts([138, 163.6], cs)
        self.assertAlmostEqual(v[0], 0.0)
        self.assertAlmostEqual(v[1], 2.0)

    def test_rgb565(self):
        raw = bytes([0x00, 0xF8, 0xE0, 0x07, 0x1F, 0x00])
        self.assertEqual(d.rgb565_to_rgb(raw), bytes([248, 0, 0, 0, 252, 0, 0, 0, 248]))

    def test_keys(self):
        self.assertEqual(d.find_key("runstop"), 19)
        self.assertEqual(d.find_key("CT-AUTOSET-KEY"), 17)
        self.assertEqual(len(d.KEYS), 49)
        with self.assertRaises(d.ScopeError):
            d.find_key("nope")

    def test_eng(self):
        self.assertEqual(d.eng(200e-6, "s"), "200us")
        self.assertEqual(d.eng(1.0, "V"), "1V")
        self.assertEqual(d.eng(1000.0, "Hz", 5), "1kHz")
        self.assertEqual(d.eng(None, "V"), "--")


class Device(unittest.TestCase):
    def test_wakes_up_despite_swallowed_commands(self):
        fake = make_scope(eat_first=3)
        dso = d.DSO5000P(fake)
        self.assertEqual(dso.echo(b"\x01\x02"), b"\x01\x02")

    def test_waveform_in_volts(self):
        dso = d.DSO5000P(make_scope())
        t, v = dso.waveform(1)
        self.assertEqual(len(v), d.SAMPLES)
        self.assertAlmostEqual(t[1], 1e-6)
        # +/- 26 counts at 10 V/div = +/- 10.156 V
        self.assertAlmostEqual(max(v), 26 / 25.6 * 10)
        self.assertAlmostEqual(min(v), -26 / 25.6 * 10)
        st = d.stats(v)
        self.assertAlmostEqual(st["mean"], 0.0)

    def test_channel_off(self):
        fake = make_scope()
        fake.settings = settings_blob(VERT_CH1_DISP=0, HORIZ_TB=15)
        dso = d.DSO5000P(fake)
        self.assertIsNone(dso.waveform(1))

    def test_screenshot_and_png(self):
        screen = struct.pack("<H", 0xF800) * (d.SCREEN_W * d.SCREEN_H)
        dso = d.DSO5000P(make_scope(screen=screen))
        rgb = dso.screenshot()
        self.assertEqual(len(rgb), d.SCREEN_W * d.SCREEN_H * 3)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.png")
            d.write_png(path, d.SCREEN_W, d.SCREEN_H, rgb)
            with open(path, "rb") as f:
                data = f.read()
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        idat_len = struct.unpack(">I", data[33:37])[0]
        pixels = zlib.decompress(data[41:41 + idat_len])
        self.assertEqual(pixels[:4], b"\x00\xf8\x00\x00")

    def test_truncated_screenshot(self):
        dso = d.DSO5000P(make_scope(screen=b"\0" * 100))
        with self.assertRaises(d.ScopeError):
            dso.screenshot()

    def test_time_file_lock_key(self):
        fake = make_scope()
        dso = d.DSO5000P(fake)
        self.assertEqual(dso.system_time(), "2018-05-06 07:08:09")
        self.assertEqual(dso.read_file("/x"), b"hello from /x")
        dso.lock_panel(True)
        dso.press(d.find_key("autoset"))
        self.assertEqual(fake.written[-1][3:6], bytes([d.CMD_KEY, 17, 1]))


class Cli(unittest.TestCase):
    def setUp(self):
        self.fake = make_scope(screen=b"\0" * (d.SCREEN_W * d.SCREEN_H * 2))
        self._orig = d.UsbTransport
        d.UsbTransport = lambda: self.fake
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        d.UsbTransport = self._orig
        self.tmp.cleanup()

    def test_capture_csv(self):
        out = os.path.join(self.tmp.name, "w.csv")
        self.assertEqual(d.main(["capture", "-o", out]), 0)
        with open(out) as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], ["time_s", "ch1_V", "ch2_V"])
        self.assertEqual(len(rows), d.SAMPLES + 1)
        self.assertTrue(self.fake.closed)

    def test_info_screenshot_key(self):
        self.assertEqual(d.main(["info", "--raw"]), 0)
        out = os.path.join(self.tmp.name, "s.png")
        self.assertEqual(d.main(["screenshot", "-o", out]), 0)
        self.assertTrue(os.path.getsize(out) > 0)
        self.assertEqual(d.main(["key", "runstop", "ch1-volts-up"]), 0)
        self.assertEqual(d.main(["keys"]), 0)
        self.assertEqual(d.main(["key", "bogus"]), 1)

    def test_unexpected_error_is_one_line(self):
        def boom():
            raise NotImplementedError("Operation not supported")
        d.UsbTransport = boom
        self.assertEqual(d.main(["info"]), 1)

    def test_plot_to_file(self):
        try:
            import matplotlib
            matplotlib.use("Agg")
        except ImportError:
            self.skipTest("matplotlib not installed")
        out = os.path.join(self.tmp.name, "p.png")
        self.assertEqual(d.main(["plot", "-c", "1", "-o", out]), 0)
        self.assertTrue(os.path.getsize(out) > 1000)


if __name__ == "__main__":
    unittest.main()
