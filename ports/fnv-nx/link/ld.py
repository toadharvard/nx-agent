#!/usr/bin/env python3
"""link stage `ld` - the guest ELF from the lifted objects, the runtime and the loader (L1 CONTRACT).

    ld.py --ld <linker> [--ld-arg A ...] --script guest.ld --in <dir> [--in <dir> ...] --out <dir>

Inputs are every `*.o` and `*.a` below the --in directories (L3's lifted objects and lifter runtime, the guest
runtime, the loader, L5's platform), in the order of their path relative to their directory.  Every archive
member is linked (`--whole-archive`): nothing is dropped, so the set of symbols is the set of the inputs.

The link is position independent (`-pie`, `-z text`): no code address is fixed (S7).  Segments are aligned to the
4 KiB page of the Switch (`-z max-page-size`), not to the 64 KiB default of the AArch64 linker.  The exe image is not an
input: the loader maps the owner's exe at run time.

Toolchain libraries (--lib-dir D, --lib NAME, --startfile F, --endfile F) are not inputs: they are searched, as the gcc driver searches
them with guest_pie.specs for the ILP32 PIC multilib: `startfiles -L.. [inputs, whole] --start-group -lNAME.. --end-group endfiles`.  The
stage's options are the specs' options (`-pie --no-dynamic-linker -z text -z now -z pack-relative-relocs -z max-page-size=0x1000
--build-id=sha1`) with `guest.ld` as the script, link base 0 (no `--defsym=FNVNX_GUEST_BASE`) and no `--gc-sections`: the set of
symbols is the set of the inputs, and a section the linker may drop is a name the inputs may lose.  The members of the libraries
that the linker pulled in (its own map file, `Archive member included`) are part of the link: the names they reference count with
the inputs' names under the rule below, and their definitions count as definitions.

Rule (gate `ld`): the output has no undefined symbol, weak ones included.  The linker runs with
`--unresolved-symbols=ignore-all --noinhibit-exec` so that the link completes and the whole set is computed, not the first error
(a PC-relative data reference to an undefined symbol, as in the dispatcher table, is a relocation error of the linker in a PIE, and
`--noinhibit-exec` keeps the output so that the names can still be computed; when the linker reports errors and no name is undefined,
the stage fails with the linker's message):
undefined = names some input references without defining (weak references included) minus the names the output
defines (the linker's own resolution: other inputs and the symbols the linker script provides).  The output
must carry the linker's PIE mark (DT_FLAGS_1 DF_1_PIE), and its dynamic relocations must be R_AARCH64_P32_RELATIVE only, in RELA and
RELR form (no symbol, no other type, no REL/JMPREL table, no text relocation): the self-relocation of the guest adds the slide
to every entry and to nothing else.  With one or more undefined symbols the stage writes `undefined.tsv`, removes the ELF and fails: an ELF with an unresolved
reference is never an output.  On success the stage writes the three words of the crt0 header into the ELF (the same rule as
elf2nso.py `patch_guest_header`): +16 the link address of the first PT_LOAD, +20 the bytes from there to the end of the last
PT_LOAD (memsz), page aligned, +24 bit 0 = relocatable (DF_1_PIE).  The header magic "FNXG" must stand at first segment + 8 and the entry
must be the first segment's address, else the stage fails.

Outputs in --out:  guest.elf, guest.map, inputs.rsp (the input list as given to the linker), libmembers.txt (the library members the
linker pulled in), undefined.tsv (only on failure).
Gate file `ld` ($GATE_OUT/ld.json, full id list in $GATE_OUT/ld.ids): universe = the names of the inputs and of the pulled library
members + the dynamic relocation entries (RELA entries, RELR words) + 2 (the PIE mark, the crt0 header); certified = those that
hold (not undefined, a P32_RELATIVE entry, the mark, the header).
guest.elf is exe-derived (lifted code): container and fnv-nx-build only.
"""
import argparse, os, struct, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'contract'))
import gatefile
import gateids

EM_AARCH64, SHT_SYMTAB, SHN_UNDEF = 183, 2, 0
PT_LOAD, PT_DYNAMIC = 1, 2
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


def map_members(path):
    """Sorted [(archive path, member name)] the linker pulled in to satisfy a reference, from its map file."""
    out, on = [], False
    with open(path) as fh:
        for ln in fh:
            if ln.startswith('Archive member included'):
                on = True
            elif on and ln.strip() and not ln[0].isspace():
                w = ln.split()[0]
                if w.endswith(')') and '(' in w:
                    out.append((w[:w.rindex('(')], w[w.rindex('(') + 1:-1]))
                else:
                    break
    return sorted(set(out))


def member_symbols(archive, name):
    """{symbol: defined} of the member `name` of `archive` (ValueError when two members of that name differ)."""
    found = [b for n, _, b in ar_members(archive) if n == name]
    if not found:
        raise ValueError('%s has no member %s' % (archive, name))
    if len(set(found)) > 1:
        raise ValueError('%s: the member name %s is ambiguous' % (archive, name))
    return elf_symbols('%s(%s)' % (archive, name), found[0])


def undefined(referenced, out_syms):
    """Names an input leaves undefined that the output does not define (the linker's own resolution of the rest)."""
    have = {n for n, df in out_syms if df}
    return sorted(n for n in referenced if n not in have)


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


