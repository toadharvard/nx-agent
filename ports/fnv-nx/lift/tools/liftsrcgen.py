#!/usr/bin/env python3
"""lift/liftsrc/build.json: the recipe of the lifted source (the tool-class root `lift/liftsrc`). One generated stage per shard of every image (tools/liftgen.py --shard K/N), then
one finish stage (tools/liftgen.py --finish) that merges the shards into the pack source: u/*.src, entries/forms/encs/shapes tsv and the module-table stamp.

    liftsrcgen.py --modules MODULES_TSV [--dest build.json]

MODULES_TSV is the loader's table. The stage set does NOT depend on the facts: every image has a fixed number N of shards, chosen from the image size alone (shards(); decision in README), and shard K of
an image takes the functions whose entry has bucket(entry) % N == K (tools/liftgen.py bucket, the rule of the pack's units). A facts change re-keys stages and never changes the stage set. It is not trusted:
finish refuses an image whose shards do not cover its universe exactly, and a row of the loader's table without an image (tools/liftgen.py check_images). The inputs of a shard are exactly what the lifter reads: the fnvlift package, the runtime headers rt/*.h (lifter_tree), the tool
root (mounted by the recipe), the externals and the platform's X86SYS."""
import argparse, glob, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.normpath(os.path.join(HERE, '..', '..', '..', '..'))
ROOT = 'ports/fnv-nx/lift/liftsrc'
LIFT = 'ports/fnv-nx/lift'
LIFTGEN = LIFT + '/tools/liftgen.py'
EXE_SHARDS = 64          # the exe (SizeOfImage 0x0f60000 range, 96,839 entries)
BIG_SHARDS = 16          # an image of size_of_image >= BIG_IMAGE (d3dx9_31, d3dx9_38)
BIG_IMAGE = 0x200000
EXE = 'FalloutNV.exe'
EXTERNALS = [
    {'name': 'exe', 'repo': 'fnv-nx-build', 'ref': 'gamedata/exe', 'commit': '0948da1e89ab719dcf8471e45b67bc60575f6b93', 'tree_sha256': 'ba933b684b5e221e74b8a841a0ebb35ea8599c78a4d582ca0c376ef5a424ef2b', 'at': '/ext/exe'},
    {'name': 'redist', 'repo': 'fnv-nx-build', 'ref': 'gamedata/redist', 'commit': 'c53729e3bac168798fddcf4312f5a64a9b5eb08f', 'tree_sha256': 'fe7d3b56dba13c2bb236bdd3f7681dab41029f192283b7d4ba0c81105878bfd2', 'at': '/ext/redist'},
    {'name': 'cpufeat', 'repo': 'fnv-nx-build', 'ref': 'l3b-lift/cpuid-feature-bits/964510be8', 'commit': 'b30137748185bd456237f5c805bcf67c2697170c', 'tree_sha256': 'f6ccfbb64fdfc54fd53524def719e4eb8b11516498b4a5a9ee826e51319ab694', 'at': '/ext/cpufeat'},
]
USES = [{'root': 'ports/fnv-nx/loader', 'output': 'MODULES_TSV'}, {'root': 'ports/fnv-nx/lift/facts', 'output': 'FACTS'}, {'root': 'ports/fnv-nx/lift/facts', 'output': 'SEGWRITES'}, {'root': 'ports/fnv-nx/lift/fpfacts', 'output': 'FPSTATE'}, {'root': 'ports/fnv-nx/lift/fpfacts', 'output': 'FPDEPTH'}, {'root': 'ports/fnv-nx/platform', 'output': 'X86SYS'}]
MOD = '/uses/ports/fnv-nx/loader/MODULES_TSV'


def shards(size_of_image):
    """N of an image: EXE_SHARDS for the exe (size None), BIG_SHARDS for an image of size_of_image >= BIG_IMAGE, else 1: a function of the loader's row alone, never of the facts"""
    return EXE_SHARDS if size_of_image is None else BIG_SHARDS if size_of_image >= BIG_IMAGE else 1


def lift_inputs():
    """the declared inputs of a shard stage: the lifter package, the runtime headers, the (empty) certificate directory"""
    return [LIFT + '/fnvlift', LIFT + '/proof/__init__.py', LIFT + '/proof/shape.py'] + sorted(os.path.relpath(f, REPO) for f in glob.glob(os.path.join(REPO, LIFT, 'rt', '*.h'))) + [ROOT + '/certs']


