#!/usr/bin/env python3
"""objcache.py - content-addressed cache of the link's own compiles (T1, incremental link).

fnvlink's probe, vtables and bodies steps compile the game's host units again and again with lines appended
(vtables.compile_probe: layout and vtable probes, the emitted slot functions, the synthesized bodies; layout.probe_unit:
the aggregate layout probe). On a warm machine almost all of those compiles had the same inputs in the last link.
This cache gives back the compiler's result - exit status, stderr and the object's bytes - when, and only when,
every input is the same:

    manifest key  sha256(CACHE_V, the compiler (driver, cc1plus and as bytes, --version), the working directory, the
                  command line verbatim (no path is rewritten: an object names its source path, so a hit must have
                  the same paths), the content of every file a command-line token names (-include headers, the
                  plugins' .so, the order-keys file), the names of every file under each -I/-isystem/-iquote
                  directory (a header added there can shadow another), the compiler's environment (CPATH, LANG, ...)
                  and the source text)
    result        one of the manifest's variants whose every dependency (gcc -MD: each file the compile read) still
                  has the recorded sha256, and whose dependencies' directories still list the same file names

A failed compile is cached as well (gcc -MD writes the dependencies even then): the probes drop the lines GCC names
and compile again, so a replay must see the same errors. A timeout is never cached. -MD does not change the
object (checked: the same bytes with and without it); the object is written by the compiler here, never edited.

    FNVNX_LINK_OBJCACHE=<dir>   the cache (default: $FNVNX_TOOL_CACHE/linkobj, else ~/.cache/fnvnx/linkobj);
                                none or off: no cache (every compile runs, as before)
    FNVNX_LINK_OBJCACHE_VERIFY=1  compile even on a hit and compare status, stderr and object bytes with the cached
                                result; a mismatch is counted (stats) and the fresh result is used and stored

Decomp-derived objects: the cache stays on the machine (or in fnv-nx-build), never in this repository.

    objcache.py stat [dir]
    objcache.py prune [dir] [max MB]   drop the least recently used results above max MB (default:
                                       $FNVNX_LINK_OBJCACHE_MAX_MB, else 8192)
"""
import collections, hashlib, json, os, re, shutil, subprocess, sys, threading, time

CACHE_V = 'linkobj-1'
VARIANTS = 8            # dependency variants kept per manifest
ENV_KEYS = ('CPATH', 'C_INCLUDE_PATH', 'CPLUS_INCLUDE_PATH', 'OBJC_INCLUDE_PATH', 'GCC_EXEC_PREFIX', 'COMPILER_PATH',
            'LIBRARY_PATH', 'SOURCE_DATE_EPOCH', 'LANG', 'LC_ALL', 'LC_MESSAGES', 'LC_CTYPE', 'GCC_COLORS',
            'GCC_URLS', 'TERM', 'DEPENDENCIES_OUTPUT', 'SUNPRO_DEPENDENCIES')
Result = collections.namedtuple('Result', 'returncode stderr')   # what the callers read of subprocess.run's result
STATS = collections.Counter()
_lock = threading.Lock()
_fh = {}                # (path, size, mtime_ns) -> sha256
_names = {}             # (dir, recursive) -> sha256 of the file names under it
_cc = {}                # compiler command -> identity


def cache_dir():
    d = os.environ.get('FNVNX_LINK_OBJCACHE')
    if d is None:
        d = os.path.join(os.environ.get('FNVNX_TOOL_CACHE') or os.path.join(os.path.expanduser('~'), '.cache', 'fnvnx'),
                         'linkobj')
    return None if d in ('', 'none', 'off', '0') else d


def file_hash(p):
    try:
        st = os.stat(p)
    except OSError:
        return None
    if not os.path.isfile(p):
        return None
    k = (p, st.st_size, st.st_mtime_ns)
    with _lock:
        h = _fh.get(k)
    if h is None:
        d = hashlib.sha256()
        with open(p, 'rb') as f:
            for b in iter(lambda: f.read(1 << 20), b''):
                d.update(b)
        h = d.hexdigest()
        with _lock:
            _fh[k] = h
    return h


