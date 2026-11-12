#!/usr/bin/env python3
"""funcfacts.py - the one reader of the function facts for the platform passes (function boundaries: no assumption).

The function boundaries of every pass (pemanifest, regsites, regclass, filesites, fontfaces, prefs_settings) come from here and only
from here. The source is L3a's lift facts (private, exe- and DLL-derived; fnv-nx-build, branch l3a-lift/facts), pinned in facts.json
beside this file: repo, branch, commit and tree_sha256 (verify/cas.py tree_hash of the checked-out facts directory, without .git).
Re-pinning to a refined facts commit is a change of those four fields and nothing else.

Facts used: manifest.json (per image: name, sha256, ranges file, base) and each image's ranges.tsv
    # base=0x...                         absolute VAs at that base (the exe: the preferred base; a module: its assigned load base)
    <entry>\\t<a-b>,<a-b>,...            the half-open runs of instruction bytes reached from the entry by proven flow
No other fact is read (universe.tsv's extent column is not).

Image access: this is the only module that imports capstone, pefile or dnfile, opens an image file, or touches the bytes of an image.
`Image` (read_image) is the typed record of one PE image: its base, sections, relocations, import and export tables and the accessors
u32/u8/bytes_at/zstr/cstr over the mapped bytes. The other passes hold an Image (pemanifest.Mod is one) and never the bytes or the parser
object. check_funcfacts.py keeps this by the import graph, whatever the names are.

Rules (each one closes the whole class):
  image     the image file's sha256 must equal the manifest's: facts of another file are refused (SystemExit).
  rebase    the ranges are moved from the facts' base to the PE ImageBase the passes work at (delta = ImageBase - facts base).
  entries   the function of an address is every entry whose runs contain it (`entries_of`): an address in the shared tail of several
            entries, or inside an inner entry and its parent, is analysed in each and the results are joined by the caller
            (`join_rows`); an address in no run has no function and is `unresolved`, never given to the nearest entry.
  no facts  an image without an entry of manifest.json answers `unresolved: no function facts`.
  decode    the runs of an entry are decoded run by run from the run start; the instructions must tile the run exactly (the last one
            ends at the run end), else the entry is `unresolved` (ins() is None, `bad` names the run) and is counted in the census.
  flow      `heads(entry)` = the addresses at which a pass makes its state unknown (top, whatever state a forward jump saved): a target
            of a direct jump or branch that does not lie after the jump (a loop head), the first instruction of every run after the
            first, and every other entry of the facts inside the entry's runs. The last set contains every target of an indirect jump:
            the facts' rule R3 makes each relocated pointer into executable bytes an entry, and a jump-table slot is such a pointer
            (ASM-012: the only way code obtains a code address is a relocated word). `ijumps(entry)` lists the indirect jumps.
"""
import bisect
import collections
import hashlib
import json
import os
import struct
import sys

import capstone
import pefile
from capstone import x86_const as X   # the operand/register constants, for the passes that read decoded instructions

HERE = os.path.dirname(os.path.abspath(__file__))
PIN = os.path.join(HERE, "facts.json")
_dir = None
_manifest = None
_images = {}


def _pin():
    p = json.load(open(PIN))
    if p.get("format") != "fnvnx-funcfacts-pin/1":
        raise SystemExit("funcfacts: %s is not a facts pin" % PIN)
    return p["external"]


def _tree_hash(path):
    sys.path.insert(0, os.path.join(HERE, "..", "..", "verify"))
    import cas
    return cas.tree_hash(path)


def set_facts_dir(d):
    """Select the facts directory; it must be the pinned tree (tree_sha256), checked out without .git."""
    global _dir, _manifest
    ext = _pin()
    h = _tree_hash(d)
    if h != ext["tree_sha256"]:
        raise SystemExit("funcfacts: %s is tree %s, the pin %s (commit %s) is %s" % (d, h, PIN, ext["commit"], ext["tree_sha256"]))
    _dir = d
    _manifest = {i["image"].lower(): i for i in json.load(open(os.path.join(d, "manifest.json")))["images"]}
    _images.clear()


def facts_dir_from_argv(argv):
    """Pop `--facts DIR` from argv (the passes take it anywhere in their arguments) and select it."""
    if "--facts" in argv:
        k = argv.index("--facts")
        d = argv[k + 1]
        del argv[k:k + 2]
        set_facts_dir(d)


