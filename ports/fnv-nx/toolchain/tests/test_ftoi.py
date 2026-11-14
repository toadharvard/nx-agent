#!/usr/bin/env python3
"""Test of toolchain/ftoi/fnvnx_ftoi.cc (FTOI3: one plugin, class C, two modes) through recipe stages on the guest compiler (plugbuild.py).
  native (a native root: every plugin, the plugin's mode comes from cxx_origin):
    - a conversion outside a helper is MSVC's _ftol (0x80000000 for NaN and out of range);
    - a function carrying __attribute__((fnvnx_f2i)) is left unrewritten (the bare fcvtzs) and is a helper in the report;
    - the report (out=/out/unit.f2i.json, kept by the recipe as the unit's f2i file) counts the sites and lists the helper;
    - a builtin conversion (lrintf) outside a helper is a finding of the report, not rewritten.
  lifted (a lifted-origin root: fnvnx_ftoi is required and its mode is lifted):
    - a conversion inside a helper builds, no rewrite, the report has the helper, the site, no finding;
    - a conversion outside a helper (cast), lround, and an asm with a float operand are each a build error naming the rule;
  the recipe validation: a lifted root without fnvnx_ftoi, with another mode or with a mode argument of a native root is red.
No network, no game data.  Run: python3 -I <this file> from the checkout root."""
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plugbuild as pb  # noqa: E402

check = pb.Checks('test_ftoi')
HELPER = '#define F2I __attribute__((fnvnx_f2i))\nF2I int helper(float f) { return (int)f; }\n'
OK = HELPER + 'int user(float f) { return helper(f) + 1; }\n'
NATIVE = HELPER + 'int plain(float f) { return (int)f; }\nlong lr(float f) { return __builtin_lrintf(f); }\n'
BAD_CAST = HELPER + 'int bad_cast(float f) { return (int)f; }\n'
BAD_BUILTIN = HELPER + 'long bad_lround(float f) { return __builtin_lroundf(f); }\n'
BAD_MD = HELPER + '#include <arm_neon.h>\nint32x4_t bad_md(float32x4_t v) { return vcvtq_s32_f32(v); }\n'
BAD_ASM = HELPER + 'float bad_asm(float f) { asm volatile("" : "+w"(f)); return f; }\n'


def comp(td, name, text, native=False, plugins=()):
    src = os.path.join(td, name + '.cpp')
    with open(src, 'w') as f:
        f.write(text)
    files = {}
    r = pb.compile(src, os.path.join(td, name + '.o'), list(plugins), files=files, native=native)
    return r, os.path.join(td, name + '.o'), (json.load(open(files['unit.f2i.json'])) if 'unit.f2i.json' in files else None)