def images(modules):
    """[(image name, module file or None)]: the exe, then every row of the loader's table in file order"""
    rows = [l.rstrip('\n').split('\t') for l in open(modules) if not l.startswith('#') and l.strip()]
    return [('exe', None, None)] + [(r[0][:-4] if r[0].lower().endswith('.dll') else r[0], r[0], int(r[5], 16)) for r in rows]      # modules.tsv column 6: size_of_image


def recipe(modules, exts=EXTERNALS):
    common = ['--lift', '/in/' + LIFT, '--fpstate', '/uses/ports/fnv-nx/lift/fpfacts/FPSTATE', '--fpdepth', '/uses/ports/fnv-nx/lift/fpfacts/FPDEPTH', '--x86sys', '/uses/ports/fnv-nx/platform/X86SYS', '--cpufeat', '/ext/cpufeat/cpuid_feature_bits.tsv', '--segwrites', '/uses/ports/fnv-nx/lift/facts/SEGWRITES', '--certs', '/in/' + ROOT + '/certs', '--jobs', '4']
    gen, imgargs = [], []
    for name, mod, size in images(modules):
        n = shards(size)
        where = ['--exe', '/ext/exe/' + EXE, '--universe', '/uses/ports/fnv-nx/lift/facts/FACTS/universe.tsv', '--jtab', '/uses/ports/fnv-nx/lift/facts/FACTS/ehtables.json'] if mod is None else \
                ['--module', mod, '--modules', MOD, '--moddirs', '/ext/exe:/ext/redist', '--facts-out', '/uses/ports/fnv-nx/lift/facts/FACTS']
        for k in range(n):
            outs = ['%s/%s_%d.%s' % (name, f, k, x) for f, x in (('texts', 'txt'), ('missing', 'tsv'), ('forms', 'tsv'), ('encs', 'tsv'), ('shapes', 'tsv'), ('inputs', 'json'))]
            gen.append({'tool': LIFTGEN, 'args': where + common + ['--shard', '%d/%d' % (k, n), '--out', '/out/' + name], 'inputs': lift_inputs(), 'outputs': outs, '_image': name})
        imgargs.append((name, '/stage/%s=%s' % (name, '/uses/ports/fnv-nx/lift/facts/FACTS/universe.tsv' if mod is None else '/uses/ports/fnv-nx/lift/facts/FACTS/%s/universe.tsv' % name)))
    # finish: the shards' outputs are stage inputs (the stage's key holds their content); the pack tree is its output
    stage = lambda i, p: {'stage': '%s:gen:%d' % (ROOT, i), 'path': p}
    fin_in = [stage(i, o) for i, g in enumerate(gen) for o in g['outputs']]
    pk = ['u/%03x.src' % i for i in range(4096)] + ['entries.tsv', 'forms.tsv', 'encs.tsv', 'shapes.tsv', 'gate_liftgen.json', 'manifest.json']
    args = ['--finish', '--out', '/out', '--exe', '/ext/exe/' + EXE, '--modules', MOD]
    for _, im in imgargs: args += ['--image', im]
    gen.append({'tool': LIFTGEN, 'args': ['--lift', '/in/' + LIFT] + args, 'inputs': fin_in + lift_inputs()[:-1], 'outputs': ['pack/' + o for o in pk]})
    for g in gen: g.pop('_image', None)
    return {'format': 'fnvnx-recipe/1', 'external': exts, 'uses': USES, 'generated': gen, 'outputs': {'PACK': 'pack'}}


def main():
    ap = argparse.ArgumentParser(); A = ap.add_argument
    A('--modules', required=True); A('--dest', default=os.path.join(REPO, ROOT, 'build.json'))
    a = ap.parse_args()
    rec = recipe(a.modules)
    with open(a.dest, 'w') as f: json.dump(rec, f, indent=1); f.write('\n')
    print('liftsrc: %d shard stages + finish, %d outputs' % (len(rec['generated']) - 1, len(rec['generated'][-1]['outputs'])))


if __name__ == '__main__': main()
