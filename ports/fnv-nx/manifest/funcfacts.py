#!/usr/bin/env python3
"""funcfacts.py - the one reader of the function facts for the platform passes (function boundaries).

ASM-501 (function boundaries and forward flow of the manifest passes) is removed by this module: the boundaries are facts, not an assumption.
The function boundaries of every pass (pemanifest, regsites, regclass, filesites, fontfaces, prefs_settings) come from here and only
from here. The source is L3a's lift facts (private, exe- and DLL-derived; fnv-nx-build, branch l3a-lift/facts), pinned in facts.json
beside this file: repo, branch, commit and tree_sha256 (verify/cas.py tree_hash of the checked-out facts directory, without .git).
Re-pinning to a refined facts commit is a change of those four fields and nothing else.

Facts used: manifest.json (per image: name, sha256, ranges file, base) and each image's ranges.tsv
    # base=0x...                         absolute VAs at that base (the exe: the preferred base; a module: its assigned load base)
    <entry>\\t<a-b>,<a-b>,...            the half-open runs of instruction bytes reached from the entry by proven flow
No other fact is read (universe.tsv's extent column is not).

Image access: this is the only module of platform/ that imports imagerec (contract/imagerec.py, THE constructor, byte reader and decoder
of an image: it opens the image file, checks the facts tree against the pin and the image against the manifest's sha256, rebases the runs,
refuses reads of code with imagerec.CodeRead, and decodes x86). It imports no capstone, pefile or mmap (dnfile only for .NET metadata).
`Image` is the typed record of one PE image on top of an imagerec.ImageRec: base, relocations, imports, exports and the data accessors
(u8/u32/bytes_at/data_at/zstr/cstr/string_at/find_bytes), each of which goes through ImageRec.read/word and so refuses code. An image
without function facts has no runs, so every executable section counts as code for these accessors. There is one CodeRead (imagerec's);
this module defines none. check_funcfacts.py keeps this by the import graph, whatever the names are.

Rules (each one closes the whole class):
  image     the image file's sha256 must equal the manifest's: facts of another file are refused (imagerec.ImageError).
  rebase    the ranges are moved from the facts' base to the PE ImageBase the passes work at (imagerec.load).
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
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "contract"))
import imagerec
from imagerec import X   # the operand/register constants of the decoded instructions, for the passes that read them

PIN = os.path.join(HERE, "facts.json")
_dir = None
_pin_tree = None


def _pin():
    p = json.load(open(PIN))
    if p.get("format") != "fnvnx-funcfacts-pin/1":
        raise SystemExit("funcfacts: %s is not a facts pin" % PIN)
    return p["external"]


def set_facts_dir(d):
    """Select the facts directory; imagerec checks that it is the pinned tree (tree_sha256), checked out without .git."""
    global _dir, _pin_tree
    _pin_tree = {"tree_sha256": _pin()["tree_sha256"]}
    imagerec.facts_manifest(d, _pin_tree)   # raises imagerec.ImageError when d is not the pinned tree
    _dir = d


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
    """One PE image as typed data on top of an imagerec.ImageRec (`rec`): base, relocs (absolute VAs of the HIGHLOW relocations, sorted),
    imports [(dll, name, slot)], normal/delay import dll names in directory order, exports (count) and export_names {va: name}, code [(va, size)]
    of the executable sections. The data accessors read through rec.read/word and refuse code (imagerec.CodeRead)."""

    def __init__(self, path):
        if _dir is None:
            raise SystemExit("funcfacts: no facts directory (pass --facts DIR, the tree pinned in %s)" % PIN)
        self._set(imagerec.load(path, _dir, _pin_tree), path)

    def _set(self, rec, path=None):
        self.rec, self.path, self.name, self.base = rec, path, rec.name, rec.base
        self.relocs = sorted(rec.relocs)
        self.imports = [(dll.lower(), nm, slot) for slot, (dll, nm) in rec.imports.items()]
        self.normal = rec.import_dlls()
        self.delay = list(dict.fromkeys(d.lower() for d in rec.delay_dlls()))
        ex = rec.exports()
        self.exports = len(ex)
        self.export_names = {self.base + f: key for key, f, fwd, _ in ex if f is not None and fwd is None}
        self.code = [(self.base + rva, vs) for rva, vs, _, _, ch in rec.secs if ch & 0x20000000]
        self._rs = frozenset(self.relocs)
        self._ff = None
        self._cr = None

    @classmethod
    def synthetic(cls, name, base, img, relocs, imports, code, runs=None, facts=None):
        """An image built from given bytes (the self-tests' synthetic PEs, pefixture.build_pe): an imagerec.ImageRec of a PE that is built
        in memory, with the function facts `runs` ({entry: [(a, b)]}), or with no facts and the reason `facts` ("no function facts")."""
        import pefixture
        self = cls.__new__(cls)
        self._set(imagerec.ImageRec(pefixture.build_pe(base, img, relocs, imports, code), name, runs or {}, facts if runs is None else None), None)
        return self

    def code_runs(self):
        """The merged [a, b) ranges that are code for this image: the runs of its function facts; with no facts, every executable section
        (code is then unknown, so none of it may be read as bytes)."""
        if self._cr is None:
            ff = for_image(self)
            self._cr = [(a, a + n) for a, n in sorted(self.code)] if not ff.ok else ff.merged()
        return self._cr

    def _chk(self, va, n):
        """An image without function facts has no runs for imagerec to refuse: its executable sections are code."""
        if self.rec.facts is not None:
            for a, b in self.code_runs():
                if a < va + n and va < b:
                    raise imagerec.CodeRead("%s: byte read of %d at 0x%08x inside code 0x%08x-0x%08x (no function facts: an executable section)"
                                            % (self.name, n, va, a, b))

    def in_runs(self, va):
        """True when the byte at va is inside a code range of this image (the runs of its facts; with no facts, an executable section)."""
        runs = self.code_runs()
        k = bisect.bisect_right(runs, (va, 1 << 62)) - 1
        return k >= 0 and runs[k][0] <= va < runs[k][1]

    def size(self):
        return self.rec.size_of_image

    def in_image(self, va):
        return 0 <= va - self.base < self.rec.size_of_image

    def in_code(self, va):
        return any(a <= va < a + n for a, n in self.code)

    def in_data(self, va):
        return self.in_image(va) and not self.in_code(va)

    def _extent(self, va, limit):
        """The number of bytes from va (at most `limit`, up to the image end) that hold no code: 0 when va is in code."""
        n = min(limit, self.base + self.rec.size_of_image - va)
        for a, b in self.code_runs():
            if b > va and a < va + n:
                n = max(0, a - va)
        return n

    def u8(self, va):
        if not self.in_image(va):
            return None
        self._chk(va, 1)
        return self.rec.read(va, 1)[0]

    def u32(self, va):
        """The 32-bit word at va; inside code only a relocated field of a decoded instruction (imagerec.word)."""
        if not 0 <= va - self.base <= self.rec.size_of_image - 4:
            return None
        if va not in self._rs:
            self._chk(va, 4)
        return self.rec.word(va)

    def bytes_at(self, va, n):
        """n data bytes at va (shorter when va+n passes the image end); None when va is outside; CodeRead when any byte is code."""
        if not self.in_image(va):
            return None
        n = min(n, self.base + self.rec.size_of_image - va)
        self._chk(va, n)
        return self.rec.read(va, n)

    def data_at(self, va, n):
        """n bytes at va if they are all data; None when va is outside the image or the span touches code (a question, not a code read:
        a pointer whose target is, or runs into, code does not designate this data structure)."""
        if not 0 <= va - self.base <= self.rec.size_of_image - n or self._extent(va, n) < n:
            return None
        return self.rec.read(va, n)

    def zstr(self, va, limit):
        """The data bytes from va up to the first NUL within `limit` bytes (None if va is outside or no NUL within limit); a string that
        runs from data into code raises CodeRead."""
        if not self.in_image(va):
            return None
        n = self._extent(va, limit)
        raw = self.rec.read(va, n) if n else b""
        e = raw.find(b"\0")
        if e >= 0:
            return raw[:e]
        if n < limit and va + n < self.base + self.rec.size_of_image:
            self._chk(va + n, 1)
            self.rec.read(va + n, 1)   # the string runs from data into code: one of the two refuses
        return None

    def find_bytes(self, pat):
        """The VA of the first occurrence of `pat` in the data of the image (None if absent). Code is not searched: a match must lie
        wholly outside the code ranges."""
        pos, gaps, end = self.base, [], self.base + self.rec.size_of_image
        for a, b in self.code_runs():
            if a > pos:
                gaps.append((pos, min(a, end)))
            pos = max(pos, b)
        gaps.append((pos, end))
        for a, b in gaps:
            if b > a:
                o = self.rec.read(a, b - a).find(pat)
                if o >= 0:
                    return a + o
        return None

    def string_at(self, va):
        """The string a pointer value designates (cstr), None when the value is not a pointer to data: outside the image, or at a code
        address of the facts' runs (a code address is a function or label, not a string; its bytes are not read)."""
        if not self.in_image(va) or self.in_runs(va):
            return None
        return self.cstr(va)

    def cstr(self, va):
        """A printable ASCII or UTF-16LE string at va (`L:` prefix for the latter), else None."""
        if not self.in_image(va):
            return None
        s = self.zstr(va, 260) or b""
        if len(s) >= 2 and all(32 <= c < 127 for c in s):
            return s.decode()
        raw = self.rec.read(va, self._extent(va, 522))
        w = bytearray()
        while len(w) < 520 and len(w) + 2 <= len(raw) and raw[len(w):len(w) + 2] != b"\0\0":
            w += raw[len(w):len(w) + 2]
        if len(w) + 2 > len(raw):
            self._chk(va + len(raw), 1)   # the terminator would lie in code: one of the two refuses
            self.rec.read(va + len(raw), 1)
        try:
            t = bytes(w).decode("utf-16le")
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

    def insn_at(self, addr):
        return None

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
        self._el = None
        self.bad = {}   # entry -> reason, for entries whose runs do not decode to exactly their bytes (reported, never analysed)

    def merged(self):
        """All runs merged into sorted disjoint [a, b) ranges: the code of the image, as the facts know it."""
        if getattr(self, "_mg", None) is None:
            out = []
            for a, b in sorted(r for rs in self.runs.values() for r in rs):
                if out and a <= out[-1][1]:
                    out[-1] = (out[-1][0], max(out[-1][1], b))
                else:
                    out.append((a, b))
            self._mg = out
        return self._mg

    def insn_at(self, addr):
        """The decoded instruction that starts at `addr`, from any entry whose runs contain it; None if there is none."""
        for e in self.entries_of(addr):
            ins = self.ins(e)
            if ins:
                k = bisect.bisect_left([i.address for i in ins], addr)
                if k < len(ins) and ins[k].address == addr:
                    return ins[k]
        return None

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

    def ins(self, entry):
        """The instructions of the entry's runs, in address order, or None when its runs do not decode to exactly their bytes (the
        entry is then `unresolved`: self.bad[entry] says why). Cached."""
        if entry not in self._ins:
            out = []
            for a, b in sorted(self.runs[entry]):
                run = list(self.image.rec.decode_range(a, b))
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
            for a, b in sorted({r for rs in self.runs.values() for r in rs}):
                run = list(self.image.rec.decode_range(a, b))
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


def for_image(image):
    """The facts of one Image: a FuncFacts over the runs of its ImageRec, or NoFacts with the reason (the record has no function facts)."""
    if image._ff is None:
        rec = image.rec
        image._ff = NoFacts(rec.facts) if rec.facts is not None else FuncFacts(image.name, image, rec.runs)
    return image._ff


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
