#!/usr/bin/env python3
"""profile.py - the first-run profile both sides of the lockstep trace boot with (same inputs).

    profile.py <Fallout_default.ini> <out dir> --device NAME [--width 1280 --height 720] [--plugins FILE]

Writes what the port's first run makes (platform/win32/profile.cpp, the PC launcher's behaviour):
  <out>/Documents/My Games/FalloutNV/Fallout.ini        a copy of Fallout_default.ini
  <out>/Documents/My Games/FalloutNV/FalloutPrefs.ini   the same plus [Display] sD3DDevice="<NAME>", iSize W/H
  <out>/AppData/Local/FalloutNV/plugins.txt             --plugins' lines, or empty (CRLF)
  <out>/AppData/Local/FalloutNV/NVDLCList.txt           empty
The device name is the only key that differs between the sides: each game compares it with its own adapter's
Description (the guest: "fnv-nx null D3D9 device"; WineD3D: the name of wine_ref.sh's PCI id). Keys are set the way
WritePrivateProfileString sets them: replaced in place in their section, else appended to it."""
import argparse
import os
import sys


def set_key(lines, section, key, value):
    sec, start, end = None, None, None
    for i, l in enumerate(lines):
        t = l.strip()
        if t.startswith("[") and "]" in t:
            if start is not None and end is None:
                end = i
            sec = t[1:t.index("]")].strip()
            if sec.lower() == section.lower() and start is None:
                start = i
    if start is None:
        lines += [f"[{section}]", f"{key}={value}"]
        return
    end = len(lines) if end is None else end
    for i in range(start + 1, end):
        k = lines[i].split("=", 1)[0].strip()
        if "=" in lines[i] and k.lower() == key.lower() and not lines[i].lstrip().startswith(";"):
            lines[i] = f"{key}={value}"
            return
    j = end
    while j > start + 1 and not lines[j - 1].strip():
        j -= 1
    lines.insert(j, f"{key}={value}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("default_ini")
    ap.add_argument("out")
    ap.add_argument("--device", required=True)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--plugins")
    a = ap.parse_args()
    raw = open(a.default_ini, "rb").read()
    crlf = b"\r\n" in raw
    text = raw.decode("latin-1")
    docs = os.path.join(a.out, "Documents", "My Games", "FalloutNV")
    local = os.path.join(a.out, "AppData", "Local", "FalloutNV")
    os.makedirs(docs, exist_ok=True)
    os.makedirs(local, exist_ok=True)
    open(os.path.join(docs, "Fallout.ini"), "wb").write(raw)
    lines = text.splitlines()
    set_key(lines, "Display", "sD3DDevice", f'"{a.device}"')
    set_key(lines, "Display", "iSize W", str(a.width))
    set_key(lines, "Display", "iSize H", str(a.height))
    eol = "\r\n" if crlf else "\n"
    open(os.path.join(docs, "FalloutPrefs.ini"), "wb").write((eol.join(lines) + eol).encode("latin-1"))
    plugins = [l.strip() for l in open(a.plugins)] if a.plugins else []
    open(os.path.join(local, "plugins.txt"), "wb").write("".join(p + "\r\n" for p in plugins if p).encode())
    open(os.path.join(local, "NVDLCList.txt"), "wb").write(b"")
    print(f"profile: {a.out} (device {a.device!r}, {a.width}x{a.height}, {len(plugins)} plugins)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
