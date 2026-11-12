#!/usr/bin/env python3
"""Synthetic check of memfx.py (the may-write rule) and its use in argflow, on hand-assembled PE32 images (no game data)."""
import os, struct, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import argflow, memfx

BASE, TEXT, RD, DATA, DATA2 = 0x400000, 0x1000, 0x2000, 0x3000, 0x4000
IMPS = ['LoadLibraryA', 'GetProcAddress', 'HeapFree', 'CloseHandle']
SLOT = {n: BASE + RD + 4 * k for k, n in enumerate(IMPS)}
G = BASE + DATA            # globals of the .data section (0x3000..0x37ff)
H = BASE + DATA2           # a second writable section (0x4000..)


class Asm:
    def __init__(self):
        self.b, self.rel, self.funcs, self.fix = bytearray(), [], {}, []

    def va(self):
        return BASE + TEXT + len(self.b)

    def raw(self, *bs):
        self.b += bytes(bs)

    def imm(self, op, v, reloc=True):
        self.b += bytes(op)
        if reloc:
            self.rel.append(TEXT + len(self.b))
        self.b += struct.pack('<I', v & 0xffffffff)

    def func(self, name):
        self.funcs[name] = [self.va(), None]

    def end(self, name):
        self.funcs[name][1] = self.va()
        self.raw(0xcc)

    def call(self, target):
        self.b += b'\xe8'
        self.fix.append((len(self.b), target))
        self.b += b'\0\0\0\0'

    def call_imp(self, n):
        self.imm([0xff, 0x15], SLOT[n])

    def push(self, v):             self.imm([0x68], v)
    def mov_ecx(self, v):          self.imm([0xb9], v)
    def mov_esi(self, v):          self.imm([0xbe], v)
    def store_g(self, g):          self.imm([0xa3], g)            # mov [g], eax
    def load_g(self, g):           self.imm([0xa1], g)            # mov eax, [g]
    def ret(self, n=0):            self.raw(0xc3) if n == 0 else self.raw(0xc2, n, 0)


def build(prog):
    a = Asm()
    prog(a)
    for pos, t in a.fix:
        tgt = a.funcs[t][0] if isinstance(t, str) else t
        struct.pack_into('<i', a.b, pos, tgt - (BASE + TEXT + pos + 4))
    text = bytes(a.b).ljust(0x400, b'\xcc')
    rd = bytearray(0x200)
    n = len(IMPS)
    for k, nm in enumerate(IMPS):
        struct.pack_into('<I', rd, 4 * k, RD + 0x80 + 0x20 * k)
        rd[0x80 + 0x20 * k + 2:0x80 + 0x20 * k + 2 + len(nm) + 1] = nm.encode() + b'\0'
    struct.pack_into('<IIIII', rd, 0x40, RD, 0, 0, RD + 0x70, RD)
    rd[0x70:0x7d] = b'kernel32.dll\0'
    data = bytearray(0x800)
    data2 = bytearray(0x100)
    rels = list(a.rel)
    data[0x100:0x108] = b'Name1\0'
    rels_text = [r for r in rels if r < RD]
    blk0 = b''.join(struct.pack('<H', 0x3000 | (r - TEXT)) for r in rels_text)
    reloc = struct.pack('<II', TEXT, 8 + len(blk0)) + blk0
    extra = a.extra_rel if hasattr(a, 'extra_rel') else []
    reloc = reloc.ljust(0x200, b'\0')
    secs = [(b'.text', TEXT, text, 0x60000020), (b'.rdata', RD, bytes(rd), 0x40000040), (b'.data', DATA, bytes(data), 0xc0000040),
            (b'.data2', DATA2, bytes(data2).ljust(0x200, b'\0'), 0xc0000040), (b'.reloc', 0x5000, reloc, 0x42000040)]
    return secs, a


