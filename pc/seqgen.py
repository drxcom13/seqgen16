"""seqgen.py - PC library for the 16-channel digital sequence generator (MAX 10 + Nios V).

Protocol: see ../PROTOCOL.md. Transport: RS-232 115200 8N1 (pyserial)
or the in-memory emulator (emulator.py) for working without hardware.
"""
from __future__ import annotations

import json
import math
import struct
import time
from dataclasses import dataclass, field, asdict
from typing import List

SYNC = b"\xA5\x5A"

CMD_PING, CMD_SET_CFG, CMD_GET_CFG = 0x01, 0x10, 0x11
CMD_SET_AUX, CMD_GET_AUX = 0x12, 0x13
CMD_SET_VOUT, CMD_GET_VOUT = 0x14, 0x15
CMD_WRITE, CMD_READ = 0x20, 0x21
CMD_START, CMD_STOP, CMD_STATUS = 0x30, 0x31, 0x32
CMD_PT_CONFIG, CMD_PT_GET_CONFIG, CMD_PT_LEVEL = 0x40, 0x41, 0x42
CMD_PT_XFER, CMD_PT_READ, CMD_PT_PINS = 0x43, 0x44, 0x45

STATUS_TEXT = {0: "OK", 1: "unknown command", 2: "bad length", 3: "bad argument", 4: "CRC error"}

NCH = 16
MAXLEN = 1024
PERIOD_MIN_MS = 0.001      # 1 us - hardware time base (step = N x 1 us)
PERIOD_MAX_MS = 1000.0
CHUNK = 256                # steps per WRITE frame
PROJECT_VERSION = 4
CLK_PERIOD_MAX_US = 1 << 21      # BASE_CLK: half period register is 20 bit
SYNC_WIDTH_MAX_US = (1 << 20) - 1
# Output level (VCCB of the output buffers, all outputs together); index = SET_VOUT level code.
# 0.0 = off: LDOs disabled, outputs high impedance (state after power-up / reset).
VOUT_LEVELS = (0.0, 1.8, 2.5, 3.3, 4.0, 5.0)


def vout_code(volts: float) -> int:
    for code, v in enumerate(VOUT_LEVELS):
        if abs(volts - v) < 1e-6:
            return code
    raise ValueError(f"output level {volts} V not supported (allowed: off, 1.8, 2.5, 3.3, 4.0, 5.0 V)")


def vout_text(volts: float) -> str:
    return "off" if volts == 0 else f"{volts:.1f} V"


# --------------------------------------------------------------------------- protocol tester (J6)
CLK_HZ = 50_000_000
PT_OFF, PT_SPI, PT_I2C, PT_UART = 0, 1, 2, 3
PT_MODES = {PT_OFF: "off", PT_SPI: "SPI master", PT_I2C: "I2C master", PT_UART: "UART"}
PT_FIFO = 512                      # entries per transfer (hardware queue depth)
SPI_LAST = 0x100                   # TX entry flag: last byte, CS goes high after it
I2C_START, I2C_STOP = 0x301, 0x302
I2C_RD_ACK, I2C_RD_NACK = 0x100, 0x200
PTF_RXOVF, PTF_BUSERR, PTF_TIMEOUT = 1, 2, 4
PT_PIN_NAMES = ("SCK", "MOSI", "CS", "MISO", "TX", "RX", "SDA", "SCL")
SPI_MAX_HZ = CLK_HZ // 4           # 12.5 MHz (half period = 2 clocks)
I2C_MAX_HZ = 1_000_000
UART_MAX_BAUD = CLK_HZ // 16       # 3.125 Mbaud (16x oversampling)


def spi_div(hz: float):
    """-> (DIV register, real SCK frequency): SCK = CLK / (2 * (div + 1)), never above hz."""
    if not 0 < hz <= SPI_MAX_HZ:
        raise ValueError(f"SPI clock must be > 0 and <= {SPI_MAX_HZ / 1e6:g} MHz")
    div = max(1, math.ceil(CLK_HZ / (2 * hz)) - 1)
    if div > 0xFFFF:
        raise ValueError("SPI clock too low (min ~382 Hz)")
    return div, CLK_HZ / (2 * (div + 1))


