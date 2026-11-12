"""mutate - byte mutations of a REAL ELF relocatable object (a negative gate case is a stated edit of an object the one build produced, never a
hand-assembled object).  Every field is located by parsing with link/elfobj.py (section header table, symbol table), never by a fixed offset.

    sections(data)                       -> [(index, name)]            the section numbers of the object
    replace_section(data, name, new)     -> bytes   the section's contents become `new` (same size: in place; else the new bytes are appended to
                                                    the file and the section header is repointed; the rest of the object is unchanged)
    rename_section(data, name, new)      -> bytes   same-length rename inside .shstrtab (`new` has the length of `name`); the section is then
                                                    no longer found under its old name
    duplicate_section(data, name)        -> bytes   a second section header with the same name and contents (appended header table)
    rename_symbol(data, old, new)        -> bytes   the st_name of the symbol `old` points at a new NUL-terminated name appended to .strtab
    set_section_flags(data, name, flags) -> bytes   sh_flags of the section
    drop_symtab(data)                    -> bytes   the symbol table section becomes an unknown section type (SHT 0x70000001)
    truncate(data, n)                    -> bytes   the first n bytes
Every function raises ValueError when the asked section or symbol is not there (a mutation that did not apply is an error, never a no-op).
"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import elfobj  # noqa: E402


def _hdr(e, i):
    """(offset of the section header i in the file, fmt, field names)"""
    if e.is64:
        shoff, = struct.unpack_from('<Q', e.data, 0x28)
        return shoff + i * 64, '<IIQQQQIIQQ'
    shoff, = struct.unpack_from('<I', e.data, 0x20)
    return shoff + i * 40, '<IIIIIIIIII'


def _fields(e, i):
    o, fmt = _hdr(e, i)
    return list(struct.unpack_from(fmt, e.data, o))      # name type flags addr off size link info align entsize


def _put(e, data, i, f):
    o, fmt = _hdr(e, i)
    struct.pack_into(fmt, data, o, *f)


def _one(e, name):
    ids = e.by_name.get(name)
    if not ids:
        raise ValueError('no section %s' % name)
    if len(ids) != 1:
        raise ValueError('%d sections named %s' % (len(ids), name))
    return ids[0]


def sections(data):
    e = elfobj.parse(data, 'mutate')
    return list(enumerate(e.names))


def replace_section(data, name, new):
    e = elfobj.parse(data, 'mutate')
    i = _one(e, name)
    f = _fields(e, i)
    out = bytearray(data)
    if len(new) == f[5]:
        out[f[4]:f[4] + len(new)] = new
    else:
        while len(out) % 8:
            out.append(0)
        f[4], f[5] = len(out), len(new)
        out += new
        _put(e, out, i, f)
    return bytes(out)


def rename_section(data, name, new):
    if len(new) != len(name):
        raise ValueError('same-length rename only')
    e = elfobj.parse(data, 'mutate')
    i = _one(e, name)
    so = e._sec[struct.unpack_from('<HHH', data, 0x3a if e.is64 else 0x2e)[2]][3]
    at = so + e._sec[i][0]
    if data[at:at + len(name)] != name.encode():
        raise ValueError('section name not where the table says')
    out = bytearray(data)
    out[at:at + len(name)] = new.encode()
    return bytes(out)


def duplicate_section(data, name):
    e = elfobj.parse(data, 'mutate')
    i = _one(e, name)
    n = len(e._sec)
    o, _ = _hdr(e, 0)
    size = 64 if e.is64 else 40
    out = bytearray(data)
    while len(out) % 8:
        out.append(0)
    new_sh = len(out)
    out += bytes(data[o:o + n * size]) + bytes(data[o + i * size:o + (i + 1) * size])
    struct.pack_into('<Q' if e.is64 else '<I', out, 0x28 if e.is64 else 0x20, new_sh)
    struct.pack_into('<H', out, 0x3c if e.is64 else 0x30, n + 1)
    return bytes(out)


def rename_symbol(data, old, new):
    e = elfobj.parse(data, 'mutate')
    sy = [i for i, s in enumerate(e._sec) if s[1] == elfobj.SHT_SYMTAB]
    if len(sy) != 1:
        raise ValueError('no unique symbol table')
    sf = _fields(e, sy[0])
    st = _fields(e, sf[6])                  # its string table
    ent = 24 if e.is64 else 16
    hit = None
    for k in range(sf[5] // ent):
        a = sf[4] + k * ent
        nm, = struct.unpack_from('<I', data, a)
        so = st[4] + nm
        end = data.index(b'\0', so)
        if data[so:end] == old.encode():
            hit = a
            break
    if hit is None:
        raise ValueError('no symbol %s' % old)
    out = bytearray(data)
    while len(out) % 8:
        out.append(0)
    new_tab = bytes(data[st[4]:st[4] + st[5]]) + new.encode() + b'\0'
    off_new = len(out)
    out += new_tab
    st[4], st[5] = off_new, len(new_tab)
    _put(e, out, sf[6], st)
    struct.pack_into('<I', out, hit, len(new_tab) - len(new) - 1)
    return bytes(out)


def set_section_flags(data, name, flags):
    e = elfobj.parse(data, 'mutate')
    i = _one(e, name)
    f = _fields(e, i)
    f[2] = flags
    out = bytearray(data)
    _put(e, out, i, f)
    return bytes(out)


def drop_symtab(data):
    e = elfobj.parse(data, 'mutate')
    sy = [i for i, s in enumerate(e._sec) if s[1] == elfobj.SHT_SYMTAB]
    if len(sy) != 1:
        raise ValueError('no unique symbol table')
    f = _fields(e, sy[0])
    f[1] = 0x70000001
    out = bytearray(data)
    _put(e, out, sy[0], f)
    return bytes(out)


def truncate(data, n):
    return bytes(data[:n])


def _func(e, name):
    hit = [s for s in e.symbols() if s[0] == name and s[3] not in (0, 0xfff1, 0xfff2)]
    if len(hit) != 1:
        raise ValueError('%d defined symbols named %s' % (len(hit), name))
    return hit[0]


def function_words(data, name):
    """[(file offset, 32-bit word)] of the function symbol `name` (size from st_size), located through the symbol table and its section"""
    e = elfobj.parse(data, 'mutate')
    s = _func(e, name)
    base = e._sec[s[3]][3] + s[4]
    return [(base + i, struct.unpack_from('<I', data, base + i)[0]) for i in range(0, s[5], 4)]


def patch_word(data, name, index, word):
    """the `index`-th instruction word of function `name` becomes `word`"""
    ws = function_words(data, name)
    if not 0 <= index < len(ws):
        raise ValueError('%s has %d words' % (name, len(ws)))
    out = bytearray(data)
    struct.pack_into('<I', out, ws[index][0], word)
    return bytes(out)


def stlr_to_str(data, name, nth=0):
    """the `nth` store-release (STLR w, [xn]) of function `name` becomes the plain STR w, [xn] with the same registers (a stated edit of the
    instruction word: 0x889ffc00|Rn<<5|Rt -> 0xb9000000|Rn<<5|Rt)"""
    hits = [i for i, (_o, w) in enumerate(function_words(data, name)) if w & 0xfffffc00 == 0x889ffc00]
    if nth >= len(hits):
        raise ValueError('%s has %d STLR words' % (name, len(hits)))
    w = function_words(data, name)[hits[nth]][1]
    return patch_word(data, name, hits[nth], 0xb9000000 | (w & 0x3ff))


def set_symbol_size(data, name, size):
    e = elfobj.parse(data, 'mutate')
    s = _func(e, name)
    sy = [i for i, x in enumerate(e._sec) if x[1] == elfobj.SHT_SYMTAB][0]
    f = _fields(e, sy)
    ent = 24 if e.is64 else 16
    for k, x in enumerate(e.symbols()):
        if x == s:
            out = bytearray(data)
            struct.pack_into('<I' if not e.is64 else '<Q', out, f[4] + k * ent + (8 if not e.is64 else 16), size)
            return bytes(out)
    raise ValueError('symbol vanished')


def _symoff(e, name):
    sy = [i for i, x in enumerate(e._sec) if x[1] == elfobj.SHT_SYMTAB][0]
    f = _fields(e, sy)
    ent = 24 if e.is64 else 16
    hit = [x for x in e.symbols() if x[0] == name]
    if len(hit) != 1:
        raise ValueError('%d symbols named %s' % (len(hit), name))
    s = hit[0]
    for k, x in enumerate(e.symbols()):
        if x == s:
            return f[4] + k * ent
    raise ValueError('symbol vanished')


def set_symbol_shndx(data, name, shndx):
    """the symbol `name` (defined or undefined) gets st_shndx = SHNDX (0xfff1 = SHN_ABS: the symbol turns into an absolute one, its value kept)"""
    e = elfobj.parse(data, 'mutate')
    out = bytearray(data)
    struct.pack_into('<H', out, _symoff(e, name) + (6 if e.is64 else 14), shndx)
    return bytes(out)


def zero_reloc_symbol(data, nth=0):
    """the `nth` relocation entry (SHT_RELA sections in order) gets symbol index 0 (the assembler's folded absolute value: no symbol, a type)"""
    e = elfobj.parse(data, 'mutate')
    ent = 24 if e.is64 else 12
    k = 0
    for i in range(len(e._sec)):
        f = _fields(e, i)
        if f[1] != 4:
            continue
        for a in range(f[4], f[4] + f[5], ent):
            if k == nth:
                out = bytearray(data)
                if e.is64:
                    typ, = struct.unpack_from('<I', out, a + 8)
                    struct.pack_into('<Q', out, a + 8, typ)
                else:
                    info, = struct.unpack_from('<I', out, a + 4)
                    struct.pack_into('<I', out, a + 4, info & 0xff)
                return bytes(out)
            k += 1
    raise ValueError('no relocation %d' % nth)
