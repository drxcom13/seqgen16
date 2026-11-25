"""make_board.py - board outline + edge connectors + M3 holes + placement zones for seqgen16.kicad_pcb.

Run with KiCad's Python (pcbnew module):
    "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" make_board.py ..\\seqgen16\\seqgen16.kicad_pcb

Board: square W x W mm (centred on the A4 sheet), rounded corners, 4x M3 holes in the corners.
Right edge: J3 (2x16, OUT_CH0..15), J4 (CLK), J5 (SYNC) - right-angle headers, body flush with the edge.
Left edge: J1 USB-C. Other blocks: rectangles + text on Dwgs.User (placement proposal).
After "Update PCB from Schematic" (option: re-link footprints by reference) J1/J3/J4/J5 keep their places.
"""
import sys
from pathlib import Path

import pcbnew

FP = r"C:\Program Files\KiCad\10.0\share\kicad\footprints"
W = 80.0          # board side, mm
PAGE = (297.0, 210.0)               # A4 landscape (paper "A4" in the .kicad_pcb)
X0 = (PAGE[0] - W) / 2              # board centred in the drawing sheet frame
Y0 = (PAGE[1] - W) / 2
R = 3.0           # corner radius
HOLE = 4.0        # hole centre inset from the edges
mm = pcbnew.FromMM


def V(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))


def shape(board, kind, layer, a, b, mid=None, width=0.1):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(kind)
    if kind == pcbnew.SHAPE_T_ARC:
        s.SetArcGeometry(V(*a), V(*mid), V(*b))
    else:
        s.SetStart(V(*a))
        s.SetEnd(V(*b))
    s.SetLayer(layer)
    s.SetWidth(mm(width))
    board.Add(s)


def text(board, s, x, y, size=1.0, layer=pcbnew.Dwgs_User):
    t = pcbnew.PCB_TEXT(board)
    t.SetText(s)
    t.SetPosition(V(x, y))
    t.SetLayer(layer)
    t.SetTextSize(V(size, size))
    t.SetTextThickness(mm(size * 0.15))
    board.Add(t)


_LIBS = {}


def load_fp(lib, name):
    """Footprint from the KiCad library (loaded before the board is opened - pcbnew.FootprintLoad
    fails after LoadBoard of a board that already holds footprints)."""
    if (lib, name) not in _LIBS:
        directory = (Path(__file__).resolve().parents[1] / "seqgen16" / "seqgen16.pretty"
                     if lib == "seqgen16" else Path(FP) / f"{lib}.pretty")
        _LIBS[(lib, name)] = pcbnew.FootprintLoad(str(directory), name)
    return pcbnew.FOOTPRINT(_LIBS[(lib, name)])


FOOTPRINTS = [("MountingHole", "MountingHole_3.2mm_M3"),
              ("Connector_PinHeader_2.54mm", "PinHeader_2x16_P2.54mm_Horizontal"),
              ("Connector_PinHeader_2.54mm", "PinHeader_2x02_P2.54mm_Horizontal"),
              ("seqgen16", "USB4105_16P_NPTH_Clearance")]


def footprint(board, lib, name, ref, value, x, y, angle=0.0, board_only=False):
    f = load_fp(lib, name)
    f.SetFPID(pcbnew.LIB_ID(lib, name))
    f.SetReference(ref)
    f.SetValue(value)
    f.SetPosition(V(x, y))
    f.SetOrientationDegrees(angle)
    if board_only:
        f.SetBoardOnly(True)
        f.SetExcludedFromBOM(True)
        f.SetExcludedFromPosFiles(True)
    board.Add(f)
    return f


def zone(board, x0, y0, x1, y1, label):
    shape(board, pcbnew.SHAPE_T_RECTANGLE, pcbnew.Dwgs_User, (x0, y0), (x1, y1), width=0.15)
    for i, line in enumerate(label.split("\n")):
        text(board, line, (x0 + x1) / 2, y0 + 1.6 + 1.4 * i, 0.9)


