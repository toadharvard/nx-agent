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
__import__('atexit').register(__import__('shutil').rmtree, tmp, True)   # a test leaves no scratch behind
fails = []


def sect(nm, text):
    return '\t.section %s,"",%%progbits\n\t.ascii "%s"\n' % (nm, text.replace('\t', '\\t').replace('\n', '\\n'))


def obj(name, rows, decl=None, sym='', cross=None, glob=(), extra=None):
    s = os.path.join(tmp, name + '.s')
    o = os.path.join(tmp, name + '.o')
    lay = ''.join('\\t'.join(map(str, r)) + '\\n' for r in rows)
    gl = ''.join('\t.globl %s\n' % k for k in list(glob) + list(decl or {}))
    decl = dict(decl or {}, **(extra or {})) if (decl is not None or extra) else None   # every symbol of the record is a global symbol of the object (DECLTYPE-COMPLETE)
    open(s, 'w').write('\t.text\n' + gl + (sect('.fnvnx.sympair', sym) if sym else '\t.section .fnvnx.sympair,"",%progbits\n') + ('' if cross is None else sect('.fnvnx.crossing', cross)) + ('' if decl is None else '\t.section .fnvnx.decltype,"",%progbits\n\t.ascii "' + ''.join(k + '\\t' + v + '\\n' for k, v in decl.items()) + '"\n') + '\t.section .fnvnx.layout,"",%progbits\n\t.ascii "' + lay + '"\n')
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
# one key shared by two entities with different Itanium names (two unnamed member classes of one class): not paired by the key alone
def rowi(key, size, it):
    return (key, size, 4, 'a:0:4', '', '', it, 'external')


n, c, ids = judge(obj('ia', [rowi('U<unnamed-tag>@H@@', 4, 'N1HUt_E')]), obj('ib', [rowi('U<unnamed-tag>@H@@', 8, 'N1HUt0_E')]))
if ids:
    fails.append('same key, different Itanium names are two entities: %r' % ids)
n, c, ids = judge(obj('ic', [rowi('U<unnamed-tag>@H@@', 4, 'N1HUt_E')]), obj('id', [rowi('U<unnamed-tag>@H@@', 8, 'N1HUt_E')]))
if not ids:
    fails.append('same key and Itanium name with different layouts is red')
# C: the tag never pairs, the declared types of an external symbol are compared by 6.2.7
S1, S2 = 'struct:S{x:int}', 'struct:S{x:long}'
n, c, ids = judge(obj('ca', [row('S', 4, 'c')], {'v': S1}), obj('cb', [row('S', 4, 'c')], {'v': 'struct:S'}))
if ids:
    fails.append('C: complete against incomplete is compatible: %r' % ids)
n, c, ids = judge(obj('cc', [row('S', 4, 'c')], {'v': S1}), obj('cd', [row('S', 4, 'c')], {'v': S2}))
if not (len(ids) == 1 and ids[0].startswith('ctype:v')):
    fails.append('C: a member type differing is red on the symbol: %r' % ids)
n, c, ids = judge(obj('ce', [row('S', 4, 'c')], {'v': S1}), obj('cf', [row('S', 4, 'c')], glob=['v']))
if not any('decltype:missing:v' in i for i in ids):
    fails.append('C: a global symbol with no decltype line is red, missing:v: %r' % ids)
n, c, ids = judge(obj('cg', [row('S', 4, 'c')], {'v': S1, 'w': '-mangled'}), obj('ch', [row('S', 4, 'c')], {'v': S1}))
if ids:
    fails.append('a complete object (a type line and a -mangled reason line) is green: %r' % ids)
n, c, ids = judge(obj('ci', [row('S', 4, 'c')], {'v': S1}), obj('cj', [row('S', 4, 'c')], {'v': S1}, extra={'zz': 'int'}))
if not any('decltype:extra:zz' in i for i in ids):
    fails.append('a decltype line for a symbol the object lacks is red, extra:zz: %r' % ids)
n, c, ids = judge(obj('ck', [row('S', 4, 'c')], {'v': S1}), obj('cl', [row('S', 4, 'c')], {'v': S1, 'w': '-bogus'}))
if not any('unreadable' in i for i in ids):
    fails.append('a reason outside the closed set is red: %r' % ids)
# the section's presence is the fact: a unit of `external` linkage rows (C units of w3's layout) that carries .fnvnx.decltype is compared all the same
n, c, ids = judge(obj('ea', [row('S', 4, 'external')], {'v': S1}), obj('eb', [row('S', 4, 'external')], {'v': S2}))
if not (len(ids) == 1 and ids[0].startswith('ctype:v')):
    fails.append('a decltype section on external-linkage rows is parsed and compared: %r' % ids)
# crossing_forbidden: a boundary symbol has a row and no term
SY = 'f\t?f@@YAXXZ\tfunc\tdef\n'
n, c, ids = judge(obj('xa1', [], sym=SY, cross='f\t-\n'), obj('xb1', []))
if ids:
    fails.append('crossing: a boundary symbol with a clean row is green: %r' % ids)
n, c, ids = judge(obj('xa2', [], sym=SY, cross='f\tpointer_to_member\n'), obj('xb2', []))
if not any(i.startswith('crossing:f') and 'pointer_to_member' in i for i in ids):
    fails.append('crossing: a term is red: %r' % ids)
n, c, ids = judge(obj('xa3', [], sym=SY), obj('xb3', []))
if not any(i.startswith('crossing:f') and 'no .fnvnx.crossing row' in i for i in ids):
    fails.append('crossing: a boundary symbol without a row is red: %r' % ids)
n, c, ids = judge(obj('xa4', [], sym=SY, cross='f\tmadeup\n'), obj('xb4', []))
if not any(i.startswith('crossing:f') and 'unknown term' in i for i in ids):
    fails.append('crossing: an unknown term id is red: %r' % ids)
n, c, ids = judge(obj('xa5', [], sym='d\t?d@@3HA\tdata\tdef\n'), obj('xb5', []))
if ids:
    fails.append('crossing: data is not a boundary function: %r' % ids)
n, c, ids = judge(obj('xa6', [], sym=SY, cross='f\t-\n'), obj('xb6', [], sym='f\t?f@@YAXXZ\tfunc\tref\n'))
if ids:
    fails.append('crossing: one row in a unit that declares it suffices: %r' % ids)
# noemit (toolchain/names/sympair_kinds.inc): the unit defines the function, GCC wrote it nowhere; the row is read, never a reference and never a boundary symbol
NE = 'f\t?f@@YAXXZ\tnoemit\tref\n'
n, c, ids = judge(obj('na1', [], sym=NE), obj('nb1', []))
if ids:
    fails.append('noemit: a row with no symbol in the object, no crossing line, is green (no pairing, no crossing obligation): %r' % ids)
n, c, ids = judge(obj('na2', [], sym=NE, cross='f\tpointer_to_member\n'), obj('nb2', []))
if ids:
    fails.append('noemit: its crossing line is a fact about the signature, with no symbol to bind it to: %r' % ids)
n, c, ids = judge(obj('na3', [], sym=NE), obj('nb3', [], sym=SY.replace('def', 'def'), cross='f\t-\n'))
if ids:
    fails.append('noemit in one unit, the definer in another: the definer\'s row is judged, green: %r' % ids)
n, c, ids = judge(obj('na4', [], sym='f\t?f@@YAXXZ\tbogus\tdef\n'), obj('nb4', []))
if not any('unreadable' in i for i in ids):
    fails.append('a kind outside the plugin set is red: %r' % ids)
if fails:
    print('FAIL\n' + '\n'.join(fails))
    sys.exit(1)
print('ok')
