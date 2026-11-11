#!/usr/bin/env python3
"""gate_r7 - R7 LAYOUT: does GCC give every record type exactly the layout cl gives it? (L2 SOURCE)

    gate_r7.py --gcc PATH [--gcc PATH ...] --cl PATH [--cl PATH ...] [--units units.json]      (inputs: link/unitpairs.py)

GCC side: section `.fnvnx.layout` of the GCC object (plugin fnvnx_layout; parsed by link/objsections.py): one line per complete record
type of the unit.  cl side: the CodeView type records (`.debug$T`) of the unit's cl object built with /Z7 (read by link/codeview.py).
Both are keyed by MSVC's decorated type name without the leading `.?A` (the "unique name" cl writes into every record; the plugin
computes it by the symbol-name rules); the key is an exact string on both sides, nothing is normalised.
UNIVERSE: every (unit, key) of every `.fnvnx.layout` line of every GCC object (a key once per unit); a GCC object that cannot be read or
whose section does not parse is one item `unit:error`.  Item id `unit:key`.  The records only cl has are not in the universe: no
GCC-built body can depend on a layout GCC never computed.
CERTIFIED: the unit's cl object has exactly one definition of the key and every fact below is the same on both sides:
  size; the set of named data members, each with byte offset (bit offset and width for a bit-field) and size; the bases (key, offset,
  virtual or not); the alignment (see A-natural); the vfptr (neither side introduces one).  Unnamed bit-fields are not compared (CodeView
  does not list them); the members of an anonymous struct or union are the record's own on both sides.
Every other item is red and listed with its reason: `differs` (the first difference), `undecided` (a fact the data cannot give), `only-gcc`
(cl's debug info of the unit has no definition: no cl object, no CodeView, or no such key).  Undecided reasons, with the missing fact:
  - the record introduces a vfptr (both sides): the offset of the vfptr is not in CodeView (LF_VFUNCTAB has a type and no offset); it is
    not derived from the other offsets, which leave it undetermined
  - alignment: cl's alignment of a record is not in CodeView; the natural alignment (codeview.py) is cl's only when the source did not
    change it.  The record, or a record held by value in it (transitively, through cl's members and bases), has a GCC line flagged
    altered/packed/useralign, or has no GCC line (what the source did is unknown)
  - the size of a member's type, or the alignment of a type, is outside what CodeView states (codeview.py: PRIM, member pointers)
  - the key has several different definitions in the unit, in one cl object or between the cl objects of a source several units list
    (the definitions of all of them are collected and identical ones count once; unnamed types share keys) or the plugin could not name the type (`nokey:`)
  - the CodeView members or bases of the record could not be read
A record with a virtual base differs by rule: cl lays it out with a vbptr and a vbtable, GCC with a vptr and vtable-held offsets; the
two object models are different layouts and the native body would read the wrong ones.
PROOF.  Offsets, sizes, bit positions and bases are the compilers' own data for the same source, compared exactly; the alignment is the
one derived fact.  ASSUMPTIONS.  A-natural: MSVC x86 gives a record without pack/align modifiers the natural alignment (largest of its
members', bases', 4 for a vfptr or vbptr; scalars align to their size up to 8, /Zp8) and GCC's flags `altered`/`packed`/`useralign` say
whether the source changed it (GCC sees the same source).  A-key: a unique name identifies one type of the unit.  Zero lines: green,
universe 0 (detail says so).
DROPPED from legacy r7: the vfptr offset taken as 0 when no member starts there (a derivation CodeView does not support); sizes and
alignments of the primitive types outside the scalar table (10, 12, 20 byte, 6 byte); pointer-to-member sizes taken from the pointer
kind; equality of two multi-definition keys by comparing the two sets of records (now: undecided); `only-cl` records; the text match
on reason strings that decided which member size was unknown; the verdict for a record whose members could not be read (it counted
every GCC member as missing).
"""
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'contract'))
import codeview
import coffobj
import elfobj
import objsections
import unitpairs

