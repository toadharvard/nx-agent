#!/usr/bin/env python3
"""Synthetic check of gpa_names.py / argflow.py on a hand-assembled PE32 image (no game data)."""
import os, struct, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import argflow, gpa_names
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

BASE, TEXT, RD, DATA = 0x400000, 0x1000, 0x2000, 0x3000
LL, GPA = BASE + RD, BASE + RD + 4            # IAT slots of LoadLibraryA, GetProcAddress
S_FOO, S_BAR, S_MOD, S_BAZ = BASE + 0x2100, BASE + 0x2110, BASE + 0x2120, BASE + DATA


class Asm:
    def __init__(self):
        self.b, self.rel, self.funcs = bytearray(), [], {}

    def va(self):
        return BASE + TEXT + len(self.b)

    def raw(self, *bs):
        self.b += bytes(bs)

    def imm(self, op, v, reloc=True):
        self.b += bytes(op)
        if reloc:
            self.rel.append(TEXT + len(self.b))
        self.b += struct.pack('<I', v)

    def func(self, name):
        self.funcs[name] = [self.va(), None]

    def end(self, name):
        self.funcs[name][1] = self.va()
        self.raw(0xcc)

    def push_s(self, v, reloc=True):  self.imm([0x68], v, reloc)
    def call_slot(self, s):           self.imm([0xff, 0x15], s)
    def ret(self):                    self.raw(0xc3)


def build():
    a = Asm()
    a.func('f_const')                      # LoadLibraryA("mod.dll"); GetProcAddress(h, "Foo")
    a.push_s(S_MOD); a.call_slot(LL); a.push_s(S_FOO); a.raw(0x50); a.call_slot(GPA); a.ret(); a.end('f_const')
    a.func('f_branch')                     # test eax,eax; je +5; push Foo; jmp +5; push Bar; push eax; call GPA
    a.push_s(S_MOD); a.call_slot(LL); a.raw(0x85, 0xc0, 0x74, 0x07)
    a.push_s(S_FOO); a.raw(0xeb, 0x05); a.push_s(S_BAR); a.raw(0x50); a.call_slot(GPA); a.ret(); a.end('f_branch')
    a.func('f_ord')
    a.push_s(S_MOD); a.call_slot(LL); a.imm([0x68], 5, False); a.raw(0x50); a.call_slot(GPA); a.ret(); a.end('f_ord')
    a.func('f_writable')
    a.push_s(S_MOD); a.call_slot(LL); a.push_s(S_BAZ); a.raw(0x50); a.call_slot(GPA); a.ret(); a.end('f_writable')
    a.func('f_reg')                        # mov esi,[GPA]; push Foo; push eax; call esi
    a.push_s(S_MOD); a.call_slot(LL); a.imm([0x8b, 0x35], GPA); a.push_s(S_FOO); a.raw(0x50, 0xff, 0xd6); a.ret(); a.end('f_reg')
    a.func('f_escape')                     # mov esi,[GPA]; push esi (the value leaves)
    a.imm([0x8b, 0x35], GPA); a.raw(0x56); a.ret(); a.end('f_escape')
    a.func('f_param')                      # GetProcAddress(LoadLibraryA("mod.dll"), [esp+4+4]) : the name is parameter 0
    a.push_s(S_MOD); a.call_slot(LL); a.raw(0xff, 0x74, 0x24, 0x04, 0x50); a.call_slot(GPA); a.ret(); a.end('f_param')
    a.func('caller1'); a.push_s(S_FOO); a.imm([0xe8], 0, False); a.raw(0x59, 0xc3); a.end('caller1')
    a.func('caller2'); a.push_s(S_BAR); a.imm([0xe8], 0, False); a.raw(0x59, 0xc3); a.end('caller2')
    a.func('f_taken')                      # same as f_param, but its address is a relocated word
    a.push_s(S_MOD); a.call_slot(LL); a.raw(0xff, 0x74, 0x24, 0x04, 0x50); a.call_slot(GPA); a.ret(); a.end('f_taken')
    a.func('caller3'); a.push_s(S_FOO); a.imm([0xe8], 0, False); a.raw(0x59, 0xc3); a.end('caller3')
    a.func('thunk'); a.imm([0xff, 0x25], GPA); a.end('thunk')   # jmp [GPA]; no code reaches it
    # direct calls
    for c, f in (('caller1', 'f_param'), ('caller2', 'f_param'), ('caller3', 'f_taken')):
        tgt = a.funcs[f][0]
        off = a.funcs[c][0] - BASE - TEXT + 5 + 1       # address of the rel32
        struct.pack_into('<i', a.b, off, tgt - (a.funcs[c][0] + 5 + 1 + 4))
    text = bytes(a.b).ljust(0x200, b'\xcc')
    rd = bytearray(0x200)
    struct.pack_into('<III', rd, 0, RD + 0x40, RD + 0x50, 0)            # IAT = lookup: hint/name rvas
    struct.pack_into('<IIIII', rd, 0x10, RD, 0, 0, RD + 0x60, RD)       # descriptor
    rd[0x42:0x42 + 13] = b'LoadLibraryA\0'
    rd[0x52:0x52 + 15] = b'GetProcAddress\0'
    rd[0x60:0x60 + 13] = b'kernel32.dll\0'
    rd[0x100:0x104] = b'Foo\0'; rd[0x110:0x114] = b'Bar\0'; rd[0x120:0x128] = b'mod.dll\0'
    struct.pack_into('<I', rd, 0x140, a.funcs['f_taken'][0])            # a relocated word holding f_taken's address
    data = bytearray(0x200); data[0:4] = b'Baz\0'
    rels = a.rel + [RD + 0x140]
    blk0 = b''.join(struct.pack('<H', 0x3000 | (r - TEXT)) for r in rels if r < RD)
    blk1 = b''.join(struct.pack('<H', 0x3000 | (r - RD)) for r in rels if r >= RD)
    reloc = struct.pack('<II', TEXT, 8 + len(blk0)) + blk0 + struct.pack('<II', RD, 8 + len(blk1)) + blk1
    reloc = reloc.ljust(0x200, b'\0')
    secs = [(b'.text', TEXT, text, 0x60000020), (b'.rdata', RD, bytes(rd), 0x40000040), (b'.data', DATA, bytes(data), 0xc0000040),
            (b'.reloc', 0x4000, reloc, 0x42000040)]
    hdr = bytearray(0x200)
    hdr[0:2] = b'MZ'; struct.pack_into('<I', hdr, 0x3c, 0x40)
    hdr[0x40:0x44] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', hdr, 0x44, 0x14c, len(secs), 0, 0, 0, 224, 0x2102)
    o = 0x58
    struct.pack_into('<H', hdr, o, 0x10b)
    struct.pack_into('<I', hdr, o + 16, TEXT); struct.pack_into('<I', hdr, o + 28, BASE)
    struct.pack_into('<II', hdr, o + 32, 0x1000, 0x200)
    struct.pack_into('<II', hdr, o + 56, 0x5000, 0x200)
    struct.pack_into('<I', hdr, o + 92, 16)
    struct.pack_into('<II', hdr, o + 96 + 8, RD + 0x10, 40)            # import directory
    struct.pack_into('<II', hdr, o + 96 + 40, 0x4000, len(reloc))      # base relocations
    sh = o + 224
    out, fo = bytearray(hdr), 0x200
    for i, (nm, rva, body, ch) in enumerate(secs):
        struct.pack_into('<8sIIIIIIHHI', hdr, sh + 40 * i, nm, 0x200, rva, 0x200, fo, 0, 0, 0, 0, ch)
        out += body
        fo += 0x200
    out[0:0x200] = hdr
    return bytes(out), a


