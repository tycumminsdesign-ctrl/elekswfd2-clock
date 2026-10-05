#!/usr/bin/env python3
"""Camera sweep of the dot-matrix bytes to map each cell's (row,col).

Lights one bit at a time across the candidate dot-matrix bytes and copies the
webcam frame for each, so Claude can read the lit dot's position and build the
grid. Byte 17 is included because earlier notes flag it as the matrix border.

    python3 dmsweep.py
"""
from __future__ import annotations
import fcntl, glob, os, shutil, time, serial

CAM = "mapping/cam/latest.jpg"
OUT = "mapping/cam"
BIT_ORDER = [4, 5, 6, 7, 0, 1, 2, 3]
BYTES = [17, 26, 27, 28, 29]

def main() -> None:
    dev = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not dev:
        raise SystemExit("no clock on USB")
    ser = serial.Serial(dev[0], 115200, timeout=0.4)
    fcntl.flock(ser.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    time.sleep(0.4); ser.reset_input_buffer()

    def emit(fr):
        out = bytearray(fr)
        for i in range(16):
            m = out[i] | out[i + 48]; out[i] = out[i + 48] = m
        ser.write(b"F" + out.hex().upper().encode() + b"\n"); ser.flush(); ser.readline()

    def wait_fresh(fr, n=2, to=12):
        last = os.path.getmtime(CAM); seen = 0; t0 = time.time()
        while time.time() - t0 < to and seen < n:
            emit(fr); time.sleep(0.3); m = os.path.getmtime(CAM)
            if m > last: seen += 1; last = m
        return seen >= n

    for b in BYTES:
        for bit in BIT_ORDER:
            fr = bytearray(64); fr[b] = 1 << bit
            ok = wait_fresh(fr)
            shutil.copy(CAM, f"{OUT}/dm_{b}_{bit}.jpg")
            print(f"  byte {b} bit {bit}{'' if ok else '  (stale?)'}")
    emit(bytearray(64)); ser.close()
    print("done")

if __name__ == "__main__":
    main()
