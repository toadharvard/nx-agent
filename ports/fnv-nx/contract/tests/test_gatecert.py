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
check('unbounded index', sorted({x[0].split(':')[0] for x in r}), ['index-unbounded'])
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

# ---- a variable index: (a) a dominating unsigned compare, (b) a loop induction variable bounded by its exit test
ADRP19 = [SAVE, ('adrp\tx19, 0 <c>', (PG, 'progs')), ('add\tx19, x19, #0x0', (ADD, 'progs'))]
SITE = [('ldr\tw0, [x19, w20, uxtw #2]', None), ('bl\t0 <from_glsl>', (CALL, 'from_glsl'))]


def idxfn(name, body):
    """a function placed FIRST in the object (start address 0), so branch targets are the addresses 4*index"""
    return [(name, body)] + base()


def codes(r):
    return sorted({x[0].split(':')[0] for x in r})


for cmp_c, cond, want in ((5, 'hs', []), (6, 'hs', ['index-unbounded']), (4, 'hs', [])):
    # 0 SAVE 1 adrp 2 add 3 cmp 4 b.cond -> 8 (REST) 5 ldr 6 bl 7 REST(unused) ... 8 REST 9 RET
    body = ADRP19 + [('cmp\tw20, #0x%x' % cmp_c, None), ('b.%s\t20 <c+0x20>' % cond, None)] + SITE + [REST, REST, RET]
    r, _, _ = run(idxfn('c9', body))
    check('(a) cmp #%d b.%s over 5 elements' % (cmp_c, cond), codes(r), want)
# b.lo to the access: taken edge bounds the index
body = ADRP19 + [('cmp\tw20, #0x5', None), ('b.lo\t18 <c+0x18>', None), ('b\t28 <c+0x28>', None)] + SITE + [REST, RET]
r, _, _ = run(idxfn('c9', body))
check('(a) b.lo taken', codes(r), [])
# b.hi fallthrough: index <= 4 < 5
body = ADRP19 + [('cmp\tw20, #0x4', None), ('b.hi\t20 <c+0x20>', None)] + SITE + [REST, REST, RET]
r, _, _ = run(idxfn('c9', body))
check('(a) b.hi fallthrough bound c+1', codes(r), [])
# the bound is on another register that was copied: mov w20, w21 after cmp w21
body = ADRP19 + [('cmp\tw21, #0x5', None), ('b.hs\t24 <c+0x24>', None), ('mov\tw20, w21', None)] + SITE + [REST, REST, RET]
r, _, _ = run(idxfn('c9', body))
check('(a) through a copy', codes(r), [])
# no compare at all
body = ADRP19 + SITE + [REST, RET]
r, _, _ = run(idxfn('c9', body))
check('unbounded: no compare', codes(r), ['index-unbounded'])
# a compare that does not dominate: the access is also reached by a path that skips it
body = ADRP19 + [('cbz\tw22, 1c <c+0x1c>', None), ('cmp\tw20, #0x5', None), ('b.hs\t2c <c+0x2c>', None)] + [('nop', None)] + SITE + [REST, REST, RET]
r, _, _ = run(idxfn('c9', body))
check('unbounded: the compare does not dominate', codes(r), ['index-unbounded'])
# (b) loop from 0 to N: 0 SAVE 1 adrp 2 add 3 mov w20,#0 4 ldr(head) 5 bl 6 add 7 cmp 8 b.lo head(16) 9 REST 10 RET
for n, want in ((5, []), (6, ['index-unbounded']), (3, [])):
    body = ADRP19 + [('mov\tw20, #0x0', None)] + SITE + [('add\tw20, w20, #0x1', None), ('cmp\tw20, #0x%x' % n, None), ('b.lo\t10 <c+0x10>', None), REST, RET]
    r, _, _ = run(idxfn('c9', body))
    check('(b) loop 0..%d over 5 elements' % n, codes(r), want)
# a loop whose step is not 1 / whose exit test is missing
body = ADRP19 + [('mov\tw20, #0x0', None)] + SITE + [('add\tw20, w20, #0x1', None), ('b\t10 <c+0x10>', None), REST, RET]
r, _, _ = run(idxfn('c9', body))
check('(b) a loop without an exit test', codes(r), ['index-unbounded'])
# a loop that starts from an unknown value
body = ADRP19 + SITE + [('add\tw20, w20, #0x1', None), ('cmp\tw20, #0x5', None), ('b.lo\tc <c+0xc>', None), REST, RET]
r, _, _ = run(idxfn('c9', body))
check('(b) a loop from an unknown start (the entry path is unbounded)', codes(r), ['index-unbounded'])

