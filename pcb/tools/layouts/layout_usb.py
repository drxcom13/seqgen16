"""USB-UART sheet (usb.kicad_sch): USB-C J1 -> ESD U3 -> FT232RNL U2, supply filter, TX/RX LEDs."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sch_build import Builder  # noqa: E402

RAIL = 73.66            # VBUS_USB -> FB1 -> VBUS


def build(path):
    b = Builder(path, paper="A4")
    P = b.pin

    # --- USB-C connector, CC pull-downs
    b.place("J1", 38.1, 101.6, fields={"Value": (38.1, 135.89, None)})
    b.place("R2", 63.5, 97.79)
    b.place("R1", 73.66, 97.79)
    b.wire(P("J1", "B5"), P("R2", "1"))                                    # CC2
    b.wire(P("J1", "A5"), (73.66, 91.44), P("R1", "1"))                    # CC1
    b.power("GND", P("R2", "2"))
    b.power("GND", P("R1", "2"))
    for p in ("A8", "B8"):
        b.nc(P("J1", p))
    b.wire(P("J1", "SH"), (30.48, 127), (60.96, 127))
    b.wire(P("J1", "A1"), (38.1, 127))
    b.power("GND", (38.1, 127))
    b.place("#FLG003", 60.96, 127, 0, {"Value": (60.96, 121.92, None)})

    # --- D-/D+ (pins A/B joined), one crossing because J1 has D- above D+
    b.wire(P("J1", "A7"), (58.42, 99.06))
    b.wire(P("J1", "B7"), (58.42, 101.6))
    b.wire((58.42, 99.06), (58.42, 111.76), (83.82, 111.76))
    b.wire(P("J1", "A6"), (55.88, 104.14))
    b.wire(P("J1", "B6"), (55.88, 106.68))
    b.wire((55.88, 104.14), (55.88, 109.22), (83.82, 109.22))

    # --- ESD protection (pins 1/6 and 3/4 are the same line -> named on both sides)
    b.place("U3", 88.9, 109.22)
    b.power("GND", P("U3", "2"))
    b.wire(P("U3", "6"), (121.92, 109.22))
    b.wire(P("U3", "4"), (121.92, 111.76))
    for x in (63.5, 101.6):
        b.label("USB_DP", (x, 109.22))
        b.label("USB_DM", (x, 111.76))

    # --- VBUS_USB (connector side) -> FB1 -> VBUS (whole board: FT232, buck, TXU VCCB)
    b.wire(P("J1", "A4"), (58.42, 86.36), (58.42, RAIL), (76.2, RAIL), (97.79, RAIL))
    b.label("VBUS_USB", (66.04, RAIL))
    b.place("D24", 76.2, RAIL + 3.81, 270, {"Reference": (78.74, 76.2, "left"),    # TVS SMF5.0A, cathode up
                                             "Value": (78.74, 78.74, "left")})
    b.power("GND", P("D24", "2"))
    b.wire(P("U3", "5"), (88.9, RAIL))
    b.place("#FLG004", 91.44, RAIL, 0, {"Value": (91.44, RAIL - 5.08, None)})
    b.place("FB1", 101.6, RAIL, 90)                                          # TDK MPZ2012S601AT000 (0805, 2 A)
    b.place("C1", 116.84, RAIL + 3.81)
    b.place("C2", 127.0, RAIL + 3.81)
    b.place("#FLG012", 137.16, RAIL, 0, {"Value": (137.16, RAIL - 5.08, None)})
    # --- U10 TPS22917 load switch: VBUS_FT (FT232) -> soft-started VBUS (whole board); ON = VIN, C45 CT -> VIN
    b.place("U10", 167.64, 60.96, fields={"Reference": (167.64, 52.07, None), "Value": (167.64, 54.61, None)})
    b.place("C45", 153.67, 60.96, 90, {"Reference": (153.67, 53.34, None), "Value": (153.67, 55.88, None)})
    b.wire(P("FB1", "2"), (144.78, RAIL), (149.86, RAIL), (149.86, 63.5), (149.86, 60.96), (149.86, 58.42),
           P("U10", "1"))
    b.wire((149.86, 63.5), P("U10", "3"))
    b.label("VBUS_FT", (149.86, 71.12), "up")
    b.power("GND", P("U10", "2"))
    b.nc(P("U10", "5"))                                                     # QOD unused
    b.wire(P("U10", "6"), (187.96, 58.42))
    b.glabel("VBUS", (187.96, 58.42), "right")
    b.power("GND", P("C1", "2"))
    b.power("GND", P("C2", "2"))

    # --- FT232RNL
    b.place("U2", 142.24, 119.38)
    b.wire((144.78, RAIL), P("U2", "20"))                                   # VCC = VBUS
    b.wire(P("U2", "4"), (139.7, 81.28), (132.08, 81.28))                   # VCCIO = +3V3
    b.power("+3V3", (139.7, 81.28))
    b.place("C3", 132.08, 85.09)
    b.power("GND", P("C3", "2"))
    b.place("C4", 111.76, 101.6, 270, {"Reference": (111.76, 97.79, None),
                                       "Value": (113.03, 104.78, "left")})      # 3V3OUT decoupling
    b.wire(P("U2", "17"), P("C4", "1"))
    b.power("GND", P("C4", "2"))
    b.power("GND", P("U2", "26"))                                           # TEST
    for p in ("19", "27", "28", "2", "3", "6", "9", "10", "11", "12", "13", "14"):
        b.nc(P("U2", p))
    for p in ("25", "7", "18", "21"):
        x = P("U2", p)[0]
        b.wire(P("U2", p), (x, 147.32))
    b.wire((137.16, 147.32), (147.32, 147.32))
    b.power("GND", (142.24, 147.32))
    b.wire(P("U2", "1"), (165.1, 101.6))
    b.glabel("RXD", (165.1, 101.6), "right")
    b.wire(P("U2", "5"), (165.1, 104.14))
    b.glabel("TXD", (165.1, 104.14), "right")

    # --- TX / RX LEDs (CBUS0 / CBUS1 sink current)
    for led, res, x, pin in (("D1", "R3", 182.88, "23"), ("D2", "R4", 195.58, "22")):
        b.place(res, x, 102.87)
        b.place(led, x, 114.3, 90)
        b.power("+3V3", P(res, "1"))
        b.wire(P(res, "2"), P(led, "2"))
        y = P("U2", pin)[1]
        b.wire(P("U2", pin), (x, y), P(led, "1"))
    b.save()


if __name__ == "__main__":
    build(sys.argv[1])
