#!/usr/bin/env python3
"""Check of link/gate_order.py, link/a64order.py, link/framesec.py and the ordering of toolchain/order/fnvnx_order.cc on REAL objects: C++ fixtures
(fixtures/order_ok.cc, order_bad.cc, order_doc.cc) built by the one build (realobj.build: msvcmodel compiler, plugins as stated per fixture; no
direct compiler, assembler or linker call).  OK object: every function certifies (ordered accesses, frame locals, an escaped local, fenced
aggregate copies, read-only table).  BAD object: the same kinds of code built with NO plugin (lifted-origin toy root without plugins): plain
store/load through a non-frame register, alloca, GOT-free global access, an LSE atomic (armv8.1); each red row is named `object:function+0xOFF`.
Negative frame-record and instruction cases are byte mutations of the real OK object (mutate.py, located through elfobj): section removed or
malformed, a record turned dynamic / dropped / duplicated / emptied, an undecodable word, a size-0 function symbol, a store-release turned into the
plain store with the same registers.
Hand-written assembler cases (instruction sequences no C++ fixture gives: exclusives, literal and GOT loads, fences on one path, read-only table
stores, x29 overwrites, record overlap/pair, aliases) are `.S` units built by the same build (assemble() -> realobj.build with asm_flags).
Run: python3 -I <this file>."""
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

# ---- hand-written assembler units (.S through the recipe): instruction sequences no C++ fixture gives
def func(name, body):
    return '\t.global %s\n\t.type %s,%%function\n%s:\n%s\t.size %s,.-%s\n' % (name, name, name, ''.join('\t' + l + '\n' for l in body), name, name)


_n = [0]


def assemble(name, funcs, frame=None, extra=''):
    """a hand-written assembler UNIT (`name.S`) built by the one build (realobj.build: the recipe preprocesses and assembles it with the pinned
    msvcmodel driver, `-mabi=ilp32 -march=armv8.1-a`; no direct `as`).  funcs {symbol: [asm lines]}; frame {symbol: record text} (None: no
    .fnvnx.frame section; a missing symbol has no line; a str value is the raw section text)"""
    t = '\t.text\n' + ''.join(func(n, b) for n, b in funcs.items()) + extra + '\t.section .rodata\ntab:\t.word 1,2,3,4\n\t.data\ngv:\t.word 0\n'
    if isinstance(frame, str):
        t += '\t.section .fnvnx.frame,"",%progbits\n\t.ascii "' + frame + '"\n'
    elif frame is not None:
        t += '\t.section .fnvnx.frame,"",%progbits\n' + ''.join('\t.ascii "%s\\t%s\\n"\n' % (k, v) for k, v in sorted(frame.items()))
    res = realobj.build({name + '.S': t}, asm_flags=['-mabi=ilp32', '-march=armv8.1-a'])
    if res['status'] != 'green':
        print('FAIL assembler unit %s: %s' % (name, res.get('failure')))
        sys.exit(1)
    return put(name, open(res['objects'][0]['object'], 'rb').read())



