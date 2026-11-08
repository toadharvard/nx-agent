#!/usr/bin/env python3
"""End-to-end test of the abi2 bridge for vtable slots (fnvlink exe_slot_fill, target_for's abi branch):
hand-written classes and a synthetic exe (test_exevft.make_pe), no game content.

    python3 tools/link/test_slotabi2.py     # needs aarch64-linux-gnu-{g++,as,ld,objcopy}, llvm-undname, SSE2NEON

Vq has the virtuals f(P3) (P3 = three floats by value), g(int *), h(int *); the exe's vftable holds
  Shell::impl5(float, float, float)  - the same x86 stack image as P3: an abi2 bridge (or 'same');
  Shell::impl6(Q3)                   - a 12-byte struct by value where g passes a pointer: stays a trap, the gap row
                                       carries abi2's reason;
  Shell::impl7(int *)                - plain: no abi2 involved;
  Shell::impl8(R3)                   - k(P3) against three ints by value: HFA in s0-s2 against x0/x1, a real bridge.
The layouts come from the real layout probe (resolve, probe, resolve again).
"""
import json
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import layout          # noqa: E402
import test_exevft     # noqa: E402
import test_slotfill as tsf   # noqa: E402

HEADER = r'''#pragma once
struct P3 { float x, y, z; };
struct Q3 { int a, b, c; };
struct R3 { int a, b, c; };
struct Vq { virtual int f(P3); virtual int g(int *); virtual int h(int *); virtual int k(P3); int q; };
struct Shell { int impl5(float, float, float); int impl6(Q3); int impl7(int *); int impl8(R3); int y; };
'''
SRC = ('#include "h.h"\nint Shell::impl5(float a, float b, float c) { return (int)(a + b + c); }\n'
       'int Shell::impl6(Q3 p) { return p.a; }\nint Shell::impl7(int *p) { return *p; }\nint Shell::impl8(R3 p) { return p.b; }\n'
       'Vq *mk_vq() { return new Vq; }\n')


def main():
    os.environ['FNVNX_VTFILL'] = '0'
    run, check, F = tsf.run, tsf.check, tsf.F
    with tempfile.TemporaryDirectory() as t:
        t = os.environ.get("FNVNX_TEST_KEEP") or t
        where = {}

        def build(rd):
            td = rd.put(struct.pack('<II', 0x0101d000, 0) + b'.?AVVq@@\0')
            bs = [rd.words(td, 0, 0, 0xffffffff, 0, 0x40, 0)]
            chd = rd.words(0, 0, 1, rd.words(*bs))
            col = rd.words(0, 0, 0, td, chd)
            rd.words(col)
            where['Vq'] = rd.here()
            rd.words(0x401000, 0x401008, 0x401010, 0x401018)
        exe = os.path.join(t, 'Synthetic.exe')
        test_exevft.make_pe(exe, build, {})
        tree = os.path.join(t, 'tree')
        bd = os.path.join(tree, 'build')   # resolve takes the tree from <tree>/build/units.json
        os.makedirs(bd)
        os.makedirs(os.path.join(tree, 'include'))
        open(os.path.join(tree, 'include', 'h.h'), 'w').write(HEADER)
        fl = [f if f != '-O2' else '-O1' for f in layout.flags(tree)] + ['-fno-inline']
        p = os.path.join(tree, 'src', 'a.cpp')
        os.makedirs(os.path.dirname(p))
        open(p, 'w').write(SRC)
        obj = os.path.join(t, 'src_a.cpp.o')
        run([tsf.CXX, *fl, p, '-o', obj], cwd=tree)
        units = {'units': [{'source': 'src/a.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
            F(0x401000, '?impl5@Shell@@QAEHMMM@Z'), F(0x401008, '?impl6@Shell@@QAEHUQ3@@@Z'),
            F(0x401010, '?impl7@Shell@@QAEHPAH@Z'), F(0x401018, '?impl8@Shell@@QAEHUR3@@@Z')],
            'symbols': {'??_7Vq@@6B@': '0x%08x' % where['Vq']}}]}
        json.dump(units, open(os.path.join(bd, 'units.json'), 'w'))
        with open(os.path.join(bd, 'objects.tsv'), 'w') as f:
            f.write('src/a.cpp\t%s\tok\n' % obj)
        out, fixed = os.path.join(t, 'out'), os.path.join(t, 'fixed')

        def resolve():
            run([sys.executable, tsf.FNVLINK, 'resolve', os.path.join(bd, 'units.json'),
                 os.path.join(bd, 'objects.tsv'), out, '--imports', 'none', '--exe', exe])
        # the pipeline iterates: the vtables the first probe synthesizes make the slot fill ask for the layouts
        for _ in range(3):
            resolve()
            run([sys.executable, tsf.FNVLINK, 'probe', out, tree])
        resolve()
        run([sys.executable, tsf.FNVLINK, 'apply', out, fixed])
        refs = {}
        for line in open(os.path.join(out, 'refs.tsv')):
            f = line.rstrip('\n').split('\t')
            if not line.startswith('#'):
                refs[f[1]] = f
        gaps = {}
        for line in open(os.path.join(out, 'slot_gaps.tsv')):
            if not line.startswith('#'):
                g = line.rstrip('\n').split('\t')
                gaps.setdefault(g[2], []).append(g[7])
        print(open(os.path.join(out, 'slot_fill.tsv')).read())
        print(gaps)
        rf = refs.get('_ZN2Vq1fE2P3')
        check(rf is not None and rf[2] == 'vslot-exe', 'f(P3) bound (bridge or same): %s' % (rf,))
        check('_ZN2Vq1fE2P3' not in gaps, 'f(P3) has no gap row: %s' % gaps.get('_ZN2Vq1fE2P3'))
        rg = refs.get('_ZN2Vq1gEPi')
        check(rg is not None and rg[2] == 'vslot-unresolved', 'g(int *) vs Q3 by value stays a trap: %s' % (rg,))
        gw = gaps.get('_ZN2Vq1gEPi') or []
        check(len(gw) == 1 and gw[0].startswith('abi: ') and '; abi2: ' in gw[0],
              'g: the gap row carries abi2\'s reason: %s' % gw)
        rh = refs.get('_ZN2Vq1hEPi')
        rk = refs.get('_ZN2Vq1kE2P3')
        check(rk is not None and rk[2] == 'vslot-exe' and rk[5].startswith('__fnvnx_br_') and
              any('abi2 bridge' in l for l in open(os.path.join(out, 'slot_fill.tsv'))), 'k(P3) vs R3: abi2 bridge: %s' % (rk,))
        check(rk is not None and rk[5].startswith('__fnvnx_br_00401018'), 'k bound to the generated bridge: %s' % (rk,))
        check(rh is not None and rh[2] == 'vslot-exe', 'h(int *) plain: %s' % (rh,))
    print('%d failed' % len(tsf.FAILS))
    return 1 if tsf.FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
