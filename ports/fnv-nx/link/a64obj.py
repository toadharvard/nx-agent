"""a64obj - the facts of an AArch64 ELF relocatable object that the native gates (gate_order, gate_addr) decide on.

    read(path)  ->  Obj

Obj.sections  [Sec(index, name, type, flags, size, data)]     data is b'' for SHT_NOBITS
Obj.symbols   [Sym(index, name, value, size, type, bind, shndx)]   shndx is the section index, or None when the symbol
                                                                   is undefined, absolute or common (not placed in a section)
Obj.relocs    {section index: {offset: Reloc(sym index, type number, addend)}}   RELA tables only
Obj.functions [(section index, start, size, [symbol names])]      one entry per distinct (section, start, size) of the
                                                                   function symbols (STT_FUNC, STT_GNU_IFUNC), names sorted
Obj.mapping   {section index: [(offset, 'x'|'d')]}                the AArch64 mapping symbols (code or data islands), sorted

A relocatable object that is not little-endian AArch64 ELF32 (the ILP32 product ABI, relocations R_AARCH64_P32_*), a
SHT_REL table, or a symbol or section index outside its tables is a ValueError naming the object.  Pure: a function
of the object's bytes.  The reader takes the AArch64 ABI's own definitions: a mapping symbol is a local NOTYPE symbol
whose name is `$x` or `$d`, optionally followed by `.` and a suffix (ELF for the Arm 64-bit Architecture, 5.4.4).
"""
import collections

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

SHF_WRITE, SHF_ALLOC, SHF_EXECINSTR = 1, 2, 4
EM_AARCH64 = 183

Sec = collections.namedtuple('Sec', 'index name type flags size data')
Sym = collections.namedtuple('Sym', 'index name value size type bind shndx')
Reloc = collections.namedtuple('Reloc', 'sym type addend')


class Obj:
    def __init__(self, path):
        self.path = path
        self.sections, self.symbols, self.relocs, self.functions, self.mapping = [], [], {}, [], {}


def read(path):
    o = Obj(path)
    try:
        with open(path, 'rb') as fh:
            e = ELFFile(fh)
            if e.header['e_type'] != 'ET_REL' or e.header['e_machine'] != 'EM_AARCH64' or e.elfclass != 32 or not e.little_endian:
                raise ValueError('%s: not a little-endian AArch64 ELF32 relocatable object (ILP32)' % path)
            for i, s in enumerate(e.iter_sections()):
                nobits = s['sh_type'] == 'SHT_NOBITS'
                o.sections.append(Sec(i, s.name, s['sh_type'], s['sh_flags'], s['sh_size'], b'' if nobits else s.data()))
            n = len(o.sections)
            for s in e.iter_sections():
                if s['sh_type'] == 'SHT_SYMTAB':
                    for k, y in enumerate(s.iter_symbols()):
                        shn = y['st_shndx']
                        placed = isinstance(shn, int) and 0 < shn < 0xff00
                        if placed and shn >= n:
                            raise ValueError('%s: symbol %d names section %d, the object has %d' % (path, k, shn, n))
                        o.symbols.append(Sym(k, y.name, y['st_value'], y['st_size'], y['st_info']['type'], y['st_info']['bind'],
                                             shn if placed else None))
                elif s['sh_type'] == 'SHT_REL':
                    raise ValueError('%s: %s is SHT_REL (RELA expected)' % (path, s.name))
            for s in e.iter_sections():
                if s['sh_type'] == 'SHT_RELA':
                    tgt = s['sh_info']
                    if not 0 < tgt < n:
                        raise ValueError('%s: %s relocates section %d' % (path, s.name, tgt))
                    tab = o.relocs.setdefault(tgt, {})
                    for r in s.iter_relocations():
                        if r['r_info_sym'] >= len(o.symbols):
                            raise ValueError('%s: %s: relocation at 0x%x names symbol %d' % (path, s.name, r['r_offset'], r['r_info_sym']))
                        tab[r['r_offset']] = Reloc(r['r_info_sym'], r['r_info_type'], r['r_addend'])
    except (ELFError, OSError, KeyError) as ex:
        raise ValueError('%s: not a readable ELF object (%s)' % (path, ex))
    body = {}
    for y in o.symbols:
        if y.shndx is not None and y.type in ('STT_FUNC', 'STT_GNU_IFUNC'):
            body.setdefault((y.shndx, y.value, y.size), []).append(y.name)
        if y.shndx is not None and y.type == 'STT_NOTYPE' and y.bind == 'STB_LOCAL' and y.name[:2] in ('$x', '$d') and y.name[2:3] in ('', '.'):
            o.mapping.setdefault(y.shndx, []).append((y.value, y.name[1]))
    o.functions = [(k[0], k[1], k[2], sorted(v)) for k, v in sorted(body.items())]
    o.mapping = {k: sorted(v) for k, v in o.mapping.items()}
    return o
