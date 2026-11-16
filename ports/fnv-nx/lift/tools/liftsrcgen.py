#!/usr/bin/env python3
"""lift/liftsrc/build.json: the recipe of the lifted source (the tool-class root `lift/liftsrc`). One generated stage per shard of every image (tools/liftgen.py --shard K/N), then
one finish stage (tools/liftgen.py --finish) that merges the shards into the pack source: u/*.src, entries/forms/encs/shapes tsv and the module-table stamp.

    liftsrcgen.py --modules MODULES_TSV [--dest build.json]

MODULES_TSV is the loader's table. The stage set does NOT depend on the facts: every image has a fixed number N of shards, chosen from the image size alone (shards(); decision in README), and shard K of
an image takes the functions whose entry has bucket(entry) % N == K (tools/liftgen.py bucket, the rule of the pack's units). A facts change re-keys stages and never changes the stage set. It is not trusted:
finish refuses an image whose shards do not cover its universe exactly, and a row of the loader's table without an image (tools/liftgen.py check_images). The inputs of a shard are exactly what the lifter reads: the fnvlift package, the runtime headers rt/*.h (lifter_tree), the tool
root (mounted by the recipe), the externals and the platform's X86SYS."""
import argparse, glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import liftrecipe
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
]
USES = [{'root': 'ports/fnv-nx/lift/sdmrows', 'output': 'SDMROWS'}, {'root': 'ports/fnv-nx/loader', 'output': 'MODULES_TSV'}, {'root': 'ports/fnv-nx/lift/facts', 'output': 'FACTS'}, {'root': 'ports/fnv-nx/lift/facts', 'output': 'SEGWRITES'}, {'root': 'ports/fnv-nx/lift/fpfacts', 'output': 'FPSTATE'}, {'root': 'ports/fnv-nx/lift/fpfacts', 'output': 'FPDEPTH'}, {'root': 'ports/fnv-nx/platform', 'output': 'X86SYS'}]
MOD = '/uses/ports/fnv-nx/loader/MODULES_TSV'
SDM_PDF = 'intel-64-ia-32-sdm-combined.pdf'      # the one file of the `sdm` external (tools/intel-sdm MANIFEST.json); the pdf's sha256 a4a62e6a7ba11a76c7753a195b825087306812aac39b930973f9168ee599f321
SDMROWS_ROOT = 'ports/fnv-nx/lift/sdmrows'      # the one producer of the lifter's `sdmrows` input (sdmrows_recipe()): every root that lifts `uses` it
SDMROWS_FILE = '/uses/' + SDMROWS_ROOT + '/SDMROWS'
SDMTOOL = LIFT + '/tools/sdmrows_stage.py'
SDM_ROOT = 'ports/fnv-nx/lift/sdm'      # the generated-only root that turns the pinned SDM pdf into text (sdmroot_recipe())
PROV_ROOT = 'ports/fnv-nx/lift/liftprov'      # the measurement root: the same shard stages with --prov (tools/liftgen.py), merged by tools/provtable.py into the density table
PROVTOOL = LIFT + '/tools/provtable.py'


SDM_EXTERNAL = {'name': 'sdm', 'repo': 'fnv-nx-build', 'ref': 'tools/intel-sdm', 'commit': '0768b0baf29a625871084414338c12f65d0f2cc8', 'tree_sha256': '07c5cdf925d7c929a81d0819a409667b212a307a89a90d5bdd04e1bf82e9b389', 'at': '/ext/sdm'}
SDMTEXT = {'name': 'sdmtext', 'exec': '/usr/bin/pdftotext', 'args': ['-layout', '/in/1', '/out/sdm.txt'], 'inputs': [{'external': 'sdm', 'path': SDM_PDF}], 'debs': {'closure': ['poppler-utils']}, 'outputs': ['sdm.txt']}


def shards(size_of_image):
    """N of an image: EXE_SHARDS for the exe (size None), BIG_SHARDS for an image of size_of_image >= BIG_IMAGE, else 1: a function of the loader's row alone, never of the facts"""
    return EXE_SHARDS if size_of_image is None else BIG_SHARDS if size_of_image >= BIG_IMAGE else 1


