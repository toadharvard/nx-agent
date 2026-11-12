#!/usr/bin/env python3
"""DISPATCH1 runtime test: native indirect calls through fnvnx_dispatch (rt/dispatch.c), native bodies and bridges into lifted guest code.
    disptest.py [--work DIR] [--cc DIR_OF_COMPILER_BIN]
The pieces under test are the product's own files, compiled and linked as they are: rt/dispatch.c, rt/rt.c, rt/enter.cc (+ seh, unhandled, rcptab, x87, imp.cpp of
the import re, table), toolchain/dispatch/fnvnx_dispatch.cc and toolchain/names/fnvnx_names.cc (the compiler plugins that rewrite the indirect calls of the native test
code), tools/bridgegen.py (the bridges and their table, from the descriptors in the test object's own `.fnvnx.disp`), tools/dispgen.py (the tables).  The test
defines no symbol of rt/ or of the generated bridges (checked below: defined(test) ∩ defined(product objects) = {}); it defines only the host boundary
that the platform provides (the thread-local slot, the tick clock, memcpy/memset, the import natives `tc_*` that stand for native product code).
Guest code: hand-assembled x86 functions at 0x401000.. lifted by tools/fnvlift, called by native code through function pointers whose value is the exe address:
each case is a native function (`tc_*`, an import of the guest root) that calls `fp(args)` through a pointer type with the call's own convention; the plugin turns the
call into the cache + fnvnx_dispatch; the bridge marshals the arguments into the x86 frame; the result comes back in eax / edx:eax / ST0 or the hidden pointer.
Expected values are computed by hand from the x86 semantics (stated at each case).  Run under Unicorn.  Prints one line per case; exit status 1 on any difference."""
import argparse, os, re, struct, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(HERE); FNV = os.path.dirname(LIFT)
for p in ('bench', 'tools', 'test', ''): sys.path.insert(0, os.path.join(LIFT, p))
import fnvlift as L, instest, phf, impgen, dispgen, bridgegen
from keystone import Ks, KS_ARCH_X86, KS_MODE_32
from unicorn import Uc, UcError, UC_ARCH_ARM64, UC_MODE_ARM
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X30, UC_ARM64_REG_SP, UC_ARM64_REG_CPACR_EL1, UC_ARM64_REG_TPIDR_EL0
from elftools.elf.elffile import ELFFile

BASE, IAT, DATA, GSTACK, HSTACK, STOP, TEXT, TLS = 0x401000, 0x406000, 0x407000, 0x60000000, 0x70000000, 0x7e000000, 0xE0000000, 0x7d000000
NATIVE_ADDR, NOWHERE = 0x403f00, 0x500000       # an exe address a native root defines (and the lifted table holds too); an address no table holds
CASES = ['this', 'fast', 'stdcdecl', 'q', 'fd', 'mem', 'a12', 'a8', 'hfa', 'hfa3', 'hfaret', 'ref', 'native', 'ud',
         'uf', 'u1', 'vec', 'vec2', 'd4', 'al', 'bf', 'f4r', 'd4r', 'ct', 'ur', 'vr', 'fsw', 'frm']
BRIDGE = [('t.dll', 'tc_' + c, 'stdcall', 'i', '', 'tc_' + c, 'sym') for c in CASES]
KEY = {r[1]: 0x00100000 + 16 * i for i, r in enumerate(sorted(BRIDGE, key=lambda r: (r[0], r[1])))}      # the key rule of tools/impgen.py
SLOT = {r[1]: IAT + 4 * i for i, r in enumerate(BRIDGE)}

