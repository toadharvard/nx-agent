#!/usr/bin/env python3
"""Check of link/vtables.py on a synthetic two-class scene (byte-built exe, hand-built object and bind facts, invented names): green on the
consistent scene, red and named on every broken fact, an error on malformed input."""
import os, sys, tempfile
import atexit  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
tempfile.tempdir = tempfile.mkdtemp(prefix="scratch-")  # every scratch dir of this test lives here and is removed at exit
atexit.register(shutil.rmtree, tempfile.tempdir, True)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, '..'), os.path.join(HERE, '..', '..', 'contract')]
import abi_fixture as F
import abi_gaterun
import vtables as G

fails = []


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r want %r' % (what, got, want))


def run(**kw):
    d = tempfile.mkdtemp()
    exe, op, bp, a = F.scene(d, **kw)
    rc, r = abi_gaterun.run(G, ['--exe', exe, '--obj', op, '--bind', bp], 'vtables')
    return rc, r, a, (exe, op, bp)

rc, r, a, _ = run()
check('green: exit and counts', (rc, r['status'], r['universe'], r['certified'], r['format'], r['name']), (0, 'green', 2, 2, 'fnvnx-gate/1', 'vtables'))
rc, r, *_ = run(drop_secondary=True)
check('unbound point', (rc, r['uncertified']), (1, ['u.o:_ZTV3Der+28 [V1 ??_7Der@@6BB@@@ is not bound to an exe vftable]']))
rc, r, *_ = run(d_native=4)
check('native offset disagrees', (rc, r['certified'], 'V3 offsets: native 4, vtpair 8, exe COL 8' in r['uncertified'][0]), (1, 1, True))
rc, r, *_ = run(vfptr=4)
check('vtpair offset disagrees', 'vtpair 4' in r['uncertified'][0], True)
rc, r, *_ = run(extra_slot=True)
check('extra native slot', (rc, r['uncertified'][0].startswith('u.o:_ZTV3Der+8 [V4 native point has 4 slots')), (1, True))
rc, r, *_ = run(d1_kind='func')
check('destructor pair broken', 'V4 slot 1 is a deleting destructor without the complete destructor' in r['uncertified'][0], True)
rc, r, *_ = run(drop_native_point=True)
check('exe vftable of the class left unbound (V5)', (rc, r['uncertified'] and 'V5' in r['uncertified'][0]), (1, True))
# a missing vtpair row is uncertified; an unnamed row leaves the universe and is counted
import store
d = tempfile.mkdtemp()
exe, op, bp, a = F.scene(d)
obj = store.load(op, 'obj')
obj[('u.o', 'vtpair')] = obj[('u.o', 'vtpair')][:1] + [('_ZTV3Der', 28, 'unnamed:no twin', 8)]
store.save(op, 'obj', obj)
rc, r = abi_gaterun.run(G, ['--exe', exe, '--obj', op, '--bind', bp], 'vtables')
check('unnamed point', (r['universe'], r['detail'].split()[0]), (1, '1'))
obj[('u.o', 'vtpair')] = obj[('u.o', 'vtpair')][:1]
store.save(op, 'obj', obj)
rc, r = abi_gaterun.run(G, ['--exe', exe, '--obj', op, '--bind', bp], 'vtables')
check('missing row', r['uncertified'][0], 'u.o:_ZTV3Der+28 [V1 no vtpair row]')
# malformed input is an error (exit 2), not a verdict
bad = os.path.join(d, 'bad.json')
open(bad, 'w').write('{}')
check('malformed obj', G.main(['--exe', exe, '--obj', bad, '--bind', bp]), 2)
check('missing exe', G.main(['--exe', os.path.join(d, 'none.exe'), '--obj', op, '--bind', bp]), 2)
for f in fails:
    print('FAIL', f)
sys.exit(1 if fails else 0)
