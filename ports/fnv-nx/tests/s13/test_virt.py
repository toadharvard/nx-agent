#!/usr/bin/env python3
"""tests/s13/test_virt.py - virtual-call class (F2b, difffuzz-20): a native call of planted slot k whose oracle call went
to slot k+1, a function the oracle runs as unit code (no call event), reads as a call-target of two unlike callees
(call-flow). Pair.run now gives it Pair.vslots_run's evidence (vslot ran) and classify calls it vslot-shift (D26 b).
Synthetic inputs only."""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
sys.path.insert(0, HERE)
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13virt'))

import arm64emu   # noqa: E402
import causes     # noqa: E402
import classify   # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402
import x86emu     # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def main():
    V, B = 0x0104dd7c, 0x03000000
    slots = [0x00401100, 0x00401200, 0x00401300, 0x00401400]

    class T:
        vt_pins = {V: struct.pack('<4I', *slots)}
        x86_code = {0x00401300: b'\xc3'}
        image = type('I', (), {'entries': {}})()
        oracle_code = None
        orig_addr = 0x00401000
    pr = runner.Pair.__new__(runner.Pair)
    pr.target = T()
    pr.deadline = None
    pr.adapted = 0
    case = world.Case(seed=1, vptrs={B: V})
    rx, ra = x86emu.Result(), arm64emu.Result()
    rx.calls = [x86emu.Call(0x00402000, [0, 0], 0)]          # the oracle's first event: after slot 2 ran as code
    rx.blocks = {0x00401300}
    ra.calls = [arm64emu.Call(0x00401200, [0] * 8, [0] * 8, 0)]    # the native called slot 1
    d = dict(kind='call-target', index=0, x86='00402000', arm='00401200', arm_vcall='planted')
    pr._run = lambda c, n: (rx, ra, dict(d))
    got = pr.run(case)[2]
    check(got.get('vslot') and got['vslot'][0]['shift'] == -1 and got['vslot'][0].get('ran'),
          'Pair.run: the native slot k, the oracle ran slot k+1 as code, is vslot evidence (%s)' % got.get('vslot'))

    def h(dv):
        return classify.hint(None, None, None, dv, dict(code=b'\xc3', address=0x00401000), 0).get('cause')
    check(h(got) == 'vslot-shift', 'classify: that call-target is vslot-shift (%s)' % h(got))
    rx.blocks = set()
    got = pr.run(case)[2]
    check(not got.get('vslot'), 'Pair.run: no evidence without the oracle running slot k+1')
    # the native vtable's slot calls another function than the exe's: native copy [dtor D1, dtor D0, 0x401500,
    # 0x401600] (slot j = exe slot j - 1), the exe's [0x401100, 0x401200, 0x401300]; the native called 0x401500 for
    # exe slot 1, 0x401200, which the oracle ran as code
    AP = 0x0bc00000
    words = [0x08401100, 0x08401100, 0x08401500, 0x08401300]
    T.vt_ap = {V: AP}
    T.vt_mem = {AP: struct.pack('<4I', *words)}
    T.stub_orig = {}
    T.vt_pins = {V: struct.pack('<3I', 0x401100, 0x401200, 0x401300)}
    rx, ra = x86emu.Result(), arm64emu.Result()
    rx.calls = [x86emu.Call(0x00402000, [0, 0], 0)]
    rx.blocks = {0x00401200}
    ra.calls = [arm64emu.Call(0x00401500, [0] * 8, [0] * 8, 0)]
    d = dict(kind='call-target', index=0, x86='00402000', arm='00401500')
    pr._run = lambda c, n: (rx, ra, dict(d))
    got = pr.run(case)[2]
    vn = got.get('vslot_native')
    check(vn and vn['native_slot'] == 2 and vn['exe_slot'] == 1 and vn['exe'] == '00401200',
          'Pair.run: the native vtable slot holds another function than the exe slot the oracle ran (%s)' % vn)
    check(h(got) == 'native-vslot-differs', 'classify: native-vslot-differs (%s)' % h(got))
    check(causes.policy('native-vslot-differs')[:2] == ('real-bug', 'S2c'), 'native-vslot-differs: real bug of S2c')
    rx.blocks = set()
    check(not pr.run(case)[2].get('vslot_native'), 'Pair.run: the oracle ran no exe slot there, no evidence')
    print('%d failures' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
