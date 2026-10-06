"""Genera los iconos PNG de la PWA sin dependencias externas (solo zlib + struct)."""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

BACKGROUND = (20, 22, 26, 255)
ACCENT = (110, 168, 254, 255)
GREEN = (78, 201, 138, 255)


def blend(base: tuple, top: tuple, alpha: float) -> tuple:
    return tuple(int(b * (1 - alpha) + t * alpha) for b, t in zip(base[:3], top[:3])) + (255,)


def canvas(size: int) -> list[list[tuple]]:
    return [[BACKGROUND for _ in range(size)] for _ in range(size)]


def fill_rect(pixels, x0, y0, x1, y1, color, alpha=1.0):
    size = len(pixels)
    for y in range(max(0, int(y0)), min(size, int(y1))):
        for x in range(max(0, int(x0)), min(size, int(x1))):
            pixels[y][x] = blend(pixels[y][x], color, alpha)


def rounded_rect(pixels, x0, y0, x1, y1, radius, color):
    size = len(pixels)
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    for y in range(max(0, y0), min(size, y1)):
        for x in range(max(0, x0), min(size, x1)):
            dx = dy = 0
            if x < x0 + radius:
                dx = (x0 + radius) - x
            elif x >= x1 - radius:
                dx = x - (x1 - radius - 1)
            if y < y0 + radius:
                dy = (y0 + radius) - y
            elif y >= y1 - radius:
                dy = y - (y1 - radius - 1)
            if dx * dx + dy * dy <= radius * radius:
                pixels[y][x] = blend(pixels[y][x], color, 1.0)


def draw_icon(size: int, maskable: bool = False) -> list[list[tuple]]:
    pixels = canvas(size)
    inset = 0 if maskable else int(size * 0.08)
    radius = 0 if maskable else int(size * 0.22)
    rounded_rect(pixels, inset, inset, size - inset, size - inset, radius, ACCENT)

    pad = size * 0.28 if not maskable else size * 0.34
    unit = max(2, int(size * 0.085))
    gap = int(size * 0.05)
    base_y = size - pad

    bars = [(1, GREEN), (3, ACCENT), (2, GREEN)]
    for index, (height, color) in enumerate(bars):
        x0 = pad + index * (unit + gap)
        rounded_rect(pixels, x0, base_y - height * unit - (height - 1) * gap, x0 + unit, base_y,
                     max(1, unit // 3), color)
    return pixels


def write_png(path: Path, pixels: list[list[tuple]]) -> None:
    size = len(pixels)
    raw = b"".join(
        b"\x00" + b"".join(struct.pack("BBBB", *pixel) for pixel in row) for row in pixels
    )

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def main(target: str = "static") -> None:
    folder = Path(target)
    folder.mkdir(parents=True, exist_ok=True)
    jobs = [
        ("icon-192.png", 192, False),
        ("icon-512.png", 512, False),
        ("icon-maskable-512.png", 512, True),
    ]
    for name, size, maskable in jobs:
        write_png(folder / name, draw_icon(size, maskable))
        print(f"{folder / name} ({size}x{size}{', maskable' if maskable else ''})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "static")