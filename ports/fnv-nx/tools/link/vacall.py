"""vacall.py - call-site argument types of variadic references (stream S2c-abi; docs/LINK.md "Variadic pairs").

A reference declared `int f(...)` (or variadic after some named parameters) whose definition at the address reads
fixed parameters from the anonymous part cannot be decided from the declarations (V1 VA-U1..U4). The call sites
can: the plugin fnvnx_vacall.so records, for every call through a variadic declaration in a unit, the classes of
the actual arguments after the default promotions. When all calls of one reference in a unit agree, resolve
compares the definition with a fixed reference signature built from them and bridges it like any fixed pair.

  resolve 1 writes vacall_requests.tsv (unit, undefined symbol);
  `fnvlink.py probe` (vacall.run) parses those units with -fsyntax-only and the plugin: out/vacalls/<unit>.tsv;
  resolve 2 reads them (load) and builds the signature (sig_for).

Outputs are derived from the decomp (local, never committed).
"""
import collections
import concurrent.futures
import copy
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'vacall', 'fnvnx_vacall.cc')
TYPE_OF = {'I8': 'int', 'I16': 'int', 'I32': 'int', 'P': 'void *', 'I64': '__int64', 'F64': 'double'}


def plugin(out):
    """Build fnvnx_vacall.so into out/vacalls (rebuilt when the source is newer) -> its path, or None."""
    cxx = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
    try:
        pdir = subprocess.run([cxx, '-print-file-name=plugin'], capture_output=True, text=True).stdout.strip()
    except OSError:
        return None
    if not os.path.isfile(os.path.join(pdir, 'include', 'gcc-plugin.h')):
        return None
    d = os.path.join(out, 'vacalls')
    os.makedirs(d, exist_ok=True)
    so = os.path.join(d, 'fnvnx_vacall.so')
    if not os.path.exists(so) or os.path.getmtime(SRC) > os.path.getmtime(so):
        r = subprocess.run([os.environ.get('HOSTCXX', 'g++'), '-shared', '-fPIC', '-fno-rtti', '-O2',
                            '-I' + os.path.join(pdir, 'include'), '-o', so + '.tmp', SRC],
                           capture_output=True, text=True)
        if r.returncode:
            print('vacall: plugin build failed: %s' % r.stderr[:2000], flush=True)
            return None
        os.replace(so + '.tmp', so)
    return so


def unit_file(out, unit):
    return os.path.join(out, 'vacalls', unit.replace('/', '_') + '.tsv')


def run(out, tree, jobs=4):
    """Record the variadic calls of every unit vacall_requests.tsv names (already recorded units are kept)."""
    req = os.path.join(out, 'vacall_requests.tsv')
    if not os.path.exists(req):
        return
    units = sorted({l.split('\t')[0] for l in open(req, encoding='utf-8', errors='surrogateescape') if l.strip()})
    so = plugin(out)
    if so is None:
        print('vacall: no plugin (gcc plugin headers missing): %d units left undecided' % len(units), flush=True)
        return
    import layout
    flags = layout.flags(tree)
    wants = collections.defaultdict(set)
    for l in open(req, encoding='utf-8', errors='surrogateescape'):
        f = l.rstrip('\n').split('\t')
        if len(f) >= 2:
            wants[f[0]].add(f[1])

    def want_text(u):
        return ''.join(n + '\n' for n in sorted(wants[u]))

    def fresh(u):   # recorded, with the same wanted callees (their format literals)
        w = unit_file(out, u) + '.want'
        return os.path.exists(unit_file(out, u)) and os.path.exists(w) and open(w).read() == want_text(u)
    todo = [u for u in units if not fresh(u)]

    def one(u):
        dst = unit_file(out, u)
        tmp = dst + '.tmp'
        if os.path.exists(tmp):
            os.remove(tmp)
        open(dst + '.want', 'w').write(want_text(u))
        r = subprocess.run([layout.CXX, *flags, '-fsyntax-only', '-fplugin=' + so,
                            '-fplugin-arg-fnvnx_vacall-out=' + tmp,
                            '-fplugin-arg-fnvnx_vacall-want=' + dst + '.want', os.path.join(tree, u)],
                           cwd=tree, capture_output=True, text=True)
        if not os.path.exists(tmp):
            open(tmp, 'w').close()
        os.replace(tmp, dst)   # a unit that stops on an error still gives the calls it parsed
        return r.returncode
    with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
        rcs = list(ex.map(one, todo))
    print('vacall: %d units (%d recorded before), %d with errors' % (len(units), len(units) - len(todo),
                                                                    sum(1 for r in rcs if r)), flush=True)


def load(out, unit, _cache={}):
    """{callee assembler name: set of tuples of the anonymous arguments' classes} of one unit's calls, None when the
    unit was not recorded."""
    p = unit_file(out, unit)
    if p not in _cache:
        if not os.path.exists(p):
            _cache[p] = None
        else:
            got = collections.defaultdict(set)
            for line in open(p, encoding='utf-8', errors='surrogateescape'):
                f = line.rstrip('\n').split('\t')
                if f[0] in ('VADEF', 'FMT'):
                    continue
                if len(f) >= 3:
                    got[f[0]].add(tuple(f[3 + int(f[2]):]))
            _cache[p] = got
    return _cache[p]


