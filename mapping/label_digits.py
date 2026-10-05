#!/usr/bin/env python3
"""Work out which bit lights which segment of each digit.

The byte-level layout makes this tractable: a 7-segment digit needs 7 segments
plus a point, which is exactly one byte, and the decoded positions show the
lower digit row occupying bytes 16-23 -- one byte per digit. So rather than
guessing where one character ends and the next begins from x gaps (which merged
neighbouring digits when tried), we take each byte as a character and name its
8 LEDs from where they sit inside that character's own bounding box.

    python3 label_digits.py [--json digits.json]
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent

# Byte ranges established from the decoded layout.
TIME_ROW_BYTES = range(16, 24)     # lower row: 8 x 7-segment digits
INFO_ROW_BYTES = range(48, 60)     # upper row: 6 x 14-segment chars (2 bytes each)


def load_leds() -> dict[int, dict]:
    regs = json.loads((HERE / "led_map_regions.json").read_text())
    best: dict[int, dict] = {}
    for r in regs:
        if r["area"] < 25:
            continue
        if r["index"] not in best or r["area"] > best[r["index"]]["area"]:
            best[r["index"]] = r
    return best


def segment_name(led: dict, box) -> str:
    """Name a stroke from its place in the character box (standard 7-seg letters)."""
    x0, y0, x1, y1 = box
    w = max(x1 - x0, 1.0)
    h = max(y1 - y0, 1.0)
    rx = (led["x"] - x0) / w
    ry = (led["y"] - y0) / h
    if led["w"] >= led["h"] * 1.2:          # clearly horizontal
        return "A" if ry < 0.33 else ("D" if ry > 0.67 else "G")
    if led["h"] >= led["w"] * 1.2:          # clearly vertical
        if ry < 0.5:
            return "F" if rx < 0.5 else "B"
        return "E" if rx < 0.5 else "C"
    # Square-ish: usually the decimal point or a colon dot, off to one side.
    return "P" if rx > 0.75 else "?"


def describe(byte_index: int, leds: dict[int, dict]) -> dict | None:
    members = [leds[i] for i in range(byte_index * 8, byte_index * 8 + 8) if i in leds]
    if len(members) < 4:
        return None
    x0 = min(m["x"] - m["w"] / 2 for m in members)
    x1 = max(m["x"] + m["w"] / 2 for m in members)
    y0 = min(m["y"] - m["h"] / 2 for m in members)
    y1 = max(m["y"] + m["h"] / 2 for m in members)
    named: dict[str, list[int]] = defaultdict(list)
    for m in members:
        named[segment_name(m, (x0, y0, x1, y1))].append(m["index"])
    return {
        "byte": byte_index,
        "cx": int((x0 + x1) / 2),
        "cy": int((y0 + y1) / 2),
        "w": int(x1 - x0),
        "h": int(y1 - y0),
        "found": len(members),
        "segments": {k: sorted(v) for k, v in sorted(named.items())},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    args = ap.parse_args()

    leds = load_leds()
    out = {"time_row": [], "info_row": []}

    print("lower row (one byte per 7-segment digit):")
    for b in TIME_ROW_BYTES:
        d = describe(b, leds)
        if not d:
            print(f"  byte {b}: too few LEDs decoded")
            continue
        out["time_row"].append(d)
        segs = " ".join(f"{k}:{','.join(map(str, v))}" for k, v in d["segments"].items())
        print(f"  byte {b} @({d['cx']:4d},{d['cy']:3d}) {d['w']:3d}x{d['h']:3d} "
              f"[{d['found']}/8]  {segs}")

    print("\nupper row (two bytes per 14-segment character):")
    for b in INFO_ROW_BYTES:
        d = describe(b, leds)
        if not d:
            print(f"  byte {b}: too few LEDs decoded")
            continue
        out["info_row"].append(d)
        print(f"  byte {b} @({d['cx']:4d},{d['cy']:3d}) {d['w']:3d}x{d['h']:3d} "
              f"[{d['found']}/8]")

    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=1))
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
