#!/usr/bin/env python3
"""Start-runtime test: import entries (tools/impgen.py), thread entry, native -> guest callbacks and unwinding through a native frame (rt/enter.cc, rt/imp.h).
    imptest.py [--work DIR]
Hand-assembled x86 guest functions (synthetic image at 0x401000) call imports through an import table; the natives are C functions of this file.
The whole thing (lifted guest code + generated import entries + rt) is linked as AArch64 ILP32 and run under Unicorn from a native `test_main`.
Expected values are computed from the x86 semantics by hand (stated at each case). Prints one line per case; exit status 1 on any difference."""
import argparse, os, struct, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(HERE)
for p in ('tools', 'test', ''): sys.path.insert(0, os.path.join(LIFT, p))
import fnvlift as L, instest, phf, impgen
from keystone import Ks, KS_ARCH_X86, KS_MODE_32
from unicorn import Uc, UcError, UC_ARCH_ARM64, UC_MODE_ARM
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X30, UC_ARM64_REG_SP, UC_ARM64_REG_CPACR_EL1
from elftools.elf.elffile import ELFFile

BASE, IAT, DATA, GSTACK, HSTACK, STOP, TEXT = 0x401000, 0x402000, 0x403000, 0x60000000, 0x70000000, 0x7e000000, 0xE0000000
BRIDGE = [  # dll, name, conv, ret, args, native symbol, bind
    ('t.dll', 'add2', 'stdcall', 'i', 'ii', 't_add2', 'sym'),
    ('t.dll', 'mul64', 'cdecl', 'q', 'iq', 't_mul64', 'sym'),
    ('t.dll', 'half', 'cdecl', 'd', 'd', '-', 'lookup'),
    ('t.dll', 'each', 'stdcall', 'i', 'iii', 't_each', 'sym'),
    ('t.dll', 'call1', 'stdcall', 'i', 'ii', 't_call1', 'sym'),
    ('t.dll', 'meth', 'thiscall', 'i', 'i', 't_meth', 'sym'),
    ('t.dll', 'nobody', 'stdcall', 'i', '', '-', 'none'),
    ('t.dll', 'fmix', 'stdcall', 'f', 'fi', 't_fmix', 'sym'),
    ('t.dll', 'boom', 'stdcall', 'i', 'i', 't_boom', 'sym'),
    ('t.dll', 'vsum', 'vararg', 'i', 'i', 't_vsum', 'sym'),
    ('kernel32.dll', 'DebugBreak', 'stdcall', 'v', '', 'fnvl_os_DebugBreak', 'l3'),
]
KEY = {r[1]: 0x00100000 + 16 * i for i, r in enumerate(sorted(BRIDGE, key=lambda r: (r[0], r[1])))}      # the key rule of tools/impgen.py
SLOT = {r[1]: IAT + 4 * i for i, r in enumerate(BRIDGE)}
NATIVE = r'''
typedef unsigned int u32; typedef unsigned long long u64;
struct fnvnx_guest_ret { u32 eax, edx; double st0; };
void fnvnx_guest_invoke(u32 addr, u32 ecx, u32 nargs, const u32 *args, u32 kind, struct fnvnx_guest_ret *out);
u32 fnvnx_lift_enter(u32 eip, u32 esp, u32 teb); int fnvnx_lift_imports_init(void);
static void *slot; void **fnvl_host_tls_slot(void) { return &slot; }
unsigned long long fnvl_host_ticks_ns(void) { return 0; }
void *memcpy(void *d, const void *s, unsigned long n) { char *a = d; const char *b = s; while (n--) *a++ = *b++; return d; }
void *memset(void *d, int v, unsigned long n) { char *a = d; while (n--) *a++ = (char)v; return d; }
void fnvnx_guest_current(u32 *esp, u32 *teb);
/* variadic: n fixed, then n guest stack words to add */
u32 t_vsum(u32 n, u32 va) { u32 r = 0; for (u32 i = 0; i < n; i++) r += ((u32 *)(unsigned long)va)[i]; return r; }
u32 t_add2(u32 a, u32 b) { u32 sp, teb; fnvnx_guest_current(&sp, &teb); return a + b + (teb != 0x50000000u) + (*(u32 *)(unsigned long)(sp + 4) != a); }      /* also checks fnvnx_guest_current: the esp of this call, the thread's TEB */
u64 t_mul64(u32 a, u64 b) { return (u64)a * b; }
static double t_half(double x) { return x * 0.5; }
float t_fmix(float x, u32 k) { return x * (float)k; }
u32 t_meth(u32 self, u32 a) { return *(u32 *)(unsigned long)self + a; }
/* calls the guest callback cb(i, ctx) (stdcall) for i = 0..n-1 and sums the results */
u32 t_each(u32 n, u32 cb, u32 ctx) { u32 s = 0; for (u32 i = 0; i < n; i++) { struct fnvnx_guest_ret r; u32 a[2] = { i, ctx }; fnvnx_guest_invoke(cb, 0, 2, a, 0, &r); s += r.eax; } return s; }
/* calls the guest function cb(arg) once (cdecl or stdcall) */
u32 t_call1(u32 cb, u32 arg) { struct fnvnx_guest_ret r; fnvnx_guest_invoke(cb, 0, 1, &arg, 0, &r); return r.eax + 1000; }
static void (*backend)(u32, u32, u32, const u32 *); void fnvnx_win32_set_raise_backend(void (*f)(u32, u32, u32, const u32 *)) { backend = f; }
/* a native body raises an exception (the platform's w32_raise); returns x + 1 when a handler continued execution */
u32 t_boom(u32 x) { u32 a[1] = { x }; backend(0xE0000001u, 0, 1, a); return x + 1; }
u32 UnhandledExceptionFilter(u32 p) { return 1; } void ExitProcess(u32 code) { for (;;) ; }
void *fnvnx_win32_lookup_export(const char *dll, const char *name) { return name[0] == 'h' ? (void *)t_half : 0; }
u32 test_main(u32 eip, u32 esp, u32 teb, u32 *out)
{
    out[0] = (u32)fnvnx_lift_imports_init();
    out[1] = out[2] = 0;
    return fnvnx_lift_enter(eip, esp, teb);
}
'''