def sha256_file(path):
    """sha256 of a file's bytes (the pins of images and of contract files are checked here, the only reader of binary files)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for ch in iter(lambda: f.read(1 << 20), b""):
            h.update(ch)
    return h.hexdigest()


def open_dotnet(path):
    """The ECMA-335 metadata of a .NET image (dnfile), for the passes that decode interface metadata; not code, not an x86 image."""
    import dnfile
    return dnfile.dnPE(path)


class Image:
    """One PE image as typed data (read_image): base, img (the mapped bytes, private to the accessors), relocs (absolute VAs of the
    HIGHLOW relocations, sorted), imports [(dll, name, slot)], normal/delay import dll names in directory order, exports (count) and
    export_names {va: name}, code [(va, size)] of the executable sections, sections [(name, va, size, characteristics)]."""

    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path).lower()
        with open(path, "rb") as fh:
            self._file = fh.read()
        pe = pefile.PE(data=self._file, fast_load=True)
        pe.parse_data_directories(directories=[0, 1, 5, 13])
        self.base = pe.OPTIONAL_HEADER.ImageBase
        self._img = pe.get_memory_mapped_image()
        self.relocs = sorted(self.base + e.rva for blk in getattr(pe, "DIRECTORY_ENTRY_BASERELOC", []) for e in blk.entries if e.type == 3)
        self.imports = []
        self.normal, self.delay = [], []
        for kind, names in (("DIRECTORY_ENTRY_IMPORT", self.normal), ("DIRECTORY_ENTRY_DELAY_IMPORT", self.delay)):
            for e in getattr(pe, kind, []):
                if e.dll.decode().lower() not in names:
                    names.append(e.dll.decode().lower())
                for i in e.imports:
                    nm = i.name.decode() if i.name else "#%d" % i.ordinal
                    self.imports.append((e.dll.decode().lower(), nm, i.address))
        ex = getattr(pe, "DIRECTORY_ENTRY_EXPORT", None)
        self.exports = len(ex.symbols) if ex else 0
        self.export_names = {self.base + s.address: (s.name or b"#%d" % s.ordinal).decode() for s in ex.symbols} if ex else {}
        self.sections = [(s.Name.rstrip(b"\0").decode("latin1"), self.base + s.VirtualAddress, s.Misc_VirtualSize, s.Characteristics)
                         for s in pe.sections]
        self.code = [(self.base + s.VirtualAddress, s.Misc_VirtualSize) for s in pe.sections if s.Characteristics & 0x20000000]

    @classmethod
    def synthetic(cls, name, base, img, relocs, imports, code):
        """An image built from given bytes (the self-tests' synthetic PEs): the same record, no file."""
        self = cls.__new__(cls)
        self.path, self.name, self.base, self._img = None, name, base, bytes(img)
        self.relocs, self.imports, self.code = sorted(relocs), list(imports), list(code)
        self.normal, self.delay, self.exports, self.export_names, self.sections = [], [], 0, {}, []
        return self

    def size(self):
        return len(self._img)

    def file_bytes(self):
        """The bytes of the image file (the input of the L1 contract's argflow.Image, whose pin is the image sha256 checked by for_image)."""
        return self._file

    def u8(self, va):
        o = va - self.base
        return self._img[o] if 0 <= o < len(self._img) else None

    def u32(self, va):
        o = va - self.base
        return struct.unpack_from("<I", self._img, o)[0] if 0 <= o <= len(self._img) - 4 else None

    def bytes_at(self, va, n):
        """n bytes at va, or the bytes up to the end of the image (shorter than n) when va+n passes it; None when va is outside."""
        o = va - self.base
        return self._img[o:o + n] if 0 <= o < len(self._img) else None

    def zstr(self, va, limit):
        """The bytes from va up to the first NUL within `limit` bytes (None if va is outside or no NUL within limit)."""
        o = va - self.base
        if not (0 <= o < len(self._img)):
            return None
        e = self._img.find(b"\0", o, o + limit)
        return self._img[o:e] if e >= 0 else None

    def find_bytes(self, pat):
        """The VA of the first occurrence of `pat` in the mapped image (None if absent): a data lookup (a string), not a code scan."""
        o = self._img.find(pat)
        return None if o < 0 else self.base + o

    def in_image(self, va):
        return 0 <= va - self.base < len(self._img)

    def in_code(self, va):
        return any(a <= va < a + n for a, n in self.code)

    def in_data(self, va):
        return self.in_image(va) and not self.in_code(va)

    def cstr(self, va):
        """A printable ASCII or UTF-16LE string at va (`L:` prefix for the latter), else None."""
        if not self.in_image(va):
            return None
        s = self.zstr(va, 260) or b""
        if len(s) >= 2 and all(32 <= c < 127 for c in s):
            return s.decode()
        w = self.bytes_at(va, 520)
        e = next((i for i in range(0, len(w) - 1, 2) if w[i:i + 2] == b"\0\0"), 0)
        try:
            t = w[:e].decode("utf-16le")
        except UnicodeDecodeError:
            return None
        return "L:" + t if len(t) >= 2 and all(32 <= ord(c) < 127 for c in t) else None


def read_image(path):
    return Image(path)


class NoFacts:
    """An image the facts do not cover: every address is unresolved with the reason."""
    ok = False

    def __init__(self, reason):
        self.reason = reason

    def entries_of(self, addr):
        return []

    def why(self, addr):
        return self.reason

    def entry_list(self):
        return []


class FuncFacts:
    ok = True

    def __init__(self, name, image, runs):
        """`image` = the Image the runs are of; `runs` = {entry: [(a, b), ...]} at its ImageBase (file_runs() reads and rebases a ranges.tsv)."""
        self.name, self.image, self.base = name, image, image.base
        self.runs = runs
        iv = sorted((a, b, e) for e, rs in self.runs.items() for a, b in rs)
        self._s = [x[0] for x in iv]
        self._e = [x[1] for x in iv]
        self._o = [x[2] for x in iv]
        m, self._pm = 0, []
        for _, b, _ in iv:
            m = max(m, b)
            self._pm.append(m)
        self._ins = collections.OrderedDict()   # a small LRU: all 100k entries decoded would not fit in memory
        self._md = None
        self._el = None
        self.bad = {}   # entry -> reason, for entries whose runs do not decode to exactly their bytes (reported, never analysed)

    def entry_list(self):
        if self._el is None:
            self._el = sorted(self.runs)
        return self._el

    def entries_of(self, addr):
        """Every entry with a run containing addr, ascending."""
        out = set()
        j = bisect.bisect_right(self._s, addr) - 1
        while j >= 0 and self._pm[j] > addr:
            if self._s[j] <= addr < self._e[j]:
                out.add(self._o[j])
            j -= 1
        return sorted(out)

    def why(self, addr):
        return "inside no run of the function facts"

    def _decode(self):
        if self._md is None:
            self._md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
            self._md.detail = True
        return self._md

    def ins(self, entry):
        """The instructions of the entry's runs, in address order, or None when its runs do not decode to exactly their bytes (the
        entry is then `unresolved`: self.bad[entry] says why). Cached."""
        if entry not in self._ins:
            md, out = self._decode(), []
            for a, b in sorted(self.runs[entry]):
                run = list(md.disasm(self.image.bytes_at(a, b - a), a))
                if not run or run[-1].address + run[-1].size != b or run[0].address != a:
                    self.bad[entry] = "the run %x-%x does not decode to exactly its bytes (facts and image disagree)" % (a, b)
                    out = None
                    break
                out += run
            self._ins[entry] = out
            if len(self._ins) > 256:
                self._ins.popitem(last=False)
        return self._ins[entry]

    def block_of(self, entry, addr):
        """The maximal contiguous list of instructions of the entry that holds `addr` (pattern windows must not cross a gap between runs)."""
        ins = self.ins(entry)
        if ins is None:
            return None
        k = next((j for j, i in enumerate(ins) if i.address <= addr < i.address + i.size), None)
        if k is None:
            return None
        a = b = k
        while a > 0 and ins[a - 1].address + ins[a - 1].size == ins[a].address:
            a -= 1
        while b + 1 < len(ins) and ins[b].address + ins[b].size == ins[b + 1].address:
            b += 1
        return ins[a:b + 1]

    def heads(self, entry):
        """Addresses where a pass must start from an unknown state unless it holds a state saved from a forward jump: backward and
        self jump targets inside the entry, and the start of every run but the first."""
        ins = self.ins(entry)
        if ins is None:
            return None
        h = {a for a, _ in sorted(self.runs[entry])[1:]}
        es = self.entry_list()
        for a, b in self.runs[entry]:   # every other entry inside the runs: a relocated code pointer (jump-table slot, callback) or call target
            k = bisect.bisect_right(es, a - 1)
            while k < len(es) and es[k] < b:
                if es[k] != entry:
                    h.add(es[k])
                k += 1
        for i in ins:
            if (i.mnemonic.startswith("j") or i.mnemonic in ("loop", "loope", "loopne")) and i.operands and i.operands[0].type == X.X86_OP_IMM:
                t = i.operands[0].imm
                if t <= i.address:
                    h.add(t)
        return h

    def edges(self):
        """The direct control transfers of every decoded instruction of the facts, once per instruction address, as
        ({target: [call sites]}, {target: [rel32 jmp sites]}): `call imm` and `jmp imm` encoded e9. A site in several entries appears once;
        its entries are entries_of(site). Runs that do not decode are skipped (self.bad names them)."""
        if getattr(self, "_edges", None) is None:
            calls, jmps = collections.defaultdict(list), collections.defaultdict(list)
            seen = set()
            md = self._decode()
            for a, b in sorted({r for rs in self.runs.values() for r in rs}):
                run = list(md.disasm(self.image.bytes_at(a, b - a), a))
                if not run or run[-1].address + run[-1].size != b:
                    continue
                for i in run:
                    if i.address in seen or not i.operands or i.operands[0].type != X.X86_OP_IMM:
                        continue
                    if i.mnemonic == "call":
                        calls[i.operands[0].imm].append(i.address)
                        seen.add(i.address)
                    elif i.mnemonic == "jmp" and i.bytes[0] == 0xe9:
                        jmps[i.operands[0].imm].append(i.address)
                        seen.add(i.address)
            self._edges = (calls, jmps)
        return self._edges

    def ijumps(self, entry):
        """Addresses of the indirect jumps (jmp reg/mem) of the entry; their targets are not in the facts."""
        ins = self.ins(entry)
        return None if ins is None else [i.address for i in ins if i.mnemonic == "jmp" and i.operands and i.operands[0].type != X.X86_OP_IMM]


def file_runs(path, base):
    """Parse a ranges.tsv and rebase it from its `# base=` header to `base`."""
    runs, fb = {}, None
    for ln in open(path):
        ln = ln.rstrip("\n")
        if ln.startswith("# base="):
            fb = int(ln.split()[1][5:].rstrip(":"), 16)
            continue
        if not ln or ln[0] == "#":
            continue
        e, _, r = ln.partition("\t")
        runs[int(e, 16)] = [tuple(int(x, 16) for x in p.split("-")) for p in r.split(",")] if r else []
    if fb is None:
        raise SystemExit("funcfacts: %s has no `# base=` header" % path)
    d = base - fb
    return {e + d: [(a + d, b + d) for a, b in rs] for e, rs in runs.items()} if d else runs


def declare(image, runs):
    """Facts for an image that has no facts directory: the self-test's synthetic PE, whose entries and runs are its own input data."""
    _images[(image.name, image.base)] = FuncFacts(image.name, image, runs)


def for_image(image):
    """The facts of one Image: a FuncFacts, or NoFacts with the reason."""
    name, base, path = image.name, image.base, image.path
    key = (name, base)
    if key in _images:
        return _images[key]
    if _dir is None:
        raise SystemExit("funcfacts: no facts directory (pass --facts DIR, the tree pinned in %s)" % PIN)
    ent = _manifest.get(name.lower())
    if ent is None:
        r = NoFacts("no function facts")
    else:
        sha = sha256_file(path)
        if sha != ent["sha256"]:
            raise SystemExit("funcfacts: %s has sha256 %s, the facts are of %s" % (path, sha, ent["sha256"]))
        r = FuncFacts(name, image, file_runs(os.path.join(_dir, ent["ranges"]), base))
    _images[key] = r
    return r


def join(ff, by_entry, key):
    """Join the rows of the entries of one image. `by_entry` = {entry: [row, ...]}; `key(row)` = the site (an address) of a row.
    A site is analysed in every entry of `by_entry` whose runs contain it. Its rows are `agreed` when each of those entries gives the
    same rows, else a conflict (site, entries, {entry: rows}) of which the pass makes a dynamic row. Returns (agreed rows, conflicts)."""
    sites = {}
    for e, rows in by_entry.items():
        for r in rows:
            sites.setdefault(key(r), {}).setdefault(e, []).append(r)
    agreed, conflicts = [], []
    for k in sorted(sites):
        per = {e: tuple(sorted(rs, key=repr)) for e, rs in sites[k].items()}
        expect = {e for e in ff.entries_of(k) if e in by_entry}
        if set(per) == expect and len(set(per.values())) == 1:
            agreed += list(next(iter(per.values())))
        else:
            conflicts.append((k, sorted(per), per))
    return agreed, conflicts
