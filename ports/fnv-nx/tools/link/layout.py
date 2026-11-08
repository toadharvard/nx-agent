"""layout.py - per-unit layout probe for the aggregates that ABI verdicts depend on (docs/LINK.md, "Layouts").

The ABI comparison needs, for every class/struct/union passed or returned by value, how AArch64 (AAPCS64, ILP32)
passes it: in 1-2 general registers, in 1-4 SIMD registers (homogeneous float/vector aggregates, HFA/HVA), or by
reference (larger than 16 bytes, or not trivially copyable / destructible - Itanium C++ ABI). The type is known only
by name inside the unit that declares it, so the probe is a translation unit that includes the unit's source and,
per type, emits

    extern "C" const unsigned __fnvnx_lay_<i>[5] = {sizeof(T), alignof(T), trivial-for-calls, __is_empty(T),
                                                    MSVC C++03 POD};
    extern "C" void __fnvnx_pas_<i>(T a) { __fnvnx_sink((void *)&a); }

The constants are read back from the object. Passing the address of `a` on makes GCC spill the registers it arrived in,
so the SIMD registers stored by the second function (s/d/q/h 0-7, with their width) tell HFA/HVA from the rest;
nothing else in that function touches SIMD registers. A type that does not compile there (local, anonymous, abstract) is dropped and the probe is
recompiled without it.

layouts.tsv: unit, type, size, align, trivial, empty, class (G1/G2: general registers, H<n>x<w>: n SIMD elements of
w bytes, IND: by reference), unit content key, MSVC C++03 POD, POD rule; one line per probed (unit, type); a size of
-1 marks a type the probe could not name. The POD bit decides how MSVC returns a free function's 1/2/4/8-byte
aggregate (in eax:edx only for what MSVC 2008 treats as a POD) and where a __fastcall non-POD's hidden buffer goes
(ecx); Itanium's trivial-for-calls does not tell (V1 A1/A2: a trivially copyable struct with a user constructor comes
back through a hidden buffer). The predicate is MSVC 2008's as V1 measured it on 24 kinds (J-0147,
tests/v1/test_fastcall_nonpod.py): an aggregate (no user constructor, no private / protected data member, no
virtual function), no base class, a trivial destructor and a trivial copy assignment (own or a member's; a reference
member deletes it). A member with a user constructor or copy constructor does not make the class non-POD for MSVC:
the earlier `__is_pod && __is_aggregate && no base` called three such kinds non-POD. Rows of another POD rule (column
10, POD_RULE) are probed again.
"""
import collections
import concurrent.futures
import hashlib
import os
import re
import struct
import subprocess

import elfsyms
import objcache

OBJDUMP = os.environ.get('OBJDUMP', 'aarch64-linux-gnu-objdump')
CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.dirname(os.path.dirname(HERE))

Layout = collections.namedtuple('Layout', 'size align trivial empty cls pod', defaults=(None,))   # pod: None unknown


SEMANTICS = ['-frounding-math', '-fsigned-char', '-fno-strict-aliasing', '-fwrapv', '-fno-delete-null-pointer-checks',
             '-ffp-contract=off']   # compile.sh's MSVC-like semantics (J-0034): probes and emitted host code too
PIC = ['-fPIC']   # compile.sh's GOT addressing of defined variables: emitted code
POD_RULE = 'pod2'   # layouts.tsv column 10: the POD predicate the row was probed with (pod2: V1's, J-0147)
# a type the probe could not name is written with this rule: it was also tried under the namespace the compiler
# names when it knows exactly one (`::T` undeclared; did you mean `ns::T`); older unnameable rows are probed again
UNNAMED_RULE = POD_RULE + '+ns'
_NOT_A_TYPE = re.compile(r':(\d+):\d+: (?:error|note): (?:[\u2018\'](?:::)?[^\u2019\']+[\u2019\'] in namespace '
                         r'[\u2018\']::[\u2019\'] does not name a type|expected a type, got)')
