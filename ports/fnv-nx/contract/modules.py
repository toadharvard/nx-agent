#!/usr/bin/env python3
"""modules - the lifted PE modules of the exe's static import closure (L1 CONTRACT, loader contract M1).

    modules.py --exe FalloutNV.exe --bridge bridge.tsv --dir DIR [--dir DIR ...] --out-dir OUT

Rules (each violated precondition is a stage error that lists everything it found; nothing gets a made-up value):

  closure   Start at the exe's import directory.  A dll name (norm_dll of iat.py: ASCII lowercase, ".dll" when it has no '.')
            is a SYSTEM dll iff bridge.tsv has rows for it (the platform implements it natively); it is then not followed.
            Any other dll must be a PE file named like it in one of the --dir directories (first directory in list order
            that has one; file names compare ASCII case-insensitively; two files of one directory that differ only in case
            are an error); its own import directory is followed the same way.  A dll that is found nowhere is an error
            (all such dlls are listed).  Delay-load directories do not add modules (the exe's own delay-load helper binds
            them at run time).  A module is an i386 PE32 DLL image, SectionAlignment 4 KiB (what loader/src/pe.c accepts).
  total     Base assignment and the table cover EVERY *.dll file of the --dir list that is not a system dll (a module loaded
            later through LoadLibrary must already have its static base: lifted code is keyed by image address), not
            only the static closure; a file that is not an i386 PE32 DLL (e.g. a 64-bit dll) is a stage error naming it;
            the imports of the static modules must be system dlls or found modules (an on-demand module may import a dll nobody provides:
            its slots are reported as without provider and its LoadLibrary fails).  `static` = in the exe's closure; the others
            are loadable on demand (loader: fnvnx_module_load).
  order     The Windows rule for static imports: depth first over the import directories in directory order, a module is
            initialised after all modules it imports from.  Assumption (documented Windows behaviour): in a dependency
            cycle the module visited first is initialised last, i.e. an edge back to a module still being visited is not
            followed.  The table order is this order for the static modules, followed by the on-demand ones sorted by name; it is the mapping order, the init order and the TLS-index order (the
            Windows loader numbers TLS indices in map order, which differs; the number is private to the loader and the
            lifted code reads it only through the TLS index word, so nothing depends on which module gets which number).
  bases     Static: modules sorted by lowercase name are placed after the end of the exe image (ImageBase + SizeOfImage
            rounded up to a page, as loader/src/pe.c rounds it), each aligned up to 64 KiB, in that order.  A module whose
            assigned base differs from its preferred base is relocated by the loader; it must then have a base relocation
            directory (and not IMAGE_FILE_RELOCS_STRIPPED) whose every entry is ABSOLUTE padding or HIGHLOW and lies inside
            the image, else the stage fails (the loader would refuse the start).  The whole range ends below 0xffff0000.
  exports   Every module's export directory is parsed by struct: the name table must be strictly sorted by byte order (the
            loader looks names up by binary search), name ordinals and function RVAs lie inside the image; an RVA inside the
            export directory is a forwarder "DLL.Name" | "DLL.#N".  exports.tsv rows: key (name, or #ordinal for an unnamed
            export), address at the assigned base (empty for a forwarder), forwarder (empty otherwise), ordinal; one row per
            (export, name) sorted by ordinal then name.  A named export is also reachable as #ordinal (iat.py).

Outputs in OUT: modules.tsv (sorted by name: name, sha256, size, preferred base, assigned base, SizeOfImage, has TLS, entry rva, static,
load index; hex for addresses), modules.c (`fnvnx_modules[]` + `fnvnx_module_count` for loader/src/modules.c), exports/<name>.tsv per module,
iat.tsv (the exe's import slots through iat.py with these modules), imports/<name>.tsv per module (that module's own
import slots through iat.py: slot, dll, name, kind, value; empty value + kind `none` = no provider).  Exit 1 when an exe
slot has no value (gate `iat`).  A module's own slots without provider are listed on stdout (the platform's work list):
the loader refuses the start for them.  Exe- and game-derived: container and fnv-nx-build only.
"""
import argparse
import hashlib
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gatefile
import iat

PAGE = 0x1000
ALIGN = 0x10000
TOP = 0xffff0000
RELOCS_STRIPPED = 0x0001
FILE_DLL = 0x2000
MAX_SECTIONS = 96


