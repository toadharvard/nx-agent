#!/usr/bin/env python3
"""abireplay.py - decide a link run's ABI pairs again with the current abi.py / abi2.py, offline (stream S2c-abi).

    abireplay.py <link out dir> <units.json> [--rows <out.tsv>] [--old] [--override <overrides.tsv>]

Reads <out>/abi.tsv (rows the link reported: one per object, undefined symbol and reference declaration) and
<out>/layouts.tsv, parses both MSVC declarations again and runs abi.compare, then abi2.compare with the layouts,
the way `fnvlink.py resolve` does (casts, undecorated C names and the pristine-code ecx reads are not replayed: those
rows keep the verdict the link gave them). It prints the S2c-abi metric: the bridge-gap pairs, i.e. `manual` rows
whose classes include a shape the link has no automatic bridge for (S1b abiclass.py BRIDGE), counted as unique
(source, address, reference) pairs, with the class breakdown. --old prints the same figures for the verdicts in the
file, --rows writes every replayed row (old verdict, new verdict, classes). --override (the table
tools/link/overrides/overrides.tsv): references to an overridden address are compared with the override's MSVC
signature (4th column), as resolve does. --tree: undecorated C names get their declarations from the prepared tree
(fnvlink.c_name_sig / c_name_standin), so those rows are replayed too.

Everything read and written is decomp-derived: scratch only, never committed. Takes seconds, not a link (D17).
"""
import argparse
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import abi    # noqa: E402
import abi2   # noqa: E402
import layout  # noqa: E402
import sigs   # noqa: E402

BRIDGE = {'varargs', 'aggregate-param', 'ret-aggregate', 'arity', 'sret-explicit', 'reg-shift'}
REPLAYED_NOT = {'cast-call', 'cast-conflict', 'ecx-unused'}


def classes_of(text):
    return set(re.sub(r'\([^)]*\)', '', text).split(',')) - {''}


def metric(rows):
    """rows: [(src, addr, ref, kind, classes)] -> (pairs, Counter of classes over pairs)."""
    pairs, cl = {}, collections.Counter()
    for src, addr, ref, kind, c in rows:
        if kind == 'manual' and classes_of(c) & BRIDGE:
            pairs.setdefault((src, addr, ref), classes_of(c))
    for c in pairs.values():
        cl.update(c)
    return len(pairs), cl


def fnvlink_demangle(name, _cache={}):
    if name not in _cache:
        import subprocess
        _cache[name] = subprocess.run(['llvm-undname', name], capture_output=True, text=True).stdout.strip().splitlines()[-1]
    return _cache[name]


TREE, DECLS = None, {}


def replay(ref_m, def_m, ref_d, def_d, src, dunit, lay):
    rs, ds = sigs.parse_msvc(ref_m, ref_d or None), sigs.parse_msvc(def_m, def_d or None)
    if TREE:   # undecorated C names: their declarations in the prepared tree, as resolve reads them
        import fnvlink
        rc_ = rs.kind == 'cname' and rs.special is None
        dc_ = ds.kind == 'cname' and ds.special is None and dunit
        if rc_:
            rs = fnvlink.c_name_sig(rs, fnvlink.unit_c_decls(TREE, src, DECLS)) or rs
        if dc_:
            ds = fnvlink.c_name_sig(ds, fnvlink.unit_c_decls(TREE, dunit, DECLS)) or ds
        if rc_ and rs.kind != 'func':
            rs = fnvlink.c_name_standin(rs, ds) or rs
        if dc_ and ds.kind != 'func':
            ds = fnvlink.c_name_standin(ds, rs) or ds
    if rs.kind != 'func' or ds.kind != 'func':
        return None
    v = abi.compare(rs, ds)
    if v.kind != 'same' and dunit is not None:
        p2 = abi2.compare(rs, ds, lambda t: lay.get((src, t)), lambda t: lay.get((dunit, t)))
        if p2 is not None and (v.kind != 'bridge' or p2.kind != 'bridge') and \
                not (v.kind == 'bridge' and 'narrow-extend' in v.classes):
            return p2.kind, ','.join(p2.classes)
    return v.kind, ','.join(v.classes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out')
    ap.add_argument('units')
    ap.add_argument('--rows')
    ap.add_argument('--old', action='store_true')
    ap.add_argument('--override')
    ap.add_argument('--tree', help='the prepared tree the link used: undecorated C names are replayed too')
    a = ap.parse_args()
    global TREE
    TREE = a.tree
    units = json.load(open(a.units))['units']
    def_unit = {}
    for u in units:
        for f in u['functions']:
            def_unit.setdefault(int(f['address'], 16), u['source'])
    over = {}
    if a.override:
        for line in open(a.override):
            f = line.rstrip('\n').split('\t')
            if line.startswith('#') or len(f) < 4 or not f[3]:
                continue
            over[int(f[0], 16)] = f[3]
    lay = {k: v for k, v in layout.load(os.path.join(a.out, 'layouts.tsv')).items() if v is not None}
    old, new, out = [], [], []
    for line in open(os.path.join(a.out, 'abi.tsv'), encoding='utf-8', errors='surrogateescape'):
        if line.startswith('#'):
            continue
        f = line.rstrip('\n').split('\t')
        if len(f) < 11:
            f += [''] * (11 - len(f))
        src = f[0].replace('#vtables', '')
        key = (src, f[2], f[3])
        old.append(key + (f[5], f[6]))
        r = None
        if not (classes_of(f[6]) & REPLAYED_NOT):
            try:
                ad = int(f[2], 16)
                if ad in over:   # the override's own signature; no unit, so no layouts (resolve: nd['unit'] None)
                    dm = over[ad]
                    dd = fnvlink_demangle(dm)
                    r = replay(f[3], dm, f[9], dd, src, None, lay)
                else:
                    r = replay(f[3], f[4], f[9], f[10], src, def_unit.get(ad), lay)
            except Exception as e:   # a declaration the parser rejects: keep the link's verdict
                print('replay failed: %s %s: %s' % (f[2], f[3], e), file=sys.stderr)
        kind, cl = r if r is not None else (f[5], f[6])
        new.append(key + (kind, cl))
        out.append((f[0], f[2], f[3], f[5], f[6], kind, cl))
    for name, rows in (('old', old), ('new', new)) if a.old else (('new', new),):
        n, cl = metric(rows)
        print('%s: bridge-gap pairs %d; %s' % (name, n, ', '.join('%s %d' % kv for kv in cl.most_common())))
        kinds = collections.Counter(r[3] for r in rows)
        print('%s: rows %s' % (name, ', '.join('%s %d' % kv for kv in sorted(kinds.items()))))
    if a.rows:
        with open(a.rows, 'w', encoding='utf-8', errors='surrogateescape') as fo:
            fo.write('#object\taddress\tref_msvc\told verdict\told classes\tverdict\tclasses\n')
            for r in out:
                fo.write('\t'.join(r) + '\n')


if __name__ == '__main__':
    main()
