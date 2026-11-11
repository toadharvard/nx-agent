"""Minimal reader for the i386 COFF objects MSVC 2008 writes (normal and /bigobj headers).

Only what synth_image.py needs: sections (name, characteristics, raw bytes, relocations, COMDAT selection and
associated section), symbols (name, value, section number, storage class, type) and the relocation records
(offset, symbol index, type). Section numbers are 1-based as in the file; symbol indices are the raw table
indices (aux records counted), which is what relocations refer to.
"""
import struct

SCN_CNT_CODE = 0x00000020
SCN_CNT_INIT = 0x00000040
SCN_CNT_UNINIT = 0x00000080
SCN_LNK_INFO = 0x00000200
SCN_LNK_REMOVE = 0x00000800
SCN_LNK_COMDAT = 0x00001000
SCN_LNK_NRELOC_OVFL = 0x01000000
SCN_MEM_DISCARDABLE = 0x02000000
SCN_MEM_EXECUTE = 0x20000000
SCN_MEM_READ = 0x40000000
SCN_MEM_WRITE = 0x80000000

REL_ABSOLUTE, REL_DIR32, REL_DIR32NB, REL_SECTION, REL_SECREL, REL_TOKEN, REL_SECREL7, REL_REL32 = \
    0x00, 0x06, 0x07, 0x0A, 0x0B, 0x0C, 0x0D, 0x14

SYM_EXTERNAL, SYM_STATIC, SYM_LABEL, SYM_FUNCTION, SYM_FILE = 2, 3, 6, 101, 103

BIGOBJ_UUID = bytes.fromhex('c7a1bad1eebaa94baf20faf66aa4dcb8')


class Section:
    __slots__ = ('index', 'name', 'size', 'flags', 'data', 'relocs', 'comdat_sel', 'assoc', 'number')

    def __init__(self, index, name, size, flags, data, relocs):
        self.index, self.name, self.size, self.flags, self.data, self.relocs = index, name, size, flags, data, relocs
        self.comdat_sel = 0      # COMDAT selection (1 nodup, 2 any, 3 same size, 4 exact, 5 associative, 6 largest)
        self.assoc = 0           # associated section for selection 5
        self.number = 0

    @property
    def align(self):
        a = (self.flags >> 20) & 0xf
        return 1 << (a - 1) if a else 16

    @property
    def is_code(self):
        return bool(self.flags & (SCN_CNT_CODE | SCN_MEM_EXECUTE))

    @property
    def is_comdat(self):
        return bool(self.flags & SCN_LNK_COMDAT)

    @property
    def is_bss(self):
        return bool(self.flags & SCN_CNT_UNINIT) and not self.flags & SCN_CNT_INIT

    @property
    def is_data(self):
        """allocated, not code, not linker info / debug"""
        f = self.flags
        if f & (SCN_LNK_INFO | SCN_LNK_REMOVE) or self.is_code:
            return False
        if self.name.startswith('.debug') or self.name == '.drectve':
            return False
        return bool(f & (SCN_CNT_INIT | SCN_CNT_UNINIT))

    @property
    def writable(self):
        return bool(self.flags & SCN_MEM_WRITE)


class Symbol:
    __slots__ = ('index', 'name', 'value', 'section', 'type', 'storage', 'naux')

    def __init__(self, index, name, value, section, typ, storage, naux):
        self.index, self.name, self.value, self.section, self.type, self.storage, self.naux = \
            index, name, value, section, typ, storage, naux

    @property
    def is_function(self):
        return (self.type >> 4) == 2


class Coff:
    def __init__(self, data):
        self.data = data = bytes(data)
        sig1, sig2 = struct.unpack_from('<HH', data, 0)
        if sig1 == 0 and sig2 == 0xffff and data[12:28] == BIGOBJ_UUID:
            machine, = struct.unpack_from('<H', data, 6)
            nsec, symptr, nsym = struct.unpack_from('<III', data, 44)
            shoff, symsize, secfmt = 56, 20, '<i'
        else:
            machine, nsec, _, symptr, nsym, opt, _ = struct.unpack_from('<HHIIIHH', data, 0)
            if opt:
                raise ValueError('not an object file')
            shoff, symsize, secfmt = 20 + opt, 18, '<h'
        if machine != 0x14c:
            raise ValueError('not i386')
        self.machine = machine
        strtab = symptr + nsym * symsize
        self.strings = data[strtab:strtab + struct.unpack_from('<I', data, strtab)[0]] if nsym else b''
        self.sections = [None]
        for i in range(nsec):
            o = shoff + 40 * i
            name, _, _, size, raw, relptr, _, nrel, _, flags = struct.unpack_from('<8sIIIIIIHHI', data, o)
            name = name.rstrip(b'\0').decode('latin-1')
            if name.startswith('/'):
                name = self._str(int(name[1:]))
            if flags & SCN_LNK_NRELOC_OVFL and nrel == 0xffff:
                nrel = struct.unpack_from('<I', data, relptr)[0]
                first = 1
            else:
                first = 0
            relocs = [struct.unpack_from('<IIH', data, relptr + 10 * (n + first)) for n in range(nrel - first)] \
                if nrel else []
            body = data[raw:raw + size] if raw else None
            self.sections.append(Section(i + 1, name, size, flags, body, relocs))
        self.symbols = {}
        i = 0
        while i < nsym:
            o = symptr + i * symsize
            raw = data[o:o + 8]
            if raw[:4] == b'\0\0\0\0':
                name = self._str(struct.unpack_from('<I', raw, 4)[0])
            else:
                name = raw.rstrip(b'\0').decode('latin-1')
            value, = struct.unpack_from('<I', data, o + 8)
            sec, = struct.unpack_from(secfmt, data, o + 12)
            typ, storage, naux = struct.unpack_from('<HBB', data, o + 12 + (4 if symsize == 20 else 2))
            sym = Symbol(i, name, value, sec, typ, storage, naux)
            self.symbols[i] = sym
            if storage == SYM_STATIC and naux and value == 0 and 0 < sec < len(self.sections) and \
                    name == self.sections[sec].name:
                a = o + symsize
                length, nrel, nlin, chk, num, sel = struct.unpack_from('<IHHIHB', data, a)
                s = self.sections[sec]
                if s.is_comdat:
                    s.comdat_sel = sel
                    s.number = num
                    if symsize == 20:          # bigobj: high 16 bits of the associated number
                        num |= struct.unpack_from('<H', data, a + 16)[0] << 16
                    if sel == 5:
                        s.assoc = num
            i += 1 + naux

    def _str(self, off):
        end = self.strings.find(b'\0', off)
        return self.strings[off:end if end >= 0 else len(self.strings)].decode('latin-1')

    def defined(self):
        """symbols defined in a section (section number > 0), in table order"""
        return [s for s in self.symbols.values() if s.section > 0]


def read(path):
    with open(path, 'rb') as f:
        return Coff(f.read())
