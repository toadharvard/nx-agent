#!/usr/bin/env python3
"""Per-instruction differential test: lifted C (AArch64 ILP32 under Unicorn) vs Unicorn x86, over the instruction forms
the lifted pack contains (--pack / FNVNX_PACK: forms.tsv and encs.tsv of tools/liftgen.py, the lifter's own decoding).  Reports lifter bugs, fixes nothing.
    instest.py [--n 8] [--k 4] [--jobs 3] [--only mn,mn] [--limit-forms N] [--work DIR] [--lifter DIR]
Inputs FNVNX_EXE / FNVNX_PACK.  Everything exe-derived goes to --work (default /home/user/l3work/instest), never the repo.
Outcomes per case: equal, diff, unlifted, nocompile, fault (the ORACLE cannot run it: #DE, #GP, undecodable, x87 stack fault
which no correct program executes, or the case collides with the harness region), x87-prec (W13, R8: an x87 arithmetic result that is
not comparable bit for bit: oracle PC=24/64, or the result is not a normal double, or an operand is not exactly a double, or two NaNs picked differently).
x87 model (W13): every ST is a carrier (kind, payload) on the lifted side and an 80-bit register on the oracle side; both are compared through the carrier's exact
80-bit image.  Exact classes (loads, stores, copies, fist, compares, fxam, fchs/fabs, control/status) must be bit-equal for every control word; arithmetic
only when the oracle ran PC=53 and its result is a normal double.  Even-numbered cases of an x87 encoding use the dynamic stack and random depth, odd-numbered ones the
static stack (entry depth XD, a state of that depth).  fnstsw: exception bits, C1, ES, B are not compared.  Compared since W6: mm0-7 (struct cpu mm[8]) and the MXCSR control bits (RC, FTZ, DAZ, masks; the exception status bits 0-5 are not modelled, so
not compared).  Not compared: C1/exception flags.  SSE/MMX cases run with a random MXCSR (FPCR mirrors RC on the ARM side, as ldmxcsr does).
A one-instruction function makes in-function branch targets (jecxz) fall outside the label set: that shows as nocompile."""
import argparse, functools, hashlib, json, math, multiprocessing as mp, os, random, re, struct, subprocess, sys, time
import numpy as np
from unicorn import *
from unicorn.x86_const import *
from unicorn.arm64_const import *
from elftools.elf.elffile import ELFFile
HERE = os.path.dirname(os.path.abspath(__file__))
# a one-instruction test has no callee: a call/tail to another entry is an event of the test harness (fnvl_call), set here and nowhere in the prelude
STUBX = 'extern "C" void fnvl_call(struct cpu*, u32 target);\n#undef CALLX\n#define CALLX(h) fnvl_call(c,0x##h)\n'
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import liftgen   # tools/liftgen.py PACK_CXX_FLAGS: the ONE definition of the lifted-code flags (the pack recipe and every test compile with it)
FLAGS = ['-c'] + liftgen.PACK_CXX_FLAGS + ['-DFNVL_FLAGS_IO']      # + the test's I/O hook and -c: they compile no product code differently
HARN, HARN_END, CPU, ASTACK, RETPAD = 0xE0000000, 0xE2000000, 0xE1000000, 0xE1F00000, 0xE1FF0000
HX = 0xE1E00000                  # x86 oracle harness page (inside the guarded range: a test access to it counts as a collision)
FSB = 0x7FFDF000                 # fs base on both sides
NOT_EQ = ('diff', 'unlifted', 'nocompile', 'x87-prec')
OUTS = ('equal', 'diff', 'unlifted', 'nocompile', 'fault', 'x87-prec')
XD = 3                           # entry depth of the static-stack variant
G = {}
LOWOK = False       # instest_extra: 16-bit addressing (0x67) reaches the low 64 KiB, which the census run treats as a NULL-page collision
R8 = {'al', 'cl', 'dl', 'bl', 'ah', 'ch', 'dh', 'bh'}; R16 = {'ax', 'cx', 'dx', 'bx', 'sp', 'bp', 'si', 'di'}
R32 = ['eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi']; SEGS = {'cs', 'ds', 'es', 'ss', 'fs', 'gs'}
GREG = [UC_ARM64_REG_X19 + i for i in range(8)]
X86R = [UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_EDX, UC_X86_REG_EBX, UC_X86_REG_ESP, UC_X86_REG_EBP, UC_X86_REG_ESI, UC_X86_REG_EDI]
PFX = {0x66, 0x67, 0xf0, 0xf2, 0xf3, 0x2e, 0x36, 0x3e, 0x26, 0x64, 0x65}
STRING = {a + b for a in ('movs', 'stos', 'lods', 'scas', 'cmps', 'ins', 'outs') for b in 'bwd'}
def form_key(ins, X):
    raw = bytes(ins.bytes); i = 0
    while i < len(raw) and raw[i] in PFX: i += 1
    opc = raw[i:i + (3 if raw[i] == 0x0f and raw[i + 1] in (0x38, 0x3a) else 2 if raw[i] == 0x0f else 1)]
    ks = []
    for o in ins.operands:
        if o.type == X.X86_OP_REG:
            n = ins.reg_name(o.reg)
            ks.append('xmm' if n.startswith('xmm') else 'st' if n.startswith('st') else 'mm' if n.startswith('mm') else 'seg' if n in SEGS
                      else 'r8' if n in R8 else 'r16' if n in R16 else 'r32' if n in R32 else n)
        elif o.type == X.X86_OP_IMM: ks.append('imm')
        else: ks.append('m%d' % (o.size * 8) + {X.X86_REG_FS: ':fs', X.X86_REG_GS: ':gs'}.get(o.mem.segment, ''))
    return '%s|%s|%s' % (ins.mnemonic, ','.join(ks), opc.hex()), opc
def load_forms(a, rows):
    """The universe is the lifter's own: the forms and encodings of the pack (tools/liftgen.py forms.tsv, encs.tsv), i.e. the instructions of the
    lifted entries as the lifter decoded them. No second decoder decides what is an instruction."""
    tsv = lambda n: [l.rstrip('\n').split('\t') for l in open(os.path.join(a.pack, n))]
    forms = {f: [int(n), 0, {}] for f, n, _ in tsv('forms.tsv')}
    for f, r, x in tsv('encs.tsv'):
        if len(forms[f][2]) < a.k: forms[f][2][bytes.fromhex(r)] = int(x, 16)
    mnf = {}
    return dict(forms=forms, mnf=mnf, undecodable=0, nfuncs=0)
DP = [0.0, -0.0, 1.0, -1.0, 0.5, 2.0, 3.0, 10.0, 100.0, 255.0, 65535.0, 2.0 ** 31, -2.0 ** 31, 2.0 ** 32, 2.0 ** 52, 2.0 ** 63, -2.0 ** 63, 1e300,
      -1e300, 1e-300, 5e-324, 2.2250738585072014e-308, math.inf, -math.inf, 1.5, 0.1, 3.14159, 1234.5678, 1e10, -7.25]
FP = [0.0, -0.0, 1.0, -1.0, 0.5, 2.0, 3.0, 100.0, 1e30, 1e-30, 1e-45, 3.4e38, math.inf, -math.inf, 7.5, -0.25]
def dbits(d): return struct.unpack('<Q', struct.pack('<d', d))[0]
NANS = [0x7ff8000000000000, 0xfff8000000000000, 0x7ff0000000000001, 0x7ff8000000001234]
X64 = [0x7ff0000000000001, 0xfff0000000000001, 0x7ff4000000000000, 0x7ff8000000000001, 0xfff8000000000001, 0x7fffffffffffffff, 0xffffffffffffffff, 2 ** 53 + 1,
       2 ** 53 + 3, 2 ** 63 - 1, 2 ** 63, 2 ** 63 + 1, 2 ** 64 - 1025, 2 ** 62 + 1, 1, 0x000fffffffffffff, 0x8000000000000001, 0x0010000000000001, 0xc3e0000000000000,
       0x7f8000017f800001, 0xff800005ff800005, 0x7fa000007fa00000, 0x7fc000007fc00000, 0xffc00001ffc00001]
P64 = [dbits(d) for d in DP] + NANS + [struct.unpack('<Q', struct.pack('<ff', random.Random(i).choice(FP + [math.nan]), random.Random(i + 99).choice(FP)))[0] for i in range(40)]
@functools.lru_cache(maxsize=4096)
def imgpage(p):
    img = G['img']; buf = None
    for n, va, vs, ro, rs in img.secs:
        if va < p + 4096 and p < va + ((vs + 4095) & ~4095):
            buf = bytearray(4096) if buf is None else buf; lo, hi = max(va, p), min(va + min(vs, rs), p + 4096)
            if hi > lo: buf[lo - p:hi - p] = img.d[ro + lo - va:ro + hi - va]
    if buf is None:                                   # no page of the exe: the encodings of the other images of the pack, at their addresses
        for a, r in G.get('other', {}).items():
            if a & ~0xfff == p: buf = bytearray(4096) if buf is None else buf; buf[a - p:a - p + len(r)] = r[:4096 - (a - p)]
    return bytes(buf) if buf is not None else None
