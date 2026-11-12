"""relocs - the relocations and symbols of ELF32 little-endian AArch64 relocatable objects and archive members, from the section
headers (structure only: no tool output, no text).

    scan(label, bytes, with_relocs=True) -> (relocations [(section, offset, type, symbol)], symbols [symbol])
    symbol = (name, bind, type, shndx), the entry of the symbol table the relocation names (index 0: ('', 0, 0, 0))
    files(path) -> [(label, bytes)]  an object itself, or every member of an archive (label `archive(member)`, base names)

A relocation section is a SHT_RELA or SHT_REL section; `section` is the name of the section it applies to (sh_info), `type` the low byte of r_info
(ELF32: type 8 bits, symbol 24 bits).  Both gates `abs` and `pcrel` read objects through this module.
"""
import os
import struct

import ld

SHT_SYMTAB, SHT_RELA, SHT_REL = 2, 4, 9
SHN_ABS = 0xfff1
STT_SECTION, STT_FILE = 3, 4


def cstr(d, off):
    return d[off:d.index(b'\0', off)].decode()


def scan(label, d, with_relocs=True):
    if d[:4] != b'\x7fELF' or d[4] != 1 or d[5] != 1:
        raise ValueError('%s: not ELF32 little-endian' % label)
    shoff, = struct.unpack_from('<I', d, 32)
    shentsize, shnum, shstrndx = struct.unpack_from('<HHH', d, 46)
    sh = [struct.unpack_from('<IIIIIIIIII', d, shoff + i * shentsize) for i in range(shnum)]
    names = [cstr(d, sh[shstrndx][4] + s[0]) for s in sh]
    tabs = {}
    for i, s in enumerate(sh):
        if s[1] == SHT_SYMTAB:
            stroff = sh[s[6]][4]
            tab = []
            for o in range(s[4], s[4] + s[5], s[9]):
                name, _, _, info, _, shndx = struct.unpack_from('<IIIBBH', d, o)
                tab.append((cstr(d, stroff + name), info >> 4, info & 15, shndx))
            tabs[i] = tab
    rels, syms = [], []
    for tab in tabs.values():
        syms += tab
    for s in sh:
        if with_relocs and s[1] in (SHT_RELA, SHT_REL):
            tab = tabs[s[6]]
            for o in range(s[4], s[4] + s[5], 12 if s[1] == SHT_RELA else 8):
                off, info = struct.unpack_from('<II', d, o)
                k = info >> 8
                if k >= len(tab):
                    raise ValueError('%s: a relocation names symbol %d of %d' % (label, k, len(tab)))
                rels.append((names[s[7]], off, info & 0xff, tab[k] if k else ('', 0, 0, 0)))
    return rels, syms


def files(path):
    b = os.path.basename(path)
    with open(path, 'rb') as fh:
        d = fh.read()
    if d[:8] != b'!<arch>\n':
        return [(b, d)]
    return [('%s(%s)' % (b, n), body) for n, _, body in ld.ar_members(path)]


def abs_names(path):
    """The names defined SHN_ABS in the symbol table of a linked image (no file or section symbols), as a frozenset."""
    with open(path, 'rb') as fh:
        d = fh.read()
    return frozenset(n for n, _, t, shndx in scan(path, d, False)[1] if n and shndx == SHN_ABS and t not in (STT_SECTION, STT_FILE))
