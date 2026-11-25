"""place_parts.py - footprints from the schematic netlist onto seqgen16.kicad_pcb, with nets and a placement.

Run with KiCad's Python (pcbnew module):
    kicad-cli sch export netlist --format kicadsexpr -o seqgen16.net ..\\seqgen16\\seqgen16.kicad_sch
    "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" place_parts.py seqgen16.net ..\\seqgen16\\seqgen16.kicad_pcb [--move]

- Footprints missing on the board are added (library footprint from the netlist), each one linked to its
  schematic symbol (path = sheet uuid / symbol uuid), so a later "Update PCB from Schematic" matches them.
- Nets of all pads are set from the netlist (also for J1/J3/J4/J5 and other footprints already on the board).
- New footprints get the position from PLACE (board coordinates relative to the top-left corner of the
  80x80 mm outline made by make_board.py). Footprints already on the board keep their position,
  unless --move is given (then everything in PLACE is moved again; J1/J3/J4/J5/H1..H4 are never moved).
Prints pads that have no net in the netlist and netlist pins without a pad.
"""
import os
import re
import sys

import pcbnew

FP = r"C:\Program Files\KiCad\10.0\share\kicad\footprints"
X0, Y0 = 108.5, 65.0      # top-left corner of the board outline (make_board.py, A4 centred)
FIXED = {"J1", "J3", "J4", "J5", "H1", "H2", "H3", "H4"}
FINE_PITCH = {"VSSOP-8_2.3x2mm_P0.5mm": 0.15}   # footprint-local clearance (pad gap 0.15 mm in the library)
mm = pcbnew.FromMM

# ---------------------------------------------------------------- placement (x, y, rotation, side)
# x, y in mm from the top-left board corner; rotation in degrees CCW; side "F" or "B".
PLACE = {}


def put(ref, x, y, rot=0, side="F"):
    PLACE[ref] = (x, y, rot, side)


# FPGA: rotated 90 CCW -> pins 37..72 (CH0..15, UART) face the output buffers on the right,
# pins 109..144 (configuration, LEDs, RESET) face the configuration block on the left,
# pins 1..36 (JTAG, CLK_50M, SYNC, BASE_CLK) face the bottom.
FX, FY = 34.0, 40.0
put("U1", FX, FY, 90)
# decoupling of VCCIO / VCCA / VCC_ONE on the bottom side, ring under the pin rows
ring = [f"C{i}" for i in range(23, 44)]
slots = []
for k in (-6.0, -4.0, -2.0, 0.0, 2.0, 4.0, 6.0):          # left / right side: pads along x
    slots += [(FX - 7.6, FY + k, 0), (FX + 7.6, FY + k, 0)]
for k in (-5.0, -2.5, 0.0, 2.5, 5.0):                      # top / bottom side: pads along y
    slots += [(FX + k, FY - 7.6, 90), (FX + k, FY + 7.6, 90)]
for ref, (x, y, r) in zip(ring, slots):
    put(ref, x, y, r, "B")

# --- output buffers U4..U7 (TXU0104, A side left, B side right), rows of J3 (pitch 2.54 from y = 10)
JROW = [10.0 + 2.54 * i for i in range(16)]
for i, u in enumerate(("U4", "U5", "U6", "U7")):
    y = sum(JROW[4 * i:4 * i + 4]) / 4
    put(u, 52.0, y)
    put(f"C{5 + 2 * i}", 50.0, y - 4.6)        # VCCA (+3V3)
    put(f"C{6 + 2 * i}", 54.0, y - 4.6)        # VCCB
for i in range(16):
    put(f"R{5 + i}", 61.0, JROW[i])            # 33R
    put(f"D{3 + i}", 67.0, JROW[i])            # TSD05C
# BASE_CLK / SYNC: U8 TXU0102, rows of J4 / J5
put("U8", 52.0, 60.0)
put("C13", 50.0, 56.4)
put("C14", 54.0, 56.4)
put("R21", 61.0, 56.0)
put("D19", 67.0, 56.0)
put("R22", 61.0, 65.0)
put("D20", 67.0, 65.0)

# --- VCCB LDOs (U11 -> VCCB_A, U12 -> VCCB_B), dividers, enable (Q1, R48, R49)
put("U11", 50.0, 70.5)
put("C46", 47.0, 70.5, 90)
put("C47", 53.0, 70.5, 90)
for i, r in enumerate(("R38", "R39", "R40", "R41", "R42")):
    put(r, 46.5 + 2.0 * i, 75.0, 90)
put("U12", 60.0, 70.5)
put("C48", 57.0, 70.5, 90)
put("C49", 63.0, 70.5, 90)
for i, r in enumerate(("R43", "R44", "R45", "R46", "R47")):
    put(r, 56.5 + 2.0 * i, 75.0, 90)
put("Q1", 67.5, 70.5)
put("R48", 66.5, 75.0, 90)
put("R49", 68.5, 75.0, 90)

