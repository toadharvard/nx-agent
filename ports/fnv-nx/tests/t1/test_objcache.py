#!/usr/bin/env python3
"""T1: tools/link/objcache.py - the link's compile cache gives back exactly what the compiler gives, and compiles
again whenever an input changes (a header, a header that shadows another, the flags, a file a flag names).
Needs aarch64-linux-gnu-g++ (skipped without it)."""
import os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'link'))
import objcache  # noqa: E402

CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
fails = 0


def ok(name, cond):
    global fails
    print(('ok   ' if cond else 'FAIL ') + name)
    fails += 0 if cond else 1


def main():
    if not shutil.which(CXX):
        print('test_objcache: skipped (no %s)' % CXX)
        return 0
    t = tempfile.mkdtemp(prefix='objcache-')
    try:
        return run(t)
    finally:
        shutil.rmtree(t, ignore_errors=True)


def run(t):
    tree, work, cache = os.path.join(t, 'tree'), os.path.join(t, 'work'), os.path.join(t, 'cache')
    for d in (os.path.join(tree, 'include'), os.path.join(tree, 'shim'), os.path.join(tree, 'src'), work):
        os.makedirs(d)
    w = lambda p, s: open(os.path.join(tree, p), 'w').write(s)
    w('include/a.h', 'struct A { int a; int b; };\n')
    w('src/u.cpp', '#include "a.h"\nint size_a() { return sizeof(A); }\n')
    keys = os.path.join(t, 'keys.txt')
    open(keys, 'w').write('one\n')
    fl = ['-mabi=ilp32', '-c', '-O2', '-I' + os.path.join(tree, 'shim'), '-I' + os.path.join(tree, 'include'),
          '-fplugin-arg-x-keys=' + keys]
    src, obj = os.path.join(work, 'p.cpp'), os.path.join(work, 'p.o')

    def plain(text, flags=fl):
        open(src, 'w').write(text)
        r = subprocess.run([CXX, *[f for f in flags if not f.startswith('-fplugin-arg')], src, '-o', obj], cwd=tree,
                           capture_output=True, text=True, errors='replace')
        return r.returncode, r.stderr, open(obj, 'rb').read() if r.returncode == 0 else None

    def cached(text, flags=fl):
        open(src, 'w').write(text)
        rc, err = objcache.compile(CXX, [f for f in flags if not f.startswith('-fplugin-arg')] +
                                   [f for f in flags if f.startswith('-fplugin-arg')][:0], src, obj, cwd=tree)
        return rc, err, open(obj, 'rb').read() if rc == 0 else None

    # the plugin-arg token is not given to gcc (no plugin here): command_inputs is tested on its own below
    os.environ['FNVNX_LINK_OBJCACHE'] = cache
    os.environ.pop('FNVNX_LINK_OBJCACHE_VERIFY', None)
    text = '#include "%s"\nint probe() { return 1; }\n' % os.path.join(tree, 'src', 'u.cpp')
    want = plain(text)
    objcache.STATS.clear()
    got1 = cached(text)
    got2 = cached(text)
    ok('first compile: a miss, stored', objcache.STATS['misses'] == 1 and objcache.STATS['stored'] == 1)
    ok('second compile: a hit', objcache.STATS['hits'] == 1)
    ok('miss and hit give the compiler\'s bytes, status and stderr', got1 == want and got2 == want)

    # other files in the work directory (the other probes) do not change the key
    open(os.path.join(work, 'other.cpp'), 'w').write('x')
    objcache.STATS.clear()
    cached(text)
    ok('a new file in the work directory: still a hit', objcache.STATS['hits'] == 1)

    # a failed compile is replayed with its errors
    bad = text + 'int broken() { return nosuch; }\n'
    want_bad = plain(bad)
    objcache.STATS.clear()
    b1, b2 = cached(bad), cached(bad)
    ok('failed compile: replayed with the same errors, no object', b1 == want_bad and b2 == want_bad and
       objcache.STATS['hits'] == 1 and want_bad[0] != 0 and not os.path.exists(obj))

    # a header changes: compiled again, the new bytes
    w('include/a.h', 'struct A { int a; int b; int c; };\n')
    want3 = plain(text)
    objcache.STATS.clear()
    got3 = cached(text)
    ok('a header changed: a miss with the new object', objcache.STATS['misses'] == 1 and got3 == want3 and
       got3 != want)

    # a header that shadows the used one (an earlier -I directory): compiled again
    objcache.STATS.clear()
    cached(text)
    ok('(warm again)', objcache.STATS['hits'] == 1)
    w('shim/a.h', 'struct A { char a; };\n')
    want4 = plain(text)
    objcache._names.clear()      # a new link process: the include listings are read again
    objcache.STATS.clear()
    got4 = cached(text)
    ok('a shadowing header added: a miss with the new object', objcache.STATS['misses'] == 1 and got4 == want4 and
       got4 != want3)
    os.remove(os.path.join(tree, 'shim', 'a.h'))
    objcache._names.clear()

    # flags change: compiled again
    objcache.STATS.clear()
    fl0 = [f if f != '-O2' else '-O0' for f in fl]
    got5 = cached(text, fl0)
    ok('other flags: a miss', objcache.STATS['misses'] == 1 and got5 == plain(text, fl0))

    # a file a flag names (the order keys, a plugin): its content is in the key
    f1, _ = objcache.command_inputs(['-c', '-fplugin-arg-x-keys=' + keys, '-o', obj, src], tree)
    ok('command_inputs: the keys file and the source, never the -o output', keys in f1 and src in f1 and obj not in f1)
    k1 = objcache.manifest_key(CXX, ['-fplugin-arg-x-keys=' + keys], tree, b'x')
    open(keys, 'w').write('two\n')
    k2 = objcache.manifest_key(CXX, ['-fplugin-arg-x-keys=' + keys], tree, b'x')
    ok('a flag-named file changed: another manifest key', k1 != k2)

    # verify mode: a hit is compiled anyway and compared
    os.environ['FNVNX_LINK_OBJCACHE_VERIFY'] = '1'
    objcache.STATS.clear()
    got6 = cached(text)
    os.environ.pop('FNVNX_LINK_OBJCACHE_VERIFY')
    ok('verify: compared, no mismatch', objcache.STATS['verified'] == 1 and not objcache.STATS['verify_mismatch']
       and got6 == want3)

    # misses are explained: the first parts that changed since this compile was stored
    objcache.WHY.clear()
    w('include/a.h', 'struct A { int a; };\n')
    cached(text)
    ok('why: a changed header is named (dep:)', list(objcache.WHY) == ['dep:' + os.path.join(tree, 'include', 'a.h')])
    objcache.WHY.clear()
    cached(text + '// changed\n')
    ok('why: changed source text', list(objcache.WHY) == ['source'])
    objcache.WHY.clear()
    cached(text, [f if f != '-O2' else '-O1' for f in fl])
    ok('why: other flags (argv; the source: the last stored compile had the changed text)', set(objcache.WHY) == {'argv', 'source'})   # against the last stored: the changed text
    objcache.WHY.clear()
    objcache.compile(CXX, [f for f in fl if not f.startswith('-fplugin-arg')], src, obj + '2.o', cwd=tree)
    ok('why: another object path is new', list(objcache.WHY) == ['new'])
    objcache.WHY.clear()
    os.makedirs(os.path.join(tree, 'include', 'sub'))
    open(os.path.join(tree, 'include', 'sub', 'n.h'), 'w').write('\n')
    objcache._names.clear()
    cached(text)
    ok('why: a file added under an -I directory (names:)',
       set(objcache.WHY) == {'argv', 'names:' + os.path.join(tree, 'include')})
    ok('why: in the summary', '; misses: 1 argv, 1 names:' in objcache.summary('t'))
    w('include/a.h', 'struct A { int a; int b; int c; };\n')   # back to want3's header

    # off: no cache directory is touched
    os.environ['FNVNX_LINK_OBJCACHE'] = 'none'
    objcache.STATS.clear()
    ok('FNVNX_LINK_OBJCACHE=none: a plain compile', cached(text) == want3 and not objcache.STATS)
    os.environ['FNVNX_LINK_OBJCACHE'] = cache

    print('test_objcache: all ok' if not fails else 'test_objcache: %d failed' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
