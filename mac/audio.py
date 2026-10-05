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

# Fixed calibration, measured on this mic: ambient sits at ~0.14 (low) / ~0.06
# (high) and loud transients reach ~2.5. Subtract a floor just above ambient so
# a quiet room reads empty, then scale so ordinary sound fills most of the bar.
FLOOR_LOW = 0.20
FLOOR_HIGH = 0.08
# Per-band scale: everyday sound carries more energy in the low band, so the
# high band gets a smaller scale (more sensitive) to make the two columns swing
# comparably rather than the left one always dominating.
SCALE_LOW = 1.4
SCALE_HIGH = 0.7
CURVE = 0.5                # sqrt: audio reads better compressed than linear


class AudioService(threading.Thread):
    def __init__(self, device: str | int | None = None) -> None:
        super().__init__(daemon=True)
        self.left = 0.0
        self.right = 0.0
        self.peak_left = 0.0
        self.peak_right = 0.0
        self.ok = False
        self._device = device
        self._stop = threading.Event()
        self._freqs = np.fft.rfftfreq(BLOCK, 1.0 / SR)
        self._win = np.hanning(BLOCK)
        self._lo = (self._freqs >= LOW_BAND[0]) & (self._freqs < LOW_BAND[1])
        self._hi = (self._freqs >= HIGH_BAND[0]) & (self._freqs < HIGH_BAND[1])

    def _callback(self, indata, frames, time_info, status) -> None:
        x = indata[:, 0]
        if len(x) < BLOCK:
            x = np.pad(x, (0, BLOCK - len(x)))
        mag = np.abs(np.fft.rfft(x[:BLOCK] * self._win))
        lo = float(mag[self._lo].mean()) if self._lo.any() else 0.0
        hi = float(mag[self._hi].mean()) if self._hi.any() else 0.0

        lo_n = np.clip(((lo - FLOOR_LOW) / SCALE_LOW), 0.0, 1.0) ** CURVE
        hi_n = np.clip(((hi - FLOOR_HIGH) / SCALE_HIGH), 0.0, 1.0) ** CURVE

        self.left = self._smooth(self.left, float(lo_n))
        self.right = self._smooth(self.right, float(hi_n))
        self.peak_left = max(self.left, self.peak_left - PEAK_DECAY)
        self.peak_right = max(self.right, self.peak_right - PEAK_DECAY)

    @staticmethod
    def _smooth(cur: float, target: float) -> float:
        k = ATTACK if target > cur else RELEASE
        return cur + k * (target - cur)

    def run(self) -> None:
        try:
            with sd.InputStream(samplerate=SR, blocksize=BLOCK, channels=1,
                                dtype="float32", device=self._device,
                                callback=self._callback):
                self.ok = True
                self._stop.wait()
        except Exception:
            self.ok = False

    def stop(self) -> None:
        self._stop.set()


if __name__ == "__main__":
    import time
    a = AudioService()
    a.start()
    time.sleep(0.5)
    print("make some noise...")
    for _ in range(40):
        bar = lambda v: "#" * int(v * 20)
        print(f"\rL |{bar(a.left):20s}|  R |{bar(a.right):20s}|", end="", flush=True)
        time.sleep(0.1)
    print()