def main(path):
    # pcbnew misbehaves when editing a loaded board that already holds items, so the board is
    # rebuilt from an empty file - only allowed while it holds nothing but what this script made
    import re
    old = open(path, encoding="utf-8").read()
    refs = set(re.findall(r'\(property "Reference" "([^"]+)"', old))
    if refs - {"J1", "J3", "J4", "J5", "H1", "H2", "H3", "H4"}:
        sys.exit(f"{path} already holds other footprints ({sorted(refs)[:5]}...) - not touched")
    open(path, "w", encoding="utf-8", newline="\n").write(
        '(kicad_pcb (version 20260206) (generator "pcbnew") (generator_version "10.0")\n)\n')
    for lib, name in FOOTPRINTS:
        load_fp(lib, name)
    board = pcbnew.LoadBoard(path)
    xa, ya, xb, yb = X0, Y0, X0 + W, Y0 + W
    E = pcbnew.Edge_Cuts
    # --- outline with rounded corners
    seg = pcbnew.SHAPE_T_SEGMENT
    shape(board, seg, E, (xa + R, ya), (xb - R, ya))
    shape(board, seg, E, (xb, ya + R), (xb, yb - R))
    shape(board, seg, E, (xb - R, yb), (xa + R, yb))
    shape(board, seg, E, (xa, yb - R), (xa, ya + R))
    k = R * (1 - 0.70710678)
    arc = pcbnew.SHAPE_T_ARC
    shape(board, arc, E, (xb - R, ya), (xb, ya + R), mid=(xb - k, ya + k))
    shape(board, arc, E, (xb, yb - R), (xb - R, yb), mid=(xb - k, yb - k))
    shape(board, arc, E, (xa + R, yb), (xa, yb - R), mid=(xa + k, yb - k))
    shape(board, arc, E, (xa, ya + R), (xa + R, ya), mid=(xa + k, ya + k))

    # --- 4x M3 mounting holes
    for i, (x, y) in enumerate(((xa + HOLE, ya + HOLE), (xb - HOLE, ya + HOLE),
                                (xa + HOLE, yb - HOLE), (xb - HOLE, yb - HOLE)), 1):
        footprint(board, "MountingHole", "MountingHole_3.2mm_M3", f"H{i}", "M3", x, y, board_only=True)

    # --- right edge: right-angle headers, plastic body (x 4.04..6.58 from pin 1) flush with the edge
    xp = xb - 6.58
    footprint(board, "Connector_PinHeader_2.54mm", "PinHeader_2x16_P2.54mm_Horizontal", "J3", "OUT CH0..15",
              xp, ya + 10.0)
    footprint(board, "Connector_PinHeader_2.54mm", "PinHeader_2x02_P2.54mm_Horizontal", "J4", "CLK",
              xp, ya + 56.0)
    footprint(board, "Connector_PinHeader_2.54mm", "PinHeader_2x02_P2.54mm_Horizontal", "J5", "SYNC",
              xp, ya + 65.0)
    # --- left edge: USB-C (footprint "PCB Edge" line at local y = +3.675, opening turned to the left)
    footprint(board, "seqgen16", "USB4105_16P_NPTH_Clearance", "J1",
              "USB_C_Receptacle_USB2.0_16P", xa + 3.675, ya + 20.0, angle=-90)

    # --- placement proposal (Dwgs.User)
    zone(board, xa + 21, ya + 25, xa + 47, ya + 51, "U1 10M08SAE144\nrotated 90 CCW:\nbank 3 (pins 37..72)\nfacing the outputs\n"
         "+ VCCIO/VCCA/VCC_ONE\ndecoupling (C23..C43)\nbottom side / around")
    zone(board, xa + 49, ya + 8, xa + 58.5, ya + 72, "U4..U8\nTXU0104\nTXU0102\n+ C5..C14")
    zone(board, xa + 59.5, ya + 8, xa + 72.5, ya + 72, "R5..R22 33R\nD3..D20 TVS\nnear connectors")
    zone(board, xa + 9, ya + 14, xa + 13.5, ya + 26, "U3\nESD")
    zone(board, xa + 14.5, ya + 6, xa + 30, ya + 22, "U2 FT232RNL\n+ FB1, C1..C4\nLED D1/D2 TX/RX")
    zone(board, xa + 2, ya + 50, xa + 22, ya + 71, "U9 TPSM828302\nC15..C22, R23..R24\nR35/R36, D21 PWR\n"
         "FB2/FB3\n(VCCA, VCC_CORE)")
    zone(board, xa + 2, ya + 29, xa + 19, ya + 48, "SW1 RESET, JP1, JP2\nR25..R29\nLED D22/D23\n(nCONFIG/CONF_DONE\nU1 left side)")
    zone(board, xa + 23, ya + 62, xa + 45, ya + 76, "J2 JTAG 2x5\nR32..R34, R37\n(pins 15..19\nU1 bottom side)")
    zone(board, xa + 33, ya + 53, xa + 47, ya + 60.5, "X1 50 MHz + C44\n(pin 27)")
    text(board, f"SeqGen16 {W:.0f} x {W:.0f} mm, 4x M3", xa + W / 2, ya - 3, 1.5)
    text(board, "OUTPUTS ->", xb + 17, ya + W / 2, 1.5)
    text(board, "<- USB", xa - 8, ya + 20, 1.5)

    # --- 4 copper layers: F.Cu signals, In1.Cu GND plane, In2.Cu power (+3V3 / VBUS), B.Cu signals
    board.SetCopperLayerCount(4)
    board.SetLayerName(pcbnew.In1_Cu, "In1.GND")
    board.SetLayerName(pcbnew.In2_Cu, "In2.PWR")
    text(board, "4 layers: F.Cu signals | In1 GND | In2 +3V3/VBUS | B.Cu signals", xa + W / 2, yb + 4, 1.2)

    board.GetDesignSettings().SetAuxOrigin(V(xa, ya))
    board.GetDesignSettings().SetGridOrigin(V(xa, ya))
    pcbnew.SaveBoard(path, board)
    print(f"{path}: {W:.0f}x{W:.0f} mm, {len(board.GetFootprints())} footprints")


if __name__ == "__main__":
    main(sys.argv[1])
