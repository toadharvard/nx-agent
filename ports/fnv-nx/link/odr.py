#!/usr/bin/env python3
"""odr - gate `odr`: the copies of every COMDAT group are identical across objects (L2 SOURCE, ABI1).

    odr.py OBJ.o [OBJ.o ...]            writes $GATE_OUT/odr.json, exit 0 iff green, 1 red, 2 malformed input

RULE.  A COMDAT group (an SHT_GROUP section whose flag word has GRP_COMDAT) is the compiler's statement "every translation unit that
needs this entity emits this same definition; the linker keeps one".  The linker keeps one without looking at the others, so the
statement is true iff all copies are the same definition.  The identity of a copy is its image as the linker would see it:
the multiset over member sections of (section name, type, flags, alignment, entry size, bytes, relocations), where a relocation
is (offset, type, addend, target) and the target is
  * a symbol with a name (global, weak, undefined or local non-section symbol): the name, or, for a local symbol defined in
    a member of the same group, (member name, value);
  * a section symbol: of a member of the same group -> (member name); of a SHF_MERGE section -> the merged entity read at
    the addend (cstring when SHF_STRINGS, else entsize bytes), the addend then being an address of this object and dropped;
    of any other section -> OPAQUE: a section symbol has no size, so the extent of the entity it points at is not in the object;
    the copy is then undecidable and its signature is uncertified (it is never passed).
Relocation sections are found by sh_info (the section they apply to), never by name.
UNIVERSE.  Every group signature (the name of the group's symbol) that occurs as a COMDAT group in two or more of the given objects.
A signature in one object only has nothing to be compared with and is not in the universe.
PROOF.  Green means all copies of every signature have equal identity and no copy has an opaque relocation; the identity includes every byte and every relocation of
every member, so two copies with equal identity assemble to the same bytes with the same symbol references in any link.  A red item names the
signature, the objects and the first difference.
INPUT FACTS.  The objects are ELF relocatable objects of one target (the objects the verdict's link takes; a member or group the gate cannot parse is a red item with the reason, ValueError below); equal symbol names mean equal symbols (the linker's own rule for one link, which is what the verdict's link performs).
"""
import argparse
import hashlib
import os
import struct
import sys

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'contract'))
import gatefile

NAME = 'odr'
SHT_GROUP, SHF_MERGE, SHF_STRINGS, GRP_COMDAT = 17, 0x10, 0x20, 1
STT_SECTION = 'STT_SECTION'


def _target(elf, symtab, rel, members):
    sym = symtab.get_symbol(rel['r_info_sym'])
    add = rel['r_addend'] if 'r_addend' in rel.entry else 0
    if sym['st_info']['type'] != STT_SECTION:
        n = sym.name
        shn = sym['st_shndx']
        if isinstance(shn, int) and shn in members and sym['st_info']['bind'] == 'STB_LOCAL':
            return ('def', members[shn], sym['st_value'], add)
        if not n:
            raise ValueError('relocation against an unnamed symbol')
        return ('sym', n, add)
    shn = sym['st_shndx']
    if not isinstance(shn, int):
        raise ValueError('section symbol without a section')
    if shn in members:
        return ('member', members[shn], add)
    sec = elf.get_section(shn)
    if sec['sh_flags'] & SHF_MERGE:
        d = sec.data()
        if add < 0 or add > len(d):
            raise ValueError('merge-section addend outside %s' % sec.name)
        if sec['sh_flags'] & SHF_STRINGS:
            e = d.find(b'\0', add)
            if e < 0:
                raise ValueError('unterminated string in %s' % sec.name)
            return ('merge', sec.name, d[add:e + 1])
        es = sec['sh_entsize']
        if es == 0 or add + es > len(d):
            raise ValueError('merge entity outside %s' % sec.name)
        return ('merge', sec.name, d[add:add + es])
    return ('opaque', sec.name, add)


