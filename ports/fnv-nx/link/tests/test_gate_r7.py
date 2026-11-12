#!/usr/bin/env python3
"""Check of link/gate_r7.py on synthetic ELF and COFF/CodeView objects (invented type names, no game data, no cl, no network):
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
import gate_r7
import synth
from synth import member, bclass, vbclass, vfunctab, cstr, num
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

fails = testcase.FAILED
INT, CHAR, UINT, REAL80 = 0x74, 0x10, 0x75, 0x42


def line(key, size, align, fields='', bases='', flags='', itanium='_Z1T'):
    return '\t'.join([key, str(size), str(align), fields, bases, flags, itanium]) + '\n'


cv = synth.CV()


def S(unique, items, size, **kw):
    fl = cv.add(0x1203, synth.fieldlist(*items)) if items is not None else 0
    return synth.record(cv, 's', unique, fl, size, **kw)


plain = S('UPlain@@', [member(INT, 0, 'a'), member(CHAR, 4, 'b')], 8)
S('USizeBad@@', [member(INT, 0, 'a'), member(CHAR, 4, 'b')], 8)
S('UOffBad@@', [member(INT, 0, 'a'), member(CHAR, 4, 'b')], 8)
S('UMissing@@', [member(INT, 0, 'a'), member(CHAR, 4, 'b')], 8)
bf1 = cv.add(0x1205, synth.bitfield(UINT, 3, 5))
S('UBits@@', [member(bf1, 0, 'x')], 4)
S('UBitsBad@@', [member(bf1, 0, 'x')], 4)
S('UBitsUnnamed@@', [member(bf1, 0, 'x')], 4)
S('UDer@@', [bclass(plain, 0), member(INT, 8, 'z')], 12)
S('UDerBad@@', [bclass(plain, 0), member(INT, 8, 'z')], 12)
S('UVf@@', [vfunctab(), member(INT, 4, 'm')], 8)
S('UVfDiff@@', [member(INT, 4, 'm')], 8)
S('UVb@@', [vbclass(plain)], 8)
S('UAlt@@', [member(INT, 0, 'a')], 4)
S('UAlign@@', [member(INT, 0, 'a')], 4)
S('UPk@@', [member(CHAR, 0, 'a')], 1)
fwd = S('UFwd@@', None, 0, fwd=True)
S('UFwd@@', [member(INT, 0, 'q')], 4)
S('UHold@@', [member(fwd, 0, 'h'), member(INT, 4, 'i')], 8)
S('UHoldPlain@@', [member(plain, 0, 'h')], 8)
S('UReal@@', [member(REAL80, 0, 'r')], 16)
ptr = synth.pointer(cv, 4)
S('UPtr@@', [member(ptr, 0, 'p')], 4)
pm = cv.add(0x1002, struct.pack('<II', INT, 0x0a | (2 << 5)))             # pointer to data member, no size field
S('UPtrMem@@', [member(pm, 0, 'm')], 4)
arr = cv.add(0x1503, struct.pack('<II', INT, INT) + num(12) + cstr(''))
S('UArr@@', [member(arr, 0, 'a')], 12)
mod = cv.add(0x1001, struct.pack('<IH', INT, 1))
S('UMod@@', [member(mod, 0, 'c')], 4)
S('UAmb@@', [member(INT, 0, 'a')], 4)
S('UAmb@@', [member(INT, 0, 'a'), member(INT, 4, 'b')], 8)
S('UDup@@', [member(INT, 0, 'a'), member(INT, 4, 'a')], 8)
S('UOnlyCl@@', [member(INT, 0, 'a')], 4)
S('TUn@@', [member(INT, 0, 'a'), member(CHAR, 0, 'b')], 4, union=True)
S('UEmpty@@', [], 1)
S('UNest@@', [synth.nest('Inner'), member(INT, 0, 'a')], 4)
SERIES = cv

GCC = ''.join([
    line('UPlain@@', 8, 4, 'a:0:4,b:4:1'),
    line('USizeBad@@', 12, 4, 'a:0:4,b:4:1'),
    line('UOffBad@@', 8, 4, 'a:0:4,b:5:1'),
    line('UMissing@@', 8, 4, 'a:0:4,b:4:1,c:8:4'),
    line('UBits@@', 4, 4, 'x:0:5:3:4'),
    line('UBitsBad@@', 4, 4, 'x:0:6:3:4'),
    line('UBitsUnnamed@@', 4, 4, '#0:0:0:5:4,x:0:5:3:4'),
    line('UDer@@', 12, 4, 'z:8:4', 'UPlain@@:0:n'),
    line('UDerBad@@', 12, 4, 'z:8:4', 'UPlain@@:4:n'),
    line('UVf@@', 8, 4, '<vptr>:0:4,m:4:4'),
    line('UVfDiff@@', 8, 4, '<vptr>:0:4,m:4:4'),
    line('UVb@@', 8, 4, '', 'UPlain@@:4:v'),
    line('UAlt@@', 4, 4, 'a:0:4', '', 'altered'),
    line('UAlign@@', 4, 8, 'a:0:4'),
    line('UPk@@', 1, 1, 'a:0:1', '', 'packed'),
    line('UFwd@@', 4, 4, 'q:0:4'),
    line('UHold@@', 8, 4, 'h:0:4,i:4:4'),
    line('UHoldPlain@@', 8, 4, 'h:0:8'),
    line('UReal@@', 16, 16, 'r:0:10'),
    line('UPtr@@', 4, 4, 'p:0:4'),
    line('UPtrMem@@', 4, 4, 'm:0:4'),
    line('UArr@@', 12, 4, 'a:0:12'),
    line('UMod@@', 4, 4, 'c:0:4'),
    line('UAmb@@', 4, 4, 'a:0:4'),
    line('UDup@@', 8, 4, 'a:0:4,a:4:4'),
    line('TUn@@', 4, 4, 'a:0:4,b:0:1'),
    line('UEmpty@@', 1, 1),
    line('UNest@@', 4, 4, 'a:0:4'),
    line('UNoCl@@', 4, 4, 'a:0:4'),
    line('!_ZN3fooE', 4, 4, 'a:0:4', '', 'nokey:no rule names it', '_ZN3fooE'),
    line('UPlain@@', 8, 4, 'a:0:4,b:4:1'),          # the same key twice in one unit with equal content: one item
])
WANT = {
    'UPlain@@': None, 'USizeBad@@': 'differs: size gcc 12 cl 8', 'UOffBad@@': 'differs: member b bit offset gcc 40 cl 32',
    'UMissing@@': 'differs: member c only in gcc (bit 64)', 'UBits@@': None, 'UBitsBad@@': 'differs: member x bit offset gcc 6 cl 5',
    'UBitsUnnamed@@': None, 'UDer@@': None, 'UDerBad@@': 'differs: base UPlain@@ offset gcc 4 cl 0',
    'UVf@@': 'undecided: the offset of the vfptr is not in CodeView (LF_VFUNCTAB has no offset)',
    'UVfDiff@@': 'differs: introduces a vfptr: gcc True cl False', 'UVb@@': 'differs: cl has a vbptr (virtual bases), gcc a vptr: different object models',
    'UAlt@@': None,         # alignment is not part of the claim: a differing or altered alignment does not change the verdict
    'UAlign@@': None,
    'UPk@@': None,
    'UFwd@@': None, 'UHold@@': None, 'UHoldPlain@@': None,
    'UReal@@': 'undecided: size of member r: primitive type 0x42 has no size here',
    'UPtr@@': None, 'UPtrMem@@': 'undecided: size of member m: pointer to member without a size',
    'UArr@@': None, 'UMod@@': None, 'UAmb@@': 'undecided: the key has 1 gcc and 2 cl different definitions in the unit',
    'UDup@@': 'undecided: member name twice: a,a', 'TUn@@': None, 'UEmpty@@': None, 'UNest@@': None,
    'UNoCl@@': "only-gcc: cl's debug info of the unit has no definition of the type",
    '!_ZN3fooE': "only-gcc: cl's debug info of the unit has no definition of the type (no key: no rule names it)",
}


def reds(r):
    return {x.split(':', 1)[1].split(': ', 1)[0]: x.split(': ', 1)[1] for x in r}


def fixture(t, gcc_text, cl_bytes, stem='u'):
    synth.write(t + '/g', stem + '.o', synth.elf([('.fnvnx.layout', gcc_text.encode() if isinstance(gcc_text, str) else gcc_text, 0)], None))
    if cl_bytes is not None:
        synth.write(t + '/c', stem + '.obj', cl_bytes)
    else:
        os.makedirs(t + '/c', exist_ok=True)
    return [t + '/g'], [t + '/c']


tmp = tempfile.mkdtemp()
try:
    g, c = fixture(tmp + '/a', GCC, synth.debug_object(SERIES))
    uni, red, cnt = gate_r7.check(g, c)
    keys = set(WANT)
    case('universe is the set of GCC keys', got=uni, want=len(keys))
    got = reds(red)
    want = {k: v for k, v in WANT.items() if v}
    for k in sorted(set(got) | set(want)):
        case('verdict ', got=got.get(k), want=want.get(k), key=k)
    case('sorted', got=red, want=sorted(red))
    case('certified count', got=uni - len(red), want=len([k for k, v in WANT.items() if not v]))
    case('verdict counts', got=(cnt['equal'], cnt['differs'], cnt['undecided'], cnt['only-gcc']),
          want=(len([k for k, v in WANT.items() if not v]), sum(1 for v in WANT.values() if v and v.startswith('differs')),
           sum(1 for v in WANT.values() if v and v.startswith('undecided')), 2))

    # no cl object / no CodeView / an unreadable cl object / a CodeView table that does not parse
    one = line('UPlain@@', 8, 4, 'a:0:4,b:4:1')
    g, c = fixture(tmp + '/n1', one, None)
    case('no cl object', got=gate_r7.check(g, c)[:2], want=(1, ['u:UPlain@@: only-gcc: no cl object for the unit']))
    g, c = fixture(tmp + '/n2', one, synth.coff([dict(name=b'.text', chars=0x60500020, data=b'\xc3')], []))
    case('no CodeView', got=gate_r7.check(g, c)[:2], want=(1, ['u:UPlain@@: only-gcc: the cl object has no CodeView types (.debug$T)']))
    g, c = fixture(tmp + '/n3', one, b'junk junk junk')
    case('unreadable cl object', got=(gate_r7.check(g, c)[0], gate_r7.check(g, c)[1][0].startswith('u:UPlain@@: undecided: a cl object of the unit is unreadable')), want=(1, True))
    g, c = fixture(tmp + '/n4', one, synth.coff([dict(name=b'.debug$T', chars=0x40000040, data=struct.pack('<I', 3) + b'xxxx')], []))
    case('bad signature', got=gate_r7.check(g, c)[1][0].startswith('u:UPlain@@: undecided: a cl object of the unit is unreadable'), want=True)
    bad = synth.CV()
    bad.add(0x1505, b'\0\0')
    g, c = fixture(tmp + '/n5', one, synth.debug_object(bad))
    case('truncated record', got=gate_r7.check(g, c)[1][0].startswith('u:UPlain@@: undecided: a cl object of the unit is unreadable'), want=True)
    blob = synth.CV()
    blob.add(0x1505, b'\0' * 4)
    g, c = fixture(tmp + '/n6', one, synth.coff([dict(name=b'.debug$T', chars=0x40000040, data=blob.blob()[:-1])], []))
    case('table cut inside a record', got=gate_r7.check(g, c)[1][0].startswith('u:UPlain@@: undecided: a cl object of the unit is unreadable'), want=True)
    # members that cannot be read: undecided, not "every member only in gcc"
    brk = synth.CV()
    fl = brk.add(0x1203, struct.pack('<H', 0x1fff) + b'\0' * 6)
    synth.record(brk, 's', 'UPlain@@', fl, 8)
    g, c = fixture(tmp + '/n7', one, synth.debug_object(brk))
    case('unreadable members', got=gate_r7.check(g, c)[1], want=['u:UPlain@@: undecided: CodeView: members: field leaf 0x1fff'])

    # malformed GCC sections are errors (one item), never a pass
    for what, text in (('six fields', 'a\t1\t1\t\t\t\n'), ('bad size', 'a\tx\t1\t\t\t\t_Z\n'), ('bad field', 'a\t1\t1\tf:1\t\t\t_Z\n'),
                       ('bad base flag', 'a\t1\t1\t\tb:0:x\t\t_Z\n'), ('unknown flag', 'a\t1\t1\t\t\tweird\t_Z\n'), ('empty key', '\t1\t1\t\t\t\t_Z\n'),
                       ('bit offset 8', 'a\t1\t1\tf:0:8:1:1\t\t\t_Z\n'), ('empty element', 'a\t1\t1\tf:0:1,,g:1:1\t\t\t_Z\n'), ('no final LF', 'a\t1\t1\t\t\t\t_Z'),
                       ('not UTF-8', b'a\t1\t1\t\t\t\t\xff\n')):
        g, c = fixture(tmp + '/m_' + what.replace(' ', '_'), text, synth.debug_object(SERIES))
        u, r, _ = gate_r7.check(g, c)
        case('malformed ', got=(u, len(r), r[0].startswith('u:error: ')), want=(1, 1, True), key=what)
    gj = tmp + '/gj'
    synth.write(gj, 'u.o', b'not an elf')
    case('garbage GCC object', got=gate_r7.check([gj], [tmp + '/a/c'])[1][0][:9], want='u:error: ')

    # an object without the section, or with an empty one: empty universe
    ge = tmp + '/ge'
    synth.write(ge, 'u.o', synth.elf([('.text', b'\0', 6)], None))
    case('absent section', got=gate_r7.check([ge], [tmp + '/a/c'])[:2], want=(0, []))
    g, c = fixture(tmp + '/e1', b'', b'junk')
    case('empty section (nothing is read)', got=gate_r7.check(g, c)[:2], want=(0, []))

    # --units: cl object u1.obj is the unit `d/x.cpp`
    gu, cu = tmp + '/gu', tmp + '/cu'
    synth.write(gu, 'd_x.cpp.o', synth.elf([('.fnvnx.layout', one.encode(), 0)], None))
    synth.write(cu, 'u1.obj', synth.debug_object(SERIES))
    units = synth.write(tmp, 'units.json', json.dumps({'units': [{'source': 'q.cpp'}, {'source': 'd/x.cpp'}]}).encode())
    case('units pairing', got=gate_r7.check([gu], [cu], units)[:2], want=(1, []))

    # a source listed by two units: the definitions of all its cl objects are collected
    def run_set(objs, text=one):
        t = tempfile.mkdtemp(dir=tmp)
        synth.write(t + '/g', 'x.cpp.o', synth.elf([('.fnvnx.layout', text.encode(), 0)], None))
        for i, o in enumerate(objs):
            synth.write(t + '/c', 'u%d.obj' % i, o)
        us = synth.write(t, 'units.json', json.dumps({'units': [{'source': 'x.cpp'}] * len(objs)}).encode())
        return gate_r7.check([t + '/g'], [t + '/c'], us)[:2]

    def one_rec(size, extra=()):
        k = synth.CV()
        synth.record(k, 's', 'UPlain@@', k.add(0x1203, synth.fieldlist(member(INT, 0, 'a'), member(CHAR, 4, 'b'), *extra)), size)
        return synth.debug_object(k)
    good, nocv = one_rec(8), synth.coff([dict(name=b'.text', chars=0x60500020, data=b'\xc3')], [])
    other = synth.CV()
    synth.record(other, 's', 'UOther@@', other.add(0x1203, synth.fieldlist(member(INT, 0, 'a'))), 4)
    case('set: identical definitions count once', got=run_set([good, good]), want=(1, []))
    case('set: one object lacks the key, the other has it', got=run_set([synth.debug_object(other), good]), want=(1, []))
    case('set: one object without CodeView', got=run_set([nocv, good]), want=(1, []))
    case('set: no CodeView in any', got=run_set([nocv, nocv]), want=(1, ['x.cpp:UPlain@@: only-gcc: the cl object has no CodeView types (.debug$T)']))
    case('set: differing definitions are undecided', got=run_set([good, one_rec(12)]),
          want=(1, ['x.cpp:UPlain@@: undecided: the key has 1 gcc and 2 cl different definitions in the unit']))
    case('set: an unreadable object is red', got=run_set([good, b'junk'])[1][0].startswith('x.cpp:UPlain@@: undecided: a cl object of the unit is unreadable'), want=True)

    # rows(): (stem, key, verdict, reason)
    rr = gate_r7.rows([tmp + '/a/g/u.o'], [tmp + '/a/c/u.obj'])
    case('rows count', got=len(rr), want=len(keys))
    case('rows sorted', got=rr, want=sorted(rr))
    case('rows shape', got=[r for r in rr if r[1] in ('UPlain@@', 'USizeBad@@', 'UNoCl@@')],
          want=[('u', 'UNoCl@@', 'only-gcc', "cl's debug info of the unit has no definition of the type"), ('u', 'UPlain@@', 'equal', ''), ('u', 'USizeBad@@', 'differs', 'size gcc 12 cl 8')])
    case('rows verdicts', got=sorted({r[2] for r in rr}), want=['differs', 'equal', 'only-gcc', 'undecided'])
    case('rows error', got=gate_r7.rows([tmp + '/gj/u.o'], [])[0][:3], want=('u', '', 'error'))

    # the gate file and the exit status
    def go(g, c, out, extra=()):
        os.makedirs(out)
        p = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', 'gate_r7.py'), '--gcc', g, '--cl', c] + list(extra), env={'GATE_OUT': out}, capture_output=True, text=True)
        with open(os.path.join(out, 'r7.json')) as fh:
            return p.returncode, json.load(fh), p.stdout
    rc, doc, out = go(tmp + '/a/g', tmp + '/a/c', tmp + '/o1')
    case('red file', got=(rc, doc['format'], doc['name'], doc['status'], doc['universe'], doc['certified']),
          want=(1, 'fnvnx-gate/1', 'r7', 'red', len(keys), len([k for k, v in WANT.items() if not v])))
    case('red file lists at most 50, sorted', got=doc['uncertified'], want=sorted(doc['uncertified'])[:50])
    case('detail', got=doc['detail'].startswith('r7: %d of %d records certified; ' % (len(keys) - len(want), len(keys))), want=True)
    with open(tmp + '/o1/r7.ids') as fh:
        case('ids file has all', got=len(fh.read().split('\n')) - 1, want=len(want))
    rc, doc, _ = go(gu, cu, tmp + '/o2', ['--units', units])
    case('green file', got=(rc, doc['status'], doc['universe'], doc['certified'], doc['uncertified']), want=(0, 'green', 1, 1, []))
    rc, doc, _ = go(ge, tmp + '/a/c', tmp + '/o3')
    case('empty file', got=(rc, doc['status'], doc['universe'], doc['certified']), want=(0, 'green', 0, 0))
    rc, doc, _ = go(tmp + '/missing', tmp + '/a/c', tmp + '/o4')
    case('missing input', got=(rc, doc['status'], doc['universe'], doc['certified']), want=(1, 'red', 1, 0))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

if fails:
    print('\n'.join(fails))
    sys.exit(1)
print('ok')
