#!/usr/bin/env python3
"""proto.declared and bodysel.decide on invented symbols and addresses."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'contract'))
import proto, bodysel

ok = True


def check(name, got, want):
    global ok
    if got != want:
        ok = False
        print('FAIL', name, got, want)


d = proto.declared('?f@C@@QAEHH@Z')
check('thiscall', (d['kind'], d['cc'], d['words']), ('member', 'thiscall', 1))
d = proto.declared('?g@@YAXXZ')
check('cdecl void', (d['kind'], d['cc'], d['words']), ('free', 'cdecl', 0))
d = proto.declared('?h@@YGHHN@Z')
check('stdcall int,double', (d['cc'], d['words']), ('stdcall', 3))
check('data symbol', proto.declared('?x@@3HA'), None)

facts = {'fn': {0x10: {}, 0x20: {}, 0x30: {}, 0x40: {}, 0x50: {}, 0x60: {}}}
n1 = {'fn': {0x10: {'n1': True, 'why': ''}, 0x20: {'n1': False, 'why': 'bytes differ'}, 0x30: {'n1': True, 'why': ''},
             0x40: {'n1': True, 'why': ''}, 0x50: {'n1': True, 'why': ''}}}
n3 = {0x10: ('ok', []), 0x30: ('ok', []), 0x40: ('ok', [0x50]), 0x50: ('variable shift', [])}
out = bodysel.decide(facts, n1, {0x30: 'call different'}, n3)
check('native', out[0x10], ['NATIVE', 'N1 N2 N3 N4'])
check('n1', out[0x20][1], 'N1: bytes differ')
check('n2', out[0x30][1], 'N2: gate call different')
check('n4', out[0x40][1], 'N4: an inlined callee is not NATIVE')
check('n3', out[0x50][1], 'N3: variable shift')
check('outside', out[0x60][1], 'not a decomp function')
print('ok' if ok else 'FAILED')
sys.exit(0 if ok else 1)
