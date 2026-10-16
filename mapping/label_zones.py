#!/usr/bin/env python3
"""Turn decoded LED positions into named panel zones.

decode2.py answers "which bit is this LED?" but says nothing about what any LED
means. This groups them into the things we actually want to draw -- digit
characters and their segments, the day-of-week labels, the VU columns, the dot
matrix -- purely from geometry, so no extra photographs are needed.

Segment naming follows the usual 7-segment convention:

      AAA
     F   B
     F   B
      GGG
     E   C
     E   C
      DDD

    python3 label_zones.py            # print the zone map
    python3 label_zones.py --json out.json
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
MIN_AREA = 25


def load_leds() -> list[dict]:
    """Largest region per index -- satellites are blur at LED borders."""
    regs = json.loads((HERE / "led_map_regions.json").read_text())
    best: dict[int, dict] = {}
    for r in regs:
        if r["area"] < MIN_AREA:
            continue
        if r["index"] not in best or r["area"] > best[r["index"]]["area"]:
            best[r["index"]] = r
    return list(best.values())


def cluster(values: list[float], gap: float) -> list[list[int]]:
    """Split sorted positions wherever a gap exceeds `gap`. Returns index groups."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    groups: list[list[int]] = []
    cur = [order[0]]
    for prev, idx in zip(order, order[1:]):
        if values[idx] - values[prev] > gap:
            groups.append(cur)
            cur = []
        cur.append(idx)
    groups.append(cur)
    return groups


def classify_segment(led: dict, box: tuple[int, int, int, int]) -> str:
    """Name a segment from where it sits inside its character's bounding box."""
    x0, y0, x1, y1 = box
    w = max(x1 - x0, 1)
    h = max(y1 - y0, 1)
    rx = (led["x"] - x0) / w          # 0 = left, 1 = right
    ry = (led["y"] - y0) / h          # 0 = top,  1 = bottom
    horizontal = led["w"] >= led["h"]

    if horizontal:
        if ry < 0.28:
            return "A"
        if ry > 0.72:
            return "D"
        return "G"
    # vertical strokes: which side, upper or lower half
    side = "B" if rx > 0.5 else "F"
    if ry > 0.5:
        side = "C" if rx > 0.5 else "E"
    return side


def label_digit_row(leds: list[dict], name: str) -> dict:
    """Split a row of LEDs into characters, then name each segment."""
    xs = [l["x"] for l in leds]
    # Characters are separated by wider gaps than the segments within one.
    span = max(xs) - min(xs)
    gap = max(18.0, span / (len(leds) ** 0.5) / 2.2)
    groups = cluster(xs, gap)

    chars = []
    for g in groups:
        members = [leds[i] for i in g]
        if len(members) < 3:            # too few strokes to be a character
            continue
        x0 = min(m["x"] - m["w"] / 2 for m in members)
        x1 = max(m["x"] + m["w"] / 2 for m in members)
        y0 = min(m["y"] - m["h"] / 2 for m in members)
        y1 = max(m["y"] + m["h"] / 2 for m in members)
        box = (x0, y0, x1, y1)
        segs: dict[str, list[int]] = defaultdict(list)
        for m in members:
            segs[classify_segment(m, box)].append(m["index"])
        chars.append({
            "x": int(sum(m["x"] for m in members) / len(members)),
            "segments": {k: sorted(v) for k, v in sorted(segs.items())},
            "count": len(members),
        })
    chars.sort(key=lambda c: c["x"])
    return {"name": name, "chars": chars}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="write the map here")
    args = ap.parse_args()

    leds = load_leds()
    print(f"{len(leds)} mapped LEDs")

    digits = [l for l in leds if l["x"] > 800]
    top = [l for l in digits if 90 < l["y"] < 225]
    bottom = [l for l in digits if l["y"] >= 225]
    print(f"digit area: {len(top)} LEDs in the upper row, {len(bottom)} in the lower")

    zones = {}
    for row, tag in ((bottom, "time_row"), (top, "info_row")):
        if len(row) < 6:
            continue
        z = label_digit_row(row, tag)
        zones[tag] = z
        print(f"\n{tag}: {len(z['chars'])} characters")
        for i, c in enumerate(z["chars"]):
            shown = {k: v for k, v in c["segments"].items()}
            print(f"  char {i} @x{c['x']:4d} ({c['count']} segs): " +
                  " ".join(f"{k}={','.join(map(str, v))}" for k, v in shown.items()))

    if args.json:
        Path(args.json).write_text(json.dumps(zones, indent=1))
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