def lift_inputs():
    """the declared inputs of a shard stage: the lifter package, the runtime headers, the (empty) certificate directory"""
    return [LIFT + '/fnvlift', LIFT + '/proof/__init__.py', LIFT + '/proof/shape.py', LIFT + '/proof/sdmfeat.py', 'ports/fnv-nx/verify/cpuid_model.json', LIFT + '/rt/rcptab.c', LIFT + '/rt/rcptab.cert.json'] + sorted(os.path.relpath(f, REPO) for f in glob.glob(os.path.join(REPO, LIFT, 'rt', '*.h'))) + [ROOT + '/certs']


def images(modules):
    """[(image name, module file or None)]: the exe, then every row of the loader's table in file order"""
    rows = [l.rstrip('\n').split('\t') for l in open(modules) if not l.startswith('#') and l.strip()]
    return [('exe', None, None)] + [(r[0][:-4] if r[0].lower().endswith('.dll') else r[0], r[0], int(r[5], 16)) for r in rows]      # modules.tsv column 6: size_of_image


def image_shards(name, n, where, common, root=ROOT, prov=False):
    """the N shard stages of one image NAME. WHERE is the image-specific part of the lifter's argv (the image file and its facts: `--exe ... --universe ... --jtab ...` for the exe, `--module ...` for a loader row);
    COMMON is the argv every image shares. The stages of a test root's image (a PE emitted by its generator) come from this same function with its own WHERE: one lifting path, the image an input."""
    out = []
    for k in range(n):
        outs = ['%s/prov_%d.json' % (name, k)] if prov else ['%s/%s_%d.%s' % (name, f, k, x) for f, x in (('texts', 'txt'), ('missing', 'tsv'), ('forms', 'tsv'), ('encs', 'tsv'), ('shapes', 'tsv'), ('inputs', 'json'))]
        out.append({'tool': LIFTGEN, 'args': where + common + ['--shard', '%d/%d' % (k, n), '--out', '/out/' + name] + (['--prov'] if prov else []), 'inputs': lift_inputs(), 'outputs': outs, '_image': name})
    return out


def shard_stages(modules, root=ROOT, prov=False):
    common = ['--lift', '/in/' + LIFT, '--fpstate', '/uses/ports/fnv-nx/lift/fpfacts/FPSTATE', '--fpdepth', '/uses/ports/fnv-nx/lift/fpfacts/FPDEPTH', '--x86sys', '/uses/ports/fnv-nx/platform/X86SYS', '--sdmrows', SDMROWS_FILE, '--cpumodel', '/in/ports/fnv-nx/verify/cpuid_model.json', '--rcptab', '/in/'+LIFT+'/rt/rcptab.c', '--rcpcert', '/in/'+LIFT+'/rt/rcptab.cert.json', '--segwrites', '/uses/ports/fnv-nx/lift/facts/SEGWRITES', '--certs', '/in/' + ROOT + '/certs', '--jobs', '4']
    gen, imgargs = [], []
    for name, mod, size in images(modules):
        n = shards(size)
        where = ['--exe', '/ext/exe/' + EXE, '--universe', '/uses/ports/fnv-nx/lift/facts/FACTS/universe.tsv', '--jtab', '/uses/ports/fnv-nx/lift/facts/FACTS/ehtables.json'] if mod is None else \
                ['--module', mod, '--modules', MOD, '--moddirs', '/ext/exe:/ext/redist', '--facts-out', '/uses/ports/fnv-nx/lift/facts/FACTS']
        gen += image_shards(name, n, where, common, root, prov)
        imgargs.append((name, '/stage/%s=%s' % (name, '/uses/ports/fnv-nx/lift/facts/FACTS/universe.tsv' if mod is None else '/uses/ports/fnv-nx/lift/facts/FACTS/%s/universe.tsv' % name)))
    return gen, imgargs


