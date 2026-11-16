#!/usr/bin/env python3
"""Helpers of the byte accounting (used by modlift.py): no-return sites and rows, ranges, the least fixed point of non-returning entries.
The accounting classes: Every byte gets one accounting class (a breakdown, NOT a partition of the properties may-execute / may-be-read: see tools/CODEMAP.md, rule DIC):
1 instruction of a decomp function, 2 instruction of a non-decomp entry, 3 table/data named by a relocation, 4 padding,
5 dead code inside a decomp function, 6 dead code in a gap (no entry, no relocation names it), 8 data named by a PE data directory,
7 hole between executable sections (not part of the universe), 0 unexplained (build error).
Dead = a run no path reaches that decodes completely from its first byte (no bytes left over), ends at a terminator or at the
next reached byte, has relocated fields only at instruction operand fields, and has no relocated dword (from a live site) and no
call pointing into it. Entries made by rule R3 alone (kind ptr) or by the closure under direct targets (kind call/tail; universe.r3: a value whose bytes no rule above reached as an instruction start) are bodies re-read
from bytes that are already classified exactly once here; they add no class of their own and take no owner cut in codemap.tsv. Where such a body overlaps
data named by a relocation (class 3) or instructions of another entry, the byte keeps its class (instruction of a committed entry, else relocation-named
data): class precedence is for accounting only, the entry still exists and its undecodable bytes raise #UD at run time.
Output: <outdir>/codemap.tsv (start, length, class, owner entry or referencing relocation)."""
import os, re, bisect
from collections import Counter
CLS = {7: 'hole', 8: 'data-pe-header', 1: 'entry-nondecomp', 2: 'entry-nondecomp', 3: 'data-reloc', 4: 'padding', 9: 'data-unreached', 0: 'UNEXPLAINED'}
TERM = {'ret', 'retf', 'jmp', 'int3', 'ud2', 'iret', 'iretd'}

NRIMP = {'ExitProcess', 'ExitThread', '_exit', 'exit', 'abort', '_amsg_exit', '_CxxThrowException', 'longjmp'}      # imports documented as never returning
def nr_sites(U, imports):
    """({`call [iat]` insn addresses}, {`jmp [iat]` insn addresses}) whose IAT slot imports a documented never-returning function."""
    slots = {a: n.split('!')[-1] for a, n in imports.items() if n.split('!')[-1] in NRIMP}; calls = set(); jmps = set(); names = {}
    for r in U.relocs:
        if U.intext(r) and U.u32(r) in slots and U.intext(r - 2):
            b = U.text[r - 2 - U.T0:r - U.T0]
            if b == b'\xff\x15': calls.add(r - 2); names[r - 2] = slots[U.u32(r)]
            elif b == b'\xff\x25': jmps.add(r - 2); names[r - 2] = slots[U.u32(r)]
    return calls, jmps, names
def nr_rows(N, res, names, seeds, T0=0):
    """[(entry, reason)] sorted: import:<names> | calls-to-noreturn | ud | fault | infinite-loop | seed:thrower."""
    out = []
    for e in sorted(N):
        ext, deps, ends = res[e]
        imp = sorted({names[a] for a in ext if a in names})
        if e in seeds: r = 'seed:thrower'
        elif 'nrimp' in ends: r = 'import:' + ','.join(imp)
        elif 'callN' in ends or any(isinstance(x, int) for x in ends): r = 'calls-to-noreturn'
        elif 'ud' in ends: r = 'ud'
        elif 'fault' in ends: r = 'fault'
        else: r = 'infinite-loop'
        out.append((e, r))
    return out
def ranges(ext, size):
    """merged half-open byte ranges of an extent (instruction granular): [(start, end)]."""
    out = []
    for a in sorted(ext):
        e = a + size(a)
        if out and out[-1][1] == a: out[-1][1] = e
        else: out.append([a, e])
    return out
def rng_str(r): return ','.join('%x-%x' % (a, b) for a, b in r)
def noreturn_lfp(E, trav, seeds):
    """N = least set such that an entry is in N iff no path of its descent reaches a ret, every path ends in a call to a member of N, a call/jmp to a
    never-returning import, `ud`, a fault, an infinite loop or a tail jump (or fall-through) into a member of N, and no path ends in an unknown
    indirect jump. trav(e, N) -> (extent, deps, ends): ends holds 'ret' | 'bad' | 'ud' | 'fault' | 'nrimp' | 'callN' | entry addresses (tail edges).
    Returns (N, {entry: (extent, deps, ends)} computed with the final N)."""
    N = set(seeds); res = {}; uses = {}; dirty = set(E)
    while dirty:
        new = []
        for e in sorted(dirty):
            ext, deps, ends = trav(e, N); res[e] = (ext, deps, ends)
            for x in deps: uses.setdefault(x, set()).add(e)
            if e not in N and 'ret' not in ends and 'bad' not in ends and all(x in N for x in ends if isinstance(x, int)): new.append(e)
        dirty = set()
        for e in new: N.add(e)
        for e in new: dirty |= uses.get(e, set())
    return N, res
