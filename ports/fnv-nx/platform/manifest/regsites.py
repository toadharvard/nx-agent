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
reference to the function exists. `entries_x` lists every other reference over the whole image: a `jmp rel32` to the entry, a relocated
word in code (address taken by a push/mov), and a relocated word in data, classified by the RTTI index (rtti.py) as a slot of a class
vtable (vtable address and slot) or another data word. A function with any such reference is entered by a path that the direct-caller
binding does not cover, so its parameters stay unbound and the row is `dynamic`, the reason naming the reference.
Receiver rule (fields of `this`): when every entry of the function is a slot of an RTTI vtable and it has no direct caller, `this` is
bound to the complete class and offset of those vtables (COL type descriptor and offset), per vtable. A field read from `this` is the
set of values stored by all writers of that field of that class, found by the typed closure of regclass.py (constructors, vtable slots,
typed calls and returns, global cells; the pointer flow is traced from the constructors, so a pointer that leaves the traced flow makes
the class `unaccounted` and the row `dynamic`, with the instruction named). A string at an address inside the object is "" when every
writer covering its first byte stores the constant 0. A handle opened into a field is the set of opens that store it; a NULL handle is
dropped (Win32: invalid handle, no key accessed). Every other writer value keeps the row `dynamic`: the reason names the writer
instruction (or the parameter / list element that is not bound).
Assumptions: OWNER 1 (compiler correct: MSVC ABI, RTTI layout, thiscall, constructors return this), OWNER 3 (Win32 as documented: NULL
handle, LPCSTR name arguments are inputs), OWNER 6 (no wild accesses: a pointer reaches only its object). The scope below is the existing
one (REMOVE items owned by L5: function boundaries, forward-only control flow, complete code ranges, no computed code addresses). No new
assumption; the ids in verify/ASSUMPTIONS.json are not yet on the trunk, so the OWNER numbers of CLAUDE.md are cited.
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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rtti import Rtti
from regclass import Closure
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
    def why(v):
        return Analyzer.describe(v) if v[0] in ("t", "p", "f", "g", "o", "H") else "string %r" % v[1] if v[0] == "s" else "constant" if v[0] == "c" else \
            "buffer of the function" if v[0] == "a" else v[1] if v[0] == "u" else v[0]
    m = lambda x, y: {k: (x[k] if x[k] == y[k] or (x[k][0] == "u" and y[k][0] == "u") else
                          ("u", "join of %s and %s" % (why(x[k]), why(y[k])))) for k in x.keys() & y.keys()}
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
        self._rtti = self._cl = None

    def region(self, start):
        if start not in self.regions:
            self.regions[start] = Region(self.mod, start)
        return self.regions[start]

    def text(self, addr):
        s = self.mod.cstr(addr)
        return None if s is None else (s[2:] if s.startswith("L:") else s)

    def run(self, start, binds=None, track=(), this_bind=None, ev=None, results=None, cells=None):
        """Flow over one function. Returns (calls, tracked): calls = [(site, api, args)] with args a list of values;
        tracked = [(site, target, pushes)] for direct calls to an address in `track`.
        Typed mode (the class closure of regclass.py): `this_bind` is the value of ecx at the entry (("o", class, offset)), `binds`
        may hold typed parameter values, `results` = {call site: value of eax after the call}, `cells` = {data cell: value} read by
        a plain `mov r,[cell]`. `ev` collects events: ("store", site, base, disp, size, value), ("cellstore", site, cell, value),
        ("call", site, target, ecx, pushes), ("ret", site, eax), ("leak", site, text, pointer). A typed value ("o", class, offset) is
        a pointer to byte `offset` of the complete object of that class; every instruction that would copy or use such a pointer in
        a way this flow does not follow is reported as a leak, never dropped."""
        rset, binds = self.rset, binds or {}
        results, cells = results or {}, cells or {}
        reg, slots, pushes, saved, dead, fptr = {"ecx": this_bind or ("t",)}, {}, [], {}, False, {}
        calls, tracked = [], []
        cur = [None]
        typed = lambda v: v[0] == "o"

        def leak(i, why, v):
            if ev is not None:
                ev.append(("leak", i.address, why, v))

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
                    if b[0] in ("t", "p", "f", "o"):
                        return ("f", b, op.mem.disp)
                if op.type == X.X86_OP_MEM and op.mem.base == 0 and op.mem.index == 0 and op.size == 4 and op.mem.disp in cells:
                    return cells[op.mem.disp]
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
                if ev is not None and i.mnemonic == "ret":
                    ev.append(("ret", i.address, reg.get("eax", UNK)))
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
                        slots[args[ok][1]] = ("H", f, tuple(args)) if any(a[0] in ("o", "f") for a in args) else \
                            ("h",) + self.open_result(f, args)
                    if ev is not None:
                        for k, v in enumerate(args):
                            if typed(v) and k == ok:  # the out handle is written into the object: a writer of that field
                                ev.append(("store", i.address, v, 0, 4, ("H", f, tuple(args))))
                            elif typed(v) and k not in (ROLES[f][2], ROLES[f][3]):  # name arguments are LPCSTR inputs (Win32 as documented)
                                leak(i, "pointer passed to %s (argument %d)" % (f, k), v)
                else:
                    if a.type == X.X86_OP_IMM and a.imm in track:
                        tracked.append((i.address, a.imm, list(pushes)))
                    if ev is not None:
                        tv = ("imm", a.imm) if a.type == X.X86_OP_IMM else rd(a)
                        ev.append(("call", i.address, tv, reg.get("ecx", UNK), list(pushes)))
                    pushes = []
                for r in ("eax", "ecx", "edx"):
                    reg[r] = UNK
                    fptr.pop(r, 0)
                if i.address in results:
                    reg["eax"] = results[i.address]
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
                elif ev is not None and d.type == X.X86_OP_MEM:
                    b = reg.get(P.get(i.reg_name(d.mem.base), ""), UNK) if d.mem.base and d.mem.index == 0 and d.mem.segment == 0 else UNK
                    if typed(b):
                        ev.append(("store", i.address, b, d.mem.disp, d.size, v if d.size == 4 else (v if v[0] == "c" else UNK)))
                        if typed(v):
                            leak(i, "pointer stored into an object", v)
                    elif d.mem.base == 0 and d.mem.index == 0 and d.mem.segment == 0:
                        ev.append(("cellstore", i.address, d.mem.disp, v))
                    elif typed(v):
                        leak(i, "pointer stored through an untyped base", v)
                    if s.type == X.X86_OP_REG and typed(v) and d.size != 4:
                        leak(i, "pointer partially stored", v)
                continue
            if i.mnemonic == "lea" and len(ops) == 2 and ops[0].type == X.X86_OP_REG:
                s = ops[1]
                b = reg.get(P.get(i.reg_name(s.mem.base), ""), UNK) if s.mem.base and s.mem.index == 0 and s.mem.segment == 0 else UNK
                if typed(b) or (s.mem.index and typed(reg.get(P.get(i.reg_name(s.mem.index), ""), UNK))):
                    if typed(b) and not s.mem.index:
                        setreg(ops[0], ("o", b[1], b[2] + s.mem.disp))
                    else:
                        leak(i, "indexed address of an object", b if typed(b) else UNK)
                        setreg(ops[0], UNK)
                    continue
                setreg(ops[0], ("a", s.mem.disp) if s.mem.base == X.X86_REG_EBP and s.mem.index == 0 else
                       ("g", b, s.mem.disp) if b[0] in ("t", "p", "f", "g") else ("u", "computed address"))
                continue
            if i.mnemonic == "xor" and len(ops) == 2 and ops[0].type == X.X86_OP_REG and ops[1].type == X.X86_OP_REG \
                    and ops[0].reg == ops[1].reg:
                setreg(ops[0], ("c", 0))
                continue
            if i.mnemonic in ("add", "sub") and len(ops) == 2 and ops[0].type == X.X86_OP_REG and ops[1].type == X.X86_OP_IMM \
                    and ops[0].size == 4 and typed(reg.get(P.get(i.reg_name(ops[0].reg), ""), UNK)):
                v = reg[P[i.reg_name(ops[0].reg)]]
                setreg(ops[0], ("o", v[1], v[2] + (ops[1].imm if i.mnemonic == "add" else -ops[1].imm)))
                continue
            if i.mnemonic == "add" and len(ops) == 2 and ops[0].type == X.X86_OP_REG and ops[1].type == X.X86_OP_IMM \
                    and ops[0].size == 4 and reg.get(P.get(i.reg_name(ops[0].reg), ""), UNK)[0] in ("t", "p", "f", "g"):
                v = reg[P[i.reg_name(ops[0].reg)]]  # base + constant: an address inside the object
                setreg(ops[0], ("g", v[1], v[2] + ops[1].imm) if v[0] == "g" else ("g", v, ops[1].imm))
                continue
            if ev is not None:
                for op in ops:
                    if op.type == X.X86_OP_REG and (op.access & 1) and i.mnemonic not in ("cmp", "test"):
                        v = reg.get(P.get(i.reg_name(op.reg), ""), UNK)
                        if typed(v):
                            leak(i, "%s %s copies or combines a pointer" % (i.mnemonic, i.op_str), v)
                    elif op.type == X.X86_OP_MEM:
                        b = reg.get(P.get(i.reg_name(op.mem.base), ""), UNK) if op.mem.base else UNK
                        if op.mem.index and typed(reg.get(P.get(i.reg_name(op.mem.index), ""), UNK)):
                            leak(i, "%s %s indexes with a pointer" % (i.mnemonic, i.op_str), UNK)
                        if typed(b) and (op.access & 2):
                            ev.append(("store", i.address, b, op.mem.disp, op.size, ("u", "%s writes the field" % i.mnemonic)))
                if i.mnemonic.split()[-1].startswith(("movs", "stos", "cmps", "scas", "lods", "rep")):
                    for r in ("esi", "edi"):
                        if typed(reg.get(r, UNK)):
                            leak(i, "string instruction through a pointer", reg[r])
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
        if v[0] == "o":
            return "object of class 0x%08x at offset 0x%x" % (v[1], v[2])
        if v[0] == "H":
            return "handle opened by %s" % v[1]
        if v[0] in ("c", "s", "a", "u"):
            return "%s value" % v[0]
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

    def entries_x(self, target):
        """Every reference to the entry of `target` other than a direct `call rel32`, over the whole image, as dicts
        {kind: jmp|code|vtable|data, text, at, vtable, slot}: a `jmp rel32`, a relocated word in code (address taken), a relocated word
        in data (a slot of an RTTI vtable, or another data word)."""
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
                    out.append({"kind": "jmp", "at": va, "text": "jmp from 0x%08x" % va})
        for r in sorted(mod.targets().get(target, [])):
            if mod.in_code(r):
                out.append({"kind": "code", "at": r, "text": "address word in code at 0x%08x" % r})
                continue
            kind, v, slot = self.rtti().word_kind(r)
            if kind == "vtable":
                out.append({"kind": "vtable", "at": r, "vtable": v, "slot": slot,
                            "text": "vtable 0x%08x slot %d (word 0x%08x)" % (v, slot, r)})
            else:
                out.append({"kind": "data", "at": r, "text": "data word 0x%08x" % r})
        return out

    def entries(self, target):
        return [e["text"] for e in self.entries_x(target)]

    def rtti(self):
        if self._rtti is None:
            self._rtti = Rtti(self.mod)
        return self._rtti

    def closure(self):
        if self._cl is None:
            self._cl = Closure(self, self.rtti())
        return self._cl

    def alts(self, v, depth=0):
        """Alternatives of a value: (list of plain values | None, reason | None). A field of a typed object is the set of the values its
        writers store (regclass.Closure.field); a parameter, a field of `this` of unknown class stays unbound."""
        if v[0] == "f" and v[1][0] == "o":
            return self.closure().field(v[1], v[2])
        if v[0] in ("p", "t", "f", "g"):
            return None, Analyzer.describe(v)
        if v[0] == "u":
            return None, v[1]
        return [v], None

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


