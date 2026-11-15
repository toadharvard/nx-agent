#!/usr/bin/env python3
"""The SSE FP flag-priority rule (dsl._pre, SDM Vol 1 11.5.2.1) and the conversion / reciprocal / MXCSR / hint Forms (proof/forms_ssefp.py), run as the real Forms through forms.build and the DSL's z3 semantics,
each Form's C text also emitted by the DSL emitter (to_c_inline).  Expected values come from the SDM text (rounding, indefinite, priority rows), not from the code under test.   test_forms_simdclass.py  (exit 0 = all pass)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import capstone as CS, z3
from proof import forms
from fnvlift import dsl
md = CS.Cs(CS.CS_ARCH_X86, CS.CS_MODE_32); md.detail = True
FAILS = []; N = [0]
def check(name, ok):
    N[0] += 1
    if not ok: FAILS.append(name); print('FAIL', name)
def build(code): return forms.build(next(md.disasm(code, 0x400000)), 0x400000)
def ev(P, key, env, sel=None):
    sub = [(z3.BitVec(k, w), z3.BitVecVal(v, w)) for k, (v, w) in env.items()]
    x = P[key] if sel is None else P[key][sel]
    return z3.simplify(z3.substitute(x, *sub)).as_long()
def run(code, **kv):
    """-> (fault, mxcsr, post dict) on the register environment kv (name -> value); xmm names are 128 bits, mxcsr 32, general registers 32"""
    F = build(code); P = F.post(); env = {k: (v, 128 if k.startswith('xmm') else 32) for k, v in kv.items()}
    env.setdefault('mxcsr', (0x1f80, 32))
    for i in range(8): env.setdefault('xmm%d' % i, (0, 128))
    for r in ('eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi'): env.setdefault(r, (0, 32))
    env.setdefault('ftag', (0, 16)); env.setdefault('ftop', (0, 3))
    return P, env
HDR = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'rt', 'fnvl.h')).read() + open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'rt', 'fnvl_x87.h')).read()
QN, SN, DEN, ONE, ZERO = 0x7fc00000, 0x7fa00000, 0x00000001, 0x3f800000, 0
def at(code, a, b, mx=0x1f80):
    """a two-operand register form xmm0 (dest), xmm1 (src): -> (fault, mxcsr, xmm0 low 32 bits)"""
    P, env = run(code, xmm0=a, xmm1=b, mxcsr=mx); return ev(P, 'fault', env), ev(P, 'mxcsr', env), ev(P, 'xmm', env, 0) & 0xffffffff
# ---- defect (a): a QNaN operand (row 2) stops the pre-computation flags; an SNaN is #I (row 1) and only #I
check('cmpeqps QNaN + denormal: no flag (row 2 before row 5)', at(b'\x0f\xc2\xc1\x00', QN, DEN) == (0, 0x1f80, 0))
check('cmpeqps denormal + QNaN: no flag', at(b'\x0f\xc2\xc1\x00', DEN, QN) == (0, 0x1f80, 0))
check('cmpeqps SNaN + denormal: IE only (row 1)', at(b'\x0f\xc2\xc1\x00', SN, DEN) == (0, 0x1f81, 0))
check('cmpltps QNaN + denormal: IE (a signalling predicate, row 3), no DE', at(b'\x0f\xc2\xc1\x01', QN, DEN) == (0, 0x1f81, 0))
check('cmpeqps two denormals: DE (row 5)', at(b'\x0f\xc2\xc1\x00', DEN, DEN) == (0, 0x1f82, 0xffffffff))
check('cmpeqps DAZ two denormals: no DE', at(b'\x0f\xc2\xc1\x00', DEN, DEN, 0x1fc0) == (0, 0x1fc0, 0xffffffff))
for m, k in ((b'\x0f\x2e\xc1', 'ucomiss'), (b'\x0f\x2f\xc1', 'comiss')):
    P, env = run(m, xmm0=QN, xmm1=DEN); fl = ev(P, 'mxcsr', env)
    check('%s QNaN + denormal: %s' % (k, 'IE only' if k == 'comiss' else 'no flag'), fl == (0x1f81 if k == 'comiss' else 0x1f80))
    check('%s QNaN + denormal: ZF PF CF = 1' % k, [ev(P, 'flags', env, f) for f in ('zf', 'pf', 'cf')] == [1, 1, 1])
# ---- defect (b): a zero divisor (row 4) before a denormal dividend (row 5)
check('divss denormal / 0: ZE only', at(b'\xf3\x0f\x5e\xc1', DEN, ZERO) == (0, 0x1f84, 0x7f800000))
check('divss 1 / 0: ZE', at(b'\xf3\x0f\x5e\xc1', ONE, ZERO) == (0, 0x1f84, 0x7f800000))
check('divss denormal / 2: DE and not ZE', at(b'\xf3\x0f\x5e\xc1', DEN, 0x40000000)[1] & 7 == 2)
check('divss SNaN / denormal: IE only', at(b'\xf3\x0f\x5e\xc1', SN, DEN)[1] & 7 == 1)
check('divsd denormal / 0: ZE only', run(b'\xf2\x0f\x5e\xc1', xmm0=1, xmm1=0) and ev(*(lambda P, e: (P, 'mxcsr', e))(*run(b'\xf2\x0f\x5e\xc1', xmm0=1, xmm1=0))) == 0x1f84)
# ---- conversions (SDM CVTSI2SS ... CVTTSD2SI): the integer indefinite, rounding by RC, PE
def cv(code, a, mx=0x1f80, reg=None):
    P, env = run(code, xmm1=a, mxcsr=mx, ecx=a & 0xffffffff, eax=0x55); return P, env
P, env = cv(b'\xf3\x0f\x2a\xc1', 0x01000001)
check('cvtsi2ss 2^24+1, RNE: 2^24, PE', ev(P, 'xmm', env, 0) & 0xffffffff == 0x4b800000 and ev(P, 'mxcsr', env) == 0x1fa0)
P, env = cv(b'\xf3\x0f\x2a\xc1', 0x01000001, 0x1f80 | (2 << 13))
check('cvtsi2ss 2^24+1, RC up: 2^24 + 2', ev(P, 'xmm', env, 0) & 0xffffffff == 0x4b800001)
P, env = cv(b'\xf3\x0f\x2a\xc1', 0x01000003)
check('cvtsi2ss 2^24+3, RNE tie to even: 2^24 + 4', ev(P, 'xmm', env, 0) & 0xffffffff == 0x4b800002)
P, env = cv(b'\xf2\x0f\x2a\xc1', 0x80000000)
check('cvtsi2sd -2^31: exact, no flag', ev(P, 'xmm', env, 0) & 0xffffffffffffffff == 0xc1e0000000000000 and ev(P, 'mxcsr', env) == 0x1f80)
def f2i(code, a, mx=0x1f80):
    P, env = run(code, xmm1=a, mxcsr=mx, eax=0x55); return ev(P, 'regs', env, 'eax'), ev(P, 'mxcsr', env)
D = lambda x: __import__('struct').unpack('<Q', __import__('struct').pack('<d', x))[0]
check('cvttsd2si 3.9 = 3, PE', f2i(b'\xf2\x0f\x2c\xc1', D(3.9)) == (3, 0x1fa0))
check('cvtsd2si 2.5 = 2 (RNE), PE', f2i(b'\xf2\x0f\x2d\xc1', D(2.5)) == (2, 0x1fa0))
check('cvtsd2si 3.5 = 4 (RNE)', f2i(b'\xf2\x0f\x2d\xc1', D(3.5))[0] == 4)
check('cvtsd2si -2.5 RC down = -3', f2i(b'\xf2\x0f\x2d\xc1', D(-2.5), 0x1f80 | (1 << 13))[0] == 0xfffffffd)
check('cvttsd2si 1e20: indefinite, IE only', f2i(b'\xf2\x0f\x2c\xc1', D(1e20)) == (0x80000000, 0x1f81))
check('cvttsd2si 2^31: indefinite, IE', f2i(b'\xf2\x0f\x2c\xc1', D(2147483648.0)) == (0x80000000, 0x1f81))
check('cvttsd2si -2^31 - 0.5 truncates to -2^31: in range, PE', f2i(b'\xf2\x0f\x2c\xc1', D(-2147483648.5)) == (0x80000000, 0x1fa0))
check('cvtsd2si QNaN: indefinite, IE', f2i(b'\xf2\x0f\x2d\xc1', 0x7ff8000000000000) == (0x80000000, 0x1f81))
check('cvtsd2si -inf: indefinite, IE', f2i(b'\xf2\x0f\x2d\xc1', 0xfff0000000000000) == (0x80000000, 0x1f81))
check('cvtsd2si denormal DAZ: 0, no flag', f2i(b'\xf2\x0f\x2d\xc1', 1, 0x1fc0) == (0, 0x1fc0))
# ---- reciprocal: the reference table; rcpss(1.0) is 126 : 0xffe (rcptab.c rcp_tab[0])
P, env = run(b'\xf3\x0f\x53\xc1', xmm0=(0xaaaa_bbbb_cccc_dddd << 32) | 0x11111111, xmm1=ONE)
check('rcpss 1.0 = 0x3f7ff000, upper lanes kept', ev(P, 'xmm', env, 0) == (0xaaaa_bbbb_cccc_dddd << 32) | 0x3f7ff000)
check('rcpss: no MXCSR effect', ev(P, 'mxcsr', env) == 0x1f80)
P, env = run(b'\x0f\x52\xc1', xmm1=(ZERO << 96) | (0x7fc00000 << 64) | (0xbf800000 << 32) | ONE)
v = ev(P, 'xmm', env, 0); L = [(v >> (32 * i)) & 0xffffffff for i in range(4)]
check('rsqrtps lane 0 (1.0)', L[0] == 0x3f7ff000); check('rsqrtps lane 1 (-1.0): indefinite', L[1] == 0xffc00000)
check('rsqrtps lane 2 (QNaN): quieted unchanged', L[2] == 0x7fc00000); check('rsqrtps lane 3 (+0): +inf', L[3] == 0x7f800000)
# ---- MXCSR, hints, MMX state
def memval(P, key, mem_bytes, regs):
    """evaluate a post-state field with the memory array set to mem_bytes ({addr: byte}) and the registers regs"""
    M0 = dsl.mem_array(); st = M0
    for a, b in mem_bytes.items(): st = z3.Store(st, z3.BitVecVal(a, 32), z3.BitVecVal(b, 8))
    sub = [(M0, st)] + [(z3.BitVec(k, 32), z3.BitVecVal(v, 32)) for k, v in regs.items()]
    return sub
F = build(b'\x0f\xae\x10'); P = F.post()          # ldmxcsr [eax]
RG = {r: 0 for r in ('ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi')}
for v, fault in ((0x1f80, 0), (0x1f80 | 0x40, 7), (0x1f80 | 0x10000, 7), (0x1f80 | (3 << 13) | 0x8000, 0)):
    sub = memval(P, 'fault', {0x100 + i: (v >> (8 * i)) & 0xff for i in range(4)}, dict(RG, eax=0x100)); sub.append((z3.BitVec('mxcsr', 32), z3.BitVecVal(0x1f80, 32)))
    f = z3.simplify(z3.substitute(P['fault'], *sub)).as_long(); m = z3.simplify(z3.substitute(P['mxcsr'], *sub)).as_long()
    check('ldmxcsr %#x: fault %d' % (v, fault), f == fault and (fault or m == v))
F = build(b'\x0f\xae\x18'); P = F.post()          # stmxcsr [eax]
sub = memval(P, 'mem', {}, dict(RG, eax=0x200)); sub.append((z3.BitVec('mxcsr', 32), z3.BitVecVal(0x1fa5, 32)))
w = z3.simplify(z3.substitute(z3.Concat(*[z3.Select(P['mem'], z3.BitVecVal(0x200 + i, 32)) for i in (3, 2, 1, 0)]), *sub)).as_long()
check('stmxcsr [eax]: stores MXCSR', w == 0x1fa5)
F = build(b'\x0f\x2b\x00'); P = F.post()
check('movntps [eax], xmm0: faults (kind 7) iff eax is not 16-byte aligned', all(z3.simplify(z3.substitute(P['fault'], (z3.BitVec('eax', 32), z3.BitVecVal(a, 32)))).as_long() == (0 if a % 16 == 0 else 7) for a in (0x100, 0x104, 0x108, 0x10f, 0x110)))
F = build(b'\x0f\x18\x00')
check('prefetchnta [eax]: no store, no register / flag / x87 / SSE effect, no fault', not F.stores and not F.regs and not F.flags and not F.faults and not F.xs)
F = build(b'\xf3\x90')
check('pause: no effect', not F.stores and not F.regs and not F.flags and not F.faults and not F.xs)
F = build(b'\x0f\x77'); P = F.post(); e = {'ftag': (0x3333, 16), 'ftop': (5, 3)}
check('emms: every tag empty, TOP unchanged', ev(P, 'ftag', e) == 0xffff and ev(P, 'ftop', e) == 5)
# ---- the DSL emitter produces C for every Form (the rule's text: cvt / tab nodes call the shipped helpers)
for code in (b'\xf3\x0f\x2a\xc1', b'\xf2\x0f\x2a\xc1', b'\x0f\x5b\xc1', b'\xf3\x0f\xe6\xc1', b'\x0f\x2a\xc1', b'\xf2\x0f\x2d\xc1', b'\xf2\x0f\x2c\xc1', b'\x0f\x53\xc1', b'\xf3\x0f\x52\xc1', b'\x0f\xae\x10', b'\x0f\xae\x18',
             b'\x0f\x2b\x00', b'\x0f\x18\x00', b'\x0f\x77', b'\xf3\x90', b'\x0f\xc2\xc1\x00', b'\x0f\x2e\xc1', b'\xf3\x0f\x5e\xc1'):
    F = build(code)
    try: txt = '\n'.join(F.to_c_inline(lambda n: 'v_' + n, lambda c, n, r: '', lambda v: '', (), 'M')[0])
    except Exception as ex: txt = None; print('emit', code.hex(), repr(ex)[:200])
    check('emitter: %s' % F.key, txt is not None)
    if txt is not None:
        import re
        used = set(re.findall(r'\b(fx_dsl_\w+|fnvl_tab_\w+)\(', txt))
        check('emitter: %s calls only shipped helpers' % F.key, all(re.search(r'static inline [\w ]+ %s\(' % h, HDR) for h in used))
        want = {'cvtsi2ss|xmm,r32|0f2a': 'fx_dsl_cvt', 'cvtdq2ps|xmm,xmm|0f5b': 'fx_dsl_cvt', 'cvtsd2si|r32,xmm|0f2d': 'fx_dsl_cvt', 'rcpps|xmm,xmm|0f53': 'fnvl_tab_rcp', 'rsqrtss|xmm,xmm|0f52': 'fnvl_tab_rsqrt', 'divss|xmm,xmm|0f5e': 'fx_dsl_fp'}.get(F.key)
        check('emitter: %s uses %s' % (F.key, want), want is None or want in used)
print('cases', N[0], 'fails', len(FAILS))
sys.exit(1 if FAILS else 0)
