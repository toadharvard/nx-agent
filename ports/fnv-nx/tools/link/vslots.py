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
# strongest first; a FUN_<addr> name last: a local class's override keeps the name of the slot it overrides
TIERS = ('def', 'ref', 'gdef', 'gref', 'name-addr')
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
        amb = None
        for g, how in ((self.gdef, 'gdef'), (self.gref, 'gref')):
            if len(g.get(name, ())) == 1:
                return next(iter(g[name])), how
            if g.get(name):
                amb = amb or how + '-ambiguous'
        m = MS_ADDR_NAME.match(name)
        if m:
            return int(m.group(1), 16), 'name-addr'
        return None, amb or 'unbound'

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


# ---------------------------------------------------------------------------------------------------- Itanium side

ITHUNK = re.compile(r'^_ZT[hv](?:n?\d+_)+(.*)$')   # non-virtual / virtual this-adjusting thunk -> the function


def gcc_vtables(path):
    """GCC vtable groups an ELF object defines: {_ZTV symbol: [(offset-to-top, [slot symbol or None])]}, one entry
    per vtable of the group (primary first); a slot is the relocation target from the address point on."""
    import vtables
    o = vtables.Obj(path)
    out = {}
    for z in sorted(o.defs):
        if not z.startswith('_ZTV') or z.startswith('_ZTVN10__cxxabiv1'):
            continue
        ents = o.ents(z)
        pts = vtables.address_points(ents)
        group = []
        for n, (ap, ott) in enumerate(pts):
            end = pts[n + 1][0] // 4 - 2 if n + 1 < len(pts) else len(ents)
            group.append((ott, [e.sym for e in ents[ap // 4:end]]))
        out[z] = group
    return out


def slot_function(sym):
    """GCC slot symbol -> the function it stands for (thunks to their target), or None for 0 / pure / deleted."""
    if not sym or sym in ('__cxa_pure_virtual', '__cxa_deleted_virtual') or sym.startswith(('.section:', '.reloc')):
        return None
    m = ITHUNK.match(sym)
    return '_Z' + m.group(1) if m else sym


def is_msvc_dtor(name):
    return bool(name) and name.startswith(('??_G', '??_E'))


def pair_slots(gsigs, msigs):
    """Pair the slots of one GCC vtable with those of the MSVC vftable of the same vfptr.

    gsigs: per GCC slot (from the address point) a sigs.Sig or None (no function: 0, pure); msigs: per MSVC slot
    (Sig or None, is deleting dtor). The Itanium vtable has two slots (D1, D0) where MSVC has one deleting
    destructor, and MSVC groups overloads of one name in reverse declaration order; otherwise both follow
    declaration order (MSVC's grouping stays inside the virtuals one class introduces). -> [(MSVC slot or None, how)]
    per GCC slot, how in 'dtor', 'exact', 'l1', 'l2', 'arity' (same name and parameter count, unique), 'position'
    (unmatched slot at its expanded position, whose MSVC slot no name claims), 'unpaired'."""
    pos = []     # expanded MSVC positions: the deleting destructor counts twice
    for k, (s, dtor) in enumerate(msigs):
        pos += [k, k] if dtor else [k]
    by_key = collections.defaultdict(list)
    for k, (s, dtor) in enumerate(msigs):
        if s is not None and not dtor:
            for tier, key in s.keys()[:4]:
                by_key[(tier, key)].append(k)
    dtors = [k for k, (_, d) in enumerate(msigs) if d]
    res, used = [], set()
    for j, g in enumerate(gsigs):
        if g is None:
            res.append((None, 'unpaired'))
            continue
        if g.variant in ('D0', 'D1', 'D2'):
            k = dtors[0] if len(dtors) == 1 else (pos[j] if j < len(pos) and pos[j] in dtors else None)
            res.append((k, 'dtor') if k is not None else (None, 'unpaired'))
            continue
        hit = None
        for tier, key in g.keys()[:4]:
            ks = [k for k in by_key.get((tier, key), []) if k not in used]
            if tier == 'arity' and len(ks) != 1:   # name and parameter count only: when it is unique
                continue
            if ks:   # one declaration per signature in a vtable; several only with hiding: take the nearest
                hit = (min(ks, key=lambda k: abs(k - (pos[j] if j < len(pos) else j))), tier)
                break
        if hit:
            used.add(hit[0])
        res.append(hit)
    claimed = {r[0] for r in res if r and r[0] is not None}
    for j, r in enumerate(res):
        if r is None:
            k = pos[j] if j < len(pos) else None
            ok = k is not None and k not in claimed and not msigs[k][1]
            res[j] = (k, 'position') if ok else (None, 'unpaired')
            if ok:
                claimed.add(k)
    return res


# ---------------------------------------------------------------------------------------------------- binding

Cand = collections.namedtuple('Cand', 'addr how msvc vtable ott j k view own')
ADDR_SYM = '__fnv_%08x'
LOOSE_TIERS = ('arity', 'name')   # fnvlink match tiers that compare only names (and the parameter count)


def canon_class(text):
    import sigs
    return '::'.join(sigs.canon_qual(sigs.split_qual(text))) if text else None


class Binder:
    """Undefined GCC vtable-slot symbols -> original addresses (the `fnvlink.py resolve` hook, docs/LINK.md
    "Vtable slots").

    units_json: path; objdir: the pristine MSVC objects (u<index>.obj); itanium(name) / msvc(name): fnvlink's Sig
    parsers (cached); need_msvc(names): adds MSVC names to fnvlink's demangle cache (so its ABI check reads the slot
    declarations) and returns {name: text}."""

    def __init__(self, units_json, objdir, itanium, msvc, need_msvc):
        import json
        self.raw_units = json.load(open(units_json))['units']
        self.sources = collections.defaultdict(list)
        for i, u in enumerate(self.raw_units):
            self.sources[u['source']].append(i)
        self.ix = MsvcIndex(self.raw_units, objdir, need_msvc)
        self.itanium, self.msvc = itanium, msvc
        self.by_class = collections.defaultdict(list)     # (class, vfptr offset) -> [view index]
        self.view_class = {}
        for k, v in enumerate(self.ix.views):
            if v.col:
                self.view_class[k] = (canon_class(v.cls), v.col.offset)
                self.by_class[self.view_class[k]].append(k)
        self._obj = {}
        self.rows, self.stats, self.checks = [], collections.Counter(), collections.Counter()
        self.pair_stats = collections.Counter()

    def view_for(self, src, cls, off):
        """MSVC view of class cls's vftable at vfptr offset off: the unit's own (same declarations) first, else the
        views of the other units when they stand for one image vftable (or none has an address)."""
        own = [k for i in self.sources.get(src, []) for k in self.ix.by_unit.get(i, [])
               if self.view_class.get(k) == (cls, off)]
        if own:
            return own[0], 'unit'
        ks = self.by_class.get((cls, off), [])
        if not ks:
            return None, 'no-msvc-view'
        if len({self.ix.view_addr[k][0] for k in ks if k in self.ix.view_addr}) > 1:
            return None, 'other-units-disagree'
        # the view with an address, then the longest (the most complete declaration)
        return sorted(ks, key=lambda k: (k not in self.ix.view_addr, -len(self.ix.views[k].slots), k))[0], 'other-unit'

    def object_slots(self, src, path):
        """{GCC slot function symbol: [Cand]} for one object (the last one is cached: resolve walks them in order)."""
        if path in self._obj:
            return self._obj[path]
        out = collections.defaultdict(list)
        self._obj = {path: out}
        try:
            groups = gcc_vtables(path)
        except (OSError, ValueError):
            return out
        for z, group in groups.items():
            cls = self.itanium(z).name
            for ott, slots in group:
                k, where = self.view_for(src, cls, -ott)
                self.pair_stats['vtables: ' + where] += 1
                if k is None:
                    continue
                v = self.ix.views[k]
                fns = [slot_function(s) for s in slots]
                gsigs = [self.itanium(f) if f else None for f in fns]
                msigs = [(self.msvc(t.name) if t.name and t.name != PURE else None, is_msvc_dtor(t.name))
                         for t in v.targets]
                pairs = pair_slots(gsigs, msigs)
                tab = self.ix.table(self.ix.view_addr[k][0]) if k in self.ix.view_addr else None
                naive = []
                for kk, (_, d) in enumerate(msigs):
                    naive += [kk, kk] if d else [kk]
                for j, (kk, how) in enumerate(pairs):
                    if fns[j] is None:
                        continue
                    self.pair_stats['slots paired: ' + how] += 1
                    if kk is None:
                        continue
                    if how in ('exact', 'l1', 'l2', 'arity') and naive[j:j + 1] != [kk]:
                        self.pair_stats['slots reordered (MSVC order -> Itanium order)'] += 1
                    t = v.targets[kk]
                    if t.how == 'pure':
                        a, ahow = None, 'pure'
                    elif tab is not None and kk < len(tab):
                        a = tab[kk][0]
                        ahow = 'table' if a is not None else ('table-disagrees' if len(tab[kk][2]) > 1 else 'unknown')
                    else:
                        a, ahow = t.addr, ('view-' + t.how) if t.addr is not None else 'unknown'
                    own = cls == '::'.join(gsigs[j].qual[:-1])
                    out[fns[j]].append(Cand(a, '%s/%s/%s' % (ahow, how, where), t.name, z, ott, j, kk, k, own))
        return out

    def lookup(self, src, path, un, isig, def_at):
        """-> (outcome, address or None, the Cand it comes from, all Cands) for one undefined slot symbol."""
        cands = self.object_slots(src, path).get(un)
        if not cands:
            return 'no-pairing', None, None, cands
        with_a = [c for c in cands if c.addr is not None]
        addrs = {c.addr for c in with_a}
        if not addrs:
            return 'no-address:' + sorted({c.how.split('/')[0] for c in cands})[0], None, None, cands
        if len(addrs) > 1:     # one symbol in several vtables: the vtable of the class that declares it decides
            own = {c.addr for c in with_a if c.own}
            if len(own) != 1:
                return 'conflict', None, None, cands
            addrs = own
        a = next(iter(addrs))
        c = next(x for x in with_a if x.addr == a)
        if a not in def_at:
            return 'address-without-definition', a, c, cands
        dtor_slot, dtor_def = isig.variant in ('D0', 'D1', 'D2'), is_msvc_dtor(def_at[a][1])
        if dtor_slot != dtor_def:
            return 'dtor-mismatch', a, c, cands
        return ('bound-dtor-' + isig.variant if dtor_slot else 'bound') + ('/conflict-settled' if len(
            {x.addr for x in with_a}) > 1 else ''), a, c, cands

    def classify(self, src, path, un, isig, native, def_at, current):
        """fnvlink's reference loop: current = its (class, tier, address, target, note) for an undefined symbol a
        vtable of the object names -> the same, or the binding through the MSVC vftables. That binding replaces
        unresolved and ambiguous results and those of the loose tiers (name / parameter count only, which pick the
        wrong overload); a strict binding is kept and only compared."""
        cls, tier, addr, _, _ = current
        loose = cls in ('unresolved', 'ambiguous') or (cls == 'addr' and (tier or '').split('/')[-1] in LOOSE_TIERS)
        if not loose:
            if cls == 'addr' and addr is not None:
                outcome, a, c, cands = self.lookup(src, path, un, isig, def_at)
                key = 'check: same address' if a == addr else ('check: OTHER ADDRESS' if a is not None else
                                                               'check: no vslot address')
                self.checks[key] += 1
                if a is not None and a != addr:
                    self._out(src, un, 'check-differs (kept %s %08x)' % (tier, addr), a, cands, c)
            return current
        outcome, a, c, cands = self.lookup(src, path, un, isig, def_at)
        before = cls if cls != 'addr' else 'loose ' + tier.split('/')[-1]
        if not outcome.startswith('bound'):
            self._out(src, un, outcome + ' (kept %s)' % before, a, cands, c)
            return current
        if cls == 'addr':
            self.stats['(loose bindings: same address %s)' % ('confirmed' if addr == a else 'REPLACED')] += 1
        var = isig.variant
        if var in ('D0', 'D1', 'D2'):
            if var == 'D0':
                target = ADDR_SYM % a
            else:   # D1 (D2 identical without virtual bases): the native complete-object destructor at the address
                nd = native.get(a)
                vv = next((x for x in ('D1', 'D2') if nd and nd['variants'].get(x) is not None), None)
                if nd is not None and vv is None:
                    self._out(src, un, 'dtor-d1-not-emitted (kept %s)' % before, a, cands, c)
                    return current
                target = (ADDR_SYM % a) + '_' + (vv or 'D1')
            self._out(src, un, outcome + ' (was %s)' % before, a, cands, c)
            return ('code' if a in native else 'code-pending'), 'vslot/' + c.how, a, target, c.msvc
        self._out(src, un, outcome + ' (was %s)' % before, a, cands, c)
        return 'addr', 'vslot/' + c.how, a, None, c.msvc

    def _out(self, src, un, outcome, a, cands, c=None):
        self.stats[outcome] += 1
        c = c or (cands[0] if cands else None)
        v = self.ix.views[c.view] if c else None
        self.rows.append((src, un, outcome, '%08x' % a if a is not None else '', c.how if c else '',
                          c.vtable if c else '', str(-c.ott) if c else '', str(c.j) if c else '',
                          str(c.k) if c else '', 'u%d' % v.unit if v else '', (c.msvc or '') if c else '',
                          ' '.join(sorted({'%08x' % x.addr for x in cands or [] if x.addr is not None}))))
        return None

    def write(self, out):
        """vslots.tsv (one row per undefined slot symbol looked at) and vslots_stats.txt."""
        with open(os.path.join(out, 'vslots.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
            f.write('#object\tsymbol\toutcome\taddress\thow\tvtable\tvfptr_offset\titanium_slot\tmsvc_slot\t'
                    'msvc_view\tmsvc_declaration\tcandidate_addresses\n')
            for r in self.rows:
                f.write('\t'.join(r) + '\n')
        ix = self.ix
        tot = known = 0
        for a in ix.by_addr:
            for best, _, _ in ix.table(a):
                tot += 1
                known += best is not None
        lines = ['MSVC objects: %d, vftables (views): %d, with an image address: %d (%d addresses, %d with several '
                 'views)' % (ix.objects, len(ix.views), len(ix.view_addr), len(ix.by_addr),
                             sum(1 for v in ix.by_addr.values() if len(v) > 1)),
                 'image vftable slots: %d, original target known: %d, disagreements: %d'
                 % (tot, known, len(ix.disagreements())),
                 'view slot targets by how: ' + ' '.join('%s=%d' % kv for kv in sorted(collections.Counter(
                     s.how for v in ix.views for s in v.targets).items()))]
        lines += ['', 'undefined slot symbols fnvlink left unresolved, ambiguous or bound by a loose tier, by outcome:']
        lines += ['  %-64s %d' % kv for kv in sorted(self.stats.items())]
        lines += ['', 'slot symbols fnvlink bound strictly, against the MSVC vftables:']
        lines += ['  %-64s %d' % kv for kv in sorted(self.checks.items())]
        lines += ['', 'GCC vtables and slots paired with MSVC vftables:'] + \
            ['  %-48s %d' % kv for kv in sorted(self.pair_stats.items())]
        open(os.path.join(out, 'vslots_stats.txt'), 'w').write('\n'.join(lines) + '\n')
        return lines


def from_env(units_json, itanium, msvc, need_msvc):
    """The Binder when FNVNX_MSVC_OBJS names the pristine MSVC objects directory (fnvlink.py resolve
    --msvc-objs sets it), else None."""
    d = os.environ.get('FNVNX_MSVC_OBJS')
    return Binder(units_json, d, itanium, msvc, need_msvc) if d and os.path.isdir(d) else None
