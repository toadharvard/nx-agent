#!/usr/bin/env python3
"""ldcheck - the post stage of the guest ELF link (L1 CONTRACT); the sandbox stage `ldcheck:<root>` of verify/recipe.py runs it.

    ldcheck.py --elf LINKED.elf --objs DIR --out DIR --name NAME.elf

verify/recipe.py links the guest ELF as the stage `ld:<root>` (fixed options, RECIPE.md "Link"): the root's objects and the objects of
its `link.roots` whole, the compiler external's guest libraries searched as the gcc driver searches them.  This tool judges the result
from ELF symbol tables only (no linker message, no map file, no name or text pattern), the rules of the former link/ld.py:
  1. no undefined symbol in the output's `.symtab`;
  2. every name the input objects reference or define (the symbol tables of every `*.o` below --objs, weak references included) is defined in the output;
  3. no dynamic relocation but R_AARCH64_P32_RELATIVE (RELA entries without a symbol) or RELR words; no REL, JMPREL, TEXTREL table;
  4. the PIE mark: DF_1_PIE in DT_FLAGS_1;
  5. the crt0 header: the magic "FNXG" at the first PT_LOAD + 8 and the entry at the first PT_LOAD.
The pull-in closure that ld.py modelled before the link is gone: the linker's own output symbol table is the witness, and a name the
linker took or dropped differently from a model cannot make the output pass with an undefined reference.
On success it writes the three words of the crt0 header into the ELF (the same rule as elf2nso.py `patch_guest_header`): +16 the link
address of the first PT_LOAD, +20 the bytes from there to the end of the last PT_LOAD (memsz), page aligned, +24 bit 0 = relocatable (DF_1_PIE).
Outputs in --out: NAME.elf (the final ELF; none when a rule fails) and the gate file `ld` ($GATE_OUT/ld.json, full id list in $GATE_OUT/ld.ids,
GATE_OUT = --out): universe = the input names + the dynamic relocation entries (RELA entries, RELR words) + 2 (the PIE mark, the crt0 header);
certified = those that hold.  Ids: `cover:<name>` (an input name the output does not define; `weak:<name>` when the inputs only reference it
weakly: the linker resolves such a reference to 0 and drops it from the symbol table, so only the inputs' tables show it), `output-undefined:<name>`, `dynreloc:..`, `pie`,
`header:..`.  Exit 1 on any id; the ids are printed on stderr, the summary line last.
The ELF is exe-derived (lifted code): container and fnv-nx-build only.
"""
import argparse, os, shutil, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'contract'))
import gatefile
import gateids

EM_AARCH64, SHT_SYMTAB, SHN_UNDEF = 183, 2, 0
PT_LOAD, PT_DYNAMIC = 1, 2
SHN_ABS, SHN_COMMON, STT_SECTION, STT_FILE, STT_GNU_IFUNC = 0xfff1, 0xfff2, 3, 4, 10
R_AARCH64_P32_RELATIVE = 183
GUEST_MAGIC = 0x47584E46      # "FNXG" at the first segment + 8 (rt/guest/crt0.s)
PAGE = 0x1000
DT = dict(NULL=0, NEEDED=1, PLTRELSZ=2, SYMTAB=6, RELA=7, RELASZ=8, REL=17, RELSZ=18, TEXTREL=22, JMPREL=23, RELRSZ=35, RELR=36,
          FLAGS_1=0x6ffffffb)


