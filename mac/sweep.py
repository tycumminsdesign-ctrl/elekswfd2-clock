#!/usr/bin/env python3
"""Camera-driven sweep of one top-row character's 16 bits.

For each bit of the chosen character it lights ONLY that segment, waits for the
running imagesnap loop to refresh mapping/cam/latest.jpg, and copies that frame
to mapping/cam/seg_<byte>_<bit>.jpg. Claude then reads the 16 frames and decodes
which physical segment each bit drives -- no human typing, just a webcam aimed at
the character.

    python3 sweep.py            # sweep character 0 (leftmost)
    python3 sweep.py --char 1   # verify another character matches
"""
from __future__ import annotations
import argparse, fcntl, glob, os, shutil, sys, time
import serial

BAUD = 115200
CAM = "mapping/cam/latest.jpg"
OUTDIR = "mapping/cam"
BIT_ORDER = [4, 5, 6, 7, 0, 1, 2, 3]
SETTLE = 2.5   # seconds to let the imagesnap loop capture the new segment

def char_bytes(c: int) -> tuple[int, int]:
    return (48 + 2 * c, 49 + 2 * c)

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--char", type=int, default=0)
    args = ap.parse_args()

    dev = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not dev:
        sys.exit("no clock on USB.")
    ser = serial.Serial(dev[0], BAUD, timeout=0.4)
    try:
        fcntl.flock(ser.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("port busy -- stop mapf.py/clock first (Ctrl-C it).")
    time.sleep(0.4); ser.reset_input_buffer()

    def emit(frame):
        out = bytearray(frame)
        for i in range(16):
            m = out[i] | out[i + 48]
            out[i] = out[i + 48] = m
        ser.write(b"F" + out.hex().upper().encode() + b"\n"); ser.flush(); ser.readline()

    def mtime() -> float:
        try:
            return os.path.getmtime(CAM)
        except OSError:
            return 0.0

    def wait_fresh(frame, new_frames: int = 2, timeout: float = 12.0):
        """Hold `frame` lit and wait until the webcam writes `new_frames` new
        captures, so the copied image is guaranteed to show THIS segment."""
        start_m = mtime()
        seen = 0
        t0 = time.time()
        last = start_m
        while time.time() - t0 < timeout:
            emit(frame)
            time.sleep(0.3)
            m = mtime()
            if m > last:
                seen += 1
                last = m
                if seen >= new_frames:
                    return True
        return False

    lo, hi = char_bytes(args.char)
    shots = []
    for byte in (lo, hi):
        for bit in BIT_ORDER:
            frame = bytearray(64)
            frame[byte] = 1 << bit
            ok = wait_fresh(frame)
            dst = os.path.join(OUTDIR, f"seg_{byte}_{bit}.jpg")
            try:
                shutil.copy(CAM, dst)
                shots.append(dst)
                print(f"  byte {byte} bit {bit} -> {os.path.basename(dst)}"
                      f"{'' if ok else '  (WARN: no fresh frame, may be stale)'}")
            except Exception as exc:
                print(f"  byte {byte} bit {bit} -> capture failed: {exc}")
    emit(bytearray(64))
    ser.close()
    print(f"\ncaptured {len(shots)} frames for char {args.char}. Tell Claude.")

if __name__ == "__main__":
    main()
