#!/usr/bin/env python3
"""writeset: the STORE census of an image and the write-set query over it (the fact "no store reaches these bytes").
    writeset.run(text, T0, entries, ranges, outdir, name, rd, rset) -> summary; writes outdir/writeset.tsv and gate_writeset.json
    query(rows, lo, hi) -> {'exact': [writers whose static address range meets [lo, hi)], 'open': N, 'stack': N}

RULE (every instruction of the image's reached ranges, decoded once; both sweeps of pcsrc.scan_calls):
  a memory operand of an instruction is a STORE iff capstone says the operand is written (CS_AC_WRITE): mov/add/xchg/fstp/movs/stos/fxsave/... all carry the flag, no mnemonic list. push, call, pushf/pusha and enter
  write the stack through esp: class `stack`.
  The address class of a store operand is decided by its form alone:
    exact   no base, no index: the address is the displacement; the written bytes are [disp, disp + size).
    ranged  no base, an index register: [disp + idx*scale, +size): the address is a function of an unknown register, so it is OPEN for any query; its static anchor disp is recorded (a table store).
    stack   base esp with no index: the stack region (esp is the stack pointer by definition; the stack is disjoint from every static section: the loader's layout).
    open    any other base register, ebp included (ebp is a frame pointer only where the entry's code makes it one; that needs the typed dataflow of pcsrc, not the form): the address is whatever the register holds; OPEN for every byte range it may reach.
  A string store (rep stos / movs) writes through edi: open.
  A query's answer is `exact` writers + the number of open stores. The fact "no store writes [lo, hi)" is PROVEN iff exact is empty AND open == 0 (stack stores cannot reach it: the stack is outside every static section); open > 0 makes it
  OPEN, never assumed closed. Stores performed by imports (ReadFile into a buffer, memcpy, ...) are not instructions of the image: they are counted as `import_call_sites` (calls through the IAT) and bound the fact the same way.
Output rows: site<TAB>class<TAB>lo<TAB>hi<TAB>base (hex; lo/hi empty for open)."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capstone
from capstone import x86 as X
import pcsrc
from collections import Counter

M32 = 0xffffffff
STACK_MN = {'push', 'call', 'pushf', 'pushfd', 'pusha', 'pushad', 'enter'}


def sweep(img):
    """yields (address, instruction) for every reached instruction once, in the same two sweeps as pcsrc.scan_calls (streaming: only the address set is kept)"""
    seen = set()
    for a0, b0 in img.mer:
        a = a0
        while a < b0:
            g = img.dec(a, False)
            if not g: a += 1; continue
            if a not in seen: seen.add(a); yield a, g
            a += g.size
    for e in sorted(img.entries):
        a = e
        while img.reached(a):
            g = img.dec(a, False)
            if not g or a in seen: break
            seen.add(a); yield a, g
            if g.mnemonic in ('jmp', 'ret', 'retf', 'iret', 'iretd', 'hlt', 'ud2', 'int3'): break
            a += g.size


def classify(g):
    """-> [(class, lo, hi, base)] one row per written memory operand of the instruction"""
    rows = []
    if g.mnemonic in STACK_MN: rows.append(('stack', None, None, 'esp'))
    for o in g.operands:
        if o.type != X.X86_OP_MEM or not (o.access & capstone.CS_AC_WRITE): continue
        m = o.mem; size = max(o.size, 1)
        b = g.reg_name(m.base) if m.base else None; ix = g.reg_name(m.index) if m.index else None
        if not b and not ix:
            lo = m.disp & M32; rows.append(('exact', lo, lo + size, ''))
        elif not b and ix: rows.append(('ranged', m.disp & M32, None, ix))
        elif b == 'esp' and not ix: rows.append(('stack', None, None, b))
        else: rows.append(('open', None, None, b))
    return rows


def run(text, T0, entries, ranges, outdir, name, rd=None, rset=None, iat=None):
    img = pcsrc.Img(text, T0, entries, ranges, rd, rset)
    rows = []; calls_imp = 0; nins = 0
    for a, g in sweep(img):
        nins += 1
        for c, lo, hi, base in classify(g): rows.append((a, c, lo, hi, base))
        if g.mnemonic == 'call' and g.operands[0].type == X.X86_OP_MEM and not g.operands[0].mem.base and not g.operands[0].mem.index and iat is not None and (g.operands[0].mem.disp & M32) in iat: calls_imp += 1
    with open(outdir + '/writeset.tsv', 'w') as f:
        f.write('# site, class (exact|ranged|stack|open), lo, hi, base: every store operand of every reached instruction (tools/writeset.py)\n')
        for a, c, lo, hi, b in sorted(rows): f.write('%08x\t%s\t%s\t%s\t%s\n' % (a, c, '' if lo is None else '%08x' % lo, '' if hi is None else '%08x' % hi, b))
    cc = Counter(r[1] for r in rows)
    res = dict(schema='fnvnx-gate/1', name='l3.writeset.' + name, instructions=nins, stores=len(rows), by_class=dict(sorted(cc.items())), open=cc['open'] + cc['ranged'], import_call_sites=calls_imp, input=pcsrc.pin_of(outdir, name))
    json.dump(res, open(outdir + '/gate_writeset.json', 'w'), sort_keys=True)
    return res


def query(rows, lo, hi):
    """rows: [(site, class, lo, hi, base)] of one or more images -> the writers of [lo, hi), the open stores that bound the fact"""
    ex = [r for r in rows if r[1] == 'exact' and r[2] < hi and lo < r[3]]
    return {'exact': ex, 'open': sum(1 for r in rows if r[1] in ('open', 'ranged')), 'stack': sum(1 for r in rows if r[1] == 'stack')}
