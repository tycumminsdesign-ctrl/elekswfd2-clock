#!/usr/bin/env python3
"""Light specific bytes on the panel and hold them, for camera-based probing.

Usage: python3 hold.py SECONDS  B=V  B=V ...
  where B=V sets frame byte B to value V (both decimal or 0x..).
Keeps refreshing so the firmware's 5 s blank-out never fires, then blanks.
"""
from __future__ import annotations
import fcntl, glob, sys, time
import serial

BAUD = 115200

def num(s: str) -> int:
    return int(s, 16) if s.lower().startswith("0x") else int(s)

def main() -> None:
    secs = float(sys.argv[1])
    frame = bytearray(64)
    for tok in sys.argv[2:]:
        b, v = tok.split("=")
        frame[num(b)] = num(v)
    dev = sorted(glob.glob("/dev/cu.usbmodem*"))[0]
    ser = serial.Serial(dev, BAUD, timeout=0.4)
    fcntl.flock(ser.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    time.sleep(0.4); ser.reset_input_buffer()

    def emit(fr):
        out = bytearray(fr)
        for i in range(16):
            m = out[i] | out[i + 48]
            out[i] = out[i + 48] = m
        ser.write(b"F" + out.hex().upper().encode() + b"\n"); ser.flush(); ser.readline()

    t0 = time.time()
    while time.time() - t0 < secs:
        emit(frame); time.sleep(1.0)
    emit(bytearray(64))
    ser.close()

if __name__ == "__main__":
    main()