def pe(secs, drel=()):
    """drel: [(rva, value)] relocated data words in .data (written into the section and listed in the reloc table)."""
    secs = [list(s) for s in secs]
    for s in secs:
        if s[0] == b'.data':
            d = bytearray(s[2])
            for rva, v in drel:
                struct.pack_into('<I', d, rva - DATA, v)
            s[2] = bytes(d)
        if s[0] == b'.reloc' and drel:
            blk = b''.join(struct.pack('<H', 0x3000 | (rva - DATA)) for rva, v in drel)
            body = bytes(s[2]).rstrip(b'\0')
            body += struct.pack('<II', DATA, 8 + len(blk)) + blk
            s[2] = body.ljust(0x200, b'\0')
    hdr = bytearray(0x400)
    hdr[0:2] = b'MZ'; struct.pack_into('<I', hdr, 0x3c, 0x40)
    hdr[0x40:0x44] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', hdr, 0x44, 0x14c, len(secs), 0, 0, 0, 224, 0x2102)
    o = 0x58
    struct.pack_into('<H', hdr, o, 0x10b)
    struct.pack_into('<I', hdr, o + 16, TEXT); struct.pack_into('<I', hdr, o + 28, BASE)
    struct.pack_into('<II', hdr, o + 32, 0x1000, 0x200)
    struct.pack_into('<II', hdr, o + 56, 0x6000, 0x400)
    struct.pack_into('<I', hdr, o + 92, 16)
    struct.pack_into('<II', hdr, o + 96 + 8, RD + 0x40, 40)
    relocsec = [s for s in secs if s[0] == b'.reloc'][0]
    struct.pack_into('<II', hdr, o + 96 + 40, 0x5000, len(relocsec[2].rstrip(b'\0')))
    sh = o + 224
    out, fo = bytearray(hdr), 0x400
    for i, (nm, rva, body, ch) in enumerate(secs):
        struct.pack_into('<8sIIIIIIHHI', hdr, sh + 40 * i, nm, len(body), rva, len(body), fo, 0, 0, 0, 0, ch)
        out += body
        fo += len(body)
    out[0:0x400] = hdr
    return bytes(out)


fails = []


def check(name, got, want):
    if got != want:
        fails.append('%s: got %r, want %r' % (name, got, want))


def image(prog, drel=(), writes='', escape=None):
    secs, a = build(prog)
    img = argflow.Image(pe(secs, drel), 'syn.exe', {s: [(s, e)] for s, e in a.funcs.values()})
    bridge = 'dll\tname\tordinal\tconv\tret\targs\n' + ''.join('kernel32.dll\t%s\t\tstdcall\ti\t%s\n' % (n, 'i' * c) for n, c in
                                                          (('LoadLibraryA', 1), ('GetProcAddress', 2), ('HeapFree', 3), ('CloseHandle', 1)))
    esc = escape if escape is not None else 'dll\tname\tescape\nkernel32.dll\tLoadLibraryA\tn\nkernel32.dll\tGetProcAddress\tnn\n' \
        'kernel32.dll\tHeapFree\tnnn\nkernel32.dll\tCloseHandle\tn\n'
    info = memfx.ImportInfo(bridge, [esc], writes)
    img.import_stack = info.stack
    return img, memfx.Memfx(img, info), a


def keeps(prog, fname, reg, which=0, **kw):
    """call_keeps for the nth call instruction of function fname."""
    img, mx, a = image(prog, **kw)
    s, e = a.funcs[fname]
    fn = img.func(s)
    calls = [v for v, i in sorted(fn.nodes.items()) if i.mnemonic == 'call']
    return mx.call_keeps(fn, calls[which], reg)[0]


def leaf(a, n=1):
    a.func('leaf'); a.ret(); a.end('leaf')


# frame slot / field of a private object: lea esi,[ebp-8]; mov [esi],eax; call g; mov eax,[esi]
def frame_prog(callee_body):
    def prog(a):
        callee_body(a)
        a.func('f')
        a.raw(0x55, 0x8b, 0xec, 0x83, 0xec, 0x10)           # push ebp; mov ebp,esp; sub esp,16
        a.raw(0x56, 0x8d, 0x75, 0xf8)                       # push esi; lea esi,[ebp-8]
        a.raw(0x89, 0x06)                                   # mov [esi], eax
        a.call('g')
        a.raw(0x8b, 0x06)                                   # mov eax,[esi]
        a.raw(0x5e, 0xc9, 0xc3); a.end('f')
    return prog


def g_leaf(a):
    a.func('g'); a.raw(0x31, 0xc0); a.ret(); a.end('g')


def g_param(a):             # writes through its register parameter: mov [esi+0],eax uses the caller's esi (custom convention)
    a.func('g'); a.raw(0x89, 0x06); a.ret(); a.end('g')


