"""seqgen_cli.py - command-line sequence programmer.

Examples:
  python seqgen_cli.py ports
  python seqgen_cli.py -p COM5 ping
  python seqgen_cli.py -p COM5 upload project.json --start
  python seqgen_cli.py -p COM5 start 0,1,5        (or: all, 0-3, 0x00FF)
  python seqgen_cli.py -p COM5 stop all
  python seqgen_cli.py -p COM5 status
  python seqgen_cli.py -p COM5 vout 3.3           (output level: off, 1.8, 2.5, 3.3, 4.0, 5.0; no value = read)
  python seqgen_cli.py -p EMU  ping               (emulator, no hardware)
  python seqgen_cli.py example project.json       (create an example project)
  python seqgen_cli.py show project.json          (print sequences as text)

Protocol tester (connector J6; --level sets the J6 logic level first, otherwise the current one is kept):
  python seqgen_cli.py -p COM5 pt-level 3.3                       (J6 level: off, 1.8 ... 5.0; no value = read)
  python seqgen_cli.py -p COM5 spi "9F 00 00 00" --hz 1e6 --mode 0 --level 3.3
  python seqgen_cli.py -p COM5 i2c-scan --hz 100e3
  python seqgen_cli.py -p COM5 i2c 0x50 --write "00 10" --read 4   (write, repeated START, read)
  python seqgen_cli.py -p COM5 uart '"AT\\r\\n"' --baud 115200 --wait 200
  python seqgen_cli.py -p COM5 pins                               (J6 line states)
"""
import argparse
import sys

from seqgen import SeqGen, Project, NCH, fmt_time, vout_text, parse_bytes, format_bytes
import patterns


def parse_level(text: str) -> float:
    return 0.0 if text.lower() == "off" else float(text)


def ascii_view(data: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)


def parse_mask(s: str) -> int:
    if s.lower() == "all":
        return (1 << NCH) - 1
    if s.lower().startswith("0x"):
        return int(s, 16)
    m = 0
    for part in s.split(","):
        if "-" in part:
            a, b = map(int, part.split("-"))
            for i in range(a, b + 1):
                m |= 1 << i
        else:
            m |= 1 << int(part)
    return m


def open_dev(port: str) -> SeqGen:
    if port.upper() == "EMU":
        from emulator import Emulator
        return SeqGen(Emulator())
    return SeqGen.open_serial(port)


def make_example(path: str):
    """Ch0..7: clocks dividing by 2,4,8..; ch8: single pulse; ch9: 1 s blink; ch10-15 off."""
    prj = Project()
    for k in range(8):
        c = prj.channels[k]
        c.enabled, c.period_ms = True, 1
        c.steps = patterns.clock(1024, 1 << k, 1 << k)
        c.name = f"CLK/{2 << k}"
    c = prj.channels[8]
    c.enabled, c.name, c.period_ms, c.loops = True, "PULSE", 10, 1
    c.steps = patterns.pulse(100, 10, 5)
    c = prj.channels[9]
    c.enabled, c.name, c.period_ms = True, "BLINK_1s", 1000
    c.steps = [1, 0]
    prj.save(path)
    print(f"saved {path}")