# --- USB (J1 fixed at the left edge, y = 20)
put("U3", 10.5, 20.0)
put("R1", 11.0, 15.0)
put("R2", 11.0, 25.0)
put("D24", 9.5, 10.5, 90)
put("FB1", 13.0, 10.5, 90)
put("U10", 13.0, 5.0)
put("C45", 16.5, 5.0, 90)
put("C1", 16.5, 9.5, 90)
put("C2", 16.5, 12.5, 90)
put("U2", 22.0, 13.0)
put("C3", 21.0, 6.5)
put("C4", 25.0, 6.5)
put("D1", 30.0, 3.2)
put("D2", 34.0, 3.2)
put("R3", 30.0, 5.6)
put("R4", 34.0, 5.6)

# --- configuration (left of the FPGA): pull-ups in the order of the FPGA pins
for i, r in enumerate(("R29", "R28", "R25", "R26", "R27", "R30", "R31")):
    put(r, 19.5, 35.5 + 2.0 * i)
put("D22", 15.5, 45.5)
put("D23", 15.5, 47.5)
put("SW1", 8.0, 34.0)
put("JP1", 4.5, 42.5)
put("JP2", 8.5, 42.5)

# --- power: U9 TPSM828302 (VBUS -> +3V3), FB2 / FB3 (VCCA, VCC_CORE), power LED
put("U9", 10.0, 63.0)
put("C15", 5.0, 56.5)
put("C16", 6.5, 61.5, 90)
put("C18", 13.5, 61.5, 90)
put("C17", 6.0, 69.5)
put("R23", 6.0, 67.0)
put("R35", 14.0, 66.5)
put("R36", 14.0, 69.0)
for i, c in enumerate(("C19", "C20", "C21", "C22")):
    put(c, 15.0 + 2.0 * i, 57.5, 90)
put("FB2", 15.0, 53.5)
put("FB3", 19.0, 53.5)
put("D21", 10.0, 73.5)
put("R24", 14.0, 73.5)

# --- JTAG (pins 15..20 at the bottom of the FPGA) and 50 MHz oscillator (pin 27)
put("J2", 26.0, 63.0)
for i, r in enumerate(("R32", "R33", "R34", "R37")):
    put(r, 33.0, 62.0 + 2.0 * i)
put("X1", 40.5, 61.0)
put("C44", 40.5, 56.0)

# --- protocol tester (2026-11-25): J6 / JP3 on the top edge (front), SMD parts on the bottom side between
# J6 and the FPGA (banks 5/6 face the top edge). Keep x < 68.5 on the bottom: TVS vias of J3 at x = 69.25.
put("J6", 43.65, 5.4, 90)                       # odd pins (PVCC, SCK, MOSI, CS, SDA, TX) y = 5.4, even y = 2.86
put("JP3", 63.0, 3.5, 90)
for i, (d, r) in enumerate(zip(("D25", "D26", "D27", "D28", "D29", "D30", "D31", "D32"),
                               ("R63", "R64", "R65", "R66", "R67", "R68", "R69", "R70"))):
    put(d, 40.5 + 2.4 * i, 9.3, 90, "B")        # TVS row next to J6
    put(r, 40.5 + 2.4 * i, 12.8, 90, "B")       # 33R series row
for i, (u, r) in enumerate(zip(("U14", "U15", "U16", "U17", "U18", "U19"), ("R57", "R58", "R59", "R60", "R61", "R62"))):
    x = 41.5 + 4.0 * i
    put(u, x, 21.5, 90, "B")                    # SN74LVC1T45 (rotated: SOT-23-6 courtyard 4.1 mm wide)
    put(r, x, 17.0, 90, "B")                    # 100k idle level
    put(f"C{52 + 2 * i}", x - 1.0, 25.5, 90, "B")
    put(f"C{53 + 2 * i}", x + 1.0, 25.5, 90, "B")
# I2C (left): U20 input translator, Q3 / Q4 open-drain drivers, pull-ups, gate pull-downs
put("U20", 33.5, 21.5, 0, "B")
put("C64", 32.5, 25.5, 90, "B")
put("C65", 34.5, 25.5, 90, "B")
put("Q3", 30.0, 15.0, 0, "B")
put("Q4", 36.0, 15.0, 0, "B")
put("R73", 30.0, 10.5, 90, "B")
put("R74", 36.0, 10.5, 90, "B")
put("R71", 30.0, 18.8, 0, "B")
put("R72", 36.0, 18.8, 0, "B")
# PVCC LDO (right): U13, divider R50..R54, enable Q2 / R55 / R56
put("U13", 63.5, 9.3, 0, "B")
put("C50", 60.8, 9.3, 90, "B")
put("C51", 66.2, 9.3, 90, "B")
for i, r in enumerate(("R50", "R51", "R52", "R53", "R54")):
    put(r, 59.5 + 2.0 * i, 12.8, 90, "B")
put("R55", 65.5, 16.5, 90, "B")
put("R56", 67.5, 16.5, 90, "B")
put("Q2", 65.5, 20.5, 0, "B")


# ---------------------------------------------------------------- netlist
TOK = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+')


