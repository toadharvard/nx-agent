"""abi_fixture - byte-built synthetic PE32 images with MSVC RTTI for the ABI1 tests (names are invented here, nothing is from the game).

    pe = PE(); a = pe.code(b'...'); v = pe.klass('.?AVA@@', [(slot addrs)], off=0, bases=[('.?AVA@@', 0, -1)])
    open(path, 'wb').write(pe.build())

Layout: image base 0x400000; .text at +0x1000, .rdata at +0x2000, .data at +0x3000, .reloc at +0x4000 (4 KiB each, raw file offset
0x400 + 0x1000 * index).  An empty import directory is present (exefacts.Image reads one).
"""
import struct

BASE = 0x400000
TEXT, RDATA, DATA, RELOC = BASE + 0x1000, BASE + 0x2000, BASE + 0x3000, BASE + 0x4000
SIZE = 0x1000


class PE:
    def __init__(self):
        self.text = bytearray()
        self.rdata = bytearray(b'\0' * 32)      # 0..19: the empty import directory
        self.data = bytearray(b'\0' * 16)
        self.relocs = []
        self.tds = {}

    def code(self, b):
        a = TEXT + len(self.text)
        self.text += b
        return a

    def _word(self, sec, base, v, reloc):
        o = len(sec)
        sec += struct.pack('<I', v & 0xffffffff)
        if reloc:
            self.relocs.append(base + o)
        return base + o

    def rw(self, v, reloc=False):
        return self._word(self.rdata, RDATA, v, reloc)

    def td(self, name):
        if name not in self.tds:
            a = DATA + len(self.data)
            self.data += struct.pack('<II', 0, 0) + name.encode() + b'\0'
            while len(self.data) % 4:
                self.data += b'\0'
            self.tds[name] = a
        return self.tds[name]

    def hierarchy(self, bases):
        bcds = []
        for name, mdisp, pdisp in bases:
            while len(self.rdata) % 4:
                self.rdata += b'\0'
            a = RDATA + len(self.rdata)
            self.rw(self.td(name), True)
            self.rw(0)
            self.rw(mdisp)
            self.rw(pdisp)
            self.rw(0)
            self.rw(0)
            bcds.append(a)
        bca = RDATA + len(self.rdata)
        for a in bcds:
            self.rw(a, True)
        chd = RDATA + len(self.rdata)
        self.rw(0)
        self.rw(0)
        self.rw(len(bases))
        self.rw(bca, True)
        return chd

    def klass(self, name, slots, off=0, bases=None, chd=None):
        """A vftable of class `name` at COL offset `off`; returns the vftable address (the first slot).  slots: addresses of .text."""
        chd = chd or self.hierarchy(bases or [(name, 0, -1)])
        col = RDATA + len(self.rdata)
        self.rw(0)
        self.rw(off)
        self.rw(0)
        self.rw(self.td(name), True)
        self.rw(chd, True)
        self.rw(col, True)         # the word before the vftable: pointer to the COL
        v = RDATA + len(self.rdata)
        for s in slots:
            self.rw(s, True)
        self.rw(0)
        return v

    def build(self):
        secs = [('.text', TEXT, self.text), ('.rdata', RDATA, self.rdata), ('.data', DATA, self.data)]
        blk = {}
        for r in sorted(self.relocs):
            blk.setdefault((r - BASE) & ~0xfff, []).append(r & 0xfff)
        rel = b''
        for page, es in sorted(blk.items()):
            ents = b''.join(struct.pack('<H', 0x3000 | e) for e in es)
            if len(ents) % 4:
                ents += b'\0\0'
            rel += struct.pack('<II', page, 8 + len(ents)) + ents
        secs.append(('.reloc', RELOC, bytearray(rel)))
        hdr = bytearray(0x400)
        hdr[0:2] = b'MZ'
        struct.pack_into('<I', hdr, 0x3c, 0x80)
        pe = 0x80
        hdr[pe:pe + 4] = b'PE\0\0'
        struct.pack_into('<HHIIIHH', hdr, pe + 4, 0x14c, len(secs), 0, 0, 0, 224, 0x102)
        struct.pack_into('<H', hdr, pe + 24, 0x10b)
        struct.pack_into('<I', hdr, pe + 24 + 28, BASE)
        struct.pack_into('<II', hdr, pe + 24 + 96 + 8, 0x2000, 20)        # import directory (empty)
        struct.pack_into('<II', hdr, pe + 24 + 96 + 40, 0x4000, len(rel))  # base relocations
        o = pe + 24 + 224
        body = b''
        for i, (n, va, b) in enumerate(secs):
            assert len(b) <= SIZE
            hdr[o:o + 8] = n.encode().ljust(8, b'\0')
            struct.pack_into('<IIII', hdr, o + 8, SIZE, va - BASE, SIZE, 0x400 + SIZE * i)
            o += 40
            body += bytes(b).ljust(SIZE, b'\0')
        return bytes(hdr) + body