@functools.lru_cache(maxsize=4096)
def genpage(seed, mode, p):
    b = hashlib.shake_128(b'%d:%d' % (seed, p)).digest(8192)
    if mode == 0: return b[:4096]
    if mode == 1:                                       # small integers and pointers (the PoC generator)
        w = np.frombuffer(b[:4096], '<u4').astype(np.uint64); s, v = w & 0xff, w >> 8
        return np.where(s < 150, v & 15, np.where(s < 200, 0, np.where(s < 230, 0x30000000 + ((v & 0xffff) << 4), w))).astype('<u4').tobytes()
    a = np.frombuffer(b, np.uint8)
    if mode == 3:                                       # x87 edge values: SNaN/QNaN patterns, integers beyond 2^53, denormals, 80-bit mantissas and exponents
        t = np.frombuffer(b[4096:8192], '<u8'); ex = np.array([0x3fff, 0x4000, 0x7fff, 0xffff, 0xbfff, 0, 0x8000, 0x0001, 0x7ffe, 0x403e, 0x403d, 0xc03e], dtype='<u8')
        pool = np.array(P64 + X64, dtype='<u8'); k = a[:512]
        return np.where(k < 70, pool[a[512:1024].astype(np.int64) % len(pool)], np.where(k < 120, ex[a[512:1024] % len(ex)], np.where(k < 190, t[:512] | np.uint64(1 << 63), t[:512]))).astype('<u8').tobytes()
    pool = np.array(P64, dtype='<u8')           # float-like: doubles, float pairs, specials
    return np.where(a[512:1024] < 40, np.frombuffer(b[4096:8192], '<u8'), pool[a[:512].astype(np.int64) % len(P64)]).astype('<u8').tobytes()
class World:
    def init_world(s, uc, guard):
        s.uc, s.guard, s.dyn, s.writes, s.collide, s.wk, s.bad = uc, guard, set(), set(), False, (0, 0), ''
        uc.hook_add(UC_HOOK_MEM_READ_UNMAPPED | UC_HOOK_MEM_WRITE_UNMAPPED | UC_HOOK_MEM_FETCH_UNMAPPED, s.unm)
    def ensure(s, addr, size, guard):
        for p in range(addr & ~0xfff, (addr + max(size, 1) - 1) + 1, 0x1000):
            if p in s.dyn: continue
            if guard and HARN <= p < HARN_END: s.collide = True; return False
            s.uc.mem_map(p, 0x1000); s.dyn.add(p); s.uc.mem_write(p, imgpage(p) or genpage(s.wk[0], s.wk[1], p))
        return True
    def unm(s, uc, access, addr, size, value, ud):
        s.bad = '%x+%d' % (addr, size)
        if s.guard and access != UC_MEM_FETCH_UNMAPPED and ((addr < 0x10000 and not LOWOK) or addr + size > 0xffff0000): s.collide = True; return False
        if addr + size > 0x100000000: return False   # NULL page / top 64K: no Win32 code touches them, 32-bit address wrap is out of scope
        return s.ensure(addr, size, s.guard and access != UC_MEM_FETCH_UNMAPPED)
    def reset(s, wk):
        for p in s.dyn: s.uc.mem_unmap(p, 0x1000)
        s.dyn.clear(); s.writes = set(); s.collide = False; s.wk = wk
    def written(s):
        m = {}
        for a, n in s.writes:
            for k, v in enumerate(bytes(s.uc.mem_read(a, n))):
                x = (a + k) & 0xffffffff
                if v != (imgpage(x & ~0xfff) or genpage(s.wk[0], s.wk[1], x & ~0xfff))[x & 0xfff]: m[x] = v      # only bytes whose value changed: a store of the old value is unobservable
        return m
def f2x80(b):
    sg, e, f = b >> 63, (b >> 52) & 0x7ff, b & (2 ** 52 - 1)
    if e == 0x7ff: return ((1 << 63) | (f << 11), 0x7fff | sg << 15)
    if e: return ((1 << 63) | (f << 11), e - 1023 + 16383 | sg << 15)
    if not f: return (0, sg << 15)
    n = f.bit_length(); return (f << (64 - n), 16383 + n - 1 - 1074 | sg << 15)
def x80d(m, e):
    """80-bit -> bits of the double when exactly representable, else None"""
    sg, ex = e >> 15, e & 0x7fff
    if ex == 0x7fff:
        if not m >> 63: return None
        f = m & (2 ** 63 - 1)
        if f == 0: return sg << 63 | 0x7ff << 52
        return (sg << 63 | 0x7ff << 52 | f >> 11) if not f & 0x7ff else None
    if m == 0: return sg << 63
    tz = (m & -m).bit_length() - 1; top = ex - 16383 - 63 + m.bit_length() - 1 if ex else -16382 - 63 + m.bit_length() - 1
    if m.bit_length() - tz > 53 or top > 1023 or (ex or 1) - 16383 - 63 + tz < -1074: return None
    return dbits(math.ldexp(float(m >> tz), (ex or 1) - 16383 - 63 + tz)) | sg << 63
def f32x80(b):
    sg, e, f = b >> 31, (b >> 23) & 0xff, b & 0x7fffff
    if e == 0xff: return ((1 << 63) | (f << 40), 0x7fff | sg << 15)
    if e: return ((1 << 63) | (f << 40), e - 127 + 16383 | sg << 15)
    if not f: return (0, sg << 15)
    n = f.bit_length(); return (f << (64 - n), 16383 - 126 - (23 - n + 1) + 0 | sg << 15) if False else (f << (64 - n), 16383 - 149 + n - 1 | sg << 15)
def i80(x):
    if x == 0: return (0, 0)
    sg, a = (1 if x < 0 else 0), abs(x); n = a.bit_length(); return (a << (64 - n), 16383 + n - 1 | sg << 15)
def img80(k, v, e):
    """the exact 80-bit image (significand, sign|exponent) of a carrier (k, v, e): what the x87 would hold"""
    if k == 1: return f2x80(v)
    if k == 2: return f32x80(v)
    if k in (3, 4, 5): return i80(v - (1 << 64) if v >> 63 else v)
    return (v, e)
QN = lambda b, w: b | (1 << (51 if w == 64 else 22)) if (b >> (52 if w == 64 else 23)) & (0x7ff if w == 64 else 0xff) == (0x7ff if w == 64 else 0xff) and b & ((1 << (52 if w == 64 else 23)) - 1) else b
def x87_range(E):
    """(least, most) stack depth at which the instruction raises no x87 stack fault: the QEMU oracle does not model overflow/underflow (the lifted dynamic
    stack does: see test/x87dyn.py for its checks), so the generator stays inside"""
    ins = E['ins']; b0, mr = ins.opcode[0], ins.modrm; g = mr >> 3 & 7; i = mr & 7
    if mr < 0xc0:
        if (b0, g) in ((0xd9, 0), (0xdb, 0), (0xdb, 5), (0xdd, 0), (0xdf, 0), (0xdf, 5)): return 0, 7
        if (b0, g) in ((0xd9, 5), (0xd9, 7), (0xdd, 7)): return 0, 8
        return 1, 8
    if b0 == 0xd9: return (i + 1, 7) if mr < 0xc8 else (i + 1, 8) if mr < 0xd0 else (0, 8) if mr in (0xd0, 0xe5) else (0, 7) if 0xe8 <= mr <= 0xee else (1, 8)
    if mr in (0xd9, 0xe9) and b0 in (0xde, 0xda): return 2, 8
    if (b0, mr) in ((0xdb, 0xe2), (0xdb, 0xe3), (0xdf, 0xe0)) or b0 == 0xdd and mr < 0xd0 or b0 == 0xdf and mr < 0xc8: return 0, 8
    return max(1, i + 1), 8
