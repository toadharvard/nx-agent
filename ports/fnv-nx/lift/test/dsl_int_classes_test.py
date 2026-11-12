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
os.environ['FNVL_EMIT'] = 'dsl'
import capstone, z3
from capstone import x86 as X
from fnvlift import core, f_dsl
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

# ================= the sys class (proof/forms_sys.py) =================
import re
from fnvlift import f_sys
from proof import forms_sys


def hand(hx):
    i = ins(hx)
    cx = core.Ctx(A0, i.size, {A0: i}, {}, [])
    cx.start, cx.a, cx.nxt, cx.pfx, cx.ops, cx.term, cx.xm, cx.xdp = A0, A0, A0 + i.size, '', i.operands, False, None, 0
    cx.m = i.mnemonic.split()[-1]
    cx.uent, cx.b0, cx.modrm, cx.s = set(), i.opcode[0], i.modrm, i.operands[0].size * 8 if i.operands else 32
    core.OPS[cx.m](cx, i)
    return '\n'.join(cx.L)


RAISE = re.compile(r'FNVL_RAISE(?:_AV)?\([^;]*?\);')
# the exception the process observes is the hand handler's, instruction by instruction
for hx in ('cd03', 'cd04', 'cd05', 'cd06', 'cd29', 'cd2c', 'cd2d', 'cd2e', 'ce', 'f1', 'fa', 'fb', 'f4', 'e410', 'ee', '6c', '0f32', '0f30', '0f06', '0f0110', '0f0118', '0f0010', '0f0018', '0f01f0',
           '0f22c0', '0f20c0', '0f23c0', '0f21c0', '0f33', 'ec', 'e610', '6e', '0f09', '0f35', '6201', '666201', '62480c'):
    r, t = emit(hx)
    check('raise rows %-8s equal the hand handler' % hx, r is not core.SKIP and RAISE.findall(t) == RAISE.findall(hand(hx)) and RAISE.findall(t) != [] and (re.search(r'if \((in_)?of\)', t) is not None) == (hx == 'ce'))
check('into is conditional on OF', 'if (of)' in emit('ce')[1].replace('( ', '(') or 'of' in emit('ce')[1])
check('bound: no raise without a memory fault condition (conditional)', emit('6201')[1].count('if (') >= 1)

# machine constants against rt/fnvl.h
hdr = open(os.path.join(os.path.dirname(HERE), 'rt', 'fnvl.h')).read()
for k, v in (('GDTR_BASE', forms_sys.GDTR_BASE), ('GDTR_LIMIT', forms_sys.GDTR_LIMIT), ('IDTR_BASE', forms_sys.IDTR_BASE), ('IDTR_LIMIT', forms_sys.IDTR_LIMIT), ('LDTR_SEL', forms_sys.LDTR_SEL),
             ('TR_SEL', forms_sys.TR_SEL), ('CR0', forms_sys.CR0)):
    m = re.search(r'#define FNVL_%s (0x[0-9a-fA-F]+|\d+)u?' % k, hdr)
    check('constant %s = rt/fnvl.h' % k, m is not None and int(m.group(1), 0) == v)
check('PRIV_MN = f_sys.PRIV_MN', tuple(forms_sys.PRIV_MN) == tuple(f_sys.PRIV_MN))

# arpl: ZF = RPL(dest) < RPL(src); the destination changes only then (memory: a conditional store)
def arpl_run(hx, regs, mem=None):
    F = forms.build(ins(hx), A0); P = F.post()
    env = [(z3.BitVec(k, 32), z3.BitVecVal(v, 32)) for k, v in regs.items()]
    m = z3.K(z3.BitVecSort(32), z3.BitVecVal(0, 8))
    for a, v in (mem or {}).items(): m = z3.Store(m, a, v)
    env.append((z3.Array('mem', z3.BitVecSort(32), z3.BitVecSort(8)), m))
    g = lambda e: z3.simplify(z3.substitute(e, *env))
    return F, P, g
for d, sv in ((0x1b, 0x23), (0x23, 0x1b), (0x21, 0x21), (0xffff, 0x0), (0x0, 0x3), (0x1, 0x2)):
    F, P, g = arpl_run('63c8', {'eax': 0xaaaa0000 | d, 'ecx': 0xbbbb0000 | sv})      # arpl ax, cx
    lt = (d & 3) < (sv & 3)
    check('arpl ax,cx d=%x s=%x' % (d, sv), g(P['flags']['zf']).as_long() == lt and g(P['regs']['eax']).as_long() == (0xaaaa0000 | ((d & ~3) | (sv & 3) if lt else d)))
    F, P, g = arpl_run('6308', {'eax': sv, 'ecx': 0x1000}, {0x1000: d & 255, 0x1001: d >> 8})    # arpl [eax]... uses eax as source, dest [eax] -> see below
F = forms.build(ins('6301'), A0)      # arpl [ecx], ax
check('arpl mem dest: the store is conditional (Form.swhen)', len(F.stores) == 1 and 0 in F.swhen)
check('negative: a plain mov store is unconditional', forms.build(ins('8901'), A0).swhen == {})
_, t = emit('6301')
check('arpl mem dest: emitted store is guarded', re.search(r'if \(.*\) M16\(', t) is not None)

