#!/usr/bin/env python3
"""x87 core gate: rt/x87.c vs Unicorn/QEMU softfloat (bit-exact results, C0/C2/C3), plus SDM flag vectors.
usage: x87test.py [--n N] [--op NAME] [--seed S] [--disasm] [--work DIR]   (DIR: build directory, a declared input)"""
import argparse, ctypes, math, os, subprocess, sys
import numpy as np
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32
from unicorn.x86_const import UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_ECX

HERE = os.path.dirname(os.path.abspath(__file__))
U = np.uint64
DRIVER = r'''
#include "x87.h"
#include <string.h>
void run(int op, unsigned n, const unsigned char *in, unsigned char *out) {
  for (unsigned i = 0; i < n; i++) {
    const unsigned char *c = in + 48 * i; unsigned char *o = out + 48 * i;
    u32 fcw, fsw = 0, x2 = 0; u64 am, bm, raw = 0; u16 ase, bse; fnvl_f80 a, b, r = {0, 0}; int f80 = 0;
    memcpy(&fcw, c, 4); memcpy(&am, c + 16, 8); memcpy(&ase, c + 24, 2); memcpy(&bm, c + 32, 8); memcpy(&bse, c + 40, 2);
    a.m = am; a.se = ase; b.m = bm; b.se = bse;
    switch (op) {
    case 0: r = fnvl_x_add(a, b, fcw, &fsw); f80 = 1; break;
    case 1: r = fnvl_x_sub(a, b, fcw, &fsw); f80 = 1; break;
    case 2: r = fnvl_x_mul(a, b, fcw, &fsw); f80 = 1; break;
    case 3: r = fnvl_x_div(a, b, fcw, &fsw); f80 = 1; break;
    case 4: r = fnvl_x_sqrt(a, fcw, &fsw); f80 = 1; break;
    case 5: r = fnvl_x_rndint(a, fcw, &fsw); f80 = 1; break;
    case 6: r = fnvl_x_from_f32((u32)am, &fsw); f80 = 1; break;
    case 7: r = fnvl_x_from_f64(am, &fsw); f80 = 1; break;
    case 8: r = fnvl_x_from_i64((short)am); f80 = 1; break;
    case 9: r = fnvl_x_from_i64((int)am); f80 = 1; break;
    case 10: r = fnvl_x_from_i64((long long)am); f80 = 1; break;
    case 11: raw = fnvl_x_to_f32(a, fcw, &fsw); break;
    case 12: raw = fnvl_x_to_f64(a, fcw, &fsw); break;
    case 13: case 14: case 15: raw = (u64)fnvl_x_to_i64(a, fcw >> 10 & 3, 16 << (op - 13), &fsw); break;
    case 16: case 17: fnvl_x_cmp(a, b, op - 16, &fsw); break;
    case 18: fsw = fnvl_x_xam(a, 1); x2 = fnvl_x_xam(a, 0); break;
    case 19: { u8 p[10]; memcpy(p, &am, 8); memcpy(p + 8, &ase, 2); r = fnvl_x_from_bcd(p); f80 = 1; break; }
    case 20: { u8 p[10]; fnvl_x_to_bcd(a, fcw, p, &fsw); memcpy(&r.m, p, 8); memcpy(&r.se, p + 8, 2); f80 = 1; break; }
    case 21: r = a; f80 = 1; break;
    }
    if (f80) { memcpy(o, &r.m, 8); memcpy(o + 8, &r.se, 2); } else memcpy(o, &raw, 8);
    memcpy(o + 16, &fsw, 2); memcpy(o + 18, &x2, 2);
  }
}
'''
LD = lambda d: bytes([0xDB, 0x6E, d])          # fld tbyte [esi+d]
ST = b'\xDB\x3F'                               # fstp tbyte [edi]
FS = b'\xDD\x7F\x10'                           # fnstsw [edi+16]
# name: (load, op, store, kind, result bytes (0 = tbyte, -1 = none), fsw mask for the unicorn mode); ab = fld b; fld a
ab = LD(32) + LD(16)
OPS = {
 'add': (ab, b'\xD8\xC1', ST, 'bin', 0, 0), 'sub': (ab, b'\xD8\xE1', ST, 'bin', 0, 0),
 'mul': (ab, b'\xD8\xC9', ST, 'bin', 0, 0), 'div': (ab, b'\xD8\xF1', ST, 'bin', 0, 0),
 'sqrt': (LD(16), b'\xD9\xFA', ST, 'un', 0, 0), 'rndint': (LD(16), b'\xD9\xFC', ST, 'un', 0, 0),
 'fld32': (b'\xD9\x46\x10', b'', ST, 'f32', 0, 0), 'fld64': (b'\xDD\x46\x10', b'', ST, 'f64', 0, 0),
 'fild16': (b'\xDF\x46\x10', b'', ST, 'i16', 0, 0), 'fild32': (b'\xDB\x46\x10', b'', ST, 'i32', 0, 0),
 'fild64': (b'\xDF\x6E\x10', b'', ST, 'i64', 0, 0),
 'fst32': (LD(16), b'', b'\xD9\x1F', 'un', 4, 0), 'fst64': (LD(16), b'', b'\xDD\x1F', 'un', 8, 0),
 'fist16': (LD(16), b'', b'\xDF\x1F', 'un', 2, 0), 'fist32': (LD(16), b'', b'\xDB\x1F', 'un', 4, 0),
 'fist64': (LD(16), b'', b'\xDF\x3F', 'un', 8, 0),
 'fcom': (ab, b'\xD8\xD1', b'', 'bin', -1, 0x4500), 'fucom': (ab, b'\xDD\xE1', b'', 'bin', -1, 0x4500),
 'fxam': (LD(16), b'\xD9\xE5\xDD\x7F\x12\xDD\xD8\xD9\xE5', b'', 'un', -1, 0x4700),
 'fbld': (b'\xDF\x66\x10', b'', ST, 'bcd', 0, 0), 'fbstp': (LD(16), b'', b'\xDF\x37', 'un', 0, 0),
 'ld80': (LD(16), b'', ST, 'un', 0, 0),
}
NAMES = list(OPS)
FCWS = [0x7f | pc << 8 | rc << 10 for pc in (0, 1, 2, 3) for rc in range(4)]
IN_T = np.dtype({'names': ['fcw', 'am', 'ase', 'bm', 'bse'], 'formats': ['<u4', '<u8', '<u2', '<u8', '<u2'],
                 'offsets': [0, 16, 24, 32, 40], 'itemsize': 48})
