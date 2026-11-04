#!/usr/bin/env python3
"""elf2nro.py - LP64 AArch64 ELF -> NRO (homebrew executable for nx-hbloader), plus the NACP and the asset section.

    elf2nro.py <in.elf> <out.nro> [--icon icon.jpg] [--nacp control.nacp] [--alignedheader]
    elf2nro.py --nacp-create <out.nacp> --name N --author A --version V [--title-id 0x...]
    elf2nro.py --dump <file.nro>

Semantics are those of switch-tools `elf2nro` and `nacptool` (2275606): the first three PT_LOAD segments are text,
rodata and data, each stored at its virtual address and padded to 4 KiB; the header goes at 0x10 of text (the
libnx crt0 leaves 0x80 bytes for NroStart + NroHeader); bss is the data segment's memsz past its padded filesz;
the GNU build id is copied; assets (ASET) follow the image. The output is byte-identical to elf2nro's for the same
inputs (tests/nx1/check_nro.py checks it against a switch-tools build when one is given).

Unlike elf2nro, this tool also refuses inputs hbloader would load wrongly: segments out of order or overlapping,
text not at 0, a gap between the segments (elf2nro silently writes a wrong header.size), and no MOD0.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nxelf  # noqa: E402

PAGE = 0x1000
NRO_MAGIC = b"NRO0"
ASET_MAGIC = b"ASET"
NACP_SIZE = 0x4000
NRO_START_SIZE, NRO_HEADER_SIZE, ASSET_HEADER_SIZE = 0x10, 0x70, 0x38


def align(x: int, a: int = PAGE) -> int:
    return (x + a - 1) & ~(a - 1)


def build_nro(elf: nxelf.Elf, icon: bytes | None = None, nacp: bytes | None = None, romfs: bytes | None = None,
              aligned_header: bool = False) -> bytes:
    if not elf.is64:
        raise ValueError("expected an LP64 AArch64 ELF (ELFCLASS64): NROs run in hbloader's 64-bit process")
    loads = [p for p in elf.phdrs if p.type == nxelf.PT_LOAD]
    if len(loads) < 3:
        raise ValueError(f"expected 3 PT_LOAD segments (text, rodata, data), got {len(loads)}")
    segs = loads[:3]
    raw = elf.data
    out = bytearray()
    hdr_segs = []
    for i, p in enumerate(segs):
        size = align(p.filesz)
        if p.vaddr != len(out):
            raise ValueError(f"segment {i} at {p.vaddr:#x}, expected {len(out):#x}: segments must be contiguous from 0")
        if p.vaddr % PAGE:
            raise ValueError(f"segment {i} at {p.vaddr:#x} is not page aligned")
        body = raw[p.offset:p.offset + p.filesz]
        out += body + bytes(size - len(body))
        hdr_segs.append((p.vaddr, size))
    data = segs[2]
    padded = align(data.filesz)
    bss = align(data.memsz - padded) if data.memsz > padded else 0
    if len(out) < NRO_START_SIZE + NRO_HEADER_SIZE:
        raise ValueError("text too small for the NRO header")
    mod_off = struct.unpack_from("<I", out, 4)[0]
    if mod_off + 4 > hdr_segs[0][1] + hdr_segs[1][1] + hdr_segs[2][1] or out[mod_off:mod_off + 4] != b"MOD0":
        raise ValueError(f"no MOD0 at the offset in NroStart+4 ({mod_off:#x}): not built with a libnx-style crt0")
    build_id = (elf.build_id() or b"")[:0x20]
    header = struct.pack("<4sIII", NRO_MAGIC, 0, len(out), 1 if aligned_header else 0)
    header += b"".join(struct.pack("<II", off, size) for off, size in hdr_segs)
    header += struct.pack("<II", bss, 0) + build_id.ljust(0x20, b"\0") + bytes(0x20)
    assert len(header) == NRO_HEADER_SIZE
    out[NRO_START_SIZE:NRO_START_SIZE + NRO_HEADER_SIZE] = header
    if icon is None and nacp is None and romfs is None:
        return bytes(out)
    off = ASSET_HEADER_SIZE
    sections, blobs = [], []
    for blob in (icon, nacp, romfs):
        if blob is None:
            sections.append((0, 0))
        else:
            sections.append((off, len(blob)))
            blobs.append(blob)
            off += len(blob)
    aset = struct.pack("<4sI", ASET_MAGIC, 0) + b"".join(struct.pack("<QQ", o, s) for o, s in sections)
    return bytes(out) + aset + b"".join(blobs)


def build_nacp(name: str, author: str, version: str, title_id: int = 0) -> bytes:
    """nacptool --create: the 12 language slots nacptool knows get the name and author (the other 4 stay zero)."""
    n = bytearray(NACP_SIZE)
    nb, ab, vb = name.encode()[:0x1FF], author.encode()[:0xFF], version.encode()[:0xF]
    for i in range(12):
        n[i * 0x300:i * 0x300 + len(nb)] = nb
        n[i * 0x300 + 0x200:i * 0x300 + 0x200 + len(ab)] = ab
    n[0x3060:0x3060 + len(vb)] = vb
    struct.pack_into("<I", n, 0x3024, 0x100)
    struct.pack_into("<I", n, 0x302C, 0xBFF)
    struct.pack_into("<I", n, 0x3034, 0x10000)
    n[0x3040:0x3060] = bytes([0x0C, 0xFF, 0xFF, 0x0A, 0xFF, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0x0D, 0x0D]) + b"\xff" * 20
    struct.pack_into("<I", n, 0x3080, 0x3E00000)
    struct.pack_into("<I", n, 0x3088, 0x180000)
    struct.pack_into("<I", n, 0x30F0, 0x102)
    if title_id:
        struct.pack_into("<Q", n, 0x3038, title_id)
        struct.pack_into("<Q", n, 0x3070, title_id + 0x1000)
        struct.pack_into("<Q", n, 0x3078, title_id)
        struct.pack_into("<Q", n, 0x30B0, title_id)
    return bytes(n)


def dump(buf: bytes) -> str:
    magic, ver, size, flags = struct.unpack_from("<4sIII", buf, 0x10)
    segs = [struct.unpack_from("<II", buf, 0x20 + 8 * i) for i in range(3)]
    bss = struct.unpack_from("<I", buf, 0x38)[0]
    lines = [f"magic {magic!r} version {ver} size {size:#x} flags {flags:#x} bss {bss:#x} "
             f"mod0 {struct.unpack_from('<I', buf, 4)[0]:#x}"]
    lines += [f"  {n:6} off {o:#x} size {s:#x}" for n, (o, s) in zip(("text", "rodata", "data"), segs)]
    if len(buf) >= size + ASSET_HEADER_SIZE and buf[size:size + 4] == ASET_MAGIC:
        for n, i in (("icon", 0), ("nacp", 1), ("romfs", 2)):
            o, s = struct.unpack_from("<QQ", buf, size + 8 + 16 * i)
            lines.append(f"  asset {n:5} off {o:#x} size {s:#x}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("input", nargs="?")
    ap.add_argument("output", nargs="?")
    ap.add_argument("--icon")
    ap.add_argument("--nacp")
    ap.add_argument("--romfs")
    ap.add_argument("--alignedheader", action="store_true")
    ap.add_argument("--dump", metavar="NRO")
    ap.add_argument("--nacp-create", metavar="OUT")
    ap.add_argument("--name")
    ap.add_argument("--author")
    ap.add_argument("--version")
    ap.add_argument("--title-id", type=lambda s: int(s, 0), default=0)
    a = ap.parse_args(argv)
    if a.dump:
        print(dump(open(a.dump, "rb").read()))
        return 0
    if a.nacp_create:
        if not (a.name and a.author and a.version):
            ap.error("--nacp-create needs --name, --author and --version")
        open(a.nacp_create, "wb").write(build_nacp(a.name, a.author, a.version, a.title_id))
        return 0
    if not (a.input and a.output):
        ap.error("need <in.elf> <out.nro>")
    rd = lambda p: open(p, "rb").read() if p else None  # noqa: E731
    try:
        nro = build_nro(nxelf.read(a.input), rd(a.icon), rd(a.nacp), rd(a.romfs), a.alignedheader)
    except (ValueError, nxelf.ElfError) as e:
        print(f"elf2nro.py: {a.input}: {e}", file=sys.stderr)
        return 1
    open(a.output, "wb").write(nro)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
