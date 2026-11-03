#!/usr/bin/env python3
"""Generate a synthetic PE32 shaped like FalloutNV.exe for the S3 loader tests (no game content).

usage: gen_synth_pe.py OUTDIR [--seed N]

Sections sit at original-like addresses: .text 0x00401000..0x00fdf000 (virtual, small raw part), .rdata from
0x00fdf000 (IAT first), .data at 0x01180000 with a large .bss tail, .tls, a separate .bss, .rsrc. The data holds
every construct the scanner must classify: MSVC vftables with RTTI (CompleteObjectLocator at vftable[-1]), a vftable
without RTTI known only by name (hint), one known by neither, an initializer table, a TLS callback, script-command
style records, EH/SEH tables, string tails and round numbers that look like code addresses, loader structures that
hold RVAs (IAT/INT, SafeSEH, debug, resources) and imports (normal and delay-load).

Writes, per variant (plain, reloc = with a .reloc section, wrapped = Steam-like: .text scrambled and a .bind section,
wrapped_encdata = wrapped with .rdata scrambled too, badprobe = one code byte changed): <variant>.exe, plus
addrmap.tsv/.bin, hints.txt, imports.txt, image_check.json, dataprobes.txt, units_fake.json and for each
scan configuration expected_<cfg>.tsv (the exact fixup list) and expect_<cfg>.txt (loadcheck checks).
Every aligned word in the mapped sections whose value falls in .text is accounted for; generation fails otherwise.
"""
import argparse
import hashlib
import json
import os
import random
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'loader', 'tools'))
from fnvnx_formats import AddrMap, CLASSES, KINDS  # noqa: E402

BASE = 0x00400000
TEXT_RVA, TEXT_VSIZE, TEXT_RAW = 0x1000, 0xBDE000, 0x4000
RDATA_RVA, DATA_RVA, TLS_RVA, BSS_RVA, RSRC_RVA, RELOC_RVA = 0xBDF000, 0xD80000, 0xE70000, 0xE71000, 0xE74000, 0xE75000
TEXT_LO, TEXT_HI = BASE + TEXT_RVA, BASE + TEXT_RVA + TEXT_VSIZE
TIMESTAMP = 0x4E0B1234

COL, HINT, TLS, RELOC, RUN, ISOLATED, VPTR = 1, 2, 3, 4, 5, 6, 7
REJ_STRING, REJ_ROUND, UNKNOWN, MID = 0x81, 0x82, 0x83, 0x84
CONF = {COL: 3, HINT: 3, TLS: 3, RELOC: 3, RUN: 2, ISOLATED: 1, VPTR: 2, REJ_STRING: 0, REJ_ROUND: 0, UNKNOWN: 0, MID: 0}

# code addresses (original VAs) used by the constructs
TI_DTOR = 0x00402000
A0, A1, PURECALL = 0x00402010, 0x00402020, 0x00ec1230
B0, B2, B3_UNK = 0x00402030, 0x00402040, 0x00402050
C0, C1 = 0x00402060, 0x00402070
D0, D1, D2 = 0x00402080, 0x00402090, 0x004020a0
INIT1, INIT2, INIT3 = 0x00403000, 0x00403010, 0x00403020
TLSCB = 0x00403100
SEH_FUNC, SEH_FILTER, SEH_HANDLER = 0x00404000, 0x00404008, 0x0040400c
UNWIND_UNK = 0x00404567
THROW_DTOR = 0x00402100
CMD = [0x00405010, 0x00405020, 0x00405030]
ROUND = 0x00500000
CRT_NATIVE, CRT_NONATIVE = 0x00ec1300, 0x00ec1240
STR_TAIL, WSTR_TAIL = 0x00426170, 0x00410070
SAFESEH_DECOY = 0x00812340
DELAY_THUNK = 0x00406000


class Sec:
    def __init__(self, name, rva, vsize, chars):
        self.name, self.rva, self.vsize, self.chars = name, rva, vsize, chars
        self.buf = bytearray()

    @property
    def va(self):
        return BASE + self.rva

    def here(self):
        return self.va + len(self.buf)

    def at(self):
        """the next 4-aligned VA (what the next u32/dptr will be written at)"""
        self.align(4)
        return self.here()

    def align(self, a):
        while len(self.buf) % a:
            self.buf.append(0)

    def emit(self, data, align=4):
        self.align(align)
        va = self.here()
        self.buf += data
        return va

    def u32(self, v):
        return self.emit(struct.pack('<I', v & 0xffffffff))

    def patch(self, va, v):
        struct.pack_into('<I', self.buf, va - self.va, v & 0xffffffff)


