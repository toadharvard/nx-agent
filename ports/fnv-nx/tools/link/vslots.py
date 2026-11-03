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