def names_hash(d, recursive):
    """sha256 of the file names under d (recursive: every subdirectory too); a directory that does not exist hashes
    as such. Memoized per process: the include directories do not change while a link runs."""
    k = (d, recursive)
    with _lock:
        h = _names.get(k)
    if h is None:
        if not os.path.isdir(d):
            h = 'absent'
        elif recursive:
            got = []
            for root, dirs, files in os.walk(d):
                dirs.sort()
                rel = os.path.relpath(root, d)
                got += [os.path.join(rel, n) for n in sorted(files)]
            h = hashlib.sha256('\n'.join(got).encode('utf-8', 'surrogateescape')).hexdigest()
        else:
            h = hashlib.sha256('\n'.join(sorted(os.listdir(d))).encode('utf-8', 'surrogateescape')).hexdigest()
        with _lock:
            _names[k] = h
    return h


def compiler_id(cxx):
    """The compiler's identity: the bytes of the driver (a wrapper script and what it runs: cc1plus and as, which
    the driver names), its --version and target. Computed once per command."""
    with _lock:
        got = _cc.get(cxx)
    if got is not None:
        return got
    h = hashlib.sha256(cxx.encode())
    path = shutil.which(cxx) or cxx
    h.update(('driver %s %s\n' % (path, file_hash(path))).encode())
    for args in (['--version'], ['-dumpmachine'], ['-print-prog-name=cc1plus'], ['-print-prog-name=as'],
                 ['-print-prog-name=collect2']):
        try:
            r = subprocess.run([cxx, *args], capture_output=True, text=True, timeout=60)
            out = r.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            out = '?'
        h.update(('%s -> %s\n' % (' '.join(args), out)).encode())
        if args[0].startswith('-print-prog-name='):
            p = out if os.path.isabs(out) else (shutil.which(out) or '')
            h.update(('  %s %s\n' % (p, file_hash(p) if p else None)).encode())
    got = h.hexdigest()
    with _lock:
        _cc[cxx] = got
    return got


INC_DIR = ('-I', '-isystem', '-iquote', '-idirafter')


def command_inputs(argv, cwd):
    """(files the command line names, include directories): every token, and the value after the last '=' of a
    token, that is an existing file is hashed (never the -o / -MF outputs); -I.. / -isystem .. directories get their
    file-name listing."""
    files, dirs = [], []
    for i, t in enumerate(argv):
        prev = argv[i - 1] if i else ''
        if prev in ('-o', '-MF', '-MT', '-MQ'):
            continue
        if prev in INC_DIR:
            dirs.append(t)
            continue
        pre = next((x for x in INC_DIR if t.startswith(x) and len(t) > len(x)), None)
        if pre:
            dirs.append(t[len(pre):])
            continue
        for c in sorted({t, t.rsplit('=', 1)[-1]}):
            p = c if os.path.isabs(c) else os.path.join(cwd, c)
            if c and os.path.isfile(p):
                files.append(p)
    return files, [d if os.path.isabs(d) else os.path.join(cwd, d) for d in dirs]


def manifest_key(cxx, argv, cwd, src_text):
    h = hashlib.sha256()
    h.update(('%s\ncompiler %s\ncwd %s\n' % (CACHE_V, compiler_id(cxx), cwd)).encode())
    h.update(json.dumps(argv).encode('utf-8', 'surrogateescape'))
    files, dirs = command_inputs(argv, cwd)
    for p in files:
        h.update(('\nfile %s %s' % (p, file_hash(p))).encode('utf-8', 'surrogateescape'))
    for d in dirs:
        h.update(('\nnames %s %s' % (d, names_hash(d, True))).encode('utf-8', 'surrogateescape'))
    for k in ENV_KEYS:
        if k in os.environ:
            h.update(('\nenv %s=%s' % (k, os.environ[k])).encode('utf-8', 'surrogateescape'))
    h.update(b'\nsource\n' + src_text)
    return h.hexdigest()