# guest targets: name -> x86 text.  Frames are the callee's view: [esp] return address, [esp+4].. arguments (MSVC x86).
TARGETS = {
    # this-call: ecx = object {a, b}; one stack argument, popped by the callee: b + arg
    'this': 'mov eax, dword ptr [ecx+4]; add eax, dword ptr [esp+4]; ret 4',
    # __fastcall: ecx, edx = the first two int arguments, the third on the stack, popped
    'fast': 'mov eax, ecx; sub eax, edx; add eax, dword ptr [esp+4]; ret 4',
    # one descriptor, two conventions: stdcall (ret 8) and cdecl (ret), the site and its bridge cannot tell them apart
    'std': 'mov eax, dword ptr [esp+4]; add eax, dword ptr [esp+8]; ret 8',
    'cdecl': 'mov eax, dword ptr [esp+4]; imul eax, dword ptr [esp+8]; ret',
    # 64-bit result in edx:eax
    'q1': 'mov eax, 0x89abcdef; mov edx, 0x01234567; ret',
    # q argument: (a, q) -> q + a, cdecl
    'q2': 'mov eax, dword ptr [esp+8]; mov edx, dword ptr [esp+12]; add eax, dword ptr [esp+4]; adc edx, 0; ret',
    # float and double results in ST0
    'f1': 'fld dword ptr [esp+4]; fadd dword ptr [esp+8]; ret',
    'd1': 'fld qword ptr [esp+4]; fmul qword ptr [esp+12]; ret',
    # result in memory: the hidden pointer is the first stack word: v[i] = arg + i, i = 0..4; returns the pointer
    'mem': 'mov eax, dword ptr [esp+4]; mov ecx, dword ptr [esp+8]; mov dword ptr [eax], ecx; inc ecx; mov dword ptr [eax+4], ecx; inc ecx; mov dword ptr [eax+8], ecx;'
           'inc ecx; mov dword ptr [eax+12], ecx; inc ecx; mov dword ptr [eax+16], ecx; ret',
    # a 12-byte struct result: MSVC returns it through the hidden pointer too (the AAPCS side returns it in x0, x1): {arg, arg+1, arg+2}
    'a12': 'mov eax, dword ptr [esp+4]; mov ecx, dword ptr [esp+8]; mov dword ptr [eax], ecx; inc ecx; mov dword ptr [eax+4], ecx; inc ecx; mov dword ptr [eax+8], ecx; ret',
    # an 8-byte struct result in edx:eax: {arg, 2 * arg}
    'a8': 'mov eax, dword ptr [esp+4]; lea edx, [eax+eax]; ret',
    # HFA arguments: {float, float} by value = two stack words, {double} = two stack words
    'hf': 'fld dword ptr [esp+4]; fadd dword ptr [esp+8]; ret',
    'hd': 'fld qword ptr [esp+4]; fadd st(0), st(0); ret',
    # a {double, double, double} argument: 24 bytes in floating-point registers on the AAPCS side (an HFA of three), 6 stack words on x86: a + b + c
    'hf3': 'fld qword ptr [esp+4]; fadd qword ptr [esp+12]; fadd qword ptr [esp+20]; ret',
    # an HFA {float, float} result: 8 bytes, edx:eax = {1.5f, 2.25f}
    'hfret': 'mov eax, 0x3fc00000; mov edx, 0x40100000; ret',
    # a class argument passed by value on the x86 stack (the AAPCS side passes the address of a copy): a + b + c
    'ref': 'mov eax, dword ptr [esp+4]; add eax, dword ptr [esp+8]; add eax, dword ptr [esp+12]; ret',
    # DISPATCH1 classes of the backend (descriptor letters in parentheses). Arguments are the x86 stack words, results through edx:eax or the hidden pointer.
    # union {float a[2]} (s8hf) and union {float x, y} (s4hf): GCC's AAPCS64 treats a union of floats as an HFA
    'uf': 'fld dword ptr [esp+4]; fadd dword ptr [esp+8]; ret',
    'u1': 'fld dword ptr [esp+4]; fadd st(0), st(0); ret',
    # struct {float x4 vector} (s16hv16) and struct {2 x float2 vector} (s16hv8): four floats, summed
    'vec': 'fld dword ptr [esp+4]; fadd dword ptr [esp+8]; fadd dword ptr [esp+12]; fadd dword ptr [esp+16]; ret',
    'vec2': 'fld dword ptr [esp+4]; fadd dword ptr [esp+8]; fadd dword ptr [esp+12]; fadd dword ptr [esp+16]; ret',
    # struct of four doubles (s32hd): summed
    'd4': 'fld qword ptr [esp+4]; fadd qword ptr [esp+12]; fadd qword ptr [esp+20]; fadd qword ptr [esp+28]; ret',
    # alignas(16) {float, float} (s16g): a + b
    'al': 'fld dword ptr [esp+4]; fadd dword ptr [esp+8]; ret',
    # {float; int : 0; float} (s8hf): a + b
    'bf': 'fld dword ptr [esp+4]; fadd dword ptr [esp+8]; ret',
    # results: F4 {1, 2, 3, 4} (a16hf) and HV {1, 2, 3, 4} (a16hv16): 16 bytes, the hidden pointer is the first stack word
    'f4r': 'mov eax, dword ptr [esp+4]; mov dword ptr [eax], 0x3f800000; mov dword ptr [eax+4], 0x40000000; mov dword ptr [eax+8], 0x40400000; mov dword ptr [eax+12], 0x40800000; ret',
    'vr': 'mov eax, dword ptr [esp+4]; mov dword ptr [eax], 0x3f800000; mov dword ptr [eax+4], 0x40000000; mov dword ptr [eax+8], 0x40400000; mov dword ptr [eax+12], 0x40800000; ret',
    # D4 {1.0, 2.0, 3.0, 4.0} (a32hd): 32 bytes through the hidden pointer
    'd4r': 'mov eax, dword ptr [esp+4]; mov dword ptr [eax], 0; mov dword ptr [eax+4], 0x3ff00000; mov dword ptr [eax+8], 0; mov dword ptr [eax+12], 0x40000000;'
           'mov dword ptr [eax+16], 0; mov dword ptr [eax+20], 0x40080000; mov dword ptr [eax+24], 0; mov dword ptr [eax+28], 0x40100000; ret',
    # a class with a constructor, 8 bytes (a8x): the AAPCS side returns it in x0, the x86 callee through the hidden pointer (not a POD): {5, 7}
    'ct': 'mov eax, dword ptr [esp+4]; mov dword ptr [eax], 5; mov dword ptr [eax+4], 7; ret',
    # union {float a[2]} as a result (a8hf): edx:eax = {1.5f, 2.25f}
    'ur': 'mov eax, 0x3fc00000; mov edx, 0x40100000; ret',
    # FP state through a bridge called from a native body (rule R-FP, enter.cc): the callee sees the caller's sticky flags and rounding mode
    'fsw': 'fnstsw ax; movzx eax, ax; ret',
    # 1.0f + 2^-60 as binary64 arithmetic under the host rounding mode, minus 1.0, stored as a float: 0 to nearest, 2^-52 rounding toward +infinity
    'frm': 'fld dword ptr [esp+4]; fadd dword ptr [esp+8]; fld1; fsubp st(1), st(0); fstp dword ptr [esp+12]; mov eax, dword ptr [esp+12]; ret',
    # the same exe address has a lifted body too: native (rule 1) wins
    'native': 'mov eax, 0x2222; ret',
}
ORDER = list(TARGETS)
ADDR = {n: BASE + 0x80 * i for i, n in enumerate(ORDER)}
ADDR['native'] = NATIVE_ADDR
# the #UD root and the handler
HANDLER = BASE + 0x80 * len(ORDER)
ROOTS = HANDLER + 0x100

