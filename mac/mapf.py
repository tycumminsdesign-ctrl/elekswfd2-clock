#!/usr/bin/env python3
"""Interactive mapper for the TOP 14-segment character row (bytes 48-59).

Why this exists: the top row packs 14 thin segments per character, too fine for
the webcam to resolve, so it's mapped by eye. This lights ONE segment at a time
on a chosen character and asks you where it lit. A background thread keeps the
frame refreshed (the firmware blanks the panel after ~5 s of silence).

Run it, answer the prompts for character 0, and it writes the segment->bit map
to /private/tmp/.../scratchpad/topmap.json for the driver to pick up.

    python3 mapf.py            # map character 0 (leftmost top-row char)
    python3 mapf.py --char 1   # spot-check another character uses the same layout
"""

from __future__ import annotations

import argparse
import fcntl
import glob
import json
import os
import sys
import threading
import time

import serial

BAUD = 115200
OUT = "mapping/topmap.json"

# Each top-row character is two consecutive bytes: char c -> bytes 48+2c, 49+2c.
def char_bytes(c: int) -> tuple[int, int]:
    return (48 + 2 * c, 49 + 2 * c)

# The physical left-to-right bit order on this panel (same everywhere).
BIT_ORDER = [4, 5, 6, 7, 0, 1, 2, 3]

# Plain-language segment tokens. The six outline segments use the names you
# already know from the bottom row; the two middle halves and six diagonals get
# unambiguous position names. Type the token (or its number) for what lights.
SEGMENTS = [
    ("top",       "A  - top horizontal bar"),
    ("topright",  "B  - upper-right vertical"),
    ("botright",  "C  - lower-right vertical"),
    ("bottom",    "D  - bottom horizontal bar"),
    ("botleft",   "E  - lower-left vertical"),
    ("topleft",   "F  - upper-left vertical"),
    ("midleft",   "G1 - middle bar, LEFT half"),
    ("midright",  "G2 - middle bar, RIGHT half"),
    ("uldiag",    "H  - upper-LEFT diagonal  \\"),
    ("ucvert",    "J  - upper-CENTER vertical |"),
    ("urdiag",    "K  - upper-RIGHT diagonal  /"),
    ("lldiag",    "L  - lower-LEFT diagonal   /"),
    ("lcvert",    "M  - lower-CENTER vertical |"),
    ("lrdiag",    "N  - lower-RIGHT diagonal  \\"),
    ("dot",       "P  - a dot / decimal point"),
    ("none",      "   - nothing lit at all"),
]

DIAGRAM = r"""
   14-segment reference (one character):

        --------- top ---------
       | \       |       / |
       |  \      |      /  |
    topleft ucvert   urdiag topright
       |    \    |    /    |
       | uldiag  |  (K)    |
        --midleft---midright--
       | lldiag  |  (N)    |
       |    /    |    \    |
    botleft lcvert   lrdiag botright
       |  /      |      \  |
       | /       |       \ |
        -------- bottom -------
"""


class Port:
    def __init__(self, dev: str) -> None:
        self.ser = serial.Serial(dev, BAUD, timeout=0.4)
        try:
            fcntl.flock(self.ser.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.ser.close()
            sys.exit(f"{dev} is busy -- stop the clock/daemon first.")
        time.sleep(0.4)
        self.ser.reset_input_buffer()
        self._frame = bytearray(64)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        threading.Thread(target=self._keepalive, daemon=True).start()

    def _emit(self) -> None:
        out = bytearray(self._frame)
        for i in range(16):                # same dual-bank mirror the driver uses
            m = out[i] | out[i + 48]
            out[i] = out[i + 48] = m
        self.ser.write(b"F" + out.hex().upper().encode() + b"\n")
        self.ser.flush()
        self.ser.readline()

    def _keepalive(self) -> None:
        while not self._stop.wait(1.5):
            with self._lock:
                try:
                    self._emit()
                except Exception:
                    pass

    def show_bit(self, byte: int, bit: int) -> None:
        with self._lock:
            for i in range(64):
                self._frame[i] = 0
            self._frame[byte] = 1 << bit
            self._emit()

    def blank(self) -> None:
        self._stop.set()
        with self._lock:
            for i in range(64):
                self._frame[i] = 0
            try:
                self._emit()
            except Exception:
                pass


def resolve(ans: str) -> str | None:
    ans = ans.strip().lower()
    if ans == "":
        return None
    if ans.isdigit():
        i = int(ans)
        if 0 <= i < len(SEGMENTS):
            return SEGMENTS[i][0]
    for tok, _ in SEGMENTS:
        if ans == tok:
            return tok
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--char", type=int, default=0)
    args = ap.parse_args()

    devs = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not devs:
        sys.exit("no clock found on USB.")
    port = Port(devs[0])
    lo, hi = char_bytes(args.char)

    print(DIAGRAM)
    print("Segment tokens (type the token OR its number):")
    for i, (tok, desc) in enumerate(SEGMENTS):
        print(f"  {i:2d}  {tok:9s} {desc}")
    print()
    print(f"Watching TOP ROW, character {args.char} (0 = leftmost). "
          "One segment lights at a time.")
    print("Type what you see and press Enter. Just Enter = nothing lit.\n")

    mapping: dict[str, list[int]] = {}   # token -> [byte, bit]
    try:
        for byte in (lo, hi):
            for bit in BIT_ORDER:
                port.show_bit(byte, bit)
                while True:
                    ans = input(f"  byte {byte} bit {bit}  -> ")
                    tok = resolve(ans)
                    if tok is None and ans.strip() != "":
                        print("    ? didn't recognize that; try a number 0-15.")
                        continue
                    break
                if tok and tok != "none":
                    mapping[tok] = [byte, bit]
                    print(f"    recorded: {tok}")
    finally:
        port.blank()

    with open(OUT, "w") as f:
        json.dump({"char": args.char, "map": mapping}, f, indent=2)
    print(f"\nSaved {len(mapping)} segments to {OUT}")
    print("Tell Claude it's done.")


if __name__ == "__main__":
    main()
