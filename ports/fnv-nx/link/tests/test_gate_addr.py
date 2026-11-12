#!/usr/bin/env python3
"""Check of link/gate_addr.py and toolchain/addr/fnvnx_addr.cc.  Compiles small C++ fixtures with the distro cross compiler and the
plugin built from the tracked source (the argv of verify/recipe.py's plugin stage), and hand-assembles negative objects.  Cases:
the plugin writes `.fnvnx.addr` with seen == routed and a barrier asm at every address sum (the code has a 32-bit add and no
`uxtw` addressing mode); thunks and constructor aliases get records; the gate is green on those objects; red and named: an
object without the section, a function without a record, two records, seen != routed, a malformed line (every function of
the object), an unreadable object; the layout fact (valid; each failing check; missing file; zero objects with and without one);
the plugin's LIMIT equals gate_addr.GUARD and a large or run-time displacement is routed (32-bit add, no 64-bit one); the
library form rows(); the gate file and exit status.  No network, no game data.  Run: python3 -I <this file>."""
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
import a64obj
import addrsec
import gate_addr
import mutate
import realobj

TOOL = os.path.join(HERE, '..', '..', 'toolchain', 'addr', 'fnvnx_addr.cc')
fails = []
tmp = tempfile.mkdtemp(prefix='gate-addr-')


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r, want %r' % (what, got, want))



def fixture(name):
    src = open(os.path.join(realobj.FIXDIR, name + '.cc')).read()
    res = realobj.build({name: src})
    if res['status'] != 'green':
        print('FAIL real-object build %s: %s' % (name, res.get('failure')))
        sys.exit(1)
    return open(res['objects'][0]['object'], 'rb').read()


def put(name, data):
    p = os.path.join(tmp, name + '.o')
    open(p, 'wb').write(data)
    return p


def asm_obj(name, funcs, section):
    """the real object of fixtures/addr_fn.cc (functions fa, fb) with its `.fnvnx.addr` section replaced by SECTION (None: section renamed away);
    FUNCS = [] -> the real object with no function (fixtures/addr_nofn.cc)"""
    data = BASE_FN if funcs else BASE_NOFN
    if not funcs:
        return put(name, data)   # the plugin writes an empty section for an object without a function
    if section is None:
        data = mutate.rename_section(data, '.fnvnx.addr', '.fnvnx.adds')
    else:
        data = mutate.replace_section(data, '.fnvnx.addr', section.replace('\\t', '\t').replace('\\n', '\n').encode())
    return put(name, data)


def layout_file(name, doc, raw=None):
    p = os.path.join(tmp, name + '.json')
    with open(p, 'w') as fh:
        fh.write(raw if raw is not None else json.dumps(doc))
    return p


def reg(name, lo, hi, prot='rw-', kind='image'):
    return {'name': name, 'lo': lo, 'hi': hi, 'prot': prot, 'kind': kind}


def good_doc():
    return {'schema': 'fnvnx-layout/1', 'regions': [
        reg('lowguard', 0, 0x10000, '---', 'guard'), reg('image', 0x10000, 0x40000000, 'r-x'), reg('heap', 0x40000000, 0xFFFF0000, 'rw-', 'heap'),
        reg('highguard', 0xFFFF0000, 1 << 32, '---', 'guard')]}


LAY = layout_file('good_layout', good_doc())


def gate(*objs, layout=LAY):
    d = tempfile.mkdtemp(dir=tmp)
    r = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', 'gate_addr.py')] + (['--layout', layout] if layout else []) + list(objs),
                       capture_output=True, text=True, env={'GATE_OUT': d, 'PATH': os.environ.get('PATH', '')})
    return r, json.load(open(os.path.join(d, 'addr.json')))


GOOD = fixture('addr_main')
BASE_FN = fixture('addr_fn')
BASE_NOFN = fixture('addr_nofn')
good = put('good', GOOD)
plain = put('plain', mutate.rename_section(GOOD, '.fnvnx.addr', '.fnvnx.adds'))
if True:
    rows = addrsec.read(good)
    check('section present', rows is not None, True)
    byname = {s: (a, b) for s, a, b in rows or []}
    check('r1 routed', byname.get('_Z2r1j', (0, 0))[0] >= 1 and byname['_Z2r1j'][0] == byname['_Z2r1j'][1], True)
    check('r6 routed', byname.get('_Z2r6Pcj', (0, 0))[0] >= 1 and byname['_Z2r6Pcj'][0] == byname['_Z2r6Pcj'][1], True)
    check('constant not counted', byname.get('_Z2r9v'), (0, 0))
    check('every record seen == routed', all(a == b for _, a, b in rows or []), True)
    check('sorted records', [s for s, _, _ in rows or []], sorted(s for s, _, _ in rows or []))
    names = sorted(n for _, _, _, ns in a64obj.read(good).functions for n in ns)
    check('record for every function symbol', sorted(set(names) - set(byname)), [])
    check('thunk or alias record', any(n.startswith('_ZThn') for n in names) and byname.get(
        [n for n in names if n.startswith('_ZThn')][0]), (0, 0))
    code = subprocess.run(['aarch64-linux-gnu-objdump', '-d', '--no-show-raw-insn', good], capture_output=True, text=True).stdout
    r1 = code.split('<_Z2r1j>:')[1].split('\n\n')[0]
    check('r1 adds in 32 bits, no displacement addressing', ('add\tw' in r1) and ('#0x40]' not in r1), True)
    r, j = gate(good)
    check('green on plugin object', (r.returncode, j['status'], j['certified'] == j['universe']), (0, 'green', True))
    r, j = gate(plain)
    check('no section: red', (r.returncode, j['status']), (2, 'red'))
    check('no section: every function named', sorted(j['uncertified']), sorted([plain] + [plain + ':' + n for n in names]))
    check('no section: object item named', plain in j['uncertified'], True)