def parse_deps(path, cwd):
    """gcc -MD's make rule -> [absolute paths] (the target dropped)."""
    try:
        text = open(path, encoding='utf-8', errors='surrogateescape').read()
    except OSError:
        return None
    text = text.replace('\\\n', ' ')
    rules = text.split('\n')
    if not rules or ':' not in rules[0]:
        return None
    body = rules[0].split(': ', 1)[1] if ': ' in rules[0] else rules[0].split(':', 1)[1]
    out, cur = [], ''
    for tok in re.split(r'(?<!\\) ', body.strip()):
        if not tok:
            continue
        p = tok.replace('\\ ', ' ')
        out.append(os.path.normpath(p if os.path.isabs(p) else os.path.join(cwd, p)))
    return out


REL_QUOTED = re.compile(rb'^\s*#\s*include\s*"(?!/)', re.M)


def deps_state(deps, skip_dir=None):
    """[(path, sha256), ...] + [(dir, names hash)] of the dependencies' directories; None when one is gone.
    skip_dir: the probe's work directory (other probes write there all the time); left out only when the source
    includes nothing by a relative quoted name (the caller checks), so no lookup can land in it."""
    got = []
    for p in deps:
        h = file_hash(p)
        if h is None:
            return None
        got.append([p, h])
    for d in sorted({os.path.dirname(p) for p in deps} - {skip_dir}):
        got.append([d + '/', names_hash(d, False)])
    return got


def _paths(root, mk):
    return (os.path.join(root, 'm', mk[:2], mk + '.json'),)


def lookup(root, mk):
    mp, = _paths(root, mk)
    try:
        variants = json.load(open(mp))
    except (OSError, ValueError):
        return None
    for v in variants:
        ok = True
        for p, h in v['deps']:
            if p.endswith('/'):
                if names_hash(p[:-1], False) != h:
                    ok = False
                    break
            elif file_hash(p) != h:
                ok = False
                break
        if not ok:
            continue
        rk = v['r']
        rp = os.path.join(root, 'r', rk[:2], rk)
        try:
            meta = json.load(open(rp + '.json'))
            if meta['rc'] == 0 and not os.path.isfile(rp + '.o'):
                continue
        except (OSError, ValueError, KeyError):
            continue
        return rk, rp, meta
    return None


def _write(path, data, mode='wb'):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = '%s.%d.%d.tmp' % (path, os.getpid(), threading.get_ident())
    with open(tmp, mode) as f:
        f.write(data)
    os.replace(tmp, path)


def store(root, mk, deps, rc, err, obj, skip_dir=None):
    st = deps_state(deps, skip_dir)
    if st is None:
        return False
    rk = hashlib.sha256((mk + json.dumps(st)).encode('utf-8', 'surrogateescape')).hexdigest()
    rp = os.path.join(root, 'r', rk[:2], rk)
    if rc == 0:
        data = open(obj, 'rb').read()
        if not data:
            return False                 # an empty object with exit 0 is never cached
        _write(rp + '.o', data)
    _write(rp + '.json', json.dumps({'rc': rc, 'err': err}), 'w')
    mp, = _paths(root, mk)
    with _lock:
        try:
            variants = json.load(open(mp))
        except (OSError, ValueError):
            variants = []
        variants = [{'deps': st, 'r': rk}] + [v for v in variants if v.get('r') != rk]
        _write(mp, json.dumps(variants[:VARIANTS]), 'w')
    return True


