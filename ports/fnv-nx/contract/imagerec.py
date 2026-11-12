"""imagerec - the image record of the contract layer (L1 CONTRACT, the lowest layer: it imports nothing of platform/ or lift/).

    rec = imagerec.load(image_path, facts_dir, pin)    THE constructor of a record of a real image, and the only code that opens an image file
                                                       or a ranges file: it checks the tree_sha256 of the facts directory against `pin`
                                                       ({"tree_sha256": ...}) and the sha256 of the image against the facts' manifest.json,
                                                       reads the image's ranges and rebases them to the ImageBase. gate_reach, gpa_names and
                                                       manifest/funcfacts.py all call it.
    rec = ImageRec(data, name, runs)                   the record of given bytes (the self-tests' synthetic PEs); no file, no pin
    imagerec.disasm(code, va, count=0)                 THE decoder of x86-32 instructions (capstone, detail on); rec.decode_at/decode_range
                                                       and the other readers of contract/ and platform/ go through it. No module outside
                                                       this file imports capstone for x86.

Code is reachable only through the decoder. A byte reader (read, word) refuses an address inside a run of the function facts (CodeRead):
a read of an instruction's bytes is the decoded instruction's field (`word` of a relocated field of an instruction answers with the decoded
immediate or displacement), never the raw image. A malformed image is an ImageError and no record is built.
"""
import bisect
import hashlib
import json
import os
import struct
import sys

import capstone
from capstone import x86_const as X  # noqa: F401  (the operand and register constants of the decoded instructions)

import modules

M32 = 0xffffffff
HERE = os.path.dirname(os.path.abspath(__file__))
FORMAT = 'fnvnx-liftfacts-manifest/1'

_CS = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
_CS.detail = True


class ImageError(Exception):
    """A malformed image, a facts directory that is not the pinned tree, or an image that is not the one the facts are of."""


class CodeRead(BaseException):
    """A byte read of an address inside a run of the function facts: code is read through the decoder only. A BaseException on purpose: `except Exception` does not catch it, so a pass that reads code bytes stops red (L1 rule)."""


def disasm(code, va, count=0):
    """The instructions of `code` decoded at address `va` (all of them, or `count`), in order; stops at the first byte that does not decode."""
    return _CS.disasm(code, va, count)


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def _runs_of(text, name='ranges'):
    """ranges.tsv text -> ({entry: [(start, end)]}, base from the `# base=` header or None, {entries marked `candidate`}); absolute VAs of the
    file's base. Row: `entry<TAB>start-end,start-end,...[<TAB>candidate]`."""
    runs, base, cand = {}, None, set()
    for ln in text.splitlines():
        if ln.startswith('# base='):
            base = int(ln.split()[1][5:].rstrip(':'), 16)
            continue
        if not ln or ln[0] == '#':
            continue
        f = ln.split('\t')
        try:
            e = int(f[0], 16)
            runs[e] = [tuple(int(x, 16) for x in p.split('-')) for p in (f[1].split(',') if len(f) > 1 else []) if p]
        except ValueError:
            raise ImageError('%s: malformed line %r' % (name, ln[:60]))
        if len(f) > 2:
            if f[2] != 'candidate' or len(f) > 3:
                raise ImageError('%s: malformed line %r (third column is `candidate` or absent)' % (name, ln[:60]))
            cand.add(e)
    return runs, base, cand


def _data_of(text, name='data_in_code'):
    """data_in_code.tsv text -> ([(start, end)] half-open absolute VAs of the file's base, base or None). Row: `start<TAB>end<TAB>kind<TAB>derivation`."""
    out, base = [], None
    for ln in text.splitlines():
        if ln.startswith('# base='):
            base = int(ln.split()[1][5:].rstrip(':'), 16)
            continue
        if not ln or ln[0] == '#':
            continue
        f = ln.split('\t')
        try:
            a, b = int(f[0], 16), int(f[1], 16)
        except (ValueError, IndexError):
            raise ImageError('%s: malformed line %r' % (name, ln[:60]))
        if not a < b:
            raise ImageError('%s: empty or reversed range %x-%x' % (name, a, b))
        out.append((a, b))
    return out, base


