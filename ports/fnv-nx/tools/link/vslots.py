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
  through units.json (`def`, `ref`, `gdef`, `gref`, then `name-addr`; adjustor thunks to their function; `??_E` to
  `??_G`; `__purecall`); vftables joined by image address (`table(address)`: per slot the best-ranked address, the
  disagreements).
* `gcc_vtables` / `pair_slots` - a GCC vtable group (ELF object) against the MSVC vftables of the same class: the
  subobject offset pairs a GCC (secondary) vtable with the MSVC vftable of the same vfptr; inside one, slots pair by
  signature (MSVC groups the overloads one class introduces in reverse declaration order), the virtual destructor's
  D1/D0 with the one `??_G`/`??_E` slot, the rest by position.
* `Binder` - the `fnvlink.py resolve` hook (`classify`): an undefined slot symbol of a GCC object (or an implicit
  D1/D2 GCC calls where MSVC inlined it) -> the native definition at its original address (D1 of a `??_G`), or a
  stub that tail-calls through the image vftable slot (`emit_image_stub`: the S3 loader rewrites that word to the
  native definition, the `(this, flags)` deleting-dtor adapter or an address-reporting trap) when the slot's image
  vftable is known but its target is named by no unit.
* `validate` - checks against the matched x86 code: thiscall `ret N` of the static targets vs the declared stack
  bytes; `this`-based `call [vptr+off]` sites (pushes and the joined slot's target).

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

    def reloc_syms(self, secnum):
        """{offset: CoffSym or None} of a section's relocations (by symbol index: the $LN labels of a switch repeat
        across the functions of one object)."""
        s = self.secs[secnum]
        out = {}
        for k in range(s.nreloc):
            off, si, _ = struct.unpack_from('<IIH', self.data, s.reloc_off + 10 * k)
            out[off] = self.syms.get(si)
        return out

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

Cand = collections.namedtuple('Cand', 'addr how msvc vtable ott j k view own img pure')
# addr: the slot's original target when the joined image vftable knows it; img: the image vftable's address (the
# view's binding), so that slot k can be read at run time; own: the vtable (or vftable) of the class that declares
# the function; pure: MSVC's slot is __purecall
ADDR_SYM = '__fnv_%08x'
STUB_SYM = '__fnvnx_vslot_%08x_%d'
LOOSE_TIERS = ('arity', 'name')   # fnvlink match tiers that compare only names (and the parameter count)
TEXT_START, TEXT_END = 0x00401000, 0x00fdf000   # the image's .text: a slot word still in here was not rewritten


def canon_class(text):
    import sigs
    return '::'.join(sigs.canon_qual(sigs.split_qual(text))) if text else None


def emit_image_stub(name, slot_va, flags, text):
    """AArch64 (ILP32) stub for a GCC vtable slot whose original target is known only as slot k of an image vftable:
    tail-call through the image word at slot_va, which the loader (S3) has rewritten to the native definition, to the
    deleting-destructor adapter (`(this, flags)`, kind G) or to a trap that reports the original address. flags (0 or
    1) is passed in w1 for D1 / D0. A word still inside the image's .text was not rewritten: fnvnx_vslot_trap(this,
    text) (weak: S2c's default or the platform's)."""
    lines = ['    .section .text.%s,"ax",%%progbits' % name, '    .p2align 2', '    .globl %s' % name,
             '    .hidden %s' % name, '    .type %s, %%function' % name, '    .weak fnvnx_vslot_trap', '%s:' % name,
             '    movz w16, #0x%x' % (slot_va & 0xffff), '    movk w16, #0x%x, lsl #16' % (slot_va >> 16),
             '    ldr w16, [x16]']
    if flags is not None:
        lines.append('    mov w1, #%d' % flags)
    lines += ['    movz w17, #0x%x' % (TEXT_START & 0xffff), '    movk w17, #0x%x, lsl #16' % (TEXT_START >> 16),
              '    cmp w16, w17', '    b.lo 1f',
              '    movz w17, #0x%x' % (TEXT_END & 0xffff), '    movk w17, #0x%x, lsl #16' % (TEXT_END >> 16),
              '    cmp w16, w17', '    b.lo 2f',
              '1:  br x16',
              '2:  adrp x1, .L%s' % name, '    add x1, x1, :lo12:.L%s' % name, '    b fnvnx_vslot_trap',
              '    .size %s, .-%s' % (name, name), '    .section .rodata.%s,"a"' % name, '.L%s:' % name,
              '    .asciz "%s"' % text.replace('\\', '\\\\').replace('"', '\\"')]
    return '\n'.join(lines) + '\n'


class Binder:
    """Undefined GCC vtable-slot symbols -> original addresses or image vftable slots (the `fnvlink.py resolve` hook,
    docs/LINK.md "Vtable slots").

    units_json: path; objdir: the pristine MSVC objects (u<index>.obj); itanium(name) / msvc(name): fnvlink's Sig
    parsers (cached); need_msvc(names): adds MSVC names to fnvlink's demangle cache (so its ABI check reads the slot
    declarations) and returns {name: text}. After resolve: rows / stats (write()), stubs {name: asm} for bridges.S."""

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
        self.class_views = collections.defaultdict(list)  # class -> [view index]
        for k, (c, _) in self.view_class.items():
            self.class_views[c].append(k)
        self._obj = {}
        self.rows, self.stats, self.checks = [], collections.Counter(), collections.Counter()
        self.pair_stats = collections.Counter()
        self.stubs = {}
        self._slotfns = set()   # slot functions of the vtables of the object last read

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

    def _cand(self, k, kk, how, where, fn_sig, z, ott, j, own):
        v = self.ix.views[k]
        t = v.targets[kk]
        img = self.ix.view_addr[k][0] if k in self.ix.view_addr else None
        if t.how == 'pure':
            a, ahow = None, 'pure'
        elif img is not None:
            tab = self.ix.table(img)
            a = tab[kk][0] if kk < len(tab) else None
            ahow = 'table' if a is not None else ('table-disagrees' if kk < len(tab) and len(tab[kk][2]) > 1
                                                  else 'image-slot')
        else:
            a, ahow = t.addr, ('view-' + t.how) if t.addr is not None else 'unknown'
        return Cand(a, '%s/%s/%s' % (ahow, how, where), t.name, z, ott, j, kk, k, own, img, t.how == 'pure')

    def object_slots(self, src, path):
        """{GCC slot function symbol: [Cand]} for one object (the last one is cached: resolve walks them in order)."""
        if path in self._obj:
            return self._obj[path]
        out = collections.defaultdict(list)
        self._obj = {path: out}
        self._slotfns = set()
        try:
            groups = gcc_vtables(path)
        except (OSError, ValueError):
            return out
        self._slotfns = {slot_function(s) for g in groups.values() for _, sl in g for s in sl}
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
                    own = cls == '::'.join(gsigs[j].qual[:-1])
                    out[fns[j]].append(self._cand(k, kk, how, where, gsigs[j], z, ott, j, own))
        return out

    def declaring(self, src, isig):
        """Fallback when no vtable of the object pairs the symbol usefully: the MSVC vftable of the class that
        declares the function (the unit's view first, the vfptr at offset 0 first), slot found by signature."""
        cls = '::'.join(isig.qual[:-1])
        mine = {k for i in self.sources.get(src, []) for k in self.ix.by_unit.get(i, [])}
        ks = sorted(self.class_views.get(cls, []), key=lambda k: (k not in mine, self.ix.views[k].col.offset,
                                                                  k not in self.ix.view_addr, k))
        for k in ks:
            v = self.ix.views[k]
            msigs = [(self.msvc(t.name) if t.name and t.name != PURE else None, is_msvc_dtor(t.name))
                     for t in v.targets]
            hit = None
            if isig.variant in ('D0', 'D1', 'D2'):
                d = [kk for kk, (_, x) in enumerate(msigs) if x]
                hit = (d[0], 'dtor') if len(d) == 1 else None
            else:
                keys = dict(isig.keys()[:4])
                for tier in ('exact', 'l1', 'l2', 'arity'):
                    if tier not in keys:
                        continue
                    ms = [kk for kk, (s, x) in enumerate(msigs) if s is not None and not x and
                          dict(s.keys()[:4]).get(tier) == keys[tier]]
                    if len(ms) == 1 or (ms and tier != 'arity'):
                        hit = (ms[0], tier)
                        break
            if hit:
                return self._cand(k, hit[0], hit[1], 'declaring-class', isig, None, -v.col.offset, -1, True)
        return None

    def lookup(self, src, path, un, isig, def_at):
        """-> (outcome, 'static' | 'image' | None, Cand or None, all Cands) for one undefined slot symbol.

        A slot is only called through its own vtable, and the symbol is renamed for every relocation of it in the
        object: when all of them are slots of one image vftable slot, its static target (or the stub through it) is
        exact; when they span several classes, a static target they all agree on, else the vtable of the class that
        declares the function, else the slot most of them name. Without any usable vtable slot: the declaring
        class's MSVC vftable (found by signature)."""
        cands = list(self.object_slots(src, path).get(un) or [])
        useful = [c for c in cands if not c.pure and (c.addr is not None or c.img is not None)]
        if not useful:
            dc = self.declaring(src, isig)
            if dc is not None and not dc.pure and (dc.addr is not None or dc.img is not None):
                cands.append(dc)
                useful.append(dc)
        if not cands:
            return 'no-pairing', None, None, cands
        if not useful:
            return 'no-address:' + sorted({c.how.split('/')[0] for c in cands})[0], None, None, cands
        groups = collections.OrderedDict()
        for c in useful:
            groups.setdefault((c.img, c.k) if c.img is not None else ('view', c.view, c.k), []).append(c)
        st = {c.addr for c in useful}

        def best(c, why):
            return (c, 'static' if c.addr is not None else 'image', why)
        if len(groups) == 1:
            pick, kind, why = best(next(iter(groups.values()))[0], 'declaring class' if useful[0].j == -1 else
                                   'one vtable slot')
        elif None not in st and len(st) == 1:
            pick, kind, why = useful[0], 'static', 'several vtables, one target'
        elif any(c.own for c in useful):
            pick, kind, why = best(next(c for c in useful if c.own), 'several classes, the declaring one')
        else:
            pick, kind, why = best(max(groups.values(), key=len)[0], 'several classes, the most common')
        if kind == 'image' and pick.img is None:
            return 'no-address:' + pick.how.split('/')[0], None, None, cands
        dtor_slot = isig.variant in ('D0', 'D1', 'D2')
        if kind == 'static':
            a = pick.addr
            if a not in def_at or dtor_slot != is_msvc_dtor(def_at[a][1]):
                if pick.img is None or (not dtor_slot and a in def_at):
                    return ('address-without-definition' if a not in def_at else 'dtor-mismatch'), None, pick, cands
                kind, why = 'image', why + '; static target ' + ('not defined' if a not in def_at else 'not a ??_G')
        return '%s (%s)' % (kind, why), kind, pick, cands

    def classify(self, src, path, un, isig, native, def_at, current):
        """fnvlink's reference loop: current = its (class, tier, address, target, note) for an undefined symbol a
        vtable of the object names -> the same, or the binding through the MSVC vftables: a native definition by
        address, or a stub through the image vftable slot (class 'vslot-image'). It replaces unresolved and ambiguous
        results and those of the loose tiers (name / parameter count only, which pick the wrong overload); a strict
        binding is kept and only compared."""
        cls, tier, addr, _, _ = current
        loose = cls in ('unresolved', 'ambiguous') or (cls == 'addr' and (tier or '').split('/')[-1] in LOOSE_TIERS)
        if not loose:
            if cls == 'addr' and addr is not None:
                outcome, kind, c, cands = self.lookup(src, path, un, isig, def_at)
                a = c.addr if kind == 'static' else None
                key = 'check: same address' if a == addr else ('check: OTHER ADDRESS' if a is not None else
                                                               'check: no static vslot address')
                self.checks[key] += 1
                if a is not None and a != addr:
                    self._out(path, src, un, 'check-differs: kept %s %08x' % (tier, addr), c, cands,
                              'check-differs (a declaration misses an override the image vftable has)')
            return current
        outcome, kind, c, cands = self.lookup(src, path, un, isig, def_at)
        before = cls if cls != 'addr' else 'loose ' + tier.split('/')[-1]
        if un not in self._slotfns:   # D1/D2 GCC calls where MSVC did not: the class's own ??_G with flags 0
            outcome, before = 'implicit dtor: ' + outcome, before
        if kind is None:
            self._out(path, src, un, outcome + ' [kept %s]' % before, c, cands)
            return current
        var = isig.variant
        if kind == 'static' and var in ('D1', 'D2'):
            nd = native.get(c.addr)
            vv = next((x for x in ('D1', 'D2') if nd and nd['variants'].get(x) is not None), None)
            if nd is not None and vv is None:
                if c.img is None:
                    self._out(path, src, un, 'dtor-d1-not-emitted [kept %s]' % before, c, cands)
                    return current
                kind, outcome = 'image', outcome + '; D1 not emitted'
        if cls == 'addr':
            self.stats['(loose bindings: %s)' % ('same address' if kind == 'static' and addr == c.addr else
                                                 'REPLACED')] += 1
        self._out(path, src, un, outcome + ' [was %s]' % before, c, cands)
        if kind == 'image':
            flags = {'D0': 1, 'D1': 0, 'D2': 0}.get(var)
            name = STUB_SYM % (c.img, c.k) + ('_d%d' % (1 - flags) if flags is not None else '')
            if name not in self.stubs:
                v = self.ix.views[c.view]
                self.stubs[name] = emit_image_stub(
                    name, c.img + 4 * c.k, flags, 'vtable slot through image vftable 0x%08x slot %d (%s, %s): the '
                    'loader left an x86 address there' % (c.img, c.k, v.cls, self.ix.text.get(c.msvc) or c.msvc))
            return 'vslot-image', 'vslot/' + c.how, None, name, c.msvc
        a = c.addr
        if var in ('D0', 'D1', 'D2'):
            if var == 'D0':
                target = ADDR_SYM % a
            else:   # D1 (D2 identical without virtual bases): the native complete-object destructor at the address
                nd = native.get(a)
                vv = next((x for x in ('D1', 'D2') if nd and nd['variants'].get(x) is not None), None)
                target = (ADDR_SYM % a) + '_' + (vv or 'D1')
            return ('code' if a in native else 'code-pending'), 'vslot/' + c.how, a, target, c.msvc
        return 'addr', 'vslot/' + c.how, a, None, c.msvc

    def _out(self, path, src, un, outcome, c, cands, stat=None):
        self.stats[stat or outcome] += 1
        c = c or (cands[0] if cands else None)
        v = self.ix.views[c.view] if c else None
        self.rows.append((os.path.basename(path), src, un, outcome, '%08x' % c.addr if c and c.addr is not None else '',
                          '%08x' % c.img if c and c.img is not None else '', c.how if c else '',
                          (c.vtable or '') if c else '', str(-c.ott) if c else '', str(c.j) if c else '',
                          str(c.k) if c else '', 'u%d' % v.unit if v else '', (c.msvc or '') if c else '',
                          ' '.join(sorted({'%08x' % x.addr for x in cands or [] if x.addr is not None}))))

    def write(self, out):
        """vslots.tsv (one row per undefined slot symbol looked at) and vslots_stats.txt."""
        with open(os.path.join(out, 'vslots.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
            f.write('#object\tunit\tsymbol\toutcome\taddress\timage_vftable\thow\tvtable\tvfptr_offset\titanium_slot\t'
                    'msvc_slot\tmsvc_view\tmsvc_declaration\tcandidate_addresses\n')
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
                     s.how for v in ix.views for s in v.targets).items())),
                 'image-slot stubs: %d' % len(self.stubs)]
        lines += ['', 'undefined slot symbols fnvlink left unresolved, ambiguous or bound by a loose tier, by outcome:']
        lines += ['  %-90s %d' % kv for kv in sorted(self.stats.items())]
        lines += ['', 'slot symbols fnvlink bound strictly, against the MSVC vftables:']
        lines += ['  %-90s %d' % kv for kv in sorted(self.checks.items())]
        lines += ['', 'GCC vtables and slots paired with MSVC vftables:'] + \
            ['  %-48s %d' % kv for kv in sorted(self.pair_stats.items())]
        open(os.path.join(out, 'vslots_stats.txt'), 'w').write('\n'.join(lines) + '\n')
        return lines


