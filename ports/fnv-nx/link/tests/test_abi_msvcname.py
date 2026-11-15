#!/usr/bin/env python3
"""Check of link/abi_msvcname.py: the decoration grammar (names, back-references, templates, types, function classes) and its errors.
Expected values were derived from the grammar by hand (names are invented)."""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, '..')]
import abi_msvcname as M

fails = []


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r want %r' % (what, got, want))


I, V = ('b', 'int'), ('b', 'void')
d = M.parse('?f@Foo@@QAEHH@Z')
check('member', (d.kind, d.scope, d.access, d.member, d.static, d.virtual, d.cc, d.ret, d.params, d.varargs),
      ('function', ('Foo', 'f'), 'public', True, False, False, 'thiscall', I, (I,), False))
d = M.parse('?f@Bar@Foo@@SAXXZ')
check('static, nested scope, void list', (d.scope, d.static, d.member, d.cc, d.params), (('Foo', 'Bar', 'f'), True, False, 'cdecl', ()))
d = M.parse('??0Foo@@QAE@XZ')
check('ctor', (d.structor, d.ret, d.scope[-1]), ('0', None, ('op', '0')))
d = M.parse('??1Foo@@UAE@XZ')
check('dtor virtual', (d.structor, d.virtual), ('1', True))
d = M.parse('?g@@YAXPBDZZ')
check('variadic cdecl', (d.params, d.varargs), (((('ptr', '*', '', ('b', 'char'))),), True))
d = M.parse('?h@@YA?AVFoo@@V1@@Z')
check('class result and parameter back-reference', (d.ret, d.params), (('tag', 'class', ('Foo',)), (('tag', 'class', ('Foo',)),)))
d = M.parse('?m@@YAXV?$Vec@H@@V1@@Z')
tv = ('tag', 'class', (('tpl', 'Vec', (I,)),))
check('template instance and its back-reference', d.params, (tv, tv))
d = M.parse('?k@@YAXP6AHH@Z@Z')
check('function pointer', d.params, ((('ptr', '*', '', ('fn', 'cdecl', I, (I,), False))),))
d = M.parse('?n@@YAXPAY0BA@H@Z')
check('pointer to array', d.params, ((('ptr', '*', '', ('arr', (16,), I))),))
d = M.parse('?p@@YAXP8Foo@@AEXH@Z@Z')
check('pointer to member function', d.params, ((('ptr', '*', '', ('memfn', ('Foo',), '', ('fn', 'thiscall', V, (I,), False)))),))
d = M.parse('?e@@YAXW4Color@@@Z')
check('enum', d.params, ((('tag', 'enum', ('Color',)),),)[0])
d = M.parse('?q@@YA?BHABV?$A@PAVB@@H@@@Z')
check('reference to template with pointer and int arguments', d.params,
      ((('ptr', '&', '', ('tag', 'class', (('tpl', 'A', ((('ptr', '*', '', ('tag', 'class', ('B',)))), I)),)))),))
d = M.parse('?s@@YAXPAVA@@PAV1@0@Z')
check('two kinds of parameter back-reference', len(d.params), 3)
d = M.parse('??_GFoo@@UAEPAXI@Z')
check('scalar deleting destructor name', (d.scope[-1], d.ret, d.params), (('op', '_G'), ('ptr', '*', '', V), (('b', 'unsigned int'),)))
d = M.parse('?w@Foo@@W3AEXXZ')
check('adjustor thunk', (d.thunk, d.virtual, d.cc), (True, True, 'thiscall'))
d = M.parse('?t@@YAXAAH@Z')
check('reference', d.params, ((('ptr', '&', '', I),),)[0])
d = M.parse('?u@@YA_NXZ')
check('bool', d.ret, ('b', 'bool'))
d = M.parse('?x@@YAX_J_K@Z')
check('64-bit integers', d.params, (('b', '__int64'), ('b', 'unsigned __int64')))
d = M.parse('?v@@3HA')
check('data symbol', d.kind, 'data')
d = M.parse('?a@?A0x1234@@YAXXZ')
check('anonymous namespace', d.scope, (('anon',), 'a'))
check('name back-reference', M.parse('?f@A@@YAXV1@@Z').scope, ('A', 'f'))
check('rtti name', M.parse_qualified_type_name('.?AVFoo@Bar@@'), ('class', ('Bar', 'Foo')))
check('rtti struct', M.parse_qualified_type_name('.?AUS@@'), ('struct', ('S',)))

for bad, part in (('f@@YAXXZ', "expected '?'"), ('?f@@YAXXZZ', 'trailing'), ('?f@@YAX', 'unterminated'), ('?f@@YAXH', 'unterminated'),
                  ('?f@@YAXV1@@Z', 'back-reference'), ('?f@@YAXV?$A@$1?x@@@@Z', 'template argument form'), ('?f@@YAX$$V@Z', 'type form'),
                  ('?f@@YAX_?@Z', 'builtin'), ('?f@@@@', 'function class'), ('?f@@YAXQ@Z', 'bad storage class'), ('?f@@Y9XXZ', 'calling convention'),
                  ('', "expected '?'"), ('?@@YAXXZ', 'empty or unterminated name')):
    try:
        M.parse(bad)
        fails.append('%r: no error' % bad)
    except ValueError as e:
        check('error text of %r' % bad, part in str(e) and str(e).startswith(bad), True)
for bad in ('?AVFoo@@', '.?AXFoo@@', '.?AVFoo@@x', '.?AV'):
    try:
        M.parse_qualified_type_name(bad)
        fails.append('%r: no error' % bad)
    except ValueError:
        pass

for f in fails:
    print('FAIL', f)
sys.exit(1 if fails else 0)
