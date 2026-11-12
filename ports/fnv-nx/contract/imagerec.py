"""imagerec - the image record of the contract layer (L1 CONTRACT, the lowest layer: it imports nothing of platform/ or lift/).

    rec = ImageRec(data, name, runs)     data: the bytes of the image file; runs: {entry: [(start, end)]} (L3a function facts, absolute VAs at
                                         the image base of the file)
    imagerec.read_ranges(text)           ranges.tsv text -> runs
    imagerec.disasm(code, va, count=0)   THE decoder of x86-32 instructions (capstone, detail on); argflow, memfx and platform/manifest/
                                         funcfacts.py all decode through it; no other module of contract/ analysis or platform/ imports capstone
                                         for x86

The record is the only input of argflow.Image: argflow opens no file, reads no ranges file and computes no boundary. The producers build it
from bytes they have checked (funcfacts.py: the pin of the facts and the image sha256); the self-tests build it from synthetic bytes
(ImageRec(synthetic_pe_bytes, 'syn.exe', runs)).
"""
import struct

import capstone
from capstone import x86_const as X  # noqa: F401  (the operand and register constants of the decoded instructions)

import modules

_CS = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
_CS.detail = True


def disasm(code, va, count=0):
    """The instructions of `code` decoded at address `va` (all of them, or `count`), in order; stops at the first byte that does not decode."""
    return _CS.disasm(code, va, count)


def read_ranges(text):
    """ranges.tsv: `entry<TAB>start-end,start-end,...` (hex, absolute VAs); an entry with no bytes has no ranges."""
    out = {}
    for line in text.splitlines():
        if not line or line[0] == '#':
            continue
        f = line.split('\t')
        rs = [tuple(int(x, 16) for x in r.split('-')) for r in (f[1].split(',') if len(f) > 1 else []) if r]
        out[int(f[0], 16)] = rs
    return out


class ImageRec:
    """One PE32 image as typed data: file bytes, name, pe (modules.PE: sections, directories, rd), base, relocs (set of the absolute VAs of the
    HIGHLOW relocations), imports {slot va: (dll, name)}, runs {entry: [(start, end)]}."""

    def __init__(self, data, name, runs):
        self.file = bytes(data)
        self.name = name
        self.pe = pe = modules.PE(self.file, name)
        self.base = pe.base
        self.runs = {e: [tuple(r) for r in rs] for e, rs in runs.items()}
        self.relocs = set()
        rva, size = pe.dirs[5]
        blob, pos = pe.rd(rva, size) if size else b'', 0
        while pos + 8 <= len(blob):
            page, bsize = struct.unpack_from('<II', blob, pos)
            if bsize < 8:
                break
            for k in range(8, bsize, 2):
                e = struct.unpack_from('<H', blob, pos + k)[0]
                if e >> 12 == 3:
                    self.relocs.add(self.base + page + (e & 0xfff))
            pos += bsize
        self.imports = {}
        for dll, items in pe.import_directory():
            for slot, nm in items:
                self.imports[self.base + slot] = (dll, nm)
