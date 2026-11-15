#!/usr/bin/env python3
"""cleared_join: join the cpufeat-#UD census of def_check (instructions whose required CPUID feature is absent in the reference CPU, fnvlift/f_cpufeat.py) with L3b's cleared_sites.tsv
(fnv-nx-build l3b-lift/sdm-partition/..., module:address, shape, form, reason), per site.
    cleared_join.py CLEARED.tsv IMAGE FACTS [IMAGE FACTS ...]  -> prints the sets and the symmetric difference, one line per site only in one set: SIDE module:addr mnemonic reason
Sites are those of the decode of def_check.universe (every range of the facts); module = the image's file name without extension."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'gates')); sys.path.insert(0, os.path.join(HERE, '..'))
import capstone
from capstone import x86 as X
import gate_flags as g
import def_check
from proof import shape


def ours(pairs):
    out = {}
    from fnvlift import f_cpufeat
    for pe, facts in pairs:
        mod = os.path.splitext(os.path.basename(pe))[0]
        for ad, i in sorted(def_check.instructions(pe, facts).items()):
            try: shape.shape_key(i)
            except shape.ShapeError: continue
            if def_check.lifted_by(i) == 'cpufeat-UD':
                gs = f_cpufeat.gates(i)
                out['%s:%08x' % (mod, ad)] = (i.mnemonic, ';'.join(x[0] for x in gs), i.bytes.hex())
    return out


def theirs(p):
    out = {}
    for l in open(p):
        if l[0] == '#' or not l.strip(): continue
        f = l.rstrip('\n').split('\t'); out[f[0]] = (f[1], f[2], f[3])
    return out


def main(a):
    o = ours(list(zip(a[1::2], a[2::2]))); t = theirs(a[0])
    print('ours %d theirs %d both %d' % (len(o), len(t), len(set(o) & set(t))))
    for k in sorted(set(o) - set(t)): print('OURS-ONLY', k, o[k][0], o[k][1], o[k][2])
    for k in sorted(set(t) - set(o)): print('THEIRS-ONLY', k, '|'.join(t[k]))
    return 0


if __name__ == '__main__': sys.exit(main(sys.argv[1:]))