def protocol_tester(dev: SeqGen, a) -> int:
    try:
        if a.cmd == "pt-level":
            if a.level is not None:
                dev.pt_set_level(parse_level(a.level))
            print(f"J6 level: {vout_text(dev.pt_get_config()['level'])}")
            return 0
        if a.cmd == "pins":
            print("  ".join(f"{k}={v}" for k, v in dev.pt_pins().items()))
            return 0
        if getattr(a, "level", None) is not None:
            dev.pt_set_level(parse_level(a.level))
        if dev.pt_get_config()["level"] == 0:
            print("warning: J6 level is off (lines high impedance) - use --level", file=sys.stderr)
        if a.cmd == "spi":
            real = dev.spi_configure(a.hz, a.mode, a.lsb, a.delay)
            data = parse_bytes(a.data)
            rx = dev.spi_transfer(data)
            print(f"SPI mode {a.mode}, {real / 1e6:.4g} MHz")
            print(f"MOSI: {format_bytes(data)}")
            print(f"MISO: {format_bytes(rx)}")
        elif a.cmd == "i2c":
            addr = int(a.addr, 0)
            dev.i2c_configure(a.hz)
            r = dev.i2c_transfer(addr, parse_bytes(a.write), a.read)
            print(f"address 0x{addr:02X}: {'ACK' if r['ack'] else 'NACK'}")
            if r["data_acks"]:
                print("written: " + " ".join("ACK" if k else "NACK" for k in r["data_acks"]))
            if a.read:
                print(f"read: {format_bytes(r['data'])}")
            return 0 if r["ack"] else 1
        elif a.cmd == "i2c-scan":
            real = dev.i2c_configure(a.hz)
            found = dev.i2c_scan()
            print(f"I2C {real / 1e3:.4g} kHz: " + (", ".join(f"0x{x:02X}" for x in found) or "no devices"))
        elif a.cmd == "uart":
            real = dev.uart_configure(a.baud, a.parity, a.stop)
            data = parse_bytes(a.data) if a.data else b""
            rx, err = dev.uart_send(data, a.wait)
            print(f"UART {real:.0f} Bd 8{a.parity}{a.stop}")
            if data:
                print(f"TX: {format_bytes(data)}")
            print(f"RX: {format_bytes(rx)}   |{ascii_view(rx)}|" + (f"   ({err} with errors)" if err else ""))
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="16-channel digital sequence generator programmer")
    ap.add_argument("-p", "--port", help="serial port (e.g. COM5) or EMU")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ports", help="list serial ports")
    sub.add_parser("ping", help="identify the device")
    u = sub.add_parser("upload", help="upload a JSON project")
    u.add_argument("project")
    u.add_argument("--start", action="store_true", help="start all uploaded channels together afterwards")
    u.add_argument("--no-verify", action="store_true")
    s = sub.add_parser("start", help="start channels (synchronously)")
    s.add_argument("mask", nargs="?", default="all")
    s = sub.add_parser("stop", help="stop channels")
    s.add_argument("mask", nargs="?", default="all")
    sub.add_parser("status", help="channel status")
    v = sub.add_parser("vout", help="set / read the output level (all outputs)")
    v.add_argument("level", nargs="?", help="off, 1.8, 2.5, 3.3, 4.0 or 5.0 (volts)")
    r = sub.add_parser("config", help="read channel configuration")
    r.add_argument("ch", type=int)
    e = sub.add_parser("example", help="create an example project")
    e.add_argument("path")
    w = sub.add_parser("show", help="print a project's sequences")
    w.add_argument("path")
    t = sub.add_parser("pt-level", help="set / read the J6 logic level (protocol tester)")
    t.add_argument("level", nargs="?", help="off, 1.8, 2.5, 3.3, 4.0 or 5.0 (volts)")
    t = sub.add_parser("spi", help="SPI master transfer on J6 (one CS-low transaction)")
    t.add_argument("data", help='bytes in hex ("9F 00 00") or a quoted string')
    t.add_argument("--hz", type=float, default=1e6, help="SCK frequency (max 12.5 MHz)")
    t.add_argument("--mode", type=int, choices=range(4), default=0)
    t.add_argument("--lsb", action="store_true", help="LSB first")
    t.add_argument("--delay", type=int, choices=range(8), default=1, help="MISO sample delay [20 ns clocks]")
    t.add_argument("--level")
    t = sub.add_parser("i2c", help="I2C master transfer on J6")
    t.add_argument("addr", help="7-bit address, e.g. 0x50")
    t.add_argument("--write", default="", help="bytes to write (hex)")
    t.add_argument("--read", type=int, default=0, help="number of bytes to read")
    t.add_argument("--hz", type=float, default=100e3, help="SCL frequency (max 1 MHz)")
    t.add_argument("--level")
    t = sub.add_parser("i2c-scan", help="list I2C addresses that acknowledge")
    t.add_argument("--hz", type=float, default=100e3)
    t.add_argument("--level")
    t = sub.add_parser("uart", help="UART on J6: send bytes and print what arrives on RX")
    t.add_argument("data", nargs="?", default="", help="bytes (hex or quoted string); empty = receive only")
    t.add_argument("--baud", type=float, default=115200)
    t.add_argument("--parity", choices="NEO", default="N")
    t.add_argument("--stop", type=int, choices=(1, 2), default=1)
    t.add_argument("--wait", type=int, default=100, help="receive time after sending [ms], max 5000")
    t.add_argument("--level")
    sub.add_parser("pins", help="J6 line states")
    a = ap.parse_args(argv)

    if a.cmd == "ports":
        from serial.tools import list_ports
        for p in list_ports.comports():
            print(f"{p.device:8} {p.description}")
        return 0
    if a.cmd == "example":
        make_example(a.path)
        return 0
    if a.cmd == "show":
        for c in Project.load(a.path).channels:
            flag = "on " if c.enabled else "off"
            print(f"CH{c.ch:<2} {flag} {c.name:<10} {fmt_time(c.period_ms)} x {len(c.steps)}: {patterns.to_text(c.steps[:64])}"
                  + ("..." if len(c.steps) > 64 else ""))
        return 0
    if not a.port:
        ap.error("specify a port: -p COMx (or EMU)")

    dev = open_dev(a.port)
    try:
        if a.cmd == "ping":
            print(dev.ping())
        elif a.cmd == "upload":
            prj = Project.load(a.project)
            dev.ping()
            mask = dev.upload_project(prj, verify=not a.no_verify,
                                      progress=lambda k, n, ch: print(f"  [{k}/{n}] channel {ch} OK"))
            print(f"uploaded channels: mask 0x{mask:04X}, output level {vout_text(prj.output_voltage)}")
            if a.start:
                dev.start(mask)
                print("started")
        elif a.cmd == "start":
            dev.start(parse_mask(a.mask))
        elif a.cmd == "stop":
            dev.stop(parse_mask(a.mask))
        elif a.cmd == "status":
            st = dev.status()
            for ch in range(NCH):
                state = "RUN " if st["running"] >> ch & 1 else ("DONE" if st["done"] >> ch & 1 else "stop")
                print(f"  ch{ch:2}: {state}  out={st['outputs'] >> ch & 1}  step {st['index'][ch]}")
        elif a.cmd == "vout":
            if a.level is not None:
                try:
                    dev.set_output_voltage(0.0 if a.level.lower() == "off" else float(a.level))
                except ValueError as e:
                    print(f"error: {e}", file=sys.stderr)
                    return 2
            print(f"output level: {vout_text(dev.get_output_voltage())}")
        elif a.cmd == "config":
            print(dev.get_config(a.ch))
        elif a.cmd in ("pt-level", "spi", "i2c", "i2c-scan", "uart", "pins"):
            return protocol_tester(dev, a)
    finally:
        dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