def parse(t):
    st = [[]]
    for x in TOK.findall(t):
        if x == "(":
            st.append([])
        elif x == ")":
            l = st.pop()
            st[-1].append(l)
        else:
            st[-1].append(x[1:-1] if x.startswith('"') else x)
    return st[0][0]


def get(l, k):
    return [x for x in l if isinstance(x, list) and x and x[0] == k]


def load_netlist(path):
    r = parse(open(path, encoding="utf-8").read())
    comps = {}
    for c in get(get(r, "components")[0], "comp"):
        ref = get(c, "ref")[0][1]
        sp = get(c, "sheetpath")[0]
        fields = {}
        for fl in get(get(c, "fields")[0], "field") if get(c, "fields") else []:
            fields[get(fl, "name")[0][1]] = fl[2] if len(fl) > 2 and isinstance(fl[2], str) else ""
        comps[ref] = dict(value=get(c, "value")[0][1], fp=get(c, "footprint")[0][1], fields=fields,
                          path=get(sp, "tstamps")[0][1] + get(c, "tstamps")[0][1])
    pins = {}
    for n in get(get(r, "nets")[0], "net"):
        name = get(n, "name")[0][1]
        for node in get(n, "node"):
            pins[(get(node, "ref")[0][1], get(node, "pin")[0][1])] = name
    return comps, pins


def lib_path(nick, board_path):
    if nick == "seqgen16":
        return os.path.join(os.path.dirname(os.path.abspath(board_path)), "seqgen16.pretty")
    return os.path.join(FP, nick + ".pretty")


def main(net_path, board_path, move):
    comps, pins = load_netlist(net_path)
    on_board = set(re.findall(r'\(property "Reference" "([^"]+)"', open(board_path, encoding="utf-8").read()))
    # library footprints are loaded before the board (FootprintLoad misbehaves after LoadBoard)
    lib = {}
    for ref, c in comps.items():
        if ref not in on_board and c["fp"] not in lib:
            nick, name = c["fp"].split(":")
            f = pcbnew.FootprintLoad(lib_path(nick, board_path), name)
            if f is None:
                sys.exit(f"footprint {c['fp']} not found")
            lib[c["fp"]] = f
    board = pcbnew.LoadBoard(board_path)
    fps = {f.GetReference(): f for f in board.GetFootprints()}

    nets = {}

    def net(name):
        if name not in nets:
            ni = board.FindNet(name)
            if ni is None:
                ni = pcbnew.NETINFO_ITEM(board, name)
                board.Add(ni)
            nets[name] = ni
        return nets[name]

    added = moved = 0
    for ref, c in sorted(comps.items()):
        f = fps.get(ref)
        new = f is None
        if new:
            f = lib[c["fp"]].Duplicate(False).Cast()          # Duplicate = new UUIDs (footprint and pads)
            nick, name = c["fp"].split(":")
            f.SetFPID(pcbnew.LIB_ID(nick, name))
            f.SetReference(ref)
            if name in FINE_PITCH:                          # library pad gap < netclass clearance
                f.SetLocalClearance(mm(FINE_PITCH[name]))
            board.Add(f)
            fps[ref] = f
            added += 1
        f.SetValue(c["value"])
        f.SetPath(pcbnew.KIID_PATH(c["path"]))
        for fname, fval in c["fields"].items():
            if fname == "Footprint":
                continue
            if fname == "Datasheet":
                f.GetField(pcbnew.FIELD_T_DATASHEET).SetText(fval)
            elif fname == "Description":
                f.GetField(pcbnew.FIELD_T_DESCRIPTION).SetText(fval)
            elif f.HasField(fname):
                f.GetField(fname).SetText(fval)
            else:
                fld = pcbnew.PCB_FIELD(f, pcbnew.FIELD_T_USER, fname)
                fld.SetText(fval)
                fld.SetVisible(False)
                fld.SetLayer(pcbnew.F_Fab)
                f.Add(fld)
        if ref in PLACE and ref not in FIXED and (new or move):
            x, y, rot, side = PLACE[ref]
            if f.IsFlipped():
                f.Flip(f.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
            f.SetOrientationDegrees(0)
            f.SetPosition(pcbnew.VECTOR2I(mm(X0 + x), mm(Y0 + y)))
            f.SetOrientationDegrees(rot)
            if side == "B":
                f.Flip(f.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
            moved += 1
        elif new:
            print("no placement for", ref)
        for p in f.Pads():
            num = p.GetNumber()
            if not num:
                continue
            name = pins.get((ref, num))
            if name is None:
                if p.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH:
                    print(f"pad {ref}.{num} has no net")
                continue
            p.SetNet(net(name))
    for (ref, num) in pins:
        if ref in fps and not any(p.GetNumber() == num for p in fps[ref].Pads()):
            print(f"netlist pin {ref}.{num} has no pad")
    pcbnew.SaveBoard(board_path, board)
    print(f"{board_path}: +{added} footprints, {moved} placed, {len(nets)} nets, {len(board.GetFootprints())} total")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], "--move" in sys.argv)