fails = testcase.FAILED
data, a = build()
ranges = {s: [(s, e)] for s, e in a.funcs.values() if s != a.funcs['thunk'][0]}
img = argflow.Image(data, 'syn.exe', ranges)
img.import_stack = gpa_names.import_stacks('dll\tname\tordinal\tconv\tret\targs\nkernel32.dll\tLoadLibraryA\t\tstdcall\ti\ti\nkernel32.dll\tGetProcAddress\t\tstdcall\ti\tii\n')


def site_of(fn, nth, slot=GPA):
    """The slot operand address of the nth instruction of fn that references `slot`."""
    s, e = a.funcs[fn]
    hits = [r for r in sorted(img.relocs) if s <= r < e and img.word(r) == slot]
    return hits[nth]


def pairs(fn, nth=0):
    return gpa_names.derive_site(img, site_of(fn, nth))


d = pairs('f_const')
case('const', got=(d['status'], sorted(d['pairs'])), want=('closed', [('mod.dll', 'Foo')]))
d = pairs('f_branch')
case('branch', got=(d['status'], sorted(d['pairs'])), want=('closed', [('mod.dll', 'Bar'), ('mod.dll', 'Foo')]))
d = pairs('f_ord')
case('ordinal', got=(d['status'], sorted(d['pairs'])), want=('closed', [('mod.dll', '#5')]))
d = pairs('f_writable')
case('writable string is open', got=d['status'], want='open')
d = pairs('f_reg')
case('register call', got=(d['status'], sorted(d['pairs'])), want=('closed', [('mod.dll', 'Foo')]))
d = pairs('f_escape')
case('escape is open', got=d['status'], want='open')
d = pairs('f_param')
case('param names', got=sorted(n for _, n in d['pairs']), want=['Bar', 'Foo'])
case('param status', got=d['status'], want='closed')
d = pairs('f_taken')
case('address-taken param is open', got=d['status'], want='open')
thunk_ref = a.funcs['thunk'][0] + 2
d = gpa_names.derive_site(img, thunk_ref)
case('unreached thunk', got=(d['status'], d['note'], d['pairs']), want=('closed', 'unreached', set()))
# cross-check: platform constant must be among the derived names
res = [(('syn.exe', site_of('f_const', 0), 'GetProcAddress', 'mod.dll Foo'), pairs('f_const')),
       (('syn.exe', site_of('f_reg', 0), 'GetProcAddress', 'mod.dll Nope'), pairs('f_reg'))]
ok, wide, unr, adj, bad = gpa_names.cross_check(res)
case('cross-check', got=(ok, len(bad)), want=(1, 1))
# platform check
have, names, mods = gpa_names.platform_pairs('dll\tname\nkernel32.dll\tFoo\n', [])
case('platform missing', got=('mod.dll', 'Foo') in gpa_names.check_platform(res, have, names, mods), want=True)
# argflow API: argument k of a call site
call = site_of('f_const', 0) - 2
r = argflow.derive_arg(img, call, 1)
case('derive_arg', got=(r.closed, [(k, v[0]) for k, v, _ in r.sorted_leaves()]), want=(True, [('str', 'Foo')]))

if fails:
    print('FAIL\n' + '\n'.join(fails))
    sys.exit(1)
print('ok test_gpa_names')
