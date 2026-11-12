#!/usr/bin/env python3
"""link stage `ld` - the guest ELF from the lifted objects, the runtime and the loader (L1 CONTRACT).

    ld.py --ld <linker> [--ld-arg A ...] --script guest.ld --in <dir> [--in <dir> ...] --out <dir>
          [--lib-dir D ...] [--lib NAME ...] [--startfile F ...] [--endfile F ...]

Inputs are every `*.o` and `*.a` below the --in directories (L3's lifted objects and lifter runtime, the guest runtime, the loader, L5's
platform), in the order of their path relative to their directory.  Every archive member is linked (`--whole-archive`): nothing is
dropped, so the set of symbols is the set of the inputs.  The toolchain's libraries (--lib-dir, --lib) are searched, as the gcc driver
searches them with guest_pie.specs for the ILP32 PIC multilib: `startfiles -L.. [inputs, whole] --start-group -lNAME.. --end-group
endfiles`.  The stage's options are the specs' options (`-pie --no-dynamic-linker -z text -z now -z pack-relative-relocs
-z max-page-size=0x1000 --build-id=sha1`) with `guest.ld` as the script, link base 0 and no `--gc-sections` (a section the linker
may drop is a name the inputs may lose).  `--ld-arg` accepts only the emulation options (`-m <emulation>`, `-EL`, `-X`): the linker
runs STRICT, with no option that defines a symbol or accepts an unresolved one.

The link is position independent (`-pie`, `-z text`): no code address is fixed (S7).  Segments are aligned to the 4 KiB page of the
Switch (`-z max-page-size`).  The exe image is not an input: the loader maps the owner's exe at run time.

Rule (gate `ld`): the output has no undefined symbol, a weak reference without a definition included.  The set is computed BEFORE
the link, from ELF symbol tables only (no linker message, no map file, no name or text pattern):
  1. D, R: the global and weak definitions, and the undefined references (strong, weak) of the start files, the inputs and the end files
     (the symbol tables of the object files and of every archive member).
  2. S: the names the script itself defines.  The script defines every boundary symbol unconditionally, so S is the set of the defined
     symbols of the linker's own output for the script and one empty relocatable object (written here, byte by byte) - the linker's
     answer, read from its symbol table.
  3. The pull-in closure, as GNU ld does it (`elf_link_add_archive_symbols`): archives in link-line order, `--start-group` rescans all
     of them until nothing changes; within an archive the entries of its symbol index (`/` member) are walked in index order and the member
     of an entry is taken iff the entry's name is an undefined STRONG reference (D and S do not contain it; a weak reference pulls nothing).
     A taken member adds its definitions and references.  The library file of `-lNAME` is the first `libNAME.a` of the -L directories.
  4. undefined = (strong and weak references) - (D and members' definitions) - S.
Preconditions, each CHECKED by structure and failing the stage when violated: every library archive has its symbol index and the index holds
exactly the global and weak definitions of its members; a library member defines no COMMON symbol, no indirect function (STT_GNU_IFUNC),
no versioned symbol (a `@` in the name); an input's COMMON symbol is not an entry of a library's index; no library index entry names a
symbol of S that is referenced.  Within these the closure is the linker's choice, and a member taken that the linker would not take, or the
reverse, is found by the check after the link:
  5. after the strict link: every referenced name is defined in the output's symbol table, and the output's symbol table holds no
     undefined symbol (weak included), no dynamic relocation but R_AARCH64_P32_RELATIVE, a PIE mark.
With undefined names the stage writes `undefined.tsv` (`name<TAB>strong|weak`), makes NO ELF and fails (no link is run); with none the
strict link must succeed, and a linker failure is a stage failure with the linker's own message.
On success the stage writes the three words of the crt0 header into the ELF (the same rule as elf2nso.py `patch_guest_header`): +16 the
link address of the first PT_LOAD, +20 the bytes from there to the end of the last PT_LOAD (memsz), page aligned, +24 bit 0 = relocatable
(DF_1_PIE).  The header magic "FNXG" must stand at first segment + 8 and the entry must be the first segment's address, else the stage fails.

Outputs in --out:  guest.elf, inputs.rsp (the input list as given to the linker), libmembers.txt (the library members of the closure),
undefined.tsv (only on failure).
Gate file `ld` ($GATE_OUT/ld.json, full id list in $GATE_OUT/ld.ids): universe = the names of the inputs and of the closure's members +
the dynamic relocation entries (RELA entries, RELR words) + 2 (the PIE mark, the crt0 header); certified = those that hold.  Ids: the
undefined name, `weak:<name>` for a name referenced only weakly, `cover:<name>`, `output-undefined:<name>`, `dynreloc:..`, `pie`, `header:..`.
guest.elf is exe-derived (lifted code): container and fnv-nx-build only.
"""
import argparse, os, struct, subprocess, sys

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


