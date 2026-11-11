#!/usr/bin/env python3
"""gate_argorder - the lemma of rule ORDER-ARGS against cl's own code (L2 SOURCE).

    gate_argorder.py --gcc PATH [--gcc PATH ...] --cl PATH [--cl PATH ...] [--units units.json]      (inputs: link/unitpairs.py)

RULE ORDER-ARGS.  MSVC 2008 x86 evaluates the arguments of a call from the last to the first, the object of a member call after
them.  The fnvnx_order plugin makes GCC do the same, and the fnvnx_pi plugin writes every call whose arguments have effects in two or
more of them to `.fnvnx.argorder` of the object: `function TAB callee TAB a0|a1|... TAB k/n` (a_i: the callees of the calls inside
argument i, a member call's object is a_0; the call is number k, from 0, of the function's n calls of that callee, in source order).
This gate checks the rule on cl's code of the same unit.
UNIVERSE: every row of .fnvnx.argorder of every GCC object (no row skipped; a GCC object that cannot be read is one item
`unit:error`).  Item id `unit:function:callee:k/n`.
CERTIFIED: a row for which cl's object SHOWS the order and the order is the rule's.  Everything else is red and listed with the
reason; the reasons are exactly the facts the object does not give (no `not readable` is a pass):
  - no cl object of the unit / unreadable cl object
  - the function or a callee has no MSVC name (no row of .fnvnx.sympair / .fnvnx.names), or a symbol has two different names
  - the callee is also called inside an argument (the numbering k/n then does not say which call is the outer one)
  - the function's code is not delimited: its symbol is not defined once, at offset 0, as the only symbol of a COMDAT code section
    (a function in a shared section has no recorded end)
  - cl's code has another number of calls of the callee than the source (an inlined or merged call)
  - a callee of an argument is not once in the window (the relocations since the previous call of the callee, up to this call)
  - contradicted: an earlier argument's callee is called before a later argument's callee.
PROOF.  The code of a COMDAT function is the whole section; the targets of its direct calls and jumps are its REL32 relocations
(IMAGE_REL_I386_REL32, 0x14), in address order.  With n calls of the callee in cl's code (as many as the source), call number k and
the relocations between it and the previous call of the callee are the calls that belong to its arguments; each callee of an
argument stands once in that window, so each has one position; the rule holds iff for every two arguments i < j that both have
calls, every callee position of j is before every callee position of i (the object being argument 0).
ASSUMPTIONS.  A-rel32: in i386 COFF code a REL32 relocation is a direct call or jump target (data references use DIR32).  A-order:
the compiler does not reorder calls with side effects across each other, so the order of the call instructions is the order of
evaluation.  A-names: .fnvnx.sympair/.fnvnx.names give the MSVC name of every symbol of the source of the unit (internal names have
none: those rows are red).  Zero rows: green, universe 0 (detail says so).
DROPPED from legacy argordergate: counting `not readable` as a non-failure and the requirement of at least one confirmed site (a threshold);
the function's code taken as everything after its symbol in a section that may hold other functions.
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

NAME = 'argorder'
REL32 = 0x14


def name_map(sympair, names):
    """({symbol: msvc name}, {symbols with two different names}) from both sections; a nameless row names nothing"""
    m, bad = {}, set()
    for sym, ms in [(r[0], r[1]) for r in sympair if r[1]] + list(names):
        if m.setdefault(sym, ms) != ms:
            bad.add(sym)
    return m, bad


def calls_of(coff, fname):
    """names (bytes) the relative relocations of the function `fname` (bytes) name, in address order; or a str reason"""
    defs = [(i, s) for i, s in coff.symbols.items() if s.name == fname and s.cls == coffobj.CLS_EXTERNAL and s.sec > 0]
    if len(defs) != 1:
        return 'the function is not defined exactly once in the cl object'
    s = defs[0][1]
    sec = coff.sections[s.sec - 1]
    if not sec.chars & coffobj.SCN_LNK_COMDAT or not sec.chars & coffobj.SCN_CNT_CODE or s.value != 0:
        return 'the function is not at offset 0 of a COMDAT code section'
    for i, t in coff.symbols.items():
        if t.sec == s.sec and i != defs[0][0] and t.cls in (coffobj.CLS_EXTERNAL, coffobj.CLS_STATIC) \
                and not (t.cls == coffobj.CLS_STATIC and t.naux == 1 and t.value == 0 and t.type == 0):
            return 'the section of the function holds another symbol'
    return [coff.symbols[si].name for off, si, ty in sorted(sec.relocs) if ty == REL32]


def judge(seq, k, n, callee, args):
    """None when `seq` (cl's calls of the function, bytes) shows the rule at call k of n of `callee`; else the reason"""
    at = [i for i, x in enumerate(seq) if x == callee]
    if len(at) != n:
        return "cl's code has %d calls of the callee, the source %d" % (len(at), n)
    window = seq[at[k - 1] + 1 if k else 0:at[k]]
    pos = []
    for a in args:
        ps = []
        for c in a:
            if window.count(c) != 1:
                return 'a callee of an argument is not once in the window before the call'
            ps.append(window.index(c))
        pos.append(ps)
    for i in range(len(pos)):
        for j in range(i + 1, len(pos)):
            if pos[i] and pos[j] and max(pos[j]) > min(pos[i]):
                return 'contradicted: argument %d is evaluated before argument %d' % (i, j)
    return None


def unit_items(stem, gpath, cpath):
    try:
        elf = elfobj.read(gpath)
        rows = objsections.parse_argorder(objsections.section(elf, '.fnvnx.argorder'), stem + ':.fnvnx.argorder')
        sp = l2sections.parse_sympair(objsections.section(elf, '.fnvnx.sympair'), stem + ':.fnvnx.sympair')
        nm = objsections.parse_names(objsections.section(elf, '.fnvnx.names'), stem + ':.fnvnx.names')
    except ValueError as e:
        return 1, ['%s:error: %s' % (stem, e)]
    cl, why = None, 'no cl object for the unit'
    if cpath is not None and rows:
        try:
            cl = coffobj.read(cpath)
        except ValueError as e:
            why = 'the cl object is unreadable: %s' % e
    names, conflict = name_map(sp, nm)
    red = []
    for fn, callee, args, k, n in rows:
        iid = '%s:%s:%s:%d/%d' % (stem, fn, callee, k, n)
        syms = [fn, callee] + [c for a in args for c in a]
        reason = None
        if cl is None:
            reason = why
        elif any(s not in names for s in syms):
            reason = 'a symbol has no MSVC name'
        elif any(s in conflict for s in syms):
            reason = 'a symbol has two different MSVC names'
        elif any(callee in a for a in args):
            reason = 'the callee is also called inside an argument'
        else:
            seq = calls_of(cl, names[fn].encode('utf-8'))
            if isinstance(seq, str):
                reason = seq
            else:
                reason = judge(seq, k, n, names[callee].encode('utf-8'), [[names[c].encode('utf-8') for c in a] for a in args])
        if reason:
            red.append('%s: %s' % (iid, reason))
    return len(rows), red


def check(gcc, cl, units=None):
    """(universe, sorted red ids)"""
    uni, red = 0, []
    for stem, g, c in unitpairs.pairs(gcc, cl, units):
        u, r = unit_items(stem, g, c)
        uni += u
        red += r
    return uni, sorted(red)


def main(argv):
    a = unitpairs.parse_args(argv, 'gate_argorder.py', __doc__)
    try:
        uni, red = check(a.gcc, a.cl, a.units)
    except unitpairs.GateInputError as e:
        return unitpairs.fail(NAME, str(e))
    detail = ('argorder: %d of %d sites not certified' % (len(red), uni)) if uni else 'argorder: no site at these inputs (nothing to certify)'
    return unitpairs.finish(NAME, uni, red, detail)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
