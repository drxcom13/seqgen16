"""sch_dump.py - list the parts of a sheet with their pins (library coordinates, rotation 0) and nets.

    python sch_dump.py SHEET.kicad_sch NETLIST.net
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sch_labels_to_wires import parse, L, num  # noqa: E402


def lib_pins(root):
    out = {}
    for sym in root.find("lib_symbols").findall("symbol"):
        pins = []
        for sub in sym.findall("symbol"):
            m = re.search(r"_(\d+)_(\d+)$", sub[1])
            unit = int(m.group(1)) if m else 0
            for pin in sub.findall("pin"):
                at = pin.find("at")
                nm = pin.find("name")[1]
                nb = pin.find("number")[1]
                pins.append((unit, nb, nm, num(at[1]), num(at[2]), num(at[3]), pin[1]))
        out[sym[1]] = pins
    return out


def netmap(path):
    r = parse(open(path, encoding="utf-8").read())
    m = {}
    for n in r.find("nets").findall("net"):
        name = n.find("name")[1]
        for x in n.findall("node"):
            m[(x.find("ref")[1], x.find("pin")[1])] = name
    return m


def main():
    root = parse(open(sys.argv[1], encoding="utf-8").read())
    lp = lib_pins(root)
    nets = netmap(sys.argv[2])
    for n in root:
        if not (isinstance(n, L) and n.tag == "symbol" and n.find("lib_id") is not None):
            continue
        lib = n.find("lib_id")[1]
        if lib.startswith("power:"):
            continue
        ln = n.find("lib_name")
        props = {p[1]: p[2] for p in n.findall("property")}
        unit = int(n.find("unit")[1])
        at = n.find("at")
        print(f"{props['Reference']} {lib} '{props['Value']}' unit{unit} at {at[1]},{at[2]} r{at[3]}")
        for (u, nb, nm, x, y, a, et) in lp[ln[1] if ln else lib]:
            if u in (0, unit):
                print(f"    {nb:>4} {nm:14s} ({x:7.2f},{y:7.2f}) a{int(a):3d} {et:12s} {nets.get((props['Reference'], nb), '?')}")


if __name__ == "__main__":
    main()