FUNCS = {
    'ok_ordered': ['ldar w1, [x0]', 'stlr w1, [x0]', 'ldaxr w2, [x0]', 'stlxr w3, w2, [x0]', 'ldarb w1, [x0]', 'ret'],
    'ok_frame': ['stp x29, x30, [sp, #-32]!', 'mov x29, sp', 'str w0, [x29, #28]', 'ldr w1, [sp, #28]', 'bl ext',
                 'ldr w2, [x29, #28]', 'ldp x29, x30, [sp], #32', 'ret'],
    'ok_fenced': ['ldr w1, [x0]', 'dmb ishld', 'dmb ish', 'str w1, [x0, #4]', 'ret'],
    'ok_fenced_branch': ['cbz w1, 1f', 'ldr w2, [x0]', 'dmb ishld', '1:', 'ret'],
    'ok_ro': ['adrp x1, tab', 'add x1, x1, :lo12:tab', 'ldr w0, [x1, x0, lsl #2]', 'adrp x2, :got:gv', 'ldr w2, [x2, :got_lo12:gv]', 'ret'],
    'ok_literal': ['ldr w0, lit', 'ret', 'lit: .word 5'],
    'ok_escape': ['sub sp, sp, #16', 'add x1, sp, #8', 'adrp x2, gv', 'add x2, x2, :lo12:gv', 'stlr w1, [x2]', 'stlr wzr, [x1]',
                  'add sp, sp, #16', 'ret'],
    'ok_local_base': ['sub sp, sp, #16', 'add x1, sp, #8', 'str w0, [x1]', 'ldr w2, [x1]', 'add sp, sp, #16', 'ret'],
    'bad_plain_store': ['str w1, [x0]', 'ret'],
    'bad_plain_load': ['ldr w0, [x1, #8]', 'ret'],
    'bad_dynsp': ['sub sp, sp, x0', 'str w1, [sp]', 'ret'],
    'bad_x29': ['stp x29, x30, [sp, #-16]!', 'mov x29, sp', 'mov x29, x0', 'str w1, [x29]', 'ldp x29, x30, [sp], #16', 'ret'],
    'bad_one_path': ['cbz w1, 1f', 'dmb ishld', '1:', 'ldr w2, [x0]', 'ret'],
    'bad_one_path2': ['ldr w2, [x0]', 'cbz w1, 1f', 'dmb ishld', '1:', 'ret'],
    'bad_call': ['stp x29, x30, [sp, #-16]!', 'ldr w2, [x0]', 'bl ext', 'dmb ishld', 'ldp x29, x30, [sp], #16', 'ret'],
    'bad_store_late': ['str w1, [x0]', 'dmb ish', 'ret'],
    'bad_store_ro': ['adrp x1, tab', 'add x1, x1, :lo12:tab', 'str w0, [x1]', 'ret'],
    'bad_second_load': ['adrp x2, :got:gv', 'ldr w2, [x2, :got_lo12:gv]', 'ldr w0, [x2]', 'ret'],
    'bad_excl': ['ldxr w1, [x0]', 'stxr w2, w1, [x0]', 'ret'],
    'bad_lse': ['ldadd w1, w2, [x0]', 'ret'],
    'bad_ro_written': ['adrp x1, tab', 'add x1, x1, :lo12:tab', 'mov x1, x0', 'ldr w0, [x1]', 'ret'],
    'bad_load_only_ishst': ['ldr w1, [x0]', 'dmb ishst', 'ret'],
    # the plugin's escaped local, with the write after the escape made plain: the twin of the ordered ok_escape
    'bad_escape_plain': ['sub sp, sp, #16', 'add x1, sp, #8', 'adrp x2, gv', 'add x2, x2, :lo12:gv', 'stlr w1, [x2]', 'str wzr, [sp, #8]',
                         'add sp, sp, #16', 'ret'],
    'bad_escape_plain_x29': ['stp x29, x30, [sp, #-32]!', 'mov x29, sp', 'add x1, x29, #24', 'adrp x2, gv', 'add x2, x2, :lo12:gv',
                             'stlr w1, [x2]', 'str wzr, [x29, #24]', 'ldp x29, x30, [sp], #32', 'ret'],
    'bad_escape_unrecorded': ['sub sp, sp, #32', 'add x0, sp, #16', 'str x1, [sp, #4]', 'bl ext', 'add sp, sp, #32', 'ret'],
    'bad_escape_unknown': ['sub sp, sp, x1', 'mov x0, sp', 'bl ext', 'ret'],
}
BADS = [n for n in FUNCS if n.startswith('bad_')]
OKS = [n for n in FUNCS if n.startswith('ok_')]
# the recorded frame: ok_escape's slot sp+8 = CFA-8 (16-byte frame) is recorded, so is bad_escape_plain's, bad_escape_plain_x29's
REC = {n: '' for n in FUNCS}
REC['ok_escape'] = '-8:4'
REC['bad_escape_plain'] = '-8:4'
REC['bad_escape_plain_x29'] = '-8:4'
REC['bad_dynsp'] = ''

good = assemble('good', {n: FUNCS[n] for n in OKS}, {n: REC[n] for n in OKS})
s = a64order.scan(good)
check('ok functions certify', [(k, v) for k, v in s.rows if v], [])
check('rules seen', sorted(s.stats), sorted(['ordered', 'frame', 'fenced-load', 'fenced-store', 'readonly', 'linker', 'literal', 'frame-escape']))
r, j = gate(good)
check('gate green', (r.returncode, j['status'], j['certified'] == j['universe'], j['uncertified']), (0, 'green', True, []))

for n in BADS:
    o = assemble(n, {n: FUNCS[n]}, {n: REC[n]})
    rs = red(o)
    check(n + ' is red', len(rs) >= 1 and all(k.startswith(n + '+0x') for k in rs), True)
bad_all = assemble('bad', {n: FUNCS[n] for n in BADS}, {n: REC[n] for n in BADS})
rs = red(bad_all)
check('exact: plain store', rs.get('bad_plain_store+0x0'), 'plain-store')
check('exact: plain load', rs.get('bad_plain_load+0x0'), 'plain-load')
check('exact: dynamic sp', rs.get('bad_dynsp+0x4'), 'plain-store')
check('exact: no frame pointer after overwrite', rs.get('bad_x29+0xc'), 'plain-store')
check('exact: second load after GOT', rs.get('bad_second_load+0x8'), 'plain-load')
check('exact: weak atomic', rs.get('bad_lse+0x0'), 'weak-atomic')
check('exact: plain sp store to an escaped slot', rs.get('bad_escape_plain+0x14'), 'plain-store')
check('exact: plain x29 store to an escaped slot', rs.get('bad_escape_plain_x29+0x18'), 'plain-store')
check('only that store in the escaped twins', sorted(k for k in rs if k.startswith('bad_escape_plain')),
      ['bad_escape_plain+0x14', 'bad_escape_plain_x29+0x18'])