def gen_x87(r, d, top):
    """8 physical carriers (k, v, e); k == 0 for the empty ones; slot i from TOP is valid for i < d. Kinds: F64 F32 I16 I32 I64 M80 with edge values."""
    def m80():
        t = r.random()
        if t < .35: return f2x80(r.choice(X64 + P64))
        if t < .5: return (r.getrandbits(63) | 1 << 63, r.choice((0x3fff, 0x4000, 0x403d, 0x403e, 0x4040, 0xc03e, 0xbfff, 0x3ffe, 0x7ffe, 0x0001, 0x3c00, 0x3bff, 0x43fe, 0xc3fe)))
        if t < .6: return (r.getrandbits(63), 0 | r.getrandbits(1) << 15)                                   # denormal (or zero), integer bit clear
        if t < .7: return r.choice(((1 << 63, 0x7fff), (1 << 63, 0xffff), (0xc000000000000000 | r.getrandbits(8), 0x7fff), (0xa000000000000000 | r.getrandbits(8), 0xffff), (0, 0), (0, 0x8000)))
        return (r.getrandbits(64) | 1 << 63, r.randrange(1, 0x7fff) | r.getrandbits(1) << 15)
    out = []
    for p in range(8):
        if (p - top) & 7 >= d: out.append((0, 0, 0)); continue
        t = r.random()
        if t < .3: out.append((1, QN(r.choice(P64 + X64 + [dbits(r.uniform(-1e6, 1e6)), r.getrandbits(64)]), 64), 0))
        elif t < .45: out.append((2, QN(r.choice([struct.unpack('<I', struct.pack('<f', x))[0] for x in FP] + [0x7f800001, 0xff800001, 0x7fc00000, 0xffc00001, 0x7fa00000, r.getrandbits(32), 0x00000001, 0x007fffff]), 32), 0))
        elif t < .55: out.append((3, r.choice((0, 1, -1, 0x7fff, -0x8000, r.randrange(-300, 300))) & (2 ** 64 - 1), 0))
        elif t < .65: out.append((4, r.choice((0, 1, -1, 0x7fffffff, -0x80000000, r.randrange(-10 ** 6, 10 ** 6), r.randrange(-2 ** 31, 2 ** 31))) & (2 ** 64 - 1), 0))
        elif t < .8: out.append((5, r.choice((0, 2 ** 53 + 1, -(2 ** 53) - 3, 2 ** 63 - 1, -2 ** 63, 2 ** 62 + 12345, r.randrange(-2 ** 63, 2 ** 63), r.randrange(-1000, 1000), 2 ** 53 + 2 ** 10 + 1)) & (2 ** 64 - 1), 0))
        else: out.append((6,) + m80())
    return out
class Oracle(World):
    def __init__(s):
        uc = Uc(UC_ARCH_X86, UC_MODE_32); s.init_world(uc, True); uc.mem_map(HX, 0x1000); s.mode = 'pro'
        d = lambda base, lim, acc, fl: struct.pack('<HHBBBB', lim & 0xffff, base & 0xffff, base >> 16 & 0xff, acc, lim >> 16 | fl << 4, base >> 24)
        uc.mem_write(HX + 0x800, b'\0' * 8 + d(FSB, 0xfffff, 0x92, 0xc) + d(0, 0xfffff, 0x92, 0xc)); uc.reg_write(UC_X86_REG_GDTR, (0, HX + 0x800, 24, 0))
        uc.mem_write(HX, b'\xdd\x25' + struct.pack('<I', HX + 0x100) + b'\xdd\x35' + struct.pack('<I', HX + 0x200))   # frstor / fnsave
        uc.hook_add(UC_HOOK_MEM_WRITE, s.onw); uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, s.hx, None, HX, HX + 0xfff)
        uc.hook_add(UC_HOOK_CODE, s.code)
    def onw(s, uc, access, addr, size, value, ud):
        if s.mode == 'test': s.writes.add((addr & 0xffffffff, size))
    def hx(s, *a):
        if s.mode == 'test': s.collide = True
    def code(s, uc, a, size, ud):
        if s.mode != 'test': return
        s.n += 1
        if s.n > 1 and not (s.rep and a == s.addr): s.nxt = a; uc.emu_stop()
    def run(s, E, st):
        uc = s.uc; s.reset(st['wk']); s.ensure(E['addr'], len(E['raw']), False)
        s.addr, s.rep, s.n, s.nxt = E['addr'], E['rep'], 0, None
        try:
            for sg, sel in ((UC_X86_REG_SS, 16), (UC_X86_REG_DS, 16), (UC_X86_REG_ES, 16), (UC_X86_REG_FS, 8)): uc.reg_write(sg, sel)   # flat segments (loading the GDT broke SS.B)
            for k, v in zip(X86R, st['regs']): uc.reg_write(k, v)
            uc.reg_write(UC_X86_REG_EFLAGS, 0x202 | st['fl'] | st['eflx'])
            if E['base'] in ('popf', 'popfd'): s.ensure(st['regs'][4], 4, False); uc.mem_write(st['regs'][4], struct.pack('<I', st['stk']))     # W14: the popped EFLAGS image (no TF/RF/VM/VIF/VIP)
            if E['mmx']:      # Unicorn cannot access MM0-7 directly: they are the x87 registers' mantissas (TOP=0, all tags valid, as after any MMX insn)
                uc.mem_write(HX + 0x100, struct.pack('<7I', 0x37f, 0, 0, 0, 0, 0, 0) + b''.join(struct.pack('<QH', v, e) for v, e in zip(st['mm'], st['mme'])))
                s.mode = 'pro'; uc.emu_start(HX, HX + 6)
            elif E['x87']:
                top = st['ftop']; ftw = sum((0 if st['valid'] >> p & 1 else 3) << 2 * p for p in range(8))
                regs = b''.join(struct.pack('<QH', *img80(*st['fx'][(top + i) & 7])) for i in range(8))
                uc.mem_write(HX + 0x100, struct.pack('<7I', st['fcw'], top << 11 | st['fc'], ftw, 0, 0, 0, 0) + regs)
                s.mode = 'pro'; uc.emu_start(HX, HX + 6)
            for i, x in enumerate(st['xmm']): uc.reg_write(UC_X86_REG_XMM0 + i, x)
            uc.reg_write(UC_X86_REG_MXCSR, st['mxcsr'])
            s.mode = 'test'; uc.emu_start(E['addr'], 0xffffffffffffffff, timeout=2000000); s.mode = 'epi'
            if s.nxt is None or s.collide: return dict(fault='collide' if s.collide else 'no next instruction', collide=s.collide)
            o = dict(regs=[uc.reg_read(k) for k in X86R], eip=s.nxt, fl=uc.reg_read(UC_X86_REG_EFLAGS) & 0xcd5, eflx=uc.reg_read(UC_X86_REG_EFLAGS) & 0x240000, mem=s.written(), fp=None, xmm=None, mm=None,
                     mxcsr=uc.reg_read(UC_X86_REG_MXCSR), wr=sorted(s.writes))
            if E['sse']: o['xmm'] = [uc.reg_read(UC_X86_REG_XMM0 + i) for i in range(8)]
            if E['mmx']:
                uc.emu_start(HX + 6, HX + 12); b = bytes(uc.mem_read(HX + 0x200, 108)); o['mm'] = [struct.unpack('<Q', b[28 + 10 * i:36 + 10 * i])[0] for i in range(8)]
                o['mme'] = [struct.unpack('<H', b[36 + 10 * i:38 + 10 * i])[0] for i in range(8)]; o['mme'] = [0xffff if i == mm_written(E) else st['mme'][i] for i in range(8)]      # SDM Vol 1 9.2: a WRITE of MMi sets bits 64-79 of Ri to all ones, nothing else changes them. Unicorn (QEMU) keeps the old exponent on an MMX write, so the expectation is the SDM's, not the oracle's
                fsw, ftw = struct.unpack('<2I', b[4:12]); o['mtop'] = fsw >> 11 & 7; o['mvalid'] = sum(1 << p for p in range(8) if ftw >> 2 * p & 3 != 3)      # MMX aliasing: the whole x87 image, not only the significands
            elif E['x87']:
                uc.emu_start(HX + 6, HX + 12); b = bytes(uc.mem_read(HX + 0x200, 108)); fcw, fsw, ftw = struct.unpack('<3I', b[:12]); top = fsw >> 11 & 7
                if fsw & 0x40: return dict(fault='x87 stack fault')
                o['fp'] = dict(top=top, fcw=fcw, cc=fsw & 0x4500, valid=sum(1 << p for p in range(8) if ftw >> 2 * p & 3 != 3),
                               st={(top + i) & 7: struct.unpack('<QH', b[28 + 10 * i:38 + 10 * i]) for i in range(8)})
            return o
        except UcError as e: return dict(fault='%s' % e, collide=s.collide)
        finally: s.mode = 'epi'
