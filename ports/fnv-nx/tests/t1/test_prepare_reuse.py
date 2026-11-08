#!/usr/bin/env python3
"""T1: the pipeline's prepare stage keeps the last tree when every input prepare.py read is unchanged (traced.py),
naming the new head in its provenance; a changed input or PREPARE_REUSE=0 runs prepare.py. A tiny synthetic decomp
(no decomp content); Prepare.run driven with stand-ins for the pipeline's build and stage record."""
import json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PIPE = os.path.abspath(os.path.join(HERE, '..', '..', 'tools', 'pipeline'))
sys.path.insert(0, PIPE)
import build  # noqa: E402

fails = 0


def ok(name, cond):
    global fails
    print(('ok   ' if cond else 'FAIL ') + name)
    fails += 0 if cond else 1


class Rec(dict):
    def metric(self, k, v):
        self.setdefault('metrics', {})[k] = v

    def artifact(self, k, v):
        self.setdefault('artifacts', {})[k] = v

    def note(self, s):
        self.setdefault('notes', []).append(s)


class B:
    def __init__(self, out, decomp, head):
        self.out, self.decomp, self.head, self.cfg = out, decomp, head, {'PREPARED': ''}
        self.ran = 0

    def sdir(self, n):
        p = os.path.join(self.out, n)
        os.makedirs(p, exist_ok=True)
        return p

    def art(self, stage, k):
        return self.decomp

    def sh(self, cmd, log, check=False, **kw):
        self.ran += 1
        r = subprocess.run(cmd, stdout=open(log, 'a'), stderr=subprocess.STDOUT)
        if check and r.returncode:
            raise SystemExit('failed: %s' % cmd)
        return r.returncode


def run(out, dec, head):
    b, r = B(out, dec, head), Rec(log=os.path.join(out, 'stage.log'))
    os.makedirs(out, exist_ok=True)
    build.port_dirty = lambda paths: []          # the test runs from a work tree with edits
    build.Prepare().run(b, r)
    return b, r


def main():
    t = tempfile.mkdtemp(prefix='prepre-')
    try:
        dec = os.path.join(t, 'dec')
        for d in ('build', 'src', 'include'):
            os.makedirs(os.path.join(dec, d))
        json.dump({'units': [{'source': 'src/a.cpp', 'functions': [], 'compiler': 'sp1', 'flags': []}]},
                  open(os.path.join(dec, 'build', 'units.json'), 'w'))
        open(os.path.join(dec, 'src', 'a.cpp'), 'w').write('// @fnv 0x00401000\nint f(int a) { return a + 1; }\n')
        out = os.path.join(t, 'b')
        b1, r1 = run(out, dec, 'a' * 40)
        deps = os.path.join(out, 'prepare', 'prepare.deps.json')
        ok('first run: prepare.py ran, traced, the tree recorded', b1.ran == 1 and r1['metrics']['prepare_reused'] == 0
           and json.load(open(deps)).get('tree_id') == r1['output_id'])
        ok('the record holds the decomp source and prepare.py', any(p.endswith('src/a.cpp') for p in
           json.load(open(deps))['files']) and any(p.endswith('tools/prepare.py') for p in json.load(open(deps))['files']))
        b2, r2 = run(out, dec, 'b' * 40)
        pf = json.load(open(os.path.join(out, 'prepare', 'prepared_from.json')))
        ok('next head, nothing changed: the tree kept, prepare.py not run', b2.ran == 0 and
           r2['metrics']['prepare_reused'] == 1 and r2['output_id'] == r1['output_id'])
        ok('provenance: this head, and the head it was kept from', pf['port_commit'] == 'b' * 40 and
           pf.get('reused_from') == 'a' * 40 and 'reused' in pf['mode'])
        open(os.path.join(dec, 'src', 'a.cpp'), 'w').write('// @fnv 0x00401000\nint f(int a) { return a + 2; }\n')
        b3, r3 = run(out, dec, 'c' * 40)
        ok('a decomp source changed: prepare.py runs, the tree follows', b3.ran == 1 and
           r3['metrics']['prepare_reused'] == 0 and 'a + 2' in open(os.path.join(out, 'prepare', 'tree', 'src', 'a.cpp')).read())
        os.environ['PREPARE_REUSE'] = '0'
        b4, r4 = run(out, dec, 'd' * 40)
        os.environ.pop('PREPARE_REUSE')
        ok('PREPARE_REUSE=0: prepare.py runs', b4.ran == 1 and r4['metrics']['prepare_reused'] == 0)
        # the tree altered after its run: not the recorded tree, so prepare.py runs again
        open(os.path.join(out, 'prepare', 'tree', 'src', 'a.cpp'), 'a').write('// edited\n')
        b5, r5 = run(out, dec, 'e' * 40)
        ok('a tree that is not the recorded one: prepare.py runs', b5.ran == 1)
        # the decomp's head moved with no file changed (prepare.py's subprocess `git rev-parse HEAD`, which the trace
        # cannot see): prepare.py runs
        g = lambda *a: subprocess.run(['git', '-C', dec, '-c', 'user.name=t', '-c', 'user.email=t@t'] + list(a),
                                      capture_output=True, check=True)
        g('init', '-q'); g('add', '-A'); g('commit', '-qm', 'one')
        run(out, dec, 'f' * 40)
        b7, r7 = run(out, dec, 'g' * 40)
        g('commit', '-q', '--allow-empty', '-m', 'two')
        b8, r8 = run(out, dec, 'h' * 40)
        ok('the decomp head moved, no file changed: kept before, prepare.py runs after', b7.ran == 0 and b8.ran == 1)
    finally:
        shutil.rmtree(t, ignore_errors=True)
    print('test_prepare_reuse: all ok' if not fails else 'test_prepare_reuse: %d failed' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
