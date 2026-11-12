#!/usr/bin/env python3
"""Check of link/vslots.py on a synthetic two-class scene (byte-built exe, hand-built object and bind facts, invented names): green on the
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
import vslots as G

fails = []


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r want %r' % (what, got, want))


def run(**kw):
    d = tempfile.mkdtemp()
    exe, op, bp, a = F.scene(d, **kw)
    rc, r = abi_gaterun.run(G, ['--exe', exe, '--obj', op, '--bind', bp], 'vslots')
    return rc, r, a, (exe, op, bp)

import store
rc, r, a, _ = run()
check('green', (rc, r['universe'], r['certified'], r['name']), (0, 6, 6, 'vslots'))
rc, r, a, _ = run(g_addr=F.TEXT)
check('wrong callee address is named', (rc, sorted(x.split(' [')[0] for x in r['uncertified'])),
      (1, ['u.o:_ZTV3Der+28#0', 'u.o:_ZTV3Der+28#1', 'u.o:_ZTV3Der+8#0', 'u.o:_ZTV3Der+8#1']))
check('reason', 'S3 native callee is 00401000, exe slot 0 reaches' in r['uncertified'][0], True)
rc, r, *_ = run(drop_secondary=True)
check('unbound point: its slots', (rc, r['certified'], 'address point unbound' in r['uncertified'][0]), (1, 3, True))
rc, r, *_ = run(extra_slot=True)
check('slot past the exe vftable', any('is exe slot 2, the exe vftable has 2' in u for u in r['uncertified']), True)
# a native slot symbol without an exe address, a zero word, a virtual-base thunk
for what, patch in (('no address', lambda o, b: b['sym'].pop(('u.o', '_ZN1B1gEv'))),
                    ('zero word', lambda o, b: o[('u.o', 'ztv')]['_ZTV3Der'][1][2].__setitem__(2, None)),
                    ('virtual thunk', lambda o, b: o[('u.o', 'ztv')]['_ZTV3Der'][1][2].__setitem__(2, '_ZTv0_n12_N1B1gEv'))):
    d = tempfile.mkdtemp()
    exe, op, bp, a = F.scene(d)
    o, b = store.load(op, 'obj'), store.load(bp, 'bind')
    patch(o, b)
    store.save(op, 'obj', o)
    store.save(bp, 'bind', b)
    rc, r = abi_gaterun.run(G, ['--exe', exe, '--obj', op, '--bind', bp], 'vslots')
    check(what, (rc, r['uncertified'][0].split(' [')[0]), (1, 'u.o:_ZTV3Der+28#2'))
# the complete destructor slot follows its deleting partner
d = tempfile.mkdtemp()
exe, op, bp, a = F.scene(d, g_addr=a['Bg'])
rc, r = abi_gaterun.run(G, ['--exe', exe, '--obj', op, '--bind', bp], 'vslots')
check('D1 slot is red when D0 is', sum('+8#0' in u or '+8#1' in u for u in r['uncertified']), 2)
check('malformed', G.main(['--exe', exe, '--obj', os.path.join(d, 'x'), '--bind', bp]), 2)
for f in fails:
    print('FAIL', f)
sys.exit(1 if fails else 0)
