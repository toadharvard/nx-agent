#!/usr/bin/env python3
"""ELF (ELF32 ILP32 or ELF64 LP64, AArch64) -> NSO, following switch-tools elf2nso.

    elf2nso.py [--no-compress] [--info] in.elf out.nso
    elf2nso.py --dump file.nso

Semantics kept from switch-tools: the first three PT_LOAD segments are .text, .rodata and .data in that order;
segment memory offset = p_vaddr (minus the first segment's address for a fixed-address ET_EXEC, whose loader
maps it back at that address), size = p_filesz; the .data entry carries the bss size (p_memsz - p_filesz);
the text/rodata "module name" words are 1; flags 0x3f (LZ4 + SHA-256 check for all three); build id from the
GNU build-id note (truncated/padded to 0x20). In addition this tool rejects what Atmosphere's loader rejects
(non-zero text offset, unaligned segments, overlapping segments, extra PT_LOADs) instead of producing an NSO
that fails at launch.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lz4block  # noqa: E402
import nxelf  # noqa: E402

PAGE = 0x1000
NSO_HEADER_SIZE = 0x100
FLAG_TEXT_COMPRESS, FLAG_RO_COMPRESS, FLAG_DATA_COMPRESS = 1, 2, 4
FLAG_TEXT_HASH, FLAG_RO_HASH, FLAG_DATA_HASH = 8, 16, 32


def build_nso(elf: nxelf.Elf, compress: bool = True) -> bytes:
    loads = elf.loads()
    if len(loads) != 3:
        raise nxelf.ElfError(f"expected exactly 3 PT_LOAD segments (text, rodata, data), found {len(loads)}")
    text, ro, data = loads
    # NSO offsets are relative to the module base. A PIE starts at 0; a fixed-address image (the fnv-nx
    # guest, ET_EXEC) is stored relative to its first segment and mapped back there by whoever loads it.
    base = text.vaddr
    if base and elf.type != nxelf.ET_EXEC:
        raise nxelf.ElfError(f"position-independent image must start at 0 (got {base:#x})")
    for name, p in (("text", text), ("rodata", ro), ("data", data)):
        if p.vaddr % PAGE:
            raise nxelf.ElfError(f"{name} segment vaddr {p.vaddr:#x} is not page aligned")
    if text.vaddr + text.filesz > ro.vaddr or ro.vaddr + ro.filesz > data.vaddr:
        raise nxelf.ElfError("segments overlap or are out of order")
    if text.memsz != text.filesz or ro.memsz != ro.filesz:
        raise nxelf.ElfError("text/rodata must not have zero-fill (bss) parts")
    if (text.flags & 7) != 5 or (ro.flags & 7) != 4 or (data.flags & 7) != 6:
        raise nxelf.ElfError("segment permissions must be R-X, R--, RW-")

    flags = FLAG_TEXT_HASH | FLAG_RO_HASH | FLAG_DATA_HASH
    if compress:
        flags |= FLAG_TEXT_COMPRESS | FLAG_RO_COMPRESS | FLAG_DATA_COMPRESS

    blobs, segs, comp_sizes, hashes = [], [], [], []
    file_off = NSO_HEADER_SIZE
    for i, p in enumerate((text, ro, data)):
        raw = elf.segment_bytes(p)
        hashes.append(hashlib.sha256(raw).digest())
        blob = lz4block.compress(raw) if compress else raw
        blobs.append(blob)
        extra = (p.memsz - p.filesz) if i == 2 else 1
        segs.append((file_off, p.vaddr - base, p.filesz, extra))
        comp_sizes.append(len(blob))
        file_off += len(blob)

    build_id = elf.build_id()[:0x20].ljust(0x20, b"\0")
    hdr = bytearray(NSO_HEADER_SIZE)
    struct.pack_into("<4sIII", hdr, 0, b"NSO0", 0, 0, flags)
    for i, s in enumerate(segs):
        struct.pack_into("<IIII", hdr, 0x10 + 0x10 * i, *s)
    hdr[0x40:0x60] = build_id
    struct.pack_into("<III", hdr, 0x60, *comp_sizes)
    for i, h in enumerate(hashes):
        hdr[0xA0 + 0x20 * i:0xC0 + 0x20 * i] = h
    return bytes(hdr) + b"".join(blobs)


def parse_nso(buf: bytes) -> dict:
    if buf[:4] != b"NSO0":
        raise ValueError("not an NSO")
    flags = struct.unpack_from("<I", buf, 0xC)[0]
    segs = [struct.unpack_from("<IIII", buf, 0x10 + 0x10 * i) for i in range(3)]
    comp = struct.unpack_from("<III", buf, 0x60)
    out = {"flags": flags, "build_id": buf[0x40:0x60].hex(), "segments": []}
    for i, (foff, dst, size, extra) in enumerate(segs):
        blob = buf[foff:foff + comp[i]]
        raw = lz4block.decompress(blob, size) if flags & (1 << i) else blob
        want = buf[0xA0 + 0x20 * i:0xC0 + 0x20 * i]
        out["segments"].append({
            "name": ("text", "rodata", "data")[i], "file_off": foff, "dst": dst, "size": size,
            "file_size": comp[i], "bss" if i == 2 else "module_word": extra,
            "hash_ok": hashlib.sha256(raw).digest() == want, "bytes": raw,
        })
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-compress", action="store_true", help="store segments uncompressed")
    ap.add_argument("--dump", action="store_true", help="print an NSO's header and verify its hashes")
    ap.add_argument("input")
    ap.add_argument("output", nargs="?")
    a = ap.parse_args(argv)
    if a.dump:
        info = parse_nso(open(a.input, "rb").read())
        print(f"flags={info['flags']:#x} build_id={info['build_id']}")
        for s in info["segments"]:
            extra = f"bss={s['bss']:#x}" if "bss" in s else f"word={s['module_word']}"
            print(f"  {s['name']:6} dst={s['dst']:#010x} size={s['size']:#x} file={s['file_size']:#x} "
                  f"{extra} sha256={'ok' if s['hash_ok'] else 'BAD'}")
        return 0 if all(s["hash_ok"] for s in info["segments"]) else 1
    if not a.output:
        ap.error("output path required")
    elf = nxelf.read(a.input)
    nso = build_nso(elf, compress=not a.no_compress)
    with open(a.output, "wb") as f:
        f.write(nso)
    kind = "ELF32/ILP32" if not elf.is64 else "ELF64/LP64"
    print(f"{a.output}: {kind}, {len(nso)} bytes (lz4 backend: {lz4block.BACKEND})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
