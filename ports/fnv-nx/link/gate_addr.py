#!/usr/bin/env python3
"""Gate "addr" (fnvnx-gate/1, class F of VERDICT.md): no integer-to-pointer conversion of a native (GCC-compiled C++) object
escaped rule ADDR32 (docs/ORDERING.md, toolchain/addr/fnvnx_addr.cc), and the address-space layout makes the rule's one
remaining window harmless.
    python3 -I gate_addr.py [--layout FILE] OBJ...       env GATE_OUT; writes GATE_OUT/addr.json; exit 0 iff green
    rows(objs, layout=None) -> sorted [(obj_stem, symbol, reason)]    library form (no printing, no GATE_OUT, no exit); reason '' = certified

RULE.  A 32-bit guest address computed in an integer must be used as that wrapped 32-bit value.  Machine code cannot tell a
wrapped integer sum from in-bounds pointer arithmetic, so the fact comes from the compiler: the plugin routes every conversion
of a computed integer to a pointer, every pointer sum with a run-time offset or a constant offset of 2^16 or more, and every
memory reference through a pointer with such a displacement, through an asm barrier at the last GIMPLE point, verifies that
nothing escaped (a compile error otherwise) and writes the object's section `.fnvnx.addr` with one line per emitted function:
symbol, conversions seen, conversions routed (addrsec.py).  The gate reads those records.
UNIVERSE.  For every object given: the object itself (row `(stem, '', reason)`: certified iff it has the section and the section
parses) and every function symbol it defines (STT_FUNC or STT_GNU_IFUNC placed in a section; row `(stem, symbol, reason)`).
Aliases, thunks and clones are function symbols too; the plugin writes `seen = routed = 0` for those without a GIMPLE body.  The
layout is one more item, row `('', 'layout', reason)`, present iff at least one object or `--layout` is given: with zero objects and
no layout the universe is 0 (green, as before); with objects and no layout the item is uncertified, so the gate cannot be green
without a layout.  OBJ stems are file names without the extension; two objects with one stem are an error (ValueError; the CLI
exits 1 without a gate file).
CERTIFIED.  A function with exactly one record in its object and seen == routed.  Uncertified: no record, two records, seen !=
routed, and every function of an object whose section is missing or malformed (the reason names the first error).  An object that
is not readable ILP32 AArch64 ELF is an uncertified item.  Records of symbols the object does not define as functions are ignored
(they cannot certify a function).  The layout item is certified iff the layout file passes LAYOUT below.
LAYOUT (the checked fact that gives the address-space edges; `--layout FILE`, emitted by L1 at link time).  A JSON object with exactly
the keys `schema` (the string "fnvnx-layout/1") and `regions` (a list); each region is an object with exactly the keys `name` (a
non-empty string), `lo` and `hi` (integers, not booleans, `0 <= lo < hi <= 2^32`, **hi exclusive**: the region holds the
addresses lo .. hi-1), `prot` (one of "---", "r--", "rw-", "r-x") and `kind` (one of "image", "heap", "stack", "tls", "host",
"guard").  Any other shape (extra or missing key, duplicate key, other type, a float, NaN, trailing text, not UTF-8) is rejected.
Checks, in this order, the first failing one is the reason (`layout: <check>: <detail>`): `file` (unreadable), `shape` (the
form above), `schema`, `partition` (sorted by lo, the regions cover [0, 2^32) exactly: the first lo is 0, each lo is the previous
hi, the last hi is 2^32: no hole, no overlap), `guard` (every region that intersects [0, 0x10000) or [0xFFFF0000, 2^32) has prot
"---" and kind "guard").  GUARD = 0x10000 is the plugin's LIMIT (toolchain/addr/fnvnx_addr.cc, rule C): a displacement the
plugin leaves unrouted is below 2^16 in magnitude, so a sum p + d that crosses 0 or 2^32 starts in the low or the high guard,
where no object lives and no access is allowed, and faults on x86 as it does here (the port's ILP32 address space ends at
2^32: an address above it is unmapped).  Everything larger is routed and wraps exactly as x86 does.
PROOF.  seen == routed in a function means every conversion at the last GIMPLE point had an opaque (asm) operand, so the
expander and the RTL passes that follow see a finished 32-bit register: no sum exists that an addressing mode could absorb.
INPUT FACTS AND RULES.  (1) Input fact: the record is written by the plugin built from the tracked source and loaded into every compile
of the object (the build gate `build` checks that the recipe loads it).  (2) Assumption ASM-006 (the exe makes no wild accesses; array
indices in bounds): a variable index into a declared array (a global or a local, whose address is a link-time or frame constant) stays
inside it.  (3) Assumption ASM-001 (the pinned compiler is correct): after the barrier no later pass of GCC folds through an `asm` (the
documented semantics of an asm statement).  (4) Rule: `.cold` parts of functions (-freorder-blocks-and-partition) are function symbols
without a record and are uncertified, so the recipe must not enable that partitioning.  (5) Input fact: the layout file describes the
layout the linked image is run with (L1 emits it from the link as layout.json, fnvnx-layout/1; this gate checks its form, not the run).
Pure: a function of the object bytes and the layout file."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'contract'))
import a64obj  # noqa: E402
import addrsec  # noqa: E402
import gatefile  # noqa: E402

NAME = 'addr'
GUARD = 0x10000
TOP = 1 << 32
SCHEMA = 'fnvnx-layout/1'
PROTS = ('---', 'r--', 'rw-', 'r-x')
KINDS = ('image', 'heap', 'stack', 'tls', 'host', 'guard')


def _pairs(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ValueError('duplicate key %r' % k)
        d[k] = v
    return d


def _no_constant(name):
    raise ValueError('constant %s' % name)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def layout_reason(path):
    """'' when the layout file passes every check, else `layout: <check>: <detail>`"""
    try:
        with open(path, 'rb') as fh:
            raw = fh.read()
    except OSError as e:
        return 'layout: file: %s' % e.strerror
    try:
        doc = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_no_constant, parse_float=_no_constant)
    except (ValueError, UnicodeDecodeError, RecursionError) as e:
        return 'layout: shape: not strict JSON (%s)' % str(e)[:80]
    if not isinstance(doc, dict) or sorted(doc) != ['regions', 'schema'] or not isinstance(doc['regions'], list):
        return 'layout: shape: the document is not {"schema", "regions": [...]}'
    regs = []
    for n, r in enumerate(doc['regions']):
        if (not isinstance(r, dict) or sorted(r) != ['hi', 'kind', 'lo', 'name', 'prot'] or not isinstance(r['name'], str) or not r['name']
                or not _is_int(r['lo']) or not _is_int(r['hi']) or not 0 <= r['lo'] < r['hi'] <= TOP
                or r['prot'] not in PROTS or r['kind'] not in KINDS):
            return 'layout: shape: region %d is not {"name", "lo", "hi", "prot", "kind"} of the documented types' % n
        regs.append((r['lo'], r['hi'], r['prot'], r['kind'], r['name']))
    if doc['schema'] != SCHEMA:
        return 'layout: schema: %r, expected %r' % (doc['schema'], SCHEMA)
    regs.sort()
    at = 0
    for lo, hi, _p, _k, name in regs:
        if lo != at:
            return 'layout: partition: %s at 0x%x (%s starts at 0x%x)' % ('hole' if lo > at else 'overlap', min(lo, at), name, lo)
        at = hi
    if at != TOP:
        return 'layout: partition: hole at 0x%x (the regions end at 0x%x, not 2^32)' % (at, at)
    for lo, hi, p, k, name in regs:
        if (lo < GUARD or hi > TOP - GUARD) and (p != '---' or k != 'guard'):
            return 'layout: guard: region %s [0x%x, 0x%x) intersects [0, 0x%x) or [0x%x, 2^32) and is %s %s, not --- guard' % (
                name, lo, hi, GUARD, TOP - GUARD, p, k)
    return ''


def stem(path):
    return os.path.splitext(os.path.basename(path))[0]


def judge_object(path):
    """-> [(symbol, reason)] sorted; symbol '' is the object"""
    try:
        o = a64obj.read(path)
    except ValueError as e:
        return [('', str(e))]
    out = []
    err = ''
    try:
        rs = addrsec.from_sections(o.sections, path)
        if rs is None:
            err = '%s: no %s section' % (path, addrsec.SECTION)
    except ValueError as e:
        rs = None
        err = str(e)
    out.append(('', err))
    recs = {}
    for sym, seen, routed in rs or []:
        recs.setdefault(sym, []).append((seen, routed))
    for n in sorted(n for _, _, _, ns in o.functions for n in ns):
        r = recs.get(n, [])
        if rs is None:
            why = 'object has no usable %s' % addrsec.SECTION
        elif len(r) != 1:
            why = '%d records' % len(r)
        elif r[0][0] != r[0][1]:
            why = 'seen %d routed %d' % r[0]
        else:
            why = ''
        out.append((n, why))
    return sorted(out)


def rows(objs, layout=None):
    out, seen = [], {}
    for p in objs:
        st = stem(p)
        if st in seen:
            raise ValueError('two objects with the stem %r: %s and %s' % (st, seen[st], p))
        seen[st] = p
        out += [(st, sym, why) for sym, why in judge_object(p)]
    if objs or layout is not None:
        out.append(('', 'layout', layout_reason(layout) if layout is not None else 'layout: file: no --layout given'))
    return sorted(out)


def main(argv):
    objs, layout, i = [], None, 0
    while i < len(argv):
        a = argv[i]
        if a == '--layout' and i + 1 < len(argv) and layout is None:
            layout = argv[i + 1]
            i += 2
            continue
        if a.startswith('--'):
            print('usage: gate_addr.py [--layout FILE] OBJ...', file=sys.stderr)
            return 1
        objs.append(a)
        i += 1
    try:
        rs = rows(objs, layout)
    except ValueError as e:
        print('addr: %s' % e, file=sys.stderr)
        return 1
    ids, bad = [], {}
    paths = {stem(p): p for p in objs}
    for st, sym, why in rs:
        fid = 'layout' if (st, sym) == ('', 'layout') else (paths[st] + (':' + sym if sym else ''))
        ids.append(fid)
        if why:
            bad[fid] = why
    detail = ('%d items in %d objects, every function has a record with seen == routed, layout checked' % (len(ids), len(objs))
              if not bad else '%d of %d items uncertified: %s' % (len(bad), len(ids), '; '.join(bad[k] for k in sorted(bad)[:3])[:300]))
    if not ids:
        detail = 'no item at these inputs (nothing to certify)'
    gatefile.write(NAME, len(ids), len(ids) - len(bad), sorted(bad), detail)
    print('addr: %s %d/%d %s' % ('red' if bad else 'green', len(ids) - len(bad), len(ids), detail[:200]))
    return 2 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