NEON = r'''#include <arm_neon.h>
typedef float v4sf __attribute__((vector_size(16)));
typedef int v4si __attribute__((vector_size(16)));
typedef unsigned v4su __attribute__((vector_size(16)));
typedef double v2df __attribute__((vector_size(16)));
typedef long long v2di __attribute__((vector_size(16)));
typedef unsigned long long v2du __attribute__((vector_size(16)));
#define V(n, T, e, x) extern "C" T n(e a) { return x; }
// vector builtins, signed: every rounding (z n a m p), 4 / 2 / 2 lanes
V(tr4, int32x4_t, float32x4_t, vcvtq_s32_f32(a)) V(nr4, int32x4_t, float32x4_t, vcvtnq_s32_f32(a)) V(aw4, int32x4_t, float32x4_t, vcvtaq_s32_f32(a))
V(fl4, int32x4_t, float32x4_t, vcvtmq_s32_f32(a)) V(ce4, int32x4_t, float32x4_t, vcvtpq_s32_f32(a))
V(tr2, int32x2_t, float32x2_t, vcvt_s32_f32(a)) V(nr2, int32x2_t, float32x2_t, vcvtn_s32_f32(a))
V(trd, int64x2_t, float64x2_t, vcvtq_s64_f64(a)) V(nrd, int64x2_t, float64x2_t, vcvtnq_s64_f64(a)) V(awd, int64x2_t, float64x2_t, vcvtaq_s64_f64(a))
V(fld, int64x2_t, float64x2_t, vcvtmq_s64_f64(a)) V(ced, int64x2_t, float64x2_t, vcvtpq_s64_f64(a))
// vector builtins, unsigned
V(utr4, uint32x4_t, float32x4_t, vcvtq_u32_f32(a)) V(unr4, uint32x4_t, float32x4_t, vcvtnq_u32_f32(a)) V(uaw4, uint32x4_t, float32x4_t, vcvtaq_u32_f32(a))
V(ufl4, uint32x4_t, float32x4_t, vcvtmq_u32_f32(a)) V(uce4, uint32x4_t, float32x4_t, vcvtpq_u32_f32(a))
V(utr2, uint32x2_t, float32x2_t, vcvt_u32_f32(a))
V(utrd, uint64x2_t, float64x2_t, vcvtq_u64_f64(a)) V(unrd, uint64x2_t, float64x2_t, vcvtnq_u64_f64(a)) V(uawd, uint64x2_t, float64x2_t, vcvtaq_u64_f64(a))
V(ufld, uint64x2_t, float64x2_t, vcvtmq_u64_f64(a)) V(uced, uint64x2_t, float64x2_t, vcvtpq_u64_f64(a))
// fixed-point (fcvtz, a fraction-bits operand)
V(fx4, int32x4_t, float32x4_t, vcvtq_n_s32_f32(a, 8)) V(ufx4, uint32x4_t, float32x4_t, vcvtq_n_u32_f32(a, 5)) V(fxd, int64x2_t, float64x2_t, vcvtq_n_s64_f64(a, 10))
// scalars
extern "C" int tr1(float a) { return vcvts_s32_f32(a); }
extern "C" int nr1(float a) { return vcvtns_s32_f32(a); }
extern "C" unsigned utr1(float a) { return vcvts_u32_f32(a); }
extern "C" long long trd1(double a) { return vcvtd_s64_f64(a); }
extern "C" long long nrd1(double a) { return vcvtnd_s64_f64(a); }
extern "C" unsigned long long utrd1(double a) { return vcvtd_u64_f64(a); }
extern "C" int fx1(float a) { return vcvts_n_s32_f32(a, 4); }
extern "C" unsigned ufx1(float a) { return vcvts_n_u32_f32(a, 7); }
extern "C" long long fxd1(double a) { return vcvtd_n_s64_f64(a, 12); }
extern "C" long long fs64(float a) { return __builtin_aarch64_fix_truncsfdi(a); }
extern "C" int fd32(double a) { return __builtin_aarch64_fix_truncdfsi(a); }
// generic vector conversions: FIX_TRUNC_EXPR on vectors, __builtin_convertvector (IFN_VEC_CONVERT), unsigned lanes
V(cv4, v4si, v4sf, __builtin_convertvector(a, v4si)) V(ucv4, v4su, v4sf, __builtin_convertvector(a, v4su)) V(cvd, v2di, v2df, __builtin_convertvector(a, v2di))
V(ucvd, v2du, v2df, __builtin_convertvector(a, v2du))
// the BUILT_IN_NORMAL families (long is 32 bits: ILP32)
extern "C" long lrf(float a) { return __builtin_lrintf(a); }
extern "C" long lrd(double a) { return __builtin_lrint(a); }
extern "C" long long llrd(double a) { return __builtin_llrint(a); }
extern "C" long lrdn(double a) { return __builtin_lround(a); }
extern "C" long long llrf(float a) { return __builtin_llroundf(a); }
extern "C" long lcd(double a) { return __builtin_lceil(a); }
extern "C" long lfd(double a) { return __builtin_lfloor(a); }
extern "C" int ird(double a) { return __builtin_irint(a); }
extern "C" int16x4_t mp3(float32x4_t a, int32x4_t b) { return vqmovn_s32(vqaddq_s32(vcvtq_s32_f32(a), b)); }
'''
F32 = [0.0, -0.0, 0.5, -0.5, 1.5, -1.5, 2.5, -2.5, 0.49999997, 1.9, -1.9, 16777216.0, 1e-45, 2147483520.0, 2147483648.0, -2147483648.0,
       -2147483904.0, 3e9, -3e9, 1e30, -1e30, float('inf'), float('-inf'), float('nan')]
F64 = [0.0, 0.5, -0.5, 1.5, -2.5, 4503599627370495.5, 2147483647.5, 2147483647.0, 2147483648.0, -2147483648.0, -2147483648.5, -2147483649.0,
       9223372036854774784.0, 9223372036854775808.0, -9223372036854775808.0, -9223372036854777856.0, 1e19, -1e19, 1e300, float('inf'),
       float('-inf'), float('nan')]


