#!/usr/bin/env python3
"""Gate for rt/msvcrt_ci.cc: the 15 msvcrt _CI* intrinsics as guest entries (rt/fmath.c under them).
    citest.py [--work DIR] [--count]
The guest entries (compiled with the lifted-code flags, test/instest.py FLAGS without the test-only flag) are driven DIRECTLY from a native `test_main` (a C++ driver compiled with the same flags: guest registers in x19..x27, `c` = struct cpu):
for every case it fills the x87 stack through the runtime's own carriers (dyn_push), points the guest esp at a return address, calls the entry, and records stack depth, ST0..ST2, errno, the x87 exception bits,
esp and c->ra. All of it runs as AArch64 ILP32 under Unicorn. The platform's `_errno` is a test double (a plain int, preset to 7 so "unchanged" is visible); nothing else of the platform is used.
Checks per entry: operand order of _CIatan2 / _CIpow / _CIfmod (ST1 first), stack depth before / after (one operand: unchanged; two operands: one less; deeper entries intact; full 8-deep stack), the value
(correctly rounded vs MPFR / exact vs integer arithmetic) and the x87 exception bits, c->ra = the pushed return address and esp += 4, and the errno rule of rt/msvcrt_ci.cc for domain / range errors.
--count prints the instructions executed per call (Unicorn basic-block hook, 4 bytes per instruction) for a few representative operands.
Prints one line per entry; exit status 1 on any difference."""
import argparse, math, os, struct, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(HERE)
for p in ('tools', 'test', ''): sys.path.insert(0, os.path.join(LIFT, p))
import instest, fmathtest as FT
import gmpy2
from fractions import Fraction
from unicorn import Uc, UcError, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_BLOCK
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X30, UC_ARM64_REG_SP, UC_ARM64_REG_CPACR_EL1
from elftools.elf.elffile import ELFFile

TEXT, HSTACK, STOP, TAB = 0xE0000000, 0x70000000, 0x7e000000, 0x10000000
GSTK = 0x20000800
ENT = 'acos asin atan atan2 cos cosh exp fmod log pow sin sinh sqrt tan tanh'.split()
TWO = ('atan2', 'fmod', 'pow')
REC = 128
DRIVER = r'''
#include "fnvl.h"
extern "C" {
''' + ''.join('void fnvl_msvcrt_CI%s(void);\n' % n for n in ENT) + r'''
}
typedef void (*fn_t)(void);
static fn_t T[15] = { ''' + ', '.join('fnvl_msvcrt_CI' + n for n in ENT) + r''' };
static int errno_v;
extern "C" int *t_errno(void) { return &errno_v; }
extern "C" void *fnvnx_win32_lookup_export(const char *dll, const char *name) { return name[0] == '_' && name[1] == 'e' ? (void *)t_errno : 0; }                              /* test double of the platform's _errno (platform/crt/heap.c) */
extern "C" void *memcpy(void *d, const void *s, unsigned long n) { char *a = (char *)d; const char *b = (const char *)s; while (n--) *a++ = *b++; return d; }
extern "C" void *memset(void *d, int v, unsigned long n) { char *a = (char *)d; while (n--) *a++ = (char)v; return d; }
struct Rec { u32 entry, depth, pad0, pad1; u64 v[8]; u32 depth_out, err, fex, esp_d, ra_out, pad2; u64 st[3]; };
static struct cpu cpu0;
extern "C" u32 test_main(Rec *r, u32 n) {
  c = &cpu0;
  for (u32 i = 0; i < n; i++) {
    fnvl_x87_init();
    for (u32 k = 0; k < r[i].depth; k++) dyn_push(fv_f64(u2d(r[i].v[k])));
    errno_v = 7; c->fex = 0;
    esp = 0x20000800u; M32(esp) = 0xAAAA0000u + i; c->ra = 0;
    T[r[i].entry]();
    u32 d = 0; for (u32 k = 0; k < 8; k++) if (c->fx[k].k) d++;
    r[i].depth_out = d; r[i].err = (u32)errno_v; r[i].fex = c->fex; r[i].esp_d = esp - 0x20000800u; r[i].ra_out = c->ra;
    for (u32 k = 0; k < 3; k++) { fv *p = &c->fx[(c->ftop + k) & 7]; r[i].st[k] = p->k == 1 ? p->v : 0xDEADDEADDEADDEADull; }
  }
  return 0;
}
'''
CF = ['-mabi=ilp32', '-O2', '-fPIC', '-ffreestanding', '-fno-stack-protector', '-fno-asynchronous-unwind-tables', '-fno-stack-clash-protection', '-w', '-c']


