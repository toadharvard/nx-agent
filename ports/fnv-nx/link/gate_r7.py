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
  virtual or not); the vfptr (neither side introduces one).  Unnamed bit-fields are not compared (CodeView
  does not list them); the members of an anonymous struct or union are the record's own on both sides.
Every other item is red and listed with its reason: `differs` (the first difference), `undecided` (a fact the data cannot give), `only-gcc`
(cl's debug info of the unit has no definition: no cl object, no CodeView, or no such key).  Undecided reasons, with the missing fact:
  - the record introduces a vfptr (both sides): the offset of the vfptr is not in CodeView (LF_VFUNCTAB has a type and no offset); it is
    not derived from the other offsets, which leave it undetermined
  - the size of a member's type is outside what CodeView states (codeview.py: PRIM, member pointers)
  - the key has several different definitions in the unit, in one cl object or between the cl objects of a source several units list
    (the definitions of all of them are collected and identical ones count once; unnamed types share keys) or the plugin could not name the type (`nokey:`)
  - the CodeView members or bases of the record could not be read
A record with a virtual base differs by rule: cl lays it out with a vbptr and a vbtable, GCC with a vptr and vtable-held offsets; the
two object models are different layouts and the native body would read the wrong ones.
CLAIM.  Size, and each member's offset, size and type key, are the compilers' own data for the same source, compared exactly.  The
alignment of a record is not part of the claim (cl's alignment is not in CodeView and is not compared).  The pairing key is defined as
follows (a fact of the gate's inputs): the key of a type is the plugin's name for it in the `.fnvnx.layout` line and the CodeView unique
name in the cl object; a key with several definitions in the unit is `undecided` (above).  Zero lines: green, universe 0 (detail says so).
DROPPED from legacy r7: the alignment comparison and its natural-alignment derivation; the vfptr offset taken as 0 when no member starts there (a derivation CodeView does not support); sizes and
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
GRec = collections.namedtuple('GRec', 'size fields bases vf nokey dup')


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
    return GRec(lay.size, tuple(fields), tuple(bases), vf,
                tuple(f[6:] for f in lay.flags if f.startswith('nokey:')), tuple(sorted(n for n, c in counts.items() if c > 1)))


def compare_one(g, c):
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
    return 'equal', ''


def unit_rows(stem, gpath, cpaths):
    """[(stem, key, verdict, reason)] of one unit"""
    try:
        lays = objsections.parse_layout(objsections.section(elfobj.read(gpath), '.fnvnx.layout'), stem + ':.fnvnx.layout')
    except ValueError as e:
        return [(stem, '', 'error', str(e))]
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

    out = []
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
                        verdict, reason = compare_one(gl[0], cl[0])
                    except ValueError as e:
                        verdict, reason = 'undecided', 'CodeView: %s' % e
                else:
                    verdict, reason = 'undecided', 'the key has %d gcc and %d cl different definitions in the unit' % (len(gl), len(cl))
        out.append((stem, k, verdict, reason))
    return out


def rows(gcc_objs, cl_objs, units=None):
    """Library entry (no GATE_OUT, no printing, no exit): the sorted list of (stem, key, verdict, reason), one row per (unit, type key)
    of the universe.  gcc_objs / cl_objs: lists of object FILE paths (directories are accepted too, as in unitpairs.pairs); units:
    path of units.json or None.  verdict: equal (reason ''), differs, undecided, only-gcc; reason: the first difference or the missing
    fact.  A GCC object that cannot be read or parsed gives one row (stem, '', 'error', message).  Raises unitpairs.GateInputError only
    for malformed declared inputs (missing path, bad units file, two objects of one stem)."""
    out = []
    for stem, g, c in unitpairs.pairs(gcc_objs, cl_objs, units):
        out += unit_rows(stem, g, c)
    return sorted(out)


def row_id(r):
    stem, key, verdict, reason = r
    return '%s:error: %s' % (stem, reason) if verdict == 'error' else '%s:%s: %s: %s' % (stem, key, verdict, reason)


def check(gcc, cl, units=None):
    """(universe, sorted red ids, Counter of verdicts)"""
    rs = rows(gcc, cl, units)
    return len(rs), sorted(row_id(r) for r in rs if r[2] != 'equal'), collections.Counter(r[2] for r in rs)


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
