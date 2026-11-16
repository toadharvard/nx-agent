#!/usr/bin/env python3
"""DSL x87 / MMX / SSE state (fnvlift/dsl.py: F.fr F.ftop F.ftag F.fsw F.fptr F.xmm F.mxcsr, F.mmx, dsl.fp). Checked from the SDM, not from the code under test:
  MMX (Vol 1 9.5.1, Table 9-1): every MMX instruction but EMMS sets TOP = 0 and all tags valid and writes fr<N>[79:64] = 0xFFFF; mmN reads fr<N>[63:0]; EMMS sets all tags empty.
  SSE FP (Vol 1 4.8.3, 4.9.1, 10.2.3): binary32 / binary64 add sub mul div sqrt: values against numpy at the source type (round to nearest even), flags from the SDM rules on chosen operands.
  Faults: a fault leaves the whole state unchanged (the x87 / SIMD kinds are in dsl.FAULTS and the lifter's fault map).
    test_dsl_xsimd.py        (exit 0 = all pass)"""
import os, random, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
import numpy as np, z3
from fnvlift import dsl
from fnvlift.dsl import C, V, Form, to_smt, fr, xmm, mm, fp, fp_val, fp_flags, concat, extract

FAILS = []
def check(name, ok):
    print('%-72s %s' % (name, 'ok' if ok else 'FAIL'))
    if not ok: FAILS.append(name)
def ev(e, env=None):
    sub = [(z3.BitVec(k, w), z3.BitVecVal(v, w)) for k, (v, w) in (env or {}).items()]
    r = z3.simplify(z3.substitute(e, *sub) if sub else e)
    return r.as_long()
def regs0(n, w): return {'fr%d' % i: (0x1234 + i, 80) for i in range(8)}

# ---- widths of the frozen pre-state names
check('XW names/widths', dsl.XW['fr0'] == 80 and dsl.XW['fr7'] == 80 and dsl.XW['ftop'] == 3 and dsl.XW['ftag'] == 16 and dsl.XW['fcw'] == 16 and dsl.XW['fsw'] == 16
      and dsl.XW['fip'] == 32 and dsl.XW['fdp'] == 32 and dsl.XW['fop'] == 11 and dsl.XW['fcs'] == 16 and dsl.XW['fds'] == 16 and dsl.XW['xmm7'] == 128 and dsl.XW['mxcsr'] == 32)
try: V('fr0', 64); check('V rejects a wrong width', False)
except AssertionError: check('V rejects a wrong width', True)

# ---- MMX
F = Form('pxor', 0x400000, 3)
F.mmx([(3, mm(3) ^ mm(5))])
P = F.post()
env = {'fr3': (0x4001_0123456789abcdef, 80), 'fr5': (0x3fff_00ff00ff00ff00ff, 80), 'ftop': (5, 3), 'ftag': (0x3333, 16)}
check('mmx write: fr3 = 0xFFFF : (mm3 ^ mm5)', ev(P['fr'][3], env) == (0xFFFF << 64) | (0x0123456789abcdef ^ 0x00ff00ff00ff00ff))
check('mmx: other registers untouched', ev(P['fr'][4], {'fr4': (0x1234, 80)}) == 0x1234)
check('mmx: TOP = 0, all tags valid', ev(P['ftop'], env) == 0 and ev(P['ftag'], env) == 0)
F = Form('movd_store', 0x400000, 3); F.mmx()
P = F.post()
check('mmx without a register write still sets TOP = 0 and tags valid', ev(P['ftop'], env) == 0 and ev(P['ftag'], env) == 0 and ev(P['fr'][3], env) == 0x4001_0123456789abcdef)
F = Form('emms', 0x400000, 2); F.mmx(emms=True)
P = F.post()
check('emms: all tags empty, TOP and registers unchanged', ev(P['ftag'], env) == 0xFFFF and ev(P['ftop'], env) == 5 and ev(P['fr'][3], env) == 0x4001_0123456789abcdef)
try: F.mmx([(0, C(0, 32))]); check('mmx rejects a 32-bit value', False)
except AssertionError: check('mmx rejects a 32-bit value', True)