def ar_members(path):
    """[(name, offset, bytes)] of an `ar` archive (GNU names, long names through the `//` table; the symbol index and the tables skipped)."""
    with open(path, 'rb') as fh:
        d = fh.read()
    if d[:8] != b'!<arch>\n':
        raise ValueError('%s: not an ar archive' % path)
    raw, longnames, o = [], b'', 8
    while o + 60 <= len(d):
        name, size = d[o:o + 16].rstrip(), int(d[o + 48:o + 58])
        body = d[o + 60:o + 60 + size]
        if name == b'//':
            longnames = body
        elif name not in (b'/', b'/SYM64/'):
            raw.append((name, o, body))
        o += 60 + size + (size & 1)
    out = []
    for name, off, body in raw:
        if name[:1] == b'/' and name[1:].isdigit():
            i = int(name[1:])
            name = longnames[i:longnames.index(b'\n', i)].rstrip(b'/')
        else:
            name = name.rstrip(b'/')
        out.append((name.decode(), off, body))
    return out


def members(path):
    """[(label, bytes)]: the file itself, or the members of an `ar` archive (the symbol index and name table skipped)."""
    with open(path, 'rb') as fh:
        d = fh.read()
    if d[:8] != b'!<arch>\n':
        return [(path, d)]
    return [('%s(%d)' % (path, off), body) for _, off, body in ar_members(path)]


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
    """[(name, defined)] over the file (every member of an archive), sorted: defined when any member defines it."""
    out = {}
    for label, d in members(path):
        for n, df in elf_symbols(label, d).items():
            out[n] = out.get(n, False) or df
    return sorted(out.items())


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


def armap(path):
    """([(name, member offset)] in index order, {offset: (member name, bytes)}) of an `ar` archive; ValueError without a symbol index
    (the `/` member) or with a malformed one."""
    with open(path, 'rb') as fh:
        d = fh.read()
    if d[:8] != b'!<arch>\n':
        raise ValueError('%s: not an ar archive' % path)
    names = {off: (n, b) for n, off, b in ar_members(path)}
    o = 8
    if d[o:o + 16].rstrip() != b'/':
        raise ValueError('%s: no symbol index' % path)
    size = int(d[o + 48:o + 58])
    body = d[o + 60:o + 60 + size]
    n, = struct.unpack_from('>I', body, 0)
    offs = struct.unpack_from('>%dI' % n, body, 4)
    strs = body[4 + 4 * n:].split(b'\0')[:n]
    if len(strs) != n or any(off not in names for off in offs):
        raise ValueError('%s: malformed symbol index' % path)
    return [(nm.decode(), off) for nm, off in zip(strs, offs)], names


def find_lib(libdirs, name):
    """The first `lib<name>.a` of the -L directories (the linker's search for -l<name>); ValueError when there is none."""
    for dd in libdirs:
        q = os.path.join(dd, 'lib%s.a' % name)
        if os.path.isfile(q):
            return q
    raise ValueError('-l%s: no lib%s.a in the -L directories' % (name, name))


def closure(base, archives, scripted):
    """The pull-in closure of the linker over archives [(path, index, {offset: (name, bytes)})] in link-line order (`--start-group`).
    base: [(defs, strong, weak, commons, bad)] of the start files, inputs and end files; scripted: the names the script defines.
    Returns (taken [(archive path, member name, offset)], defs, strong, weak, ValueError text list of unmodelled cases)."""
    defs, strong, weak, problems = set(), set(), set(), []
    commons = set()
    for df, st, wk, cm, _ in base:
        defs |= df
        strong |= st
        weak |= wk
        commons |= cm
    taken, done = [], set()
    infos = {}
    again = True
    while again:
        again = False
        for ai, (path, index, mem) in enumerate(archives):
            inner = True
            while inner:
                inner = False
                for name, off in index:
                    if name in strong and name not in defs and name not in scripted and (ai, off) not in done:
                        mname, body = mem[off]
                        if (ai, off) not in infos:
                            infos[(ai, off)] = symtab_info(body)
                        df, st, wk, cm, bad = infos[(ai, off)]
                        problems += ['%s(%s): %s' % (path, mname, 'common ' + x) for x in sorted(cm)] + ['%s(%s): unmodelled symbol %s' % (path, mname, x) for x in sorted(bad)]
                        done.add((ai, off))
                        taken.append((path, mname, off))
                        defs |= df
                        strong |= st
                        weak |= wk
                        inner = again = True
    for path, index, _ in archives:
        for name, _ in index:
            if name in commons:
                problems.append('%s: an input COMMON symbol %s is an entry of the library index' % (path, name))
            if name in scripted and (name in strong or name in weak):
                problems.append('%s: the script defines %s, which the library index lists' % (path, name))
    return taken, defs, strong, weak, problems


