#!/usr/bin/env python3
"""Play the bit-plane mapping sequence for filming.

Runs through the bridge firmware, so nothing needs reflashing: every frame is
just a 64-byte pattern sent over USB.

Frames, in order:
    all-on marker  ->  reference  ->  planes 0..8  ->  all-on marker

Plane k lights every LED whose index has bit k set, so an LED's presence or
absence across the nine planes spells out its 9-bit address. The markers
bracket the run so the frames can be located in the video automatically.

    python3 play_planes.py [--dwell 1.8]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mac"))

from panel import FRAME_BYTES, NUM_LEDS, Frame  # noqa: E402
from wfd2 import Clock, find_port               # noqa: E402

NPLANES = 9


def hold(clock: Clock, frame: Frame, seconds: float) -> None:
    """Display a frame for `seconds`, resending so the watchdog can't blank it."""
    end = time.time() + seconds
    while time.time() < end:
        clock.send(frame)
        time.sleep(0.25)


def all_on() -> Frame:
    f = Frame()
    for i in range(FRAME_BYTES):
        f.buf[i] = 0xFF
    return f


def plane(k: int) -> Frame:
    f = Frame()
    for led in range(NUM_LEDS):
        if (led >> k) & 1:
            f.set(led)
    return f


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dwell", type=float, default=1.3,
                    help="seconds per frame (default 1.8)")
    ap.add_argument("--gap", type=float, default=0.35)
    ap.add_argument("--marker", type=float, default=2.0)
    args = ap.parse_args()

    clock = Clock(find_port())
    blank = Frame()
    try:
        print(f"MARKER_START  ({args.marker}s all-on)")
        hold(clock, all_on(), args.marker)
        hold(clock, blank, args.gap * 2)

        # A fresh all-on reference before every plane. Phone auto-exposure
        # drifts over a half-minute take -- measured at ~25% dimming across
        # four seconds of identical content -- so comparing every plane against
        # one reference shot at the start silently corrupts the ratios. Pairing
        # each plane with a reference recorded moments earlier means both share
        # whatever the camera was doing at that instant.
        for k in range(NPLANES):
            print(f"FRAME ref{k}   ({args.dwell}s)")
            hold(clock, all_on(), args.dwell)
            hold(clock, blank, args.gap)
            print(f"FRAME plane {k} ({args.dwell}s)")
            hold(clock, plane(k), args.dwell)
            hold(clock, blank, args.gap)

        print(f"MARKER_END    ({args.marker}s all-on)")
        hold(clock, all_on(), args.marker)
        total = args.marker * 2 + args.gap * 2 + (args.dwell + args.gap) * NPLANES * 2
        print(f"done — sequence ran about {total:.0f}s")
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        clock.close()


if __name__ == "__main__":
    main()
