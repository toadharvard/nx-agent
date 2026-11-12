#!/usr/bin/env python3
"""Class-typed closure of the objects that reach a function through a vtable (L5 declared machine; used by regsites.py).

Question answered: for an object of complete class C, what values can the field (or the NUL-terminated string) at displacement D of
the complete object hold? The answer is a closed set only when every pointer to such an object is accounted for; otherwise the
caller is told the exact flow that is not accounted for, and the registry row stays `dynamic`.

Typed value ("o", C, k): a pointer to byte k of a complete object whose class is C (C = type descriptor address; k counts from the
start of the complete object, so a base or embedded sub-object is the same class at a larger offset). The seeds are exact, from the
exe's RTTI (rtti.py):
  vtable entries   a function that is a slot of the vtable of C located at offset o of the object is entered with this = ("o", C, o)
                   (a thunk `add/sub ecx,n; jmp f` adjusts it). Whoever calls a slot holds the object, and every holder is reached from a
                   creation by the flow below, so a typed result returned from a slot goes back to the traced call that entered it;
  constructors     a function that stores the vtable of C (the relocated word in code) into [this+disp] is entered with
                   this = ("o", C, o - disp) (o = the offset field of the COL); it returns this, so its callers get ("o", C, ..) in eax.
Flow (regsites.Analyzer.run in typed mode, the same forward-only control flow as pemanifest.py): the typed value is followed through
registers, ebp slots, `lea`/`add`/`sub` with constants, loads through it (fields), stores through it (writers), a direct `call rel32`
(callee entered with ecx and pushed typed arguments typed), a call through a slot of the vtable of a typed receiver (the callee is that
slot of the vtable of C at the receiver's offset), a typed return value (every direct caller of the function gets the result typed), a
store into a global data cell (every function that references the cell gets loads from it typed; the cell is admitted only if every
reference to it in the image is a plain `[cell]` operand). Everything else that would copy or use the pointer is a LEAK and is recorded
with the instruction: a pointer stored into an object or through an unknown base, passed to an import or to an unresolved indirect call,
combined with an index, copied by an instruction this flow does not follow, a vtable stored through a base that is not `this`, a
constructor that does not return `this`, a typed value returned from a function that has entries other than direct calls.
A class with any leak has no closed writer set: `field` and `string` answer None with the exact leak.

Soundness (the registry of assumptions is verify/ASSUMPTIONS.json; ids are the OWNER numbers of CLAUDE.md, 2026-11-11):
  OWNER 1 (the compiler is correct): the vtable/RTTI layout and the thiscall convention (this in ecx, callee-saved ebx/esi/edi/ebp,
    constructors return this) are what the ABI says; MSVC allocates a complete object, stores its vtable words in its constructor and
    never moves it.
  OWNER 6 (the exe makes no wild accesses, array indices in bounds): a pointer to an object reaches only that object's bytes. It makes
    the `("o", C, k)` coordinates exact, and a byte string at D is written only by stores that cover D (the string rule below).
  OWNER 3 (Win32 as documented): a registry call with a NULL key handle fails with ERROR_INVALID_HANDLE and accesses no key.
  Existing scope of pemanifest.py (REMOVE items, owner: L5): function boundaries, forward-only control flow, complete code ranges,
    no computed code addresses (so every reference to a function is a relocation or a direct rel32).
No new assumption: where the closure needs a fact that none of these gives, the answer is None and names it.
String rule: the string at D is "" when every store that covers byte D writes the constant 0 and the class has no leak; any other
writer (a copy of a longer string, a call that receives the address) leaves it unresolved, so no extent of the array is assumed.
"""
import collections
import struct

from capstone import x86_const as X

MAXJOBS = 6000