def key_alts(an, v, depth=0):
    """[(root, path)] of a key argument | (None, reason). A NULL handle is dropped: Win32 documents ERROR_INVALID_HANDLE and no key is
    accessed (OWNER 3). A handle opened by a call whose arguments were fields is ("H", api, args) and is resolved here, lazily."""
    a, why = an.alts(v)
    if a is None:
        return None, why
    out = []
    for x in a:
        if x[0] == "c":
            if x[1] == 0:
                continue
            r = HKEY.get(x[1])
            if r is None:
                return None, "key constant 0x%x is not a root" % x[1]
            out.append((r, ""))
        elif x[0] == "h":
            if x[1] is None or x[2] is None:
                return None, "handle from a dynamic open"
            out.append((x[1], x[2]))
        elif x[0] == "H":
            if depth > 4:
                return None, "handle chain deeper than 4 opens"
            role = ROLES[x[1]]
            roots, why = key_alts(an, x[2][role[1]], depth + 1)
            if roots is None:
                return None, why
            subs, why = name_alts(an, x[2][role[2]]) if role[2] is not None else ([""], None)
            if subs is None:
                return None, "subkey: " + why
            out += [(r, "\\".join(p for p in (pa, s) if p)) for r, pa in roots for s in subs]
        else:
            return None, "not a key" if x[0] != "u" else x[1]
    if not out:
        return None, "the handle is never opened (only the NULL handle is stored)"
    return list(dict.fromkeys(out)), None


