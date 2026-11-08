#!/usr/bin/env python3
"""Execution test of generated ABI bridges (abi2) on hand-written declarations (no decomp content).

    python3 tools/link/test_bridges.py     # needs aarch64-linux-gnu-{g++,as,ld,objcopy}, qemu-aarch64, llvm-undname

Each case is a pair of MSVC declarations (reference, definition) plus a C++ definition with the definition's
AArch64 signature and a caller that calls the bridge with the reference's signature. Layouts come from the real
probe (layout.py) on the test unit; abi2 decides and emits the bridges; everything is linked freestanding for ILP32
and run under qemu-aarch64 (the ILP32 image is wrapped into an ELF64 container at the same addresses, since qemu
user mode has no ILP32 loader). Exit status of the program = number of failed checks.
"""
import copy
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
struct F4 { float a, b, c, d; };
struct S8 { int lo, hi; };
struct B1 { unsigned char ok; };
struct B2 { unsigned char ok; };
enum Mode { MODE_A, MODE_B = 7 };
typedef struct { int a, b, c, d; } QI;
namespace ns1 { struct W3 { float x, y, z; }; }
struct Hidden { int a, b; Hidden(); ~Hidden(); };
int Hidden(int);
namespace ns2 { struct Outer { enum Bits { B0, B1 }; }; template <class T> struct Ref { T *p; }; struct Si { int x; }; }
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
    # a __restrict pointer is a pointer (it was an unknown type: the pair stayed manual, 0x00a6dbd0 Negate)
    ('restrict_out', '?n@A@@QBE?AUQ@@XZ', '?n@B@@QAEXPIAH@Z', 'bridge',
     'extern "C" void def_restrict_out(Foo *self, int *__restrict o) { o[0] = -self->k; o[1] = 2; o[2] = 3; o[3] = 4; }',
     'extern "C" Q br_restrict_out(Foo *self);\n'
     'static int t_restrict_out() { Foo f{9}; Q q = br_restrict_out(&f);'
     ' return (q.a == -9) + (q.b == 2) + (q.d == 4) - 3; }'),
    ('ptr_vs_holder', '?d@@YAHPAUFoo@@@Z', '?d@@YAHUHolder@@@Z', 'bridge',
     'extern "C" int def_ptr_vs_holder(Holder h) { return h.p->k; }',
     'extern "C" int br_ptr_vs_holder(Foo *f);\n'
     'static int t_ptr_vs_holder() { Foo f{77}; return (br_ptr_vs_holder(&f) == 77) - 1; }'),
    # D24 option (a): the abi-manual classes bind through bridges
    # free-cdecl->static-member-cdecl,sret-explicit: a free function's hidden result (x8) against a static member's
    # explicit out pointer (x0), the int moves from x0 to x1, the definition's returned pointer is dropped
    ('free_vs_static_out', '?f@@YA?AUBig@@H@Z', '?f@A@@SAPAUBig@@PAU2@H@Z', 'bridge',
     'extern "C" Big *def_free_vs_static_out(Big *o, int n) { for (int i = 0; i < 8; i++) o->a[i] = n + i; return o; }',
     'extern "C" Big br_free_vs_static_out(int n);\n'
     'static int t_free_vs_static_out() { Big b = br_free_vs_static_out(40);'
     ' return (b.a[0] == 40) + (b.a[7] == 47) - 2; }'),
    # ret-aggregate: a 16-byte struct of ints (x0:x1) read from a definition returning four floats (v0-v3); x86
    # passed both through a buffer, so the bytes are the floats' bits
    ('ret_g2_vs_hfa', '?q@@YA?AUQ@@XZ', '?q@@YA?AUF4@@XZ', 'bridge',
     'extern "C" F4 def_ret_g2_vs_hfa() { F4 f; f.a = 1.0f; f.b = 2.0f; f.c = -1.0f; f.d = 0.5f; return f; }',
     'extern "C" Q br_ret_g2_vs_hfa();\n'
     'static int t_ret_g2_vs_hfa() { Q q = br_ret_g2_vs_hfa(); return (q.a == 0x3f800000) + (q.b == 0x40000000)'
     ' + (q.c == (int)0xbf800000) + (q.d == 0x3f000000) - 4; }'),
    # aggregate-param: an 8-byte struct by value (x0) against its two words (x0, x1)
    ('s8_vs_ints', '?e@@YAHUS8@@@Z', '?e@@YAHHH@Z', 'bridge',
     'extern "C" int def_s8_vs_ints(int lo, int hi) { return lo * 10 + hi; }',
     'extern "C" int br_s8_vs_ints(S8 s);\n'
     'static int t_s8_vs_ints() { return (br_s8_vs_ints(S8{4, 2}) == 42) - 1; }'),
    # varargs-tail,ret-aggregate: a variadic definition's 1-byte POD result against another 1-byte POD: the named
    # part and the result agree, nothing to bridge (the anonymous arguments arrive where named ones would)
    ('va_ret_pod', '?v@@YA?AUB1@@PAH@Z', '?v@@YA?AUB2@@PAHZZ', 'same', '', ''),
    # a variadic definition whose named part needs a bridge stays manual (the bridge would move the anonymous ones)
    ('va_needs_bridge', '?x@@YAXUQ@@@Z', '?x@@YAXHHHHZZ', None, '', ''),
    # a void reference against a member returning an aggregate through MSVC's hidden buffer (R2 InsertAt): the
    # arguments match without the buffer; a register result (S8, x0) is not read, a memory one (Big, x8) gets a
    # scratch buffer in the bridge's frame
    ('void_vs_hidden_regs', '?ins@L@@QAEXUS8@@ABH@Z', '?ins@V@@QAE?AUS8@@US8@@ABH@Z', 'same', '', ''),
    # the same for a free cdecl pair (no pop check): read with the buffer, the S8 would be split and the int
    # reference missing
    ('void_vs_hidden_free', '?inf@@YAXUS8@@ABH@Z', '?inf@@YA?AUBig@@US8@@ABH@Z', 'bridge',
     'extern "C" int g_inf; int g_inf; extern "C" Big def_void_vs_hidden_free(S8 it, const int &v) '
     '{ Big b; for (int i = 0; i < 8; i++) b.a[i] = -1; g_inf = it.lo * 100 + it.hi * 10 + v; return b; }',
     'extern "C" void br_void_vs_hidden_free(S8 it, const int &v);\n'
     'static int t_void_vs_hidden_free() { int v = 9; br_void_vs_hidden_free(S8{5, 6}, v); return (g_inf == 569) - 1; }'),
    # a void reference that passes the buffer itself (sret-explicit): the explicit reading is consistent and wins
    ('void_explicit_out', '?xo@@YAXPAUBig@@H@Z', '?xo@@YA?AUBig@@H@Z', 'bridge',
     'extern "C" Big def_void_explicit_out(int n) { Big b; for (int i = 0; i < 8; i++) b.a[i] = n * i; return b; }',
     'extern "C" void br_void_explicit_out(Big *o, int n);\n'
     'static int t_void_explicit_out() { Big b; br_void_explicit_out(&b, 3); return (b.a[1] == 3) + (b.a[7] == 21) - 2; }'),
    ('void_vs_hidden_mem', '?inm@L@@QAEXUS8@@ABH@Z', '?inm@V@@QAE?AUBig@@US8@@ABH@Z', 'bridge',
     'extern "C" int g_inm; int g_inm; extern "C" Big def_void_vs_hidden_mem(Foo *self, S8 it, const int &v) '
     '{ Big b; for (int i = 0; i < 8; i++) b.a[i] = -1; g_inm = self->k * 1000 + it.lo * 100 + it.hi * 10 + v;'
     ' return b; }',
     'extern "C" void br_void_vs_hidden_mem(Foo *self, S8 it, const int &v);\n'
     'static int t_void_vs_hidden_mem() { Foo f{3}; int v = 4; volatile int guard[4] = {7, 7, 7, 7};'
     ' br_void_vs_hidden_mem(&f, S8{1, 2}, v);'
     ' return (g_inm == 3124) + (guard[0] == 7) + (guard[3] == 7) - 3; }'),
]

