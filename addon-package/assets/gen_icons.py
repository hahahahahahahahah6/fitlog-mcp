"""Generate FitLog add-on icon/carousel PNGs with stdlib only (no Pillow).

Simple dark rounded-square tile with a white dumbbell glyph. Run:
    python3 addon-package/assets/gen_icons.py
"""

import os
import struct
import zlib

BG = (15, 23, 42)      # slate-900
FG = (241, 245, 249)   # slate-100
ACCENT = (34, 197, 94)  # green-500


def write_png(path, w, h, rows):
    def chunk(typ, data):
        c = typ + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))

    raw = b"".join(
        b"\x00" + b"".join(struct.pack("3B", *px) for px in row) for row in rows
    )
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as f:
        f.write(png)


def draw_icon(size):
    s = size / 241.0
    rows = [[BG for _ in range(size)] for _ in range(size)]
    cx, cy = size / 2, size / 2

    def rect(x0, y0, x1, y1, color):
        for y in range(max(0, int(y0)), min(size, int(y1))):
            for x in range(max(0, int(x0)), min(size, int(x1))):
                rows[y][x] = color

    def circle(px, py, r, color):
        r2 = r * r
        for y in range(max(0, int(py - r)), min(size, int(py + r))):
            for x in range(max(0, int(px - r)), min(size, int(px + r))):
                if (x - px) ** 2 + (y - py) ** 2 <= r2:
                    rows[y][x] = color

    # bar
    bar_len, bar_t = 150 * s, 16 * s
    rect(cx - bar_len / 2, cy - bar_t / 2, cx + bar_len / 2, cy + bar_t / 2, FG)
    # plates: 3 per side, shrinking outward
    for side in (-1, 1):
        for i, (w, h) in enumerate([(26, 96), (20, 78), (15, 60)]):
            w, h = w * s, h * s
            x = cx + side * (bar_len / 2 - 14 * s - i * 30 * s)
            rect(x - w / 2, cy - h / 2, x + w / 2, cy + h / 2, FG)
    # accent dot under the bar
    circle(cx, cy + 62 * s, 10 * s, ACCENT)
    return rows


def draw_carousel(w, h):
    rows = [[BG for _ in range(w)] for _ in range(h)]
    s = min(w, h) / 241.0 * 1.6
    cx, cy = w / 2, h / 2 - 40 * s

    def rect(x0, y0, x1, y1, color):
        for y in range(max(0, int(y0)), min(h, int(y1))):
            for x in range(max(0, int(x0)), min(w, int(x1))):
                rows[y][x] = color

    bar_len, bar_t = 150 * s, 16 * s
    rect(cx - bar_len / 2, cy - bar_t / 2, cx + bar_len / 2, cy + bar_t / 2, FG)
    for side in (-1, 1):
        for i, (pw, ph) in enumerate([(26, 96), (20, 78), (15, 60)]):
            pw, ph = pw * s, ph * s
            x = cx + side * (bar_len / 2 - 14 * s - i * 30 * s)
            rect(x - pw / 2, cy - ph / 2, x + pw / 2, cy + ph / 2, FG)
    # accent underline bar
    rect(cx - 90 * s, cy + 110 * s, cx + 90 * s, cy + 122 * s, ACCENT)
    return rows


def main():
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(out, exist_ok=True)
    for size in (72, 64, 88, 126, 180, 241):
        p = os.path.join(out, f"icon-{size}x{size}.png")
        write_png(p, size, size, draw_icon(size))
        print("wrote", p)
    p = os.path.join(out, "carousel-600x900.png")
    write_png(p, 600, 900, draw_carousel(600, 900))
    print("wrote", p)


if __name__ == "__main__":
    main()