class ImageRec:
    """One PE32 image as typed data: name, base, size_of_image, secs and dirs of the PE headers, relocs (set of the absolute VAs of the HIGHLOW
    relocations), imports {slot va: (dll, name)}, runs {entry: [(start, end)]}, sha (of the image bytes) and facts (the reason an image has no
    function facts, or None). The bytes are private; they are read through read/word/decode_at/decode_range."""

    def __init__(self, data, name, runs, facts=None, data_ranges=(), candidates=()):
        self._file = bytes(data)
        self.sha = sha256_bytes(self._file)
        self.name = name
        self.facts = facts
        self._pe = pe = modules.PE(self._file, name)
        self.base = pe.base
        self.size_of_image = pe.size_of_image
        self.secs = pe.secs
        self.dirs = pe.dirs
        self.ranges_sha = None
        self._ins = {}
        self._set_runs(runs, data_ranges, candidates)
        self.relocs = set()
        rva, size = pe.dirs[5]
        blob = pe.rd(rva, size) if size else b''
        pos = 0
        while pos < len(blob):
            if pos + 8 > len(blob):
                raise ImageError('%s: base relocation directory ends inside a block header' % name)
            page, bsize = struct.unpack_from('<II', blob, pos)
            if bsize < 8 or bsize % 2 or pos + bsize > len(blob):
                raise ImageError('%s: base relocation block at +0x%x has size %d (directory size %d)' % (name, pos, bsize, len(blob)))
            for k in range(8, bsize, 2):
                e = struct.unpack_from('<H', blob, pos + k)[0]
                t = e >> 12
                if t == 3:
                    self.relocs.add(self.base + page + (e & 0xfff))
                elif t != 0:
                    raise ImageError('%s: base relocation of type %d at 0x%x (PE32 has HIGHLOW and padding only)' % (name, t, self.base + page + (e & 0xfff)))
            pos += bsize
        self.imports = {}
        for dll, items in pe.import_directory():
            for slot, nm in items:
                self.imports[self.base + slot] = (dll, nm)

    def _set_runs(self, runs, data_ranges=(), candidates=()):
        """runs: code. data_ranges: data in code (half-open, jump tables). A data range that overlaps a run of a candidate entry wins over that
        run; one that overlaps a run of any other entry is a contradictory fact (ImageError)."""
        self.runs = {e: [tuple(r) for r in rs] for e, rs in runs.items()}
        self.candidates = set(candidates)
        self.data = sorted((a, b) for a, b in data_ranges)
        self._dstarts = [a for a, _ in self.data]
        for da, db in self.data:
            for e, rs in self.runs.items():
                if e not in self.candidates and any(a < db and da < b for a, b in rs):
                    raise ImageError('%s: data range %x-%x overlaps a run of entry %x, which is not a candidate' % (self.name, da, db, e))
        self.flat = sorted((a, b) for rs in self.runs.values() for a, b in rs)
        self._merged = []                   # the union of the runs: disjoint, ascending
        for a, b in self.flat:
            if self._merged and a <= self._merged[-1][1]:
                self._merged[-1][1] = max(self._merged[-1][1], b)
            else:
                self._merged.append([a, b])
        self._starts = [a for a, _ in self._merged]
        self._ins.clear()

    # ---- the bytes: never inside a run
    def in_run(self, va, n=1):
        """True iff [va, va+n) overlaps a run of the function facts."""
        k = bisect.bisect_left(self._starts, va + n) - 1       # the last merged run that starts before va+n
        return k >= 0 and self._merged[k][1] > va

    def in_data(self, va, n=1):
        """True iff [va, va+n) lies inside one declared data range (data in code)."""
        k = bisect.bisect_right(self._dstarts, va) - 1
        return k >= 0 and va + n <= self.data[k][1] and self.data[k][0] <= va

    def read(self, va, n):
        if not self.in_data(va, n) and self.in_run(va, n):
            raise CodeRead('%s: bytes at 0x%x+%d are inside a run of the function facts (code is read through the decoder)' % (self.name, va, n))
        return self._pe.rd(va - self.base, n)

    def word(self, va):
        """The little-endian 32-bit word at va. Inside a run only the relocated field of a decoded instruction (its immediate or
        displacement) is answered; any other read of code raises."""
        if self.in_data(va, 4) or not self.in_run(va, 4):
            return struct.unpack('<I', self._pe.rd(va - self.base, 4))[0]
        if va not in self.relocs:
            raise CodeRead('%s: word at 0x%x is inside a run of the function facts and is not a relocated field' % (self.name, va))
        return self._field(va)

    def _field(self, va):
        k = bisect.bisect_right(self._starts, va) - 1
        a, b = self._merged[k]
        if a not in self._ins:           # the 4-byte immediate and displacement fields of the decoded instructions of the merged run, {va: value}
            f = {}
            for i in disasm(self._pe.rd(a - self.base, b - a), a):
                for op in i.operands:
                    if op.type == X.X86_OP_MEM and i.disp_size == 4:
                        f[i.address + i.disp_offset] = op.mem.disp & M32
                    elif op.type == X.X86_OP_IMM and i.imm_size == 4:
                        f[i.address + i.imm_offset] = op.imm & M32
            self._ins[a] = f
        if va in self._ins[a]:
            return self._ins[a][va]
        raise CodeRead('%s: no decoded immediate or displacement at 0x%x' % (self.name, va))

    def exports(self):
        """[(key, rva | None, forwarder | None, ordinal)] of the export directory (modules.PE.exports); the export tables are data."""
        return self._pe.exports()

    def delay_dlls(self):
        """The dll names of the delay-load directory, in directory order."""
        return self._pe.delay_dll_names()

    def import_dlls(self):
        """The dll names of the import directory, in directory order, without repeats."""
        return list(dict.fromkeys(d.lower() for d, _ in self._pe.import_directory()))

    def decode_at(self, va, count=1):
        """The first `count` instructions decoded at va."""
        return disasm(self._pe.rd(va - self.base, 16 * count), va, count)

    def decode_range(self, a, b):
        """All instructions of [a, b)."""
        return disasm(self._pe.rd(a - self.base, b - a), a)


