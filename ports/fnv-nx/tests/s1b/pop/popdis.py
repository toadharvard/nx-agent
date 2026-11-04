#!/usr/bin/env python3
"""popdis.py ADDR [ADDR..] [--src PATH] - read a function of the pristine MSVC objects for hand-written S1b patches.

Prints the x86 code of the definition at ADDR (hex) with every relocation resolved to its symbol and address, and
for every call the callee's address, its real pop count (`ret N` of its definition, or the @N of a C name) and the
stack words its code reads (popscan's World). Also lists the pop-mismatch rows of popscan's table (--pop) for that
caller. This is what a hand-written XINC1 pop patch is read from (docs/PATCHES.md, "Pop-mismatch families").

Environment: DECOMP (fnv_decomp with build/units.json), S11 (pristine objects in $S11/obj/pristine). The output is
decomp-derived: read it in scratch, never commit it.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import popscan  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('addr', nargs='+')
    ap.add_argument('--src', help='only the definition in this source path')
    ap.add_argument('--pop', help="popscan.py's table: list this caller's rows")
    a = ap.parse_args()
    w = popscan.World(os.environ['DECOMP'], os.environ['S11'])
    for x in a.addr:
        addr = int(x, 16)
        defs = w.defs.get(addr, [])
        if a.src:
            defs = [d for d in defs if w.units[d[0]]['source'] == a.src] or defs
        if not defs:
            print('%08x: no definition' % addr)
            continue
        i, sym = defs[0]
        f = w.func(i, sym)
        print('== %08x u%d %s  %s  (%d definitions)' % (addr, i, w.units[i]['source'], sym, len(defs)))
        if f is None:
            print('   no code in the pristine object')
            continue
        code, rel = f
        syms = w.units[i]['symbols']
        for ins in popscan.CS.disasm(code, 0):
            note = ''
            for k in range(ins.address, ins.address + ins.size):
                if k in rel:
                    nm = rel[k][0]
                    t = syms.get(nm)
                    note = '  ; %s%s' % (nm, ' = ' + t if t else '')
                    if ins.mnemonic == 'call':
                        p = w.target_pops(i, nm)
                        rd = w.reads(int(t, 16)) if t else None
                        note += '  [pops %s, reads %s]' % ('?' if p is None else p, '?' if rd is None else rd)
                    break
            print('  %4x  %-8s %s%s' % (ins.address, ins.mnemonic, ins.op_str, note))
        if a.pop:
            for line in open(a.pop):
                r = line.rstrip('\n').split('\t')
                if len(r) > 10 and r[5].lower().zfill(8) == '%08x' % addr:
                    print('  pop: callee %s off %s decl %s real %s %s donor %s %s' % (r[1], r[7], r[8], r[9], r[10],
                                                                                      r[12], r[13]))


if __name__ == '__main__':
    main()