class Synth:
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.rdata = Sec('.rdata', RDATA_RVA, DATA_RVA - RDATA_RVA, 0x40000040)
        self.data = Sec('.data', DATA_RVA, TLS_RVA - DATA_RVA, 0xC0000040)
        self.tls = Sec('.tls', TLS_RVA, 0x1000, 0xC0000040)
        self.rsrc = Sec('.rsrc', RSRC_RVA, 0x400, 0x40000040)
        self.relocs = set()        # VAs holding absolute addresses
        self.intended = {}         # loc -> (value, normal record or None if excluded, is_reloc)
        self.checks = []           # (kind, va, arg) loadcheck expectations independent of relocation
        self.dirs = {}
        self.map = {}              # orig -> (kind, flags, native)
        self.xranges = []          # import names: excluded by the scanners' import walk

    def sec_of(self, va):
        for s in (self.rdata, self.data, self.tls, self.rsrc):
            if s.va <= va < s.va + max(len(s.buf), 1):
                return s
        raise KeyError(hex(va))

    # pointers -------------------------------------------------------------------------------------------
    def dptr(self, sec, target):
        """absolute data pointer (relocated)"""
        va = sec.u32(target)
        self.relocs.add(va)
        return va

    def cptr(self, sec, target, klass, aux=0, reloc=True):
        va = sec.u32(target)
        self.intend(va, target, klass, aux, reloc)
        return va

    def intend(self, va, target, klass, aux=0, reloc=True):
        if reloc:
            self.relocs.add(va)
        self.intended[va] = (target, None if klass is None else (klass, CONF[klass], aux), reloc)

    def func(self, a, kind='FUNC', flags='', native=True):
        if native is True:
            native = (0x20000000 if kind == 'FUNC' else 0x21000000) + a if kind != 'MID' else None
        self.map[a] = (kind, flags, native or None)

    # build ----------------------------------------------------------------------------------------------
    def build(self):
        r, d = self.rdata, self.data
        # .rdata starts with the IAT; INT and names follow the descriptors
        imports = [('KERNEL32.dll', ['GetTickCount', 'Sleep', 'UnresolvedThing']), ('WSOCK32.dll', [3])]
        iat = {}
        iat_start = r.at()
        for dll, funcs in imports:
            iat[dll] = r.at()
            for _ in funcs:
                r.u32(0)
            r.u32(0)
        self.dirs[12] = (iat_start - BASE, r.at() - iat_start)
        desc = r.emit(bytes(20 * (len(imports) + 1)))
        self.dirs[1] = (desc - BASE, 20 * (len(imports) + 1))
        hn_rvas = []
        for k, (dll, funcs) in enumerate(imports):
            intva = r.at()
            for _ in funcs:
                r.u32(0)
            r.u32(0)
            name = r.emit(dll.encode() + b'\0')
            self.xranges.append((name, name + len(dll) + 1))
            struct.pack_into('<IIIII', r.buf, desc + 20 * k - r.va, intva - BASE, 0, 0, name - BASE, iat[dll] - BASE)
            for j, fn in enumerate(funcs):
                if isinstance(fn, int):
                    v = 0x80000000 | fn
                else:
                    hn = r.emit(struct.pack('<H', 0x10 + j) + fn.encode() + b'\0', align=2)
                    self.xranges.append((hn, hn + 3 + len(fn)))
                    v = hn - BASE
                    hn_rvas.append(v)
                r.patch(intva + 4 * j, v)
                r.patch(iat[dll] + 4 * j, v)
                for loc in (intva + 4 * j, iat[dll] + 4 * j):
                    if TEXT_LO <= v < TEXT_HI:
                        self.intend(loc, v, None, reloc=False)
        self.iat = iat
        # an RVA in the IAT/INT that equals a known function start: must stay excluded
        self.func(hn_rvas[1], native=True)

        # delay-load import (attrs=1: RVAs); its IAT initially points at a .text thunk
        dname = r.emit(b'XINPUT1_3.dll\0')
        dhn = r.emit(struct.pack('<H', 0) + b'XInputGetState\0', align=2)
        self.xranges += [(dname, dname + 14), (dhn, dhn + 17)]
        dint = r.u32(dhn - BASE)
        self.intend(dint, dhn - BASE, None, reloc=False)
        r.u32(0)
        diat = r.u32(DELAY_THUNK)
        self.intend(diat, DELAY_THUNK, None, reloc=True)
        r.u32(0)
        dhmod = d.u32(0)
        ddesc = r.emit(struct.pack('<8I', 1, dname - BASE, dhmod - BASE, diat - BASE, dint - BASE, 0, 0, 0) + bytes(32))
        self.dirs[13] = (ddesc - BASE, 64)
        self.func(DELAY_THUNK, 'TEXT', native=False)
        self.delay_iat = diat

        # strings that look like code addresses
        s1 = self.s1 = r.emit(b'Hi, paB\0')              # word at +4 is 0x00426170
        self.intend(s1 + 4, STR_TAIL, REJ_STRING, reloc=False)
        self.func(STR_TAIL)
        s2 = r.emit('!!pA\0'.encode('utf-16-le'))        # word at +4 is 0x00410070
        self.intend(s2 + 4, WSTR_TAIL, REJ_STRING, reloc=False)
        self.func(WSTR_TAIL)
        names = [r.emit(n + b'\0') for n in (b'alpha', b'beta', b'gamma')]
        self.names = names

        # RTTI type descriptors live in .data (type_info objects are writable)
        def td(name):
            va = d.u32(0)          # vptr patched once the type_info vftable exists
            d.u32(0)
            d.emit(name + b'\0', align=1)
            return va
        td_ti, td_a, td_b = td(b'.?AVtype_info@@'), td(b'.?AVBase@@'), td(b'.?AVDerived@@')
        self.tds = {'?AVtype_info@@': td_ti, '?AVBase@@': td_a, '?AVDerived@@': td_b}
        # '.?AVtype_info@@' ends with the word "o@@\0" = 0x0040406f, a .text address nobody knows: realistic noise
        self.intend(td_ti + 8 + 12, 0x0040406f, UNKNOWN, reloc=False)
        self.relocs.update({td_ti, td_a, td_b})

        def chd(bcds):
            bca = r.at()
            for _ in bcds:
                r.u32(0)
            c = r.emit(struct.pack('<III', 0, 1 if len(bcds) > 1 else 0, len(bcds)))
            self.dptr(r, bca)
            for k, (tdva) in enumerate(bcds):
                bcd = r.at()
                self.dptr(r, tdva)
                r.emit(struct.pack('<IiiiI', len(bcds) - 1 - k, 0, -1, 0, 0))
                self.dptr(r, c)
                r.patch(bca + 4 * k, bcd)
                self.relocs.add(bca + 4 * k)
            return c

        def col(tdva, chdva):
            c = r.emit(struct.pack('<III', 0, 0, 0))
            self.dptr(r, tdva)
            self.dptr(r, chdva)
            return c

        col_ti = col(td_ti, chd([td_ti]))
        col_a = col(td_a, chd([td_a]))
        col_b = col(td_b, chd([td_b, td_a]))

        # vftables: [COL][slots...]
        self.dptr(r, col_ti)
        vt_ti = self.cptr(r, TI_DTOR, COL, 0)
        self.dptr(r, col_a)
        vt_a = self.cptr(r, A0, COL, 0)
        self.cptr(r, A1, COL, 1)
        self.cptr(r, PURECALL, COL, 2)
        self.dptr(r, col_b)
        vt_b = self.cptr(r, B0, COL, 0)
        self.cptr(r, A1, COL, 1)
        self.cptr(r, B2, COL, 2)
        self.cptr(r, B3_UNK, COL, 3)
        vt_c = self.cptr(r, C0, HINT, 0)            # no RTTI, directly after B: only the hint separates them
        self.cptr(r, C1, HINT, 1)
        r.u32(0)
        vt_d = self.cptr(r, D0, RUN, 0)              # no RTTI, no hint: a run
        self.cptr(r, D1, RUN, 1)
        self.cptr(r, D2, RUN, 2)
        r.u32(0)
        xc_a = r.u32(0)                              # __xc_a .. __xc_z
        for k, f in enumerate((INIT1, INIT2, INIT3)):
            self.cptr(r, f, RUN, k)
            self.func(f, flags='init')
        r.u32(0)
        for va in (td_ti, td_a, td_b):
            d.patch(va, vt_ti)
        self.vt = dict(ti=vt_ti, a=vt_a, b=vt_b, c=vt_c, d=vt_d, xc=xc_a)
        for f in (TI_DTOR, A1, B0, B2, C0, C1, D0, D1, D2):
            self.func(f)
        self.func(A0, flags='deldtor')               # Base's ??_G: S2 kind G, gets the flags adapter
        self.func(PURECALL, 'TEXT', native=False)
        self.hints = sorted({vt_a, vt_c, names[0]})  # duplicate of a COL vftable, a real one, a bogus one

        # TLS directory and callbacks
        cbs = r.at()
        self.cptr(r, TLSCB, TLS, 0)
        r.u32(0)
        self.func(TLSCB)
        tls_index = d.u32(0)
        tdir = r.at()
        self.dptr(r, self.tls.va)
        self.dptr(r, self.tls.va + 8)
        self.dptr(r, tls_index)
        self.dptr(r, cbs)
        r.emit(bytes(8))
        self.dirs[9] = (tdir - BASE, 24)
        self.tls.emit(b'TLS!' + struct.pack('<I', 0x12345678))

        # load config with a SafeSEH table of RVAs
        cookie = d.u32(0xBB40E64E)
        seh = r.u32(SAFESEH_DECOY - BASE)
        r.u32(0x1230)
        self.intend(seh, SAFESEH_DECOY - BASE, None, reloc=False)
        self.func(SAFESEH_DECOY - BASE)               # the RVA value itself is a known address
        lc = r.emit(bytes(72))
        r.patch(lc, 72)
        r.patch(lc + 60, cookie)
        r.patch(lc + 64, seh)
        r.patch(lc + 68, 2)
        self.relocs.update({lc + 60, lc + 64})
        self.dirs[10] = (lc - BASE, 72)

        # debug directory with a CodeView record that happens to contain a known address
        dbg_data = r.emit(b'RSDS' + bytes([0xAA] * 16) + struct.pack('<I', 1) + b'synth.pdb\0\0\0' +
                          struct.pack('<I', A0))
        self.intend(dbg_data + 36, A0, None, reloc=False)
        ddir = r.emit(struct.pack('<IIHHIIII', 0, TIMESTAMP, 0, 0, 2, 40, dbg_data - BASE, 0))
        self.dbg_patch = (ddir + 24, dbg_data)
        self.dirs[6] = (ddir - BASE, 28)

        # EH: FuncInfo -> unwind map with a funclet address; ThrowInfo with a destructor
        unwind = r.emit(struct.pack('<i', -1))
        self.cptr(r, UNWIND_UNK, UNKNOWN)
        fi = r.u32(0x19930522)
        r.u32(1)
        self.dptr(r, unwind)
        r.emit(bytes(16))
        ct = r.u32(0)
        self.dptr(r, td_a)
        r.emit(struct.pack('<iiiII', 0, -1, 0, 4, 0))
        cta = r.u32(1)
        self.dptr(r, ct)
        ti = r.u32(0)
        self.cptr(r, THROW_DTOR, ISOLATED)
        r.u32(0)
        self.dptr(r, cta)
        self.func(THROW_DTOR)
        # SEH scope table: filter and handler are labels inside a function
        r.emit(struct.pack('<iiii', -2, 0, -52, 0))
        r.emit(struct.pack('<i', -2))
        self.cptr(r, SEH_FILTER, MID)
        self.cptr(r, SEH_HANDLER, MID)
        self.func(SEH_FUNC)
        self.func(SEH_FILTER, 'MID')
        self.func(SEH_HANDLER, 'MID')
        self.fi, self.ti = fi, ti

        # .data: an object with an MSVC vptr on disk, command records, sizes, CRT pointers, plain data
        self.obj = self.dptr(d, vt_a)
        self.vptrs = {self.obj: vt_a}               # rewritten to the native vtable of Base
        self.map[vt_a] = ('VTABLE', '', 0x22000000 + (vt_a & 0xffffff))
        d.u32(5)
        self.cmds = []
        for k, f in enumerate(CMD):
            self.dptr(d, names[k])
            self.cmds.append(self.cptr(d, f, ISOLATED))
            d.u32(0x10)
            d.u32(0)
            self.func(f)
        d.u32(7)
        self.round = d.u32(ROUND)
        self.intend(self.round, ROUND, REJ_ROUND, reloc=False)
        d.u32(0)
        self.func(ROUND)
        d.u32(0x11)
        self.crt1 = self.cptr(d, CRT_NATIVE, ISOLATED)
        d.u32(0x22)
        d.u32(0x33)
        self.crt2 = self.cptr(d, CRT_NONATIVE, ISOLATED)
        d.u32(0x44)
        self.func(CRT_NATIVE, 'TEXT')
        self.func(CRT_NONATIVE, 'TEXT', native=False)
        self.bss_var = BASE + BSS_RVA + 0x100
        self.pbss = self.dptr(d, self.bss_var)
        d.emit(struct.pack('<fffd', 1.0, 0.5, -1.0, 3.25), align=8)
        self.strtab = self.dptr(d, names[0])
        self.dptr(d, names[1])
        self.data_bss_tail = d.here() + 0x100

        # resources: bytes that must never be looked at
        rs = bytearray(self.rng.getrandbits(8) for _ in range(0x200))
        for k in range(0, 0x200, 64):
            struct.pack_into('<I', rs, k, A1)
        self.rsrc.emit(bytes(rs))
        self.dirs[2] = (self.rsrc.rva, 0x400)

        # filler known functions so the map is not trivially small
        for _ in range(3000):
            a = self.rng.randrange(TEXT_LO // 16 + 0x800, TEXT_HI // 16) * 16
            if a not in self.map:
                self.func(a, 'FUNC' if self.rng.random() < 0.9 else 'TEXT', native=self.rng.random() < 0.95)
        self.func(B3_UNK + 0x1000)   # near but not equal to B3_UNK, which stays unknown

    # file -----------------------------------------------------------------------------------------------
    def image(self, variant):
        rng = random.Random(1)
        text = bytearray(rng.getrandbits(8) for _ in range(TEXT_RAW))
        if variant == 'badprobe':
            text[0x10] ^= 0xff
        if variant.startswith('wrapped'):            # stands for SteamStub's encrypted .text (nothing decrypts it)
            text = bytearray(rng.getrandbits(8) for _ in range(TEXT_RAW))
        secs = [('.text', TEXT_RVA, TEXT_VSIZE, bytes(text), 0x60000020)]
        for s in (self.rdata, self.data, self.tls):
            raw = bytes(s.buf)
            if variant == 'wrapped_encdata' and s is self.rdata:
                raw = bytes(rng.getrandbits(8) for _ in range(len(raw)))
            secs.append((s.name, s.rva, s.vsize, raw, s.chars))
        secs.append(('.bss', BSS_RVA, 0x3000, b'', 0xC0000080))
        secs.append(('.rsrc', RSRC_RVA, self.rsrc.vsize, bytes(self.rsrc.buf), self.rsrc.chars))
        dirs = dict(self.dirs)
        if variant == 'reloc':
            blob = reloc_blob(self.relocs)
            secs.append(('.reloc', RELOC_RVA, len(blob), blob, 0x42000040))
            dirs[5] = (RELOC_RVA, len(blob))
        if variant.startswith('wrapped'):
            secs.append(('.bind', RELOC_RVA, 0x1000, b'\xcc' * 0x200, 0xE0000020))
        end = max(rva + vs for _, rva, vs, _, _ in secs)
        size_of_image = (end + 0xfff) & ~0xfff
        hdr = 0x400
        out = bytearray(hdr)
        offs = []
        for name, rva, vs, raw, ch in secs:
            off = len(out) if raw else 0
            if raw:
                out += raw + bytes(-len(raw) % 0x200)
            offs.append((off, len(raw) + (-len(raw) % 0x200) if raw else 0))
        # the debug directory records the file offset of its data
        dva, dbg = self.dbg_patch
        rd_off = offs[1][0]
        struct.pack_into('<I', out, rd_off + dva - self.rdata.va, rd_off + dbg - self.rdata.va)
        out[0:2] = b'MZ'
        struct.pack_into('<I', out, 0x3c, 0x80)
        out[0x80:0x84] = b'PE\0\0'
        chars = 0x0102 if variant == 'reloc' else 0x0103
        struct.pack_into('<HHIIIHH', out, 0x84, 0x14c, len(secs), TIMESTAMP, 0, 0, 224, chars)
        o = 0x98
        struct.pack_into('<HBBIIIIII', out, o, 0x10b, 9, 0, TEXT_RAW, 0x10000, 0, TEXT_RVA, TEXT_RVA, RDATA_RVA)
        struct.pack_into('<III', out, o + 28, BASE, 0x1000, 0x200)
        struct.pack_into('<HHHHHHI', out, o + 40, 5, 0, 0, 0, 5, 0, 0)
        struct.pack_into('<IIIHH', out, o + 56, size_of_image, hdr, 0, 2, 0)
        struct.pack_into('<IIIIII', out, o + 72, 0x100000, 0x1000, 0x100000, 0x1000, 0, 16)
        for i, (rva, size) in dirs.items():
            struct.pack_into('<II', out, o + 96 + 8 * i, rva, size)
        sh = o + 224
        for k, (name, rva, vs, raw, ch) in enumerate(secs):
            off, rawsize = offs[k]
            struct.pack_into('<8sIIIIIIHHI', out, sh + 40 * k, name.encode(), vs, rva, rawsize, off, 0, 0, 0, 0, ch)
        return bytes(out)

    def probes(self, exe):
        out = []
        for va, n in ((0x00401000, 255), (0x00402000, 100), (0x00403f00, 200)):
            off = 0x400 + va - TEXT_LO
            out.append(('0x%08x' % va, n, hashlib.sha256(exe[off:off + n]).hexdigest()))
        return out


def reloc_blob(relocs):
    pages = {}
    for va in sorted(relocs):
        pages.setdefault((va - BASE) & ~0xfff, []).append((va - BASE) & 0xfff)
    out = bytearray()
    for page in sorted(pages):
        ents = [0x3000 | o for o in pages[page]]
        if len(ents) % 2:
            ents.append(0)
        out += struct.pack('<II', page, 8 + 2 * len(ents)) + struct.pack('<%dH' % len(ents), *ents)
    return bytes(out)


def mapped_words(s):
    """(va, value) of every aligned word in the mapped sections"""
    for sec in (s.rdata, s.data, s.tls, s.rsrc):
        for k in range(0, len(sec.buf) - 3, 4):
            yield sec.va + k, struct.unpack_from('<I', sec.buf, k)[0]


def expected(s, cfg):
    """the exact scanner output for a configuration: hints | nohints | reloc"""
    out = [(loc, v, VPTR, CONF[VPTR], 0) for loc, v in s.vptrs.items()]
    for loc in sorted(s.intended):
        target, rec, is_reloc = s.intended[loc]
        if rec is None:
            continue
        klass, conf, aux = rec
        if cfg == 'nohints' and klass == HINT:       # C's slots then continue B's vftable
            klass, aux = COL, 4 + aux
        if cfg == 'reloc':
            if not is_reloc:
                continue
            if klass in (RUN, ISOLATED):
                klass, aux = RELOC, 0
            conf = CONF[klass]
        out.append((loc, target, klass, conf, aux))
    return sorted(out)


def native_of(s, target):
    e = s.map.get(target)
    return e[2] if e else None


def write_outputs(s, outdir):
    os.makedirs(outdir, exist_ok=True)
    # account for every .text-looking word
    dirs = [(BASE + rva, BASE + rva + size) for rva, size in s.dirs.values()] + s.xranges
    for va, v in mapped_words(s):
        in_dir = any(va < hi and lo < va + 4 for lo, hi in dirs)   # excluded wholesale by both scanners
        if TEXT_LO <= v < TEXT_HI and va not in s.intended and not in_dir:
            raise SystemExit('unaccounted .text-like word 0x%08x at 0x%08x' % (v, va))
    am = AddrMap()
    for a, (kind, flags, native) in s.map.items():
        info = KINDS[kind] | (0x100 if 'init' in flags else 0) | (0x800 if 'deldtor' in flags else 0)
        am.entries[a] = (info, native)
    am.write_tsv(os.path.join(outdir, 'addrmap.tsv'))
    with open(os.path.join(outdir, 'addrmap_s2.tsv'), 'w') as f:      # what S2's fnvlink.py writes
        f.write('# fnvnx-addrmap 1\n# address\tkind\tlink_symbol\tnative_symbol\tobject\tnote\n')
        for a in sorted(s.map):
            kind, flags, native = s.map[a]
            if kind == 'FUNC' and native:
                f.write('%08x\t%s\t__fnv_%08x\tsym_%08x\tsynth.o\t\n' % (a, 'G' if 'deldtor' in flags else 'F', a, a))
    open(os.path.join(outdir, 'addrmap.bin'), 'wb').write(am.to_bin())
    with open(os.path.join(outdir, 'hints.txt'), 'w') as f:
        f.writelines('0x%08x\n' % h for h in s.hints)
    with open(os.path.join(outdir, 'imports.txt'), 'w') as f:
        f.write('KERNEL32.dll GetTickCount 0x30000001\nKERNEL32.dll Sleep 0x30000002\nWSOCK32.dll #3 0x30000003\n'
                'XINPUT1_3.dll XInputGetState 0x30000004\n')
    # data fingerprints, directly and as the decorated names mkdataprobes.py decodes
    lits = [(s.names[0], b'alpha', '??_C@_05ABCDEFGH@alpha?$AA@'), (s.names[1], b'beta', '??_C@_04ABCDEFGH@beta?$AA@'),
            (s.names[2], b'gamma', '??_C@_05ABCDEFGH@gamma?$AA@'), (s.s1, b'Hi, paB', '??_C@_07ABCDEFGH@Hi?0?5paB?$AA@')]
    tds = [(s.tds[k], k) for k in ('?AVtype_info@@', '?AVBase@@', '?AVDerived@@')]
    with open(os.path.join(outdir, 'dataprobes.txt'), 'w') as f:
        for va, b, _ in lits:
            f.write('0x%08x %s\n' % (va, (b + b'\0').hex()))
        for va, k in tds:
            f.write('0x%08x %s\n' % (va + 8, ('.' + k).encode().hex()))
    syms = {n: '0x%08x' % va for va, _, n in lits}
    syms.update({'??_R0%s@8' % k: '0x%08x' % va for va, k in tds})
    json.dump({'units': [{'source': 'synthetic', 'functions': [], 'symbols': syms}]},
              open(os.path.join(outdir, 'units_fake.json'), 'w'), indent=1)
    for variant in ('plain', 'reloc', 'wrapped', 'wrapped_encdata', 'badprobe'):
        open(os.path.join(outdir, variant + '.exe'), 'wb').write(s.image(variant))
    plain = s.image('plain')
    probes = s.probes(plain)
    json.dump({'release': 'synthetic', 'probes': [list(p) for p in probes]},
              open(os.path.join(outdir, 'image_check.json'), 'w'), indent=1)
    for cfg in ('hints', 'nohints', 'reloc'):
        exp = expected(s, cfg)
        with open(os.path.join(outdir, 'expected_%s.tsv' % cfg), 'w') as f:
            for loc, tgt, k, c, aux in exp:
                f.write('0x%08x\t0x%08x\t%s\t%d\t%d\n' % (loc, tgt, CLASSES[k], c, aux))
        for trap in (False, True):
            for delta in ((0, 0x01c00000) if cfg == 'reloc' else (0,)):
                name = 'expect_%s%s%s.txt' % (cfg, '_trap' if trap else '', '_moved' if delta else '')
                write_expect(s, exp, os.path.join(outdir, name), trap, delta, cfg)
    # the Steam-like exe: code probes fail (encrypted .text), data probes carry the check, the rest is identical
    with open(os.path.join(outdir, 'expect_hints.txt')) as f:
        text = f.read().replace('stat probes_matched 3\n', 'stat probes_matched 0\nstat wrapped 1\n'
                                'stat data_probes_matched 7\n')
    with open(os.path.join(outdir, 'expect_wrapped.txt'), 'w') as f:
        f.write(text)


def write_expect(s, exp, path, trap, delta, cfg):
    L = []
    counts = {}
    applied = unresolved = skipped = adapted = 0
    for loc, tgt, k, c, aux in exp:
        counts[k] = counts.get(k, 0) + 1
        if k >= 0x80:
            skipped += 1
            L.append('word 0x%08x 0x%08x' % (loc + delta, tgt + delta))   # never rewritten
            continue
        nat = native_of(s, tgt)
        if nat and 'deldtor' in s.map[tgt][1]:      # loadcheck's stand-in for the adapter address
            adapted += 1
            L.append('word 0x%08x 0x%08x' % (loc + delta, 0xdd000000 | (nat & 0xffffff)))
        elif nat:
            applied += 1
            L.append('word 0x%08x 0x%08x' % (loc + delta, nat))
        else:
            unresolved += 1
            L.append('word 0x%08x 0x%08x' % (loc + delta, (0xee000000 | tgt) if trap else tgt + delta))
    for name, kid in (('vtable_col', COL), ('vtable_hint', HINT), ('tls', TLS), ('reloc', RELOC), ('run', RUN),
                      ('isolated', ISOLATED), ('rej_string', REJ_STRING), ('rej_round', REJ_ROUND),
                      ('unknown', UNKNOWN), ('mid', MID), ('vptr', VPTR)):
        L.append('stat fx_%s %d' % (name, counts.get(kid, 0)))
    L.append('stat fx_total %d' % len(exp))
    L.append('stat apply_applied %d' % applied)
    L.append('stat apply_%s %d' % ('trapped' if trap else 'unresolved', unresolved))
    L.append('stat apply_mismatched 0')
    L.append('stat apply_skipped %d' % skipped)
    L.append('stat apply_adapted %d' % adapted)
    L.append('stat data_plain 1')
    L.append('stat vtables_col 3')
    L.append('stat vtables_hint %d' % (0 if cfg == 'nohints' else 1))
    L.append('stat vptr_refs 4')
    L.append('stat imp_dlls 2')
    L.append('stat imp_slots 4')
    L.append('stat imp_resolved 4')       # 3 + the delay-load one
    L.append('stat imp_unresolved 1')
    L.append('stat delay_dlls 1')
    L.append('stat delay_slots 1')
    L.append('stat probes_matched 3')
    # imports
    k32 = s.iat['KERNEL32.dll']
    for off, v in ((0, 0x30000001), (4, 0x30000002), (8, 0xdead0000), (12, 0)):
        L.append('word 0x%08x 0x%08x' % (k32 + off + delta, v))
    L.append('word 0x%08x 0x30000003' % (s.iat['WSOCK32.dll'] + delta))
    L.append('word 0x%08x 0x30000004' % (s.delay_iat + delta))
    # data-to-data pointers still lead to their data (relocated by delta when moved)
    L.append('deref 0x%08x %s' % (s.strtab + delta, b'alpha\0'.hex()))
    L.append('deref 0x%08x %s' % (s.strtab + 4 + delta, b'beta\0'.hex()))
    L.append('deref 0x%08x %s' % (s.cmds[2] - 4 + delta, b'gamma\0'.hex()))
    L.append('deref 0x%08x %s' % (s.pbss + delta, '00' * 16))
    L.append('zero 0x%08x 0x1000' % (s.data_bss_tail + delta))
    L.append('zero 0x%08x 0x3000' % (BASE + BSS_RVA + delta))
    L.append('word 0x%08x 0x12345678' % (BASE + TLS_RVA + 4 + delta))
    L.append('unmapped 0x%08x' % (TEXT_LO + delta))
    L.append('unmapped 0x%08x' % (BASE + RELOC_RVA + delta))   # discardable .reloc is not mapped
    with open(path, 'w') as f:
        f.write('\n'.join(L) + '\n')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('outdir')
    ap.add_argument('--seed', type=int, default=3)
    a = ap.parse_args()
    s = Synth(a.seed)
    s.build()
    write_outputs(s, a.outdir)
    print('synthetic PE: %d intended .text-like words, %d relocs, %d map entries' % (
        len(s.intended), len(s.relocs), len(s.map)))


if __name__ == '__main__':
    main()
