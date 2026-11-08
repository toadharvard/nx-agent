#!/usr/bin/env python3
"""T1: the incremental link gives the cold link's bytes.

On test_vtables.py's hand-written classes (no decomp content), the link stage's order (resolve, probe, vtables,
bodies, resolve, apply, check) runs for two consecutive heads at one fixed path, as on a farm:

    head 1, cold                     (fills out/ and the compile cache, tools/link/objcache.py)
    head 2 = head 1 with one unit changed:
      warm: out/ of head 1 kept, the cache of head 1
      cold: a new out/, an empty cache
      cold again (the link is deterministic at all)
      a new out/ with head 1's compile cache only (the objcache alone, without fnvlink's own caches)

Every output (addrmap.tsv, aliases.ld, refs.tsv, the synthesized vtables and bodies, every object of fixed/ and the
trial link's text) must have the same sha256 warm and cold; the warm link must have compiled less; a repeat of
head 2 warm compiles nothing. Needs what test_vtables.py needs (aarch64-linux-gnu-*, llvm-undname, SSE2NEON, the
gcc plugin headers); skipped without aarch64-linux-gnu-g++.
"""
import hashlib, os, re, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LINK = os.path.join(HERE, '..', '..', 'tools', 'link')
sys.path.insert(0, LINK)
import layout        # noqa: E402
import test_vtables as tv   # noqa: E402

FNVLINK = os.path.join(LINK, 'fnvlink.py')
fails = 0


def ok(name, cond):
    global fails
    print(('ok   ' if cond else 'FAIL ') + name, flush=True)
    fails += 0 if cond else 1


