"""Power sheet (power.kicad_sch): VBUS -> TPSM828302 -> +3V3, VCCA / VCC_CORE ferrite filters,
VCCIO decoupling."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sch_build import Builder  # noqa: E402


def cap_row(b, refs, x0, rail_y, step=10.16):
    for i, r in enumerate(refs):
        x = x0 + i * step
        b.place(r, x, rail_y + 3.81)
        b.power("GND", b.pin(r, "2"))
    return x0 + (len(refs) - 1) * step


def build(path):
    b = Builder(path, paper="A4")
    P = b.pin

    # --- buck converter: VBUS rail (y 60.96) -> U9 -> +3V3 rail (y 66.04)
    vin = 60.96
    b.glabel("VBUS", (25.4, vin), "left")
    b.place("C15", 30.48, vin + 3.81)
    b.place("C16", 45.72, vin + 3.81)
    b.place("R23", 55.88, vin + 3.81)                 # EN pull-up from VBUS
    b.place("C17", 55.88, vin + 11.43)                # EN delay
    for r in ("C15", "C16", "C17"):
        b.power("GND", P(r, "2"))
    b.place("U9", 81.28, 71.12)
    b.wire((25.4, vin), (68.58, vin), P("U9", "1"))    # VIN
    b.wire(P("R23", "2"), P("U9", "9"))               # EN
    b.power("GND", P("U9", "3"))                      # MODE = GND (auto PSM/PWM)
    b.power("GND", P("U9", "2"))
    b.nc(P("U9", "7"))
    b.nc(P("U9", "8"))
    vout = P("U9", "6")[1]
    b.wire(P("U9", "5"), (96.52, P("U9", "5")[1]), (96.52, vout))     # VOS -> VOUT
    b.place("R35", 101.6, vout + 3.81)
    b.place("R36", 101.6, vout + 11.43)
    b.wire(P("U9", "4"), P("R35", "2"))               # FB
    b.power("GND", P("R36", "2"))
    b.power("+3V3", (109.22, vout))
    last = cap_row(b, ["C18", "C19", "C20", "C21", "C22"], 116.84, vout)
    b.place("R24", 167.64, vout + 3.81)               # power LED
    b.place("D21", 167.64, vout + 11.43, 90, {"Reference": (171.45, vout + 10.16, "left"),
                                              "Value": (171.45, vout + 12.7, "left")})
    b.wire(P("R24", "2"), P("D21", "2"))
    b.power("GND", P("D21", "1"))
    b.wire(P("U9", "6"), (167.64, vout))
    assert last < 167.64

    # --- VCCA_3V3 and VCC_CORE: +3V3 -> ferrite -> rail with decoupling, PWR_FLAG, global label
    b.move_text("VCCA / VCC_ONE", 25.4, 96.52)
    rail = 114.3
    for fb, caps, flag, name, x0 in (("FB2", ["C23", "C24", "C25", "C26", "C27", "C28", "C29", "C30"],
                                      "#FLG326", "VCCA_3V3", 30.48),
                                     ("FB3", ["C31", "C32", "C33", "C34"], "#FLG336", "VCC_CORE", 147.32)):
        b.place(fb, x0, rail - 3.81)
        b.power("+3V3", P(fb, "1"))
        xe = cap_row(b, caps, x0 + 10.16, rail)
        b.place(flag, xe + 5.08, rail, 0, {"Value": (xe + 5.08, rail - 5.08, None)})
        b.wire(P(fb, "2"), (xe + 7.62, rail))
        b.glabel(name, (xe + 7.62, rail), "right")

    # --- VCCIO decoupling on +3V3
    rail = 137.16
    b.power("+3V3", (30.48, rail))
    xe = cap_row(b, ["C35", "C36", "C37", "C38", "C39", "C40", "C41", "C42", "C43"], 40.64, rail)
    b.wire((30.48, rail), (xe, rail))
    b.save()


if __name__ == "__main__":
    build(sys.argv[1])
