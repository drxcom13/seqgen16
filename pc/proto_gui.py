"""proto_gui.py - "Serial tester" window of the GUI: SPI / I2C / UART master on connector J6.

Opened from the main window (button "Serial tester…"); uses the main window's connection
(app.dev, app.dev_lock) and runs every device operation in a background thread.
"""
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

from seqgen import (DeviceError, VOUT_LEVELS, vout_text, parse_bytes, format_bytes,
                    SPI_MAX_HZ, I2C_MAX_HZ, UART_MAX_BAUD)

BAUDS = ("9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600", "1000000", "2000000",
         "3000000")
I2C_CLOCKS = ("100 kHz", "400 kHz", "1 MHz")


def _ascii(data: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)


def _freq(text: str) -> float:
    """'400 kHz', '1 MHz', '12.5M', '100000' -> Hz."""
    t = text.strip().lower().replace(" ", "").rstrip("hz")
    mul = 1.0
    if t.endswith("k"):
        mul, t = 1e3, t[:-1]
    elif t.endswith("m"):
        mul, t = 1e6, t[:-1]
    return float(t) * mul


class ProtoWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("SeqGen16 - Serial tester (J6: SPI / I2C / UART)")
        self.geometry("820x620")
        self.minsize(700, 480)
        self.cfg_key = None                 # last configuration sent to the device
        self.level_sent = None
        self.auto_job = None
        self._build()
        self._show_proto()
        self.protocol("WM_DELETE_WINDOW", self._close)

    # ------------------------------------------------------------------ layout
    def _build(self):
        top = ttk.Frame(self, padding=6)
        top.pack(fill="x")
        self.v_proto = tk.StringVar(value="SPI")
        for p in ("SPI", "I2C", "UART"):
            ttk.Radiobutton(top, text=p, value=p, variable=self.v_proto, command=self._show_proto).pack(side="left", padx=4)
        ttk.Label(top, text="   J6 level").pack(side="left")
        self.v_level = tk.StringVar(value="off")
        cb = ttk.Combobox(top, textvariable=self.v_level, state="readonly", width=7,
                          values=[vout_text(v) for v in VOUT_LEVELS])
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self._set_level())
        ttk.Button(top, text="Line states", command=self._pins).pack(side="right")

        self.settings = ttk.LabelFrame(self, text="Settings", padding=6)
        self.settings.pack(fill="x", padx=6)
        # SPI
        f = self.f_spi = ttk.Frame(self.settings)
        ttk.Label(f, text="SCK").grid(row=0, column=0, sticky="e")
        self.v_spi_hz = tk.StringVar(value="1 MHz")
        ttk.Combobox(f, textvariable=self.v_spi_hz, width=10,
                     values=("100 kHz", "500 kHz", "1 MHz", "2 MHz", "5 MHz", "8.33 MHz", "12.5 MHz")).grid(
            row=0, column=1, padx=4)
        ttk.Label(f, text="Mode").grid(row=0, column=2, sticky="e")
        self.v_spi_mode = tk.StringVar(value="0")
        ttk.Combobox(f, textvariable=self.v_spi_mode, state="readonly", width=3, values=("0", "1", "2", "3")).grid(
            row=0, column=3, padx=4)
        self.v_spi_lsb = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="LSB first", variable=self.v_spi_lsb).grid(row=0, column=4, padx=8)
        ttk.Label(f, text="MISO sample delay [x20 ns]").grid(row=0, column=5, sticky="e")
        self.v_spi_del = tk.StringVar(value="1")
        ttk.Spinbox(f, textvariable=self.v_spi_del, from_=0, to=7, width=3).grid(row=0, column=6, padx=4)
        ttk.Label(f, text="Data = bytes sent on MOSI in one CS-low transaction; the log shows MISO. "
                          f"Max {SPI_MAX_HZ / 1e6:g} MHz.", foreground="#888").grid(
            row=1, column=0, columnspan=7, sticky="w", pady=(4, 0))
        # I2C
        f = self.f_i2c = ttk.Frame(self.settings)
        ttk.Label(f, text="SCL").grid(row=0, column=0, sticky="e")
        self.v_i2c_hz = tk.StringVar(value="100 kHz")
        ttk.Combobox(f, textvariable=self.v_i2c_hz, width=9, values=I2C_CLOCKS).grid(row=0, column=1, padx=4)
        ttk.Label(f, text="Address (7-bit, hex)").grid(row=0, column=2, sticky="e")
        self.v_i2c_addr = tk.StringVar(value="50")
        ttk.Entry(f, textvariable=self.v_i2c_addr, width=5).grid(row=0, column=3, padx=4)
        ttk.Label(f, text="Read bytes").grid(row=0, column=4, sticky="e")
        self.v_i2c_rd = tk.StringVar(value="0")
        ttk.Spinbox(f, textvariable=self.v_i2c_rd, from_=0, to=250, width=5).grid(row=0, column=5, padx=4)
        ttk.Button(f, text="Scan bus", command=self._i2c_scan).grid(row=0, column=6, padx=8)
        ttk.Label(f, text="Data = bytes written after the address; with Read > 0: repeated START and read "
                          f"(last byte NACK). Max {I2C_MAX_HZ // 1000} kHz. JP3 = pull-ups 2.2k to J6 level.",
                  foreground="#888").grid(row=1, column=0, columnspan=7, sticky="w", pady=(4, 0))
        # UART
        f = self.f_uart = ttk.Frame(self.settings)
        ttk.Label(f, text="Baud").grid(row=0, column=0, sticky="e")
        self.v_baud = tk.StringVar(value="115200")
        ttk.Combobox(f, textvariable=self.v_baud, width=9, values=BAUDS).grid(row=0, column=1, padx=4)
        ttk.Label(f, text="Parity").grid(row=0, column=2, sticky="e")
        self.v_par = tk.StringVar(value="N")
        ttk.Combobox(f, textvariable=self.v_par, state="readonly", width=3, values=("N", "E", "O")).grid(
            row=0, column=3, padx=4)
        ttk.Label(f, text="Stop bits").grid(row=0, column=4, sticky="e")
        self.v_stop = tk.StringVar(value="1")
        ttk.Combobox(f, textvariable=self.v_stop, state="readonly", width=3, values=("1", "2")).grid(
            row=0, column=5, padx=4)
        ttk.Label(f, text="Receive after send [ms]").grid(row=0, column=6, sticky="e")
        self.v_wait = tk.StringVar(value="100")
        ttk.Spinbox(f, textvariable=self.v_wait, from_=0, to=5000, increment=50, width=6).grid(row=0, column=7, padx=4)
        self.v_auto = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Receive continuously", variable=self.v_auto, command=self._auto_toggle).grid(
            row=0, column=8, padx=8)
        ttk.Label(f, text=f"8 data bits. TX = J6 pin 11, RX = pin 12. Max {UART_MAX_BAUD} Bd.",
                  foreground="#888").grid(row=1, column=0, columnspan=9, sticky="w", pady=(4, 0))

        d = ttk.Frame(self, padding=6)
        d.pack(fill="x")
        ttk.Label(d, text="Data").pack(side="left")
        self.v_fmt = tk.StringVar(value="hex")
        ttk.Radiobutton(d, text="Hex", value="hex", variable=self.v_fmt).pack(side="left", padx=(6, 0))
        ttk.Radiobutton(d, text="Text (\\r \\n \\x41 escapes)", value="text", variable=self.v_fmt).pack(side="left")
        self.v_data = tk.StringVar(value="9F 00 00 00")
        e = ttk.Entry(d, textvariable=self.v_data, font=("Consolas", 10))
        e.pack(side="left", fill="x", expand=True, padx=6)
        e.bind("<Return>", lambda ev: self._send())
        ttk.Button(d, text="Send", command=self._send).pack(side="left")

        lf = ttk.Frame(self, padding=(6, 0, 6, 6))
        lf.pack(fill="both", expand=True)
        self.log = tk.Text(lf, state="disabled", font=("Consolas", 9), wrap="none")
        sb = ttk.Scrollbar(lf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.tag_configure("tx", foreground="#1060c0")
        self.log.tag_configure("rx", foreground="#108030")
        self.log.tag_configure("err", foreground="#c02020")
        sb.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        b = ttk.Frame(self, padding=(6, 0, 6, 6))
        b.pack(fill="x")
        ttk.Button(b, text="Clear log", command=self._clear).pack(side="right")

    def _show_proto(self):
        for f in (self.f_spi, self.f_i2c, self.f_uart):
            f.pack_forget()
        {"SPI": self.f_spi, "I2C": self.f_i2c, "UART": self.f_uart}[self.v_proto.get()].pack(fill="x")
        self.settings.configure(text=f"{self.v_proto.get()} settings")
        if self.v_proto.get() != "UART":
            self.v_auto.set(False)
            self._auto_toggle()

    # ------------------------------------------------------------------ log
    def _out(self, text, tag=""):
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("%H:%M:%S ") + text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # ------------------------------------------------------------------ device access
    def _bg(self, fn):
        """Run fn(dev) in a worker thread; fn returns a list of (text, tag) lines for the log."""
        dev = self.app.dev
        if not dev:
            messagebox.showwarning("Not connected", "Connect to the device first", parent=self)
            return

        def work():
            with self.app.dev_lock:
                try:
                    lines = fn(dev)
                except (DeviceError, ValueError, OSError) as e:
                    lines = [(f"error: {e}", "err")]
            for text, tag in lines or []:
                self.app._ui(self._out, text, tag)
        threading.Thread(target=work, daemon=True).start()

    def _settings(self):
        """-> (key, configure function) for the selected protocol (ValueError on bad input)."""
        p = self.v_proto.get()
        if p == "SPI":
            hz, mode, lsb, dl = _freq(self.v_spi_hz.get()), int(self.v_spi_mode.get()), self.v_spi_lsb.get(), \
                int(self.v_spi_del.get())
            return (p, hz, mode, lsb, dl), lambda dev: dev.spi_configure(hz, mode, lsb, dl)
        if p == "I2C":
            hz = _freq(self.v_i2c_hz.get())
            return (p, hz), lambda dev: dev.i2c_configure(hz)
        baud, par, stop = float(self.v_baud.get()), self.v_par.get(), int(self.v_stop.get())
        return (p, baud, par, stop), lambda dev: dev.uart_configure(baud, par, stop)

    def _ensure(self, dev, key, configure, lines):
        if key != self.cfg_key:
            real = configure(dev)
            self.cfg_key = key
            unit = "Bd" if key[0] == "UART" else "Hz"
            lines.append((f"{key[0]} configured: {real:,.0f} {unit}", ""))

    def _data(self) -> bytes:
        t = self.v_data.get()
        if self.v_fmt.get() == "text":
            return t.encode("latin-1").decode("unicode_escape").encode("latin-1")
        return parse_bytes(t)

    def _set_level(self):
        txt = self.v_level.get()
        volts = 0.0 if txt == "off" else float(txt.split()[0])

        def fn(dev):
            dev.pt_set_level(volts)
            self.level_sent = volts
            return [(f"J6 level: {vout_text(volts)}" + ("" if volts else " (lines high impedance)"), "")]
        self._bg(fn)

    def _send(self):
        try:
            key, configure = self._settings()
            data = self._data()
            proto = self.v_proto.get()
            if proto == "I2C":
                addr = int(self.v_i2c_addr.get(), 16)
                nrd = int(self.v_i2c_rd.get())
            if proto == "UART":
                wait = int(self.v_wait.get())
        except ValueError as e:
            messagebox.showerror("Serial tester", str(e), parent=self)
            return

        def fn(dev):
            lines = []
            self._ensure(dev, key, configure, lines)
            if self.level_sent in (None, 0.0):
                lines.append(("warning: J6 level is off - lines are high impedance", "err"))
            if proto == "SPI":
                if not data:
                    raise ValueError("no data")
                rx = dev.spi_transfer(data)
                lines += [(f"SPI  → {format_bytes(data)}", "tx"), (f"     ← {format_bytes(rx)}", "rx")]
            elif proto == "I2C":
                r = dev.i2c_transfer(addr, data, nrd)
                txt = f"I2C 0x{addr:02X} {'ACK' if r['ack'] else 'NACK'}"
                if data:
                    acks = "".join("A" if k else "N" for k in r["data_acks"])
                    txt += f"  W: {format_bytes(data)}  [{acks}]"
                lines.append((txt, "tx" if r["ack"] else "err"))
                if nrd:
                    lines.append((f"     R: {format_bytes(r['data'])}   |{_ascii(r['data'])}|", "rx"))
            else:
                rx, err = dev.uart_send(data, wait)
                if data:
                    lines.append((f"TX → {format_bytes(data)}   |{_ascii(data)}|", "tx"))
                lines.append((f"RX ← {format_bytes(rx) or '(nothing)'}   |{_ascii(rx)}|"
                              + (f"  {err} with framing/parity error" if err else ""), "err" if err else "rx"))
            return lines
        self._bg(fn)

    def _i2c_scan(self):
        try:
            key, configure = self._settings()
        except ValueError as e:
            messagebox.showerror("Serial tester", str(e), parent=self)
            return

        def fn(dev):
            lines = []
            self._ensure(dev, key, configure, lines)
            found = dev.i2c_scan()
            lines.append(("I2C scan: " + (", ".join(f"0x{a:02X}" for a in found) or "no devices"),
                          "rx" if found else "err"))
            return lines
        self._bg(fn)

    def _pins(self):
        self._bg(lambda dev: [("J6 lines: " + "  ".join(f"{k}={v}" for k, v in dev.pt_pins().items()), "")])

    def _auto_toggle(self):
        if self.auto_job:
            self.after_cancel(self.auto_job)
            self.auto_job = None
        if self.v_auto.get():
            self._auto_rx()

    def _auto_rx(self):
        self.auto_job = None
        if not self.v_auto.get():
            return
        dev = self.app.dev
        if dev and self.app.dev_lock.acquire(blocking=False):
            try:
                key, configure = self._settings()
                lines = []
                self._ensure(dev, key, configure, lines)
                rx, err = dev.uart_receive()
                if rx:
                    lines.append((f"RX ← {format_bytes(rx)}   |{_ascii(rx)}|" + (f"  {err} with errors" if err else ""),
                                  "err" if err else "rx"))
                for t, tag in lines:
                    self._out(t, tag)
            except (DeviceError, ValueError, OSError) as e:
                self._out(f"error: {e}", "err")
                self.v_auto.set(False)
                return
            finally:
                self.app.dev_lock.release()
        self.auto_job = self.after(200, self._auto_rx)

    def _close(self):
        self.v_auto.set(False)
        self._auto_toggle()
        self.app.proto_win = None
        self.destroy()