def name_alts(an, v):
    """[string] of a name argument | (None, reason). A null pointer is the empty name (the key itself / the default value)."""
    a, why = an.alts(v)
    if a is None:
        return None, why
    out = []
    for x in a:
        if x[0] == "s":
            out.append(x[1])
        elif x[0] == "c" and x[1] == 0:
            out.append("")
        elif x[0] == "o":
            ss, why = an.closure().string(x)
            if ss is None:
                return None, why
            out += ss
        elif x[0] == "a":
            return None, "computed buffer"
        else:
            return None, x[1] if x[0] == "u" else "not a string"
    return list(dict.fromkeys(out)), None


def rows_of(an, site, f, args, via=None):
    """Rows of one registry call: one `static` row per (root, key path, value name) of the full value set, or one `dynamic` row."""
    role = ROLES[f]
    roots, why = key_alts(an, args[role[1]])
    reasons = [why] if why else []
    subs = [""]
    if role[2] is not None:
        subs, r = name_alts(an, args[role[2]])
        if subs is None:
            reasons.append("subkey: " + r)
    vals = ["-"]
    if role[3] is not None:
        vals, r = name_alts(an, args[role[3]])
        if vals is None:
            reasons.append("value: " + r)
    elif role[5] in ("read", "write") and role[2] is not None:
        vals = [""]  # RegQueryValue/RegSetValue: the default value of the subkey
    if reasons:
        one = lambda xs: xs[0] if xs and len(set(xs)) == 1 else "dynamic"
        r0 = one([x[0] for x in roots]) if roots else "dynamic"
        p0 = "dynamic"
        if roots and len({x[1] for x in roots}) == 1 and subs:
            p0 = "\\".join(p for p in (roots[0][1], subs[0]) if p) if len(subs) == 1 else "dynamic"
        return [(site, f, r0, p0, one(vals) if vals else "dynamic", "dynamic", "; ".join(reasons), via)]
    return [(site, f, r, "\\".join(p for p in (pa, s) if p), v, "static", "", via)
            for r, pa in roots for s in subs for v in vals]


