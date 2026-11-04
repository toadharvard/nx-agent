"""layout.py - per-unit layout probe for the aggregates that ABI verdicts depend on (docs/LINK.md, "Layouts").

The ABI comparison needs, for every class/struct/union passed or returned by value, how AArch64 (AAPCS64, ILP32)
passes it: in 1-2 general registers, in 1-4 SIMD registers (homogeneous float/vector aggregates, HFA/HVA), or by
reference (larger than 16 bytes, or not trivially copyable / destructible - Itanium C++ ABI). The type is known only
by name inside the unit that declares it, so the probe is a translation unit that includes the unit's source and,
per type, emits

    extern "C" const unsigned __fnvnx_lay_<i>[4] = {sizeof(T), alignof(T), trivial-for-calls, __is_empty(T)};
    extern "C" void __fnvnx_pas_<i>(T a) { __fnvnx_sink((void *)&a); }

The constants are read back from the object. Passing the address of `a` on makes GCC spill the registers it arrived in,
so the SIMD registers stored by the second function (s/d/q/h 0-7, with their width) tell HFA/HVA from the rest;
nothing else in that function touches SIMD registers. A type that does not compile there (local, anonymous, abstract) is dropped and the probe is
recompiled without it.

layouts.tsv: unit, type, size, align, trivial, empty, class (G1/G2: general registers, H<n>x<w>: n SIMD elements of
w bytes, IND: by reference), one line per probed (unit, type); a size of -1 marks a type the probe could not name.
"""
import collections
import concurrent.futures
import os
import re
import struct
import subprocess

import elfsyms

OBJDUMP = os.environ.get('OBJDUMP', 'aarch64-linux-gnu-objdump')
CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.dirname(os.path.dirname(HERE))

Layout = collections.namedtuple('Layout', 'size align trivial empty cls')


SEMANTICS = ['-frounding-math', '-fsigned-char', '-fno-strict-aliasing', '-fwrapv', '-fno-delete-null-pointer-checks',
             '-ffp-contract=off']   # compile.sh's MSVC-like semantics (J-0034): probes and emitted host code too
PIC = ['-fPIC', '-fno-semantic-interposition']   # compile.sh's GOT addressing of defined variables: emitted code


def flags(tree):
    sse = os.environ.get('SSE2NEON') or os.path.join(PORT, 'ext', 'sse2neon')
    return ['-mabi=ilp32', '-std=gnu++17', '-c', '-O2', '-fms-extensions', '-fshort-wchar', *SEMANTICS,
            '-fpermissive', '-w',
            '-fmax-errors=200', '-include', os.path.join(PORT, 'shim', 'include', 'fnvnx_compat.h'),
            '-I' + os.path.join(PORT, 'shim', 'include'), '-I' + sse, '-I' + os.path.join(tree, 'include')]


def probe_source(unit_path, types):
    lines = ['#include "%s"' % unit_path,
             'extern "C" void __fnvnx_sink(void *);',
             'namespace __fnvnx_probe { template <class T> constexpr unsigned triv() '
             '{ return __is_trivially_copyable(T) && __has_trivial_destructor(T); } }']
    first = len(lines) + 1
    for i, t in enumerate(types):
        q = '::' + t
        lines.append('extern "C" const unsigned __fnvnx_lay_%d[4] = {sizeof(%s), alignof(%s), '
                     '__fnvnx_probe::triv<%s>(), __is_empty(%s)}; extern "C" void __fnvnx_pas_%d(%s a) '
                     '{ __fnvnx_sink((void *)&a); }' % (i, q, q, q, q, i, q))
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
    res = {}
    for _ in range(6):
        if not alive:
            break
        text, first = probe_source(os.path.join(tree, unit), alive)
        open(src, 'w').write(text)
        r = subprocess.run([CXX, *flags(tree), '-fno-ipa-icf', src, '-o', obj], cwd=tree, capture_output=True, text=True,
                           errors='replace')
        if os.environ.get('FNVNX_PROBE_DEBUG'):
            print(r.stderr[:3000])
        if r.returncode == 0:
            words = symbol_words(obj, '__fnvnx_lay_')
            simd = simd_use(obj)
            for i, t in enumerate(alive):
                w = words.get('__fnvnx_lay_%d' % i)
                if w:
                    res[t] = Layout(w[0], w[1], w[2], w[3], classify(w[0], w[1], w[2], w[3], simd.get(i)))
            break
        bad = {int(n) - first for n in re.findall(r'%s:(\d+):\d+: error' % re.escape(os.path.basename(src)),
                                                  r.stderr)}
        bad = {b for b in bad if 0 <= b < len(alive)}
        if not bad:  # error elsewhere (the unit itself no longer compiles): give up on this unit
            break
        alive = [t for i, t in enumerate(alive) if i not in bad]
    for t in types:
        res.setdefault(t, None)
    return res


def load(path):
    lay = {}
    if os.path.exists(path):
        for line in open(path, encoding='utf-8', errors='surrogateescape'):
            if line.startswith('#'):
                continue
            f = line.rstrip('\n').split('\t')
            if len(f) < 7:
                continue
            lay[(f[0], f[1])] = None if f[2] == '-1' else Layout(int(f[2]), int(f[3]), int(f[4]), int(f[5]), f[6])
    return lay


def run(out, tree, jobs=4):
    req = collections.defaultdict(set)
    for line in open(os.path.join(out, 'probe_requests.tsv'), encoding='utf-8', errors='surrogateescape'):
        u, t = line.rstrip('\n').split('\t')
        req[u].add(t)
    path = os.path.join(out, 'layouts.tsv')
    lay = load(path)
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
        f.write('#unit\ttype\tsize\talign\ttrivial\tempty\tclass\n')
        for (u, t), l in sorted(lay.items()):
            if l is None:
                f.write('%s\t%s\t-1\t0\t0\t0\t?\n' % (u, t))
            else:
                f.write('%s\t%s\t%d\t%d\t%d\t%d\t%s\n' % (u, t, *l))
    got = sum(1 for l in lay.values() if l)
    print('layouts: %d known, %d not nameable' % (got, len(lay) - got))
    print('classes:', dict(collections.Counter(l.cls for l in lay.values() if l)))
