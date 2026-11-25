"""bin -> Intel HEX dla altera_avalon_onchip_memory2 (32 bit, adres = numer slowa).

Uzycie: python mkhex.py fw.bin onchip_ram.hex [rozmiar_bajtow=12288]
"""
import sys


def main():
    src, dst = sys.argv[1], sys.argv[2]
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 12288
    data = open(src, "rb").read()
    if len(data) > size:
        sys.exit(f"Program ({len(data)} B) nie miesci sie w RAM ({size} B)")
    data += bytes(size - len(data))
    lines = []
    for w in range(size // 4):
        b = data[4 * w: 4 * w + 4]
        rec = [4, (w >> 8) & 0xFF, w & 0xFF, 0, b[3], b[2], b[1], b[0]]  # slowo LE -> MSB pierwszy
        rec.append((-sum(rec)) & 0xFF)
        lines.append(":" + "".join(f"{x:02X}" for x in rec))
    lines.append(":00000001FF")
    open(dst, "w").write("\n".join(lines) + "\n")
    print(f"{dst}: {len(data.rstrip(bytes(1)))} B kodu / {size} B RAM")


if __name__ == "__main__":
    main()
