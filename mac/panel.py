#!/usr/bin/env python3
"""Panel geometry and framebuffer for the EleksWFD 2.

The clock's LED controller holds 64 bytes of RAM and lights one LED per bit,
512 in all. Nothing in the hardware says which bit is which segment, so the map
below was recovered by photographing nine binary "bit plane" frames and
decoding each LED's address from whether it appeared in each one
(see ../mapping/decode2.py).

Two findings shape everything here:

  * Within a byte the segments run left to right in bit order 4,5,6,7,0,1,2,3 --
    not 0..7. Get this wrong and every bar fills in a scrambled order.
  * Each meter's segments occupy a contiguous block of indices, so a bar is a
    plain ordered list of LED numbers and "fill to 60%" is a slice.
"""

from __future__ import annotations

FRAME_BYTES = 64
NUM_LEDS = FRAME_BYTES * 8


def byte_order(byte_index: int) -> list[int]:
    """LED indices for one byte, in physical left-to-right order."""
    base = byte_index * 8
    return [base + b for b in (4, 5, 6, 7, 0, 1, 2, 3)]


def _run(*byte_indices: int) -> list[int]:
    out: list[int] = []
    for b in byte_indices:
        out.extend(byte_order(b))
    return out


# Meters, ordered from the left end of each bar. The first 16 segments of each
# are the green run; the yellow/red tail continues in the later bytes.
# Verified on hardware: writing bytes 34-35 lights exactly the CPU bar's green
# section and nothing else.
# Byte 40 splits 6/2 between two bars: its last two LEDs (322, 323) sit in the
# GPU bar's yellow zone, not the CPU bar -- found when the CPU fill's final two
# steps visibly lit the wrong row.
CPU_BAR = _run(34, 35) + [324, 325, 326, 327, 320]
GPU_BAR = _run(36, 37) + [322, 323, 332, 333, 334]
RAM_BAR = _run(38, 39) + [328, 329, 330, 331, 340]

METERS = {"cpu": CPU_BAR, "gpu": GPU_BAR, "ram": RAM_BAR}

# The green "%" glyph at the end of each meter row is its own LED, always lit
# while the meter is active. All three confirmed on hardware (lighting 321/335/341
# alone showed three stacked green dots at the three rows). NOTE 321 and 335 were
# initially miscounted as the last bar segment of CPU/GPU -- they are the % labels,
# so the bars end one segment earlier. (Earlier wrong guesses 251/267 were a CLOUD
# LED and half of RAINS, which lit weather words by mistake.)
METER_PCT = {"cpu": 321, "gpu": 335, "ram": 341}

# --- lower digit row -------------------------------------------------------
#
# Six 7-segment digits, one byte each. The byte order is NOT sequential across
# the row -- the two leading digits live at 60 and 62 while the rest run 16..22,
# so extrapolating from the middle of the row lands nowhere near them.
# Established on hardware by lighting candidate bytes with distinct digits and
# reading back which position each landed in. (Worth doing with UNIQUE digits:
# a first pass reused 1 and 2 across candidates and produced a false positive.)
TIME_DIGITS = [60, 62, 16, 18, 20, 22]

# Segment bit assignment inside a digit byte, recovered from the decoded
# geometry of byte 18: horizontal bars at the top/middle/bottom, verticals down
# each side, and a small square off to the right for the point.
SEG_A = 0x10   # bit 4, top bar
SEG_B = 0x20   # bit 5, upper right
SEG_C = 0x40   # bit 6, lower right
SEG_D = 0x80   # bit 7, bottom bar
SEG_E = 0x01   # bit 0, lower left
SEG_F = 0x02   # bit 1, upper left
SEG_G = 0x04   # bit 2, middle bar
SEG_P = 0x08   # bit 3, decimal point / colon

DIGIT_FONT = {
    0: SEG_A | SEG_B | SEG_C | SEG_D | SEG_E | SEG_F,
    1: SEG_B | SEG_C,
    2: SEG_A | SEG_B | SEG_D | SEG_E | SEG_G,
    3: SEG_A | SEG_B | SEG_C | SEG_D | SEG_G,
    4: SEG_B | SEG_C | SEG_F | SEG_G,
    5: SEG_A | SEG_C | SEG_D | SEG_F | SEG_G,
    6: SEG_A | SEG_C | SEG_D | SEG_E | SEG_F | SEG_G,
    7: SEG_A | SEG_B | SEG_C,
    8: SEG_A | SEG_B | SEG_C | SEG_D | SEG_E | SEG_F | SEG_G,
    9: SEG_A | SEG_B | SEG_C | SEG_D | SEG_F | SEG_G,
}

