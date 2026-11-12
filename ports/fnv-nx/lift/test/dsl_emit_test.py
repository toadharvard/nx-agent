#!/usr/bin/env python3
"""Reads the recipe build of root lift/test/dslemit and reports global numbers.
No compiler is called here: the units were compiled by verify/recipe.py.
Usage: dsl_emit_test.py OBJECTS_JSON GEN_DIR   (GEN_DIR is the generator stage's output directory:
emitted.tsv bad.tsv undef.tsv).
Exit 1 (red) if any unit is missing from objects.json."""
import importlib.util, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load_gen():
    sp = importlib.util.spec_from_file_location('gen_dslemit', os.path.join(HERE, '..', 'gen', 'gen_dslemit.py'))
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


def recipe_outputs_match():
    """build.json's declared outputs are exactly gen_dslemit.outputs() (the unit list is derived from the generator, never hand-edited)"""
    m = load_gen()
    g = json.load(open(os.path.join(HERE, 'dslemit', 'build.json')))['generated']
    g = g[0] if isinstance(g, list) else g
    return g['outputs'] == m.outputs()

def one(root, name):
    p = os.path.join(root, name)
    if not os.path.isfile(p):
        sys.exit('red: %s missing' % p)
    return p

def rows(p):
    return [l for l in open(p).read().splitlines() if l and not l.startswith('#')]

def main():
    obj, gen = sys.argv[1], sys.argv[2]
    if not recipe_outputs_match():
        print('red: build.json outputs differ from gen_dslemit.outputs()')
        return 1
    units = {os.path.basename(o['object']) for o in json.load(open(obj))['objects']}
    want = {os.path.basename(n) + '.o' for n in load_gen().unit_names()}
    missing = sorted(want - units)
    e, b, u = (rows(one(gen, n)) for n in ('emitted.tsv', 'bad.tsv', 'undef.tsv'))
    print('units compiled %d/%d, failed %d' % (len(want & units), len(want), len(missing)))
    print('emitted rows %d, bad rows %d, undef rows %d' % (len(e), len(b), len(u)))
    if missing:
        print('red: units without object:', ' '.join(missing))
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