# untagged named parameter types (a C name's declaration read from source: a typedef or an enum the reader did not
# expand) - (name, ref MSVC, def MSVC, def parameter spellings replacing the definition's, expected verdict, C++ def,
# C++ check); the probe measures them like aggregates
UNTAGGED = [
    ('enum_param', '?w@@YAXHH@Z', '?w@@YAXHH@Z', ['int', 'Mode'], 'same', '', ''),
    ('ints_vs_qi', '?c@@YAXHHHH@Z', '?c@@YAXHHHH@Z', ['QI'], 'bridge',
     'extern "C" int g_qi; int g_qi; extern "C" void def_ints_vs_qi(QI q) { g_qi = q.a * 1000 + q.b * 100 + q.c * 10'
     ' + q.d; }',
     'extern "C" void br_ints_vs_qi(int a, int b, int c, int d);\n'
     'static int t_ints_vs_qi() { br_ints_vs_qi(5, 6, 7, 8); return (g_qi == 5678) - 1; }'),
    # fastcall: an enum would go in ecx/edx, a struct on the stack - not known from the spelling
    ('fastcall_untagged', '?s@@YIXHH@Z', '?s@@YIXHH@Z', ['Mode', 'int'], None, '', ''),
    # a bare name the probe cannot measure, while the unit has one `ns1::W3`: abi2.lookup takes it
    ('bare_ns_name', '?k@@YAXMMM@Z', '?k@@YAXMMM@Z', ['W3'], 'same', '', ''),
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
    names = [c[1] for c in CASES + UNTAGGED] + [c[2] for c in CASES + UNTAGGED]
    dem = fnvlink.batch_demangle(fnvlink.UNDNAME, names)
    with tempfile.TemporaryDirectory() as tree:
        os.makedirs(os.path.join(tree, 'src')); os.makedirs(os.path.join(tree, 'include'))
        open(os.path.join(tree, 'src', 'types.cpp'), 'w').write(TYPES)
        lay = layout.probe_unit(tree, tree, 'src/types.cpp', ['Vec3', 'Big', 'Rgba', 'Q', 'P2', 'NiPoint2', 'V2', 'I2', 'F2',
                                                              'Holder', 'F4', 'S8', 'B1', 'B2', 'Mode', 'QI', 'W3',
                                                              'ns1::W3', 'Hidden', 'Outer::Bits', 'Ref<Si>', 'Nope'])
        # names a C declaration's spelling leaves to the probe: the one namespace GCC names for a bare name or its
        # first component or template arguments, `struct ::T` for a class a function of the same name hides
        for t, want in (('W3', (12, 'H3x4')), ('Hidden', (8, 'IND')), ('Outer::Bits', (4, 'G1')),
                        ('Ref<Si>', (4, 'G1')), ('Nope', None)):
            got = lay.get(t) and (lay[t].size, lay[t].cls)
            if got != want:
                print('FAIL probe %s: %s (want %s)' % (t, got, want)); fails += 1
        look = abi2.lookup({('u', t): l for t, l in lay.items() if l is not None})('u')
        asm, defs, checks, calls = ['    .text'], [], [], []
        cases = [(n, rm, dm, None, w, cd, cc) for n, rm, dm, w, cd, cc in CASES] + UNTAGGED
        for name, rm, dm, dparams, want, cdef, ccheck in cases:
            rs, ds = sigs.parse_msvc(rm, dem[rm]), sigs.parse_msvc(dm, dem[dm])
            if dparams is not None:
                ds = copy.copy(ds)
                ds.raw_params = ds.params = list(dparams)
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
