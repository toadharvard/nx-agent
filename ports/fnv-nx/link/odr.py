#!/usr/bin/env python3
"""odr - gate `odr`: the copies of every COMDAT group are identical across objects (L2 SOURCE, ABI1).

    odr.py OBJ.o [OBJ.o ...]            writes $GATE_OUT/odr.json, exit 0 iff green, 1 red, 2 malformed input

RULE.  A COMDAT group (an SHT_GROUP section whose flag word has GRP_COMDAT) is the compiler's statement "every translation unit that
needs this entity emits this same definition; the linker keeps one".  The linker keeps one without looking at the others, so the
statement is true iff all copies are the same definition.  "The same definition" is decided by ONE function, link/comdat.py
comdat_identity (rule, normalisation of relocation targets and the undecidable case are stated there; gate linkstrict and gate onedef use it too).
A copy with an undecidable relocation target (a section symbol outside the group with no or several global/weak symbols at that offset) is
red, named `identity-undecidable` with the object, section and offset; it is never passed.
UNIVERSE.  Every group signature (the name of the group's symbol) that occurs as a COMDAT group in two or more of the given objects.
A signature in one object only has nothing to be compared with and is not in the universe.
PROOF.  Green means all copies of every signature have equal identity and no copy is undecidable; the identity includes every byte and every relocation of
every member, so two copies with equal identity assemble to the same bytes with the same symbol references in any link.  A red item names the
signature, the objects and the first difference.
INPUT FACTS.  The objects are ELF relocatable objects of one target (the objects the verdict's link takes; a member or group the gate cannot parse is a red item with the reason, ValueError below); equal symbol names mean equal symbols (the linker's own rule for one link, which is what the verdict's link performs).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'contract'))
import comdat
import gatefile

NAME = 'odr'


def copies(path):
    """{signature: comdat.Identity} of the COMDAT groups of one object; ValueError on malformed input."""
    return comdat.copies(path)[0]


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
        und = sorted(u for _, ident in cs for u in ident.undecidable)
        if und:
            red[s] = 'identity-undecidable: object %s section %s offset %d: %s' % und[0]
            continue
        p0, f0 = cs[0]
        for p, f in cs[1:]:
            if not comdat.same(f, f0):
                red[s] = 'copies in %s (%s) and %s (%s) differ: %s' % (p0, f0.digest[:12], p, f.digest[:12], comdat.first_difference(f0, f))
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
