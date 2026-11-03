"""elfsyms.py - minimal ELF symbol-table reader (ELF32 and ELF64, little endian), no external tools.

symbols(path) -> list of Sym(name, value, size, type, bind, shndx, section, comdat)
  type: 'func' / 'object' / 'notype' / 'section' / 'file' / other; bind: 'local' / 'global' / 'weak'
  shndx 0 == undefined; section is the section name ('' for undefined/absolute/common); comdat is True when the
  section belongs to a COMDAT group (inline functions, template instances: any copy may be the one kept).
"""
import struct
from collections import namedtuple

Sym = namedtuple('Sym', 'name value size type bind shndx section comdat')
TYPES = {0: 'notype', 1: 'object', 2: 'func', 3: 'section', 4: 'file', 5: 'common', 6: 'tls', 10: 'ifunc'}
BINDS = {0: 'local', 1: 'global', 2: 'weak', 10: 'unique'}
SHT_SYMTAB, SHT_GROUP = 2, 17


def symbols(path):
    data = open(path, 'rb').read()
    if data[:4] != b'\x7fELF' or data[5] != 1:
        raise ValueError('%s: not a little-endian ELF file' % path)
    is64 = data[4] == 2
    if is64:
        shoff, = struct.unpack_from('<Q', data, 0x28)
        shentsize, shnum, shstrndx = struct.unpack_from('<HHH', data, 0x3a)
        sh_fmt, sym_fmt, sym_size = '<IIQQQQIIQQ', '<IBBHQQ', 24
    else:
        shoff, = struct.unpack_from('<I', data, 0x20)
        shentsize, shnum, shstrndx = struct.unpack_from('<HHH', data, 0x2e)
        sh_fmt, sym_fmt, sym_size = '<IIIIIIIIII', '<IIIBBH', 16
    secs = [struct.unpack_from(sh_fmt, data, shoff + i * shentsize) for i in range(shnum)]
    # (name, type, flags, addr, offset, size, link, info, addralign, entsize)

    def cstr(off):
        return data[off:data.index(b'\0', off)].decode('utf-8', 'surrogateescape')

    shstr = secs[shstrndx][4]
    names = [cstr(shstr + s[0]) for s in secs]
    comdat = set()
    for s in secs:
        if s[1] == SHT_GROUP:
            words = struct.unpack_from('<%dI' % (s[5] // 4), data, s[4])
            if words and words[0] & 1:
                comdat.update(words[1:])
    out = []
    for s in secs:
        if s[1] != SHT_SYMTAB:
            continue
        stroff = secs[s[6]][4]
        for k in range(1, s[5] // sym_size):
            o = s[4] + k * sym_size
            if is64:
                nm, info, other, shndx, value, size = struct.unpack_from(sym_fmt, data, o)
            else:
                nm, value, size, info, other, shndx = struct.unpack_from(sym_fmt, data, o)
            sec = names[shndx] if 0 < shndx < len(names) else ''
            out.append(Sym(cstr(stroff + nm), value, size, TYPES.get(info & 15, str(info & 15)),
                           BINDS.get(info >> 4, str(info >> 4)), shndx, sec, shndx in comdat))
    return out


def rename_undefined(src, dst, mapping):
    """Copy ELF object src to dst with undefined symbols renamed per mapping {old: new}.

    objcopy --redefine-syms refuses two old names with one new name, which is the normal case here (many
    declarations, one address), so the string table is rebuilt instead: new names are appended to a copy of the
    symbol string table placed at the end of the file, and st_name of each renamed undefined symbol points there.
    Returns the number of symbols renamed."""
    data = bytearray(open(src, 'rb').read())
    is64 = data[4] == 2
    if is64:
        shoff, = struct.unpack_from('<Q', data, 0x28)
        shentsize, shnum, _ = struct.unpack_from('<HHH', data, 0x3a)
        sh_fmt, sym_size = '<IIQQQQIIQQ', 24
    else:
        shoff, = struct.unpack_from('<I', data, 0x20)
        shentsize, shnum, _ = struct.unpack_from('<HHH', data, 0x2e)
        sh_fmt, sym_size = '<IIIIIIIIII', 16
    secs = [list(struct.unpack_from(sh_fmt, data, shoff + i * shentsize)) for i in range(shnum)]
    renamed = 0
    for s in secs:
        if s[1] != SHT_SYMTAB:
            continue
        strsec = secs[s[6]]
        old_tab = bytes(data[strsec[4]:strsec[4] + strsec[5]])
        new_tab = bytearray(old_tab)
        offs = {}
        for k in range(1, s[5] // sym_size):
            o = s[4] + k * sym_size
            nm, = struct.unpack_from('<I', data, o)
            shndx, = struct.unpack_from('<H', data, o + (6 if is64 else 14))
            if shndx != 0:
                continue
            name = old_tab[nm:old_tab.index(b'\0', nm)].decode('utf-8', 'surrogateescape')
            new = mapping.get(name)
            if new is None or new == name:
                continue
            if new not in offs:
                offs[new] = len(new_tab)
                new_tab += new.encode('utf-8', 'surrogateescape') + b'\0'
            struct.pack_into('<I', data, o, offs[new])
            renamed += 1
        if offs:
            while len(data) % 8:
                data.append(0)
            strsec[4], strsec[5] = len(data), len(new_tab)
            data += new_tab
    if renamed:
        for i, s in enumerate(secs):
            struct.pack_into(sh_fmt, data, shoff + i * shentsize, *s)
    open(dst, 'wb').write(data)
    return renamed


def undefine_symbols(src, dst, names):
    """Copy ELF32 object src to dst with the global definitions of `names` turned into undefined symbols (st_shndx
    0, value and size 0, NOTYPE): the object's own references to them then go through the undefined symbol, which
    rename_undefined can redirect. The bodies stay, unreferenced. Returns the number of symbols changed."""
    data = bytearray(open(src, 'rb').read())
    if data[4] != 1:
        raise ValueError('%s: ELF32 only' % src)
    shoff, = struct.unpack_from('<I', data, 0x20)
    shentsize, shnum, _ = struct.unpack_from('<HHH', data, 0x2e)
    secs = [struct.unpack_from('<IIIIIIIIII', data, shoff + i * shentsize) for i in range(shnum)]
    n = 0
    for s in secs:
        if s[1] != SHT_SYMTAB:
            continue
        stroff = secs[s[6]][4]
        for k in range(1, s[5] // 16):
            o = s[4] + k * 16
            nm, _, _, info, _, shndx = struct.unpack_from('<IIIBBH', data, o)
            name = data[stroff + nm:data.index(b'\0', stroff + nm)].decode('utf-8', 'surrogateescape')
            if name in names and shndx and info >> 4 in (1, 2):
                struct.pack_into('<IIBBH', data, o + 4, 0, 0, (info >> 4) << 4, 0, 0)
                n += 1
    open(dst, 'wb').write(data)
    return n


def vtable_slot_refs(path):
    """Names of the symbols that relocations inside a vtable object (_ZTV*) point at: {symbol name: count}.
    These references come from virtual-function slots, not from code, so units.json never lists them."""
    data = open(path, 'rb').read()
    is64 = data[4] == 2
    if is64:
        shoff, = struct.unpack_from('<Q', data, 0x28)
        shentsize, shnum, _ = struct.unpack_from('<HHH', data, 0x3a)
        sh_fmt, rela_fmt, rela_size = '<IIQQQQIIQQ', '<QQq', 24
    else:
        shoff, = struct.unpack_from('<I', data, 0x20)
        shentsize, shnum, _ = struct.unpack_from('<HHH', data, 0x2e)
        sh_fmt, rela_fmt, rela_size = '<IIIIIIIIII', '<IIi', 12
    secs = [struct.unpack_from(sh_fmt, data, shoff + i * shentsize) for i in range(shnum)]
    syms = symbols(path)  # index k-1 in this list == symbol index k (single symtab in relocatable objects)
    spans = {}
    for s in syms:
        if s.name.startswith('_ZTV') and s.shndx:
            spans.setdefault(s.shndx, []).append((s.value, s.value + s.size))
    out = {}
    for s in secs:
        if s[1] != 4 or s[7] not in spans:  # SHT_RELA against a section that holds a vtable
            continue
        for k in range(s[5] // rela_size):
            off, info, _ = struct.unpack_from(rela_fmt, data, s[4] + k * rela_size)
            idx = info >> 32 if is64 else info >> 8
            if not any(a <= off < b for a, b in spans[s[7]]) or not 0 < idx <= len(syms):
                continue
            name = syms[idx - 1].name
            out[name] = out.get(name, 0) + 1
    return out
