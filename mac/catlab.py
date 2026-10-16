#!/usr/bin/env python3
"""Design lab for the dot-matrix cat: render an ASCII bitmap to the panel.

The inner dot matrix is a raster-wired 7-wide grid (rows 1-4 full, row 0 has
only cols 5-6); byte 17 lights the surrounding border frame. Pass a bitmap as
lines of 'X'/'.' and it lights the matching cells, holds it, and captures the
webcam so we can eyeball it. Edit the BITMAP below and re-run to iterate.

    python3 catlab.py
"""
from __future__ import annotations
import fcntl, glob, os, shutil, sys, time
import serial

CAM = "mapping/cam/latest.jpg"
OUT = "mapping/cam"

# validated (row,col) -> (byte,bit); rows 0..4, cols 0..6
DM_GRID = {
    (0,5):(26,4),(0,6):(26,5),
    (1,0):(26,6),(1,1):(26,7),(1,2):(26,0),(1,3):(26,1),(1,4):(26,2),(1,5):(26,3),(1,6):(27,4),
    (2,0):(27,5),(2,1):(27,6),(2,2):(27,7),(2,3):(27,0),(2,4):(27,1),(2,5):(27,2),(2,6):(27,3),
    (3,0):(28,4),(3,1):(28,5),(3,2):(28,6),(3,3):(28,7),(3,4):(28,0),(3,5):(28,1),(3,6):(28,2),
    (4,0):(28,3),(4,1):(29,4),(4,2):(29,5),(4,3):(29,6),(4,4):(29,7),(4,5):(29,0),(4,6):(29,1),
}
BORDER_BYTE = 17

def render(bitmap_lines, border=True, name="catlab"):
    dev = sorted(glob.glob("/dev/cu.usbmodem*"))[0]
    ser = serial.Serial(dev, 115200, timeout=0.4)
    fcntl.flock(ser.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    time.sleep(0.4); ser.reset_input_buffer()
    fr = bytearray(64)
    if border:
        fr[BORDER_BYTE] = 0xFF
    for r, line in enumerate(bitmap_lines):
        for c, ch in enumerate(line):
            if ch not in ".":
                if (r, c) in DM_GRID:
                    b, bit = DM_GRID[(r, c)]
                    fr[b] |= 1 << bit
    def emit(f):
        out = bytearray(f)
        for i in range(16):
            m = out[i] | out[i+48]; out[i] = out[i+48] = m
        ser.write(b"F"+out.hex().upper().encode()+b"\n"); ser.flush(); ser.readline()
    ser.write(b"B6\n"); ser.flush(); ser.readline()
    last = os.path.getmtime(CAM); seen = 0; t0 = time.time()
    while time.time()-t0 < 12 and seen < 3:
        emit(fr); time.sleep(0.3); m = os.path.getmtime(CAM)
        if m > last: seen += 1; last = m
    shutil.copy(CAM, f"{OUT}/{name}.jpg")
    # hold a while so it's visible live too
    t1 = time.time()
    while time.time()-t1 < 8:
        emit(fr); time.sleep(0.5)
    ser.write(b"B15\n"); ser.flush(); ser.readline()
    emit(bytearray(64)); ser.close()
    print(f"{name} captured")

# --- edit this: 7 columns wide, rows 0..4 (row0 only cols5,6 exist) ---
BITMAP = [
    ".....X.",   # row0 (only cols 5,6 usable)
    "X.....X",   # row1  ears
    "XX...XX",   # row2  ear base + head
    ".XXXXX.",   # row3  face
    "..XXX..",   # row4  chin
]

if __name__ == "__main__":
    render(BITMAP, name=sys.argv[1] if len(sys.argv) > 1 else "catlab")