def build(w):
    os.makedirs(w, exist_ok=True)
    fl = [f for f in instest.FLAGS if f != '-DFNVL_FLAGS_IO'] + ['-I', LIFT + '/rt']
    open(w + '/driver.cpp', 'w').write(DRIVER)
    jobs = [(['aarch64-linux-gnu-g++'] + fl + ['-x', 'c++'], w + '/driver.cpp'), (['aarch64-linux-gnu-g++'] + fl + ['-x', 'c++'], LIFT + '/rt/msvcrt_ci.cc'),
            (['aarch64-linux-gnu-gcc'] + CF + ['-I', LIFT + '/rt'], LIFT + '/rt/fmath.c'), (['aarch64-linux-gnu-gcc'] + CF + ['-I', LIFT + '/rt'], LIFT + '/rt/x87.c')]
    objs = []
    for cmd, src in jobs:
        o = '%s/%s.o' % (w, os.path.basename(src).rsplit('.', 1)[0]); objs.append(o)
        r = subprocess.run(cmd + [src, '-o', o], capture_output=True, text=True)
        if r.returncode: sys.exit('compile %s: %s' % (src, r.stderr[:800]))
    undef = set()
    for o in objs:
        for l in subprocess.run(['aarch64-linux-gnu-nm', o], capture_output=True, text=True).stdout.splitlines():
            if l.startswith('        U '): undef.add(l.split()[1])
    elf = w + '/ci.elf'
    r = subprocess.run(['aarch64-linux-gnu-ld', '-m', 'aarch64elf32', '-Ttext=0x%x' % TEXT, '-e', 'test_main', '-o', elf] + objs, capture_output=True, text=True)
    if r.returncode: sys.exit('link: ' + r.stderr[:800])
    # undefined symbols of the guest entry object alone (what the lead's link has to provide): the platform's _errno + the runtime's
    ci = subprocess.run(['aarch64-linux-gnu-nm', '-u', w + '/msvcrt_ci.o'], capture_output=True, text=True).stdout.split()
    return elf, sorted(x for x in ci if x not in ('U',))


