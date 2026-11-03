"""vslots.py - GCC vtable slots bound by original address through the MSVC 2008 vftables (D12; docs/LINK.md,
"Vtable slots").

GCC emits vtables whose slots name virtual functions the decomp never names at an address (`TESForm::Unk_49`): MSVC
vftables live in the image's .rdata, which units.json does not describe. The pristine decomp compiled with MSVC 2008
(tools/equiv, the exe's code byte for byte) holds those vftables: every object that needs one emits `??_7C@@6B@`
(and `??_7C@@6B<B>@@@`, ``C::`vftable'{for `B'}``) as a COMDAT with its CompleteObjectLocator, one relocation per
slot naming the MSVC-decorated function. units.json binds the vftable names a unit's code uses to the image address,
so every unit that emits a vftable at the same image address is one view of the same table, and wherever one view's
slot names something units.json binds (a definition, a reference, a `FUN_<addr>` name) the slot's original target
is known for all of them.

Steps:

* `Coff(path)` - i386 COFF reader (sections, symbols with aux records, weak externals, relocations);
  `Coff.vftables()` - each `??_7` with its slots and its COL (vfptr offset in the complete object, class name).
* `MsvcIndex` - every unit's vftables (`u<index>.obj`, index = position in units.json); each slot target resolved
  through units.json (`def`, `ref`, `name-addr`, `gdef`, `gref`; adjustor thunks to their function; `??_E` to `??_G`;
  `__purecall`); vftables joined by image address (`table(address)`, per slot the agreeing addresses, the
  disagreements).
* `itanium_to_msvc(...)` - GCC vtable group of a class (from the ELF object) against the MSVC vftables of the same
  class: subobject offsets pair a GCC (secondary) vtable with the MSVC vftable of the same vfptr; inside one, slots
  pair by signature (overloads: MSVC groups them in reverse declaration order), the virtual destructor's D1/D0 pair
  with the one `??_G`/`??_E` slot, the rest by position.
* `Binder` - for `fnvlink.py resolve` (hook `classify`): an undefined slot symbol of a GCC object -> its original
  address (the native definition there, a pending unit's address, `??_G` for D0/D1).
* `validate(...)` - checks of the slot model against the matched x86 code (thiscall `ret N` of the bound targets vs
  the declared arity; `this`-based `call [reg+off]` sites).

Everything this reads or writes is derived from the decomp or MSVC output: local only, never committed.
"""
import collections
import os
import re
import struct

# ---------------------------------------------------------------------------------------------------- COFF

IMAGE_REL_I386_DIR32 = 6
IMAGE_REL_I386_REL32 = 0x14
IMAGE_SCN_LNK_COMDAT = 0x1000
SYM_CLASS_EXTERNAL, SYM_CLASS_STATIC, SYM_CLASS_WEAK_EXTERNAL = 2, 3, 105
BIGOBJ_CLSID = bytes.fromhex('c7a1bad1eebaa94baf20faf66aa4dcb8')

CoffSym = collections.namedtuple('CoffSym', 'name value secnum type storage')   # secnum: 0 undef, -1 abs, -2 debug
Section = collections.namedtuple('Section', 'name size data_off reloc_off nreloc flags')


