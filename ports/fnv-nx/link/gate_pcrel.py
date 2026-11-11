#!/usr/bin/env python3
"""gate_pcrel - the guest is position independent everywhere (S7 of the loader rules; part of the L1 verdict).

    gate_pcrel.py --elf <linked guest elf> [--inputs inputs.rsp ...] [--object FILE ...]

A PC-relative relocation (adrp/adr/literal load/branch) whose symbol is absolute in the linked ELF is right only at the link base: a relocated
guest moves the PC and not the target.  An image address must be reached through the GOT (relocated by value) or as an immediate.
Universe (no sampling): every PC-relative relocation (AArch64 ILP32 types ADR_PREL_LO21 10, ADR_PREL_PG_HI21 11, LD_PREL_LO19 9, TSTBR14 18,
CONDBR19 19, JUMP26 20, CALL26 21) of every object and archive member of the files the link consumed: the files of each `--inputs`
response file (ld.py's inputs.rsp, one quoted path per line; every member of an archive) and every `--object`.  Read from the relocation
sections and symbol tables (relocs.py), not from tool output.
A relocation is an offending site when the name of its symbol is a symbol of the linked ELF defined SHN_ABS (the symbol table of --elf), or
when its symbol is absolute in its own object (SHN_ABS) or absent (symbol index 0 with a relocation type: the value is folded into the addend).
Gate: 0 sites.  A "pinned" guest (one that may only run at its link base) is not a state the clean branch holds.  Exit status 1 otherwise.
Gate file `pcrel` ($GATE_OUT/pcrel.json, gatefile.py; all ids in $GATE_OUT/pcrel.ids): universe = the PC-relative relocations examined,
certified = examined - offending sites, uncertified = `member:section+offset:symbol` (offset as 8 hex digits; `*ABS*` for no symbol).
Counts only on stdout.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'contract'))
import gatefile
import gateids
import relocs

PCREL = (9, 10, 11, 18, 19, 20, 21)


def check(paths, absnames):
    """(examined, [offending site ids]) over the files `paths`."""
    examined, ids = 0, []
    for p in paths:
        for label, d in relocs.files(p):
            for sec, off, typ, (name, bind, stype, shndx) in relocs.scan(label, d)[0]:
                if typ not in PCREL:
                    continue
                examined += 1
                if not name and stype == 0 and shndx == 0 and typ:
                    ids.append('%s:%s+%08x:*ABS*' % (label, sec, off))
                elif name and bind != 0 and name in absnames or shndx == relocs.SHN_ABS:
                    ids.append('%s:%s+%08x:%s' % (label, sec, off, name))
    return examined, ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--elf', required=True)
    ap.add_argument('--inputs', action='append', default=[], help='response file of ld.py (one quoted path per line)')
    ap.add_argument('--object', action='append', default=[], help='one more linked object or archive')
    a = ap.parse_args()
    paths = list(a.object)
    for rsp in a.inputs:
        with open(rsp) as fh:
            paths += [ln.strip().strip('"') for ln in fh if ln.strip()]
    if not paths:
        sys.exit('gate_pcrel: no input files (--inputs or --object)')
    examined, ids = check(paths, relocs.abs_names(a.elf))
    detail = 'pc-relative references to absolute symbols: %d; examined %d in %d files' % (len(ids), examined, len(paths))
    gatefile.write('pcrel', examined, examined - len(ids), ids, detail)
    gateids.write_ids('pcrel', ids)
    print(detail)
    sys.exit(1 if ids else 0)


if __name__ == '__main__':
    main()
