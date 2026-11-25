"""seqgen_gui.py - graphical programmer for the 16-channel digital sequence generator.

    python seqgen_gui.py          (port "EMU" = built-in emulator, no hardware needed)

Waveform editor (selected channel):
    left button drag   draw HIGH (1)          right button drag   draw LOW (0)
    Shift + drag       select a range         Esc                 clear selection
    mouse wheel        scroll                 Ctrl + wheel        zoom
    Ctrl+Z             undo                   Ctrl+A              select all
Overview (bottom): all 16 channels on a common time axis - click a lane to edit it.
"""
import queue
from copy import deepcopy
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import patterns
from seqgen import (SeqGen, Project, NCH, MAXLEN, PERIOD_MIN_MS, PERIOD_MAX_MS,
                    DeviceError, parse_levels, format_levels, fmt_time, split_time, TIME_UNITS,
                    VOUT_LEVELS, vout_text)

HIGH_FILL = "#c8ecc8"
LINE = "#1a7f37"
SEL_FILL = "#7aa7ff"
PLAYHEAD = "#d62728"
GRID = "#e6e6e6"
GRID_MAJOR = "#c4c4c4"


class TimeField:
    """Entry + unit selector (µs / ms / s). Value is kept in milliseconds."""

    def __init__(self, master, width=8, on_change=None):
        self.var = tk.StringVar()
        self.unit = tk.StringVar(value="ms")
        self.entry = ttk.Entry(master, textvariable=self.var, width=width)
        self.combo = ttk.Combobox(master, textvariable=self.unit, values=tuple(TIME_UNITS), width=3,
                                  state="readonly")
        if on_change:
            self.entry.bind("<Return>", lambda e: on_change())
            self.entry.bind("<FocusOut>", lambda e: on_change())
            self.combo.bind("<<ComboboxSelected>>", lambda e: on_change())

    def grid(self, row, column, **kw):
        self.entry.grid(row=row, column=column, sticky="w", **kw)
        self.combo.grid(row=row, column=column + 1, sticky="w")

    def set_ms(self, ms):
        v, u = split_time(ms)
        self.var.set(f"{v:g}")
        self.unit.set(u)

    def get_ms(self) -> float:
        """Value in ms; raises ValueError for a bad number."""
        try:
            v = float(self.var.get().replace(",", ".").strip())
        except ValueError:
            raise ValueError(f"'{self.var.get()}' is not a number") from None
        return round(v * TIME_UNITS[self.unit.get()], 6)


def runs(steps):
    """Yield (start, end_exclusive, level) for runs of equal levels."""
    if not steps:
        return
    s = 0
    for i in range(1, len(steps) + 1):
        if i == len(steps) or steps[i] != steps[s]:
            yield s, i, steps[s]
            s = i


