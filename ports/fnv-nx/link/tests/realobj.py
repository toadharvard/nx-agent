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
import cas  # noqa: E402
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


def _store():
    d = os.environ.get('GATE_CACHE') or os.path.join(tempfile.gettempdir(), 'realobj-store')
    os.makedirs(d, exist_ok=True)
    return d


def _unitfile(n):
    """a source key with an extension .S or .s is an assembler unit (kept as named); any other key is a C++ unit `key.cc`"""
    return n if n.endswith(('.S', '.s')) else n + '.cc'


def build(sources, plugins=None, lifted=False, flags=(), link_script=None, asm_flags=()):
    """Build C++ SOURCES ({unit file name: text}) as a toy guest root of a temp git repo -> the recipe's result dict (status green or red, with
    'failure' holding the stage, exit and the compiler's stderr tail).  PLUGINS: None = all of recipe.PLUGIN_SOURCES, else the names to load (any subset
    needs LIFTED: a native root must load them all, RECIPE.md gate rule).  LIFTED: the toy root is a lifted-origin root (RECIPE.md): its C++ units are
    outputs of a `generated` step (gen/lift.py writes them) whose pack is an external served from a local git repo through ARTIFACTS_URL; the guest
    ASM_FLAGS: flags of the recipe's `asm` list (.S/.s units: key `name.S`).
    LINK_SCRIPT: text of the root's link.ld (the recipe's `link` stage links the objects into an ELF32 image, entry symbol `entry`).
    FLAGS: extra C++ flags of the recipe (subject to the recipe's flag policy).  The guest compiler external is taken from the store's external cache (fetched by pin on the first native build)."""
    base = tempfile.mkdtemp(prefix='realobj-')
    pin = json.load(open(os.path.join(REAL, T + 'verify/guest_compiler.json')))
    ext = pin['external']
    names = list(recipe.PLUGIN_SOURCES) if plugins is None else list(plugins)
    if plugins is not None and not lifted:
        raise ValueError('a plugin subset needs a lifted-origin root')
    plugs = [dict({'name': n, 'source': recipe.PLUGIN_SOURCES[n], 'args': dict(recipe.PLUGIN_ARGS[n])},
                  **({'inputs': recipe.PLUGIN_INPUTS[n]} if n in recipe.PLUGIN_INPUTS else {})) for n in recipe.PLUGIN_SOURCES if n in names]
    rec = {'format': 'fnvnx-recipe/1', 'compiler': {'external': ext['name'], 'cc': pin['cc'], 'cxx': pin['cxx']}, 'external': [ext],
           'flags': {'c': [], 'cxx': ['-O2'] + list(recipe.SEMANTICS_FLAGS) + list(flags), 'asm': list(asm_flags)}, 'include': [], 'units': 'all',
           'plugin_cxx': 'x86_64-linux-gnu-g++-13', 'plugins': plugs}
    asm_only = all(n.endswith(('.S', '.s')) for n in sources)
    if asm_only:
        plugs = []
        rec.pop('plugin_cxx')
        rec.pop('plugins')
    elif not plugs:
        rec.pop('plugin_cxx')
        rec.pop('plugins')
    if link_script is not None:
        rec['link'] = {'script': 'link.ld', 'output': 'x.elf', 'flags': ['-m', 'aarch64linux32', '-pie', '-e', 'entry']}
    repo = os.path.join(base, 'repo')
    os.makedirs(repo)
    _git(repo, 'init', '-q')
    for n in list(recipe.PLUGIN_SOURCES.values()) + [i for v in recipe.PLUGIN_INPUTS.values() for i in v]:
        os.makedirs(os.path.dirname(os.path.join(repo, n)), exist_ok=True)
        shutil.copyfile(os.path.join(REAL, n), os.path.join(repo, n))
    store = _store()
    old_url = os.environ.get('ARTIFACTS_URL')
    root = {'path': ROOT, 'class': 'product', 'target': 'guest', 'gates': [], 'recipe': ROOT + '/build.json'}
    if lifted:
        pack = os.path.join(base, 'pack')
        os.makedirs(pack)
        _git(pack, 'init', '-q')
        _w(pack, 'pack.txt', 'lifter product\n')
        _git(pack, 'add', '-A')
        _git(pack, 'commit', '-q', '-m', 'pack')
        commit = _git(pack, 'rev-parse', 'HEAD').strip()
        chk = os.path.join(base, 'pack-tree')
        subprocess.run(['git', '-C', pack, 'checkout-index', '-a', '-f', '--prefix=' + chk + '/'], check=True)
        tree = cas.ingest(chk, os.path.join(base, 'ingest'))
        outs = []
        lift = 'import os\nos.makedirs("/out/gen")\n'
        for n, text in sources.items():
            fn = _unitfile(n)
            outs.append('gen/' + fn)
            lift += 'open("/out/gen/%s", "w").write(%r)\n' % (fn, text)
        rec['generated'] = [{'tool': T + 'gen/lift.py', 'args': [], 'inputs': [], 'outputs': outs}]
        rec['external'] = [ext, {'name': 'pack', 'repo': 'fnv-nx-build', 'ref': 't', 'commit': commit, 'tree_sha256': tree, 'at': '/ext/pack'}]
        _w(repo, T + 'gen/lift.py', lift)
        root['cxx_origin'] = {'lifted_by': T + 'gen/lift.py', 'pack': 'pack'}
        os.environ['ARTIFACTS_URL'] = pack
    else:
        for n, text in sources.items():
            _w(repo, ROOT + '/' + _unitfile(n), text)
        if not asm_only:
            root['cxx_origin'] = 'native'
    if link_script is not None:
        _w(repo, ROOT + '/link.ld', link_script)
    _w(repo, ROOT + '/build.json', json.dumps(rec, indent=1))
    _w(repo, T + 'verify/guest_compiler.json', json.dumps(pin))
    shutil.copyfile(os.path.join(REAL, T + 'verify/env.json'), os.path.join(repo, T + 'verify/env.json'))   # the declared machine (recipe.env_blocked)
    _w(repo, T + 'verify/admit.json', json.dumps({'format': 'fnvnx-admit/1', 'roots': [
        root, {'path': T + 'toolchain', 'class': 'tool', 'gates': []}, {'path': T + 'verify', 'class': 'tool', 'gates': []},
        {'path': T + 'gen', 'class': 'tool', 'gates': []}]}))
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-q', '-m', 'toy')
    try:
        return recipe.build(repo, ROOT, store, os.path.join(base, 'out'))
    finally:
        if old_url is None:
            os.environ.pop('ARTIFACTS_URL', None)
        else:
            os.environ['ARTIFACTS_URL'] = old_url


def objects(names):
    key = tuple(sorted(names))
    if key in _cache:
        return _cache[key]
    res = build({n: open(os.path.join(FIXDIR, n + '.cc')).read() for n in names})
    if res['status'] != 'green':
        raise RuntimeError('real-object build is red: %s' % (res.get('failure'),))
    out = {os.path.splitext(o['unit'])[0]: o['object'] for o in res['objects']}
    _cache[key] = out
    return out