o = asm_obj('norec', ['fa', 'fb'], 'fa\\t0\\t0\\n')
r, j = gate(o)
check('function without a record', (r.returncode, j['uncertified']), (2, [o + ':fb']))
o = asm_obj('two', ['fa'], 'fa\\t0\\t0\\nfa\\t0\\t0\\nfb\\t0\\t0\\n')
r, j = gate(o)
check('two records', (r.returncode, j['uncertified']), (2, [o + ':fa']))
o = asm_obj('skew', ['fa', 'fb'], 'fa\\t2\\t1\\nfb\\t1\\t1\\n')
r, j = gate(o)
check('seen != routed', (r.returncode, j['uncertified']), (2, [o + ':fa']))
for k, bad in enumerate(['fa\\t0\\n', 'fa\\t0\\t0', 'fa\\t-1\\t0\\n', 'fa\\t0\\t0\\t0\\n', '\\t0\\t0\\n', 'fa\\t0x1\\t0\\n']):
    o = asm_obj('mal%d' % k, ['fa', 'fb'], bad)
    r, j = gate(o)
    check('malformed %r: all functions red' % bad, (r.returncode, sorted(j['uncertified'])), (2, sorted([o, o + ':fa', o + ':fb'])))
o = asm_obj('ok', ['fa', 'fb'], 'fb\\t3\\t3\\nfa\\t0\\t0\\nzz\\t1\\t0\\n')
r, j = gate(o)
check('record of a non-function is ignored; green', (r.returncode, j['status'], j['universe']), (0, 'green', 4))
o = asm_obj('empty', [], '')
r, j = gate(o)
check('no functions (the plugin writes no section): green', (r.returncode, j['status']), (0, 'green'))
junk = os.path.join(tmp, 'junk.o')
open(junk, 'wb').write(b'not an object')
r, j = gate(junk)
check('unreadable object', (r.returncode, j['uncertified']), (2, [junk]))
r1, j1 = gate(o, junk)
r2, j2 = gate(junk, o)
check('deterministic', j1, j2)

# layout
check('GUARD equals the plugin LIMIT', ('LIMIT = 0x%x;' % gate_addr.GUARD) in open(TOOL).read(), True)
check('layout item is certified with a good file', [x for x in gate_addr.rows([o], LAY) if x[1] == 'layout'], [('', 'layout', '')])
r, j = gate(layout=None)
check('zero objects, no layout: universe 0, green', (r.returncode, j['status'], j['universe']), (0, 'green', 0))
r, j = gate(layout=LAY)
check('zero objects with layout: one item, green', (r.returncode, j['status'], j['universe']), (0, 'green', 1))
r, j = gate(o, layout=None)
check('objects without layout: red on layout', (r.returncode, j['uncertified']), (2, ['layout']))
check('missing layout file', gate_addr.layout_reason(os.path.join(tmp, 'absent.json'))[:14], 'layout: file: ')
bad = [
    ('shape', 'not json', None, '{'),
    ('shape', 'trailing text', None, json.dumps(good_doc()) + ' x'),
    ('shape', 'duplicate key', None, '{"schema":"fnvnx-layout/1","schema":"fnvnx-layout/1","regions":[]}'),
    ('shape', 'NaN', None, '{"schema":"fnvnx-layout/1","regions":[],"x":NaN}'),
    ('shape', 'not an object', [], None),
    ('shape', 'extra key', dict(good_doc(), extra=1), None),
    ('shape', 'missing key', {'schema': 'fnvnx-layout/1'}, None),
    ('shape', 'regions not a list', {'schema': 'fnvnx-layout/1', 'regions': {}}, None),
]
for what, nm, doc, raw in bad:
    check('layout %s: %s' % (what, nm), gate_addr.layout_reason(layout_file('b_' + nm.replace(' ', '_'), doc, raw)).startswith('layout: ' + what), True)


def mut(f):
    d = good_doc()
    f(d)
    return d