_DID_YOU_MEAN = re.compile(r':(\d+):\d+: error: [\u2018\'](?:::)?(\w+)[\u2019\'] (?:has not been declared|was not '
                           r'declared in this scope|does not name a type); did you mean [\u2018\']([\w:]+)[\u2019\']')
_NAME_AT = r'(?:(?<=^)|(?<=[<,\s]))%s(?![\w])'   # a bare name in a type spelling (not after `::`)


_GAME_FLAGS = {}
_GAME_FLAGS_LOCK = __import__('threading').Lock()   # the emitters call it from worker threads: one plugin build


def game_flags(tree, workdir):
    """compile.sh's own flags (`compile.sh --print-flags`: semantics, code generation, -fPIC, FTOI2 and the S12
    order plugin, built in <workdir>) for code that is linked into the game: vtables.py's emitted slot functions,
    bodies.py's synthesized bodies. One source, no fallback (objects with and without a plugin must never mix):
    a failure stops the step. -fmax-errors is raised so a probe can drop all its failing lines in one round."""
    key = (os.path.abspath(tree), os.path.abspath(workdir), os.environ.get('CXX', ''),
           tuple(os.environ.get(k, '') for k in ('ORDER_MODE', 'ORDER_ARGS', 'ORDER_REFCOUNT', 'ORDER_KEYS', 'OPT',
                                                 'SSE2NEON')))
    with _GAME_FLAGS_LOCK:
        return list(_game_flags(key, tree, workdir))


def _game_flags(key, tree, workdir):
    if key not in _GAME_FLAGS:
        r = subprocess.run(['bash', os.path.join(PORT, 'tools', 'link', 'compile.sh'), '--print-flags', tree, workdir],
                           capture_output=True, text=True)
        fl = r.stdout.split('\n')[:-1] if r.returncode == 0 else []
        if not fl or '-c' not in fl or any(not f.startswith('-') and (i == 0 or fl[i - 1] not in (
                '-include', '-isystem', '-imacros', '-x')) for i, f in enumerate(fl)):
            # every line is a flag: a stray message on stdout (compile.sh's keys step printed one) would become
            # input files and fail every emitted compile
            raise SystemExit('compile.sh --print-flags failed (%s)' % (r.stderr.strip().splitlines() or ['?'])[-1])
        _GAME_FLAGS[key] = ['-fmax-errors=200' if f.startswith('-fmax-errors=') else f for f in fl]
    return _GAME_FLAGS[key]


def game_flags_id(tree, workdir):
    """game_flags for a cache key: the tree and work dir spelled out, plus the plugins' sources (their .so paths
    stay the same when they are rebuilt)."""
    h = hashlib.sha1()
    for d in ('ftoi', 'order'):
        p = os.path.join(PORT, 'toolchain', d)
        for n in sorted(os.listdir(p)) if os.path.isdir(p) else ():
            if n.endswith(('.cc', '.c', '.h', '.sh')):
                h.update(n.encode() + open(os.path.join(p, n), 'rb').read())
    t, w = os.path.abspath(tree), os.path.abspath(workdir)
    return ' '.join(game_flags(tree, workdir)).replace(t, '<tree>').replace(w, '<work>').replace(PORT, '<port>') + \
        ' plugins:' + h.hexdigest()[:16]


def flags(tree):
    sse = os.environ.get('SSE2NEON') or os.path.join(PORT, 'ext', 'sse2neon')
    return ['-mabi=ilp32', '-std=gnu++17', '-c', '-O2', '-fms-extensions', '-fshort-wchar', *SEMANTICS,
            '-fpermissive', '-w',
            '-fmax-errors=200', '-include', os.path.join(PORT, 'shim', 'include', 'fnvnx_compat.h'),
            '-I' + os.path.join(PORT, 'shim', 'include'), '-I' + sse, '-I' + os.path.join(tree, 'include')]


