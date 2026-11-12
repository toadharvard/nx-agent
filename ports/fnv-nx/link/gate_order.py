#!/usr/bin/env python3
"""Gate "order" (fnvnx-gate/1, class B of VERDICT.md): every memory access of a native (GCC-compiled C++) object follows the
TSO map R9 (docs/ORDERING.md), decided on the machine code, independent of the compiler plugin's ordering pass.
    python3 -I gate_order.py OBJ...        env GATE_OUT; writes GATE_OUT/order.json; exit 0 iff green
    rows(objs) -> sorted [(obj_stem, item, kind)]        kind '' = certified (library form: no printing, no GATE_OUT, no exit)
    functions(objs) -> sorted [(obj_stem, function, reason)]    reason '' iff every item of the function is certified

UNIVERSE.  For every object given (rows of a64order.scan, rules in a64order.py): the object (item ``, certified iff its
`.fnvnx.frame` section is present and parses), every function symbol that is a usable code range (item `FUNC`, certified iff every
symbol of the function has the one same record), every memory-access instruction of every executable byte (item `FUNC+0xOFF`;
loads, stores, exclusives, atomics, literal loads; prefetch hints are no access), every word that cannot be decoded (it might be one),
every function symbol of size 0 or overlapping another, and every instruction that hands a frame address out of the function
(item `FUNC+0xOFF/escape`: stored, passed in x0-x8 to a call, returned, used as an index).  Code in no function symbol is named by
its section.  An object that is not readable ILP32 AArch64 ELF is one uncertified item named by the object.
CERTIFIED by the rules of a64order.py: O ordered forms, F frame (record R1 of the compiler: the address-taken variables of the
frame), L linker or read-only load, R fenced run; an escape of a frame address that points into a recorded variable.
PROOF.  Under TSO every load is acquire-like and every store release-like, so a thread-visible access must be ordered with all earlier
loads (loads) or all earlier accesses (stores).  O: the instruction is itself ordered.  F: the access lies in the function's own frame
and overlaps no variable whose address the compiler took, and the gate itself checked that no frame address leaves the function
unless it points into such a variable, so no other thread has an address of the slot.  L: the loaded word never changes after link
or load.  R: the plain access is bracketed by `dmb` on every path, so the barrier orders it against everything the statement does
not itself contain.
INPUT FACTS AND RULES (the full text is in a64order.py).  R1: the `.fnvnx.frame` record names every variable whose address the compiler
computes for a purpose the machine code does not show (the record is produced by the pinned compiler's plugin, ASM-001: the gate reads
it for WHICH variables are address-taken, and checks the machine code for everything else: offsets, sizes of accesses, escapes of
computed frame addresses).  Assumption ASM-006 (the exe makes no wild accesses; array indices in bounds): pointer arithmetic stays inside
the object it starts in.  STACK, ROBND, GOT, RWX are input facts (objects.json; layout.json fnvnx-layout/1 prot and PT_GNU_RELRO); RUN is
part of rule R9 (docs/ORDERING.md).
Pure: a function of the object bytes."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'contract'))
import a64order  # noqa: E402
import gatefile  # noqa: E402

NAME = 'order'


def stem(path):
    return os.path.splitext(os.path.basename(path))[0]


def scan_object(path):
    """-> (rows [(item, kind)], stats {rule name: n}); an unreadable object is the one row ('', the reason)"""
    try:
        s = a64order.scan(path)
    except ValueError as e:
        return [('', str(e))], {}
    return list(s.rows), s.stats


def rows(objs):
    out = []
    for p in objs:
        out += [(stem(p), item, kind) for item, kind in scan_object(p)[0]]
    return sorted(out)


def functions(objs):
    """(obj_stem, function symbol, reason): reason is '' iff the function item and every instruction item inside it are
    certified and the object item is; else the first (sorted) kind found.  Code in no function symbol is a `function` named by
    its section."""
    out = []
    for p in objs:
        rs = scan_object(p)[0]
        obj = [k for item, k in rs if item == ''][0]
        by = {}
        for item, kind in rs:
            if item == '':
                continue
            fn = item.split('+0x')[0] if '+0x' in item else item
            by.setdefault(fn, []).append(kind)
        for fn, ks in by.items():
            bad = sorted(k for k in ks if k) or ([obj] if obj else [])
            out.append((stem(p), fn, bad[0] if bad else ''))
    return sorted(out)


def main(argv):
    items, bad, stats = [], {}, {}
    for p in argv:
        rs, st = scan_object(p)
        for item, kind in rs:
            fid = '%s:%s' % (p, item) if item else p
            items.append(fid)
            if kind:
                bad[fid] = kind
        for k, v in st.items():
            stats[k] = stats.get(k, 0) + v
    kinds = {}
    for v in bad.values():
        kinds[v] = kinds.get(v, 0) + 1
    detail = '%d items in %d objects; certified by rule: %s' % (
        len(items), len(argv), ', '.join('%s %d' % kv for kv in sorted(stats.items())) or 'none')
    if bad:
        detail = '%d of %d items uncertified (%s); first %s; %s' % (
            len(bad), len(items), ', '.join('%s %d' % kv for kv in sorted(kinds.items()))[:160], sorted(bad)[0][:160], detail)
    gatefile.write(NAME, len(items), len(items) - len(bad), sorted(bad), detail)
    print('order: %s %d/%d %s' % ('red' if bad else 'green', len(items) - len(bad), len(items), detail[:300]))
    return 2 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
