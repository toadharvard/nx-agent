"""plugbuild - the one way the plugin tests build a GCC plugin and compile a fixture with it: recipe stages (no build scripts, no compiler call).

A call creates a toy GUEST product root in a temp git repo (the plugin sources and verify/guest_compiler.json of this checkout, a fixture
generator) and runs verify/recipe.py `build` on it into a GATE_CACHE store (env GATE_CACHE, else a fixed directory under the system temp dir:
the store is content-keyed, so a second run of an unchanged test is all hits). The compiler is the pinned external of guest_compiler.json
(msvcmodel); the plugins are the recipe's `plugin:<name>` stages, built against that compiler's own plugin headers; the fixture is the root's one
C++ unit (PP then CC stage, loading the plugins in the given order with the given arguments). The recipe's native-root rule demands all
plugins with fixed arguments, so a test of one plugin (or an A/B argument, or a missing plugin) is a root whose C++ is generated:
cxx_origin {"lifted_by": gen/lift.py, "pack": the compiler external}; the generator copies the fixture text.
Recipe stages emit objects only (unitstage forbids -S and -save-temps): `compile(asm=True)` therefore cannot return assembly text.
Tests run as `python3 -I FILE` from the checkout root, so a test inserts its own directory in sys.path to import this."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REAL = os.path.normpath(os.path.join(HERE, '..', '..', '..', '..'))    # checkout root
sys.path.insert(0, os.path.join(HERE, '..', '..', 'link'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'verify'))
import elfobj  # noqa: E402
import recipe  # noqa: E402

T = 'ports/fnv-nx/'
ROOT = T + 'toy'
TOOLCHAIN = os.path.normpath(os.path.join(HERE, '..'))
STORE = os.environ.get('GATE_CACHE') or os.path.join(tempfile.gettempdir(), 'fnvnx-toolchain-tests-cache')
STATS = {'run': 0, 'hit': 0}
PRODUCT_FLAGS = ['-mabi=ilp32', '-std=gnu++17', '-O2', '-fms-extensions', '-fshort-wchar', '-frounding-math', '-fsigned-char',
                 '-fno-strict-aliasing', '-fwrapv', '-fno-delete-null-pointer-checks', '-fcheck-new', '-fno-sized-deallocation',
                 '-fno-lifetime-dse', '-ffp-contract=off', '-fPIC', '-fpermissive', '-Wno-narrowing', '-march=armv8-a+crc+crypto',
                 '-mtune=cortex-a57', '-fno-stack-protector', '-fno-stack-clash-protection', '-mno-outline-atomics',
                 '-U_FORTIFY_SOURCE', '-fno-devirtualize']
_N = [0]


def _git(repo, *a):
    return subprocess.run(['git', '-C', repo, '-c', 'user.name=t', '-c', 'user.email=t@t'] + list(a), capture_output=True, text=True, check=True).stdout


def _w(repo, rel, text):
    p = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w') as f:
        f.write(text)


def _plugin(name, args):
    p = {'name': name, 'source': recipe.PLUGIN_SOURCES[name], 'args': dict(args)}
    if name in recipe.PLUGIN_INPUTS:
        p['inputs'] = recipe.PLUGIN_INPUTS[name]
    return p


def _recipe(tmp, plugins, fixtures, flags):
    """build the toy guest root: plugins [(name, {key: value})] in load order, fixtures {unit file name: text}; -> (objects.json dict, out dir)"""
    _N[0] += 1
    repo = os.path.join(tmp, 'toy%d' % _N[0])
    os.makedirs(repo)
    _git(repo, 'init', '-q')
    pin = json.load(open(os.path.join(REAL, T + 'verify/guest_compiler.json')))
    ext = pin['external']
    plugins = [('fnvnx_' + n, a) for n, a in plugins]
    names = [n for n, _ in plugins]
    for rel in sorted({recipe.PLUGIN_SOURCES[n] for n in names} | {i for n in names for i in recipe.PLUGIN_INPUTS.get(n, [])}):
        os.makedirs(os.path.dirname(os.path.join(repo, rel)), exist_ok=True)
        shutil.copyfile(os.path.join(REAL, rel), os.path.join(repo, rel))
    _w(repo, T + 'verify/guest_compiler.json', json.dumps(pin))
    gen = ('import os\nfor n, t in %r.items():\n    os.makedirs(os.path.dirname("/out/" + n), exist_ok=True)\n'
           '    open("/out/" + n, "w").write(t)\n') % {'gen/' + k: v for k, v in fixtures.items()}
    _w(repo, T + 'gen/lift.py', gen)
    rec = {'format': 'fnvnx-recipe/1', 'compiler': {'external': ext['name'], 'cc': pin['cc'], 'cxx': pin['cxx']},
           'flags': {'c': [], 'cxx': list(flags), 'asm': []}, 'include': [], 'units': 'all', 'external': [ext],
           'generated': [{'tool': T + 'gen/lift.py', 'args': [], 'inputs': [], 'outputs': ['gen/' + k for k in fixtures]}]}
    if plugins:
        rec['plugin_cxx'] = 'x86_64-linux-gnu-g++-13'
        rec['plugins'] = [_plugin(n, a) for n, a in plugins]
    _w(repo, ROOT + '/build.json', json.dumps(rec))
    _w(repo, T + 'verify/admit.json', json.dumps({'format': 'fnvnx-admit/1', 'roots': [
        {'path': ROOT, 'class': 'product', 'target': 'guest', 'gates': [], 'recipe': ROOT + '/build.json',
         'cxx_origin': {'lifted_by': T + 'gen/lift.py', 'pack': ext['name']}},
        {'path': T + 'toolchain', 'class': 'tool', 'gates': []}, {'path': T + 'verify', 'class': 'tool', 'gates': []},
        {'path': T + 'gen', 'class': 'tool', 'gates': []}]}))
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-q', '-m', 'toy')
    out = os.path.join(tmp, 'out%d' % _N[0])
    os.makedirs(STORE, exist_ok=True)
    res = recipe.build(repo, ROOT, STORE, out)
    STATS['run'] += res['stages']['run']
    STATS['hit'] += res['stages']['hit']
    return res, out


def _trivial(tmp, names):
    return _recipe(tmp, [(n, {}) for n in names], {'t.cpp': 'int t(int x) { return x; }\n'}, PRODUCT_FLAGS)


def build(name, tmp, source=None):
    """-> path of the plugin's .so out of the recipe's plugin:<name> stage (the plugin's name is the file's base name), or raises
    AssertionError with the diagnostics. Only the tracked source is built (SOURCE is not accepted: one source, the recipe's)."""
    assert source is None, 'the recipe builds the tracked source only'
    res, _ = _trivial(tmp, [name])
    assert res['status'] == 'green', 'plugin %s does not build: %s' % (name, json.dumps(res.get('failure'))[-1500:])
    return next(p['path'] for p in res['plugins'] if p['name'] == 'fnvnx_' + name)


def compile(src, out, plugins, flags=None, args=(), asm=False):
    """compile src to out (an object) through the recipe with the product flags and the plugins (paths of .so made by build()), in that
    order; `args` holds extra compiler flags and -fplugin-arg-<plugin>-<key>[=<value>] plugin arguments; -> CompletedProcess (the caller
    checks returncode: some tests expect an error). asm=True: recipe stages emit objects only, so no assembly can be returned: the build
    is still run (its diagnostics are real) and a green one is reported as returncode 1 with the reason in stderr."""
    pl, extra = [], []
    names = [re.fullmatch(r'fnvnx_([a-z0-9_]+)\.so', os.path.basename(p)).group(1) for p in plugins]
    pa = {n: {} for n in names}
    for a in args:
        m = re.fullmatch(r'-fplugin-arg-fnvnx_([a-z0-9_]+)-([a-z][a-z0-9-]*)(?:=(.*))?', a)
        if m:
            pa[m.group(1)][m.group(2)] = m.group(3) or ''
        else:
            extra.append(a)
    tmp = tempfile.mkdtemp(prefix='plugbuild-')
    try:
        base = os.path.basename(src)
        res, odir = _recipe(tmp, [(n, pa[n]) for n in names], {base: open(src).read()}, (PRODUCT_FLAGS if flags is None else flags) + extra)
        if res['status'] != 'green':
            f = res['failure']
            return subprocess.CompletedProcess([], f.get('exit') or 1, '', '\n'.join(f.get('stderr') or []) + '\n' + json.dumps(f)[:600])
        if asm:
            return subprocess.CompletedProcess([], 1, '', 'recipe stages emit objects only (-S and -save-temps are forbidden by unitstage/toolflags): no assembly')
        shutil.copyfile(next(o['object'] for o in res['objects']), out)
        return subprocess.CompletedProcess([], 0, '', '')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def section(obj, name):
    """the text of section `name` of an object file ('' when the object has no such section), read in-process"""
    data = elfobj.read(obj).section(name)
    return '' if data is None else data.decode('utf-8')


def undefined(obj):
    """names of the undefined symbols of an object file"""
    return {s[0] for s in elfobj.read(obj).symbols() if s[3] == 0 and s[0]}


class Checks:
    """collects failures; `done()` prints the verdict line and returns the exit status"""
    def __init__(self, name):
        self.name, self.fails, self.n = name, [], 0

    def __call__(self, what, cond, detail=''):
        self.n += 1
        if not cond:
            self.fails.append(what)
            print('FAIL', what, str(detail)[:1500])
        return bool(cond)

    def done(self, extra=''):
        print('%s: %s (%d checks)%s [recipe stages run %d hit %d]' % (self.name, 'FAILED %d' % len(self.fails) if self.fails else 'ok', self.n, extra, STATS['run'], STATS['hit']))
        return 1 if self.fails else 0