def _tree_hash(path):
    sys.path.insert(0, os.path.join(HERE, '..', 'verify'))
    import cas
    return cas.tree_hash(path)


_trees = {}


def facts_manifest(facts_dir, pin):
    """The manifest {image name lower: entry} of the facts directory; the directory is the pinned tree (pin['tree_sha256'])."""
    key = (os.path.realpath(facts_dir), pin['tree_sha256'])
    if key not in _trees:
        h = _tree_hash(facts_dir)
        if h != pin['tree_sha256']:
            raise ImageError('facts %s is tree %s, the pin is %s' % (facts_dir, h, pin['tree_sha256']))
        with open(os.path.join(facts_dir, 'manifest.json')) as fh:
            m = json.load(fh)
        if m.get('format') != FORMAT:
            raise ImageError('%s/manifest.json: format %r, expected %s' % (facts_dir, m.get('format'), FORMAT))
        _trees[key] = {i['image'].lower(): i for i in m['images']}
    return _trees[key]


def load(image_path, facts_dir, pin):
    """The record of the image at image_path with the function facts of the pinned facts directory. An image the manifest does not list is a
    record with no runs and facts = 'no function facts'; an image whose sha256 differs from the manifest's is an ImageError."""
    man = facts_manifest(facts_dir, pin)
    with open(image_path, 'rb') as fh:
        data = fh.read()
    name = os.path.basename(image_path).lower()
    ent = man.get(name)
    if ent is None:
        return ImageRec(data, name, {}, 'no function facts')
    if sha256_bytes(data) != ent['sha256']:
        raise ImageError('%s has sha256 %s, the facts are of %s' % (image_path, sha256_bytes(data), ent['sha256']))
    rp = os.path.realpath(os.path.join(facts_dir, ent['ranges']))
    if not rp.startswith(os.path.realpath(facts_dir) + os.sep):
        raise ImageError('%s: ranges path leaves the facts directory' % ent['ranges'])
    with open(rp) as fh:
        runs, fb, cand = _runs_of(fh.read(), ent['ranges'])
    if fb is None:
        raise ImageError('%s has no `# base=` header' % ent['ranges'])
    dr = []
    if 'data_in_code' in ent:     # data in code (jump tables) of the facts, in the same tree, rebased the same way
        dp = os.path.realpath(os.path.join(facts_dir, ent['data_in_code']))
        if not dp.startswith(os.path.realpath(facts_dir) + os.sep):
            raise ImageError('%s: data path leaves the facts directory' % ent['data_in_code'])
        with open(dp) as fh:
            dr, db = _data_of(fh.read(), ent['data_in_code'])
        if db != fb:
            raise ImageError('%s: `# base=` %s differs from the ranges base %s' % (ent['data_in_code'], db, fb))
    rec = ImageRec(data, name, {}, None)
    d = rec.base - fb
    rec._set_runs({e + d: [(a + d, b + d) for a, b in rs] for e, rs in runs.items()}, [(a + d, b + d) for a, b in dr], {e + d for e in cand})
    rec.ranges_sha = sha256_bytes(open(rp, 'rb').read())
    return rec


def load_images(specs, facts_dir, tree_sha256):
    """{name lower: record} of the `NAME=image path` specs of a stage, all with the facts directory pinned by tree_sha256 (the one constructor)."""
    out = {}
    for spec in specs:
        nm, path = spec.split('=', 1)
        rec = load(path, facts_dir, {'tree_sha256': tree_sha256})
        if rec.facts is not None:
            raise ImageError('%s: %s' % (nm, rec.facts))
        out[nm.lower()] = rec
    return out