def g_store_other(a):       # writes through a pointer of unknown origin (a loaded pointer)
    a.func('g'); a.raw(0xa1); a.rel.append(TEXT + len(a.b)); a.b += struct.pack('<I', G); a.raw(0x89, 0x08); a.ret(); a.end('g')


def g_import(a):
    a.func('g'); a.push(G + 0x200); a.call_imp('CloseHandle'); a.ret(); a.end('g')


check('private frame object, leaf callee', keeps(frame_prog(g_leaf), 'f', 'esi'), True)
check('callee writes through the register that holds the frame address', keeps(frame_prog(g_param), 'f', 'esi'), False)
check('callee writes through an unknown pointer, frame address never escapes', keeps(frame_prog(g_store_other), 'f', 'esi'), True)


def esc_prog(a):            # the frame address is stored to a global: it escapes
    g_store_other(a)
    a.func('f')
    a.raw(0x55, 0x8b, 0xec, 0x83, 0xec, 0x10, 0x56, 0x8d, 0x75, 0xf8)
    a.raw(0x89, 0x35); a.rel.append(TEXT + len(a.b)); a.b += struct.pack('<I', G + 0x10)     # mov [G+16], esi
    a.call('g'); a.raw(0x8b, 0x06, 0x5e, 0xc9, 0xc3); a.end('f')


check('frame address escaped to a global: a call through an unknown pointer may write', keeps(esc_prog, 'f', 'esi'), False)


def callee_passes(a):       # g(esi) passes its esi on to a callee that writes through it
    g_param_ = lambda: None
    a.func('h'); a.raw(0x89, 0x06); a.ret(); a.end('h')
    a.func('g'); a.call('h'); a.ret(); a.end('g')


check('frame address passed down two levels, written there', keeps(frame_prog(callee_passes), 'f', 'esi'), False)


def callee_rec(a):          # g and h call each other; neither writes
    a.func('h'); a.call('g'); a.ret(); a.end('h')
    a.func('g'); a.raw(0x85, 0xc0, 0x74, 0x05); a.call('h'); a.ret(); a.end('g')


check('recursion fixpoint, no write', keeps(frame_prog(callee_rec), 'f', 'esi'), True)


def callee_rec_w(a):        # the recursion writes
    a.func('h'); a.raw(0x89, 0x06); a.call('g'); a.ret(); a.end('h')
    a.func('g'); a.raw(0x85, 0xc0, 0x74, 0x05); a.call('h'); a.ret(); a.end('g')


check('recursion fixpoint, write inside the cycle', keeps(frame_prog(callee_rec_w), 'f', 'esi'), False)


def indirect_prog(a):       # call eax: the call target is not known
    a.func('f')
    a.raw(0x55, 0x8b, 0xec, 0x83, 0xec, 0x10, 0x56, 0x8d, 0x75, 0xf8, 0x89, 0x06)
    a.raw(0xff, 0xd0)
    a.raw(0x8b, 0x06, 0x5e, 0xc9, 0xc3); a.end('f')


check('indirect call receiving the tainted esi', keeps(indirect_prog, 'f', 'esi'), False)


def imp_prog(a):            # frame address passed to an import
    a.func('f')
    a.raw(0x55, 0x8b, 0xec, 0x83, 0xec, 0x10, 0x56, 0x8d, 0x75, 0xf8, 0x89, 0x06)
    a.raw(0x56); a.call_imp('CloseHandle')
    a.raw(0x8b, 0x06, 0x5e, 0xc9, 0xc3); a.end('f')


check('frame address passed to an import with no write facts', keeps(imp_prog, 'f', 'esi'), False)
check('... the import is declared to write through no argument', keeps(imp_prog, 'f', 'esi',
                                                                       writes='dll\tname\twrites\nkernel32.dll\tCloseHandle\tn\n'), True)
check('... the import escapes the argument', keeps(imp_prog, 'f', 'esi', writes='dll\tname\twrites\nkernel32.dll\tCloseHandle\tn\n',
                                                   escape='dll\tname\tescape\nkernel32.dll\tCloseHandle\te\n'), False)


# ---- globals
def glob_prog(body):
    def prog(a):
        body(a)
        a.func('rd'); a.push(G + 0x100); a.push(0); a.call_imp('GetProcAddress'); a.ret(); a.end('rd')
    return prog


