#!/usr/bin/env python3
"""elfobj - an in-process reader of ELF relocatable objects (sections and symbol table), no tool output, no shelling out.

    read(path)           -> Elf       (reads the file; every defect is ValueError('<where>: <what>'))
    parse(data, where)   -> Elf
    Elf.section(name)    -> bytes or None   the contents of the section of that name (None: no such section; NOBITS: b'');
                            ValueError when several sections carry the asked name (names are not unique: one `.group` per COMDAT)
    Elf.symbols()        -> [(name, bind, type, shndx, value, size, other)] of the one SHT_SYMTAB (ValueError: none)
    Elf.section_flags(i) -> sh_flags of section number i

Format: ELF32 or ELF64, little endian, e_type ET_REL.  Pure: bytes in, tuples out.  A defect is an error, never a guess:
bad magic/class/data/type, a header or table outside the file, a section outside the file (except NOBITS), a name outside
its string table or without NUL, a compressed section (SHF_COMPRESSED), a second symbol table, a
symbol whose section index is SHN_XINDEX or names no section (other than SHN_UNDEF, SHN_ABS, SHN_COMMON), a symbol table
whose size is not a multiple of its entry size.
"""
import struct

SHT_SYMTAB, SHT_STRTAB, SHT_NOBITS = 2, 3, 8
SHF_GROUP, SHF_COMPRESSED = 0x200, 0x800
SHN_UNDEF, SHN_ABS, SHN_COMMON, SHN_XINDEX = 0, 0xfff1, 0xfff2, 0xffff
STB_LOCAL, STB_GLOBAL, STB_WEAK = 0, 1, 2
STT_SECTION, STT_FILE = 3, 4


def _cstr(data, off, where, what):
    if off < 0 or off >= len(data):
        raise ValueError('%s: %s outside its string table' % (where, what))
    end = data.find(b'\0', off)
    if end < 0:
        raise ValueError('%s: %s has no terminating NUL' % (where, what))
    try:
        return data[off:end].decode('utf-8')
    except UnicodeDecodeError:
        raise ValueError('%s: %s is not UTF-8' % (where, what))


class Elf:
    def __init__(self, data, where):
        self.data, self.where = data, where
        if len(data) < 16 or data[:4] != b'\x7fELF':
            raise ValueError('%s: not an ELF file' % where)
        if data[4] not in (1, 2):
            raise ValueError('%s: unknown ELF class %d' % (where, data[4]))
        if data[5] != 1:
            raise ValueError('%s: not little endian' % where)
        self.is64 = data[4] == 2
        try:
            if self.is64:
                etype, = struct.unpack_from('<H', data, 16)
                shoff, = struct.unpack_from('<Q', data, 0x28)
                shentsize, shnum, shstrndx = struct.unpack_from('<HHH', data, 0x3a)
                want = 64
            else:
                etype, = struct.unpack_from('<H', data, 16)
                shoff, = struct.unpack_from('<I', data, 0x20)
                shentsize, shnum, shstrndx = struct.unpack_from('<HHH', data, 0x2e)
                want = 40
        except struct.error:
            raise ValueError('%s: ELF header cut short' % where)
        if etype != 1:
            raise ValueError('%s: not a relocatable object (e_type %d)' % (where, etype))
        if shnum == 0 and shoff == 0:
            raise ValueError('%s: no section header table' % where)
        if shnum == 0 or shstrndx >= shnum:
            raise ValueError('%s: extended section numbering or bad e_shstrndx is not supported' % where)
        if shentsize != want:
            raise ValueError('%s: e_shentsize %d, expected %d' % (where, shentsize, want))
        if shoff + shnum * shentsize > len(data):
            raise ValueError('%s: section header table outside the file' % where)
        self._sec = []
        for i in range(shnum):
            o = shoff + i * shentsize
            if self.is64:
                name, typ, flags, _a, off, size, link, info, _al, entsize = struct.unpack_from('<IIQQQQIIQQ', data, o)
            else:
                name, typ, flags, _a, off, size, link, info, _al, entsize = struct.unpack_from('<IIIIIIIIII', data, o)
            if typ != SHT_NOBITS and off + size > len(data):
                raise ValueError('%s: section %d lies outside the file' % (where, i))
            self._sec.append((name, typ, flags, off, size, link, info, entsize))
        if self._sec[shstrndx][1] != SHT_STRTAB:
            raise ValueError('%s: e_shstrndx is not a string table' % where)
        so, ss = self._sec[shstrndx][3], self._sec[shstrndx][4]
        strtab = data[so:so + ss]
        self.names = [_cstr(strtab, s[0], where, 'section %d name' % i) for i, s in enumerate(self._sec)]
        # section names are not unique in an ELF object (one `.group` per COMDAT, `.text.*` per function): the index
        # keeps every section of a name, and only asking for a name that several sections carry is an error
        self.by_name = {}
        for i, n in enumerate(self.names):
            self.by_name.setdefault(n, []).append(i)

    def section(self, name):
        ids = self.by_name.get(name)
        if ids is None:
            return None
        if len(ids) != 1:
            raise ValueError('%s: %d sections are named %s' % (self.where, len(ids), name))
        i = ids[0]
        _n, typ, flags, off, size, _l, _i, _e = self._sec[i]
        if flags & SHF_COMPRESSED:
            raise ValueError('%s: section %s is compressed' % (self.where, name))
        return b'' if typ == SHT_NOBITS else self.data[off:off + size]

    def section_flags(self, i):
        return self._sec[i][2]

    def symbols(self):
        tabs = [i for i, s in enumerate(self._sec) if s[1] == SHT_SYMTAB]
        if len(tabs) != 1:
            raise ValueError('%s: %d symbol tables, expected 1' % (self.where, len(tabs)))
        _n, _t, _f, off, size, link, _i, entsize = self._sec[tabs[0]]
        ent = 24 if self.is64 else 16
        if entsize != ent or size % ent:
            raise ValueError('%s: symbol table entry size %d / table size %d' % (self.where, entsize, size))
        if link >= len(self._sec) or self._sec[link][1] != SHT_STRTAB:
            raise ValueError('%s: the symbol table has no string table' % self.where)
        so, ss = self._sec[link][3], self._sec[link][4]
        strtab = self.data[so:so + ss]
        out = []
        for k in range(size // ent):
            if self.is64:
                nm, info, other, shndx, value, sz = struct.unpack_from('<IBBHQQ', self.data, off + k * ent)
            else:
                nm, value, sz, info, other, shndx = struct.unpack_from('<IIIBBH', self.data, off + k * ent)
            if shndx == SHN_XINDEX:
                raise ValueError('%s: symbol %d has SHN_XINDEX, not supported' % (self.where, k))
            if shndx >= len(self._sec) and shndx not in (SHN_ABS, SHN_COMMON):
                raise ValueError('%s: symbol %d names section %d, which does not exist' % (self.where, k, shndx))
            name = _cstr(strtab, nm, self.where, 'symbol %d name' % k) if nm else ''
            out.append((name, info >> 4, info & 15, shndx, value, sz, other))
        return out


def parse(data, where):
    return Elf(data, where)


def read(path):
    try:
        with open(path, 'rb') as fh:
            data = fh.read()
    except OSError as e:
        raise ValueError('%s: cannot read: %s' % (path.rsplit('/', 1)[-1], e.strerror))
    return Elf(data, path.rsplit('/', 1)[-1])
