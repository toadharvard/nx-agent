"""elfsyms.py - minimal ELF symbol-table reader (ELF32 and ELF64, little endian), no external tools.

symbols(path) -> list of Sym(name, value, size, type, bind, shndx, section, comdat)
  type: 'func' / 'object' / 'notype' / 'section' / 'file' / other; bind: 'local' / 'global' / 'weak'
  shndx 0 == undefined; section is the section name ('' for undefined/absolute/common); comdat is True when the
  section belongs to a COMDAT group (inline functions, template instances: any copy may be the one kept).
"""
import collections
import os
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


def archive_symbols(path):
    """Global symbols an ar archive defines, from its symbol index (`/`, the System V / GNU layout ar and llvm-ar
    write; `/SYM64/` too), else from each ELF member's symbol table. A missing file reads as no symbols."""
    if not path or not os.path.isfile(path):
        return set()
    data = open(path, 'rb').read()
    if not data.startswith(b'!<arch>\n'):
        return {s.name for s in symbols(path) if s.shndx and s.bind in ('global', 'weak')}
    out, off, members = set(), 8, []
    while off + 60 <= len(data):
        name = data[off:off + 16].decode('latin-1').strip()
        size = int(data[off + 48:off + 58].decode('latin-1').strip() or 0)
        body = data[off + 60:off + 60 + size]
        if name in ('/', '/SYM64/'):
            w = 4 if name == '/' else 8
            n = int.from_bytes(body[:w], 'big')
            for nm in body[w + w * n:].split(b'\0')[:n]:
                out.add(nm.decode('utf-8', 'surrogateescape'))
        elif not name.startswith('//'):
            members.append(body)
        off += 60 + size + (size & 1)
    if not out:
        import tempfile
        for body in members:
            if body[:4] == b'\x7fELF':
                with tempfile.NamedTemporaryFile(suffix='.o') as t:
                    t.write(body); t.flush()
                    out |= {s.name for s in symbols(t.name) if s.shndx and s.bind in ('global', 'weak')}
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
    rename_undefined can redirect. The bodies stay, unreferenced. A COMDAT group whose signature is one of these
    symbols (an inline function, a typeinfo) stops being COMDAT: the linker keeps the first group of a signature in
    link order and drops the others, so the emptied group would otherwise discard the definition its references
    are meant to reach. Returns the number of symbols changed."""
    data = bytearray(open(src, 'rb').read())
    if data[4] != 1:
        raise ValueError('%s: ELF32 only' % src)
    shoff, = struct.unpack_from('<I', data, 0x20)
    shentsize, shnum, _ = struct.unpack_from('<HHH', data, 0x2e)
    secs = [struct.unpack_from('<IIIIIIIIII', data, shoff + i * shentsize) for i in range(shnum)]
    n = 0
    for si, s in enumerate(secs):
        if s[1] != SHT_SYMTAB:
            continue
        stroff = secs[s[6]][4]
        changed = set()
        for k in range(1, s[5] // 16):
            o = s[4] + k * 16
            nm, _, _, info, _, shndx = struct.unpack_from('<IIIBBH', data, o)
            name = data[stroff + nm:data.index(b'\0', stroff + nm)].decode('utf-8', 'surrogateescape')
            if name in names and shndx and info >> 4 in (1, 2):
                struct.pack_into('<IIBBH', data, o + 4, 0, 0, (info >> 4) << 4, 0, 0)
                n += 1
                changed.add(k)
        for g in secs:
            if g[1] == SHT_GROUP and g[6] == si and g[7] in changed and g[5] >= 4:
                flags, = struct.unpack_from('<I', data, g[4])
                struct.pack_into('<I', data, g[4], flags & ~1)   # GRP_COMDAT off: a plain group, always kept
    open(dst, 'wb').write(data)
    return n


def set_sizes(path, sizes):
    """Set st_size of the named symbols of an ELF32 object in place ({name: size}; objcopy --add-symbol gives 0)."""
    data = bytearray(open(path, 'rb').read())
    shoff, = struct.unpack_from('<I', data, 0x20)
    shentsize, shnum, _ = struct.unpack_from('<HHH', data, 0x2e)
    secs = [struct.unpack_from('<IIIIIIIIII', data, shoff + i * shentsize) for i in range(shnum)]
    for s in secs:
        if s[1] != SHT_SYMTAB:
            continue
        stroff = secs[s[6]][4]
        for k in range(1, s[5] // 16):
            o = s[4] + k * 16
            nm, = struct.unpack_from('<I', data, o)
            name = data[stroff + nm:data.index(b'\0', stroff + nm)].decode('utf-8', 'surrogateescape')
            if name in sizes:
                struct.pack_into('<I', data, o + 8, sizes[name])
    open(path, 'wb').write(data)


def retarget_relocs(src, dst, ranges, mapping):
    """Copy ELF32 object src to dst with the RELA relocations inside [(section index, lo, hi)] whose symbol index is a
    key of mapping pointed at mapping[index] instead. -> relocations rewritten."""
    data = bytearray(open(src, 'rb').read())
    shoff, = struct.unpack_from('<I', data, 0x20)
    shentsize, shnum, _ = struct.unpack_from('<HHH', data, 0x2e)
    secs = [struct.unpack_from('<IIIIIIIIII', data, shoff + i * shentsize) for i in range(shnum)]
    by_sec = collections.defaultdict(list)
    for sh, lo, hi in ranges:
        by_sec[sh].append((lo, hi))
    n = 0
    for r in secs:
        if r[1] != 4 or r[7] not in by_sec:
            continue
        for o in range(r[4], r[4] + r[5] - 11, 12):
            off, info = struct.unpack_from('<II', data, o)
            k = info >> 8
            if k in mapping and any(lo <= off < hi for lo, hi in by_sec[r[7]]):
                struct.pack_into('<I', data, o + 4, (mapping[k] << 8) | (info & 0xff))
                n += 1
    open(dst, 'wb').write(data)
    return n


ABS_RELOCS = (1, 2)   # R_AARCH64_P32_ABS32, _ABS16


def retarget_local_relocs(src, dst, names):
    """Copy ELF32 object src to dst with every absolute data relocation (ABS32 / ABS16) that reaches one of the
    defined symbols `names` through a local symbol (a section or label plus offset) rewritten to name that symbol,
    addend adjusted: once the variable is rebound (apply renames and weakens it, aliases.ld defines it), pointers to
    it in the object's own data follow it too. Code relocations through a local symbol are never rewritten: GCC
    addresses several variables from one section anchor with immediate offsets no relocation shows, so pointing
    the anchor at one variable's image address would misplace the others, and a PC-relative reference to an
    absolute address ties the guest to its link address; compile.sh's -fPIC addresses defined variables through
    the GOT by symbol instead. Relocations of non-allocated sections (debug info) are left alone.
    -> (relocations rewritten, code relocations through a local symbol left: should be 0)."""
    data = bytearray(open(src, 'rb').read())
    if data[4] != 1:
        raise ValueError('%s: ELF32 only' % src)
    shoff, = struct.unpack_from('<I', data, 0x20)
    shentsize, shnum, _ = struct.unpack_from('<HHH', data, 0x2e)
    secs = [struct.unpack_from('<IIIIIIIIII', data, shoff + i * shentsize) for i in range(shnum)]
    n = left = 0
    for si, s in enumerate(secs):
        if s[1] != SHT_SYMTAB:
            continue
        stroff = secs[s[6]][4]
        syms = []   # index -> (value, shndx, local, type)
        ranges = {}   # shndx -> [(lo, hi, index, value)]
        for k in range(s[5] // 16):
            o = s[4] + k * 16
            nm, value, size, info, _, shndx = struct.unpack_from('<IIIBBH', data, o)
            syms.append((value, shndx, info >> 4 == 0, info & 15))
            if k and 0 < shndx < 0xff00 and info >> 4 in (1, 2):
                name = data[stroff + nm:data.index(b'\0', stroff + nm)].decode('utf-8', 'surrogateescape')
                if name in names:
                    ranges.setdefault(shndx, []).append((value, value + max(1, size), k, value))
        if not ranges:
            continue
        for r in secs:
            if r[1] != 4 or r[6] != si or not secs[r[7]][2] & 2:   # SHT_RELA of this symtab, allocated target
                continue
            for o in range(r[4], r[4] + r[5] - 11, 12):
                info, addend = struct.unpack_from('<Ii', data, o + 4)
                k = info >> 8
                if k >= len(syms):
                    continue
                value, shndx, local, typ = syms[k]
                if not local or typ == 4 or shndx not in ranges:   # globals already go by name; never STT_FILE
                    continue
                at = (value if typ != 3 else 0) + addend
                for lo, hi, idx, vval in ranges[shndx]:
                    if lo <= at < hi:
                        if info & 0xff in ABS_RELOCS:
                            struct.pack_into('<Ii', data, o + 4, (idx << 8) | (info & 0xff), at - vval)
                            n += 1
                        else:
                            left += 1
                        break
    open(dst, 'wb').write(data)
    return n, left


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
