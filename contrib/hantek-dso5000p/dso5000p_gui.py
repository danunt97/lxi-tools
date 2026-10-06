#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Grafische Oberflaeche fuer Hantek DSO5072P/5102P/5202P.

  python dso5000p_gui.py          echtes Scope per USB
  python dso5000p_gui.py --demo   simuliertes Scope zum Ausprobieren

Nur Python-Standardbibliothek (tkinter) plus pyusb. Die USB-Kommunikation
laeuft in einem eigenen Thread, damit die Oberflaeche nie haengt.
"""

import argparse
import base64
import queue
import sys
import threading
import time
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import dso5000p as d

BG = "#16181d"
PANEL = "#20232a"
FG = "#e6e6e6"
DIM = "#8a8f98"
GRID = "#3a3f4a"
GRID_MINOR = "#262a32"
COL = {1: "#f2d01e", 2: "#22c4f0"}
BTN = "#2d313a"
BTN_ACTIVE = "#3d4350"
RED = "#e05555"
GREEN = "#3fbf6f"

V_DIVS = 8
H_DIVS = d.H_DIVS


# --------------------------------------------------------------------------
# worker thread: the only place that talks to the scope

class Worker(threading.Thread):
    def __init__(self, demo, out):
        super().__init__(daemon=True)
        self.demo = demo
        self.out = out
        self.cmds = queue.Queue()
        self.stop_evt = threading.Event()
        self.mode = "live"        # "live" or "screen"
        self.paused = False
        self.dso = None

    def post(self, *msg):
        self.out.put(msg)

    def _connect(self):
        try:
            self.dso = d.DSO5000P(d.SimTransport() if self.demo else None)
            self.post("connected", "Demo (simuliertes Scope)" if self.demo else "DSO5000P per USB")
        except Exception as e:  # ScopeError, USB errors, missing pyusb ...
            self.dso = None
            self.post("disconnected", str(e))
            self.stop_evt.wait(2.0)

    def _disconnect(self):
        if self.dso is not None:
            try:
                self.dso.close()
            except Exception:
                pass
        self.dso = None

    def _run_cmd(self, cmd):
        kind = cmd[0]
        if kind == "key":
            self.dso.press(cmd[1])
        elif kind == "lock":
            self.dso.lock_panel(cmd[1])
        elif kind == "screenshot":
            path = cmd[1]
            d.write_png(path, d.SCREEN_W, d.SCREEN_H, self.dso.screenshot())
            self.post("info", "Screenshot gespeichert: %s" % path)

    def run(self):
        errors = 0
        while not self.stop_evt.is_set():
            if self.dso is None:
                self._connect()
                continue
            try:
                while True:
                    try:
                        cmd = self.cmds.get_nowait()
                    except queue.Empty:
                        break
                    self._run_cmd(cmd)
                if self.paused:
                    self.stop_evt.wait(0.05)
                    continue
                t0 = time.monotonic()
                s = self.dso.settings()
                if self.mode == "screen":
                    rgb = self.dso.screenshot()
                    self.post("screen", rgb, s)
                else:
                    waves = {}
                    for ch in (1, 2):
                        if s.channel(ch)["enabled"]:
                            waves[ch] = self.dso.samples(ch)
                    self.post("snap", s, waves, time.monotonic() - t0)
                errors = 0
                if self.demo:
                    self.stop_evt.wait(0.08)
            except d.ScopeError as e:
                errors += 1
                self.post("warn", str(e))
                if errors > 5:
                    self._disconnect()
                    self.post("disconnected", "Scope antwortet nicht mehr: %s" % e)
                else:
                    try:
                        self.dso.drain()
                    except Exception:
                        pass
            except Exception as e:  # cable pulled, USB gone
                self._disconnect()
                self.post("disconnected", "%s: %s" % (type(e).__name__, e))
        self._disconnect()

    def send(self, *cmd):
        self.cmds.put(cmd)

    def stop(self):
        self.stop_evt.set()


# --------------------------------------------------------------------------
# GUI

class App:
    def __init__(self, root, demo):
        self.root = root
        self.demo = demo
        self.q = queue.Queue()
        self.worker = Worker(demo, self.q)
        self.settings = None
        # ch -> (counts, settings at capture time). Kept while STOP, so the
        # volts are always computed with the scale they were measured at.
        self.waves = {}
        self.screen_img = None
        self.fps = 0.0
        self._last_frame = None
        self.connected = False

        root.title("Hantek DSO5102P" + (" – DEMO" if demo else ""))
        root.configure(bg=BG)
        root.minsize(1100, 640)
        self._style()
        self._build()
        self._bind_keys()

        self.worker.start()
        self.root.after(30, self._poll)
        root.protocol("WM_DELETE_WINDOW", self.close)

    # -- look --------------------------------------------------------------

    def _style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=PANEL, foreground=FG, fieldbackground=PANEL)
        st.configure("TLabelframe", background=PANEL, bordercolor=GRID)
        st.configure("TLabelframe.Label", background=PANEL, foreground=DIM,
                     font=("Segoe UI", 9, "bold"))
        st.configure("TRadiobutton", background=PANEL, foreground=FG)
        st.configure("TCheckbutton", background=PANEL, foreground=FG)
        st.map("TRadiobutton", background=[("active", PANEL)])
        st.map("TCheckbutton", background=[("active", PANEL)])

    def _btn(self, parent, text, cmd, width=7, fg=FG, bg=BTN):
        b = tk.Button(parent, text=text, command=cmd, width=width, fg=fg, bg=bg,
                      activebackground=BTN_ACTIVE, activeforeground=FG, relief="flat",
                      bd=0, highlightthickness=0, padx=4, pady=4,
                      font=("Segoe UI", 9))
        return b

    def _keybtn(self, parent, text, key, width=7, fg=FG, bg=BTN):
        code = d.find_key(key)
        return self._btn(parent, text, lambda: self.press(code), width, fg, bg)

    # -- layout ------------------------------------------------------------

    def _build(self):
        root = self.root
        # toolbar
        bar = tk.Frame(root, bg=PANEL)
        bar.pack(side="top", fill="x")
        self.mode = tk.StringVar(value="live")
        ttk.Radiobutton(bar, text="Live-Kurven", value="live", variable=self.mode,
                        command=self._mode_changed).pack(side="left", padx=(10, 4), pady=6)
        ttk.Radiobutton(bar, text="Scope-Bildschirm (Menues, ~1 Bild/s)", value="screen",
                        variable=self.mode, command=self._mode_changed).pack(side="left", padx=4)
        self.paused = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Anzeige anhalten", variable=self.paused,
                        command=lambda: setattr(self.worker, "paused", self.paused.get())
                        ).pack(side="left", padx=12)
        self.locked = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Tasten am Scope sperren", variable=self.locked,
                        command=lambda: self.worker.send("lock", self.locked.get())
                        ).pack(side="left", padx=4)
        self._btn(bar, "Screenshot speichern", self.save_screenshot, width=18).pack(side="right", padx=6, pady=4)
        self._btn(bar, "Kurven als CSV", self.save_csv, width=14).pack(side="right", padx=2, pady=4)

        # status bar
        self.status = tk.Label(root, text="Verbinde ...", anchor="w", bg=PANEL, fg=DIM,
                               font=("Segoe UI", 9))
        self.status.pack(side="bottom", fill="x")

        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True)

        # right: controls
        ctl = tk.Frame(body, bg=PANEL)
        ctl.pack(side="right", fill="y", padx=(0, 6), pady=6)
        self._build_controls(ctl)

        # left: display + soft keys + measurements
        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="both", expand=True, padx=6, pady=6)

        disp_row = tk.Frame(left, bg=BG)
        disp_row.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(disp_row, bg="#000000", highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._redraw_all())

        soft = tk.Frame(disp_row, bg=BG)
        soft.pack(side="left", fill="y", padx=(6, 0))
        tk.Label(soft, text="Menue-\ntasten", bg=BG, fg=DIM, font=("Segoe UI", 8)).pack(pady=(0, 4))
        for i in range(7):
            self._keybtn(soft, "F%d" % i, "f%d" % i, width=4).pack(fill="x", pady=3, ipady=6)

        self._build_measurements(left)

    def _group(self, parent, title):
        f = ttk.LabelFrame(parent, text=title, padding=6)
        f.pack(fill="x", padx=6, pady=4)
        return f

    def _build_controls(self, ctl):
        g = self._group(ctl, "BEDIENUNG")
        row = tk.Frame(g, bg=PANEL)
        row.pack(fill="x")
        self.run_btn = self._keybtn(row, "RUN / STOP", "runstop", width=11, bg="#2f5f3f")
        self.run_btn.grid(row=0, column=0, padx=2, pady=2)
        self._keybtn(row, "SINGLE", "single", width=8).grid(row=0, column=1, padx=2, pady=2)
        self._keybtn(row, "AUTOSET", "autoset", width=8, bg="#5a4a20").grid(row=0, column=2, padx=2, pady=2)
        self._keybtn(row, "Default", "default-setup", width=11).grid(row=1, column=0, padx=2, pady=2)
        self._keybtn(row, "Help", "help", width=8).grid(row=1, column=1, padx=2, pady=2)
        self._keybtn(row, "F7", "f7", width=8).grid(row=1, column=2, padx=2, pady=2)

        g = self._group(ctl, "MENUES")
        row = tk.Frame(g, bg=PANEL)
        row.pack(fill="x")
        for i, (t, k) in enumerate((("Save/Rec", "save-recall"), ("Measure", "measure"),
                                    ("Acquire", "acquire"), ("Utility", "utility"),
                                    ("Cursor", "cursor"), ("Display", "display"))):
            self._keybtn(row, t, k, width=8).grid(row=i // 3, column=i % 3, padx=2, pady=2)

        g = self._group(ctl, "DREHKNOPF (Multifunktion)")
        row = tk.Frame(g, bg=PANEL)
        row.pack()
        self._keybtn(row, "⟲ links", "knob-left", width=8).pack(side="left", padx=2)
        self._keybtn(row, "drücken", "knob-push", width=8).pack(side="left", padx=2)
        self._keybtn(row, "rechts ⟳", "knob-right", width=8).pack(side="left", padx=2)

        g = self._group(ctl, "VERTIKAL")
        for ch in (1, 2):
            row = tk.Frame(g, bg=PANEL)
            row.pack(fill="x", pady=2)
            self._keybtn(row, "CH%d" % ch, "ch%d" % ch, width=5, fg="#000", bg=COL[ch]).grid(row=0, column=0, rowspan=2, padx=(0, 6), sticky="ns")
            tk.Label(row, text="V/div", bg=PANEL, fg=DIM, width=6).grid(row=0, column=1)
            self._keybtn(row, "−", "ch%d-volts-down" % ch, width=3).grid(row=0, column=2, padx=1)
            self._keybtn(row, "+", "ch%d-volts-up" % ch, width=3).grid(row=0, column=3, padx=1)
            tk.Label(row, text="Position", bg=PANEL, fg=DIM, width=6).grid(row=1, column=1)
            self._keybtn(row, "▼", "ch%d-pos-down" % ch, width=3).grid(row=1, column=2, padx=1, pady=1)
            self._keybtn(row, "▲", "ch%d-pos-up" % ch, width=3).grid(row=1, column=3, padx=1, pady=1)
            self._keybtn(row, "0", "ch%d-pos-zero" % ch, width=3).grid(row=1, column=4, padx=1)
        row = tk.Frame(g, bg=PANEL)
        row.pack(fill="x", pady=(4, 0))
        self._keybtn(row, "MATH", "math", width=5).pack(side="left")

        g = self._group(ctl, "HORIZONTAL")
        row = tk.Frame(g, bg=PANEL)
        row.pack(fill="x")
        self._keybtn(row, "Menü", "horiz", width=5).grid(row=0, column=0, rowspan=2, padx=(0, 6), sticky="ns")
        tk.Label(row, text="Zeit/div", bg=PANEL, fg=DIM, width=6).grid(row=0, column=1)
        self._keybtn(row, "−", "time-down", width=3).grid(row=0, column=2, padx=1)
        self._keybtn(row, "+", "time-up", width=3).grid(row=0, column=3, padx=1)
        tk.Label(row, text="Position", bg=PANEL, fg=DIM, width=6).grid(row=1, column=1)
        self._keybtn(row, "◀", "horiz-pos-down", width=3).grid(row=1, column=2, padx=1, pady=1)
        self._keybtn(row, "▶", "horiz-pos-up", width=3).grid(row=1, column=3, padx=1, pady=1)
        self._keybtn(row, "0", "horiz-pos-zero", width=3).grid(row=1, column=4, padx=1)

        g = self._group(ctl, "TRIGGER")
        row = tk.Frame(g, bg=PANEL)
        row.pack(fill="x")
        self._keybtn(row, "Menü", "trig", width=5).grid(row=0, column=0, rowspan=2, padx=(0, 6), sticky="ns")
        tk.Label(row, text="Pegel", bg=PANEL, fg=DIM, width=6).grid(row=0, column=1)
        self._keybtn(row, "▼", "trig-level-down", width=3).grid(row=0, column=2, padx=1)
        self._keybtn(row, "▲", "trig-level-up", width=3).grid(row=0, column=3, padx=1)
        self._keybtn(row, "0", "trig-level-zero", width=3).grid(row=0, column=4, padx=1)
        self._keybtn(row, "50 %", "trig-50", width=7).grid(row=1, column=1, columnspan=2, padx=1, pady=2)
        self._keybtn(row, "Force", "force", width=7).grid(row=1, column=3, columnspan=2, padx=1, pady=2)

        tk.Label(ctl, text="Tastatur: Leertaste = Run/Stop, A = Autoset,\n"
                           "S = Single, ←/→ = Zeit/div, ↑/↓ = CH1 V/div",
                 bg=PANEL, fg=DIM, font=("Segoe UI", 8), justify="left").pack(padx=6, pady=(8, 6), anchor="w")

    def _build_measurements(self, parent):
        f = tk.Frame(parent, bg=PANEL)
        f.pack(fill="x", pady=(6, 0))
        heads = ["", "V/div", "Kopplung", "Tastkopf", "Vpp", "Min", "Max", "Mittel", "RMS", "Frequenz"]
        for c, h in enumerate(heads):
            tk.Label(f, text=h, bg=PANEL, fg=DIM, font=("Segoe UI", 9)).grid(row=0, column=c, padx=6, pady=(4, 0), sticky="e" if c else "w")
        self.meas = {}
        for r, ch in enumerate((1, 2), start=1):
            tk.Label(f, text="CH%d" % ch, bg=PANEL, fg=COL[ch],
                     font=("Segoe UI", 10, "bold")).grid(row=r, column=0, padx=6, sticky="w")
            cells = []
            for c in range(1, len(heads)):
                lb = tk.Label(f, text="--", bg=PANEL, fg=FG, width=9, anchor="e",
                              font=("Consolas", 10))
                lb.grid(row=r, column=c, padx=4, pady=1)
                cells.append(lb)
            self.meas[ch] = cells
        info = tk.Frame(parent, bg=PANEL)
        info.pack(fill="x")
        self.info = tk.Label(info, text="", bg=PANEL, fg=FG, anchor="w", font=("Segoe UI", 10))
        self.info.pack(fill="x", padx=6, pady=(2, 6))

    def _bind_keys(self):
        r = self.root
        r.bind("<space>", lambda e: self.press(d.find_key("runstop")))
        r.bind("a", lambda e: self.press(d.find_key("autoset")))
        r.bind("s", lambda e: self.press(d.find_key("single")))
        r.bind("<Left>", lambda e: self.press(d.find_key("time-down")))
        r.bind("<Right>", lambda e: self.press(d.find_key("time-up")))
        r.bind("<Up>", lambda e: self.press(d.find_key("ch1-volts-up")))
        r.bind("<Down>", lambda e: self.press(d.find_key("ch1-volts-down")))

    # -- actions -----------------------------------------------------------

    def press(self, code):
        if not self.connected:
            self._status("Nicht verbunden – Taste ignoriert", RED)
            return
        self.worker.send("key", code)

    def _mode_changed(self):
        self.worker.mode = self.mode.get()
        self._redraw_all()

    def save_csv(self):
        if not self.settings or not self.waves:
            messagebox.showinfo("Kurven als CSV", "Noch keine Kurvendaten vorhanden.")
            return
        path = filedialog.asksaveasfilename(
            title="Kurven als CSV speichern", defaultextension=".csv",
            initialfile="dso-%s.csv" % d.stamp(), filetypes=[("CSV", "*.csv")])
        if not path:
            return
        waves = {}
        for ch, (counts, ws) in self.waves.items():
            dt = ws.sample_interval or 0.0
            volts = d.counts_to_volts(counts, ws.channel(ch))
            waves[ch] = ([i * dt for i in range(len(volts))], volts)
        n = d.write_csv(path, waves)
        self._status("%d Punkte gespeichert: %s" % (n, path), GREEN)

    def save_screenshot(self):
        if not self.connected:
            return
        path = filedialog.asksaveasfilename(
            title="Screenshot des Scopes speichern", defaultextension=".png",
            initialfile="dso-%s.png" % d.stamp(), filetypes=[("PNG", "*.png")])
        if path:
            self._status("Screenshot wird geladen ...", FG)
            self.worker.send("screenshot", path)

    def close(self):
        if self.locked.get() and self.connected:
            self.worker.send("lock", False)
            time.sleep(0.3)
        self.worker.stop()
        self.worker.join(timeout=3)
        self.root.destroy()

    # -- incoming data -----------------------------------------------------

    def _status(self, text, color=DIM):
        self.status.configure(text=text, fg=color)

    def _poll(self):
        latest = None
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind in ("snap", "screen"):
                    latest = msg
                elif kind == "connected":
                    self.connected = True
                    self._status("Verbunden: %s" % msg[1], GREEN)
                    self._redraw_all()
                    if self.locked.get():
                        self.worker.send("lock", True)
                elif kind == "disconnected":
                    self.connected = False
                    self._status("Nicht verbunden: %s  (neuer Versuch ...)" % msg[1].replace("\n", " "), RED)
                    self._redraw_all()
                elif kind == "warn":
                    self._status("Hinweis: %s" % msg[1], "#d9a441")
                elif kind == "info":
                    self._status(msg[1], GREEN)
        except queue.Empty:
            pass
        if latest:
            now = time.monotonic()
            if self._last_frame:
                self.fps = 0.8 * self.fps + 0.2 / max(1e-3, now - self._last_frame)
            self._last_frame = now
            if latest[0] == "snap":
                _, s, waves, _dur = latest
                self.settings = s
                for ch in (1, 2):
                    if not s.channel(ch)["enabled"]:
                        self.waves.pop(ch, None)
                    elif waves.get(ch):
                        self.waves[ch] = (waves[ch], s)
                if self.mode.get() == "live":
                    self._draw_traces()
            else:
                _, rgb, s = latest
                self.settings = s
                if self.mode.get() == "screen":
                    self._show_screen(rgb)
            self._update_info()
        self.root.after(30, self._poll)

    # -- drawing -----------------------------------------------------------

    def _plot_rect(self):
        w = max(100, self.canvas.winfo_width())
        h = max(100, self.canvas.winfo_height())
        # keep divisions square: H_DIVS x V_DIVS
        div = min((w - 20) / H_DIVS, (h - 40) / V_DIVS)
        pw, ph = div * H_DIVS, div * V_DIVS
        x0 = (w - pw) / 2
        y0 = 28 + (h - 28 - ph) / 2
        return x0, y0, pw, ph

    def _redraw_all(self):
        self.canvas.delete("all")
        self.screen_img = None
        if self.mode.get() == "live":
            self._draw_grid()
            self._draw_traces()
        elif not self.connected:
            self.canvas.create_text(self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2,
                                    text="Kein Scope verbunden", fill=DIM, font=("Segoe UI", 14))

    def _draw_grid(self):
        c = self.canvas
        x0, y0, pw, ph = self._plot_rect()
        for i in range(H_DIVS + 1):
            x = x0 + i * pw / H_DIVS
            c.create_line(x, y0, x, y0 + ph, fill=GRID if i in (0, H_DIVS, H_DIVS // 2) else GRID_MINOR, tags="grid")
        for j in range(V_DIVS + 1):
            y = y0 + j * ph / V_DIVS
            c.create_line(x0, y, x0 + pw, y, fill=GRID if j in (0, V_DIVS, V_DIVS // 2) else GRID_MINOR, tags="grid")
        # centre ticks
        for i in range(H_DIVS * 5 + 1):
            x = x0 + i * pw / (H_DIVS * 5)
            c.create_line(x, y0 + ph / 2 - 3, x, y0 + ph / 2 + 3, fill=GRID, tags="grid")
        for j in range(V_DIVS * 5 + 1):
            y = y0 + j * ph / (V_DIVS * 5)
            c.create_line(x0 + pw / 2 - 3, y, x0 + pw / 2 + 3, y, fill=GRID, tags="grid")
        if not self.connected:
            c.create_text(x0 + pw / 2, y0 + ph / 2 - 30, text="Kein Scope verbunden",
                          fill=DIM, font=("Segoe UI", 14), tags="grid")

    def _draw_traces(self):
        c = self.canvas
        c.delete("trace")
        if not c.find_withtag("grid"):
            self._draw_grid()
        x0, y0, pw, ph = self._plot_rect()
        px_per_count = ph / V_DIVS / d.COUNTS_PER_DIV
        yc = y0 + ph / 2
        s = self.settings
        for ch in (2, 1):
            if ch not in self.waves:
                continue
            counts = self.waves[ch][0]
            n = len(counts)
            cols = max(2, int(pw))
            pts = []
            if n > 2 * cols:
                # min/max per pixel column so short spikes stay visible
                step = n / cols
                for xi in range(cols):
                    seg = counts[int(xi * step):max(int(xi * step) + 1, int((xi + 1) * step))]
                    x = x0 + xi
                    lo, hi = min(seg), max(seg)
                    pts += [x, yc - (lo - d.CENTRE_COUNT) * px_per_count,
                            x, yc - (hi - d.CENTRE_COUNT) * px_per_count]
            else:
                for i, v in enumerate(counts):
                    pts += [x0 + i * pw / (n - 1), yc - (v - d.CENTRE_COUNT) * px_per_count]
            # clamp to plot area
            pts = [min(max(p, y0), y0 + ph) if k % 2 else p for k, p in enumerate(pts)]
            c.create_line(*pts, fill=COL[ch], width=1, tags="trace")
            if s:
                pos = self.waves[ch][1].channel(ch)["position"]
                gy = min(max(yc - pos * px_per_count, y0), y0 + ph)
                c.create_polygon(x0 - 12, gy - 6, x0 - 2, gy, x0 - 12, gy + 6, fill=COL[ch], tags="trace")
                c.create_text(x0 - 16, gy, text=str(ch), fill=COL[ch], anchor="e",
                              font=("Segoe UI", 9, "bold"), tags="trace")
        # header text inside canvas
        if s:
            parts = []
            for ch in (1, 2):
                cs = s.channel(ch)
                if cs["enabled"]:
                    parts.append(("CH%d %s/div %s" % (ch, d.eng(cs["volts_div"], "V"), cs["coupling"] or ""), COL[ch]))
            parts.append(("%s/div" % d.eng(s.seconds_div, "s"), FG))
            t = s.trigger
            parts.append(("Trig %s %s %s" % (t["source"] or "?", "↑" if t["slope"] == "Rising" else "↓", t["mode"] or ""), FG))
            parts.append(("RUN" if s.running else "STOP", GREEN if s.running else RED))
            x = x0
            for text, col in parts:
                item = c.create_text(x, 12, text=text, fill=col, anchor="w",
                                     font=("Segoe UI", 10, "bold"), tags="trace")
                bbox = c.bbox(item)
                x = (bbox[2] if bbox else x + 100) + 18

    def _show_screen(self, rgb):
        c = self.canvas
        png = d.png_bytes(d.SCREEN_W, d.SCREEN_H, rgb, level=1)
        img = tk.PhotoImage(data=base64.b64encode(png).decode("ascii"), format="png")
        w, h = c.winfo_width(), c.winfo_height()
        if w < d.SCREEN_W // 1.2 or h < d.SCREEN_H // 1.2:
            img = img.subsample(2, 2)
        self.screen_img = img
        c.delete("all")
        c.create_image(w / 2, h / 2, image=img)

    def _update_info(self):
        s = self.settings
        if not s:
            return
        dt = s.sample_interval
        for ch in (1, 2):
            cells = self.meas[ch]
            cs = s.channel(ch)
            counts, ws = self.waves.get(ch, (None, None)) if cs["enabled"] else (None, None)
            vals = [d.eng(cs["volts_div"], "V"), cs["coupling"] or "?", "x%s" % cs["probe"]]
            if counts:
                v = d.counts_to_volts(counts, ws.channel(ch))
                st = d.stats(v)
                f = d.frequency(v, ws.sample_interval)
                vals += [d.eng(st["pkpk"], "V"), d.eng(st["min"], "V"), d.eng(st["max"], "V"),
                         d.eng(st["mean"], "V"), d.eng(st["rms"], "V"), d.eng(f, "Hz", 4)]
                clip = d.clipped(counts)
            else:
                vals += ["--"] * 6
                clip = False
            if not cs["enabled"]:
                vals = ["aus"] + ["--"] * 8
            for lb, val in zip(cells, vals):
                lb.configure(text=val, fg=FG)
            if clip:
                cells[3].configure(fg=RED)
        t = s.trigger
        txt = "Zeitbasis %s/div   Abtastabstand %s   Trigger-Zähler %s   Erfassung %s   %s   %.1f Bilder/s" % (
            d.eng(s.seconds_div, "s"), d.eng(dt, "s"), d.eng(t["frequency_hz"] or None, "Hz", 5),
            s.acquire_mode or "?", "RUN" if s.running else "STOP", self.fps)
        if any(d.clipped(c) for c, _ in self.waves.values()):
            txt += "   ⚠ Signal übersteuert (rot) – V/div erhöhen"
        self.info.configure(text=txt)
        self.run_btn.configure(bg="#2f5f3f" if s.running else "#6a2a2a",
                               text="RUN / STOP  ●" if s.running else "RUN / STOP  ■")


def main(argv=None):
    p = argparse.ArgumentParser(description="GUI fuer Hantek DSO5072P/5102P/5202P")
    p.add_argument("--demo", action="store_true", help="simuliertes Scope")
    args = p.parse_args(argv)
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", max(1.0, root.winfo_fpixels("1i") / 72.0))
    except tk.TclError:
        pass
    App(root, args.demo)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # under pythonw there is no console, so show the error in a box
        msg = traceback.format_exc()
        try:
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror("DSO5102P – Fehler", msg)
        except Exception:
            print(msg, file=sys.stderr)
        sys.exit(1)
