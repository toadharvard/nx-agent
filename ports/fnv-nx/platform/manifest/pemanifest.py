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

Dynamic-binding calls (LoadLibrary*, GetModuleHandle*, GetProcAddress) are found by a LINEAR DISASSEMBLY of the whole function that
loads the import slot (capstone), so a call through a register (`mov esi,[GetProcAddress]; push name; push hmod; call esi`, the CRT
idiom) is a site like `call [slot]`. Per call: the name operand is the last pushed string literal (a `push imm32` with a relocation); the
hmod operand is resolved by one local data-flow rule: a register or an ebp-slot holds the literal(s) of the LoadLibrary/GetModuleHandle
calls whose result (eax) was copied into it, and anything else written to it (or its address taken with lea) makes it `dynamic`; the
slot value is the union over all writes in the function. Output column hmod = `dynamic` or the comma list of module literals; the
operand column then holds those literals + the name. Function boundaries and flow: the entries of the L3a function facts (funcfacts.py, `--facts DIR`; pinned in facts.json). A site is analysed in every entry whose runs contain it and the rows are joined (a disagreement is `dynamic`); a site in no run is `unresolved` with the reason; an image without facts is `unresolved: no function facts`. Control flow is followed forward only inside an entry (state saved at forward jumps, joined at their target, cleared after ret/jmp unless a jump targets the next address); the state is top at every head (`funcfacts.heads`: loop heads, run starts after the first, other entries inside the runs); an ebp slot is not written through
another pointer (its address is never taken, checked).
Global cell: a data cell g is tracked like an ebp slot when every relocated reference to g in the image is a plain `[g]` operand (never lea, push imm or a stored pointer) and the references outside the function only read it, so every write to g is seen in the function. A cell failing this, and any object field (`[reg+disp]`), stays `dynamic`.