# ---- plain effects through post
F = Form('x', 0x400000, 2)
F.fr(2, concat(C(0x7fff, 16), C(0x8000000000000000, 64))); F.ftop((V('ftop', 3) - C(1, 3))); F.fsw(C(0x4100, 16)); F.fptr(C(1, 32), C(2, 32), C(0x7ff, 11), C(3, 16), C(4, 16))
F.xmm(1, concat(extract(xmm(0), 63, 0), extract(xmm(1), 127, 64))); F.mxcsr(V('mxcsr', 32) | C(0x20, 32))
P = F.post()
e2 = {'ftop': (0, 3), 'mxcsr': (0x1f80, 32), 'xmm0': (0x11112222333344445555666677778888, 128), 'xmm1': (0xaaaabbbbccccddddeeeeffff00001111, 128)}
check('fr effect', ev(P['fr'][2]) == (0x7fff << 64) | 0x8000000000000000)
check('ftop effect (wraps mod 8)', ev(P['ftop'], e2) == 7)
check('fsw / fptr effects', ev(P['fsw']) == 0x4100 and [ev(v) for v in P['fptr']] == [1, 2, 0x7ff, 3, 4])
check('xmm effect: (low qword of xmm0) : (high qword of xmm1)', ev(P['xmm'][1], e2) == (0x5555666677778888 << 64) | 0xaaaabbbbccccdddd)
check('mxcsr effect', ev(P['mxcsr'], e2) == 0x1fa0)
check('untouched state is the pre-state', ev(P['xmm'][0], e2) == e2['xmm0'][0])
G = Form('g', 0x400000, 2); G.fr(0, C(5, 80)); G.mxcsr(C(0, 32)); G.fault(12, dsl.bit(V('mxcsr', 32), 0)); P = G.post()
e3 = {'fr0': (7, 80), 'mxcsr': (1, 32)}
check('a fault leaves x87 / SIMD state unchanged', ev(P['fr'][0], e3) == 7 and ev(P['mxcsr'], e3) == 1 and ev(P['fault'], e3) == 12)
check('no fault: effects commit', ev(P['fr'][0], {'fr0': (7, 80), 'mxcsr': (0, 32)}) == 5)
check('fault kinds 12..18 are FLT_*', all(dsl.FAULTS[k].startswith('FLT_') for k in range(12, 19)) and set(dsl.FLT_STATUS) == set(range(12, 19)))

# ---- SSE FP against numpy
rng = random.Random(11)
def f32b(x): return struct.unpack('<I', struct.pack('<f', x))[0]
def f64b(x): return struct.unpack('<Q', struct.pack('<d', x))[0]
def pickbits(w):
    r = rng.random()
    if w == 32:
        return rng.choice([0, 0x80000000, 0x00000001, 0x007fffff, 0x00800000, 0x7f7fffff, 0x3f800000, 0xbf800000, 0x40490fdb]) if r < .4 else rng.getrandbits(32)
    return rng.choice([0, 1 << 63, 1, 0x000fffffffffffff, 0x0010000000000000, 0x7fefffffffffffff, 0x3ff0000000000000, 0x400921fb54442d18]) if r < .4 else rng.getrandbits(64)
NP = {32: (np.float32, np.uint32), 64: (np.float64, np.uint64)}
OPS = {'add': np.add, 'sub': np.subtract, 'mul': np.multiply, 'div': np.divide, 'sqrt': None}
bad = 0; tot = 0
node = {(op, w): (fp(op, w, V('a', w), V('b', w), V('mxcsr', 32))) for op in OPS for w in (32, 64)}
for (op, w), n in sorted(node.items()):
    FT, UT = NP[w]; vv = to_smt(fp_val(n)); ff = to_smt(fp_flags(n))
    for _ in range(300):
        a, b = pickbits(w), pickbits(w)
        env = {'a': (a, w), 'b': (b, w), 'mxcsr': (0x1f80, 32)}
        x, y = np.array([a], UT).view(FT)[0], np.array([b], UT).view(FT)[0]
        if np.isnan(x) or np.isnan(y) or False: continue
        with np.errstate(all='ignore'):
            want = np.sqrt(x) if op == 'sqrt' else OPS[op](x, y)
        if np.isnan(want): want_b = 0xFFC00000 if w == 32 else 0xFFF8000000000000
        else: want_b = int(np.array([want], FT).view(UT)[0])
        got = ev(vv, env); tot += 1
        if got != want_b:
            bad += 1
            if bad < 4: print('  %s%d a=%x b=%x got=%x want=%x' % (op, w, a, b, got, want_b))
