#!/usr/bin/env python3
"""Find which display-memory bytes drive the 7x7 dot matrix.

The tool used to map the grid (result: GRID_ROWS in panel.py). Lights each
part of the frame no mapped element uses, with the step number on the time
digits, so someone watching the clock can say which dots each step lights.
Step 0 lights every candidate at once. Stop the daemon first -- two writers on
the port fight.

    python mac/gridcal.py [--seconds 5]          # run every step in turn
    python mac/gridcal.py --hold 3               # show step 3 until Ctrl+C
    python mac/gridcal.py --hold 8 --with 9 10   # steps 8, 9 and 10 together
    python mac/gridcal.py --raw 63:ef 60:08 --label 49   # exact byte:mask pairs
"""

from __future__ import annotations

import argparse
import time

from panel import Frame
from wfd2 import Clock, find_port

# (label, {byte: bitmask}) -- bits not used by any mapped element, from panel.py.
STEPS: list[dict[int, int]] = [
    {17: 0xFF}, {26: 0xFF}, {27: 0xFF}, {28: 0xFF}, {29: 0xFF},
    {33: 0xF3},
    {42: 0xCF}, {43: 0xFF}, {44: 0xFF}, {45: 0xFF}, {46: 0xFF}, {47: 0xFF},
    {49: 0xFF, 51: 0xFF, 53: 0xFF, 55: 0xFF},
    {57: 0xFF, 59: 0xFF, 61: 0xFF, 63: 0xFF},
    {19: 0x30, 25: 0x0E, 31: 0x02, 50: 0x08, 52: 0x08, 54: 0x08, 56: 0x08, 58: 0x08},
]


def frame_for(step: int, bits: dict[int, int]) -> Frame:
    f = Frame()
    f.text(f"{step:6d}")
    for byte, mask in bits.items():
        f.buf[byte] |= mask
    return f


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--hold", type=int, metavar="STEP", help="show one step until stopped")
    ap.add_argument("--raw", nargs="+", metavar="BYTE:MASK",
                    help="show exactly these byte:mask pairs (hex mask) until stopped, "
                         "e.g. 63:ff 61:f0; --label sets the number shown")
    ap.add_argument("--label", type=int, default=0)
    ap.add_argument("--with", dest="extra", type=int, nargs="*", default=[], metavar="STEP",
                    help="with --hold: also light these steps")
    args = ap.parse_args()
    everything: dict[int, int] = {}
    for s in STEPS:
        for b, m in s.items():
            everything[b] = everything.get(b, 0) | m
    plan = [everything] + STEPS
    clock = Clock(find_port())
    if args.raw:
        plan = [{int(b): int(m, 16) for b, m in (p.split(":") for p in args.raw)}]
        args.hold, args.extra = 0, []
    if args.hold is not None:
        bits = dict(plan[args.hold])
        for extra in args.extra:
            for b, m in plan[extra].items():
                bits[b] = bits.get(b, 0) | m
        f = frame_for(args.label if args.raw else args.hold, bits)
        print(f"holding bytes {sorted(bits)}", flush=True)
        try:
            while True:
                clock.send(f)
                time.sleep(0.5)
        except KeyboardInterrupt:
            clock.send(Frame())
    else:
        for i, bits in enumerate(plan):
            print(f"step {i}: bytes {sorted(bits)}", flush=True)
            f = frame_for(i, bits)
            end = time.time() + args.seconds
            while time.time() < end:
                clock.send(f)
                time.sleep(0.2)
        clock.send(Frame())


if __name__ == "__main__":
    main()
