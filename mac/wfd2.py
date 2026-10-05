#!/usr/bin/env python3
"""Drive the EleksWFD 2 over USB from this Mac.

Talks to the custom bridge firmware, which does nothing but copy the frames we
send onto the panel. No network is involved at any point: the clock has no WiFi
credentials, contacts nothing, and goes dark if this program stops -- whichever
Mac holds the cable owns the display.

    wfd2.py                # live clock, day and CPU/GPU/RAM meters
    wfd2.py --demo         # sweep the meters, to check the mapping
    wfd2.py --test         # light each meter in turn
"""

from __future__ import annotations

import argparse
import datetime
import fcntl
import os
import glob
import sys
import time

import serial

from panel import METERS, Frame
from weather import WeatherService
from audio import AudioService
from wfd2_stats import read_cpu, read_stats

BAUD = 115200
FPS = 10.0


def find_port(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not ports:
        sys.exit("no clock found: is it plugged into this Mac?")
    return ports[0]


class Clock:
    """Exclusive connection to the panel.

    Two writers on one serial port do not queue politely -- their frames
    interleave and the panel appears to flash randomly, with whole zones
    dropping out. Rather than rely on remembering to stop the last run, take an
    advisory lock on the port and refuse to start if something else holds it.
    """

    def __init__(self, port: str) -> None:
        self.ser = serial.Serial(port, BAUD, timeout=0.4)
        try:
            fcntl.flock(self.ser.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.ser.close()
            sys.exit(f"{port} is already in use -- stop the other wfd2 first "
                     "(Ctrl-C in that terminal)")
        time.sleep(0.4)          # board resets when the port opens
        self.ser.reset_input_buffer()

    def send(self, frame: Frame) -> bool:
        self.ser.write(frame.line())
        self.ser.flush()
        reply = self.ser.readline().decode("utf-8", "replace").strip()
        return reply == "OK"

    def brightness(self, level: int) -> None:
        self.ser.write(f"B{max(0, min(15, level))}\n".encode())
        self.ser.flush()
        self.ser.readline()

    def blank(self) -> None:
        self.ser.write(b"X\n")
        self.ser.flush()
        self.ser.readline()

    def close(self) -> None:
        try:
            self.blank()
        finally:
            self.ser.close()


def demo(clock: Clock) -> None:
    """Sweep every meter 0->100->0 so the fill order can be eyeballed."""
    f = Frame()
    for pct in list(range(0, 101, 4)) + list(range(100, -1, -4)):
        f.clear()
        for name in METERS:
            f.meter(name, pct)
        clock.send(f)
        time.sleep(0.05)


def test(clock: Clock) -> None:
    """Light each meter alone, then all three, pausing between."""
    f = Frame()
    for name in METERS:
        f.clear()
        f.meter(name, 100)
        clock.send(f)
        print(f"  {name} bar at 100%")
        time.sleep(2.0)
    f.clear()
    for name, pct in (("cpu", 100), ("gpu", 60), ("ram", 30)):
        f.meter(name, pct)
    clock.send(f)
    print("  cpu 100% / gpu 60% / ram 30%")
    time.sleep(3.0)


def stream(clock: Clock, quiet: bool, tau: float = 0.4) -> None:
    """Stream live stats, smoothed.

    Raw utilisation is spiky, and a bar quantised to ~24 segments turns small
    wobbles into visible flicker. An exponential moving average with time
    constant `tau` seconds gives the meters some weight: they still respond to
    real load within a fraction of a second, but stop rattling between segments
    when nothing is really changing. tau=0 disables it.
    """
    read_cpu()   # start the sampler thread
    f = Frame()
    dt = 1.0 / FPS
    alpha = 1.0 if tau <= 0 else dt / (tau + dt)
    smooth: dict[str, float] = {}

    def ema(key: str, value: float) -> float:
        prev = smooth.get(key)
        cur = value if prev is None else prev + alpha * (value - prev)
        smooth[key] = cur
        return cur

    wx = WeatherService()
    wx.start()
    audio = AudioService()
    audio.start()

    while True:
        s = read_stats()
        raw = {"cpu": s.cpu, "gpu": s.cpu if s.gpu < 0 else s.gpu, "ram": s.ram}
        now = datetime.datetime.now()
        w = wx.latest

        f.clear()
        for name, value in raw.items():
            f.meter(name, ema(name, value))
        f.weekday(now.weekday())        # today only -- dithering the rest flickers
        f.weather(w.condition)
        f.brand()                       # decorative ELEKSMAKER logo, always lit
        f.vu("left", audio.left, peak=audio.peak_left)
        f.vu("right", audio.right, peak=audio.peak_right)

        # Upper 14-segment row: the date as YY MM DD, like the stock firmware.
        # Lower 7-segment row: the time in 12-hour form. Both steady, no rotation.
        f.top_number(now.strftime("%y%m%d"))
        f.text(now.strftime("%I%M%S"), points={1, 3})           # 12-hour HH:MM:SS

        clock.send(f)
        if not quiet:
            temp = f"{w.temp_f:.0f}" if w.temp_f is not None else "--"
            print(f"\r{now:%H:%M:%S}  CPU {smooth['cpu']:4.0f}%  GPU {smooth['gpu']:4.0f}%  "
                  f"RAM {smooth['ram']:4.0f}%  wx {temp} {w.condition or '--':5s}",
                  end="", flush=True)
        time.sleep(dt)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--smooth", type=float, default=0.4, metavar="SECONDS",
                    help="meter smoothing time constant; 0 for raw (default 0.4)")
    args = ap.parse_args()

    port = find_port(args.port)
    print(f"clock on {port}")
    clock = Clock(port)
    try:
        if args.demo:
            demo(clock)
        elif args.test:
            test(clock)
        else:
            stream(clock, args.quiet, args.smooth)
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        clock.close()


if __name__ == "__main__":
    main()