def run_cases(elf, recs, count=False, per_case=False):
    """recs: list of dicts (entry, vals). -> list of result dicts (and instruction counts)"""
    with open(elf, 'rb') as fh:
        e = ELFFile(fh); segs = [(p['p_vaddr'], p.data(), p['p_memsz']) for p in e.iter_segments() if p['p_type'] == 'PT_LOAD']
        sym = {x.name: x['st_value'] for x in e.get_section_by_name('.symtab').iter_symbols() if x.name}
    out = []; counts = []
    groups = [[r] for r in recs] if per_case else [recs]
    for grp in groups:
        uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        lo = min(v for v, d, m in segs) & ~0xfff; hi = (max(v + m for v, d, m in segs) + 0xffff) & ~0xfff; uc.mem_map(lo, hi - lo)
        for v, d, m in segs: uc.mem_write(v, d)
        uc.mem_map(HSTACK, 0x40000); uc.mem_map(STOP, 0x1000); uc.mem_map(0x20000000, 0x10000); uc.mem_map(TAB, 0x10000 * ((len(grp) * REC) // 0x10000 + 1))
        uc.reg_write(UC_ARM64_REG_CPACR_EL1, 3 << 20); uc.reg_write(UC_ARM64_REG_SP, HSTACK + 0x3f000); uc.reg_write(UC_ARM64_REG_X30, STOP)
        buf = bytearray()
        for r in grp:
            v = list(r['vals']) + [0.0] * (8 - len(r['vals']))
            buf += struct.pack('<4I', r['entry'], len(r['vals']), 0, 0) + struct.pack('<8d', *v) + b'\0' * 48
        uc.mem_write(TAB, bytes(buf))
        uc.reg_write(UC_ARM64_REG_X0, TAB); uc.reg_write(UC_ARM64_REG_X1, len(grp))
        n = [0]
        if count: uc.hook_add(UC_HOOK_BLOCK, lambda u, a, s, d: n.__setitem__(0, n[0] + s // 4))
        try:
            uc.emu_start(sym['test_main'], STOP, count=0 if count else 400000000)
        except UcError as ex:
            sys.exit('unicorn: %s' % ex)
        raw = bytes(uc.mem_read(TAB, len(grp) * REC))
        for i in range(len(grp)):
            o = raw[i * REC + 80:(i + 1) * REC]
            dep, err, fex, esp_d, ra = struct.unpack_from('<5I', o, 0)
            st = struct.unpack_from('<3Q', o, 24)
            out.append(dict(depth=dep, err=err, fex=fex, esp_d=esp_d, ra=ra, st=st))
        counts.append(n[0])
    return out, counts


# ------------------------------------------------------------------ expectations
def b(x): return FT.u64_(x)
def f(u): return FT.f64_(u)
IND = 0xFFF8000000000000


def fmod_exact(x, y):
    q = Fraction(x) / Fraction(y); t = int(q)                                  # trunc toward zero
    r = Fraction(x) - t * Fraction(y)
    return math.copysign(0.0, x) if r == 0 else float(r)


def expect(name, ops):
    """-> (value bits or the string 'nan', value for the flag check or None). ops in x87 order bottom -> top (the operands of the call)"""
    O = FT.ORACLE
    if name == 'atan2': v = O['atan2'](ops[0], ops[1])
    elif name == 'pow': v = O['pow'](ops[0], ops[1])
    elif name == 'fmod': return b(fmod_exact(ops[0], ops[1])), None
    elif name == 'sqrt':
        with FT.prec(0): v = +gmpy2.sqrt(FT.mf(ops[0]))
    else: v = O[name](ops[0])
    return b(FT.rnd(v)), v


# (entry, operands bottom->top, result bits | None = NaN, errno, note): special / domain cases with hand-given results
INF = math.inf
SPECIAL = [
    ('acos', [2.0], IND, 33), ('acos', [-1.5], IND, 33), ('acos', [INF], IND, 33), ('acos', [1.0], 0, 7), ('acos', [-1.0], b(FT.rnd(+gmpy2.const_pi())), 7),
    ('asin', [1.0000001], IND, 33), ('asin', [-INF], IND, 33), ('asin', [0.0], 0, 7), ('asin', [float('nan')], None, 33), ('acos', [float('nan')], None, 7),
    ('atan', [float('nan')], None, 33), ('atan', [INF], b(FT.rnd(+gmpy2.const_pi() / 2)), 7), ('atan', [-0.0], 1 << 63, 7),
    ('log', [-1.0], IND, 33), ('log', [0.0], b(-INF), 34), ('log', [-0.0], b(-INF), 34), ('log', [1.0], 0, 7), ('log', [INF], b(INF), 7), ('log', [-INF], IND, 33), ('log', [float('nan')], None, 7),
    ('exp', [1000.0], b(INF), 34), ('exp', [-1000.0], 0, 34), ('exp', [0.0], b(1.0), 7), ('exp', [-INF], 0, 7), ('exp', [INF], b(INF), 7), ('exp', [float('nan')], None, 33),
    ('cosh', [1000.0], b(INF), 34), ('cosh', [-INF], b(INF), 7), ('cosh', [0.0], b(1.0), 7), ('sinh', [-1000.0], b(-INF), 34), ('sinh', [INF], b(INF), 7), ('sinh', [-0.0], 1 << 63, 7),
    ('tanh', [30.0], b(1.0), 7), ('tanh', [-INF], b(-1.0), 7), ('tanh', [float('nan')], None, 33),
    ('sqrt', [-4.0], IND, 33), ('sqrt', [-0.0], 1 << 63, 7), ('sqrt', [INF], b(INF), 7), ('sqrt', [-INF], IND, 33), ('sqrt', [float('nan')], None, 33), ('sqrt', [0.0], 0, 7),
    ('sin', [INF], IND, 33), ('cos', [-INF], IND, 33), ('tan', [INF], IND, 33), ('sin', [float('nan')], None, 7), ('sin', [0.0], 0, 7), ('cos', [0.0], b(1.0), 7),
    ('atan2', [1e-300, 1e300], 0, 34), ('atan2', [0.0, -1.0], b(FT.rnd(+gmpy2.const_pi())), 7), ('atan2', [0.0, 0.0], 0, 7), ('atan2', [-INF, INF], b(FT.rnd(-gmpy2.const_pi() / 4)), 7),
    ('atan2', [float('nan'), 1.0], None, 7),
    ('pow', [-8.0, 1.0 / 3], IND, 33), ('pow', [0.0, -1.0], b(INF), 34), ('pow', [-0.0, -3.0], b(-INF), 34), ('pow', [10.0, 400.0], b(INF), 34), ('pow', [10.0, -400.0], 0, 34), ('pow', [2.0, 0.0], b(1.0), 7),
    ('pow', [1.0, float('nan')], b(1.0), 7), ('pow', [float('nan'), 0.0], b(1.0), 7), ('pow', [float('nan'), 2.0], None, 7), ('pow', [-2.0, 3.0], b(-8.0), 7), ('pow', [-2.0, 2.0], b(4.0), 7),
    ('pow', [-INF, 3.0], b(-INF), 7), ('pow', [0.0, INF], 0, 7), ('pow', [0.0, -INF], b(INF), 7), ('pow', [-1.0, INF], b(1.0), 7), ('pow', [3.0, 34.0], b(float(3 ** 34 + 1)) if False else b(FT.rnd(Fraction(3 ** 34))), 7),
    ('fmod', [5.0, 0.0], IND, 33), ('fmod', [INF, 2.0], IND, 33), ('fmod', [5.0, INF], b(5.0), 7), ('fmod', [-0.0, 3.0], 1 << 63, 7), ('fmod', [float('nan'), 2.0], None, 7), ('fmod', [-6.0, 3.0], 1 << 63, 7),
]
VALS1 = {'acos': [0.5, -0.25, 0.9999999999999999, 1e-300], 'asin': [0.5, -0.75, 1.0, 1e-10], 'atan': [1.0, -3.5, 1e300, 1e-300], 'cos': [1.0, 1e22, 0.5],
         'cosh': [1.0, -20.5, 709.5], 'exp': [1.0, -1.5, 709.0, -745.0], 'log': [2.0, 0.1, 1e300, 5e-324], 'sin': [1.0, -2.5, 1e22], 'sinh': [1.0, -0.5, 700.0], 'sqrt': [2.0, 1e-310, 9.0, 1.7976931348623157e308],
         'tan': [1.0, 1.5707963267948966, -100.0], 'tanh': [0.5, -1.0, 3.0]}
VALS2 = {'atan2': [(1.0, 2.0), (2.0, 1.0), (-3.0, -4.0), (1e-5, -7.0)], 'fmod': [(10.0, 3.0), (3.0, 10.0), (-7.5, 2.0), (1e300, 3.0), (5.0, 0.3)], 'pow': [(2.0, 10.0), (10.0, 2.0), (2.0, 0.5), (1.1, 100.0), (-3.0, 5.0), (7.0, -0.5)]}
SENT = [777.0, 888.0]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--work', default='/home/user/l3work/citest'); ap.add_argument('--count', action='store_true'); a = ap.parse_args()
    elf, undef = build(a.work)
    print('msvcrt_ci.o undefined symbols: %s' % ' '.join(undef))
    cases = []                      # (entry, vals, expected bits or None(NaN), errno, flagval, label)
    for n in ENT:
        ops = [[x] for x in VALS1[n]] if n not in TWO else [list(p) for p in VALS2[n]]
        for o in ops:
            bits, v = expect(n, o)
            for under in (SENT, [], SENT[:1]):                     # two / zero / one entries beneath the operands
                cases.append((n, under + o, bits, None, v, 'value %s%s depth+%d' % (n, o, len(under))))
        o = [VALS2[n][0][0], VALS2[n][0][1]] if n in TWO else [VALS1[n][0]]
        bits, v = expect(n, o)
        cases.append((n, [float(i + 100) for i in range(8 - len(o))] + o, bits, None, v, '8-deep %s' % n))        # full stack: pop before push
    for n, o, bits, err in SPECIAL:
        cases.append((n, SENT + o, bits, err, None, 'special %s%s' % (n, o)))
    recs = [dict(entry=ENT.index(c[0]), vals=c[1]) for c in cases]
    res, _ = run_cases(elf, recs)
    bad = 0; per = {n: [0, 0] for n in ENT}
    for ci, (c, r) in enumerate(zip(cases, res)):
        n, vals, bits, err, v, label = c
        nop = 2 if n in TWO else 1
        under = len(vals) - nop
        want_depth = under + 1
        ok = True; why = []
        if r['depth'] != want_depth: ok = False; why.append('depth %d want %d' % (r['depth'], want_depth))
        if r['esp_d'] != 4: ok = False; why.append('esp +%d' % r['esp_d'])
        if r['ra'] != 0xAAAA0000 + ci: ok = False; why.append('ra %x' % r['ra'])
        got = r['st'][0]
        if bits is None:
            if not ((got & 0x7ff0000000000000) == 0x7ff0000000000000 and (got & 0xfffffffffffff)): ok = False; why.append('not NaN: %x' % got)
        elif got != bits: ok = False; why.append('result %s want %s' % (float.hex(f(got)), float.hex(f(bits))))
        if under >= 1 and r['st'][1] != b(vals[under - 1]):
            ok = False; why.append('entry below changed: %s' % float.hex(f(r['st'][1])))
        if under >= 2 and r['st'][2] != b(vals[under - 2]): ok = False; why.append('second entry below changed')
        if err is not None and r['err'] != err: ok = False; why.append('errno %d want %d' % (r['err'], err))
        if err is None and v is not None:
            want_fl = FT.expect_flags(v, f(bits)); got_fl = r['fex'] & 0x23c
            if got_fl != want_fl: ok = False; why.append('flags %x want %x' % (got_fl, want_fl))
        if err is None and r['err'] != 7: ok = False; why.append('errno set to %d on a value case' % r['err'])
        if bits == IND and not (r['fex'] & 1): ok = False; why.append('IE missing')
        per[n][0 if ok else 1] += 1
        if not ok:
            bad += 1
            if bad <= 25: print('   DIFF %s: %s' % (label, '; '.join(why)))
    for n in ENT: print('CI%-6s cases %3d  equal %3d  different %d' % (n, sum(per[n]), per[n][0], per[n][1]))
    print('%s: %d cases, %d different' % ('FAIL' if bad else 'PASS', len(cases), bad))
    if a.count:
        samp = [(n, ([VALS2[n][1][0], VALS2[n][1][1]] if n in TWO else [VALS1[n][0]])) for n in ENT]
        samp += [('sin', [1e22]), ('exp', [-745.0]), ('pow', [3.0, 34.0]), ('pow', [1.1, 100.0]), ('log', [5e-324]), ('fmod', [1e300, 3.0]), ('sqrt', [2.0])]
        res, cnt = run_cases(elf, [dict(entry=ENT.index(n), vals=o) for n, o in samp], count=True, per_case=True)
        for (n, o), k in zip(samp, cnt): print('instructions CI%s%s: %d' % (n, o, k))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
