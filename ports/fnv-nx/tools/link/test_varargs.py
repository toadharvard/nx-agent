#!/usr/bin/env python3
"""Variadic pairs (abi.varargs_check, stream S2c-abi): verdicts on hand-written declarations, and execution of the
pairs that need no bridge or a generated one under qemu-aarch64 (no decomp content).

    python3 tools/link/test_varargs.py     # needs aarch64-linux-gnu-{g++,as,ld,objcopy}, qemu-aarch64, llvm-undname

A `same` pair is called through a plain branch to the definition, a `bridge` pair through abi.emit_bridge: the
caller uses the reference's signature, the definition its own (with va_arg where it is variadic), so the run shows
that AArch64 Linux passes anonymous arguments where the other side expects them.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import abi           # noqa: E402
import fnvlink       # noqa: E402
import sigs          # noqa: E402
import test_bridges  # noqa: E402

TYPES = r'''
typedef __builtin_va_list va_list;
#define va_start(a, l) __builtin_va_start(a, l)
#define va_arg(a, t) __builtin_va_arg(a, t)
#define va_end(a) __builtin_va_end(a)
struct Foo { int k; };
'''

# (name, ref MSVC, def MSVC, expected verdict, C++ definition `def_<name>`, C++ check calling `br_<name>`)
CASES = [
    # the retail exe's debug loggers are `return 0`: the reference's arguments are never read
    ('logger', '?Warning@@YAHPBDZZ', '?f@@YAHXZ', 'same',
     'extern "C" int def_logger() { return 0; }',
     'extern "C" int br_logger(const char *, ...);\n'
     'static int t_logger() { return br_logger("x %d %f", 5, 1.5) == 0 ? 0 : 1; }'),
    # fixed reference, variadic definition: va_arg reads the reference's third argument
    ('va_def', '?ap@@YAXHHPAM@Z', '?ap@@YAXHPAUFoo@@ZZ', 'same',
     'extern "C" void def_va_def(int n, Foo *f, ...) { va_list a; va_start(a, f); float *p = va_arg(a, float *);'
     ' *p = f->k + n; va_end(a); }',
     'extern "C" void br_va_def(int, Foo *, float *);\n'
     'static int t_va_def() { Foo f{40}; float r = 0; br_va_def(2, &f, &r); return r == 42.0f ? 0 : 1; }'),
    # ten fixed ints against (int, ...): the last two travel on the stack on both sides
    ('va_stack', '?t@@YAXHHHHHHHHHH@Z', '?t@@YAXHZZ', 'same',
     'extern "C" int g_t; int g_t; extern "C" void def_va_stack(int a0, ...) { va_list a; va_start(a, a0);'
     ' int s = a0; for (int i = 1; i < 10; i++) s += i * va_arg(a, int); va_end(a); g_t = s; }',
     'extern "C" void br_va_stack(int, int, int, int, int, int, int, int, int, int);\n'
     'static int t_va_stack() { br_va_stack(1, 1, 1, 1, 1, 1, 1, 1, 1, 2); return g_t == 1 + 36 + 18 ? 0 : 1; }'),
    # doubles and 64-bit integers in the tail
    ('va_wide', '?u@@YAXHN_J@Z', '?u@@YAXHZZ', 'same',
     'extern "C" double g_u; double g_u; extern "C" void def_va_wide(int a0, ...) { va_list a; va_start(a, a0);'
     ' double d = va_arg(a, double); long long q = va_arg(a, long long); va_end(a); g_u = a0 + d + (double)q; }',
     'extern "C" void br_va_wide(int, double, long long);\n'
     'static int t_va_wide() { br_va_wide(1, 0.5, 1LL << 40); return g_u == 1.5 + 1099511627776.0 ? 0 : 1; }'),
    # both variadic, a named bool against an int: extended in place, the anonymous part passes through
    ('va_both', '?g@@YAHH_NPBDZZ', '?g@@YAHHHPBDZZ', 'bridge',
     'extern "C" int def_va_both(int a0, int b, const char *s, ...) { va_list a; va_start(a, s);'
     ' int x = va_arg(a, int); va_end(a); return b * 100 + x + (s[0] == \'k\'); }',
     'extern "C" int br_va_both(int, int, const char *, ...);   // the bool travels with junk above bit 7\n'
     'static int t_va_both() { return br_va_both(0, 0x7701, "k", 20) == 121 ? 0 : 1; }'),
    # a short read as the int va_arg returns: sign-extended in place
    ('va_narrow', '?h@@YAXHF@Z', '?h@@YAXHZZ', 'bridge',
     'extern "C" int g_h; int g_h; extern "C" void def_va_narrow(int a0, ...) { va_list a; va_start(a, a0);'
     ' g_h = a0 + va_arg(a, int); va_end(a); }',
     'extern "C" void br_va_narrow(int, int);   // the short travels with junk above bit 15\n'
     'static int t_va_narrow() { br_va_narrow(10, 0x5ffff); return g_h == 9 ? 0 : 1; }'),
    # the decomp's unprototyped declaration `int f(...)` against a fixed definition: integers and pointers
    ('unproto', '?p@@YAHZZ', '?p@@YAXPBDH@Z', 'same',
     'extern "C" int g_p; int g_p; extern "C" void def_unproto(const char *s, int n) { g_p = (s[0] == \'k\') + n; }',
     'extern "C" int br_unproto(...);\n'
     'static int t_unproto() { br_unproto("k", 41); return g_p == 42 ? 0 : 1; }'),
    # both variadic, the definition names two of the reference's anonymous arguments
    ('va_more', '?q@@YAHPADZZ', '?q@@YAHPADHPBDZZ', 'same',
     'extern "C" int def_va_more(char *d, int n, const char *f, ...) { va_list a; va_start(a, f);'
     ' int x = va_arg(a, int); va_end(a); d[0] = f[0]; return n + x; }',
     'extern "C" int br_va_more(char *, ...);\n'
     'static int t_va_more() { char b[2] = {0, 0}; return br_va_more(b, 40, "z", 2) == 42 && b[0] == \'z\' ? 0 : 1; }'),
    # not bridgeable without the call site's types or another convention
    ('unproto_double', '?p2@@YAHZZ', '?p2@@YAXN@Z', 'manual', '', ''),
    ('va_float', '?k@@YAXHM@Z', '?k@@YAXHZZ', 'manual', '', ''),
    ('va_beyond', '?d@@YAXPBDZZ', '?d@@YAXPBD0@Z', 'manual', '', ''),
    ('va_stdcall', '?s@@YGXH@Z', '?s@@YAXHZZ', 'manual', '', ''),
]


def main():
    fails = 0
    names = [c[1] for c in CASES] + [c[2] for c in CASES]
    dem = fnvlink.batch_demangle(fnvlink.UNDNAME, names)
    asm, defs, checks, calls = ['    .text'], [], [], []
    for name, rm, dm, want, cdef, ccheck in CASES:
        rs, ds = sigs.parse_msvc(rm, dem[rm]), sigs.parse_msvc(dm, dem[dm])
        v = abi.compare(rs, ds)
        if v.kind != want:
            print('FAIL %s: verdict %s (want %s) %s %s' % (name, v.kind, want, v.classes, v.notes))
            fails += 1
            continue
        if want == 'same':
            asm.append('    .p2align 2\n    .globl br_%s\nbr_%s:\n    b def_%s\n' % (name, name, name))
        elif want == 'bridge':
            asm.append(abi.emit_bridge('br_' + name, 'def_' + name, v, name))
        else:
            continue
        defs.append(cdef); checks.append(ccheck); calls.append('t_%s()' % name)
    with tempfile.TemporaryDirectory() as tree:
        fails += test_bridges.execute(tree, TYPES, defs, checks, calls, asm)
    print('ok' if not fails else 'FAILED')
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