class StageError(ValueError):
    pass


def page_up(v):
    return (v + PAGE - 1) & ~(PAGE - 1)


def lower(s):
    return ''.join(chr(ord(c) + 32) if 'A' <= c <= 'Z' else c for c in s)


class PE:
    """The headers and the mapped image of one PE32 file, as loader/src/pe.c and image.c read them."""

    def __init__(self, data, what):
        self.what = what
        d = data
        if len(d) < 0x40 or d[:2] != b'MZ':
            raise StageError('%s: not a PE file (no MZ header)' % what)
        lf = struct.unpack_from('<I', d, 0x3c)[0]
        if lf < 0x40 or lf > 0x1000 or lf + 24 > len(d) or d[lf:lf + 4] != b'PE\0\0':
            raise StageError('%s: not a PE file (no PE signature)' % what)
        self.machine, nsec, _, _, _, optsize, self.characteristics = struct.unpack_from('<HHIIIHH', d, lf + 4)
        if self.machine != 0x14c:
            raise StageError('%s: not an i386 image' % what)
        if optsize < 96 or lf + 24 + optsize > len(d) or not 0 < nsec <= MAX_SECTIONS:
            raise StageError('%s: malformed headers' % what)
        o = lf + 24
        if struct.unpack_from('<H', d, o)[0] != 0x10b:
            raise StageError('%s: not PE32' % what)
        self.entry_rva = struct.unpack_from('<I', d, o + 16)[0]
        self.base = struct.unpack_from('<I', d, o + 28)[0]
        salign, falign = struct.unpack_from('<II', d, o + 32)
        self.size_of_image, self.size_of_headers = struct.unpack_from('<II', d, o + 56)
        ndirs = min(struct.unpack_from('<I', d, o + 92)[0], 16, (optsize - 96) // 8)
        if salign != PAGE:
            raise StageError('%s: SectionAlignment 0x%x is not 4 KiB' % (what, salign))
        if self.base & 0xffff or not 0 < self.size_of_image <= 0xfffff000:
            raise StageError('%s: image base or size not usable' % what)
        self.size_of_image = page_up(self.size_of_image)
        self.dirs = [struct.unpack_from('<II', d, o + 96 + 8 * i) for i in range(ndirs)] + [(0, 0)] * (16 - ndirs)
        for i, (rva, size) in enumerate(self.dirs):
            if size and i != 4 and (rva > self.size_of_image or size > self.size_of_image - rva):
                raise StageError('%s: data directory %d outside the image' % (what, i))
        shoff = o + optsize
        if shoff + 40 * nsec > len(d) or self.size_of_headers < shoff + 40 * nsec or self.size_of_headers > len(d):
            raise StageError('%s: malformed section table' % what)
        self.secs = []
        prev_end = 0
        for i in range(nsec):
            name, vs, rva, rs, ro, _, _, _, _, ch = struct.unpack_from('<8sIIIIIIHHI', d, shoff + 40 * i)
            vs = vs or rs
            if rva & 0xfff or rva < self.size_of_headers or not vs or rva + vs > self.size_of_image or rva < prev_end:
                raise StageError('%s: section %d outside the image or unsorted' % (what, i))
            if rs and ro + rs > len(d):
                raise StageError('%s: section %d raw data outside the file' % (what, i))
            prev_end = page_up(rva + vs)
            self.secs.append((rva, vs, ro, rs, ch))
        # the mapped image: headers, then min(raw, virtual) bytes of every section, zeros elsewhere
        self.image = bytearray(self.size_of_image)
        self.image[:self.size_of_headers] = d[:self.size_of_headers]
        self.mapped = [(0, page_up(self.size_of_headers))]
        for rva, vs, ro, rs, ch in self.secs:
            n = min(rs, vs)
            self.image[rva:rva + n] = d[ro:ro + n]
            self.mapped.append((rva, page_up(vs)))

    def rd(self, rva, n):
        if rva > self.size_of_image or n > self.size_of_image - rva:
            raise StageError('%s: rva 0x%x+%d outside the image' % (self.what, rva, n))
        return bytes(self.image[rva:rva + n])

    def u32(self, rva):
        return struct.unpack('<I', self.rd(rva, 4))[0]

    def cstr(self, rva, limit=512):
        end = self.image.find(b'\0', rva, min(self.size_of_image, rva + limit))
        if rva >= self.size_of_image or end < 0:
            raise StageError('%s: unterminated string at rva 0x%x' % (self.what, rva))
        return self.image[rva:end].decode('latin-1')

    def inside_mapping(self, rva, n):
        return any(lo <= rva and rva + n <= lo + size for lo, size in self.mapped)

    def import_directory(self):
        """[(dll name, [(slot rva, "name" | "#ordinal")])] in directory order (loader/src/imports.c walk_imports)."""
        rva, size = self.dirs[1]
        out = []
        for d in range(0, size - size % 20, 20):
            oft, _, _, name, ft = struct.unpack('<IIIII', self.rd(rva + d, 20))
            if not (oft or name or ft):
                break
            if not ft:
                raise StageError('%s: import descriptor without a first thunk' % self.what)
            items, lookup = [], oft or ft
            for j in range(1 << 20):
                t = self.u32(lookup + 4 * j)
                self.rd(ft + 4 * j, 4)
                if not t:
                    break
                items.append((ft + 4 * j, '#%d' % (t & 0xffff) if t & 0x80000000 else self.cstr(t + 2)))
            else:
                raise StageError('%s: import thunk list without a terminator' % self.what)
            out.append((self.cstr(name, 256), items))
        return out

    def delay_dll_names(self):
        """[dll name] of the delay-load directory (data directory 13, IMAGE_DELAYLOAD_DESCRIPTOR, 32 bytes) in directory order; the name
        field is an RVA when attribute bit 0 is set, else a virtual address."""
        rva, size = self.dirs[13]
        out = []
        for d in range(0, size - size % 32, 32):
            attrs, name = struct.unpack('<II', self.rd(rva + d, 8))
            if not attrs and not name:
                break
            out.append(self.cstr(name if attrs & 1 else name - self.base, 256))
        return out

    def exports(self):
        """[(key, rva | None, forwarder | None, ordinal)]: one row per (export, name), unnamed exports keyed #ordinal."""
        rva, size = self.dirs[0]
        if not size:
            return []
        if size < 40:
            raise StageError('%s: export directory too small' % self.what)
        base, nfunc, nname, aof, aon, aono = struct.unpack('<6I', self.rd(rva + 0x10, 24))
        fns = [self.u32(aof + 4 * i) for i in range(nfunc)]
        names = [self.cstr(self.u32(aon + 4 * i)) for i in range(nname)]
        ords = [struct.unpack('<H', self.rd(aono + 2 * i, 2))[0] for i in range(nname)]
        enc = [n.encode('latin-1') for n in names]
        if any(a >= b for a, b in zip(enc, enc[1:])):
            raise StageError('%s: export name table is not strictly sorted' % self.what)
        if any(o >= nfunc for o in ords):
            raise StageError('%s: a name ordinal is outside the function table' % self.what)
        named = {}
        for n, o in zip(names, ords):
            named.setdefault(o, []).append(n)
        out = []
        for i, f in enumerate(fns):
            if not f:
                continue
            fwd = self.cstr(f, rva + size - f) if rva <= f < rva + size else None
            if fwd is None and not self.inside_mapping(f, 1):
                raise StageError('%s: export %d points outside the image' % (self.what, base + i))
            for key in named.get(i, ['#%d' % (base + i)]):
                out.append((key, None if fwd is not None else f, fwd, base + i))
        return sorted(out, key=lambda r: (r[3], r[0]))

    def check_relocations(self):
        """Number of HIGHLOW entries; the loader's preconditions (image.c load_relocs) as stage errors."""
        rva, size = self.dirs[5]
        if self.characteristics & RELOCS_STRIPPED or not size:
            raise StageError('%s: needs relocating but has no base relocations' % self.what)
        blob, pos, n = self.rd(rva, size), 0, 0
        while pos + 8 <= size:
            page, bsize = struct.unpack_from('<II', blob, pos)
            if bsize < 8 or bsize > size - pos or bsize & 1:
                raise StageError('%s: malformed relocation block' % self.what)
            for k in range(8, bsize, 2):
                e = struct.unpack_from('<H', blob, pos + k)[0]
                if e >> 12 == 0:
                    continue
                if e >> 12 != 3:
                    raise StageError('%s: relocation type %d (only ABSOLUTE and HIGHLOW exist in an i386 image)' % (self.what, e >> 12))
                if not self.inside_mapping(page + (e & 0xfff), 4):
                    raise StageError('%s: relocation outside the mapped sections' % self.what)
                n += 1
            pos += bsize
        return n


class Module:
    def __init__(self, name, path, data):
        self.name, self.path = name, path
        self.sha256 = hashlib.sha256(data).hexdigest()
        self.size = len(data)
        self.pe = PE(data, name)
        if not self.pe.characteristics & FILE_DLL:
            raise StageError('%s: not a DLL' % name)
        self.imports = self.pe.import_directory()
        self.base = None
        self.static = False
        self.relocs = 0


def find_file(dirs, name):
    """The path of the first file in the first --dir that has it (case-insensitive name), or None."""
    for d in dirs:
        hits = [f for f in sorted(os.listdir(d)) if lower(f) == name and os.path.isfile(os.path.join(d, f))]
        if len(hits) > 1:
            raise StageError('%s: %s holds %s, which differ only in case' % (name, d, ', '.join(hits)))
        if hits:
            return os.path.join(d, hits[0])
    return None


def scan(dirs, system):
    """Every *.dll file of the --dir list that is not a system dll: {lowercase name: Module}.  The first directory in list
    order that has a name wins; a file that is not an i386 PE32 DLL image is an error naming it (all are listed)."""
    mods, errors = {}, []
    for d in dirs:
        files = sorted(os.listdir(d))
        for f in files:
            name = lower(f)
            if not name.endswith('.dll') or not os.path.isfile(os.path.join(d, f)) or name in system or name in mods:
                continue
            if sum(1 for g in files if lower(g) == name) > 1:
                errors.append('%s: %s holds files that differ only in case' % (name, d))
                continue
            with open(os.path.join(d, f), 'rb') as fh:
                data = fh.read()
            try:
                mods[name] = Module(name, os.path.join(d, f), data)
            except StageError as e:
                errors.append(str(e))
    if errors:
        raise StageError('; '.join(errors))
    return mods


def closure(exe, system, mods):
    """-> (static modules in init order, dependencies first; the rest sorted by name).  Every import of a STATIC module that
    is not a system dll must be a module of `mods` (all missing ones are listed); an on-demand module whose dll is
    missing simply has slots without provider (its LoadLibrary fails; listed in the import report)."""
    order, seen, missing = [], set(), set()

    def visit(dll_names):
        for raw in dll_names:
            name = iat.norm_dll(raw)
            if name in system or name in seen:   # a module still being visited: the cycle edge is not followed
                continue
            if name not in mods:
                missing.add(name)
                continue
            seen.add(name)
            visit([d for d, _ in mods[name].imports])
            order.append(mods[name])

    visit([d for d, _ in exe.import_directory()])
    if missing:
        raise StageError('module not found in any --dir: ' + ', '.join(sorted(missing)))
    for m in mods.values():
        m.static = m.name in seen
    return order + sorted((m for m in mods.values() if not m.static), key=lambda m: m.name)


def assign_bases(exe, mods):
    cur = (exe.base + exe.size_of_image + ALIGN - 1) & ~(ALIGN - 1)
    for m in sorted(mods, key=lambda m: m.name):
        m.base = cur
        cur = (cur + m.pe.size_of_image + ALIGN - 1) & ~(ALIGN - 1)
        if cur > TOP:
            raise StageError('the modules do not fit below 0x%x' % TOP)
        if m.base != m.pe.base:
            m.relocs = m.pe.check_relocations()
    return cur


def c_source(order):
    t = ['/* Generated by contract/modules.py: do not edit.  Game-derived hashes: not for the public tree. */\n',
         '#include "fnvnx_modules.h"\n\n', 'const fnvnx_module_desc fnvnx_modules[] = {\n']
    for m in order:
        t.append('    {"%s", {%s}, %du, 0x%08xu, 0x%08xu, %du},\n' % (m.name, ', '.join('0x%02x' % b for b in bytes.fromhex(m.sha256)), m.size, m.base, m.pe.size_of_image, int(m.static)))
    if not order:   # C has no zero-size array; the count says there is no entry
        t.append('    {"", {0}, 0u, 0u, 0u, 0u}\n')
    t += ['};\n', 'const uint32_t fnvnx_module_count = %du;\n' % len(order)]
    return ''.join(t)


def tsv(path, text):
    with open(path, 'w', newline='\n') as fh:
        fh.write(text)


def exe_slots(exe):
    return {exe.base + slot: (dll, name) for dll, items in exe.import_directory() for slot, name in items}


def run(exe_path, bridge_path, dirs, out):
    with open(exe_path, 'rb') as fh:
        exe = PE(fh.read(), os.path.basename(exe_path))
    brows, _ = iat.bridge_rows(bridge_path)
    bridge = [(r['dll'], r['name'], r['conv'], r['ordinal']) for r in brows]
    system = {r['dll'] for r in brows}
    order = closure(exe, system, scan(dirs, system))
    end = assign_bases(exe, order)
    os.makedirs(os.path.join(out, 'exports'), exist_ok=True)
    os.makedirs(os.path.join(out, 'imports'), exist_ok=True)
    load_index = {m.name: i for i, m in enumerate(m for m in order if m.static)}
    tsv(os.path.join(out, 'modules.tsv'), '# name\tsha256\tsize\tpreferred_base\tassigned_base\tsize_of_image\thas_tls\tentry_rva\tstatic\tload_index\n' +
        ''.join('%s\t%s\t%d\t%08x\t%08x\t%08x\t%d\t%08x\t%d\t%s\n' % (
            m.name, m.sha256, m.size, m.pe.base, m.base, m.pe.size_of_image, 1 if m.pe.dirs[9][1] else 0, m.pe.entry_rva,
            int(m.static), load_index.get(m.name, '')) for m in sorted(order, key=lambda m: m.name)))
    tsv(os.path.join(out, 'modules.c'), c_source(order))
    raw = {}
    for m in order:
        rows = m.pe.exports()
        raw[m.name] = [(k, None if r is None else m.base + r, f, o) for k, r, f, o in rows]
        tsv(os.path.join(out, 'exports', m.name + '.tsv'), ''.join(
            '%s\t%s\t%s\t%d\n' % (k, '' if a is None else '%08x' % a, f or '', o) for k, a, f, o in raw[m.name]))
    values = iat.resolve_exports(raw, bridge)
    rows, bad = iat.table(exe_slots(exe), bridge, values)
    iat.write_table(os.path.join(out, 'iat.tsv'), rows)
    gatefile.write('iat', len(rows) + len(bad), len(rows), ['%08x %s!%s: %s' % b for b in bad],
                   '%d slots, %d without a value' % (len(rows) + len(bad), len(bad)))
    summary = ['modules %d: range 0x%08x..0x%08x' % (len(order), order and min(m.base for m in order) or 0, end)]
    summary += ['  %d %s base 0x%08x preferred 0x%08x size 0x%x relocs %d static %d' % (i, m.name, m.base, m.pe.base, m.pe.size_of_image, m.relocs, int(m.static))
                for i, m in enumerate(order)]
    summary.append('exe slots with a value %d of %d' % (len(rows), len(rows) + len(bad)))
    for b in bad:
        summary.append('exe slot %08x %s!%s: %s' % b)
    mkinds = {}
    for m in order:
        slots = {m.base + slot: (dll, name) for dll, items in m.imports for slot, name in items}
        mrows, mbad = iat.table(slots, bridge, values)
        iat.write_table(os.path.join(out, 'imports', m.name + '.tsv'), sorted(
            mrows + [(s, d, n, 'none', None) for s, d, n, _ in mbad]))
        k = mkinds.setdefault(m.name, {'system': 0, 'module': 0, 'none': 0})
        for r in mrows:
            k['module' if r[3] == 'module' else 'system'] += 1
        k['none'] = len(mbad)
        summary += ['module import slots %s: via bridge %d, via modules %d, no provider %d' % (m.name, k['system'], k['module'], k['none'])]
        summary += ['  no provider: %s!%s' % (d, n) for _, d, n, _ in mbad]
    return '\n'.join(summary), bool(bad)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', required=True)
    ap.add_argument('--bridge', required=True)
    ap.add_argument('--dir', action='append', required=True, help='where module files are looked for; first match in list order')
    ap.add_argument('--out-dir', required=True)
    a = ap.parse_args()
    try:
        text, bad = run(a.exe, a.bridge, a.dir, a.out_dir)
    except (StageError, ValueError, OSError) as e:
        sys.exit('modules: %s' % e)
    print(text)
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
