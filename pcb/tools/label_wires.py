"""label_wires.py <sheet.kicad_sch> [--write]: local labels get a wire under the whole text (style rule 2026-11-25).
- wire on the far side long enough -> label turned 180 deg (text over the existing wire)
- otherwise a new wire A->B along the text, label moved to B and turned 180 deg; junction added when A is
  inside an existing wire. Global labels are not touched. Check with net_compare.py and ERC afterwards."""
import math, uuid
import re, sys
def block_end(t, s):
    d = 0
    for j in range(s, len(t)):
        if t[j] == '(': d += 1
        elif t[j] == ')':
            d -= 1
            if d == 0: return j + 1
def scan(path):
    t = open(path, encoding='utf-8').read()
    wires, labels = [], []
    for m in re.finditer(r'^\t\((wire|label|global_label|hierarchical_label)\b', t, re.M):
        s = m.start() + 1; b = t[s:block_end(t, s)]
        if m.group(1) == 'wire':
            p = re.findall(r'\(xy ([-\d.]+) ([-\d.]+)\)', b); wires.append(tuple(map(float, p[0] + p[1])))
        else:
            name = re.match(r'\(\w+ "([^"]*)"', b).group(1)
            at = re.search(r'\(at ([-\d.]+) ([-\d.]+) (\d+)\)', b)
            sz = re.search(r'\(size ([\d.]+)', b)
            labels.append((m.group(1), name, float(at.group(1)), float(at.group(2)), int(at.group(3)), float(sz.group(1)) if sz else 1.27))
    return wires, labels
def touching(w, x, y):
    x1, y1, x2, y2 = w
    if abs(x1 - x2) < 1e-3 and abs(x - x1) < 1e-3: return min(y1, y2) - 1e-3 <= y <= max(y1, y2) + 1e-3
    if abs(y1 - y2) < 1e-3 and abs(y - y1) < 1e-3: return min(x1, x2) - 1e-3 <= x <= max(x1, x2) + 1e-3
    return False
path = sys.argv[1]; write = '--write' in sys.argv
t = open(path, encoding='utf-8').read()
G = 1.27
D = {0: (1, 0), 180: (-1, 0), 90: (0, -1), 270: (0, 1)}
JUST = {0: 'left', 180: 'right', 90: 'left', 270: 'right'}
wires, pts, labels = [], [], []
for m in re.finditer(r'^\t\((wire|label|global_label|junction|no_connect|symbol)\b', t, re.M):
    s = m.start() + 1; e = block_end(t, s); b = t[s:e]
    if m.group(1) == 'wire':
        p = re.findall(r'\(xy ([-\d.]+) ([-\d.]+)\)', b); wires.append(tuple(map(float, p[0] + p[1])))
    elif m.group(1) == 'symbol':
        continue
    else:
        at = re.search(r'\(at ([-\d.]+) ([-\d.]+)', b); pts.append((float(at.group(1)), float(at.group(2))))
        if m.group(1) == 'label': labels.append((s, e, b))
def text_len(name, sz=1.27):
    return len(name) * sz * 0.95 + 0.6
def free(ax, ay, bx, by):
    """no other wire end / label / junction strictly beyond A on the new segment (and 1.27 past B)"""
    dx, dy = (bx - ax), (by - ay); L = math.hypot(dx, dy); ux, uy = dx / L, dy / L
    for x, y in pts + [(w[0], w[1]) for w in wires] + [(w[2], w[3]) for w in wires]:
        along = (x - ax) * ux + (y - ay) * uy; perp = abs((x - ax) * uy - (y - ay) * ux)
        if perp < 0.01 and 0.01 < along <= L + G: return False
    for w in wires:   # crossing wires
        if touching(w, bx, by): return False
    return True
edits = []; new_wires = []; stats = {'ok': 0, 'flip': 0, 'extend': 0, 'blocked': 0}
for s, e, b in labels:
    name = re.match(r'\(label "([^"]*)"', b).group(1)
    at = re.search(r'\(at ([-\d.]+) ([-\d.]+) (\d+)\)', b); ax, ay, a = float(at.group(1)), float(at.group(2)), int(at.group(3))
    dx, dy = D[a]; L = text_len(name)
    cov_f = cov_b = 0
    for w in wires:
        if not touching(w, ax, ay): continue
        for ex, ey in ((w[0], w[1]), (w[2], w[3])):
            c = (ex - ax) * dx + (ey - ay) * dy
            if abs((ex - ax) * dy - (ey - ay) * dx) < 0.01:
                cov_f = max(cov_f, c); cov_b = max(cov_b, -c)
    if cov_f >= L - 0.3: stats['ok'] += 1; continue
    na = (a + 180) % 360
    if cov_b >= L - 0.3:
        nb = b.replace(at.group(0), '(at %s %s %d)' % (at.group(1), at.group(2), na), 1)
        stats['flip'] += 1
    else:
        Lr = math.ceil(L / 2.54) * 2.54
        bx, by = round(ax + dx * Lr, 2), round(ay + dy * Lr, 2)
        if not free(ax, ay, bx, by):
            stats['blocked'] += 1; print('BLOCKED', name, ax, ay, a); continue
        f = lambda v: ('%.2f' % v).rstrip('0').rstrip('.')
        nb = b.replace(at.group(0), '(at %s %s %d)' % (f(bx), f(by), na), 1)
        new_wires.append('\t(wire\n\t\t(pts\n\t\t\t(xy %s %s) (xy %s %s)\n\t\t)\n\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n\t\t(uuid "%s")\n\t)\n'
                         % (f(ax), f(ay), f(bx), f(by), uuid.uuid4()))
        if any(touching(w, ax, ay) and (ax, ay) not in ((w[0], w[1]), (w[2], w[3])) for w in wires):
            new_wires.append('\t(junction\n\t\t(at %s %s)\n\t\t(diameter 0)\n\t\t(color 0 0 0 0)\n\t\t(uuid "%s")\n\t)\n'
                             % (f(ax), f(ay), uuid.uuid4()))
            stats['junction'] = stats.get('junction', 0) + 1
        pts.append((bx, by)); wires.append((ax, ay, bx, by))
        stats['extend'] += 1
    nb = re.sub(r'\(justify (left|right)( bottom)?\)', lambda m: '(justify %s%s)' % (JUST[na], m.group(2) or ''), nb)
    edits.append((s, e, nb))
for s, e, nb in sorted(edits, reverse=True):
    t = t[:s] + nb + t[e:]
if new_wires:
    m = list(re.finditer(r'^\t\((sheet_instances|embedded_fonts)', t, re.M)); i = m[0].start() if m else t.rstrip().rfind(')')
    t = t[:i] + ''.join(new_wires) + t[i:]
print(path, stats)
if write: open(path, 'w', encoding='utf-8', newline='\n').write(t)