def compile(cxx, flags, src, obj, cwd, timeout=None):
    """Run `cxx *flags src -o obj` in cwd, or give back the cached result of the same compile.
    -> (returncode, stderr text). The object is at obj when the returncode is 0 (and absent otherwise).
    subprocess.TimeoutExpired is raised as by subprocess.run (and never cached)."""
    root = cache_dir()
    argv = [*flags, src, '-o', obj]
    if not root:
        r = subprocess.run([cxx, *argv], cwd=cwd, capture_output=True, text=True, errors='replace', timeout=timeout)
        return r.returncode, r.stderr
    t0 = time.time()
    try:
        src_text = open(src if os.path.isabs(src) else os.path.join(cwd, src), 'rb').read()
        mk = manifest_key(cxx, argv, cwd, src_text)
        got = lookup(root, mk)
    except OSError:
        got, mk = None, None
    verify = os.environ.get('FNVNX_LINK_OBJCACHE_VERIFY') == '1'
    if got is not None and got[2]['rc'] == 0:
        try:
            empty = os.path.getsize(got[1] + '.o') == 0
        except OSError:
            empty = True
        if empty:
            got = None    # an empty cached object (a store cut short): compile again (layoutgate af034f14, sw30)
    if got is not None and not verify:
        rk, rp, meta = got
        if meta['rc'] == 0:
            tmp = obj + '.cache.tmp'
            shutil.copyfile(rp + '.o', tmp)
            os.replace(tmp, obj)
            try:
                os.utime(rp + '.json')
            except OSError:
                pass
        elif os.path.exists(obj):
            os.remove(obj)
        with _lock:
            STATS['hits'] += 1
            STATS['hit_s'] += time.time() - t0
        return meta['rc'], meta['err']
    dfile = obj + '.objcache.d'
    if os.path.exists(obj):
        os.remove(obj)
    t1 = time.time()
    r = subprocess.run([cxx, *flags, '-MD', '-MF', dfile, src, '-o', obj], cwd=cwd, capture_output=True, text=True,
                       errors='replace', timeout=timeout)
    with _lock:
        STATS['misses'] += 1
        STATS['compile_s'] += time.time() - t1
    if r.returncode and os.path.exists(obj):
        os.remove(obj)   # as a failed gcc leaves it: absent
    if verify and got is not None:
        rk, rp, meta = got
        same = meta['rc'] == r.returncode and meta['err'] == r.stderr and (
            r.returncode != 0 or open(rp + '.o', 'rb').read() == open(obj, 'rb').read())
        with _lock:
            STATS['verified' if same else 'verify_mismatch'] += 1
        if not same:
            print('objcache: VERIFY MISMATCH for %s (cached rc %s, now %s)' % (src, meta['rc'], r.returncode),
                  file=sys.stderr, flush=True)
    if mk is not None:
        deps = parse_deps(dfile, cwd)
        try:
            sp = src if os.path.isabs(src) else os.path.join(cwd, src)
            skip = None if REL_QUOTED.search(src_text) else os.path.dirname(os.path.normpath(sp))
            if deps and store(root, mk, deps, r.returncode, r.stderr, obj, skip):
                with _lock:
                    STATS['stored'] += 1
            else:
                with _lock:
                    STATS['not_stored'] += 1
        except OSError as e:
            with _lock:
                STATS['store_errors'] += 1
            print('objcache: store failed: %r' % e, file=sys.stderr, flush=True)
    try:
        os.remove(dfile)
    except OSError:
        pass
    return r.returncode, r.stderr


SRC_EXT = ('.cpp', '.cc', '.cxx', '.c', '.C')
ARG_TAKES = {'-include', '-imacros', '-isystem', '-iquote', '-idirafter', '-I', '-D', '-U', '-x', '-MF', '-MT', '-MQ',
             '-o', '-Xassembler', '-Xlinker', '-aux-info', '-imultilib', '-isysroot', '-iprefix', '-iwithprefix'}


