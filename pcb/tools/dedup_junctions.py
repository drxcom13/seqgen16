"""dedup_junctions.py - remove duplicated junctions (same position) from KiCad schematic sheets.

    python dedup_junctions.py SHEET.kicad_sch [...] [--check]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sch_labels_to_wires import parse, L, key, num  # noqa: E402


def main():
    check = "--check" in sys.argv
    total = 0
    for path in [a for a in sys.argv[1:] if not a.startswith("--")]:
        text = open(path, encoding="utf-8").read()
        seen, dup = set(), []
        for n in parse(text):
            if isinstance(n, L) and n.tag == "junction":
                a = n.find("at")
                k = key((num(a[1]), num(a[2])))
                if k in seen:
                    dup.append(n)
                seen.add(k)
        total += len(dup)
        print(f"{path}: {len(dup)} duplicated junctions")
        if dup and not check:
            for n in sorted(dup, key=lambda n: n.start, reverse=True):
                ls = text.rfind("\n", 0, n.start)
                text = text[:ls] + text[n.end:]
            open(path, "w", encoding="utf-8", newline="\n").write(text)
    return 1 if check and total else 0


if __name__ == "__main__":
    sys.exit(main())
