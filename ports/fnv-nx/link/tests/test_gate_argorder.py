#!/usr/bin/env python3
"""Check of link/gate_argorder.py on synthetic ELF and COFF objects (invented names, no game data, no cl, no network):
a site whose cl code evaluates the arguments last to first is certified; the opposite order is red and named; every fact the object
does not give (inlined callee, name missing, function not delimited, callee inside an argument, no cl object) is red, never a pass;
malformed sections are errors; an empty universe is green; the gate file and exit status.  Run: python3 -I <this file>."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))
import gate_argorder
import synth

fails = []


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r, want %r' % (what, got, want))


def rows(*r):
    return ''.join('\t'.join(x) + '\n' for x in r).encode()


SP = rows(('_Z1fv', '?f@@YAXXZ', 'func', 'def'), ('_Z1gv', '?g@@YAXXZ', 'func', 'def'), ('_Z1Xii', '?X@@YAXHH@Z', 'func', 'def'),
          ('_ZN1AC1Ev', '??0A@@QAE@XZ', 'ctor.c1', 'def'), ('_ZN1AC2Ev', '??0A@@QAE@XZ', 'ctor.c2', 'def'))
NM = rows(('_Z1av', '?a@@YAHXZ'), ('_Z1bv', '?b@@YAHXZ'), ('_Z1cv', '?c@@YAHXZ'), ('_Z1dv', '?d@@YAHXZ'))


def gcc(argorder, sp=SP, nm=NM):
    return synth.elf([('.fnvnx.argorder', argorder, 0), ('.fnvnx.sympair', sp, 0), ('.fnvnx.names', nm, 0)], None)


def cl(calls, fname=b'?f@@YAXXZ', chars=0x60501020, value=0, extra=(), reltype=0x14, more_sections=()):
    """a cl object: one function in its own section whose REL32 relocations name `calls` in order"""
    names = []
    for c in calls:
        if c not in names:
            names.append(c)
    syms = [dict(name=fname, value=value, sec=1, type=0x20, cls=2)] + [dict(name=n, value=0, sec=0, type=0x20, cls=2) for n in names if n != fname] + list(extra)
    sec = dict(name=b'.text$mn', chars=chars, data=b'\x90' * (5 * len(calls) + 1), relocs=[(1 + 5 * i, c, reltype) for i, c in enumerate(calls)])
    return synth.coff([sec] + list(more_sections), syms)


A, B, C, D, X = b'?a@@YAHXZ', b'?b@@YAHXZ', b'?c@@YAHXZ', b'?d@@YAHXZ', b'?X@@YAXHH@Z'
SITE = ('_Z1fv', '_Z1Xii', '_Z1av|_Z1bv', '0/1')


def run(site_rows, clobj, sp=SP, nm=NM):
    t = tempfile.mkdtemp()
    try:
        synth.write(t + '/g', 'u.o', gcc(rows(*site_rows) if not isinstance(site_rows, bytes) else site_rows, sp, nm))
        if clobj is not None:
            synth.write(t + '/c', 'u.obj', clobj)
        else:
            os.makedirs(t + '/c')
        return gate_argorder.check([t + '/g'], [t + '/c'])
    finally:
        shutil.rmtree(t, ignore_errors=True)


ID = 'u:_Z1fv:_Z1Xii:%s'
# the rule: the last argument first: b() then a() then X
check('confirmed', run([SITE], cl([B, A, X])), (1, []))
check('contradicted', run([SITE], cl([A, B, X])), (1, [ID % '0/1' + ': contradicted: argument 0 is evaluated before argument 1']))
check('other calls in between are fine', run([SITE], cl([C, B, D, A, C, X])), (1, []))
# two calls of X: the second is judged on the calls since the first
two = cl([B, A, X, D, C, X])
check('call 1 of 2', run([('_Z1fv', '_Z1Xii', '_Z1cv|_Z1dv', '1/2')], two), (1, []))
check('call 0 of 2', run([('_Z1fv', '_Z1Xii', '_Z1av|_Z1bv', '0/2')], two), (1, []))
check('call 1 of 2 wrong window', run([('_Z1fv', '_Z1Xii', '_Z1av|_Z1bv', '1/2')], two)[1][0].split(': ', 1)[1], 'a callee of an argument is not once in the window before the call')
# an argument without calls (the object of a member call with no effects)
check('empty first argument', run([('_Z1fv', '_Z1Xii', '|_Z1bv', '0/1')], cl([B, X])), (1, []))
check('three arguments', run([('_Z1fv', '_Z1Xii', '_Z1av|_Z1bv|_Z1cv', '0/1')], cl([C, B, A, X])), (1, []))
check('three arguments, one pair swapped', run([('_Z1fv', '_Z1Xii', '_Z1av|_Z1bv|_Z1cv', '0/1')], cl([B, C, A, X]))[1][0].split(': ', 1)[1],
      'contradicted: argument 1 is evaluated before argument 2')
# facts the object does not give: red with the reason, never a pass
check('callee inlined (no call in cl)', run([SITE], cl([B, A]))[1][0].split(': ', 1)[1], "cl's code has 0 calls of the callee, the source 1")
check('too many calls', run([SITE], cl([B, A, X, X]))[1][0].split(': ', 1)[1], "cl's code has 2 calls of the callee, the source 1")
check('an argument callee inlined', run([SITE], cl([B, X]))[1][0].split(': ', 1)[1], 'a callee of an argument is not once in the window before the call')
check('an argument callee twice', run([SITE], cl([B, B, A, X]))[1][0].split(': ', 1)[1], 'a callee of an argument is not once in the window before the call')
check('callee inside an argument', run([('_Z1fv', '_Z1Xii', '_Z1Xii|_Z1bv', '0/1')], cl([B, X]))[1][0].split(': ', 1)[1], 'the callee is also called inside an argument')
check('function without a name', run([('_Z1zv', '_Z1Xii', '_Z1av|_Z1bv', '0/1')], cl([B, A, X]))[1][0].split(': ', 1)[1], 'a symbol has no MSVC name')
check('callee without a name', run([('_Z1fv', '_Z1Xii', '_Z1av|_Z1qv', '0/1')], cl([B, A, X]))[1][0].split(': ', 1)[1], 'a symbol has no MSVC name')
check('nameless sympair row names nothing', run([SITE], cl([B, A, X]), sp=rows(('_Z1fv', '', 'internal', 'def'), ('_Z1Xii', '?X@@YAXHH@Z', 'func', 'def')))[1][0].split(': ', 1)[1], 'a symbol has no MSVC name')
check('two names for a symbol', run([SITE], cl([B, A, X]), nm=NM + rows(('_Z1av', '?a2@@YAHXZ')))[1][0].split(': ', 1)[1], 'a symbol has two different MSVC names')
check('same name twice is fine', run([SITE], cl([B, A, X]), nm=NM + rows(('_Z1av', '?a@@YAHXZ'))), (1, []))
check('no cl object', run([SITE], None)[1][0].split(': ', 1)[1], 'no cl object for the unit')
check('unreadable cl object', run([SITE], b'junk')[1][0].split(': ', 1)[1].split(':')[0], 'the cl object is unreadable')
check('function not in the cl object', run([SITE], cl([B, A, X], fname=b'?other@@YAXXZ'))[1][0].split(': ', 1)[1], 'the function is not defined exactly once in the cl object')
check('not a COMDAT section', run([SITE], cl([B, A, X], chars=0x60500020))[1][0].split(': ', 1)[1], 'the function is not at offset 0 of a COMDAT code section')
check('not a code section', run([SITE], cl([B, A, X], chars=0x40001040))[1][0].split(': ', 1)[1], 'the function is not at offset 0 of a COMDAT code section')
check('function not at offset 0', run([SITE], cl([B, A, X], value=3))[1][0].split(': ', 1)[1], 'the function is not at offset 0 of a COMDAT code section')
other = [dict(name=b'?g@@YAXXZ', value=8, sec=1, type=0x20, cls=2)]
check('another function in the section', run([SITE], cl([B, A, X], extra=other))[1][0].split(': ', 1)[1], 'the section of the function holds another symbol')
sect = [dict(name=b'.text$mn', value=0, sec=1, type=0, cls=3, naux=1)]
check('a section symbol is not another function', run([SITE], cl([B, A, X], extra=sect)), (1, []))
check('only REL32 relocations are calls', run([SITE], cl([B, A, X], reltype=0x6))[1][0].split(': ', 1)[1], "cl's code has 0 calls of the callee, the source 1")
# a constructor named by c1 and c2 symbols: both are the one MSVC name
check('ctor callee', run([('_Z1fv', '_ZN1AC2Ev', '_Z1av|_Z1bv', '0/1')], cl([B, A, b'??0A@@QAE@XZ'])), (1, []))
# malformed sections are errors (one item for the unit), never a pass
for what, bad in (('three fields', b'a\tb\tc\n'), ('one argument', b'f\tc\ta\t0/1\n'), ('k >= n', b'f\tc\ta|b\t1/1\n'), ('no slash', b'f\tc\ta|b\t01\n'),
                  ('empty element', b'f\tc\ta,,b|c\t0/1\n'), ('no final LF', b'f\tc\ta|b\t0/1'), ('blank in name', b'f g\tc\ta|b\t0/1\n'),
                  ('negative n', b'f\tc\ta|b\t0/-1\n')):
    u, r = run(bad, cl([B, A, X]))
    check('malformed ' + what, (u, len(r), r[0].startswith('u:error: ')), (1, 1, True))
u, r = run(rows(SITE), cl([B, A, X]), sp=b'a\tb\tfunc\n')
check('malformed sympair', (u, len(r), r[0].startswith('u:error: ')), (1, 1, True))
u, r = run(rows(SITE), cl([B, A, X]), nm=b'a\n')
check('malformed names', (u, len(r), r[0].startswith('u:error: ')), (1, 1, True))
check('garbage GCC object', gate_argorder.unit_items('u', __file__, None)[1][0][:9], 'u:error: ')
# the universe is every row; sorted ids
u, r = run([SITE, ('_Z1fv', '_Z1Xii', '_Z1av|_Z1bv', '0/1'), ('_Z1gv', '_Z1Xii', '_Z1av|_Z1bv', '0/1')], cl([A, B, X]))
check('every row counted', (u, len(r)), (3, 3))
check('sorted', r, sorted(r))
# an empty universe: no site, even with an unreadable cl object nothing is read
check('empty section', run(b'', b'junk'), (0, []))
check('unreadable file', gate_argorder.unit_items('u', os.path.join(tempfile.gettempdir(), 'nonexistent-fixture'), None)[0], 1)

tmp = tempfile.mkdtemp()
try:
    synth.write(tmp + '/g', 'u.o', gcc(rows(SITE)))
    synth.write(tmp + '/c', 'u.obj', cl([B, A, X]))
    synth.write(tmp + '/gr', 'u.o', gcc(rows(SITE)))
    synth.write(tmp + '/cr', 'u.obj', cl([A, B, X]))
    synth.write(tmp + '/ge', 'u.o', synth.elf([('.text', b'\0', 6)], None))

    def go(g, c, out):
        os.makedirs(out)
        p = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', 'gate_argorder.py'), '--gcc', g, '--cl', c], env={'GATE_OUT': out}, capture_output=True, text=True)
        with open(os.path.join(out, 'argorder.json')) as fh:
            return p.returncode, json.load(fh)
    rc, doc = go(tmp + '/g', tmp + '/c', tmp + '/o1')
    check('green file', (rc, doc['format'], doc['name'], doc['status'], doc['universe'], doc['certified'], doc['uncertified']), (0, 'fnvnx-gate/1', 'argorder', 'green', 1, 1, []))
    rc, doc = go(tmp + '/gr', tmp + '/cr', tmp + '/o2')
    check('red file', (rc, doc['status'], doc['universe'], doc['certified'], len(doc['uncertified'])), (1, 'red', 1, 0, 1))
    with open(tmp + '/o2/argorder.ids') as fh:
        check('ids file', fh.read().split('\n')[:-1], doc['uncertified'])
    rc, doc = go(tmp + '/ge', tmp + '/c', tmp + '/o3')
    check('empty file', (rc, doc['status'], doc['universe'], doc['certified']), (0, 'green', 0, 0))
    rc, doc = go(tmp + '/nope', tmp + '/c', tmp + '/o4')
    check('missing input', (rc, doc['status'], doc['universe'], doc['certified']), (1, 'red', 1, 0))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

if fails:
    print('\n'.join(fails))
    sys.exit(1)
print('ok')
