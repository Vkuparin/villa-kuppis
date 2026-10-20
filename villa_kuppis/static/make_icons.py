"""Draw the PWA icons (pure Python, no dependencies).

Run once after changing the design: ``python villa_kuppis/static/make_icons.py``.
Writes icon-192.png, icon-512.png and icon-maskable-512.png beside this file.
The shapes match icon.svg: a white house on the app's blue.
"""

import struct
import zlib
from pathlib import Path

BLUE = (0x0B, 0x5F, 0xFF)
WHITE = (255, 255, 255)
SS = 3  # supersampling per axis


def _in_poly(x, y, pts):
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _in_round_rect(x, y, x0, y0, x1, y1, r):
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _sample(x, y, maskable):
    """Colour (r, g, b, a) at a point of the 512 x 512 design space."""
    if maskable:
        # Full bleed; the house is scaled into the central safe zone.
        x = 256 + (x - 256) / 0.7
        y = 256 + (y - 256) / 0.7
        bg = (0, 0, 512, 512, 0)
        base = (*BLUE, 255)
    else:
        bg = (0, 0, 512, 512, 96)
        base = (*BLUE, 255)
    house = [(256, 108), (96, 250), (146, 250), (146, 400),
             (366, 400), (366, 250), (416, 250)]
    if not maskable and not _in_round_rect(x, y, *bg[:4], bg[4]):
        return (0, 0, 0, 0)
    if _in_poly(x, y, house):
        if _in_round_rect(x, y, 224, 296, 288, 400, 8):
            return base
        return (*WHITE, 255)
    return base


def _png(size, maskable):
    rows = []
    for py in range(size):
        row = bytearray([0])
        for px in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(SS):
                for sx in range(SS):
                    x = (px + (sx + .5) / SS) * 512 / size
                    y = (py + (sy + .5) / SS) * 512 / size
                    r, g, b, a = _sample(x, y, maskable)
                    acc[0] += r * a
                    acc[1] += g * a
                    acc[2] += b * a
                    acc[3] += a
            n = SS * SS
            a = acc[3]
            if a:
                row += bytes([round(acc[0] / a), round(acc[1] / a),
                              round(acc[2] / a), round(a / n)])
            else:
                row += bytes(4)
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag, data):
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body)))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


if __name__ == "__main__":
    here = Path(__file__).parent
    (here / "icon-192.png").write_bytes(_png(192, False))
    (here / "icon-512.png").write_bytes(_png(512, False))
    (here / "icon-maskable-512.png").write_bytes(_png(512, True))