def command(ld, script, out, rsp, libdirs=(), libs=(), startfiles=(), endfiles=(), mapfile=None):
    """The linker command: the driver's line with guest_pie.specs (`-pie --no-dynamic-linker -z text -z now -z pack-relative-relocs
    -z max-page-size=0x1000 --build-id=sha1`, startfiles, -L dirs, inputs, `--start-group -l.. --end-group`, endfiles) with our script,
    no `--defsym` and no `--gc-sections`; our inputs whole, the toolchain's libraries searched."""
    cmd = list(ld) + ['-pie', '--no-dynamic-linker', '-z', 'text', '-z', 'now', '-z', 'pack-relative-relocs', '-z', 'max-page-size=0x1000',
                      '-z', 'common-page-size=0x1000', '--build-id=sha1', '--unresolved-symbols=ignore-all', '--noinhibit-exec',
                      '-T', script, '-o', out]
    if mapfile:
        cmd += ['-Map', mapfile]
    cmd += list(startfiles) + ['-L' + d for d in libdirs] + ['--whole-archive', '@' + rsp, '--no-whole-archive']
    if libs:
        cmd += ['--start-group'] + ['-l' + n for n in libs] + ['--end-group']
    return cmd + list(endfiles)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ld', required=True, help='path of the AArch64 ILP32 linker (no PATH lookup)')
    ap.add_argument('--ld-arg', action='append', default=[], help='emulation options of the toolchain (-m.., -EL, -X)')
    ap.add_argument('--script', required=True)
    ap.add_argument('--in', dest='ins', action='append', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--lib-dir', action='append', default=[], help='-L directory of the toolchain libraries (the driver\'s search path)')
    ap.add_argument('--lib', action='append', default=[], help='toolchain library NAME (-lNAME), searched in --lib-dir, not swallowed')
    ap.add_argument('--startfile', action='append', default=[], help='object linked before the inputs (crti.o, crtbegin.o)')
    ap.add_argument('--endfile', action='append', default=[], help='object linked after the libraries (crtend.o, crtn.o)')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    elf, rsp, und, mapf, libm = (os.path.join(a.out, n) for n in ('guest.elf', 'inputs.rsp', 'undefined.tsv', 'guest.map', 'libmembers.txt'))
    try:
        ins = inputs(a.ins)
    except ValueError as e:
        sys.exit('ld: %s' % e)
    with open(rsp, 'w', newline='\n') as fh:
        fh.write(''.join('"%s"\n' % p for p in ins))
    for stale in (elf, und, mapf, libm):
        if os.path.exists(stale):
            os.remove(stale)
    r = subprocess.run(command([a.ld] + a.ld_arg, a.script, elf, rsp, a.lib_dir, a.lib, a.startfile, a.endfile, mapf), capture_output=True, text=True)
    if not os.path.exists(elf):
        sys.stderr.write(r.stderr[-4000:])
        sys.exit('ld: the linker failed (exit %d), no output' % r.returncode)
    try:
        pulled = map_members(mapf)
        libsyms = [member_symbols(ar, m) for ar, m in pulled]
    except (OSError, ValueError) as e:
        os.remove(elf)
        sys.exit('ld: %s' % e)
    with open(libm, 'w', newline='\n') as fh:
        fh.write(''.join('%s(%s)\n' % x for x in pulled))
    objs = a.startfile + ins + a.endfile
    insyms = [dict(symbols(p)) for p in objs] + libsyms
    refs = {n for sy in insyms for n, df in sy.items() if not df}
    names = {n for sy in insyms for n in sy}
    bad = undefined(refs, symbols(elf))
    cnt, badrel = dyn_relocs(elf)
    ndyn = cnt['P32_RELATIVE'] + cnt['RELR'] + len(badrel)
    ids = list(bad) + badrel
    pie = is_pie(elf)
    if not pie:
        ids.append('pie:no-DF_1_PIE')
    hdr = None
    if not ids and r.returncode == 0:
        try:
            hdr = write_header(elf)
        except ValueError as e:
            ids.append('header:%s' % e)
    elif not any(i.startswith('header') for i in ids):
        ids.append('header:not-written')
    universe = len(names) + ndyn + 2
    detail = '%d inputs, %d library members, %d names, %d undefined; dynamic relocations: P32_RELATIVE %d, RELR %d, other %d' % (
        len(objs), len(pulled), len(names), len(bad), cnt['P32_RELATIVE'], cnt['RELR'], len(badrel))
    if hdr:
        detail += '; header base %#x size %#x flags %d' % hdr
    gatefile.write('ld', universe, universe - len(ids), ids, detail)
    gateids.write_ids('ld', ids)
    print(detail)
    if bad:
        with open(und, 'w', newline='\n') as fh:
            fh.write(''.join(n + '\n' for n in bad))
    if ids:
        os.remove(elf)
        if not bad and r.returncode != 0:
            sys.stderr.write(r.stderr[-4000:])
        sys.exit(1)
    if r.returncode != 0:
        os.remove(elf)
        sys.stderr.write(r.stderr[-4000:])
        sys.exit('ld: the linker failed (exit %d)' % r.returncode)


if __name__ == '__main__':
    main()
