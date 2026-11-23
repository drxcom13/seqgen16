"""sch_labels_to_wires.py - replace "label-only" connections between nearby parts with wires.

KiCad schematic post-processor (KiCad 7..10 .kicad_sch files).

For every net on a sheet (local label name, global label name or power-symbol value) the
connection points (label anchors / power-symbol pins, which sit on pin ends) are grouped:
points closer than --dist (Manhattan, mm) are joined with orthogonal wires (minimum spanning
tree, L / Z shaped routes). A route is accepted only if it
  * does not touch any pin end, label anchor, no-connect or wire of another net,
  * does not pass through another pin end of the same net (pins connect only at wire ends),
  * does not overlap another wire collinearly and does not cut through a symbol body
    or a remaining label's text.
After routing, each connected group keeps exactly ONE label / power symbol (global labels are
kept once per group so the net still reaches the other sheets); the other ones are deleted.
Junctions are added where three or more connections meet.

The file is edited textually (removed nodes cut out, wires/junctions inserted), so the rest
of the formatting is left untouched.

Usage:
    python sch_labels_to_wires.py SHEET.kicad_sch [--dist 20] [--dry-run]
Verify afterwards: kicad-cli sch export netlist (connectivity must not change) and ERC.
"""
import argparse
import math
import re
import sys
import uuid
from collections import defaultdict

EPS = 1e-3


# ----------------------------------------------------------------------------- s-expression parser
class Q(str):
    """Quoted string atom."""


class L(list):
    """List node with source offsets."""
    start = 0
    end = 0

    @property
    def tag(self):
        return self[0] if self and isinstance(self[0], str) and not isinstance(self[0], Q) else None

    def find(self, tag):
        for c in self:
            if isinstance(c, L) and c.tag == tag:
                return c
        return None

    def findall(self, tag):
        return [c for c in self if isinstance(c, L) and c.tag == tag]


TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)


def parse(text):
    stack, root, pos = [], None, 0
    n = len(text)
    while pos < n:
        m = TOKEN.match(text, pos)
        if not m or m.end() == pos:
            if text[pos:].strip() == "":
                break
            raise ValueError(f"parse error at {pos}")
        if m.group(1):
            node = L()
            node.start = m.start(1)
            if stack:
                stack[-1].append(node)
            stack.append(node)
        elif m.group(2):
            node = stack.pop()
            node.end = m.end(2)
            if not stack:
                root = node
        elif m.group(3) is not None:
            stack[-1].append(Q(m.group(3).replace('\\"', '"').replace("\\\\", "\\")))
        else:
            stack[-1].append(m.group(4))
        pos = m.end()
    return root


def num(x):
    return float(x)


def key(p):
    return (round(p[0], 3), round(p[1], 3))


# ----------------------------------------------------------------------------- geometry
def transform(px, py, sx, sy, rot, mirror):
    """Library point (Y up) -> sheet point (Y down) for a symbol instance."""
    x, y = px, -py                       # to sheet orientation (Y down)
    if mirror == "x":                    # mirror about X axis
        y = -y
    elif mirror == "y":
        x = -x
    r = math.radians(-rot)               # KiCad rotation is CCW on screen
    c, s = round(math.cos(r), 9), round(math.sin(r), 9)
    xr, yr = x * c - y * s, x * s + y * c
    return (sx + xr, sy + yr)


def seg_contains(a, b, p, interior_only=False):
    """Point p on axis-parallel segment a-b?"""
    if abs(a[0] - b[0]) < EPS:                       # vertical
        if abs(p[0] - a[0]) > EPS:
            return False
        lo, hi = sorted((a[1], b[1]))
        if interior_only:
            return lo + EPS < p[1] < hi - EPS
        return lo - EPS <= p[1] <= hi + EPS
    lo, hi = sorted((a[0], b[0]))                    # horizontal
    if abs(p[1] - a[1]) > EPS:
        return False
    if interior_only:
        return lo + EPS < p[0] < hi - EPS
    return lo - EPS <= p[0] <= hi + EPS