# A handful of non-digit glyphs the 7-segment digits can render, for dates and
# temperatures (e.g. "89'F"). Space blanks the position.
CHAR_FONT = {str(d): p for d, p in DIGIT_FONT.items()}
CHAR_FONT.update({
    " ": 0,
    "-": SEG_G,
    "F": SEG_A | SEG_E | SEG_F | SEG_G,
    "C": SEG_A | SEG_D | SEG_E | SEG_F,
    "'": SEG_A | SEG_B | SEG_F | SEG_G,   # degree ring, top-right
})

# --- upper 14-segment row (date line) --------------------------------------
#
# Six 14-segment characters above the time. Each character is TWO bytes: the
# even byte holds the seven "digit" segments (A,B,C,D,E,F,G) in the SAME bit
# layout as the lower 7-segment row -- A=4,B=5,C=6,D=7,E=0,F=1,G=2 -- while the
# odd byte holds only the extra diagonal/centre strokes a letter needs and a
# digit never does. So a date is drawn purely on the even bytes with the digit
# font, leaving the odd bytes clear. Mapped on hardware by lighting each of
# character 0's sixteen bits alone and reading the lit segment's position from
# the webcam (see ../mapping/cam, decoded by centroid).
# Note byte 48 bit 3 is shared with SUN's second LED (see DAY_MASKS), so writing
# a top digit preserves that bit rather than clobbering the Sunday label.
TOP_DIGITS = [48, 50, 52, 54, 56, 58]   # even byte per character, left to right
TOP_EXTRA = [49, 51, 53, 55, 57, 59]    # odd byte: diagonals, unused for digits


# --- day-of-week labels ----------------------------------------------------
#
# Each printed day word is backlit by TWO LEDs, not one, so a day is a pair of
# bits and lighting a single bit shows half a word. The pairs follow the same
# 4,5,6,7,0,1,2,3 physical order as everything else on this panel, four days to
# byte 24 and the rest in byte 25. Indexed to match datetime.weekday(), where
# Monday is 0.
# SUN is the odd one out: its two LEDs are not even in the same byte -- one sits
# in byte 25 and the other is tucked into byte 48, which otherwise belongs to the
# upper character row. Nothing about the layout is logical; it follows whatever
# was convenient to route on the board, so every entry here was confirmed by
# lighting candidates on the hardware.
DAY_MASKS = [
    [(24, 0x30)],              # MON: bits 4,5
    [(24, 0xC0)],              # TUE: bits 6,7
    [(24, 0x03)],              # WED: bits 0,1
    [(24, 0x0C)],              # THU: bits 2,3
    [(25, 0x30)],              # FRI: bits 4,5
    [(25, 0xC0)],              # SAT: bits 6,7
    [(25, 0x01), (48, 0x08)],  # SUN: split across two bytes
]


# --- spectrum / VU columns -------------------------------------------------
#
# Two vertical columns beside the dot matrix, eleven LEDs each, listed from the
# BOTTOM of the panel upward so a level is simply a slice. They span three
# bytes: 23 holds the bottom four of each column, 21 the middle four, 19 the top
# three. (Byte 17, which sits among them in memory, drives the dot-matrix border
# instead -- confirmed on hardware.)
# The even/odd split is NOT consistent across the three bytes, which is the one
# thing here that resists a tidy rule: in bytes 19 and 23 the left column takes
# the even bits, but in byte 21 it takes the odd ones. Confirmed directly by
# lighting byte 21's even bits alone (they land in the RIGHT column) -- assuming
# the rule held cost a round of flip-flopping.
#
# Each column's 12th LED hides in a DIGIT byte: bits 163 and 179 are the
# "decimal point" position of the two seconds digits, which have no physical
# point -- the spare bits were routed to the VU columns instead. (A 512-bit
# binary search initially "proved" no bit drove them, because the search's own
# round-label was written onto the digit row each frame, clobbering exactly
# these two bits. Labels must never share bytes with candidates.)
LEFT_VU = [186, 184, 190, 188, 171, 169, 175, 173, 163, 154, 152, 158]
RIGHT_VU = [187, 185, 191, 189, 179, 170, 168, 174, 172, 155, 153, 159]
VU_COLUMNS = {"left": LEFT_VU, "right": RIGHT_VU}
VU_BYTES = (19, 21, 23)


