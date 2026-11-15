#!/usr/bin/env python3
"""Facts stage FUNCS: the function starts of the decomp (address, size), the list universe.py takes as its decomp starts.
    funcsfacts.py --units UNITS_JSON --out funcs.tsv
UNITS_JSON = build/units.json of the pinned decomp tree (fnv-nx-build tools/fnv_decomp, the external `decomp`): {"units": [{"functions": [{"address": "0x..", "size": N, ...}]}]}.
RULE: one row `address<TAB>size` (8 hex digits, decimal size) per distinct function address, in address order, after the header `# decomp functions (units.json at decomp <commit>): address<TAB>size`.
An address that two units list with different sizes is an error (the list is a function of the tree: no row is chosen). The text is canonical: the same for every run."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli


def rows(units):
    out = {}
    for u in units['units']:
        for f in u['functions']:
            a, s = int(f['address'], 16), int(f['size'])
            if out.setdefault(a, s) != s: sys.exit('funcsfacts: function %08x has two sizes (%d, %d)' % (a, out[a], s))
    return sorted(out.items())


def main(units_path, out, commit):
    r = rows(json.load(open(units_path)))
    open(out, 'w').write('# decomp functions (units.json at decomp %s): address\tsize\n' % commit + ''.join('%08x\t%d\n' % x for x in r))
    print('funcsfacts: %d functions' % len(r))


if __name__ == '__main__':
    (a, b, c), _ = cli.parse('usage: funcsfacts.py UNITS_JSON OUT DECOMP_COMMIT\n  funcs.tsv of the decomp function starts (address, size)', sys.argv[1:], (3, 3))
    main(a, b, c)