def const(prog, lo, hi, **kw):
    img, mx, a = image(prog, **kw)
    return mx.const_range(lo, hi)


v = const(glob_prog(lambda a: None), G + 0x100, G + 0x106)
check('global read only by pointers passed to a no-write import', (v.ok, [r for r in v.reasons if 'import' in r][:1] != []), (False, True))
v = const(glob_prog(lambda a: None), G + 0x100, G + 0x106, writes='dll\tname\twrites\nkernel32.dll\tGetProcAddress\tnn\n')
check('global constant when the import writes nowhere', v.ok, True)
v = const(glob_prog(lambda a: None), G + 0x100, G + 0x106, writes='dll\tname\twrites\nkernel32.dll\tGetProcAddress\tnn\n',
          escape='dll\tname\tescape\nkernel32.dll\tGetProcAddress\tee\n')
check('global handed to an import that retains it', v.ok, False)


def w_direct(a):
    a.func('w'); a.raw(0xc6, 0x05); a.rel.append(TEXT + len(a.b)); a.b += struct.pack('<I', G + 0x102); a.raw(0x41); a.ret(); a.end('w')   # mov byte [G+0x102], 0x41


v = const(glob_prog(w_direct), G + 0x100, G + 0x106, writes='dll\tname\twrites\nkernel32.dll\tGetProcAddress\tnn\n')
check('global written by another function directly', (v.ok, any('direct store' in r for r in v.reasons)), (False, True))
v = const(glob_prog(w_direct), G + 0x108, G + 0x10c, writes='dll\tname\twrites\nkernel32.dll\tGetProcAddress\tnn\n')
check('a direct store elsewhere does not matter', v.ok, True)


def w_idx_unbounded(a):     # mov [G + eax*4], ecx with eax unknown (a parameter register)
    a.func('w'); a.raw(0x89, 0x0c, 0x85); a.rel.append(TEXT + len(a.b)); a.b += struct.pack('<I', G); a.ret(); a.end('w')


def w_idx_bounded(a):       # and eax,3 ; mov [G + eax*4], ecx
    a.func('w'); a.raw(0x83, 0xe0, 0x03, 0x89, 0x0c, 0x85); a.rel.append(TEXT + len(a.b)); a.b += struct.pack('<I', G); a.ret(); a.end('w')


wi = 'dll\tname\twrites\nkernel32.dll\tGetProcAddress\tnn\n'
v = const(glob_prog(w_idx_unbounded), G + 0x100, G + 0x106, writes=wi)
check('indexed write, unbounded index covers the section', (v.ok, any('unbounded' in r for r in v.reasons)), (False, True))
v = const(glob_prog(w_idx_bounded), G + 0x100, G + 0x106, writes=wi)
check('indexed write, proven bound does not reach the string', v.ok, True)
v = const(glob_prog(w_idx_bounded), G + 8, G + 12, writes=wi)
check('indexed write, proven bound covers G+8..G+16', v.ok, False)
v = const(glob_prog(w_idx_bounded), G + 0x10, G + 0x14, writes=wi)
check('indexed write, [G+0..G+0x10) bound excludes G+0x10', v.ok, True)


def w_cmp(jcc, k):           # cmp eax,k ; jcc over the store ; mov [G + eax*4], ecx ; ret
    def prog(a):
        a.func('w'); a.raw(0x83, 0xf8, k, jcc, 7, 0x89, 0x0c, 0x85); a.rel.append(TEXT + len(a.b)); a.b += struct.pack('<I', G); a.ret(); a.end('w')
    return prog


v = const(glob_prog(w_cmp(0x77, 3)), G + 0x10, G + 0x14, writes=wi)
check('cmp eax,3 ; ja over the store: the store runs with eax<=3, covers [G, G+0x10)', v.ok, True)
v = const(glob_prog(w_cmp(0x77, 3)), G + 0x0c, G + 0x10, writes=wi)
check('... and the last element is written', v.ok, False)
v = const(glob_prog(w_cmp(0x73, 4)), G + 0x10, G + 0x14, writes=wi)
check('cmp eax,4 ; jae over the store: eax<=3', v.ok, True)
v = const(glob_prog(w_cmp(0x72, 3)), G + 0x100, G + 0x106, writes=wi)
check('cmp eax,3 ; jb over the store: the store runs with eax>=3, no bound, the section is covered', v.ok, False)
v = const(glob_prog(w_cmp(0x7f, 3)), G + 0x100, G + 0x106, writes=wi)
check('a signed jump gives no bound', v.ok, False)