def index_matches(index, mem):
    """True when the symbol index holds exactly the global and weak definitions of the members (a name once per member that defines it)."""
    want = []
    for off in sorted(mem):
        want += [(n, off) for n in sorted(symtab_info(mem[off][1])[0])]
    return sorted(index) == sorted(want)


def empty_object():
    """The bytes of an ELF32 little-endian AArch64 relocatable object with no section but the name table."""
    shstr = b'\0.shstrtab\0'
    shdrs = struct.pack('<IIIIIIIIII', 0, 0, 0, 0, 0, 0, 0, 0, 0, 0) + struct.pack('<IIIIIIIIII', 1, 3, 0, 0, 52, len(shstr), 0, 0, 1, 0)
    hdr = b'\x7fELF' + bytes([1, 1, 1, 0]) + bytes(8) + struct.pack('<HHIIIIIHHHHHH', 1, EM_AARCH64, 1, 0, 0, 52 + len(shstr), 0, 52, 0, 0, 40, 2, 1)
    return hdr + shstr + shdrs


ALLOWED_EMULATION = ('-EL', '-X')


def check_ld_args(args):
    """The linker options the stage accepts: the emulation (`-m <name>` or `-m<name>`), `-EL`, `-X`.  Nothing that defines a symbol or
    accepts an unresolved one is an option of the stage.  ValueError on any other argument."""
    prev = None
    for a in args:
        if prev == '-m' or a in ALLOWED_EMULATION or (a.startswith('-m') and len(a) > 2 and a[2] != '-'):
            prev = None
        elif a == '-m':
            prev = a
        else:
            raise ValueError('--ld-arg %s: the stage accepts only -m <emulation>, -EL and -X' % a)
    if prev:
        raise ValueError('--ld-arg -m: no emulation name')


def inputs(dirs):
    out, seen = [], {}
    for top in dirs:
        found, todo = [], [top]
        while todo:
            cur = todo.pop()
            for f in sorted(os.listdir(cur)):
                q = os.path.join(cur, f)
                if os.path.isdir(q):
                    todo.append(q)
                elif f.endswith(('.o', '.a')):
                    found.append(q)
        for p in sorted(found, key=lambda p: os.path.relpath(p, top)):
            b = os.path.basename(p)
            if b in seen:
                raise ValueError('two link inputs are named %s: %s and %s' % (b, seen[b], p))
            seen[b] = p
            out.append(p)
    return out


def command(ld, script, out, rsp, libdirs=(), libs=(), startfiles=(), endfiles=()):
    """The strict linker command: the driver's line with guest_pie.specs (`-pie --no-dynamic-linker -z text -z now -z pack-relative-relocs
    -z max-page-size=0x1000 --build-id=sha1`, startfiles, -L dirs, inputs, `--start-group -l.. --end-group`, endfiles) with our script,
    no symbol definition on the command line and no `--gc-sections`; our inputs whole, the toolchain's libraries searched."""
    cmd = list(ld) + ['-pie', '--no-dynamic-linker', '-z', 'text', '-z', 'now', '-z', 'pack-relative-relocs', '-z', 'max-page-size=0x1000',
                      '-z', 'common-page-size=0x1000', '--build-id=sha1', '-T', script, '-o', out]
    cmd += list(startfiles) + ['-L' + d for d in libdirs] + ['--whole-archive', '@' + rsp, '--no-whole-archive']
    if libs:
        cmd += ['--start-group'] + ['-l' + n for n in libs] + ['--end-group']
    return cmd + list(endfiles)


