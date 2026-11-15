#!/usr/bin/env python3
"""Synthetic check of contract/gatecert.py (the proof of gate_reach `gate_cert`): objdump text of toy ILP32 objects shaped like the gl4 case
(translator result, a program table, a blob, a tail through a parameter).  Selftests: covered producers certified; a constant, an unknown
caller (outside the universe), an unknown call result, a clobbered register, an unbounded index, an address taken outside a producer, no site,
a join with one bad path."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import gatecert

fails = []


def check(name, got, want):
    if got != want:
        fails.append('%s: got %r, want %r' % (name, got, want))


def fn(name, insns, start=0):
    """insns [(mnemonic+ops, reloc or None)] -> objdump -dr text of one function at addresses start, start+4, ..."""
    out = ['', '%016x <%s>:' % (start, name)]
    for i, (text, rel) in enumerate(insns):
        out.append('  %x:\t%s' % (start + 4 * i, text))
        if rel:
            out.append('\t\t\t%x: %s\t%s' % (start + 4 * i, rel[0], rel[1]))
    return out


def obj(name, funcs, syms, data=()):
    """funcs [(name, insns)]; syms [(name, section, size, global)]; data [(section, offset, type, symbol)]"""
    out = ['', '%s:     file format elf32-littleaarch64' % name, '', 'SYMBOL TABLE:']
    for n, sec, size, g in syms:
        out.append('00000000 %s %s\t%08x %s' % (('g     O' if g else 'l     O') if sec != '*UND*' else '       ', sec, size, n))
    for n, _ in funcs:
        out.append('00000000 g     F .text\t00000040 %s' % n)
    out += ['', 'Disassembly of section .text:']
    a = 0
    for n, insns in funcs:
        out += fn(n, insns, a)
        a += 4 * len(insns) + 0x10
    rs = {}
    for sec, off, typ, sym in data:
        rs.setdefault(sec, []).append((off, typ, sym))
    for sec, rows in rs.items():
        out += ['', 'RELOCATION RECORDS FOR [%s]:' % sec, 'OFFSET   TYPE              VALUE']
        out += ['%08x %s %s' % r for r in rows]
    return '\n'.join(out) + '\n'


CALL, PG, ADD, LD = 'R_AARCH64_P32_CALL26', 'R_AARCH64_P32_ADR_PREL_PG_HI21', 'R_AARCH64_P32_ADD_ABS_LO12_NC', 'R_AARCH64_P32_LDST32_ABS_LO12_NC'
SAVE, REST = ('stp\tx29, x30, [sp, #-16]!', None), ('ldp\tx29, x30, [sp], #16', None)
RET = ('ret', None)
PRODUCERS = [dict(symbol='translate', kind='function'), dict(symbol='progs', kind='data'), dict(symbol='ffp_blob', kind='data')]
SYMS = [('glsrc', '*UND*', 0, True), ('translate', '*UND*', 0, True), ('progs', '.rodata.progs', 20, True), ('ffp_blob', '.rodata.ffp', 100, True),
        ('rogue', '.rodata.rogue', 8, True)]


def base():
    return [
        ('from_glsl', [SAVE, ('bl\t0 <glsrc>', (CALL, 'glsrc')), REST, RET]),
        ('d3d_path', [SAVE, ('bl\t0 <translate>', (CALL, 'translate')), ('bl\t0 <from_glsl>', (CALL, 'from_glsl')), REST, RET]),
        ('program', [SAVE, ('adrp\tx1, 0 <program>', (PG, 'progs')), ('add\tx1, x1, #0x0', (ADD, 'progs')), ('ldr\tw0, [x1]', None),
                     ('bl\t0 <from_glsl>', (CALL, 'from_glsl')), REST, RET]),
        ('blob', [SAVE, ('adrp\tx0, 0 <blob>', (PG, 'ffp_blob')), ('add\tx0, x0, #0x0', (ADD, 'ffp_blob')), ('bl\t0 <from_glsl>', (CALL, 'from_glsl')), REST, RET]),
    ]


def run(funcs, syms=SYMS, data=(), site='glsrc', producers=PRODUCERS):
    return gatecert.prove(gatecert.parse_objdump(obj('a.o', funcs, syms, data)), site, 0, producers)


r, callers, vals = run(base())
check('green', (r, vals), ([], ['addr:ffp_blob', 'data:progs', 'prod:translate']))
check('the site and the three callers of from_glsl', len(callers), 4)

# a constant argument
f = base() + [('bad', [SAVE, ('mov\tw0, #0x0', None), ('bl\t0 <from_glsl>', (CALL, 'from_glsl')), REST, RET])]
r, _, _ = run(f)
check('constant', sorted({x[0] for x in r}), ['constant'])
# a caller outside the universe: data not a producer
f = base() + [('rogue_c', [SAVE, ('adrp\tx0, 0 <r>', (PG, 'rogue')), ('add\tx0, x0, #0x0', (ADD, 'rogue')), ('bl\t0 <from_glsl>', (CALL, 'from_glsl')), REST, RET])]
r, _, _ = run(f)
check('caller outside the universe', [(x[0], x[2]) for x in r], [('not-producer', 'rogue+0')])
# a call result of a function that is not a producer
f = base() + [('c2', [SAVE, ('bl\t0 <other>', (CALL, 'other')), ('bl\t0 <from_glsl>', (CALL, 'from_glsl')), REST, RET])]
r, _, _ = run(f)
check('unknown call result', [(x[0], x[2]) for x in r], [('not-producer', 'other')])
# a register clobbered by a call between the producer and the site
f = base() + [('c3', [SAVE, ('bl\t0 <translate>', (CALL, 'translate')), ('mov\tw1, w0', None), ('bl\t0 <other>', (CALL, 'other')),
                      ('bl\t0 <from_glsl>', (CALL, 'from_glsl')), REST, RET])]
r, _, _ = run(f)
check('call-result of the later call', sorted({x[0] for x in r}), ['not-producer'])
# a callee-saved copy survives the call: x19 = translate(); other(); w0 = w19; from_glsl(w0)
f = base() + [('c4', [SAVE, ('bl\t0 <translate>', (CALL, 'translate')), ('mov\tw19, w0', None), ('bl\t0 <other2>', (CALL, 'other2')), ('mov\tw0, w19', None),
                      ('bl\t0 <from_glsl>', (CALL, 'from_glsl')), REST, RET])]
r, _, v = run(f)
check('callee-saved copy', r, [])
# a join with one bad path: cbz skips to the join, w0 is translate() on one path and a constant on the other
f = base() + [('c5', [SAVE, ('bl\t0 <translate>', (CALL, 'translate')), ('cbz\tw1, 18 <c5+0x8>', None), ('mov\tw0, #0x1', None),
                      ('bl\t0 <from_glsl>', (CALL, 'from_glsl')), REST, RET])]
# the instruction at address of `bl from_glsl` is index 4 of c5; objects place functions sequentially: compute the target from the layout
text = obj('a.o', f, SYMS)
start = int([l for l in text.split('\n') if l.endswith('<c5>:')][0].split()[0], 16)
bl_addr = start + 4 * 4
text = text.replace('cbz\tw1, 18 <c5+0x8>', 'cbz\tw1, %x <c5+0x10>' % bl_addr)
r, _, _ = gatecert.prove(gatecert.parse_objdump(text), 'glsrc', 0, PRODUCERS)
check('join with a constant path', sorted({x[0] for x in r}), ['constant'])
# an unbounded index and a bounded constant offset
f = base() + [('c6', [SAVE, ('adrp\tx1, 0 <c6>', (PG, 'progs')), ('add\tx1, x1, #0x0', (ADD, 'progs')), ('ldr\tw0, [x1, x2, lsl #2]', None),
                      ('bl\t0 <from_glsl>', (CALL, 'from_glsl')), REST, RET])]
r, _, _ = run(f)
check('unbounded index', sorted({x[0] for x in r}), ['index-unbounded'])
f = base() + [('c7', [SAVE, ('adrp\tx1, 0 <c7>', (PG, 'progs')), ('add\tx1, x1, #0x0', (ADD, 'progs')), ('ldr\tw0, [x1, #20]', None),
                      ('bl\t0 <from_glsl>', (CALL, 'from_glsl')), REST, RET])]
r, _, _ = run(f)
check('offset past the size of the symbol', [(x[0], x[2]) for x in r], [('not-producer', 'progs+20')])
# address taken: in a producer data symbol (the table slot) it is accepted; elsewhere it is red
dsyms = SYMS + [('slots', '.data.slots', 16, True), ('other_tab', '.data.other', 16, True)]
r, _, _ = run(base(), dsyms, data=[('.data.slots', 4, 'R_AARCH64_P32_ABS32', 'from_glsl')], producers=PRODUCERS + [dict(symbol='slots', kind='data')])
check('address taken in a producer slot', r, [])
r, _, _ = run(base(), dsyms, data=[('.data.other', 4, 'R_AARCH64_P32_ABS32', 'from_glsl')])
check('address taken elsewhere', sorted({x[0] for x in r}), ['address-taken'])
r, _, _ = run(base(), dsyms, data=[('.debug_info', 4, 'R_AARCH64_P32_ABS32', 'from_glsl')])
check('debug relocations do not count', r, [])
# no call of the site
r, _, _ = run(base(), site='nothere')
check('no site', [x[0] for x in r], ['no-site'])
# a parameter with no caller at all
r, _, _ = run([('from_glsl', [SAVE, ('bl\t0 <glsrc>', (CALL, 'glsrc')), REST, RET])])
check('no caller', [x[0] for x in r], ['no-caller'])
# the argument is never set (callee-saved register at the entry)
f = base() + [('c8', [SAVE, ('mov\tw0, w20', None), ('bl\t0 <from_glsl>', (CALL, 'from_glsl')), REST, RET])]
r, _, _ = run(f)
check('uninitialized', sorted({x[0] for x in r}), ['uninitialized'])

if fails:
    print('\n'.join(fails))
    sys.exit(1)
print('ok')