def sh(cmd, env, log, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    log.append(r.stdout + r.stderr)
    if check and r.returncode:
        raise SystemExit('FAILED (%d): %s\n%s' % (r.returncode, ' '.join(cmd), (r.stdout + r.stderr)[-3000:]))
    return r.returncode


def link(run_dir, cache, head, keep_out=False):
    """One link of head (1 or 2) in run_dir with the compile cache at cache -> ({output: sha256}, log text)."""
    if not keep_out:
        shutil.rmtree(run_dir, ignore_errors=True)
    tree = os.path.join(run_dir, 'tree')
    os.makedirs(os.path.join(tree, 'include'), exist_ok=True)
    open(os.path.join(tree, 'include', 'h.h'), 'w').write(tv.HEADER)
    open(os.path.join(tree, 'include', 'e.h'), 'w').write(tv.EHEADER)
    src = dict(tv.SRC)
    if head == 2:   # the small change: one unit gains a function
        k = 'src/b.cpp'   # a vtable host unit (it references Key's vtable): its probes must compile again
        src[k] = src[k] + '\nint fnvnx_t1_added(int x) { return x * 3 + 1; }\n'
    env = dict(os.environ, FNVNX_LINK_OBJCACHE=cache)
    env.pop('FNVNX_LINK_OBJCACHE_VERIFY', None)
    fl = [f if f != '-O2' else '-O1' for f in layout.flags(tree)] + ['-fno-inline']
    objs, log = {}, []
    for rel, text in src.items():
        p = os.path.join(tree, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if not os.path.exists(p) or open(p).read() != text:
            open(p, 'w').write(text)
        objs[rel] = os.path.join(run_dir, rel.replace('/', '_') + '.o')
        r = subprocess.run([tv.CXX, *fl, p, '-o', objs[rel]], cwd=tree, capture_output=True, text=True)
        if r.returncode:
            raise SystemExit('unit compile failed: %s' % r.stderr[-2000:])
    import json
    json.dump(tv.UNITS, open(os.path.join(run_dir, 'units.json'), 'w'))
    with open(os.path.join(run_dir, 'objects.tsv'), 'w') as f:
        for rel in src:
            f.write('%s\t%s\tok\n' % (rel, objs[rel]))
    out, fixed = os.path.join(run_dir, 'out'), os.path.join(run_dir, 'fixed')
    os.makedirs(out, exist_ok=True)
    py = [sys.executable, '-I', FNVLINK]
    resolve = py + ['resolve', os.path.join(run_dir, 'units.json'), os.path.join(run_dir, 'objects.tsv'), out,
                    '--crt-symbols', 'none', '--imports', 'none', '--exe', 'none']
    # the link stage's steps (tools/pipeline/build.py Link.run)
    sh([sys.executable, '-I', '-c', 'import sys; sys.path.insert(0, sys.argv[1]); import fnvlink; '
        'fnvlink.reset_synth(sys.argv[2])', LINK, out], env, log)
    shutil.rmtree(os.path.join(out, 'objcopy'), ignore_errors=True)
    sh(resolve, env, log)
    sh(py + ['probe', out, tree, '2'], dict(env, FNVNX_NO_VTABLES='1'), log, check=False)
    sh(py + ['vtables', out, tree, '2'], env, log, check=False)
    sh(py + ['bodies', out, tree, '--jobs', '2'], env, log, check=False)
    shutil.rmtree(os.path.join(out, 'objcopy'), ignore_errors=True)
    sh(resolve, env, log)
    shutil.rmtree(fixed, ignore_errors=True)
    sh(py + ['apply', out, fixed], env, log)
    clog = []
    sh(py + ['check', out, fixed], env, clog)
    got = {}
    for n in ('addrmap.tsv', 'aliases.ld', 'refs.tsv', 'abi.tsv', 'bridges.S', 'layouts.tsv', 'vtables/synth.tsv',
              'bodies/bodies.tsv', 'bodies/bind.tsv'):
        p = os.path.join(out, n)
        got['out/' + n] = hashlib.sha256(open(p, 'rb').read()).hexdigest() if os.path.exists(p) else None
    for d in ('vtables', 'bodies'):
        for n in sorted(os.listdir(os.path.join(out, d))) if os.path.isdir(os.path.join(out, d)) else ():
            if n.endswith('.o'):
                got['out/%s/%s' % (d, n)] = hashlib.sha256(open(os.path.join(out, d, n), 'rb').read()).hexdigest()
    for n in sorted(os.listdir(fixed)):
        p = os.path.join(fixed, n)
        if os.path.isfile(p):
            got['fixed/' + n] = hashlib.sha256(open(p, 'rb').read()).hexdigest()
    got['check'] = hashlib.sha256(re.sub(r'\[exit.*', '', clog[0]).encode()).hexdigest()
    return got, '\n'.join(log)


def compiles(log):
    """(compiles, hits) summed over the objcache lines of one link."""
    n = h = 0
    for m in re.finditer(r'objcache \w+: (\d+) compiles, (\d+) hits', log):
        n, h = n + int(m.group(1)), h + int(m.group(2))
    return n, h


def main():
    if not shutil.which(tv.CXX):
        print('test_link_incremental: skipped (no %s)' % tv.CXX)
        return 0
    keep = os.environ.get('FNVNX_TEST_KEEP')
    t = keep or tempfile.mkdtemp(prefix='linkinc-')
    try:
        run_dir = os.path.join(t, 'run')        # one path for every link, as a farm's build directory
        c1, c2, c3 = (os.path.join(t, 'cache%d' % i) for i in (1, 2, 3))
        h1, log1 = link(run_dir, c1, 1)
        c1b = os.path.join(t, 'cache1b')         # head 1's compile cache alone (for a link with a new out/)
        shutil.copytree(c1, c1b)
        warm, logw = link(run_dir, c1, 2, keep_out=True)
        again, loga = link(run_dir, c1, 2, keep_out=True)
        cold, logc = link(run_dir, c2, 2)
        cold2, _ = link(run_dir, c3, 2)
        ok('the link is deterministic (two cold links of head 2)', cold == cold2)
        ok('head 2 differs from head 1 (the change reached the outputs)', h1 != cold)
        diff = sorted(k for k in set(warm) | set(cold) if warm.get(k) != cold.get(k))
        ok('warm link of head 2 == cold link of head 2 (%d outputs; addrmap %s)' % (len(cold),
           cold.get('out/addrmap.tsv', '')[:12]), not diff)
        if diff:
            print('  differ: %s' % diff[:20])
        nw, hw = compiles(logw)
        nc, hc = compiles(logc)
        ok('the warm link compiled less: %d of %d compiles from the cache; cold %d compiled' % (hw, nw, nc - hc),
           nw > 0 and hw > 0 and nw - hw < nc - hc)
        na, ha = compiles(loga)
        ok('head 2 again, warm: every compile from the cache (%d of %d)' % (ha, na), na > 0 and ha == na)
        ok('head 2 again == cold', again == cold)
        # a new out/ (another build directory, no fnvlink caches): only head 1's compile cache
        fresh, logf = link(run_dir, c1b, 2)
        nf, hf = compiles(logf)
        ok('new out/, head 1\'s compile cache: == cold', fresh == cold)
        ok('new out/: the changed host compiled again, the rest from the cache (%d of %d)' % (hf, nf),
           0 < hf < nf)
    finally:
        if not keep:
            shutil.rmtree(t, ignore_errors=True)
    print('test_link_incremental: all ok' if not fails else 'test_link_incremental: %d failed' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
