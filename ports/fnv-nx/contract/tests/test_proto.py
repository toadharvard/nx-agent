#!/usr/bin/env python3
"""proto.declared and bodysel.decide on invented symbols and addresses."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
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

def sig(sym):
    d = proto.declared(sym)
    return (d['ret'], d['args'], d['regs'], d['words'], d['varargs'], d['sret']) if d else None


check('void', sig('?a@@YAXXZ'), ('v', [], [], 0, False, False))
check('int', sig('?b@@YAHH@Z'), ('i', ['i'], [''], 1, False, False))
check('ptr ret', sig('?c@@YAPAHXZ')[0], 'i')
check('ref ret', sig('?d@@YAAAHXZ')[0], 'i')
check('bool ret', sig('?e@@YA_NXZ')[0], 'i')
check('float ret', sig('?f@@YAMXZ')[0], 'f')
check('double ret', sig('?g@@YANXZ')[0], 'd')
check('int64 ret', sig('?h@@YA_JXZ')[0], 'q')
check('uint64 ret', sig('?h@@YA_KXZ')[0], 'q')
check('struct ret', sig('?i@@YA?AUPoint@@XZ'), ('m?', [], [], 0, False, True))
check('class ret', sig('?i@@YA?AVBox@@H@Z')[0], 'm?')
check('arg classes', sig('?j@@YAXH_JMN@Z'), ('v', ['i', 'q', 'f', 'd'], ['', '', '', ''], 6, False, False))
check('struct arg', sig('?k@@YAXUPoint@@H@Z'), ('v', ['s?', 'i'], ['', ''], None, False, False))
check('fastcall', sig('?m@@YIXHMPAHH@Z'), ('v', ['i', 'f', 'i', 'i'], ['ecx', '', 'edx', ''], 2, False, False))
check('thiscall', sig('?n@Foo@@QAEHH_J@Z'), ('i', ['i', 'q'], ['', ''], 3, False, False))
check('varargs', sig('?o@@YAHPBDZZ'), ('i', ['i'], [''], 1, True, False))
check('fnptr arg', sig('?p@@YAXP6AHH@ZH@Z'), ('v', ['i', 'i'], ['', ''], 2, False, False))
check('fnptr varargs arg', sig('?q@@YAXP6AHHZZH@Z')[1], ['i', 'i'])
check('backref', sig('?r@@YAXPAH_J01@Z'), ('v', ['i', 'q', 'i', 'q'], ['', '', '', ''], 6, False, False))
check('backref struct', sig('?s@@YAXUPoint@@0@Z')[1:4:2], (['s?', 's?'], None))
check('template arg', sig('?t@@YAXV?$vec@H@@@Z')[1], ['s?'])
check('template ptr arg', sig('?u@@YAXPAV?$vec@H@@H@Z')[1], ['i', 'i'])
check('enum arg', sig('?v@@YAXW4Color@@@Z')[1], ['i'])
check('backref after fnptr', sig('?w@@YAXP6AXH@ZH0@Z')[3], None)
check('fastcall sret', sig('?x@@YI?AUPoint@@H@Z')[3], None)
check('ctor', sig('??0Foo@@QAE@XZ')[:2], ('n', []))
check('array ptr', sig('?y@@YAXPAY02HH@Z')[1], ['i', 'i'])
check('lvalue ref', sig('?z@@YAXAAHH@Z')[1], ['i', 'i'])
check('rvalue ref', sig('?z@@YAX$$QAHH@Z')[1], ['i', 'i'])

universe = {0x10: 'decomp', 0x20: 'decomp', 0x30: 'decomp', 0x40: 'decomp', 0x50: 'decomp', 0x60: 'eh-unwind', 0x70: 'decomp', 0x80: 'decomp'}
n1 = {'fn': {0x10: {'n1': True, 'why': '', 'sym': '?a@@YAXXZ'}, 0x20: {'n1': False, 'why': 'bytes differ'}, 0x30: {'n1': True, 'why': '', 'sym': '?b@@YAXXZ'},
             0x40: {'n1': True, 'why': '', 'sym': '?c@@YAXXZ'}, 0x50: {'n1': True, 'why': '', 'sym': '?d@@YAXXZ'},
             0x70: {'n1': True, 'why': '', 'sym': '??_EC@@UAEPAXI@Z'}, 0x80: {'n1': True, 'why': '', 'sym': '??_EC@@UAEPAXI@Z'}}}
n3 = {0x10: ('ok', []), 0x30: ('ok', []), 0x40: ('ok', [0x50]), 0x50: ('variable shift', []), 0x70: ('ok', []), 0x80: ('ok+E', [])}
out = bodysel.decide(universe, n1, {0x30: 'call different'}, n3)
check('native', out[0x10], ['NATIVE', 'N1 N2 N3 N4'])
check('n1', out[0x20][1], 'N1: bytes differ')
check('n2', out[0x30][1], 'N2: gate call different')
check('n4', out[0x40][1], 'N4: an inlined callee is not NATIVE')
check('n3', out[0x50][1], 'N3: variable shift')
check('outside', out[0x60][1], 'not a decomp function (eh-unwind)')
check('vector dtor', out[0x70][1], 'N3: ??_E without the vector branch of __sdd')
check('vector dtor ok', out[0x80][0], 'NATIVE')
print('ok' if ok else 'FAILED')
sys.exit(0 if ok else 1)
