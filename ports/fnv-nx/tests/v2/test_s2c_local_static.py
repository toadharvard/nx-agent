#!/usr/bin/env python3
"""V2 adversarial case for S2c-abi 73d4357's count "26 of 35 split-global rows are function-local statics (the saved
records say local-static, a harness artifact)", i.e. for the rule it rests on: tools/difffuzz/classify.py hint()
labels a divergence `local-static` (artifact) whenever the native read only function-local statics of its unit,
whatever the divergence is.

    python3 tests/v2/test_s2c_local_static.py      # synthetic records; capstone

  L1  call-target where the two machines make the same two calls in the opposite order (x86 A then B, native B then
      A): an evaluation-order difference, not the static. reports/s13b-causes has this shape: 0x00837550
      (impactmixer.cpp, `ImpactAudio::FUN_00453a70()->FUN_00ad73b0(pSoundA->FUN_0084e3a0(), ..)`: MSVC calls the
      argument 0x0084e3a0 first, the native the object 0x00453a70 first - ORDER-OBJECT, V1's C2 obj-arrow shape)
  C1  control: the native makes one extra call (its own static's initializer) and then the oracle's next call:
      local-static is right (0x00726920, 0x0076a9d0 have this shape)
"""
import os
import sys
import types

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
sys.path.insert(0, os.path.join(PORT, 'tools', 'difffuzz'))
import classify  # noqa: E402

FN = {'code': b'\x55\x8b\xec\x5d\xc3', 'address': 0x00401000}       # push ebp; mov ebp, esp; pop ebp; ret
TARGET = types.SimpleNamespace(source=None, conv=types.SimpleNamespace(x86=[]), orig_addr=0x00401000,
                               oracle_callees=None)
STATIC = ['_ZGVZN1S1fEvE1s', '_ZZN1S1fEvE1s']


def cause(div):
    return classify.hint(None, TARGET, None, div, FN, 0).get('cause')


def main():
    fails = 0
    l1 = cause(dict(kind='call-target', index=17, x86='0084e3a0', arm='00453a70', x86_next='00453a70',
                    arm_next='0084e3a0', arm_private_reads=1, arm_private_syms=STATIC[:1], arm_local_static=True))
    c1 = cause(dict(kind='call-target', index=0, x86='00726c40', arm='00a00940', x86_next='imp:EnterCriticalSection',
                    arm_next='00726c40', arm_private_reads=2, arm_private_syms=STATIC, arm_local_static=True))
    for name, got, ok in (('L1 swapped call pair', l1, l1 != 'local-static'),
                          ('C1 extra initializer call', c1, c1 == 'local-static')):
        print('%s %s: %s' % ('ok' if ok else 'FAIL', name, got))
        fails += not ok
    print('ok' if not fails else 'FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
