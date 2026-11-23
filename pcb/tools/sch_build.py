"""sch_build.py - re-draw a KiCad schematic sheet from a placement script.

The parts (symbol instances with their references, values, footprints, UUIDs) are kept and only
moved; all wires, junctions, labels, global labels, no-connect flags and +3V3/GND power symbols
are deleted and drawn again by the layout script:

    b = Builder("usb.kicad_sch", paper="A4")
    b.place("U2", 142.24, 119.38)                 # rotation 0, fields shifted with the part
    b.place("R1", 73.66, 97.79)                   # 2-pin parts: Ref/Value right of the body
    b.wire(b.pin("U2", "1"), (165.1, 101.6))
    b.power("GND", b.pin("R1", "2"))              # GND points down, other power symbols up
    b.glabel("RXD", (165.1, 101.6), "right")
    b.nc(b.pin("U2", "2"))
    b.save()

save() splits wires at every connection point lying inside them, adds junctions where three
or more connections meet and checks that no two wires overlap. Connectivity has to be
verified afterwards: kicad-cli sch export netlist + net_compare.py.
"""
import glob
import math
import os
import re
import sys
import uuid
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sch_labels_to_wires import parse, L, num, key, transform, fmt, seg_contains, seg_overlap  # noqa: E402

DIRS = {"right": 0, "up": 90, "left": 180, "down": 270}
TWO_PIN = re.compile(r"^Device:(R|C|C_Polarized|FerriteBead|L|LED|D.*)$")


def _u():
    return str(uuid.uuid4())


