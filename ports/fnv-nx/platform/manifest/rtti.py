#!/usr/bin/env python3
"""MSVC RTTI index of a PE module (L5 declared machine; used by regsites.py).

Layout (32-bit MSVC, the exe's ABI):
  vtable[-1]        pointer to the CompleteObjectLocator (COL)
  COL               +0 signature(0)  +4 offset of this vtable inside the complete object  +8 cdOffset
                    +12 pTypeDescriptor of the complete class  +16 pClassHierarchyDescriptor
  TypeDescriptor    +0 vtable of type_info  +4 spare  +8 decorated name (".?AV...@@")
  CHD               +0 signature  +4 attributes  +8 numBaseClasses  +12 pBaseClassArray (relocated pointer to BCD pointers)
  BCD               +0 pTypeDescriptor  +4 numContainedBases  +8 PMD.mdisp  +12 PMD.pdisp  +16 PMD.vdisp  +20 attributes
A vtable is accepted when the word before it is a relocated pointer to a COL whose fields are relocated pointers into the image and
whose type descriptor carries a decorated class name; the slots are the run of relocated words that point into code. Nothing here
reads a name to decide anything: the decorated name is only a label for the reports; classes are identified by their TypeDescriptor
address and bases by their TypeDescriptor addresses.
Assumption (OWNER 3, the compiler is correct, id 1 of the CLAUDE.md list): the compiler emitted these structures as the ABI defines
them; a vtable that fails the checks above is not a class vtable and is reported as `unknown` by `kind_of_word`.
"""
import bisect


class Rtti:
    def __init__(self, mod):
        self.mod = mod
        rs = set(mod.relocs)
        self.col = {}  # col address -> (type descriptor, offset, chd)
        for r in mod.relocs:
            if mod.in_code(r):
                continue
            td = mod.u32(r)
            o = None if td is None else td - mod.base
            if o is None or not (0 < o < len(mod.img) - 12) or mod.img[o + 8:o + 11] != b".?A":
                continue
            c = r - 12
            if c >= mod.base and mod.u32(c) == 0 and (c + 16) in rs and mod.u32(c + 4) is not None:
                self.col[c] = (td, mod.u32(c + 4), mod.u32(c + 16))
        self.vt, self.run = {}, {}  # vtable address -> col ; vtable address -> [slot function addresses]
        for r in mod.relocs:
            if mod.in_code(r):
                continue
            c = mod.u32(r)
            if c in self.col and (r + 4) in rs and mod.in_code(mod.u32(r + 4) or 0):
                v = r + 4
                self.vt[v] = c
                run, k = [], v
                while k in rs and mod.in_code(mod.u32(k) or 0):
                    run.append(mod.u32(k))
                    k += 4
                self.run[v] = run
        self.starts = sorted(self.vt)
        self.by_class = {}  # complete class type descriptor -> {offset: vtable}
        for v, c in self.vt.items():
            td, off, _ = self.col[c]
            self.by_class.setdefault(td, {})[off] = v

    def name(self, td):
        o = td + 8 - self.mod.base
        e = self.mod.img.find(b"\0", o, o + 200)
        return self.mod.img[o:e].decode("latin1") if e > o else "?"

    def complete(self, v):
        """(type descriptor of the complete class, offset of this vtable inside the complete object)."""
        td, off, _ = self.col[self.vt[v]]
        return td, off

    def word_kind(self, r):
        """Classify a relocated data word that holds a code address: ('vtable', v, slot) or ('data', None, None)."""
        i = bisect.bisect_right(self.starts, r) - 1
        if i >= 0:
            v = self.starts[i]
            if r < v + 4 * len(self.run[v]):
                return "vtable", v, (r - v) // 4
        return "data", None, None

    def bases(self, td):
        """[(base type descriptor, mdisp)] of a complete class (its own CHD; the first entry is the class itself)."""
        mod = self.mod
        for c, (t, off, chd) in self.col.items():
            if t == td and off == 0:
                n, arr = mod.u32(chd + 8), mod.u32(chd + 12)
                return [(mod.u32(mod.u32(arr + 4 * k)), mod.u32(mod.u32(arr + 4 * k) + 8)) for k in range(n)]
        return []

    def derived_of(self, base_td):
        """Complete classes whose hierarchy contains `base_td` (including itself)."""
        return sorted({t for t in self.by_class if any(b == base_td for b, _ in self.bases(t))} | {base_td})