def row_of(an, site, f, args, via=None):
    return rows_of(an, site, f, args, via)[0]


def regsites(mod, stats=None):
    """Rows of every registry call of `mod`. `stats`, when given, receives one dict per function that passes a parameter, `this` or a
    field into a registry call (a wrapper): {func, sites, direct, entries, resolved}."""
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
                if any(v[0] in ("p", "t", "f", "g", "H") or (v[0] == "h" and v[2] is None) for v in refs):
                    pend.append((site, f, args))
                else:
                    rows += rows_of(an, site, f, args)
            if not pend:
                continue
            ent, direct = an.entries_x(st), an.callers(st)
            this_dep = lambda v: v[0] in ("t", "f", "g", "H")
            fld = [(s, f, a) for s, f, a in pend if any(this_dep(a[k]) for k in (ROLES[f][1], ROLES[f][2], ROLES[f][3]) if k is not None)]
            par = [x for x in pend if x not in fld]
            got = []
            if fld:
                vts = [e for e in ent if e["kind"] == "vtable"]
                others = [e["text"] for e in ent if e["kind"] != "vtable"] + ["call from 0x%08x" % va for va, _ in direct]
                if not vts:
                    why = "this: the function is not a vtable slot, so the class of the receiver is not bound"
                elif others:
                    why = "this: entered also through " + ", ".join(others) + ", whose receiver is not typed"
                else:
                    why = None
                if why:
                    got += [row for s, f, a in fld for row in rows_of(an, s, f, a, why)]
                else:
                    want = {(s, f) for s, f, _ in fld}
                    for td, off in sorted({an.rtti().complete(e["vtable"]) for e in vts}):
                        c2, _ = an.run(st, None, (), ("o", td, off))
                        n = sum(len(an.closure().vcalls[w]) for w in {st})
                        via = "receiver %s at offset %d; %d virtual call sites of this slot are in the closure of the class, the " \
                              "arguments of calls through other receivers are not bound" % (an.rtti().name(td)[4:-2], off, n)
                        got += [row for s, f, a in c2 if (s, f) in want for row in rows_of(an, s, f, a, via)]
            if par:
                if ent:
                    why = "entered also through " + ", ".join(e["text"] for e in ent) + "; the direct callers do not cover these entries"
                    got += [row for s, f, a in par for row in rows_of(an, s, f, a, why)]
                else:
                    sets = an.arg_sets(st, 0)
                    if not sets:
                        got += [row for s, f, a in par for row in rows_of(an, s, f, a, "no static callers")]
                    seen = set()
                    for va, p in sets:
                        bind = {k: (p[-1 - k] if len(p) > k else NOPUSH) for k in range(8)}
                        key = repr(sorted(bind.items()))
                        if key in seen:
                            continue
                        seen.add(key)
                        c2, _ = an.run(st, bind)
                        want = {(s, f) for s, f, _ in par}
                        got += [row for s, f, a in c2 if (s, f) in want for row in rows_of(an, s, f, a, "via 0x%08x" % va)]
            rows += got
            if stats is not None:
                stats.append({"func": st, "sites": len(pend), "direct": len(direct), "entries": [e["kind"] for e in ent],
                              "resolved": bool(got) and all(r[5] == "static" for r in got)})
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
