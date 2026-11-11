"""Hand-assembled PE32 images for the synthetic tests (no game data): .text, .rdata with imports, .data, .data2, .reloc."""
import os, struct, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import argflow, memfx

BASE, TEXT, RD, DATA, DATA2 = 0x400000, 0x1000, 0x2000, 0x3000, 0x4000
IMPS = ['CreateFileA', 'CreateFileW', 'CreateFileMappingA', 'CloseHandle']
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


