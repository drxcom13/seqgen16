"""seqgen.py - PC library for the 16-channel digital sequence generator (MAX 10 + Nios V).

Protocol: see ../PROTOCOL.md. Transport: RS-232 115200 8N1 (pyserial)
or the in-memory emulator (emulator.py) for working without hardware.
"""
from __future__ import annotations

import json
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
        if not 0 <= self.ch < NCH:
            raise ValueError(f"channel {self.ch}: number out of range 0..{NCH - 1}")
        if not PERIOD_MIN_MS <= self.period_ms <= PERIOD_MAX_MS:
            raise ValueError(f"channel {self.ch}: step {fmt_time(self.period_ms)} out of range 1 us .. 1 s")
        if abs(self.period_ms * 1000 - round(self.period_ms * 1000)) > 1e-6:
            raise ValueError(f"channel {self.ch}: step must be a multiple of 1 us")
        if not 1 <= len(self.steps) <= MAXLEN:
            raise ValueError(f"channel {self.ch}: {len(self.steps)} steps, allowed 1..{MAXLEN}")
        if not 0 <= self.loops <= 0xFFFF:
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
        self.set_aux(*prj.validate_aux())
        prj.validate_vout()
        self.set_output_voltage(prj.output_voltage)
        mask = 0
        enabled = [c for c in prj.channels if c.enabled]
        for k, c in enumerate(enabled):
            self.upload_channel(c, verify)
            mask |= 1 << c.ch
            if progress:
                progress(k + 1, len(enabled), c.ch)
        return mask
