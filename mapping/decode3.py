#!/usr/bin/env python3
"""Decode the panel map from paired reference/plane captures.

Improves on decode2.py in one way that turned out to matter more than anything
else: each plane is compared against an all-on reference filmed a second before
it, not against a single reference from the start of the take. Phone auto
exposure drifts noticeably over half a minute -- measured at 25% dimming across
four seconds of identical content -- and that drift alone was enough to make
every LED read as lit on some planes.

Expects captures named r0,p0,r1,p1,... one reference per plane.

    python3 decode3.py --dir captures3 [--debug]
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).parent
NPLANES = 9
MIN_REF = 50
MIN_REGION = 10


def gray(path: Path) -> np.ndarray:
    img = cv2.imread(str(path))
    if img is None:
        raise SystemExit(f"missing capture: {path}")
    return cv2.cvtColor(cv2.GaussianBlur(img, (3, 3), 0),
                        cv2.COLOR_BGR2GRAY).astype(np.float32)


def otsu(vals: np.ndarray, bins: int = 256, hi: float = 1.5) -> float:
    hist, edges = np.histogram(vals, bins=bins, range=(0.0, hi))
    mids = (edges[:-1] + edges[1:]) / 2.0
    w0 = np.cumsum(hist)
    w1 = hist.sum() - w0
    m0 = np.cumsum(hist * mids) / np.maximum(w0, 1)
    m1 = np.cumsum((hist * mids)[::-1])[::-1] / np.maximum(w1, 1)
    return float(mids[int(np.argmax(w0 * w1 * (m0 - m1) ** 2))])


def panel_bbox(ref: np.ndarray, planes: list[np.ndarray], pad: int = 10):
    var = np.stack(planes).std(axis=0)
    m = ((ref > 70) & (var > 10)).astype(np.uint8)
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
    ap.add_argument("--dir", default="captures3")
    ap.add_argument("--crop")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    caps = HERE / args.dir

    refs = [gray(caps / f"r{k}.png") for k in range(NPLANES)]
    planes = [gray(caps / f"p{k}.png") for k in range(NPLANES)]

    if args.crop:
        x0, y0, x1, y1 = (int(v) for v in args.crop.split(","))
    else:
        x0, y0, x1, y1 = panel_bbox(refs[0], planes)
    print(f"panel crop: x {x0}-{x1}, y {y0}-{y1}")
    refs = [r[y0:y1, x0:x1] for r in refs]
    planes = [p[y0:y1, x0:x1] for p in planes]

    # Union of the references marks every pixel the panel can light.
    active = np.maximum.reduce(refs) > MIN_REF
    print(f"{int(active.sum())} lit pixels inside the panel")

    code = np.zeros(refs[0].shape, dtype=np.int32)
    ok = 0
    with np.errstate(divide="ignore", invalid="ignore"):
        for k, (r, p) in enumerate(zip(refs, planes)):
            ratio = np.where(r > 0, p / np.maximum(r, 1e-6), 0.0)
            vals = np.clip(ratio[active], 0, 1.5)
            thr = otsu(vals)
            frac = float((vals > thr).mean())
            good = 0.40 <= frac <= 0.60
            ok += good
            print(f"  plane {k}: threshold {thr:.2f} -> {frac*100:.0f}% lit"
                  f"{'' if good else '   <-- suspect'}")
            code |= ((ratio >= thr) & active).astype(np.int32) << k
    code[~active] = -1
    print(f"{ok}/{NPLANES} planes landed near the expected 50%")

    regions = []
    for value in np.unique(code):
        if value < 0:
            continue
        m = (code == value).astype(np.uint8)
        n, _, stats, cents = cv2.connectedComponentsWithStats(m, connectivity=8)
        for i in range(1, n):
            area = int(stats[i, cv2.CC_STAT_AREA])
            if area < MIN_REGION:
                continue
            regions.append({
                "index": int(value),
                "x": int(round(cents[i][0])), "y": int(round(cents[i][1])),
                "area": area,
                "w": int(stats[i, cv2.CC_STAT_WIDTH]),
                "h": int(stats[i, cv2.CC_STAT_HEIGHT]),
            })
    regions.sort(key=lambda r: (r["y"], r["x"]))
    out = HERE / f"led_map_{args.dir}.json"
    out.write_text(json.dumps(regions, indent=1))

    # Quality check that actually means something: eight LEDs sharing a byte are
    # physically adjacent, so a correct decode puts them in a tight cluster.
    by_byte: dict[int, list] = defaultdict(list)
    best: dict[int, dict] = {}
    for r in regions:
        if r["area"] < 20:
            continue
        if r["index"] not in best or r["area"] > best[r["index"]]["area"]:
            best[r["index"]] = r
    for r in best.values():
        by_byte[r["index"] // 8].append(r)
    compact = 0
    for b, v in by_byte.items():
        if len(v) < 4:
            continue
        spread = max(max(x["x"] for x in v) - min(x["x"] for x in v),
                     max(x["y"] for x in v) - min(x["y"] for x in v))
        if spread < 160:
            compact += 1
    print(f"{len(best)} unique LEDs; {compact}/{len(by_byte)} bytes form a "
          f"compact cluster (higher is better)")
    print(f"wrote {out}")

    if args.debug:
        vis = cv2.imread(str(caps / "r0.png"))[y0:y1, x0:x1].copy()
        for r in best.values():
            cv2.putText(vis, str(r["index"]), (r["x"] - 8, r["y"] + 3),
                        cv2.FONT_HERSHEY_PLAIN, 0.5, (0, 255, 255), 1)
        cv2.imwrite(str(caps / "overlay.png"), vis)
        print(f"wrote {caps/'overlay.png'}")


if __name__ == "__main__":
    main()
