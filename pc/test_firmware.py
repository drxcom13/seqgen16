"""Runs the device test-suite against the REAL firmware binary (fw/build/fw.bin)
executed on the RV32I simulator in fw_sim.py.

    python -m unittest test_firmware -v        (build the firmware first: ..\\fw\\build.ps1)
"""
import os
import struct
import unittest

import test_seqgen
from fw_sim import FirmwareSim, DEFAULT_BIN
from seqgen import SeqGen, build_frame, FrameParser


@unittest.skipUnless(os.path.exists(DEFAULT_BIN), "firmware not built (run fw/build.ps1)")
class TestFirmwareDevice(test_seqgen.TestDevice):
    """Same scenarios as against the Python emulator - now with the compiled firmware."""

    def setUp(self):
        self.clk = test_seqgen.FakeClock()
        self.sim = FirmwareSim(clock=self.clk)
        self.dev = SeqGen(self.sim, timeout=2.0)


@unittest.skipUnless(os.path.exists(DEFAULT_BIN), "firmware not built (run fw/build.ps1)")
class TestFirmwareRobustness(unittest.TestCase):
    def setUp(self):
        self.sim = FirmwareSim()
        self.dev = SeqGen(self.sim, timeout=2.0)

    def raw(self, data):
        self.sim.write(data)
        return FrameParser().feed(self.sim.read(4096))

    def test_registers_written(self):
        self.dev.set_config(5, 250_000, 100, loops=3, idle=1, idle_on_done=True)
        writes = [w for w in self.sim.cfg_writes if w[0] == 5][-3:]
        self.assertEqual(writes, [(5, 0, 249_999), (5, 1, 99), (5, 2, 3 | (1 << 16) | (1 << 25))])

    def test_aux_registers_written(self):
        self.assertEqual((self.sim.bclk_half, self.sim.sync_w), (499, 1000))    # written at boot
        self.dev.set_aux(200, 0)
        self.assertEqual((self.sim.bclk_half, self.sim.sync_w), (99, 0))

    def test_vccb_register_written(self):
        self.dev.ping()
        self.assertEqual(self.sim.vccb_writes, [0])                  # boot: outputs off
        for v, bits in ((3.3, 0x5), (1.8, 0x1), (2.5, 0x3), (4.0, 0x7), (5.0, 0x9), (0.0, 0x0)):
            self.dev.set_output_voltage(v)
            self.assertEqual(self.sim.vccb_writes[-1], bits)
        n = len(self.sim.vccb_writes)
        self.dev.set_output_voltage(0.0)                             # same level -> no write (no 2.5 ms gap)
        self.assertEqual(len(self.sim.vccb_writes), n)

    def test_bad_crc_reply(self):
        f = bytearray(build_frame(0x01))
        f[-1] ^= 0x55
        (cmd, payload, ok), = self.raw(bytes(f))
        self.assertTrue(ok)
        self.assertEqual((cmd, payload[0]), (0x81, 4))

    def test_garbage_then_valid_frame(self):
        frames = self.raw(b"\x00\xA5\xA5\x12\x34" + build_frame(0x32))
        self.assertEqual(frames[-1][0], 0xB2)
        self.assertEqual(frames[-1][1][0], 0)
        self.assertEqual(len(frames[-1][1]), 1 + 6 + 32)

    def test_unknown_command(self):
        (cmd, payload, ok), = self.raw(build_frame(0x55))
        self.assertEqual((cmd, payload[0]), (0xD5, 1))

    def test_oversize_length(self):
        frames = self.raw(b"\xA5\x5A\x20" + struct.pack("<H", 5000))
        self.assertEqual(frames[0][1][0], 2)               # BAD_LEN, parser resynchronised
        self.assertEqual(self.dev.ping()["fw"], "2.2")

    def test_full_upload_speed(self):
        self.dev.write_steps(0, [i & 1 for i in range(1024)])
        n0 = self.sim.icount
        self.dev.read_steps(0, 1024)
        per_byte = (self.sim.icount - n0) / 1024
        # at 115200 bd one byte lasts 86.8 us; Nios V/c @ 50 MHz executes several thousand instr. in that time
        self.assertLess(per_byte, 200)


if __name__ == "__main__":
    unittest.main()
