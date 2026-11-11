#!/usr/bin/env python3
"""Registry read/write sites of a PE module set (L5 declared machine).

Rule (owner): the registry hive holds exactly the keys that the exe and the modules read, found statically from their call sites.
This pass finds, for every call of an imported registry API in every module of the closure, the root HKEY, the key path and the
value name by constant-argument data-flow over the capstone disassembly of the calling function. No name or string grep: an operand
is a constant only when an instruction chain puts it into the argument slot.

Abstract values (per function, forward flow, state joined at forward-jump targets, equal facts stay, others become unknown):
  ("c", n)         integer constant (a root HKEY 0x8000000x is one)
  ("s", text)      relocated address of a C/UTF-16 string in the image (a `push imm32`/`mov r,imm32` carrying a relocation)
  ("h", root, path) handle produced by an earlier open/create whose out pointer was `lea r,[ebp-x]` of this function; root is a
                   HKEY name, path the key path below it; None when not static
  ("a", slot)      address of an ebp slot (out parameter or a stack buffer)
  ("p", k)         parameter k of the function ([ebp+8+4k], not written in the function)
  ("t",)           `this`: ecx at the entry of the function (thiscall), until the function writes ecx
  ("f", base, d)   the 32-bit field at displacement d of the object `base` (a "t", "p" or "f" value)
  ("g", base, d)   the address base+d inside the object `base` (an embedded array or sub-object)
  ("u", reason)    unknown, with the reason that ends up in the table
Wrapper rule: an intra-module function that passes a parameter into a registry call is a wrapper. Its parameters are bound from the
arguments pushed at every direct `call rel32` site that targets it (found by scanning the code for E8 and checked against the
caller's disassembly) and the function is analysed again per caller binding, up to DEPTH levels of wrappers calling wrappers. What
cannot be bound statically stays `dynamic` with the reason.
Parameter rule (closes the set of callers): the binding of a parameter from the direct call sites is complete only when no other
reference to the function exists. `entries` lists every other reference over the whole image: a `jmp rel32` to the entry, a relocated
word in code (address taken by a push/mov), and a relocated word in data. A data word is classified by its run of relocated code
pointers: the run start is the vtable and the position in the run is the slot. A function with any such reference is entered by a path
that the direct-caller binding does not cover, so its rows stay `dynamic` and the reason names the reference.
Field rule: a key or name read from a field of `this` or of a parameter is a ("f", base, d) value. It is resolved only by the set of
all writers of that field for the objects that reach the function; the writers of a field are the stores at displacement d through
objects of one class, and the receivers of a vtable slot are the objects whose vtable word is that vtable. The pass has no typing of
receivers to classes, so the row stays `dynamic` and the reason names the field, the entry references and the two facts that are missing.
Scope: as for pemanifest.py (function boundaries, forward-only control flow, no ebp slot written through a pointer);
stdcall/cdecl argument order (the first argument is the last push); a call through a function pointer other than a register loaded
from the import slot, and a `jmp [slot]` thunk, is reported `dynamic` (reason `indirect`), never skipped; a global cell is not
tracked.

Domain: ROLES is the argument-position table of the registry APIs. It is checked against the Wine export signatures
(`check_roles`: argument count, string-typed subkey/value positions, long key, ptr out) and every imported advapi32/shlwapi
function that is a registry function by the export family must have a row (`gate_domain`): an API added to a module without a
row stops the pass. Output rows: module, site, api, root, subkey, value, status static|dynamic, reason. A wrapper row's site is
`site<-caller`.
"""
import os, re, struct, sys
try:
    import capstone
    from capstone import x86_const as X
except ImportError:  # the role table and gate_domain serve the generator without a disassembler; the pass itself needs it
    capstone = X = None