OUT_T = np.dtype({'names': ['r', 'rse', 'fsw', 'fsw2', 'r2', 'r2se'], 'formats': ['<u8', '<u2', '<u2', '<u2', '<u8', '<u2'],
                  'offsets': [0, 8, 16, 18, 24, 32], 'itemsize': 48})
NATIVE = r"""
#define _GNU_SOURCE
#include <sys/mman.h>
#include <string.h>
/* run the same instruction sequence the unicorn loop uses on the real x87: fn(out in rdi, in in rsi) per case */
int nat_run(const unsigned char *code, int len, unsigned n, const unsigned char *in, unsigned char *out) {
  void *p = mmap(0, 4096, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (p == MAP_FAILED) return -1;
  memcpy(p, code, len);
  for (unsigned i = 0; i < n; i++) ((void (*)(unsigned char *, const unsigned char *))p)(out + 48 * i, in + 48 * i);
  munmap(p, 4096); return 0;
}
"""

def build(SCR):
    os.makedirs(SCR, exist_ok=True)
    open(SCR + "driver.c", "w").write(DRIVER); open(SCR + "native.c", "w").write(NATIVE)
    subprocess.check_call(["gcc", "-O2", "-shared", "-fPIC", "-I", HERE + "/../rt", HERE + "/../rt/x87.c",
                           SCR + "driver.c", "-o", SCR + "x87t.so"])
    subprocess.check_call(["gcc", "-O1", "-shared", "-fPIC", SCR + "native.c", "-o", SCR + "nat.so"])
    return ctypes.CDLL(SCR + "x87t.so"), ctypes.CDLL(SCR + "nat.so")

def body(op):
    ld, o, st, *_ = OPS[op]
    return ld + o + (FS + st if st[:2] == ST else st + FS)      # status word before the m80 store (which clears C1)

def native(op, rec, nat):
    code = b'\xDB\xE3\xD9\x2E' + body(op) + b'\xDB\xE3\xC3'      # fninit; fldcw [rsi]; ...; fninit; ret
    out = np.zeros(len(rec), OUT_T)
    assert nat.nat_run(code, len(code), len(rec), rec.ctypes.data_as(ctypes.c_char_p), out.ctypes.data_as(ctypes.c_char_p)) == 0
    return out

# ---------------------------------------------------------------- oracle (Unicorn)
CH, CODE, INB, OUTB = 32768, 0x1000, 0x100000, 0x400000
def oracle(op, rec):
    uc = Uc(UC_ARCH_X86, UC_MODE_32)
    code = b'\xDB\xE3\xD9\x2E' + body(op) + b'\x83\xC6\x30\x83\xC7\x30\x49'      # ...; esi+=48; edi+=48; dec ecx
    code += b'\x0F\x85' + (-(len(code) + 6)).to_bytes(4, 'little', signed=True)
    uc.mem_map(CODE, 0x1000); uc.mem_map(INB, 0x300000); uc.mem_map(OUTB, 0x200000)
    uc.mem_write(CODE, code)
    out = np.zeros(len(rec), OUT_T)
    for i in range(0, len(rec), CH):
        k = min(CH, len(rec) - i)
        uc.mem_write(INB, rec[i:i + k].tobytes())
        uc.reg_write(UC_X86_REG_ESI, INB); uc.reg_write(UC_X86_REG_EDI, OUTB); uc.reg_write(UC_X86_REG_ECX, k)
        uc.emu_start(CODE, CODE + len(code))
        out[i:i + k] = np.frombuffer(bytes(uc.mem_read(OUTB, 48 * k)), OUT_T)
    return out