def rnd(x, mode):
    """the rounding of the exact value x (a float or a Fraction) in the named mode; None for NaN / inf"""
    from fractions import Fraction
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    f = Fraction(x)
    return {'trunc': lambda: math.trunc(f), 'floor': lambda: math.floor(f), 'ceil': lambda: math.ceil(f),
            'even': lambda: round(f), 'away': lambda: (1 if f >= 0 else -1) * math.floor(abs(f) + Fraction(1, 2))}[mode]()


def oracle(x, mode, bits, uns=False, shift=0):
    """the x86 result (cvttps2dq / cvtps2dq / cvttsd2si ...) of the conversion of x * 2^shift with rounding MODE to a BITS-bit signed or unsigned
    integer: R(y) when it fits, else the integer indefinite (bit BITS-1 set), as a signed or unsigned value of the lane"""
    from fractions import Fraction
    y = x * (1 << shift) if (math.isnan(x) or math.isinf(x)) else Fraction(x) * (1 << shift)
    r = rnd(y, mode) if isinstance(y, Fraction) else None
    lo, hi = (0, (1 << bits) - 1) if uns else (-(1 << (bits - 1)), (1 << (bits - 1)) - 1)
    return r if r is not None and lo <= r <= hi else (1 << (bits - 1) if uns else -(1 << (bits - 1)))


