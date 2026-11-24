"""Outputs sheet (outputs.kicad_sch): 4x TXU0104 + TXU0102 level shifters, 33R + TVS per output,
connectors J3 (2x16), J4 / J5 (2x2)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sch_build import Builder  # noqa: E402

ROW = 7.62
G = 2.54

# buffer, inputs (pin, global label), outputs (pin, R, TVS, net), VCCA, VCCB, OE, GND, NC, caps (+3V3, VCCB)
TXU4 = dict(vcca="1", vccb="14", oe="8", gnd="7", nc=("6", "9"))


def block(b, u, ux, uy, inputs, outputs, caps, pins, loop_dx=22.86, vccb="VCCB_A"):
    P = b.pin
    b.placed[u] = (ux, uy, 0, None)           # pin positions before the fields are set
    bot = P(u, pins["gnd"])[1]
    b.place(u, ux, uy, 0, {"Reference": (ux - 3.81, bot + 1.27, "right"),
                           "Value": (ux - 3.81, bot + 3.81, "right")})
    rail = min(P(u, pins["vcca"])[1] - 7.62, uy - 20.32)
    xa, xb = P(u, pins["vcca"])[0], P(u, pins["vccb"])[0]
    # VCCA (+3V3) rail with decoupling, OE tied high by a loop around the inputs
    xl = ux - loop_dx
    oe = P(u, pins["oe"])
    b.wire(P(u, pins["vcca"]), (xa, rail), (xl, rail), (xl, oe[1]), oe)
    b.power("+3V3", (xa, rail))
    b.place(caps[0], ux - 12.7, rail + 3.81)
    b.power("GND", P(caps[0], "2"))
    # VCCB rail (VCCB_A / VCCB_B from the LDOs on the Power sheet) with decoupling
    b.wire(P(u, pins["vccb"]), (xb, rail), (ux + 17.78, rail))
    b.place(caps[1], ux + 12.7, rail + 3.81)
    b.power("GND", P(caps[1], "2"))
    b.glabel(vccb, (ux + 17.78, rail), "right")
    b.power("GND", P(u, pins["gnd"]))
    for p in pins["nc"]:
        b.nc(P(u, p))
    for pin, name in inputs:
        q = P(u, pin)
        b.wire(q, (q[0] - G, q[1]))
        b.glabel(name, (q[0] - G, q[1]), "left")
    # outputs: fan out to rows 7.62 mm apart, 33R -> node (label) -> TVS -> GND rail
    pts = [P(u, o[0]) for o in outputs]
    n = len(outputs)
    xp = pts[0][0]
    y0 = pts[0][1]
    xr1 = xp + 4 * G
    xr = xr1 + 3.81
    xn = xr + 3.81 + 2 * G
    xd = xn + 4 * G + 3.81
    xd2 = xd + 3.81
    rows = [y0 + ROW * i for i in range(n)]
    for i, ((pin, r, d, net), q, y) in enumerate(zip(outputs, pts, rows)):
        if i == 0:
            b.wire(q, (xr1, y))
        else:
            xj = xp + G * (n - i)
            b.wire(q, (xj, q[1]), (xj, y), (xr1, y))
        b.place(r, xr, y, 90, {"Reference": (xr, y - G, None), "Value": (xr, y + G, None)})
        b.wire(P(r, "2"), P(r, "2"), (xn, y), (xd - 3.81, y))
        b.label(net, (xn, y))
        b.place(d, xd, y, 0, {"Reference": (xd, y - G, None), "Value": (xd, y + G, None)})
    b.wire((xd2, rows[0]), (xd2, rows[-1] + 2 * G))
    b.power("GND", (xd2, rows[-1] + 2 * G))


def header2(b, j, x, y, net):
    """2x2 header: pins 1/3 = signal (joined, label), 2/4 = GND."""
    P = b.pin
    b.place(j, x, y)
    p1, p3, p2, p4 = P(j, "1"), P(j, "3"), P(j, "2"), P(j, "4")
    b.wire(p1, (p1[0] - G, p1[1]), (p3[0] - G, p3[1]), p3)
    b.wire((p1[0] - G, p1[1]), (p1[0] - 5 * G, p1[1]))
    b.label(net, (p1[0] - 5 * G, p1[1]), "right")
    b.wire(p2, (p2[0] + G, p2[1]), (p4[0] + G, p4[1] + G))
    b.wire(p4, (p4[0] + G, p4[1]))
    b.power("GND", (p4[0] + G, p4[1] + G))


def build(path):
    b = Builder(path, paper="A4")
    ch = 0
    rr, dd = 5, 3
    for u, ux, uy, caps in (("U4", 38.1, 73.66, ("C5", "C6")), ("U5", 129.54, 73.66, ("C7", "C8")),
                            ("U6", 220.98, 73.66, ("C9", "C10")), ("U7", 38.1, 142.24, ("C11", "C12"))):
        inputs = [(p, f"CH{ch + i}") for i, p in enumerate(("2", "3", "4", "5"))]
        outputs = [(p, f"R{rr + i}", f"D{dd + i}", f"OUT_CH{ch + i}") for i, p in enumerate(("13", "12", "11", "10"))]
        block(b, u, ux, uy, inputs, outputs, caps, TXU4, vccb="VCCB_A" if u in ("U4", "U5") else "VCCB_B")
        ch += 4
        rr += 4
        dd += 4
    block(b, "U8", 137.16, 142.24, [("5", "BASE_CLK"), ("4", "SYNC")],
          [("8", "R21", "D19", "OUT_CLK"), ("1", "R22", "D20", "OUT_SYNC")], ("C13", "C14"),
          dict(vcca="3", vccb="7", oe="6", gnd="2", nc=()), loop_dx=30.48, vccb="VCCB_B")

    # --- connectors
    b.move_text("J3: odd pins", 25.4, 43.18)
    P = b.pin
    b.place("J3", 233.68, 132.08)
    for k in range(16):
        p = P("J3", str(2 * k + 1))
        b.wire(p, (p[0] - 3 * G, p[1]))
        b.label(f"OUT_CH{k}", (p[0] - 3 * G, p[1]), "left")
        e = P("J3", str(2 * k + 2))
        b.wire(e, (e[0] + G, e[1]))
    top, bot = P("J3", "2"), P("J3", "32")
    b.wire((top[0] + G, top[1]), (bot[0] + G, bot[1] + G))
    b.power("GND", (bot[0] + G, bot[1] + G))
    header2(b, "J4", 269.24, 119.38, "OUT_CLK")
    header2(b, "J5", 269.24, 134.62, "OUT_SYNC")
    b.save()


if __name__ == "__main__":
    build(sys.argv[1])
