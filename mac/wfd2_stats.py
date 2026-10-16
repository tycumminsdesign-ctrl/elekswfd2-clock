#!/usr/bin/env python3
"""Fast CPU / GPU / RAM sampling for the panel, without sudo.

Speed matters here: the display is refreshed ~10x a second, so a sampler that
blocks is a sampler that stutters the bars. CPU and RAM come from psutil (Mach
host statistics, effectively free). GPU is the awkward one -- powermetrics would
need root, so we read IOKit's per-accelerator "Device Utilization %" via ioreg,
which any user can do but costs a few hundred milliseconds. That call therefore
runs on a background thread at its own slow cadence and the display reuses the
last value in between.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass

import psutil

GPU_POLL_SECONDS = 3.0      # ioreg spawns a process; polling it hard shows up AS cpu load
CPU_WINDOW_SECONDS = 1.0    # long enough that brief idle gaps don't read as 0%


@dataclass
class Stats:
    cpu: float   # 0-100
    gpu: float   # 0-100, -1 when the machine will not report it
    ram: float   # 0-100


def _ioreg_gpu() -> float:
    try:
        out = subprocess.run(
            ["ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"],
            capture_output=True, text=True, timeout=4,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return -1.0
    best = -1.0
    for key in ("Device Utilization %", "GPU Activity(%)", "Renderer Utilization %"):
        for m in re.finditer(re.escape(key) + r'"?\s*=\s*(\d+)', out):
            best = max(best, float(m.group(1)))
    return min(best, 100.0)


class _Sampler(threading.Thread):
    """Samples all three metrics on their own schedule, off the render loop.

    Both readings need this, for opposite reasons. GPU (ioreg) is far too SLOW
    to call per frame. CPU is too FAST: psutil measures usage since the previous
    call, so polling it at frame rate hands it a ~100 ms window that often shows
    no tick movement at all and returns a bogus 0%, which made the bar flash
    empty between frames. Here it gets a proper 250 ms measurement window and
    the render loop simply reads whatever the latest value is.
    """

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.cpu = 0.0
        self.gpu = -1.0
        self.ram = float(psutil.virtual_memory().percent)
        self._stop = threading.Event()
        self._last_gpu = 0.0
        self.ready = threading.Event()

    def run(self) -> None:
        psutil.cpu_percent(interval=None)   # prime
        while not self._stop.is_set():
            # Blocks for the window, which is exactly what makes it accurate.
            self.cpu = float(psutil.cpu_percent(interval=CPU_WINDOW_SECONDS))
            self.ram = float(psutil.virtual_memory().percent)
            self.ready.set()
            now = time.time()
            if now - self._last_gpu >= GPU_POLL_SECONDS:
                self.gpu = _ioreg_gpu()
                self._last_gpu = now

    def stop(self) -> None:
        self._stop.set()


_sampler: _Sampler | None = None


def _get() -> _Sampler:
    global _sampler
    if _sampler is None:
        _sampler = _Sampler()
        _sampler.start()
        # Block until a real measurement exists, so callers never see the
        # placeholder 0% that made the meters start out empty.
        _sampler.ready.wait(timeout=CPU_WINDOW_SECONDS * 2 + 0.5)
    return _sampler


def read_cpu() -> float:
    return _get().cpu


def read_ram() -> float:
    return _get().ram


def read_gpu() -> float:
    return _get().gpu


def read_stats() -> Stats:
    s = _get()
    return Stats(cpu=s.cpu, gpu=s.gpu, ram=s.ram)


if __name__ == "__main__":
    read_cpu()
    time.sleep(0.3)
    for _ in range(5):
        t = time.time()
        s = read_stats()
        ms = (time.time() - t) * 1000
        gpu = f"{s.gpu:5.1f}%" if s.gpu >= 0 else "  n/a"
        print(f"CPU {s.cpu:5.1f}%  GPU {gpu}  RAM {s.ram:5.1f}%   ({ms:.1f} ms)")
        time.sleep(0.3)
