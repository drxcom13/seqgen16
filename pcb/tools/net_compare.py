"""net_compare.py - compare connectivity of two KiCad netlists (kicad-cli sch export netlist
--format kicadsexpr). Nets are compared as sets of (reference, pin) - net names are ignored.

    python net_compare.py before.net after.net
Exit code 0 = identical connectivity.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sch_labels_to_wires import parse  # noqa: E402


def nets(path):
    root = parse(open(path, encoding="utf-8").read())
    out = {}
    for net in root.find("nets").findall("net"):
        name = net.find("name")[1]
        nodes = frozenset((n.find("ref")[1], n.find("pin")[1]) for n in net.findall("node"))
        if nodes:
            out[nodes] = name
    return out


def main():
    a, b = nets(sys.argv[1]), nets(sys.argv[2])
    only_a = sorted(a[k] for k in a if k not in b)
    only_b = sorted(b[k] for k in b if k not in a)
    renamed = sum(1 for k in a if k in b and a[k] != b[k])
    print(f"nets: {len(a)} vs {len(b)}; identical pin sets: {len(set(a) & set(b))}; renamed: {renamed}")
    for n in only_a:
        print("  only in first :", n)
    for n in only_b:
        print("  only in second:", n)
    return 0 if not only_a and not only_b else 1


if __name__ == "__main__":
    sys.exit(main())
