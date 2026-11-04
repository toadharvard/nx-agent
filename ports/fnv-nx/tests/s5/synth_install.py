#!/usr/bin/env python3
"""synth_install.py - a synthetic game install for the emulated boot when the player's Data/ is not at hand: the
player's FalloutNV.exe (linked, never copied into the repository), plus INI, archive and plugin files we write here.

    python3 -I tests/s5/synth_install.py <dir> <FalloutNV.exe>
    FARM_GAME=<dir> tests/s5/farm_boot.sh <commit> <out>

The files are ours, not the game's: Fallout_default.ini holds only the keys the data path needs ([General] sLanguage,
[Archive] SArchiveList/bInvalidateOlderFiles), each listed archive is a BSA header with no folders, and FalloutNV.esm
is one TES4 record with no groups. They exercise the file API path (INI read, archive opens, the first ESM read); a
milestone reached on them is not the game's progress on its data, and the summary says "synthetic install".
<dir> must be outside the repository.
"""
from __future__ import annotations

import os
import struct
import sys

ARCHIVES = ["Synth - Meshes.bsa", "Synth - Textures.bsa", "Synth - Misc.bsa"]


def bsa_header():
    # BSA v104 header: magic, version, folder record offset (36), flags, folders, files, name lengths, file flags
    return b"BSA\0" + struct.pack("<8I", 104, 36, 3, 0, 0, 0, 0, 0)


def tes4_record():
    # one TES4 record: HEDR (version 1.34, 0 records, next id 0x800) and CNAM (author)
    hedr = b"HEDR" + struct.pack("<H", 12) + struct.pack("<fII", 1.34, 0, 0x800)
    cnam = b"CNAM" + struct.pack("<H", 7) + b"fnv-nx\0"
    data = hedr + cnam
    return b"TES4" + struct.pack("<IIIHH", len(data), 1, 0, 0, 15) + struct.pack("<I", 0) + data


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2])
        return 2
    out, exe = os.path.abspath(argv[0]), os.path.abspath(argv[1])
    top = os.path.dirname(os.path.abspath(__file__))
    while top != os.path.dirname(top) and not os.path.exists(os.path.join(top, ".git")):
        top = os.path.dirname(top)
    if os.path.exists(os.path.join(top, ".git")) and (out + "/").startswith(top + "/"):
        print(f"synth_install: {out} is inside the repository {top}", file=sys.stderr)
        return 1
    os.makedirs(os.path.join(out, "Data"), exist_ok=True)
    link = os.path.join(out, "FalloutNV.exe")
    if os.path.lexists(link):
        os.remove(link)
    os.symlink(exe, link)
    with open(os.path.join(out, "Fallout_default.ini"), "w", newline="\r\n") as f:
        f.write("; fnv-nx synthetic install (tests/s5/synth_install.py)\n[General]\nsLanguage=ENGLISH\n"
                "[Archive]\nSArchiveList=" + ", ".join(ARCHIVES) + "\nbInvalidateOlderFiles=0\n")
    for a in ARCHIVES:
        with open(os.path.join(out, "Data", a), "wb") as f:
            f.write(bsa_header())
    with open(os.path.join(out, "Data", "FalloutNV.esm"), "wb") as f:
        f.write(tes4_record())
    with open(os.path.join(out, "SYNTHETIC"), "w") as f:
        f.write("fnv-nx synthetic install: not the game's data\n")
    print(f"synthetic install in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
