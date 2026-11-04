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
        names = ['?p@@YAHZZ'] + [c[1] for c in CASES]
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
        fails += test_bridges.execute(os.path.join(t, 'run'), TYPES, defs, checks, runs, asm) \
            if not os.makedirs(os.path.join(t, 'run')) else 0
    print('ok' if not fails else 'FAILED')
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
