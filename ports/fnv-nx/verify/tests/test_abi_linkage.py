#!/usr/bin/env python3
"""gate_abi layout item by the `linkage` column (8th field of .fnvnx.layout): only `external` records pair by key; `internal`, `none` and `c` records are
distinct entities per unit and never pair by name.  Hand-assembled objects with an empty .fnvnx.sympair.  Run: python3 -I <this file>."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, '..'), os.path.join(HERE, '..', '..', 'link'), os.path.join(HERE, '..', '..', 'contract')]
import gate_abi  # noqa: E402

tmp = tempfile.mkdtemp(prefix='abilink-')
fails = []


def obj(name, rows, decl=None):
    s = os.path.join(tmp, name + '.s')
    o = os.path.join(tmp, name + '.o')
    lay = ''.join('\\t'.join(map(str, r)) + '\\n' for r in rows)
    open(s, 'w').write('\t.text\n\t.section .fnvnx.sympair,"",%progbits\n' + ('' if decl is None else '\t.section .fnvnx.decltype,"",%progbits\n\t.ascii "' + ''.join(k + '\\t' + v + '\\n' for k, v in decl.items()) + '"\n') + '\t.section .fnvnx.layout,"",%progbits\n\t.ascii "' + lay + '"\n')
    r = subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', s, '-o', o], capture_output=True, text=True)
    if r.returncode:
        fails.append('assemble ' + name + ': ' + r.stderr[-200:])
    return (name, o)


def judge(a, b):
    n, c, ids, d = gate_abi.judge_objects([a, b])
    return n, c, ids


def row(key, size, linkage):
    return (key, size, 4, 'a:0:4', '', '', '', linkage)


for link, green in (('external', False), ('internal', True), ('none', True), ('c', True)):
    dc = {'v': 'int'} if link == 'c' else None
    n, c, ids = judge(obj('a' + link, [row('S', 4, link)], dc), obj('b' + link, [row('S', 8, link)], dc))
    if (not ids) != green:
        fails.append('%s: differing layouts, green=%s got %r' % (link, green, ids))
n, c, ids = judge(obj('ea', [row('S', 4, 'external')]), obj('eb', [row('S', 4, 'external')]))
if ids:
    fails.append('external equal layouts are green: %r' % ids)
n, c, ids = judge(obj('xa', [row('S', 4, 'external')]), obj('xb', [row('S', 8, 'internal')]))
if ids:
    fails.append('an external record never pairs with an internal one of the same key: %r' % ids)
# C: the tag never pairs, the declared types of an external symbol are compared by 6.2.7
S1, S2 = 'struct:S{x:int}', 'struct:S{x:long}'
n, c, ids = judge(obj('ca', [row('S', 4, 'c')], {'v': S1}), obj('cb', [row('S', 4, 'c')], {'v': 'struct:S'}))
if ids:
    fails.append('C: complete against incomplete is compatible: %r' % ids)
n, c, ids = judge(obj('cc', [row('S', 4, 'c')], {'v': S1}), obj('cd', [row('S', 4, 'c')], {'v': S2}))
if not (len(ids) == 1 and ids[0].startswith('ctype:v')):
    fails.append('C: a member type differing is red on the symbol: %r' % ids)
n, c, ids = judge(obj('ce', [row('S', 4, 'c')], {'v': S1}), obj('cf', [row('S', 4, 'c')]))
if not any('no .fnvnx.decltype' in i for i in ids):
    fails.append('C: a C unit without the record is red: %r' % ids)
if fails:
    print('FAIL\n' + '\n'.join(fails))
    sys.exit(1)
print('ok')
