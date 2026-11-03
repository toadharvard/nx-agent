#!/usr/bin/env python3
"""Pack files into a PFS0 (e.g. Atmosphere's /atmosphere/contents/<program id>/exefs.nsp), like switch-tools
build_pfs0, or list one.

    build_pfs0.py out.nsp main=build/host.nso main.npdm=build/main.npdm
    build_pfs0.py --list file.nsp

Layout (as build_pfs0): header {"PFS0", count, string table size, 0}, count * {u64 offset, u64 size,
u32 name offset, u32 0}, string table padded to 0x20, then the file data back to back (offsets relative to it).
Files are stored in the order given.
"""
from __future__ import annotations

import argparse
import struct
import sys


def build_pfs0(files: list[tuple[str, bytes]]) -> bytes:
    names = bytearray()
    entries = bytearray()
    data_off = 0
    for name, blob in files:
        if not name or "/" in name:
            raise ValueError(f"bad PFS0 entry name {name!r}")
        entries += struct.pack("<QQII", data_off, len(blob), len(names), 0)
        names += name.encode() + b"\0"
        data_off += len(blob)
    names += b"\0" * (((len(names) + 0x1F) & ~0x1F) - len(names))
    hdr = struct.pack("<4sIII", b"PFS0", len(files), len(names), 0)
    return hdr + bytes(entries) + bytes(names) + b"".join(b for _, b in files)


def parse_pfs0(buf: bytes) -> list[tuple[str, int, int]]:
    magic, count, strsz, _ = struct.unpack_from("<4sIII", buf, 0)
    if magic != b"PFS0":
        raise ValueError("not a PFS0")
    strtab_off = 0x10 + 0x18 * count
    data_base = strtab_off + strsz
    out = []
    for i in range(count):
        off, size, noff, _ = struct.unpack_from("<QQII", buf, 0x10 + 0x18 * i)
        end = buf.index(b"\0", strtab_off + noff)
        out.append((buf[strtab_off + noff:end].decode(), data_base + off, size))
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("output")
    ap.add_argument("files", nargs="*", help="NAME=PATH")
    a = ap.parse_args(argv)
    if a.list:
        for name, off, size in parse_pfs0(open(a.output, "rb").read()):
            print(f"{name:12} offset={off:#x} size={size:#x}")
        return 0
    files = []
    for spec in a.files:
        name, _, path = spec.partition("=")
        if not path:
            ap.error(f"expected NAME=PATH, got {spec!r}")
        files.append((name, open(path, "rb").read()))
    blob = build_pfs0(files)
    with open(a.output, "wb") as f:
        f.write(blob)
    print(f"{a.output}: {len(files)} files, {len(blob)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