def load_defs(out, unit, _cache={}):
    """{assembler name: va_reads} of the variadic functions one unit defines (abi.va_reads_check's form: ('none',),
    ('escape',) or ('reads', classes)), None when the unit was not recorded."""
    p = unit_file(out, unit)
    if p not in _cache:
        if not os.path.exists(p):
            _cache[p] = None
        else:
            got = {}
            for line in open(p, encoding='utf-8', errors='surrogateescape'):
                f = line.rstrip('\n').split('\t')
                if f[0] == 'VADEF' and len(f) >= 3:
                    got[f[1]] = (f[2], tuple(f[3:])) if f[2] == 'reads' else (f[2],)
            _cache[p] = got
    return _cache[p]


def sig_for(ref, anon):
    """A fixed copy of the variadic reference Sig `ref` whose anonymous part is the call site's classes `anon`, or
    None when a class has no fixed spelling here (aggregates by value, long double)."""
    extra = []
    for c in anon:
        t = TYPE_OF.get(c)
        if t is None:
            return None
        extra.append(t)
    s = copy.copy(ref)
    named = [p for p in (ref.raw_params or []) if p.strip() != '...']
    s.raw_params = named + extra
    s.params = s.raw_params
    return s


def needs(v):
    """Does abi.compare's verdict v say the definition reads the reference's anonymous arguments?"""
    return v.kind == 'manual' and 'varargs' in v.classes and \
        any('anonymous argument of the reference' in n for n in v.notes)


def decide(rs, ds, seqs, va_reads=None):
    """(verdict, fixed reference Sig, call-site classes, stat) for a variadic reference rs to the definition ds whose
    calls in the unit pass `seqs` (set of class tuples, None when unrecorded); verdict None when undecided."""
    import abi
    if not seqs:
        return None, None, None, 'vacall_unrecorded'
    dnamed = [p for p in (ds.raw_params or []) if p.strip() != '...']
    if len(dnamed) < len(ds.raw_params or []):
        # a variadic definition: only the words its named parameters read matter; the rest stays anonymous on both
        # sides (0x00406d00, snprintf-like: its callers pass any number of arguments)
        k = max(0, len(dnamed) - len([p for p in (rs.raw_params or []) if p.strip() != '...']))
        if any(len(q) < k for q in seqs):
            return None, None, None, 'vacall_not_bridgeable'
        seqs = {q[:k] for q in seqs}
    if len(seqs) > 1:
        return None, None, None, 'vacall_sites_disagree'
    seq = next(iter(seqs))
    fs = sig_for(rs, seq)
    v = abi.compare(fs, ds, va_reads=va_reads) if fs is not None else None
    if v is not None and v.kind not in ('same', 'bridge'):
        # words regrouped (a double or long long read as two ints, V1 VA-U1/U2): decide on bytes; scalars need no
        # layouts, an aggregate makes abi2 give up
        import abi2
        p2 = abi2.compare(fs, ds, lambda t: None, lambda t: None)
        if p2 is not None and p2.kind in ('same', 'bridge'):
            v = abi.Verdict()
            v.kind, v.classes, v.notes, v.plan = p2.kind, list(p2.classes), list(p2.notes), p2
    if v is None or v.kind not in ('same', 'bridge'):
        return None, None, None, 'vacall_not_bridgeable'
    v.notes.append('every call site passes (%s)' % ', '.join(seq))
    v.classes.append('varargs-callsite')
    return v, fs, seq, 'vacall_decided'


def load_fmts(out, unit, _cache={}):
    """{callee: [{argument index: format bytes}] per call} of a unit's calls of the wanted callees (FMT lines), None
    when the unit was not recorded."""
    p = unit_file(out, unit)
    if p not in _cache:
        if not os.path.exists(p):
            _cache[p] = None
        else:
            got = collections.defaultdict(list)
            for line in open(p, encoding='utf-8', errors='surrogateescape'):
                f = line.rstrip('\n').split('\t')
                if f[0] == 'FMT' and len(f) >= 3:
                    lits = {}
                    for x in f[3:]:
                        i, _, h = x.partition(':')
                        lits[int(i)] = bytes.fromhex(h)
                    got[f[1]].append(lits)
            _cache[p] = got
    return _cache[p]


