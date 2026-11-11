#!/usr/bin/env python3
"""link stage `ld` - the guest ELF from the lifted objects, the runtime and the loader (L1 CONTRACT).

    ld.py --ld <linker> [--ld-arg A ...] --script guest.ld --in <dir> [--in <dir> ...] --out <dir>

Inputs are every `*.o` and `*.a` below the --in directories (L3's lifted objects and lifter runtime, the guest
runtime, the loader, L5's platform), in the order of their path relative to their directory.  Every archive
member is linked (`--whole-archive`): nothing is dropped, so the set of symbols is the set of the inputs.

The link is position independent (`-pie`, `-z text`): no code address is fixed (S7).  Segments are aligned to the
4 KiB page of the Switch (`-z max-page-size`), not to the 64 KiB default of the AArch64 linker.  The exe image is not an
input: the loader maps the owner's exe at run time.

Rule (gate `ld`): the output has no undefined symbol, weak ones included.  The linker runs with
`--unresolved-symbols=ignore-all` so that the link completes and the whole set is computed, not the first error:
undefined = names some input references without defining (weak references included) minus the names the output
defines (the linker's own resolution: other inputs and the symbols the linker script provides).  The output
must carry the linker's PIE mark (DT_FLAGS_1 DF_1_PIE).  With one or more
undefined symbols the stage writes `undefined.tsv`, removes the ELF and fails: an ELF with an unresolved
reference is never an output.

Outputs in --out:  guest.elf, inputs.rsp (the input list as given to the linker), undefined.tsv (only on failure).
Gate file `ld` ($GATE_OUT/ld.json): universe = the names of the inputs, certified = those not undefined.
guest.elf is exe-derived (lifted code): container and fnv-nx-build only.
"""
import argparse, os, struct, subprocess, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'contract'))
import gatefile

EM_AARCH64, SHT_SYMTAB, SHN_UNDEF = 183, 2, 0


def members(path):
    """[(label, bytes)]: the file itself, or the members of an `ar` archive (the symbol index and name table skipped)."""
    with open(path, 'rb') as fh:
        d = fh.read()
    if d[:8] != b'!<arch>\n':
        return [(path, d)]
    out, o = [], 8
    while o + 60 <= len(d):
        name, size = d[o:o + 16].rstrip(), int(d[o + 48:o + 58])
        if name not in (b'/', b'//', b'/SYM64/'):
            out.append(('%s(%d)' % (path, o), d[o + 60:o + 60 + size]))
        o += 60 + size + (size & 1)
    return out


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


def is_pie(path):
    """The linker's own mark of a position-independent executable: DT_FLAGS_1 has DF_1_PIE.  (e_type is not the
    witness: GNU ld writes ET_EXEC for a PIE whose first segment is linked at a non-zero address.)"""
    with open(path, 'rb') as fh:
        d = fh.read()
    phoff, = struct.unpack_from('<I', d, 28)
    phentsize, phnum = struct.unpack_from('<HH', d, 42)
    for i in range(phnum):
        typ, off, _, _, filesz = struct.unpack_from('<IIIII', d, phoff + i * phentsize)
        if typ == 2:
            for o in range(off, off + filesz, 8):
                tag, val = struct.unpack_from('<II', d, o)
                if tag == 0x6ffffffb:
                    return bool(val & 0x08000000)
    return False


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


def command(ld, script, out, rsp):
    return list(ld) + ['-pie', '--no-dynamic-linker', '-z', 'text', '-z', 'max-page-size=0x1000',
                       '-z', 'common-page-size=0x1000', '--unresolved-symbols=ignore-all', '--whole-archive', '-T', script, '-o', out, '@' + rsp]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ld', required=True, help='path of the AArch64 ILP32 linker (no PATH lookup)')
    ap.add_argument('--ld-arg', action='append', default=[], help='emulation and library options of the toolchain')
    ap.add_argument('--script', required=True)
    ap.add_argument('--in', dest='ins', action='append', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    elf, rsp, und = (os.path.join(a.out, n) for n in ('guest.elf', 'inputs.rsp', 'undefined.tsv'))
    try:
        ins = inputs(a.ins)
    except ValueError as e:
        sys.exit('ld: %s' % e)
    with open(rsp, 'w', newline='\n') as fh:
        fh.write(''.join('"%s"\n' % p for p in ins))
    r = subprocess.run(command([a.ld] + a.ld_arg, a.script, elf, rsp), capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-4000:])
        sys.exit('ld: the linker failed (exit %d)' % r.returncode)
    if not is_pie(elf):
        os.remove(elf)
        sys.exit('ld: the output is not position independent (no DF_1_PIE)')
    refs = {n for p in ins for n, df in symbols(p) if not df}
    names = {n for p in ins for n, _ in symbols(p)}
    bad = undefined(refs, symbols(elf))
    detail = '%d inputs, %d names, %d undefined' % (len(ins), len(names), len(bad))
    gatefile.write('ld', len(names), len(names) - len(bad), bad, detail)
    print(detail)
    if bad:
        with open(und, 'w', newline='\n') as fh:
            fh.write(''.join(n + '\n' for n in bad))
        os.remove(elf)
        sys.exit(1)


if __name__ == '__main__':
    main()
