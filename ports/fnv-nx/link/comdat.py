#!/usr/bin/env python3
"""comdat - the ONE definition of "two copies of a COMDAT group are the same definition" (gates odr/abi, linkstrict, onedef).

    comdat_identity(obj, group)  -> Identity      obj: an Obj (parse(path)), group: the section index of an SHT_GROUP with GRP_COMDAT
    copies(path)                 -> {signature: Identity}, {member section index: signature}   (one object)
    same(a, b) / first_difference(a, b)

RULE.  A COMDAT group is identified by its signature symbol.  Two copies of a group are the same definition iff, for every
section of the group (group order; relocation sections are not members, they belong to their section by sh_info), the header
(name, type, flags, alignment, entry size), the bytes and the relocation lists are equal after the targets are normalised:
  * a section symbol (or a local symbol) into a section OF THE SAME GROUP  -> (group member ordinal in group order, offset + addend);
  * a symbol with a global, weak, undefined or common binding              -> (symbol name, addend);
  * a section symbol (or local symbol) into a SHF_MERGE section            -> the merged entity read at offset + addend (the NUL
    terminated string for SHF_STRINGS, else entsize bytes): literals of one inline function are one entity wherever the object put them;
  * a section symbol (or local symbol) into any other section OUTSIDE the group -> the one GLOBAL or WEAK symbol defined in that
    section at offset + addend (its name), a name being what the linker resolves; if no or several such symbols, the copy is
    UNDECIDABLE: Identity.undecidable names (object, section, offset) and the copy is never passed.
The signature is the name of the group's signature symbol (the section name for a STT_SECTION signature).  Equal identities
assemble to the same bytes with the same symbol references in any link; different identities are an ODR violation.
Pure function of the object's bytes: struct reads of the ELF section and symbol tables, no tool text.  A malformed object is ValueError.
"""
import hashlib
import struct

SHT_RELA, SHT_REL, SHT_SYMTAB, SHT_NOBITS, SHT_GROUP = 4, 9, 2, 8, 17
SHF_MERGE, SHF_STRINGS, GRP_COMDAT = 0x10, 0x20, 1
STB_LOCAL, STB_GLOBAL, STB_WEAK = 0, 1, 2
STT_SECTION = 3
SHN_ABS, SHN_COMMON = 0xFFF1, 0xFFF2


class Obj:
    """An ELF relocatable (ELF32/ELF64 little endian): sections, symbols, relocations by target section."""

    def __init__(self, path):
        try:
            d = open(path, "rb").read()
            self.path, self.data = path, d
            if d[:4] != b"\x7fELF" or d[5] != 1 or d[4] not in (1, 2):
                raise ValueError("not a little-endian ELF")
            w64 = self.w64 = d[4] == 2
            if struct.unpack_from("<H", d, 16)[0] != 1:
                raise ValueError("not a relocatable object")
            shoff, = struct.unpack_from("<Q" if w64 else "<I", d, 0x28 if w64 else 0x20)
            shent, shnum, shstr = struct.unpack_from("<HHH", d, 0x3A if w64 else 0x2E)
            self.secs = [struct.unpack_from("<IIQQQQIIQQ" if w64 else "<IIIIIIIIII", d, shoff + i * shent) for i in range(shnum)]
            # (name_off, type, flags, addr, off, size, link, info, align, entsize)
            self.names = [self._cs(self.secs[shstr][4], s[0]) for s in self.secs] if shstr else [""] * shnum
            self.syms, self.rels, self.defs = [], {}, {}
            st = next((i for i, s in enumerate(self.secs) if s[1] == SHT_SYMTAB), None)
            if st is not None:
                ss = self.secs[st]
                strtab = self.secs[ss[6]][4]
                for t in struct.iter_unpack("<IBBHQQ" if w64 else "<IIIBBH", d[ss[4]:ss[4] + ss[5]]):
                    n, info, shn, val = (t[0], t[1], t[3], t[4]) if w64 else (t[0], t[3], t[5], t[1])
                    self.syms.append((self._cs(strtab, n), info >> 4, info & 15, shn, val))
                for name, bind, typ, shn, val in self.syms:
                    if bind in (STB_GLOBAL, STB_WEAK) and 0 < shn < SHN_ABS and name:
                        self.defs.setdefault((shn, val), set()).add(name)
            for s in self.secs:
                if s[1] == SHT_REL:
                    raise ValueError("SHT_REL relocations (implicit addends) are not supported")
                if s[1] == SHT_RELA:
                    for o, inf, a in struct.iter_unpack("<QQq" if w64 else "<IIi", d[s[4]:s[4] + s[5]]):
                        self.rels.setdefault(s[7], []).append((o, inf >> 32 if w64 else inf >> 8, inf & 0xFFFFFFFF if w64 else inf & 255, a))
        except (struct.error, IndexError, OSError) as e:
            raise ValueError("%s: malformed ELF object (%s)" % (path, e))
        except ValueError as e:
            raise ValueError("%s: %s" % (path, e))

    def _cs(self, off, n):
        end = self.data.index(b"\0", off + n)
        return self.data[off + n:end].decode("latin-1")


