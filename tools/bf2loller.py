#!/usr/bin/env python3
"""bf2loller.py - lay a Brainfuck program out as a Brainloller PNG image.

Usage: python3 tools/bf2loller.py [-w WIDTH] json2dir.bf json2dir.png

The command stream is laid out as a boustrophedon snake: row 0 runs left to
right, a pair of clockwise rotations at the right edge sends the IP into row 1
running right to left, a pair of counter-clockwise rotations at the left edge
sends it into row 2 running left to right, and so on.  Every row has a
rotation pixel at each end (row 0 starts with a no-op pixel instead); the
cells in between carry WIDTH-2 commands.  The tail of the last row is padded
with no-op pixels (black), after which the IP walks off the image and the
program ends.

Colours (esolangs wiki table, as used by Brainfunk's CLollerState):
  >  (255,0,0)    <  (128,0,0)    +  (0,255,0)    -  (0,128,0)
  .  (0,0,255)    ,  (0,0,128)    [  (255,255,0)  ]  (128,128,0)
  rotate clockwise (0,255,255)    rotate counter-clockwise (0,128,128)
  anything else (here black) is a no-op.

Only stdlib: zlib + struct for the PNG (8-bit RGB, no interlace).
"""
import struct
import sys
import zlib

COLOURS = {
    '>': (255, 0, 0), '<': (128, 0, 0),
    '+': (0, 255, 0), '-': (0, 128, 0),
    '.': (0, 0, 255), ',': (0, 0, 128),
    '[': (255, 255, 0), ']': (128, 128, 0),
}
CW = (0, 255, 255)
CCW = (0, 128, 128)
NOP = (0, 0, 0)


def layout(code, width):
    inner = width - 2
    nrows = max(1, -(-len(code) // inner))
    rows = []
    for r in range(nrows):
        chunk = code[r * inner:(r + 1) * inner]
        cells = [COLOURS[c] for c in chunk] + [NOP] * (inner - len(chunk))
        last = r == nrows - 1
        if r % 2 == 0:
            # entered at x=0 moving down (CCW turns it right); row 0 starts here moving right
            left = NOP if r == 0 else CCW
            right = NOP if last else CW          # moving right, CW turns it down
            rows.append([left] + cells + [right])
        else:
            # entered at x=W-1 moving down (CW turns it left), runs right to left
            left = NOP if last else CCW          # moving left, CCW turns it down
            rows.append([left] + cells[::-1] + [CW])
    return rows


def write_png(path, rows):
    height, width = len(rows), len(rows[0])
    raw = bytearray()
    for row in rows:
        raw.append(0)  # filter type: none
        for rgb in row:
            raw += bytes(rgb)

    def chunk(kind, data):
        return (struct.pack('>I', len(data)) + kind + data
                + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff))

    png = (b'\x89PNG\r\n\x1a\n'
           + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(bytes(raw), 9))
           + chunk(b'IEND', b''))
    with open(path, 'wb') as f:
        f.write(png)


def main(argv):
    width = 352
    if len(argv) >= 2 and argv[0] == '-w':
        width = int(argv[1])
        argv = argv[2:]
    if len(argv) != 2 or width < 3:
        sys.exit('usage: bf2loller.py [-w WIDTH] in.bf out.png')
    with open(argv[0], encoding='latin-1') as f:
        code = ''.join(c for c in f.read() if c in COLOURS)
    rows = layout(code, width)
    write_png(argv[1], rows)
    print(f'{len(code)} commands -> {len(rows[0])}x{len(rows)} pixels', file=sys.stderr)


if __name__ == '__main__':
    main(sys.argv[1:])
