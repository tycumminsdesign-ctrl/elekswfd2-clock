#!/usr/bin/env python3
"""Turn the Mac's mic into the panel's two VU columns.

The stock clock had an on-board mic that "peaked at any sound or did nothing" --
the whole reason for driving the spectrum from the Mac instead. This splits the
mic signal into a low band (-> left column) and a high band (-> right column) so
the two columns move independently and the display actually reads as sound.

Runs the audio stream on its own thread; the render loop just reads .left /
.right (bar height 0..1) and .peak_left / .peak_right (a peak-hold dot that
decays). An automatic gain follows the recent loudness so it fills the bars in a
quiet room and does not clip in a loud one, without the user setting levels.
"""

from __future__ import annotations

import threading

import numpy as np
import sounddevice as sd

SR = 16000
BLOCK = 1024
LOW_BAND = (60, 500)       # Hz -> left column
HIGH_BAND = (500, 5000)    # Hz -> right column
ATTACK = 0.6               # how fast bars rise (0..1 per block)
RELEASE = 0.15             # how fast they fall
PEAK_DECAY = 0.03          # peak-hold dot fall per block

# Self-calibrating: each band tracks its own noise floor (drops fast when the room
# goes quiet, creeps up over ~a minute under sustained sound), and the bar shows
# how far above that floor the sound is, in dB. That makes any mic, gain or input
# volume behave the same. Measured on a desk Yeti: typing sits within ~12 dB of the
# floor (reads empty), speech and music run 15-35 dB above it.
FLOOR_FALL = 0.10          # per block, toward a quieter reading
FLOOR_RISE = 0.001         # per block, toward a louder reading
# The floor may not creep above these (dB, low band / high band): under long
# speech or music it otherwise climbs to just under the voice and the bars die.
FLOOR_MAX_LO = -35.0
FLOOR_MAX_HI = -45.0
DB_EMPTY = 18.0            # dB above the floor where the bar starts
DB_FULL = 55.0             # dB above the floor where the bar is full
DEVICE_CHECK_S = 2.0       # how often to look for a changed system mic
GAIN = 1.0                 # overall bar height; 1.0 = full scale
LEVEL_CURVE = 2.0          # >1 keeps typing/speech low but lets yelling reach the top


def _default_input_id() -> int | None:
    """The macOS default input device's CoreAudio id (None if unavailable).

    PortAudio snapshots the device list at startup, so it cannot see the user
    switching mics; ask CoreAudio directly and reopen when this changes.
    """
    try:
        import ctypes
        ca = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")

        class Address(ctypes.Structure):
            _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32),
                        ("element", ctypes.c_uint32)]

        fourcc = lambda s: int.from_bytes(s.encode(), "big")
        addr = Address(fourcc("dIn "), fourcc("glob"), 0)
        dev = ctypes.c_uint32(0)
        size = ctypes.c_uint32(ctypes.sizeof(dev))
        err = ca.AudioObjectGetPropertyData(1, ctypes.byref(addr), 0, None,
                                            ctypes.byref(size), ctypes.byref(dev))
        return dev.value if err == 0 else None
    except Exception:
        return None


class AudioService(threading.Thread):
    """VU levels from a mic. device=None follows the macOS system input."""

    def __init__(self, device: str | int | None = None) -> None:
        super().__init__(daemon=True)
        self.left = 0.0
        self.right = 0.0
        self.peak_left = 0.0
        self.peak_right = 0.0
        self.ok = False
        self.device_name = ""
        self._device = device
        self._stop = threading.Event()
        self._freqs = np.fft.rfftfreq(BLOCK, 1.0 / SR)
        self._win = np.hanning(BLOCK)
        self._lo = (self._freqs >= LOW_BAND[0]) & (self._freqs < LOW_BAND[1])
        self._hi = (self._freqs >= HIGH_BAND[0]) & (self._freqs < HIGH_BAND[1])
        self._floor_lo: float | None = None   # dB
        self._floor_hi: float | None = None

    @staticmethod
    def _track(floor: float | None, db: float) -> float:
        if floor is None:
            return db
        k = FLOOR_FALL if db < floor else FLOOR_RISE
        return floor + k * (db - floor)

    @staticmethod
    def _level(db: float, floor: float) -> float:
        x = (db - floor - DB_EMPTY) / (DB_FULL - DB_EMPTY)
        return GAIN * float(np.clip(x, 0.0, 1.0)) ** LEVEL_CURVE

    def _callback(self, indata, frames, time_info, status) -> None:
        x = indata[:, 0]
        if len(x) < BLOCK:
            x = np.pad(x, (0, BLOCK - len(x)))
        mag = np.abs(np.fft.rfft(x[:BLOCK] * self._win))
        lo = float(mag[self._lo].mean()) if self._lo.any() else 0.0
        hi = float(mag[self._hi].mean()) if self._hi.any() else 0.0
        if lo <= 0.0 and hi <= 0.0:
            # Exact digital silence: the mic is muted (or blocked), not a quiet
            # room. Let the bars fall to zero, but keep it out of the floor.
            target_lo = target_hi = 0.0
        else:
            lo_db = 20 * np.log10(lo + 1e-9)
            hi_db = 20 * np.log10(hi + 1e-9)
            self._floor_lo = min(self._track(self._floor_lo, lo_db), FLOOR_MAX_LO)
            self._floor_hi = min(self._track(self._floor_hi, hi_db), FLOOR_MAX_HI)
            target_lo = self._level(lo_db, self._floor_lo)
            target_hi = self._level(hi_db, self._floor_hi)

        self.left = self._smooth(self.left, target_lo)
        self.right = self._smooth(self.right, target_hi)
        self.peak_left = max(self.left, self.peak_left - PEAK_DECAY)
        self.peak_right = max(self.right, self.peak_right - PEAK_DECAY)

    @staticmethod
    def _smooth(cur: float, target: float) -> float:
        k = ATTACK if target > cur else RELEASE
        return cur + k * (target - cur)

    def _open(self, device):
        stream = sd.InputStream(samplerate=SR, blocksize=BLOCK, channels=1,
                                dtype="float32", device=device,
                                callback=self._callback)
        stream.start()
        self.device_name = sd.query_devices(stream.device, kind="input")["name"]
        self._floor_lo = self._floor_hi = None   # new mic, relearn its floor
        return stream

    def run(self) -> None:
        stream = None
        current = object()
        while not self._stop.is_set():
            want = _default_input_id()
            if stream is None or (self._device is None and want != current):
                if stream is not None:
                    stream.close()
                    stream = None
                try:
                    sd._terminate()        # refresh PortAudio's device list
                    sd._initialize()
                    devices = [self._device, None] if self._device is not None else [None]
                    for device in devices:
                        try:
                            stream = self._open(device)
                            break
                        except Exception:
                            continue
                except Exception:
                    stream = None
                current = want
                self.ok = stream is not None
                if not self.ok:
                    self.left = self.right = 0.0
            self._stop.wait(DEVICE_CHECK_S)
        if stream is not None:
            stream.close()

    def stop(self) -> None:
        self._stop.set()


if __name__ == "__main__":
    import time
    a = AudioService()
    a.start()
    time.sleep(0.5)
    print(f"listening on {a.device_name or '(no mic)'} -- make some noise...")
    for _ in range(40):
        bar = lambda v: "#" * int(v * 20)
        print(f"\rL |{bar(a.left):20s}|  R |{bar(a.right):20s}|", end="", flush=True)
        time.sleep(0.1)
    print()
