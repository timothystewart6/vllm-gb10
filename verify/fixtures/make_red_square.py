#!/usr/bin/env python3
"""Generate the tiny red-square image fixture used by the multimodal tests.

The CI verify harness checks in a 128x128 solid red PNG (verify/fixtures/
red-square.png). This script regenerates it deterministically so the fixture
can be reproduced rather than trusted as a binary blob. A solid red square is
deliberately trivial: a model either sees the color or it does not, which makes
the multimodal path the only variable under test.

Usage: python3 make_red_square.py [output.png]
"""

import struct
import sys
import zlib

WIDTH = 128
HEIGHT = 128
# Pure red, fully opaque. sRGB.
RED = (255, 0, 0, 255)


def chunk(tag: bytes, data: bytes) -> bytes:
    payload = tag + data
    return struct.pack(">I", len(data)) + payload + struct.pack(
        ">I", zlib.crc32(payload) & 0xFFFFFFFF
    )


def png_bytes() -> bytes:
    # Each scanline is prefixed with a filter byte (0 = none).
    raw = b"".join(
        b"\x00" + bytes(byte for pixel in (RED for _ in range(WIDTH)) for byte in pixel)
        for _ in range(HEIGHT)
    )
    ihdr = struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "red-square.png"
    with open(out, "wb") as fh:
        fh.write(png_bytes())
    print(f"wrote {out} ({WIDTH}x{HEIGHT})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
