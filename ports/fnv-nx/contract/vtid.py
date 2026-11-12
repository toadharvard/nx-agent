#!/usr/bin/env python3
"""vtid - vftable identity by construction (MASTER_PLAN4 R6).

    vtid.py --n1 n1.json --facts facts.json --units units.json --pairs <dir or file from L2> --out vtid.json

R6: in a function that is byte-equal to the exe (N1) a COFF relocation names `??_7C@@6B...@` and the exe holds
the vftable address at the same offset.  n1.py turns that into the map (unit, `??_7` symbol) -> address, a
function by its fixed point.  The native side refers to a sub-vtable by its own symbol (one relocation per
address point; L2's compiler plugin), and L2's pair file gives, from the compiler's own class hierarchy,

    unit <tab> `_ZTV` symbol <tab> address-point offset <tab> `??_7` symbol

so   (unit, _ZTV, offset)  ->  `??_7`  ->  exe vftable address   is a composition of two functions.
Nothing is learned from store order, offsets, slot contents or names.

A row is an error (the stage fails) when the pair names a `??_7` the unit's N1 functions never reference, or
when two rows bind one (unit, _ZTV, offset) to different addresses.  Checks on a bound row: the address is a
vftable of the exe fact base.

Output (store.py kind `vtid`, canonical JSON; exe- and decomp-derived: container and fnv-nx-build only):
  bind   {(unit source, _ZTV symbol, offset): vftable address}
  errors [(unit, _ZTV, offset, reason)]
"""
import argparse, collections, glob, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store


def read_pairs(path):
    files = sorted(glob.glob(os.path.join(path, '**', '*.tsv'), recursive=True)) if os.path.isdir(path) else [path]
    for f in files:
        for ln in open(f):
            p = ln.rstrip('\n').split('\t')
            if len(p) >= 4 and p[0][:1] != '#':
                yield p[0], p[1], int(p[2], 0), p[3]


def bind(n1, facts, pairs, unit_index):
    vt, vft = n1['vt'], facts['vft']
    out, errors = {}, []
    for unit, ztv, off, msvc in pairs:
        key = (unit, ztv, off)
        ui = unit_index.get(unit)
        a = vt.get((ui, msvc))
        if a is None:
            errors.append(key + ('the unit has no N1 relocation naming ' + msvc,))
        elif a not in vft:
            errors.append(key + ('%08x is not a vftable of the exe fact base' % a,))
        elif out.setdefault(key, a) != a:
            errors.append(key + ('two addresses',))
    return out, errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n1', required=True)
    ap.add_argument('--facts', required=True)
    ap.add_argument('--units', required=True)
    ap.add_argument('--pairs', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    n1 = store.load(a.n1, 'n1')
    facts = store.load(a.facts, 'facts')
    unit_index = {un['source']: i for i, un in enumerate(json.load(open(a.units))['units'])}
    out, errors = bind(n1, facts, read_pairs(a.pairs), unit_index)
    store.save(a.out, 'vtid', dict(bind=out, errors=errors))
    c = collections.Counter(e[3].split(' naming ')[0].split(' is not ')[-1] for e in errors)
    print('bound %d; errors %d %s' % (len(out), len(errors), dict(c)))
    sys.exit(1 if errors else 0)


if __name__ == '__main__':
    main()