class Closure:
    def __init__(self, an, rtti):
        self.an, self.rt, self.mod = an, rtti, an.mod
        self.done, self.jobs, self.todo = set(), set(), []
        self.writers = collections.defaultdict(list)  # class -> [(D, size, site, value)]
        self.leaks = collections.defaultdict(list)  # class -> [(site, text)]
        self.cellvals, self.rets = {}, {}
        self.vcalls = collections.defaultdict(list)  # slot function -> [(site, class, offset, pushes)] reached through typed receivers
        self.origin = collections.defaultdict(set)  # job key -> {(caller job key, call site)} for jobs entered through a typed call
        self.seeded = set()

    # ---- seeds -------------------------------------------------------------------------------------------------------------
    def ensure(self, td):
        if td in self.done:
            return
        self.done.add(td)
        mod, rt = self.mod, self.rt
        for off, v in sorted(rt.by_class.get(td, {}).items()):
            for w in rt.run[v]:
                self.add(w, ("o", td, off), slot=True)
            for r in mod.targets().get(v, []):
                if not mod.in_code(r):
                    continue
                st = mod.func_start(r)
                ins = None if st is None else next((i for i in self.an.region(st).ins if i.address <= r < i.address + i.size), None)
                if ins is None or ins.mnemonic != "mov" or ins.operands[0].type != X.X86_OP_MEM or ins.operands[1].type != X.X86_OP_IMM:
                    self.leaks[td].append((r, "vtable address used by a form that is not `mov [this+d],vtable`"))
                    continue
                self.add(st, ("o", td, off - ins.operands[0].mem.disp), None, None, None, (ins.address, td))
        self.drain()

    def add(self, func, this, binds=None, results=None, cells=None, ctor=None, origin=None, slot=False):
        key = (func, this, tuple(sorted((binds or {}).items())), tuple(sorted((results or {}).items())),
               tuple(sorted((cells or {}).items())), ctor)
        if origin is None:
            if not slot:
                self.seeded.add(key)
        else:
            self.origin[key].add(origin)
        if key not in self.jobs:
            self.jobs.add(key)
            self.todo.append(key)

    def drain(self):
        while self.todo:
            if len(self.jobs) > MAXJOBS:
                self.leaks[None].append((0, "closure exceeds %d jobs" % MAXJOBS))
                self.todo = []
                return
            self.process(self.todo.pop())

    # ---- one job -----------------------------------------------------------------------------------------------------------
    def process(self, key):
        func, this, binds, results, cells, ctor = key
        ev = []
        self.an.run(func, dict(binds), (), this, ev, dict(results), dict(cells))
        stored = False
        for e in ev:
            k = e[0]
            if k == "store":
                _, site, base, disp, size, val = e
                self.writers[base[1]].append((base[2] + disp, size, site, val))
                stored = stored or (ctor is not None and site == ctor[0] and base[1] == ctor[1])
            elif k == "leak":
                self.leaks[e[3][1] if e[3][0] == "o" else None].append((e[1], e[2]))
            elif k == "cellstore":
                self.cellstore(e[1], e[2], e[3])
            elif k == "ret":
                self.ret(key, e[1], e[2])
            elif k == "call":
                self.call(key, *e[1:])
        if ctor is not None and not stored:
            self.leaks[ctor[1]].append((ctor[0], "vtable stored through a base that is not this"))

    def call(self, key, site, tv, ecx, pushes):
        args = {k: pushes[-1 - k] for k in range(len(pushes)) if pushes[-1 - k][0] == "o"}
        t = None
        if tv[0] == "imm":
            t = tv[1]
        elif tv[0] == "f" and tv[1][0] == "f" and tv[1][1][0] == "o" and tv[1][2] == 0:  # [[recv+0]+4k]: virtual call
            _, td, off = tv[1][1]
            v = self.rt.by_class.get(td, {}).get(off)
            if v is not None and tv[2] % 4 == 0 and tv[2] // 4 < len(self.rt.run[v]):
                t = self.rt.run[v][tv[2] // 4]
                self.vcalls[t].append((site, td, off, list(pushes)))
        if ecx[0] != "o" and not args:
            return
        if t is None or not self.mod.in_code(t):
            for v in ([ecx] if ecx[0] == "o" else []) + list(args.values()):
                self.leaks[v[1]].append((site, "typed pointer passed to a call whose target is not static"))
            return
        self.add(t, ecx if ecx[0] == "o" else None, args, origin=(key, site))

    def ret(self, key, site, val):
        if val[0] != "o":
            return
        func = key[0]
        for ck, csite in sorted(self.origin.get(key, ()), key=repr):  # the result goes back to the call that entered with typed arguments
            nk = (ck[0], ck[1], ck[2], tuple(sorted({**dict(ck[3]), csite: val}.items())), ck[4], ck[5])
            if nk not in self.jobs:
                self.jobs.add(nk)
                self.todo.append(nk)
                if ck in self.seeded:
                    self.seeded.add(nk)
                self.origin[nk] |= self.origin.get(ck, set())
        if key not in self.seeded:
            return
        if self.rets.setdefault(func, val) != val:
            self.leaks[val[1]].append((site, "function returns pointers of different objects"))
            return
        for va, cst in self.an.callers(func):
            self.add(cst, None, None, {va: val})
        other = self.an.entries(func)
        if other:
            self.leaks[val[1]].append((site, "typed pointer returned from a function also entered through " + ", ".join(other)))

    def cellstore(self, site, cell, val):
        if val[0] != "o":
            return
        if self.cellvals.setdefault(cell, val) != val:
            self.leaks[val[1]].append((site, "global cell 0x%08x receives pointers of different objects" % cell))
            return
        funcs = set()
        for r in self.mod.targets().get(cell, []):
            st = self.mod.func_start(r) if self.mod.in_code(r) else None
            ins = None if st is None else next((i for i in self.an.region(st).ins if i.address <= r < i.address + i.size), None)
            plain = ins is not None and any(o.type == X.X86_OP_MEM and o.mem.base == 0 and o.mem.index == 0 and o.mem.disp == cell
                                            for o in ins.operands) and ins.mnemonic in ("mov", "cmp", "test")
            if not plain:
                self.leaks[val[1]].append((r, "address of global cell 0x%08x used by a form that is not a plain [cell] operand" % cell))
            else:
                funcs.add(st)
        for f in sorted(funcs):
            self.add(f, None, None, None, {cell: val})

    # ---- queries -----------------------------------------------------------------------------------------------------------
    def blocked(self, td):
        self.ensure(td)
        ls = self.leaks.get(td, []) + self.leaks.get(None, [])
        return "pointer of class %s not accounted: %s at 0x%08x" % (self.rt.name(td)[4:-2], ls[0][1], ls[0][0]) if ls else None

    def field(self, o, d):
        """Alternatives of the 4-byte field at offset d of the typed pointer o: (list of values | None, reason | None)."""
        td, D = o[1], o[2] + d
        why = self.blocked(td)
        if why:
            return None, why
        ws = [w for w in self.writers[td] if w[0] < D + 4 and D < w[0] + w[1]]
        bad = [w for w in ws if w[0] != D or w[1] != 4]
        if bad:
            return None, "writer 0x%08x covers field 0x%x of %s only partly" % (bad[0][2], D, self.rt.name(td)[4:-2])
        if not ws:
            return None, "no writer of field 0x%x of %s" % (D, self.rt.name(td)[4:-2])
        out = []
        for _, _, site, v in ws:
            if v not in out:
                out.append(v)
        for v in out:
            if v[0] in ("u", "p", "t", "f", "g", "o", "a"):
                site = next(w[2] for w in ws if w[3] == v)
                return None, "writer 0x%08x of field 0x%x stores a value that is not bound (%s)" % (
                    site, D, v[1] if v[0] == "u" and isinstance(v[1], str) else "parameter or object value")
        return out, None

    def string(self, o):
        """Content of the NUL-terminated string at typed pointer o: (list of strings | None, reason | None)."""
        td, D = o[1], o[2]
        why = self.blocked(td)
        if why:
            return None, why
        ws = [w for w in self.writers[td] if w[0] <= D < w[0] + w[1]]
        for _, _, site, v in ws:
            if v != ("c", 0):
                return None, "writer 0x%08x of byte 0x%x of %s stores a value that is not the constant 0" % (site, D, self.rt.name(td)[4:-2])
        if not ws:
            return None, "no writer of byte 0x%x of %s" % (D, self.rt.name(td)[4:-2])
        return [""], None