NATIVE = r'''
typedef unsigned int u32; typedef unsigned long long u64;
#define CC(x) __attribute__((fnvnx_cc(x)))
extern "C" {
struct fnvnx_guest_ret { u32 eax, edx; double st0; };
u32 fnvnx_lift_enter(u32 eip, u32 esp, u32 teb); int fnvnx_lift_imports_init(void);
void fnvnx_guest_current(u32 *esp, u32 *teb);
void *memcpy(void *d, const void *s, unsigned long n) { char *a = (char *)d; const char *b = (const char *)s; while (n--) *a++ = *b++; return d; }
void *memset(void *d, int v, unsigned long n) { char *a = (char *)d; while (n--) *a++ = (char)v; return d; }
u32 test_main(u32 eip, u32 esp, u32 teb, u32 *out)
{
    out[0] = (u32)fnvnx_lift_imports_init();
    out[1] = out[2] = 0;
    return fnvnx_lift_enter(eip, esp, teb);
}
}
struct Obj { u32 a, b; };
struct F2 { float a, b; }; struct D1 { double a; }; struct D3 { double a, b, c; };
struct B20 { u32 v[5]; }; struct S12 { u32 a, b, c; }; struct S8 { u32 a, b; };
struct NT { NT(const NT &o) : a(o.a), b(o.b), c(o.c) {} NT(u32 x, u32 y, u32 z) : a(x), b(y), c(z) {} u32 a, b, c; };
union U1 { float a[2]; }; union U2 { float x, y; };
typedef float v16f __attribute__((vector_size(16))); typedef float v8f __attribute__((vector_size(8)));
struct HV { v16f v; }; struct HW { v8f v, w; }; struct D4 { double a, b, c, d; }; struct F4 { float a, b, c, d; };
struct AL { alignas(16) float a; float b; }; struct BF { float a; int : 0; float b; }; struct CT { CT() : a(0), b(0) {} u32 a, b; };
#define FP(T, A) ((T)(unsigned long)(A))
/* every `tc_*` below is called by the guest root as a stdcall import without arguments; the value it returns is the case's result. The indirect calls are plain
   C++ calls through pointers whose value is an exe address: the plugin (and nothing written here) turns each into the cache + fnvnx_dispatch. */
extern "C" {
u32 tc_this(void) { Obj o = { 10, 20 }; return FP(u32 (CC("this") *)(Obj *, u32), @this@)(&o, 5); }
u32 tc_fast(void) { return FP(u32 (CC("fast") *)(u32, u32, u32), @fast@)(10, 3, 100); }
/* one call site, three targets in turn (the cache switches targets), stdcall and cdecl bodies under one descriptor; the guest esp is the same afterwards */
u32 tc_stdcdecl(void)
{
    u32 t[3] = { @std@, @cdecl@, @std@ }, s = 0, e0, e1, teb;
    fnvnx_guest_current(&e0, &teb);
    for (int i = 0; i < 3; i++) s += FP(u32 (CC("stack") *)(u32, u32), t[i])(3, 4);
    fnvnx_guest_current(&e1, &teb);
    return s + (e0 != e1) * 1000;
}
u32 tc_q(void)
{
    u64 a = FP(u64 (CC("stack") *)(void), @q1@)();
    u64 b = FP(u64 (CC("stack") *)(u32, u64), @q2@)(1, 0x00000001ffffffffull);
    return (((u32)(a >> 32)) ^ (u32)a) + (((u32)(b >> 32)) ^ (u32)b);
}
u32 tc_fd(void)
{
    float f = FP(float (CC("stack") *)(float, float), @f1@)(1.5f, 2.25f);
    double d = FP(double (CC("stack") *)(double, double), @d1@)(1.5, 4.0);
    return (u32)(int)(f * 4) * 100 + (u32)(int)d;
}
u32 tc_mem(void) { B20 r = FP(B20 (CC("stack") *)(u32), @mem@)(7); return r.v[0] + r.v[1] + r.v[2] + r.v[3] + r.v[4]; }
u32 tc_a12(void) { S12 r = FP(S12 (CC("stack") *)(u32), @a12@)(5); return r.a * 10000 + r.b * 100 + r.c; }
u32 tc_a8(void) { S8 r = FP(S8 (CC("stack") *)(u32), @a8@)(9); return r.a + r.b * 1000; }
u32 tc_hfa(void)
{
    F2 x = { 1.5f, 2.5f }; D1 y = { 2.5 };
    float f = FP(float (CC("stack") *)(F2), @hf@)(x);
    double d = FP(double (CC("stack") *)(D1), @hd@)(y);
    return (u32)(int)f * 10 + (u32)(int)d;
}
u32 tc_hfa3(void) { D3 x = { 1.5, 2.0, 3.5 }; return (u32)(int)FP(double (CC("stack") *)(D3), @hf3@)(x); }
u32 tc_hfaret(void) { F2 r = FP(F2 (CC("stack") *)(u32), @hfret@)(0); return (u32)(int)(r.a * 4 + r.b * 100); }
u32 tc_ref(void) { NT n(1, 20, 300); return FP(u32 (CC("stack") *)(NT), @ref@)(n); }
/* rule 1: a native root defines the body of this exe address: it is called, not the lifted body at the same address */
u32 native_body(u32 x) { return x + 0x1100; }
u32 tc_native(void) { return FP(u32 (CC("stack") *)(u32), @native@)(0x11); }
u32 tc_uf(void) { U1 u = { { 1.5f, 2.5f } }; return (u32)(int)(FP(float (CC("stack") *)(U1), @uf@)(u) * 10); }
u32 tc_u1(void) { U2 u; u.x = 3.0f; return (u32)(int)(FP(float (CC("stack") *)(U2), @u1@)(u) * 10); }
u32 tc_vec(void) { HV h = { { 1, 2, 3, 4 } }; return (u32)(int)FP(float (CC("stack") *)(HV), @vec@)(h); }
u32 tc_vec2(void) { HW h = { { 1, 2 }, { 3, 4 } }; return (u32)(int)FP(float (CC("stack") *)(HW), @vec2@)(h); }
u32 tc_d4(void) { D4 d = { 1.5, 2.0, 3.5, 4.0 }; return (u32)(int)FP(double (CC("stack") *)(D4), @d4@)(d); }
u32 tc_al(void) { AL x; x.a = 2.5f; x.b = 4.0f; return (u32)(int)(FP(float (CC("stack") *)(AL), @al@)(x) * 2); }
u32 tc_bf(void) { BF x; x.a = 1.5f; x.b = 2.5f; return (u32)(int)(FP(float (CC("stack") *)(BF), @bf@)(x) * 10); }
u32 tc_f4r(void) { F4 r = FP(F4 (CC("stack") *)(void), @f4r@)(); return (u32)(int)(r.a + r.b * 10 + r.c * 100 + r.d * 1000); }
u32 tc_vr(void) { HV r = FP(HV (CC("stack") *)(void), @vr@)(); return (u32)(int)(r.v[0] + r.v[1] * 10 + r.v[2] * 100 + r.v[3] * 1000); }
u32 tc_d4r(void) { D4 r = FP(D4 (CC("stack") *)(void), @d4r@)(); return (u32)(int)(r.a + r.b * 10 + r.c * 100 + r.d * 1000); }
u32 tc_ct(void) { CT r = FP(CT (CC("stack") *)(void), @ct@)(); return r.a * 100 + r.b; }
u32 tc_ur(void) { U1 r = FP(U1 (CC("stack") *)(void), @ur@)(); return (u32)(int)(r.a[0] * 4 + r.a[1] * 100); }
/* R-FP: a native body is guest code, so a bridge into lifted code leaves FPCR/FPSR as the native code had them. The guest root first makes the x87 family the owner of the live FPSR
   (rt/fnvl_x87.h: a reader adds the live FPSR only while its family is the owner), then IXC set in FPSR by the native code reaches the guest's fnstsw as PE (0x20); RMode RP in FPCR
   makes the guest's binary64 addition round up (the result minus 1.0 is 2^-52, bits 0x25800000; to nearest it would be 0) */
u32 tc_fsw(void)
{
    unsigned long s = 0x10; __asm__ volatile("msr fpsr, %0" :: "r"(s) : "memory");
    u32 r = FP(u32 (CC("stack") *)(void), @fsw@)();
    return r & 0x3f;
}
u32 tc_frm(void)
{
    unsigned long z = 0, rp = 0x400000ul; __asm__ volatile("msr fpcr, %0" :: "r"(rp) : "memory");
    u32 r = FP(u32 (CC("stack") *)(float, float, u32), @frm@)(1.0f, 5.421010862427522e-20f, 0);
    __asm__ volatile("msr fpcr, %0" :: "r"(z) : "memory");
    return r;
}
/* rule 3: an address no table holds raises #UD there, through the guest's exception chain; the guest handler computes code ^ address */
u32 tc_ud(void) { return FP(u32 (CC("stack") *)(void), @nowhere@)(); }
}
'''
for n, a in ADDR.items(): NATIVE = NATIVE.replace('@%s@' % n, '0x%x' % a)
NATIVE = NATIVE.replace('@nowhere@', '0x%x' % NOWHERE)


