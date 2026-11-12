#!/usr/bin/env python3
"""Count the locked instructions of an image's universe by what is statically known of the operand's alignment
(docs/ORDERING.md "Lifted code", ASM-231 LOCK-SPLIT).
    python3 -I locksites.py --sites SETFILE OUT       the `sites` facts of gate_order (docs/ORDERING.md): SETFILE lines IMAGE<TAB>RANGES<TAB>BASE, one per image; OUT lines `1 HEXADDR` (locked
                                                    instruction), `2 HEXADDR mem|ax` (FSTSW FNSTENV FNSAVE); the decoder predicate is fnvlift/sites.py, the lifter's own
    python3 -I locksites.py IMAGE RANGES [BASE]      RANGES: ranges.tsv of tools/universe.py (entry, half-open instruction ranges);
                                                    BASE (hex): the base the ranges are stated at, when not the image's preferred one
                                                    (a page multiple apart, so the alignment of a constant address is the same)
A locked instruction: a valid LOCK prefix (fnvlift/f_sys.py lock_ok) or XCHG with a memory operand. Classes:
  byte      1-byte operand: aligned by construction
  const     the operand is an absolute address (no base, no index): aligned iff address mod size == 0 (const-misaligned otherwise)
  unproven  the address has a register: its alignment is a run-time fact (rt/fnvl.h LKA tests it at every execution)
Prints one line of counts. Nothing is decided from this count; it states the size of the domain of ASM-231."""
import os
import sys

import capstone
from capstone import x86 as X

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def scan(exe, ranges, base=None):
    """-> [(address, site)] of every instruction of the ranges, in address order; site is fnvlift.sites.site(ins): (1,) a locked instruction, (2, 'mem'|'ax') a status-word instruction. The address is
    stated at BASE (the ranges' base). One decoder predicate for the lifter and for these facts: fnvlift/sites.py."""
    import x86flow
    from fnvlift import sites
    img = x86flow.Image(exe)
    d = int(base, 16) - img.base if base else 0
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); md.detail = True
    spans = set()
    for l in open(ranges):
        if l[0] == '#': continue
        for r in l.split('\t')[1].replace(',', ' ').split():
            lo, hi = (int(x, 16) for x in r.split('-')); spans.add((lo, hi))
    out = {}
    for lo, hi in sorted(spans):
        for ins in md.disasm(img.bytes(lo - d, hi - lo), lo):
            k = sites.site(ins)
            if k:
                out[ins.address] = (ins, k)
    return [(a, out[a]) for a in sorted(out)]


def lines(found):
    """the `sites` facts lines of gate_order (docs/ORDERING.md): `1 HEXADDR` per locked instruction, `2 HEXADDR mem|ax` per status-word instruction"""
    return ['%d %08x%s' % (k[0], a, ' ' + k[1] if k[0] == 2 else '') for a, (ins, k) in found]


def main(exe, ranges, base=None):
    from fnvlift import f_sys
    n = {}
    for a, (ins, k) in scan(exe, ranges, base):
        if k[0] == 2:
            for key in ('fsw', 'fsw ' + k[1]): n[key] = n.get(key, 0) + 1
            continue
        w = ins.mnemonic.split(' '); m = w[-1]
        pre = 'lock' in w[:-1]
        op = [o for o in ins.operands if o.type == X.X86_OP_MEM][0]
        kk = 'byte' if op.size == 1 else 'unproven' if op.mem.base or op.mem.index else 'const' if (op.mem.disp & 0xffffffff) % op.size == 0 else 'const-misaligned'
        for key in ('total', kk, ('lock ' if pre else '') + m): n[key] = n.get(key, 0) + 1
    print(', '.join('%s %d' % kv for kv in sorted(n.items())))


def facts(setfile, out):
    """SETFILE: lines `IMAGE<TAB>RANGES<TAB>BASE` (BASE may be empty), one per image of the set. Writes OUT (the facts file `sites`: the lines of every image, in the set's order, each image's by address)
    and prints the global counts."""
    rows = []
    for l in open(setfile):
        if l.strip() and l[0] != '#':
            c = l.rstrip('\n').split('\t'); rows += lines(scan(c[0], c[1], c[2] if len(c) > 2 and c[2] else None))
    open(out, 'w').write('\n'.join(rows) + '\n')
    print('sites: kind 1 %d, kind 2 %d (mem %d, ax %d), lines %d' % (sum(r[0] == '1' for r in rows), sum(r[0] == '2' for r in rows), sum(r.endswith(' mem') for r in rows), sum(r.endswith(' ax') for r in rows), len(rows)))


if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cli
    U = 'usage: locksites.py EXE RANGES [BASE]   lock-prefix census of the image\n       locksites.py --sites SETFILE OUT   the .fnvl.lk/.fnvl.fsw site facts (SETFILE lines: IMAGE<TAB>RANGES<TAB>BASE)'
    pos, o = cli.parse(U, sys.argv[1:], (2, 3), {'--sites': False})
    if o and len(pos) != 2: cli.parse(U, [], (2, 2))
    if o: facts(*pos)
    elif len(pos) < 2 or len(pos) > 3: cli.parse(U, [], (2, 3))
    else: main(*pos)