def i2c_div(hz: float):
    """-> (DIV register, real SCL frequency): SCL = CLK / (4 * (div + 1)), never above hz."""
    if not 0 < hz <= I2C_MAX_HZ:
        raise ValueError(f"I2C clock must be > 0 and <= {I2C_MAX_HZ // 1000} kHz")
    div = max(3, math.ceil(CLK_HZ / (4 * hz)) - 1)
    if div > 0xFFFF:
        raise ValueError("I2C clock too low (min ~191 Hz)")
    return div, CLK_HZ / (4 * (div + 1))


def uart_inc(baud: float):
    """-> (DIV register = phase increment, real baud rate): 16 x baud from a 32-bit accumulator."""
    if not 300 <= baud <= UART_MAX_BAUD:
        raise ValueError(f"UART baud rate must be 300 .. {UART_MAX_BAUD}")
    inc = round(16 * baud * (1 << 32) / CLK_HZ)
    return inc, inc * CLK_HZ / (16 * (1 << 32))


def pt_ctrl(mode: int, cpol=0, cpha=0, lsb=False, parity=0, stop2=False, sample_delay=1) -> int:
    """CTRL register of proto_io (parity: 0 none, 1 even, 2 odd)."""
    if mode not in PT_MODES or parity not in (0, 1, 2) or not 0 <= sample_delay <= 7:
        raise ValueError("bad protocol tester setting")
    return (mode | (cpol & 1) << 3 | (cpha & 1) << 4 | bool(lsb) << 5 | parity << 6 | bool(stop2) << 8
            | sample_delay << 9)


def parse_bytes(text: str) -> bytes:
    """Bytes from text: hex ('9F 00 0x12', '9f0012') or a quoted string with escapes ("AT\\r\\n")."""
    t = text.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "'\"":
        return t[1:-1].encode("latin-1").decode("unicode_escape").encode("latin-1")
    out = bytearray()
    for tok in t.replace(",", " ").replace(";", " ").split():
        tok = tok[2:] if tok.lower().startswith("0x") else tok
        if len(tok) % 2:
            tok = "0" + tok
        try:
            out += bytes.fromhex(tok)
        except ValueError:
            raise ValueError(f"not a hex byte: {tok!r}") from None
    return bytes(out)


def format_bytes(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def fmt_time(ms: float) -> str:
    """Human-readable duration: 1 us, 250 us, 1.5 ms, 2 s ..."""
    ms = round(ms, 6)
    if ms >= 1000:
        return f"{round(ms / 1000, 6):g} s"
    if ms >= 1 or ms == 0:
        return f"{ms:g} ms"
    return f"{round(ms * 1000, 3):g} µs"


TIME_UNITS = {"µs": 0.001, "ms": 1.0, "s": 1000.0}     # unit -> milliseconds


def split_time(ms: float):
    """Pick a display unit for an input field: returns (value, unit)."""
    if ms >= 1000 and abs(ms / 1000 - round(ms / 1000, 3)) < 1e-9:
        return round(ms / 1000, 6), "s"
    if ms < 1 or abs(ms - round(ms, 3)) > 1e-9:
        return round(ms * 1000, 3), "µs"
    return round(ms, 6), "ms"


def crc16(data: bytes, crc: int = 0xFFFF) -> int:
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection)."""
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def build_frame(cmd: int, payload: bytes = b"") -> bytes:
    body = struct.pack("<BH", cmd, len(payload)) + payload
    return SYNC + body + struct.pack("<H", crc16(body))


class FrameParser:
    """Streaming frame parser (used by the PC side and by the emulator)."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes):
        self.buf += data
        frames = []
        while True:
            i = self.buf.find(SYNC)
            if i < 0:
                del self.buf[:-1]
                return frames
            del self.buf[:i]
            if len(self.buf) < 5:
                return frames
            cmd, ln = struct.unpack_from("<BH", self.buf, 2)
            if ln > 2048:                       # garbage - skip one byte
                del self.buf[:1]
                continue
            total = 5 + ln + 2
            if len(self.buf) < total:
                return frames
            body = bytes(self.buf[2:5 + ln])
            (rcrc,) = struct.unpack_from("<H", self.buf, 5 + ln)
            del self.buf[:total]
            frames.append((cmd, body[3:], rcrc == crc16(body)))


class DeviceError(Exception):
    pass


