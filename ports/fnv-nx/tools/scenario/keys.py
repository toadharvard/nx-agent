#!/usr/bin/env python3
"""keys.py - one key vocabulary for both sides of a scenario (S18).

The scenario files name keys the way xdotool does (X keysyms: Return, Escape, grave, a, 1, space, Up ...). The Wine
side sends them to Xvfb with xdotool; the guest side turns them into FNVNX_EV_KEY events (USB HID usages, the code
the host's input_poll reports and platform/guest/platform_glue.cpp maps to DirectInput DIK codes). Text is typed as
a sequence of keys, with Shift for the characters that need it on a US layout.

    keys.py hid <keysym>            the HID usage (decimal)
    keys.py text <text>             the keysyms that type <text>, one per line ('shift+<key>' where Shift is held)
"""
import sys

# USB HID keyboard usages (HID Usage Tables 1.12, page 0x07)
HID = {"Return": 0x28, "Escape": 0x29, "BackSpace": 0x2A, "Tab": 0x2B, "space": 0x2C, "minus": 0x2D, "equal": 0x2E,
       "bracketleft": 0x2F, "bracketright": 0x30, "backslash": 0x31, "semicolon": 0x33, "apostrophe": 0x34,
       "grave": 0x35, "comma": 0x36, "period": 0x37, "slash": 0x38, "Caps_Lock": 0x39,
       "Right": 0x4F, "Left": 0x50, "Down": 0x51, "Up": 0x52, "Delete": 0x4C, "Home": 0x4A, "End": 0x4D,
       "Page_Up": 0x4B, "Page_Down": 0x4E, "Insert": 0x49,
       "Control_L": 0xE0, "Shift_L": 0xE1, "Alt_L": 0xE2, "Control_R": 0xE4, "Shift_R": 0xE5, "Alt_R": 0xE6}
for _i, _c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    HID[_c] = 0x04 + _i
for _i, _c in enumerate("1234567890"):
    HID[_c] = 0x1E + _i
for _i in range(12):
    HID[f"F{_i + 1}"] = 0x3A + _i

# characters -> (keysym, shift)
CHAR = {" ": ("space", False), "-": ("minus", False), "_": ("minus", True), "=": ("equal", False),
        "+": ("equal", True), "[": ("bracketleft", False), "]": ("bracketright", False), "\\": ("backslash", False),
        ";": ("semicolon", False), ":": ("semicolon", True), "'": ("apostrophe", False), '"': ("apostrophe", True),
        ",": ("comma", False), ".": ("period", False), "/": ("slash", False), "?": ("slash", True),
        "<": ("comma", True), ">": ("period", True), "`": ("grave", False), "~": ("grave", True),
        "!": ("1", True), "@": ("2", True), "#": ("3", True), "$": ("4", True), "%": ("5", True), "^": ("6", True),
        "&": ("7", True), "*": ("8", True), "(": ("9", True), ")": ("0", True)}


def char_key(ch):
    """(keysym, shift) for one character of typed text"""
    if ch in CHAR:
        return CHAR[ch]
    if ch.isascii() and ch.isalpha():
        return ch.lower(), ch.isupper()
    if ch.isascii() and ch.isdigit():
        return ch, False
    raise ValueError(f"cannot type {ch!r}")


def text_keys(text):
    """the key presses that type text: ['a', 'shift+minus', ...]"""
    out = []
    for ch in text:
        k, sh = char_key(ch)
        out.append(("shift+" if sh else "") + k)
    return out


def hid(keysym):
    """the HID usage of an xdotool keysym name (KeyError for an unknown one)"""
    return HID[keysym if keysym in HID else keysym.lower() if len(keysym) == 1 else keysym]


def press_events(key):
    """[(hid usage, down)] for one press of 'key' or 'shift+key' / 'ctrl+key' / 'alt+key'"""
    mods = {"shift": "Shift_L", "ctrl": "Control_L", "alt": "Alt_L"}
    parts = key.split("+")
    held = [hid(mods[m]) for m in parts[:-1]]
    k = hid(parts[-1])
    return [(m, 1) for m in held] + [(k, 1), (k, 0)] + [(m, 0) for m in reversed(held)]


def main(a):
    if len(a) == 2 and a[0] == "hid":
        print(hid(a[1]))
        return 0
    if len(a) == 2 and a[0] == "text":
        print("\n".join(text_keys(a[1])))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