def disasm():
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32
    for n in OPS:
        print(n, '; '.join(f"{i.mnemonic} {i.op_str}" for i in Cs(CS_ARCH_X86, CS_MODE_32).disasm(b'\xDB\xE3\xD9\x2E' + body(n), 0)))

# ---------------------------------------------------------------- operand generator
def norm(v):                       # uint64 array -> (normalized m, shift)
    sh = np.zeros(len(v), U)
    for s in (32, 16, 8, 4, 2, 1):
        z = (v >> U(64 - s)) == 0
        v = np.where(z, v << U(s), v); sh += np.where(z, U(s), U(0))
    return v, sh

def f80s(sign, e, m): return m.astype(U), (e.astype(np.int64) & 0x7FFF).astype(np.uint16) | (sign.astype(np.uint16) << 15)

def d2f80(x):                      # float64 array (finite, nonzero) -> exact f80
    mant, e = np.frexp(np.abs(x))
    return f80s(x < 0, e - 1 + 16383, (mant * 2.0 ** 64).astype(U))

def build_pool():
    P = []
    for s in (0, 1):
        sg = s << 15
        P += [(0, sg), (1, sg), (2**63 - 1, sg), (2**63, sg), (0x8000000000000001, sg), (2**64 - 1, sg), (1 << 62, sg)]  # zero/denormal/pseudo-denormal
        P += [(2**63, sg | 1), (2**64 - 1, sg | 1), (2**63 + 1, sg | 1), (2**63, sg | 0x7FFE), (2**64 - 1, sg | 0x7FFE), (2**64 - 2, sg | 0x7FFE)]
        for e in list(range(16383 - 70, 16383 + 70, 3)) + [16383 - 16, 16383 + 16, 2, 3, 0x7FFD]:
            P += [(2**63, sg | e), (2**63 + 1, sg | e), (2**64 - 1, sg | e), (2**63 + 2**39, sg | e), (2**63 + 2**10, sg | e)]
        for k in (24, 31, 32, 53, 63):
            for d in (-2, -1, 0, 1, 2):
                v = (1 << k) + d; sh = 64 - v.bit_length(); P.append((v << sh, sg | (16383 + 63 - sh)))
        for x in (1.0, 0.5, 1.5, 2.5, 3.0, 10.0, 0.1, 1 / 3, 255.5, 65535.5, 1e30, 1e-30, 3.4e38, 1e39, 1e-45, 1e-50):
            mt, ex = math.frexp(x); P.append((int(mt * 2**64), sg | (ex - 1 + 16383)))
        P += [(2**63, sg | 0x7FFF), (0, sg | 0x7FFF), (2**63 + 1, sg | 0x7FFF), (2**62, sg | 0x7FFF)]      # inf, pseudo-inf, SNaN, pseudo-NaN
        P += [(0xC000000000000000, sg | 0x7FFF), (0xC000000000000001, sg | 0x7FFF), (0xFFFFFFFFFFFFFFFF, sg | 0x7FFF),
              (0xA000000000000000, sg | 0x7FFF), (0x8000000000000001, sg | 0x7FFF), (0xBFFFFFFFFFFFFFFF, sg | 0x7FFF),
              (0xC123456789ABCDEF, sg | 0x7FFF), (0x9234567890ABCDEF, sg | 0x7FFF)]
    return np.array([p[0] for p in P], U), np.array([p[1] for p in P], np.uint16)
POOL = build_pool()

def marg(rng, N):
    """Marginal operand mix: special pool, random patterns, moderate/boundary exponents, typical doubles/floats, integers."""
    c = rng.integers(0, 100, N); sign = rng.integers(0, 2, N)
    m = rng.integers(0, 2**64, N, dtype=U) | (U(1) << U(63)) * (rng.random(N) < 0.9).astype(U)
    e = rng.integers(0, 0x8000, N)
    e = np.where((c >= 10) & (c < 30), 16383 + rng.integers(-80, 80, N), e)
    e = np.where((c >= 30) & (c < 40), rng.choice([0, 1, 2, 0x7FFD, 0x7FFE, 0x7FFF], N), e)
    am, ase = m.copy(), np.array(f80s(sign, e, m)[1])
    am = np.where(c < 10, rng.integers(0, 2**64, N, dtype=U), am)                    # fully random pattern
    x = rng.standard_normal(N) * 2.0 ** rng.uniform(-30, 30, N)
    x = np.where(rng.random(N) < 0.5, x.astype(np.float32).astype(np.float64), x); x[x == 0] = 1.0
    dm, dse = d2f80(x); t = (c >= 40) & (c < 65)
    am, ase = np.where(t, dm, am), np.where(t, dse, ase)
    k = rng.integers(0, 64, N).astype(U); v = (U(1) << k) + rng.integers(0, 6, N).astype(U) - U(3)
    nm, sh = norm(np.maximum(v, U(1))); im, ise = f80s(sign, 16383 + 63 - sh.astype(np.int64), nm); t = (c >= 65) & (c < 75)
    am, ase = np.where(t, im, am), np.where(t, ise, ase)
    pm, pse = POOL; pi = rng.integers(0, len(pm), N); t = c >= 75
    return np.where(t, pm[pi], am), np.where(t, pse[pi], ase)