# --------------------------------------------------------------------------- data model
@dataclass
class ChannelConfig:
    ch: int
    name: str = ""
    enabled: bool = False
    period_ms: float = 1.0              # duration of one step
    loops: int = 0                      # 0 = repeat forever
    idle: int = 0                       # output level when stopped (0/1)
    idle_on_done: bool = False          # after the last repeat: True = idle level, False = hold last step
    steps: List[int] = field(default_factory=lambda: [0] * 64)

    def __post_init__(self):
        if not self.name:
            self.name = f"CH{self.ch}"
        self.steps = [1 if s else 0 for s in self.steps]
        self.idle = 1 if self.idle else 0

    @property
    def duration_ms(self) -> float:
        return len(self.steps) * self.period_ms

    def validate(self):
        if not isinstance(self.ch, int) or not 0 <= self.ch < NCH:
            raise ValueError(f"channel {self.ch}: number out of range 0..{NCH - 1}")
        if not PERIOD_MIN_MS <= self.period_ms <= PERIOD_MAX_MS:
            raise ValueError(f"channel {self.ch}: step {fmt_time(self.period_ms)} out of range 1 us .. 1 s")
        if abs(self.period_ms * 1000 - round(self.period_ms * 1000)) > 1e-6:
            raise ValueError(f"channel {self.ch}: step must be a multiple of 1 us")
        if not 1 <= len(self.steps) <= MAXLEN:
            raise ValueError(f"channel {self.ch}: {len(self.steps)} steps, allowed 1..{MAXLEN}")
        if not isinstance(self.loops, int) or not 0 <= self.loops <= 0xFFFF:
            raise ValueError(f"channel {self.ch}: repeat count out of range 0..65535")

    def resize(self, n: int, fill: int | None = None):
        """Change sequence length; new steps continue the last level (or `fill`)."""
        n = max(1, min(MAXLEN, int(n)))
        if n < len(self.steps):
            del self.steps[n:]
        else:
            v = fill if fill is not None else (self.steps[-1] if self.steps else self.idle)
            self.steps += [v] * (n - len(self.steps))


@dataclass
class Project:
    channels: List[ChannelConfig] = field(default_factory=lambda: [ChannelConfig(ch=i) for i in range(NCH)])
    clock_period_ms: float = 1.0       # BASE_CLK output period (50 % duty), restarted by every START
    sync_width_ms: float = 1.0         # SYNC pulse width; 0 = high while any channel runs
    output_voltage: float = 3.3        # output level of all outputs (VOUT_LEVELS), 0 = off

    def validate_aux(self):
        per = round(self.clock_period_ms * 1000)
        sw = round(self.sync_width_ms * 1000)
        if abs(self.clock_period_ms * 1000 - per) > 1e-6 or per < 2 or per % 2 or per > CLK_PERIOD_MAX_US:
            raise ValueError("base clock period must be an even number of microseconds, 0.002 .. 2097.152 ms")
        if abs(self.sync_width_ms * 1000 - sw) > 1e-6 or not 0 <= sw <= SYNC_WIDTH_MAX_US:
            raise ValueError("sync width must be 0 .. 1048.575 ms in 1 us steps")
        return per, sw

    def validate_vout(self) -> int:
        return vout_code(self.output_voltage)

    def save(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"version": PROJECT_VERSION, "clock_period_ms": self.clock_period_ms,
                       "sync_width_ms": self.sync_width_ms, "output_voltage": self.output_voltage,
                       "channels": [asdict(c) for c in self.channels]}, f, indent=1)

    @classmethod
    def load(cls, path: str) -> "Project":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        p = cls(clock_period_ms=d.get("clock_period_ms", 1.0), sync_width_ms=d.get("sync_width_ms", 1.0),
                output_voltage=d.get("output_voltage", 3.3))
        known = set(ChannelConfig.__dataclass_fields__)
        for cd in d.get("channels", []):
            c = ChannelConfig(**{k: v for k, v in cd.items() if k in known})
            p.channels[c.ch] = c
        return p


def parse_levels(text: str) -> List[int]:
    """Steps from text: '0'/'1' characters ('_' and '-' also accepted), or numbers
    separated by , ; tab or newline (CSV column - non-zero = 1). Header lines and
    comment lines starting with '#' are skipped."""
    t = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#")).strip()
    if t and all(ch in "01_- \n\r\t|" for ch in t) and "," not in t:
        return [1 if ch in "1-" else 0 for ch in t if ch in "01_-"]
    out = []
    for line in t.splitlines():
        first = line.replace(";", ",").replace("\t", ",").split(",")[0].strip()
        try:
            out.append(1 if float(first) != 0 else 0)
        except ValueError:
            continue
    return out


