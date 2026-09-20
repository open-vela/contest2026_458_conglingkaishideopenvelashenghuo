#!/usr/bin/env python3
"""Build the symbol list for the WristClaw CJK LVGL bitmap font.

Covers ASCII, CJK punctuation, fullwidth forms and the 3755 GB2312 level-1
hanzi (the most common simplified Chinese characters) so the watch UI never
falls back to an empty box ("tofu").
"""
import sys

chars = set()

# --- GB2312 level 1 hanzi (0xB0A1..0xD7FE) -------------------------------
for lead in range(0xB0, 0xD8):
    for trail in range(0xA1, 0xFF):
        try:
            chars.add(bytes((lead, trail)).decode("gb2312"))
        except UnicodeDecodeError:
            pass

# --- GB2312 level 2 hanzi (0xD8A1..0xF7FE) -------------------------------
# Adds ~3000 rarer characters. Drop this block to shrink the font by roughly
# one third if flash space ever becomes tight.
for lead in range(0xD8, 0xF8):
    for trail in range(0xA1, 0xFF):
        try:
            chars.add(bytes((lead, trail)).decode("gb2312"))
        except UnicodeDecodeError:
            pass

# --- punctuation / symbols we actually show -----------------------------
chars.update("°—…·、。，！？“”‘’：；（）《》【】〈〉　℃±×÷≈→←↑↓●○■□★☆✓✕")
chars.update("？！.,:;'\"()<>[]{}%+/-=_")

# --- every character used in the app sources ----------------------------
import glob
import os
import re

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "app", "wristclaw")
for path in glob.glob(os.path.join(APP, "*.c")) + glob.glob(os.path.join(APP, "*.h")):
    with open(path, encoding="utf-8", errors="replace") as fh:
        src = fh.read()
    for lit in re.findall(r'"((?:[^"\\]|\\.)*)"', src):
        chars.update(lit)

out = "".join(sorted(c for c in chars if ord(c) >= 0x20))

# Deduplicate while preserving the sorted order, then write one giant line.
with open(sys.argv[1], "w", encoding="utf-8", newline="") as fh:
    fh.write(out)

print("symbols: %d  (file: %s)" % (len(out), sys.argv[1]))
print("hanzi:   %d" % sum(1 for c in out if 0x4E00 <= ord(c) <= 0x9FFF))
