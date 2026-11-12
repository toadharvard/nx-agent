#!/usr/bin/env python3
"""Import manifest of a PE module set (R10a).

Rule: the manifest is the union of the import tables (static and delay) of every
module in the load closure. The Windows loader resolves every slot before the
entry point runs, so every slot is part of the load contract; nothing is dropped
because a trace did not call it. Reference counts are reported as information.

Also lists every call site of the dynamic-binding imports (LoadLibrary*,
GetModuleHandle*, GetProcAddress, CoCreateInstance) with the constant string or
GUID operand when the bytes before the call push one. A site without a constant
is reported as `dynamic`: the implementation must be total over all names anyway.

Sites (dynsites.tsv): a site is the address of a decoded CALL INSTRUCTION inside the reached runs of the L3a function facts (loaded and
pin-checked by funcfacts.py, `--facts DIR`) whose operand is the import slot (`call [slot]`), a direct call of a thunk `jmp [slot]`, or
`call reg` after `mov reg,[slot]` (the L1 contract rule argflow.imports_called, whose Image is built from the runs funcfacts loaded). A slot
operand, a thunk body or a slot load is never a site; a slot use that escapes is listed on stderr as OPEN. No function bound and no byte window
is guessed. A loader operand is the module literal when its argument is a closed constant (argflow.derive_arg), else `dynamic`; a GetProcAddress
row has operand and hmod `dynamic` (resolve_dynsites.py derives them from the same facts); a CoCreateInstance operand is its CLSID and IID.

Usage: pemanifest.py <dirs, ':'-separated in DECLARED_ROOTS order> <root.exe> <outdir> <bridge.tsv> <dllkind.py> <dllkind-sha256> <contract-dir> <import_escape.tsv> --facts DIR
The role of every dll is the one rule of contract/dllkind.py (L1 contract, branch claude/fnv-l1-contract-w18, pinned by the sha256
argument: the file is read from that path, never copied): image | bridge | declared | platform (the root is `root`). BRIDGE is the set
of dlls with rows in bridge.tsv (the previous table: the closure is a fixed point; bridgetable.py writes it from this run's sys_names).
Reach: the root's normal then delay-load imports, depth first through images (dllkind.classify), and every module of the directory
that a reached module loads by a constant name with LoadLibrary* (its dynsites row: the literal operand) joins as a delay-load import of the
root (bound at run time, like a delay import), repeated until no new module is reached. closure.tsv column `reach`: `static` (an import
of a reached module) or `dynamic` (only a constant-name load reaches it). A name met without a role, or a module of the directory not met,
is listed with role `unresolved`; an import name of an analysed module that dllkind gives no role is an error.
Output (exe-derived; keep out of the public repository):
  imports.tsv   module, dll, name|#ordinal, slot_va, code_refs, data_refs
  closure.tsv   module, role (root|image|bridge|declared|platform|unresolved), imports, exports, reach (static|dynamic)
  dynsites.tsv  module, site_va, api, operand, hmod
  regsites.tsv  module, site, api, root, subkey, value, status, reason  (registry call sites, see regsites.py)
"""
import bisect, collections, os, re, struct, sys
import capstone
import pefile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import funcfacts
from capstone import x86_const as X

DYN = {"LoadLibraryA", "LoadLibraryW", "LoadLibraryExA", "LoadLibraryExW",
       "GetModuleHandleA", "GetModuleHandleW", "GetProcAddress",
       "CoCreateInstance"}


JOINED = []   # (module, site, api, entries) of every site whose entries disagree (census)


def relocs(pe):
    out = []
    base = pe.OPTIONAL_HEADER.ImageBase
    for blk in getattr(pe, "DIRECTORY_ENTRY_BASERELOC", []):
        for e in blk.entries:
            if e.type == 3:
                out.append(base + e.rva)
    out.sort()
    return out