def load(obj):
    """the object's .text and its read-only data at fixed addresses on the emulator, with the relocations of leaf code applied (the page and
    low-12 forms of ADRP/ADD/LDR, and LDR literal; any other type is an error) -> (emulator, {global function: address})"""
    import re
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
    from unicorn.arm64_const import UC_ARM64_REG_CPACR_EL1
    tool = lambda *a: subprocess.run(list(a), capture_output=True, check=True).stdout
    secs = {}
    base = 0x10000
    for name in ('.text', '.rodata', '.rodata.cst4', '.rodata.cst8', '.rodata.cst16', '.rodata.cst32'):
        raw = tool('aarch64-linux-gnu-objcopy', '-O', 'binary', '-j', name, obj, '/dev/stdout')
        if raw:
            secs[name] = (base, raw)
            base += (len(raw) + 0xfff) & ~0xfff
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.reg_write(UC_ARM64_REG_CPACR_EL1, 3 << 20)
    uc.mem_map(0x10000, (base - 0x10000) + 0x10000)
    text = bytearray(secs['.text'][1])
    rel = tool('aarch64-linux-gnu-objdump', '-r', obj).decode()
    cur = None
    for line in rel.splitlines():
        m = re.match(r'RELOCATION RECORDS FOR \[(.+)\]', line)
        if m:
            cur = m.group(1)
            continue
        if cur != '.text':   # .eh_frame and friends are not loaded: the emulator runs leaf code
            continue
        m = re.match(r'([0-9a-f]+) R_AARCH64_P32_(\w+)\s+(\S+?)(?:\+0x([0-9a-f]+))?$', line)
        if not m:
            assert not re.match(r'[0-9a-f]+ R_', line), 'unsupported relocation: ' + line
            continue
        off, typ, tgt, add = int(m.group(1), 16), m.group(2), m.group(3), int(m.group(4) or '0', 16)
        assert tgt in secs, 'relocation against ' + tgt
        s_addr = secs[tgt][0] + add
        pc = secs['.text'][0] + off
        ins = struct.unpack_from('<I', text, off)[0]
        if typ == 'ADR_PREL_PG_HI21':
            imm = ((s_addr >> 12) - (pc >> 12)) & 0x1fffff
            ins |= ((imm & 3) << 29) | ((imm >> 2) << 5)
        elif typ == 'ADD_ABS_LO12_NC':
            ins |= (s_addr & 0xfff) << 10
        elif typ.startswith('LDST') and typ.endswith('_ABS_LO12_NC'):
            sz = int(typ[4:-12]) // 8
            ins |= ((s_addr & 0xfff) // sz) << 10
        else:
            raise AssertionError('unsupported relocation type ' + typ)
        struct.pack_into('<I', text, off, ins)
    uc.mem_write(secs['.text'][0], bytes(text))
    for name, (addr, raw) in secs.items():
        if name != '.text':
            uc.mem_write(addr, raw)
    nm = tool('aarch64-linux-gnu-nm', obj).decode()
    sym = {l.split()[2]: secs['.text'][0] + int(l.split()[0], 16) for l in nm.splitlines() if len(l.split()) == 3 and l.split()[1] == 'T'}
    return uc, sym


def run_neon(td, obj, fn, ins, nfloat, bits, nlanes, scalar=False, uns=False, fpcr=0):
    """execute FN of the object (leaf code, no relocation) on the emulator with the float lanes INS in v0 (and w/x/v1 args none); -> signed lanes"""
    from unicorn.arm64_const import UC_ARM64_REG_LR, UC_ARM64_REG_Q0, UC_ARM64_REG_Q1, UC_ARM64_REG_X0
    uc, sym = run_neon.cache.get(obj) or (None, None)
    if uc is None:
        uc, sym = load(obj)
        run_neon.cache[obj] = (uc, sym)
    from unicorn.arm64_const import UC_ARM64_REG_FPCR
    uc.reg_write(UC_ARM64_REG_FPCR, fpcr)   # the rounding mode of the run (RMode, bits 23:22): 0 nearest-even, 1 +inf, 2 -inf, 3 zero
    fmt = {32: 'f', 64: 'd'}[nfloat]
    uc.reg_write(UC_ARM64_REG_Q0, int.from_bytes(struct.pack('<%d%s' % (len(ins), fmt), *ins), 'little'))
    uc.reg_write(UC_ARM64_REG_Q1, 0)   # the second vector argument of mp3: b = 0
    uc.reg_write(UC_ARM64_REG_LR, 0x1f000)   # unmapped on purpose: the run stops when it reaches it
    uc.emu_start(sym[fn], 0x1f000)
    if scalar:   # an integer result is in x0 / w0
        v = uc.reg_read(UC_ARM64_REG_X0) & ((1 << bits) - 1)
        return [v if uns or not v >> (bits - 1) else v - (1 << bits)]
    raw = uc.reg_read(UC_ARM64_REG_Q0).to_bytes(16, 'little')
    code = {16: 'h', 32: 'i', 64: 'q'}[bits]
    return list(struct.unpack('<%d%s' % (nlanes, code.upper() if uns else code), raw[:nlanes * bits // 8]))


run_neon.cache = {}


def lane_sets(vals, n):
    """each value in each lane against NaN and 7.5 neighbours, and a rotating mix: every lane sees every value (lane independence)"""
    out = []
    for v in vals:
        for p in range(n):
            for filler in (float('nan'), 7.5):
                out.append([v if i == p else filler for i in range(n)])
    for k in range(len(vals)):
        out.append([vals[(k + i) % len(vals)] for i in range(n)])
    return out


def main():
    td = tempfile.mkdtemp(prefix='test-ftoi-')
    try:
        so = pb.build('ftoi', td)
        check('the plugin builds as a recipe plugin stage against msvcmodel\'s headers', os.path.basename(so) == 'fnvnx_ftoi.so')
        # --- native
        r, obj, j = comp(td, 'nat', NATIVE, native=True)
        check('native: builds', r.returncode == 0, r.stderr[-1500:])
        if r.returncode == 0:
            d = pb.objdump(obj)
            plain = '\n'.join(d.get('_Z5plainf', []))
            helper = '\n'.join(d.get('_Z6helperf', []))
            check('native: a conversion outside a helper is _ftol (0x80000000)', '#0x80000000' in plain, plain)
            check('native: an fnvnx_f2i helper is not rewritten (bare fcvtzs)', 'fcvtzs' in helper and '#0x80000000' not in helper, helper)
            check('native: report holds the helper, 3 sites, the builtin rewritten (kind builtin, normal/current) and no finding',
                  j and j['format'] == 'fnvnx-f2i/1' and j['helpers'] == ['int helper(float)'] and j['sites'] == 3 and j['findings'] == []
                  and [(f['function'], f['kind'], f['how']) for f in j['rewritten']] == [('long int lr(float)', 'builtin', 'normal/current')], j)
        r, _, j = comp(td, 'nat_md', BAD_MD, native=True)
        check('native: a target float->int builtin (vcvtq_s32_f32) is rewritten, kind mdbuiltin, no finding',
              r.returncode == 0 and j and j['findings'] == [] and [f['kind'] for f in j['rewritten']] == ['mdbuiltin'] and j['rewritten'][0]['how'] == 'md/trunc', (r.stderr[-800:], j))
        r, _, _ = comp(td, 'nat_jcvt', 'int j(double d) { return __builtin_aarch64_jcvtzs(d); }\n', native=True)
        check('native: a target builtin that converts with no rounding in GCC\'s table (jcvtzs) is a build error, never passed through',
              r.returncode != 0 and 'FTOI' in r.stderr and 'table' in r.stderr, r.stderr[-800:])
        for name, text, tys in (
                ('mixed', 'typedef float v4sf __attribute__((vector_size(16)));\ntypedef long long v4di __attribute__((vector_size(32)));\n'
                          'v4di cv(v4sf v) { return __builtin_convertvector(v, v4di); }\n', ('v4sf', 'v4di')),
                ('ld', 'long long ld(long double x) { return (long long)x; }\n', ('long double', 'long long')),
                ('i128', '__int128 w(double d) { return (__int128)d; }\n', ('double', '__int128'))):
            r, _, _ = comp(td, 'wide_' + name, text, native=True)
            check('native: a conversion the rewrite does not cover (%s) is a build error naming both types' % name,
                  r.returncode != 0 and 'FTOI' in r.stderr and all(t in r.stderr for t in tys), r.stderr[-1500:])
        # --- every float -> integer conversion C does not fully specify: x86's answer in range, the integer indefinite out of it (RULE in the plugin)
        r, nobj, j = comp(td, 'neon', NEON, native=True)
        check('native: the conversion class builds with no finding and every site is in the report as rewritten',
              r.returncode == 0 and j and j['findings'] == [] and len(j['rewritten']) > 40, (r.stderr[-1500:], j and j['findings']))
        if r.returncode == 0:
            try:
                import unicorn  # noqa: F401
            except ImportError:
                check('native: the conversions run on the emulator', False, 'unicorn is not installed (verify/envcheck.py pins it)')
            else:
                def f32(x):
                    return struct.unpack('<f', struct.pack('<f', x))[0]
                f32v = [f32(x) for x in F32]
                RM = {'even': 0, 'ceil': 1 << 22, 'floor': 2 << 22, 'trunc': 3 << 22}   # FPCR.RMode of the run, for the current-mode families
                # (fn, rounding, lanes, float bits, int bits, unsigned, fixed-point shift, scalar result in x0)
                T = [('tr4', 'trunc', 4, 32, 32), ('nr4', 'even', 4, 32, 32), ('aw4', 'away', 4, 32, 32), ('fl4', 'floor', 4, 32, 32), ('ce4', 'ceil', 4, 32, 32),
                     ('tr2', 'trunc', 2, 32, 32), ('nr2', 'even', 2, 32, 32),
                     ('trd', 'trunc', 2, 64, 64), ('nrd', 'even', 2, 64, 64), ('awd', 'away', 2, 64, 64), ('fld', 'floor', 2, 64, 64), ('ced', 'ceil', 2, 64, 64),
                     ('utr4', 'trunc', 4, 32, 32, 1), ('unr4', 'even', 4, 32, 32, 1), ('uaw4', 'away', 4, 32, 32, 1), ('ufl4', 'floor', 4, 32, 32, 1),
                     ('uce4', 'ceil', 4, 32, 32, 1), ('utr2', 'trunc', 2, 32, 32, 1),
                     ('utrd', 'trunc', 2, 64, 64, 1), ('unrd', 'even', 2, 64, 64, 1), ('uawd', 'away', 2, 64, 64, 1), ('ufld', 'floor', 2, 64, 64, 1),
                     ('uced', 'ceil', 2, 64, 64, 1),
                     ('fx4', 'trunc', 4, 32, 32, 0, 8), ('ufx4', 'trunc', 4, 32, 32, 1, 5), ('fxd', 'trunc', 2, 64, 64, 0, 10),
                     ('tr1', 'trunc', 1, 32, 32, 0, 0, 1), ('nr1', 'even', 1, 32, 32, 0, 0, 1), ('utr1', 'trunc', 1, 32, 32, 1, 0, 1),
                     ('trd1', 'trunc', 1, 64, 64, 0, 0, 1), ('nrd1', 'even', 1, 64, 64, 0, 0, 1), ('utrd1', 'trunc', 1, 64, 64, 1, 0, 1),
                     ('fx1', 'trunc', 1, 32, 32, 0, 4, 1), ('ufx1', 'trunc', 1, 32, 32, 1, 7, 1), ('fxd1', 'trunc', 1, 64, 64, 0, 12, 1),
                     ('fs64', 'trunc', 1, 32, 64, 0, 0, 1), ('fd32', 'trunc', 1, 64, 32, 0, 0, 1),
                     ('cv4', 'trunc', 4, 32, 32), ('ucv4', 'trunc', 4, 32, 32, 1), ('cvd', 'trunc', 2, 64, 64), ('ucvd', 'trunc', 2, 64, 64, 1),
                     ('lcd', 'ceil', 1, 64, 32, 0, 0, 1), ('lfd', 'floor', 1, 64, 32, 0, 0, 1), ('lrdn', 'away', 1, 64, 32, 0, 0, 1),
                     ('llrf', 'away', 1, 32, 64, 0, 0, 1)]
                for e in (T + [('lrf', m, 1, 32, 32, 0, 0, 1, m) for m in RM] + [('lrd', m, 1, 64, 32, 0, 0, 1, m) for m in RM]
                          + [('llrd', m, 1, 64, 64, 0, 0, 1, m) for m in RM] + [('ird', m, 1, 64, 32, 0, 0, 1, m) for m in RM]):
                    fn, mode, n, nf, bits = e[:5]
                    uns, shift, scalar = (list(e[5:8]) + [0, 0, 0])[:3]
                    fpcr = RM[e[8]] if len(e) > 8 else 0
                    bad = []
                    cases = 0
                    for ins in lane_sets(f32v if nf == 32 else F64, n):
                        got = run_neon(td, nobj, fn, ins, nf, bits, n, scalar=bool(scalar), uns=bool(uns), fpcr=fpcr)
                        if fn in ('utr1', 'utrd1'):   # a plain scalar (unsigned) cast is MSVC's: x87 int64, then the low bits (the scalar rewrite)
                            want = [(oracle(x, 'trunc', 64) if not math.isnan(x) else -(1 << 63)) & ((1 << bits) - 1) for x in ins]
                        else:
                            want = [oracle(x, mode, bits, bool(uns), shift) for x in ins]
                        cases += 1
                        if got != want:
                            bad.append((ins, got, want))
                    check('native: %s (%s%s, %d lane(s) %s%d-bit from %d-bit floats, shift %d): %d lane vectors equal x86' %
                          (fn, mode, ', FPCR ' + e[8] if len(e) > 8 else '', n, 'u' if uns else 's', bits, nf, shift, cases), not bad, bad[:2])
                # minimp3's expression: vqmovn_s32(vqaddq_s32(vcvtq_s32_f32(a), b)), b = 0: the saturating narrow of the x86 result
                bad = []
                for ins in lane_sets(f32v, 4):
                    got = run_neon(td, nobj, 'mp3', ins, 32, 16, 4)
                    want = [max(-32768, min(32767, oracle(x, 'trunc', 32))) for x in ins]
                    if got != want:
                        bad.append((ins, got, want))
                check('native: minimp3 vqmovn_s32(vqaddq_s32(vcvtq_s32_f32(a), 0)) equals saturate(x86 trunc)', not bad, bad[:2])
        # --- lifted
        r, obj, j = comp(td, 'ok', OK)
        check('lifted: a conversion inside a helper builds', r.returncode == 0, r.stderr[-1500:])
        if r.returncode == 0:
            helper = '\n'.join(pb.objdump(obj).get('_Z6helperf', []))
            check('lifted: no rewrite (the helper is the bare fcvtzs)', 'fcvtzs' in helper and '#0x80000000' not in helper, helper)
            check('lifted: report holds the helper, one site, no finding',
                  j and j['helpers'] == ['int helper(float)'] and j['sites'] == 1 and j['findings'] == [], j)
        for name, text, kind in (('cast', BAD_CAST, 'conv'), ('lround', BAD_BUILTIN, 'builtin'), ('target builtin', BAD_MD, 'mdbuiltin'), ('asm', BAD_ASM, 'asm')):
            r, _, _ = comp(td, 'bad_' + name, text)
            check('lifted: %s outside a helper is a build error' % name,
                  r.returncode != 0 and 'FTOI (lifted)' in r.stderr and '(%s)' % kind in r.stderr, r.stderr[-1500:])
        # --- the recipe validation (the mode is the root's, a lifted root needs the plugin)
        for what, plug in (('without fnvnx_ftoi', []), ('with mode native', [('ftoi', {'mode': 'native'})]), ('with another argument', [('ftoi', {'mode': 'lifted', 'verbose': ''})])):
            try:
                if not plug:
                    pb._recipe(td, [('sdd', {})], {'t.cpp': 'int t();\n'}, pb.PRODUCT_FLAGS, raw=True)
                else:
                    pb._recipe(td, plug, {'t.cpp': 'int t();\n'}, pb.PRODUCT_FLAGS, raw=True)
                check('recipe: a lifted root %s is red' % what, False)
            except pb.recipe.RecipeError as e:
                check('recipe: a lifted root %s is red' % what, any('fnvnx_ftoi' in x for x in e.errors), e.errors)
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