class Fake:
    def __init__(s, code): s.d = code; s.secs = [('.text', BASE, len(code), 0, len(code))]; s.rset = set(); s.taken = []; s.base = BASE
    def bytes(s, a, n): return s.d[a - BASE:a - BASE + n]
    def u32(s, a): return struct.unpack_from('<I', s.d, a - BASE)[0]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--work', default='/home/user/l3work/disptest'); ap.add_argument('--cc', default=''); ap.add_argument('--trace', default=''); a = ap.parse_args()
    w = a.work; os.makedirs(w, exist_ok=True)
    cc = lambda t: (a.cc + '/' if a.cc else '') + 'aarch64-linux-gnu-' + t
    code = bytearray(0x4000)
    ks = Ks(KS_ARCH_X86, KS_MODE_32); ents = {}
    def put(name, addr, text):
        b = bytes(ks.asm(text, addr)[0]); assert len(b) <= 0x80, name
        code[addr - BASE:addr - BASE + len(b)] = b.ljust(0x80, b'\xcc'); ents[name] = (addr, len(b), [])
    for n, t in TARGETS.items(): put(n, ADDR[n], t)
    S = lambda n: 'dword ptr [0x%x]' % SLOT['tc_' + n]
    # the root of the #UD case and its handler: the handler unwinds to the root's frame and continues at a label of the root (a non-local transfer, as RtlUnwind
    # and a catch continuation do; the native frames in between are discarded) with eax = ExceptionCode ^ ExceptionAddress; the root then returns it
    udroot = ROOTS + 0x40 * CASES.index('ud')
    udtext = 'push 0x%x; push dword ptr fs:[0]; mov fs:[0], esp; call %s; mov eax, 0xdead; ret; nop; nop; nop; nop; ret' % (HANDLER, S('ud'))
    label = udroot + bytes(ks.asm(udtext, udroot)[0]).index(b'\x90\x90\x90\x90') + 4
    put('udh', HANDLER, 'mov ecx, dword ptr [esp+4]; mov eax, dword ptr [esp+8]; mov esp, eax; pop dword ptr fs:[0]; add esp, 4; mov eax, dword ptr [ecx]; xor eax, dword ptr [ecx+12];'
                        'mov edx, 0x%x; jmp edx' % label)
    roots = {}
    for i, c in enumerate(CASES):
        ad = ROOTS + 0x40 * i
        t = 'call %s; ret' % S(c)
        if c == 'ud': t = udtext
        if c == 'fsw': t = 'fld1; fadd st(0), st(0); fstp st(0); call %s; ret' % S(c)       # the x87 family owns the live FPSR from here
        put('root_' + c, ad, t); roots[c] = ad
        if c == 'ud': ents['root_ud'] = (ad, ents['root_ud'][1], [label])       # the label is a continuation of the root (what the lifter takes as `owns`)
    code = bytes(code); img = Fake(code); img.entries = set(v[0] for v in ents.values())
    open(w + '/bridge.tsv', 'w').write('dll\tname\tordinal\tconv\tret\targs\tnative_symbol\tbind\n' + ''.join('%s\t%s\t\t%s\t%s\t%s\t%s\t%s\n' % r for r in BRIDGE))
    open(w + '/need.tsv', 'w').write(''.join('%08x\t%s\t%s\tkey\t%08x\n' % (SLOT[r[1]], r[0], r[1], KEY[r[1]]) for r in BRIDGE))
    ent, blocked, hit, absent = impgen.generate(w + '/bridge.tsv', w, [w + '/need.tsv'], None)
    ok = len(ent) == len(BRIDGE) and not blocked and not hit and not absent
    print('gen   %s entries %d' % ('equal' if ok else 'DIFF', len(ent))); bad = not ok
    texts = [L.lift(img, ad, sz, owns=owns)[0] for ad, sz, owns in ents.values()]
    flags = [f for f in instest.FLAGS if f not in ('-DFNVL_FLAGS_IO', '-Werror=attributes')] + ['-Wno-attributes', '-I', LIFT + '/rt']
    CF = ['-mabi=ilp32', '-O2', '-fPIC', '-ffreestanding', '-fno-stack-protector', '-fno-asynchronous-unwind-tables', '-fno-stack-clash-protection', '-w', '-c', '-I', FNV + '/shim/include']
    syms = {ad: 'L_%08x' % ad for ad, sz, owns in ents.values()}; syms.update({k: s for k, s, *_ in ent})
    h = phf.build(list(syms)); open(w + '/tab.S', 'w').write(phf.table_asm(syms, h))
    open(w + '/guest.cpp', 'w').write(L.PRELUDE + '\n'.join(texts)); open(w + '/native.cc', 'w').write(NATIVE)
    # the table of native bodies: rule 1's rows, `exe address <TAB> native symbol`
    nat, nh = dispgen.table(['%08x\tnative_body\n' % NATIVE_ADDR], 'fnvnx_native'); open(w + '/ntab.S', 'w').write(nat)
    # plugins, built from the repo sources against the pinned compiler's plugin headers
    pdir = subprocess.run([cc('g++'), '-print-file-name=plugin'], capture_output=True, text=True).stdout.strip() + '/include'
    plug = []
    for n, src in (('fnvnx_names', 'names/fnvnx_names.cc'), ('fnvnx_dispatch', 'dispatch/fnvnx_dispatch.cc')):
        so = '%s/%s.so' % (w, n)
        r = subprocess.run(['x86_64-linux-gnu-g++-13', '-shared', '-fPIC', '-fno-rtti', '-fno-exceptions', '-O2', '-w', '-I' + pdir, '%s/toolchain/%s' % (FNV, src), '-o', so], capture_output=True, text=True)
        if r.returncode: sys.exit('plugin %s: %s' % (n, r.stderr[:600]))
        plug.append('-fplugin=' + so)
    NF = ['-mabi=ilp32', '-std=gnu++17', '-O2', '-fPIC', '-ffreestanding', '-fno-exceptions', '-fno-stack-protector', '-fno-asynchronous-unwind-tables', '-fno-stack-clash-protection', '-c', '-I', FNV + '/shim/include']
    def build(cmd, src, o):
        r = subprocess.run(cmd + [src, '-o', o], capture_output=True, text=True)
        if r.returncode: sys.exit('compile %s: %s' % (src, r.stderr[:800]))
    build([cc('g++')] + NF + plug, w + '/native.cc', w + '/native.o')
    # the descriptors come from the test object's own fact section; the bridges and their table are generated from them
    r = subprocess.run([sys.executable, LIFT + '/tools/bridgegen.py', '--obj', w + '/native.o', '--out', w + '/bridges.cc', '--map', w + '/bridges.tsv'], capture_output=True, text=True)
    print('bridgegen: ' + (r.stdout.strip() or r.stderr.strip()))
    if r.returncode: sys.exit(1)
    jobs = [([cc('g++')] + flags + ['-x', 'c++'], w + '/guest.cpp'), ([cc('g++')] + flags + ['-x', 'c++'], w + '/imp.cpp'),
            ([cc('g++')] + flags + ['-x', 'c++'], LIFT + '/rt/enter.cc'), ([cc('g++')] + flags + ['-x', 'c++'], LIFT + '/rt/seh.cc'), ([cc('g++')] + flags + ['-x', 'c++'], LIFT + '/rt/unhandled.cc'),
            ([cc('g++')] + NF + ['-x', 'c++'], w + '/bridges.cc'),
            ([cc('gcc')] + CF, LIFT + '/rt/rt.c'), ([cc('gcc')] + CF, LIFT + '/rt/dispatch.c'), ([cc('gcc')] + CF, LIFT + '/rt/rcptab.c'), ([cc('gcc')] + CF + ['-I', LIFT + '/rt'], LIFT + '/rt/x87.c'),
            ([cc('gcc')] + CF, w + '/tab.S'), ([cc('gcc')] + CF, w + '/ntab.S')]
    objs = [w + '/native.o']; prod = []
    for cmd, src in jobs:
        o = '%s/%s.o' % (w, os.path.basename(src).rsplit('.', 1)[0]); objs.append(o); prod.append(o)
        build(cmd, src, o)
    # no double: no symbol the test object defines is defined by a product object
    def defs(o): return set(l.split()[-1] for l in subprocess.run(['aarch64-linux-gnu-nm', '--defined-only', '-g', o], capture_output=True, text=True).stdout.splitlines() if len(l.split()) == 3)
    dt = defs(w + '/native.o'); dp = set().union(*[defs(o) for o in prod if not o.endswith(('tab.o',))]) | defs(w + '/tab.o') | defs(w + '/ntab.o')
    dbl = sorted(dt & dp); print('doubles %s' % ('none' if not dbl else 'DIFF ' + ' '.join(dbl))); bad |= bool(dbl)
    elf = w + '/disp.elf'
    r = subprocess.run(['aarch64-linux-gnu-ld', '-m', 'aarch64elf32', '-Ttext=0x%x' % TEXT, '-e', 'test_main', '-o', elf] + objs, capture_output=True, text=True)
    if r.returncode:
        und = sorted(set(re.findall(r"undefined reference to `([^']+)'", r.stderr)))
        # the platform symbols the runtime calls are the platform root's (platform/win32/thread.cpp, platform/boundary/host_hooks.cpp, ...): this test defines none of them (no doubles),
        # so until the platform root is linkable here it is BLOCKED, never green
        if und: sys.exit(3 if print('BLOCKED: the platform root is not linked; undefined: ' + ' '.join(und)) is None else 3)
        sys.exit('link: ' + r.stderr[:900])
    with open(elf, 'rb') as fh:
        e = ELFFile(fh); segs = [(p['p_vaddr'], p.data(), p['p_memsz']) for p in e.iter_segments() if p['p_type'] == 'PT_LOAD']
        sym = {x.name: x['st_value'] for x in e.get_section_by_name('.symtab').iter_symbols() if x.name}
    want = {'this': 25, 'fast': 107, 'stdcdecl': 7 + 12 + 7, 'q': ((0x01234567 ^ 0x89abcdef) + (2 ^ 0)) & 0xffffffff, 'fd': 375 * 4 // 4 * 1 + 0, 'mem': 7 + 8 + 9 + 10 + 11,
            'a12': 50607, 'a8': 9 + 18 * 1000, 'hfa': 4 * 10 + 5, 'hfa3': 7, 'hfaret': 6 + 225, 'ref': 321, 'native': 0x1111, 'ud': 0xC000001D ^ NOWHERE,
            'uf': 40, 'u1': 60, 'vec': 10, 'vec2': 10, 'd4': 11, 'al': 13, 'bf': 40, 'f4r': 4321, 'vr': 4321, 'd4r': 4321, 'ct': 507, 'ur': 6 + 225, 'fsw': 0x20, 'frm': 0x25800000}
    want['fd'] = 15 * 100 + 6
    want['vr'] = 4321
    for name in CASES:
        uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        lo = min(v for v, d, m in segs) & ~0xfff; hi = (max(v + m for v, d, m in segs) + 0xffff) & ~0xfff; uc.mem_map(lo, hi - lo)
        for v, d, m in segs: uc.mem_write(v, d)
        uc.mem_map(0x400000, 0x10000); uc.mem_write(BASE, code); uc.mem_write(IAT, b''.join(struct.pack('<I', KEY[r[1]]) for r in BRIDGE))
        uc.mem_map(GSTACK, 0x20000); uc.mem_map(HSTACK, 0x40000); uc.mem_map(STOP, 0x1000); uc.mem_map(0x50000000, 0x1000); uc.mem_write(0x50000000, b'\xff\xff\xff\xff')
        uc.mem_map(TLS, 0x1000); uc.reg_write(UC_ARM64_REG_TPIDR_EL0, TLS)       # the host thread's TLS block (fnvnx_call_fp lives there)
        uc.reg_write(UC_ARM64_REG_CPACR_EL1, 3 << 20); uc.reg_write(UC_ARM64_REG_SP, HSTACK + 0x3f000); uc.reg_write(UC_ARM64_REG_X30, STOP)
        gsp = GSTACK + 0x1ff00
        for i, v in enumerate((roots[name], gsp, 0x50000000, HSTACK + 0x100)): uc.reg_write(UC_ARM64_REG_X0 + i, v)
        if a.trace and name == a.trace:      # debugging aid: report each entry of the named runtime functions
            from unicorn import UC_HOOK_CODE
            at = {sym[n]: n for n in ('fnvnx_dispatch', 'fnvl_nobody', 'fnvl_raise_ud', 'UnhandledExceptionFilter', 'ExitProcess', 'fnvl_has_entry', 'fnvnx_guest_invoke_regs') if n in sym}
            uc.hook_add(UC_HOOK_CODE, lambda u, ad, sz, ud: print('  hit', at[ad], hex(u.reg_read(UC_ARM64_REG_X0))) if ad in at else None)
        try:
            uc.emu_start(sym['test_main'], STOP, count=5000000); got = uc.reg_read(UC_ARM64_REG_X0) & 0xffffffff; err = ''
        except UcError as ex: got = None; err = str(ex)
        o = struct.unpack('<3I', bytes(uc.mem_read(HSTACK + 0x100, 12)))
        good = got == want[name] and o == (0, 0, 0) and uc.reg_read(UC_ARM64_REG_SP) == HSTACK + 0x3f000
        bad += not good
        print('%-9s %s %s' % (name, 'equal' if good else 'DIFF', '' if good else 'eax %s want %x init/key %s %s' % (got if got is None else '%x' % got, want[name], o, err)))
    sys.exit(1 if bad else 0)


if __name__ == '__main__': main()
