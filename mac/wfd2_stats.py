#!/usr/bin/env python3
"""Fast CPU / GPU / RAM sampling for the panel, without sudo.

Speed matters here: the display is refreshed ~10x a second, so a sampler that
blocks is a sampler that stutters the bars. CPU comes from psutil (Mach host
statistics, effectively free); RAM is macOS memory pressure from sysctl (also
free -- see _ram_pressure). GPU is the awkward one -- powermetrics would
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

# RAM meter = memory PRESSURE, not "percent used". macOS deliberately fills spare
# RAM with cache and compressed pages, so percent-used sits ~70-80% on a healthy
# Mac and the bar read like a permanent alarm. Pressure is what Activity
# Monitor's graph shows: kern.memorystatus_level is the free percentage, and
# kern.memorystatus_vm_pressure_level is the kernel's own verdict (1 normal,
# 2 warning, 4 critical). Normal pressure stays in the low part of the bar; the
# warning/critical states jump it up, so a high bar means "something's wrong".
RAM_NORMAL_SCALE = 0.6      # normal: 0-100% pressure -> 0-60% of the bar
RAM_WARN_FLOOR = 70.0       # warning: at least this
RAM_CRITICAL = 100.0
CPU_WINDOW_SECONDS = 1.0    # long enough that brief idle gaps don't read as 0%


def _sysctl_int(name: str) -> int | None:
    try:
        import ctypes
        libc = ctypes.CDLL(None)
        val = ctypes.c_int(0)
        size = ctypes.c_size_t(ctypes.sizeof(val))
        if libc.sysctlbyname(name.encode(), ctypes.byref(val), ctypes.byref(size), None, 0) == 0:
            return val.value
    except Exception:
        pass
    return None


def _ram_pressure() -> float:
    """Memory pressure for the RAM bar, 0-100 (see RAM_* above)."""
    free = _sysctl_int("kern.memorystatus_level")
    state = _sysctl_int("kern.memorystatus_vm_pressure_level")
    if free is None:
        return float(psutil.virtual_memory().percent)   # not macOS: plain usage
    pressure = max(0.0, min(100.0, 100.0 - free))
    if state is not None and state >= 4:
        return RAM_CRITICAL
    if state is not None and state >= 2:
        return min(95.0, RAM_WARN_FLOOR + 0.3 * pressure)
    return pressure * RAM_NORMAL_SCALE


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
        self.ram = _ram_pressure()
        self._stop = threading.Event()
        self._last_gpu = 0.0
        self.ready = threading.Event()

    def run(self) -> None:
        psutil.cpu_percent(interval=None)   # prime
        while not self._stop.is_set():
            # Blocks for the window, which is exactly what makes it accurate.
            self.cpu = float(psutil.cpu_percent(interval=CPU_WINDOW_SECONDS))
            self.ram = _ram_pressure()
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