def mm_written(E):
    """index of the MM register the instruction writes (its first operand when that is an mm register and the form is not a store-like read of it), else None"""
    from capstone import x86 as X
    ops = E['ins'].operands
    return int(E['ins'].reg_name(ops[0].reg)[2:]) if ops and ops[0].type == X.X86_OP_REG and E['ins'].reg_name(ops[0].reg).startswith('mm') and E['base'] not in ('maskmovq', 'emms') else None
class Arm(World):
    def __init__(s, elf):
        with open(elf, 'rb') as fh:
            e = ELFFile(fh); segs = [(p['p_vaddr'], p.data()) for p in e.iter_segments() if p['p_type'] == 'PT_LOAD']
            s.sym = {x.name: x['st_value'] for x in e.get_section_by_name('.symtab').iter_symbols() if x.name}
        uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM); s.init_world(uc, False); uc.mem_map(HARN, HARN_END - HARN)
        for va, d in segs: uc.mem_write(va, d)
        uc.reg_write(UC_ARM64_REG_CPACR_EL1, 3 << 20)
        s.stubs = {s.sym[k]: k for k in ('fnvl_call', 'fnvl_dispatch', 'fnvl_raise', 'fnvl_raise_av')}
        uc.hook_add(UC_HOOK_CODE, s.stub, None, min(s.stubs), max(s.stubs) + 4)
        uc.hook_add(UC_HOOK_MEM_WRITE, lambda uc, ac, a, n, v, ud: None if HARN <= a < HARN_END else s.writes.add((a & 0xffffffff, n)))
    def stub(s, uc, addr, size, ud):
        k = s.stubs.get(addr)
        if k:     # event (kind, target or fault address, fault code): fnvl_call(c, t) / fnvl_dispatch(t) / fnvl_raise(code, at)
            x0, x1 = uc.reg_read(UC_ARM64_REG_X0) & 0xffffffff, uc.reg_read(UC_ARM64_REG_X1) & 0xffffffff
            s.ev = (k, x1, 0) if k == 'fnvl_call' else (k, x0, 0) if k == 'fnvl_dispatch' else (k, uc.reg_read(UC_ARM64_REG_X2) & 0xffffffff, 99) if k == 'fnvl_raise_av' else (k, x1, x0); uc.emu_stop()
    def run(s, E, st):
        uc = s.uc; s.reset(st['wk']); s.ev = None; r0 = st['regs']
        uc.mem_write(CPU, struct.pack('<12I', *r0, FSB, st['ftop'], st['fcw'], st['fc'])
                     + b''.join(x.to_bytes(16, 'little') for x in st['xmm']) + struct.pack('<II8Q', st['fl'], st['mxcsr'], *st['mm']) + struct.pack('<II', 0, 0)
                     + b''.join(struct.pack('<QII', v, e, k) for k, v, e in st['fx']) + struct.pack('<I', st['eflx']) + b'\0' * 12 + struct.pack('<7I', 0x23, 0x1b, 0x23, 0x23, 0x3b, 0, FSB))     # sel[6] ES CS SS DS FS GS, teb (fnvl_seg_init)
        uc.reg_write(UC_ARM64_REG_FPCR, (0, 2, 1, 3)[(st['fcw'] >> 10 if E['x87'] else st['mxcsr'] >> 13) & 3] << 22)      # what ldmxcsr does on the ARM side (x86 RC 1=down, 2=up)
        for k, v in zip(GREG, r0): uc.reg_write(k, v)      # guest registers live in w19..w26, the cpu pointer in x27
        if E['base'] in ('popf', 'popfd'): s.ensure(r0[4], 4, False); uc.mem_write(r0[4], struct.pack('<I', st['stk']))
        uc.reg_write(UC_ARM64_REG_X27, CPU); uc.reg_write(UC_ARM64_REG_SP, ASTACK); uc.reg_write(UC_ARM64_REG_X30, RETPAD)
        try: uc.emu_start(s.sym['L_%08x' % E['addr']], RETPAD, timeout=5000000, count=200000)
        except UcError as e: return dict(fault='%s at %s' % (e, s.bad))
        pc = uc.reg_read(UC_ARM64_REG_PC)
        if s.ev is None and pc != RETPAD: return dict(fault='budget')
        b = bytes(uc.mem_read(CPU, 460)); v = [uc.reg_read(k) & 0xffffffff for k in GREG]
        if s.ev is None:     # plain return: only `ret` is allowed to; the next EIP is the word it popped
            s.ensure(r0[4], 4, False); eip = struct.unpack('<I', bytes(uc.mem_read(r0[4], 4)))[0] if E['base'] in ('ret', 'retn') else 'returned'
        else: eip = s.ev[1] if s.ev[0] not in ('fnvl_raise', 'fnvl_raise_av') or s.ev[2] == 2 else 'trap%d' % s.ev[2]
        ftop, fcw, fc = struct.unpack_from('<3I', b, 36); fxv = [struct.unpack_from('<QII', b, 256 + 16 * i) for i in range(8)]
        return dict(regs=list(v), eip=eip, fl=struct.unpack_from('<I', b, 176)[0] & 0xcd5, eflx=struct.unpack_from('<I', b, 384)[0] & 0x240000, mem=s.written(), xmm=[int.from_bytes(b[48 + 16 * i:64 + 16 * i], 'little') for i in range(8)],
                    fp=dict(top=ftop & 7, fcw=fcw, cc=fc & 0x4500, valid=sum(1 << i for i in range(8) if fxv[i][2]), st=dict(enumerate(fxv)), wr=sorted(s.writes)),
                    mxcsr=struct.unpack_from('<I', b, 180)[0], mm=[img80(k, v, e)[0] for v, e, k in fxv], mme=[img80(k, v, e)[1] & 0xffff for v, e, k in fxv], mtop=ftop & 7, mvalid=sum(1 << i for i in range(8) if fxv[i][2]))
