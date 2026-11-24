"""Tests for the PC library, run against the emulator:  python -m unittest test_seqgen -v"""
import os
import struct
import tempfile
import unittest

import patterns
from emulator import Emulator
from seqgen import (SeqGen, Project, ChannelConfig, DeviceError, FrameParser, fmt_time, split_time, format_levels,
                    build_frame, crc16, parse_levels, CMD_SET_CFG, CMD_SET_VOUT)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class TestFraming(unittest.TestCase):
    def test_crc_ccitt_false(self):
        self.assertEqual(crc16(b"123456789"), 0x29B1)      # CRC-16/CCITT-FALSE reference vector

    def test_parser_resync_and_split(self):
        f1 = build_frame(0x30, b"\x01\x00")
        f2 = build_frame(0x32)
        p = FrameParser()
        got = []
        stream = b"\x00\xA5\x11" + f1 + f2
        for i in range(0, len(stream), 3):
            got += p.feed(stream[i:i + 3])
        self.assertEqual(got, [(0x30, b"\x01\x00", True), (0x32, b"", True)])

    def test_bad_crc_detected(self):
        f = bytearray(build_frame(0x01))
        f[-1] ^= 0xFF
        self.assertFalse(FrameParser().feed(bytes(f))[0][2])


class TestPatterns(unittest.TestCase):
    def test_clock(self):
        self.assertEqual(patterns.clock(8, 2, 1), [1, 1, 0, 1, 1, 0, 1, 1])
        self.assertEqual(patterns.clock(6, 1, 2, phase=1), [0, 0, 1, 0, 0, 1])

    def test_pulse(self):
        self.assertEqual(patterns.pulse(6, 2, 3), [0, 0, 1, 1, 1, 0])

    def test_parse_levels(self):
        self.assertEqual(parse_levels("1100_-1"), [1, 1, 0, 0, 0, 1, 1])
        self.assertEqual(parse_levels("level\n0\n1\n1\n0\n"), [0, 1, 1, 0])
        self.assertEqual(parse_levels("t,v\n0;0.0\n2.5;1\n3.3,7"), [0, 1, 1])
        self.assertEqual(patterns.to_text([1, 0, 1]), "101")
        self.assertEqual(patterns.edges([0, 1, 1, 0, 1]), 3)


