#!/usr/bin/env python3
"""Check of link/thisadj.py on a synthetic two-class scene (byte-built exe, hand-built object and bind facts, invented names): green on the
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
import thisadj as G
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

fails = testcase.FAILED
def run(**kw):
    d = tempfile.mkdtemp()
    exe, op, bp, a = F.scene(d, **kw)
    rc, r = abi_gaterun.run(G, ['--exe', exe, '--obj', op, '--bind', bp], 'thisadj')
    return rc, r, a, (exe, op, bp)

import store
rc, r, a, _ = run()
case('green', got=(rc, r['universe'], r['certified'], r['name']), want=(0, 6, 6, 'thisadj'))
rc, r, a, _ = run(thunk_n=4)
case('wrong thunk offset', got=(rc, sorted(x.split(' [')[0] for x in r['uncertified'])), want=(1, ['u.o:_ZTV3Der+28#0', 'u.o:_ZTV3Der+28#1']))
case('reason names both numbers', got='native adjusts this by 4, the exe slot 0 implies 8' in r['uncertified'][0], want=True)
rc, r, *_ = run(drop_secondary=True)
case('unbound', got=(rc, r['certified']), want=(1, 3))
# entry convention: a function held directly only at one secondary offset is entered there (e = d'); native must then adjust by it
case('entry convention', got=[G.entry_convention({1: [0, 8]}, 1), G.entry_convention({1: [8]}, 1), G.entry_convention({1: [8, 16]}, 1), G.entry_convention({}, 1)], want=[0, 8, None, None])
# native plain function in the secondary vtable where the exe slot is a thunk by 8: red; a virtual thunk: red
for what, sym in (('plain where exe thunks', '_ZN3DerD1Ev'), ('virtual-base thunk', '_ZTv0_n12_N3DerD1Ev')):
    d = tempfile.mkdtemp()
    exe, op, bp, a = F.scene(d)
    o = store.load(op, 'obj')
    o[('u.o', 'ztv')]['_ZTV3Der'][1][2][0] = sym
    store.save(op, 'obj', o)
    rc, r = abi_gaterun.run(G, ['--exe', exe, '--obj', op, '--bind', bp], 'thisadj')
    case('thisadj', got=(rc, r['uncertified'][0].split(' [')[0]), want=(1, 'u.o:_ZTV3Der+28#0'), key=what)
case('malformed', got=G.main(['--exe', exe, '--obj', os.path.join(d, 'x'), '--bind', bp]), want=2)
for f in fails:
    print('FAIL', f)
sys.exit(1 if fails else 0)
