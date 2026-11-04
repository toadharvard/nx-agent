#!/usr/bin/env python3
"""Variadic references decided by their call sites (vacall.py, fnvnx_vacall.so; V1 VA-U1..U4), end to end on
hand-written code (no decomp content): the plugin records each check's call through `int br_<case>(...)`, decide()
compares the definition with the call site's fixed signature, and the pair runs under qemu-aarch64 through a plain
branch (`same`) or the generated bridge.

    python3 tools/link/test_vacall.py   # needs gcc-13-plugin-dev-aarch64-linux-gnu, aarch64-linux-gnu-*, qemu-aarch64
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import abi           # noqa: E402
import abi2          # noqa: E402
import fnvlink       # noqa: E402
import layout        # noqa: E402
import sigs          # noqa: E402
import test_bridges  # noqa: E402
import vacall        # noqa: E402

TYPES = r'''
struct B5 { int a[5]; };
struct F1 { float f; };
'''

# (case, definition MSVC, verdict wanted, C++ definition def_<case>, C++ check calling br_<case>(...)); the
# reference is always the decomp's unprototyped `int f(...)`; each check wants the x86 reading of its call
CASES = [
    ('c0', '?p@@YAXHH@Z', 'same',
     'extern "C" int g_c0; int g_c0; extern "C" void def_c0(int a, int b) { g_c0 = a * 1000 + b; }',
     'extern "C" int br_c0(...);\nstatic int t_c0() { g_c0 = 0; br_c0(1, 42); return g_c0 == 1042 ? 0 : 1; }'),
    ('u1', '?p@@YAXHH@Z', 'bridge',   # a long long is two x86 words, low first
     'extern "C" int g_u1; int g_u1; extern "C" void def_u1(int a, int b) { g_u1 = a * 1000 + b; }',
     'extern "C" int br_u1(...);\n'
     'static int t_u1() { g_u1 = 0; br_u1(0x0000002a00000001LL); return g_u1 == 1042 ? 0 : 1; }'),
    ('u2', '?p@@YAXHH@Z', 'bridge',   # a double's two words
     'extern "C" unsigned g_u2[2]; unsigned g_u2[2]; extern "C" void def_u2(int lo, int hi) '
     '{ g_u2[0] = lo; g_u2[1] = hi; }',
     'extern "C" int br_u2(...);\n'
     'static int t_u2() { double d = 2.5; unsigned w[2]; __builtin_memcpy(w, &d, 8); g_u2[0] = g_u2[1] = 0;'
     ' br_u2(d); return g_u2[0] == w[0] && g_u2[1] == w[1] ? 0 : 1; }'),
    ('m1', '?p@@YAXPBDHN@Z', 'same',  # pointer, int, double: one register each on both readings
     'extern "C" double g_m1; double g_m1; extern "C" void def_m1(const char *s, int n, double d) '
     '{ g_m1 = (s[0] == \'k\') + n + d; }',
     'extern "C" int br_m1(...);\nstatic int t_m1() { g_m1 = 0; br_m1("k", 40, 1.5); return g_m1 == 42.5 ? 0 : 1; }'),
    ('u3', '?p@@YAXHHHHH@Z', 'manual', '',   # a 20-byte struct by value: not from the declarations
     'extern "C" int br_u3(...);\nstatic int t_u3() { B5 s = {{1, 2, 3, 4, 5}}; br_u3(s); return 0; }'),
    ('u4', '?p@@YAXH@Z', 'manual', '',       # a struct { float }: an s register on AArch64
     'extern "C" int br_u4(...);\nstatic int t_u4() { F1 f = {1.0f}; br_u4(f); return 0; }'),
    ('d2', '?p@@YAXHH@Z', 'manual', '',      # two call sites passing different types: one bridge cannot serve both
     'extern "C" int br_d2(...);\nstatic int t_d2() { br_d2(1, 2); br_d2(3.0); return 0; }'),
]


# fixed references against variadic definitions whose bodies the plugin reads (VADEF, V2 VA2-VA4): (case,
# reference MSVC, definition MSVC, wanted, C++ definition def_<case>, C++ check calling br_<case>)
DEF_CASES = [
    ('v2', '?l@@YAXHHH@Z', '?l@@YAXHZZ', 'manual',     # two words where the body reads a double
     'extern "C" double g_v2; double g_v2; extern "C" void def_v2(int n, ...) { __builtin_va_list a;'
     ' __builtin_va_start(a, n); g_v2 = __builtin_va_arg(a, double); __builtin_va_end(a); }', ''),
    ('v4', '?r@@YAXHHH@Z', '?r@@YAXHZZ', 'manual',     # two words where the body reads a long long
     'extern "C" long long g_v4; long long g_v4; extern "C" void def_v4(int n, ...) { __builtin_va_list a;'
     ' __builtin_va_start(a, n); g_v4 = __builtin_va_arg(a, long long); __builtin_va_end(a); }', ''),
    ('v5', '?d@@YAXHN@Z', '?d@@YAXHZZ', 'same',        # a double where the body reads a double
     'extern "C" double g_v5; double g_v5; extern "C" void def_v5(int n, ...) { __builtin_va_list a;'
     ' __builtin_va_start(a, n); g_v5 = n + __builtin_va_arg(a, double); __builtin_va_end(a); }',
     'extern "C" void br_v5(int, double);\nstatic int t_v5() { g_v5 = 0; br_v5(1, 0.5); return g_v5 == 1.5 ? 0 : 1; }'),
    ('v6', '?e@@YAXHH@Z', '?e@@YAXHZZ', 'manual',      # the va_list goes on to another function
     'extern "C" int g_v6; int g_v6; extern "C" int sink6(int, __builtin_va_list);'
     ' extern "C" void def_v6(int n, ...) { __builtin_va_list a; __builtin_va_start(a, n); g_v6 = sink6(n, a);'
     ' __builtin_va_end(a); }', ''),
]


# fixed references into a definition that hands its va_list on (vsnprintf-like): the call sites' format literals
# decide (vacall.decide_format). mini() reads the arguments the way vsnprintf would and sums their bits.
FMT_LIB = r'''
extern "C" unsigned long long g_fmt; unsigned long long g_fmt;
extern "C" void mini(const char *f, __builtin_va_list a) {
  unsigned long long s = 0;
  for (; *f; f++) {
    if (*f != '%') continue;
    f++;
    if (f[0] == 'l' && f[1] == 'l') { s += (unsigned long long)__builtin_va_arg(a, long long); f += 2; continue; }
    if (*f == 'd') s += (unsigned)__builtin_va_arg(a, int);
    else if (*f == 's') s += (unsigned)(unsigned long)__builtin_va_arg(a, const char *)[0];
    else if (*f == 'f') { double d = __builtin_va_arg(a, double); s += (unsigned long long)(d * 4); }
  }
  g_fmt = s;
}
extern "C" void def_fmt(const char *f, ...) { __builtin_va_list a; __builtin_va_start(a, f); mini(f, a);
  __builtin_va_end(a); }
'''
# (case, reference MSVC (fixed), wanted, C++ check calling br_<case> with the reference's own signature; the check
# wants the x86 reading: the pushed words reinterpreted by the format)
FMT_CASES = [
    ('f1', '?L@@YAXPBD0@Z', 'same',
     'extern "C" void br_f1(const char *, const char *);\n'
     'static int t_f1() { g_fmt = 0; br_f1("%s", "A"); return g_fmt == 65 ? 0 : 1; }'),
    ('f2', '?L@@YAXPBDHH@Z', 'bridge',   # two pushed words read as one %lld (low word first)
     'extern "C" void br_f2(const char *, int, int);\n'
     'static int t_f2() { g_fmt = 0; br_f2("%lld", 7, 1); return g_fmt == 0x100000007ULL ? 0 : 1; }'),
    ('f4', '?L@@YAXPBDHH@Z', 'bridge',   # two pushed words read as one double
     'extern "C" void br_f4(const char *, int, int);\n'
     'static int t_f4() { double d = 2.5; int w[2]; __builtin_memcpy(w, &d, 8); g_fmt = 0;'
     ' br_f4("%f", w[0], w[1]); return g_fmt == 10 ? 0 : 1; }'),
    ('f3', '?L@@YAXPBDH@Z', 'manual',    # a format the call site computes
     'extern "C" void br_f3(const char *, int);\nstatic int t_f3(const char *f) { br_f3(f, 1); return 0; }'),
    ('f5', '?L@@YAXPBDHH@Z', 'manual',   # call sites read the same words differently
     'extern "C" void br_f5(const char *, int, int);\n'
     'static int t_f5() { br_f5("%lld", 1, 2); br_f5("%d %d", 1, 2); return 0; }'),
]


def main():
    fails = 0
    with tempfile.TemporaryDirectory() as t:
        so = vacall.plugin(t)
        if so is None:
            print('SKIP test_vacall: no gcc plugin headers (gcc-13-plugin-dev-aarch64-linux-gnu)')
            return
        src = os.path.join(t, 'calls.cpp')
        open(src, 'w').write(TYPES + '\n'.join(c[3] + '\n' + c[4] for c in CASES) + '\n')
        rec = os.path.join(t, 'calls.tsv')
        subprocess.run([layout.CXX, '-mabi=ilp32', '-fsyntax-only', '-fplugin=' + so,
                        '-fplugin-arg-fnvnx_vacall-out=' + rec, src], check=True)
        calls = {}
        for line in open(rec):
            f = line.rstrip('\n').split('\t')
            calls.setdefault(f[0], set()).add(tuple(f[3 + int(f[2]):]))
        dsrc = os.path.join(t, 'defs.cpp')
        open(dsrc, 'w').write('\n'.join(c[4] for c in DEF_CASES) + '\n')
        drec = os.path.join(t, 'defs.tsv')
        subprocess.run([layout.CXX, '-mabi=ilp32', '-fsyntax-only', '-fplugin=' + so,
                        '-fplugin-arg-fnvnx_vacall-out=' + drec, dsrc], check=True)
        va = {}
        for line in open(drec):
            f = line.rstrip('\n').split('\t')
            if f[0] == 'VADEF':
                va[f[1]] = (f[2], tuple(f[3:])) if f[2] == 'reads' else (f[2],)
        names = ['?p@@YAHZZ'] + [c[1] for c in CASES] + [c[1] for c in DEF_CASES] + [c[2] for c in DEF_CASES]
        dem = fnvlink.batch_demangle(fnvlink.UNDNAME, names)
        ref = sigs.parse_msvc('?p@@YAHZZ', dem['?p@@YAHZZ'])
        asm, defs, checks, runs = ['    .text'], [], [], []
        for case, dm, want, cdef, ccheck in CASES:
            dfn = sigs.parse_msvc(dm, dem[dm])
            v0 = abi.compare(ref, dfn)
            if not vacall.needs(v0):
                print('FAIL %s: the declarations alone gave %s %s' % (case, v0.kind, v0.notes)); fails += 1
                continue
            v, _, seq, st = vacall.decide(ref, dfn, calls.get('br_' + case))
            got = v.kind if v is not None else 'manual'
            if got != want:
                print('FAIL %s: %s (%s), want %s; call sites %s' % (case, got, st, want, calls.get('br_' + case)))
                fails += 1
                continue
            if want == 'manual':
                continue
            if want == 'same':
                asm.append('    .p2align 2\n    .globl br_%s\nbr_%s:\n    b def_%s\n' % (case, case, case))
            elif v.plan is not None:
                asm.append(abi2.emit('br_' + case, 'def_' + case, v.plan, case))
            else:
                asm.append(abi.emit_bridge('br_' + case, 'def_' + case, v, case))
            defs.append(cdef); checks.append(ccheck); runs.append('t_%s()' % case)
        for case, rm, dm, want, cdef, ccheck in DEF_CASES:
            rs, dfn = sigs.parse_msvc(rm, dem[rm]), sigs.parse_msvc(dm, dem[dm])
            reads = va.get('def_' + case)
            v = abi.compare(rs, dfn, va_reads=reads)
            if abi.compare(rs, dfn).kind != 'manual' or v.kind != want:
                print('FAIL %s: %s with %s (want %s) %s' % (case, v.kind, reads, want, v.notes)); fails += 1
                continue
            if want == 'same':
                asm.append('    .p2align 2\n    .globl br_%s\nbr_%s:\n    b def_%s\n' % (case, case, case))
                defs.append(cdef); checks.append(ccheck); runs.append('t_%s()' % case)
        fsrc = os.path.join(t, 'fmt.cpp')
        open(fsrc, 'w').write(FMT_LIB + '\n'.join(c[3] for c in FMT_CASES) + '\n')
        frec = os.path.join(t, 'fmt.tsv')
        fwant = os.path.join(t, 'want.txt')
        open(fwant, 'w').write(''.join('br_%s\n' % c[0] for c in FMT_CASES))
        subprocess.run([layout.CXX, '-mabi=ilp32', '-fsyntax-only', '-fplugin=' + so,
                        '-fplugin-arg-fnvnx_vacall-out=' + frec, '-fplugin-arg-fnvnx_vacall-want=' + fwant, fsrc],
                       check=True)
        fsites, freads = {}, None
        for line in open(frec):
            f = line.rstrip('\n').split('\t')
            if f[0] == 'FMT':
                fsites.setdefault(f[1], []).append({int(x.split(':')[0]): bytes.fromhex(x.split(':')[1])
                                                    for x in f[3:]})
            elif f[0] == 'VADEF' and f[1] == 'def_fmt':
                freads = (f[2],)
        fdem = fnvlink.batch_demangle(fnvlink.UNDNAME, ['?L@@YAXPBDZZ'] + [c[1] for c in FMT_CASES])
        fdef = sigs.parse_msvc('?L@@YAXPBDZZ', fdem['?L@@YAXPBDZZ'])
        if freads != ('escape',):
            print('FAIL fmt: the plugin says the definition is %s, want escape' % (freads,)); fails += 1
        fmt_defs = False
        for case, rm, want, ccheck in FMT_CASES:
            rs = sigs.parse_msvc(rm, fdem[rm])
            if abi.compare(rs, fdef, va_reads=freads).kind != 'manual':
                print('FAIL %s: decided without the formats' % case); fails += 1
                continue
            v, _, st = vacall.decide_format(rs, fdef, fsites.get('br_' + case))
            got = v.kind if v is not None else 'manual'
            if got != want:
                print('FAIL %s: %s (%s), want %s; sites %s' % (case, got, st, want, fsites.get('br_' + case)))
                fails += 1
                continue
            if want == 'manual':
                continue
            if want == 'same':
                asm.append('    .p2align 2\n    .globl br_%s\nbr_%s:\n    b def_fmt\n' % (case, case))
            elif v.plan is not None:
                asm.append(abi2.emit('br_' + case, 'def_fmt', v.plan, case))
            else:
                asm.append(abi.emit_bridge('br_' + case, 'def_fmt', v, case))
            if not fmt_defs:
                defs.append(FMT_LIB); fmt_defs = True
            checks.append(ccheck); runs.append('t_%s()' % case)
        fails += test_bridges.execute(os.path.join(t, 'run'), TYPES, defs, checks, runs, asm) \
            if not os.makedirs(os.path.join(t, 'run')) else 0
    print('ok' if not fails else 'FAILED')
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
