#!/usr/bin/env python3
"""Recover the panel map by per-pixel signature clustering.

decode.py's first approach -- find LED blobs in the all-on frame, then read
each blob's bits -- fails on this panel: the camera blurs neighbouring segments
together, so one blob can span several LEDs and the decoded address is garbage.

This inverts the problem. Every pixel gets its own 9-bit signature (is it lit in
plane k?), and pixels are then grouped by identical signature. Two segments that
bleed into each other still carry different codes, so they separate cleanly, and
a single LED's pixels merge no matter how ragged its shape. Blur only hurts at
the boundary between regions, which shows up as small stray islands we drop.

    python3 decode2.py [--debug]
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).parent
CAPS = HERE / "captures"
NPLANES = 9
LIT_RATIO = 0.42       # per-pixel "is this lit" cut, relative to the reference
MIN_REF = 45           # ignore pixels the reference frame never lights
MIN_REGION = 12        # px; smaller islands are blur artefacts at LED borders


def load(name: str) -> np.ndarray:
    img = cv2.imread(str(CAPS / f"{name}.png"))
    if img is None:
        raise SystemExit(f"missing capture: {name}.png")
    return cv2.GaussianBlur(img, (3, 3), 0)


def gray(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)


def _otsu(vals: np.ndarray, bins: int = 256, hi: float = 1.5) -> float:
    """Otsu's threshold over a 1-D sample (the ratio image inside the panel)."""
    hist, edges = np.histogram(vals, bins=bins, range=(0.0, hi))
    mids = (edges[:-1] + edges[1:]) / 2.0
    w0 = np.cumsum(hist)
    w1 = hist.sum() - w0
    m0 = np.cumsum(hist * mids) / np.maximum(w0, 1)
    m1 = np.cumsum((hist * mids)[::-1])[::-1] / np.maximum(w1, 1)
    between = w0 * w1 * (m0 - m1) ** 2
    return float(mids[int(np.argmax(between))])


def panel_bbox(ref: np.ndarray, planes: list[np.ndarray], pad: int = 10):
    """Bounding box of the display itself.

    Keyed on pixels that are both bright in the reference AND vary between
    planes -- the room, the reflections on the table, and anything else static
    fail the second test, so the box lands on the panel and nothing else.
    """
    var = np.stack(planes).std(axis=0)
    m = ((ref > 70) & (var > 12)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = (int(stats[i, k]) for k in
                  (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP,
                   cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
    H, W = ref.shape
    return (max(0, x - pad), max(0, y - pad),
            min(W, x + w + pad), min(H, y + h + pad))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--crop", help="override panel crop as x0,y0,x1,y1")
    ap.add_argument("--dir", default="captures", help="capture directory")
    args = ap.parse_args()

    global CAPS
    CAPS = HERE / args.dir
    ref_full = gray(load("ref"))
    planes_full = [gray(load(f"p{k}")) for k in range(NPLANES)]

    if args.crop:
        x0, y0, x1, y1 = (int(v) for v in args.crop.split(","))
    else:
        x0, y0, x1, y1 = panel_bbox(ref_full, planes_full)
    print(f"panel crop: x {x0}-{x1}, y {y0}-{y1}")
    ref = ref_full[y0:y1, x0:x1]
    planes = [p[y0:y1, x0:x1] for p in planes_full]

    active = ref > MIN_REF
    print(f"reference frame: {int(active.sum())} lit pixels inside the panel")

    # Per-pixel 9-bit code. Dividing by the reference keeps the decision fair
    # across colours -- a dim red segment and a bright cyan one both get judged
    # against their own maximum rather than a shared absolute level.
    #
    # The cut for "lit" is chosen per plane by Otsu rather than fixed, because
    # exposure and bleed differ frame to frame. Binary encoding guarantees each
    # plane lights about half the LEDs, so a threshold landing near 50% is the
    # signal that it found the real gap between lit and dark.
    # Undo the camera's auto-exposure before comparing frames.
    #
    # A phone brightens the image when half the panel goes dark, so a plane's
    # raw pixels are not on the same scale as the reference's. Left uncorrected
    # the ratios inflate and every LED reads as lit -- which showed up as bit 7
    # being set on all 512 addresses. Both frames contain plenty of
    # fully-lit LEDs, so matching their top percentile puts them back on a
    # common scale regardless of what the exposure did.
    ref_hi = float(np.percentile(ref[active], 99))
    code = np.zeros(ref.shape, dtype=np.int32)
    with np.errstate(divide="ignore", invalid="ignore"):
        for k, p in enumerate(planes):
            scale = ref_hi / max(float(np.percentile(p[active], 99)), 1e-6)
            adj = p * scale
            ratio = np.where(ref > 0, adj / np.maximum(ref, 1e-6), 0.0)
            vals = np.clip(ratio[active], 0, 1.5)
            thr = _otsu(vals)
            frac = float((vals > thr).mean())
            flag = "" if 0.40 <= frac <= 0.60 else "   <-- off 50%, suspect"
            print(f"  plane {k}: exposure x{scale:.2f}, threshold {thr:.2f} "
                  f"-> {frac*100:.0f}% lit{flag}")
            code |= ((ratio >= thr) & active).astype(np.int32) << k
    code[~active] = -1

    # Group pixels that share a signature AND touch each other.
    regions = []
    for value in np.unique(code):
        if value < 0:
            continue
        m = (code == value).astype(np.uint8)
        n, labels, stats, cents = cv2.connectedComponentsWithStats(m, connectivity=8)
        for i in range(1, n):
            area = int(stats[i, cv2.CC_STAT_AREA])
            if area < MIN_REGION:
                continue
            regions.append({
                "index": int(value),
                "x": int(round(cents[i][0])),
                "y": int(round(cents[i][1])),
                "area": area,
                "w": int(stats[i, cv2.CC_STAT_WIDTH]),
                "h": int(stats[i, cv2.CC_STAT_HEIGHT]),
            })

    regions.sort(key=lambda r: (r["y"], r["x"]))
    by_index: dict[int, list] = defaultdict(list)
    for r in regions:
        by_index[r["index"]].append(r)

    print(f"found {len(regions)} regions across {len(by_index)} distinct indices")
    print(f"index range {min(by_index)}..{max(by_index)} (valid 0..511)")

    # A healthy decode has most indices appearing as one compact region.
    multi = {i: v for i, v in by_index.items() if len(v) > 1}
    print(f"{len(multi)} indices appear in more than one place "
          f"(expected for segments of the same word, suspicious otherwise)")

    out = HERE / f"led_map_regions_{args.dir}.json"
    out.write_text(json.dumps(regions, indent=1))
    print(f"wrote {out}")

    if args.debug:
        vis = cv2.imread(str(CAPS / "ref.png"))[y0:y1, x0:x1].copy()
        for r in regions:
            if r["area"] < 25:
                continue
            cv2.putText(vis, str(r["index"]), (r["x"] - 8, r["y"] + 3),
                        cv2.FONT_HERSHEY_PLAIN, 0.55, (0, 255, 255), 1)
            cv2.circle(vis, (r["x"], r["y"]), 2, (0, 0, 255), -1)
        cv2.imwrite(str(CAPS / "overlay2.png"), vis)
        print("wrote overlay2.png")


if __name__ == "__main__":
    main()