# kind: open/create (out handle), read, write, enum, delete, info, close
_BASE = {
    "RegOpenKey":      (3, 0, 1, None, 2, "open"),
    "RegOpenKeyEx":    (5, 0, 1, None, 4, "open"),
    "RegCreateKey":    (3, 0, 1, None, 2, "create"),
    "RegCreateKeyEx":  (9, 0, 1, None, 7, "create"),
    "RegQueryValue":   (4, 0, 1, None, None, "read"),
    "RegQueryValueEx": (6, 0, None, 1, None, "read"),
    "RegGetValue":     (7, 0, 1, 2, None, "read"),
    "RegSetValue":     (5, 0, 1, None, None, "write"),
    "RegSetValueEx":   (6, 0, None, 1, None, "write"),
    "RegEnumKey":      (4, 0, None, None, None, "enum"),
    "RegEnumKeyEx":    (8, 0, None, None, None, "enum"),
    "RegEnumValue":    (8, 0, None, None, None, "enum"),
    "RegDeleteKey":    (2, 0, 1, None, None, "delete"),
    "RegDeleteKeyEx":  (4, 0, 1, None, None, "delete"),
    "RegDeleteValue":  (2, 0, None, 1, None, "delete"),
    "RegQueryInfoKey": (12, 0, None, None, None, "info"),
    "SHGetValue":      (6, 0, 1, 2, None, "read"),
    "SHSetValue":      (6, 0, 1, 2, None, "write"),
    "SHDeleteKey":     (2, 0, 1, None, None, "delete"),
    "SHDeleteValue":   (3, 0, 1, 2, None, "delete"),
}
ROLES = {"RegCloseKey": (1, 0, None, None, None, "close")}
for _n, _r in _BASE.items():
    ROLES[_n + "A"] = ROLES[_n + "W"] = _r
# RegDeleteKeyEx has no A-less variant in shlwapi; SH* names take the same suffix rule
HKEY = {0x80000000: "HKEY_CLASSES_ROOT", 0x80000001: "HKEY_CURRENT_USER", 0x80000002: "HKEY_LOCAL_MACHINE",
        0x80000003: "HKEY_USERS", 0x80000004: "HKEY_PERFORMANCE_DATA", 0x80000005: "HKEY_CURRENT_CONFIG",
        0x80000006: "HKEY_DYN_DATA"}
DEPTH = 3
P = {"eax": "eax", "ax": "eax", "al": "eax", "ah": "eax", "ecx": "ecx", "cx": "ecx", "cl": "ecx", "ch": "ecx", "edx": "edx",
     "dx": "edx", "dl": "edx", "dh": "edx", "ebx": "ebx", "bx": "ebx", "bl": "ebx", "bh": "ebx", "esi": "esi", "si": "esi",
     "edi": "edi", "di": "edi"}
UNK = ("u", "unknown")
NOPUSH = ("u", "arguments not pushed in this function")


def spec_exports(specdir, name):
    """{function: [param types]} of the stdcall exports in a Wine .spec file."""
    out = {}
    for line in open(os.path.join(specdir, name), errors="replace"):
        m = re.match(r"\s*(?:@|\d+)\s+(?:stdcall|cdecl)\s+(?:-\w+\s+)*(\w+)\(([^)]*)\)", line)
        if m:
            out[m.group(1)] = m.group(2).split()
    return out


def is_registry_function(name):
    """Registry function by the export family (advapi32 Reg*, shlwapi SH*Value/SH*Key). It only widens the gate."""
    return name.startswith("Reg") or (name.startswith("SH") and re.search(r"(Value|Key)[AW]?$", name) is not None)


def gate_domain(mods):
    return [(n, dll, nm) for n, m in mods.items() for dll, nm, _ in m.imports
            if dll in ("advapi32.dll", "shlwapi.dll") and is_registry_function(nm) and nm not in ROLES]


def check_roles(specdir):
    errs, sig = [], {}
    for f in ("advapi32.spec", "shlwapi.spec"):
        sig.update(spec_exports(specdir, f))
    for nm, (n, key, sub, val, out, kind) in sorted(ROLES.items()):
        t = sig.get(nm)
        if t is None:
            continue  # variants a spec does not export (e.g. a W form) are not importable
        if len(t) != n:
            errs.append("%s: table has %d args, spec %d" % (nm, n, len(t)))
            continue
        for what, k in (("subkey", sub), ("value", val)):
            if k is not None and t[k] not in ("str", "wstr"):
                errs.append("%s: %s arg %d is %s" % (nm, what, k, t[k]))
        if t[key] != "long":
            errs.append("%s: key arg %d is %s" % (nm, key, t[key]))
        if out is not None and t[out] != "ptr":
            errs.append("%s: out arg %d is %s" % (nm, out, t[out]))
    return errs


