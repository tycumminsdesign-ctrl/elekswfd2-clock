#!/usr/bin/env python3
"""Fill each meter and spectrum column one segment at a time, cumulatively.

Easier to judge by eye than single-LED stepping: a correct map grows a solid
bar with no gaps and no back-tracking, so any wrong entry shows up as a hole
that fills in later, or a segment lighting out of sequence.

The digit row shows `zone` then the count of segments currently lit, so a
mis-step can be reported by number.

    filltest.py [--dwell 0.5] [--zone cpu]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from panel import METERS, VU_COLUMNS, Frame
from wfd2 import Clock, find_port

ZONES = {
    "cpu": ("1", lambda f, n: f.set_many(METERS["cpu"][:n])),
    "gpu": ("2", lambda f, n: f.set_many(METERS["gpu"][:n])),
    "ram": ("3", lambda f, n: f.set_many(METERS["ram"][:n])),
    "left": ("4", lambda f, n: f.set_many(VU_COLUMNS["left"][:n])),
    "right": ("5", lambda f, n: f.set_many(VU_COLUMNS["right"][:n])),
}
SIZES = {
    "cpu": len(METERS["cpu"]), "gpu": len(METERS["gpu"]), "ram": len(METERS["ram"]),
    "left": len(VU_COLUMNS["left"]), "right": len(VU_COLUMNS["right"]),
}


def hold(clock: Clock, frame: Frame, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        clock.send(frame)
        time.sleep(0.08)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dwell", type=float, default=0.5)
    ap.add_argument("--zone", choices=list(ZONES) + ["all"], default="all")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--port")
    args = ap.parse_args()

    names = list(ZONES) if args.zone == "all" else [args.zone]
    clock = Clock(find_port(args.port))
    try:
        while True:
            for name in names:
                code, draw = ZONES[name]
                total = SIZES[name]
                print(f"\n{name}: filling to {total} segments", flush=True)
                for n in range(1, total + 1):
                    f = Frame()
                    draw(f, n)
                    f.number(f"{code}  {n:02d}")
                    print(f"   {n:02d}", end="", flush=True)
                    hold(clock, f, args.dwell)
                print()
                hold(clock, Frame(), 0.6)
            if not args.loop:
                break
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        clock.close()


if __name__ == "__main__":
    main()