class Coff:
    """i386 COFF object (plain or /bigobj). syms: {raw symbol index: CoffSym} (aux records skipped); defs: name ->
    index of its definition (external over static); weak: weak external name -> default symbol name; comdat_sel:
    section number -> (selection, associated section number)."""

    def __init__(self, path):
        self.path = path
        d = self.data = open(path, 'rb').read()
        if d[:4] == b'\0\0\xff\xff' and d[12:28] == BIGOBJ_CLSID:
            self.machine, = struct.unpack_from('<H', d, 6)
            nsec, symptr, nsym = struct.unpack_from('<III', d, 44)
            sec_off, symsize, secfmt = 56, 20, '<IiHBB'
        else:
            self.machine, nsec, _, symptr, nsym, opt, _ = struct.unpack_from('<HHIIIHH', d, 0)
            sec_off, symsize, secfmt = 20 + opt, 18, '<IhHBB'
        strtab = symptr + symsize * nsym

        def sname(raw):   # symbol name: inline (8 bytes) or 0, string table offset
            if raw[:4] == b'\0\0\0\0':
                return self._cstr(strtab + struct.unpack_from('<I', raw, 4)[0])
            return raw.rstrip(b'\0').decode('latin-1')
        self.secs = [None]
        for i in range(nsec):
            o = sec_off + 40 * i
            raw = d[o:o + 8].rstrip(b'\0')
            name = self._cstr(strtab + int(raw[1:])) if raw[:1] == b'/' and raw[1:].isdigit() else raw.decode('latin-1')
            _, _, size, data_off, reloc_off, _, nreloc, _, flags = struct.unpack_from('<IIIIIIHHI', d, o + 8)
            self.secs.append(Section(name, size, data_off, reloc_off, nreloc, flags))
        self.syms, self.defs, self.weak, self.comdat_sel = {}, {}, {}, {}
        i = 0
        while i < nsym:
            o = symptr + symsize * i
            raw = d[o:o + 8]
            value, secnum, typ, storage, naux = struct.unpack_from(secfmt, d, o + 8)
            name = sname(raw)
            self.syms[i] = CoffSym(name, value, secnum, typ, storage)
            if naux and storage == SYM_CLASS_STATIC and value == 0 and 0 < secnum < len(self.secs) and \
                    name == self.secs[secnum].name:
                a = o + symsize   # section definition aux: length, nreloc, nline, checksum, number, selection
                _, _, _, _, num, sel = struct.unpack_from('<IHHIHB', d, a)
                if symsize == 20:
                    num |= struct.unpack_from('<H', d, a + 16)[0] << 16
                self.comdat_sel[secnum] = (sel, num)
            elif naux and storage == SYM_CLASS_WEAK_EXTERNAL:
                tag, = struct.unpack_from('<I', d, o + symsize)
                self.weak[name] = tag   # resolved to a name below (the tag may come later in the table)
            if secnum > 0 and (name not in self.defs or storage == SYM_CLASS_EXTERNAL):
                self.defs[name] = i
            i += 1 + naux
        self.weak = {n: self.syms[t].name for n, t in self.weak.items() if t in self.syms}
        self._rel = {}

    def _cstr(self, off):
        return self.data[off:self.data.index(b'\0', off)].decode('latin-1')

    def relocs(self, secnum):
        """{offset: (symbol name, type)} of a section."""
        if secnum not in self._rel:
            s = self.secs[secnum]
            out = {}
            for k in range(s.nreloc):
                off, si, typ = struct.unpack_from('<IIH', self.data, s.reloc_off + 10 * k)
                sym = self.syms.get(si)
                out[off] = (sym.name if sym else '?%d' % si, typ)
            self._rel[secnum] = out
        return self._rel[secnum]

    def section_bytes(self, secnum):
        s = self.secs[secnum]
        return self.data[s.data_off:s.data_off + s.size] if s.data_off else bytes(s.size)

    def symbols_in(self, secnum):
        return sorted((s.value, s.name) for s in self.syms.values() if s.secnum == secnum and s.storage in
                      (SYM_CLASS_EXTERNAL, SYM_CLASS_STATIC) and s.name != self.secs[secnum].name)

    def word_at(self, name, k):
        """(relocation target or None, dword) of the k-th dword of a defined symbol."""
        s = self.syms[self.defs[name]]
        off = s.value + 4 * k
        r = self.relocs(s.secnum).get(off)
        w = struct.unpack_from('<I', self.section_bytes(s.secnum), off)[0] if off + 4 <= self.secs[s.secnum].size \
            else None
        return (r[0] if r and r[1] == IMAGE_REL_I386_DIR32 else None), w

    def vftables(self):
        """{??_7 name: Vft}. A vftable runs from its symbol to the next symbol of its section or the section's end;
        the dword before it is its CompleteObjectLocator (`??_R4`) when the class has RTTI."""
        out = {}
        for name, i in self.defs.items():
            if not name.startswith('??_7'):
                continue
            s = self.syms[i]
            sec = self.secs[s.secnum]
            later = [v for v, n in self.symbols_in(s.secnum) if v > s.value]
            end = min(later) if later else sec.size
            rel = self.relocs(s.secnum)
            slots = []
            for off in range(s.value, end - 3, 4):
                r = rel.get(off)
                slots.append(r[0] if r and r[1] == IMAGE_REL_I386_DIR32 else None)
            col = None
            if s.value >= 4:
                r = rel.get(s.value - 4)
                if r and r[0].startswith('??_R4') and r[0] in self.defs:
                    col = self.col(r[0])
            out[name] = Vft(name, slots, col)
        return out

    def col(self, name):
        """CompleteObjectLocator: signature, offset (of this vfptr in the complete object), cdOffset, type
        descriptor (`??_R0...`), class hierarchy descriptor."""
        words = [self.word_at(name, k) for k in range(5)]
        return Col(words[1][1], words[2][1], words[3][0], words[4][0])