class TestDevice(unittest.TestCase):
    def setUp(self):
        self.clk = FakeClock()
        self.dev = SeqGen(Emulator(clock=self.clk), timeout=0.2)

    def test_ping(self):
        info = self.dev.ping()
        self.assertEqual(info["id"], 0x53513136)
        self.assertEqual((info["nch"], info["maxlen"], info["fw"]), (16, 1024, "2.2"))
        self.assertGreaterEqual(info["period_max_us"], 1_000_000)

    def test_write_read_1024(self):
        steps = patterns.rand(1024, 0.5, seed=1)
        self.dev.write_steps(9, steps)
        self.assertEqual(self.dev.read_steps(9, 1024), steps)

    def test_nonzero_written_as_one(self):
        self.dev.transact(0x20, struct.pack("<BHH", 0, 0, 3) + b"\x00\x05\xFF")
        self.assertEqual(self.dev.read_steps(0, 3), [0, 1, 1])

    def test_bad_args(self):
        with self.assertRaises(DeviceError):
            self.dev.write_steps(16, [1])
        with self.assertRaises(DeviceError):
            self.dev.write_steps(0, [1, 0], offset=1023)
        with self.assertRaises(DeviceError):
            self.dev.set_config(0, 2_000_000, 10)              # step > 1.048 s
        with self.assertRaises(DeviceError):
            self.dev.transact(CMD_SET_CFG, b"\x00")            # bad length

    def test_aux_roundtrip(self):
        self.assertEqual(self.dev.get_aux(), dict(clock_period_us=1000, sync_width_us=1000))   # defaults
        self.dev.set_aux(20, 0)
        self.assertEqual(self.dev.get_aux(), dict(clock_period_us=20, sync_width_us=0))
        self.dev.set_aux(2_097_152, 1_048_575)                                                # maxima
        for per, sw in ((3, 10), (0, 10), (2_097_154, 10), (1000, 1_048_576)):                # odd / 0 / too big
            with self.assertRaises(DeviceError):
                self.dev.set_aux(per, sw)

    def test_output_voltage(self):
        self.assertEqual(self.dev.get_output_voltage(), 0.0)          # off after reset
        for v in (1.8, 2.5, 3.3, 4.0, 5.0, 0.0):
            self.dev.set_output_voltage(v)
            self.assertEqual(self.dev.get_output_voltage(), v)
        with self.assertRaises(ValueError):
            self.dev.set_output_voltage(3.0)
        with self.assertRaises(DeviceError):
            self.dev.transact(CMD_SET_VOUT, b"")                   # level out of range
        with self.assertRaises(DeviceError):
            self.dev.transact(CMD_SET_VOUT, b"")                       # bad length

    def test_1us_step(self):
        c = ChannelConfig(ch=4, enabled=True, period_ms=0.001, loops=1, steps=[1, 0] * 5)
        self.dev.upload_channel(c)
        self.assertEqual(self.dev.get_config(4)["period_us"], 1)
        self.dev.start(1 << 4)
        self.clk.t = 5.5e-6
        st = self.dev.status()
        self.assertEqual((st["running"] >> 4 & 1, st["index"][4]), (1, 5))
        self.clk.t = 10.5e-6
        self.assertEqual(self.dev.status()["done"] >> 4 & 1, 1)

    def test_config_roundtrip(self):
        self.dev.set_config(3, 1_000_000, 1024, loops=5, idle=1, idle_on_done=True)
        c = self.dev.get_config(3)
        self.assertEqual((c["period_us"], c["length"], c["loops"], c["idle"], c["idle_on_done"]),
                         (1_000_000, 1024, 5, 1, True))

    def test_run_loops_done_and_outputs(self):
        self.dev.write_steps(0, [1, 0, 1, 1, 0, 0, 1, 0, 1, 1])
        self.dev.set_config(0, 1000, 10, loops=2, idle=0, idle_on_done=False)   # 10 x 1 ms, twice
        self.dev.set_config(1, 1000, 10, loops=0, idle=1)
        self.dev.start(0b11)
        self.clk.t = 0.0135                                     # step 13 -> index 3 of second pass
        st = self.dev.status()
        self.assertEqual(st["running"] & 3, 3)
        self.assertEqual(st["index"][0], 3)
        self.assertEqual(st["outputs"] & 1, 1)
        self.clk.t = 0.0201
        st = self.dev.status()
        self.assertEqual(st["running"] & 3, 2)                  # ch0 finished, ch1 looping
        self.assertEqual(st["done"] & 1, 1)
        self.assertEqual(st["outputs"] & 1, 1)                  # holds last step (=1)
        self.dev.stop(0b10)
        st = self.dev.status()
        self.assertEqual(st["running"], 0)
        self.assertEqual(st["outputs"] >> 1 & 1, 1)             # stopped -> idle level 1

    def test_project_upload_and_json(self):
        prj = Project()
        for ch, per in ((0, 1), (7, 37), (15, 1000)):
            c = prj.channels[ch]
            c.enabled, c.period_ms, c.name = True, per, f"SIG{ch}"
            c.steps = patterns.clock(1024, ch + 1, 3)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.json")
            prj.save(path)
            prj2 = Project.load(path)
        self.assertEqual(prj2.channels[7].name, "SIG7")
        self.assertEqual(prj2.output_voltage, 3.3)                     # default
        prj2.clock_period_ms, prj2.sync_width_ms, prj2.output_voltage = 0.5, 0, 2.5
        mask = self.dev.upload_project(prj2)
        self.assertEqual(self.dev.get_aux(), dict(clock_period_us=500, sync_width_us=0))
        self.assertEqual(self.dev.get_output_voltage(), 2.5)
        self.assertEqual(mask, (1 << 0) | (1 << 7) | (1 << 15))
        self.assertEqual(self.dev.get_config(7)["period_us"], 37_000)
        self.assertEqual(self.dev.read_steps(15, 1024), prj.channels[15].steps)


