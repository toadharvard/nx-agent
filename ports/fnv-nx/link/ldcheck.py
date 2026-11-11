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
GUEST_MAGIC = 0x47584E46      # "FNXG" at the first segment + 8 (toolchain/guest/crt0.s)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--elf', required=True)
    ap.add_argument('--objs', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--name', required=True)
    a = ap.parse_args()
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
