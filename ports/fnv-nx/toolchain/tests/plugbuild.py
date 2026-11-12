"""plugbuild - the one way the plugin tests build a GCC plugin and compile a fixture with it: recipe stages (no build scripts, no compiler call).

A call creates a toy GUEST product root in a temp git repo (the plugin sources and verify/guest_compiler.json of this checkout, a fixture
generator) and runs verify/recipe.py `build` on it into a GATE_CACHE store (env GATE_CACHE, else a fixed directory under the system temp dir:
the store is content-keyed, so a second run of an unchanged test is all hits). The compiler is the pinned external of guest_compiler.json
(msvcmodel); the plugins are the recipe's `plugin:<name>` stages, built against that compiler's own plugin headers; the fixture is the root's one
C++ unit (PP then CC stage, loading the plugins in the given order with the given arguments). The recipe's native-root rule demands all
plugins with fixed arguments, so a test of one plugin (or an A/B argument, or a missing plugin) is a root whose C++ is generated:
cxx_origin {"lifted_by": gen/lift.py, "pack": the compiler external}; the generator copies the fixture text.
Recipe stages emit objects only; tests read the object back with the pinned binutils (`objdump`, `cstring`, `section_size`, elfobj).
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
import sandbox  # noqa: E402
import cas  # noqa: E402
import glob  # noqa: E402

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
# guest plugins that recipe.PLUGIN_SOURCES does not (yet) list: name -> tracked source (the product list is L1's)
EXTRA_SOURCES = {'fnvnx_f2i_check': T + 'toolchain/f2i_check/fnvnx_f2i_check.cc'}


# plugins whose argument is not optional (the plugin is an error without it): short name -> arguments every use carries
REQUIRED_ARGS = {'f2i_check': {'out': '/out/f2i.json'}}


def source_of(name):
    return recipe.PLUGIN_SOURCES.get(name) or EXTRA_SOURCES[name]


def _git(repo, *a):
    return subprocess.run(['git', '-C', repo, '-c', 'user.name=t', '-c', 'user.email=t@t'] + list(a), capture_output=True, text=True, check=True).stdout


def _w(repo, rel, text):
    p = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w') as f:
        f.write(text)


def _plugin(name, args):
    p = {'name': name, 'source': source_of(name), 'args': dict(args)}
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
    for rel in sorted({source_of(n) for n in names} | {i for n in names for i in recipe.PLUGIN_INPUTS.get(n, [])}):
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
    return _recipe(tmp, [(n, dict(REQUIRED_ARGS.get(n, {}))) for n in names], {'t.cpp': 'int t(int x) { return x; }\n'}, PRODUCT_FLAGS)


def build(name, tmp, source=None):
    """-> path of the plugin's .so out of the recipe's plugin:<name> stage (the plugin's name is the file's base name), or raises
    AssertionError with the diagnostics. Only the tracked source is built (SOURCE is not accepted: one source, the recipe's)."""
    assert source is None, 'the recipe builds the tracked source only'
    res, _ = _trivial(tmp, [name])
    assert res['status'] == 'green', 'plugin %s does not build: %s' % (name, json.dumps(res.get('failure'))[-1500:])
    return next(p['path'] for p in res['plugins'] if p['name'] == 'fnvnx_' + name)


def compile(src, out, plugins, flags=None, args=(), keep=None, files=None):
    """compile src to out (an object) through the recipe with the product flags and the plugins (paths of .so made by build()), in that
    order; `args` holds extra compiler flags and -fplugin-arg-<plugin>-<key>[=<value>] plugin arguments; -> CompletedProcess (the caller
    checks returncode: some tests expect an error, and then stderr is the recipe's failure diagnostics)"""
    names = [re.fullmatch(r'fnvnx_([a-z0-9_]+)\.so', os.path.basename(p)).group(1) for p in plugins]
    pa = {n: dict(REQUIRED_ARGS.get(n, {})) for n in names}
    extra = []
    for a in args:
        m = re.fullmatch(r'-fplugin-arg-fnvnx_([a-z0-9_]+)-([a-z][a-z0-9-]*)(?:=(.*))?', a)
        if m:
            pa[m.group(1)][m.group(2)] = m.group(3) or ''
        else:
            extra.append(a)
    tmp = tempfile.mkdtemp(prefix='plugbuild-')
    try:
        res, odir = _recipe(tmp, [(n, pa[n]) for n in names], {os.path.basename(src): open(src).read()}, (PRODUCT_FLAGS if flags is None else flags) + extra)
        if res['status'] != 'green':
            f = res['failure']
            return subprocess.CompletedProcess([], f.get('exit') or 1, '', '\n'.join(f.get('stderr') or []) + '\n' + json.dumps(f)[:600])
        shutil.copyfile(next(o['object'] for o in res['objects']), out)
        if files is not None:
            files.update({n: p for n, p in cc_files(res).items() if n != 'unit.o'})
        return subprocess.CompletedProcess([], 0, '', '')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cc_files(res):
    """{file name: path in the store} of the /out tree of the CC stage(s) of the recipe result RES (the unit object and what a plugin wrote
    next to it, e.g. out=/out/f2i.json): read from the store's stage documents and results, not by running anything"""
    out = {}
    for key in res['stage_keys']:
        docs = glob.glob(os.path.join(STORE, 'stage-docs', '*', key + '.json')) or glob.glob(os.path.join(STORE, 'stage-docs', key + '.json'))
        if not docs or not json.load(open(docs[0]))['stage'].startswith('cc:'):
            continue
        r = sandbox._load(STORE, key)
        for n, e in cas.manifest(r['outputs']['/out'], STORE)['entries'].items():
            out[n] = cas._obj(STORE, e)
    return out


def objdump(obj):
    """the object read back with the pinned binutils (env.json): `objdump -dr` -> {symbol: [lines]} (a relocation is the line after its
    instruction). Disassembly is reading, not compiling: the object is the recipe's."""
    t = subprocess.run(['aarch64-linux-gnu-objdump', '-dr', obj], capture_output=True, text=True, check=True).stdout
    out = {}
    for m in re.finditer(r'^[0-9a-f]+ <([^>]+)>:\n(.*?)(?:\n\n|\Z)', t, re.S | re.M):
        out[m.group(1)] = m.group(2).splitlines()
    return out


def cstring(obj, sec, off):
    """the NUL-terminated string at offset OFF of section SEC of the object"""
    d = elfobj.read(obj).section(sec)
    return d[off:d.index(b'\0', off)].decode()


def section_size(obj, name):
    """size of section NAME (readelf -SW: NOBITS sections have no data in elfobj)"""
    t = subprocess.run(['aarch64-linux-gnu-readelf', '-SW', obj], capture_output=True, text=True, check=True).stdout
    m = re.search(r'\]\s+%s\s+\S+\s+[0-9a-f]+\s+[0-9a-f]+\s+([0-9a-f]+)' % re.escape(name), t)
    return int(m.group(1), 16) if m else None


_BR = {'b', 'bl', 'cbz', 'cbnz', 'tbz', 'tbnz'}


def insns(obj, sym):
    """function SYM of the object as one normalised string: instructions of `objdump -dr`, a call or jump with a relocation names its target
    symbol, a PC-relative literal load shows its value (`ldr w9, =16`), the other branch targets become labels `L<n>:` numbered by address,
    the literal pool is dropped. Comments, addresses and encodings are not kept."""
    rec = []
    for l in objdump(obj).get(sym, []):
        m = re.match(r'\s*([0-9a-f]+):\s+(R_\S+)\s+(\S+)', l)
        if m:
            rec[-1]['rel'] = re.sub(r'\+0x[0-9a-f]+$', '', m.group(3))
            continue
        m = re.match(r'\s*([0-9a-f]+):\s+([0-9a-f]{8})\s+(\S+)\s*(.*)', l)
        ops = re.sub(r'\s*<[^>]*>', '', re.sub(r'\s*//.*$', '', m.group(4))).strip()
        rec.append({'addr': int(m.group(1), 16), 'mn': m.group(3), 'ops': ops, 'raw': int(m.group(2), 16)})
    word = {r['addr']: r['raw'] - (1 << 32 if r['raw'] >> 31 else 0) for r in rec if r['mn'] == '.word'}
    rec = [r for r in rec if r['mn'] != '.word']
    tgt = set()
    for r in rec:
        m = re.search(r'(?:^|, )([0-9a-f]+)$', r['ops'])
        if r['mn'] in _BR or r['mn'].startswith('b.'):
            if r.get('rel'):
                r['ops'] = re.sub(r'(?:^|, )?[0-9a-f]+$', lambda x: (', ' if x.group(0).startswith(', ') else '') + r['rel'], r['ops'])
            elif m:
                tgt.add(int(m.group(1), 16))
        elif r['mn'] == 'ldr' and m and '[' not in r['ops']:
            r['ops'] = r['ops'][:m.start()] + (', ' if r['ops'][:m.start()] else '') + '=%d' % word[int(m.group(1), 16)]
    lab = {a: 'L%d' % (i + 1) for i, a in enumerate(sorted(tgt))}
    out = []
    for r in rec:
        if r['addr'] in lab:
            out.append(lab[r['addr']] + ':')
        ops = r['ops']
        if (r['mn'] in _BR or r['mn'].startswith('b.')) and not r.get('rel'):
            m = re.search(r'(?:^|, )([0-9a-f]+)$', ops)
            ops = ops[:m.start()] + (', ' if ops[:m.start()] else '') + lab[int(m.group(1), 16)]
        out.append((r['mn'] + ' ' + ops).strip())
    return ' '.join(out)


def _tables(obj):
    sec = {}
    t = subprocess.run(['aarch64-linux-gnu-readelf', '-SW', obj], capture_output=True, text=True, check=True).stdout
    for m in re.finditer(r'^\s*\[\s*(\d+)\]\s+(\S+)\s+\S+\s+[0-9a-f]+\s+[0-9a-f]+\s+([0-9a-f]+)', t, re.M):
        sec[int(m.group(1))] = m.group(2)
    syms = {}
    t = subprocess.run(['aarch64-linux-gnu-readelf', '-sW', obj], capture_output=True, text=True, check=True).stdout
    for m in re.finditer(r'^\s*\d+:\s+([0-9a-f]+)\s+(\d+)\s+(\w+)\s+(\w+)\s+\w+\s+(\w+)\s+(\S+)$', t, re.M):
        syms[m.group(6)] = {'value': int(m.group(1), 16), 'size': int(m.group(2)), 'type': m.group(3), 'bind': m.group(4),
                            'sec': sec.get(int(m.group(5))) if m.group(5).isdigit() else m.group(5)}
    rel = {}
    t = subprocess.run(['aarch64-linux-gnu-readelf', '-rW', obj], capture_output=True, text=True, check=True).stdout
    cur = None
    for l in t.splitlines():
        m = re.match(r"Relocation section '\.rela(\S+)'", l)
        if m:
            cur = m.group(1)
            rel[cur] = []
            continue
        m = re.match(r'([0-9a-f]{8,})\s+[0-9a-f]+\s+(\S+)\s+[0-9a-f]+\s+(\S+)(?:\s+([+-])\s+([0-9a-f]+))?', l)
        if m and cur:
            rel[cur].append((int(m.group(1), 16), m.group(2), m.group(3), int(m.group(5) or '0', 16) * (-1 if m.group(4) == '-' else 1)))
    return sec, syms, rel


def symbols_of(obj):
    """{name: {value, size, type, bind, sec}} of the object's symbol table (readelf -sW; sec is the section name or UND/ABS)"""
    return _tables(obj)[1]


def relocs_of(obj):
    """{section name: [(offset, type, symbol, addend)]} (readelf -rW)"""
    return _tables(obj)[2]


def words(obj, sym):
    """the 32-bit words of data symbol SYM: the relocated symbol name (with +addend when not 0) or the signed value, as strings"""
    sec, syms, rel = _tables(obj)
    s = syms[sym]
    data = elfobj.read(obj).section(s['sec'])[s['value']:s['value'] + s['size']]
    rr = {o - s['value']: (n, a) for o, _, n, a in rel.get(s['sec'], []) if s['value'] <= o < s['value'] + s['size']}
    out = []
    for i in range(0, len(data), 4):
        if i in rr:
            n, a = rr[i]
            out.append(n + ('%+d' % a if a else ''))
        else:
            v = int.from_bytes(data[i:i + 4], 'little')
            out.append(str(v - (1 << 32 if v >> 31 else 0)))
    return out


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