def pbits(fcw): return np.where((fcw >> 8) & 3, np.where((fcw >> 8) & 3 == 2, 53, 64), 24).astype(np.int64)

def oddbits(rng, k):               # random integers with exactly k bits (top bit set), shifted to the top of 64
    r = rng.integers(0, 2**64, len(k), dtype=U) >> (U(64) - k.astype(U))
    return (r | (U(1) << (k.astype(U) - U(1)))) << (U(64) - k.astype(U))

def ties(op, rng, N, p):
    """Operand pairs whose result lands on, or one step beside, a rounding tie at precision p."""
    e0 = 16383 + rng.integers(-30, 30, N); sg = rng.integers(0, 2, N)
    if op in ('add', 'sub'):
        am = oddbits(rng, rng.integers(1, 65, N)) & ~((U(1) << (U(64) - p.astype(U))) - U(1)) | (U(1) << U(63))   # a has p bits
        d = rng.integers(-1, 2, N); bm = np.where(d < 0, ~U(0), U(2**63) + (rng.integers(0, 3, N)).astype(U) * (d == 0))
        be = e0 - p - (d < 0) + (d > 0) * rng.integers(0, 2, N)
        return (*f80s(sg, e0, am), *f80s(rng.integers(0, 2, N), be, bm))
    if op == 'mul' or op == 'div':
        k1 = np.minimum(rng.integers(1, 65, N), p + 1) if op == 'mul' else np.maximum(1, np.minimum(rng.integers(1, 40, N), 63 - p))
        k2 = np.where(p + 1 - k1 >= 1, p + 1 - k1, 1) if op == 'mul' else np.minimum(p + 1, 64 - k1)
        x, y = oddbits(rng, k1), oddbits(rng, k2)
        if op == 'div':                                    # a = q * b exactly: q has p+1 bits
            q = y >> U(64) - k2.astype(U); y2 = x >> U(64) - k1.astype(U); x, _ = norm(q * y2)
            x = np.where(x == 0, U(2**63), x); y = x * 0 + (y2 << (U(64) - k1.astype(U))); y = np.where(y == 0, U(2**63), y)
        j = rng.integers(0, 4, N).astype(U); x = np.where(j == 1, x + (U(1) << U(11)), np.where(j == 2, x - (U(1) << U(11)), x))
        x |= U(1) << U(63)
        return (*f80s(sg, e0, x), *f80s(rng.integers(0, 2, N), 16383 + rng.integers(-30, 30, N), y))
    return None

def tie_unary(op, rng, N, m, se):
    """Force the dropped bits of a unary operand to a tie pattern (fst/fist/rndint/sqrt)."""
    if op in ('fst32', 'fst64'):
        mb, emin = (23, -126) if op == 'fst32' else (52, -1022)
        E = np.where(rng.random(N) < 0.15, -emin + rng.integers(-2, 3, N), emin + rng.integers(-mb - 3, 2 * mb, N))
        t = 62 - mb + np.maximum(0, emin - E)
    else:
        E = rng.integers(-3, 66, N); t = 62 - E
    t = np.clip(t, 0, 63).astype(U); d = rng.integers(0, 4, N).astype(U)
    nm = ((m >> (t + U(1))) << (t + U(1))) | (U(1) << t)
    nm = np.where(d == 1, nm + U(1), np.where(d == 2, nm - U(1), np.where(d == 3, nm & ~((U(1) << t) - U(1)) | (U(1) << t) - U(1), nm)))
    nm |= U(1) << U(63)
    return nm, f80s((se >> 15), 16383 + E, nm)[1]

def edge(op, rng, N, am, ase, bm, bse):
    """Results landing at the exponent limits (min normal / max normal, +-2) with mostly-ones significands."""
    t = rng.random(N) < 0.12; hi = rng.random(N) < 0.4; d = rng.integers(-2, 3, N); k = np.where(hi, 0x7FFE + d, d + 1)
    if op == 'mul':
        ea = np.where(hi, rng.integers(20000, 29000, N), rng.integers(1000, 15000, N)); eb = k + 16382 - ea
    elif op == 'div':
        ea = np.where(hi, rng.integers(17000, 32000, N), rng.integers(1, 16000, N)); eb = ea + 16383 - k
    else:
        ea = rng.integers(0, 4, N); eb = rng.integers(0, 4, N)
    top = U(1) << U(63); ones = rng.random(N) < 0.5; sh = rng.integers(0, 40, N).astype(U)
    m1 = np.where(ones, ~U(0) >> sh, rng.integers(0, 2**64, N, dtype=U)) | top
    m2 = np.where(ones, ~U(0) << sh >> sh, rng.integers(0, 2**64, N, dtype=U)) | top
    s1, s2 = (ase & 0x8000), (bse & 0x8000)
    return (np.where(t, m1, am), np.where(t, (ea & 0x7FFF).astype(np.uint16) | s1, ase),
            np.where(t, m2, bm), np.where(t, (eb & 0x7FFF).astype(np.uint16) | s2, bse))

