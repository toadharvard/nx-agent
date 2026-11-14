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
#define V(n, T, e) extern "C" T n(e a)
V(tr4, int32x4_t, float32x4_t) { return vcvtq_s32_f32(a); }
V(nr4, int32x4_t, float32x4_t) { return vcvtnq_s32_f32(a); }
V(aw4, int32x4_t, float32x4_t) { return vcvtaq_s32_f32(a); }
V(fl4, int32x4_t, float32x4_t) { return vcvtmq_s32_f32(a); }
V(ce4, int32x4_t, float32x4_t) { return vcvtpq_s32_f32(a); }
V(tr2, int32x2_t, float32x2_t) { return vcvt_s32_f32(a); }
V(nr2, int32x2_t, float32x2_t) { return vcvtn_s32_f32(a); }
V(trd, int64x2_t, float64x2_t) { return vcvtq_s64_f64(a); }
V(nrd, int64x2_t, float64x2_t) { return vcvtnq_s64_f64(a); }
V(awd, int64x2_t, float64x2_t) { return vcvtaq_s64_f64(a); }
V(fld, int64x2_t, float64x2_t) { return vcvtmq_s64_f64(a); }
V(ced, int64x2_t, float64x2_t) { return vcvtpq_s64_f64(a); }
extern "C" int tr1(float a) { return vcvts_s32_f32(a); }
extern "C" int nr1(float a) { return vcvtns_s32_f32(a); }
extern "C" long long trd1(double a) { return vcvtd_s64_f64(a); }
extern "C" long long nrd1(double a) { return vcvtnd_s64_f64(a); }
extern "C" long long fs64(float a) { return __builtin_aarch64_fix_truncsfdi(a); }
extern "C" int fd32(double a) { return __builtin_aarch64_fix_truncdfsi(a); }
extern "C" int16x4_t mp3(float32x4_t a, int32x4_t b) { return vqmovn_s32(vqaddq_s32(vcvtq_s32_f32(a), b)); }
extern "C" uint32x4_t uns(float32x4_t a) { return vcvtq_u32_f32(a); }
'''
F32 = [0.0, -0.0, 0.5, -0.5, 1.5, -1.5, 2.5, -2.5, 0.49999997, 1.9, -1.9, 16777216.0, 1e-45, 2147483520.0, 2147483648.0, -2147483648.0,
       -2147483904.0, 3e9, -3e9, 1e30, -1e30, float('inf'), float('-inf'), float('nan')]
F64 = [0.0, 0.5, -0.5, 1.5, -2.5, 4503599627370495.5, 2147483647.5, 2147483647.0, 2147483648.0, -2147483648.0, -2147483648.5, -2147483649.0,
       9223372036854774784.0, 9223372036854775808.0, -9223372036854775808.0, -9223372036854777856.0, 1e19, -1e19, 1e300, float('inf'),
       float('-inf'), float('nan')]


def rnd(x, mode):
    """x86's rounding of the exact value x (a float) in the named mode; None for NaN / inf"""
    if math.isnan(x) or math.isinf(x):
        return None
    from fractions import Fraction
    f = Fraction(x)
    return {'trunc': lambda: math.trunc(f), 'floor': lambda: math.floor(f), 'ceil': lambda: math.ceil(f),
            'even': lambda: round(f), 'away': lambda: (1 if f >= 0 else -1) * math.floor(abs(f) + Fraction(1, 2))}[mode]()


def oracle(x, mode, bits):
    """the x86 result (cvttps2dq / cvtps2dq / cvttsd2si ...): R(x) when it fits, else the integer indefinite INT_MIN"""
    r = rnd(x, mode)
    return r if r is not None and -(1 << (bits - 1)) <= r < (1 << (bits - 1)) else -(1 << (bits - 1))


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


def run_neon(td, obj, fn, ins, nfloat, bits, nlanes, scalar=False):
    """execute FN of the object (leaf code, no relocation) on the emulator with the float lanes INS in v0 (and w/x/v1 args none); -> signed lanes"""
    from unicorn.arm64_const import UC_ARM64_REG_LR, UC_ARM64_REG_Q0, UC_ARM64_REG_Q1, UC_ARM64_REG_X0
    uc, sym = run_neon.cache.get(obj) or (None, None)
    if uc is None:
        uc, sym = load(obj)
        run_neon.cache[obj] = (uc, sym)
    fmt = {32: 'f', 64: 'd'}[nfloat]
    uc.reg_write(UC_ARM64_REG_Q0, int.from_bytes(struct.pack('<%d%s' % (len(ins), fmt), *ins), 'little'))
    uc.reg_write(UC_ARM64_REG_Q1, 0)   # the second vector argument of mp3: b = 0
    uc.reg_write(UC_ARM64_REG_LR, 0x1f000)   # unmapped on purpose: the run stops when it reaches it
    uc.emu_start(sym[fn], 0x1f000)
    if scalar:   # an integer result is in x0 / w0
        v = uc.reg_read(UC_ARM64_REG_X0) & ((1 << bits) - 1)
        return [v - (1 << bits) if v >> (bits - 1) else v]
    raw = uc.reg_read(UC_ARM64_REG_Q0).to_bytes(16, 'little')
    return list(struct.unpack('<%d%s' % (nlanes, {16: 'h', 32: 'i', 64: 'q'}[bits]), raw[:nlanes * bits // 8]))


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
            check('native: report holds the helper, 3 sites, and the builtin as the only finding',
                  j and j['format'] == 'fnvnx-f2i/1' and j['helpers'] == ['int helper(float)'] and j['sites'] == 3
                  and [(f['function'], f['kind']) for f in j['findings']] == [('long int lr(float)', 'builtin')], j)
        r, _, j = comp(td, 'nat_md', BAD_MD, native=True)
        check('native: a target float->int builtin (vcvtq_s32_f32) is a finding of kind mdbuiltin, not builtin',
              r.returncode == 0 and j and [f['kind'] for f in j['findings']] == ['mdbuiltin'], (r.stderr[-800:], j))
        for name, text, tys in (
                ('vec', 'typedef float v4sf __attribute__((vector_size(16)));\ntypedef int v4si __attribute__((vector_size(16)));\n'
                        'v4si cv(v4sf v) { return __builtin_convertvector(v, v4si); }\n', ('v4sf', 'v4si')),
                ('ld', 'long long ld(long double x) { return (long long)x; }\n', ('long double', 'long long')),
                ('i128', '__int128 w(double d) { return (__int128)d; }\n', ('double', '__int128'))):
            r, _, _ = comp(td, 'wide_' + name, text, native=True)
            check('native: a conversion the rewrite does not cover (%s) is a build error naming both types' % name,
                  r.returncode != 0 and 'FTOI' in r.stderr and all(t in r.stderr for t in tys), r.stderr[-1500:])
        # --- the AArch64 float -> signed integer target builtins (rewritten lane by lane to x86's integer indefinite; unsigned ones stay findings)
        r, nobj, j = comp(td, 'neon', NEON, native=True)
        check('native: NEON conversions build, and only the unsigned builtin is a finding', r.returncode == 0 and j and
              [(f['function'], f['kind']) for f in j['findings']] == [('uint32x4_t vcvtq_u32_f32(float32x4_t)', 'builtin')], (r.stderr[-1500:], j))
        if r.returncode == 0:
            try:
                import unicorn  # noqa: F401
            except ImportError:
                check('native: NEON conversions run on the emulator', False, 'unicorn is not installed (verify/envcheck.py pins it)')
            else:
                def f32(x):
                    return struct.unpack('<f', struct.pack('<f', x))[0]
                f32v = [f32(x) for x in F32]
                for fn, mode, n, nf, bits in (('tr4', 'trunc', 4, 32, 32), ('nr4', 'even', 4, 32, 32), ('aw4', 'away', 4, 32, 32), ('fl4', 'floor', 4, 32, 32),
                                              ('ce4', 'ceil', 4, 32, 32), ('tr2', 'trunc', 2, 32, 32), ('nr2', 'even', 2, 32, 32),
                                              ('trd', 'trunc', 2, 64, 64), ('nrd', 'even', 2, 64, 64), ('awd', 'away', 2, 64, 64),
                                              ('fld', 'floor', 2, 64, 64), ('ced', 'ceil', 2, 64, 64),
                                              ('tr1', 'trunc', 1, 32, 32), ('nr1', 'even', 1, 32, 32), ('trd1', 'trunc', 1, 64, 64),
                                              ('nrd1', 'even', 1, 64, 64), ('fs64', 'trunc', 1, 32, 64), ('fd32', 'trunc', 1, 64, 32)):
                    bad = []
                    cases = 0
                    for ins in lane_sets(f32v if nf == 32 else F64, n):
                        got = run_neon(td, nobj, fn, ins, nf, bits, n, scalar=fn[-1] in '1' or fn in ('fs64', 'fd32'))
                        want = [oracle(x, mode, bits) for x in ins]
                        cases += 1
                        if got != want:
                            bad.append((ins, got, want))
                    check('native: %s (%s, %d lanes of %d bits from %d-bit floats): %d lane vectors equal x86 (NaN, inf, +-2^%d, in range)' %
                          (fn, mode, n, bits, nf, cases, bits - 1), not bad, bad[:2])
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
