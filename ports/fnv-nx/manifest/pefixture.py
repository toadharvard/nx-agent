#!/usr/bin/env python3
"""pefixture - a PE32 file built from given bytes, for the self-tests of the manifest passes (no game bytes, no private input).

  build_pe(base, img, relocs, imports, code) -> bytes of a PE32 whose mapped image at `base` is `img` (the bytes at base+0 ..), with
      .text   the page-rounded range `code` = (va, size)            (executable section)
      .data   everything of `img` from the end of .text to the end   (data)
      .idt    the import directory of `imports` [(dll, name, slot va)]: descriptors, lookup tables, hint/name entries, dll names
              (one descriptor per run of consecutive 4-byte slots of one dll)
      .reloc  the HIGHLOW relocations `relocs` (absolute VAs)
This module writes bytes; it reads none. It is test support: the only consumer of its output is imagerec.ImageRec, the one reader.
"""
import struct

PAGE = 0x1000


def _up(n):
    return (n + PAGE - 1) & ~(PAGE - 1)


def build_pe(base, img, relocs, imports, code):
    img = bytes(img)
    (cva, csz), = code
    text_rva, text_vs = cva - base, _up(csz)
    data_rva = text_rva + text_vs
    assert text_rva >= PAGE and data_rva <= len(img), "the code range lies inside the image after the first page"
    data = img[data_rva:]
    idt_rva = _up(len(img))
    # the import directory
    runs, last = [], None
    for dll, name, slot in sorted(imports, key=lambda t: (t[0], t[2])):
        if last is not None and last[0] == dll and slot == last[2] + 4:
            runs[-1][1].append(name)
        else:
            runs.append([dll, [name], slot - base])
        last = (dll, name, slot)
    nd = len(runs) + 1
    body, off = bytearray(20 * nd), 20 * nd
    desc = []
    for dll, names, ft in runs:
        lk, off = off, off + 4 * (len(names) + 1)
        body += b"\0" * (4 * (len(names) + 1))
        hn = []
        for nm in names:
            hn.append(len(body))
            body += b"\0\0" + nm.encode() + b"\0" + (b"\0" if (len(nm) + 3) % 2 else b"")
        for k, h in enumerate(hn):
            struct.pack_into("<I", body, lk + 4 * k, idt_rva + h)
        dn = len(body)
        body += dll.encode() + b"\0"
        desc.append((idt_rva + lk, dn, ft))
    for k, (oft, dn, ft) in enumerate(desc):
        struct.pack_into("<IIIII", body, 20 * k, oft, 0, 0, idt_rva + dn, ft)
    reloc_rva = idt_rva + _up(len(body))
    blocks = {}
    for r in sorted(relocs):
        blocks.setdefault((r - base) & ~0xfff, []).append(0x3000 | ((r - base) & 0xfff))
    rel = b""
    for page, es in sorted(blocks.items()):
        if len(es) % 2:
            es.append(0)
        rel += struct.pack("<II", page, 8 + 2 * len(es)) + b"".join(struct.pack("<H", e) for e in es)
    secs = [(b".text", text_rva, img[text_rva:text_rva + text_vs].ljust(text_vs, b"\0"), 0x60000020),
            (b".data", data_rva, data, 0x40000040),
            (b".idt", idt_rva, bytes(body), 0x40000040),
            (b".reloc", reloc_rva, rel or b"\0\0\0\0", 0x42000040)]
    hdr = bytearray(0x400)
    hdr[0:2] = b"MZ"
    struct.pack_into("<I", hdr, 0x3c, 0x40)
    hdr[0x40:0x44] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", hdr, 0x44, 0x14c, len(secs), 0, 0, 0, 224, 0x2102)
    o = 0x58
    struct.pack_into("<H", hdr, o, 0x10b)
    struct.pack_into("<I", hdr, o + 16, text_rva)
    struct.pack_into("<I", hdr, o + 28, base)
    struct.pack_into("<II", hdr, o + 32, PAGE, 0x200)
    struct.pack_into("<II", hdr, o + 56, _up(reloc_rva + len(rel or b"\0\0\0\0")), 0x400)
    struct.pack_into("<I", hdr, o + 92, 16)
    struct.pack_into("<II", hdr, o + 96 + 8, idt_rva, len(body))
    struct.pack_into("<II", hdr, o + 96 + 40, reloc_rva, len(rel))
    sh, fo, out = o + 224, 0x400, b""
    for i, (nm, rva, d, ch) in enumerate(secs):
        struct.pack_into("<8sIIIIIIHHI", hdr, sh + 40 * i, nm, len(d), rva, len(d), fo + len(out), 0, 0, 0, 0, ch)
        out += d
    return bytes(hdr) + out
