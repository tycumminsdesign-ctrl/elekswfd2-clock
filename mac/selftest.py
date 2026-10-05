#!/usr/bin/env python3
"""Exercise every mapped zone of the panel, one at a time.

Each stage announces itself on the terminal AND, where possible, labels itself
on the digit row, so any moment of the test is self-describing. Run it after
changing the map in panel.py to confirm nothing regressed.

    selftest.py            # full run
    selftest.py --loop     # repeat until interrupted
"""

from __future__ import annotations

import argparse
import datetime
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from panel import DAY_MASKS, METERS, VU_COLUMNS, Frame
from wfd2 import Clock, find_port

DAY_NAMES = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def hold(clock: Clock, frame: Frame, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        clock.send(frame)
        time.sleep(0.1)


def stage(name: str) -> None:
    print(f"\n>>> {name}", flush=True)


def run(clock: Clock) -> None:
    f = Frame()

    stage("1. all LEDs on — every zone should light")
    f.clear()
    for i in range(len(f.buf)):
        f.buf[i] = 0xFF
    hold(clock, f, 3)

    stage("2. digits: counting 000000 -> 999999")
    for d in range(10):
        f.clear()
        f.number(str(d) * 6)
        hold(clock, f, 0.7)

    stage("3. digits: a live clock with separators")
    for _ in range(4):
        now = datetime.datetime.now()
        f.clear()
        f.number(now.strftime("%H%M%S"), points={1, 3})
        hold(clock, f, 1.0)

    stage("4. days: MON through SUN in turn")
    for i, day in enumerate(DAY_NAMES):
        f.clear()
        f.weekday(i)
        f.number(day[:3].replace("MON", "1").replace("TUE", "2")
                 .replace("WED", "3").replace("THU", "4").replace("FRI", "5")
                 .replace("SAT", "6").replace("SUN", "7"))
        print(f"    {day}")
        hold(clock, f, 1.5)

    stage("5. meters: each one sweeps 0 -> 100% alone")
    for name in METERS:
        print(f"    {name}")
        for pct in range(0, 101, 8):
            f.clear()
            f.meter(name, pct)
            hold(clock, f, 0.08)
        hold(clock, f, 0.5)

    stage("6. meters: all three at different levels")
    f.clear()
    f.meter("cpu", 100); f.meter("gpu", 60); f.meter("ram", 30)
    hold(clock, f, 2.5)

    stage("7. spectrum: each column sweeps alone, then together")
    for col in VU_COLUMNS:
        print(f"    {col}")
        for step in range(12):
            f.clear()
            f.vu(col, step / 11)
            hold(clock, f, 0.12)
    for t in range(60):
        f.clear()
        f.vu("left", (math.sin(t * 0.3) + 1) / 2)
        f.vu("right", (math.sin(t * 0.3 + 1.2) + 1) / 2)
        hold(clock, f, 0.05)

    stage("8. everything together — the real display")
    for t in range(80):
        now = datetime.datetime.now()
        f.clear()
        f.number(now.strftime("%H%M%S"), points={1, 3})
        f.weekday(now.weekday())
        f.meter("cpu", 50 + 40 * math.sin(t * 0.15))
        f.meter("gpu", 50 + 40 * math.sin(t * 0.15 + 2))
        f.meter("ram", 50 + 40 * math.sin(t * 0.15 + 4))
        f.vu("left", (math.sin(t * 0.4) + 1) / 2)
        f.vu("right", (math.sin(t * 0.4 + 1.2) + 1) / 2)
        hold(clock, f, 0.06)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--port")
    args = ap.parse_args()

    clock = Clock(find_port(args.port))
    try:
        while True:
            run(clock)
            print("\nself-test complete")
            if not args.loop:
                break
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        clock.close()


if __name__ == "__main__":
    main()
