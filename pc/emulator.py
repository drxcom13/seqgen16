"""emulator.py - software model of the board (firmware + seq_gen16) for use without hardware.

Usage:  SeqGen(Emulator())  instead of  SeqGen.open_serial("COM5")
or port name "EMU" in the GUI / CLI.
Behaviour mirrors fw/main.c and quartus/ip/seq_gen16/seq_gen16.vhd.
"""
import struct
import time

from seqgen import (FrameParser, build_frame, NCH, MAXLEN,
                    CMD_PING, CMD_SET_CFG, CMD_GET_CFG, CMD_SET_AUX, CMD_GET_AUX, CMD_WRITE, CMD_READ,
                    CMD_START, CMD_STOP, CMD_STATUS, CMD_SET_VOUT, CMD_GET_VOUT)

CLK_HZ = 50_000_000
PERIOD_MAX = (1 << 20) - 1      # PERIOD register: microseconds - 1
ID = 0x53513136


class _Ch:
    def __init__(self):
        self.mem = [0] * MAXLEN
        self.period = 999          # us - 1 (1 ms)
        self.last = MAXLEN - 1
        self.loops = 0
        self.idle = 0
        self.idle_on_done = False
        self.t0 = None             # start time (s) or None
        self.done_flag = False

    def state(self, now):
        """-> (running, done, index, output) computed from time since start."""
        if self.t0 is None:
            return False, False, 0, self.idle
        step = (self.period + 1) / 1e6
        steps = int((now - self.t0) / step)
        n = self.last + 1
        if self.loops and steps >= n * self.loops:
            out = self.idle if self.idle_on_done else self.mem[self.last]
            return False, True, self.last, out
        idx = steps % n
        return True, False, idx, self.mem[idx]


class Emulator:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.ch = [_Ch() for _ in range(NCH)]
        self.parser = FrameParser()
        self.out = bytearray()
        self.clock_period_us = 1000
        self.sync_width_us = 1000
        self.vout_level = 0            # 0 = off, 1..5 = 1.8 / 2.5 / 3.3 / 4.0 / 5 V

    # --- transport interface (like serial.Serial)
    def write(self, data: bytes):
        for cmd, payload, ok in self.parser.feed(data):
            if not ok:
                self._reply(cmd, 4)
            else:
                self._handle(cmd, payload)
        return len(data)

    def read(self, n: int) -> bytes:
        r = bytes(self.out[:n])
        del self.out[:n]
        return r

    def close(self):
        pass

    # --- logic
    def _reply(self, cmd, status, data=b""):
        self.out += build_frame(cmd | 0x80, bytes([status]) + data)

    def _handle(self, cmd, p):
        now = self.clock()
        if cmd == CMD_PING:
            if p:
                return self._reply(cmd, 2)
            return self._reply(cmd, 0, struct.pack("<IBBBHII", ID, 2, 2, NCH, MAXLEN, CLK_HZ, PERIOD_MAX))
        if cmd == CMD_SET_CFG:
            if len(p) != 11:
                return self._reply(cmd, 2)
            ch, per_us, length, loops, idle, flags = struct.unpack("<BIHHBB", p)
            if ch >= NCH or per_us == 0 or not 1 <= length <= MAXLEN or per_us > PERIOD_MAX + 1:
                return self._reply(cmd, 3)
            c = self.ch[ch]
            c.period, c.last, c.loops = per_us - 1, length - 1, loops
            c.idle, c.idle_on_done = 1 if idle else 0, bool(flags & 2)
            return self._reply(cmd, 0)
        if cmd == CMD_SET_AUX:
            if len(p) != 8:
                return self._reply(cmd, 2)
            per, sw = struct.unpack("<II", p)
            if per < 2 or per % 2 or per // 2 - 1 > PERIOD_MAX or sw > PERIOD_MAX:
                return self._reply(cmd, 3)
            self.clock_period_us, self.sync_width_us = per, sw
            return self._reply(cmd, 0)
        if cmd == CMD_GET_AUX:
            if p:
                return self._reply(cmd, 2)
            return self._reply(cmd, 0, struct.pack("<II", self.clock_period_us, self.sync_width_us))
        if cmd == CMD_SET_VOUT:
            if len(p) != 1:
                return self._reply(cmd, 2)
            if p[0] > 5:
                return self._reply(cmd, 3)
            self.vout_level = p[0]
            return self._reply(cmd, 0)
        if cmd == CMD_GET_VOUT:
            if p:
                return self._reply(cmd, 2)
            return self._reply(cmd, 0, bytes([self.vout_level]))
        if cmd == CMD_GET_CFG:
            if len(p) != 1:
                return self._reply(cmd, 2)
            if p[0] >= NCH:
                return self._reply(cmd, 3)
            c = self.ch[p[0]]
            run, done, idx, out = c.state(now)
            stat = idx | (run << 16) | (done << 17) | (out << 24)
            return self._reply(cmd, 0, struct.pack("<BIHHBBI", p[0], c.period + 1, c.last + 1, c.loops,
                                                   c.idle, 2 if c.idle_on_done else 0, stat))
        if cmd == CMD_WRITE:
            if len(p) < 5:
                return self._reply(cmd, 2)
            ch, off, cnt = struct.unpack_from("<BHH", p)
            if len(p) != 5 + cnt:
                return self._reply(cmd, 2)
            if ch >= NCH or cnt == 0 or off + cnt > MAXLEN:
                return self._reply(cmd, 3)
            self.ch[ch].mem[off:off + cnt] = [1 if b else 0 for b in p[5:]]
            return self._reply(cmd, 0)
        if cmd == CMD_READ:
            if len(p) != 5:
                return self._reply(cmd, 2)
            ch, off, cnt = struct.unpack("<BHH", p)
            if ch >= NCH or cnt == 0 or off + cnt > MAXLEN:
                return self._reply(cmd, 3)
            return self._reply(cmd, 0, bytes(self.ch[ch].mem[off:off + cnt]))
        if cmd in (CMD_START, CMD_STOP):
            if len(p) != 2:
                return self._reply(cmd, 2)
            (mask,) = struct.unpack("<H", p)
            for i in range(NCH):
                if mask >> i & 1:
                    self.ch[i].t0 = now if cmd == CMD_START else None
            return self._reply(cmd, 0)
        if cmd == CMD_STATUS:
            if p:
                return self._reply(cmd, 2)
            run = done = outs = 0
            idx = []
            for i, c in enumerate(self.ch):
                r, d, k, o = c.state(now)
                run |= r << i
                done |= d << i
                outs |= o << i
                idx.append(k)
            return self._reply(cmd, 0, struct.pack("<HHH16H", run, done, outs, *idx))
        return self._reply(cmd, 1)