check('exact: frame address out of every range', rs.get('bad_escape_unrecorded+0xc/escape'), 'frame-escape-unrecorded')
check('exact: frame address of an unknown stack pointer', rs.get('bad_escape_unknown+0x8/escape'), 'frame-escape-unknown')
check('only bad functions named', sorted({k.split('+')[0] for k in rs}), sorted(BADS))
r, j = gate(bad_all)
check('gate red', (r.returncode, j['status']), (2, 'red'))
check('gate lists the plain store', '%s:bad_plain_store+0x0' % bad_all in j['uncertified'], True)
check('uncertified sorted', j['uncertified'], sorted(j['uncertified']))
r2, j2 = gate(bad_all, good)
r3, j3 = gate(good, bad_all)
check('deterministic', j2, j3)
# the same hand-assembled escape with the ordered store and with a record that does not cover the slot
o = assemble('esc_norec', {'f': FUNCS['ok_escape']}, {'f': ''})
check('escape of a slot the record lacks is red', red(o), {'f+0x10/escape': 'frame-escape-unrecorded'})
o = assemble('esc_rec', {'f': FUNCS['ok_escape']}, {'f': '-8:4'})
check('escape into a recorded slot is green', red(o), {})
o = assemble('rec_overlap', {'f': ['sub sp, sp, #16', 'str w0, [sp, #4]', 'str w0, [sp, #12]', 'add sp, sp, #16', 'ret']}, {'f': '-4:4'})
check('plain store beside a recorded slot is F, onto it is not', red(o), {'f+0x8': 'plain-store'})
o = assemble('rec_above', {'f': ['ldr w0, [sp, #8]', 'ret']}, {'f': ''})
check('access above the CFA (incoming stack argument) is not F', red(o), {'f+0x0': 'plain-load'})
o = assemble('rec_pair', {'f': ['sub sp, sp, #16', 'stp x0, x1, [sp, #0]', 'add sp, sp, #16', 'ret']}, {'f': '-12:4'})
check('a pair store sizes 16 bytes', red(o), {'f+0x4': 'plain-store'})

# frame record problems
o = assemble('noline', {'fa': FUNCS['ok_frame'], 'fb': FUNCS['ok_frame']}, {'fa': ''})
check('function without a line: its frame accesses are not F either', sorted(red(o)), sorted(
    ['fb'] + ['fb+0x%x' % x for x in (0, 8, 0xc, 0x14, 0x18)]))
check('function without a line is named', red(o).get('fb'), 'no-frame-record')
check('function with a line is not', [k for k in red(o) if k.startswith('fa')], [])
o = assemble('nosec', {'fa': FUNCS['ok_frame']}, None)
check('no section: object and function', (red(o).get(''), red(o).get('fa')), ('no-frame-section', 'no-frame-record'))
o = assemble('badsec', {'fa': FUNCS['ok_frame']}, 'fa\\t1:0\\n')
check('malformed section', (red(o).get(''), red(o).get('fa')), ('bad-frame-section', 'no-frame-record'))
o = assemble('dynrec', {'fa': FUNCS['ok_frame']}, {'fa': 'dynamic'})
check('dynamic record: no frame access is F', sorted(k for k in red(o) if '+' in k), ['fa+0x0', 'fa+0x14', 'fa+0x18', 'fa+0x8', 'fa+0xc'])
o = assemble('tworec', {'fa': FUNCS['ok_frame']}, {'fa': ''}, '')
o2 = assemble('dup', {'fa': FUNCS['ok_frame']}, 'fa\\t\\nfa\\t\\n')
check('two records for one function', red(o2).get('fa'), 'no-frame-record')
alias = '\t.global fb\n\t.set fb, fa\n'
o = assemble('alias_ok', {'fa': FUNCS['ok_frame']}, {'fa': '', 'fb': ''}, alias)
check('alias with the same record', red(o), {})
o = assemble('alias_bad', {'fa': FUNCS['ok_frame']}, {'fa': '-8:4', 'fb': ''}, alias)
check('alias with another record', red(o).get('fa'), 'frame-record-mismatch')

# an undecodable word inside a function, and a function symbol of size 0
o = assemble('undec', {}, {'u1': '', 'z0': ''}, '\t.global u1\n\t.type u1,%function\nu1:\n\t.inst 0x00100000\n\tret\n\t.size u1,.-u1\n'
             '\t.global z0\n\t.type z0,%function\nz0:\n\tldar w0, [x0]\n\tret\n')
check('undecodable word and size-0 symbol', {k: v for k, v in red(o).items()}, {'u1+0x0': 'undecodable', 'z0': 'size0'})

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
