#!/usr/bin/env python3
"""The far-transfer Form (proof/forms_flow.py far_transfer): CALL / JMP far, SDM Vol 2, protected mode, same privilege.  Expected values come from the SDM pseudocode and platform/declared/x86sys.json (cs = 0x1b),
not from the code under test.  Run through forms.build and the DSL's z3 semantics, and through the DSL emitter.   test_forms_far.py  (exit 0 = all pass)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import capstone as CS, z3
from proof import forms
from fnvlift import x86sys
md = CS.Cs(CS.CS_ARCH_X86, CS.CS_MODE_32); md.detail = True
FAILS = []; N = [0]
def check(name, ok):
    N[0] += 1
    if not ok: FAILS.append(name); print('FAIL', name)
CS_ = x86sys.SEL['cs']; A = 0x400000
def ins(code): return next(md.disasm(code, A))
def run(code, mem={}, **regs):
    """-> (fault, eip, esp, stores {addr: value-by-bytes}) with the memory bytes `mem` {addr: byte}; unspecified memory reads 0"""
    i = ins(code); F = forms.build(i, A); P = F.post()
    m = z3.K(z3.BitVecSort(32), z3.BitVecVal(0, 8))
    for a, b in mem.items(): m = z3.Store(m, z3.BitVecVal(a, 32), z3.BitVecVal(b, 8))
    sub = [(z3.Array('mem', z3.BitVecSort(32), z3.BitVecSort(8)), m)] + [(z3.BitVec(r, 32), z3.BitVecVal(regs.get(r, 0), 32)) for r in ('eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi')]
    g = lambda x: z3.simplify(z3.substitute(x, *sub))
    mem2 = g(P['mem']); rd = lambda a, n: sum(z3.simplify(mem2[z3.BitVecVal(a + k, 32)]).as_long() << (8 * k) for k in range(n))
    return g(P['fault']).as_long(), g(P['eip']).as_long(), g(P['regs']['esp']).as_long() if 'esp' in P['regs'] else regs.get('esp', 0), rd, F
def ptr(off, sel, w=4): return {0x1000 + k: (off >> (8 * k)) & 255 for k in range(w)} | {0x1000 + w + k: (sel >> (8 * k)) & 255 for k in range(2)}
JMPM, CALLM = b'\xff\x28', b'\xff\x18'          # jmp / call far [eax]
for sel, ok in ((CS_, 1), (CS_ & 0xfffc | 0, 1), (0x18, 1), (0x19, 1), (0x1a, 1), (0, 0), (0x23, 0), (0x20, 0), (0x08, 0), (0x1f, 0), (CS_ | 4, 0), (0x28, 0), (0x3b, 0)):
    f, e, s, rd, F = run(JMPM, ptr(0x12345678, sel), eax=0x1000)
    check('jmp far [m16:32] sel 0x%x: %s' % (sel, 'transfers' if ok else '#GP (fault 7)'), (f, e) == ((0, 0x12345678) if ok else (7, e)))
    f, e, s, rd, F = run(CALLM, ptr(0x12345678, sel), eax=0x1000, esp=0x2000)
    check('call far [m16:32] sel 0x%x: %s' % (sel, 'transfers' if ok else '#GP (fault 7)'), (f, e) == ((0, 0x12345678) if ok else (7, e)))
f, e, s, rd, F = run(CALLM, ptr(0x12345678, CS_), eax=0x1000, esp=0x2000)
check('call far m16:32: ESP - 8', s == 0x2000 - 8)
check('call far m16:32: [esp+4] = CS zero-extended to 32 bits, [esp] = the return EIP', rd(0x2000 - 4, 4) == CS_ and rd(0x2000 - 8, 4) == A + 2)
f, e, s, rd, F = run(b'\x66\xff\x18', ptr(0x1234, CS_, 2) | {0x1000 + k: v for k, v in ptr(0xdead1234, CS_, 2).items()}, eax=0x1000, esp=0x2000)
check('66 call far m16:16: EIP = the offset masked to 16 bits, ESP - 4, [esp+2] = CS, [esp] = IP', (f, e, s) == (0, 0x1234, 0x2000 - 4) and rd(0x2000 - 2, 2) == CS_ and rd(0x2000 - 4, 2) == (A + 3) & 0xffff)
f, e, s, rd, F = run(b'\x9a\x78\x56\x34\x12' + CS_.to_bytes(2, 'little'), esp=0x2000)
check('9A ptr16:32 with cs: EIP = the offset, pushes CS and the return EIP (next = +7)', (f, e, s) == (0, 0x12345678, 0x2000 - 8) and rd(0x2000 - 4, 4) == CS_ and rd(0x2000 - 8, 4) == A + 7)
f, e, s, rd, F = run(b'\x9a\x78\x56\x34\x12\x23\x00', esp=0x2000)
check('9A ptr16:32 with a data selector: fault 7', f == 7)
f, e, s, rd, F = run(b'\xea\x78\x56\x34\x12' + CS_.to_bytes(2, 'little'))
check('EA ptr16:32 with cs: EIP = the offset, no stack effect', (f, e, s) == (0, 0x12345678, 0))
f, e, s, rd, F = run(b'\xea\x78\x56\x34\x12\x08\x00')
check('EA ptr16:32 with the kernel code selector: fault 7', f == 7)
f, e, s, rd, F = run(b'\x66\xea\x34\x12' + CS_.to_bytes(2, 'little'))
check('66 EA ptr16:16: EIP = the 16-bit offset', (f, e) == (0, 0x1234))
# the form keys differ from the near FF forms of the same mnemonic and operand class
fk = forms.form_key
check('form key: far FF /3 differs from near FF /2 with the same operand class', fk(ins(b'\xff\x18')) != fk(ins(b'\xff\x10')) and fk(ins(b'\xff\x28')) != fk(ins(b'\xff\x20')))
check('form key: near FF /2 unchanged', fk(ins(b'\xff\x10')).endswith('|ff'))
# the emitter produces C for every far shape
for code in (JMPM, CALLM, b'\x66\xff\x18', b'\x66\xff\x28', b'\x9a\x78\x56\x34\x12\x1b\x00', b'\xea\x78\x56\x34\x12\x1b\x00', b'\x66\xea\x34\x12\x1b\x00'):
    F = forms.build(ins(code), A)
    try: F.to_c_inline(lambda n: 'v_' + n, lambda c, n, r: '', lambda v: '', (), 'M'); ok = True
    except Exception as ex: ok = False; print(repr(ex)[:200])
    check('emitter: %s' % F.key, ok)
print('cases', N[0], 'fails', len(FAILS))
sys.exit(1 if FAILS else 0)