def write_list(path, rows):
    with open(path, 'w', newline='\n') as fh:
        fh.write(''.join(r + '\n' for r in rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ld', required=True, help='path of the AArch64 ILP32 linker (no PATH lookup)')
    ap.add_argument('--ld-arg', action='append', default=[], help='emulation options of the toolchain (-m <emulation>, -EL, -X)')
    ap.add_argument('--script', required=True)
    ap.add_argument('--in', dest='ins', action='append', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--lib-dir', action='append', default=[], help='-L directory of the toolchain libraries (the driver\'s search path)')
    ap.add_argument('--lib', action='append', default=[], help='toolchain library NAME (-lNAME), searched in --lib-dir, not swallowed')
    ap.add_argument('--startfile', action='append', default=[], help='object linked before the inputs (crti.o, crtbegin.o)')
    ap.add_argument('--endfile', action='append', default=[], help='object linked after the libraries (crtend.o, crtn.o)')
    a = ap.parse_args()
    try:
        check_ld_args(a.ld_arg)
    except ValueError as e:
        sys.exit('ld: %s' % e)
    os.makedirs(a.out, exist_ok=True)
    elf, rsp, und, libm, probe_o, probe = (os.path.join(a.out, n) for n in ('guest.elf', 'inputs.rsp', 'undefined.tsv', 'libmembers.txt', 'probe.o', 'probe.elf'))
    try:
        ins = inputs(a.ins)
    except ValueError as e:
        sys.exit('ld: %s' % e)
    write_list(rsp, ['"%s"' % p for p in ins])
    for stale in (elf, und, libm, probe_o, probe):
        if os.path.exists(stale):
            os.remove(stale)
    # S: what the script defines, from the linker's own symbol table for the script and one empty object
    with open(probe_o, 'wb') as fh:
        fh.write(empty_object())
    prsp = os.path.join(a.out, 'probe.rsp')
    write_list(prsp, ['"%s"' % probe_o])
    r = subprocess.run(command([a.ld] + a.ld_arg, a.script, probe, prsp), capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(probe):
        sys.stderr.write(r.stderr[-4000:])
        sys.exit('ld: the linker failed on the script alone (exit %d)' % r.returncode)
    scripted = all_defined(probe)
    for f in (probe_o, probe, prsp):
        os.remove(f)
    objs = a.startfile + ins + a.endfile
    try:
        base = []
        for p in objs:
            parts = [symtab_info(b) for _, b in members(p)]
            base.append(tuple(set().union(*[x[i] for x in parts]) if parts else set() for i in range(4)) + (sorted(x for q in parts for x in q[4]),))
        archives = []
        for n in a.lib:
            path = find_lib(a.lib_dir, n)
            index, mem = armap(path)
            if not index_matches(index, mem):
                raise ValueError('%s: the symbol index is not the set of the definitions of its members' % path)
            archives.append((path, index, mem))
        taken, defs, strong, weak, problems = closure(base, archives, scripted)
    except (OSError, ValueError) as e:
        sys.exit('ld: %s' % e)
    if problems:
        sys.exit('ld: the pull-in closure does not model: %s' % '; '.join(problems))
    write_list(libm, ['%s(%s)' % (p, m) for p, m, _ in taken])
    names = defs | strong | weak
    und_strong = sorted(strong - defs - scripted)
    und_weak = sorted(weak - strong - defs - scripted)
    ids = list(und_strong) + ['weak:' + n for n in und_weak]
    cnt = {'P32_RELATIVE': 0, 'RELR': 0}
    badrel, hdr = [], None
    if not ids:
        rl = subprocess.run(command([a.ld] + a.ld_arg, a.script, elf, rsp, a.lib_dir, a.lib, a.startfile, a.endfile), capture_output=True, text=True)
        if rl.returncode != 0 or not os.path.exists(elf):
            sys.stderr.write(rl.stderr[-4000:])
            sys.exit('ld: the linker failed (exit %d)' % rl.returncode)
        out = symbols(elf)
        have = all_defined(elf)
        ids += ['cover:' + n for n in sorted(names - have)] + ['output-undefined:' + n for n, df in out if not df]
        cnt, badrel = dyn_relocs(elf)
        ids += badrel
        if not is_pie(elf):
            ids.append('pie:no-DF_1_PIE')
        if not ids:
            try:
                hdr = write_header(elf)
            except ValueError as e:
                ids.append('header:%s' % e)
    ndyn = cnt['P32_RELATIVE'] + cnt['RELR'] + len(badrel)
    universe = len(names | scripted) + ndyn + 2
    detail = '%d inputs, %d library members, %d names, %d undefined (%d weak only); dynamic relocations: P32_RELATIVE %d, RELR %d, other %d' % (
        len(objs), len(taken), len(names), len(und_strong) + len(und_weak), len(und_weak), cnt['P32_RELATIVE'], cnt['RELR'], len(badrel))
    if hdr:
        detail += '; header base %#x size %#x flags %d' % hdr
    gatefile.write('ld', universe, universe - len(ids), ids, detail)
    gateids.write_ids('ld', ids)
    print(detail)
    if und_strong or und_weak:
        write_list(und, ['%s\tstrong' % n for n in und_strong] + ['%s\tweak' % n for n in und_weak])
    if ids:
        if os.path.exists(elf):
            os.remove(elf)
        sys.exit(1)


if __name__ == '__main__':
    main()
