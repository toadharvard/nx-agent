#!/usr/bin/env python3
"""Check of link/gate_order.py, link/a64order.py, link/framesec.py and the ordering of toolchain/order/fnvnx_order.cc on REAL objects: C++ fixtures
(fixtures/order_ok.cc, order_bad.cc, order_doc.cc) built by the one build (realobj.build: msvcmodel compiler, plugins as stated per fixture; no
direct compiler, assembler or linker call).  OK object: every function certifies (ordered accesses, frame locals, an escaped local, fenced
aggregate copies, read-only table).  BAD object: the same kinds of code built with NO plugin (lifted-origin toy root without plugins): plain
store/load through a non-frame register, alloca, GOT-free global access, an LSE atomic (armv8.1); each red row is named `object:function+0xOFF`.
Negative frame-record and instruction cases are byte mutations of the real OK object (mutate.py, located through elfobj): section removed or
malformed, a record turned dynamic / dropped / duplicated / emptied, an undecodable word, a size-0 function symbol, a store-release turned into the
plain store with the same registers.
NOT COVERED any more (assembler-only instruction sequences, no C++ fixture and no byte edit of a real object gives them; reported to the L2 lead):
see link/tests/FIXTURES.md section test_gate_order.  Run: python3 -I <this file>."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'contract'))
import a64order
import elfobj
import framesec
import mutate
import realobj

fails = []
tmp = tempfile.mkdtemp(prefix='gate-order-')


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r, want %r' % (what, got, want))


def fixture(name, plugins=None, lifted=False, flags=()):
    src = open(os.path.join(realobj.FIXDIR, name + '.cc')).read()
    res = realobj.build({name: src}, plugins=plugins, lifted=lifted, flags=flags)
    if res['status'] != 'green':
        print('FAIL real-object build %s: %s' % (name, res.get('failure')))
        sys.exit(1)
    return open(res['objects'][0]['object'], 'rb').read()


def put(name, data):
    p = os.path.join(tmp, name + '.o')
    open(p, 'wb').write(data)
    return p


def gate(*objs):
    d = tempfile.mkdtemp(dir=tmp)
    r = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', 'gate_order.py')] + list(objs), capture_output=True, text=True,
                       env={'GATE_OUT': d, 'PATH': os.environ.get('PATH', '')})
    return r, json.load(open(os.path.join(d, 'order.json')))


def red(o):
    return {k: v for k, v in a64order.scan(o).rows if v}


# ---- the section parser
P = framesec.parse_frame
check('parse empty', P(b'', 'x'), [])
check('parse lines', P(b'a\t\nb\tdynamic\nc\t-16:12 -4:4 0:8\n', 'x'), [('a', ()), ('b', 'dynamic'), ('c', ((-16, 12), (-4, 4), (0, 8)))])
for bad in (b'a\t', b'a', b'a\t\tb\n', b'\t\n', b'a b\t\n', b'a\t1:0\n', b'a\t01:4\n', b'a\t+1:4\n', b'a\t4:1 -4:4\n', b'a\tx\n', b'a\t1:2 \n',
            b'a\tDynamic\n', b'a\t1:2:3\n', b'\xff\t\n', b'a\t-0:4\n', b'a\t 1:2\n'):
    try:
        P(bad, 'x')
        fails.append('parser accepted %r' % bad)
    except ValueError:
        pass

# ---- the real objects
OK = fixture('order_ok')                                   # all plugins (native root)
BAD = fixture('order_bad', plugins=[], lifted=True)         # no plugin
LSE = fixture('order_bad', plugins=[], lifted=True, flags=['-march=armv8.1-a'])
DOC = fixture('order_doc')


def frame_edit(data, fn):
    t = elfobj.parse(data, 'x').section('.fnvnx.frame').decode()
    rows = fn([l.split('\t') for l in t.split('\n') if l])
    return mutate.replace_section(data, '.fnvnx.frame', ''.join('\t'.join(r) + '\n' for r in rows).encode())


FR = dict(framesec.read(put('ok_fr', OK)))
check('frame records of the real OK object', sorted(FR), ['_Z2roi', '_Z3aggv', '_Z3cpyv', '_Z3escv', '_Z3ldbv', '_Z3retv', '_Z3stbv', '_Z3voli', '_Z4atomPi',
                                                           '_Z4esc2v', '_Z4globv', '_Z4gseti', '_Z6nonesci'])
check('record: non-escaped local has none', FR['_Z6nonesci'], ())
check('record: escaped scalar', FR['_Z3escv'], ((-4, 4),))
check('record: escaped array', FR['_Z4esc2v'], ((-16, 16),))

ok = put('ok', OK)
s = a64order.scan(ok)
check('ok functions certify', [(k, v) for k, v in s.rows if v], [])
check('rules seen', sorted(s.stats), sorted(['ordered', 'frame', 'fenced-load', 'fenced-store', 'readonly', 'frame-escape']))
r, j = gate(ok)
check('gate green', (r.returncode, j['status'], j['certified'] == j['universe'], j['uncertified']), (0, 'green', True, []))
w = [x for _o, x in mutate.function_words(OK, '_Z3voli')]
check('plugin: volatile read is ldar, write is stlr', (any(x & 0xfffffc00 == 0x88dffc00 for x in w), any(x & 0xfffffc00 == 0x889ffc00 for x in w)), (True, True))
wesc = [x for _o, x in mutate.function_words(OK, '_Z3escv')]
check('plugin: the write to the escaped local is stlr', any(x & 0xfffffc00 == 0x889ffc00 for x in wesc), True)

# ---- BAD (no plugin): the plain forms are named
rs = red(put('bad', BAD))
check('exact: plain store through a pointer', rs.get('_Z6pstorePi+0x8'), 'plain-store')
check('exact: plain load through a pointer', rs.get('_Z5ploadPi+0x4'), 'plain-load')
check('exact: plain global store', rs.get('_Z6gstorei+0x4'), 'plain-store')
check('exact: second load after a pointer load', (rs.get('_Z6gload2PPi+0x4'), rs.get('_Z6gload2PPi+0x8')), ('plain-load', 'plain-load'))
check('exact: dynamic sp store', rs.get('_Z3dyni+0x4'), 'plain-store')
check('exact: frame address of an unknown stack pointer', rs.get('_Z3dyni+0x1c/escape'), 'frame-escape-unknown')
check('no section: object', rs.get(''), 'no-frame-section')
check('no record: a function without one', rs.get('_Z6pstorePi'), 'no-frame-record')
check('weak atomic (armv8.1 LSE)', red(put('lse', LSE)).get('_Z3lsePi+0x8'), 'weak-atomic')
r, j = gate(put('bad2', BAD))
check('gate red', (r.returncode, j['status']), (2, 'red'))
check('gate lists the plain store', '%s:_Z6pstorePi+0x8' % put('bad2', BAD) in j['uncertified'], True)
check('uncertified sorted', j['uncertified'], sorted(j['uncertified']))
r2, j2 = gate(put('bad2', BAD), ok)
r3, j3 = gate(ok, put('bad2', BAD))
check('deterministic', j2, j3)

# ---- plugin output for the documented forms (alloca, a by-value aggregate argument copy)
drs = {k: v for k, v in a64order.scan(put('doc', DOC)).rows if v}
check('plugin output: red functions are exactly the documented ones', sorted({k.split('+')[0].split('/')[0] for k in drs}), ['_Z3dyni', '_Z4passv'])
check('alloca: its frame accesses are not F', any(k.startswith('_Z3dyni+') and v == 'plain-store' for k, v in drs.items()), True)
check('by-value aggregate argument copy is an escape no record covers',
      [k.split('+')[0] for k, v in drs.items() if v == 'frame-escape-unrecorded'], ['_Z4passv'])
check('record: alloca is dynamic', dict(framesec.read(put('doc2', DOC)))['_Z3dyni'], 'dynamic')
r, j = gate(put('doc3', DOC))
check('gate on the plugin object is red for the two documented functions', (r.returncode, j['status']), (2, 'red'))

# ---- mutations of the real OK object
o = put('esc_norec', frame_edit(OK, lambda rs: [[a, ''] if a == '_Z3escv' else [a, b] for a, b in rs]))
check('escape: the frame address is unrecorded', set(v for k, v in red(o).items() if k.startswith('_Z3escv')) >= {'frame-escape-unrecorded'}, True)
o = put('noline', frame_edit(OK, lambda rs: [r for r in rs if r[0] != '_Z3escv']))
check('function without a line is named', red(o).get('_Z3escv'), 'no-frame-record')
check('function without a line: its frame accesses are not F either', any(k.startswith('_Z3escv+') for k in red(o)), True)
check('functions with a line are not', [k for k in red(o) if k.startswith('_Z6nonesci')], [])
o = put('nosec', mutate.rename_section(OK, '.fnvnx.frame', '.fnvnx.framx'))
check('no section: object and function', (red(o).get(''), red(o).get('_Z3escv')), ('no-frame-section', 'no-frame-record'))
o = put('badsec', mutate.replace_section(OK, '.fnvnx.frame', b'_Z3escv\t1:0\n'))
check('malformed section', (red(o).get(''), red(o).get('_Z3escv')), ('bad-frame-section', 'no-frame-record'))
o = put('dynrec', frame_edit(OK, lambda rs: [[a, 'dynamic'] if a == '_Z6nonesci' else [a, b] for a, b in rs]))
check('dynamic record: no frame access is F', sorted(k for k in red(o) if k.startswith('_Z6nonesci+')) != [], True)
o = put('dup', frame_edit(OK, lambda rs: rs + [r for r in rs if r[0] == '_Z6nonesci']))
check('two records for one function', red(o).get('_Z6nonesci'), 'no-frame-record')
# an object with a stlr turned into the plain store with the same registers: the twin of the ordered escape
o = put('plain_esc', mutate.stlr_to_str(OK, '_Z3escv', 0))
check('stlr turned plain is red', any(v == 'plain-store' for k, v in red(o).items() if k.startswith('_Z3escv+')), True)
check('stlr turned plain: only that function', sorted({k.split('+')[0] for k in red(o)}), ['_Z3escv'])
# an undecodable word inside a function, and a function symbol of size 0
o = put('undec', mutate.patch_word(OK, '_Z6nonesci', 0, 0x00100000))
check('undecodable word', red(o).get('_Z6nonesci+0x0'), 'undecodable')
o = put('size0', mutate.set_symbol_size(OK, '_Z4globv', 0))
check('size-0 symbol', red(o).get('_Z4globv'), 'size0')

junk = os.path.join(tmp, 'junk.o')
open(junk, 'wb').write(mutate.truncate(OK, 20))
r, j = gate(junk)
check('unreadable object', (r.returncode, j['uncertified']), (2, [junk]))

shutil.rmtree(tmp, ignore_errors=True)
if fails:
    print('FAIL\n' + '\n'.join(fails))
    sys.exit(1)
print('ok')
