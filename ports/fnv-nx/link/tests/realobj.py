"""realobj - real objects for the gate tests: tracked C++ sources under link/tests/fixtures/ are built by the one build (verify/recipe.py) as a toy
guest root in a temp git repo (admit entry "target": "guest", "cxx_origin": "native", compiler = the external pinned in verify/guest_compiler.json,
plugins = recipe.PLUGIN_SOURCES / PLUGIN_ARGS), exactly as verify/tests/test_plugins.py and test_guest.py do.  No direct compiler call.

    objects(names)  -> {fixture stem: path of the produced .o}    (built once per process; the store is $GATE_CACHE, else a scratch store)
The external compiler is fetched by the pin (commit + tree hash) from ARTIFACTS_URL; when it cannot be fetched this raises, never skips.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.dirname(os.path.dirname(HERE))                    # ports/fnv-nx
REAL = os.path.dirname(os.path.dirname(TOOL))                    # checkout root
sys.path.insert(0, os.path.join(TOOL, 'verify'))
import recipe  # noqa: E402

T = 'ports/fnv-nx/'
ROOT = T + 'toy'
FIXDIR = os.path.join(HERE, 'fixtures')
_cache = {}


def _git(repo, *a):
    return subprocess.run(['git', '-C', repo, '-c', 'user.name=t', '-c', 'user.email=t@t'] + list(a), capture_output=True, text=True, check=True).stdout


def _w(repo, rel, text):
    p = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, 'w').write(text)


def objects(names):
    key = tuple(sorted(names))
    if key in _cache:
        return _cache[key]
    base = tempfile.mkdtemp(prefix='realobj-')
    pin = json.load(open(os.path.join(REAL, T + 'verify/guest_compiler.json')))
    ext = pin['external']
    plugs = [dict({'name': n, 'source': recipe.PLUGIN_SOURCES[n], 'args': dict(recipe.PLUGIN_ARGS[n])},
                  **({'inputs': recipe.PLUGIN_INPUTS[n]} if n in recipe.PLUGIN_INPUTS else {})) for n in recipe.PLUGIN_SOURCES]
    rec = {'format': 'fnvnx-recipe/1', 'compiler': {'external': ext['name'], 'cc': pin['cc'], 'cxx': pin['cxx']}, 'external': [ext],
           'flags': {'c': [], 'cxx': ['-O2'] + list(recipe.SEMANTICS_FLAGS), 'asm': []}, 'include': [], 'units': 'all',
           'plugin_cxx': 'x86_64-linux-gnu-g++-13', 'plugins': plugs}
    repo = os.path.join(base, 'repo')
    os.makedirs(repo)
    _git(repo, 'init', '-q')
    for n in list(recipe.PLUGIN_SOURCES.values()) + [i for v in recipe.PLUGIN_INPUTS.values() for i in v]:
        os.makedirs(os.path.dirname(os.path.join(repo, n)), exist_ok=True)
        shutil.copyfile(os.path.join(REAL, n), os.path.join(repo, n))
    for n in names:
        _w(repo, ROOT + '/%s.cc' % n, open(os.path.join(FIXDIR, n + '.cc')).read())
    _w(repo, ROOT + '/build.json', json.dumps(rec, indent=1))
    _w(repo, T + 'verify/guest_compiler.json', json.dumps(pin))
    _w(repo, T + 'verify/admit.json', json.dumps({'format': 'fnvnx-admit/1', 'roots': [
        {'path': ROOT, 'class': 'product', 'target': 'guest', 'cxx_origin': 'native', 'gates': [], 'recipe': ROOT + '/build.json'},
        {'path': T + 'toolchain', 'class': 'tool', 'gates': []}, {'path': T + 'verify', 'class': 'tool', 'gates': []},
        {'path': T + 'gen', 'class': 'tool', 'gates': []}]}))
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-q', '-m', 'toy')
    store = os.environ.get('GATE_CACHE') or os.path.join(base, 'store')
    res = recipe.build(repo, ROOT, store, os.path.join(base, 'out'))
    if res['status'] != 'green':
        raise RuntimeError('real-object build is red: %s' % (res.get('failure'),))
    out = {os.path.splitext(o['unit'])[0]: o['object'] for o in res['objects']}
    _cache[key] = out
    return out
