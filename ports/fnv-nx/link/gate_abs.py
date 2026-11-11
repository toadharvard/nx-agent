#!/usr/bin/env python3
"""gate_abs - no input of the link refers to an absolute symbol (part of the L1 verdict; closes the binutils ILP32-PIE case of docs/TOOLCHAIN.md section 9).

    gate_abs.py --inputs inputs.rsp [--inputs ...] [--object FILE ...] [--ld-arg A ...] [--elf guest.elf]

The guest is linked at base 0 and moved by the loader: a value that the linker takes for absolute (SHN_ABS) is not moved, and binutils turns such
values into R_AARCH64_P32_RELATIVE entries with the absolute value as addend (a plain "add the slide" would corrupt them).  No absolute symbol
may take part in the link: then every dynamic relocation is an image address and the guest's self-relocation moves all of them.
Universe (no sampling): every relocation of every object and of every member of every archive named by --inputs (response files, one quoted
path per line, as ld.py writes them) or --object; every symbol DEFINED by those inputs; every --ld-arg.
Red items:
  - a relocation whose symbol is SHN_ABS in its own object                         id `member:section+offset:symbol`
  - a relocation whose symbol is undefined in its object and SHN_ABS in --elf (the symbol of a script or a --defsym), same id
  - a symbol an input defines as SHN_ABS (an assembler `.set` constant, not a file or section symbol)   id `member:def:symbol`
  - an --ld-arg that defines a symbol (`--defsym`, `-defsym`)                       id `ld-arg:<argument>`
member is `file` for an object and `archive(member)` for an archive member (base names).
Gate file `abs` ($GATE_OUT/abs.json, gatefile.py; full list in $GATE_OUT/abs.ids): universe = relocations + defined symbols + ld args;
certified = universe - red.  Counts on stdout.  Exit status 1 when red.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'contract'))
import gatefile
import gateids
import ld

SHT_SYMTAB, SHT_RELA, SHT_REL, SHN_UNDEF, SHN_ABS, STT_SECTION, STT_FILE = 2, 4, 9, 0, 0xfff1, 3, 4


def sections(d):
    shoff, = struct.unpack_from('<I', d, 32)
    shentsize, shnum, shstrndx = struct.unpack_from('<HHH', d, 46)
    sh = [struct.unpack_from('<IIIIIIIIII', d, shoff + i * shentsize) for i in range(shnum)]
    return sh, shstrndx


def cstr(d, off):
    return d[off:d.index(b'\0', off)].decode()


def scan(label, d, absnames):
    """(relocations, defined symbols, [red ids]) of one ELF32 relocatable image; absnames: names SHN_ABS in the linked ELF (or empty)."""
    if d[:4] != b'\x7fELF' or d[4] != 1 or d[5] != 1:
        raise ValueError('%s: not ELF32 little-endian' % label)
    sh, shstrndx = sections(d)
    names = [cstr(d, sh[shstrndx][4] + s[0]) for s in sh]
    syms = {}
    for i, s in enumerate(sh):
        if s[1] == SHT_SYMTAB:
            stroff = sh[s[6]][4]
            tab = []
            for o in range(s[4], s[4] + s[5], s[9]):
                name, _, _, info, _, shndx = struct.unpack_from('<IIIBBH', d, o)
                tab.append((cstr(d, stroff + name), info & 15, shndx))
            syms[i] = tab
    nrel, ndef, red = 0, 0, []
    for tab in syms.values():
        for name, typ, shndx in tab:
            if name and typ not in (STT_SECTION, STT_FILE) and shndx != SHN_UNDEF:
                ndef += 1
                if shndx == SHN_ABS:
                    red.append('%s:def:%s' % (label, name))
    for s in sh:
        if s[1] in (SHT_RELA, SHT_REL):
            tab = syms[s[6]]
            ent = 12 if s[1] == SHT_RELA else 8
            for o in range(s[4], s[4] + s[5], ent):
                off, info = struct.unpack_from('<II', d, o)
                nrel += 1
                name, typ, shndx = tab[info >> 8] if info >> 8 < len(tab) else ('', 0, 0)
                if info >> 8 and (shndx == SHN_ABS or (shndx == SHN_UNDEF and name in absnames)) and typ != STT_FILE:
                    red.append('%s:%s+%x:%s' % (label, names[s[7]], off, name))
    return nrel, ndef, red


def abs_names(path):
    """The names SHN_ABS in the linked ELF (sorted), except file and section symbols."""
    with open(path, 'rb') as fh:
        d = fh.read()
    sh, _ = sections(d)
    out = []
    for s in sh:
        if s[1] == SHT_SYMTAB:
            stroff = sh[s[6]][4]
            for o in range(s[4], s[4] + s[5], s[9]):
                name, _, _, info, _, shndx = struct.unpack_from('<IIIBBH', d, o)
                if shndx == SHN_ABS and (info & 15) not in (STT_SECTION, STT_FILE) and name:
                    out.append(cstr(d, stroff + name))
    return sorted(set(out))


def labels(path):
    """[(label, bytes)] of a file: itself or every member of an archive, labelled with base names."""
    b = os.path.basename(path)
    with open(path, 'rb') as fh:
        d = fh.read()
    if d[:8] != b'!<arch>\n':
        return [(b, d)]
    return [('%s(%s)' % (b, n), body) for n, _, body in ld.ar_members(path)]


def defsym_arg(a):
    return a.split('=')[0].lstrip('-') == 'defsym' or a.lstrip('-').startswith('defsym')


def check(paths, ldargs, absnames):
    """(universe, [red ids]) over the files `paths` (objects and archives) and the linker arguments."""
    uni, red = 0, []
    for p in paths:
        for label, d in labels(p):
            r, df, bad = scan(label, d, absnames)
            uni += r + df
            red += bad
    for a in ldargs:
        uni += 1
        if defsym_arg(a):
            red.append('ld-arg:%s' % a)
    return uni, sorted(set(red))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--inputs', action='append', default=[], help='response file of ld.py (one quoted path per line)')
    ap.add_argument('--object', action='append', default=[])
    ap.add_argument('--ld-arg', action='append', default=[])
    ap.add_argument('--elf')
    a = ap.parse_args()
    paths = list(a.object)
    for rsp in a.inputs:
        with open(rsp) as fh:
            paths += [ln.strip().strip('"') for ln in fh if ln.strip()]
    uni, red = check(paths, a.ld_arg, abs_names(a.elf) if a.elf else [])
    detail = 'absolute symbols in the link: %d of %d items, %d files' % (len(red), uni, len(paths))
    gatefile.write('abs', uni, uni - len(red), red, detail)
    gateids.write_ids('abs', red)
    print(detail)
    sys.exit(1 if red else 0)


if __name__ == '__main__':
    main()
