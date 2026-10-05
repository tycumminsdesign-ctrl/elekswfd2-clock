#!/usr/bin/env python3
"""Recover the EleksWFD 2 panel map from the bit-plane capture.

The clock's LED controller exposes 64 bytes of RAM = 512 individually
addressable LEDs, but nothing says which bit lights which physical segment.
Rather than photograph 512 states, the firmware displays 9 "bit plane" frames:
plane k lights every LED whose index has bit k set. Each LED's 9 yes/no answers
spell out its address in binary.

This script locates every LED in the all-on reference frame, reads its state in
each plane, and writes out (x, y) -> bit index.

    python3 decode.py            # decode and report
    python3 decode.py --debug    # also write an annotated overlay image
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).parent
CAPS = HERE / "captures"
NPLANES = 9

# An LED blob smaller than this is camera noise; larger is usually two adjacent
# segments bleeding together, which we split rather than accept.
MIN_AREA = 6
MAX_AREA = 4000

# A plane sample counts as "lit" above this fraction of the LED's reference
# brightness. Colours differ hugely in apparent brightness (deep red reads much
# dimmer than cyan), so the threshold has to be relative per LED, never global.
LIT_RATIO = 0.40


def load(name: str) -> np.ndarray:
    p = CAPS / f"{name}.png"
    img = cv2.imread(str(p))
    if img is None:
        sys.exit(f"missing capture: {p}")
    return img


def find_leds(ref: np.ndarray) -> tuple[np.ndarray, list[tuple[int, int]]]:
    """Locate LED centres in the all-on frame."""
    gray = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    # The panel is bright text on a dark bezel, so Otsu separates it cleanly.
    _, mask = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))

    n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    pts: list[tuple[int, int]] = []
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        if area < MIN_AREA or area > MAX_AREA:
            continue
        pts.append((int(round(cents[i][0])), int(round(cents[i][1]))))
    return mask, pts


def sample(img: np.ndarray, x: int, y: int, r: int = 2) -> float:
    """Mean brightness in a small window, so one hot pixel can't decide a bit."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = g.shape
    y0, y1 = max(0, y - r), min(h, y + r + 1)
    x0, x1 = max(0, x - r), min(w, x + r + 1)
    return float(g[y0:y1, x0:x1].mean())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    ref = load("ref")
    planes = [load(f"p{k}") for k in range(NPLANES)]

    mask, pts = find_leds(ref)
    print(f"detected {len(pts)} LED blobs in the reference frame")

    rows = []
    for (x, y) in pts:
        base = sample(ref, x, y)
        if base < 40:                      # too dim to trust as an LED
            continue
        idx = 0
        conf = []
        for k, pimg in enumerate(planes):
            v = sample(pimg, x, y)
            ratio = v / base if base else 0.0
            if ratio >= LIT_RATIO:
                idx |= (1 << k)
            conf.append(ratio)
        # Confidence = distance of the weakest decision from the threshold.
        margin = min(abs(c - LIT_RATIO) for c in conf)
        rows.append({"x": x, "y": y, "index": idx, "margin": round(margin, 3)})

    rows.sort(key=lambda r: (r["y"], r["x"]))
    out = CAPS.parent / "led_map_raw.json"
    out.write_text(json.dumps(rows, indent=1))

    idxs = [r["index"] for r in rows]
    uniq = len(set(idxs))
    dupes = len(idxs) - uniq
    weak = sum(1 for r in rows if r["margin"] < 0.10)
    print(f"decoded {len(rows)} LEDs -> {uniq} unique indices ({dupes} duplicated)")
    print(f"index range {min(idxs) if idxs else 0}..{max(idxs) if idxs else 0} "
          f"(valid 0..511)")
    print(f"{weak} LEDs decided by a margin under 0.10 (suspect)")
    print(f"wrote {out}")

    if args.debug:
        vis = ref.copy()
        for r in rows:
            cv2.circle(vis, (r["x"], r["y"]), 3, (0, 255, 0), 1)
            cv2.putText(vis, str(r["index"]), (r["x"] + 3, r["y"] - 3),
                        cv2.FONT_HERSHEY_PLAIN, 0.5, (0, 255, 255), 1)
        cv2.imwrite(str(CAPS / "overlay.png"), vis)
        cv2.imwrite(str(CAPS / "mask.png"), mask)
        print("wrote overlay.png and mask.png")


if __name__ == "__main__":
    main()
