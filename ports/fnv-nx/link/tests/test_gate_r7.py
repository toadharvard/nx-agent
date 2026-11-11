#!/usr/bin/env python3
"""Check of link/gate_r7.py on a REAL GCC object (fixtures/r7_types.cc built by the one build, see realobj.py; negative GCC-side cases are text edits of
its real .fnvnx.layout rows, written back by mutate.py) paired with hand-built COFF/CodeView objects (blocked: cl toolchain as recipe external, L1)
(invented type names, no game data, no cl, no network):
equal records are certified; every difference (size, member offset, bit-field, base offset, vfptr presence, virtual base, alignment)
is red and names the record; every fact CodeView does not give (vfptr offset, alignment of a record whose source changed it, a
primitive outside the scalar table, member pointer size, ambiguous keys) is red as undecided, never a pass; no cl object, no CodeView and
unreadable objects are red; malformed GCC sections are errors; an empty universe is green; the gate file and the exit status.
Run: python3 -I <this file>."""
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))
import elfobj
import gate_r7
import mutate
import realobj
import synth
from synth import member, bclass, vbclass, vfunctab, cstr, num

fails = []


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r, want %r' % (what, got, want))


INT, CHAR, UINT, REAL80 = 0x74, 0x10, 0x75, 0x42



R0 = open(realobj.objects(['r7_types'])['r7_types'], 'rb').read()      # the real object
LAYOUT = elfobj.parse(R0, 'R0').section('.fnvnx.layout').decode()
ROWS = [l.split('\t') for l in LAYOUT.split('\n') if l]               # key size align fields bases flags itanium
UNIT = 'r7_types.cc'
K = lambda n: ('T' if n == 'TUn' else 'U') + n + '@@'                   # MSVC unique name of the fixture's record `n`
check('the real layout has one row per fixture record', len([r for r in ROWS if r[0].startswith(('UU', 'TT'))]), 29)


def text(rows):
    return ''.join('\t'.join(r) + '\n' for r in rows).encode()


def with_layout(rows, data=R0):
    return mutate.replace_section(data, '.fnvnx.layout', text(rows))


def mut(rows, which, **kw):
    """the real row of `which` with fields edited (size, align, fields, bases, flags, key)"""
    out = []
    for r in rows:
        if r[0] == which:
            r = list(r)
            for k, v in kw.items():
                r[dict(key=0, size=1, align=2, fields=3, bases=4, flags=5, itanium=6)[k]] = v
        out.append(r)
    return out


INT, CHAR, UINT, REAL80 = 0x74, 0x10, 0x75, 0x42
cv = synth.CV()


def S(name, items, size, **kw):
    fl = cv.add(0x1203, synth.fieldlist(*items)) if items is not None else 0
    return synth.record(cv, 's', K(name) if not name.startswith('!') else name, fl, size, **kw)