def joins(a, b):
    """State join at a control-flow merge: (regs, slots, pushes). Equal facts stay, anything else is unknown."""
    if a is None:
        return (dict(b[0]), dict(b[1]), list(b[2]))
    m = lambda x, y: {k: (x[k] if x[k] == y[k] or (x[k][0] == "u" and y[k][0] == "u") else ("u", "join")) for k in x.keys() & y.keys()}
    return (m(a[0], b[0]), m(a[1], b[1]), a[2] if a[2] == b[2] else [])


class Region:
    """Decoded instructions of one function: from `start` up to the next function entry."""

    def __init__(self, mod, start):
        if capstone is None:
            raise SystemExit("regsites: the capstone module is required for the registry-site pass")
        md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        md.detail = True
        o = start - mod.base
        ins = list(md.disasm(mod.img[o:o + 0x10000], start))
        end = None
        for k, i in enumerate(ins[4:], 4):
            if i.bytes[:3] == b"\x55\x8b\xec" or i.bytes[:2] == b"\x8b\xff" or \
                    (i.bytes[:1] == b"\xcc" and k + 1 < len(ins) and ins[k + 1].bytes[:1] != b"\xcc" and ins[k + 1].address % 16 == 0):
                end = i.address
                break
        self.ins = [i for i in ins if end is None or i.address < end]


