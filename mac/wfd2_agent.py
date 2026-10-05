#!/usr/bin/env python3
"""Stream this Mac's CPU / GPU / RAM to an EleksWFD 2 clock.

The clock's stock firmware has an undocumented hardware-monitor command
(EM_API 101) that the shipped web UI never exposes -- it was meant for a
Windows-only PC client that was never released for the WFD2. We drive it
directly over the clock's local WebSocket, so the stock firmware keeps doing
what it is good at (rendering the bars, clock, weather) and no reflashing is
involved.

The device drops back to an idle animation if it stops hearing from us, so the
loop has to keep sending; SEND_HZ is a compromise between a lively display and
not hammering a small ESP32.

Usage:
    wfd2_agent.py                 # find the clock, stream until interrupted
    wfd2_agent.py --host 1.2.3.4  # skip discovery
    wfd2_agent.py --once 90 50 25 # send one fixed reading (for testing)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
import time

import websockets

from wfd2_stats import read_cpu, read_stats

DEFAULT_HOSTNAME = "em_wfd2_2ddb5110.local"
FALLBACK_HOST = "192.168.1.170"
SEND_HZ = 1.0
RECONNECT_WAIT = 3.0


def discover(explicit: str | None) -> str:
    """Resolve the clock's address, preferring mDNS so DHCP churn can't break us."""
    if explicit:
        return explicit
    for name in (DEFAULT_HOSTNAME, DEFAULT_HOSTNAME.replace(".local", ".localdomain")):
        try:
            return socket.gethostbyname(name)
        except OSError:
            continue
    return FALLBACK_HOST


def frame(cpu: float, gpu: float, ram: float) -> str:
    """Build a hardware-monitor packet.

    The firmware clamps each value to a floor of 5 before drawing, so an idle
    machine still shows a sliver rather than an empty bar. GPU reads as -1 when
    IOKit will not tell us; mirroring CPU there beats showing a dead meter.
    """
    if gpu < 0:
        gpu = cpu
    clamp = lambda v: max(0, min(100, int(round(v))))
    return json.dumps({
        "EM_API": 101,
        "EM_SYS_DATA1": clamp(cpu),
        "EM_SYS_DATA2": clamp(gpu),
        "EM_SYS_DATA3": clamp(ram),
    })


async def stream(host: str, verbose: bool) -> None:
    uri = f"ws://{host}/ws"
    read_cpu()  # prime the tick delta so the first sample is meaningful
    while True:
        try:
            async with websockets.connect(uri, open_timeout=10) as ws:
                print(f"connected to {uri}", file=sys.stderr)
                while True:
                    s = read_stats()
                    await ws.send(frame(s.cpu, s.gpu, s.ram))
                    if verbose:
                        gpu = f"{s.gpu:5.1f}" if s.gpu >= 0 else "  n/a"
                        print(f"\rCPU {s.cpu:5.1f}%  GPU {gpu}%  RAM {s.ram:5.1f}%",
                              end="", flush=True)
                    await asyncio.sleep(1.0 / SEND_HZ)
        except (OSError, websockets.WebSocketException) as exc:
            print(f"\nlink down ({exc.__class__.__name__}); retrying in "
                  f"{RECONNECT_WAIT:.0f}s", file=sys.stderr)
            await asyncio.sleep(RECONNECT_WAIT)


async def once(host: str, cpu: float, gpu: float, ram: float) -> None:
    async with websockets.connect(f"ws://{host}/ws", open_timeout=10) as ws:
        await ws.send(frame(cpu, gpu, ram))
        print(f"sent cpu={cpu} gpu={gpu} ram={ram} to {host}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", help="clock IP or hostname")
    p.add_argument("--quiet", action="store_true", help="no per-sample output")
    p.add_argument("--once", nargs=3, type=float, metavar=("CPU", "GPU", "RAM"),
                   help="send a single fixed reading and exit")
    args = p.parse_args()

    host = discover(args.host)
    try:
        if args.once:
            asyncio.run(once(host, *args.once))
        else:
            asyncio.run(stream(host, verbose=not args.quiet))
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
