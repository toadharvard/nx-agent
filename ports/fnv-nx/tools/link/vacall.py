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
    todo = [u for u in units if not os.path.exists(unit_file(out, u))]

    def one(u):
        dst = unit_file(out, u)
        tmp = dst + '.tmp'
        if os.path.exists(tmp):
            os.remove(tmp)
        r = subprocess.run([layout.CXX, *flags, '-fsyntax-only', '-fplugin=' + so,
                            '-fplugin-arg-fnvnx_vacall-out=' + tmp, os.path.join(tree, u)],
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
                if len(f) >= 3:
                    got[f[0]].add(tuple(f[3 + int(f[2]):]))
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


def decide(rs, ds, seqs):
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
    v = abi.compare(fs, ds) if fs is not None else None
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
