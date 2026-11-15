#!/usr/bin/env python3
"""C11 6.2.7 compatibility of declared types across units (link/ctypes_compat.py): a compatible pair, a member type differing, an incomplete type against a
complete one, names, bit-fields, qualifiers, arrays, functions, recursion, non-transitivity. Run: python3 -I <this file>."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import ctypes_compat as C

fails = []


def ok(a, b, want, what):
    got = C.compatible(C.parse(a), C.parse(b))
    if got != want:
        fails.append('%s: %s vs %s got %s' % (what, a, b, got))


S = 'struct:S{x:int;y:*char}'
ok(S, S, True, 'identical')
ok(S, 'struct:S{x:int;y:*int}', False, 'a member type differs')
ok(S, 'struct:S{y:*char;x:int}', False, 'member order differs')
ok(S, 'struct:S{x:int;z:*char}', False, 'a member name differs')
ok(S, 'struct:S', True, 'incomplete against complete with the same tag')
ok(S, 'struct:T', False, 'incomplete with another tag')
ok('struct:S', 'struct:S', True, 'two incomplete')
ok('struct:-{a:int}', 'struct:-{a:int}', True, 'unnamed tags, same members')
ok('struct:-{a:int}', 'struct:-{a:long}', False, 'unnamed tags, member differs')
ok('struct:S{f:unsigned_int:3}', 'struct:S{f:unsigned_int:4}', False, 'bit-field width')
ok('const:int', 'int', False, 'qualifier')
ok('*const:int', '*const:int', True, 'qualified pointee')
ok('[4]int', '[]int', True, 'array incomplete against complete')
ok('[4]int', '[5]int', False, 'array sizes')
ok('(int,*char)int', '(int,*char)int', True, 'function')
ok('(int)int', '(int,int)int', False, 'parameter count')
ok('(int,...)int', '(int)int', False, 'varargs')
ok('(?)int', '(int,*char)int', True, 'K&R against promoted-free prototype')
ok('(?)int', '(char)int', False, 'K&R against a prototype with a promoted parameter')
ok('struct:L{next:*struct:L}', 'struct:L{next:*struct:L}', True, 'recursive')
ok('struct:L{next:*struct:L{v:int}}', 'struct:L{next:*struct:L{v:long}}', False, 'recursive, inner member differs')
ok('enum:E=unsigned_int', 'enum:E=unsigned_int', True, 'enum')
ok('enum:E=unsigned_int', 'enum:E=int', False, 'enum underlying type')
# per symbol over objects: every pair is judged
it, red = C.check({'a.o': {'f': S}, 'b.o': {'f': 'struct:S'}, 'c.o': {'g': 'int'}})
if it != ['f'] or red:
    fails.append('compatible pair across units: %r %r' % (it, red))
it, red = C.check({'a.o': {'f': S}, 'b.o': {'f': 'struct:S{x:long;y:*char}'}})
if list(red) != ['f'] or 'a.o' not in red['f'] or 'b.o' not in red['f']:
    fails.append('member type differing across units is red naming both: %r' % red)
it, red = C.check({'a.o': {'f': 'struct:S{x:int}'}, 'b.o': {'f': 'struct:S'}, 'c.o': {'f': 'struct:S{x:long}'}})
if list(red) != ['f']:
    fails.append('incompatibility behind an incomplete type is found: %r' % red)
for bad in ('struct:S{x:int', '[x]int', '(int', 'const:', '*'):
    try:
        C.parse(bad)
        fails.append('parsed %r' % bad)
    except ValueError:
        pass
if C.parse_decltype(b'f\tint\n', 'x') != {'f': 'int'}:
    fails.append('parse_decltype')
for bad in (b'f\tint', b'f\tint\nf\tint\n', b'f\t[\n'):
    try:
        C.parse_decltype(bad, 'x')
        fails.append('parse_decltype accepted %r' % bad)
    except ValueError:
        pass
# RULE DECLTYPE-COMPLETE: a line is `symbol<TAB>type` or `symbol<TAB>-reason`, the reasons being the plugin's closed set
types, why = C.parse_decltype_lines(b'f\t(int)int\n_ZN1AC1Ev\t-mangled\nmemcpy\t-builtin\n__addtf3\t-libcall\nx\t-cxx\ny\t-noname\n', 'x')
if types != {'f': '(int)int'} or why != {'_ZN1AC1Ev': 'mangled', 'memcpy': 'builtin', '__addtf3': 'libcall', 'x': 'cxx', 'y': 'noname'}:
    fails.append('parse_decltype_lines: %r %r' % (types, why))
if C.parse_decltype(b'f\t(int)int\ng\t-mangled\n', 'x') != {'f': '(int)int'}:
    fails.append('parse_decltype keeps the typed rows only')
for bad in (b'f\t-unknown\n', b'f\t-mangled\nf\t-mangled\n', b'f\t(int)int\nf\t-cxx\n'):
    try:
        C.parse_decltype_lines(bad, 'x')
        fails.append('parse_decltype_lines accepted %r' % bad)
    except ValueError:
        pass
for a, b, want in (('complex:8:4:float', 'complex:8:4:float', True), ('complex:8:4:float', 'complex:16:8:double', False),
                  ('complex:16:8:double', 'complex:16:16:double', False), ('vec:4:16:16:int', 'vec:4:16:16:int', True), ('vec:4:16:16:int', 'vec:2:8:8:int', False),
                  ('atomic:int', 'int', False), ('atomic:const:int', 'const:atomic:int', True), ('host:int', 'host:int', True), ('host:int', 'int', False),
                  ('entry:*()void', 'entry:*()void', True), ('entry:*()void', '*()void', False), ('*entry:*()void', '*entry:*(int)void', False)):
    if C.compatible(C.parse(a), C.parse(b)) != want:
        fails.append('compatible %s %s != %s' % (a, b, want))
if fails:
    print('FAIL\n' + '\n'.join(fails))
    sys.exit(1)
print('ok')