# --- weather condition labels ----------------------------------------------
#
# The printed words SUNNY / CLOUD / RAINS light beside the meter rows, confirmed
# on hardware bit-by-bit. SUNNY and CLOUD share byte 31 (bits 2 and 3); RAINS is
# byte 33 (bits 2,3). Byte 32's bits 2,3 drive the ELEKSMAKER logo, NOT a weather
# word -- an earlier by-symmetry guess put CLOUD there and lit the logo by
# mistake.
WEATHER_MASKS = {
    "sunny": (31, 0x04),
    "cloud": (31, 0x08),
    "rains": (33, 0x0C),
}

# The printed ELEKSMAKER brand mark below the meters. Its orange backlight spans
# bytes 30-32. Byte 31 is shared: bits 4,5,6,7,0 are logo letters (mask 0xF1),
# while bits 1,2 drive a red indicator bar and bit 3 a weather label -- all
# excluded so only the word lights and weather stays free. Bytes 30 and 32 are
# entirely logo. Bytes 26-29 next door are the blue dot matrix, not the logo.
# Purely decorative, lit steadily so the panel doesn't look bare.
BRAND_MASKS = [(30, 0xFF), (31, 0xF1), (32, 0xFF)]


class Frame:
    """A 64-byte panel framebuffer addressed by LED index."""

    __slots__ = ("buf",)

    def __init__(self) -> None:
        self.buf = bytearray(FRAME_BYTES)

    def clear(self) -> None:
        for i in range(FRAME_BYTES):
            self.buf[i] = 0

    def set(self, led: int, on: bool = True) -> None:
        if not 0 <= led < NUM_LEDS:
            return
        byte, bit = divmod(led, 8)
        if on:
            self.buf[byte] |= 1 << bit
        else:
            self.buf[byte] &= ~(1 << bit) & 0xFF

    def set_many(self, leds, on: bool = True) -> None:
        for led in leds:
            self.set(led, on)

    def meter(self, name: str, percent: float) -> None:
        """Fill a meter to `percent`, rounding to whole segments.

        A non-zero reading always lights at least one segment: a machine that is
        busy but only slightly should not look identical to one that is off.
        """
        segs = METERS[name]
        pct = max(0.0, min(100.0, percent))
        n = int(round(len(segs) * pct / 100.0))
        if pct > 0:
            n = max(1, n)
        self.set_many(segs[:n])
        pct_led = METER_PCT.get(name)
        if pct_led is not None:
            self.set(pct_led)

    def digit(self, position: int, value: int | None, point: bool = False) -> None:
        """Draw one digit of the lower row. `value` None blanks the position.

        On the two seconds digits (bytes 20 and 22) the point bit is not a
        point at all -- it drives the top LED of a VU column -- so those bytes
        keep their existing bit 3 and ignore the `point` flag.
        """
        if not 0 <= position < len(TIME_DIGITS):
            return
        byte = TIME_DIGITS[position]
        bits = 0 if value is None else DIGIT_FONT.get(value % 10, 0)
        if byte in (20, 22):
            bits |= self.buf[byte] & SEG_P     # preserve the VU LED
        elif point:
            bits |= SEG_P
        self.buf[byte] = bits

    def number(self, text: str, points: set[int] | None = None) -> None:
        """Write up to six characters across the lower row; space blanks a slot."""
        points = points or set()
        for pos in range(len(TIME_DIGITS)):
            ch = text[pos] if pos < len(text) else " "
            value = int(ch) if ch.isdigit() else None
            self.digit(pos, value, point=pos in points)

    def text(self, s: str, points: set[int] | None = None) -> None:
        """Write up to six glyphs (digits, space, - F C ') across the lower row."""
        points = points or set()
        byte20_22 = (20, 22)
        for pos in range(len(TIME_DIGITS)):
            byte = TIME_DIGITS[pos]
            ch = s[pos] if pos < len(s) else " "
            bits = CHAR_FONT.get(ch, 0)
            if byte in byte20_22:              # preserve the VU LED on these bytes
                bits |= self.buf[byte] & SEG_P
            elif pos in points:
                bits |= SEG_P
            self.buf[byte] = bits

    def top_text(self, s: str, points: set[int] | None = None) -> None:
        """Write up to six glyphs across the UPPER 14-segment row.

        Only the digit segments are driven (even byte per character), so digits,
        space and the few CHAR_FONT glyphs render; the odd 'diagonal' bytes are
        cleared. Byte 48's bit 3 is left untouched because SUN borrows it.
        """
        points = points or set()
        for pos in range(len(TOP_DIGITS)):
            lo = TOP_DIGITS[pos]
            hi = TOP_EXTRA[pos]
            ch = s[pos] if pos < len(s) else " "
            bits = CHAR_FONT.get(ch, 0)
            if pos in points:
                bits |= SEG_P
            keep = self.buf[lo] & SEG_P if lo == 48 else 0   # preserve SUN
            self.buf[lo] = bits | keep
            self.buf[hi] = 0

    def top_number(self, text: str, points: set[int] | None = None) -> None:
        """Convenience for a numeric upper row, e.g. a YYMMDD date."""
        self.top_text(text, points)

    def weekday(self, index: int | None, others: bool = False) -> None:
        """Light day labels. 0 = Monday, matching datetime.weekday().

        The current day (`index`) is always lit. With `others=True` the other six
        days are lit too; the driver toggles that flag across frames so the other
        days sit at a lower duty cycle and read dimmer than today (the controller
        has no per-LED brightness, only on/off, so brightness is faked in time).

        Clears only the day bits rather than whole bytes: SUN borrows a bit from
        byte 48, which otherwise drives a character in the upper row.
        """
        for spans in DAY_MASKS:
            for byte, mask in spans:
                self.buf[byte] &= ~mask & 0xFF
        lit = set(range(len(DAY_MASKS))) if others else set()
        if index is not None and 0 <= index < len(DAY_MASKS):
            lit.add(index)
        for i in lit:
            for byte, mask in DAY_MASKS[i]:
                self.buf[byte] |= mask

    def vu(self, column: str, level: float, peak: float | None = None) -> None:
        """Fill one spectrum column from the bottom to `level` (0..1).

        An optional `peak` lights a single LED at that height, the classic
        VU-meter peak-hold dot -- it makes brief transients visible that a bar
        alone would blink past too quickly to see.
        """
        leds = VU_COLUMNS[column]
        n = int(round(len(leds) * max(0.0, min(1.0, level))))
        self.set_many(leds[:n])
        if peak is not None:
            i = min(len(leds) - 1, int(round(len(leds) * max(0.0, min(1.0, peak)))))
            if i >= 0:
                self.set(leds[i])

    def clear_vu(self) -> None:
        for b in VU_BYTES:
            self.buf[b] = 0

    def brand(self, on: bool = True) -> None:
        """Light the decorative ELEKSMAKER logo backlight (spans bytes 30-32)."""
        for byte, mask in BRAND_MASKS:
            if on:
                self.buf[byte] |= mask
            else:
                self.buf[byte] &= ~mask & 0xFF

    def weather(self, condition: str | None) -> None:
        """Light one weather-condition word (sunny/cloud/rains), clearing the rest."""
        for byte, mask in WEATHER_MASKS.values():
            self.buf[byte] &= ~mask & 0xFF
        span = WEATHER_MASKS.get((condition or "").lower())
        if span:
            self.buf[span[0]] |= span[1]

    def hex(self) -> str:
        # The controller double-banks part of its RAM: bytes 0-15 and 48-63
        # drive the SAME LEDs on alternating scan phases, so content written to
        # only one bank displays at half duty -- visible as fast flashing.
        # (Discovered when a static test pattern would not stop blinking.)
        # Mirroring the banks at emit time makes everything solid and lets the
        # rest of the code keep addressing either alias.
        out = bytearray(self.buf)
        for i in range(16):
            m = out[i] | out[i + 48]
            out[i] = out[i + 48] = m
        return out.hex().upper()

    def line(self) -> bytes:
        """Wire format expected by the firmware."""
        return b"F" + self.hex().encode() + b"\n"