def cc_main(args):
    """`objcache.py cc <compiler> <args...>`: one compile through the cache (T1: compile.sh's units, shared across
    the farms; FNVNX_UNIT_OBJCACHE names the cache). The source is the argument that names an existing C/C++ file and
    is no option's value; the object is -o's value. Anything else (no -o, no source, -E, -M without an object, a
    pipe): the compiler runs as given. stderr and the exit status are the compiler's (or the cached ones)."""
    if not args:
        print('objcache cc: no compiler', file=sys.stderr)
        return 2
    cxx, rest = args[0], args[1:]
    d = os.environ.get('FNVNX_UNIT_OBJCACHE')
    if d is not None:
        os.environ['FNVNX_LINK_OBJCACHE'] = d
    obj, src, flags, i = None, None, [], 0
    while i < len(rest):
        t = rest[i]
        if t == '-o' and i + 1 < len(rest):
            obj = rest[i + 1]
            i += 2
            continue
        if t in ARG_TAKES and i + 1 < len(rest):
            flags += [t, rest[i + 1]]
            i += 2
            continue
        if not t.startswith('-') and t.endswith(SRC_EXT) and os.path.isfile(t) and src is None:
            src = t
            i += 1
            continue
        flags.append(t)
        i += 1
    if obj is None or src is None or '-E' in flags or '-' in flags:
        return subprocess.run([cxx] + rest).returncode
    rc, err = compile(cxx, flags, src, obj, os.getcwd())
    sys.stderr.write(err)
    return rc


def summary(tag=''):
    s = STATS
    n = s['hits'] + s['misses']
    return ('objcache%s: %d compiles, %d hits (%.0f %%), %d compiled (%.1f s), %d stored%s%s' % (
        ' ' + tag if tag else '', n, s['hits'], 100.0 * s['hits'] / n if n else 0, s['misses'], s['compile_s'],
        s['stored'], ', %d verified' % s['verified'] if s['verified'] else '',
        ', %d VERIFY MISMATCHES' % s['verify_mismatch'] if s['verify_mismatch'] else ''))


def stat(root):
    n = size = 0
    for dp, _, fs in os.walk(os.path.join(root, 'r')):
        for f in fs:
            n += f.endswith('.json')
            size += os.path.getsize(os.path.join(dp, f))
    m = sum(len(fs) for _, _, fs in os.walk(os.path.join(root, 'm')))
    print('%s: %d manifests, %d results, %.1f MB' % (root, m, n, size / 1e6))


def prune(root, max_mb=8192):
    """Drop the least recently used results (a hit touches its .json) until the results fit max_mb; manifests that
    name a dropped result simply miss."""
    items = []
    for dp, _, fs in os.walk(os.path.join(root, 'r')):
        for f in fs:
            if f.endswith('.json'):
                p = os.path.join(dp, f)
                base = p[:-5]
                sz = os.path.getsize(p) + (os.path.getsize(base + '.o') if os.path.exists(base + '.o') else 0)
                items.append((os.path.getmtime(p), sz, base))
    total = sum(sz for _, sz, _ in items)
    items.sort()
    dropped = 0
    while items and total > max_mb * 1e6:
        _, sz, base = items.pop(0)
        for ext in ('.json', '.o'):
            try:
                os.remove(base + ext)
            except OSError:
                pass
        total -= sz
        dropped += 1
    print('objcache prune: %d results dropped, %.1f MB kept' % (dropped, total / 1e6))


if __name__ == '__main__':
    a = sys.argv[1:]
    if a and a[0] == 'cc':
        sys.exit(cc_main(a[1:]))
    if not a or a[0] not in ('stat', 'prune'):
        sys.exit(__doc__)
    root = a[1] if len(a) > 1 else cache_dir()
    if not root:
        sys.exit('objcache: no cache (FNVNX_LINK_OBJCACHE=none)')
    if a[0] == 'stat':
        stat(root)
    else:
        prune(root, float(a[2]) if len(a) > 2 else float(os.environ.get('FNVNX_LINK_OBJCACHE_MAX_MB') or 8192))