def recipe(modules, exts=EXTERNALS):
    gen, imgargs = shard_stages(modules)
    # finish: the shards' outputs are stage inputs (the stage's key holds their content); the pack tree is its output
    stage = lambda i, p: {'stage': '%s:gen:%d' % (ROOT, i), 'path': p}
    fin_in = [stage(i, o) for i, g in enumerate(gen) for o in g['outputs']]
    pk = ['u/%03x.src' % i for i in range(4096)] + ['entries.tsv', 'forms.tsv', 'encs.tsv', 'shapes.tsv', 'gate_liftgen.json', 'manifest.json']
    args = ['--finish', '--out', '/out', '--exe', '/ext/exe/' + EXE, '--modules', MOD]
    for _, im in imgargs: args += ['--image', im]
    gen.append({'tool': LIFTGEN, 'args': ['--lift', '/in/' + LIFT] + args, 'inputs': fin_in + lift_inputs()[:-1], 'outputs': ['pack/' + o for o in pk]})
    for g in gen: g.pop('_image', None)
    return {'format': 'fnvnx-recipe/1', 'external': exts, 'uses': USES, 'generated': gen, 'outputs': {'PACK': 'pack'}}


def provrecipe(modules, exts=EXTERNALS):
    """lift/liftprov/build.json: the same shard stages as the lifted source, run with --prov (every entry lifted with the provenance tag off and on, texts compared, the side table written), and
    one stage that merges the side tables into the density table (tools/provtable.py). Output TABLE. It produces no text: the pack is lift/liftsrc."""
    gen, imgargs = shard_stages(modules, PROV_ROOT, True)
    stage = lambda i, p: {'stage': '%s:gen:%d' % (PROV_ROOT, i), 'path': p}
    ins = [stage(i, o) for i, g in enumerate(gen) for o in g['outputs']]
    gen.append({'tool': PROVTOOL, 'args': ['--stage', '/stage', '--out', '/out/table'], 'inputs': ins, 'outputs': ['table/' + o for o in ('categories.tsv', 'roots.tsv', 'forms.tsv', 'families.tsv', 'images.tsv', 'shards.tsv', 'summary.json')]})
    for g in gen: g.pop('_image', None)
    return {'format': 'fnvnx-recipe/1', 'external': exts, 'uses': USES, 'generated': gen, 'outputs': {'TABLE': 'table'}}


def sdmrows_recipe():
    """lift/sdmrows/build.json: the one producer of the lifter's `sdmrows` input (sdmfeat.py dumprows of the SDM text of lift/sdm); output SDMROWS"""
    return {'format': 'fnvnx-recipe/1', 'uses': [{'root': SDM_ROOT, 'output': 'SDM_TEXT'}],
            'generated': [{'tool': SDMTOOL, 'args': ['--sdm', '/uses/' + SDM_ROOT + '/SDM_TEXT', '--out', '/out', '--lift', '/in/' + LIFT], 'inputs': [LIFT + '/proof/sdmfeat.py', LIFT + '/proof/__init__.py'], 'outputs': ['sdmrows/sdmrows.json']}],
            'outputs': {'SDMROWS': 'sdmrows/sdmrows.json'}}


def sdmroot_recipe():
    """lift/sdm/build.json: a generated-only root. The pinned Intel SDM pdf (external `sdm`, one file of it) through /usr/bin/pdftotext -layout of the pinned deb poppler-utils (RECIPE.md "Single file of an external"); its output SDM_TEXT is what the sdmrows generator of liftsrc reads"""
    return {'format': 'fnvnx-recipe/1', 'external': [SDM_EXTERNAL], 'run': [SDMTEXT], 'outputs': {'SDM_TEXT': 'sdm.txt'}}


GUEST_TESTS = {'imptest': 'guestgen_imptest.py', 'msvcrttest': 'guestgen_msvcrttest.py'}      # the lifted roots of lift/tests/guest whose generator lifts a byte buffer (tools/lifted.py configure_buffer)