def seg_overlap(a, b, c, d):
    """Collinear overlap (length > 0) of two axis-parallel segments."""
    av, cv = abs(a[0] - b[0]) < EPS, abs(c[0] - d[0]) < EPS
    if av != cv:
        return False
    if av:
        if abs(a[0] - c[0]) > EPS:
            return False
        lo1, hi1 = sorted((a[1], b[1]))
        lo2, hi2 = sorted((c[1], d[1]))
    else:
        if abs(a[1] - c[1]) > EPS:
            return False
        lo1, hi1 = sorted((a[0], b[0]))
        lo2, hi2 = sorted((c[0], d[0]))
    return min(hi1, hi2) - max(lo1, lo2) > EPS


def seg_hits_box(a, b, box):
    """Axis-parallel segment passes through the interior of box (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = box
    if abs(a[0] - b[0]) < EPS:
        x = a[0]
        lo, hi = sorted((a[1], b[1]))
        return x0 + EPS < x < x1 - EPS and min(hi, y1) - max(lo, y0) > EPS
    y = a[1]
    lo, hi = sorted((a[0], b[0]))
    return y0 + EPS < y < y1 - EPS and min(hi, x1) - max(lo, x0) > EPS


# ----------------------------------------------------------------------------- sheet model
class Sheet:
    def __init__(self, text):
        self.text = text
        self.root = parse(text)
        self.lib = {}            # lib name -> {'pins': [(unit, x, y)], 'gfx': [(unit, pts)]}
        self._read_lib()
        self.pins = []           # (point, owner uuid, net_item?) all pin ends
        self.bodies = []         # (box, owner uuid)
        self.items = []          # connection items: dict(kind, name, pt, uuid, node, box)
        self.noconn = []
        self.wires = []
        self.pin_lines = []      # (connection point, inner end)
        self.texts = []          # boxes of visible symbol field texts
        self._read_instances()

    # -- library
    def _read_lib(self):
        ls = self.root.find("lib_symbols")
        for sym in ls.findall("symbol") if ls else []:
            name = sym[1]
            ent = {"pins": [], "gfx": [], "power": sym.find("power") is not None}
            for sub in sym.findall("symbol"):
                m = re.search(r"_(\d+)_(\d+)$", sub[1])
                unit = int(m.group(1)) if m else 0
                for pin in sub.findall("pin"):
                    at = pin.find("at")
                    ln = pin.find("length")
                    ent["pins"].append((unit, num(at[1]), num(at[2]), num(at[3]) if len(at) > 3 else 0,
                                        num(ln[1]) if ln else 0))
                for g in sub:
                    if not isinstance(g, L):
                        continue
                    pts = []
                    if g.tag == "rectangle":
                        s, e = g.find("start"), g.find("end")
                        pts = [(num(s[1]), num(s[2])), (num(e[1]), num(e[2]))]
                    elif g.tag in ("polyline", "bezier"):
                        p = g.find("pts")
                        pts = [(num(xy[1]), num(xy[2])) for xy in p.findall("xy")] if p else []
                    elif g.tag == "circle":
                        c, r = g.find("center"), g.find("radius")
                        cx, cy, rr = num(c[1]), num(c[2]), num(r[1])
                        pts = [(cx - rr, cy - rr), (cx + rr, cy + rr)]
                    elif g.tag == "arc":
                        pts = [(num(g.find(t)[1]), num(g.find(t)[2])) for t in ("start", "mid", "end")]
                    if pts:
                        ent["gfx"].append((unit, pts))
            self.lib[name] = ent

    # -- instances, labels
    def _read_instances(self):
        for node in self.root:
            if not isinstance(node, L):
                continue
            t = node.tag
            if t == "symbol" and node.find("lib_id") is not None:
                self._add_symbol(node)
            elif t in ("label", "global_label"):
                at = node.find("at")
                pt = (num(at[1]), num(at[2]))
                ang = num(at[3]) if len(at) > 3 else 0
                name = node[1]
                self.items.append(dict(kind=t, name=name, pt=pt, uuid=node.find("uuid")[1], node=node,
                                       box=label_box(pt, ang, name, t == "global_label")))
            elif t == "no_connect":
                at = node.find("at")
                self.noconn.append((num(at[1]), num(at[2])))
            elif t == "wire":
                p = node.find("pts").findall("xy")
                self.wires.append(((num(p[0][1]), num(p[0][2])), (num(p[1][1]), num(p[1][2]))))

    def _add_symbol(self, node):
        lib_id = node.find("lib_id")[1]
        lname = lib_id
        ls = node.find("lib_name")
        if ls is not None:
            lname = ls[1]
        ent = self.lib.get(lname) or self.lib.get(lib_id)
        if ent is None:
            return
        at = node.find("at")
        sx, sy, rot = num(at[1]), num(at[2]), num(at[3]) if len(at) > 3 else 0
        mir = node.find("mirror")
        mirror = mir[1] if mir is not None else None
        unit = int(node.find("unit")[1]) if node.find("unit") is not None else 1
        uid = node.find("uuid")[1]
        pin_pts = []
        for (u, px, py, pa, pl) in ent["pins"]:
            if u in (0, unit):
                p = transform(px, py, sx, sy, rot, mirror)
                pin_pts.append(p)
                self.pins.append((p, uid))
                # pin line: from the connection point towards the body
                r = math.radians(pa)
                q = transform(px + pl * math.cos(r), py + pl * math.sin(r), sx, sy, rot, mirror)
                self.pin_lines.append((p, q))
        # visible property texts (reference, value ...) - routes should avoid them
        for pr in node.findall("property"):
            a = pr.find("at")
            eff = pr.find("effects")
            hidden = pr.find("hide") is not None or (eff is not None and eff.find("hide") is not None)
            if a is None or hidden or not pr[2]:
                continue
            fx, fy, fa = num(a[1]), num(a[2]), (num(a[3]) if len(a) > 3 else 0) + rot
            w, h = len(pr[2]) * 1.27, 1.4
            just = eff.find("justify") if eff is not None else None
            jl = just is not None and "left" in just
            jr = just is not None and "right" in just
            if int(round(fa)) % 180 == 0:
                x0 = fx if jl else fx - w if jr else fx - w / 2
                self.texts.append((x0, fy - h / 2, x0 + w, fy + h / 2))
            else:
                y0 = fy - w if jl else fy if jr else fy - w / 2
                self.texts.append((fx - h / 2, y0, fx + h / 2, y0 + w))
        pts = []
        for (u, gp) in ent["gfx"]:
            if u in (0, unit):
                pts += [transform(x, y, sx, sy, rot, mirror) for (x, y) in gp]
        box = None
        if pts:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            box = (min(xs), min(ys), max(xs), max(ys))
        is_power = ent["power"] or lib_id.startswith("power:")
        if is_power:
            val = None
            for pr in node.findall("property"):
                if pr[1] == "Value":
                    val = pr[2]
            if pin_pts:
                self.items.append(dict(kind="power", name=val, pt=pin_pts[0], uuid=uid, node=node, box=box))
        elif box:
            self.bodies.append((box, uid))


def label_box(pt, ang, name, is_global):
    """Approximate drawing of a label. Global labels: outline centred on the anchor line.
    Local labels: text above the wire (left of it when vertical)."""
    w = len(name) * 1.05 + (2.6 if is_global else 0.6)
    x, y = pt
    a = int(round(ang)) % 360
    if is_global:
        h = 0.9
        if a == 0:
            return (x + 0.4, y - h, x + w, y + h)
        if a == 180:
            return (x - w, y - h, x - 0.4, y + h)
        if a == 90:
            return (x - h, y - w, x + h, y - 0.4)
        return (x - h, y + 0.4, x + h, y + w)
    if a == 0:
        return (x + 0.4, y - 1.6, x + w, y - 0.1)
    if a == 180:
        return (x - w, y - 1.6, x - 0.4, y - 0.1)
    if a == 90:
        return (x - 1.6, y - w, x - 0.1, y - 0.4)
    return (x - 1.6, y + 0.4, x - 0.1, y + w)


# ----------------------------------------------------------------------------- routing
class Router:
    def __init__(self, sh):
        self.sh = sh
        self.wires = [(a, b, None) for a, b in sh.wires]     # (a, b, net)
        self.points_by_net = defaultdict(set)
        for it in sh.items:
            self.points_by_net[self.net_of(it)].add(key(it["pt"]))
        self.all_pins = [key(p) for p, _ in sh.pins]

    @staticmethod
    def net_of(it):
        return ("L:" if it["kind"] == "label" else "G:" if it["kind"] == "global_label" else "P:") + it["name"]

    def segment_ok(self, a, b, net, endpoints, ignore_boxes):
        if key(a) == key(b):
            return False
        own_pts = self.points_by_net[net]
        # pins: only allowed at the segment ends and only if they belong to this net
        for p in self.all_pins:
            if seg_contains(a, b, p):
                if p in (key(a), key(b)) and p in endpoints:
                    continue
                if p in (key(a), key(b)) and p in own_pts:
                    continue
                return False
        for it in self.sh.items:
            p = key(it["pt"])
            if self.net_of(it) != net and seg_contains(a, b, p):
                return False
        for p in self.sh.noconn:
            if seg_contains(a, b, p):
                return False
        for (c, d, wnet) in self.wires:
            if seg_overlap(a, b, c, d):
                return False
            if wnet != net:
                if seg_contains(a, b, c) or seg_contains(a, b, d) or seg_contains(c, d, a) or seg_contains(c, d, b):
                    return False
        for box, uid in self.sh.bodies:
            if seg_hits_box(a, b, box):
                return False
        for it in self.sh.items:
            if it["uuid"] in ignore_boxes or it["box"] is None:
                continue
            if self.net_of(it) == net:
                continue
            if seg_hits_box(a, b, it["box"]):
                return False
        return True

    # ------------------------------------------------------------------ grid A* router
    GRID = 1.27
    TURN = 6          # cost of a bend (in grid steps)
    CROSS = 3         # cost of crossing another wire
    TEXT = 2          # cost of running over a field text
    MARGIN = 12       # search window around the two points (grid steps)

    def _g(self, p):
        return (round(p[0] / self.GRID), round(p[1] / self.GRID))

    def _on_grid(self, p):
        g = self._g(p)
        return abs(g[0] * self.GRID - p[0]) < EPS and abs(g[1] * self.GRID - p[1]) < EPS

    def _static(self):
        """Grid points nobody may pass (built once)."""
        if hasattr(self, "_blk"):
            return
        sh = self.sh
        self._pin_pts = {self._g(p) for p, _ in sh.pins}
        blk = set(self._g(p) for p in sh.noconn)
        for p, q in sh.pin_lines:                      # pin lines (not the connection point)
            g0, g1 = self._g(p), self._g(q)
            for gp in self._line(g0, g1)[1:]:
                blk.add(gp)
        for box, _ in sh.bodies:
            blk |= self._box_pts(box)
        self._blk = blk
        self._text = set()
        for box in sh.texts:
            self._text |= self._box_pts(box)
        self._item_pts = defaultdict(set)              # net -> anchor points
        self._item_box = defaultdict(set)              # net -> points under its labels / power symbols
        for it in sh.items:
            n = self.net_of(it)
            self._item_pts[n].add(self._g(it["pt"]))
            if it["box"]:
                self._item_box[n] |= self._box_pts(it["box"])

    def _box_pts(self, box):
        x0, y0, x1, y1 = box
        G = self.GRID
        return {(i, j) for i in range(math.floor(x0 / G), math.ceil(x1 / G) + 1)
                for j in range(math.floor(y0 / G), math.ceil(y1 / G) + 1)
                if x0 - EPS <= i * G <= x1 + EPS and y0 - EPS <= j * G <= y1 + EPS}

    @staticmethod
    def _line(g0, g1):
        (x0, y0), (x1, y1) = g0, g1
        if x0 == x1:
            s = 1 if y1 >= y0 else -1
            return [(x0, y) for y in range(y0, y1 + s, s)]
        if y0 == y1:
            s = 1 if x1 >= x0 else -1
            return [(x, y0) for x in range(x0, x1 + s, s)]
        return [g0, g1]

    def _wire_maps(self):
        """Per grid point: orientations of wires passing through, and wire end points."""
        thru = defaultdict(set)
        ends = set()
        for a, b, _n in self.wires:
            ga, gb = self._g(a), self._g(b)
            o = "v" if ga[0] == gb[0] else "h"
            for gp in self._line(ga, gb):
                thru[gp].add(o)
            ends.add(ga)
            ends.add(gb)
        return thru, ends

    def route(self, a, b, net, ignore):
        import heapq
        if not (self._on_grid(a) and self._on_grid(b)):
            return None
        self._static()
        ga, gb = self._g(a), self._g(b)
        thru, wends = self._wire_maps()
        others_pts = set()
        others_box = set()
        for n, pts in self._item_pts.items():
            if n != net:
                others_pts |= pts
                others_box |= self._item_box[n]
        xmin, xmax = min(ga[0], gb[0]) - self.MARGIN, max(ga[0], gb[0]) + self.MARGIN
        ymin, ymax = min(ga[1], gb[1]) - self.MARGIN, max(ga[1], gb[1]) + self.MARGIN

        def free(p):
            if p == gb:
                return True
            if p in self._pin_pts or p in self._blk or p in others_pts or p in others_box or p in wends:
                return False
            return True

        dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        start = (ga, -1)
        dist = {start: 0}
        prev = {}
        h = lambda p: abs(p[0] - gb[0]) + abs(p[1] - gb[1])
        pq = [(h(ga), 0, ga, -1)]
        found = None
        while pq:
            f, g, p, d = heapq.heappop(pq)
            if p == gb:
                found = (p, d)
                break
            if dist.get((p, d), 1e9) < g:
                continue
            crossing = p in thru and p != ga
            for nd, (dx, dy) in enumerate(dirs):
                if d != -1 and nd != d and (dx, dy) == (-dirs[d][0], -dirs[d][1]):
                    continue                                   # no U-turn
                if crossing and nd != d:
                    continue                                   # no bend on top of another wire
                q = (p[0] + dx, p[1] + dy)
                if not (xmin <= q[0] <= xmax and ymin <= q[1] <= ymax) or not free(q):
                    continue
                o = "h" if dy == 0 else "v"
                if q != gb and q in thru and o in thru[q]:
                    continue                                   # would run along another wire
                cost = 1 + (self.TURN if d not in (-1, nd) else 0)
                if q in thru and q != gb:
                    cost += self.CROSS
                if q in self._text:
                    cost += self.TEXT
                ng = g + cost
                if ng < dist.get((q, nd), 1e9):
                    dist[(q, nd)] = ng
                    prev[(q, nd)] = (p, d)
                    heapq.heappush(pq, (ng + h(q), ng, q, nd))
        if not found:
            return None
        # rebuild path, compress to corner points
        path = [found]
        while path[-1] in prev:
            path.append(prev[path[-1]])
        cells = [s[0] for s in reversed(path)]
        corners = [cells[0]]
        for i in range(1, len(cells) - 1):
            d1 = (cells[i][0] - cells[i - 1][0], cells[i][1] - cells[i - 1][1])
            d2 = (cells[i + 1][0] - cells[i][0], cells[i + 1][1] - cells[i][1])
            if d1 != d2:
                corners.append(cells[i])
        corners.append(cells[-1])
        pts = [a] + [(c[0] * self.GRID, c[1] * self.GRID) for c in corners[1:-1]] + [b]
        segs = [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
        # no long detours (e.g. a loop around a chip): keep the label instead
        length = sum(abs(s[0] - e[0]) + abs(s[1] - e[1]) for s, e in segs)
        manh = abs(a[0] - b[0]) + abs(a[1] - b[1])
        if length > 1.5 * manh + 5.0 or len(segs) > 4:
            return None
        ends = {key(a), key(b)}
        if not all(self.segment_ok(s, e, net, ends, ignore) for s, e in segs):
            return None
        for s, e in segs:
            self.wires.append((s, e, net))
        return segs


def mst_edges(pts, dist):
    edges = []
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = abs(pts[i][0] - pts[j][0]) + abs(pts[i][1] - pts[j][1])
            if d <= dist:
                # prefer straight connections
                straight = abs(pts[i][0] - pts[j][0]) < EPS or abs(pts[i][1] - pts[j][1]) < EPS
                edges.append((d * (1.0 if straight else 1.6), i, j))
    edges.sort()
    return edges


def process(text, dist):
    sh = Sheet(text)
    rt = Router(sh)

    # sanity: every label / power anchor should sit on a pin end
    pinset = set(rt.all_pins)
    floating = [it for it in sh.items if it["kind"] != "power" and key(it["pt"]) not in pinset]

    groups = defaultdict(list)
    for it in sh.items:
        groups[rt.net_of(it)].append(it)

    new_wires, remove, kept = [], [], []
    stats = defaultdict(int)
    # route the nets with the most local points first
    for net, its in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(its) < 2:
            continue
        pts = [it["pt"] for it in its]
        parent = list(range(len(pts)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        ignore = {it["uuid"] for it in its}
        for _, i, j in mst_edges(pts, dist):
            if find(i) == find(j):
                continue
            segs = rt.route(pts[i], pts[j], net, ignore)
            if segs:
                parent[find(i)] = find(j)
                new_wires += [(s, e) for s, e in segs]
                stats["edges"] += 1
            else:
                stats["blocked"] += 1
        comps = defaultdict(list)
        for i, it in enumerate(its):
            comps[find(i)].append(it)
        if len(comps) == 1 and all(it["kind"] == "label" for it in its):
            # local net completely drawn with wires on this sheet: the label is redundant
            for it in its:
                remove.append(it)
                stats["removed_label"] += 1
            continue
        for comp in comps.values():
            if len(comp) < 2:
                continue
            my_wires = [(a, b) for (a, b, n) in rt.wires if n == net]

            def clear(it):
                if it["box"] is None:
                    return True
                return not any(seg_hits_box(a, b, it["box"]) for a, b in my_wires)

            # keep the first (file order) item whose drawing is not crossed by the new wires
            keep = next((it for it in comp if clear(it)), comp[0])
            kept.append(keep)
            for it in comp:
                if it is not keep:
                    remove.append(it)
                    stats["removed_" + it["kind"]] += 1

    # kept labels lying on top of wires: turn them to a free direction
    removed_ids = {id(it) for it in remove}
    live = [it for it in sh.items if id(it) not in removed_ids]
    spin = []
    for it in kept:
        if it["kind"] not in ("label", "global_label"):
            continue

        def free_box(box, me=it):
            if any(seg_hits_box(a, b, box) for a, b, _n in rt.wires):
                return False
            if any(boxes_overlap(box, b) for b, _ in sh.bodies):
                return False
            if any(boxes_overlap(box, o["box"]) for o in live if o is not me and o["box"]):
                return False
            return not any(boxes_overlap(box, t) for t in sh.texts)

        if free_box(it["box"]):
            continue
        cur = int(round(num(it["node"].find("at")[3]))) % 360 if len(it["node"].find("at")) > 3 else 0
        for ang in [a for a in (0, 180, 90, 270) if a != cur]:
            box = label_box(it["pt"], ang, it["name"], it["kind"] == "global_label")
            if free_box(box):
                spin.append((it, ang))
                it["box"] = box
                stats["label_turned"] += 1
                break

    # junctions: >= 3 connections at a point
    deg = defaultdict(int)
    for a, b in new_wires + list(sh.wires):
        deg[key(a)] += 1
        deg[key(b)] += 1
    for p in pinset:
        if p in deg:
            deg[p] += 1
    junctions = [p for p, d in deg.items() if d >= 3]
    # split wires whose interior is touched by another wire's end (explicit T connections)
    allw = new_wires
    changed = True
    while changed:
        changed = False
        ends = {key(a) for a, b in allw} | {key(b) for a, b in allw}
        for idx, (a, b) in enumerate(allw):
            for p in ends:
                if seg_contains(a, b, p, interior_only=True):
                    allw[idx:idx + 1] = [(a, p), (p, b)]
                    if p not in junctions:
                        junctions.append(p)
                    changed = True
                    break
            if changed:
                break
    stats["wires"] = len(allw)
    stats["junctions"] = len(junctions)
    return sh, allw, junctions, remove, floating, stats, spin


def boxes_overlap(a, b):
    return min(a[2], b[2]) - max(a[0], b[0]) > EPS and min(a[3], b[3]) - max(a[1], b[1]) > EPS


def fmt(v):
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def emit(text, sh, wires, junctions, remove, spin=()):
    edits = [(it["node"].start, it["node"].end, None) for it in remove]
    for it, ang in spin:
        n = it["node"]
        a = n.find("at")
        edits.append((a.start, a.end, f"(at {fmt(it['pt'][0])} {fmt(it['pt'][1])} {ang})"))
        eff = n.find("effects")
        j = eff.find("justify") if eff is not None else None
        if j is not None:
            side = "left" if ang in (0, 90) else "right"
            new = f"(justify {side} bottom)" if it["kind"] == "label" else f"(justify {side})"
            edits.append((j.start, j.end, new))
    out = text
    for s, e, r in sorted(edits, key=lambda x: x[0], reverse=True):
        if r is None:
            # also eat the indentation / newline before the node
            ls = out.rfind("\n", 0, s)
            out = out[:ls if ls >= 0 else s] + out[e:]
        else:
            out = out[:s] + r + out[e:]
    add = []
    for a, b in wires:
        add.append(f"\t(wire\n\t\t(pts\n\t\t\t(xy {fmt(a[0])} {fmt(a[1])}) (xy {fmt(b[0])} {fmt(b[1])})\n\t\t)\n"
                   f"\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n\t\t(uuid \"{uuid.uuid4()}\")\n\t)")
    for p in junctions:
        add.append(f"\t(junction\n\t\t(at {fmt(p[0])} {fmt(p[1])})\n\t\t(diameter 0)\n\t\t(color 0 0 0 0)\n"
                   f"\t\t(uuid \"{uuid.uuid4()}\")\n\t)")
    if not add:
        return out
    # insert before (sheet_instances / (embedded_fonts or before the final ')'
    m = re.search(r"\n\t\((sheet_instances|embedded_fonts)", out)
    pos = m.start() if m else out.rstrip().rfind(")")
    return out[:pos] + "\n" + "\n".join(add) + out[pos:]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sheets", nargs="+")
    ap.add_argument("--dist", type=float, default=20.0, help="max Manhattan distance [mm] to join with a wire")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    for path in a.sheets:
        text = open(path, encoding="utf-8").read()
        sh, wires, junctions, remove, floating, stats, spin = process(text, a.dist)
        print(f"{path}: " + ", ".join(f"{k}={v}" for k, v in sorted(stats.items())))
        for it in floating:
            print(f"   note: {it['kind']} '{it['name']}' at {it['pt']} is not on a pin end")
        if not a.dry_run:
            open(path, "w", encoding="utf-8", newline="\n").write(emit(text, sh, wires, junctions, remove, spin))


if __name__ == "__main__":
    sys.exit(main())