def format_levels(c: "ChannelConfig", fmt: str = "txt") -> str:
    """Export a channel's steps in a form parse_levels() reads back.
    txt: '#' header + 0/1 characters (64 per line); csv: 'level,time_ms' rows."""
    head = (f"# SeqGen16 {c.name} (CH{c.ch}): {len(c.steps)} steps x {fmt_time(c.period_ms)}, "
            f"repeat {c.loops or 'forever'}, idle {c.idle}\n")
    if fmt == "csv":
        rows = [f"{v},{round(i * c.period_ms, 6):g}" for i, v in enumerate(c.steps)]
        return head + "level,time_ms\n" + "\n".join(rows) + "\n"
    bits = "".join("1" if v else "0" for v in c.steps)
    return head + "\n".join(bits[i:i + 64] for i in range(0, len(bits), 64)) + "\n"


# --------------------------------------------------------------------------- device
class SeqGen:
    def __init__(self, transport, timeout: float = 1.0):
        """transport: object with write(bytes) and read(n) (e.g. serial.Serial)."""
        self.t = transport
        self.timeout = timeout
        self.parser = FrameParser()
        self.info = None

    @classmethod
    def open_serial(cls, port: str, baud: int = 115200) -> "SeqGen":
        import serial
        ser = serial.Serial(port, baud, timeout=0.05)
        ser.reset_input_buffer()
        return cls(ser)

    def close(self):
        close = getattr(self.t, "close", None)
        if close:
            close()

    def transact(self, cmd: int, payload: bytes = b"") -> bytes:
        self.t.write(build_frame(cmd, payload))
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            data = self.t.read(512)
            if not data:
                continue
            for rcmd, rpayload, ok in self.parser.feed(data):
                if not ok:
                    raise DeviceError("response CRC error")
                if rcmd != (cmd | 0x80):
                    continue
                if not rpayload:
                    raise DeviceError("empty response")
                if rpayload[0] != 0:
                    raise DeviceError(f"command 0x{cmd:02X}: {STATUS_TEXT.get(rpayload[0], rpayload[0])}")
                return rpayload[1:]
        raise DeviceError(f"no response to command 0x{cmd:02X} (timeout)")

    # --- commands
    def ping(self) -> dict:
        r = self.transact(CMD_PING)
        hid, fmaj, fmin, nch, maxlen, clk, pmax = struct.unpack("<IBBBHII", r)
        self.info = dict(id=hid, fw=f"{fmaj}.{fmin}", nch=nch, maxlen=maxlen, clk_hz=clk, period_max_us=pmax + 1)
        return self.info

    def set_config(self, ch: int, period_us: int, length: int, loops: int = 0,
                   idle: int = 0, idle_on_done: bool = False):
        flags = 2 if idle_on_done else 0
        self.transact(CMD_SET_CFG, struct.pack("<BIHHBB", ch, period_us, length, loops, 1 if idle else 0, flags))

    def get_config(self, ch: int) -> dict:
        r = self.transact(CMD_GET_CFG, bytes([ch]))
        ch, per, length, loops, idle, flags, stat = struct.unpack("<BIHHBBI", r)
        return dict(ch=ch, period_us=per, length=length, loops=loops, idle=idle,
                    idle_on_done=bool(flags & 2), index=stat & 0x3FF,
                    running=bool(stat >> 16 & 1), done=bool(stat >> 17 & 1), output=stat >> 24 & 1)

    def set_aux(self, clock_period_us: int, sync_width_us: int):
        """BASE_CLK period (even, us) and SYNC pulse width (us, 0 = while running)."""
        self.transact(CMD_SET_AUX, struct.pack("<II", clock_period_us, sync_width_us))

    def get_aux(self) -> dict:
        per, sw = struct.unpack("<II", self.transact(CMD_GET_AUX))
        return dict(clock_period_us=per, sync_width_us=sw)

    def set_output_voltage(self, volts: float):
        """Output level of all outputs: 0 (off, high impedance), 1.8, 2.5, 3.3, 4.0 or 5.0 V.
        A change switches the outputs off for 2.5 ms before the new level is applied."""
        self.transact(CMD_SET_VOUT, bytes([vout_code(volts)]))

    def get_output_voltage(self) -> float:
        (code,) = self.transact(CMD_GET_VOUT)
        return VOUT_LEVELS[code]

    def write_steps(self, ch: int, steps: List[int], offset: int = 0):
        for i in range(0, len(steps), CHUNK):
            part = bytes(1 if s else 0 for s in steps[i:i + CHUNK])
            self.transact(CMD_WRITE, struct.pack("<BHH", ch, offset + i, len(part)) + part)

    def read_steps(self, ch: int, count: int, offset: int = 0) -> List[int]:
        out = []
        for i in range(0, count, CHUNK):
            n = min(CHUNK, count - i)
            out += list(self.transact(CMD_READ, struct.pack("<BHH", ch, offset + i, n)))
        return out

    def start(self, mask: int):
        self.transact(CMD_START, struct.pack("<H", mask & 0xFFFF))

    def stop(self, mask: int = 0xFFFF):
        self.transact(CMD_STOP, struct.pack("<H", mask & 0xFFFF))

    def status(self) -> dict:
        r = self.transact(CMD_STATUS)
        vals = struct.unpack("<HHH16H", r)
        return dict(running=vals[0], done=vals[1], outputs=vals[2], index=list(vals[3:]))

    # --- protocol tester (J6)
    def pt_configure(self, mode: int, div: int, **opts):
        """Raw configuration: mode PT_*, DIV register (see spi_div / i2c_div / uart_inc), pt_ctrl() options.
        Clears the queues of the tester."""
        self.transact(CMD_PT_CONFIG, struct.pack("<HI", pt_ctrl(mode, **opts), div))

    def pt_get_config(self) -> dict:
        ctrl, div, level = struct.unpack("<HIB", self.transact(CMD_PT_GET_CONFIG))
        return dict(mode=ctrl & 7, ctrl=ctrl, div=div, level=VOUT_LEVELS[level])

    def pt_set_level(self, volts: float):
        """Logic level of J6 (PVCC): 0 (off, all J6 lines high impedance), 1.8, 2.5, 3.3, 4.0 or 5.0 V."""
        self.transact(CMD_PT_LEVEL, bytes([vout_code(volts)]))

    def pt_xfer(self, entries, wait_ms: int = 0, clear: bool = True):
        """Send TX entries (<= 512) and collect RX entries; returns (flags, entries).
        wait_ms: keep receiving after the transmission (UART answers)."""
        entries = list(entries)
        if len(entries) > PT_FIFO or not 0 <= wait_ms <= 5000:
            raise ValueError("at most 512 entries and 0..5000 ms wait per transfer")
        old = self.timeout
        self.timeout = max(old, 2.5 + wait_ms / 1000)
        try:
            r = self.transact(CMD_PT_XFER, struct.pack(f"<BHH{len(entries)}H", 1 if clear else 0, wait_ms,
                                                       len(entries), *entries))
        finally:
            self.timeout = old
        flags, n = struct.unpack_from("<BH", r)
        out = list(struct.unpack_from(f"<{n}H", r, 3))
        if flags & PTF_TIMEOUT:
            raise DeviceError("protocol tester: transfer timeout (SCL held low by a device?)")
        if flags & PTF_BUSERR:
            raise DeviceError("protocol tester: I2C bus error (SCL held low > 21 ms)")
        return flags, out

    def pt_read(self, max_entries: int = PT_FIFO):
        """Entries received so far (e.g. UART data arriving without a transfer)."""
        r = self.transact(CMD_PT_READ, struct.pack("<H", max_entries))
        flags, n = struct.unpack_from("<BH", r)
        return flags, list(struct.unpack_from(f"<{n}H", r, 3))

    def pt_pins(self) -> dict:
        (v,) = self.transact(CMD_PT_PINS)
        return {name: v >> i & 1 for i, name in enumerate(PT_PIN_NAMES)}

    def spi_configure(self, hz: float, mode: int = 0, lsb_first: bool = False, sample_delay: int = 1) -> float:
        """SPI master, mode 0..3; returns the real SCK frequency."""
        div, real = spi_div(hz)
        self.pt_configure(PT_SPI, div, cpol=mode >> 1 & 1, cpha=mode & 1, lsb=lsb_first, sample_delay=sample_delay)
        return real

    def spi_transfer(self, data: bytes) -> bytes:
        """One CS-low transaction (full duplex); returns the bytes read on MISO.
        Longer than 512 bytes: several transfers, CS stays low between them."""
        data = bytes(data)
        out = bytearray()
        for i in range(0, len(data), PT_FIFO):
            ent = list(data[i:i + PT_FIFO])
            if i + PT_FIFO >= len(data):
                ent[-1] |= SPI_LAST
            _, rx = self.pt_xfer(ent)
            out += bytes(e & 0xFF for e in rx)
        return bytes(out)

    def i2c_configure(self, hz: float = 100_000) -> float:
        """I2C master; returns the real SCL frequency."""
        div, real = i2c_div(hz)
        self.pt_configure(PT_I2C, div)
        return real

    def i2c_transfer(self, addr: int, write: bytes = b"", read: int = 0) -> dict:
        """START, addr+W, write bytes, [repeated START, addr+R, read bytes (last one NACK)], STOP.
        Without write bytes and with read > 0 only the read part is sent.
        -> dict(ack: address acknowledged, data_acks: [bool per written byte], data: bytes read)."""
        if not 0 <= addr <= 0x7F:
            raise ValueError("I2C address must be 0x00..0x7F")
        write = bytes(write)
        ent = []
        nw = 0
        if write or not read:
            ent += [I2C_START, addr << 1] + list(write)
            nw = 1 + len(write)
        if read:
            ent += [I2C_START, addr << 1 | 1] + [I2C_RD_ACK] * (read - 1) + [I2C_RD_NACK]
        ent.append(I2C_STOP)
        if len(ent) > PT_FIFO:
            raise ValueError("I2C transfer too long (max ~500 bytes)")
        _, rx = self.pt_xfer(ent)
        acks = [not e >> 8 & 1 for e in rx[:nw]]
        res = dict(ack=bool(acks and acks[0]), data_acks=acks[1:], data=b"")
        if read:
            ra = len(rx) > nw and not rx[nw] >> 8 & 1
            res["ack"] = ra if not nw else res["ack"] and ra
            res["data"] = bytes(e & 0xFF for e in rx[nw + 1:nw + 1 + read])
        return res

    def i2c_scan(self, first: int = 0x08, last: int = 0x77):
        """Addresses that acknowledge (START, addr+W, STOP for every address)."""
        ent = []
        for a in range(first, last + 1):
            ent += [I2C_START, a << 1, I2C_STOP]
        _, rx = self.pt_xfer(ent)
        return [a for a, e in zip(range(first, last + 1), rx) if not e >> 8 & 1]

    def uart_configure(self, baud: float = 115200, parity: str = "N", stop_bits: int = 1) -> float:
        """UART 8 data bits, parity N/E/O, 1 or 2 stop bits; returns the real baud rate."""
        par = {"N": 0, "E": 1, "O": 2}[parity.upper()]
        inc, real = uart_inc(baud)
        self.pt_configure(PT_UART, inc, parity=par, stop2=stop_bits == 2)
        return real

    def uart_send(self, data: bytes, wait_ms: int = 50):
        """Send bytes on TX, receive on RX during the transmission + wait_ms.
        -> (received bytes, number of bytes with a framing / parity error)."""
        data = bytes(data)
        out, err = bytearray(), 0
        chunks = [data[i:i + PT_FIFO] for i in range(0, len(data), PT_FIFO)] or [b""]
        for k, part in enumerate(chunks):
            _, rx = self.pt_xfer(list(part), wait_ms if k == len(chunks) - 1 else 0, clear=False)
            out += bytes(e & 0xFF for e in rx)
            err += sum(1 for e in rx if e >> 8 & 1)
        return bytes(out), err

    def uart_receive(self):
        """Bytes received on RX since the last call -> (bytes, number with errors)."""
        _, rx = self.pt_read()
        return bytes(e & 0xFF for e in rx), sum(1 for e in rx if e >> 8 & 1)

    # --- high level
    def upload_channel(self, c: ChannelConfig, verify: bool = True):
        c.validate()
        self.stop(1 << c.ch)
        self.set_config(c.ch, round(c.period_ms * 1000), len(c.steps), c.loops, c.idle, c.idle_on_done)
        self.write_steps(c.ch, c.steps)
        if verify and self.read_steps(c.ch, len(c.steps)) != c.steps:
            raise DeviceError(f"channel {c.ch}: memory verification failed")

    def upload_project(self, prj: Project, verify: bool = True, progress=None) -> int:
        """Upload BASE_CLK/SYNC settings, the output level and all enabled channels;
        returns the mask of uploaded channels."""
        aux = prj.validate_aux()
        prj.validate_vout()
        enabled = [c for c in prj.channels if c.enabled]
        for c in enabled:
            c.validate()

        self.set_aux(*aux)
        self.set_output_voltage(prj.output_voltage)
        mask = 0
        for k, c in enumerate(enabled):
            self.upload_channel(c, verify)
            mask |= 1 << c.ch
            if progress:
                progress(k + 1, len(enabled), c.ch)
        return mask