# ---------------------------------------------------------------- the out-parameter shape (a64) and x86-64 (one rule set, two ISAs)
OUTP = [dict(symbol='translate', kind='function'), dict(symbol='progs', kind='data'), dict(symbol='produce', kind='function', out=[dict(arg=1, size=8)])]
OSYMS = SYMS + [('produce', '*UND*', 0, True), ('other', '*UND*', 0, True)]
BLS, BLP = ('bl\t0 <glsrc>', (CALL, 'glsrc')), ('bl\t0 <produce>', (CALL, 'produce'))
PRO, EPI = ('stp\tx29, x30, [sp, #-32]!', None), ('ldp\tx29, x30, [sp], #32', None)


def arun(body, producers=OUTP):
    return gatecert.prove(gatecert.parse_objdump(obj('a.o', [('use', body)], OSYMS)), 'glsrc', 0, producers)


G = [PRO, ('add\tx1, sp, #0x10', None), BLP, ('ldr\tx0, [sp, #16]', None), BLS, EPI, RET]
r, _, v = arun(G)
check('a64 out-param green', (codes(r), v), ([], ['prod:produce']))
r, _, _ = arun([PRO, ('add\tx1, sp, #0x10', None), BLP, ('str\tx2, [sp, #16]', None), ('ldr\tx0, [sp, #16]', None), BLS, EPI, RET])
check('a64 intervening store', codes(r), ['out-store'])
r, _, _ = arun([PRO, ('add\tx0, sp, #0x10', None), ('add\tx1, sp, #0x10', None), BLP, ('ldr\tx0, [sp, #16]', None), BLS, EPI, RET])
check('a64 escaped slot address (into a non-out argument)', codes(r), ['out-escape'])
r, _, _ = arun([PRO, ('add\tx1, sp, #0x10', None), ('str\tx1, [sp, #24]', None), BLP, ('ldr\tx0, [sp, #16]', None), BLS, EPI, RET])
check('a64 escaped slot address (stored)', codes(r), ['out-escape'])
r, _, _ = arun([PRO, ('add\tx1, sp, #0x10', None), ('cbz\tw3, 10 <use+0x10>', None), BLP, ('ldr\tx0, [sp, #16]', None), BLS, EPI, RET])
check('a64 a path without the producer call', codes(r), ['out-uninit'])
r, _, _ = arun([PRO, ('add\tx1, sp, #0x10', None), BLP, ('ldr\tx0, [sp, #20]', None), BLS, EPI, RET])
check('a64 offset past the declared out type', sorted(set(codes(r))), ['out-type', 'out-uninit'])
r, _, _ = arun([PRO, ('add\tx1, sp, #0x10', None), ('stlr\tw2, [sp, #16]', None), BLP, ('ldr\tx0, [sp, #16]', None), BLS, EPI, RET])
check('a64 an ordered store before the call is overwritten by the call: green', codes(r), [])
r, _, _ = arun([PRO, ('add\tx1, sp, #0x10', None), BLP, ('stlr\tw2, [sp, #16]', None), ('ldr\tx0, [sp, #16]', None), BLS, EPI, RET])
check('a64 an stlr store after the call', codes(r), ['out-store'])
r, _, _ = arun([PRO, ('add\tx1, sp, #0x10', None), BLP, ('ldr\tx0, [sp, #16]', None), BLS, EPI, RET], producers=[dict(symbol='produce', kind='function')])
check('a64 a producer without a declared out type', 'out-uninit' in codes(r) or 'out-type' in codes(r), True)


def xfn(name, insns, start=0):
    out = ['', '%016x <%s>:' % (start, name)]
    for i, (text, rel) in enumerate(insns):
        out.append('  %x:\t%s' % (start + 4 * i, text))
        if rel:
            out.append('\t\t\t%x: %s\t%s' % (start + 4 * i, rel[0], rel[1]))
    return out


def xobj(funcs, syms, data=()):
    out = ['', 'x.o:     file format elf64-x86-64', '', 'SYMBOL TABLE:']
    for n, sec, size, g in syms:
        out.append('0000000000000000 %s %s\t%016x %s' % (('g     O' if g else 'l     O') if sec != '*UND*' else '       ', sec, size, n))
    for n, _ in funcs:
        out.append('0000000000000000 g     F .text\t0000000000000040 %s' % n)
    out += ['', 'Disassembly of section .text:']
    a = 0
    for n, insns in funcs:
        out += xfn(n, insns, a)
        a += 4 * len(insns) + 0x10
    return '\n'.join(out) + '\n'


PLT, PC32 = 'R_X86_64_PLT32', 'R_X86_64_PC32'
XCG, XCP = ('call\t0 <glsrc>', (PLT, 'glsrc-0x4')), ('call\t0 <produce>', (PLT, 'produce-0x4'))
XCT = ('call\t0 <translate>', (PLT, 'translate-0x4'))