def gen(op, rng, N, fcw):
    kind = OPS[op][3]; p = pbits(fcw)
    if kind in ('f32', 'f64'):
        w = 32 if kind == 'f32' else 64
        x = rng.integers(0, 2**64, N, dtype=U) & U(2**w - 1); eb = 8 if w == 32 else 11; mb = w - 1 - eb
        ex = rng.choice([0, 0, 1, 2 ** eb - 2, 2 ** eb - 1, 2 ** eb - 1], N).astype(U)
        x = np.where(rng.random(N) < 0.3, (x & ~(U(2**eb - 1) << U(mb))) | (ex << U(mb)), x)
        return x, np.zeros(N, np.uint16), np.zeros(N, U), np.zeros(N, np.uint16)
    if kind == 'bcd':                                   # valid BCD mostly; sometimes raw garbage nibbles / sign bits
        dg = rng.integers(0, 10, (N, 18)); sh = (np.arange(16, dtype=U) * U(4))
        m = (dg[:, :16].astype(U) << sh).sum(1, dtype=U); se = (dg[:, 16] | dg[:, 17] << 4).astype(np.uint16) | (rng.integers(0, 2, N).astype(np.uint16) << 15)
        g = rng.random(N) < 0.3; m = np.where(g, rng.integers(0, 2**64, N, dtype=U), m); se = np.where(g, rng.integers(0, 65536, N).astype(np.uint16), se)
        z = rng.random(N) < 0.1; m = np.where(z, U(0), m)
        return m, se, np.zeros(N, U), np.zeros(N, np.uint16)
    if kind[0] == 'i':
        w = int(kind[1:]); k = rng.integers(0, w + 1, N).astype(U)
        v = rng.integers(0, 2**64, N, dtype=U) >> (U(64) - np.maximum(k, U(1)))
        v = np.where(rng.random(N) < 0.15, rng.choice(np.array([0, 1, 2**(w - 1) - 1, 2**(w - 1), 2**w - 1, 2**w - 2], U), N), v)
        v = np.where(rng.random(N) < 0.5, (U(0) - v), v) & U(2**w - 1)
        return v, np.zeros(N, np.uint16), np.zeros(N, U), np.zeros(N, np.uint16)
    am, ase = marg(rng, N); bm, bse = marg(rng, N)
    t = rng.random(N) < 0.35
    if kind == 'bin' and op in ('add', 'sub', 'mul', 'div'):
        tm = ties(op, rng, N, p)
        am, ase, bm, bse = [np.where(t, a, b) for a, b in zip(tm, (am, ase, bm, bse))]
        am, ase, bm, bse = edge(op, rng, N, am, ase, bm, bse)
    elif op == 'sqrt':
        k = np.minimum(np.where(p == 24, 25, 32), rng.integers(1, 33, N)); y = oddbits(rng, k) >> U(64) - k.astype(U)
        y = y + (rng.integers(0, 3, N).astype(U) - U(1)) * (rng.random(N) < 0.3); y = np.maximum(y, U(1))
        sm, sh = norm(y * y); sm, sse = f80s(np.zeros(N), 16383 + rng.integers(-20, 20, N) - sh.astype(np.int64), sm)
        am, ase = np.where(t, sm, am), np.where(t, sse, ase)
        ase = np.where(rng.random(N) < 0.9, ase & np.uint16(0x7FFF), ase)               # mostly non-negative
    elif op in ('rndint', 'fst32', 'fst64', 'fbstp') or op.startswith('fist'):
        nm, nse = tie_unary(op, rng, N, am, ase); am, ase = np.where(t, nm, am), np.where(t, nse, ase)
        if op == 'fbstp':                                 # integers around 10^18
            vm, sh = norm((U(10**18) + rng.integers(0, 7, N).astype(U) - U(3)) * (U(1) + (rng.random(N) < 0.0))); vm, vse = f80s(ase >> 15, 16383 + 63 - sh.astype(np.int64), vm)
            u = rng.random(N) < 0.15; am, ase = np.where(u, vm, am), np.where(u, vse, ase)
    return am, ase, bm, bse

# ---------------------------------------------------------------- SDM flag vectors
def D(x):
    if x == 0: return (0, 0x8000 if math.copysign(1, x) < 0 else 0)
    if math.isinf(x): return (2**63, 0x7FFF | (0x8000 if x < 0 else 0))
    mt, e = math.frexp(abs(x)); return (int(mt * 2**64), (e - 1 + 16383) | (0x8000 if x < 0 else 0))