# =============================================================================== waveform editor
class WaveEditor(ttk.Frame):
    LEFT = 12
    TOP = 26
    LANE_H = 110

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.ch = None
        self.cell_w = 8.0
        self.sel = None             # (a, b) inclusive, a <= b
        self.playhead = None
        self._drag = None           # ("paint", level, last_idx) or ("select", anchor)

        self.canvas = tk.Canvas(self, bg="white", height=self.TOP + self.LANE_H + 30,
                                highlightthickness=1, highlightbackground="#bbb")
        self.hbar = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=self.hbar.set)
        self.canvas.pack(fill="both", expand=True)
        self.hbar.pack(fill="x")

        c = self.canvas
        c.bind("<Configure>", lambda e: self.redraw())
        c.bind("<ButtonPress-1>", lambda e: self._press(e, 1))
        c.bind("<ButtonPress-3>", lambda e: self._press(e, 0))
        c.bind("<Shift-ButtonPress-1>", self._sel_press)
        c.bind("<B1-Motion>", self._motion_drag)
        c.bind("<B3-Motion>", self._motion_drag)
        c.bind("<ButtonRelease-1>", self._release)
        c.bind("<ButtonRelease-3>", self._release)
        c.bind("<Motion>", self._hover)
        c.bind("<Leave>", lambda e: self.app.set_cursor_info(""))
        c.bind("<MouseWheel>", self._wheel)
        c.bind("<Control-MouseWheel>", self._ctrl_wheel)

    # ---------------------------------------------------------------- geometry
    @property
    def n(self):
        return len(self.ch.steps) if self.ch else 0

    def _idx(self, event, clamp=True):
        x = self.canvas.canvasx(event.x)
        i = int((x - self.LEFT) // self.cell_w)
        if clamp:
            i = max(0, min(self.n - 1, i))
        return i

    def _x(self, i):
        return self.LEFT + i * self.cell_w

    def fit(self):
        w = max(self.canvas.winfo_width(), 200)
        self.cell_w = max(0.5, (w - 2 * self.LEFT) / max(self.n, 1))
        self.canvas.xview_moveto(0)
        self.redraw()

    def zoom(self, factor, anchor_idx=None):
        if not self.n:
            return
        w = max(self.canvas.winfo_width(), 200)
        min_w = (w - 2 * self.LEFT) / self.n
        if anchor_idx is None:
            anchor_idx = int((self.canvas.canvasx(w / 2) - self.LEFT) / self.cell_w)
        self.cell_w = max(min(min_w, 40.0), min(40.0, self.cell_w * factor))
        self.redraw()
        total = 2 * self.LEFT + self.n * self.cell_w
        self.canvas.xview_moveto(max(0.0, (self._x(anchor_idx) - w / 2) / total))

    # ---------------------------------------------------------------- drawing
    def set_channel(self, ch):
        self.ch = ch
        self.sel = None
        self.playhead = None
        self.fit()

    def redraw(self):
        c = self.canvas
        c.delete("all")
        if not self.ch:
            return
        n, cw = self.n, self.cell_w
        h_top = self.TOP + 12
        h_bot = self.TOP + self.LANE_H
        right = self._x(n)
        c.configure(scrollregion=(0, 0, right + self.LEFT, h_bot + 30))

        # visible range only
        x0 = c.canvasx(0)
        x1 = c.canvasx(max(c.winfo_width(), 200))
        i0 = max(0, int((x0 - self.LEFT) // cw) - 1)
        i1 = min(n, int((x1 - self.LEFT) // cw) + 2)

        # grid + axis labels
        label_every = next(k for k in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1024) if k * cw >= 64)
        if cw >= 5:
            for i in range(i0, i1 + 1):
                c.create_line(self._x(i), h_top, self._x(i), h_bot, fill=GRID)
        for i in range((i0 // label_every) * label_every, i1 + 1, label_every):
            x = self._x(i)
            c.create_line(x, self.TOP - 4, x, h_bot + 4, fill=GRID_MAJOR)
            c.create_text(x + 2, 2, anchor="nw", text=str(i), font=("Segoe UI", 8))
            c.create_text(x + 2, 13, anchor="nw", text=fmt_time(i * self.ch.period_ms),
                          font=("Segoe UI", 7), fill="#777")
        c.create_text(4, h_top, anchor="w", text="1", font=("Segoe UI", 7), fill="#777")
        c.create_text(4, h_bot, anchor="w", text="0", font=("Segoe UI", 7), fill="#777")

        # selection
        if self.sel:
            a, b = self.sel
            c.create_rectangle(self._x(a), self.TOP - 2, self._x(b + 1), h_bot + 6,
                               fill=SEL_FILL, stipple="gray25", outline=SEL_FILL)

        # waveform (run-length)
        pts = []
        for a, b, v in runs(self.ch.steps):
            if b < i0 or a > i1:
                continue
            y = h_top if v else h_bot
            if v:
                c.create_rectangle(self._x(a), h_top, self._x(b), h_bot, fill=HIGH_FILL, outline="")
            pts += [self._x(a), y, self._x(b), y]
        if len(pts) >= 4:
            c.create_line(*pts, fill=LINE, width=2)

        # end of sequence + idle level after it
        c.create_line(right, self.TOP - 4, right, h_bot + 16, fill="#555", dash=(3, 2))
        end_txt = "loop" if self.ch.loops == 0 else (
            f"x{self.ch.loops}, then " + ("idle=" + str(self.ch.idle) if self.ch.idle_on_done else "hold"))
        c.create_text(right - 3, h_bot + 16, anchor="e", text=f"end ({end_txt})", font=("Segoe UI", 8), fill="#555")

        # playhead
        if self.playhead is not None and 0 <= self.playhead < n:
            x = self._x(self.playhead + 0.5)
            c.create_line(x, self.TOP - 4, x, h_bot + 6, fill=PLAYHEAD, width=2)

    # ---------------------------------------------------------------- mouse
    def _press(self, e, level):
        if not self.ch:
            return
        self.canvas.focus_set()
        i = self._idx(e)
        self.app.push_undo()
        self.sel = None
        self._drag = ("paint", level, i)
        self.ch.steps[i] = level
        self.redraw()

    def _sel_press(self, e):
        if not self.ch:
            return
        i = self._idx(e)
        self._drag = ("select", i)
        self.sel = (i, i)
        self.redraw()
        return "break"

    def _motion_drag(self, e):
        if not self._drag or not self.ch:
            return
        i = self._idx(e)
        if self._drag[0] == "paint":
            _, level, last = self._drag
            a, b = sorted((last, i))
            for k in range(a, b + 1):         # fill gaps when the mouse moves fast
                self.ch.steps[k] = level
            self._drag = ("paint", level, i)
        else:
            a = self._drag[1]
            self.sel = (min(a, i), max(a, i))
        self._hover(e)
        self.redraw()

    def _release(self, e):
        if self._drag and self._drag[0] == "paint":
            self.app.channel_changed()
        self._drag = None
        self.app.update_selection_info()

    def _hover(self, e):
        if not self.ch:
            return
        i = self._idx(e, clamp=False)
        if 0 <= i < self.n:
            self.app.set_cursor_info(f"step {i}   t = {fmt_time(i * self.ch.period_ms)}   level {self.ch.steps[i]}")
        else:
            self.app.set_cursor_info("")

    def _wheel(self, e):
        self.canvas.xview_scroll(-1 if e.delta > 0 else 1, "units")
        self.redraw()

    def _ctrl_wheel(self, e):
        self.zoom(1.25 if e.delta > 0 else 0.8, self._idx(e))


# =============================================================================== overview
class Overview(tk.Canvas):
    LABEL_W = 96
    ROW_H = 18

    def __init__(self, master, app):
        super().__init__(master, bg="white", height=(NCH + 2) * self.ROW_H + 26,
                         highlightthickness=1, highlightbackground="#bbb")
        self.app = app
        self.outputs = 0
        self.running = 0
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Button-1>", self._click)

    def redraw(self):
        self.delete("all")
        prj, sel = self.app.prj, self.app.cur
        w = max(self.winfo_width(), 300)
        plot_w = w - self.LABEL_W - 10
        shown = [c for c in prj.channels if c.enabled] or prj.channels
        total = max(c.duration_ms for c in shown)
        scale = plot_w / total
        for k in range(0, 11):
            x = self.LABEL_W + plot_w * k / 10
            self.create_line(x, 0, x, (NCH + 2) * self.ROW_H + 4, fill=GRID)
            self.create_text(x, (NCH + 2) * self.ROW_H + 7, anchor="n", text=fmt_time(round(total * k / 10, 3)),
                             font=("Segoe UI", 7), fill="#666")
        for c in prj.channels:
            y0 = c.ch * self.ROW_H
            if c.ch == sel:
                self.create_rectangle(0, y0, w, y0 + self.ROW_H, fill="#eef3ff", outline="")
            led = "#2ecc40" if self.outputs >> c.ch & 1 else "#ddd"
            self.create_oval(4, y0 + 5, 12, y0 + 13, fill=led, outline="#888")
            col = "#000" if c.enabled else "#aaa"
            run = " ▶" if self.running >> c.ch & 1 else ""
            self.create_text(16, y0 + self.ROW_H / 2, anchor="w", text=f"{c.ch:2} {c.name[:9]}{run}",
                             font=("Consolas", 8), fill=col)
            yh, yl = y0 + 4, y0 + self.ROW_H - 4
            pts = []
            for a, b, v in runs(c.steps):
                xa = self.LABEL_W + a * c.period_ms * scale
                xb = self.LABEL_W + b * c.period_ms * scale
                if xa > w:
                    break
                pts += [xa, yh if v else yl, min(xb, w), yh if v else yl]
            if len(pts) >= 4:
                self.create_line(*pts, fill=LINE if c.enabled else "#bbb", width=1)
        self._aux_lanes(prj, w, scale, total)

    def _aux_lanes(self, prj, w, scale, total):
        """BASE_CLK and SYNC as they will look after "Start enabled" (t = 0 = trigger)."""
        y_sep = NCH * self.ROW_H + 2
        self.create_line(0, y_sep, w, y_sep, fill="#999")
        en = [c for c in prj.channels if c.enabled]
        if en and all(c.loops for c in en):
            run_end = max(c.duration_ms * c.loops for c in en)  # all channels finish
        else:
            run_end = total                                     # some channel loops forever
        sync_end = prj.sync_width_ms if prj.sync_width_ms > 0 else run_end
        # BASE_CLK runs only while any channel is running (0 when idle)
        lanes = (("CLK", f"{fmt_time(prj.clock_period_ms)}", run_end), ("SYNC", "", sync_end))
        for k, (name, info, end) in enumerate(lanes):
            is_clk = k == 0
            y0 = y_sep + 2 + k * self.ROW_H
            self.create_text(16, y0 + self.ROW_H / 2, anchor="w", text=f"{name:<4} {info}",
                             font=("Consolas", 8), fill="#7a3d00")
            yh, yl = y0 + 4, y0 + self.ROW_H - 4
            x0, xe = self.LABEL_W, self.LABEL_W + total * scale
            if is_clk:                                          # 50 % duty from t = 0 until run end
                xr = min(x0 + end * scale, xe)
                half = prj.clock_period_ms / 2 * scale
                if half < 1.5:                                  # too dense to draw
                    self.create_rectangle(x0, yh, xr, yl, fill="#f3d9c0", outline="")
                    self.create_line(xr, yl, xe, yl, fill="#c05a00")
                    continue
                pts, x, v = [], x0, 1
                while x < xr:
                    y = yh if v else yl
                    pts += [x, y, min(x + half, xr), y]
                    x, v = x + half, 1 - v
                pts += [xr, yl, xe, yl]
            else:
                xs = min(x0 + end * scale, xe)
                pts = [x0, yh, xs, yh, xs, yl, xe, yl]
            self.create_line(*pts, fill="#c05a00", width=1)

    def _click(self, e):
        ch = int(e.y // self.ROW_H)
        if 0 <= ch < NCH:
            self.app.select_channel(ch)


# =============================================================================== application
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SeqGen16 - Digital Sequence Programmer")
        self.geometry("1280x920")
        self.minsize(1000, 700)
        self.prj = Project()
        self.prj.channels[0].enabled = True
        self.dev = None
        self.dev_lock = threading.Lock()
        self.ui_q = queue.Queue()
        self.cur = 0
        self.undo = []
        self.clipboard_steps = None
        self.project_path = None
        self._loading = False
        self._build()
        self._load_aux()
        self.select_channel(0)
        self.after(50, self._pump)
        self.after(300, self._poll_status)

    # ------------------------------------------------------------------ thread-safe UI
    def _pump(self):
        while True:
            try:
                fn, args = self.ui_q.get_nowait()
            except queue.Empty:
                break
            fn(*args)
        self.after(50, self._pump)

    def _ui(self, fn, *args):
        self.ui_q.put((fn, args))

    # ------------------------------------------------------------------ layout
    def _build(self):
        # --- top bar: connection + project
        top = ttk.Frame(self, padding=(6, 4))
        top.pack(fill="x")
        ttk.Label(top, text="Port:").pack(side="left")
        self.port = ttk.Combobox(top, width=10, values=self._ports())
        self.port.set("EMU")
        self.port.pack(side="left")
        ttk.Button(top, text="⟳", width=3, command=lambda: self.port.configure(values=self._ports())).pack(side="left")
        self.btn_conn = ttk.Button(top, text="Connect", command=self._connect)
        self.btn_conn.pack(side="left", padx=4)
        self.lbl_dev = ttk.Label(top, text="not connected", foreground="gray")
        self.lbl_dev.pack(side="left", padx=8)
        for txt, cmd in (("Save As…", lambda: self._save(True)), ("Save", self._save),
                         ("Open…", self._open), ("New", self._new)):
            ttk.Button(top, text=txt, command=cmd).pack(side="right", padx=2)

        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=6)

        # --- left: channel table
        left = ttk.Frame(body)
        cols = ("name", "en", "step", "len", "rep", "state")
        self.tree = ttk.Treeview(left, columns=cols, height=NCH, selectmode="browse", show="headings")
        for col, txt, w in zip(cols, ("Name", "On", "Step", "Steps", "Repeat", "State"), (80, 34, 62, 50, 52, 70)):
            self.tree.heading(col, text=txt)
            self.tree.column(col, width=w, anchor="center", stretch=False)
        self.tree.pack(fill="y", expand=False)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        ttk.Label(left, text="Click the 'On' cell to enable / disable a channel", foreground="#888").pack(anchor="w", pady=2)
        self.tree.bind("<Button-1>", self._tree_click)
        bb = ttk.Frame(left)
        bb.pack(fill="x")
        ttk.Button(bb, text="Enable all", command=lambda: self._enable_all(True)).pack(side="left")
        ttk.Button(bb, text="Disable all", command=lambda: self._enable_all(False)).pack(side="left", padx=4)

        aux = ttk.LabelFrame(left, text="Sync outputs (J8)", padding=6)
        aux.pack(fill="x", pady=(10, 0))
        ttk.Label(aux, text="BASE_CLK period").grid(row=0, column=0, sticky="w")
        self.f_clk = TimeField(aux, width=8, on_change=self._apply_aux)
        self.f_clk.grid(row=0, column=1, padx=4)
        ttk.Label(aux, text="SYNC pulse width").grid(row=1, column=0, sticky="w")
        self.f_syncw = TimeField(aux, width=8, on_change=self._apply_aux)
        self.f_syncw.grid(row=1, column=1, padx=4)
        ttk.Label(aux, text="SYNC 0 = high while any channel runs.\nBASE_CLK runs only while channels run.\n"
                            "Both restart on every START; their edges\ncoincide with the channel output edges.",
                  foreground="#888", justify="left").grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))

        lvl = ttk.LabelFrame(left, text="Output level", padding=6)
        lvl.pack(fill="x", pady=(10, 0))
        ttk.Label(lvl, text="All outputs").grid(row=0, column=0, sticky="w")
        self.v_vout = tk.StringVar()
        cb = ttk.Combobox(lvl, textvariable=self.v_vout, state="readonly", width=8,
                          values=[vout_text(v) for v in VOUT_LEVELS])
        cb.grid(row=0, column=1, padx=4)
        cb.bind("<<ComboboxSelected>>", self._apply_vout)
        ttk.Label(lvl, text="CH0..15, BASE_CLK and SYNC. Off = high impedance\n(state after power-up). "
                            "A change switches the\noutputs off for 2.5 ms. Sent on upload / here.",
                  foreground="#888", justify="left").grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        body.add(left, weight=0)

        # --- right: settings, editor, tools, overview
        right = ttk.Frame(body, padding=(6, 0))
        body.add(right, weight=1)

        st = ttk.LabelFrame(right, text="Channel", padding=4)
        st.pack(fill="x")
        self.v_name = tk.StringVar()
        self.v_en = tk.BooleanVar()
        self.v_len = tk.StringVar()
        self.v_loops = tk.StringVar()
        self.v_idle = tk.StringVar()
        self.v_end = tk.StringVar()
        self.lbl_ch = ttk.Label(st, text="", font=("Segoe UI", 11, "bold"), width=6)
        self.lbl_ch.grid(row=0, column=0, rowspan=2, padx=(0, 8))
        ttk.Label(st, text="Name").grid(row=0, column=1, sticky="e")
        e_name = ttk.Entry(st, textvariable=self.v_name, width=12)
        e_name.grid(row=0, column=2, sticky="w", padx=2)
        ttk.Checkbutton(st, text="Enabled", variable=self.v_en, command=self._apply).grid(row=1, column=2, sticky="w")
        ttk.Label(st, text="Step").grid(row=0, column=3, sticky="e")
        stepf = ttk.Frame(st)
        stepf.grid(row=0, column=4, sticky="w", padx=2)
        self.f_per = TimeField(stepf, width=7, on_change=self._apply)
        self.f_per.grid(row=0, column=0)
        ttk.Label(st, text="Length [steps]").grid(row=1, column=3, sticky="e")
        e_len = ttk.Spinbox(st, textvariable=self.v_len, from_=1, to=MAXLEN, width=7, command=self._apply)
        e_len.grid(row=1, column=4, sticky="w", padx=2)
        ttk.Label(st, text="Repeat (0 = forever)").grid(row=0, column=5, sticky="e")
        e_loops = ttk.Spinbox(st, textvariable=self.v_loops, from_=0, to=65535, width=7, command=self._apply)
        e_loops.grid(row=0, column=6, sticky="w", padx=2)
        ttk.Label(st, text="Idle level").grid(row=1, column=5, sticky="e")
        cb_idle = ttk.Combobox(st, textvariable=self.v_idle, values=("0", "1"), width=4, state="readonly")
        cb_idle.grid(row=1, column=6, sticky="w", padx=2)
        ttk.Label(st, text="After last repeat").grid(row=0, column=7, sticky="e")
        cb_end = ttk.Combobox(st, textvariable=self.v_end, values=("hold last step", "go to idle level"),
                              width=15, state="readonly")
        cb_end.grid(row=0, column=8, sticky="w", padx=2)
        self.lbl_dur = ttk.Label(st, text="", foreground="#555")
        self.lbl_dur.grid(row=1, column=7, columnspan=2, sticky="w", padx=4)
        for w in (e_name, e_len, e_loops):
            w.bind("<Return>", lambda e: self._apply())
            w.bind("<FocusOut>", lambda e: self._apply())
        for w in (cb_idle, cb_end):
            w.bind("<<ComboboxSelected>>", lambda e: self._apply())

        # editor toolbar
        tb = ttk.Frame(right, padding=(0, 4))
        tb.pack(fill="x")
        for txt, cmd in (("Undo", self._undo), ("Zoom +", lambda: self.editor.zoom(2)),
                         ("Zoom −", lambda: self.editor.zoom(0.5)), ("Fit", lambda: self.editor.fit()),
                         ("Select all", self._select_all)):
            ttk.Button(tb, text=txt, command=cmd, width=len(txt) + 1).pack(side="left", padx=1)
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=6)
        for txt, cmd in (("Set 1", lambda: self._fill(1)), ("Set 0", lambda: self._fill(0)),
                         ("Invert", self._invert), ("◀ Shift", lambda: self._rotate(-1)),
                         ("Shift ▶", lambda: self._rotate(1))):
            ttk.Button(tb, text=txt, command=cmd, width=len(txt) + 1).pack(side="left", padx=1)
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=6)
        for txt, cmd in (("Copy", self._copy), ("Paste", self._paste), ("Copy to channels…", self._copy_to)):
            ttk.Button(tb, text=txt, command=cmd, width=len(txt) + 1).pack(side="left", padx=1)

        self.editor = WaveEditor(right, self)
        self.editor.pack(fill="x")
        info = ttk.Frame(right)
        info.pack(fill="x")
        self.lbl_cursor = ttk.Label(info, text="", foreground="#555")
        self.lbl_cursor.pack(side="left")
        self.lbl_sel = ttk.Label(info, text="", foreground="#335")
        self.lbl_sel.pack(side="right")

        # generators
        gen = ttk.LabelFrame(right, text="Generate (applies to selection, or to the whole sequence)", padding=4)
        gen.pack(fill="x", pady=(2, 4))
        self.g_high = tk.IntVar(value=1)
        self.g_low = tk.IntVar(value=1)
        self.g_phase = tk.IntVar(value=0)
        self.g_pstart = tk.IntVar(value=0)
        self.g_pwidth = tk.IntVar(value=1)
        self.g_dens = tk.DoubleVar(value=0.5)
        r = 0
        for lbl, var in (("High", self.g_high), ("Low", self.g_low), ("Phase", self.g_phase)):
            ttk.Label(gen, text=lbl).grid(row=0, column=r, padx=(6, 1))
            ttk.Spinbox(gen, textvariable=var, from_=0, to=MAXLEN, width=5).grid(row=0, column=r + 1)
            r += 2
        ttk.Button(gen, text="Clock", command=self._gen_clock, width=6).grid(row=0, column=r, padx=4)
        r += 1
        for lbl, var in (("Start", self.g_pstart), ("Width", self.g_pwidth)):
            ttk.Label(gen, text=lbl).grid(row=0, column=r, padx=(10, 1))
            ttk.Spinbox(gen, textvariable=var, from_=0, to=MAXLEN, width=5).grid(row=0, column=r + 1)
            r += 2
        ttk.Button(gen, text="Pulse", command=self._gen_pulse, width=6).grid(row=0, column=r, padx=4)
        r += 1
        ttk.Label(gen, text="Density").grid(row=0, column=r, padx=(10, 1))
        ttk.Spinbox(gen, textvariable=self.g_dens, from_=0, to=1, increment=0.1, width=4).grid(row=0, column=r + 1)
        ttk.Button(gen, text="Random", command=self._gen_random, width=8).grid(row=0, column=r + 2, padx=4)
        io = ttk.Frame(gen)
        io.grid(row=1, column=0, columnspan=r + 3, sticky="w", pady=(4, 0))
        ttk.Label(io, text="Whole channel:").pack(side="left", padx=(6, 4))
        ttk.Button(io, text="Import file (0/1 text or CSV)…", command=self._import).pack(side="left", padx=2)
        ttk.Button(io, text="Export file…", command=self._export).pack(side="left", padx=2)
        ttk.Button(io, text="Paste text", command=self._paste_text).pack(side="left", padx=2)
        ttk.Button(io, text="Copy as text", command=self._copy_text).pack(side="left", padx=2)

        ttk.Label(right, text="All channels (common time axis) - click a lane to edit it").pack(anchor="w")
        self.overview = Overview(right, self)
        self.overview.pack(fill="x")

        # device actions
        act = ttk.Frame(right, padding=(0, 6))
        act.pack(fill="x")
        ttk.Button(act, text="Upload channel", command=lambda: self._upload(only_current=True)).pack(side="left")
        ttk.Button(act, text="Upload all enabled", command=self._upload).pack(side="left", padx=4)
        ttk.Button(act, text="▶ Start enabled (synchronous)", command=self._start).pack(side="left", padx=4)
        ttk.Button(act, text="▶ Start this channel", command=self._start_one).pack(side="left")
        ttk.Button(act, text="■ Stop all", command=self._stop).pack(side="left", padx=4)
        self.prog = ttk.Progressbar(act, length=180)
        self.prog.pack(side="right")

        self.log = tk.Text(self, height=5, state="disabled", font=("Consolas", 9))
        self.log.pack(fill="x", padx=6, pady=(0, 6))

        self.bind_all("<Control-z>", lambda e: self._undo())
        self.bind_all("<Control-a>", lambda e: self._select_all() if not isinstance(e.widget, (tk.Entry, ttk.Entry)) else None)
        self.bind_all("<Escape>", lambda e: self._clear_sel())
        self.protocol("WM_DELETE_WINDOW", self._quit)

    @staticmethod
    def _ports():
        try:
            from serial.tools import list_ports
            return ["EMU"] + [p.device for p in list_ports.comports()]
        except Exception:
            return ["EMU"]

    def _msg(self, s):
        self.log.configure(state="normal")
        self.log.insert("end", s + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_cursor_info(self, s):
        self.lbl_cursor.configure(text=s)

    # ------------------------------------------------------------------ channel selection / settings
    @property
    def chan(self):
        return self.prj.channels[self.cur]

    def select_channel(self, ch):
        if ch != self.cur and self.cur >= 0:
            self._apply()
        self.cur = ch
        c = self.chan
        self._loading = True
        self.lbl_ch.configure(text=f"CH{ch}")
        self.v_name.set(c.name)
        self.v_en.set(c.enabled)
        self.f_per.set_ms(c.period_ms)
        self.v_len.set(str(len(c.steps)))
        self.v_loops.set(str(c.loops))
        self.v_idle.set(str(c.idle))
        self.v_end.set("go to idle level" if c.idle_on_done else "hold last step")
        self._loading = False
        self._refresh_table()
        if self.tree.selection() != (str(ch),):
            self.tree.selection_set(str(ch))
        self.editor.set_channel(c)
        self.channel_changed(redraw_editor=False)

    def _apply(self):
        """Copy settings fields into the model (validated)."""
        if self._loading:
            return True
        c = self.chan
        try:
            per = self.f_per.get_ms()
            if not PERIOD_MIN_MS <= per <= PERIOD_MAX_MS:
                raise ValueError(f"Step must be {fmt_time(PERIOD_MIN_MS)} … {fmt_time(PERIOD_MAX_MS)}")
            if abs(per * 1000 - round(per * 1000)) > 1e-6:
                raise ValueError("Step must be a whole number of microseconds")
            n = int(self.v_len.get())
            if not 1 <= n <= MAXLEN:
                raise ValueError(f"Length must be 1 … {MAXLEN} steps")
            loops = int(self.v_loops.get())
            if not 0 <= loops <= 65535:
                raise ValueError("Repeat must be 0 … 65535")
        except ValueError as e:
            messagebox.showerror("Invalid value", str(e).replace("could not convert string to float", "not a number"))
            self.select_channel(self.cur)
            return False
        if n != len(c.steps):
            self.push_undo()
            c.resize(n)
            self.editor.fit()
        c.name = self.v_name.get().strip() or f"CH{c.ch}"
        c.enabled = self.v_en.get()
        c.period_ms, c.loops = per, loops
        c.idle = int(self.v_idle.get() or 0)
        c.idle_on_done = self.v_end.get() == "go to idle level"
        self.channel_changed()
        return True

    def channel_changed(self, redraw_editor=True):
        c = self.chan
        self.lbl_dur.configure(text=f"sequence = {fmt_time(round(c.duration_ms, 3))}, "
                                    f"{patterns.edges(c.steps)} edges")
        self._refresh_table()
        if redraw_editor:
            self.editor.redraw()
        self.overview.redraw()
        self.update_selection_info()

    def _refresh_table(self, status=None):
        for c in self.prj.channels:
            st = ""
            if status:
                if status["running"] >> c.ch & 1:
                    st = f"RUN {status['index'][c.ch]}"
                elif status["done"] >> c.ch & 1:
                    st = "DONE"
                else:
                    st = "stop"
            vals = (c.name, "✔" if c.enabled else "", fmt_time(c.period_ms), len(c.steps), c.loops or "∞", st)
            iid = str(c.ch)
            if self.tree.exists(iid):
                self.tree.item(iid, values=vals)
            else:
                self.tree.insert("", "end", iid=iid, values=vals)

    def _on_tree_select(self, _e):
        sel = self.tree.selection()
        if sel and int(sel[0]) != self.cur:
            self.select_channel(int(sel[0]))

    def _tree_click(self, e):
        row = self.tree.identify_row(e.y)
        if row and self.tree.identify_column(e.x) == "#2":
            c = self.prj.channels[int(row)]
            c.enabled = not c.enabled
            if c.ch == self.cur:
                self.v_en.set(c.enabled)
            self.channel_changed()

    def _enable_all(self, on):
        for c in self.prj.channels:
            c.enabled = on
        self.v_en.set(on)
        self.channel_changed()

    def _load_aux(self):
        self.f_clk.set_ms(self.prj.clock_period_ms)
        self.f_syncw.set_ms(self.prj.sync_width_ms)
        self.v_vout.set(vout_text(self.prj.output_voltage))

    def _apply_vout(self, _event=None):
        volts = VOUT_LEVELS[[vout_text(v) for v in VOUT_LEVELS].index(self.v_vout.get())]
        self.prj.output_voltage = volts
        if self.dev:
            self._run_bg(lambda: self.dev.set_output_voltage(volts), f"Output level: {vout_text(volts)}")

    def _apply_aux(self):
        old = (self.prj.clock_period_ms, self.prj.sync_width_ms)
        try:
            self.prj.clock_period_ms = self.f_clk.get_ms()
            self.prj.sync_width_ms = self.f_syncw.get_ms()
            self.prj.validate_aux()
        except ValueError as e:
            self.prj.clock_period_ms, self.prj.sync_width_ms = old
            self._load_aux()
            messagebox.showerror("Invalid value", str(e).replace("could not convert string to float", "not a number"))
            return False
        self.overview.redraw()
        return True

    # ------------------------------------------------------------------ editing
    def push_undo(self):
        self.undo.append((self.cur, list(self.chan.steps)))
        del self.undo[:-200]

    def _undo(self):
        if not self.undo:
            return
        ch, steps = self.undo.pop()
        self.prj.channels[ch].steps = steps
        if ch != self.cur:
            self.select_channel(ch)
        else:
            self.v_len.set(str(len(steps)))
            self.editor.sel = None
            self.editor.fit()
            self.channel_changed()

    def _range(self):
        if self.editor.sel:
            return self.editor.sel
        return 0, len(self.chan.steps) - 1

    def update_selection_info(self):
        s = self.editor.sel
        if s:
            n = s[1] - s[0] + 1
            self.lbl_sel.configure(text=f"selection: steps {s[0]}…{s[1]} ({n} steps, "
                                        f"{fmt_time(round(n * self.chan.period_ms, 3))})")
        else:
            self.lbl_sel.configure(text="no selection (Shift+drag to select)")

    def _select_all(self):
        self.editor.sel = (0, len(self.chan.steps) - 1)
        self.editor.redraw()
        self.update_selection_info()

    def _clear_sel(self):
        self.editor.sel = None
        self.editor.redraw()
        self.update_selection_info()

    def _modify(self, fn):
        a, b = self._range()
        self.push_undo()
        seg = fn(self.chan.steps[a:b + 1])
        self.chan.steps[a:b + 1] = seg
        self.channel_changed()

    def _fill(self, level):
        self._modify(lambda s: [level] * len(s))

    def _invert(self):
        self._modify(lambda s: [1 - v for v in s])

    def _rotate(self, k):
        self._modify(lambda s: s[-k:] + s[:-k] if k > 0 else s[-k:] + s[:-k])

    def _gen_clock(self):
        try:
            hi, lo, ph = self.g_high.get(), self.g_low.get(), self.g_phase.get()
        except tk.TclError:
            return
        self._modify(lambda s: patterns.clock(len(s), hi, lo, ph))

    def _gen_pulse(self):
        try:
            st, wd = self.g_pstart.get(), self.g_pwidth.get()
        except tk.TclError:
            return
        self._modify(lambda s: patterns.pulse(len(s), st, wd))

    def _gen_random(self):
        try:
            d = float(self.g_dens.get())
        except tk.TclError:
            return
        self._modify(lambda s: patterns.rand(len(s), d))

    def _copy(self):
        a, b = self._range()
        self.clipboard_steps = self.chan.steps[a:b + 1]
        self._msg(f"Copied {len(self.clipboard_steps)} steps from CH{self.cur}")

    def _paste(self):
        """Paste at selection start (or step 0); overwrites, extends the sequence if needed."""
        if not self.clipboard_steps:
            return
        a = self.editor.sel[0] if self.editor.sel else 0
        data = self.clipboard_steps
        self.push_undo()
        c = self.chan
        end = min(MAXLEN, a + len(data))
        if end > len(c.steps):
            c.resize(end)
            self.v_len.set(str(end))
        c.steps[a:end] = data[:end - a]
        self.editor.sel = (a, end - 1)
        self.editor.fit()
        self.channel_changed()

    def _copy_to(self):
        win = tk.Toplevel(self)
        win.title(f"Copy CH{self.cur} to…")
        win.transient(self)
        win.grab_set()
        vars_ = []
        for ch in range(NCH):
            v = tk.BooleanVar(value=False)
            vars_.append(v)
            cb = ttk.Checkbutton(win, text=f"CH{ch} {self.prj.channels[ch].name}", variable=v)
            cb.grid(row=ch % 8, column=ch // 8, sticky="w", padx=8, pady=1)
            if ch == self.cur:
                cb.state(["disabled"])
        with_cfg = tk.BooleanVar(value=True)
        ttk.Checkbutton(win, text="also copy step time / repeat / idle settings",
                        variable=with_cfg).grid(row=8, column=0, columnspan=2, sticky="w", padx=8, pady=4)

        def ok():
            src = self.chan
            n = 0
            for ch, v in enumerate(vars_):
                if v.get() and ch != self.cur:
                    d = self.prj.channels[ch]
                    self.undo.append((ch, list(d.steps)))
                    d.steps = list(src.steps)
                    d.enabled = True
                    if with_cfg.get():
                        d.period_ms, d.loops, d.idle, d.idle_on_done = (src.period_ms, src.loops,
                                                                          src.idle, src.idle_on_done)
                    n += 1
            win.destroy()
            self.channel_changed()
            self._msg(f"Copied CH{self.cur} to {n} channel(s)")
        ttk.Button(win, text="Copy", command=ok).grid(row=9, column=0, pady=6)
        ttk.Button(win, text="Cancel", command=win.destroy).grid(row=9, column=1, pady=6)

    def _set_steps(self, steps, source):
        if not steps:
            messagebox.showerror("Import", "No 0/1 levels found")
            return
        if len(steps) > MAXLEN:
            self._msg(f"{source}: {len(steps)} steps - truncated to {MAXLEN}")
            steps = steps[:MAXLEN]
        self.push_undo()
        self.chan.steps = steps
        self.v_len.set(str(len(steps)))
        self.editor.sel = None
        self.editor.fit()
        self.channel_changed()
        self._msg(f"CH{self.cur}: loaded {len(steps)} steps from {source}")

    def _import(self):
        path = filedialog.askopenfilename(filetypes=[("Text / CSV", "*.txt *.csv"), ("All files", "*.*")])
        if path:
            with open(path, encoding="utf-8") as f:
                self._set_steps(parse_levels(f.read()), path)

    def _export(self):
        if not self._apply():
            return
        c = self.chan
        path = filedialog.asksaveasfilename(
            title=f"Export CH{c.ch} sequence", initialfile=f"{c.name.replace('/', '_')}.txt",
            defaultextension=".txt",
            filetypes=[("0/1 text", "*.txt"), ("CSV (level, time_ms)", "*.csv")])
        if not path:
            return
        fmt = "csv" if path.lower().endswith(".csv") else "txt"
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(format_levels(c, fmt))
        except OSError as e:
            messagebox.showerror("Export", str(e))
            return
        self._msg(f"CH{c.ch}: exported {len(c.steps)} steps to {path}")

    def _paste_text(self):
        try:
            txt = self.clipboard_get()
        except tk.TclError:
            return
        self._set_steps(parse_levels(txt), "clipboard")

    def _copy_text(self):
        self.clipboard_clear()
        self.clipboard_append(patterns.to_text(self.chan.steps))
        self._msg(f"CH{self.cur} copied to clipboard as text ({len(self.chan.steps)} characters)")

    # ------------------------------------------------------------------ project files
    def load_project(self, prj):
        """Replace the whole project (settings fields of the old one are discarded)."""
        self.prj = prj
        self.undo.clear()
        self.cur = -1
        self._load_aux()
        self.select_channel(0)

    def _new(self):
        if messagebox.askyesno("New project", "Discard the current project?"):
            prj = Project()
            prj.channels[0].enabled = True
            self.project_path = None
            self.load_project(prj)

    def _open(self):
        path = filedialog.askopenfilename(filetypes=[("SeqGen project", "*.json")])
        if not path:
            return
        try:
            prj = Project.load(path)
        except Exception as e:
            messagebox.showerror("Open", f"Cannot open project:\n{e}")
            return
        self.project_path = path
        self.load_project(prj)
        self.title(f"SeqGen16 - {path}")
        self._msg(f"Opened {path}")

    def _save(self, ask=False):
        if not self._apply() or not self._apply_aux():
            return
        path = self.project_path
        if ask or not path:
            path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("SeqGen project", "*.json")])
            if not path:
                return
        self.prj.save(path)
        self.project_path = path
        self.title(f"SeqGen16 - {path}")
        self._msg(f"Saved {path}")

    # ------------------------------------------------------------------ device
    def _connect(self):
        if self.dev:
            with self.dev_lock:
                self.dev.close()
                self.dev = None
            self.btn_conn.configure(text="Connect")
            self.lbl_dev.configure(text="not connected", foreground="gray")
            self.overview.outputs = self.overview.running = 0
            self.editor.playhead = None
            self.channel_changed()
            return
        port = self.port.get()
        try:
            if port.upper() == "EMU":
                from emulator import Emulator
                dev = SeqGen(Emulator())
            else:
                dev = SeqGen.open_serial(port)
            info = dev.ping()
        except Exception as e:
            messagebox.showerror("Connection", str(e))
            return
        self.dev = dev
        self.btn_conn.configure(text="Disconnect")
        self.lbl_dev.configure(text=f"{port}: FW {info['fw']}, {info['nch']} ch, {info['maxlen']} steps",
                               foreground="green")
        self._msg(f"Connected: {info}")

    def _run_bg(self, fn, done_msg, *, apply_fields=True):
        if not self.dev:
            messagebox.showwarning("Not connected", "Connect to the device first")
            return
        if apply_fields and (not self._apply() or not self._apply_aux()):
            return

        def work():
            with self.dev_lock:
                try:
                    fn()
                    self._ui(self._msg, done_msg)
                except (DeviceError, ValueError, OSError) as e:
                    self._ui(messagebox.showerror, "Error", str(e))
        threading.Thread(target=work, daemon=True).start()

    def _progress(self, k, n, ch):
        self._ui(lambda: (self.prog.configure(maximum=n, value=k), self._msg(f"  CH{ch} uploaded and verified")))

    def _upload(self, only_current=False):
        if not self._apply() or not self._apply_aux():
            return
        project = deepcopy(self.prj)
        if only_current:
            channel = project.channels[self.cur]
            project.channels = [channel]
            channel.enabled = True
            self._run_bg(lambda: self.dev.upload_project(project), f"CH{channel.ch} uploaded", apply_fields=False)
        else:
            self._run_bg(lambda: self.dev.upload_project(project, progress=self._progress), "Project uploaded",
                         apply_fields=False)

    def _start(self):
        mask = sum(1 << c.ch for c in self.prj.channels if c.enabled)
        self._run_bg(lambda: self.dev.start(mask), f"Started, mask 0x{mask:04X}")

    def _start_one(self):
        ch = self.cur
        self._run_bg(lambda: self.dev.start(1 << ch), f"Started CH{ch}")

    def _stop(self):
        self._run_bg(lambda: self.dev.stop(0xFFFF), "Stopped all channels")

    def _poll_status(self):
        if self.dev and self.dev_lock.acquire(blocking=False):
            try:
                st = self.dev.status()
            except Exception as e:
                st = None
                self._msg(f"status: {e}")
            finally:
                self.dev_lock.release()
            if st:
                self._refresh_table(st)
                if (st["outputs"], st["running"]) != (self.overview.outputs, self.overview.running):
                    self.overview.outputs, self.overview.running = st["outputs"], st["running"]
                    self.overview.redraw()
                ph = st["index"][self.cur] if st["running"] >> self.cur & 1 else None
                if ph != self.editor.playhead:
                    self.editor.playhead = ph
                    self.editor.redraw()
        self.after(300, self._poll_status)

    def _quit(self):
        if self.dev:
            self.dev.close()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