def elf_symbols(label, d):
    """{name: defined} of one ELF32 little-endian AArch64 image, for the names other files can bind to: global and
    weak symbols, and local symbols with hidden or internal visibility (what the linker makes of a hidden global)."""
    if d[:4] != b'\x7fELF':
        raise ValueError('%s: not an ELF file' % label)
    if d[4] != 1 or d[5] != 1:
        raise ValueError('%s: not ELF32 little-endian' % label)
    machine, = struct.unpack_from('<H', d, 18)
    if machine != EM_AARCH64:
        raise ValueError('%s: not AArch64' % label)
    shoff, = struct.unpack_from('<I', d, 32)
    shentsize, shnum = struct.unpack_from('<HH', d, 46)
    sh = [struct.unpack_from('<IIIIIIIIII', d, shoff + i * shentsize) for i in range(shnum)]
    out = {}
    for s in sh:
        if s[1] != SHT_SYMTAB:
            continue
        stroff = sh[s[6]][4]
        for o in range(s[4] + s[9], s[4] + s[5], s[9]):
            name, _, _, info, other, shndx = struct.unpack_from('<IIIBBH', d, o)
            if info >> 4 in (1, 2) or (other & 3) in (1, 2):
                n = d[stroff + name:d.index(b'\0', stroff + name)].decode()
                out[n] = out.get(n, False) or shndx != SHN_UNDEF
    return out


def symbols(path):
    """[(name, defined)] of one ELF file, sorted."""
    with open(path, 'rb') as fh:
        return sorted(elf_symbols(path, fh.read()).items())


def all_defined(path):
    """The names of every defined, named symbol of the symbol table of an ELF32 image, local ones included (the linker makes the symbols a script
    defines with HIDDEN() local), file and section symbols excluded."""
    with open(path, 'rb') as fh:
        d = fh.read()
    elf_symbols(path, d)
    shoff, = struct.unpack_from('<I', d, 32)
    shentsize, shnum = struct.unpack_from('<HH', d, 46)
    sh = [struct.unpack_from('<IIIIIIIIII', d, shoff + i * shentsize) for i in range(shnum)]
    out = set()
    for s in sh:
        if s[1] == SHT_SYMTAB:
            stroff = sh[s[6]][4]
            for o in range(s[4] + s[9], s[4] + s[5], s[9]):
                name, _, _, info, _, shndx = struct.unpack_from('<IIIBBH', d, o)
                if name and shndx != SHN_UNDEF and (info & 15) not in (STT_SECTION, STT_FILE):
                    out.add(d[stroff + name:d.index(b'\0', stroff + name)].decode())
    return out


def elf_header(d):
    """(program headers [(type, offset, vaddr, filesz, memsz, flags)], e_entry) of an ELF32 little-endian file."""
    if d[:4] != b'\x7fELF' or d[4] != 1 or d[5] != 1:
        raise ValueError('not ELF32 little-endian')
    entry, phoff = struct.unpack_from('<II', d, 24)
    phentsize, phnum = struct.unpack_from('<HH', d, 42)
    ph = []
    for i in range(phnum):
        typ, off, va, _, filesz, memsz, flags, _ = struct.unpack_from('<IIIIIIII', d, phoff + i * phentsize)
        ph.append((typ, off, va, filesz, memsz, flags))
    return ph, entry


def file_offset(ph, addr, n):
    """The file offset of the n bytes at virtual address addr inside the file image of one PT_LOAD (None when none holds them)."""
    for typ, off, va, filesz, _, _ in ph:
        if typ == PT_LOAD and va <= addr and addr + n <= va + filesz:
            return off + addr - va
    return None


def dynamic(d, ph):
    """{tag name: value} of the PT_DYNAMIC of the image (the last value of a repeated tag)."""
    out = {}
    names = {v: k for k, v in DT.items()}
    for typ, off, _, filesz, _, _ in ph:
        if typ == PT_DYNAMIC:
            for o in range(off, off + filesz, 8):
                tag, val = struct.unpack_from('<II', d, o)
                if tag == 0:
                    break
                out[names.get(tag, 'tag_%x' % tag)] = val
    return out


