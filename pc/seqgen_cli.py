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
"""
import argparse
import sys

from seqgen import SeqGen, Project, NCH, fmt_time, vout_text
import patterns


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
    finally:
        dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
