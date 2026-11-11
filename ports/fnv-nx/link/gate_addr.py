#!/usr/bin/env python3
"""Gate "addr" (fnvnx-gate/1, class F of VERDICT.md): no integer-to-pointer conversion of a native (GCC-compiled C++) object
escaped rule ADDR32 (docs/ORDERING.md, toolchain/addr/fnvnx_addr.cc).
    python3 -I gate_addr.py OBJ...        env GATE_OUT; writes GATE_OUT/addr.json; exit 0 iff green

RULE.  A 32-bit guest address computed in an integer must be used as that wrapped 32-bit value.  Machine code cannot tell a
wrapped integer sum from in-bounds pointer arithmetic, so the fact comes from the compiler: the plugin routes every conversion
of a computed integer to a pointer, and every pointer sum with a run-time offset, through an asm barrier at the last GIMPLE
point, verifies that nothing escaped (a compile error otherwise) and writes the object's section `.fnvnx.addr` with one line
per emitted function: symbol, conversions seen, conversions routed (addrsec.py).  The gate reads those records.
UNIVERSE.  For every object given: the object itself (item `OBJ`: certified iff it has the section and the section parses)
and every function symbol it defines (STT_FUNC or STT_GNU_IFUNC placed in a section; item `OBJ:SYMBOL`).  Aliases, thunks
and clones are function symbols too; the plugin writes `seen = routed = 0` for those without a GIMPLE body.
CERTIFIED.  A function with exactly one record in its object and seen == routed.  Uncertified: no record, two records, seen != routed,
and every function of an object whose section is missing or malformed (the detail names the first error).  An object that is not
readable ILP32 AArch64 ELF is an uncertified item.  Records of symbols the object does not define as functions are ignored (they
cannot certify a function).
PROOF.  seen == routed in a function means every conversion at the last GIMPLE point had an opaque (asm) operand, so the
expander and the RTL passes that follow see a finished 32-bit register: no sum exists that an addressing mode could absorb.
ASSUMPTIONS.  (1) The record is written by the plugin built from the tracked source and loaded into every compile of the
object (the build gate `build` checks that the recipe loads it).  (2) A-EDGE and A-BOUNDS of the plugin: constant displacements
below 2^16 and in-bounds array indexes do not wrap.  (3) After the barrier no later pass of GCC folds through an `asm`
(it cannot look inside one).  (4) `.cold` parts of functions (-freorder-blocks-and-partition) are function symbols without a
record: they are uncertified, so the recipe must not enable that partitioning.
Pure: a function of the object bytes."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'contract'))
import a64obj  # noqa: E402
import addrsec  # noqa: E402
import gatefile  # noqa: E402

NAME = 'addr'


def judge_object(path):
    """-> (items [id], uncertified {id: why})"""
    items, bad = [path], {}
    try:
        o = a64obj.read(path)
    except ValueError as e:
        return items, {path: str(e)}
    try:
        rows = addrsec.from_sections(o.sections, path)
        if rows is None:
            bad[path] = '%s: no %s section' % (path, addrsec.SECTION)
    except ValueError as e:
        rows = None
        bad[path] = str(e)
    recs = {}
    for sym, seen, routed in rows or []:
        recs.setdefault(sym, []).append((seen, routed))
    names = sorted(n for _, _, _, ns in o.functions for n in ns)
    for n in names:
        fid = '%s:%s' % (path, n)
        items.append(fid)
        r = recs.get(n, [])
        if rows is None:
            bad[fid] = 'object has no usable %s' % addrsec.SECTION
        elif len(r) != 1:
            bad[fid] = '%d records' % len(r)
        elif r[0][0] != r[0][1]:
            bad[fid] = 'seen %d routed %d' % r[0]
    return items, bad


def main(argv):
    items, bad = [], {}
    for p in argv:
        i, b = judge_object(p)
        items += i
        bad.update(b)
    detail = ('%d items in %d objects, every function has a record with seen == routed' % (len(items), len(argv)) if not bad else
              '%d of %d items uncertified: %s' % (len(bad), len(items), '; '.join(bad[k] for k in sorted(bad)[:3])[:300]))
    gatefile.write(NAME, len(items), len(items) - len(bad), sorted(bad), detail)
    print('addr: %s %d/%d %s' % ('red' if bad else 'green', len(items) - len(bad), len(items), detail[:200]))
    return 2 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
