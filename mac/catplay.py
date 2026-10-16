#!/usr/bin/env python3
"""Play a looping dot-matrix animation live so we can eyeball it on the panel.

The dot matrix is a 7x7 grid, wired irregularly across double-banked bytes:
rows come from bytes 15 (top), 13, 26, 27, 28, 29 (bottom); byte 17 is the
border frame. A few cells are dead (29.2/29.3) or unmapped, marked below.
Frames are 7-line ASCII bitmaps (7 cols). Edit FRAMES / FPS and re-run.
"""
from __future__ import annotations
import fcntl, glob, time
import serial

# (row,col) -> (byte,bit). 7 rows x 7 cols. Missing keys = dead/unmapped cell.
DM_GRID = {
    (0,0):(15,5),(0,1):(15,6),(0,2):(15,7),(0,3):(15,0),(0,4):(15,1),(0,5):(15,2),(0,6):(15,3),
                 (1,1):(13,4),(1,2):(13,5),(1,3):(13,6),(1,4):(13,7),(1,5):(13,0),(1,6):(13,1),
    (2,0):(13,2),(2,1):(13,3),                                        (2,5):(26,4),(2,6):(26,5),
    (3,0):(26,6),(3,1):(26,7),(3,2):(26,0),(3,3):(26,1),(3,4):(26,2),(3,5):(26,3),(3,6):(27,4),
    (4,0):(27,5),(4,1):(27,6),(4,2):(27,7),(4,3):(27,0),(4,4):(27,1),(4,5):(27,2),(4,6):(27,3),
    (5,0):(28,4),(5,1):(28,5),(5,2):(28,6),(5,3):(28,7),(5,4):(28,0),(5,5):(28,1),(5,6):(28,2),
    (6,0):(28,3),(6,1):(29,4),(6,2):(29,5),(6,3):(29,6),(6,4):(29,7),(6,5):(29,0),(6,6):(29,1),
}
BORDER = 17

def bitmap_to_frame(lines, border=True):
    fr = bytearray(64)
    if border:
        fr[BORDER] = 0xFF
    for r, line in enumerate(lines):
        for c, ch in enumerate(line):
            if ch not in "." and (r, c) in DM_GRID:
                b, bit = DM_GRID[(r, c)]
                fr[b] |= 1 << bit
    return fr

# --- cat: ears flap up/down, framed by the border. cols 2-4 of row2 are dead,
#     so the head starts at row3. ---
EARS_UP = [
    ".X...X.",   # 0  ear tips
    ".XX.XX.",   # 1  ears
    "X.....X",   # 2  ear sides (cols 2-4 dead here)
    "XXXXXXX",   # 3  head top
    "X.XXX.X",   # 4  eyes = dark holes at col1 & col5
    "XXX.XXX",   # 5  nose = dark hole at col3
    ".XXXXX.",   # 6  chin
]
EARS_DOWN = [
    ".......",   # 0
    ".X...X.",   # 1  ears lowered
    "XX...XX",   # 2  ears folded to sides
    "XXXXXXX",   # 3
    "X.XXX.X",   # 4  eyes
    "XXX.XXX",   # 5  nose
    ".XXXXX.",   # 6
]
BLINK = [
    ".X...X.",
    ".XX.XX.",
    "X.....X",
    "XXXXXXX",
    "XXXXXXX",   # eyes closed (both filled)
    "XXX.XXX",   # nose
    ".XXXXX.",
]
FRAMES = [EARS_UP, EARS_UP, EARS_DOWN, EARS_UP, EARS_UP, BLINK]
FPS = 2.5
SECONDS = 120
BRIGHTNESS = "B3"   # lower = less bloom so the dark eyes/nose show

def main():
    dev = sorted(glob.glob("/dev/cu.usbmodem*"))[0]
    ser = serial.Serial(dev, 115200, timeout=0.4)
    fcntl.flock(ser.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    time.sleep(0.4); ser.reset_input_buffer()
    frames = [bitmap_to_frame(f) for f in FRAMES]
    def emit(f):
        out = bytearray(f)
        for i in range(16):
            m = out[i] | out[i+48]; out[i] = out[i+48] = m
        ser.write(b"F"+out.hex().upper().encode()+b"\n"); ser.flush(); ser.readline()
    ser.write((BRIGHTNESS+"\n").encode()); ser.flush(); ser.readline()
    dt = 1.0/FPS; t0 = time.time(); i = 0
    while time.time()-t0 < SECONDS:
        emit(frames[i % len(frames)]); i += 1; time.sleep(dt)
    ser.write(b"B15\n"); ser.flush(); ser.readline()
    emit(bytearray(64)); ser.close()

if __name__ == "__main__":
    main()