P2 = lambda k, s=0: (2**63, 16383 + k | s << 15)         # +-2^k
MINN, MAXN, DEN1, QN, SN, INF, NINF = (2**63, 1), (2**64 - 1, 0x7FFE), (1, 0), (0xC000000000000000, 0x7FFF), (0xA000000000000000, 0x7FFF), D(math.inf), D(-math.inf)
F = {c: 1 << i for i, c in enumerate("IDZOUP")}; F['c'] = 0x200
NEAR, DOWN, UP, ZERO = 0x37f, 0x77f, 0xb7f, 0xf7f
# (op, args, fcw, flags, expected result or None). cmp args: (a, b, quiet); to_i64: (a, rc, bits); xam: (a, empty)
VECT = [
 ('add', (D(1), D(1)), NEAR, '', D(2)), ('add', (D(1), P2(-64)), NEAR, 'P', D(1)), ('add', (D(1), P2(-64)), UP, 'Pc', (2**63 + 1, 16383)),
 ('add', (D(1), P2(-64)), DOWN, 'P', D(1)), ('add', (D(1), P2(-30)), 0x7f, 'P', D(1)), ('add', (D(1), D(2**-24 * 1.5)), 0x7f, 'Pc', (2**63 + 2**40, 16383)),
 ('add', (D(1), P2(-53)), 0x27f, 'P', D(1)), ('add', (D(1), D(1.5 * 2**-53)), 0x27f, 'Pc', (2**63 + 2**11, 16383)),
 ('add', (MAXN, MAXN), NEAR, 'OPc', INF), ('add', (MAXN, MAXN), ZERO, 'OP', MAXN), ('add', (MAXN, MAXN), 0x27f, 'OPc', INF),
 ('add', (D(0.0), D(-0.0)), NEAR, '', D(0.0)), ('add', (D(0.0), D(-0.0)), DOWN, '', D(-0.0)), ('sub', (D(1), D(1)), DOWN, '', D(-0.0)),
 ('sub', (INF, INF), NEAR, 'I', (3 << 62, 0xFFFF)), ('add', (INF, NINF), NEAR, 'I', (3 << 62, 0xFFFF)), ('add', (INF, INF), NEAR, '', INF),
 ('add', (DEN1, DEN1), NEAR, 'D', (2, 0)), ('add', (DEN1, D(1)), NEAR, 'DP', D(1)), ('add', (SN, D(1)), NEAR, 'I', (0xE000000000000000, 0x7FFF)),
 ('add', (QN, D(1)), NEAR, '', QN), ('add', (D(1), SN), NEAR, 'I', (0xE000000000000000, 0x7FFF)), ('add', (SN, QN), NEAR, 'I', QN),
 ('add', ((2**62, 5), D(1)), NEAR, 'I', (3 << 62, 0xFFFF)), ('add', ((0, 0x7FFF), D(1)), NEAR, 'I', (3 << 62, 0xFFFF)),
 ('mul', (P2(8192), P2(8192)), NEAR, 'OPc', INF), ('mul', (P2(8192), P2(8192)), ZERO, 'OP', MAXN), ('mul', (MINN, D(0.5)), NEAR, '', (2**62, 0)),
 ('mul', (MINN, (0xAAAAAAAAAAAAAAAA, 16383 - 2)), ZERO, 'UP', (0x2AAAAAAAAAAAAAAA, 0)), ('mul', (DEN1, D(1)), NEAR, 'D', DEN1),
 ('mul', (D(0.0), INF), NEAR, 'I', (3 << 62, 0xFFFF)), ('mul', (D(-2), D(0.0)), NEAR, '', D(-0.0)), ('mul', (D(3), D(5)), NEAR, '', D(15)),
 ('mul', (P2(-8223), P2(-8223)), NEAR, 'UP', (0, 0)), ('mul', (P2(-8223), P2(-8223)), UP, 'UPc', (1, 0)), ('mul', (P2(-8200), P2(-8200)), NEAR, '', (2**45, 0)),
 ('div', (D(1), D(0.0)), NEAR, 'Z', INF), ('div', (D(-1), D(0.0)), NEAR, 'Z', NINF), ('div', (D(0.0), D(0.0)), NEAR, 'I', (3 << 62, 0xFFFF)),
 ('div', (INF, INF), NEAR, 'I', (3 << 62, 0xFFFF)), ('div', (INF, D(0.0)), NEAR, '', INF), ('div', (D(1), D(3)), ZERO, 'P', (0xAAAAAAAAAAAAAAAA, 16381)),
 ('div', (D(1), D(3)), UP, 'Pc', (0xAAAAAAAAAAAAAAAB, 16381)), ('div', (D(6), D(3)), NEAR, '', D(2)), ('div', (D(1), INF), NEAR, '', D(0.0)),
 ('div', (DEN1, D(1)), NEAR, 'D', DEN1), ('div', (MAXN, D(0.5)), NEAR, 'OPc', INF),
 ('sqrt', (D(-1),), NEAR, 'I', (3 << 62, 0xFFFF)), ('sqrt', (D(2),), ZERO, 'P', (0xB504F333F9DE6484, 16383)), ('sqrt', (D(4),), NEAR, '', D(2)),
 ('sqrt', (D(-0.0),), NEAR, '', D(-0.0)), ('sqrt', (INF,), NEAR, '', INF), ('sqrt', (NINF,), NEAR, 'I', (3 << 62, 0xFFFF)), ('sqrt', (DEN1,), NEAR, 'DP', None),
 ('sqrt', (SN,), NEAR, 'I', (0xE000000000000000, 0x7FFF)),
 ('rndint', (D(2.5),), NEAR, 'P', D(2)), ('rndint', (D(3.5),), NEAR, 'Pc', D(4)), ('rndint', (D(0.3),), UP, 'Pc', D(1)), ('rndint', (D(-0.2),), DOWN, 'Pc', D(-1)),
 ('rndint', (D(-0.2),), NEAR, 'P', D(-0.0)), ('rndint', (P2(70),), NEAR, '', P2(70)), ('rndint', (D(5.0),), NEAR, '', D(5)),
 ('from_f32', (0x7F800001,), 0, 'I', (3 << 62 | 1 << 40, 0x7FFF)), ('from_f32', (1,), 0, 'D', (2**63, 16383 - 149)), ('from_f32', (0x3F800000,), 0, '', D(1)),
 ('from_f64', (1,), 0, 'D', (2**63, 16383 - 1074)), ('from_f64', (0xFFF8000000000000,), 0, '', (0xC000000000000000, 0xFFFF)),
 ('to_f32', (D(1e-50),), NEAR, 'UP', 0), ('to_f32', (D(1e39),), NEAR, 'OPc', 0x7F800000), ('to_f32', (D(1e39),), ZERO, 'OP', 0x7F7FFFFF),
 ('to_f32', (D(0.1),), ZERO, 'P', 0x3DCCCCCC), ('to_f32', (D(0.5),), NEAR, '', 0x3F000000), ('to_f64', (P2(1030),), NEAR, 'OPc', 0x7FF0000000000000),
 ('to_f64', (D(0.1),), NEAR, '', 0x3FB999999999999A), ('to_f64', (P2(-1074),), NEAR, '', 1), ('to_f64', (P2(-1080),), UP, 'UPc', 1),
 ('to_i64', (D(2.5), 0, 64), 0, 'P', 2), ('to_i64', (D(3.5), 0, 32), 0, 'Pc', 4), ('to_i64', (D(-0.5), 1, 16), 0, 'Pc', -1), ('to_i64', (D(40000.0), 0, 16), 0, 'I', -32768),
 ('to_i64', (D(-32768.0), 0, 16), 0, '', -32768), ('to_i64', (D(32767.5), 0, 16), 0, 'I', -32768), ('to_i64', (QN, 0, 32), 0, 'I', -2**31), ('to_i64', (INF, 3, 64), 0, 'I', -2**63),
 ('to_i64', (P2(63), 0, 64), 0, 'I', -2**63), ('to_i64', (P2(63, 1), 0, 64), 0, '', -2**63), ('to_i64', (D(-2.5), 3, 32), 0, 'P', -2),
 ('cmp', (D(1), D(2), 0), 0, '', 0x100), ('cmp', (D(2), D(1), 0), 0, '', 0), ('cmp', (D(1), D(1), 0), 0, '', 0x4000), ('cmp', (D(0.0), D(-0.0), 0), 0, '', 0x4000),
 ('cmp', (QN, D(1), 0), 0, 'I', 0x4500), ('cmp', (QN, D(1), 1), 0, '', 0x4500), ('cmp', (SN, D(1), 1), 0, 'I', 0x4500), ('cmp', (D(-1), D(-2), 0), 0, '', 0),
 ('xam', (D(0.0), 0), 0, '', 0x4000), ('xam', (D(-0.0), 0), 0, '', 0x4200), ('xam', (DEN1, 0), 0, '', 0x4400), ('xam', (D(-1), 0), 0, '', 0x600),
 ('xam', (INF, 0), 0, '', 0x500), ('xam', (QN, 0), 0, '', 0x100), ('xam', (D(1), 1), 0, '', 0x4100),
]
def vectors(lib):
    class F80(ctypes.Structure): _fields_ = [("m", ctypes.c_uint64), ("se", ctypes.c_uint16)]
    f = lambda t: F80(*t)
    bad = n = 0; fsw = ctypes.c_uint32()
    for op, args, fcw, flags, want in VECT:
        fsw.value = 0; fn = {'add': lib.fnvl_x_add, 'sub': lib.fnvl_x_sub, 'mul': lib.fnvl_x_mul, 'div': lib.fnvl_x_div, 'sqrt': lib.fnvl_x_sqrt, 'rndint': lib.fnvl_x_rndint,
                                'from_f32': lib.fnvl_x_from_f32, 'from_f64': lib.fnvl_x_from_f64, 'from_i64': lib.fnvl_x_from_i64, 'to_f32': lib.fnvl_x_to_f32,
                                'to_f64': lib.fnvl_x_to_f64, 'to_i64': lib.fnvl_x_to_i64, 'cmp': lib.fnvl_x_cmp, 'xam': lib.fnvl_x_xam}[op]
        fn.restype = {'to_f32': ctypes.c_uint32, 'to_f64': ctypes.c_uint64, 'to_i64': ctypes.c_longlong, 'cmp': ctypes.c_uint32, 'xam': ctypes.c_uint32}.get(op, F80)
        a = [f(x) if isinstance(x, tuple) else x for x in args]
        fn.argtypes = [F80 if isinstance(x, tuple) else ctypes.c_longlong if op == 'from_i64' else ctypes.c_uint64 if op == 'from_f64' else ctypes.c_uint32 for x in args] \
            + ([] if op in ('cmp', 'xam', 'to_i64', 'from_f32', 'from_f64') else [ctypes.c_uint32]) + ([] if op == 'xam' else [ctypes.POINTER(ctypes.c_uint32)])
        if op in ('cmp',): r = fn(a[0], a[1], a[2], ctypes.byref(fsw))
        elif op == 'xam': r = fn(a[0], a[1])
        elif op == 'to_i64': r = fn(a[0], a[1], a[2], ctypes.byref(fsw))
        elif op in ('from_f32', 'from_f64'): r = fn(a[0], ctypes.byref(fsw))
        else: r = fn(*a, fcw, ctypes.byref(fsw))
        got = (r.m, r.se) if hasattr(r, 'se') else r
        exp = sum(F[c] for c in flags)
        n += 1; okf = op == 'xam' or (fsw.value & 0x23F) == exp
        if op == 'cmp': okf = (fsw.value & 0x23F) == exp
        if not okf or (want is not None and got != want):
            bad += 1; print(f"VEC FAIL {op} {args} fcw={fcw:#x} fsw={fsw.value & 0x23F:#x} want={exp:#x} got={got} want={want}")
    print(f"vectors {n} equal {n - bad} diff {bad}"); return bad