def scene(d, thunk_n=8, g_addr=None, drop_secondary=False, d_native=8, vfptr=8, d1_kind='dtor.d1', drop_native_point=False, extra_slot=False):
    """exe + obj + bind of two classes `Der : A, B` (invented): returns (exe path, obj path, bind path, dict of addresses).
    Exe: vA [GA, Af], vB [GB, Bg], vDer0 [GD, Af], vDer8 [thunk(sub ecx,8 -> GD), Bg].  Native `_ZTV3Der`: point 8 (d 0): D1 D0 f; point 28 (d 8):
    thunk D1, thunk D0, g.  The keyword arguments break one fact each."""
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'contract'))
    import store

    def rel(at, target):
        return (target - (at + 5)).to_bytes(4, 'little', signed=True)
    pe = PE()
    Af, Bg, GA, GB, GD = (pe.code(b'\xc3') for _ in range(5))
    th = TEXT + len(pe.text)
    pe.code(b'\x83\xe9\x08\xe9' + rel(th + 3, GD))
    vA = pe.klass('.?AVA@@', [GA, Af])
    vB = pe.klass('.?AVB@@', [GB, Bg])
    vD0 = pe.klass('.?AVDer@@', [GD, Af], 0, [('.?AVDer@@', 0, -1), ('.?AVA@@', 0, -1), ('.?AVB@@', 8, -1)])
    chd = None
    vD8 = None if drop_secondary else pe.klass('.?AVDer@@', [th, Bg], 8, [('.?AVDer@@', 0, -1), ('.?AVA@@', 0, -1), ('.?AVB@@', 8, -1)])
    exe = os.path.join(d, 'scene.exe')
    open(exe, 'wb').write(pe.build())
    ob = 'u.o'
    z = '_ZTV3Der'
    t = lambda s: '_ZThn%d_%s' % (thunk_n, s)
    obj = {(ob, 'ztv'): {z: [(8, 0, ['_ZN3DerD1Ev', '_ZN3DerD0Ev', '_ZN1A1fEv']),
                             (28, d_native, [t('N3DerD1Ev'), t('N3DerD0Ev'), '_ZN1B1gEv'])]},
           (ob, 'vtpair'): [(z, 8, '??_7Der@@6B@', 0), (z, 28, '??_7Der@@6BB@@@', vfptr)],
           (ob, 'sympair'): [('_ZN3DerD1Ev', '??1Der@@UAE@XZ', d1_kind, 'def'), ('_ZN3DerD0Ev', '??_GDer@@UAEPAXI@Z', 'dtor.d0', 'def'),
                             ('_ZN1A1fEv', '?f@A@@UAEXXZ', 'func', 'ref'), ('_ZN1B1gEv', '?g@B@@UAEXXZ', 'func', 'ref'),
                             (t('N3DerD1Ev'), '', 'thunk', 'def'), (t('N3DerD0Ev'), '', 'thunk', 'def')],
           (ob, 'n3'): []}
    bind = dict(sym={(ob, '_ZN3DerD0Ev'): g_addr or GD, (ob, '_ZN3DerD1Ev'): GD + 0x100, (ob, '_ZN1A1fEv'): Af, (ob, '_ZN1B1gEv'): Bg},
                vt={(ob, z, 8): vD0}, n3={}, unbound_sym=[], unbound_vt=[])
    if vD8 is not None:
        bind['vt'][(ob, z, 28)] = vD8
    if drop_native_point:
        obj[(ob, 'ztv')][z] = obj[(ob, 'ztv')][z][:1]
        obj[(ob, 'vtpair')] = obj[(ob, 'vtpair')][:1]
    if extra_slot:
        obj[(ob, 'ztv')][z][0][2].append('_ZN1A1hEv')
    op, bp = os.path.join(d, 'obj.json'), os.path.join(d, 'bind.json')
    store.save(op, 'obj', obj)
    store.save(bp, 'bind', bind)
    return exe, op, bp, dict(Af=Af, Bg=Bg, GA=GA, GB=GB, GD=GD, th=th, vA=vA, vB=vB, vD0=vD0, vD8=vD8)