def gen_state(E, i, att, xd=None):
    r = random.Random('%x:%d:%d' % (E['addr'], i, att)); regs = []
    for _ in range(8):
        t = r.random()
        regs.append(0x30000000 + (r.getrandbits(16) << 4) + (0 if r.random() < .75 else r.getrandbits(4)) if t < .5 else
                    r.choice((0, 1, 2, 3, 4, 5, 7, 8, 15, 16, 0xff, 0xffff, 0x7fffffff, 0x80000000, 0xfffffffe, 0xffffffff, r.getrandbits(4))) if t < .7 else r.getrandbits(32))
    regs[4] = 0x60100000 - 4 * r.randrange(1, 256) + (r.getrandbits(2) if r.random() < .1 else 0)
    if E['rep']: regs[1] = r.randrange(0, 7)
    if E['base'] in ('div', 'idiv') and r.random() < .7:                       # keep most divisions out of #DE: dividend upper half = extension of the lower
        sz = E['ins'].operands[0].size
        if sz == 1: regs[0] &= 0x7f
        else: regs[2] = (regs[2] & ~(2 ** (8 * sz) - 1)) | ((2 ** (8 * sz) - 1) if E['base'] == 'idiv' and regs[0] >> (8 * sz - 1) & 1 else 0)
    def dv():
        t = r.random()
        if t < .35: return r.choice(P64)
        if t < .6: return dbits(float(r.randrange(-1 << 20, 1 << 20)) / r.choice((1, 1, 2, 4)))
        if t < .8: return r.getrandbits(1) << 63 | (1023 + r.randrange(-40, 41)) << 52 | r.getrandbits(52)
        return r.getrandbits(64)
    def xv():
        t = r.random()
        if t < .3: return r.getrandbits(128)
        if t < .6: return int.from_bytes(struct.pack('<4f', *[r.choice(FP + [r.uniform(-1e3, 1e3), math.nan]) for _ in range(4)]), 'little')
        if t < .8: return int.from_bytes(b''.join(struct.pack('<Q', r.choice(P64)) for _ in range(2)), 'little')
        return int.from_bytes(b''.join(struct.pack('<I', r.randrange(0, 40)) for _ in range(4)), 'little')
    top, d = r.randrange(8), r.randrange(9)
    if xd is not None: d = xd
    if E['x87']: lo, hi = x87_range(E); d = min(max(d, lo), hi)
    # W6 state, drawn from its own generator so the other families' seeds are unchanged: NaNs with payloads in some float lanes, mm0-7, MXCSR
    r2 = random.Random('w6:%x:%d:%d' % (E['addr'], i, att)); xm = [xv() for _ in range(8)]
    for k in range(8):
        if r2.random() < .3:
            if r2.random() < .5: xm[k] = xm[k] & ~(0xffffffff << 32 * r2.randrange(4)) | r2.choice((0x7f800001, 0x7fc00000, 0xffc00000, 0xff800005, 0x7fa00001, 0x7fffffff)) << 32 * r2.randrange(4)
            else: xm[k] = xm[k] & ~(2 ** 64 - 1 << 64 * r2.randrange(2)) | r2.choice(NANS + [0xfff0000000000007, 0x7ff4000000000000]) << 64 * r2.randrange(2)
    # Unicorn/QEMU propagates NaNs of SSE arithmetic by the x87 rule (a QNaN beats an SNaN, else the larger significand); Intel SDM table 4-7 (and
    # the hardware) takes the FIRST operand.  The oracle cannot judge two different NaNs in one lane, so such lanes are made identical (register
    # source) or the destination NaNs are dropped (memory source).  fnvl.h implements the SDM rule; see the W6 report.
    b_, ops_ = E['base'], E['ins'].operands
    if len(b_) == 5 and b_[:3] in ('add', 'sub', 'mul', 'div') and b_[3:] in ('ss', 'sd', 'ps', 'pd') and len(ops_) == 2 and ops_[0].type == 1:
        w_ = 32 if b_[3:] in ('ss', 'ps') else 64; e_, m_ = (0xff, (1 << 23) - 1) if w_ == 32 else (0x7ff, (1 << 52) - 1); mk_ = (1 << w_) - 1
        isn = lambda v: (v >> (w_ - 1 - (8 if w_ == 32 else 11))) & e_ == e_ and v & m_
        dn_ = int(E['ins'].reg_name(ops_[0].reg)[3:]); sn_ = int(E['ins'].reg_name(ops_[1].reg)[3:]) if ops_[1].type == 1 else None
        for k in range(128 // w_):
            dq_, sq_ = xm[dn_] >> k * w_ & mk_, (xm[sn_] >> k * w_ & mk_) if sn_ is not None else 0
            if sn_ is None and isn(dq_): xm[dn_] = xm[dn_] & ~(mk_ << k * w_) | (0x3f800000 if w_ == 32 else 0x3ff0000000000000) << k * w_
            elif sn_ is not None and isn(dq_) and isn(sq_): xm[sn_] = xm[sn_] & ~(mk_ << k * w_) | dq_ << k * w_
    r3 = random.Random('w14:%x:%d:%d' % (E['addr'], i, att)); eflx = r3.getrandbits(1) << 18 | r3.getrandbits(1) << 21     # W14: AC/ID state, and the EFLAGS image a popf/popfd pops
    stk = r3.getrandbits(32) & ~(0x100 | 0x10000 | 0x20000 | 0x80000 | 0x100000)
    r2x = random.Random('x87:%x:%d:%d' % (E['addr'], i, att)); fx = gen_x87(r2x, d, top)      # its own generator: the other families' seeds are unchanged
    mm = [r2.getrandbits(64) if r2.random() < .5 else int.from_bytes(bytes(r2.choice((0, 1, 0x7f, 0x80, 0xff, r2.getrandbits(8))) for _ in range(8)), 'little') for _ in range(8)]
    mme = [0xffff if r2.random() < .5 else r2.getrandbits(16) for _ in range(8)]     # exponent|sign of each physical register before an MMX instruction (random: only a WRITE of mm_i sets it to all ones)
    if E['mmx']: top, d, fx = 0, 8, [(6, m_, e_) for m_, e_ in zip(mm, mme)]           # MMX pre-state = the aliased x87 file: TOP 0, all valid, mm_i = significand of R_i (carrier kind M80)
    # RC and DAZ are generated; FTZ is not: Unicorn never flushes a tiny SSE result to zero (checked: mulss 1e-30*1e-10 with MXCSR.FTZ), so it cannot judge it
    mxcsr = 0x1f80 if r2.random() < .5 else 0x1f80 | r2.getrandbits(2) << 13 | r2.getrandbits(1) << 6
    return dict(eflx=eflx, stk=stk, mm=mm, mme=mme, mxcsr=mxcsr, regs=regs, fl=r.getrandbits(32) & 0xcd5, ftop=top, valid=sum(1 << ((top + k) & 7) for k in range(d)), st=[dv() for _ in range(8)], fx=fx,
                fcw=r.choice((0x27f, 0x7f, 0xa7f, 0xe7f, 0x37f)) if not E['x87'] else r2x.choice((0x27f, 0x7f, 0xa7f, 0xe7f, 0x37f, 0x67f, 0x47f, 0x87f, 0xf7f, 0x27f, 0xa7f, 0xe7f, 0x67f)), fc=r.getrandbits(1) << 8 | r.getrandbits(1) << 10 | r.getrandbits(1) << 14,
                xmm=xm, wk=((E['addr'] * 31 + i + 1000003 * att) & 0x7fffffff, 3 if E['x87'] and i % 3 else i % 3))
CF, PF, AF, ZF, SF, OF = 1, 4, 16, 64, 128, 0x800
FLN = [(CF, 'CF'), (PF, 'PF'), (AF, 'AF'), (ZF, 'ZF'), (SF, 'SF'), (1 << 10, 'DF'), (OF, 'OF')]
C0, C2, C3 = 0x100, 0x400, 0x4000
CC_DEF = {m: C0 | C2 | C3 for m in ('fcom', 'fcomp', 'fcompp', 'fucom', 'fucomp', 'fucompp', 'ficom', 'ficomp', 'ftst', 'fxam', 'fprem', 'fprem1', 'fninit', 'fldenv', 'frstor')}
CC_DEF.update({m: C2 for m in ('fsin', 'fcos', 'fsincos', 'fptan')})
# SDM FPU-flags-affected rule: C0/C2/C3 are defined only after the comparisons (FCOM family, FTST, FXAM), FPREM/FPREM1, FNINIT and the loads of a
# saved state; FSIN/FCOS/FSINCOS/FPTAN define C2 only; every other x87 instruction lists "C0, C2, C3 undefined" and is masked (table above).
SIMPLE = {**dict.fromkeys(('mul', 'imul'), SF | ZF | AF | PF),               # MUL/IMUL: SF ZF AF PF undefined
          **dict.fromkeys(('div', 'idiv'), CF | OF | SF | ZF | AF | PF),    # DIV/IDIV: all six arithmetic flags undefined
          **dict.fromkeys(('tzcnt', 'lzcnt', 'bt', 'bts', 'btr', 'btc'), OF | SF | AF | PF),   # TZCNT/LZCNT: CF ZF defined; BT family: CF defined, rest undefined
          **dict.fromkeys(('aam', 'aad'), OF | AF | CF), **dict.fromkeys(('aaa', 'aas'), OF | SF | ZF | PF), **dict.fromkeys(('daa', 'das'), OF)}   # AAM/AAD, AAA/AAS, DAA/DAS
def undef(E, st, o, X):
    """-> (undefined EFLAGS bits, {reg index: undefined bits}) per the Intel SDM 'flags affected' / operation text of the mnemonic"""
    m, ops = E['base'], E['ins'].operands; cnt = lambda op: (op.imm if op.type == X.X86_OP_IMM else st['regs'][1]) & 31
    if m in ('and', 'or', 'xor', 'test'): return AF, {}                       # AND/OR/XOR/TEST: AF undefined
    if m in ('shld', 'shrd') and ops[0].size == 2 and cnt(ops[-1]) > 16 and ops[0].type == X.X86_OP_REG:      # SDM: count > operand size: the destination is undefined too
        nm = E['ins'].reg_name(ops[0].reg); return (CF | OF | SF | ZF | PF | AF), {R32.index('e' + nm): 0xffff}
    if m in ('shl', 'sal', 'shr', 'sar', 'shld', 'shrd'):                      # SHL/SHR/SAR/SHLD/SHRD: count 0 changes no flag; count>0 AF undefined; count>1 OF undefined
        n = cnt(ops[-1]); return (AF if n else 0) | (OF if n > 1 else 0) | ((CF | OF | SF | ZF | PF) if m in ('shld', 'shrd') and n > ops[0].size * 8 else 0), {}   # SHLD/SHRD count > operand size: result flags undefined
    if m in ('rol', 'ror', 'rcl', 'rcr'): return (OF if cnt(ops[-1]) > 1 else 0), {}   # ROL/ROR/RCL/RCR: OF defined only for 1-bit rotates
    if m in SIMPLE: return SIMPLE[m], {}
    if m in ('bsf', 'bsr'):                                                    # BSF/BSR: only ZF defined; destination undefined when the source is 0 (ZF=1)
        nm = E['ins'].reg_name(ops[0].reg); rg = R32.index(nm if len(nm) == 3 else 'e' + nm)
        return CF | OF | SF | AF | PF, ({rg: ops[0].size == 2 and 0xffff or 0xffffffff} if o['fl'] & ZF else {})
    if m == 'fnstsw' and ops[0].type == X.X86_OP_REG: return 0, {0: 0x82ff}      # FNSTSW AX: exception bits, SF, ES, C1, B are not modelled
    return 0, {}
ARITH = ('fadd', 'fsub', 'fmul', 'fdiv', 'fiadd', 'fisub', 'fimul', 'fidiv', 'fsqrt', 'fldpi', 'fldl2e', 'fldl2t', 'fldlg2', 'fldln2')
def ndbl(m, se):
    """a normal double or zero?"""
    w = x80d(m, se)
    return w is not None and (((w >> 52) & 0x7ff) not in (0, 0x7ff) or w << 1 & (2 ** 64 - 1) == 0)
def priv_want(E):
    """the CPL-0-only instructions at CPL 3 (the lifter's class 2, f_sys.PRIV_MN): #GP(0), which Windows reports by KiTrap0D's opcode table (f_sys.trap0d_privileged: STATUS_PRIVILEGED_INSTRUCTION =
    fnvl_raise code 6, else STATUS_ACCESS_VIOLATION = fnvl_raise_av). -> the expected event ('trap6' / 'trap99') or None when the form is not of that class. Unicorn runs ring 0 and executes them."""
    f = G['L'].f_sys
    return None if E['base'] not in f.PRIV_MN else 'trap6' if f.trap0d_privileged(E['ins'].bytes) else 'trap99'
def gs_null_av(E, X):
    """a `gs:` memory access that touches memory, with the harness' GS = the null selector (fnvl_seg_init): #GP(0) -> access violation event; the Unicorn oracle has a flat GS"""
    return any(o.type == X.X86_OP_MEM and o.mem.segment == X.X86_REG_GS for o in E['ins'].operands) and E['ins'].mnemonic.split(' ')[-1] not in G['L'].core.NO_ACCESS
SELV = {'cs': 0x1b, 'ss': 0x23, 'ds': 0x23, 'es': 0x23, 'fs': 0x3b, 'gs': 0}       # selectors of a Win32 x86 user process (rt/seh.cc CONTEXT); Unicorn's flat selectors are its own
def origbyte(st, x): return (imgpage(x & ~0xfff) or genpage(st['wk'][0], st['wk'][1], x & ~0xfff))[x & 0xfff]
def model_fix(E, st, o, c, X):
    """W14: forms whose result is defined by the reference model, not by Unicorn: cpuid (rt/cpumodel.c through ctypes), rdtsc (ticks = 2100 * the stub counter; Unicorn's TSC is its own),
    segment register reads (mov r/m16, Sreg; push Sreg). Rewrites the oracle result o; returns a diff tuple or None."""
    b, ins = E['base'], E['ins']; ops = ins.operands
    if b == 'cpuid':
        import ctypes; out = (ctypes.c_uint32 * 4)(); G['cpum'].fnvl_cpuid(ctypes.c_uint32(st['regs'][0]), ctypes.c_uint32(st['regs'][1]), out)
        for ri, oi in ((0, 0), (3, 1), (1, 2), (2, 3)): o['regs'][ri] = out[oi]
    elif b == 'rdtsc':
        t = c['regs'][0] | c['regs'][2] << 32
        if t == 0 or t % 2100: return 'diff', 'rdtsc not 2.1 * a 1000 ns counter', '%x' % t, ''
        o['regs'][0], o['regs'][2] = c['regs'][0], c['regs'][2]
    elif b in ('mov', 'push') and any(op.type == X.X86_OP_REG and ins.reg_name(op.reg) in SELV for op in ops):
        sv = SELV[ins.reg_name(ops[-1].reg)] if b == 'mov' else SELV[ins.reg_name(ops[0].reg)]
        if b == 'mov' and ops[0].type == X.X86_OP_REG:
            nm = ins.reg_name(ops[0].reg); ri = R32.index(nm if len(nm) == 3 else 'e' + nm)
            o['regs'][ri] = sv if ops[0].size == 4 else (st['regs'][ri] & ~0xffff) | sv
        elif o['wr']:
            a0, n = o['wr'][0]; m = dict(o['mem'])
            for k in range(n):
                x = (a0 + k) & 0xffffffff; v = (sv >> 8 * k) & 0xff
                if v != origbyte(st, x): m[x] = v
                else: m.pop(x, None)
            o['mem'] = m
    return None
def compare(E, st, o, c, X):
    if 'fault' in c: return 'diff', 'lifted ' + c['fault'], '', ''
    pw = priv_want(E)
    if pw: return ('equal', '', '', '') if c['eip'] == pw else ('diff', 'privileged instruction: want ' + pw, str(c['eip']), '')
    if gs_null_av(E, X): return ('equal', '', '', '') if c['eip'] == 'trap99' else ('diff', 'gs: access with GS null: want #GP(0) = AV', str(c['eip']), '')
    r_ = model_fix(E, st, o, c, X)
    if r_: return r_
    if (o['eflx'] ^ c['eflx']): return 'diff', 'eflags AC/ID', '%x' % c['eflx'], '%x' % o['eflx']
    um, rm = undef(E, st, o, X)
    for i in range(8):
        if (o['regs'][i] ^ c['regs'][i]) & ~rm.get(i, 0) & 0xffffffff: return 'diff', R32[i], '%x' % c['regs'][i], '%x' % o['regs'][i]
    if o['eip'] != c['eip']: return 'diff', 'eip', str(c['eip']) if isinstance(c['eip'], str) else '%x' % c['eip'], '%x' % o['eip']
    om, cm = o['mem'], c['mem']
    if E['base'] == 'fnstsw' and E['ins'].operands[0].type != X.X86_OP_REG and o['wr']:     # FNSTSW m16: mask the same bits (byte 0 all, byte 1 bits 1 and 7)
        a0 = min(a for a, n in o['wr']); mk = {a0: 0xff, a0 + 1: 0x82}
        om = {a: v & ~mk.get(a, 0) for a, v in om.items()}; cm = {a: v & ~mk.get(a, 0) for a, v in cm.items()}
    if om != cm and E['base'] in ('fst', 'fstp') and o['wr'] and len(o['wr']) == 1 and o['wr'][0][1] in (4, 8):    # QEMU 5.0 narrows an 80-bit SNaN to m32/m64 without setting the quiet bit; the x87 does
        a0, w = o['wr'][0]; orig = lambda x: (imgpage(x & ~0xfff) or genpage(st['wk'][0], st['wk'][1], x & ~0xfff))[x & 0xfff]
        ov, cv = (int.from_bytes(bytes(d.get((a0 + k) & 0xffffffff, orig((a0 + k) & 0xffffffff)) for k in range(w)), 'little') for d in (om, cm)); qb = 1 << (22 if w == 4 else 51); ex = (0xff, 23) if w == 4 else (0x7ff, 52)
        if ov | qb == cv and (cv >> ex[1]) & ex[0] == ex[0]: om = cm
    if om != cm:
        for a in sorted(set(o['mem']) | set(c['mem'])):
            if o['mem'].get(a) != c['mem'].get(a): return 'diff', 'mem[%x]' % a, str(c['mem'].get(a)), str(o['mem'].get(a))
    dfl = (o['fl'] ^ c['fl']) & ~um
    if dfl: return 'diff', 'flags ' + '/'.join(n for b, n in FLN if dfl & b), '%x' % c['fl'], '%x' % o['fl']
    prec = None
    if o['fp']:
        f, g = o['fp'], c['fp']
        tg = ' (fcw %x)' % f['fcw']
        for k in ('top', 'valid', 'fcw'):
            if f[k] != g[k]: return 'diff', 'x87 ' + k, '%x' % g[k], '%x' % f[k]
        gi80 = lambda p: img80(g['st'][p][2], g['st'][p][0], g['st'][p][1])
        bad = [p for p in range(8) if f['valid'] >> p & 1 and gi80(p) != f['st'][p]]
        if bad and not E['base'].startswith(ARITH):
            p = bad[0]; return 'diff', 'x87 phys%d' % p + tg, '%04x%016x' % gi80(p)[::-1], '%04x%016x' % f['st'][p][::-1]
        if bad:
            p = bad[0]; gi = gi80(p); oi = f['st'][p]; det = ('x87 phys%d' % p + tg, '%04x%016x' % gi[::-1], '%04x%016x' % oi[::-1])
            nan = lambda m, e: e & 0x7fff == 0x7fff and m << 1 & (2 ** 64 - 1)
            exin = any(st['valid'] >> q & 1 and x80d(*img80(*st['fx'][q])) is None for q in range(8))                         # an operand that is not exactly a double: the first use rounds
            nn = sum(1 for q in range(8) if st['valid'] >> q & 1 and nan(*img80(*st['fx'][q]))) + (1 if any(o.type == X.X86_OP_MEM for o in E['ins'].operands) else 0)   # NaN operands (a memory one counts as one)
            if exin: prec = prec or det
            elif nan(*gi) and nan(*oi):                                                                                      # two NaN operands: the pick (payload, sign) may differ
                if gi != oi: prec = prec or det if nn >= 2 else None
            elif nan(*gi) or nan(*oi): return ('diff',) + det
            elif (f['fcw'] >> 8 & 3) != 2 or not all(ndbl(*f['st'][q]) for q in bad): prec = prec or det
            else: return ('diff',) + det
            if prec is None: return ('diff',) + det
        dcc = (f['cc'] ^ g['cc']) & CC_DEF.get(E['base'], 0) if E['base'].startswith('f') else 0
        if dcc: return 'diff', 'x87 cc', '%x' % g['cc'], '%x' % f['cc']
    if o['mm']:      # MMX = the x87 file (SDM Vol 1 9.2): significands, exponent|sign fields, TOP and the tag word are all compared
        for key, nm, fm in (('mm', 'mm%d', '%016x'), ('mme', 'mm%d.exp', '%04x')):
            if o[key] != c[key]: i = next(i for i in range(8) if o[key][i] != c[key][i]); return 'diff', nm % i, fm % c[key][i], fm % o[key][i]
        if o['mtop'] != c['mtop']: return 'diff', 'mmx top', '%d' % c['mtop'], '%d' % o['mtop']
        if o['mvalid'] != c['mvalid']: return 'diff', 'mmx tags', '%02x' % c['mvalid'], '%02x' % o['mvalid']
    if (o['mxcsr'] ^ c['mxcsr']) & 0xffc0: return 'diff', 'mxcsr', '%x' % c['mxcsr'], '%x' % o['mxcsr']
    if o['xmm'] and o['xmm'] != c['xmm']:
        i = next(i for i in range(8) if o['xmm'][i] != c['xmm'][i]); return 'diff', 'xmm%d' % i, '%032x' % c['xmm'][i], '%032x' % o['xmm'][i]
    return ('x87-prec',) + prec if prec else ('equal', '', '', '')
def build(batch_id, items, work):
    """items [(addr, text)] -> (elf or None, {addr: reason}); failing functions are found from g++'s 'In function' lines, else by bisection"""
    env = dict(os.environ, LC_ALL='C'); bad = {}; objs = []; d = '%s/b%d' % (work, batch_id); os.makedirs(d, exist_ok=True)
    def comp(its):
        if not its: return
        cpp = '%s/%d.cpp' % (d, len(objs)); o = cpp[:-4] + '.o'; open(cpp, 'w').write(G['L'].PRELUDE + STUBX + '\n'.join(t for a, t in its))
        r = subprocess.run(['aarch64-linux-gnu-g++'] + FLAGS + [cpp, '-o', o], capture_output=True, text=True, env=env)
        if r.returncode == 0: objs.append(o); return
        why = {int(m.group(2), 16): re.findall(r'error: ([^\n]*)', r.stderr[m.start():])[:1] for m in re.finditer(r'In function [^\n]*?(L_([0-9a-f]{8}))', r.stderr)}
        hit = [it for it in its if it[0] in why]
        if hit and len(hit) < len(its):
            for a, t in hit: bad[a] = (why[a] or ['compile error'])[0][:80]
            return comp([it for it in its if it[0] not in why])
        if len(its) == 1: bad[its[0][0]] = (re.findall(r'error: ([^\n]*)', r.stderr) or ['compile error'])[0][:80]; return
        h = len(its) // 2; comp(its[:h]); comp(its[h:])
    comp(items)
    if not objs: return None, bad
    elf = d + '/t.elf'; r = subprocess.run(['aarch64-linux-gnu-ld', '-m', 'aarch64elf32', '-Ttext=0x%x' % HARN, '-e', '0', '-o', elf, work + '/rt.o', work + '/x87.o', work + '/cpumodel.o', work + '/hostticks.o', work + '/fmath.o', work + '/rcptab.o'] + objs, capture_output=True, text=True)
    if r.returncode: return None, dict((a, 'link: ' + r.stderr[:60]) for a, t in items)
    return elf, bad
def run_batch(job):
    bid, batch, n = job; L = G['L']; img = G['img']; work = G['work']; from capstone import x86 as X
    out, items, items_s, enc = {}, [], [], {}
    for eid, key, addr, raw in batch:
        ins = next(L.core.md.disasm(raw, addr)); base = ins.mnemonic.split()[-1]; opc = form_key(ins, X)[1]
        enc[eid] = dict(addr=addr, raw=raw, ins=ins, base=base, rep=ins.mnemonic.split()[0] in ('rep', 'repe', 'repne', 'repz', 'repnz') and base in STRING,
                        x87=0xd8 <= opc[0] <= 0xdf or base == 'fwait',
                        mmx=base == 'emms' or any(o.type == X.X86_OP_REG and ins.reg_name(o.reg).startswith('mm') for o in ins.operands),
                        sse=base in ('ldmxcsr', 'stmxcsr') or any(o.type == X.X86_OP_REG and ins.reg_name(o.reg)[:2] in ('xm', 'mm') for o in ins.operands))
        try:
            items.append((addr, L.lift(img, addr, len(raw), strict=False, x87='dyn' if enc[eid]['x87'] else None)[0]))
            if enc[eid]['x87']: items_s.append((addr, L.lift(img, addr, len(raw), strict=False, x87=XD)[0]))
        except L.Unsupported as e: out[eid] = [('unlifted', str(e)[:70], '', '')] * n
        except Exception as e: out[eid] = [('unlifted', 'lifter error %s: %s' % (type(e).__name__, str(e)[:50]), '', '')] * n
    elf, bad = build(bid, items, work); arm = Arm(elf) if elf else None
    elf2, bad2 = build(bid + 100000, items_s, work); arm2 = Arm(elf2) if elf2 else None
    if 'orc' not in G: G['orc'] = Oracle()
    orc = G['orc']
    for eid, key, addr, raw in batch:
        if eid in out: continue
        if addr in bad or arm is None: out[eid] = [('nocompile', bad.get(addr, 'no object'), '', '')] * n; continue
        E = enc[eid]; res = []
        for i in range(n):
            sv = E['x87'] and i % 2 == 1                  # odd cases of an x87 encoding: the static-stack variant, a state of depth XD
            for att in range(8):
                st = gen_state(E, i, att, XD if sv else None); o = orc.run(E, st)
                if not o.get('collide'): break
            ar = arm2 if sv else arm
            res.append(('fault', o['fault'][:60], '', '') if 'fault' in o else ('nocompile', 'static variant ' + bad2.get(addr, 'no object'), '', '') if ar is None or sv and addr in bad2 else compare(E, st, o, ar.run(E, st), X))
        out[eid] = res
    import shutil; shutil.rmtree('%s/b%d' % (work, bid), ignore_errors=True); shutil.rmtree('%s/b%d' % (work, bid + 100000), ignore_errors=True)
    return out
def runtime_objects(work, lifter):
    """the ARM-side runtime objects every test link needs, built into `work`: rt.o (test link symbols, libc string stubs), fmath.o, rcptab.o, x87.o, hostticks.o, cpumodel.o"""
    open(work + '/rt.S', 'w').write('.text\n' + ''.join('.globl %s\n%s: ret\n' % (k, k) for k in ('fnvl_call', 'fnvl_dispatch', 'fnvl_raise', 'fnvl_raise_av', 'fnvl_trace')) +   # fnvl_rcp32/rsqrt32: runtime-owner hooks, a bare `ret` here (their rows are 'hook', not gate failures)
        '.globl memcpy\nmemcpy: mov x3,x0\n1: cbz x2,2f\n ldrb w4,[x1],#1\n strb w4,[x3],#1\n sub x2,x2,#1\n b 1b\n2: ret\n'
        '.globl memset\nmemset: mov x3,x0\n1: cbz x2,2f\n strb w1,[x3],#1\n sub x2,x2,#1\n b 1b\n2: ret\n'
        '.globl memmove\nmemmove: cmp x0,x1\n b.ls 3f\n add x3,x0,x2\n add x1,x1,x2\n1: cbz x2,2f\n ldrb w4,[x1,#-1]!\n strb w4,[x3,#-1]!\n sub x2,x2,#1\n b 1b\n2: ret\n'
        '3: mov x3,x0\n4: cbz x2,2b\n ldrb w4,[x1],#1\n strb w4,[x3],#1\n sub x2,x2,#1\n b 4b\n')
    subprocess.check_call(['aarch64-linux-gnu-gcc', '-mabi=ilp32', '-c', work + '/rt.S', '-o', work + '/rt.o'])
    for f in ('fmath', 'rcptab'): subprocess.check_call(['aarch64-linux-gnu-gcc', '-mabi=ilp32', '-O2', '-fPIC', '-ffreestanding', '-fno-stack-protector', '-fno-builtin', '-c', os.path.join(lifter, 'rt', f + '.c'), '-o', work + '/' + f + '.o'])
    subprocess.check_call(['aarch64-linux-gnu-gcc', '-mabi=ilp32', '-O2', '-fPIC', '-ffreestanding', '-fno-stack-protector', '-fno-asynchronous-unwind-tables', '-I', os.path.join(lifter, 'rt'), '-c', os.path.join(lifter, 'rt', 'x87.c'), '-o', work + '/x87.o'])
    # W14: the reference CPU model (rt/cpumodel.c) linked into the ARM side with a counter as the host clock (+1000 ns per call), and built for the host
    # (ctypes) as the expected values of cpuid. fnvl_rdtsc is checked against its definition, ticks = ns * 21 / 10, through the counter.
    open(work + '/hostticks.c', 'w').write('unsigned long long fnvl_host_ticks_ns(void){ static unsigned long long n; return n += 1000; }\n')
    subprocess.check_call(['aarch64-linux-gnu-gcc', '-mabi=ilp32', '-O2', '-fPIC', '-ffreestanding', '-fno-stack-protector', '-c', work + '/hostticks.c', '-o', work + '/hostticks.o'])
    subprocess.check_call(['aarch64-linux-gnu-gcc', '-mabi=ilp32', '-O2', '-fPIC', '-ffreestanding', '-fno-stack-protector', '-c', os.path.join(lifter, 'rt', 'cpumodel.c'), '-o', work + '/cpumodel.o'])
def main():
    ap = argparse.ArgumentParser(); A = ap.add_argument
    A('--n', type=int, default=8); A('--k', type=int, default=4); A('--jobs', type=int, default=3); A('--only'); A('--limit-forms', type=int)
    A('--work', default='/home/user/l3work/instest'); A('--lifter', default=os.path.dirname(HERE))
    A('--exe', default=os.environ.get('FNVNX_EXE', '/home/user/game/FalloutNV.exe')); A('--pack', default=os.environ.get('FNVNX_PACK', '/home/user/l3work/pack'))
    a = ap.parse_args(); t0 = time.time(); os.makedirs(a.work, exist_ok=True); sys.path.insert(0, a.lifter)
    import fnvlift as L, fnvlift.core
    from fnvlift import fpfacts; fpfacts.ASSUME.update(('I-FPMASK', 'I-FPSTICKY'))      # the generated states have every exception masked and nothing reads sticky flags: the invariants hold by construction of the test states (fnvlift/fpfacts.py)
    class TImage(L.Image):                             # an encoding outside the exe (a module of the pack): its bytes come from encs.tsv
        def bytes(self, x, n):
            r = G['other'].get(x)
            return r[:n] + b'\x90' * max(0, n - len(r)) if r is not None else super().bytes(x, n)
    G.update(L=L, img=TImage(a.exe), work=a.work, other={})
    runtime_objects(a.work, a.lifter)
    open(a.work + '/hostshim.c', 'w').write('unsigned long long fnvl_host_ticks_ns(void){ return 0; }\n')
    subprocess.check_call(['gcc', '-shared', '-fPIC', '-O1', '-o', a.work + '/cpumodel_host.so', os.path.join(a.lifter, 'rt', 'cpumodel.c'), a.work + '/hostshim.c'])
    import ctypes; G['cpum'] = ctypes.CDLL(a.work + '/cpumodel_host.so')
    open(a.work + '/off.cpp', 'w').write(L.PRELUDE + '\n#include <stddef.h>\nstatic_assert(offsetof(cpu,fs)==32&&offsetof(cpu,ftop)==36&&offsetof(cpu,fcw)==40&&offsetof(cpu,fc)==44&&offsetof(cpu,x)==48&&offsetof(cpu,fl)==176&&offsetof(cpu,mxcsr)==180&&offsetof(cpu,mm_unused)==184&&offsetof(cpu,ra)==248&&offsetof(cpu,fex)==252&&offsetof(cpu,fx)==256&&offsetof(cpu,eflx)==384&&offsetof(cpu,sel)==400&&offsetof(cpu,teb)==424&&offsetof(cpu,fip)==428&&offsetof(cpu,fdp)==432&&offsetof(cpu,eflags)==460&&offsetof(cpu,dr)==464&&sizeof(cpu)==488,"cpu layout");\n')
    subprocess.check_call(['aarch64-linux-gnu-g++'] + FLAGS + ['-fsyntax-only', a.work + '/off.cpp'])
    d = load_forms(a, None); forms = d['forms']; print('pack: %d forms' % len(forms), file=sys.stderr)
    keys = sorted(k for k in forms if not a.only or k.split('|')[0] in a.only.split(','))
    if a.limit_forms and len(keys) > a.limit_forms: keys = [keys[i * len(keys) // a.limit_forms] for i in range(a.limit_forms)]
    encs = [(k, ia, raw) for k in keys for raw, ia in sorted(forms[k][2].items(), key=lambda t: t[1])]
    G['other'].update({ia: raw for k, ia, raw in encs if not any(va <= ia < va + vs for n_, va, vs, ro, rs in G['img'].secs)})
    jobs = [(b, [(i,) + encs[i] for i in range(s, min(s + 60, len(encs)))], a.n) for b, s in enumerate(range(0, len(encs), 60))]
    res = {}
    with mp.get_context('fork').Pool(a.jobs) as p:
        for k, r in enumerate(p.imap_unordered(run_batch, jobs)): res.update(r); print('batch %d/%d %.0fs' % (k + 1, len(jobs), time.time() - t0), file=sys.stderr) if k % 20 == 0 else 0
    report(a, d, keys, encs, res, time.time() - t0)
def report(a, d, keys, encs, res, secs):
    mn = {}; js = {}; ex = {}
    for k in keys:
        m = k.split('|')[0]; e = mn.setdefault(m, dict(forms=0, encs=0, occ=0, c={o: 0 for o in OUTS})); e['forms'] += 1; e['occ'] += d['forms'][k][0]
        js[k] = dict(occurrences=d['forms'][k][0], functions=d['forms'][k][1], encodings=[])
    for eid, (k, ia, raw) in enumerate(encs):
        m = k.split('|')[0]; e = mn[m]; e['encs'] += 1; js[k]['encodings'].append(dict(addr='%08x' % ia, bytes=raw.hex(), cases=res[eid]))
        for sd, r in enumerate(res[eid]):
            e['c'][r[0]] += 1
            if r[0] != 'equal' and (m, r[0]) not in ex: ex[(m, r[0])] = '%s %s form=%s bytes=%s seed=%s %s got=%s want=%s' % (m, r[0], k, raw.hex(), sd if r[0] not in ('unlifted', 'nocompile') else '-', r[1], r[2], r[3])
    fne = fall = 0
    for k in keys:
        oc = [r[0] for e in js[k]['encodings'] for r in e['cases']]; js[k]['not_equal'] = any(o in NOT_EQ for o in oc); fne += js[k]['not_equal']; fall += 'equal' not in oc and not js[k]['not_equal']
    json.dump(js, open(a.work + '/result.json', 'w'), indent=0)
    print('mnemonic forms encodings cases equal diff unlifted nocompile fault x87-prec occurrences functions   (n=%d k=%d)' % (a.n, a.k)); T = [0] * 10
    for m in sorted(mn):
        e = mn[m]; row = [e['forms'], e['encs'], sum(e['c'].values())] + [e['c'][o] for o in OUTS]; row += [e['occ']]
        print(m, *row, d['mnf'].get(m, 0)); T = [x + y for x, y in zip(T, row)]
    print('TOTAL', *T, d['nfuncs']); print('forms not equal: %d of %d   (forms with no equal case and no failure, i.e. oracle always faulted: %d)' % (fne, len(keys), fall))
    lines = sorted(ex.items(), key=lambda t: (NOT_EQ.index(t[0][1]) if t[0][1] in NOT_EQ else 9, t[0][0]))
    open(a.work + '/examples.txt', 'w').write('\n'.join(v for k, v in sorted(ex.items())) + '\n'); cap = max(0, 440 - len(mn) - 6)
    for k, v in sorted(lines[:cap], key=lambda t: t[0]): print(v)
    if len(lines) > cap: print('... %d more example lines in %s/examples.txt' % (len(lines) - cap, a.work))
    print('run time %.0fs; results %s/result.json' % (secs, a.work))
if __name__ == '__main__': main()