def leak_prog(a):           # the address of a global kept in a data word
    a.func('rd'); a.push(G + 0x100); a.push(0); a.call_imp('GetProcAddress'); a.ret(); a.end('rd')


img_, mx_, a_ = image(leak_prog, drel=[(DATA + 0x40, G + 0x20)], writes=wi)
v = mx_.const_range(G + 0x100, G + 0x106)
check('address leaked by a data relocation: not constant', (v.ok, any('data word' in r for r in v.reasons)), (False, True))


def ptrwrite_prog(a):       # the address is passed to a local function that writes through it
    a.func('w'); a.raw(0x8b, 0x54, 0x24, 0x04, 0xc6, 0x02, 0x41, 0x31, 0xd2); a.ret(4); a.end('w')     # mov eax,[esp+4]; mov byte [eax],'A'
    a.func('rd'); a.push(G + 0x100); a.call('w'); a.ret(); a.end('rd')


v = const(ptrwrite_prog, G + 0x100, G + 0x106, writes=wi)
check('global written through a pointer passed to a local function', (v.ok, any('write through a pointer' in r for r in v.reasons)), (False, True))
v = const(ptrwrite_prog, G + 0x180, G + 0x184, writes=wi)
check('exact pointer does not reach another address', (v.ok, v.reasons), (True, []))


def storeptr_prog(a):       # the address is stored to memory
    a.func('rd'); a.raw(0xc7, 0x05); a.rel.append(TEXT + len(a.b)); a.b += struct.pack('<I', G + 0x30)
    a.rel.append(TEXT + len(a.b)); a.b += struct.pack('<I', G + 0x100); a.ret(); a.end('rd')      # mov dword [G+0x30], G+0x100


v = const(storeptr_prog, G + 0x100, G + 0x106, writes=wi)
check('address stored to memory escapes', (v.ok, any('escape' in r for r in v.reasons)), (False, True))
v = const(glob_prog(lambda a: None), BASE + RD, BASE + RD + 4, writes=wi)
check('import slot is written by the loader', (v.ok, any('loader' in r for r in v.reasons)), (False, True))
v = const(glob_prog(lambda a: None), BASE + TEXT + 0x300, BASE + TEXT + 0x304)
check('read-only section is constant', v.ok, True)

# ---- argflow: A7 replaced by the rule: a CreateFile-style handle stored through a pointer register, loaded after a call
def handle_prog(callee):
    def prog(a):
        callee(a)
        a.func('f')
        a.raw(0x55, 0x8b, 0xec, 0x83, 0xec, 0x10, 0x56, 0x8d, 0x75, 0xf8)      # frame object at esi
        a.push(G + 0x100); a.call_imp('LoadLibraryA')                          # eax = LoadLibraryA("Name1")
        a.raw(0x89, 0x06)
        a.call('g')
        a.raw(0xff, 0x36)                                                      # push [esi]
        a.push(G + 0x100); a.raw(0x83, 0xec, 0x04, 0x83, 0xc4, 0x04)           # push name; (balanced noise)
        a.call_imp('GetProcAddress'); a.raw(0x5e, 0xc9, 0xc3); a.end('f')
    return prog


def origin(callee, mode):
    img, mx, a = image(handle_prog(callee), writes=wi)
    s, e = a.funcs['f']
    fn = img.func(s)
    gp = [v for v, i in sorted(fn.nodes.items()) if i.mnemonic == 'call'][-1]
    # argument 1 of GetProcAddress is hModule?  The args pushed: [esi] first (= arg 1), name pushed last (= arg 0); read arg 1
    ok, orgs, brk = argflow.handle_origin(img, gp, 1, mode)
    return ok


check('handle through [esi] across a leaf call: rule closes', origin(g_leaf, 'rule'), True)
check('handle through [esi] across a writing call: open', origin(g_param, 'rule'), False)
check('handle through [esi]: strict (no call allowed) stays open', origin(g_leaf, 'strict'), False)

if fails:
    print('FAIL\n' + '\n'.join(fails))
    sys.exit(1)
print('ok test_memfx')
