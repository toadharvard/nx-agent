#!/usr/bin/env python3
"""Execution test of generated ABI bridges (abi2) on hand-written declarations (no decomp content).

    python3 tools/link/test_bridges.py     # needs aarch64-linux-gnu-{g++,as,ld,objcopy}, qemu-aarch64, llvm-undname

Each case is a pair of MSVC declarations (reference, definition) plus a C++ definition with the definition's
AArch64 signature and a caller that calls the bridge with the reference's signature. Layouts come from the real
probe (layout.py) on the test unit; abi2 decides and emits the bridges; everything is linked freestanding for ILP32
and run under qemu-aarch64 (the ILP32 image is wrapped into an ELF64 container at the same addresses, since qemu
user mode has no ILP32 loader). Exit status of the program = number of failed checks.
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import abi      # noqa: E402
import abi2     # noqa: E402
import fnvlink  # noqa: E402
import layout   # noqa: E402
import sigs     # noqa: E402

TYPES = r'''
struct Vec3 { float x, y, z; };
struct Big { int a[8]; };
struct Rgba { unsigned char r, g, b, a; };
struct Q { int a, b, c, d; };
struct P2 { float x, y; };
struct NiPoint2 { float x, y; };
struct V2 { float x, y; };
struct Foo { int k; };
struct I2 { int a, b; };
struct F2 { float x, y; };
struct Holder { Foo *p; ~Holder(); };
Holder::~Holder() {}
'''

# (name, ref MSVC, def MSVC, expected verdict, C++ definition, C++ check calling bridge `br_<name>`)
CASES = [
    ('hidden_vs_out', '?Get@Foo@@QAE?AUVec3@@M@Z', '?Get@Foo@@QAEPAUVec3@@PAU2@M@Z', 'bridge',
     'extern "C" Vec3 *def_hidden_vs_out(Foo *self, Vec3 *o, float s) '
     '{ o->x = self->k * s; o->y = 2 * s; o->z = 3 * s; return o; }',
     'extern "C" Vec3 br_hidden_vs_out(Foo *self, float s);\n'
     'static int t_hidden_vs_out() { Foo f{5}; Vec3 v = br_hidden_vs_out(&f, 1.5f);'
     ' return (v.x == 7.5f) + (v.y == 3.0f) + (v.z == 4.5f) - 3; }'),
    ('out_vs_hidden', '?Get@Foo@@QAEPAUBig@@PAU2@H@Z', '?Get@Foo@@QAE?AUBig@@H@Z', 'bridge',
     'extern "C" Big def_out_vs_hidden(Foo *self, int n) { Big b; for (int i = 0; i < 8; i++) b.a[i] = self->k + n * i;'
     ' return b; }',
     'extern "C" Big *br_out_vs_hidden(Foo *self, Big *o, int n);\n'
     'static int t_out_vs_hidden() { Foo f{1}; Big b; Big *r = br_out_vs_hidden(&f, &b, 10);'
     ' return (r == &b) + (b.a[0] == 1) + (b.a[7] == 71) - 3; }'),
    ('agg_vs_int', '?f@@YAXIURgba@@@Z', '?f@@YAXHH@Z', 'same', '', ''),
    ('q_vs_ints', '?g@@YAXUQ@@@Z', '?g@@YAXHHHH@Z', 'bridge',
     'extern "C" int g_sum; int g_sum; extern "C" void def_q_vs_ints(int a, int b, int c, int d) '
     '{ g_sum = a * 1000 + b * 100 + c * 10 + d; }',
     'extern "C" void br_q_vs_ints(Q q);\n'
     'static int t_q_vs_ints() { br_q_vs_ints(Q{1, 2, 3, 4}); return (g_sum == 1234) - 1; }'),
    ('renamed_agg', '?h@A@@QAE?AUP2@@XZ', '?h@B@@QAE?AUNiPoint2@@XZ', 'same', '', ''),
    ('hfa_vs_floats', '?k@@YAXUV2@@@Z', '?k@@YAXMM@Z', 'same', '', ''),
    ('edx_stack', '?s@@YIXPAVFoo@@HHHHHHHHH@Z', '?s@Foo@@QAEXHHHHHHHH@Z', 'bridge',
     'extern "C" int g_s; int g_s; extern "C" void def_edx_stack(Foo *self, int a, int b, int c, int d, int e, int f, '
     'int g, int h) { g_s = self->k + a + 2 * b + 3 * c + 4 * d + 5 * e + 6 * f + 7 * g + 8 * h; }',
     'extern "C" void br_edx_stack(Foo *self, int junk, int a, int b, int c, int d, int e, int f, int g, int h);\n'
     'static int t_edx_stack() { Foo f{100}; br_edx_stack(&f, 99, 1, 1, 1, 1, 1, 1, 1, 2);'
     ' return (g_s == 100 + 28 + 16) - 1; }'),
    ('ret_regs', '?r@A@@QAE?AUI2@@XZ', '?r@B@@QAE?AUF2@@XZ', 'bridge',
     'extern "C" F2 def_ret_regs(Foo *self) { F2 f; f.x = 1.0f; f.y = -2.0f; return f; }',
     'extern "C" I2 br_ret_regs(Foo *self);\n'
     'static int t_ret_regs() { Foo f{0}; I2 i = br_ret_regs(&f); return (i.a == 0x3f800000) + (i.b == (int)0xc0000000) - 2; }'),
    ('ind_vs_ints', '?b@@YAXUBig@@@Z', '?b@@YAXHHHHHHHH@Z', 'bridge',
     'extern "C" int g_b; int g_b; extern "C" void def_ind_vs_ints(int a, int b, int c, int d, int e, int f, int g, '
     'int h) { g_b = a + 2 * b + 3 * c + 4 * d + 5 * e + 6 * f + 7 * g + 8 * h; }',
     'extern "C" void br_ind_vs_ints(Big b);\n'
     'static int t_ind_vs_ints() { Big b; for (int i = 0; i < 8; i++) b.a[i] = i + 1; br_ind_vs_ints(b);'
     ' return (g_b == 204) - 1; }'),
    ('ints_vs_q', '?c@@YAXHHHH@Z', '?c@@YAXUQ@@@Z', 'bridge',
     'extern "C" int g_c; int g_c; extern "C" void def_ints_vs_q(Q q) { g_c = q.a * 1000 + q.b * 100 + q.c * 10 + q.d; }',
     'extern "C" void br_ints_vs_q(int a, int b, int c, int d);\n'
     'static int t_ints_vs_q() { br_ints_vs_q(4, 3, 2, 1); return (g_c == 4321) - 1; }'),
    ('ptr_vs_holder', '?d@@YAHPAUFoo@@@Z', '?d@@YAHUHolder@@@Z', 'bridge',
     'extern "C" int def_ptr_vs_holder(Holder h) { return h.p->k; }',
     'extern "C" int br_ptr_vs_holder(Foo *f);\n'
     'static int t_ptr_vs_holder() { Foo f{77}; return (br_ptr_vs_holder(&f) == 77) - 1; }'),
]

RUNTIME = r'''
extern "C" void *memcpy(void *d, const void *s, unsigned long n)
{ char *a = (char *)d; const char *b = (const char *)s; while (n--) *a++ = *b++; return d; }
extern "C" void *memset(void *d, int c, unsigned long n) { char *a = (char *)d; while (n--) *a++ = c; return d; }
extern "C" int main_();
asm(".globl _start\n_start:\n adrp x9, stack_top\n add x9, x9, :lo12:stack_top\n mov sp, x9\n bl main_\n"
    " mov x8, #93\n svc #0\n");
'''


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        raise SystemExit('FAILED: %s\n%s%s' % (' '.join(cmd), r.stdout, r.stderr))
    return r


def execute(tree, types, defs, checks, calls, asm):
    """Compile the definitions and checks, assemble the bridges, link freestanding ILP32 and run under qemu-aarch64:
    -> number of failures (0 or 1)."""
    if not calls:
        return 0
    fails = 0
    src = os.path.join(tree, 't.cpp')
    open(src, 'w').write(types + RUNTIME + '\n'.join(defs) + '\n' + '\n'.join(checks) +
                         '\nextern "C" int main_() { int bad = 0; %s return bad; }\n'
                         % ''.join('bad += %s != 0;' % c for c in calls) +
                         'extern "C" { __attribute__((aligned(16))) char stack[65536]; }\n'
                         'asm(".globl stack_top\\n.set stack_top, stack + 65536\\n");\n')
    open(os.path.join(tree, 'b.S'), 'w').write('\n'.join(asm))
    cxx = [layout.CXX, '-mabi=ilp32', '-O1', '-ffreestanding', '-fno-exceptions', '-fno-rtti', '-fno-builtin',
           '-c']
    run(cxx + [src, '-o', os.path.join(tree, 't.o')])
    run([fnvlink.AS, '-mabi=ilp32', os.path.join(tree, 'b.S'), '-o', os.path.join(tree, 'b.o')])
    exe32 = os.path.join(tree, 't32')
    run([fnvlink.LD, '-m', 'aarch64linux32', '-N', '-static', '--build-id=none', '-Ttext=0x400000', '-e', '_start',
         os.path.join(tree, 't.o'), os.path.join(tree, 'b.o'), '-o', exe32])
    flat = os.path.join(tree, 'flat.bin')
    run([fnvlink.OBJCOPY, '-O', 'binary', '--remove-section=.comment', exe32, flat])
    entry = int(next(l.split()[0] for l in run(['aarch64-linux-gnu-nm', exe32]).stdout.splitlines()
                     if l.endswith(' _start')), 16)
    open(os.path.join(tree, 'w.S'), 'w').write(
        '    .section .img, "awx"\n    .incbin "%s"\n    .space 0x40000\n' % flat)
    run([fnvlink.AS, os.path.join(tree, 'w.S'), '-o', os.path.join(tree, 'w.o')])
    exe64 = os.path.join(tree, 't64')
    run([fnvlink.LD, '-m', 'aarch64linux', '-static', '-z', 'max-page-size=4096', '--section-start=.img=0x400000',
         '-e', '0x%x' % entry, os.path.join(tree, 'w.o'), '-o', exe64])
    r = subprocess.run(['qemu-aarch64', exe64], capture_output=True, text=True)
    if r.returncode:
        print('FAIL: %d bridge execution checks failed (exit %d) %s' % (r.returncode, r.returncode, r.stderr))
        fails += 1
    else:
        print('bridges executed: %d' % len(calls))
    return fails


def main():
    fails = 0
    names = [c[1] for c in CASES] + [c[2] for c in CASES]
    dem = fnvlink.batch_demangle(fnvlink.UNDNAME, names)
    with tempfile.TemporaryDirectory() as tree:
        os.makedirs(os.path.join(tree, 'src')); os.makedirs(os.path.join(tree, 'include'))
        open(os.path.join(tree, 'src', 'types.cpp'), 'w').write(TYPES)
        lay = layout.probe_unit(tree, tree, 'src/types.cpp', ['Vec3', 'Big', 'Rgba', 'Q', 'P2', 'NiPoint2', 'V2', 'I2', 'F2',
                                                              'Holder'])
        look = lambda t: lay.get(t)  # noqa: E731
        asm, defs, checks, calls = ['    .text'], [], [], []
        for name, rm, dm, want, cdef, ccheck in CASES:
            rs, ds = sigs.parse_msvc(rm, dem[rm]), sigs.parse_msvc(dm, dem[dm])
            p = abi2.compare(rs, ds, look, look)
            got = p.kind if p else None
            if os.environ.get('FNVNX_TEST_DEBUG'):
                print(name, got, p and p.classes, p and p.pieces, p and (p.rch, p.dch))
            if got != want:
                print('FAIL %s: verdict %s (want %s) %s %s' % (name, got, want, p and p.classes, p and p.notes))
                fails += 1
                continue
            if want != 'bridge':
                continue
            code = abi2.emit('br_' + name, 'def_' + name, p, name)
            if code is None:
                print('FAIL %s: no bridge' % name); fails += 1; continue
            asm.append(code); defs.append(cdef); checks.append(ccheck); calls.append('t_%s()' % name)
        fails += execute(tree, TYPES, defs, checks, calls, asm)
    print('ok' if not fails else 'FAILED')
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