def probe_source(unit_path, types):
    lines = ['#include "%s"' % unit_path,
             'extern "C" void __fnvnx_sink(void *);',
             '#include <tr2/type_traits>',
             'namespace __fnvnx_probe { template <class T> constexpr unsigned triv() '
             '{ return __is_trivially_copyable(T) && __has_trivial_destructor(T); } '
             'template <class T> constexpr unsigned pod() '
             '{ return __is_aggregate(T) && std::tr2::direct_bases<T>::type::empty::value && '
             '__has_trivial_destructor(T) && __is_trivially_assignable(T &, const T &); } }']
    first = len(lines) + 1
    for i, t in enumerate(types):
        q = 'struct ::' + t[7:] if t.startswith('struct ') else '::' + t
        lines.append('extern "C" const unsigned __fnvnx_lay_%d[5] = {sizeof(%s), alignof(%s), '
                     '__fnvnx_probe::triv<%s>(), __is_empty(%s), __fnvnx_probe::pod<%s>()}; '
                     'extern "C" void __fnvnx_pas_%d(%s a) { __fnvnx_sink((void *)&a); }' % (i, q, q, q, q, q, i, q))
    return '\n'.join(lines) + '\n', first


def symbol_words(path, prefix):
    """{symbol name: tuple of uint32} for the initialized constants whose names start with prefix."""
    data = open(path, 'rb').read()
    shoff, = struct.unpack_from('<I', data, 0x20)
    shentsize, shnum, _ = struct.unpack_from('<HHH', data, 0x2e)
    secs = [struct.unpack_from('<IIIIIIIIII', data, shoff + i * shentsize) for i in range(shnum)]
    out = {}
    for s in elfsyms.symbols(path):
        if s.name.startswith(prefix) and s.shndx and s.size:
            off = secs[s.shndx][4] + s.value
            out[s.name] = struct.unpack_from('<%dI' % (s.size // 4), data, off)
    return out


SIMD = re.compile(r'\b([sdqh])([0-7])\b')


def simd_use(obj):
    """{function index: (count of distinct SIMD argument registers, element width) or 'ref'} from the disassembly."""
    r = subprocess.run([OBJDUMP, '-d', '--no-show-raw-insn', obj], capture_output=True, text=True)
    out, cur, regs, byref = {}, None, None, set()
    for line in r.stdout.splitlines():
        m = re.match(r'^[0-9a-f]+ <__fnvnx_pas_(\d+)>:', line)
        if m:
            cur = int(m.group(1)); regs = out.setdefault(cur, set()); continue
        if line and not line.startswith(' '):
            cur = None
        if cur is not None:
            for k, n in SIMD.findall(line.split(':', 1)[-1].split('//')[0]):  # not the address column
                regs.add((k, int(n)))
    res = {}
    for i, regs in out.items():
        if i in byref:
            res[i] = 'ref'
        elif regs:
            w = {'h': 2, 's': 4, 'd': 8, 'q': 16}[sorted(regs)[0][0]]
            res[i] = (len({n for _, n in regs}), w)
    return res


def classify(size, align, trivial, empty, simd):
    if not trivial or simd == 'ref':
        return 'IND'
    if simd:
        n = simd[0]  # an HFA/HVA has no padding: element width = size / count (register letters vary with spills)
        if n and size % n == 0 and size // n in (2, 4, 8, 16):
            return 'H%dx%d' % (n, size // n)
        return 'H?'
    if size > 16:
        return 'IND'
    return 'G1' if size <= 8 else 'G2'


def probe_unit(tree, workdir, unit, types):
    """-> {type: Layout or None}."""
    base = unit.replace('/', '_')
    src, obj = os.path.join(workdir, base + '.probe.cpp'), os.path.join(workdir, base + '.probe.o')
    alive = list(types)
    spell = {t: t for t in types}   # the name the probe source uses (a bare name: the one namespace that has it)
    res = {}
    for _ in range(8):
        if not alive:
            break
        text, first = probe_source(os.path.join(tree, unit), [spell[t] for t in alive])
        open(src, 'w').write(text)
        r = objcache.Result(*objcache.compile(CXX, [*flags(tree), '-fno-ipa-icf'], src, obj, cwd=tree))
        if os.environ.get('FNVNX_PROBE_DEBUG'):
            print(r.stderr[:3000])
        if r.returncode == 0:
            words = symbol_words(obj, '__fnvnx_lay_')
            simd = simd_use(obj)
            for i, t in enumerate(alive):
                w = words.get('__fnvnx_lay_%d' % i)
                if w and len(w) >= 5:
                    res[t] = Layout(w[0], w[1], w[2], w[3], classify(w[0], w[1], w[2], w[3], simd.get(i)), w[4])
            break
        bad = {int(n) - first for n in re.findall(r'%s:(\d+):\d+: error' % re.escape(os.path.basename(src)),
                                                  r.stderr)}
        bad = {b for b in bad if 0 <= b < len(alive)}
        if not bad:  # error elsewhere (the unit itself no longer compiles): give up on this unit
            break
        # a C name's declaration read from the source loses the namespace it stands in (`RefBlockArgument` declared
        # in `namespace fnv_00cc83b0`): GCC names the namespace when exactly one declares the name ("did you mean";
        # several get "suggested alternatives") - such a type is probed again under that name
        # a class whose name a function of the same name hides (`struct FUN_0040c0e0` and `FUN_0040c0e0(...)`) is
        # named with its elaborated specifier, `struct ::T` (the class / struct tags are interchangeable there)
        respelled = set()
        for line in r.stderr.splitlines():
            if not line.startswith(src):
                continue
            m = _DID_YOU_MEAN.search(line[len(src):])
            if m:
                # the bare name may be the type, its first component (`PacketMask::Bits`) or a template and its
                # arguments (`Ref<SolverInfo>`, both in `ApplyForcesStepPrivate`): each is qualified in place
                i, name, sug = int(m.group(1)) - first, m.group(2), m.group(3)
                if i in bad and sug.endswith('::' + name) and not spell[alive[i]].startswith('struct '):
                    new = re.sub(_NAME_AT % re.escape(name), sug, spell[alive[i]])
                    if new != spell[alive[i]]:
                        spell[alive[i]] = new
                        respelled.add(i)
                continue
            m = _NOT_A_TYPE.search(line[len(src):])
            if m:
                i = int(m.group(1)) - first
                if i in bad and i not in respelled and spell[alive[i]] == alive[i] and '<' not in alive[i]:
                    spell[alive[i]] = 'struct ' + alive[i]
                    respelled.add(i)
        alive = [t for i, t in enumerate(alive) if i not in bad or i in respelled]
    for t in types:
        res.setdefault(t, None)
    return res


_INC = re.compile(r'^[ \t]*#[ \t]*include[ \t]*([<"])([^>"]+)[>"]', re.M)
_FILE_SHA, _KEYS = {}, {}


def unit_key(tree, unit):
    """Content key of a unit for every probe cache (layouts, vtables, emitted slot functions): its prepared source
    and every local header it reaches (#include resolved like the compiler: the including file's directory, then
    shim/include, SSE2NEON, <tree>/include; the forced fnvnx_compat.h first), and the probe flags with the tree and
    port paths taken out - a row measured on another tree with the same text keeps its key, any header change
    drops it (S9: seeded caches carried layouts from before a CRITICAL_SECTION change)."""
    k = (tree, unit)
    if k in _KEYS:
        return _KEYS[k]
    sse = os.environ.get('SSE2NEON') or os.path.join(PORT, 'ext', 'sse2neon')
    dirs = [os.path.join(PORT, 'shim', 'include'), sse, os.path.join(tree, 'include')]
    h = hashlib.sha1(' '.join(flags(tree)).replace(tree, '<tree>').replace(PORT, '<port>').replace(sse, '<sse>')
                     .encode())
    todo = [os.path.join(tree, unit), os.path.join(PORT, 'shim', 'include', 'fnvnx_compat.h')]
    seen = set()
    while todo:
        f = os.path.normpath(todo.pop())
        if f in seen or not os.path.isfile(f):
            continue
        seen.add(f)
        if f not in _FILE_SHA:
            data = open(f, 'rb').read()
            incs = [(kind, name) for kind, name in _INC.findall(data.decode('utf-8', 'surrogateescape'))]
            _FILE_SHA[f] = (hashlib.sha1(data).hexdigest(), incs)
        sha, incs = _FILE_SHA[f]
        rel = f.replace(tree, '<tree>').replace(PORT, '<port>').replace(sse, '<sse>')
        h.update(('%s %s\n' % (rel, sha)).encode('utf-8', 'surrogateescape'))
        for kind, name in incs:
            for d in ([os.path.dirname(f)] if kind == '"' else []) + dirs:
                g = os.path.join(d, name)
                if os.path.isfile(g):
                    todo.append(g)
                    break
    _KEYS[k] = h.hexdigest()[:20]
    return _KEYS[k]


def load(path, tree=None):
    """layouts.tsv -> {(unit, type): Layout or None}; with tree, rows whose unit content key differs (or that have
    none, or no POD column or another POD rule: older files) are left out; without, their POD bit is unknown."""
    lay = {}
    if os.path.exists(path):
        for line in open(path, encoding='utf-8', errors='surrogateescape'):
            if line.startswith('#'):
                continue
            f = line.rstrip('\n').split('\t')
            if len(f) < 7:
                continue
            if tree is not None and (len(f) < 10 or f[7] != unit_key(tree, f[0]) or
                                     f[9] != (UNNAMED_RULE if f[2] == '-1' else POD_RULE)):
                continue
            pod = int(f[8]) if len(f) > 9 and f[8] in ('0', '1') and f[9] == POD_RULE else None
            lay[(f[0], f[1])] = None if f[2] == '-1' else Layout(int(f[2]), int(f[3]), int(f[4]), int(f[5]), f[6], pod)
    return lay


def run(out, tree, jobs=4):
    req = collections.defaultdict(set)
    for line in open(os.path.join(out, 'probe_requests.tsv'), encoding='utf-8', errors='surrogateescape'):
        u, t = line.rstrip('\n').split('\t')
        req[u].add(t)
    path = os.path.join(out, 'layouts.tsv')
    before = len(load(path))
    lay = load(path, tree)   # rows of another content (a seed from an older tree) are probed again
    print('probe: %d cached rows, %d dropped (unit content changed)' % (len(lay), before - len(lay)), flush=True)
    todo = {u: sorted(t for t in ts if (u, t) not in lay) for u, ts in req.items()}
    todo = {u: ts for u, ts in todo.items() if ts}
    workdir = os.path.join(out, 'probe')
    os.makedirs(workdir, exist_ok=True)
    print('probe: %d units, %d types (%d cached)' % (len(todo), sum(map(len, todo.values())), len(lay)), flush=True)
    with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
        futs = {ex.submit(probe_unit, tree, workdir, u, ts): u for u, ts in todo.items()}
        for f in concurrent.futures.as_completed(futs):
            for t, l in f.result().items():
                lay[(futs[f], t)] = l
    with open(path, 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#unit\ttype\tsize\talign\ttrivial\tempty\tclass\tunit content key\tmsvc pod\tpod rule\n')
        for (u, t), l in sorted(lay.items()):
            if l is None:
                f.write('%s\t%s\t-1\t0\t0\t0\t?\t%s\t?\t%s\n' % (u, t, unit_key(tree, u), UNNAMED_RULE))
            else:
                f.write('%s\t%s\t%d\t%d\t%d\t%d\t%s\t%s\t%s\t%s\n' % (u, t, *l[:5], unit_key(tree, u),
                                                                     '?' if l.pod is None else l.pod, POD_RULE))
    got = sum(1 for l in lay.values() if l)
    print('layouts: %d known, %d not nameable' % (got, len(lay) - got))
    print(objcache.summary('layouts'), flush=True)
    print('classes:', dict(collections.Counter(l.cls for l in lay.values() if l)))
