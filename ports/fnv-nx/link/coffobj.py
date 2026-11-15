#!/usr/bin/env python3
"""coffobj - an in-process reader of i386 COFF objects (the objects MSVC 2008 writes): sections, symbols, relocations.

    read(path) / parse(data, where) -> Coff
    Coff.sections   [Section(name, chars, data, relocs)]   number i+1 is sections[i]; name: bytes
                    relocs: [(offset, symbol index, type)] in file order
    Coff.symbols    {index: Sym(name, value, sec, type, cls, naux)}  only the primary records; the aux records of a symbol
                    are skipped (their indices are not in the dict, so a relocation naming one is an error)
    Coff.blobs(name)  the contents of every section of that name (bytes), in section order

Format: machine IMAGE_FILE_MACHINE_I386 (0x14c); section headers 40 bytes after the optional header; symbols 18 bytes;
names longer than 8 bytes in the string table (`/n` for sections).  Every defect is ValueError('<where>: <what>'): wrong
machine, a table or a section outside the file, a string outside the string table or without NUL, a section number of a
symbol that is not -2, -1, 0 or a section, a relocation whose symbol index is out of range or an aux record, extended
relocation counts (IMAGE_SCN_LNK_NRELOC_OVFL).  Pure: bytes in, tuples out.
"""
import collections
import struct

I386 = 0x14c
SCN_CNT_CODE, SCN_LNK_COMDAT, SCN_LNK_NRELOC_OVFL = 0x20, 0x1000, 0x01000000
CLS_EXTERNAL, CLS_STATIC = 2, 3
Section = collections.namedtuple('Section', 'name chars data relocs')
Sym = collections.namedtuple('Sym', 'name value sec type cls naux')


class Coff:
    def __init__(self, b, where):
        self.where = where
        if len(b) < 20:
            raise ValueError('%s: COFF header cut short' % where)
        machine, nsec, _ts, symoff, nsym, opt, _ch = struct.unpack_from('<HHIIIHH', b, 0)
        if machine != I386:
            raise ValueError('%s: machine 0x%x is not i386' % (where, machine))
        if nsym and symoff + 18 * nsym + 4 > len(b):
            raise ValueError('%s: symbol table outside the file' % where)
        stroff = symoff + 18 * nsym
        strsize = 0
        if nsym:
            strsize, = struct.unpack_from('<I', b, stroff)
            if strsize < 4 or stroff + strsize > len(b):
                raise ValueError('%s: string table outside the file' % where)

        def string(off, what):
            if off < 4 or off >= strsize:
                raise ValueError('%s: %s outside the string table' % (where, what))
            end = b.find(b'\0', stroff + off, stroff + strsize)
            if end < 0:
                raise ValueError('%s: %s has no terminating NUL' % (where, what))
            return b[stroff + off:end]

        if 20 + opt + 40 * nsec > len(b):
            raise ValueError('%s: section headers outside the file' % where)
        self.sections = []
        for k in range(nsec):
            o = 20 + opt + 40 * k
            raw, _vs, _va, size, ptr, relptr, _lp, nrel, _nl, chars = struct.unpack_from('<8sIIIIIIHHI', b, o)
            if raw[:1] == b'/':
                digits = raw[1:].rstrip(b'\0')
                if not digits.isdigit():
                    raise ValueError('%s: section %d name %r' % (where, k + 1, raw))
                name = string(int(digits), 'section %d name' % (k + 1))
            else:
                name = raw.rstrip(b'\0')
            if chars & SCN_LNK_NRELOC_OVFL:
                raise ValueError('%s: section %d has extended relocations, not supported' % (where, k + 1))
            if size and ptr + size > len(b):
                raise ValueError('%s: section %d lies outside the file' % (where, k + 1))
            if relptr + 10 * nrel > len(b):
                raise ValueError('%s: relocations of section %d lie outside the file' % (where, k + 1))
            rels = [struct.unpack_from('<IIH', b, relptr + 10 * r) for r in range(nrel)]
            self.sections.append(Section(name, chars, b[ptr:ptr + size] if size else b'', rels))
        self.symbols = {}
        i = 0
        while i < nsym:
            o = symoff + 18 * i
            raw = b[o:o + 8]
            val, sec, typ, cls, naux = struct.unpack_from('<IhHBB', b, o + 8)
            if raw[:4] == b'\0\0\0\0':
                name = string(struct.unpack_from('<I', raw, 4)[0], 'symbol %d name' % i)
            else:
                name = raw.rstrip(b'\0')
            if sec > nsec or sec < -2:
                raise ValueError('%s: symbol %d names section %d' % (where, i, sec))
            if i + 1 + naux > nsym:
                raise ValueError('%s: aux records of symbol %d outside the table' % (where, i))
            self.symbols[i] = Sym(name, val, sec, typ, cls, naux)
            i += 1 + naux
        for s in self.sections:
            for _off, si, _ty in s.relocs:
                if si not in self.symbols:
                    raise ValueError('%s: a relocation names symbol %d, which is no symbol record' % (where, si))

    def blobs(self, name):
        return [s.data for s in self.sections if s.name == name]

    def externals(self):
        """{name (bytes): 'def' | 'comdat' | 'undef' | 'common' | 'abs'} of the class-external symbols; a definition wins
        over an undefined record of the same name"""
        out = {}
        for s in self.symbols.values():
            if s.cls != CLS_EXTERNAL:
                continue
            if s.sec == 0:
                kind = 'undef' if s.value == 0 else 'common'
            elif s.sec < 0:
                kind = 'abs' if s.sec == -1 else 'debug'
            else:
                kind = 'comdat' if self.sections[s.sec - 1].chars & SCN_LNK_COMDAT else 'def'
            if kind != 'undef' or s.name not in out:
                out[s.name] = kind
        return out


def parse(data, where):
    return Coff(data, where)


def read(path):
    try:
        with open(path, 'rb') as fh:
            data = fh.read()
    except OSError as e:
        raise ValueError('%s: cannot read: %s' % (path.rsplit('/', 1)[-1], e.strerror))
    return Coff(data, path.rsplit('/', 1)[-1])
