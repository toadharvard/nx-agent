#!/usr/bin/env python3
"""Check of link/abi_msvcname.py: the decoration grammar (names, back-references, templates, types, function classes) and its errors.
Expected values were derived from the grammar by hand (names are invented)."""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, '..')]
import abi_msvcname as M
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

fails = testcase.FAILED
I, V = ('b', 'int'), ('b', 'void')
d = M.parse('?f@Foo@@QAEHH@Z')
case('member', got=(d.kind, d.scope, d.access, d.member, d.static, d.virtual, d.cc, d.ret, d.params, d.varargs),
      want=('function', ('Foo', 'f'), 'public', True, False, False, 'thiscall', I, (I,), False))
d = M.parse('?f@Bar@Foo@@SAXXZ')
case('static, nested scope, void list', got=(d.scope, d.static, d.member, d.cc, d.params), want=(('Foo', 'Bar', 'f'), True, False, 'cdecl', ()))
d = M.parse('??0Foo@@QAE@XZ')
case('ctor', got=(d.structor, d.ret, d.scope[-1]), want=('0', None, ('op', '0')))
d = M.parse('??1Foo@@UAE@XZ')
case('dtor virtual', got=(d.structor, d.virtual), want=('1', True))
d = M.parse('?g@@YAXPBDZZ')
case('variadic cdecl', got=(d.params, d.varargs), want=(((('ptr', '*', '', ('b', 'char'))),), True))
d = M.parse('?h@@YA?AVFoo@@V1@@Z')
case('class result and parameter back-reference', got=(d.ret, d.params), want=(('tag', 'class', ('Foo',)), (('tag', 'class', ('Foo',)),)))
d = M.parse('?m@@YAXV?$Vec@H@@V1@@Z')
tv = ('tag', 'class', (('tpl', 'Vec', (I,)),))
case('template instance and its back-reference', got=d.params, want=(tv, tv))
d = M.parse('?k@@YAXP6AHH@Z@Z')
case('function pointer', got=d.params, want=((('ptr', '*', '', ('fn', 'cdecl', I, (I,), False))),))
d = M.parse('?n@@YAXPAY0BA@H@Z')
case('pointer to array', got=d.params, want=((('ptr', '*', '', ('arr', (16,), I))),))
d = M.parse('?p@@YAXP8Foo@@AEXH@Z@Z')
case('pointer to member function', got=d.params, want=((('ptr', '*', '', ('memfn', ('Foo',), '', ('fn', 'thiscall', V, (I,), False)))),))
d = M.parse('?e@@YAXW4Color@@@Z')
case('enum', got=d.params, want=((('tag', 'enum', ('Color',)),),)[0])
d = M.parse('?q@@YA?BHABV?$A@PAVB@@H@@@Z')
case('reference to template with pointer and int arguments', got=d.params,
      want=((('ptr', '&', '', ('tag', 'class', (('tpl', 'A', ((('ptr', '*', '', ('tag', 'class', ('B',)))), I)),)))),))
d = M.parse('?s@@YAXPAVA@@PAV1@0@Z')
case('two kinds of parameter back-reference', got=len(d.params), want=3)
d = M.parse('??_GFoo@@UAEPAXI@Z')
case('scalar deleting destructor name', got=(d.scope[-1], d.ret, d.params), want=(('op', '_G'), ('ptr', '*', '', V), (('b', 'unsigned int'),)))
d = M.parse('?w@Foo@@W3AEXXZ')
case('adjustor thunk', got=(d.thunk, d.virtual, d.cc), want=(True, True, 'thiscall'))
d = M.parse('?t@@YAXAAH@Z')
case('reference', got=d.params, want=((('ptr', '&', '', I),),)[0])
d = M.parse('?u@@YA_NXZ')
case('bool', got=d.ret, want=('b', 'bool'))
d = M.parse('?x@@YAX_J_K@Z')
case('64-bit integers', got=d.params, want=(('b', '__int64'), ('b', 'unsigned __int64')))
d = M.parse('?v@@3HA')
case('data symbol', got=d.kind, want='data')
d = M.parse('?a@?A0x1234@@YAXXZ')
case('anonymous namespace', got=d.scope, want=(('anon',), 'a'))
case('name back-reference', got=M.parse('?f@A@@YAXV1@@Z').scope, want=('A', 'f'))
case('rtti name', got=M.parse_qualified_type_name('.?AVFoo@Bar@@'), want=('class', ('Bar', 'Foo')))
case('rtti struct', got=M.parse_qualified_type_name('.?AUS@@'), want=('struct', ('S',)))

for bad, part in (('f@@YAXXZ', "expected '?'"), ('?f@@YAXXZZ', 'trailing'), ('?f@@YAX', 'unterminated'), ('?f@@YAXH', 'unterminated'),
                  ('?f@@YAXV1@@Z', 'back-reference'), ('?f@@YAXV?$A@$1?x@@@@Z', 'template argument form'), ('?f@@YAX$$V@Z', 'type form'),
                  ('?f@@YAX_?@Z', 'builtin'), ('?f@@@@', 'function class'), ('?f@@YAXQ@Z', 'bad storage class'), ('?f@@Y9XXZ', 'calling convention'),
                  ('', "expected '?'"), ('?@@YAXXZ', 'empty or unterminated name')):
    try:
        M.parse(bad)
        fails.append('%r: no error' % bad)
    except ValueError as e:
        case('error text of %r', got=part in str(e) and str(e).startswith(bad), want=True, key=bad)
for bad in ('?AVFoo@@', '.?AXFoo@@', '.?AVFoo@@x', '.?AV'):
    try:
        M.parse_qualified_type_name(bad)
        fails.append('%r: no error' % bad)
    except ValueError:
        pass

for f in fails:
    print('FAIL', f)
sys.exit(1 if fails else 0)
