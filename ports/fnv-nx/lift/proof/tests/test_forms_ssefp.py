#!/usr/bin/env python3
"""SSE FP Forms (proof/forms_ssefp.py): directed cases from the SDM text only (flags, faults, the sticky MXCSR bits).  No emulator is an oracle: the certificate of a Form is L3b's reference run (Form == reference per shape), and fpc_host.py is a measurement of the C helper against the z3 rule.   test_forms_ssefp.py  (exit 0 = all pass)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import capstone as CS, z3
from proof import forms
from fnvlift import dsl
md = CS.Cs(CS.CS_ARCH_X86, CS.CS_MODE_32); md.detail = True
FAILS = []; N = 0
def main():
    # divss xmm1, xmm0 with 1.0 / 0.0
    code = b'\xf3\x0f\x5e\xc8'; ins = next(md.disasm(code, 0x400000)); P = forms.build(ins, 0x400000).post()
    def at(mx, a, b):
        sub = [(z3.BitVec('mxcsr', 32), z3.BitVecVal(mx, 32)), (z3.BitVec('xmm1', 128), z3.BitVecVal(a, 128)), (z3.BitVec('xmm0', 128), z3.BitVecVal(b, 128))]
        e = lambda x: z3.simplify(z3.substitute(x, *sub)).as_long(); return e(P['fault']), e(P['mxcsr']), e(P['xmm'][1])
    XM = __import__('fnvlift.faults', fromlist=['x']).BYNAME['XM']['code']      # one fault for every unmasked SIMD exception (Wine 9.0 fpe_handler); SDM Vol 1 11.5.2: the flags are set, the destination is unchanged
    one, zero = 0x3f800000, 0
    f, m, x = at(0x1f80, one, zero); ok = f == 0 and m == 0x1f84 and x == 0x7f800000
    FAILS.extend([] if ok else [('divss 1/0 masked', f, hex(m), hex(x))])
    f, m, x = at(0x1f80 & ~(1 << 9), one, zero); FAILS.extend([] if (f, m, x) == (XM, 0x1d84, one) else [('divss 1/0 unmasked ZM', f, hex(m), hex(x))])
    f, m, x = at(0x1f80, 0x3f800000, 0x40400000); ok = f == 0 and m == 0x1fa0 and x == 0x3eaaaaab      # 1/3: PE, rounded to nearest even
    FAILS.extend([] if ok else [('divss 1/3', f, hex(m), hex(x))])
    f, m, x = at(0x1f80 & ~(1 << 12), 0x3f800000, 0x40400000); FAILS.extend([] if (f, m, x) == (XM, 0x0fa0, 0x3f800000) else [('divss 1/3 unmasked PM', f, hex(m), hex(x))])
    f, m, x = at(0x1f80 & ~(1 << 7), 0x7fa00000, one); FAILS.extend([] if (f, m, x) == (XM, 0x1f01, 0x7fa00000) else [('divss SNaN unmasked IM', f, hex(m), hex(x))])
    print('cases', N, 'fails', len(FAILS))
    for f in FAILS: print('FAIL', f)
    return 1 if FAILS else 0
if __name__ == '__main__': sys.exit(main())