# ---------------------------------------------------------------- main
def eqmask(op, want, got, full):
    """Result bytes equal and status bits equal (full: all flags + C bits except TOP; else the unicorn-reliable C bits)."""
    _, _, _, kind, w, smask = OPS[op]
    if w == 0: eq = (want['r'] == got['r']) & (want['rse'] == got['rse'])
    elif w > 0: msk = U(2 ** (8 * w) - 1) if w < 8 else ~U(0); eq = (want['r'] & msk) == (got['r'] & msk)
    else: eq = np.ones(len(want), bool)
    if op == 'xtract': eq &= (want['r2'] == got['r2']) & (want['r2se'] == got['r2se'])
    m = 0xC7FF if full else smask
    if m:
        eq &= (want['fsw'] & m) == (got['fsw'] & m)
        if op == 'fxam': eq &= (want['fsw2'] & m) == (got['fsw2'] & m)
    return eq

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--n', type=int, default=20000); ap.add_argument('--op'); ap.add_argument('--work', default='/home/user/l3work/x87test')
    ap.add_argument('--seed', type=int, default=1); ap.add_argument('--disasm', action='store_true')
    ap.add_argument('--oracle', choices=['native', 'unicorn'], default='native'); a = ap.parse_args()
    if a.disasm: return disasm()
    lib, nat = build(a.work.rstrip('/') + '/'); lib.run.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    print(f"oracle {a.oracle} seed {a.seed} n {a.n}")
    tot = [0, 0, 0]; bad = vectors(lib); full = a.oracle == 'native'
    for oi, op in enumerate(NAMES):
        if a.op and a.op != op: continue
        rng = np.random.default_rng([a.seed, oi]); N = a.n * len(FCWS)
        fcw = np.tile(np.array(FCWS, np.uint32), a.n)
        am, ase, bm, bse = gen(op, rng, N, fcw)
        rec = np.zeros(N, IN_T); rec['fcw'], rec['am'], rec['ase'], rec['bm'], rec['bse'] = fcw, am, ase, bm, bse
        got = np.zeros(N, OUT_T); lib.run(oi, N, rec.ctypes.data, got.ctypes.data)
        hw = native(op, rec, nat)
        if full: want, extra = hw, ''
        else: want = oracle(op, rec); extra = f" unicorn_vs_hw {int((~eqmask(op, want, hw, False)).sum())}"
        ne = ~eqmask(op, want, got, full); nd = int(ne.sum())
        print(f"{op} {N} equal {N - nd} diff {nd}{extra}")
        tot[0] += N; tot[1] += N - nd; tot[2] += nd
        for i in np.nonzero(ne)[0][:5]:
            print(f"  {op} a={am[i]:016x}:{ase[i]:04x} b={bm[i]:016x}:{bse[i]:04x} fcw={fcw[i]:#x} got={got['r'][i]:016x}:{got['rse'][i]:04x}/{got['fsw'][i]:04x}/{got['fsw2'][i]:04x} want={want['r'][i]:016x}:{want['rse'][i]:04x}/{want['fsw'][i]:04x}/{want['fsw2'][i]:04x}")
    print(f"total {tot[0]} equal {tot[1]} diff {tot[2] + bad}")
    return 1 if (tot[2] if full else 0) + bad else 0

if __name__ == '__main__': sys.exit(main())
