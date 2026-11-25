"""fw_sim.py - runs the REAL compiled firmware (fw/build/fw.bin) on a small RV32I
instruction-set simulator, with models of the UART and seq_gen16 registers.

Used as a transport for SeqGen, so the PC library talks to the actual firmware binary:
    dev = SeqGen(FirmwareSim("../fw/build/fw.bin"))
"""
import os
import time

from emulator import _Ch, ID, CLK_HZ
from seqgen import NCH, MAXLEN

RAM_SIZE = 0x4000
UART_BASE = 0x21000
SG_BASE = 0x40000
SG_END = SG_BASE + 0x20000

DEFAULT_BIN = os.path.join(os.path.dirname(__file__), "..", "fw", "build", "fw.bin")


def _sx(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


class CpuError(Exception):
    pass


class FirmwareSim:
    def __init__(self, bin_path=DEFAULT_BIN, clock=time.monotonic):
        self.clock = clock
        self.mem = bytearray(RAM_SIZE)
        with open(bin_path, "rb") as firmware:
            img = firmware.read()
        self.mem[:len(img)] = img
        self.x = [0] * 32
        self.pc = 0
        self.icount = 0
        self.rx = bytearray()
        self.tx = bytearray()
        self.idle_polls = 0
        self.ch = [_Ch() for _ in range(NCH)]
        self.cfg_writes = []           # (ch, reg, value) log for tests
        self.bclk_half = 499           # registers 7 / 8 (reset values as in VHDL)
        self.sync_w = 1000
        self.vccb_writes = []          # register 9 (VCCB) write log
        self.run(200_000)              # boot: crt0 + init loop until first idle poll

    # ------------------------------------------------------------------ transport
    def write(self, data: bytes):
        self.rx += data
        self.run()
        return len(data)

    def read(self, n):
        r = bytes(self.tx[:n])
        del self.tx[:n]
        return r

    def close(self):
        pass

    # ------------------------------------------------------------------ MMIO
    def _load32_io(self, a):
        if UART_BASE <= a < UART_BASE + 0x20:
            off = a - UART_BASE
            if off == 0x00:                                   # RXDATA
                return self.rx.pop(0) if self.rx else 0
            if off == 0x08:                                   # STATUS
                if self.rx:
                    self.idle_polls = 0
                    return (1 << 7) | (1 << 6) | (1 << 5)
                self.idle_polls += 1
                return (1 << 6) | (1 << 5)
            return 0
        if SG_BASE <= a < SG_END:
            w = (a - SG_BASE) >> 2
            now = self.clock()
            if w & 0x4000:
                ch, i = (w >> 10) & 0xF, w & 0x3FF
                return self.ch[ch].mem[i]
            if (w >> 6) == 1:
                ch, r = (w >> 2) & 0xF, w & 3
                if r != 3:
                    return 0                                  # write-only registers
                run, done, idx, out = self.ch[ch].state(now)
                return idx | (run << 16) | (done << 17) | (out << 24)
            if w == 0:
                return ID
            if w in (3, 4):
                m = 0
                for i, c in enumerate(self.ch):
                    run, done, _, _ = c.state(now)
                    m |= (run if w == 3 else done) << i
                return m
            if w == 5:
                return CLK_HZ
            if w == 6:
                return (20 << 24) | (1 << 16) | (10 << 8) | NCH
            return 0
        raise CpuError(f"load from unmapped address 0x{a:08X} at pc=0x{self.pc:X}")

    def _store32_io(self, a, v):
        if UART_BASE <= a < UART_BASE + 0x20:
            if a - UART_BASE == 0x04:
                self.tx.append(v & 0xFF)
                self.idle_polls = 0            # still transmitting -> not idle
            return
        if SG_BASE <= a < SG_END:
            w = (a - SG_BASE) >> 2
            now = self.clock()
            if w & 0x4000:
                self.ch[(w >> 10) & 0xF].mem[w & 0x3FF] = v & 1      # 1-bit RAM: only bit 0 stored
                return
            if (w >> 6) == 1:
                ch, r = (w >> 2) & 0xF, w & 3
                c = self.ch[ch]
                self.cfg_writes.append((ch, r, v))
                if r == 0:
                    c.period = v & 0xFFFFF
                elif r == 1:
                    c.last = v & 0x3FF
                elif r == 2:
                    c.loops, c.idle, c.idle_on_done = v & 0xFFFF, (v >> 16) & 1, bool(v >> 25 & 1)
                return
            if w == 7:
                self.bclk_half = v & 0xFFFFF
                return
            if w == 8:
                self.sync_w = v & 0xFFFFF
                return
            if w == 9:
                self.vccb_writes.append(v & 0xF)
                return
            if w in (1, 2):
                for i in range(NCH):
                    if v >> i & 1:
                        self.ch[i].t0 = now if w == 1 else None
            return
        raise CpuError(f"store to unmapped address 0x{a:08X} at pc=0x{self.pc:X}")

    def _load(self, a, size, signed):
        if a + size <= RAM_SIZE:
            v = int.from_bytes(self.mem[a:a + size], "little")
            return _sx(v, size * 8) if signed else v
        if size != 4:
            raise CpuError(f"non-word I/O access at 0x{a:08X}")
        return self._load32_io(a)

    def _store(self, a, size, v):
        if a + size <= RAM_SIZE:
            self.mem[a:a + size] = (v & ((1 << (size * 8)) - 1)).to_bytes(size, "little")
            return
        if size != 4:
            raise CpuError(f"non-word I/O access at 0x{a:08X}")
        self._store32_io(a, v & 0xFFFFFFFF)

    # ------------------------------------------------------------------ CPU
    def run(self, max_instr=20_000_000):
        """Execute until the firmware is idle (RX empty, polling UART), or the budget runs out."""
        self.idle_polls = 0
        x, mem = self.x, self.mem
        for _ in range(max_instr):
            if not self.rx and self.idle_polls > 3:
                return
            pc = self.pc
            if pc + 4 > RAM_SIZE or pc & 3:
                raise CpuError(f"bad pc 0x{pc:08X}")
            ins = int.from_bytes(mem[pc:pc + 4], "little")
            op = ins & 0x7F
            rd = (ins >> 7) & 31
            f3 = (ins >> 12) & 7
            rs1 = x[(ins >> 15) & 31]
            rs2 = x[(ins >> 20) & 31]
            f7 = ins >> 25
            npc = pc + 4
            val = None
            if op == 0x37:                                    # LUI
                val = ins & 0xFFFFF000
            elif op == 0x17:                                  # AUIPC
                val = (pc + (ins & 0xFFFFF000)) & 0xFFFFFFFF
            elif op == 0x6F:                                  # JAL
                imm = _sx(((ins >> 31) << 20) | (((ins >> 12) & 0xFF) << 12) |
                          (((ins >> 20) & 1) << 11) | (((ins >> 21) & 0x3FF) << 1), 21)
                val, npc = npc, (pc + imm) & 0xFFFFFFFF
            elif op == 0x67:                                  # JALR
                val, npc = npc, (rs1 + _sx(ins >> 20, 12)) & 0xFFFFFFFE
            elif op == 0x63:                                  # BRANCH
                imm = _sx(((ins >> 31) << 12) | (((ins >> 7) & 1) << 11) |
                          (((ins >> 25) & 0x3F) << 5) | (((ins >> 8) & 0xF) << 1), 13)
                a, b = _sx(rs1, 32), _sx(rs2, 32)
                take = {0: rs1 == rs2, 1: rs1 != rs2, 4: a < b, 5: a >= b, 6: rs1 < rs2, 7: rs1 >= rs2}.get(f3)
                if take is None:
                    raise CpuError(f"bad branch at 0x{pc:X}")
                if take:
                    npc = (pc + imm) & 0xFFFFFFFF
            elif op == 0x03:                                  # LOAD
                a = (rs1 + _sx(ins >> 20, 12)) & 0xFFFFFFFF
                size, signed = {0: (1, True), 1: (2, True), 2: (4, False), 4: (1, False), 5: (2, False)}[f3]
                val = self._load(a, size, signed) & 0xFFFFFFFF
            elif op == 0x23:                                  # STORE
                a = (rs1 + _sx(((ins >> 25) << 5) | ((ins >> 7) & 31), 12)) & 0xFFFFFFFF
                self._store(a, {0: 1, 1: 2, 2: 4}[f3], rs2)
            elif op in (0x13, 0x33):                          # OP-IMM / OP
                if op == 0x13:
                    b = _sx(ins >> 20, 12) & 0xFFFFFFFF
                    sub = False
                    sra = f3 == 5 and (ins >> 30) & 1
                else:
                    if f7 == 1:
                        raise CpuError(f"M-extension instruction at 0x{pc:X} (not in Nios V/c)")
                    b = rs2
                    sub = f3 == 0 and f7 == 0x20
                    sra = f3 == 5 and f7 == 0x20
                sh = b & 31
                if f3 == 0:
                    val = rs1 - b if sub else rs1 + b
                elif f3 == 1:
                    val = rs1 << sh
                elif f3 == 2:
                    val = int(_sx(rs1, 32) < _sx(b, 32))
                elif f3 == 3:
                    val = int(rs1 < b)
                elif f3 == 4:
                    val = rs1 ^ b
                elif f3 == 5:
                    val = (_sx(rs1, 32) >> sh) if sra else (rs1 >> sh)
                elif f3 == 6:
                    val = rs1 | b
                else:
                    val = rs1 & b
                val &= 0xFFFFFFFF
            elif op == 0x0F:                                  # FENCE - nop
                pass
            else:
                raise CpuError(f"unsupported instruction 0x{ins:08X} at 0x{pc:X}")
            if val is not None and rd:
                x[rd] = val
            self.pc = npc
            self.icount += 1
        raise CpuError("instruction budget exhausted (firmware stuck?)")
