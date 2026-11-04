"""elf32.py - V1-link: minimal AArch64 ILP32 ELF reader (relocatable objects and the trial executable).

Independent of tools/link/elfsyms.py on purpose: verification must not share the reader of the code under test.
Uses pyelftools for the container format only; relocation types are the ILP32 (P32) numbers of the AArch64 ELF ABI
(cross-checked against binutils readelf names by selftest_harness.py).
"""
import bisect
import struct

from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection
from elftools.elf.relocation import RelocationSection

# AArch64 ELF ABI, ILP32 relocation numbers (aaelf64 "ILP32 relocations")
P32 = {
    0: 'NONE', 1: 'ABS32', 2: 'ABS16', 3: 'PREL32', 4: 'PREL16',
    5: 'MOVW_UABS_G0', 6: 'MOVW_UABS_G0_NC', 7: 'MOVW_UABS_G1', 8: 'MOVW_SABS_G0',
    9: 'LD_PREL_LO19', 10: 'ADR_PREL_LO21', 11: 'ADR_PREL_PG_HI21', 12: 'ADD_ABS_LO12_NC',
    13: 'LDST8_ABS_LO12_NC', 14: 'LDST16_ABS_LO12_NC', 15: 'LDST32_ABS_LO12_NC', 16: 'LDST64_ABS_LO12_NC',
    17: 'LDST128_ABS_LO12_NC',
    18: 'TSTBR14', 19: 'CONDBR19', 20: 'JUMP26', 21: 'CALL26',
    22: 'MOVW_PREL_G0', 23: 'MOVW_PREL_G0_NC', 24: 'MOVW_PREL_G1',
    25: 'GOT_LD_PREL19', 26: 'ADR_GOT_PAGE', 27: 'LD32_GOT_LO12_NC', 28: 'LD32_GOTPAGE_LO14',
    80: 'TLSGD_ADR_PREL21', 81: 'TLSGD_ADR_PAGE21', 82: 'TLSGD_ADD_LO12_NC',
    83: 'TLSLD_ADR_PREL21', 84: 'TLSLD_ADR_PAGE21', 85: 'TLSLD_ADD_LO12_NC',
    103: 'TLSIE_ADR_GOTTPREL_PAGE21', 104: 'TLSIE_LD32_GOTTPREL_LO12_NC', 105: 'TLSIE_LD_GOTTPREL_PREL19',
    106: 'TLSLE_MOVW_TPREL_G1', 107: 'TLSLE_MOVW_TPREL_G0', 108: 'TLSLE_MOVW_TPREL_G0_NC',
    109: 'TLSLE_ADD_TPREL_HI12', 110: 'TLSLE_ADD_TPREL_LO12', 111: 'TLSLE_ADD_TPREL_LO12_NC',
    112: 'TLSLE_LDST8_TPREL_LO12', 113: 'TLSLE_LDST8_TPREL_LO12_NC', 114: 'TLSLE_LDST16_TPREL_LO12',
    115: 'TLSLE_LDST16_TPREL_LO12_NC', 116: 'TLSLE_LDST32_TPREL_LO12', 117: 'TLSLE_LDST32_TPREL_LO12_NC',
    118: 'TLSLE_LDST64_TPREL_LO12', 119: 'TLSLE_LDST64_TPREL_LO12_NC',
    122: 'TLSDESC_LD_PREL19', 123: 'TLSDESC_ADR_PREL21', 124: 'TLSDESC_ADR_PAGE21', 125: 'TLSDESC_LD32_LO12_NC',
    126: 'TLSDESC_ADD_LO12_NC', 127: 'TLSDESC_CALL',
}
CODE_ADDR = {'ADR_PREL_PG_HI21', 'ADR_PREL_LO21', 'ADD_ABS_LO12_NC', 'LD_PREL_LO19', 'LDST8_ABS_LO12_NC',
             'LDST16_ABS_LO12_NC', 'LDST32_ABS_LO12_NC', 'LDST64_ABS_LO12_NC', 'LDST128_ABS_LO12_NC',
             'MOVW_UABS_G0', 'MOVW_UABS_G0_NC', 'MOVW_UABS_G1'}
DATA_ADDR = {'ABS32', 'ABS16', 'PREL32', 'PREL16'}
BRANCH = {'CALL26', 'JUMP26', 'TSTBR14', 'CONDBR19'}


