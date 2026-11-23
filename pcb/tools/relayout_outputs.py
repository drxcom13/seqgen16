"""relayout_outputs.py - re-place the output chain on outputs.kicad_sch and connect it with wires.

Per buffer (TXU0104 U4..U7, TXU0102 U8) every output becomes one row (rows 7.62 mm apart):

    Bx --wire(fan-out)-- R (33R, horizontal) --wire-- node [label OUT_xx] --wire-- D (TVS) --+
                                                                                            GND rail
The label at the node is the only label left on the channel (it reaches the header J3/J4/J5,
which stays a separate block). The OUT_xx_B labels disappear, the TVS GND symbols are merged
into one per buffer.

Channel mapping is read from the existing labels (OUT_xx_B: buffer pin <-> R pin 1,
OUT_xx: R pin 2 <-> D pin 1 <-> header), so the netlist must not change - verify with
kicad-cli + net_compare.py.

    python relayout_outputs.py outputs.kicad_sch
"""
import os
import re
import sys
import uuid
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sch_labels_to_wires import Sheet, key, fmt, num, parse, L  # noqa: E402

ROW = 7.62
G = 2.54


def main(path):
    text = open(path, encoding="utf-8").read()
    sh = Sheet(text)
    root = sh.root

    # --- symbol instances by reference, pin ends by symbol uuid
    inst = {}
    for n in root:
        if isinstance(n, L) and n.tag == "symbol" and n.find("lib_id") is not None:
            ref = next(p[2] for p in n.findall("property") if p[1] == "Reference")
            inst[ref] = n
    pins_of = defaultdict(list)
    for p, uid in sh.pins:
        pins_of[uid].append(key(p))
    owner = {}
    for ref, n in inst.items():
        for p in pins_of[n.find("uuid")[1]]:
            owner.setdefault(p, []).append(ref)

    labels = defaultdict(list)
    for it in sh.items:
        if it["kind"] == "label":
            labels[it["name"]].append(it)
    gnd_at = {key(it["pt"]): it for it in sh.items if it["kind"] == "power" and it["name"] == "GND"}

    def refs_at(pt, prefix):
        return [r for r in owner.get(key(pt), []) if r.startswith(prefix)]

    # --- channels
    chans = []
    for name in labels:
        m = re.fullmatch(r"(OUT_\w+?)_B", name)
        if not m:
            continue
        base = m.group(1)
        u_pt = r_pt = None
        for it in labels[name]:
            if refs_at(it["pt"], "U"):
                u_pt, u_ref = it["pt"], refs_at(it["pt"], "U")[0]
            elif refs_at(it["pt"], "R"):
                r_ref = refs_at(it["pt"], "R")[0]
        d_ref, d_lbl, keep_lbl, drop = None, None, None, []
        for it in labels[base]:
            if refs_at(it["pt"], "D"):
                d_ref = refs_at(it["pt"], "D")[0]
                drop.append(it)
            elif refs_at(it["pt"], "R"):
                keep_lbl = it                       # reused as the node label
            elif refs_at(it["pt"], "J"):
                pass                                # header side stays
            else:
                drop.append(it)
        chans.append(dict(base=base, u=u_ref, u_pt=u_pt, r=r_ref, d=d_ref, lbl=keep_lbl,
                          drop=drop + labels[name]))

    edits = []          # (start, end, replacement)
    wires = []
    junctions = []

    def at_nodes(node):
        out = []

        def walk(n):
            for c in n:
                if isinstance(c, L):
                    if c.tag == "at":
                        out.append(c)
                    elif c.tag != "instances":
                        walk(c)
        walk(node)
        return out

    def move_symbol(ref, x, y, rot=None, fields=None):
        n = inst[ref]
        ats = at_nodes(n)
        sx, sy = num(ats[0][1]), num(ats[0][2])
        r0 = ats[0][3] if len(ats[0]) > 3 else "0"
        edits.append((ats[0].start, ats[0].end, f"(at {fmt(x)} {fmt(y)} {rot if rot is not None else r0})"))
        for prop in n.findall("property"):
            a = prop.find("at")
            if a is None:
                continue
            if fields and prop[1] in fields:
                fx, fy = fields[prop[1]]
                # field angle is stored relative to the symbol: 90 on a 90-deg symbol = horizontal text
                new = f"(at {fmt(fx)} {fmt(fy)} {rot if rot is not None else 0})"
            else:
                new = f"(at {fmt(num(a[1]) - sx + x)} {fmt(num(a[2]) - sy + y)} {a[3] if len(a) > 3 else '0'})"
            edits.append((a.start, a.end, new))

    def remove(node):
        edits.append((node.start, node.end, None))

    def move_label(it, x, y):
        a = it["node"].find("at")
        edits.append((a.start, a.end, f"(at {fmt(x)} {fmt(y)} 0)"))

    by_u = defaultdict(list)
    for c in chans:
        by_u[c["u"]].append(c)

    for u, cs in sorted(by_u.items()):
        cs.sort(key=lambda c: c["u_pt"][1])
        n = len(cs)
        xp, y0 = cs[0]["u_pt"]
        rows = [y0 + ROW * i for i in range(n)]
        xr1 = xp + 5 * G             # R pin 1
        xr = xr1 + 3.81              # R centre
        xr2 = xr + 3.81              # R pin 2
        xn = xr2 + 2 * G             # node / label
        xd1 = xn + 5 * G             # TVS pin 1
        xd = xd1 + 3.81
        xd2 = xd + 3.81              # TVS pin 2 = GND rail
        gnd_syms = []
        for i, c in enumerate(cs):
            yp = c["u_pt"][1]
            yr = rows[i]
            if i == 0:
                wires.append(((xp, yp), (xr1, yr)))
            else:
                xj = xp + G * (n - i)
                wires += [((xp, yp), (xj, yp)), ((xj, yp), (xj, yr)), ((xj, yr), (xr1, yr))]
            move_symbol(c["r"], xr, yr, "90", {"Reference": (xr - 1.6, yr - 2.2), "Value": (xr - 1.6, yr + 3.4)})
            wires += [((xr2, yr), (xn, yr)), ((xn, yr), (xd1, yr))]
            move_symbol(c["d"], xd, yr)
            move_label(c["lbl"], xn, yr)
            for it in c["drop"]:
                if it is not c["lbl"]:
                    remove(it["node"])
            # the TVS GND symbol (at the old D pin 2)
            d_node = inst[c["d"]]
            dpins = pins_of[d_node.find("uuid")[1]]
            for p in dpins:
                if p in gnd_at:
                    gnd_syms.append(gnd_at[p])
        # GND rail along the TVS pin 2 column, one GND symbol below the last row
        for i in range(n - 1):
            wires.append(((xd2, rows[i]), (xd2, rows[i + 1])))
        yg = rows[-1] + 2 * G
        wires.append(((xd2, rows[-1]), (xd2, yg)))
        junctions += [(xd2, rows[i]) for i in range(1, n)]
        keep = gnd_syms[0]
        n_keep = keep["node"]
        a = n_keep.find("at")
        edits.append((a.start, a.end, f"(at {fmt(xd2)} {fmt(yg)} 0)"))      # GND symbol pointing down
        for prop in n_keep.findall("property"):
            pa = prop.find("at")
            dy = {"Reference": 6.35, "Value": 3.81}.get(prop[1], 0)
            edits.append((pa.start, pa.end, f"(at {fmt(xd2)} {fmt(yg + dy)} 0)"))
        for g in gnd_syms[1:]:
            remove(g["node"])

    # --- apply edits (innermost first is irrelevant: spans never overlap except removals of whole nodes)
    edits.sort(key=lambda e: e[0], reverse=True)
    out = text
    removed_spans = [(s, e) for s, e, r in edits if r is None]
    for s, e, r in edits:
        if r is None:
            ls = out.rfind("\n", 0, s)
            out = out[:ls if ls >= 0 else s] + out[e:]
        else:
            if any(rs <= s and e <= re_ for rs, re_ in removed_spans):
                continue
            out = out[:s] + r + out[e:]
    add = []
    for a, b in wires:
        add.append(f"\t(wire\n\t\t(pts\n\t\t\t(xy {fmt(a[0])} {fmt(a[1])}) (xy {fmt(b[0])} {fmt(b[1])})\n\t\t)\n"
                   f"\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n\t\t(uuid \"{uuid.uuid4()}\")\n\t)")
    for p in junctions:
        add.append(f"\t(junction\n\t\t(at {fmt(p[0])} {fmt(p[1])})\n\t\t(diameter 0)\n\t\t(color 0 0 0 0)\n"
                   f"\t\t(uuid \"{uuid.uuid4()}\")\n\t)")
    m = re.search(r"\n\t\((sheet_instances|embedded_fonts)", out)
    pos = m.start() if m else out.rstrip().rfind(")")
    out = out[:pos] + "\n" + "\n".join(add) + out[pos:]
    open(path, "w", encoding="utf-8", newline="\n").write(out)
    print(f"{path}: {len(chans)} channels re-placed, {len(wires)} wires, {len(junctions)} junctions")


if __name__ == "__main__":
    main(sys.argv[1])