def guest_test_recipe(name, rec):
    """lift/tests/guest/NAME/lifted/build.json: REC (the existing recipe) with its generated stages rewritten: the sdmrows generator, then the root's guest generator with the lifter's declared inputs as explicit arguments
    (the same mounts as the shard stages: X86SYS from the platform root, the SDM rows from the SDM text, the tracked cpuid model and rcptab)"""
    root = 'ports/fnv-nx/lift/tests/guest/%s/lifted' % name
    gen = next(g for g in rec['generated'] if g['tool'] == LIFT + '/tools/' + GUEST_TESTS[name])      # the root's own guest generator (the recipe is re-read: its inc stages and an earlier rewrite are not it)
    outs = gen['outputs']
    st = [{'tool': gen['tool'], 'args': ['/out/gen', '/in/' + LIFT, '--x86sys', '/uses/ports/fnv-nx/platform/X86SYS', '--sdmrows', SDMROWS_FILE, '--cpumodel', '/in/ports/fnv-nx/verify/cpuid_model.json',
                                         '--rcptab', '/in/' + LIFT + '/rt/rcptab.c', '--rcpcert', '/in/' + LIFT + '/rt/rcptab.cert.json'],
           'inputs': [LIFT + '/fnvlift', 'ports/fnv-nx/verify/cpuid_model.json', LIFT + '/proof', LIFT + '/rt/rcptab.c', LIFT + '/rt/rcptab.cert.json'], 'outputs': outs}]
    uses = [u for u in rec.get('uses', []) if (u['root'], u['output']) not in (('ports/fnv-nx/lift/sdm', 'SDM_TEXT'), (SDMROWS_ROOT, 'SDMROWS'), ('ports/fnv-nx/platform', 'X86SYS'))]
    uses += [{'root': SDMROWS_ROOT, 'output': 'SDMROWS'}, {'root': 'ports/fnv-nx/platform', 'output': 'X86SYS'}, {'root': 'ports/fnv-nx/platform', 'output': 'INCLUDE'}]
    inc = list(dict.fromkeys([root + '/inc'] + [i for i in rec.get('include', []) if i not in liftrecipe.INC_DIRS] + ['ports/fnv-nx/platform/include']))      # the pack's own header directory: inc/ is generated by the pack's own stages (liftrecipe.inc_stages, the one function: the runtime's headers of the current tree, inlined), then the root's own
    return dict(rec, generated=st + liftrecipe.inc_stages(), uses=uses, include=inc, outputs={'GEN': 'gen'})      # the generator's directory (guest.cc, image.inc, labels.txt): the native twin takes it through include_uses and includes image.inc by name


def main():
    ap = argparse.ArgumentParser(); A = ap.add_argument
    A('--modules', required=True); A('--dest', default=os.path.join(REPO, ROOT, 'build.json'))
    a = ap.parse_args()
    rec = recipe(a.modules)
    with open(a.dest, 'w') as f: json.dump(rec, f, indent=1); f.write('\n')
    for n in GUEST_TESTS:
        gp = os.path.join(REPO, LIFT, 'tests', 'guest', n, 'lifted', 'build.json'); g = json.load(open(gp))
        with open(gp, 'w') as f: json.dump(guest_test_recipe(n, g), f, indent=1); f.write('\n')
    os.makedirs(os.path.join(REPO, PROV_ROOT), exist_ok=True)
    with open(os.path.join(REPO, PROV_ROOT, 'build.json'), 'w') as f: json.dump(provrecipe(a.modules), f, indent=1); f.write('\n')
    with open(os.path.join(REPO, SDM_ROOT, 'build.json'), 'w') as f: json.dump(sdmroot_recipe(), f, indent=1); f.write('\n')
    with open(os.path.join(REPO, SDMROWS_ROOT, 'build.json'), 'w') as f: json.dump(sdmrows_recipe(), f, indent=1); f.write('\n')
    print('liftsrc: %d shard stages + finish, %d outputs' % (len(rec['generated']) - 1, len(rec['generated'][-1]['outputs'])))


if __name__ == '__main__': main()