cases = [
    ('shape', 'float bound', mut(lambda d: d['regions'][1].update(lo=65536.0))),
    ('shape', 'bool bound', mut(lambda d: d['regions'][1].update(lo=True))),
    ('shape', 'empty name', mut(lambda d: d['regions'][1].update(name=''))),
    ('shape', 'bad prot', mut(lambda d: d['regions'][1].update(prot='rwx'))),
    ('shape', 'bad kind', mut(lambda d: d['regions'][1].update(kind='rom'))),
    ('shape', 'lo >= hi', mut(lambda d: d['regions'][1].update(lo=0x40000000))),
    ('shape', 'hi above 2^32', mut(lambda d: d['regions'][3].update(hi=(1 << 32) + 1))),
    ('shape', 'region extra key', mut(lambda d: d['regions'][1].update(x=1))),
    ('schema', 'schema string', mut(lambda d: d.update(schema='fnvnx-layout/2'))),
    ('partition', 'hole', mut(lambda d: d['regions'][1].update(lo=0x20000))),
    ('partition', 'overlap', mut(lambda d: d['regions'][1].update(lo=0x8000))),
    ('partition', 'not from 0', mut(lambda d: d['regions'].pop(0))),
    ('partition', 'not to 2^32', mut(lambda d: d['regions'].pop())),
    ('partition', 'empty', mut(lambda d: d['regions'].clear())),
    ('guard', 'low guard readable', mut(lambda d: d['regions'][0].update(prot='r--'))),
    ('guard', 'low guard kind', mut(lambda d: d['regions'][0].update(kind='host'))),
    ('guard', 'high guard writable', mut(lambda d: d['regions'][3].update(prot='rw-'))),
    ('guard', 'guard too small', {'schema': 'fnvnx-layout/1', 'regions': [
        reg('g0', 0, 0xFFFF, '---', 'guard'), reg('image', 0xFFFF, 0xFFFF0000, 'rw-'), reg('g1', 0xFFFF0000, 1 << 32, '---', 'guard')]}),
    ('guard', 'image over the top', {'schema': 'fnvnx-layout/1', 'regions': [
        reg('g0', 0, 0x10000, '---', 'guard'), reg('image', 0x10000, 1 << 32, 'rw-')]}),
]
for what, nm, doc in cases:
    check('layout %s: %s' % (what, nm), gate_addr.layout_reason(layout_file('c_' + nm.replace(' ', '_'), doc)).startswith('layout: ' + what + ':'), True)
check('layout unsorted regions still a partition', gate_addr.layout_reason(layout_file('uns', mut(lambda d: d['regions'].reverse()))), '')
check('layout a guard larger than 0x10000 is fine', gate_addr.layout_reason(layout_file('big', {'schema': 'fnvnx-layout/1', 'regions': [
    reg('g0', 0, 0x100000, '---', 'guard'), reg('image', 0x100000, 0xFFF00000, 'rw-'), reg('g1', 0xFFF00000, 1 << 32, '---', 'guard')]})), '')
r, j = gate(o, layout=layout_file('badgate', mut(lambda d: d['regions'][0].update(prot='rw-'))))
check('gate red on a bad layout', (r.returncode, j['uncertified']), (2, ['layout']))
# rows shape
rs = gate_addr.rows([o], LAY)
check('rows shape', rs, sorted(rs))
check('rows tuples', all(len(x) == 3 and all(isinstance(y, str) for y in x) for x in rs), True)
check('rows object item', ('empty', '', '') in rs, True)
try:
    gate_addr.rows([o, o], LAY)
    check('duplicate stem raises', False, True)
except ValueError:
    pass

# plugin: displacements of 2^16 or more and run-time field-array sums are routed (32-bit add, no 64-bit sum)
if True:
    d = put('disp', fixture('addr_disp'))
    rr = {s: (a, b) for s, a, b in addrsec.read(d) or []}
    check('big displacement routed', rr.get('_Z3bigP1S', (0, 1))[0] >= 1 and rr['_Z3bigP1S'][0] == rr['_Z3bigP1S'][1], True)
    check('field-array index routed', rr.get('_Z3fldP1Sj', (0, 1))[0] >= 1 and rr['_Z3fldP1Sj'][0] == rr['_Z3fldP1Sj'][1], True)
    check('small displacement not routed', rr.get('_Z5smallPi'), (0, 0))
    dis = subprocess.run(['aarch64-linux-gnu-objdump', '-d', '--no-show-raw-insn', d], capture_output=True, text=True).stdout
    for fn in ('_Z3bigP1S', '_Z3fldP1Sj'):
        body = dis.split('<%s>:' % fn)[1].split('\n\n')[0]
        check('%s: no 64-bit add or uxtw' % fn, ('add\tx' in body) or ('uxtw' in body), False)
    rg, jg = gate(d)
    check('gate green on the displacement object', (rg.returncode, jg['status']), (0, 'green'))
shutil.rmtree(tmp, ignore_errors=True)
if fails:
    print('FAIL\n' + '\n'.join(fails))
    sys.exit(1)
print('ok')