NAME = 'r7'
ALTERING = frozenset(('altered', 'packed', 'useralign'))
GRec = collections.namedtuple('GRec', 'size align fields bases vf altered nokey dup')


def gcc_rec(lay):
    fields, vf = [], False
    for name, off, bit, width, size in lay.fields:
        if name == '<vptr>':
            vf = True
        elif name[0] != '#':
            fields.append((name, off * 8 + (bit or 0), width or 0, size))
    fields.sort(key=lambda f: (f[0], f[1], f[2]))
    bases = sorted(((k, None if fl in ('v', 'i') else off, fl) for k, off, fl in lay.bases),
                   key=lambda b: (b[0], b[2], -1 if b[1] is None else b[1]))
    counts = collections.Counter(f[0] for f in fields)
    return GRec(lay.size, lay.align, tuple(fields), tuple(bases), vf, any(f in ALTERING for f in lay.flags),
                tuple(f[6:] for f in lay.flags if f.startswith('nokey:')), tuple(sorted(n for n, c in counts.items() if c > 1)))


def closure_reason(g, c, gcc_of, cl_of):
    """None when the natural alignment is cl's for the record and everything it holds by value; else the missing fact"""
    seen, todo = set(), list(c.deps)
    while todo:
        k = todo.pop()
        if k in seen:
            continue
        seen.add(k)
        gl = gcc_of(k)
        if not gl:
            return 'alignment: no GCC record of the held type %s (what the source did to its alignment is unknown)' % k
        if any(r.altered for r in gl):
            return 'alignment: the held type %s has pack/align in the source (cl alignment not in CodeView)' % k
        for r in cl_of(k):
            todo.extend(r.deps)
    if g.altered:
        return 'alignment: pack/align in the source (cl alignment not in CodeView)'
    return None


def compare_one(g, c, gcc_of, cl_of):
    """('equal'|'differs'|'undecided', reason) for one definition on each side"""
    if c.fatal:
        return 'undecided', 'CodeView: %s' % c.fatal
    if g.size != c.size:
        return 'differs', 'size gcc %d cl %d' % (g.size, c.size)
    if g.dup or c.dup:
        return 'undecided', 'member name twice: %s' % ','.join(g.dup + c.dup)
    gf = {f[0]: f for f in g.fields}
    cf = {f[0]: f for f in c.fields}
    for n in sorted(set(gf) | set(cf)):
        a, b = gf.get(n), cf.get(n)
        if a is None:
            return 'differs', 'member %s only in cl (bit %d)' % (n, b[1])
        if b is None:
            return 'differs', 'member %s only in gcc (bit %d)' % (n, a[1])
        if a[2] != b[2]:
            return 'differs', 'member %s bit width gcc %d cl %d' % (n, a[2], b[2])
        if a[1] != b[1]:
            return 'differs', 'member %s bit offset gcc %d cl %d' % (n, a[1], b[1])
        if b[3] is not None and a[3] != b[3]:
            return 'differs', 'member %s size gcc %d cl %d' % (n, a[3], b[3])
    gb = {(b[0], b[2]): b[1] for b in g.bases}
    cb = {(b[0], b[2]): b[1] for b in c.bases}
    for k in sorted(set(gb) | set(cb)):
        if k not in cb:
            return 'differs', 'base %s (%s) only in gcc' % k
        if k not in gb:
            return 'differs', 'base %s (%s) only in cl' % k
        if gb[k] != cb[k]:
            return 'differs', 'base %s offset gcc %s cl %s' % (k[0], gb[k], cb[k])
    if c.vbptr:
        return 'differs', 'cl has a vbptr (virtual bases), gcc a vptr: different object models'
    if g.vf != c.vf:
        return 'differs', 'introduces a vfptr: gcc %s cl %s' % (g.vf, c.vf)
    if g.vf:
        return 'undecided', 'the offset of the vfptr is not in CodeView (LF_VFUNCTAB has no offset)'
    if g.nokey:
        return 'undecided', 'no key: ' + '; '.join(g.nokey)
    if c.why:
        return 'undecided', '; '.join(c.why)
    why = closure_reason(g, c, gcc_of, cl_of)
    if why:
        return 'undecided', why
    if g.align != c.align:
        return 'differs', 'align gcc %d cl %d (natural rule)' % (g.align, c.align)
    return 'equal', ''


