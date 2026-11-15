#!/usr/bin/env python3
"""The C helper fx_dsl_fp of rt/fnvl.h against the z3 model of dsl.fp (the same rules, two implementations). The helper's text is cut out of rt/fnvl.h (the template and its wrapper), compiled on the host
with a shim for the aarch64 FPCR / FPSR hooks (fenv.h: rounding mode and the sticky flags), and run on random operands, with the fixed special cases, over every MXCSR combination of RC / DAZ / FTZ / UM.
The z3 side is the model that the prover judges; this test says the shipped C text computes the same values and flags. Both sides use the host's IEEE hardware only through fenv in the C side.
    fpc_host.py [N]       (exit 0 = no disagreement)"""
import ctypes, os, random, re, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, LIFT)
import z3
from fnvlift import dsl
from fnvlift.dsl import V, fp, fp_val, fp_flags, to_smt

src = open(os.path.join(LIFT, 'rt', 'fnvl.h')).read()
i = src.index('/* tininess AFTER rounding of a mul / div'); j = src.index('static inline int sse_sat(')
body = src[i:j].replace('"+w"', '"+x"')
SHIM = r'''
#include <fenv.h>
#include <string.h>
typedef unsigned char u8; typedef unsigned short u16; typedef unsigned int u32; typedef unsigned long long u64; typedef unsigned __int128 u128;
struct cpu_ { u32 mxcsr; } cpu_; static struct cpu_ *c = &cpu_;
#define FOWN_SSE() do{}while(0)
static inline u32 sse_live(void){ return 0; }
static inline void fnvl_fp_x87_clear(void){ feclearexcept(FE_ALL_EXCEPT); }
static inline u32 fnvl_fp_x87_flags(void){ int e=fetestexcept(FE_ALL_EXCEPT); return (e&FE_INVALID?1u:0u)|(e&FE_DIVBYZERO?4u:0u)|(e&FE_OVERFLOW?8u:0u)|(e&FE_INEXACT?32u:0u); }
static inline void sse_fpcr(u32 mx){ static const int m[4]={FE_TONEAREST,FE_DOWNWARD,FE_UPWARD,FE_TOWARDZERO}; fesetround(m[(mx>>13)&3]); }
'''
CODE = SHIM + body + r'''
extern "C" void dsl_fp(u32 op, u32 w, u64 a, u64 b, u32 mx, u64 *val, u32 *fl){ u128 r=fx_dsl_fp(op,w,a,b,mx); *val=(u64)r & (w==32?0xffffffffull:~0ull); *fl=(u32)(r>>w)&0x3f; }
'''
def build():
    d = tempfile.mkdtemp(); f = os.path.join(d, 'h.cc'); open(f, 'w').write(CODE)
    subprocess.check_call(['g++', '-O0', '-std=gnu++17', '-shared', '-fPIC', '-frounding-math', '-fno-fast-math', f, '-o', os.path.join(d, 'h.so')])
    return ctypes.CDLL(os.path.join(d, 'h.so'))

def main():
    N = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    lib = build(); lib.dsl_fp.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_ulonglong, ctypes.c_ulonglong, ctypes.c_uint, ctypes.POINTER(ctypes.c_ulonglong), ctypes.POINTER(ctypes.c_uint)]
    rng = random.Random(5); bad = tot = 0
    SP = {32: [0, 0x80000000, 1, 0x007fffff, 0x00800000, 0x00800001, 0x7f7fffff, 0x3f800000, 0xbf800000, 0x7f800000, 0xff800000, 0x7fc00000, 0x7f800001, 0x40000000, 0x3f000000, 0x40400000, 0x30800000],
          64: [0, 1 << 63, 1, 0x000fffffffffffff, 0x0010000000000000, 0x0010000000000001, 0x7fefffffffffffff, 0x3ff0000000000000, 0xbff0000000000000, 0x7ff0000000000000, 0xfff0000000000000,
               0x7ff8000000000000, 0x7ff0000000000001, 0x4000000000000000, 0x3fe0000000000000, 0x4008000000000000]}
    nodes = {(op, w): fp(op, w, V('a', w), V('b', w), V('mxcsr', 32)) for op in dsl.FPOPS for w in (32, 64)}
    smt = {k: (to_smt(fp_val(n)), to_smt(fp_flags(n))) for k, n in nodes.items()}
    for (op, w), (vv, ff) in sorted(smt.items()):
        fb, eb, bias = (23, 8, 127) if w == 32 else (52, 11, 1023)
        def near():       # operands whose exact product / quotient lies within a few exponents of the smallest normal, with mantissas near all-ones / all-zeros: the tininess-after-rounding boundary
            def mant(): return rng.choice([(1 << fb) - 1 - rng.getrandbits(3), rng.getrandbits(3), rng.getrandbits(fb), (1 << fb) - 1 - rng.getrandbits(fb // 2)])
            if op == 'mul': e1 = rng.randrange(1, 2 * bias); e2 = 1 - bias - (e1 - bias) + bias + rng.choice([-1, 0, 1, 2]); e2 = min(max(e2, 0), 2 * bias)
            else: e1 = rng.randrange(1, 2 * bias); e2 = e1 + bias - 1 + rng.choice([-1, 0, 1, 2]); e2 = min(max(e2, 1), 2 * bias)
            sg = rng.getrandbits(2); return ((sg & 1) << (w - 1) | e1 << fb | mant(), (sg >> 1) << (w - 1) | e2 << fb | mant())
        cases = [(a, b) for a in SP[w] for b in SP[w]] + [(rng.getrandbits(w), rng.getrandbits(w)) for _ in range(N)] + ([near() for _ in range(N * 6)] if op in ('mul', 'div') else [])
        for a, b in cases:
            for rc in range(4):
                for daz in (0, 1):
                    for ftz in (0, 1):
                        for um in (0, 1):
                            if rng.random() > 0.25 and (a, b) not in cases[:len(SP[w]) ** 2]: continue
                            mx = 0x1f80 | (rc << 13) | (daz << 6) | (ftz << 15); mx = (mx & ~(1 << 11)) | (um << 11)
                            v = ctypes.c_ulonglong(); f = ctypes.c_uint(); lib.dsl_fp(dsl.FPOPS.index(op), w, a, b, mx, ctypes.byref(v), ctypes.byref(f))
                            sub = [(z3.BitVec('a', w), z3.BitVecVal(a, w)), (z3.BitVec('b', w), z3.BitVecVal(b, w)), (z3.BitVec('mxcsr', 32), z3.BitVecVal(mx, 32))]
                            zv = z3.simplify(z3.substitute(vv, *sub)).as_long(); zf = z3.simplify(z3.substitute(ff, *sub)).as_long(); tot += 1
                            if (zv, zf) != (v.value, f.value):
                                bad += 1
                                if bad <= 12: print('DISAGREE %s%d a=%x b=%x mx=%x z3=(%x,%02x) c=(%x,%02x)' % (op, w, a, b, mx, zv, zf, v.value, f.value))
    print('fx_dsl_fp C vs z3: %d cases, %d disagree' % (tot, bad))
    return 1 if bad else 0
if __name__ == '__main__': sys.exit(main())
