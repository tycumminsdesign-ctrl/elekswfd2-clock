#!/usr/bin/env python3
"""Tetris-style animation for the 7x7 dot-matrix usage gauge.

The gauge is a stack that fills from the bottom-left. Instead of snapping to a
new level, each new dot DROPS from the top of its column and lands on the stack
like a Tetris piece; when usage falls (a 5-hour or weekly window resets) dots
LIFT off the top of the stack and rise out through the top -- the same motion in
reverse. Dots move one row per render frame (~10 fps) and a new dot is released
every RELEASE_FRAMES, so a big jump cascades instead of dumping all at once.

The render loop does, every frame:
    anim.update(fraction)        # new level; None = unknown, keep what's shown
    anim.step()                  # advance in-flight dots one row
    frame.grid_set(anim.cells()) # light settled + in-flight dots

    python mac/grid_anim.py      # print an ASCII simulation
"""

from __future__ import annotations

from panel import GRID_FILL_ORDER, GRID_ROWS

ROWS = len(GRID_ROWS)               # 7
COLS = len(GRID_ROWS[0])            # 7
N = len(GRID_FILL_ORDER)            # 49
RELEASE_FRAMES = 2                  # frames between releasing successive dots
FALL, RISE = 1, -1


def _home(idx: int) -> tuple[int, int]:
    """(row, col) where fill-order dot `idx` rests: bottom row first, left to right."""
    return ROWS - 1 - idx // COLS, idx % COLS


class GridAnimator:
    def __init__(self) -> None:
        self.target = 0
        self.shown: set[int] = set()              # settled dots, by fill index
        self.flying: dict[int, list[int]] = {}    # idx -> [current row, direction]
        self._cooldown = 0

    def update(self, fraction: float | None) -> None:
        """Set the level to animate toward. None (usage unknown) keeps the current one."""
        if fraction is None:
            return
        self.target = int(round(N * max(0.0, min(1.0, fraction))))

    @property
    def settled(self) -> bool:
        return not self.flying and self.shown == set(range(self.target))

    def _release(self) -> bool:
        # Drain first: the topmost dot above the target level lifts off...
        extra = [i for i in self.shown if i >= self.target]
        if extra:
            i = max(extra)
            self.shown.discard(i)
            self.flying[i] = [_home(i)[0], RISE]
            return True
        # ...then fill: the lowest missing dot drops in from the top of its column.
        missing = [i for i in range(self.target) if i not in self.shown and i not in self.flying]
        if missing:
            self.flying[min(missing)] = [0, FALL]
            return True
        return False

    def step(self) -> None:
        """Advance one frame: move every in-flight dot a row, then maybe release one."""
        for i, state in list(self.flying.items()):
            row = state[0] + state[1]
            if state[1] == FALL:
                if row >= _home(i)[0]:        # landed on the stack
                    del self.flying[i]
                    self.shown.add(i)
                else:
                    state[0] = row
            elif row < 0:                     # risen out the top
                del self.flying[i]
            else:
                state[0] = row
        if self._cooldown > 0:
            self._cooldown -= 1
        elif self._release():
            self._cooldown = RELEASE_FRAMES - 1

    def cells(self) -> list[tuple[int, int]]:
        """(byte, bit) of every dot to light this frame."""
        out = [GRID_FILL_ORDER[i] for i in self.shown]
        for i, (row, _) in self.flying.items():
            if 0 <= row < ROWS:
                out.append(GRID_ROWS[row][_home(i)[1]])
        return out

    def ascii(self) -> str:
        lit = set(self.cells())
        return "\n".join("".join("#" if GRID_ROWS[r][c] in lit else "." for c in range(COLS))
                         for r in range(ROWS))


if __name__ == "__main__":
    a = GridAnimator()
    for label, frac, frames in (("fill to 25%", 0.25, 40), ("top up to 45%", 0.45, 40),
                                ("window resets to 5%", 0.05, 60)):
        a.update(frac)
        print(f"=== {label} ===")
        snaps = []
        for n in range(frames):
            a.step()
            if n % 4 == 0 or a.settled:
                snaps.append((n, a.ascii()))
            if a.settled:
                break
        for n, pic in snaps:
            print(f"frame {n:2d}:\n{pic}\n")