def xrun(body, producers=OUTP, site='glsrc'):
    return gatecert.prove(gatecert.parse_objdump(xobj([('use', body)], OSYMS), 'x64'), site, 0, producers)


XRET = ('ret', None)
r, _, v = xrun([('sub\trsp,0x18', None), XCT, ('mov\trdi,rax', None), XCG, ('add\trsp,0x18', None), XRET])
check('x64 call result', (codes(r), v), ([], ['prod:translate']))
r, _, v = xrun([('sub\trsp,0x18', None), ('lea\trsi,[rsp+0x8]', None), XCP, ('mov\trdi,QWORD PTR [rsp+0x8]', None), XCG, ('add\trsp,0x18', None), XRET])
check('x64 out-param green', (codes(r), v), ([], ['prod:produce']))
r, _, _ = xrun([('sub\trsp,0x18', None), ('lea\trsi,[rsp+0x8]', None), XCP, ('mov\tQWORD PTR [rsp+0x8],rdx', None), ('mov\trdi,QWORD PTR [rsp+0x8]', None), XCG,
                ('add\trsp,0x18', None), XRET])
check('x64 intervening store', codes(r), ['out-store'])
r, _, _ = xrun([('sub\trsp,0x18', None), ('lea\trsi,[rsp+0x8]', None), XCP, ('movaps\tXMMWORD PTR [rsp+0x8],xmm0', None), ('mov\trdi,QWORD PTR [rsp+0x8]', None), XCG,
                ('add\trsp,0x18', None), XRET])
check('x64 intervening store of an unknown form', codes(r), ['out-store'])
r, _, _ = xrun([('sub\trsp,0x18', None), ('lea\trdi,[rsp+0x8]', None), ('lea\trsi,[rsp+0x8]', None), XCP, ('mov\trdi,QWORD PTR [rsp+0x8]', None), XCG,
                ('add\trsp,0x18', None), XRET])
check('x64 escaped slot address', codes(r), ['out-escape'])
r, _, _ = xrun([('sub\trsp,0x18', None), ('lea\trsi,[rsp+0x8]', None), ('mov\trbx,rsi', None), ('mov\tQWORD PTR [rsp+0x10],rbx', None), XCP,
                ('mov\trdi,QWORD PTR [rsp+0x8]', None), XCG, ('add\trsp,0x18', None), XRET])
check('x64 escaped through a copy stored to memory', codes(r), ['out-escape'])
r, _, _ = xrun([('sub\trsp,0x18', None), ('lea\trsi,[rsp+0x8]', None), ('test\tedx,edx', None), ('je\t14 <use+0x14>', None), XCP, ('mov\trdi,QWORD PTR [rsp+0x8]', None),
                XCG, ('add\trsp,0x18', None), XRET])
check('x64 a path without the producer call', codes(r), ['out-uninit'])


def xidx(n):
    return [('cmp\tedi,0x%x' % n, None), ('ja\t18 <use+0x18>', None), ('mov\teax,edi', None), ('lea\trdx,[rip+0x0]', (PC32, 'progs-0x4')),
            ('mov\tedi,DWORD PTR [rdx+rax*4]', None), XCG, XRET, XRET]


for n, want in ((3, []), (4, []), (5, ['index-unbounded'])):
    r, _, v = xrun(xidx(n))
    check('x64 index bound %d over 5 elements (ja: index <= n)' % n, codes(r), want)
r, _, _ = xrun([('lea\trdx,[rip+0x0]', (PC32, 'progs-0x4')), ('mov\tedi,DWORD PTR [rdx+rax*4]', None), XCG, XRET])
check('x64 no compare', codes(r), ['index-unbounded'])
r, _, v = xrun([('lea\trdi,[rip+0x0]', (PC32, 'progs-0x4')), XCG, XRET])
check('x64 address of a producer table', (codes(r), v), ([], ['addr:progs']))
r, _, _ = xrun([('mov\tedi,0x0', None), XCG, XRET])
check('x64 constant', codes(r), ['constant'])
r, _, _ = xrun([XCT, XCT, ('mov\trdi,rax', None), XCG, XRET])
check('x64 a call result used after another call is the last call', codes(r), [])
r, _, _ = xrun([XCT, ('mov\trbx,rax', None), XCP, ('mov\trdi,rbx', None), XCG, XRET])
check('x64 callee-saved copy of an earlier result', codes(r), [])
r, _, _ = xrun([XCT, ('mov\trdi,rax', None), ('call\t0 <other>', (PLT, 'other-0x4')), XCG, XRET])
check('x64 clobbered argument register', codes(r), ['clobbered'])

if fails:
    print('\n'.join(fails))
    sys.exit(1)
print('ok')
