#!/usr/bin/env python3
"""gate_names - NAMES1: the MSVC names the fnvnx_names plugin gave to GCC's definitions, against cl's own symbol tables (L2 SOURCE).

    gate_names.py --gcc PATH [--gcc PATH ...] --cl PATH [--cl PATH ...] [--units units.json]      (inputs: link/unitpairs.py)

RULE.  The pairing `.fnvnx.sympair` of a GCC object of unit U says, for every symbol of the object that has a contract name, which
MSVC decorated name it has.  MSVC's object of the same unit defines every entity that the unit's own text defines (the pristine
objects are built /Gy, so a function is a definition or a COMDAT definition), so the name of such an entity must be a definition of
cl's object of U.  A name that cl's object does not define is a wrong naming rule.  A row no rule named is a gap in the naming rules.
UNIVERSE (nothing sampled, nothing skipped), per unit with a GCC object, in the order of the unit names:
  1. every row of .fnvnx.sympair whose kind is one that has an MSVC name (func, data, ctor.c1, ctor.c2, dtor.d2, dtor.d1, dtor.d0,
     dtor.sdd, dtor.sdd.thunk) and that is `def` of a symbol the object defines STRONGLY: binding GLOBAL (not weak, not unique), defined
     in a section (not SHN_UNDEF/ABS/COMMON), not a section or file symbol, and the defining section is not in a COMDAT group
     (SHF_GROUP).  Item id `unit:symbol`.
  2. every row of .fnvnx.sympair whose kind is `unnamed:<reason>` (no rule names it), `ref` or `def`.  Item id `unit:symbol:unnamed`.
  3. every row of .fnvnx.vtpair whose MSVC name is `unnamed:<reason>`.  Item id `unit:ztv-symbol:vftable-unnamed`.
  4. a GCC object that cannot be read (ELF, symbol table, a plugin section): one item `unit:error`.
CERTIFIED: an item of class 1 whose MSVC name is `def`, `comdat` or `common` among the external symbols of at least one cl object of the
unit (an unreadable one among them does not hide a proof in another; if none proves it, the item is red and says so).
Red (listed): class 1 with no cl object for the unit, with an unreadable cl object, or with a name cl does not define; every item of
classes 2, 3, 4.  The result is a function of the objects and the units list; ids sorted; zero items: green, universe 0 (detail says so).
PROOF.  A strong GCC definition is emitted from the unit's own source text, not from an inline or template instantiation that the
unit merely uses; so the entity has a definition in the unit's cl object, and that object's symbol table names it with the one decorated
name.  If the table holds the plugin's name as a definition, the name is that of a definition of the same unit; if it does not, the
naming rule is refuted for this item.  Classes 2 and 3 are decided by the rule's own totality: every extern symbol must be named.
RULES AND INPUTS.  Rule (checked, class 1): a strong definition of a GCC object that has a naming kind is certified only when the cl
object of the same unit defines the name; a GCC-only strong symbol with a naming kind is red, and the plugin gives GCC-only symbols the
nameless kinds below (classes 2 and 3, red).  Scope of the check (a statement of what is read, not a claim beyond it): the check shows
that the plugin's name is a definition of the same unit's cl object, not that it is the name of this very declaration (two definitions of
one unit with swapped names would pass); the plugin's rules are verified by the object that cl wrote.  Input: the sections are the
plugin's own output (contract/l2sections.py documents their format).
NOT IN THE UNIVERSE (no class-closing rule is known, reported to the orchestrator; legacy namegate counted them only): rows of
kind `thunk`, `vtable`, `gcc`, `internal` (no cl twin by the plugin's classification); `ref` rows and weak or COMDAT definitions of
named kinds (cl emits an inline function, a vftable or a reference only where it chose to use it, so a name its object lacks proves
nothing); vtpair rows with a name.  Pairing is by file stem (the `u<N>` rule of unitpairs.py with --units).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'contract'))
import coffobj
import elfobj
import l2sections
import objsections
import unitpairs

NAME = 'names'
NAMED = frozenset(('func', 'data', 'ctor.c1', 'ctor.c2', 'dtor.d2', 'dtor.d1', 'dtor.d0', 'dtor.sdd', 'dtor.sdd.thunk'))
DEFINED_BY_CL = frozenset(('def', 'comdat', 'common'))


def strong_defs(elf):
    """names of the symbols the object defines strongly (see the UNIVERSE)"""
    out = set()
    for name, bind, typ, shndx, _v, _s, _o in elf.symbols():
        if (name and bind == elfobj.STB_GLOBAL and typ not in (elfobj.STT_SECTION, elfobj.STT_FILE)
                and shndx not in (elfobj.SHN_UNDEF, elfobj.SHN_ABS, elfobj.SHN_COMMON)
                and not elf.section_flags(shndx) & elfobj.SHF_GROUP):
            out.add(name)
    return out


def unit_rows(stem, gpath, cpaths):
    """[(stem, symbol, msvc name, reason)] of one unit; reason '' = certified"""
    try:
        elf = elfobj.read(gpath)
        sp = l2sections.parse_sympair(objsections.section(elf, '.fnvnx.sympair'), stem + ':.fnvnx.sympair')
        vt = l2sections.parse_vtpair(objsections.section(elf, '.fnvnx.vtpair'), stem + ':.fnvnx.vtpair')
        strong = strong_defs(elf)
    except ValueError as e:
        return [(stem, '', '', 'error: %s' % e)]
    cls, bad = [], []
    for cpath in cpaths:
        try:
            cls.append(coffobj.read(cpath).externals())
        except ValueError as e:
            bad.append(str(e))
    why = 'no cl object for the unit' if not cpaths else ('the cl object is unreadable: %s' % bad[0] if bad else '')
    out = []
    for sym, ms, kind, df in sp:
        if kind.startswith('unnamed:'):
            out.append((stem, sym, ms, 'unnamed'))
        elif kind in NAMED and df == 'def' and sym in strong:
            if any(cl.get(ms.encode('utf-8')) in DEFINED_BY_CL for cl in cls):
                out.append((stem, sym, ms, ''))
            elif not cls:
                out.append((stem, sym, ms, why))
            else:
                out.append((stem, sym, ms, '%s is not a definition of the cl object' % ms + (' (a cl object of the unit is unreadable)' if bad else '')))
    for z, _ap, ms, _off in vt:
        if ms.startswith('unnamed:'):
            out.append((stem, z, ms, 'vftable-unnamed'))
    return out


def rows(gcc_objs, cl_objs, units=None):
    """Library entry (no GATE_OUT, no printing, no exit): the sorted list of (stem, symbol, msvc_name, reason), one row per item
    of the universe.  gcc_objs / cl_objs: lists of object FILE paths (directories are accepted too, as in unitpairs.pairs); units:
    path of units.json or None.  stem: the unit (unitpairs); symbol: the GCC assembler symbol (the stem is part of the item because
    two units can define one symbol strongly); msvc_name: the plugin's name (certified when reason is ''; for a nameless item ''
    or the `unnamed:` text); reason: '' certified, else `<name> is not a definition of the cl object[...]`, `no cl object for the
    unit`, `the cl object is unreadable: ...`, `unnamed` (a sympair row no rule names), `vftable-unnamed` (symbol = the _ZTV
    symbol), or `error: ...` (a GCC object that cannot be read or parsed: symbol and name ''; one row for the unit).
    Raises unitpairs.GateInputError only for malformed declared inputs (missing path, bad units file, two objects of one stem)."""
    out = []
    for stem, g, c in unitpairs.pairs(gcc_objs, cl_objs, units):
        out += unit_rows(stem, g, c)
    return sorted(out)


def row_id(r):
    stem, sym, ms, why = r
    if why.startswith('error: '):
        return '%s:%s' % (stem, why)
    if why in ('unnamed', 'vftable-unnamed'):
        return '%s:%s:%s' % (stem, sym, why)
    return '%s:%s: %s' % (stem, sym, why)


def check(gcc, cl, units=None):
    """(universe, sorted red ids)"""
    rs = rows(gcc, cl, units)
    return len(rs), sorted(row_id(r) for r in rs if r[3])


def main(argv):
    a = unitpairs.parse_args(argv, 'gate_names.py', __doc__)
    try:
        uni, red = check(a.gcc, a.cl, a.units)
    except unitpairs.GateInputError as e:
        return unitpairs.fail(NAME, str(e))
    detail = ('names: %d of %d items not certified' % (len(red), uni)) if uni else 'names: no item at these inputs (nothing to certify)'
    return unitpairs.finish(NAME, uni, red, detail)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