def _copy(elf, grp):
    """Identity of one COMDAT group: sorted list of member fingerprints."""
    d = grp.data()
    if len(d) < 4 or len(d) % 4:
        raise ValueError('group %s has a malformed body' % grp.name)
    words = struct.unpack(('<' if elf.little_endian else '>') + '%dI' % (len(d) // 4), d)
    idx = list(words[1:])
    members = {}
    for i in idx:
        members[i] = elf.get_section(i).name
    if len(set(idx)) != len(idx):
        raise ValueError('group %s lists a member twice' % grp.name)
    fps = []
    for i in idx:
        s = elf.get_section(i)
        rels = []
        for r in elf.iter_sections():
            if isinstance(r, RelocationSection) and r['sh_info'] == i:
                symtab = elf.get_section(r['sh_link'])
                for rel in r.iter_relocations():
                    try:
                        t = _target(elf, symtab, rel, members)
                    except (ValueError, KeyError, IndexError) as e:
                        raise ValueError('member %s: %s' % (s.name, e))
                    rels.append((rel['r_offset'], rel['r_info_type'], t))
        rels.sort(key=repr)
        body = b'' if s['sh_type'] == 'SHT_NOBITS' else s.data()
        fps.append((s.name, s['sh_type'], s['sh_flags'], s['sh_addralign'], s['sh_entsize'],
                    s['sh_size'], hashlib.sha256(body).hexdigest(), tuple(rels)))
    return sorted(fps, key=repr)


def copies(path):
    """{signature: identity} of the COMDAT groups of one object; ValueError on malformed input."""
    try:
        with open(path, 'rb') as fh:
            elf = ELFFile(fh)
            if elf['e_type'] != 'ET_REL':
                raise ValueError('%s: not a relocatable object' % path)
            out = {}
            for s in elf.iter_sections():
                if s['sh_type'] != 'SHT_GROUP' and s['sh_type'] != SHT_GROUP:
                    continue
                d = s.data()
                if len(d) < 4:
                    raise ValueError('%s: group %s has no flag word' % (path, s.name))
                flags = struct.unpack('<I' if elf.little_endian else '>I', d[:4])[0]
                if not flags & GRP_COMDAT:
                    continue
                sym = elf.get_section(s['sh_link']).get_symbol(s['sh_info'])
                if not sym.name:
                    raise ValueError('%s: group %s has an unnamed signature' % (path, s.name))
                if sym.name in out:
                    raise ValueError('%s: signature %s has two groups' % (path, sym.name))
                try:
                    out[sym.name] = _copy(elf, s)
                except (ValueError, KeyError, IndexError) as e:
                    raise ValueError('%s: group %s: %s' % (path, sym.name, e))
            return out
    except (ELFError, struct.error, AttributeError, TypeError) as e:
        raise ValueError('%s: malformed ELF object (%s)' % (path, e))


def first_difference(a, b):
    for x, y in zip(a, b):
        if x != y:
            if x[0] != y[0]:
                return 'member %s vs %s' % (x[0], y[0])
            for k, what in enumerate(('name', 'type', 'flags', 'alignment', 'entry size', 'size', 'bytes', 'relocations')):
                if x[k] != y[k]:
                    return 'member %s: %s differ' % (x[0], what)
    return 'member count %d vs %d' % (len(a), len(b))


def check(paths):
    """-> (universe ids, {id: reason})"""
    per = {p: copies(p) for p in sorted(set(paths))}
    sigs = {}
    for p, c in per.items():
        for s, fp in c.items():
            sigs.setdefault(s, []).append((p, fp))
    universe, red = [], {}
    for s, cs in sorted(sigs.items()):
        if len(cs) < 2:
            continue
        universe.append(s)
        op = sorted((p, t[1]) for p, fp in cs for m in fp for rel in m[7] for t in [rel[2]] if t[0] == 'opaque')
        if op:
            red[s] = 'relocation into section %s of %s by a section symbol: the entity is not delimited, identity undecidable' % (op[0][1], op[0][0])
            continue
        p0, f0 = cs[0]
        for p, f in cs[1:]:
            if f != f0:
                red[s] = 'copies in %s and %s differ: %s' % (p0, p, first_difference(f0, f))
                break
    return universe, red


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('objs', nargs='+')
    a = ap.parse_args(argv)
    try:
        uni, red = check(a.objs)
    except (ValueError, OSError) as e:
        print('odr: %s' % e, file=sys.stderr)
        return 2
    gatefile.write(NAME, len(uni), len(uni) - len(red), ['%s [%s]' % (i, r) for i, r in sorted(red.items())],
                   'COMDAT signatures present in two or more of %d objects' % len(a.objs))
    print('odr: %d signatures, %d differ' % (len(uni), len(red)))
    return 1 if red else 0


if __name__ == '__main__':
    sys.exit(main())