Vft = collections.namedtuple('Vft', 'name slots col')
Col = collections.namedtuple('Col', 'offset cdoffset td chd')


def adjustor_target(coff, name):
    """Adjustor thunk (`?f@C@@W<n>AE...`, ``[thunk]: ... `adjustor{n}'``: `sub ecx, n` / `add ecx, -n`, then
    `jmp f`) defined in the object -> (target symbol, this adjustment) or None."""
    i = coff.defs.get(name)
    if i is None:
        return None
    s = coff.syms[i]
    code = coff.section_bytes(s.secnum)[s.value:s.value + 16]
    rel = coff.relocs(s.secnum)
    adj, k = 0, 0
    if code[:2] == b'\x83\xe9':
        adj, k = code[2], 3
    elif code[:2] == b'\x81\xe9':
        adj, k = struct.unpack_from('<I', code, 2)[0], 6
    elif code[:2] == b'\x83\xc1':
        adj, k = -struct.unpack_from('<b', code, 2)[0], 3
    elif code[:2] == b'\x81\xc1':
        adj, k = -struct.unpack_from('<i', code, 2)[0], 6
    else:
        return None
    if code[k:k + 1] != b'\xe9':
        return None
    r = rel.get(s.value + k + 1)
    return (r[0], adj) if r and r[1] == IMAGE_REL_I386_REL32 else None


# ---------------------------------------------------------------------------------------------------- MSVC index

PURE = '__purecall'
# Ghidra-style names that carry the function's address (fnvlink.ADDR_IN_NAME), as the identifier of an MSVC name
MS_ADDR_NAME = re.compile(r'^\?(?:FUN|UNK|thunk_FUN)_([0-9a-f]{8})@')
VFT_TEXT = re.compile(r"^const (.*)::`vftable'(?:\{for `(.*)'\})?$")
TIERS = ('def', 'ref', 'name-addr', 'gdef', 'gref')     # strongest first
View = collections.namedtuple('View', 'unit name slots col cls base targets')
# targets: per slot (address or None, how, resolved MSVC name, adjustment)
Slot = collections.namedtuple('Slot', 'addr how name adj')