plain = S('UPlain', [member(INT, 0, 'a'), member(CHAR, 4, 'b')], 8)
S('USizeBad', [member(INT, 0, 'a'), member(CHAR, 4, 'b')], 8)
S('UOffBad', [member(INT, 0, 'a'), member(CHAR, 4, 'b')], 8)
S('UMissing', [member(INT, 0, 'a'), member(CHAR, 4, 'b')], 8)
bf0 = cv.add(0x1205, synth.bitfield(UINT, 5, 0))
bf1 = cv.add(0x1205, synth.bitfield(UINT, 3, 5))
S('UBits', [member(bf0, 0, 'p'), member(bf1, 0, 'x')], 4)
S('UBitsBad', [member(bf0, 0, 'p'), member(bf1, 0, 'x')], 4)
S('UBitsUnnamed', [member(bf1, 0, 'x')], 4)
S('UDer', [bclass(plain, 0), member(INT, 8, 'z')], 12)
S('UDerBad', [bclass(plain, 0), member(INT, 8, 'z')], 12)
S('UVf', [vfunctab(), member(INT, 4, 'm')], 8)
S('UVfDiff', [member(INT, 4, 'm')], 8)
S('UVb', [vbclass(plain)], 12)
S('UAlt', [member(INT, 0, 'a')], 4)
S('UAlign', [member(INT, 0, 'a')], 8)
S('UPk', [member(CHAR, 0, 'a')], 1)
fwd = S('UFwd', None, 0, fwd=True)
S('UFwd', [member(INT, 0, 'q')], 4)
S('UHold', [member(fwd, 0, 'h'), member(INT, 4, 'i')], 8)
S('UHoldPlain', [member(plain, 0, 'h')], 8)
S('UReal', [member(REAL80, 0, 'r')], 16)
ptr = synth.pointer(cv, 4)
S('UPtr', [member(ptr, 0, 'p')], 4)
pm = cv.add(0x1002, struct.pack('<II', INT, 0x0a | (2 << 5)))             # pointer to data member, no size field
S('UPtrMem', [member(pm, 0, 'm')], 4)
arr = cv.add(0x1503, struct.pack('<II', INT, INT) + num(12) + cstr(''))
S('UArr', [member(arr, 0, 'a')], 12)
mod = cv.add(0x1001, struct.pack('<IH', INT, 1))
S('UMod', [member(mod, 0, 'c')], 4)
S('UAmb', [member(INT, 0, 'a')], 4)
S('UAmb', [member(INT, 0, 'a'), member(INT, 4, 'b')], 8)
S('UDup', [member(INT, 0, 'a'), member(INT, 4, 'a')], 8)
S('UOnlyCl', [member(INT, 0, 'a')], 4)
S('TUn', [member(INT, 0, 'a'), member(CHAR, 0, 'b')], 4, union=True)
S('UEmpty', [], 1)
S('UNest', [synth.nest('Inner'), member(INT, 0, 'a')], 4)
synth.record(cv, 's', 'UInner@UNest@@', cv.add(0x1203, synth.fieldlist()), 1)
synth.record(cv, 's', 'U<unnamed-tag>@@', cv.add(0x1203, synth.fieldlist(member(INT, 0, 'a'))), 4)
synth.record(cv, 's', 'UL@?1??use_local@@YAHXZ@', cv.add(0x1203, synth.fieldlist(member(INT, 0, 'a'))), 4)
SERIES = cv

NOKEY = '!_ZN3fooE'
GROWS = ROWS
for key, kw in ((K('USizeBad'), dict(size='12')), (K('UOffBad'), dict(fields='a:0:4,b:5:1')), (K('UMissing'), dict(fields='a:0:4,b:4:1,c:8:4')),
                (K('UBitsBad'), dict(fields='p:0:0:5:4,x:0:6:3:4')), (K('UDerBad'), dict(bases='%s:4:n' % K('UPlain'))),
                (K('UDup'), dict(fields='a:0:4,a:4:4')),
                ('UE@foo@@', dict(key=NOKEY, flags='nokey:no rule names it', itanium='_ZN3fooE'))):
    GROWS = mut(GROWS, key, **kw)
GCC = text(GROWS).decode()
WANT = {r[0]: None for r in GROWS}
WANT.update({
    K('USizeBad'): 'differs: size gcc 12 cl 8', K('UOffBad'): 'differs: member b bit offset gcc 40 cl 32',
    K('UMissing'): 'differs: member c only in gcc (bit 64)', K('UBitsBad'): 'differs: member x bit offset gcc 6 cl 5',
    K('UDerBad'): 'differs: base %s offset gcc 4 cl 0' % K('UPlain'),
    K('UVf'): 'undecided: the offset of the vfptr is not in CodeView (LF_VFUNCTAB has no offset)',
    K('UVfDiff'): 'differs: introduces a vfptr: gcc True cl False',
    K('UVb'): 'differs: cl has a vbptr (virtual bases), gcc a vptr: different object models',
    K('UReal'): 'undecided: size of member r: primitive type 0x42 has no size here',
    K('UPtrMem'): 'undecided: size of member m: pointer to member without a size',
    K('UAmb'): 'undecided: the key has 1 gcc and 2 cl different definitions in the unit',
    K('UDup'): 'undecided: member name twice: a,a',
    K('UNoCl'): "only-gcc: cl's debug info of the unit has no definition of the type",
    NOKEY: "only-gcc: cl's debug info of the unit has no definition of the type (no key: no rule names it)",
})
# alignment is not part of the claim: UAlt (user alignment) and UAlign (altered) stay equal; UE@foo@@ became the nokey row


