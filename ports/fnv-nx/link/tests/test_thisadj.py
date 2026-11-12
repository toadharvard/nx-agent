#!/usr/bin/env python3
"""Check of link/thisadj.py on a synthetic two-class scene (byte-built exe, hand-built object and bind facts, invented names): green on the
consistent scene, red and named on every broken fact, an error on malformed input."""
import os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, '..'), os.path.join(HERE, '..', '..', 'contract')]
import abi_fixture as F
import abi_gaterun
import thisadj as G

fails = []


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r want %r' % (what, got, want))


def run(**kw):
    d = tempfile.mkdtemp()
    exe, op, bp, a = F.scene(d, **kw)
    rc, r = abi_gaterun.run(G, ['--exe', exe, '--obj', op, '--bind', bp], 'thisadj')
    return rc, r, a, (exe, op, bp)

import store
rc, r, a, _ = run()
check('green', (rc, r['universe'], r['certified'], r['name']), (0, 6, 6, 'thisadj'))
rc, r, a, _ = run(thunk_n=4)
check('wrong thunk offset', (rc, sorted(x.split(' [')[0] for x in r['uncertified'])), (1, ['u.o:_ZTV3Der+28#0', 'u.o:_ZTV3Der+28#1']))
check('reason names both numbers', 'native adjusts this by 4, the exe slot 0 implies 8' in r['uncertified'][0], True)
rc, r, *_ = run(drop_secondary=True)
check('unbound', (rc, r['certified']), (1, 3))
# entry convention: a function held directly only at one secondary offset is entered there (e = d'); native must then adjust by it
check('entry convention', [G.entry_convention({1: [0, 8]}, 1), G.entry_convention({1: [8]}, 1), G.entry_convention({1: [8, 16]}, 1), G.entry_convention({}, 1)], [0, 8, None, None])
# native plain function in the secondary vtable where the exe slot is a thunk by 8: red; a virtual thunk: red
for what, sym in (('plain where exe thunks', '_ZN3DerD1Ev'), ('virtual-base thunk', '_ZTv0_n12_N3DerD1Ev')):
    d = tempfile.mkdtemp()
    exe, op, bp, a = F.scene(d)
    o = store.load(op, 'obj')
    o[('u.o', 'ztv')]['_ZTV3Der'][1][2][0] = sym
    store.save(op, 'obj', o)
    rc, r = abi_gaterun.run(G, ['--exe', exe, '--obj', op, '--bind', bp], 'thisadj')
    check(what, (rc, r['uncertified'][0].split(' [')[0]), (1, 'u.o:_ZTV3Der+28#0'))
check('malformed', G.main(['--exe', exe, '--obj', os.path.join(d, 'x'), '--bind', bp]), 2)
for f in fails:
    print('FAIL', f)
sys.exit(1 if fails else 0)