class Identity:
    """One copy of a group: members [(name, type, flags, align, entsize, size, sha256(bytes), relocs)], undecidable [(section, offset, why)]."""

    def __init__(self, sig, members, undecidable):
        self.sig, self.members, self.undecidable = sig, members, undecidable
        self.digest = hashlib.sha256(repr(members).encode()).hexdigest()


def _signature(o, g):
    n, bind, typ, shn, val = o.syms[o.secs[g][7]]
    return o.names[shn] if typ == STT_SECTION else n


def _target(o, rel_sec, rel, ordinal, undec):
    """Normalised target of relocation REL (offset, symbol index, type, addend) of member REL_SEC of a group whose members are ORDINAL."""
    off, si, ty, a = rel
    n, bind, typ, shn, val = o.syms[si]
    if bind != STB_LOCAL or shn == 0 or shn == SHN_COMMON:
        return ("sym", n, a)                              # global, weak, undefined, common: by name
    if shn == SHN_ABS:
        return ("abs", val + a)
    if shn in ordinal:
        return ("member", ordinal[shn], val + a)          # inside the group: member ordinal and offset
    sec = o.secs[shn]
    at = val + a
    if sec[2] & SHF_MERGE and sec[9]:                     # a merge section: the entity pointed at
        base = sec[4] + at
        es = sec[9]
        end = base + es
        if at < 0 or at > sec[5]:
            raise ValueError("merge-section offset outside %s" % o.names[shn])
        if sec[2] & SHF_STRINGS:
            while o.data[end - es:end] != bytes(es):
                end += es
                if end > sec[4] + sec[5]:
                    raise ValueError("unterminated string in %s" % o.names[shn])
        return ("merge", o.data[base:end].hex())
    names = o.defs.get((shn, at), set())                  # outside the group: the one global/weak symbol defined at that offset
    if len(names) == 1:
        return ("sym", next(iter(names)), 0)
    undec.append((o.names[shn], at, "%d global or weak symbols are defined there" % len(names)))
    return ("undecidable", o.names[shn], at)


def comdat_identity(o, g):
    """Identity of the COMDAT group (section index G of Obj O)."""
    d = o.data
    gs = o.secs[g]
    if gs[5] < 4 or gs[5] % 4:
        raise ValueError("%s: group %d has a malformed body" % (o.path, g))
    idx = struct.unpack_from("<%dI" % (gs[5] // 4), d, gs[4])[1:]
    mem = [m for m in idx if o.secs[m][1] != SHT_RELA]      # relocation sections follow their section
    if len(set(mem)) != len(mem):
        raise ValueError("%s: group %d lists a member twice" % (o.path, g))
    ordinal = {m: k for k, m in enumerate(mem)}
    members, undec = [], []
    for m in mem:
        s = o.secs[m]
        body = b"" if s[1] == SHT_NOBITS else d[s[4]:s[4] + s[5]]
        rl = [(off, ty, _target(o, m, (off, si, ty, a), ordinal, undec)) for off, si, ty, a in sorted(o.rels.get(m, []))]
        members.append((o.names[m], s[1], s[2], s[8], s[9], s[5], hashlib.sha256(body).hexdigest(), tuple(rl)))
    return Identity(_signature(o, g), members, [(o.path,) + u for u in undec])


def copies(path):
    """-> ({signature: Identity}, {member section index: signature}) of the COMDAT groups of one object.  A signature in one object twice
    joins both (their members and findings concatenated: two copies in one object)."""
    o = Obj(path)
    out, member = {}, {}
    for g, s in enumerate(o.secs):
        if s[1] != SHT_GROUP or s[5] < 4 or struct.unpack_from("<I", o.data, s[4])[0] & GRP_COMDAT == 0:
            continue
        try:
            ident = comdat_identity(o, g)
        except (ValueError, IndexError, struct.error) as e:
            raise ValueError("%s: group %d: %s" % (path, g, e))
        if not ident.sig:
            raise ValueError("%s: group %d has an unnamed signature" % (path, g))
        for m in struct.unpack_from("<%dI" % (s[5] // 4 - 1), o.data, s[4] + 4):
            if o.secs[m][1] != SHT_RELA:
                member[m] = ident.sig
        if ident.sig in out:
            prev = out[ident.sig]
            ident = Identity(ident.sig, prev.members + ident.members, prev.undecidable + ident.undecidable)
        out[ident.sig] = ident
    return out, member


def same(a, b):
    return a.digest == b.digest


def first_difference(a, b):
    for x, y in zip(a.members, b.members):
        if x != y:
            if x[0] != y[0]:
                return "member %s vs %s" % (x[0], y[0])
            for k, what in enumerate(("name", "type", "flags", "alignment", "entry size", "size", "bytes", "relocations")):
                if x[k] != y[k]:
                    return "member %s: %s differ" % (x[0], what)
    return "member count %d vs %d" % (len(a.members), len(b.members))