Usage: pemanifest.py <dir-with-modules> <root.exe> <outdir> <bridge.tsv> <dllkind.py> <dllkind-sha256>
The role of every dll is the one rule of contract/dllkind.py (L1 contract, branch claude/fnv-l1-contract-w18, pinned by the sha256
argument: the file is read from that path, never copied): image | bridge | declared | platform (the root is `root`). BRIDGE is the set
of dlls with rows in bridge.tsv (the previous table: the closure is a fixed point; bridgetable.py writes it from this run's sys_names).
Reach: the root's normal then delay-load imports, depth first through images (dllkind.classify), and every module of the directory
that a reached module loads by a constant name with LoadLibrary* (its dynsites row: the literal operand) joins as a delay-load import of the
root (bound at run time, like a delay import), repeated until no new module is reached. closure.tsv column `reach`: `static` (an import
of a reached module) or `dynamic` (only a constant-name load reaches it). A name met without a role, or a module of the directory not met,
is listed with role `not-in-static-closure`; an import name of an analysed module that dllkind gives no role is an error.
Output (exe-derived; keep out of the public repository):
  imports.tsv   module, dll, name|#ordinal, slot_va, code_refs, data_refs
  closure.tsv   module, role (root|image|bridge|declared|platform|not-in-static-closure), imports, exports, reach (static|dynamic)
  dynsites.tsv  module, site_va, api, operand, hmod
  regsites.tsv  module, site, api, root, subkey, value, status, reason  (registry call sites, see regsites.py)
"""
import bisect, collections, os, struct, sys
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

    def dynsites(self, code):
        """Rows (site, api, operand, hmod) of every LoadLibrary*/GetModuleHandle*/GetProcAddress call and CoCreateInstance site."""
        rs = self.relocs
        rset = set(rs)
        covered = set()
        api = {s: n for _, n, s in self.imports if n in DYN and n != "CoCreateInstance"}
        done, by_entry = set(), {}
        for slot in api:
            for site in code.get(slot, []):
                for entry in self.entries_of(site):   # a site is analysed in every entry that contains it; the rows are joined below
                    if entry in done:
                        continue
                    done.add(entry)
                    by_entry[entry] = list(self.flow(entry, api, rset, covered))
        agreed, conflicts = funcfacts.join(self.ff, by_entry, lambda r: r[0])
        for row in agreed:
            yield row
        for site, entries, per in conflicts:   # the entries disagree: the row is dynamic (the operand and the module are not decided)
            nm = next(iter(per.values()))[0][1]
            JOINED.append((self.name, site, nm, entries))
            covered.add(site)
            yield site, nm, "dynamic", "dynamic" if nm == "GetProcAddress" else "-"
        for dll, nm, slot in self.imports:
            for site in code.get(slot, []):
                if nm == "CoCreateInstance" or (nm in DYN and site not in covered):  # window rule: a thunk or a stored pointer
                    ops = []
                    i = bisect.bisect_left(rs, site - 48)
                    while i < len(rs) and rs[i] < site:
                        r = rs[i]
                        i += 1
                        if self.img[r - 1 - self.base] != 0x68:  # push imm32
                            continue
                        t = self.u32(r)
                        if nm == "CoCreateInstance":
                            g = self.img[t - self.base:t - self.base + 16]
                            if len(g) == 16:
                                a, b, c = struct.unpack_from("<IHH", g)
                                ops.append("{%08X-%04X-%04X-%s-%s}" % (a, b, c, g[8:10].hex().upper(), g[10:].hex().upper()))
                        else:
                            s = self.cstr(t)
                            if s:
                                ops.append(s)
                    yield site, nm, " ".join(ops) if ops else "dynamic", "-" if nm != "GetProcAddress" else "dynamic"

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

    def flow(self, entry, api, rset, covered):
        """Rows of the dynamic-name calls of one entry of the function facts, by forward flow over its decoded runs; the state is unknown
        at every head of the entry (loop heads, run starts, inner entries: funcfacts.heads)."""
        ins = self.ff.ins(entry)
        heads = self.ff.heads(entry)
        lo_a, hi_a = ins[0].address, ins[-1].address + ins[-1].size
        P = {"eax": "eax", "ax": "eax", "al": "eax", "ah": "eax", "ecx": "ecx", "cx": "ecx", "cl": "ecx", "edx": "edx", "dx": "edx",
             "dl": "edx", "ebx": "ebx", "bx": "ebx", "bl": "ebx", "esi": "esi", "si": "esi", "edi": "edi", "di": "edi"}
        UNK = None

        def join(a, b):  # state merge at a control-flow join: equal facts stay, module-literal sets unite, anything else is unknown
            if a is None:
                return (dict(b[0]), dict(b[1]), list(b[2]))
            m = lambda x, y: {k: (x[k] if x[k] == y[k] else (x[k] | y[k] if isinstance(x[k], set) and isinstance(y[k], set) else UNK))
                              for k in x.keys() & y.keys()}
            return (m(a[0], b[0]), {k: a[1][k] for k in a[1].keys() & b[1].keys() if a[1][k] == b[1][k]}, a[2] if a[2] == b[2] else [])
        memo = {}

        def cell_ok(g):
            """A global cell may hold a tracked module handle only if every reference to its address is a plain `[g]` memory operand
            (never lea/push imm/data pointer) and every reference outside this function only READS it, so all writes are seen here."""
            if g not in memo:
                ok = True
                for r in self.targets().get(g, []):
                    if lo_a <= r < hi_a:
                        cand, wr_ok = ins, True
                    else:  # a reference outside this entry: the instruction it belongs to, from the entries that contain it (only its reads count)
                        cand, wr_ok = [i for e in self.entries_of(r) for i in self.ff.ins(e)] if self.in_code(r) else [], False
                    ii = next((i for i in cand if i.address <= r < i.address + i.size), None)
                    m = [o for o in ii.operands if o.type == X.X86_OP_MEM and o.mem.base == 0 and o.mem.index == 0 and o.mem.disp == g] if ii else []
                    if not m or ii.mnemonic == "lea" or len(m) != 1 or any(o.type == X.X86_OP_IMM and o.imm == g for o in ii.operands) \
                            or (not wr_ok and m[0].access & 2):
                        ok = False
                        break
                memo[g] = ok
            return memo[g]

        def kd(op):
            if op.type != X.X86_OP_MEM or op.mem.index != 0 or op.mem.segment != 0:
                return None
            if op.mem.base == X.X86_REG_EBP:
                return op.mem.disp
            if op.mem.base == 0 and op.mem.disp not in api and self.in_data(op.mem.disp) and cell_ok(op.mem.disp):
                return ("g", op.mem.disp)
            return None
        slots, rows = collections.defaultdict(set), []
        for final in (0, 1):  # pass 0 collects the ebp-slot unions, pass 1 reads them
            reg, ptr, pushes, saved, dead = {}, {}, [], {}, False
            dynamic = False
            for i in ins:
                ops = i.operands
                if i.address in heads:  # a head: the state is unknown (top), whatever a forward jump saved
                    saved.pop(i.address, None)
                    reg, ptr, pushes, dead = {}, {}, [], False
                elif dead or i.address in saved:  # control-flow join: forward jumps carry the state to their target
                    s = saved.pop(i.address, None)
                    reg, ptr, pushes = (({}, {}, []) if s is None and dead else
                                        s if dead else join(s, (reg, ptr, pushes)))
                    dead = False
                if i.mnemonic.startswith("j") or i.mnemonic == "ret":
                    t = ops[0].imm if i.mnemonic.startswith("j") and ops and ops[0].type == X.X86_OP_IMM else None
                    if t is not None and t > i.address:
                        saved[t] = join(saved.get(t), (reg, ptr, pushes))
                    dead = dead or i.mnemonic in ("jmp", "ret")
                    continue
                mem = lambda op: kd(op) is not None
                val = lambda op: (op.type == X.X86_OP_REG and reg.get(P.get(i.reg_name(op.reg), ""), UNK)) or \
                    (slots.get(kd(op), UNK) if mem(op) and final else UNK)
                if i.mnemonic == "call":
                    a = ops[0]
                    f = (a.type == X.X86_OP_MEM and a.mem.base == 0 and a.mem.index == 0 and api.get(a.mem.disp)) or \
                        (a.type == X.X86_OP_REG and (ptr.get(P.get(i.reg_name(a.reg), "")) or ("", 0))[0])
                    if f:
                        if final:
                            covered.add(i.address + i.size - 4 if a.type == X.X86_OP_MEM else ptr[P[i.reg_name(a.reg)]][1])
                        nm = lits = None
                        if pushes:
                            top = pushes[-1] if f != "GetProcAddress" else (pushes[-2] if len(pushes) > 1 else None)
                            nm = top[1] if top and top[0] == "s" else None
                            hm = pushes[-1] if f == "GetProcAddress" else None
                            lits = None if dynamic else (sorted(hm[1]) if hm and hm[0] == "m" and hm[1] else None)
                        if final:
                            toks = (lits or []) if f == "GetProcAddress" else []
                            rows.append((i.address, f, " ".join(toks + [nm]) if nm else (" ".join(toks) or "dynamic"),
                                         ",".join(lits) if lits else "dynamic" if f == "GetProcAddress" else "-"))
                        res = {nm} if f != "GetProcAddress" and nm and not dynamic else UNK
                        for r in ("eax", "ecx", "edx"):
                            reg.pop(r, 0)
                            ptr.pop(r, 0)
                        reg["eax"] = res
                    else:
                        for r in ("eax", "ecx", "edx"):
                            reg.pop(r, 0)
                            ptr.pop(r, 0)
                    pushes = []
                    continue
                if i.mnemonic == "push":
                    a = ops[0]
                    if a.type == X.X86_OP_IMM:
                        s = self.cstr(a.imm) if (i.address + 1) in rset else None
                        pushes.append(("s", s) if s else ("x", UNK))
                    elif a.type == X.X86_OP_REG or mem(a):
                        v = val(a)
                        pushes.append(v if isinstance(v, tuple) else ("m", v))
                    else:
                        pushes.append(("x", UNK))
                    continue
                wr = {P.get(i.reg_name(r), i.reg_name(r)) for r in i.regs_access()[1]}
                if i.mnemonic == "mov" and len(ops) == 2 and ops[1].type == X.X86_OP_IMM and (i.address + i.size - 4) in rset:
                    t = self.cstr(ops[1].imm)  # a relocated string address stored in a register or into the pushed argument slot
                    if ops[0].type == X.X86_OP_REG and P.get(i.reg_name(ops[0].reg)):
                        reg[P[i.reg_name(ops[0].reg)]], _ = ("s", t) if t else UNK, ptr.pop(P[i.reg_name(ops[0].reg)], 0)
                        continue
                    if ops[0].type == X.X86_OP_MEM and ops[0].mem.base == X.X86_REG_ESP and ops[0].mem.disp == 0 and t:
                        pushes[-1:] = [("s", t)]  # the argument slot at [esp] is replaced, or (after a callee-popped call) opened
                        continue
                if i.mnemonic == "mov" and len(ops) == 2 and ops[0].type == X.X86_OP_REG:
                    d, s = P.get(i.reg_name(ops[0].reg)), ops[1]
                    if d and ops[0].size == 4:
                        v = val(s) if s.type == X.X86_OP_REG or mem(s) else UNK
                        sl = s.type == X.X86_OP_MEM and s.mem.base == 0 and s.mem.index == 0 and api.get(s.mem.disp)
                        reg[d], ptr[d] = v, (sl, i.address + i.size - 4)
                        if not sl:
                            ptr.pop(d, 0)
                        continue
                if i.mnemonic == "mov" and len(ops) == 2 and mem(ops[0]):
                    v = val(ops[1]) if ops[1].type == X.X86_OP_REG else UNK
                    v = None if isinstance(v, tuple) else v
                    if not final:
                        slots[kd(ops[0])] = (slots[kd(ops[0])] | v) if v and slots[kd(ops[0])] is not UNK else UNK
                    continue
                if i.mnemonic == "lea" and len(ops) == 2 and mem(ops[1]):
                    slots[kd(ops[1])] = UNK
                for r in wr:
                    if r in reg or r in P.values():
                        reg[r] = UNK
                        ptr.pop(r, 0)
                if any(mem(op) and (op.access & 2) for op in ops):  # any other write into an ebp slot
                    for op in ops:
                        if mem(op) and (op.access & 2):
                            slots[kd(op)] = UNK
        return rows


def load_dllkind(path, sha):
    import hashlib, importlib.util
    got = hashlib.sha256(open(path, "rb").read()).hexdigest()
    if got != sha:
        sys.exit("dllkind.py sha256 %s != pinned %s" % (got, sha))
    spec = importlib.util.spec_from_file_location("dllkind", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(d, root, out, bridge_tsv, dllkind_py, dllkind_sha):
    dk = load_dllkind(dllkind_py, dllkind_sha)
    bridge = {l.split("\t")[0].lower() for l in open(bridge_tsv, encoding="utf-8")
              if l.strip() and not l.startswith(("#", "dll\t")) and not l.startswith("!")}
    files = {f.lower(): os.path.join(d, f) for f in os.listdir(d)
             if f.lower().endswith((".exe", ".dll"))}
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
            code, _ = m.refs()
            for site, nm, op, hm in sorted(set(m.dynsites(code))):
                if nm.startswith("LoadLibrary"):
                    for lit in op.split():
                        lit = lit.lower()
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
            for site, nm, op, hm in sorted(set(m.dynsites(code))):
                fd.write("%s\t0x%08x\t%s\t%s\t%s\n" % (n, site, nm, op, hm))
            fc.write("%s\t%s\t%d\t%d\t%s\n" % (n, kind.get(n, "root"), len(m.imports), m.exports,
                                                 "-" if n == root else "static" if n in static_names else "dynamic"))
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import regsites
        regsites.write(mods, out)
        for n in sorted(k for k in kind if k not in mods):          # met names that are not analysed modules
            fc.write("%s\t%s\t-\t-\t%s\n" % (n, kind[n], "static"))
        for n in sorted(set(files) - set(mods) - set(kind)):
            fc.write("%s\tnot-in-static-closure\t-\t-\t-\n" % n)


if __name__ == "__main__":
    funcfacts.facts_dir_from_argv(sys.argv)
    main(*sys.argv[1:7])
