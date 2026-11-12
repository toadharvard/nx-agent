#!/usr/bin/env python3
"""flagdiff.py - compare builds of the same units compiled with different flags (stream S15-perf).

    flagdiff.py <root> [--base base] [--variants a,b,...] [--top N]

<root>/<variant>/<unit>.o as written by a variants driver (one directory per flag set, same object names). For
every variant: static instructions over the functions present in both builds, GOT loads, calls (and of those the
stack-protector, outline-atomic and bridge calls), and the functions that changed most. Static counts only; the
dynamic side is the measured instruction count.
"""
import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import objcensus  # noqa: E402


def load(path):
    """{function: Counter(insns, got, calls, prot, atom, bridge)} for one object"""
    o = objcensus.Obj(path)
    call26 = (objcensus.LP64_CALL26, objcensus.LP64_JUMP26) if o.is64 else (objcensus.R_CALL26, objcensus.R_JUMP26)
    gotlo = objcensus.LP64_GOT_LO12 if o.is64 else objcensus.R_GOT_LO12
    out = {}
    for sh in o.text_sections():
        fl = o.funcs(sh)
        starts = [f[0] for f in fl]
        for v, sz, n in fl:
            out.setdefault(n, Counter())['insns'] += sz // 4
        for off, typ, nm in o.relocs.get(sh, ()):
            k = objcensus._owner(starts, off)
            if k is None:
                continue
            c = out[fl[k][2]]
            if typ in call26:
                c['calls'] += 1
                cl = objcensus.call_class(nm)
                if cl == 'stack_chk_fail':
                    c['prot'] += 1
                elif cl == 'outline_atomic':
                    c['atom'] += 1
                elif cl == 'bridge':
                    c['bridge'] += 1
            elif typ == gotlo:
                c['got'] += 1
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('root')
    ap.add_argument('--base', default='base')
    ap.add_argument('--variants')
    ap.add_argument('--top', type=int, default=0)
    a = ap.parse_args()
    vs = a.variants.split(',') if a.variants else sorted(d for d in os.listdir(a.root)
                                                       if os.path.isdir(os.path.join(a.root, d)) and d != a.base)
    bdir = os.path.join(a.root, a.base)
    objs = sorted(n for n in os.listdir(bdir) if n.endswith('.o'))
    base = {n: load(os.path.join(bdir, n)) for n in objs}
    btot = Counter()
    for n in objs:
        for f, c in base[n].items():
            btot.update(c)
    print('%-9s %9s %8s %7s %7s %6s %6s %6s  per unit insns delta' % ('variant', 'insns', 'delta', 'got', 'calls',
                                                                      'prot', 'atom', 'funcs'))
    print('%-9s %9d %8s %7d %7d %6d %6d %6d' % (a.base, btot['insns'], '', btot['got'], btot['calls'], btot['prot'],
                                                btot['atom'], sum(len(base[n]) for n in objs)))
    for v in vs:
        tot, btot_common, per_unit, changed = Counter(), Counter(), [], []
        for n in objs:
            p = os.path.join(a.root, v, n)
            if not os.path.exists(p):
                continue
            var = load(p)
            ui_b = ui_v = 0
            for f, cb in base[n].items():
                cv = var.get(f)
                if cv is None:                 # inlined away or renamed in this variant: counts as 0 there
                    cv = Counter()
                tot.update(cv)
                btot_common.update(cb)
                ui_b += cb['insns']
                ui_v += cv['insns']
                if cb['insns'] != cv['insns']:
                    changed.append((cv['insns'] - cb['insns'], cb['insns'], cv['insns'], f, n))
            for f, cv in var.items():           # functions only this variant emits (out-of-line copies)
                if f not in base[n]:
                    tot.update(cv)
                    ui_v += cv['insns']
            per_unit.append('%s %+.1f%%' % (n.split('_')[-1].replace('.cpp.o', ''),
                                            100.0 * (ui_v - ui_b) / max(ui_b, 1)))
        d = 100.0 * (tot['insns'] - btot_common['insns']) / max(btot_common['insns'], 1)
        print('%-9s %9d %+7.2f%% %7d %7d %6d %6d %6s  %s' % (v, tot['insns'], d, tot['got'], tot['calls'],
                                                            tot['prot'], tot['atom'], '', ', '.join(per_unit)))
        if a.top:
            for dlt, b, vv, f, n in sorted(changed, key=lambda x: -abs(x[0]))[:a.top]:
                print('    %+5d  %5d -> %5d  %s  (%s)' % (dlt, b, vv, f[:90], n))


if __name__ == '__main__':
    main()