check('fp values = numpy (RNE, %d cases, add sub mul div sqrt, f32 f64)' % tot, bad == 0)
n = fp('add', 32, V('a', 32), V('b', 32), V('mxcsr', 32)); ff = to_smt(fp_flags(n)); vv = to_smt(fp_val(n))
def flagsof(op, w, a, b, mx=0x1f80):
    n = node[(op, w)]
    return ev(to_smt(fp_flags(n)), {'a': (a, w), 'b': (b, w), 'mxcsr': (mx, 32)}), ev(to_smt(fp_val(n)), {'a': (a, w), 'b': (b, w), 'mxcsr': (mx, 32)})
IE, DE, ZE, OE, UE, PE = 1, 2, 4, 8, 16, 32
check('1.0 + 1.0: no flags', flagsof('add', 32, 0x3f800000, 0x3f800000) == (0, 0x40000000))
check('1.0 / 3.0: PE', flagsof('div', 32, 0x3f800000, 0x40400000)[0] == PE)
check('1.0 / 0: ZE, +inf', flagsof('div', 32, 0x3f800000, 0) == (ZE, 0x7f800000))
check('0 / 0: IE, default QNaN', flagsof('div', 32, 0, 0) == (IE, 0xFFC00000))
check('inf - inf: IE', flagsof('sub', 32, 0x7f800000, 0x7f800000) == (IE, 0xFFC00000))
check('SNaN + 1: IE, the SNaN quieted', flagsof('add', 32, 0x7f800001, 0x3f800000) == (IE, 0x7fc00001))
check('QNaN + 1: no flags, the QNaN', flagsof('add', 32, 0x7fc00005, 0x3f800000) == (0, 0x7fc00005))
check('max * 2: OE | PE, inf', flagsof('mul', 32, 0x7f7fffff, 0x40000000) == (OE | PE, 0x7f800000))
check('min normal / 2 exact: tiny but exact, no UE (UM masked)', flagsof('div', 32, 0x00800000, 0x40000000) == (0, 0x00400000))
check('tiny inexact: UE | PE', flagsof('div', 32, 0x00800001, 0x40000000)[0] == UE | PE)
check('denormal operand: DE', flagsof('add', 32, 0x00000001, 0x3f800000)[0] & DE == DE)
check('DAZ: denormal read as zero, no DE', flagsof('add', 32, 0x00000001, 0x3f800000, 0x1f80 | 0x40) == (0, 0x3f800000))
check('FTZ: tiny result flushed to zero, UE | PE', flagsof('div', 32, 0x00800000, 0x40000000, 0x1f80 | 0x8000) == (UE | PE, 0))
check('RC down: 1 + 2^-30 rounds down', flagsof('add', 32, 0x3f800000, 0x30800000, 0x1f80 | (1 << 13)) == (PE, 0x3f800000))
check('RC up: 1 + 2^-30 rounds up', flagsof('add', 32, 0x3f800000, 0x30800000, 0x1f80 | (2 << 13)) == (PE, 0x3f800001))
check('sqrt(-1): IE', ev(to_smt(fp_flags(node[('sqrt', 32)])), {'a': (0xbf800000, 32), 'b': (0, 32), 'mxcsr': (0x1f80, 32)}) == IE)
check('sqrt(2) binary64: PE', ev(to_smt(fp_flags(node[('sqrt', 64)])), {'a': (0x4000000000000000, 64), 'b': (0, 64), 'mxcsr': (0x1f80, 32)}) == PE)
print('FAIL: %d' % len(FAILS) if FAILS else 'ALL OK')
sys.exit(1 if FAILS else 0)
