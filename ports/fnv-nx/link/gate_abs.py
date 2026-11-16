#!/usr/bin/env python3
"""gate_abs - no input of the link refers to an absolute symbol (part of the L1 verdict; closes the binutils ILP32-PIE case of docs/TOOLCHAIN.md section 9).

    gate_abs.py --inputs inputs.rsp [--inputs ...] [--object FILE ...] [--elf guest.elf]

The guest is linked at base 0 and moved by the loader: a value that the linker takes for absolute (SHN_ABS) is not moved, and binutils turns such
values into R_AARCH64_P32_RELATIVE entries with the absolute value as addend (a plain "add the slide" would corrupt them).  No absolute symbol
may take part in the link: then every dynamic relocation is an image address and the guest's self-relocation moves all of them.
The linker's command line carries no symbol definition (the recipe's link accepts only the emulation options), so an absolute symbol can come only from an
input object or from the script, and both are in the universe below.
Universe (no sampling): every relocation of every object and of every member of every archive named by --inputs (response files, one quoted
path per line, as the link's response file lists them) or --object, and every symbol DEFINED by those files.  Read from the section headers and symbol tables
(relocs.py): no tool output, no text.
Red items:
  - a relocation whose symbol is SHN_ABS in its own object                                  id `member:section+offset:symbol`
  - a relocation without a symbol (symbol index 0 with a type: the assembler folded an absolute value into the addend), symbol `*ABS*`
  - with --elf: a relocation whose symbol is undefined in its object and defined SHN_ABS in the linked image (a symbol of the script or
    of the linker; the same id)
  - a symbol an input defines as SHN_ABS (not a file or section symbol)                      id `member:def:symbol`
member is `file` for an object and `archive(member)` for an archive member (base names); offsets are 8 hex digits.
Gate file `abs` ($GATE_OUT/abs.json, gatefile.py; full list in $GATE_OUT/abs.ids): universe = relocations + defined symbols;
certified = universe - red.  Counts on stdout.  Exit status 1 when red.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'contract'))
import gatefile
import gateids
import relocs

SHN_UNDEF = 0


def check(paths, absnames):
    """(universe, sorted [red ids]) over the files `paths` (objects and archives); absnames: names SHN_ABS in the linked image (or empty)."""
    uni, red, absnames = 0, [], frozenset(absnames)
    for p in paths:
        for label, d in relocs.files(p):
            rels, syms = relocs.scan(label, d)
            uni += len(rels)
            for sec, off, typ, (name, bind, stype, shndx) in rels:
                if not name and stype == 0 and shndx == 0 and typ:
                    red.append('%s:%s+%08x:*ABS*' % (label, sec, off))
                elif name and stype != relocs.STT_FILE and (shndx == relocs.SHN_ABS or (shndx == SHN_UNDEF and name in absnames)):
                    red.append('%s:%s+%08x:%s' % (label, sec, off, name))
            for name, bind, stype, shndx in syms:
                if name and stype not in (relocs.STT_SECTION, relocs.STT_FILE) and shndx != SHN_UNDEF:
                    uni += 1
                    if shndx == relocs.SHN_ABS:
                        red.append('%s:def:%s' % (label, name))
    return uni, sorted(set(red))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--inputs', action='append', default=[], help='response file of the link (one quoted path per line)')
    ap.add_argument('--object', action='append', default=[])
    ap.add_argument('--elf')
    a = ap.parse_args()
    paths = list(a.object)
    for rsp in a.inputs:
        with open(rsp) as fh:
            paths += [ln.strip().strip('"') for ln in fh if ln.strip()]
    uni, red = check(paths, relocs.abs_names(a.elf) if a.elf else frozenset())
    detail = 'absolute symbols in the link: %d of %d items, %d files' % (len(red), uni, len(paths))
    gatefile.write('abs', uni, uni - len(red), red, detail)
    gateids.write_ids('abs', red)
    print(detail)
    sys.exit(1 if red else 0)


if __name__ == '__main__':
    main()
