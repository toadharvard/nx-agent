#!/usr/bin/env python3
"""writeset classification rule (tools/writeset.py): the class of a store is decided by the operand form and capstone's write flag alone."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
import capstone, writeset
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); md.detail = True
FAILS = []


def cls(code):
    g = next(md.disasm(code, 0x1000, 1)); return writeset.classify(g)


def check(n, ok):
    print('%-66s %s' % (n, 'ok' if ok else 'FAIL'))
    if not ok: FAILS.append(n)


check('mov [abs],eax: exact, 4 bytes', cls(bytes.fromhex('a300304000')) == [('exact', 0x403000, 0x403004, '')])
check('mov [esp+4],eax: stack', [r[0] for r in cls(bytes.fromhex('89442404'))] == ['stack'])
check('mov [ebp-4],eax: open (ebp is not a frame pointer by form)', [r[0] for r in cls(bytes.fromhex('8945fc'))] == ['open'])
check('mov [eax+4],ecx: open', [r[0] for r in cls(bytes.fromhex('894804'))] == ['open'])
check('mov [tbl+eax*4],ecx: ranged', [r[0] for r in cls(bytes.fromhex('890c8500304000'))] == ['ranged'])
check('push eax: stack', [r[0] for r in cls(bytes.fromhex('50'))] == ['stack'])
check('mov eax,[abs]: not a store', cls(bytes.fromhex('a100304000')) == [])
check('rep stosd (writes [edi]): open', [r[0] for r in cls(bytes.fromhex('f3ab'))] == ['open'])
check('add [abs],1: exact (read-modify-write)', [r[0] for r in cls(bytes.fromhex('830500304000' + '01'))] == ['exact'])
check('query: exact writer overlaps, open bounds the fact', writeset.query([(1, 'exact', 0x10, 0x14, ''), (2, 'open', None, None, 'eax')], 0x12, 0x13) == {'exact': [(1, 'exact', 0x10, 0x14, '')], 'open': 1, 'stack': 0})
sys.exit(1 if FAILS else 0)
