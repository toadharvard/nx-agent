#!/usr/bin/env python3
"""Reads the recipe build of root lift/test/dslemit and reports global numbers.
No compiler is called here: the units were compiled by verify/recipe.py.
Usage: dsl_emit_test.py OBJECTS_JSON GEN_DIR   (GEN_DIR is the generator stage's output directory:
emitted.tsv bad.tsv undef.tsv).
Exit 1 (red) if any unit is missing from objects.json."""
import json, os, sys

def one(root, name):
    p = os.path.join(root, name)
    if not os.path.isfile(p):
        sys.exit('red: %s missing' % p)
    return p

def rows(p):
    return [l for l in open(p).read().splitlines() if l and not l.startswith('#')]

def main():
    obj, gen = sys.argv[1], sys.argv[2]
    units = {os.path.basename(o['object']) for o in json.load(open(obj))['objects']}
    want = {'%02x.cpp.o' % i for i in range(128)}
    missing = sorted(want - units)
    e, b, u = (rows(one(gen, n)) for n in ('emitted.tsv', 'bad.tsv', 'undef.tsv'))
    print('units compiled %d/%d, failed %d' % (len(want & units), len(want), len(missing)))
    print('emitted rows %d, bad rows %d, undef rows %d' % (len(e), len(b), len(u)))
    if missing:
        print('red: units without object:', ' '.join(missing))
        return 1
    return 0

sys.exit(main())
