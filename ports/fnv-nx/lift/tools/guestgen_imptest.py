#!/usr/bin/env python3
"""imptest generator (a `generated` stage of the lift test root): the start-runtime test's guest side.
Hand-assembled x86 guest functions (a synthetic image at 0x401000, assembled by the pinned keystone) call imports through an import table; the stage lifts them (fnvlift, capstone),
generates the import entries (tools/impgen.py) and the perfect-hash dispatch table (tools/phf.py), and writes:
  guest.cc   the lifted functions (lifted-code flags)          imp.cc   the generated import entries          tab.S    the dispatch table
  image.inc  the image bytes, the import table words, the entry address and the expected eax of every case (main.cc includes it)
Expected values are computed from the x86 semantics by hand, stated at each case (as in the comments below).
usage: gen.py <out dir>"""
import os, struct, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.join(HERE, '..', '..', '..')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # the tool root (/tool)
sys.path.insert(0, sys.argv[2])   # lift/, whose fnvlift package is a declared input
import fnvlift as L, phf, impgen
from keystone import Ks, KS_ARCH_X86, KS_MODE_32

BASE, IAT, DATA, GSTACK, HSTACK, STOP, TEXT = 0x401000, 0x402000, 0x403000, 0x60000000, 0x70000000, 0x7e000000, 0xE0000000
BRIDGE = [  # dll, name, conv, ret, args, native symbol, bind
    ('t.dll', 'add2', 'stdcall', 'i', 'ii', 't_add2', 'sym'),
    ('t.dll', 'mul64', 'cdecl', 'q', 'iq', 't_mul64', 'sym'),
    ('t.dll', 'half', 'cdecl', 'd', 'd', 't_half', 'sym'),
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
    w = sys.argv[1]; os.makedirs(w, exist_ok=True)
    code, ents, cases = guest(); img = Fake(code); img.entries = set(v[0] for v in ents.values())
    work = tempfile.mkdtemp()            # scratch outside /out: the stage's outputs are exactly the files listed in the header
    open(work + '/bridge.tsv', 'w').write('dll\tname\tordinal\tconv\tret\targs\tnative_symbol\tbind\n' + ''.join('%s\t%s\t\t%s\t%s\t%s\t%s\t%s\n' % r for r in BRIDGE))
    open(work + '/need.tsv', 'w').write(''.join('%08x\t%s\t%s\tkey\t%08x\n' % (SLOT[r[1]], r[0], r[1], KEY[r[1]]) for r in BRIDGE if r[6] != 'none'))
    ent, blocked, hit, absent = impgen.generate(work + '/bridge.tsv', work, [work + '/need.tsv'], None)
    # the generation is itself a check: 10 entries, exactly the `nobody` row blocked, nothing hit or absent (a violation fails the stage)
    if not (len(ent) == 10 and len(blocked) == 1 and not hit and not absent): sys.exit('imptest gen: %d entries, %d blocked, hit %s absent %s' % (len(ent), len(blocked), hit, absent))
    texts = [L.lift(img, ad, sz, owns=owns)[0] for ad, sz, owns in ents.values()]
    syms = {ad: 'L_%08x' % ad for ad, sz, owns in ents.values()}; syms.update({k: s for k, s, *_ in ent})
    if {n: k for k, s, d, n, b in ent} != {r[1]: KEY[r[1]] for r in BRIDGE if r[6] != 'none'}: sys.exit('imptest gen: the import keys differ from the key rule')
    h = phf.build(list(syms))
    open(w + '/tab.S', 'w').write(phf.table_asm(syms, h)); open(w + '/guest.cc', 'w').write(L.PRELUDE + '\n'.join(texts))
    open(w + '/imp.cc', 'w').write(open(work + '/imp.cpp').read())
    inc = ['#define IMG_BASE 0x%xu' % BASE, '#define IAT_BASE 0x%xu' % IAT, '#define NCASES %d' % len(cases)]
    inc.append('static const unsigned char IMG[] = {%s};' % ','.join(str(x) for x in code))
    inc.append('static const unsigned IAT[] = {%s};' % ','.join('0x%x' % KEY[r[1]] for r in BRIDGE))
    inc.append('static const struct { unsigned eip, want; } K[] = {%s};' % ','.join('{0x%x,0x%x}' % (ents[n][0], want) for n, want in cases))
    open(w + '/image.inc', 'w').write('\n'.join(inc) + '\n')
    open(w + '/labels.txt', 'w').write(''.join('%d\t%s\n' % (i + 1, n) for i, (n, want) in enumerate(cases)))


if __name__ == '__main__':
    main()
