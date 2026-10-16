#!/usr/bin/env python3
"""Step through the meters and spectrum columns one LED at a time, for filming.

Each step lights exactly ONE mapped LED and prints its position number on the
digit row, so a video of the run says which map entry drives which physical
segment. Misalignments then show up directly: a step whose LED sits out of
sequence, or lights nothing at all, is a wrong entry in panel.py.

Bracketed by all-on markers so the run can be located in the footage.

    sweeptest.py [--dwell 0.6]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from panel import FRAME_BYTES, METERS, VU_COLUMNS, Frame
from wfd2 import Clock, find_port

# Digit-row labels: the zone marker sits in the leftmost slot, the step index
# in the rightmost two, so a single frame identifies both.
ZONE_CODES = {"cpu": 1, "gpu": 2, "ram": 3, "left": 4, "right": 5}


def hold(clock: Clock, frame: Frame, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        clock.send(frame)
        time.sleep(0.08)


def all_on() -> Frame:
    f = Frame()
    for i in range(FRAME_BYTES):
        f.buf[i] = 0xFF
    return f


def sweep(clock: Clock, zone: str, leds: list[int], dwell: float) -> None:
    code = ZONE_CODES[zone]
    print(f"\n{zone}: {len(leds)} LEDs (zone code {code})", flush=True)
    for i, led in enumerate(leds):
        f = Frame()
        f.set(led)
        # e.g. zone 1, step 07  ->  "1  07"
        f.number(f"{code}  {i:02d}")
        print(f"   step {i:02d}  led {led}", flush=True)
        hold(clock, f, dwell)
        blank = Frame()
        blank.number(f"{code}  {i:02d}")   # keep the label, drop the LED
        hold(clock, blank, dwell * 0.35)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dwell", type=float, default=0.6)
    ap.add_argument("--port")
    args = ap.parse_args()

    clock = Clock(find_port(args.port))
    try:
        print("MARKER_START")
        hold(clock, all_on(), 2.0)
        hold(clock, Frame(), 1.0)

        for zone in ("cpu", "gpu", "ram"):
            sweep(clock, zone, METERS[zone], args.dwell)
        for zone in ("left", "right"):
            sweep(clock, zone, VU_COLUMNS[zone], args.dwell)

        print("\nMARKER_END")
        hold(clock, all_on(), 2.0)
        total = sum(len(METERS[z]) for z in ("cpu", "gpu", "ram")) + \
            sum(len(VU_COLUMNS[z]) for z in ("left", "right"))
        print(f"done — {total} steps, about "
              f"{total * args.dwell * 1.35 + 5:.0f}s")
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        clock.close()


if __name__ == "__main__":
    main()