def reds(r):
    return {x.split(':', 1)[1].split(': ', 1)[0]: x.split(': ', 1)[1] for x in r}


def fixture(t, gcc, cl_bytes, stem=UNIT):
    """gcc: layout text/bytes or a whole object (bytes starting with the ELF magic); a unit list pairs u0.obj with the unit"""
    obj = gcc if isinstance(gcc, bytes) and gcc[:4] == b'\x7fELF' else with_layout([r.split('\t') for r in gcc.split('\n') if r]) if isinstance(gcc, str) else mutate.replace_section(R0, '.fnvnx.layout', gcc)
    synth.write(t + '/g', stem + '.o', obj)
    if cl_bytes is not None:
        synth.write(t + '/c', 'u0.obj', cl_bytes)
    else:
        os.makedirs(t + '/c', exist_ok=True)
    synth.write(t, 'units.json', json.dumps({'units': [{'source': stem}]}).encode())
    return [t + '/g'], [t + '/c'], t + '/units.json'


def chk(g, c, us):
    return gate_r7.check(g, c, us)


tmp = tempfile.mkdtemp()
try:
    g, c, us = fixture(tmp + '/a', GCC, synth.debug_object(SERIES))
    uni, red, cnt = chk(g, c, us)
    keys = set(WANT)
    check('universe is the set of GCC keys', uni, len(keys))
    got = reds(red)
    want = {k: v for k, v in WANT.items() if v}
    for k in sorted(set(got) | set(want)):
        check('verdict ' + k, got.get(k), want.get(k))
    check('sorted', red, sorted(red))
    check('certified count', uni - len(red), len([k for k, v in WANT.items() if not v]))
    check('verdict counts', (cnt['equal'], cnt['differs'], cnt['undecided'], cnt['only-gcc']),
          (len([k for k, v in WANT.items() if not v]), sum(1 for v in WANT.values() if v and v.startswith('differs')),
           sum(1 for v in WANT.values() if v and v.startswith('undecided')), 2))

    # no cl object / no CodeView / an unreadable cl object / a CodeView table that does not parse (one real row)
    one_row = [r for r in ROWS if r[0] == K('UPlain')]
    one = text(one_row)
    PK = K('UPlain')
    g, c, us = fixture(tmp + '/n1', one, None)
    check('no cl object', chk(g, c, us)[:2], (1, ['%s:%s: only-gcc: no cl object for the unit' % (UNIT, PK)]))
    g, c, us = fixture(tmp + '/n2', one, synth.coff([dict(name=b'.text', chars=0x60500020, data=b'\xc3')], []))
    check('no CodeView', chk(g, c, us)[:2], (1, ['%s:%s: only-gcc: the cl object has no CodeView types (.debug$T)' % (UNIT, PK)]))
    UNR = '%s:%s: undecided: a cl object of the unit is unreadable' % (UNIT, PK)
    g, c, us = fixture(tmp + '/n3', one, b'junk junk junk')
    check('unreadable cl object', (chk(g, c, us)[0], chk(g, c, us)[1][0].startswith(UNR)), (1, True))
    g, c, us = fixture(tmp + '/n4', one, synth.coff([dict(name=b'.debug$T', chars=0x40000040, data=struct.pack('<I', 3) + b'xxxx')], []))
    check('bad signature', chk(g, c, us)[1][0].startswith(UNR), True)
    bad = synth.CV()
    bad.add(0x1505, b'\0\0')
    g, c, us = fixture(tmp + '/n5', one, synth.debug_object(bad))
    check('truncated record', chk(g, c, us)[1][0].startswith(UNR), True)
    blob = synth.CV()
    blob.add(0x1505, b'\0' * 4)
    g, c, us = fixture(tmp + '/n6', one, synth.coff([dict(name=b'.debug$T', chars=0x40000040, data=blob.blob()[:-1])], []))
    check('table cut inside a record', chk(g, c, us)[1][0].startswith(UNR), True)
    # members that cannot be read: undecided, not "every member only in gcc"
    brk = synth.CV()
    fl = brk.add(0x1203, struct.pack('<H', 0x1fff) + b'\0' * 6)
    synth.record(brk, 's', PK, fl, 8)
    g, c, us = fixture(tmp + '/n7', one, synth.debug_object(brk))
    check('unreadable members', chk(g, c, us)[1], ['%s:%s: undecided: CodeView: members: field leaf 0x1fff' % (UNIT, PK)])

    # malformed GCC sections are errors (one item), never a pass: text edits of the first real row
    r0 = ROWS[0]
    for what, edit in (('six fields', lambda r: '\t'.join(r[:6]) + '\n'), ('bad size', lambda r: '\t'.join([r[0], 'x'] + r[2:]) + '\n'),
                       ('bad field', lambda r: '\t'.join(r[:3] + ['f:1'] + r[4:]) + '\n'), ('bad base flag', lambda r: '\t'.join(r[:4] + ['b:0:x'] + r[5:]) + '\n'),
                       ('unknown flag', lambda r: '\t'.join(r[:5] + ['weird'] + r[6:]) + '\n'), ('empty key', lambda r: '\t'.join([''] + r[1:]) + '\n'),
                       ('bit offset 8', lambda r: '\t'.join(r[:3] + ['f:0:8:1:1'] + r[4:]) + '\n'),
                       ('empty element', lambda r: '\t'.join(r[:3] + ['f:0:1,,g:1:1'] + r[4:]) + '\n'),
                       ('no final LF', lambda r: '\t'.join(r)), ('not UTF-8', lambda r: ('\t'.join(r[:6]) + '\t\xff\n'))):
        data = edit(r0).encode('latin-1') if what == 'not UTF-8' else edit(r0).encode()
        g, c, us = fixture(tmp + '/m_' + what.replace(' ', '_'), data, synth.debug_object(SERIES))
        u, r, _ = chk(g, c, us)
        check('malformed ' + what, (u, len(r), r[0].startswith('%s:error: ' % UNIT)), (1, 1, True))
    gj = tmp + '/gj'
    synth.write(gj, UNIT + '.o', mutate.truncate(R0, 10))
    synth.write(tmp, 'ujunk.json', json.dumps({'units': [{'source': UNIT}]}).encode())
    check('garbage GCC object', gate_r7.check([gj], [tmp + '/a/c'], tmp + '/ujunk.json')[1][0][:len(UNIT) + 8], UNIT + ':error: ')

    # an object without the section, or with an empty one: empty universe
    ge = tmp + '/ge'
    synth.write(ge, UNIT + '.o', mutate.rename_section(R0, '.fnvnx.layout', '.fnvnx.layoux'))
    check('absent section', gate_r7.check([ge], [tmp + '/a/c'], tmp + '/ujunk.json')[:2], (0, []))
    g, c, us = fixture(tmp + '/e1', b'', b'junk')
    check('empty section (nothing is read)', chk(g, c, us)[:2], (0, []))

    # --units: cl object u1.obj is the unit `d/x.cpp` (the real object under another file name)
    gu, cu = tmp + '/gu', tmp + '/cu'
    synth.write(gu, 'd_x.cpp.o', with_layout(one_row))
    synth.write(cu, 'u1.obj', synth.debug_object(SERIES))
    units = synth.write(tmp, 'units3.json', json.dumps({'units': [{'source': 'q.cpp'}, {'source': 'd/x.cpp'}]}).encode())
    check('units pairing', gate_r7.check([gu], [cu], units)[:2], (1, []))

    # a source listed by two units: the definitions of all its cl objects are collected
    def run_set(objs, rows=one_row):
        t = tempfile.mkdtemp(dir=tmp)
        synth.write(t + '/g', 'x.cpp.o', with_layout(rows))
        for i, o in enumerate(objs):
            synth.write(t + '/c', 'u%d.obj' % i, o)
        us = synth.write(t, 'units.json', json.dumps({'units': [{'source': 'x.cpp'}] * len(objs)}).encode())
        return gate_r7.check([t + '/g'], [t + '/c'], us)[:2]

    def one_rec(size, extra=()):
        k = synth.CV()
        synth.record(k, 's', PK, k.add(0x1203, synth.fieldlist(member(INT, 0, 'a'), member(CHAR, 4, 'b'), *extra)), size)
        return synth.debug_object(k)
    good, nocv = one_rec(8), synth.coff([dict(name=b'.text', chars=0x60500020, data=b'\xc3')], [])
    other = synth.CV()
    synth.record(other, 's', 'UOther@@', other.add(0x1203, synth.fieldlist(member(INT, 0, 'a'))), 4)
    check('set: identical definitions count once', run_set([good, good]), (1, []))
    check('set: one object lacks the key, the other has it', run_set([synth.debug_object(other), good]), (1, []))
    check('set: one object without CodeView', run_set([nocv, good]), (1, []))
    check('set: no CodeView in any', run_set([nocv, nocv]), (1, ['x.cpp:%s: only-gcc: the cl object has no CodeView types (.debug$T)' % PK]))
    check('set: differing definitions are undecided', run_set([good, one_rec(12)]),
          (1, ['x.cpp:%s: undecided: the key has 1 gcc and 2 cl different definitions in the unit' % PK]))
    check('set: an unreadable object is red', run_set([good, b'junk'])[1][0].startswith('x.cpp:%s: undecided: a cl object of the unit is unreadable' % PK), True)

    # rows(): (stem, key, verdict, reason)
    rr = gate_r7.rows([tmp + '/a/g/' + UNIT + '.o'], [tmp + '/a/c/u0.obj'], tmp + '/a/units.json')
    check('rows count', len(rr), len(keys))
    check('rows sorted', rr, sorted(rr))
    check('rows shape', [r for r in rr if r[1] in (K('UPlain'), K('USizeBad'), K('UNoCl'))],
          sorted([(UNIT, K('UNoCl'), 'only-gcc', "cl's debug info of the unit has no definition of the type"), (UNIT, K('UPlain'), 'equal', ''),
                  (UNIT, K('USizeBad'), 'differs', 'size gcc 12 cl 8')]))
    check('rows verdicts', sorted({r[2] for r in rr}), ['differs', 'equal', 'only-gcc', 'undecided'])
    check('rows error', gate_r7.rows([gj + '/' + UNIT + '.o'], [], tmp + '/ujunk.json')[0][:3], (UNIT, '', 'error'))

    # the gate file and the exit status
    def go(g, c, out, extra=()):
        os.makedirs(out)
        p = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', 'gate_r7.py'), '--gcc', g, '--cl', c] + list(extra), env={'GATE_OUT': out}, capture_output=True, text=True)
        with open(os.path.join(out, 'r7.json')) as fh:
            return p.returncode, json.load(fh), p.stdout
    rc, doc, out = go(tmp + '/a/g', tmp + '/a/c', tmp + '/o1', ['--units', tmp + '/a/units.json'])
    check('red file', (rc, doc['format'], doc['name'], doc['status'], doc['universe'], doc['certified']),
          (1, 'fnvnx-gate/1', 'r7', 'red', len(keys), len([k for k, v in WANT.items() if not v])))
    check('red file lists at most 50, sorted', doc['uncertified'], sorted(doc['uncertified'])[:50])
    check('detail', doc['detail'].startswith('r7: %d of %d records certified; ' % (len(keys) - len(want), len(keys))), True)
    with open(tmp + '/o1/r7.ids') as fh:
        check('ids file has all', len(fh.read().split('\n')) - 1, len(want))
    rc, doc, _ = go(gu, cu, tmp + '/o2', ['--units', units])
    check('green file', (rc, doc['status'], doc['universe'], doc['certified'], doc['uncertified']), (0, 'green', 1, 1, []))
    rc, doc, _ = go(ge, tmp + '/a/c', tmp + '/o3', ['--units', tmp + '/ujunk.json'])
    check('empty file', (rc, doc['status'], doc['universe'], doc['certified']), (0, 'green', 0, 0))
    rc, doc, _ = go(tmp + '/missing', tmp + '/a/c', tmp + '/o4')
    check('missing input', (rc, doc['status'], doc['universe'], doc['certified']), (1, 'red', 1, 0))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

if fails:
    print('\n'.join(fails))
    sys.exit(1)
print('ok')