# bound: signed bounds, both widths; faults exactly outside [lo, hi]
for hx, w in (('6201', 32), ('666201', 16)):
    F = forms.build(ins(hx), A0); P = F.post()
    for i, lo, hi in ((5, 0, 10), (-1, 0, 10), (11, 0, 10), (0, 0, 10), (10, 0, 10), (-5, -10, -3), (-11, -10, -3)):
        mk = lambda v: v & ((1 << w) - 1)
        mem = {}
        for k, v in ((0, lo), (w // 8, hi)):
            for b in range(w // 8): mem[0x2000 + k + b] = (mk(v) >> 8 * b) & 255
        env = [(z3.BitVec('eax', 32), z3.BitVecVal(mk(i), 32)), (z3.BitVec('ecx', 32), z3.BitVecVal(0x2000, 32))]
        m = z3.K(z3.BitVecSort(32), z3.BitVecVal(0, 8))
        for a, v in mem.items(): m = z3.Store(m, a, v)
        env.append((z3.Array('mem', z3.BitVecSort(32), z3.BitVecSort(8)), m))
        flt = z3.simplify(z3.substitute(P['fault'], *env)).as_long()
        check('bound w=%d i=%d [%d,%d]: fault=%d' % (w, i, lo, hi, 4 if not lo <= i <= hi else 0), flt == (4 if not lo <= i <= hi else 0))

# str / sldt / smsw / sgdt / sidt, cpuid / rdtsc: by structure
def stores(hx):
    F = forms.build(ins(hx), A0)
    return [(z3.simplify(to_smt(a)), v.w, z3.simplify(to_smt(v))) for a, v in F.stores]
st = stores('0f0008')
check('str [eax]: one 16-bit store of 0x28', len(st) == 1 and st[0][1] == 16 and st[0][2].as_long() == 0x28)
check('sgdt [eax]: limit then base', [(x[1], x[2].as_long()) for x in stores('0f0100')] == [(16, 0x3ff), (32, 0x80b95000)])
check('sidt [eax]: limit then base', [(x[1], x[2].as_long()) for x in stores('0f0108')] == [(16, 0x7ff), (32, 0x80b95400)])
check('smsw eax: CR0 (32 bits)', forms.build(ins('0f01e0'), A0).regs[0][0] == 'eax' and z3.simplify(to_smt(forms.build(ins('0f01e0'), A0).regs[0][1])).as_long() == 0x80010033)
check('smsw [eax]: low word of CR0', [(x[1], x[2].as_long()) for x in stores('0f0120')] == [(16, 0x0033)])
Fc = forms.build(ins('0fa2'), A0)
check('cpuid: four registers, all functions of (eax, ecx)', [r for r, _, _ in Fc.regs] == ['eax', 'ebx', 'ecx', 'edx'] and all(v.op == 'uf' and v.a[0].x == 'eax' and v.a[1].x == 'ecx' for _, v, _ in Fc.regs))
Fr = forms.build(ins('0f31'), A0)
check('rdtsc: eax / edx are the two halves of ONE timer read', Fr.regs[0][1].a[0] is Fr.regs[1][1].a[0] and Fr.regs[0][1].a[0].op == 'uf' and Fr.regs[0][1].a[0].w == 64)
_, t = emit('0f31')
check('rdtsc: emitted once (one fnvl_rdtsc call)', t.count('fnvl_rdtsc()') == 1)
# R9: inside a locked instruction (core.Ctx.macc == 'LK') every memory access of the Form is the LK accessor, never plain M
for hx in ('0108', '870a', '0fab08'):
    i = ins(hx)
    for macc, other in (('M', 'LK'), ('LK', 'M')):
        L, _ = forms.build(i, A0).to_c_inline(lambda n: n, lambda c, n, r: '', lambda v: '', (), macc)
        t = '\n'.join(L)
        check('%s accessor %s: loads and stores use only it' % (hx, macc), re.search(r'(?<![A-Za-z])%s(8|16|32)\(' % macc, t) is not None and re.search(r'(?<![A-Za-z])%s(8|16|32)\(' % other, t) is None)
cx_ = core.Ctx(A0, 3, {A0: ins('0108')}, {}, [])
cx_.start, cx_.a, cx_.nxt, cx_.m, cx_.pfx, cx_.ops, cx_.term, cx_.xm, cx_.xdp = A0, A0, A0 + 2, 'add', '', ins('0108').operands, False, None, 0
cx_.uent, cx_.b0, cx_.modrm, cx_.s, cx_.macc = set(), 1, ins('0108').modrm, 32, 'LK'
f_dsl.emit_form(cx_, ins('0108'))
check('f_dsl.emit_form passes cx.macc (LK in the emitted lines)', 'LK32(' in '\n'.join(cx_.L) and 'M32(' not in '\n'.join(cx_.L).replace('LK32(', ''))
sys.exit(1 if FAILS else 0)