def parse_format(fmt):
    """printf conversions of an MSVC 2008 format string -> the classes vsnprintf reads, in order, or None when one is
    not modelled (%n, positional $, long double, an unknown conversion). `*` width/precision read an int; h/l/w
    lengths keep a 4-byte word (MSVC long is 32 bits), ll / I64 / q / j a 64-bit integer; e f g a read a double;
    s S p Z read a pointer."""
    out, i, n = [], 0, len(fmt)
    while i < n:
        if fmt[i:i + 1] != b'%':
            i += 1
            continue
        i += 1
        if fmt[i:i + 1] == b'%':
            i += 1
            continue
        while i < n and fmt[i:i + 1] in b'-+ #0':
            i += 1
        if fmt[i:i + 1] == b'*':
            out.append('I32'); i += 1
        while i < n and fmt[i:i + 1].isdigit():
            i += 1
        if fmt[i:i + 1] == b'$':
            return None
        if fmt[i:i + 1] == b'.':
            i += 1
            if fmt[i:i + 1] == b'*':
                out.append('I32'); i += 1
            while i < n and fmt[i:i + 1].isdigit():
                i += 1
        wide = False
        for pre, w in ((b'I64', True), (b'I32', False), (b'll', True), (b'hh', False), (b'h', False), (b'l', False),
                       (b'w', False), (b'q', True), (b'j', True), (b'z', False), (b't', False), (b'I', False),
                       (b'L', None)):
            if fmt.startswith(pre, i):
                if w is None:
                    return None   # long double: binary128 here, 8 bytes on MSVC
                wide = w
                i += len(pre)
                break
        c = fmt[i:i + 1]
        i += 1
        if c in (b'd', b'i', b'u', b'o', b'x', b'X', b'c', b'C'):
            out.append('I64' if wide else 'I32')
        elif c in (b'e', b'E', b'f', b'F', b'g', b'G', b'a', b'A'):
            out.append('F64')
        elif c in (b's', b'S', b'p', b'Z'):
            out.append('P')
        else:
            return None
    return out


def format_reads(rs, ds, sites):
    """('exact',) when every call site's format literal (the definition's last named parameter) reads, conversion by
    conversion, a word of the width the fixed reference passes there; None otherwise (a non-literal format, a
    conversion not modelled, other widths, unrecorded calls)."""
    import abi
    if not sites:
        return None
    dnamed = [p for p in (ds.raw_params or []) if p.strip() != '...']
    if not dnamed or abi.classify(dnamed[-1]) != 'P':
        return None
    k = len(dnamed) - 1 + (1 if ds.member else 0)          # the format's index among the call's arguments
    tail = [abi.classify(p) for p in (rs.raw_params or [])[len(dnamed):]]
    want = [abi.VA_WIDTH.get(c) for c in tail]
    if None in want:
        return None
    for lits in sites:
        fmt = lits.get(k)
        conv = parse_format(fmt) if fmt is not None else None
        if conv is None or len(conv) > len(tail):
            return None
        if [abi.VA_WIDTH.get(c) for c in conv] != want[:len(conv)]:
            return None
    return ('exact',)


def site_convs(rs, ds, sites):
    """The set of conversion tuples the call sites' format literals read, or None (non-literal, not modelled)."""
    import abi
    dnamed = [p for p in (ds.raw_params or []) if p.strip() != '...']
    if not sites or not dnamed or abi.classify(dnamed[-1]) != 'P':
        return None
    k = len(dnamed) - 1 + (1 if ds.member else 0)
    got = set()
    for lits in sites:
        fmt = lits.get(k)
        conv = parse_format(fmt) if fmt is not None else None
        if conv is None:
            return None
        got.add(tuple(conv))
    return got


def decide_format(rs, ds, sites):
    """(verdict, bridge key suffix, stat) for a fixed reference rs to a definition ds that hands its va_list to
    vsnprintf, from the call sites' format literals: `exact` widths -> abi.compare with ('exact',); one conversion
    list for every site whose words regroup (two ints read as %lld) -> the definition compared as fixed with those
    conversions (abi, then abi2 bytes); else (None, None, stat)."""
    import abi
    import abi2
    r = format_reads(rs, ds, sites)
    if r is not None:
        v = abi.compare(rs, ds, va_reads=r)
        if v.kind in ('same', 'bridge'):
            v.classes.append('varargs-format')
            return v, None, 'format_exact'
    convs = site_convs(rs, ds, sites)
    if not convs:
        return None, None, 'format_unknown'
    if len(convs) > 1:
        return None, None, 'format_sites_disagree'
    conv = next(iter(convs))
    fd = copy.copy(ds)
    fd.raw_params = [p for p in (ds.raw_params or []) if p.strip() != '...'] + [TYPE_OF[c] for c in conv]
    fd.params = fd.raw_params
    v = abi.compare(rs, fd)
    if v.kind not in ('same', 'bridge'):
        p2 = abi2.compare(rs, fd, lambda t: None, lambda t: None)
        if p2 is None or p2.kind not in ('same', 'bridge'):
            return None, None, 'format_not_bridgeable'
        v = abi.Verdict()
        v.kind, v.classes, v.notes, v.plan = p2.kind, list(p2.classes), list(p2.notes), p2
    v.classes.append('varargs-format')
    v.notes.append('every call site formats (%s)' % ', '.join(conv))
    return v, '|fmt ' + ','.join(conv), 'format_bridged'
