#!/usr/bin/env python3
"""DSL coverage of the int classes that dsl_emit_test / gen_dslemit reported as emit errors (Unsupported('segment'), Unsupported('form variable bx|bp|si|di'),
the 16-bit pop AssertionError). One rule per class, checked here from the SDM, not from the code under test:
  GS override  -> the Form's gs_base is C `FNVL_GS_BASE()` (the GS base follows the current GS selector, core.Ctx.ea); FS stays `fsb`; ES/CS/SS/DS are flat (no base term).
  16-bit addressing (67) -> base / index are the low halves of the 32-bit registers, base + index*scale + disp is summed modulo 2^16 and zero-extended, the segment base is added after (SDM Vol 1 3.7.5).
  operand size of push / pop (66 prefix, stack size 32) -> the stack moves by 2 and a word is stored / loaded; a 16-bit register destination keeps the upper half (pop sp: SP gets the loaded word).
Numeric checks evaluate the Form with z3 on concrete states. Negative cases: a 32-bit push / pop still moves by 4; an unknown pre-state variable still raises Unsupported; no GS term without a GS override.
    dsl_int_classes_test.py          (exit 0 = all pass)"""
import os, random, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import capstone, z3
from capstone import x86 as X
from fnvlift import core, f_dsl
core.set_emit('dsl')
from fnvlift.dsl import to_smt
from proof import forms

md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); md.detail = True
A0, FAILS = 0x400000, []


def ins(hx):
    return next(md.disasm(bytes.fromhex(hx), A0))


def check(name, ok):
    print('%-64s %s' % (name, 'ok' if ok else 'FAIL'))
    if not ok:
        FAILS.append(name)


def emit(hx):
    i = ins(hx)
    cx = core.Ctx(A0, i.size, {A0: i}, {}, [])
    cx.start, cx.a, cx.nxt, cx.m, cx.pfx, cx.ops, cx.term, cx.xm, cx.xdp = A0, A0, A0 + i.size, i.mnemonic, '', i.operands, False, None, 0
    cx.uent, cx.b0, cx.modrm, cx.s = set(), i.opcode[0], i.modrm, i.operands[0].size * 8 if i.operands else 32
    r = f_dsl.emit_form(cx, i)
    return r, '\n'.join(cx.L)


def ev(e, env):
    return z3.simplify(z3.substitute(e, *[(z3.BitVec(k, w), z3.BitVecVal(v, w)) for k, (v, w) in env.items()])).as_long()


# ---- segment overrides
r, t = emit('658b00')                       # mov eax, gs:[eax]
check('gs: emitted (no Unsupported)', r is not core.SKIP and 'FNVL_GS_BASE()' in t)
r, t = emit('648b00')                       # mov eax, fs:[eax]
check('fs: base is fsb, no GS term', 'fsb' in t and 'FNVL_GS_BASE' not in t)
r, t = emit('268b00')                       # mov eax, es:[eax]: flat
check('es: flat, no segment term', 'FNVL_GS_BASE' not in t and 'fsb' not in t)
r, t = emit('8b00')
check('no override: no segment term', 'FNVL_GS_BASE' not in t and 'fsb' not in t)
r, t = emit('65678b00')                     # mov eax, gs:[bx+si]
check('gs + 16-bit addressing: both', 'FNVL_GS_BASE()' in t)