class Analyzer:
    def __init__(self, mod, apis):
        self.mod, self.rset, self.api, self.regions = mod, set(mod.relocs), apis, {}
        self.callers_cache = {}
        self.covered = set()

    def region(self, start):
        if start not in self.regions:
            self.regions[start] = Region(self.mod, start)
        return self.regions[start]

    def text(self, addr):
        s = self.mod.cstr(addr)
        return None if s is None else (s[2:] if s.startswith("L:") else s)

    def run(self, start, binds=None, track=()):
        """Flow over one function. Returns (calls, tracked): calls = [(site, api, args)] with args a list of values;
        tracked = [(site, target, pushes)] for direct calls to an address in `track`."""
        rset, binds = self.rset, binds or {}
        reg, slots, pushes, saved, dead, fptr = {"ecx": ("t",)}, {}, [], {}, False, {}
        calls, tracked = [], []
        cur = [None]

        def kd(op):
            if op.type == X.X86_OP_MEM and op.mem.index == 0 and op.mem.segment == 0 and op.mem.base == X.X86_REG_EBP:
                return op.mem.disp
            return None

        def rd(op):
            i = cur[0]
            if op.type == X.X86_OP_REG:
                return reg.get(P.get(i.reg_name(op.reg), ""), UNK)
            if op.type == X.X86_OP_IMM:
                return ("c", op.imm & 0xffffffff)
            k = kd(op)
            if k is None:
                if op.type == X.X86_OP_MEM and op.mem.base != 0 and op.mem.index == 0 and op.mem.segment == 0 and op.size == 4:
                    b = reg.get(P.get(i.reg_name(op.mem.base), ""), UNK)
                    if b[0] in ("t", "p", "f"):
                        return ("f", b, op.mem.disp)
                return ("u", "object field" if op.type == X.X86_OP_MEM and op.mem.base != 0 else "global cell")
            if k in slots:
                return slots[k]
            if k >= 8 and (k - 8) % 4 == 0:
                return binds.get((k - 8) // 4, ("p", (k - 8) // 4))
            return UNK

        def setreg(op, v):
            r = P.get(cur[0].reg_name(op.reg))
            if r:
                reg[r] = v if op.size == 4 else UNK
                fptr.pop(r, 0)

        for i in self.region(start).ins:
            cur[0] = i
            ops = i.operands
            if dead or i.address in saved:
                s = saved.pop(i.address, None)
                reg, slots, pushes = (({}, {}, []) if s is None and dead else s if dead else joins(s, (reg, slots, pushes)))
                fptr, dead = {}, False
            if i.mnemonic.startswith("j") or i.mnemonic == "ret":
                t = ops[0].imm if i.mnemonic.startswith("j") and ops and ops[0].type == X.X86_OP_IMM else None
                if t is not None and t > i.address:
                    saved[t] = joins(saved.get(t), (reg, slots, pushes))
                dead = dead or i.mnemonic in ("jmp", "ret")
                continue
            if i.mnemonic == "call":
                a = ops[0]
                f = None
                if a.type == X.X86_OP_MEM and a.mem.base == 0 and a.mem.index == 0:
                    f = self.api.get(a.mem.disp)
                elif a.type == X.X86_OP_REG:
                    fp = fptr.get(P.get(i.reg_name(a.reg), ""))
                    if fp:
                        f = fp[0]
                        self.covered.add(fp[1])  # the load of the import pointer is part of this call
                if f:
                    n = ROLES[f][0]
                    args = [pushes[-1 - k] if len(pushes) > k else NOPUSH for k in range(n)]
                    calls.append((i.address, f, args))
                    pushes = pushes[:-n] if len(pushes) >= n else []
                    ok = ROLES[f][4]
                    if ok is not None and args[ok][0] == "a":
                        slots[args[ok][1]] = ("h",) + self.open_result(f, args)
                else:
                    if a.type == X.X86_OP_IMM and a.imm in track:
                        tracked.append((i.address, a.imm, list(pushes)))
                    pushes = []
                for r in ("eax", "ecx", "edx"):
                    reg[r] = UNK
                    fptr.pop(r, 0)
                continue
            if i.mnemonic == "push":
                a = ops[0]
                if a.type == X.X86_OP_IMM:
                    if (i.address + 1) in rset:
                        t = self.text(a.imm)
                        pushes.append(("s", t) if t is not None else ("u", "non-string pointer"))
                    else:
                        pushes.append(("c", a.imm & 0xffffffff))
                else:
                    pushes.append(rd(a))
                continue
            if i.mnemonic == "mov" and len(ops) == 2:
                d, s = ops
                if s.type == X.X86_OP_MEM and s.mem.base == 0 and s.mem.index == 0 and s.mem.disp in self.api \
                        and d.type == X.X86_OP_REG and P.get(i.reg_name(d.reg)):
                    reg[P[i.reg_name(d.reg)]] = UNK
                    fptr[P[i.reg_name(d.reg)]] = (self.api[s.mem.disp], i.address)
                    continue
                if s.type == X.X86_OP_IMM and (i.address + i.size - 4) in rset:
                    t = self.text(s.imm)
                    v = ("s", t) if t is not None else ("u", "non-string pointer")
                else:
                    v = rd(s)
                if d.type == X.X86_OP_REG:
                    setreg(d, v)
                elif kd(d) is not None:
                    slots[kd(d)] = v if d.size == 4 else UNK
                continue
            if i.mnemonic == "lea" and len(ops) == 2 and ops[0].type == X.X86_OP_REG:
                s = ops[1]
                b = reg.get(P.get(i.reg_name(s.mem.base), ""), UNK) if s.mem.base and s.mem.index == 0 and s.mem.segment == 0 else UNK
                setreg(ops[0], ("a", s.mem.disp) if s.mem.base == X.X86_REG_EBP and s.mem.index == 0 else
                       ("g", b, s.mem.disp) if b[0] in ("t", "p", "f", "g") else ("u", "computed address"))
                continue
            if i.mnemonic == "xor" and len(ops) == 2 and ops[0].type == X.X86_OP_REG and ops[1].type == X.X86_OP_REG \
                    and ops[0].reg == ops[1].reg:
                setreg(ops[0], ("c", 0))
                continue
            if i.mnemonic == "add" and len(ops) == 2 and ops[0].type == X.X86_OP_REG and ops[1].type == X.X86_OP_IMM \
                    and ops[0].size == 4 and reg.get(P.get(i.reg_name(ops[0].reg), ""), UNK)[0] in ("t", "p", "f", "g"):
                v = reg[P[i.reg_name(ops[0].reg)]]  # base + constant: an address inside the object
                setreg(ops[0], ("g", v[1], v[2] + ops[1].imm) if v[0] == "g" else ("g", v, ops[1].imm))
                continue
            for r in i.regs_access()[1]:
                nm = P.get(i.reg_name(r))
                if nm:
                    reg[nm] = UNK
                    fptr.pop(nm, 0)
            for op in ops:
                if op.type == X.X86_OP_MEM and (op.access & 2) and kd(op) is not None:
                    slots[kd(op)] = UNK
        return calls, tracked

    @staticmethod
    def describe(v):
        if v[0] == "t":
            return "this"
        if v[0] == "p":
            return "parameter %d" % v[1]
        if v[0] == "g":
            return "address of offset 0x%x in %s" % (v[2], Analyzer.describe(v[1]))
        return "field 0x%x of %s" % (v[2], Analyzer.describe(v[1]))

    @staticmethod
    def hkey_of(v):
        """(root | None, path | None, reason | None) of a key argument value."""
        if v[0] == "c":
            r = HKEY.get(v[1])
            return (r, "", None) if r else (None, None, "key constant 0x%x is not a root" % v[1])
        if v[0] == "h":
            return v[1], v[2], (None if v[1] is not None and v[2] is not None else "handle from a dynamic open")
        if v[0] in ("p", "t", "f", "g"):
            return None, None, Analyzer.describe(v)
        return None, None, v[1] if v[0] == "u" else "not a key"

    @staticmethod
    def name_of(v):
        """(string | None, reason | None). A null pointer is the empty name (the key itself / the default value)."""
        if v[0] == "s":
            return v[1], None
        if v[0] == "c" and v[1] == 0:
            return "", None
        if v[0] in ("p", "t", "f", "g"):
            return None, Analyzer.describe(v)
        if v[0] == "a":
            return None, "computed buffer"
        return None, v[1] if v[0] == "u" else "not a string"

    def open_result(self, f, args):
        role = ROLES[f]
        root, path, _ = self.hkey_of(args[role[1]])
        sub, _ = self.name_of(args[role[2]])
        if root is None or path is None or sub is None:
            return (root, None)
        return (root, "\\".join(x for x in (path, sub) if x))

    def callers(self, target):
        """Direct `call rel32` sites targeting `target`, each verified as a call instruction in its function's disassembly."""
        if target not in self.callers_cache:
            mod, out = self.mod, []
            for a, n in mod.code:
                o0, k = a - mod.base, a - mod.base
                while True:
                    k = mod.img.find(b"\xe8", k, o0 + n - 4)
                    if k < 0:
                        break
                    va = mod.base + k
                    k += 1
                    if (va + 5 + struct.unpack_from("<i", mod.img, k)[0]) & 0xffffffff != target:
                        continue
                    st = mod.func_start(va)
                    if st is not None and any(i.address == va and i.mnemonic == "call" for i in self.region(st).ins):
                        out.append((va, st))
            self.callers_cache[target] = out
        return self.callers_cache[target]

    def entries(self, target):
        """Every reference to the entry of `target` other than a direct `call rel32`, over the whole image, as text:
        a `jmp rel32`, a relocated word in code, a relocated word in data (vtable and slot by the run of relocated code pointers)."""
        mod, out = self.mod, []
        for a, n in mod.code:
            o0, k = a - mod.base, a - mod.base
            while True:
                k = mod.img.find(b"\xe9", k, o0 + n - 4)
                if k < 0:
                    break
                va = mod.base + k
                k += 1
                if (va + 5 + struct.unpack_from("<i", mod.img, k)[0]) & 0xffffffff == target:
                    out.append("jmp from 0x%08x" % va)
        rs = set(mod.relocs)
        for r in sorted(mod.targets().get(target, [])):
            if mod.in_code(r):
                out.append("address word in code at 0x%08x" % r)
                continue
            b = r
            while b - 4 in rs and mod.in_code(mod.u32(b - 4) or 0):
                b -= 4
            out.append("vtable 0x%08x slot %d (word 0x%08x)" % (b, (r - b) // 4, r))
        return out

    def arg_sets(self, target, depth, chain=()):
        """[(call site, pushes)] of every direct call to `target`; pushes of a caller that is itself a wrapper are resolved
        through its own callers (up to DEPTH)."""
        out = []
        for va, cst in self.callers(target):
            _, tr = self.run(cst, None, {target})
            for cs, _, p in tr:
                if cs != va:
                    continue
                if depth < DEPTH and cst not in chain and any(v[0] == "p" for v in p):
                    for _, ps in self.arg_sets(cst, depth + 1, chain + (target,)):
                        b = {j: (ps[-1 - j] if len(ps) > j else NOPUSH) for j in range(8)}
                        _, tr2 = self.run(cst, b, {target})
                        out += [(va, p2) for cs2, _, p2 in tr2 if cs2 == va]
                else:
                    out.append((va, p))
        return out


def row_of(an, site, f, args, via=None):
    role = ROLES[f]
    root, path, why = an.hkey_of(args[role[1]])
    reasons = [why] if why else []
    sub = ""
    if role[2] is not None:
        sub, r = an.name_of(args[role[2]])
        if sub is None:
            reasons.append("subkey: " + r)
    val = "-"
    if role[3] is not None:
        val, r = an.name_of(args[role[3]])
        if val is None:
            reasons.append("value: " + r)
    elif role[5] in ("read", "write") and role[2] is not None:
        val = ""  # RegQueryValue/RegSetValue: the default value of the subkey
    full = "\\".join(x for x in (path, sub) if x) if root and path is not None and sub is not None else None
    return (site, f, root or "dynamic", full if full is not None else "dynamic", val if val is not None else "dynamic",
            "dynamic" if reasons else "static", "; ".join(reasons), via)


def regsites(mod):
    api = {s: n for _, n, s in mod.imports if n in ROLES}
    if not api:
        return []
    code, _ = mod.refs()
    an = Analyzer(mod, api)
    rows, seen_calls, done = [], set(), set()
    for slot in sorted(api):
        for ref in code.get(slot, []):
            st = mod.func_start(ref)
            if st is None:
                rows.append((ref, api[slot], "dynamic", "dynamic", "dynamic", "dynamic", "no function boundary", None))
                continue
            if st in done:
                continue
            done.add(st)
            calls, _ = an.run(st)
            pend = []
            for site, f, args in calls:
                seen_calls.add(site)
                if ROLES[f][5] == "close":
                    continue
                refs = [args[k] for k in (ROLES[f][1], ROLES[f][2], ROLES[f][3]) if k is not None]
                if any(v[0] in ("p", "t", "f", "g") or (v[0] == "h" and v[2] is None) for v in refs):
                    pend.append((site, f, args))
                else:
                    rows.append(row_of(an, site, f, args))
            if not pend:
                continue
            ent = an.entries(st)
            fld = [(s, f, a) for s, f, a in pend if any(a[k][0] in ("t", "f", "g") for k in (ROLES[f][1], ROLES[f][2], ROLES[f][3]) if k is not None)]
            if ent or fld:
                why = "entered also through " + ", ".join(ent) if ent else "no other entry"
                if fld:
                    why += "; the receivers of the entry are not typed to a class and the writers of the field are not closed"
                else:
                    why += "; the direct callers do not cover these entries"
                rows += [row_of(an, s, f, a, why) for s, f, a in pend]
                continue
            sets = an.arg_sets(st, 0)
            if not sets:
                rows += [row_of(an, s, f, a, "no static callers") for s, f, a in pend]
                continue
            seen = set()
            for va, p in sets:
                bind = {k: (p[-1 - k] if len(p) > k else NOPUSH) for k in range(8)}
                key = repr(sorted(bind.items()))
                if key in seen:
                    continue
                seen.add(key)
                c2, _ = an.run(st, bind)
                want = {(s, f) for s, f, _ in pend}
                rows += [row_of(an, s, f, a, "via 0x%08x" % va) for s, f, a in c2 if (s, f) in want]
    # a code reference to a registry slot that is not a call analysed above (thunk, stored pointer) is reported, not dropped
    for slot in sorted(api):
        for ref in code.get(slot, []):
            st = mod.func_start(ref)
            if st is None or ROLES[api[slot]][5] == "close":
                continue
            ii = next((i for i in an.region(st).ins if i.address <= ref < i.address + i.size), None)
            if ii is None:  # the reference lies outside the located function (a frameless thunk after another function)
                rows.append((ref, api[slot], "dynamic", "dynamic", "dynamic", "dynamic", "indirect (outside function region)", None))
            elif ii.address not in seen_calls and ii.address not in an.covered:
                rows.append((ii.address, api[slot], "dynamic", "dynamic", "dynamic", "dynamic", "indirect (%s)" % ii.mnemonic, None))
    return rows


def write(mods, out):
    bad = gate_domain(mods)
    if bad:
        raise SystemExit("registry import without a ROLES row: %r" % bad)
    n = 0
    with open(os.path.join(out, "regsites.tsv"), "w") as f:
        f.write("module\tsite\tapi\troot\tsubkey\tvalue\tstatus\treason\n")
        for name in sorted(mods):
            for site, api, root, sub, val, st, why, via in sorted(set(regsites(mods[name])), key=lambda r: (r[0], str(r[7]))):
                s = "0x%08x" % site + ("<-" + via.split()[-1] if via and via.startswith("via ") else "")
                f.write("\t".join((name, s, api, root, sub, val, st, "; ".join(x for x in (why, via if via and not via.startswith("via ") else "") if x))) + "\n")
                n += 1
    return n