class Builder:
    def __init__(self, path, paper=None):
        self.path = path
        self.text = open(path, encoding="utf-8").read()
        self.root = parse(self.text)
        self.paper = paper
        self.edits = []            # (start, end, replacement or None)
        self.new = []              # new top-level node texts
        self.wires = []
        self.points = []           # (pt, kind) connection points of pins / labels / power symbols / nc
        self.placed = {}
        self._read_lib()
        self._read_nodes()

    # ------------------------------------------------------------------ reading
    def _read_lib(self):
        self.lib = {}
        for sym in self.root.find("lib_symbols").findall("symbol"):
            pins = []
            for sub in sym.findall("symbol"):
                m = re.search(r"_(\d+)_(\d+)$", sub[1])
                unit = int(m.group(1)) if m else 0
                for pin in sub.findall("pin"):
                    at = pin.find("at")
                    pins.append((unit, pin.find("number")[1], num(at[1]), num(at[2])))
            self.lib[sym[1]] = pins

    def _read_nodes(self):
        self.inst = {}
        self.templates = {}
        self.free_refs = defaultdict(list)
        self.glabel_shape = {}
        self.texts = []
        self.inst_path = None
        for n in self.root:
            if not isinstance(n, L):
                continue
            t = n.tag
            if t == "symbol" and n.find("lib_id") is not None:
                lib = n.find("lib_id")[1]
                ref = self._prop(n, "Reference")
                if self.inst_path is None:
                    self.inst_path = n.find("instances").find("project").find("path")[1]
                if lib in ("power:+3V3", "power:GND"):
                    self.templates.setdefault(lib, self.text[n.start:n.end])
                    self.free_refs[ref[:4]].append(ref)
                    self._remove(n)
                else:
                    self.inst[ref] = n
            elif t in ("wire", "junction", "label", "no_connect", "bus", "bus_entry"):
                self._remove(n)
            elif t == "global_label":
                self.glabel_shape[n[1]] = n.find("shape")[1]
                self._remove(n)
            elif t == "text":
                self.texts.append(n)
        # numbering of new power symbols: above every #PWR in the project
        mx = 0
        for f in glob.glob(os.path.join(os.path.dirname(os.path.abspath(self.path)), "*.kicad_sch")):
            for m in re.finditer(r'"#PWR0*(\d+)"', open(f, encoding="utf-8").read()):
                mx = max(mx, int(m.group(1)))
        self.next_pwr = mx + 1
        if "power:GND" not in self.templates or "power:+3V3" not in self.templates:
            cfg = os.path.join(os.path.dirname(os.path.abspath(self.path)), "config.kicad_sch")
            ct = open(cfg, encoding="utf-8").read()
            for n in parse(ct):
                if isinstance(n, L) and n.tag == "symbol" and n.find("lib_id") is not None:
                    self.templates.setdefault(n.find("lib_id")[1], ct[n.start:n.end])

    @staticmethod
    def _prop(n, name):
        for p in n.findall("property"):
            if p[1] == name:
                return p[2]
        return None

    def _remove(self, n):
        self.edits.append((n.start, n.end, None))

    # ------------------------------------------------------------------ parts
    def pin(self, ref, number):
        n = self.inst[ref]
        x, y, rot, mir = self.placed.get(ref) or self._cur(n)
        unit = int(n.find("unit")[1])
        ln = n.find("lib_name")
        name = ln[1] if ln is not None else n.find("lib_id")[1]
        for (u, nb, px, py) in self.lib[name]:
            if nb == str(number) and u in (0, unit):
                p = transform(px, py, x, y, rot, mir)
                return (round(p[0], 2), round(p[1], 2))
        raise KeyError(f"{ref} pin {number}")

    @staticmethod
    def _cur(n):
        at = n.find("at")
        mir = n.find("mirror")
        return num(at[1]), num(at[2]), num(at[3]), (mir[1] if mir is not None else None)

    def place(self, ref, x, y, rot=0, fields=None):
        """Move a part. fields: None = automatic (2-pin parts: text right of / above the body,
        others: shifted with the part), or {"Reference": (x, y, justify), "Value": ...}."""
        n = self.inst[ref]
        x0, y0, r0, mir = self._cur(n)
        self.placed[ref] = (x, y, rot, mir)
        at = n.find("at")
        self.edits.append((at.start, at.end, f"(at {fmt(x)} {fmt(y)} {fmt(rot)})"))
        lib = n.find("lib_id")[1]
        if fields is None and TWO_PIN.match(lib):
            pa, pb = self.pin(ref, "1"), self.pin(ref, "2")
            if abs(pa[0] - pb[0]) < 1e-3 and "LED" in lib:     # vertical LED: text on the left
                fields = {"Reference": (x - 3.81, y - 1.27, "right"), "Value": (x - 3.81, y + 1.27, "right")}
            elif abs(pa[0] - pb[0]) < 1e-3:              # vertical part: text on the right
                fields = {"Reference": (x + 2.54, y - 1.27, "left"), "Value": (x + 2.54, y + 1.27, "left")}
            else:
                fields = {"Reference": (x, y - 3.175, None), "Value": (x, y + 3.175, None)}
        for p in n.findall("property"):
            a = p.find("at")
            if a is None:
                continue
            if fields and p[1] in fields:
                fx, fy, just = fields[p[1]][:3]
                fang = fields[p[1]][3] if len(fields[p[1]]) > 3 else rot
                self.edits.append((a.start, a.end, f"(at {fmt(fx)} {fmt(fy)} {fmt(fang)})"))
                eff = p.find("effects")
                if just and rot % 180 == 90:       # KiCad mirrors the justification of 90/270 parts
                    just = {"left": "right", "right": "left"}[just]
                j = f"\n\t\t\t\t(justify {just})" if just else ""
                self.edits.append((eff.start, eff.end, "(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n"
                                   f"\t\t\t\t)" + j + "\n\t\t\t)"))
            else:
                ang = a[3] if len(a) > 3 else "0"
                if fields is not None and p[1] in ("Reference", "Value"):
                    ang = fmt(rot)
                self.edits.append((a.start, a.end,
                                   f"(at {fmt(num(a[1]) - x0 + x)} {fmt(num(a[2]) - y0 + y)} {ang})"))
        unit = int(n.find("unit")[1])
        ln = n.find("lib_name")
        name = ln[1] if ln is not None else lib
        for (u, nb, px, py) in self.lib[name]:
            if u in (0, unit):
                p = transform(px, py, x, y, rot, mir)
                self.points.append(((round(p[0], 2), round(p[1], 2)), "pin"))

    def move_text(self, prefix, x, y):
        for t in self.texts:
            if t[1].startswith(prefix):
                a = t.find("at")
                self.edits.append((a.start, a.end, f"(at {fmt(x)} {fmt(y)} 0)"))
                return
        raise KeyError(prefix)

    # ------------------------------------------------------------------ drawing
    def wire(self, *pts):
        for a, b in zip(pts, pts[1:]):
            a, b = (round(a[0], 2), round(a[1], 2)), (round(b[0], 2), round(b[1], 2))
            if a == b:
                continue
            if abs(a[0] - b[0]) > 1e-3 and abs(a[1] - b[1]) > 1e-3:
                raise ValueError(f"diagonal wire {a} {b}")
            self.wires.append((a, b))

    def power(self, name, pt, wire_to=None):
        """Power symbol with its pin at pt (GND points down, +3V3 up)."""
        lib = "power:" + name
        t = self.templates[lib]
        n = parse(t)
        x0, y0 = num(n.find("at")[1]), num(n.find("at")[2])
        prefix = "#PWR"
        ref = self.free_refs[prefix].pop(0) if self.free_refs[prefix] else None
        if ref is None:
            ref = f"#PWR{self.next_pwr:04d}"
            self.next_pwr += 1
        out = t
        for a in sorted(_ats(n), key=lambda a: a.start, reverse=True):
            out = out[:a.start - n.start] + (f"(at {fmt(num(a[1]) - x0 + pt[0])} {fmt(num(a[2]) - y0 + pt[1])}"
                                             f"{' ' + a[3] if len(a) > 3 else ''})") + out[a.end - n.start:]
        out = re.sub(r'"#PWR\d+"', f'"{ref}"', out)
        out = re.sub(r'\(uuid "[^"]+"\)', lambda m: f'(uuid "{_u()}")', out)
        out = re.sub(r'\(path "[^"]+"', f'(path "{self.inst_path}"', out)
        self.new.append(out)
        self.points.append(((round(pt[0], 2), round(pt[1], 2)), "pin"))

    def glabel(self, name, pt, direction="left", shape=None):
        ang = DIRS[direction]
        just = "right" if ang in (180, 270) else "left"
        shape = shape or self.glabel_shape.get(name, "passive")
        x, y = pt
        self.new.append(
            f'\t(global_label "{name}"\n\t\t(shape {shape})\n\t\t(at {fmt(x)} {fmt(y)} {ang})\n'
            f'\t\t(fields_autoplaced yes)\n\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n'
            f'\t\t\t(justify {just})\n\t\t)\n\t\t(uuid "{_u()}")\n'
            f'\t\t(property "Intersheetrefs" "${{INTERSHEET_REFS}}"\n\t\t\t(at {fmt(x)} {fmt(y)} 0)\n'
            f'\t\t\t(hide yes)\n\t\t\t(show_name no)\n\t\t\t(do_not_autoplace no)\n\t\t\t(effects\n'
            f'\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(justify {just})\n\t\t\t)\n\t\t)\n\t)')
        self.points.append(((round(x, 2), round(y, 2)), "label"))

    def label(self, name, pt, direction="right"):
        ang = DIRS[direction]
        just = "right" if ang in (180, 270) else "left"
        x, y = pt
        self.new.append(
            f'\t(label "{name}"\n\t\t(at {fmt(x)} {fmt(y)} {ang})\n\t\t(effects\n\t\t\t(font\n'
            f'\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(justify {just} bottom)\n\t\t)\n\t\t(uuid "{_u()}")\n\t)')
        self.points.append(((round(x, 2), round(y, 2)), "label"))

    def nc(self, pt):
        self.new.append(f'\t(no_connect\n\t\t(at {fmt(pt[0])} {fmt(pt[1])})\n\t\t(uuid "{_u()}")\n\t)')

    # ------------------------------------------------------------------ output
    def _finish_wires(self):
        stops = {p for p, _ in self.points}
        for a, b in self.wires:
            stops.add(a)
            stops.add(b)
        out = []
        for a, b in self.wires:
            cuts = sorted([p for p in stops if seg_contains(a, b, p, interior_only=True)],
                          key=lambda p: abs(p[0] - a[0]) + abs(p[1] - a[1]))
            seq = [a] + cuts + [b]
            out += list(zip(seq, seq[1:]))
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                if seg_overlap(*out[i], *out[j]):
                    raise ValueError(f"overlapping wires {out[i]} {out[j]}")
        cnt = defaultdict(int)
        for a, b in out:
            cnt[a] += 1
            cnt[b] += 1
        for p, k in self.points:
            if k == "pin" and p in cnt:
                cnt[p] += 1
        junctions = [p for p, c in cnt.items() if c >= 3]
        conn = {p for p, _ in self.points}
        dangling = [p for p, c in cnt.items() if c == 1 and p not in conn]
        return out, junctions, dangling

    def save(self, path=None):
        wires, junctions, dangling = self._finish_wires()
        for a, b in wires:
            self.new.append(f"\t(wire\n\t\t(pts\n\t\t\t(xy {fmt(a[0])} {fmt(a[1])}) (xy {fmt(b[0])} {fmt(b[1])})\n"
                            f"\t\t)\n\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n\t\t(uuid \"{_u()}\")\n\t)")
        for p in junctions:
            self.new.append(f"\t(junction\n\t\t(at {fmt(p[0])} {fmt(p[1])})\n\t\t(diameter 0)\n"
                            f"\t\t(color 0 0 0 0)\n\t\t(uuid \"{_u()}\")\n\t)")
        if self.paper:
            pa = self.root.find("paper")
            self.edits.append((pa.start, pa.end, f'(paper "{self.paper}")'))
        out = self.text
        removed = [(s, e) for s, e, r in self.edits if r is None]
        for s, e, r in sorted(self.edits, key=lambda e: e[0], reverse=True):
            if r is None:
                ls = out.rfind("\n", 0, s)
                out = out[:ls if ls >= 0 else s] + out[e:]
            elif not any(rs <= s and e <= re_ for rs, re_ in removed):
                out = out[:s] + r + out[e:]
        m = re.search(r"\n\t\((sheet_instances|embedded_fonts)", out)
        pos = m.start() if m else out.rstrip().rfind(")")
        out = out[:pos] + "\n" + "\n".join(self.new) + out[pos:]
        open(path or self.path, "w", encoding="utf-8", newline="\n").write(out)
        unplaced = sorted(set(self.inst) - set(self.placed))
        print(f"{path or self.path}: {len(self.placed)} parts placed, {len(wires)} wires, "
              f"{len(junctions)} junctions" + (f", NOT placed: {unplaced}" if unplaced else ""))
        if dangling:
            print("  wire ends not connected to anything:", dangling)


def _ats(n):
    out = []

    def walk(x):
        for c in x:
            if isinstance(c, L):
                if c.tag == "at":
                    out.append(c)
                elif c.tag != "instances":
                    walk(c)
    walk(n)
    return out


if __name__ == "__main__":
    print(__doc__)