# ---- 16-bit addressing: z3 evaluation of the effective address against base+index+disp mod 2^16
rng = random.Random(7)
for hx, f in (('678b00', lambda b, c, d, s, i: b + s), ('678b01', lambda b, c, d, s, i: b + i), ('678b02', lambda b, c, d, s, i: c + s), ('678b03', lambda b, c, d, s, i: c + i),
              ('678b04', lambda b, c, d, s, i: s), ('678b05', lambda b, c, d, s, i: i), ('678b07', lambda b, c, d, s, i: b),
              ('678b4010', lambda b, c, d, s, i: b + s + 0x10), ('678b4610', lambda b, c, d, s, i: c + 0x10), ('678b40f0', lambda b, c, d, s, i: b + s - 0x10),
              ('678b803412', lambda b, c, d, s, i: b + s + 0x1234), ('678b80f0ff', lambda b, c, d, s, i: b + s - 0x10)):
    F = forms.build(ins(hx), A0)
    ea = F.regs[0][1]                                    # eax <- load(ea, 32): the address is the first operand of the load node
    ld = ea
    while ld.op != 'load':
        ld = ld.a[0]
    ok = True
    for _ in range(12):
        b, c, d, s, i = (rng.getrandbits(32) for _ in range(5))
        env = {'ebx': (b, 32), 'ebp': (c, 32), 'edx': (d, 32), 'esi': (s, 32), 'edi': (i, 32)}
        got = ev(to_smt(ld.a[0]), env)
        want = f(b & 0xffff, c & 0xffff, d, s & 0xffff, i & 0xffff) & 0xffff
        ok &= got == want
    check('67 %s: address = sum of 16-bit halves mod 2^16' % hx, ok)

# ---- operand size of push / pop
def run(hx, esp, words):
    F = forms.build(ins(hx), A0); P = F.post()
    mem = z3.K(z3.BitVecSort(32), z3.BitVecVal(0, 8))
    for a, v, n in words:
        for k in range(n):
            mem = z3.Store(mem, a + k, (v >> 8 * k) & 0xff)
    env = {'esp': (esp, 32), 'ecx': (0x11223344, 32), 'ebp': (0x55667788, 32)}
    sub = [(z3.BitVec(k, w), z3.BitVecVal(v, w)) for k, (v, w) in env.items()]
    sub += [(z3.Array('mem', z3.BitVecSort(32), z3.BitVecSort(8)), mem)]
    return F, P, lambda e: z3.simplify(z3.substitute(e, *sub))
F, P, E = run('6659', 0x1000, [(0x1000, 0xbeef, 2)])               # pop cx
check('66 59 pop cx: esp+2, cx low word loaded, high kept', E(P['regs']['esp']).as_long() == 0x1002 and E(P['regs']['ecx']).as_long() == 0x1122beef)
F, P, E = run('665d', 0x1000, [(0x1000, 0xbeef, 2)])               # pop bp
check('66 5d pop bp', E(P['regs']['esp']).as_long() == 0x1002 and E(P['regs']['ebp']).as_long() == 0x5566beef)
F, P, E = run('665c', 0x1000, [(0x1000, 0xbeef, 2)])               # pop sp: SP = the loaded word, upper half from the incremented ESP
check('66 5c pop sp', E(P['regs']['esp']).as_long() == 0x0000beef)
F, P, E = run('6651', 0x1000, [])                                  # push cx
st = E(z3.Select(P['mem'], 0x1000 - 2)).as_long() | E(z3.Select(P['mem'], 0x1000 - 1)).as_long() << 8
check('66 51 push cx: esp-2, word 3344 stored', E(P['regs']['esp']).as_long() == 0x0ffe and st == 0x3344)
F, P, E = run('59', 0x1000, [(0x1000, 0xdeadbeef, 4)])             # negative: pop ecx moves by 4
check('59 pop ecx still esp+4 and loads a dword', E(P['regs']['esp']).as_long() == 0x1004 and E(P['regs']['ecx']).as_long() == 0xdeadbeef)
F, P, E = run('51', 0x1000, [])
check('51 push ecx still esp-4', E(P['regs']['esp']).as_long() == 0x0ffc)

# ---- negative: an unknown pre-state variable is still an emitter error
cx = core.Ctx(A0, 1, {}, {}, [])
try:
    f_dsl.varc(cx)('bx'); check('unknown variable raises Unsupported', False)
except core.Unsupported:
    check('unknown variable raises Unsupported', True)
check('gs_base maps to FNVL_GS_BASE()', f_dsl.varc(cx)('gs_base') == 'FNVL_GS_BASE()')
sys.exit(1 if FAILS else 0)
