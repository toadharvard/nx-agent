"""The one way a selftest compiles guest code: through verify/recipe.py, as a product root of a throw-away git tree (verify/tests/test_guest.py's
precedent), with the pinned compiler of verify/guest_compiler.json and all the plugins of the product roots, into the verdict's store (GATE_CACHE;
without it, a store under TMPDIR). A test never calls a compiler itself.

    objs = guestbuild.build({'guest.cpp': text, ...}, 'lifted')     -> {unit name: object path}
The units of origin 'lifted' are C++ and the output of the generator step of the root (the text is a tracked input of the throw-away tree that the step copies);
origin 'native' units are C, C++ or assembler of a native root. The headers of lift/rt and platform/include are the product's own files (copied unchanged into the
tree, the same bytes), the root's recipe is lift/rt/build.json with the flags of the product root: nothing of the semantic flags is chosen here."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(HERE)))))
T = 'ports/fnv-nx/'
ROOT = T + 'lift/gt'
sys.path.insert(0, os.path.join(REPO, T + 'verify'))
import recipe  # noqa: E402

GEN_TOOL = T + 'lift/tools/gt_copy.py'
IN = T + 'lift/tools/gt_in'     # the texts of the lifted units: tracked inputs of the generator step (a tool root; a C++ file under the product root would be a unit that no step made)
GEN_SRC = ('import os, shutil\nfor n in sorted(os.listdir("/in/%s")): shutil.copyfile("/in/%s/" + n, "/out/" + n)\n') % (IN, IN)


def git(d, *a):
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', '-C', d] + list(a), capture_output=True, text=True, check=True).stdout


def put(d, rel, text):
    p = os.path.join(d, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, 'wb' if isinstance(text, bytes) else 'w').write(text)


def copy_real(d, rel):
    p = os.path.join(d, rel); os.makedirs(os.path.dirname(p), exist_ok=True); shutil.copyfile(os.path.join(REPO, rel), p)


def tree(origin, units, flags, include=()):
    d = tempfile.mkdtemp(prefix='gt-'); git(d, 'init', '-q')
    for n in list(recipe.PLUGIN_SOURCES.values()) + [i for v in recipe.PLUGIN_INPUTS.values() for i in v] + [recipe.ENV_JSON, T + 'verify/guest_compiler.json']:
        copy_real(d, n)
    real_roots = {r['path'] for r in json.load(open(os.path.join(REPO, T + 'verify/admit.json')))['roots']}
    homes = (T + 'lift/rt', T + 'platform') + tuple(sorted({r for i in include for r in real_roots if i == r or i.startswith(r + '/')}))    # the include paths lie in these product roots: the real roots, every tracked file of them unchanged (the same bytes, the same keys in the store)
    for rel in subprocess.run(['git', '-C', REPO, 'ls-files', '-z', '--'] + list(homes), capture_output=True, check=True).stdout.decode().split('\0'):
        if rel: copy_real(d, rel)
    real = {r['path']: r for r in json.load(open(os.path.join(REPO, T + 'verify/admit.json')))['roots']}
    rec = json.load(open(os.path.join(REPO, T + 'lift/rt/build.json')))      # the product runtime's own recipe: compiler, plugins, include, external, per-root flags
    rec['include'] = list(rec['include']) + [i for i in include if i not in rec['include']]
    for k in ('c', 'cxx', 'asm'): rec['flags'][k] = list(rec['flags'][k]) + list(flags.get(k, []))
    ent = {'path': ROOT, 'class': 'product', 'target': 'guest', 'gates': [], 'recipe': ROOT + '/build.json'}
    if origin is None:     # no test root: the tree is the real roots only (rt_objects)
        git(d, 'add', '-A'); put(d, T + 'verify/admit.json', json.dumps({'format': 'fnvnx-admit/1', 'roots': [{'path': T + x, 'class': 'tool', 'gates': []} for x in ('toolchain', 'verify', 'lift/tools')] + [real[h] for h in homes]}))
        git(d, 'add', '-A'); git(d, 'commit', '-q', '-m', 'gt')
        return d
    if origin == 'lifted':
        ent['cxx_origin'] = {'lifted_by': GEN_TOOL, 'pack': rec['external'][0]['name']}
        rec['plugins'] = [dict(p, args=dict(recipe.plugin_args(ent['cxx_origin'])[p['name']])) if p['name'] in recipe.plugin_args(ent['cxx_origin']) else p for p in rec['plugins']]
        rec['generated'] = [{'tool': GEN_TOOL, 'args': [], 'inputs': [IN + '/' + n for n in units], 'outputs': list(units)}]
        put(d, GEN_TOOL, GEN_SRC)
        for n, t in units.items(): put(d, IN + '/' + n, t)
    else:
        if any(n.endswith(('.cc', '.cpp', '.cxx')) for n in units): ent['cxx_origin'] = 'native'
        for n, t in units.items(): put(d, ROOT + '/' + n, t)
    put(d, ROOT + '/build.json', json.dumps(rec, indent=1))
    put(d, T + 'verify/admit.json', json.dumps({'format': 'fnvnx-admit/1', 'roots': [ent] + [{'path': T + x, 'class': 'tool', 'gates': []} for x in ('toolchain', 'verify', 'lift/tools')] + [real[h] for h in homes]}))   # lift/rt and platform: the header homes of the include paths (headers only in this tree)
    git(d, 'add', '-A'); git(d, 'commit', '-q', '-m', 'gt')
    return d


def rt_objects():
    """the objects of the REAL runtime root lift/rt (its recipe, its units, unchanged): {unit file name: object path}"""
    return build({}, None, root=T + 'lift/rt')


def build(units, origin, flags=None, root=ROOT, include=()):
    """-> {unit file name: object path}; raises RuntimeError with the recipe's failure when the build is not green"""
    cache = os.environ.get('GATE_CACHE') or tempfile.mkdtemp(prefix='gtc-')
    d = tree(origin, units, flags or {}, include); out = tempfile.mkdtemp(prefix='gto-')
    try:
        res = recipe.build(d, root, os.path.join(cache, 'store'), out, int(os.environ.get('JOBS', '4')), os.path.join(cache, 'external'))
        if res['status'] != 'green': raise RuntimeError(json.dumps(res.get('failure'), indent=1)[:2000])
        keep = tempfile.mkdtemp(prefix='gtk-')
        got = {}
        for o in res['objects']:
            q = os.path.join(keep, os.path.basename(o['object'])); shutil.copyfile(o['object'], q); got[o['unit']] = q
        return got
    finally:
        shutil.rmtree(d, ignore_errors=True); shutil.rmtree(out, ignore_errors=True)
