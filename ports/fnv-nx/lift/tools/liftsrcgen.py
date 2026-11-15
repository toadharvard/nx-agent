#!/usr/bin/env python3
"""lift/liftsrc/build.json: the recipe of the lifted source (the tool-class root `lift/liftsrc`). One generated stage per shard of every image (tools/liftgen.py --shard K/N), then
one finish stage (tools/liftgen.py --finish) that merges the shards into the pack source: u/*.src, entries/forms/encs/shapes tsv and the module-table stamp.

    liftsrcgen.py --facts DIR --modules MODULES_TSV [--per-shard N] [--dest build.json]

DIR is the checkout of the facts external (the per-image universe.tsv; exe: DIR/universe.tsv, module M: DIR/M/universe.tsv), MODULES_TSV the loader's table. The number of shards of an image is
ceil(entries / per-shard): a static function of the universe. It is not trusted: finish refuses an image whose shards do not cover its universe exactly, and a row of the loader's table
without an image (tools/liftgen.py check_images). The inputs of a shard are exactly what the lifter reads: the fnvlift package, the runtime headers rt/*.h (lifter_tree), the tool
root (mounted by the recipe), the externals and the platform's X86SYS."""
import argparse, glob, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.normpath(os.path.join(HERE, '..', '..', '..', '..'))
ROOT = 'ports/fnv-nx/lift/liftsrc'
LIFT = 'ports/fnv-nx/lift'
LIFTGEN = LIFT + '/tools/liftgen.py'
PER_SHARD = 1024
EXE = 'FalloutNV.exe'
EXTERNALS = [
    {'name': 'exe', 'repo': 'fnv-nx-build', 'ref': 'gamedata/exe', 'commit': '0948da1e89ab719dcf8471e45b67bc60575f6b93', 'tree_sha256': 'ba933b684b5e221e74b8a841a0ebb35ea8599c78a4d582ca0c376ef5a424ef2b', 'at': '/ext/exe'},
    {'name': 'redist', 'repo': 'fnv-nx-build', 'ref': 'gamedata/redist', 'commit': 'c53729e3bac168798fddcf4312f5a64a9b5eb08f', 'tree_sha256': '0' * 64, 'at': '/ext/redist'},
    {'name': 'facts', 'repo': 'fnv-nx-build', 'ref': 'l3a-lift/facts', 'commit': '6944080c904b1d11c1ddf06601d5e19fcd8d32fd', 'tree_sha256': '900e4f0b20407252f0b4de91698854276976fc52e57b2787d3f9cc53a5ab1e8d', 'at': '/ext/facts'},
    {'name': 'fp', 'repo': 'fnv-nx-build', 'ref': 'l3a-lift/fpfixture-w8', 'commit': '616cc88c0a13fa8fe7ce9c17a9ebfd84b57f9197', 'tree_sha256': '0' * 64, 'at': '/ext/fp'},
]
USES = [{'root': 'ports/fnv-nx/loader', 'output': 'MODULES_TSV'}, {'root': 'ports/fnv-nx/platform', 'output': 'X86SYS'}]
MOD = '/uses/ports/fnv-nx/loader/MODULES_TSV'


def universe_size(path):
    return sum(1 for l in open(path) if l.strip() and not l.startswith('#'))


def lift_inputs():
    """the declared inputs of a shard stage: the lifter package, the runtime headers, the (empty) certificate directory"""
    return [LIFT + '/fnvlift'] + sorted(os.path.relpath(f, REPO) for f in glob.glob(os.path.join(REPO, LIFT, 'rt', '*.h'))) + [ROOT + '/certs']


def images(modules):
    """[(image name, module file or None)]: the exe, then every row of the loader's table in file order"""
    rows = [l.rstrip('\n').split('\t') for l in open(modules) if not l.startswith('#') and l.strip()]
    return [('exe', None)] + [(r[0][:-4] if r[0].lower().endswith('.dll') else r[0], r[0]) for r in rows]


def recipe(facts, modules, per_shard=PER_SHARD, exts=EXTERNALS):
    common = ['--lift', '/in/' + LIFT, '--fpstate', '/ext/fp/fpstate', '--fpdepth', '/ext/fp/fpmodes/depth.tsv', '--x86sys', '/uses/ports/fnv-nx/platform/X86SYS', '--certs', '/in/' + ROOT + '/certs', '--jobs', '4']
    gen, imgargs = [], []
    for name, mod in images(modules):
        u = os.path.join(facts, 'universe.tsv' if mod is None else name + '/universe.tsv')
        n = max(1, -(-universe_size(u) // per_shard))
        where = ['--exe', '/ext/exe/' + EXE, '--universe', '/ext/facts/universe.tsv'] if mod is None else \
                ['--module', mod, '--modules', MOD, '--moddirs', '/ext/exe:/ext/redist', '--facts-out', '/ext/facts']
        for k in range(n):
            outs = ['%s/%s_%d.%s' % (name, f, k, x) for f, x in (('texts', 'txt'), ('missing', 'tsv'), ('forms', 'tsv'), ('encs', 'tsv'), ('shapes', 'tsv'), ('inputs', 'json'))]
            gen.append({'tool': LIFTGEN, 'args': where + common + ['--shard', '%d/%d' % (k, n), '--out', '/out/' + name], 'inputs': lift_inputs(), 'outputs': outs, '_image': name})
        imgargs.append((name, '/stage/%s=%s' % (name, '/ext/facts/universe.tsv' if mod is None else '/ext/facts/%s/universe.tsv' % name)))
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
    A('--facts', required=True); A('--modules', required=True); A('--per-shard', type=int, default=PER_SHARD); A('--dest', default=os.path.join(REPO, ROOT, 'build.json'))
    a = ap.parse_args()
    rec = recipe(a.facts, a.modules, a.per_shard)
    with open(a.dest, 'w') as f: json.dump(rec, f, indent=1); f.write('\n')
    print('liftsrc: %d shard stages + finish, %d outputs' % (len(rec['generated']) - 1, len(rec['generated'][-1]['outputs'])))


if __name__ == '__main__': main()