def from_env(units_json, itanium, msvc, need_msvc):
    """The Binder when FNVNX_MSVC_OBJS names the pristine MSVC objects directory (fnvlink.py resolve
    --msvc-objs sets it), else None."""
    d = os.environ.get('FNVNX_MSVC_OBJS')
    return Binder(units_json, d, itanium, msvc, need_msvc) if d and os.path.isdir(d) else None


# ---------------------------------------------------------------------------------------------------- validation

def stack_bytes(sig):
    """Bytes a thiscall/stdcall callee pops for an MSVC signature (abi.py's x86 model), or None when unknown
    (by-value aggregates of unknown size, varargs, cdecl)."""
    import abi
    if sig is None or sig.kind != 'func' or sig.cc not in ('thiscall', 'stdcall'):
        return None
    n = 0
    for label, cls, loc in abi.x86_locations(sig):
        if cls == 'VARARGS' or cls.startswith(('AGG:', '?:')):
            return None
        if isinstance(loc, tuple):
            n += abi.X86_SIZE.get(cls, 4)
    return n


class Code:
    """x86 code of the functions units.json defines, read from the MSVC objects (capstone)."""

    def __init__(self, ix, objdir):
        import capstone
        self.cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        self.cs.detail = True
        self.ix, self.objdir = ix, objdir
        self.at = {}          # address -> (unit, symbol, size), the first definition
        for i, u in enumerate(ix.units):
            for f in u['functions']:
                self.at.setdefault(int(f['address'], 16), (i, f['symbol'], f['size']))
        self._coff, self._rets = {}, {}

    def coff(self, i):
        if i not in self._coff:
            if len(self._coff) > 64:
                self._coff.clear()
            p = os.path.join(self.objdir, 'u%d.obj' % i)
            self._coff[i] = Coff(p) if os.path.exists(p) else None
        return self._coff[i]

    def insns(self, i, name, size):
        c = self.coff(i)
        if c is None or name not in c.defs:
            return []
        s = c.syms[c.defs[name]]
        return list(self.cs.disasm(c.section_bytes(s.secnum)[s.value:s.value + size], 0))

    def ret_bytes(self, a):
        """Stack bytes the function at original address a pops (`ret N`), or None (no ret: tail calls, noreturn;
        several different ones)."""
        if a not in self._rets:
            i, name, size = self.at.get(a, (None, None, 0))
            rets = set()
            for ins in self.insns(i, name, size) if i is not None else []:
                if ins.mnemonic == 'ret':
                    rets.add(ins.operands[0].imm if ins.operands else 0)
            self._rets[a] = next(iter(rets)) if len(rets) == 1 else None
        return self._rets[a]

    def names_at(self):
        """{decorated name: original address} for relocation targets: every unit's definitions and references, plus
        self.aliases [(address, old name, unit index or None)]: S1b's renamed definitions and references keep their
        old names in the pristine objects."""
        if not hasattr(self, '_names'):
            self._names, self._unit_names = {}, collections.defaultdict(dict)
            for i, u in enumerate(self.ix.units):
                for f in u['functions']:
                    self._unit_names[i][f['symbol']] = int(f['address'], 16)
                    self._names.setdefault(f['symbol'], int(f['address'], 16))
                for n, a in u.get('symbols', {}).items():
                    self._unit_names[i][n] = int(a, 16)
            for a, n, i in getattr(self, 'aliases', ()):
                self._names.setdefault(n, a)
                if i is not None:
                    self._unit_names[i].setdefault(n, a)
        return self._names

    def entry_use(self, a, takes, need=None, arg_bytes=None, _depth=0, _busy=None):
        """How the code at original address a uses the incoming ecx and edx, strongest over every pristine
        definition there: {'ecx': kind, 'edx': kind}, or None when no object has the code. A definition S1b renamed
        (self.renamed: its pristine code is the unpatched one) uses the registers its patched declaration has.

        kind: 'read' (an operand, an address register or a sub-register reads it, or a read of the stack word it
        was pushed to); 'pass' (it reaches a callee: still in the register at a call or tail jump to a callee that
        takes it, or pushed to a stack word inside the callee's arguments; a callee takes a register when its own
        pristine code uses it, else when its declaration puts a parameter there - takes(symbol, through_slot) ->
        {'ecx', 'edx'} subset or None when unknown - and any call or jump it cannot follow takes both); 'push'
        (only pushed to a stack word that is overwritten or never read: MSVC reserves a local with `push ecx`);
        None (never used). The stack is followed by depth: a pushed incoming register is a slot; writes through
        [esp+k] / [ebp+m] overwrite it, reads read it, `pop` restores it, and at a call it is an argument when it
        lies within the callee's stack argument bytes (arg_bytes(symbol) -> (bytes, popped by the callee); else the
        callee's own `ret N`; else the caller's cleanup right after the call, `add esp, N` or pops into eax / ecx /
        edx; else the bytes pushed since the previous call). Every path from the entry is followed, both ways at a conditional
        branch; a switch through the function's own jump table goes to every label its relocations name. Calls
        write ecx and edx; `xor r, r`, `sub r, r`, `or r, -1`, `and r, 0` write without reading. need(names), when
        given, is called once per function with the relocation targets (to demangle them in one go)."""
        import capstone.x86 as X
        if not hasattr(self, '_all_at'):
            self._all_at = collections.defaultdict(list)
            for i, u in enumerate(self.ix.units):
                for f in u['functions']:
                    self._all_at[int(f['address'], 16)].append((i, f['symbol'], f['size']))
            self._use_memo = {}
        if a in self._use_memo:
            return self._use_memo[a]
        busy = _busy if _busy is not None else set()
        busy.add(a)
        rank = {None: 0, 'push': 1, 'pass': 2, 'read': 3}
        sub = {'ecx': {X.X86_REG_ECX, X.X86_REG_CX, X.X86_REG_CL, X.X86_REG_CH},
               'edx': {X.X86_REG_EDX, X.X86_REG_DX, X.X86_REG_DL, X.X86_REG_DH}}
        full = {X.X86_REG_ECX: 'ecx', X.X86_REG_EDX: 'edx'}
        if a in getattr(self, 'renamed', ()) and self._all_at.get(a):
            # S1b renamed (patched) this definition: its pristine code is the unpatched one, and the patch (XINC1)
            # gave it the register the pristine declaration lacked - judge it by its patched declaration
            t = takes(self._all_at[a][0][1], False)
            res = {r: ('pass' if t is None or r in t else None) for r in ('ecx', 'edx')}
            self._use_memo[a] = res
            busy.discard(a)
            return res
        best, found = {'ecx': None, 'edx': None}, False
        for i, name, size in self._all_at.get(a, []):
            c = self.coff(i)
            if c is None or name not in c.defs:
                continue
            s = c.syms[c.defs[name]]
            if not 0 < s.secnum < len(c.secs):
                continue
            found = True
            ins = {x.address: x for x in self.cs.disasm(c.section_bytes(s.secnum)[s.value:s.value + size], 0)}
            lo, hi = s.value, s.value + size
            rs = {off - lo: y for off, y in c.reloc_syms(s.secnum).items() if lo <= off < hi and y is not None}
            rel = {o: y.name for o, y in rs.items()}
            own = {o: y.value - lo for o, y in rs.items() if y.secnum == s.secnum and lo <= y.value < hi}
            labels = set(own.values())
            if need:
                need(sorted(set(rel.values())))
            order = sorted(ins)
            nxt = dict(zip(order, order[1:] + [None]))
            use = {'ecx': None, 'edx': None}
            unit_names = self.names_at() and self._unit_names[i]

            def note(r, kind):
                if rank[kind] > rank[use[r]]:
                    use[r] = kind

            def reads_mem(op, r):
                return op.type == X.X86_OP_MEM and (op.mem.base in sub[r] or op.mem.index in sub[r])

            def callee_takes(target, slot):
                """Registers the callee at `target` uses: its own code when there is some, else its declaration."""
                ta = None if slot else unit_names.get(target, self._names.get(target))
                if ta is not None and ta not in busy and _depth < 6:
                    r_ = self.entry_use(ta, takes, need, arg_bytes, _depth + 1, busy)
                    if r_ is not None:
                        return {x for x in ('ecx', 'edx') if r_[x] in ('read', 'pass')}
                return takes(target, slot)

            def stack_depth(op, d, fp):
                """The slot depth a memory operand [esp+k] / [ebp+m] addresses, or None."""
                if op.type != X.X86_OP_MEM or op.mem.index or op.mem.segment:
                    return None
                if op.mem.base == X.X86_REG_ESP:
                    return d - op.mem.disp
                if op.mem.base == X.X86_REG_EBP and fp is not None:
                    return fp - op.mem.disp
                return None
            seen, work, steps = set(), [(0, frozenset(('ecx', 'edx')), frozenset(), 0, None, 0)], 0
            while work and steps < 20000:
                pc, live, slots, d, fp, since = work.pop()
                while pc is not None and pc in ins and (live or slots) and steps < 20000:
                    key = (pc, live, slots, d, fp)
                    if key in seen:
                        break
                    seen.add(key)
                    steps += 1
                    x = ins[pc]
                    m, ops = x.mnemonic, x.operands
                    at = next((o for o in range(pc, pc + x.size) if o in rel), None)
                    target = rel[at] if at is not None else None
                    sl = dict(slots)   # depth -> register whose incoming value the stack word holds
                    # memory operands touching a slot
                    for k_, op in enumerate(ops):
                        sd = stack_depth(op, d, fp) if op.type == X.X86_OP_MEM else None
                        if sd in sl:
                            if k_ == 0 and len(ops) == 2 and m in ('mov', 'movss', 'movd') and op.size == 4:
                                del sl[sd]                      # overwritten: a reserved local
                            elif m != 'lea':
                                note(sl[sd], 'read')
                    if m == 'jmp' and ops and (ops[0].type == X.X86_OP_REG or (
                            ops[0].type == X.X86_OP_MEM and (ops[0].mem.base or ops[0].mem.index))):
                        for r in live:
                            note(r, 'read' if (ops[0].type == X.X86_OP_REG and ops[0].reg in sub[r]) or
                                 reads_mem(ops[0], r) else 'pass' if at not in own else None)
                        if at in own:
                            work.extend((t, live, frozenset(sl.items()), d, fp, since)
                                        for t in sorted(labels - {own[at]}))
                        else:
                            for r in set(sl.values()):
                                note(r, 'pass')
                        break
                    if m == 'jmp' and at in own:
                        pc = own[at]
                        continue
                    if m == 'call' or (m == 'jmp' and target is not None):
                        op = ops[0] if ops else None
                        direct = op is not None and (op.type == X.X86_OP_IMM or (
                            op.type == X.X86_OP_MEM and not op.mem.base and not op.mem.index))
                        slot_call = op is not None and op.type == X.X86_OP_MEM
                        nb, pops = (arg_bytes(target, slot_call) if (arg_bytes and direct and target) else None) \
                            or (None, None)
                        if nb is None and direct and target is not None and not slot_call:
                            # no declaration: the callee's own `ret N` (callee-pop), else the caller's cleanup
                            # right after the call (`add esp, N`, `pop` into a scratch register: V1 R4-1)
                            ta_ = unit_names.get(target, self._names.get(target))
                            rb_ = self.ret_bytes(ta_) if ta_ is not None else None
                            if rb_:
                                nb, pops = rb_, rb_
                        if nb is None and m == 'call':
                            q_, cl_ = nxt[pc], 0
                            while q_ is not None and q_ in ins:
                                y_ = ins[q_]
                                if y_.mnemonic == 'add' and len(y_.operands) == 2 and \
                                        y_.operands[0].type == X.X86_OP_REG and y_.operands[0].reg == X.X86_REG_ESP and \
                                        y_.operands[1].type == X.X86_OP_IMM:
                                    cl_ += y_.operands[1].imm
                                    break
                                if y_.mnemonic == 'pop' and y_.operands and y_.operands[0].type == X.X86_OP_REG and \
                                        y_.operands[0].reg in (X.X86_REG_EAX, X.X86_REG_ECX, X.X86_REG_EDX):
                                    cl_ += 4
                                    q_ = nxt[q_]
                                    continue
                                break
                            if cl_:
                                nb, pops = cl_, 0
                        if direct and target is not None:
                            t = callee_takes(target, slot_call)
                            for r in live:
                                if t is None or r in t:
                                    note(r, 'pass')
                        else:
                            for r in live:
                                note(r, 'read' if op is not None and ((op.type == X.X86_OP_REG and op.reg in sub[r])
                                                                      or reads_mem(op, r)) else 'pass')
                        span = nb if nb is not None else since
                        lo_ = 4 if m == 'jmp' else 0   # a tail jump: the return address is at [esp]
                        for sd, r in sl.items():
                            if lo_ <= d - sd < lo_ + span or (m == 'jmp' and nb is None):
                                note(r, 'pass')                 # a stack argument of the callee
                        if m == 'jmp':
                            break
                        live, since = frozenset(), 0
                        if pops:
                            d -= pops
                            sl = {k: v for k, v in sl.items() if k <= d}
                        slots, pc = frozenset(sl.items()), nxt[pc]
                        continue
                    try:
                        rd, wr = (set(v) for v in x.regs_access())
                    except Exception:
                        rd, wr = sub['ecx'] | sub['edx'], set()
                    if len(ops) == 2 and ops[0].type == X.X86_OP_REG and (
                            (m in ('xor', 'sub') and ops[1].type == X.X86_OP_REG and ops[0].reg == ops[1].reg) or
                            (ops[1].type == X.X86_OP_IMM and ((m == 'or' and ops[1].imm & 0xffffffff == 0xffffffff)
                                                              or (m == 'and' and ops[1].imm == 0)))):
                        rd.discard(ops[0].reg)
                    if m == 'push':
                        if ops and ops[0].type == X.X86_OP_REG and full.get(ops[0].reg) in live:
                            note(full[ops[0].reg], 'push')
                            d += 4
                            sl[d] = full[ops[0].reg]
                        else:
                            for r in live:
                                if rd & sub[r]:
                                    note(r, 'read')
                            d += 4
                        since += 4
                    elif m == 'pop':
                        r_ = full.get(ops[0].reg) if ops and ops[0].type == X.X86_OP_REG else None
                        held = sl.pop(d, None)
                        live = frozenset(r for r in live if not (ops and ops[0].type == X.X86_OP_REG and
                                                                 ops[0].reg in sub[r]))
                        if held is not None:
                            if r_ == held:
                                live = live | {held}            # the incoming value is back in its register
                            else:
                                note(held, 'read')              # moved to another register: assume it is used
                        d -= 4
                        since = max(0, since - 4)
                    else:
                        for r in live:
                            if rd & sub[r]:
                                note(r, 'read')
                        live = frozenset(r for r in live if not any(w_ in (X.X86_REG_ECX, X.X86_REG_EDX) and
                                                                    full[w_] == r for w_ in wr))
                        if len(ops) == 2 and ops[0].type == X.X86_OP_REG and ops[0].reg == X.X86_REG_ESP:
                            if m in ('sub', 'add') and ops[1].type == X.X86_OP_IMM:
                                n_ = ops[1].imm if m == 'sub' else -ops[1].imm
                                d += n_
                                since = max(0, since + n_) if n_ > 0 else max(0, since + n_)
                            elif m == 'mov' and ops[1].type == X.X86_OP_REG and ops[1].reg == X.X86_REG_EBP:
                                d = fp if fp is not None else d
                            elif m == 'lea' and ops[1].type == X.X86_OP_MEM and ops[1].mem.base == X.X86_REG_EBP \
                                    and fp is not None:
                                d = fp - ops[1].mem.disp
                            sl = {k: v for k, v in sl.items() if k <= d}
                        if len(ops) == 2 and m == 'mov' and ops[0].type == X.X86_OP_REG and \
                                ops[0].reg == X.X86_REG_EBP and ops[1].type == X.X86_OP_REG and \
                                ops[1].reg == X.X86_REG_ESP:
                            fp = d
                    slots = frozenset(sl.items())
                    if m in ('ret', 'retf', 'int3', 'ud2', 'hlt'):
                        break
                    if m == 'jmp':
                        if ops and ops[0].type == X.X86_OP_IMM:
                            pc = ops[0].imm
                            continue
                        for r in live:
                            note(r, 'read' if ops and reads_mem(ops[0], r) else 'pass')
                        for r in set(sl.values()):
                            note(r, 'pass')
                        break
                    if (m.startswith('j') or m.startswith('loop')) and ops and ops[0].type == X.X86_OP_IMM:
                        work.append((ops[0].imm, live, slots, d, fp, since))
                    pc = nxt[pc]
            for r in best:
                if rank[use[r]] > rank[best[r]]:
                    best[r] = use[r]
        res = best if found else None
        busy.discard(a)
        self._use_memo[a] = res
        return res

    def this_vcalls(self, i, name, size):
        """Virtual calls through the vptr of `this` in a thiscall member function: [(slot, pushed bytes)].

        `this` is followed from ecx through register copies and stack slots written once (unoptimized code spills it
        to [ebp-x]); its vptr is a load from [this]. Pushed bytes count from the end of the prologue (frame, locals,
        SEH registration, security cookie) or from the previous call; a site after a branch target since then is left
        out (merging paths make the count unreliable)."""
        import capstone.x86 as X
        ins = self.insns(i, name, size)
        cs = self.cs
        targets = {op.imm for x in ins if x.group(X.X86_GRP_JUMP) for op in x.operands if op.type == X.X86_OP_IMM}

        def reg(op):
            return FULL_REG.get(cs.reg_name(op.reg), cs.reg_name(op.reg)) if op.type == X.X86_OP_REG else None

        def frame_slot(op):   # [ebp+d] / [esp+d] with no index -> ('ebp', d)
            if op.type == X.X86_OP_MEM and op.mem.index == 0 and op.mem.base and op.mem.segment == 0:
                b = cs.reg_name(op.mem.base)
                if b == 'ebp':
                    return b, op.mem.disp
            return None
        writes = collections.Counter(frame_slot(x.operands[0]) for x in ins if x.mnemonic == 'mov' and x.operands
                                     and frame_slot(x.operands[0]))
        out = []
        this, vptr, fnreg, this_slots = {'ecx'}, set(), {}, set()
        pushed, counting, merged, after_call = 0, False, False, False
        prologue = True
        for x in ins:
            m, ops = x.mnemonic, x.operands
            if x.address in targets:
                vptr.clear(); fnreg.clear()
                this &= {'esi', 'edi', 'ebx', 'ebp'}
                merged = True
            if prologue:     # frame, locals, SEH registration, security cookie, saved registers
                pro = (m == 'push' and ops and (ops[0].type == X.X86_OP_IMM or reg(ops[0]) in
                                                 ('ebp', 'ebx', 'esi', 'edi', 'eax', 'ecx'))) or \
                    (m == 'mov' and len(ops) == 2 and (
                        (reg(ops[0]) == 'ebp' and reg(ops[1]) == 'esp') or
                        any(o.type == X.X86_OP_MEM and o.mem.segment != 0 for o in ops) or
                        (reg(ops[0]) == 'eax' and ops[1].type == X.X86_OP_MEM and ops[1].mem.base == 0))) or \
                    (m in ('sub', 'and') and reg(ops[0]) == 'esp') or (m in ('xor', 'lea') and reg(ops[0]) == 'eax')
                if pro:
                    continue
                prologue, counting, pushed = False, True, 0
            if m == 'add' and after_call and reg(ops[0]) == 'esp':
                continue            # cdecl cleanup of the previous call
            after_call = False
            if m == 'call':
                site = None
                if ops and ops[0].type == X.X86_OP_MEM and ops[0].mem.index == 0 and ops[0].mem.base and \
                        cs.reg_name(ops[0].mem.base) in vptr:
                    site = ops[0].mem.disp
                elif ops and reg(ops[0]) in fnreg:
                    site = fnreg[reg(ops[0])]
                if site is not None and site % 4 == 0 and site >= 0 and counting and not merged:
                    out.append((site // 4, pushed))
                pushed, counting, merged, after_call = 0, True, False, True
                for r in ('eax', 'ecx', 'edx'):
                    this.discard(r); vptr.discard(r); fnreg.pop(r, None)
                continue
            if m == 'push':
                pushed += 4
            elif m == 'pop':
                pushed -= 4
            elif m in ('sub', 'add') and reg(ops[0]) == 'esp' and ops[1].type == X.X86_OP_IMM:
                pushed += ops[1].imm if m == 'sub' else -ops[1].imm
            if m == 'mov' and len(ops) == 2 and frame_slot(ops[0]):
                fs = frame_slot(ops[0])
                if reg(ops[1]) in this and writes[fs] == 1:
                    this_slots.add(fs)
                else:
                    this_slots.discard(fs)
                continue
            if m == 'mov' and len(ops) == 2 and ops[0].type == X.X86_OP_REG:
                dst, src = reg(ops[0]), ops[1]
                new_this = reg(src) in this or frame_slot(src) in this_slots
                new_vptr = reg(src) in vptr or (src.type == X.X86_OP_MEM and src.mem.index == 0 and
                                                src.mem.disp == 0 and src.mem.base and
                                                FULL_REG.get(cs.reg_name(src.mem.base), cs.reg_name(src.mem.base))
                                                in this)
                new_fn = src.mem.disp if (src.type == X.X86_OP_MEM and src.mem.index == 0 and src.mem.base and
                                          cs.reg_name(src.mem.base) in vptr) else None
                this.discard(dst); vptr.discard(dst); fnreg.pop(dst, None)
                if new_this:
                    this.add(dst)
                elif new_vptr:
                    vptr.add(dst)
                elif new_fn is not None:
                    fnreg[dst] = new_fn
                continue
            try:
                _, written = x.regs_access()
            except Exception:   # capstone cannot tell: forget everything this instruction may touch
                written = []
            for r in written:
                k = FULL_REG.get(cs.reg_name(r), cs.reg_name(r))
                this.discard(k); vptr.discard(k); fnreg.pop(k, None)
        return out


FULL_REG = {p + s: 'e' + p + 'x' for p in 'abcd' for s in ('x', 'l', 'h')}
FULL_REG.update({r: 'e' + r for r in ('si', 'di', 'bp', 'sp')})


def validate(ix, objdir, msvc_sig, vslots_tsv=None, max_units=None):
    """Checks of the slot model against the matched x86 code -> report lines.

    A. every binding in vslots.tsv (resolve --msvc-objs): the declared slot's stack bytes against the `ret N` of the
       function at the bound address (thiscall callees pop their arguments);
    B. `this`-based virtual calls `call [vptr+off]` in the thiscall member functions of classes whose primary
       vftable the unit emits: pushed bytes against the declared slot off/4 of that vftable (MSVC's own order), and
       against the `ret N` of the function the joined image vftable has in that slot."""
    code = Code(ix, objdir)
    lines = []
    a_stats = collections.Counter()
    if vslots_tsv and os.path.exists(vslots_tsv):
        for line in open(vslots_tsv, encoding='utf-8', errors='surrogateescape'):
            f = line.rstrip('\n').split('\t')   # object unit symbol outcome address ... msvc_declaration (12)
            if line.startswith('#') or len(f) < 13 or not f[4]:
                continue
            what = 'bound' if 'static (' in f[3] and '[was' in f[3] else 'check-differs' if \
                f[3].startswith('check-differs') else None
            if what is None:
                continue
            want = 4 if is_msvc_dtor(f[12]) else stack_bytes(msvc_sig(f[12]))
            got = code.ret_bytes(int(f[4], 16))
            key = 'unknown' if want is None or got is None else ('same' if want == got else 'DIFFERENT')
            a_stats['%s %s%s' % (what, 'dtor ' if is_msvc_dtor(f[12]) else '', key)] += 1
        lines.append('A. static slot targets, declared stack bytes vs ret N of the target: ' +
                     ' '.join('%s=%d' % kv for kv in sorted(a_stats.items())))
    b, examples = collections.Counter(), []
    units = sorted(ix.by_unit)[:max_units] if max_units else sorted(ix.by_unit)
    for i in units:
        prim = {}
        for k in ix.by_unit[i]:
            v = ix.views[k]
            if v.col and v.col.offset == 0:
                prim[canon_class(v.cls)] = k
        if not prim:
            continue
        for f in ix.units[i]['functions']:
            s = msvc_sig(f['symbol'])
            if s is None or s.kind != 'func' or s.cc != 'thiscall' or not s.member:
                continue
            k = prim.get('::'.join(s.qual[:-1]))
            if k is None:
                continue
            v = ix.views[k]
            tab = ix.table(ix.view_addr[k][0]) if k in ix.view_addr else None
            for slot, pushed in code.this_vcalls(i, f['symbol'], f['size']):
                b['sites'] += 1
                if slot >= len(v.targets):
                    b['declared: SLOT BEYOND THE VFTABLE'] += 1
                    continue
                t = v.targets[slot]
                want = 4 if is_msvc_dtor(t.name) else stack_bytes(msvc_sig(t.name)) if t.name and t.name != PURE \
                    else None
                b['declared: ' + ('unknown' if want is None else 'same' if want == pushed else 'DIFFERENT')] += 1
                a = tab[slot][0] if tab is not None and slot < len(tab) else t.addr
                got = code.ret_bytes(a) if a is not None else None
                key = 'unknown' if got is None else 'same' if got == pushed else 'DIFFERENT'
                b['joined target vs pushed: ' + key] += 1
                if got is not None and want is not None:
                    b['joined target vs declared: ' + ('same' if got == want else 'DIFFERENT')] += 1
                if got is not None and want == pushed:
                    b['joined target vs pushed, where pushed == declared: ' + key] += 1
                if key == 'DIFFERENT' and want == pushed and len(examples) < 8:
                    examples.append('   u%d %s slot %d: pushed %d, %08x pops %d (%s)'
                                    % (i, f['symbol'], slot, pushed, a, got, t.name))
    lines.append('B. this-based virtual calls in matched code: ' + ' '.join('%s=%d' % kv for kv in sorted(b.items())))
    return lines + examples


def main():
    import json
    import sys
    if len(sys.argv) < 4 or sys.argv[1] != 'validate':
        print('usage: vslots.py validate <units.json> <msvc objdir> [vslots.tsv] [max units]   (docs/LINK.md)')
        sys.exit(2)
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import fnvlink
    import sigs
    units = json.load(open(sys.argv[2]))['units']
    cache = fnvlink.DemangleCache(os.path.join(os.path.dirname(os.path.abspath(sys.argv[4])) if len(sys.argv) > 4
                                               else '.', 'vslots_msvc.tsv'), fnvlink.UNDNAME)

    def dem(names):
        cache.need(names)
        return {n: cache[n] for n in names}
    ix = MsvcIndex(units, sys.argv[3], dem)
    cache.need({f['symbol'] for u in units for f in u['functions']})
    memo = {}

    def msvc_sig(n):
        if n not in memo:
            memo[n] = sigs.parse_msvc(n, cache[n]) if n else None
        return memo[n]
    for line in validate(ix, sys.argv[3], msvc_sig, sys.argv[4] if len(sys.argv) > 4 else None,
                         int(sys.argv[5]) if len(sys.argv) > 5 else None):
        print(line)


if __name__ == '__main__':
    main()