class Mod:
    def __init__(self, path):
        self.name = os.path.basename(path).lower()
        self.path = path
        self.pe = pe = pefile.PE(path, fast_load=True)
        pe.parse_data_directories(directories=[0, 1, 5, 13])
        self.base = pe.OPTIONAL_HEADER.ImageBase
        self.img = pe.get_memory_mapped_image()
        self.relocs = relocs(pe)
        self.imports = []  # (dll, name, slot)
        self.normal, self.delay = [], []   # dll names of the normal and of the delay-load import directory, directory order
        for kind, names in (("DIRECTORY_ENTRY_IMPORT", self.normal), ("DIRECTORY_ENTRY_DELAY_IMPORT", self.delay)):
            for e in getattr(pe, kind, []):
                if e.dll.decode().lower() not in names:
                    names.append(e.dll.decode().lower())
                for i in e.imports:
                    nm = i.name.decode() if i.name else "#%d" % i.ordinal
                    self.imports.append((e.dll.decode().lower(), nm, i.address))
        ex = getattr(pe, "DIRECTORY_ENTRY_EXPORT", None)
        self.exports = len(ex.symbols) if ex else 0
        self.code = [(self.base + s.VirtualAddress, s.Misc_VirtualSize)
                     for s in pe.sections if s.Characteristics & 0x20000000]

    def u32(self, va):
        o = va - self.base
        return struct.unpack_from("<I", self.img, o)[0] if 0 <= o <= len(self.img) - 4 else None

    def in_code(self, va):
        return any(a <= va < a + n for a, n in self.code)

    def in_data(self, va):
        return 0 <= va - self.base < len(self.img) and not self.in_code(va)

    def cstr(self, va):
        o = va - self.base
        if not (0 <= o < len(self.img)):
            return None
        e = self.img.find(b"\0", o, o + 260)
        s = self.img[o:e] if e > o else b""
        if len(s) >= 2 and all(32 <= c < 127 for c in s):
            return s.decode()
        w = self.img[o:o + 520]
        e = next((i for i in range(0, len(w) - 1, 2) if w[i:i + 2] == b"\0\0"), 0)
        try:
            t = w[:e].decode("utf-16le")
        except UnicodeDecodeError:
            return None
        return "L:" + t if len(t) >= 2 and all(32 <= ord(c) < 127 for c in t) else None

    def refs(self):
        """slot -> [code sites], [data sites] from the relocation table."""
        slots = {s for _, _, s in self.imports}
        code, data = collections.defaultdict(list), collections.defaultdict(list)
        for r in self.relocs:
            t = self.u32(r)
            if t in slots:
                (code if self.in_code(r) else data)[t].append(r)
        return code, data

    def argimage(self, contract, bridge_tsv, escape_tsv):
        """The L1 contract's argflow.Image of this module over the pinned L3a function facts: the ranges are the ones funcfacts.py loaded
        (tree_sha256 and image sha256 checked, rebased to the image base), so argflow reads no facts file of its own. None when the image has no facts."""
        if getattr(self, "_ai", "unset") == "unset":
            ff = self.ff
            if not ff.ok:
                self._ai = None
                return None
            sys.path.insert(0, contract)
            import argflow, gpa_names, memfx
            self._argflow = argflow
            im = argflow.Image(open(self.path, "rb").read(), self.name, {e: list(rs) for e, rs in ff.runs.items()})
            im.import_stack = gpa_names.import_stacks(open(bridge_tsv, encoding="utf-8").read())
            memfx.Memfx(im, memfx.ImportInfo(open(bridge_tsv, encoding="utf-8").read(), [open(escape_tsv, encoding="utf-8").read()], ""))
            self._ai = im
        return self._ai

    def dynsites(self, contract, bridge_tsv, escape_tsv):
        """(rows, open): rows (call va, api, operand, hmod) of every call of LoadLibrary*/GetModuleHandle*/GetProcAddress/CoCreateInstance;
        open = [(api, va, why)] for slot uses that make the call set open (an escaped slot, an address-taken thunk).
        Rule (L1 contract argflow.imports_called): a site is the address of a DECODED CALL INSTRUCTION inside the reached runs of the L3a function
        facts whose operand is the import slot (`call [slot]`), a direct call of a thunk `jmp [slot]`, or `call reg` after `mov reg,[slot]`; the
        slot operand of a call, a thunk and a slot load are never sites. A loader operand is the literal module name when the argument is a
        closed constant (argflow.derive_arg), else `dynamic`; a GetProcAddress operand and hmod are `dynamic` (resolve_dynsites.py derives them);
        a CoCreateInstance operand is the CLSID and IID constants (stdcall arguments 1 and 4) read from the image."""
        im = self.argimage(contract, bridge_tsv, escape_tsv)
        if im is None:
            return [], [("*", 0, "unresolved: " + self.ff.reason)]
        af = self._argflow
        rows, op_ = [], []
        for api in sorted({n for _, n, _ in self.imports if n in DYN}):
            calls, o = af.imports_called(im, api)
            op_ += [(api, va, why) for va, why in o]
            for c in calls:
                if api == "CoCreateInstance":
                    gs = []
                    for k in (0, 3):
                        r = af.derive_arg(im, c, k)
                        for kind, val, _ in r.sorted_leaves():
                            g = im.pe.rd(val - im.base, 16) if kind == "imm" and im.in_raw(val, 16) else None
                            if g and len(g) == 16:
                                a, b2, c2 = struct.unpack_from("<IHH", g)
                                gs.append("{%08X-%04X-%04X-%s-%s}" % (a, b2, c2, g[8:10].hex().upper(), g[10:].hex().upper()))
                    rows.append((c, api, " ".join(gs) if gs else "dynamic", "-"))
                elif api == "GetProcAddress":
                    rows.append((c, api, "dynamic", "dynamic"))
                else:
                    wide = api.endswith("W")
                    r = af.derive_arg(im, c, 0)
                    lits = [v[1 if wide else 0] for k2, v, _ in r.sorted_leaves() if k2 == "str" and v[1 if wide else 0] is not None]
                    ok = r.closed and lits and all(k2 == "str" for k2, _, _ in r.sorted_leaves())
                    rows.append((c, api, " ".join(sorted(set(("L:" if wide else "") + l for l in lits))) if ok else "dynamic", "-"))
        return rows, op_

    def targets(self):
        """relocation target -> [relocation addresses]"""
        if not hasattr(self, "_tg"):
            self._tg = collections.defaultdict(list)
            for r in self.relocs:
                self._tg[self.u32(r)].append(r)
        return self._tg

    @property
    def ff(self):
        """the function facts of this image (funcfacts.py): FuncFacts, or NoFacts with the reason"""
        return funcfacts.for_image(self.name, self.img, self.base, self.path)

    def entries_of(self, site):
        """Every entry of the function facts whose runs contain `site` and decode (a site is analysed in each; see funcfacts.py). [] when
        there is none: `unresolved_why(site)` then names the reason."""
        ff = self.ff
        return [e for e in ff.entries_of(site) if ff.ins(e) is not None]

    def unresolved_why(self, site):
        """The reason a site has no function: `no function facts` (image not covered), a run that does not decode, or `inside no run`."""
        ff = self.ff
        if not ff.ok:
            return ff.reason
        es = ff.entries_of(site)
        return "inside no run of the function facts" if not es else "; ".join(
            "entry %x: %s" % (e, ff.bad[e]) for e in es if e in ff.bad)