class TestModel(unittest.TestCase):
    def test_validation(self):
        for bad in (dict(period_ms=0.0005), dict(period_ms=0), dict(period_ms=1001), dict(period_ms=1.0005),
                    dict(steps=[]), dict(steps=[0] * 1025), dict(loops=70000)):
            c = ChannelConfig(ch=0, **{"steps": [1], **bad})
            with self.assertRaises(ValueError):
                c.validate()
        ChannelConfig(ch=0, period_ms=1, steps=[0] * 1024).validate()
        ChannelConfig(ch=0, period_ms=1000, steps=[1]).validate()
        ChannelConfig(ch=0, period_ms=2.5, steps=[1]).validate()
        ChannelConfig(ch=0, period_ms=0.001, steps=[1]).validate()      # 1 us
        ChannelConfig(ch=0, period_ms=0.25, steps=[1]).validate()       # 250 us

    def test_export_import_roundtrip(self):
        c = ChannelConfig(ch=3, name="DATA", period_ms=0.25, steps=patterns.rand(1000, 0.4, seed=7))
        for fmt in ("txt", "csv"):
            text = format_levels(c, fmt)
            self.assertTrue(text.startswith("# SeqGen16 DATA (CH3): 1000 steps x 250 µs"))
            self.assertEqual(parse_levels(text), c.steps, fmt)
        self.assertIn("1,0.5" if c.steps[2] else "0,0.5", format_levels(c, "csv"))     # step 2 at 0.5 ms

    def test_time_format(self):
        self.assertEqual([fmt_time(x) for x in (0.001, 0.25, 1, 2.5, 1000, 1500)],
                         ["1 µs", "250 µs", "1 ms", "2.5 ms", "1 s", "1.5 s"])
        self.assertEqual(split_time(0.001), (1, "µs"))
        self.assertEqual(split_time(1.5), (1.5, "ms"))
        self.assertEqual(split_time(1.0005), (1000.5, "µs"))
        self.assertEqual(split_time(2000), (2, "s"))

    def test_aux_validation(self):
        p = Project()
        self.assertEqual(p.validate_aux(), (1000, 1000))
        for per, sw in ((0.001, 1), (0.0015, 1), (3000, 1), (1, 2000), (1, -1)):
            p.clock_period_ms, p.sync_width_ms = per, sw
            with self.assertRaises(ValueError):
                p.validate_aux()

    def test_resize_and_normalize(self):
        c = ChannelConfig(ch=2, steps=[0, 5, 0, 1])
        self.assertEqual(c.steps, [0, 1, 0, 1])
        self.assertEqual(c.name, "CH2")
        c.resize(6)
        self.assertEqual(c.steps, [0, 1, 0, 1, 1, 1])
        c.resize(2)
        self.assertEqual(c.steps, [0, 1])
        c.resize(5000)
        self.assertEqual(len(c.steps), 1024)


class TestWireFormat(unittest.TestCase):
    """Byte layout matching fw/main.c (SET_CFG = 11 B)."""

    def test_set_cfg_layout(self):
        sent = []

        class T:
            def write(self, b): sent.append(b)
            def read(self, n): return build_frame(0x90, b"\x00")
        SeqGen(T(), timeout=0.1).set_config(2, 1000, 1024, 3, 1, True)
        f = sent[0]
        self.assertEqual(f[:5], b"\xA5\x5A\x10\x0B\x00")
        self.assertEqual(f[5:16], struct.pack("<BIHHBB", 2, 1000, 1024, 3, 1, 2))


if __name__ == "__main__":
    unittest.main()