def unit_items(stem, gpath, cpaths):
    """(universe, [red ids], Counter of verdicts) of one unit"""
    try:
        lays = objsections.parse_layout(objsections.section(elfobj.read(gpath), '.fnvnx.layout'), stem + ':.fnvnx.layout')
    except ValueError as e:
        return 1, ['%s:error: %s' % (stem, e)], collections.Counter(error=1)
    ug = {}
    for lay in lays:
        r = gcc_rec(lay)
        if r not in ug.setdefault(lay.key, []):
            ug[lay.key].append(r)
    keys = sorted(ug)
    table, why = None, 'only-gcc: no cl object for the unit'
    if cpaths and keys:
        table = []
        why = 'only-gcc: the cl object has no CodeView types (.debug$T)'
        for cpath in cpaths:
            try:
                table += [codeview.Table(b, stem + ':.debug$T') for b in coffobj.read(cpath).blobs(b'.debug$T')]
            except ValueError as e:
                table, why = None, 'undecided: a cl object of the unit is unreadable: %s' % e
                break
        if table is not None and not table:
            table = None
    cache = {}

    def cl_of(k):
        if k not in cache:
            recs = []
            for t in table:
                for r in t.record(k):
                    if r not in recs:
                        recs.append(r)
            cache[k] = recs
        return cache[k]

    red, cnt = [], collections.Counter()
    for k in keys:
        gl = ug[k]
        if table is None:
            verdict, reason = why.split(': ', 1)[0], why.split(': ', 1)[1]
        else:
            try:
                cl = cl_of(k)
            except ValueError as e:
                verdict, reason, cl = 'undecided', 'CodeView: %s' % e, None
            else:
                if not cl:
                    verdict, reason = 'only-gcc', 'cl\'s debug info of the unit has no definition of the type' + \
                        (' (no key: %s)' % '; '.join(sorted({w for r in gl for w in r.nokey})) if any(r.nokey for r in gl) else '')
                elif len(gl) == 1 and len(cl) == 1:
                    try:
                        verdict, reason = compare_one(gl[0], cl[0], lambda x: ug.get(x, []), cl_of)
                    except ValueError as e:
                        verdict, reason = 'undecided', 'CodeView: %s' % e
                else:
                    verdict, reason = 'undecided', 'the key has %d gcc and %d cl different definitions in the unit' % (len(gl), len(cl))
        cnt[verdict] += 1
        if verdict != 'equal':
            red.append('%s:%s: %s: %s' % (stem, k, verdict, reason))
    return len(keys), red, cnt


def check(gcc, cl, units=None):
    """(universe, sorted red ids, Counter of verdicts)"""
    uni, red, cnt = 0, [], collections.Counter()
    for stem, g, c in unitpairs.pairs(gcc, cl, units):
        u, r, n = unit_items(stem, g, c)
        uni += u
        red += r
        cnt.update(n)
    return uni, sorted(red), cnt


def main(argv):
    a = unitpairs.parse_args(argv, 'gate_r7.py', __doc__)
    try:
        uni, red, cnt = check(a.gcc, a.cl, a.units)
    except unitpairs.GateInputError as e:
        return unitpairs.fail(NAME, str(e))
    detail = ('r7: %d of %d records certified; %s' % (uni - len(red), uni, ', '.join('%s %d' % kv for kv in sorted(cnt.items()) if kv[0] != 'equal'))) \
        if uni else 'r7: no record at these inputs (nothing to certify)'
    return unitpairs.finish(NAME, uni, red, detail)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