def load_dllkind(path, sha):
    import hashlib, importlib.util
    got = hashlib.sha256(open(path, "rb").read()).hexdigest()
    if got != sha:
        sys.exit("dllkind.py sha256 %s != pinned %s" % (got, sha))
    spec = importlib.util.spec_from_file_location("dllkind", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(d, root, out, bridge_tsv, dllkind_py, dllkind_sha, contract, escape_tsv):
    dk = load_dllkind(dllkind_py, dllkind_sha)
    bridge = {l.split("\t")[0].lower() for l in open(bridge_tsv, encoding="utf-8")
              if l.strip() and not l.startswith(("#", "dll\t")) and not l.startswith("!")}
    files = {}
    for dd in d.split(os.pathsep):      # the declared file set in DECLARED_ROOTS order (contract/dllkind.py): the first directory holding a name wins
        for f in sorted(os.listdir(dd)):
            if f.lower().endswith((".exe", ".dll")):
                files.setdefault(f.lower(), os.path.join(dd, f))
    root = root.lower()
    parsed = {}

    def mod_of(path):
        if path not in parsed:
            parsed[path] = Mod(path)
        return parsed[path]

    def imports_of(path):
        m = mod_of(path)
        return m.normal, m.delay

    def find(name):
        return files.get(name.lower())

    loaded = []        # names that constant-name loads reach, in discovery order
    while True:
        r = mod_of(files[root])
        order, kind = dk.classify((r.normal, r.delay + [n for n in loaded if n not in r.delay]), imports_of, find, bridge, root)
        new = []
        for n in [root] + [x for x, _ in order]:
            if kind.get(n, "root") not in ("image", "root"):
                continue
            m = mod_of(files[n])
            for site, nm, op, hm in sorted(set(m.dynsites(contract, bridge_tsv, escape_tsv)[0])):
                if nm.startswith("LoadLibrary"):
                    for lit in op.split():
                        lit = lit.lower()
                        lit = lit[2:] if lit.startswith('l:') else lit
                        if lit in files and lit not in bridge and lit != root and lit not in loaded and lit not in new \
                                and lit not in r.normal and lit not in r.delay:
                            new.append(lit)
        if not new:
            break
        loaded += new
    mods = {n: mod_of(files[n]) for n in [root] + [x for x, _ in order] if kind.get(n, "root") in ("image", "root")}
    for n, m in mods.items():
        for dll in m.normal + m.delay:
            if dll not in kind and dll != root:
                sys.exit("dll %s imported by %s has no role in dllkind" % (dll, n))
    static_names = {x for m in mods.values() for x in m.normal + m.delay}
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "imports.tsv"), "w") as fi, \
         open(os.path.join(out, "dynsites.tsv"), "w") as fd, \
         open(os.path.join(out, "closure.tsv"), "w") as fc:
        fi.write("module\tdll\tname\tslot\tcode_refs\tdata_refs\n")
        fd.write("module\tsite\tapi\toperand\thmod\n")
        fc.write("module\trole\timports\texports\treach\n")
        for n in sorted(mods):
            m = mods[n]
            code, data = m.refs()
            for dll, nm, slot in m.imports:
                fi.write("%s\t%s\t%s\t0x%08x\t%d\t%d\n" % (n, dll, nm, slot, len(code[slot]), len(data[slot])))
            rows, opn = m.dynsites(contract, bridge_tsv, escape_tsv)
            for api, va, why in opn:
                sys.stderr.write("OPEN %s %s@%x %s\n" % (n, api, va, why))
            for site, nm, op, hm in sorted(set(rows)):
                fd.write("%s\t0x%08x\t%s\t%s\t%s\n" % (n, site, nm, op, hm))
            fc.write("%s\t%s\t%d\t%d\t%s\n" % (n, kind.get(n, "root"), len(m.imports), m.exports,
                                                 "-" if n == root else "static" if n in static_names else "dynamic"))
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import regsites
        regsites.write(mods, out)
        for n in sorted(k for k in kind if k not in mods):          # met names that are not analysed modules
            fc.write("%s\t%s\t-\t-\t%s\n" % (n, kind[n], "static"))
        for n in sorted(set(files) - set(mods) - set(kind)):
            fc.write("%s\tunresolved\t-\t-\t-\n" % n)


if __name__ == "__main__":
    funcfacts.facts_dir_from_argv(sys.argv)
    main(*sys.argv[1:9])