def is_pie(path):
    """The linker's own mark of a position-independent executable: DT_FLAGS_1 has DF_1_PIE.  (e_type is not the
    witness: GNU ld writes ET_EXEC for a PIE whose first segment is linked at a non-zero address.)"""
    with open(path, 'rb') as fh:
        d = fh.read()
    ph, _ = elf_header(d)
    return bool(dynamic(d, ph).get('FLAGS_1', 0) & 0x08000000)


def dyn_relocs(path):
    """({what: count}, [bad ids]) of the dynamic relocations of the image: `P32_RELATIVE` (RELA entries without symbol), `RELR` (words
    the RELR bitmap entries relocate), and every other entry as a bad id `dynreloc:<offset>:<type or table>`; a REL, JMPREL or
    PLTREL table, a symbol table (DT_SYMTAB with relocations that name it) or DT_TEXTREL is bad as well."""
    with open(path, 'rb') as fh:
        d = fh.read()
    ph, _ = elf_header(d)
    dy = dynamic(d, ph)
    cnt, bad = {'P32_RELATIVE': 0, 'RELR': 0}, []

    def table(tag, size_tag):
        if tag not in dy or not dy.get(size_tag, 0):
            return None, 0
        o = file_offset(ph, dy[tag], dy[size_tag])
        if o is None:
            raise ValueError('%s lies outside the PT_LOAD image' % tag)
        return o, dy[size_tag]
    o, n = table('RELA', 'RELASZ')
    for k in range(n // 12 if o is not None else 0):
        off, info, _ = struct.unpack_from('<IIi', d, o + 12 * k)
        if info == R_AARCH64_P32_RELATIVE:
            cnt['P32_RELATIVE'] += 1
        else:
            bad.append('dynreloc:%08x:type%d%s' % (off, info & 0xff, '+sym' if info >> 8 else ''))
    for tag, szt in (('REL', 'RELSZ'), ('JMPREL', 'PLTRELSZ')):
        if dy.get(szt, 0):
            bad.append('dynreloc:table:%s' % tag)
    if 'TEXTREL' in dy:
        bad.append('dynreloc:table:TEXTREL')
    o, n = table('RELR', 'RELRSZ')
    where = 0
    for k in range(n // 4 if o is not None else 0):
        e, = struct.unpack_from('<I', d, o + 4 * k)
        if not e & 1:
            where = e + 4
            cnt['RELR'] += 1
        else:
            cnt['RELR'] += bin(e >> 1).count('1')
            where += 31 * 4
    return cnt, bad


def write_header(path):
    """Write the crt0 header words +16 (link address of the first PT_LOAD), +20 (bytes to map, first segment to the end of the last, page
    aligned) and +24 (bit 0: relocatable) into the ELF, as elf2nso.py `patch_guest_header` does.  ValueError when the magic is not at
    first segment + 8 or the entry is not the first segment.  Returns (base, span, flags)."""
    with open(path, 'rb') as fh:
        d = bytearray(fh.read())
    ph, entry = elf_header(d)
    loads = [p for p in ph if p[0] == PT_LOAD]
    if not loads:
        raise ValueError('no PT_LOAD segment')
    _, off, base, filesz, _, _ = loads[0]
    if filesz < 32 or struct.unpack_from('<I', d, off + 8)[0] != GUEST_MAGIC:
        raise ValueError('the guest header magic FNXG is not at the first segment + 8')
    if entry != base:
        raise ValueError('the entry %#x is not the first segment %#x' % (entry, base))
    last = loads[-1]
    span = (last[2] + last[4] - base + PAGE - 1) & ~(PAGE - 1)
    flags = 1 if dynamic(d, ph).get('FLAGS_1', 0) & 0x08000000 else 0
    struct.pack_into('<III', d, off + 16, base, span, flags)
    with open(path, 'wb') as fh:
        fh.write(d)
    return base, span, flags


def symtab_info(d):
    """(defs, strong, weak, commons, bad) of one ELF32 little-endian AArch64 object, from its symbol table: the global and weak
    definitions (a COMMON symbol is a definition and also listed in commons), the undefined global references, the undefined weak
    references, and `bad` = [name] of the symbols the pull-in closure does not model (indirect function, `@` in the name)."""
    elf_symbols('object', d)        # the header checks
    shoff, = struct.unpack_from('<I', d, 32)
    shentsize, shnum = struct.unpack_from('<HH', d, 46)
    sh = [struct.unpack_from('<IIIIIIIIII', d, shoff + i * shentsize) for i in range(shnum)]
    defs, strong, weak, commons, bad = set(), set(), set(), set(), []
    for s in sh:
        if s[1] != SHT_SYMTAB:
            continue
        stroff = sh[s[6]][4]
        for o in range(s[4] + s[9], s[4] + s[5], s[9]):
            name, _, _, info, _, shndx = struct.unpack_from('<IIIBBH', d, o)
            bind, typ = info >> 4, info & 15
            if not name or typ in (STT_SECTION, STT_FILE) or bind not in (1, 2):
                continue
            n = d[stroff + name:d.index(b'\0', stroff + name)].decode()
            if typ == STT_GNU_IFUNC or b'@' in d[stroff + name:d.index(b'\0', stroff + name)]:
                bad.append(n)
            if shndx == SHN_UNDEF:
                (strong if bind == 1 else weak).add(n)
            else:
                defs.add(n)
                if shndx == SHN_COMMON:
                    commons.add(n)
    return defs, strong, weak, commons, bad


def write_list(path, rows):
    with open(path, 'w', newline='\n') as fh:
        fh.write(''.join(r + '\n' for r in rows))




def object_files(top):
    out = []
    for cur, dirs, files in os.walk(top):
        dirs.sort()
        out += [os.path.join(cur, f) for f in sorted(files)]
    return out

# ---- host images (kind exe, shared): any ELF class and machine
def elf_any(d):
    """(class, e_type, e_entry, sections, program headers) of an ELF little-endian file of class 32 or 64; sections as dicts."""
    if d[:4] != b'\x7fELF' or d[5] != 1 or d[4] not in (1, 2):
        raise ValueError('not an ELF32/ELF64 little-endian file')
    w = d[4] == 2
    etype, = struct.unpack_from('<H', d, 16)
    entry, phoff, shoff = struct.unpack_from('<QQQ', d, 24) if w else struct.unpack_from('<III', d, 24)
    phentsize, phnum, shentsize, shnum = struct.unpack_from('<HHHH', d, 54 if w else 42)
    secs = []
    for i in range(shnum):
        v = struct.unpack_from('<IIQQQQIIQQ' if w else '<IIIIIIIIII', d, shoff + i * shentsize)
        secs.append(dict(type=v[1], addr=v[3], off=v[4], size=v[5], link=v[6], entsize=v[9]))
    ph = []
    for i in range(phnum):
        v = struct.unpack_from('<IIQQQQQQ' if w else '<IIIIIIII', d, phoff + i * phentsize)
        ph.append((v[0], v[2], v[5]) if w else (v[0], v[1], v[4]))      # type, offset, filesz
    return d[4], etype, entry, secs, ph


def syms_any(d, want):
    """[(name, bind, visibility, shndx, type)] of the section type `want` (2 symtab, 11 dynsym) of an ELF file of either class."""
    cls, _, _, secs, _ = elf_any(d)
    out = []
    for s in secs:
        if s['type'] != want:
            continue
        stroff = secs[s['link']]['off']
        for o in range(s['off'] + s['entsize'], s['off'] + s['size'], s['entsize']):
            if cls == 2:
                name, info, other, shndx = struct.unpack_from('<IBBH', d, o)
            else:
                name, _, _, info, other, shndx = struct.unpack_from('<IIIBBH', d, o)
            if name:
                out.append((d[stroff + name:d.index(b'\0', stroff + name)].decode(), info >> 4, other & 3, shndx, info & 15))
    return out


def dyn_tags(d):
    cls, _, _, secs, ph = elf_any(d)
    out = {}
    for typ, off, filesz in ph:
        if typ == PT_DYNAMIC:
            step = 16 if cls == 2 else 8
            for o in range(off, off + filesz, step):
                tag, val = struct.unpack_from('<QQ' if cls == 2 else '<II', d, o)
                if tag == 0:
                    break
                out[tag] = val
    return out


def version_globals(text):
    """The names a GNU ld version script exports as global: ([exact names], [wildcard patterns]).  Grammar modelled: version nodes
    `[TAG] { [global: | local:] names; ... } [DEP];`, `#` and `/* */` comments, quoted or bare names.  `extern` blocks and any other
    syntax raise ValueError (not modelled: no guess)."""
    import re
    text = re.sub(r'/\*.*?\*/', ' ', re.sub(r'#[^\n]*', ' ', text), flags=re.S)
    exact, pats = [], []
    pos = 0
    while True:
        m = re.compile(r'\s*([A-Za-z0-9_.]*)\s*\{').match(text, pos)
        if not m:
            break
        end = text.index('}', m.end())
        body, mode = text[m.end():end], 'global'
        for item in body.split(';'):
            item = item.strip()
            while re.match(r'(global|local)\s*:', item):
                mode = item[:item.index(':')].strip()
                item = item[item.index(':') + 1:].strip()
            if not item:
                continue
            if 'extern' in item or '{' in item or not re.fullmatch(r'"?[A-Za-z0-9_.$*?\[\]!-]+"?', item):
                raise ValueError('version script syntax not modelled: %r' % item)
            if mode == 'global':
                item = item.strip('"')
                (pats if re.search(r'[*?\[]', item) else exact).append(item)
        rest = re.compile(r'\s*[A-Za-z0-9_.]*\s*;').match(text, end + 1)
        pos = rest.end() if rest else end + 1
    if text[pos:].strip():
        raise ValueError('version script: unparsed text %r' % text[pos:pos + 40].strip())
    return exact, pats


def host_main(a):
    """Rules for a host image (exe, shared): no undefined name except those the link's shared libraries define (their .dynsym); every name the
    inputs reference or define is defined in the output or by a library; no text relocation; for `shared`, every name the version script exports as
    global is defined, global and default-visible in the output's .dynsym (a wildcard must match one)."""
    import fnmatch
    os.environ['GATE_OUT'] = a.out
    os.makedirs(a.out, exist_ok=True)
    final = os.path.join(a.out, a.name)
    with open(a.elf, 'rb') as fh:
        dd = fh.read()
    cls, etype, entry, _, _ = elf_any(dd)
    defs, strong, weak, targets = set(), set(), set(), set()
    for p in object_files(a.objs):
        with open(p, 'rb') as fh:
            d = fh.read()
        targets.add((elf_any(d)[0], struct.unpack_from('<H', d, 18)[0]))
        for n, bind, vis, shndx, typ in syms_any(d, 2):
            if typ in (STT_SECTION, STT_FILE) or bind not in (1, 2):
                continue
            (defs if shndx != SHN_UNDEF else strong if bind == 1 else weak).add(n)
    libdef = set()
    for p in a.lib:
        with open(p, 'rb') as fh:
            libdef |= {n for n, _, _, shndx, _ in syms_any(fh.read(), 11) if shndx != SHN_UNDEF}
    dyn = syms_any(dd, 11)
    out_def = {n for n, bind, _, shndx, _ in dyn + syms_any(dd, 2) if shndx != SHN_UNDEF}
    ids = ['cover:' + n for n in sorted((defs | strong | weak) - out_def - libdef)]
    ids += ['output-undefined:' + n for n in sorted({n for n, _, _, shndx, _ in dyn if shndx == SHN_UNDEF} - libdef)]
    tags = dyn_tags(dd)
    if 22 in tags or tags.get(30, 0) & 4:
        ids.append('textrel')
    if a.kind == 'exe' and entry == 0:
        ids.append('entry:none')
    nexp = 0
    if a.kind == 'shared':
        try:
            exact, pats = version_globals(open(a.version_script).read())
        except ValueError as e:
            exact, pats = [], []
            ids.append('version-script:%s' % e)
        exported = {n for n, bind, vis, shndx, _ in dyn if shndx != SHN_UNDEF and bind in (1, 2) and vis == 0}
        for n in exact:
            nexp += 1
            if n not in exported:
                ids.append('export:%s' % n)
        for pt in pats:
            nexp += 1
            if not any(fnmatch.fnmatchcase(n, pt) for n in exported):
                ids.append('export-pattern:%s' % pt)
    universe = len(defs | strong | weak) + nexp + 2
    detail = '%s image, %d input names, %d library definitions, %d exports' % (a.kind, len(defs | strong | weak), len(libdef), nexp)
    gatefile.write('ld', universe, universe - len(ids), ids, detail)
    gateids.write_ids('ld', ids)
    for i in ids:
        print('ldcheck: %s' % i, file=sys.stderr)
    print('ldcheck: %s' % detail, file=sys.stderr)
    if ids:
        sys.exit(1)
    shutil.copyfile(a.elf, final)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--elf', required=True)
    ap.add_argument('--objs', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--name', required=True)
    ap.add_argument('--kind', default='pie', choices=('pie', 'exe', 'shared'))
    ap.add_argument('--version-script', default=None)
    ap.add_argument('--lib', action='append', default=[], help='a shared library of the link (its .dynsym defines names the image may leave undefined)')
    a = ap.parse_args()
    if a.kind != 'pie':
        return host_main(a)
    os.environ['GATE_OUT'] = a.out
    os.makedirs(a.out, exist_ok=True)
    final = os.path.join(a.out, a.name)
    defs, strong, weak = set(), set(), set()
    n_objs = 0
    for p in object_files(a.objs):
        if not p.endswith('.o'):
            sys.exit('ldcheck: %s: not an object file' % p)
        with open(p, 'rb') as fh:
            d, st, wk, _, _ = symtab_info(fh.read())
        defs |= d
        strong |= st
        weak |= wk
        n_objs += 1
    names = defs | strong | weak
    out = symbols(a.elf)
    have = all_defined(a.elf)
    weak_only = {n for n in weak if n not in strong}
    ids = [('weak:' if n in weak_only else 'cover:') + n for n in sorted(names - have)]
    ids += ['output-undefined:' + n for n, df in out if not df]
    cnt, badrel = dyn_relocs(a.elf)
    ids += badrel
    if not is_pie(a.elf):
        ids.append('pie:no-DF_1_PIE')
    hdr = None
    if not ids:
        shutil.copyfile(a.elf, final)
        try:
            hdr = write_header(final)
        except ValueError as e:
            ids.append('header:%s' % e)
    ndyn = cnt['P32_RELATIVE'] + cnt['RELR'] + len(badrel)
    universe = len(names) + ndyn + 2
    detail = '%d inputs, %d names, %d undefined in the output; dynamic relocations: P32_RELATIVE %d, RELR %d, other %d' % (
        n_objs, len(names), len([1 for _, df in out if not df]), cnt['P32_RELATIVE'], cnt['RELR'], len(badrel))
    if hdr:
        detail += '; header base %#x size %#x flags %d' % hdr
    gatefile.write('ld', universe, universe - len(ids), ids, detail)
    gateids.write_ids('ld', ids)
    for i in ids:
        print('ldcheck: %s' % i, file=sys.stderr)
    print('ldcheck: %s' % detail, file=sys.stderr)
    if ids:
        if os.path.exists(final):
            os.remove(final)
        sys.exit(1)


if __name__ == '__main__':
    main()
