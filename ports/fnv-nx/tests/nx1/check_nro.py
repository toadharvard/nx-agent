#!/usr/bin/env python3
"""NX1 check 1: the NRO format, validated independently of the packer.

    check_nro.py <file.nro> [--elf <in.elf>] [--ref-elf2nro <switch-tools elf2nro>] [--ref-nacptool <nacptool>]
                 [--require-assets] [--json out.json]

This parser is written from the switchbrew pages (NRO, NACP_Format, NSO MOD0) and nx-hbloader 82b9512
`loadNro()`; it shares no code with toolchain/tools/elf2nro.py. It checks:
  header   NroStart (MOD0 offset), NRO0 magic, version 0, size, text at 0 / rodata / data contiguous and page
           aligned, bss page aligned, the bounds hbloader enforces (file_off < size, file_off + size <= size);
  MOD0     magic, the dynamic table inside rodata/data, bss_start/bss_end inside data+bss, the libnx LNY0/LNY1
           extensions when present (homebrew crt0 conventions);
  dynamic  DT_NULL-terminated, no DT_NEEDED (hbloader has no dynamic linker), RELA/RELR inside the image, every
           RELA entry R_AARCH64_NONE or R_AARCH64_RELATIVE (libnx's __nx_dynamic aborts on any other type);
  assets   ASET version 0 inside the file; the icon is a baseline JPEG of 256x256 (hbmenu's decoder); the NACP is
           0x4000 bytes with a NUL-terminated UTF-8 name, author and version in language slot 0;
  ELF      (with --elf) the text segment is R-X, rodata R--, data RW-, and the image bytes match the ELF segments;
  round    (with --ref-elf2nro and --elf) switch-tools elf2nro, given the same ELF and the assets extracted from
  trip     this file, produces a byte-identical NRO; (with --ref-nacptool) nacptool --create with this NACP's name,
           author and version produces a byte-identical NACP.
Exit 0 when every check passes, 1 otherwise. One line per check: `PASS <id>` / `FAIL <id>: why`.
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import subprocess
import sys
import tempfile

PAGE = 0x1000
DT_NULL, DT_NEEDED, DT_RELA, DT_RELASZ, DT_RELAENT, DT_RELR, DT_RELRSZ = 0, 1, 7, 8, 9, 36, 35
R_NONE, R_RELATIVE = 0, 1027


class Checks:
    def __init__(self):
        self.rows: list[tuple[str, bool, str]] = []

    def ok(self, cid: str, cond: bool, why: str = "") -> bool:
        self.rows.append((cid, bool(cond), "" if cond else why))
        return bool(cond)

    @property
    def passed(self) -> bool:
        return all(r[1] for r in self.rows)

    def print(self):
        for cid, good, why in self.rows:
            print(f"PASS {cid}" if good else f"FAIL {cid}: {why}")


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def jpeg_info(b: bytes):
    """(width, height, sof marker) of a JPEG, or None. Walks the marker segments up to the first SOFn."""
    if b[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 4 <= len(b):
        if b[i] != 0xFF:
            return None
        m = b[i + 1]
        if m == 0xFF:
            i += 1
            continue
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        ln = struct.unpack_from(">H", b, i + 2)[0]
        if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack_from(">HH", b, i + 5)
            return w, h, m
        i += 2 + ln
    return None


def cstr(b: bytes, o: int, n: int):
    s = b[o:o + n]
    z = s.find(b"\0")
    if z < 0:
        return None
    try:
        return s[:z].decode("utf-8")
    except UnicodeDecodeError:
        return None


def check(path: str, elf: str | None, ref_elf2nro: str | None, ref_nacptool: str | None, require_assets: bool) -> Checks:
    c = Checks()
    buf = open(path, "rb").read()
    if not c.ok("header.size", len(buf) >= 0x80, f"file is {len(buf)} bytes, smaller than NroStart+NroHeader"):
        return c
    mod_off = u32(buf, 4)
    magic, version, size, flags = struct.unpack_from("<4sIII", buf, 0x10)
    segs = [struct.unpack_from("<II", buf, 0x20 + 8 * i) for i in range(3)]
    bss = u32(buf, 0x38)
    c.ok("header.magic", magic == b"NRO0", f"magic {magic!r}")
    c.ok("header.version", version == 0, f"version {version}")
    c.ok("header.flags", flags & ~1 == 0, f"unknown flags {flags:#x}")
    c.ok("header.filesize", size <= len(buf), f"header size {size:#x} > file {len(buf):#x}")
    c.ok("seg.text_at_0", segs[0][0] == 0, f"text at {segs[0][0]:#x}: hbloader enters at the image base")
    pos = 0
    for name, (off, sz) in zip(("text", "rodata", "data"), segs):
        c.ok(f"seg.{name}.aligned", off % PAGE == 0 and sz % PAGE == 0, f"{name} {off:#x}+{sz:#x} not page aligned")
        c.ok(f"seg.{name}.contiguous", off == pos, f"{name} at {off:#x}, expected {pos:#x}")
        c.ok(f"seg.{name}.hbloader_bounds", off < size and sz <= size and off + sz <= size,
             f"{name} {off:#x}+{sz:#x} outside header size {size:#x} (hbloader refuses it)")
        pos = off + sz
    c.ok("seg.size_matches", pos == size, f"segments end at {pos:#x}, header size {size:#x}")
    c.ok("seg.bss_aligned", bss % PAGE == 0, f"bss {bss:#x}")
    c.ok("seg.nonempty", segs[0][1] > 0 and segs[2][1] + bss > 0, "empty text or data")
    img_end = size + bss
    rx = (segs[0][0], segs[0][0] + segs[0][1])
    ro = (segs[1][0], segs[1][0] + segs[1][1])
    rw = (segs[2][0], segs[2][0] + segs[2][1] + bss)

    # MOD0
    if c.ok("mod0.offset", mod_off % 4 == 0 and mod_off + 0x1C <= size, f"MOD0 offset {mod_off:#x}"):
        c.ok("mod0.magic", buf[mod_off:mod_off + 4] == b"MOD0", f"{buf[mod_off:mod_off + 4]!r} at {mod_off:#x}")
        rel = lambda k: (mod_off + struct.unpack_from("<i", buf, mod_off + 4 * k)[0]) & 0xFFFFFFFF  # noqa: E731
        dyn, bss_s, bss_e, eh_s, eh_e = rel(1), rel(2), rel(3), rel(4), rel(5)
        c.ok("mod0.dynamic", ro[0] <= dyn < rw[1] and dyn % 8 == 0, f"_DYNAMIC at {dyn:#x}")
        c.ok("mod0.bss", rw[0] <= bss_s <= bss_e <= img_end, f"bss {bss_s:#x}..{bss_e:#x} outside data {rw[0]:#x}..{img_end:#x}")
        c.ok("mod0.eh_frame_hdr", eh_s <= eh_e and (eh_s == eh_e or ro[0] <= eh_s and eh_e <= rw[1]),
             f"eh_frame_hdr {eh_s:#x}..{eh_e:#x}")
        ext = mod_off + 0x1C
        if buf[ext:ext + 4] == b"LNY0":
            g0 = (mod_off + struct.unpack_from("<i", buf, ext + 4)[0]) & 0xFFFFFFFF
            g1 = (mod_off + struct.unpack_from("<i", buf, ext + 8)[0]) & 0xFFFFFFFF
            c.ok("mod0.lny0_got", rw[0] <= g0 <= g1 <= rw[1], f"GOT {g0:#x}..{g1:#x} outside data")
        # dynamic table
        if dyn + 16 <= size:
            tags, i, terminated = {}, dyn, False
            needed = 0
            while i + 16 <= size:
                t, v = struct.unpack_from("<qQ", buf, i)
                i += 16
                if t == DT_NULL:
                    terminated = True
                    break
                if t == DT_NEEDED:
                    needed += 1
                tags.setdefault(t, v)
            c.ok("dyn.terminated", terminated, "no DT_NULL inside the image")
            c.ok("dyn.no_needed", needed == 0, f"{needed} DT_NEEDED: hbloader loads no shared libraries")
            if DT_RELA in tags:
                ra, rs = tags[DT_RELA], tags.get(DT_RELASZ, 0)
                if c.ok("dyn.rela_bounds", ra + rs <= size and tags.get(DT_RELAENT, 24) == 24, f"RELA {ra:#x}+{rs:#x}"):
                    bad = [struct.unpack_from("<QQq", buf, ra + k)[1] & 0xFFFFFFFF for k in range(0, rs, 24)]
                    bad = sorted({t for t in bad if t not in (R_NONE, R_RELATIVE)})
                    c.ok("dyn.rela_types", not bad, f"relocation types {bad}: libnx __nx_dynamic aborts (LibnxError_BadReloc)")
                    tgt = [struct.unpack_from("<Q", buf, ra + k)[0] for k in range(0, rs, 24)]
                    c.ok("dyn.rela_targets", all(rw[0] <= t < rw[1] - 7 or ro[0] <= t < ro[1] - 7 for t in tgt),
                         "a relocation writes outside rodata/data (text is mapped R-X)")
            if DT_RELR in tags:
                c.ok("dyn.relr_bounds", tags[DT_RELR] + tags.get(DT_RELRSZ, 0) <= size, "RELR outside the image")

    # assets
    have_aset = len(buf) >= size + 0x38 and buf[size:size + 4] == b"ASET"
    if require_assets:
        c.ok("aset.present", have_aset, "no ASET after the image: hbmenu shows no icon or title")
    if have_aset:
        av = u32(buf, size + 4)
        secs = [struct.unpack_from("<QQ", buf, size + 8 + 16 * i) for i in range(3)]
        c.ok("aset.version", av == 0, f"version {av}")
        for nm, (o, s) in zip(("icon", "nacp", "romfs"), secs):
            c.ok(f"aset.{nm}.bounds", size + o + s <= len(buf), f"{nm} {o:#x}+{s:#x} outside the file")
        io, isz = secs[0]
        if require_assets:
            c.ok("aset.icon.present", isz > 0, "no icon")
        if isz:
            ji = jpeg_info(buf[size + io:size + io + isz])
            c.ok("aset.icon.jpeg", ji is not None and buf[size + io + isz - 2:size + io + isz] == b"\xff\xd9",
                 "icon is not a complete JPEG")
            if ji:
                c.ok("aset.icon.256", ji[:2] == (256, 256), f"icon is {ji[0]}x{ji[1]}, hbmenu expects 256x256")
                c.ok("aset.icon.baseline", ji[2] == 0xC0, f"SOF marker {ji[2]:#x}: hbmenu wants a baseline JPEG")
        no, nsz = secs[1]
        if require_assets:
            c.ok("aset.nacp.present", nsz > 0, "no NACP")
        if nsz:
            n = buf[size + no:size + no + nsz]
            c.ok("nacp.size", nsz == 0x4000, f"NACP is {nsz:#x} bytes")
            name, author, ver = cstr(n, 0, 0x200), cstr(n, 0x200, 0x100), cstr(n, 0x3060, 0x10)
            c.ok("nacp.name", bool(name), "language 0 name empty, not NUL-terminated or not UTF-8")
            c.ok("nacp.author", bool(author), "language 0 author empty or invalid")
            c.ok("nacp.version", bool(ver), "display version empty or invalid")
            if ref_nacptool and name and author and ver:
                with tempfile.TemporaryDirectory() as t:
                    out = os.path.join(t, "ref.nacp")
                    r = subprocess.run([ref_nacptool, "--create", name, author, ver, out], capture_output=True)
                    same = r.returncode == 0 and open(out, "rb").read() == n
                    c.ok("roundtrip.nacptool", same, "switch-tools nacptool makes a different NACP from the same strings")

    if elf:
        e = open(elf, "rb").read()
        if c.ok("elf.class", e[:4] == b"\x7fELF" and e[4] == 2 and struct.unpack_from("<H", e, 18)[0] == 183,
                "not an ELF64 AArch64 file"):
            phoff, phentsize, phnum = struct.unpack_from("<Q", e, 32)[0], *struct.unpack_from("<HH", e, 54)
            loads = []
            for k in range(phnum):
                t, fl, off, va, _pa, fsz, msz, _al = struct.unpack_from("<IIQQQQQQ", e, phoff + k * phentsize)
                if t == 1:
                    loads.append((fl, off, va, fsz, msz))
            c.ok("elf.loads", len(loads) >= 3, f"{len(loads)} PT_LOAD")
            for (fl, off, va, fsz, _m), nm, want in zip(loads, ("text", "rodata", "data"), (5, 4, 6)):
                c.ok(f"elf.{nm}.perm", fl == want, f"{nm} flags {fl:#x}, want {want:#x} (W^X: text R-X, data RW-)")
                c.ok(f"elf.{nm}.bytes", buf[va:va + fsz] == e[off:off + fsz] if nm != "text"
                     else buf[0x80:fsz] == e[off + 0x80:off + fsz], f"{nm} bytes differ from the ELF")
            if ref_elf2nro:
                with tempfile.TemporaryDirectory() as t:
                    args = [ref_elf2nro, elf, os.path.join(t, "ref.nro")]
                    if have_aset:
                        secs = [struct.unpack_from("<QQ", buf, size + 8 + 16 * i) for i in range(3)]
                        for nm, (o, s) in zip(("icon", "nacp", "romfs"), secs):
                            if s:
                                p = os.path.join(t, nm)
                                open(p, "wb").write(buf[size + o:size + o + s])
                                args.append(f"--{nm}={p}")
                    if u32(buf, 0x1C) & 1:
                        args.append("--alignedheader")
                    r = subprocess.run(args, capture_output=True)
                    ref = open(args[2], "rb").read() if r.returncode == 0 else b""
                    c.ok("roundtrip.elf2nro", ref == buf,
                         f"switch-tools elf2nro output differs (rc {r.returncode}, {len(ref)} vs {len(buf)} bytes)")
    return c


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("nro")
    ap.add_argument("--elf")
    ap.add_argument("--ref-elf2nro")
    ap.add_argument("--ref-nacptool")
    ap.add_argument("--require-assets", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    c = check(a.nro, a.elf, a.ref_elf2nro, a.ref_nacptool, a.require_assets)
    c.print()
    if a.json:
        json.dump({"schema": "fnvnx-nx1-nro/1", "passed": c.passed,
                   "checks": [{"id": i, "pass": p, "why": w} for i, p, w in c.rows]}, open(a.json, "w"), indent=1)
    print(f"nro: {sum(r[1] for r in c.rows)}/{len(c.rows)} pass -> {'PASS' if c.passed else 'FAIL'}")
    return 0 if c.passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