class MsvcIndex:
    """The vftables of every pristine MSVC object, each slot resolved through units.json, joined by image address.

    units: units.json 'units' (list, the object of entry i is objdir/u<i>.obj); demangle(names) -> {name: text}
    (llvm-undname). views: [View]; by_addr: {image vftable address: [view index]}; by_unit: {unit index: [view
    index]}."""

    def __init__(self, units, objdir, demangle=None):
        self.units = units
        self.defs = [{} for _ in units]
        self.refs = [{} for _ in units]
        self.gdef = collections.defaultdict(set)
        self.gref = collections.defaultdict(set)
        for i, u in enumerate(units):
            for f in u['functions']:
                a = int(f['address'], 16)
                for n in [f['symbol']] + list(f.get('fold_aliases', [])):
                    self.defs[i][n] = a
                    self.gdef[n].add(a)
            for n, a in u['symbols'].items():
                self.refs[i][n] = int(a, 16)
                self.gref[n].add(int(a, 16))
        self.views, self.by_unit, self.by_addr, self.view_addr = [], collections.defaultdict(list), \
            collections.defaultdict(list), {}
        self.objects = 0
        raw = []
        for i in range(len(units)):
            p = os.path.join(objdir, 'u%d.obj' % i)
            if not os.path.exists(p):
                continue
            self.objects += 1
            c = Coff(p)
            for name, v in sorted(c.vftables().items()):
                tg = []
                for s in v.slots:
                    t, adj = s, 0
                    if t in c.weak:            # ??_E (vector deleting dtor) -> its default, ??_G
                        t = c.weak[t]
                    th = adjustor_target(c, t) if t else None
                    if th:
                        t, adj = th
                        if t in c.weak:
                            t = c.weak[t]
                    tg.append((t, adj))
                td = c.syms[c.defs[v.col.td]].name if v.col and v.col.td in c.defs else (v.col.td if v.col else None)
                raw.append((i, name, v.slots, v.col, td, tg))
        self.text = demangle(sorted({n for r in raw for n in [r[1]] + [t for t, _ in r[5] if t]}
                                    | {n for r in raw for n in r[2] if n})) if demangle else {}
        for i, name, slots, col, td, tg in raw:
            m = VFT_TEXT.match(self.text.get(name) or '')
            cls, base = (m.group(1), m.group(2)) if m else (None, None)
            targets = [Slot(*self.resolve(i, t), t, adj) if t else Slot(None, 'none', None, 0) for t, adj in tg]
            k = len(self.views)
            self.views.append(View(i, name, slots, col, cls, base, targets))
            self.by_unit[i].append(k)
            a, how = self.vftable_address(i, name)
            if a is not None:
                self.view_addr[k] = (a, how)
                self.by_addr[a].append(k)
        self._tables = {}

    def resolve(self, i, name):
        """MSVC function name as unit i sees it -> (original address or None, how)."""
        if name == PURE:
            return None, 'pure'
        if name in self.defs[i]:
            return self.defs[i][name], 'def'
        if name in self.refs[i]:
            return self.refs[i][name], 'ref'
        m = MS_ADDR_NAME.match(name)
        if m:
            return int(m.group(1), 16), 'name-addr'
        for g, how in ((self.gdef, 'gdef'), (self.gref, 'gref')):
            if len(g.get(name, ())) == 1:
                return next(iter(g[name])), how
            if g.get(name):
                return None, how + '-ambiguous'
        return None, 'unbound'

    def vftable_address(self, i, name):
        if name in self.refs[i]:
            return self.refs[i][name], 'unit'
        if len(self.gref.get(name, ())) == 1:
            return next(iter(self.gref[name])), 'global'
        return None, ('ambiguous' if self.gref.get(name) else 'unbound')

    def table(self, a):
        """Image vftable at address a, joined over its views: [(address or None, [(view, Slot)] supporting it,
        {address: [(view, Slot)]} of all candidates)] per slot. A slot's address is the best-ranked candidate:
        a function of the view's own class (an override declared by the class) before an inherited one, then by
        tier (TIERS), then by the number of views naming it; candidates of equal rank that disagree leave it
        None (a disagreement)."""
        if a in self._tables:
            return self._tables[a]
        vs = self.by_addr.get(a, [])
        n = max((len(self.views[k].slots) for k in vs), default=0)
        out = []
        for j in range(n):
            cand = collections.defaultdict(list)
            for k in vs:
                v = self.views[k]
                if j < len(v.targets) and v.targets[j].addr is not None:
                    cand[v.targets[j].addr].append((k, v.targets[j]))
            best = None
            if cand:
                def rank(x):
                    sup = cand[x]
                    own = any(self.owns(k, s.name) for k, s in sup)
                    tier = min(TIERS.index(s.how) for _, s in sup)
                    return (not own, tier, -len(sup))
                ranked = sorted(cand, key=rank)
                if len(ranked) == 1 or rank(ranked[0])[:2] != rank(ranked[1])[:2]:
                    best = ranked[0]
            out.append((best, cand[best] if best is not None else [], dict(cand)))
        self._tables[a] = out
        return out

    def owns(self, k, name):
        """The function `name` in slot of view k is declared by the view's class itself (an override), judged from
        the demangled qualified name."""
        v = self.views[k]
        return bool(v.cls) and re.search(r'(?:^|[\s*&,<(])%s::[^:]*$' % re.escape(v.cls),
                                         (self.text.get(name) or '').split('(')[0]) is not None

    def disagreements(self):
        """[(address, slot, {address: [(unit, MSVC name, how)]})] over all joined vftables."""
        out = []
        for a in sorted(self.by_addr):
            for j, (best, _, cand) in enumerate(self.table(a)):
                if best is None and len(cand) > 1:
                    out.append((a, j, {x: [(self.views[k].unit, s.name, s.how) for k, s in sup]
                                       for x, sup in cand.items()}))
        return out
