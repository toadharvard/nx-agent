#!/usr/bin/env python3
"""proto.declared and bodysel.decide on invented symbols and addresses."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import proto, bodysel
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402





d = proto.declared('?f@C@@QAEHH@Z')
case('thiscall', got=(d['kind'], d['cc'], d['words']), want=('member', 'thiscall', 1))
d = proto.declared('?g@@YAXXZ')
case('cdecl void', got=(d['kind'], d['cc'], d['words']), want=('free', 'cdecl', 0))
d = proto.declared('?h@@YGHHN@Z')
case('stdcall int,double', got=(d['cc'], d['words']), want=('stdcall', 3))
case('data symbol', got=proto.declared('?x@@3HA'), want=None)

def sig(sym):
    d = proto.declared(sym)
    return (d['ret'], d['args'], d['regs'], d['words'], d['varargs'], d['sret']) if d else None


case('void', got=sig('?a@@YAXXZ'), want=('v', [], [], 0, False, False))
case('int', got=sig('?b@@YAHH@Z'), want=('i', ['i'], [''], 1, False, False))
case('ptr ret', got=sig('?c@@YAPAHXZ')[0], want='i')
case('ref ret', got=sig('?d@@YAAAHXZ')[0], want='i')
case('bool ret', got=sig('?e@@YA_NXZ')[0], want='i')
case('float ret', got=sig('?f@@YAMXZ')[0], want='f')
case('double ret', got=sig('?g@@YANXZ')[0], want='d')
case('int64 ret', got=sig('?h@@YA_JXZ')[0], want='q')
case('uint64 ret', got=sig('?h@@YA_KXZ')[0], want='q')
case('struct ret', got=sig('?i@@YA?AUPoint@@XZ'), want=('m?', [], [], 0, False, True))
case('class ret', got=sig('?i@@YA?AVBox@@H@Z')[0], want='m?')
case('arg classes', got=sig('?j@@YAXH_JMN@Z'), want=('v', ['i', 'q', 'f', 'd'], ['', '', '', ''], 6, False, False))
case('struct arg', got=sig('?k@@YAXUPoint@@H@Z'), want=('v', ['s?', 'i'], ['', ''], None, False, False))
case('fastcall', got=sig('?m@@YIXHMPAHH@Z'), want=('v', ['i', 'f', 'i', 'i'], ['ecx', '', 'edx', ''], 2, False, False))
case('thiscall with int64 argument', got=sig('?n@Foo@@QAEHH_J@Z'), want=('i', ['i', 'q'], ['', ''], 3, False, False))
case('varargs', got=sig('?o@@YAHPBDZZ'), want=('i', ['i'], [''], 1, True, False))
case('fnptr arg', got=sig('?p@@YAXP6AHH@ZH@Z'), want=('v', ['i', 'i'], ['', ''], 2, False, False))
case('fnptr varargs arg', got=sig('?q@@YAXP6AHHZZH@Z')[1], want=['i', 'i'])
case('backref', got=sig('?r@@YAXPAH_J01@Z'), want=('v', ['i', 'q', 'i', 'q'], ['', '', '', ''], 6, False, False))
case('backref struct', got=sig('?s@@YAXUPoint@@0@Z')[1:4:2], want=(['s?', 's?'], None))
case('template arg', got=sig('?t@@YAXV?$vec@H@@@Z')[1], want=['s?'])
case('template ptr arg', got=sig('?u@@YAXPAV?$vec@H@@H@Z')[1], want=['i', 'i'])
case('enum arg', got=sig('?v@@YAXW4Color@@@Z')[1], want=['i'])
case('backref after fnptr', got=sig('?w@@YAXP6AXH@ZH0@Z')[3], want=None)
case('fastcall sret', got=sig('?x@@YI?AUPoint@@H@Z')[3], want=None)
case('ctor', got=sig('??0Foo@@QAE@XZ')[:2], want=('n', []))
case('array ptr', got=sig('?y@@YAXPAY02HH@Z')[1], want=['i', 'i'])
case('lvalue ref', got=sig('?z@@YAXAAHH@Z')[1], want=['i', 'i'])
case('rvalue ref', got=sig('?z@@YAX$$QAHH@Z')[1], want=['i', 'i'])

universe = {0x10: 'decomp', 0x20: 'decomp', 0x30: 'decomp', 0x40: 'decomp', 0x50: 'decomp', 0x60: 'eh-unwind', 0x70: 'decomp', 0x80: 'decomp'}
n1 = {'fn': {0x10: {'n1': True, 'why': '', 'sym': '?a@@YAXXZ'}, 0x20: {'n1': False, 'why': 'bytes differ'}, 0x30: {'n1': True, 'why': '', 'sym': '?b@@YAXXZ'},
             0x40: {'n1': True, 'why': '', 'sym': '?c@@YAXXZ'}, 0x50: {'n1': True, 'why': '', 'sym': '?d@@YAXXZ'},
             0x70: {'n1': True, 'why': '', 'sym': '??_EC@@UAEPAXI@Z'}, 0x80: {'n1': True, 'why': '', 'sym': '??_EC@@UAEPAXI@Z'}}}
n3 = {0x10: ('ok', []), 0x30: ('ok', []), 0x40: ('ok', [0x50]), 0x50: ('variable shift', []), 0x70: ('ok', []), 0x80: ('ok+E', [])}
out = bodysel.decide(universe, n1, {0x30: 'call different'}, n3)
case('native', got=out[0x10], want=['NATIVE', 'N1 N2 N3 N4'])
case('n1', got=out[0x20][1], want='N1: bytes differ')
case('n2', got=out[0x30][1], want='N2: gate call different')
case('n4', got=out[0x40][1], want='N4: an inlined callee is not NATIVE')
case('n3', got=out[0x50][1], want='N3: variable shift')
case('outside', got=out[0x60][1], want='not a decomp function (eh-unwind)')
case('vector dtor', got=out[0x70][1], want='N3: ??_E without the vector branch of __sdd')
case('vector dtor ok', got=out[0x80][0], want='NATIVE')
sys.exit(testcase.finish())
