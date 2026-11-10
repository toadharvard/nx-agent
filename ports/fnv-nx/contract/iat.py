#!/usr/bin/env python3
"""iat - the value the loader writes in every import slot of an image (L1 CONTRACT, loader contract with L3 and L5).

    iat.py --facts facts.json --bridge bridge.tsv [--module name.dll=exports.tsv ...] --out iat.tsv

Image code pointers are not rewritten and `call [slot]` is an ordinary indirect call, so a slot must hold a 32-bit
value the dispatcher of the lifted code maps to an entry.  Rule, total over the image's import directory:

  module    the dll is a lifted PE module: the slot holds the address of the module's export (exports.tsv: name or
            #ordinal <tab> address at the module's load base), as the Windows loader does.
  key       the dll is implemented by the platform (a bridge.tsv row of L5): the slot holds the function's key,
            IMPORT_BASE + STEP * (index of (dll, name) among the non-data rows of bridge.tsv sorted by dll, name).
            One function has one key in every image, so pointer equality behaves as on Windows.
  data      bridge.tsv conv `data`: the slot holds the address of the native object; no key (bound by the loader
            from the ELF symbol).

Keys lie in [IMPORT_BASE, LIMIT), and SENTINEL (the return address under the first guest frame) below them.
Assumption, checked by the loader at start: no mapping of the process intersects [SENTINEL, LIMIT) - the range
is below the lowest address the 32-bit address space hands out (0x00200000), so a key is never a readable or
executable address and never the address of an image, module, heap or stack.

A slot without a provider, an export the module does not have, a dll that is both a module and a bridge dll: the
stage fails and lists them (gate `iat`: universe = slots, certified = slots with a value).  Output rows:
slot VA, dll, name (an import by ordinal is written with the name bridge.tsv gives that ordinal), kind, value
(empty for data).  Exe-derived: container and fnv-nx-build only.
"""
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gatefile, store

SENTINEL, IMPORT_BASE, STEP, LIMIT = 0x000ffff0, 0x00100000, 16, 0x00200000


def system_keys(bridge):
    """bridge: [(dll, name, conv[, ordinal])] -> {(dll, name): key} for the rows that are functions."""
    names = sorted((b[0].lower(), b[1]) for b in bridge if b[2] != 'data')
    if len(set(names)) != len(names):
        raise ValueError('bridge.tsv names one function twice')
    if IMPORT_BASE + STEP * len(names) > LIMIT:
        raise ValueError('more platform functions than the key range holds')
    return {k: IMPORT_BASE + STEP * i for i, k in enumerate(names)}


def table(imports, bridge, modules):
    """imports {slot: (dll, name)}, modules {dll: {name: address}} -> (rows, bad)."""
    keys = system_keys(bridge)
    data = {(b[0].lower(), b[1]) for b in bridge if b[2] == 'data'}
    by_ord = {}      # an import by ordinal binds to the row of that ordinal, as the export table of the dll would
    for b in bridge:
        if len(b) > 3 and b[3] != '':
            if by_ord.setdefault((b[0].lower(), '#%d' % int(b[3])), b[1]) != b[1]:
                raise ValueError('bridge.tsv gives ordinal %s of %s to two names' % (b[3], b[0]))
    both = sorted({d for d, _ in list(keys) + sorted(data)} & set(modules))
    if both:
        raise ValueError('both a lifted module and a platform dll: %s' % ', '.join(both))
    rows, bad = [], []
    for slot, (dll, name) in sorted(imports.items()):
        k = (dll.lower(), name)
        if k[0] not in modules and k in by_ord:
            k = (k[0], by_ord[k])
        if k[0] in modules:
            if name in modules[k[0]]:
                rows.append((slot,) + k + ('module', modules[k[0]][name]))
            else:
                bad.append((slot,) + k + ('not an export of the module',))
        elif k in keys:
            rows.append((slot,) + k + ('key', keys[k]))
        elif k in data:
            rows.append((slot,) + k + ('data', None))
        else:
            bad.append((slot,) + k + ('no provider',))
    return rows, bad


def read_tsv(path, n):
    out = []
    with open(path) as fh:
        for i, ln in enumerate(fh):
            p = ln.rstrip('\n').split('\t')
            if ln[:1] == '#' or (i == 0 and p[0] == 'dll'):
                continue
            if len(p) < n:
                raise ValueError('%s:%d: %d columns, expected %d' % (path, i + 1, len(p), n))
            out.append(p)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--facts', required=True)
    ap.add_argument('--bridge', required=True, help="L5's bridge.tsv: dll, name, ordinal, conv, ...")
    ap.add_argument('--module', action='append', default=[], help='name.dll=exports.tsv (name|#ordinal, hex address)')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    try:
        bridge = [(p[0], p[1], p[3], p[2]) for p in read_tsv(a.bridge, 4)]
        modules = {}
        for m in a.module:
            name, path = m.split('=', 1)
            modules[name.lower()] = {p[0]: int(p[1], 16) for p in read_tsv(path, 2)}
        rows, bad = table(store.load(a.facts, 'facts')['imports'], bridge, modules)
    except ValueError as e:
        sys.exit('iat: %s' % e)
    with open(a.out, 'w', newline='\n') as fh:
        for slot, dll, name, kind, v in rows:
            fh.write('%08x\t%s\t%s\t%s\t%s\n' % (slot, dll, name, kind, '' if v is None else '%08x' % v))
    gatefile.write('iat', len(rows) + len(bad), len(rows), ['%08x %s!%s: %s' % b for b in bad], '%d slots, %d without a value' % (len(rows) + len(bad), len(bad)))
    print('%d slots with a value, %d without' % (len(rows), len(bad)))
    for b in bad:
        print('%08x\t%s!%s\t%s' % b)
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