class Sym:
    __slots__ = ('idx', 'name', 'value', 'size', 'type', 'bind', 'shndx', 'vis')

    def __init__(self, idx, s):
        self.idx, self.name = idx, s.name
        self.value, self.size = s['st_value'], s['st_size']
        self.type, self.bind = s['st_info']['type'], s['st_info']['bind']
        self.shndx, self.vis = s['st_shndx'], s['st_other']['visibility']

    def defined(self):
        return self.shndx not in ('SHN_UNDEF',)

    def __repr__(self):
        return '<%s %s %s %s+%#x>' % (self.name, self.bind, self.type, self.shndx, self.value)


class Rel:
    __slots__ = ('sec', 'offset', 'type', 'sym', 'addend')

    def __init__(self, sec, offset, rtype, sym, addend):
        self.sec, self.offset, self.type, self.sym, self.addend = sec, offset, rtype, sym, addend


class Obj:
    """A relocatable object: sections (index -> name, flags, data on demand), symbols, relocations."""

    def __init__(self, path, want_relocs=True):
        self.path = path
        self.f = open(path, 'rb')
        self.elf = ELFFile(self.f)
        self.secs = {}
        self.syms = []
        self.rels = []
        for i, s in enumerate(self.elf.iter_sections()):
            self.secs[i] = s
        for s in self.secs.values():
            if isinstance(s, SymbolTableSection):
                self.syms = [Sym(i, x) for i, x in enumerate(s.iter_symbols())]
                break
        if want_relocs:
            for s in self.secs.values():
                if isinstance(s, RelocationSection) and s['sh_type'] == 'SHT_RELA':
                    target = s['sh_info']
                    for r in s.iter_relocations():
                        self.rels.append(Rel(target, r['r_offset'], P32.get(r['r_info_type'], r['r_info_type']),
                                             self.syms[r['r_info_sym']], r['r_addend']))

    def secname(self, idx):
        if not isinstance(idx, int):
            return str(idx)
        return self.secs[idx].name

    def secdata(self, idx):
        return self.secs[idx].data()

    def close(self):
        self.f.close()


class Image:
    """The linked trial executable: symbols by name / by address, and memory reads by address."""

    def __init__(self, path):
        self.f = open(path, 'rb')
        self.elf = ELFFile(self.f)
        self.segs = []
        for s in self.elf.iter_sections():
            if s['sh_flags'] & 2 and s['sh_type'] != 'SHT_NOBITS' and s['sh_addr']:
                self.segs.append((s['sh_addr'], s['sh_addr'] + s['sh_size'], s.name, s))
            elif s['sh_flags'] & 2 and s['sh_type'] == 'SHT_NOBITS':
                self.segs.append((s['sh_addr'], s['sh_addr'] + s['sh_size'], s.name, None))
        self.segs.sort(key=lambda x: x[0])
        self._data = {}
        self.by_name = {}
        self.by_addr = {}
        self.locals_by_name = {}
        st = self.elf.get_section_by_name('.symtab')
        for x in st.iter_symbols():
            if x['st_shndx'] == 'SHN_UNDEF' or not x.name:
                continue
            t = x['st_info']['type']
            if t in ('STT_FILE', 'STT_SECTION'):
                continue
            b = x['st_info']['bind']
            v = x['st_value']
            if b == 'STB_LOCAL':
                self.locals_by_name.setdefault(x.name, []).append(v)
            else:
                self.by_name[x.name] = (v, x['st_size'], t, b, x['st_shndx'])
            self.by_addr.setdefault(v, []).append((x.name, b, t, x['st_size']))
        self.addrs = sorted(self.by_addr)

    def section_of(self, addr):
        for lo, hi, name, s in self.segs:
            if lo <= addr < hi:
                return name
        return None

    def read(self, addr, n):
        for lo, hi, name, s in self.segs:
            if lo <= addr and addr + n <= hi:
                if s is None:
                    return b'\0' * n
                if name not in self._data:
                    self._data[name] = s.data()
                return self._data[name][addr - lo:addr - lo + n]
        return None

    def u32(self, addr):
        b = self.read(addr, 4)
        return None if b is None else struct.unpack('<I', b)[0]

    def i32(self, addr):
        b = self.read(addr, 4)
        return None if b is None else struct.unpack('<i', b)[0]

    def names_at(self, addr):
        return [n for n, b, t, s in self.by_addr.get(addr, [])]

    def containing(self, addr):
        """(symbol name, start) of the function/object symbol whose [start, start+size) holds addr."""
        i = bisect.bisect_right(self.addrs, addr) - 1
        while i >= 0 and addr - self.addrs[i] < 1 << 20:
            for n, b, t, s in self.by_addr[self.addrs[i]]:
                if self.addrs[i] <= addr < self.addrs[i] + max(s, 1):
                    return n, self.addrs[i]
            i -= 1
        return None, None
