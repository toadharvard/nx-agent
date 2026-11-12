#!/usr/bin/env python3
"""Count the locked instructions of an image's universe by what is statically known of the operand's alignment
(docs/ORDERING.md "Lifted code", assumption LOCK-SPLIT).
    python3 -I locksites.py IMAGE RANGES [BASE]      RANGES: ranges.tsv of tools/universe.py (entry, half-open instruction ranges);
                                                    BASE (hex): the base the ranges are stated at, when not the image's preferred one
                                                    (a page multiple apart, so the alignment of a constant address is the same)
A locked instruction: a valid LOCK prefix (fnvlift/f_sys.py lock_ok) or XCHG with a memory operand. Classes:
  byte      1-byte operand: aligned by construction
  const     the operand is an absolute address (no base, no index): aligned iff address mod size == 0 (const-misaligned otherwise)
  unproven  the address has a register: its alignment is a run-time fact (rt/fnvl.h LKA tests it at every execution)
Prints one line of counts. Nothing is decided from this count; it states the size of the assumption's domain."""
import os
import sys

import capstone
from capstone import x86 as X

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main(exe, ranges, base=None):
    import x86flow
    from fnvlift import f_sys
    img = x86flow.Image(exe)
    d = int(base, 16) - img.base if base else 0
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); md.detail = True
    spans = set()
    for l in open(ranges):
        if l[0] == '#': continue
        for r in l.split('\t')[1].replace(',', ' ').split():
            lo, hi = (int(x, 16) for x in r.split('-')); spans.add((lo, hi))
    n = {}; seen = set()
    for lo, hi in sorted(spans):
        for ins in md.disasm(img.bytes(lo - d, hi - lo), lo):
            if ins.address in seen: continue
            seen.add(ins.address)
            w = ins.mnemonic.split(' '); m = w[-1]
            pre = 'lock' in w[:-1] and f_sys.lock_ok(ins, m)
            if not f_sys.locked(ins, m, pre): continue
            op = [o for o in ins.operands if o.type == X.X86_OP_MEM][0]
            k = 'byte' if op.size == 1 else 'unproven' if op.mem.base or op.mem.index else 'const' if (op.mem.disp & 0xffffffff) % op.size == 0 else 'const-misaligned'
            for key in ('total', k, ('lock ' if pre else '') + m): n[key] = n.get(key, 0) + 1
    print(', '.join('%s %d' % kv for kv in sorted(n.items())))


if __name__ == '__main__':
    main(*sys.argv[1:])