class Fake:
    def __init__(s, code): s.d = code; s.secs = [('.text', BASE, len(code), 0, len(code))]; s.rset = set(); s.taken = []; s.base = BASE
    def bytes(s, a, n): return s.d[a - BASE:a - BASE + n]
    def u32(s, a): return struct.unpack_from('<I', s.d, a - BASE)[0]


def guest():
    """-> (code bytes, {name: (addr, size, owns)}, cases [(name, root, expected eax, note, check)])"""
    ks = Ks(KS_ARCH_X86, KS_MODE_32); F = {}; code = bytearray(); srcs = []
    def fn(name, text, owns=()): srcs.append((name, text, owns)); F[name] = BASE + 0x80 * (len(srcs) - 1)
    S = lambda n: 'dword ptr [0x%x]' % SLOT[n]
    D = lambda i: 'dword ptr [0x%x]' % (DATA + 4 * i)
    # c1: stdcall import, two arguments popped by the entry: eax = 5 + 7; esp balanced (ebx - esp == 0 added)
    fn('c1', 'mov ebx, esp; push 7; push 5; call %s; sub ebx, esp; add eax, ebx; ret' % S('add2'))
    # c2: cdecl import returning 64 bits: 0x10001 * 0x100000003 = 0x10001_00030003 -> edx:eax; eax ^ edx = 0x00030003 ^ 0x10001; caller pops 12
    fn('c2', 'push 1; push 3; push 0x10001; call %s; add esp, 12; xor eax, edx; ret' % S('mul64'))
    # c3: lookup-bound cdecl import, double in, double out in ST0: half(9.0) = 4.5 -> fistp rounds to nearest even = 4; + fmix(2.5f, 3) = 7.5 -> 8
    fn('c3', 'sub esp, 8; mov dword ptr [esp], 0; mov dword ptr [esp+4], 0x40220000; call %s; fistp dword ptr [esp]; mov ebx, dword ptr [esp]; add esp, 8;'
             'push 3; push 0x40200000; call %s; push eax; fistp dword ptr [esp]; pop eax; add eax, ebx; ret' % (S('half'), S('fmix')))
    # c4: the import calls the guest callback cb(i, ctx) = i * ctx + esi three times (esi is callee-saved: the callback sees the caller's 100): 300 + 10 * (0+1+2) = 330
    fn('c4', 'push esi; mov esi, 100; push 10; push 0x%x; push 3; call %s; pop esi; ret' % (BASE + 0x80 * 4, S('each')))
    fn('cb', 'mov eax, dword ptr [esp+4]; imul eax, dword ptr [esp+8]; add eax, esi; ret 8')
    # c5: nested: import -> guest callback (cdecl, one argument) -> import -> guest callback: call1(c5b, 4) = c5b(4) + 1000; c5b(x) = each(x, cb, 2) + 1 = 2*(0+1+2+3) + 4*esi(0) + 1 = 13
    fn('c5', 'push esi; xor esi, esi; push 4; push 0x%x; call %s; pop esi; ret' % (BASE + 0x80 * 6, S('call1')))
    fn('c5b', 'push 2; push 0x%x; push dword ptr [esp+12]; call %s; inc eax; ret' % (BASE + 0x80 * 4, S('each')))
    # c6: the callback leaves by a non-local jump to a label of the root with the root's saved esp (what RtlUnwind + a handler jump or longjmp do):
    #     the native frame of call1 is discarded, the root goes on at the label: eax = 0x77 + ebx(0x1000)
    c6 = BASE + 0x80 * 7
    fn('c6', 'mov ebx, 0x1000; mov %s, esp; push 0; push 0x%x; call %s; mov eax, 0xdead; ret; nop; nop; nop; nop; mov eax, 0x77; add eax, ebx; ret' % (D(0), BASE + 0x80 * 8, S('call1')))
    fn('c6b', 'mov esp, %s; push 0x%x; ret' % (D(0), 0))        # patched below with the label address
    # c7: thiscall import: this in ecx, one stack argument popped: *this(0x1234) + 5
    fn('c7', 'mov %s, 0x1234; mov ecx, 0x%x; push 5; call %s; ret' % (D(1), DATA + 4, S('meth')))
    # c8: a native body raises (platform backend); the guest frame's handler leaves non-locally: resets esp to its frame, unlinks it and returns from the root with 0x55
    #     plus the first exception parameter read from the record (7): the native frame of `boom` is discarded
    h8 = BASE + 0x80 * 11
    fn('c8', 'push 0x%x; push dword ptr fs:[0]; mov dword ptr fs:[0], esp; push 7; call %s; mov eax, 0xdead; ret' % (h8, S('boom')))
    fn('c8h', 'mov ecx, dword ptr [esp+4]; mov eax, dword ptr [esp+8]; mov esp, eax; pop dword ptr fs:[0]; add esp, 4; mov eax, dword ptr [ecx+20]; add eax, 0x55; ret')
    # c9: DebugBreak with a handler that continues execution (returns ExceptionContinueExecution = 0): the root goes on after the call: 0x99
    h9 = BASE + 0x80 * 13
    fn('c9', 'push 0x%x; push dword ptr fs:[0]; mov dword ptr fs:[0], esp; call %s; pop dword ptr fs:[0]; add esp, 4; mov eax, 0x99; ret' % (h9, S('DebugBreak')))
    fn('c9h', 'xor eax, eax; ret')
    # c10: variadic cdecl import: vsum(3, 10, 20, 30) = 60; the caller pops 16 bytes; esp balanced (ebx - esp == 0 added)
    fn('c10', 'mov ebx, esp; push 30; push 20; push 10; push 3; call %s; add esp, 16; sub ebx, esp; add eax, ebx; ret' % S('vsum'))
    ents = {}
    for i, (name, text, owns) in enumerate(srcs):
        if name == 'c6':
            b = bytes(ks.asm(text, F[name])[0]); lab = F[name] + b.index(b'\x90\x90\x90\x90') + 4; c6lab = lab
        if name == 'c6b': text = text.replace('push 0x0;', 'push 0x%x;' % c6lab) if 'push 0x0;' in text else text.replace('push 0x0', 'push 0x%x' % c6lab)
        b = bytes(ks.asm(text, F[name])[0]); assert len(b) <= 0x80, name
        code[F[name] - BASE:F[name] - BASE + len(b)] = b.ljust(0x80, b'\xcc'); ents[name] = (F[name], len(b), [c6lab] if name == 'c6' else [])
    cases = [('c1', 12), ('c2', 0x00030003 ^ 0x10001), ('c3', 12), ('c4', 330), ('c5', 1013), ('c6', 0x1077), ('c7', 0x1239), ('c8', 0x5c), ('c9', 0x99), ('c10', 60)]
    return bytes(code), ents, cases


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--work', default='/home/user/l3work/imptest'); a = ap.parse_args(); w = a.work; os.makedirs(w, exist_ok=True)
    code, ents, cases = guest(); img = Fake(code); img.entries = set(v[0] for v in ents.values())
    open(w + '/bridge.tsv', 'w').write('dll\tname\tordinal\tconv\tret\targs\tnative_symbol\tbind\n' + ''.join('%s\t%s\t\t%s\t%s\t%s\t%s\t%s\n' % r for r in BRIDGE))
    open(w + '/need.tsv', 'w').write(''.join('%08x\t%s\t%s\tkey\t%08x\n' % (SLOT[r[1]], r[0], r[1], KEY[r[1]]) for r in BRIDGE if r[6] != 'none'))
    ent, blocked, hit, absent = impgen.generate(w + '/bridge.tsv', w, [w + '/need.tsv'], None)
    ok = len(ent) == 10 and len(blocked) == 1 and not hit and not absent
    print('gen   %s entries %d blocked %d' % ('equal' if ok else 'DIFF', len(ent), len(blocked))); bad = not ok
    texts = [L.lift(img, ad, sz, owns=owns)[0] for ad, sz, owns in ents.values()]
    flags = [f for f in instest.FLAGS if f != '-DFNVL_FLAGS_IO'] + ['-I', LIFT + '/rt']
    CF = ['-mabi=ilp32', '-O2', '-fPIC', '-ffreestanding', '-fno-stack-protector', '-fno-asynchronous-unwind-tables', '-fno-stack-clash-protection', '-w', '-c']
    syms = {ad: 'L_%08x' % ad for ad, sz, owns in ents.values()}; syms.update({k: s for k, s, *_ in ent})
    assert {n: k for k, s, d, n, b in ent} == {r[1]: KEY[r[1]] for r in BRIDGE if r[6] != 'none'}
    h = phf.build(list(syms)); open(w + '/tab.S', 'w').write(phf.table_asm(syms, h)); open(w + '/native.c', 'w').write(NATIVE); open(w + '/guest.cpp', 'w').write(L.prelude(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'rt')) + '\n'.join(texts))
    jobs = [(['aarch64-linux-gnu-g++'] + flags + ['-x', 'c++'], w + '/guest.cpp'), (['aarch64-linux-gnu-g++'] + flags + ['-x', 'c++'], w + '/imp.cpp'),
            (['aarch64-linux-gnu-g++'] + flags + ['-x', 'c++'], LIFT + '/rt/enter.cc'), (['aarch64-linux-gnu-g++'] + flags + ['-x', 'c++'], LIFT + '/rt/seh.cc'), (['aarch64-linux-gnu-g++'] + flags + ['-x', 'c++'], LIFT + '/rt/unhandled.cc'),
            (['aarch64-linux-gnu-gcc'] + CF, LIFT + '/rt/rt.c'), (['aarch64-linux-gnu-gcc'] + CF, LIFT + '/rt/rcptab.c'), (['aarch64-linux-gnu-gcc'] + CF + ['-I', LIFT + '/rt'], LIFT + '/rt/x87.c'),
            (['aarch64-linux-gnu-gcc'] + CF, w + '/tab.S'), (['aarch64-linux-gnu-gcc'] + CF, w + '/native.c')]
    objs = []
    for cmd, src in jobs:
        o = '%s/%s.o' % (w, os.path.basename(src).rsplit('.', 1)[0]); objs.append(o)
        r = subprocess.run(cmd + [src, '-o', o], capture_output=True, text=True)
        if r.returncode: sys.exit('compile %s: %s' % (src, r.stderr[:600]))
    elf = w + '/imp.elf'
    r = subprocess.run(['aarch64-linux-gnu-ld', '-m', 'aarch64elf32', '-Ttext=0x%x' % TEXT, '-e', 'test_main', '-o', elf] + objs, capture_output=True, text=True)
    if r.returncode: sys.exit('link: ' + r.stderr[:600])
    with open(elf, 'rb') as fh:
        e = ELFFile(fh); segs = [(p['p_vaddr'], p.data(), p['p_memsz']) for p in e.iter_segments() if p['p_type'] == 'PT_LOAD']
        sym = {x.name: x['st_value'] for x in e.get_section_by_name('.symtab').iter_symbols() if x.name}
    for name, want in cases:
        uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        lo = min(v for v, d, m in segs) & ~0xfff; hi = (max(v + m for v, d, m in segs) + 0xffff) & ~0xfff; uc.mem_map(lo, hi - lo)
        for v, d, m in segs: uc.mem_write(v, d)
        uc.mem_map(0x400000, 0x10000); uc.mem_write(BASE, code); uc.mem_write(IAT, b''.join(struct.pack('<I', KEY[r[1]]) for r in BRIDGE))
        uc.mem_map(GSTACK, 0x20000); uc.mem_map(HSTACK, 0x40000); uc.mem_map(STOP, 0x1000); uc.mem_map(0x50000000, 0x1000); uc.mem_write(0x50000000, b'\xff\xff\xff\xff')
        uc.reg_write(UC_ARM64_REG_CPACR_EL1, 3 << 20); uc.reg_write(UC_ARM64_REG_SP, HSTACK + 0x3f000); uc.reg_write(UC_ARM64_REG_X30, STOP)
        gsp = GSTACK + 0x1ff00
        for i, v in enumerate((ents[name][0], gsp, 0x50000000, HSTACK + 0x100)): uc.reg_write(UC_ARM64_REG_X0 + i, v)
        try:
            uc.emu_start(sym['test_main'], STOP, count=2000000); got = uc.reg_read(UC_ARM64_REG_X0) & 0xffffffff; err = ''
        except UcError as ex: got = None; err = str(ex)
        o = struct.unpack('<3I', bytes(uc.mem_read(HSTACK + 0x100, 12)))
        good = got == want and o == (0, 0, 0) and uc.reg_read(UC_ARM64_REG_SP) == HSTACK + 0x3f000
        bad += not good
        print('%-5s %s %s' % (name, 'equal' if good else 'DIFF', '' if good else 'eax %s want %x init/key %s %s' % (got if got is None else '%x' % got, want, o, err)))
    sys.exit(1 if bad else 0)


if __name__ == '__main__': main()
