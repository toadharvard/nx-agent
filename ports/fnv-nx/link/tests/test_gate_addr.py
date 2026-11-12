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
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'contract'))
import a64obj
import addrsec
import gate_addr
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

TOOL = os.path.join(HERE, '..', '..', 'toolchain', 'addr', 'fnvnx_addr.cc')
CXX = 'aarch64-linux-gnu-g++-13'
FLAGS = ['-mabi=ilp32', '-std=gnu++17', '-O2', '-fwrapv', '-fno-strict-aliasing', '-march=armv8-a+crc+crypto', '-mtune=cortex-a57',
         '-fno-stack-protector', '-fno-stack-clash-protection', '-mno-outline-atomics', '-fno-exceptions', '-fno-rtti']
fails = testcase.FAILED
tmp = tempfile.mkdtemp(prefix='gate-addr-')




def run(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, **kw)


def build_plugin():
    out = run([CXX, '-print-file-name=plugin'])
    pdir = out.stdout.strip()
    so = os.path.join(tmp, 'fnvnx_addr.so')
    host = 'x86_64-linux-gnu-g++-13'
    crt = [run([host, '-print-file-name=' + n]).stdout.strip() for n in ('crti.o', 'crtbeginS.o')]
    end = [run([host, '-print-file-name=' + n]).stdout.strip() for n in ('crtendS.o', 'crtn.o')]
    r = run([host, '-shared', '-nostdlib', '-fPIC', '-fno-rtti', '-fno-exceptions', '-O2', '-I' + pdir + '/include', '-o', so] + crt + [TOOL] + end)
    if r.returncode:
        fails.append('plugin build: ' + r.stderr[-800:])
        return None
    return so


def compile_cxx(src, name, plugin):
    p = os.path.join(tmp, name + '.cpp')
    o = os.path.join(tmp, name + '.o')
    open(p, 'w').write(src)
    r = run([CXX] + FLAGS + (['-fplugin=' + plugin] if plugin else []) + ['-c', p, '-o', o])
    if r.returncode:
        fails.append('compile %s: %s' % (name, r.stderr[-800:]))
    return o


def asm(name, text):
    s = os.path.join(tmp, name + '.s')
    o = os.path.join(tmp, name + '.o')
    open(s, 'w').write(text)
    r = run(['aarch64-linux-gnu-as', '-mabi=ilp32', s, '-o', o])
    if r.returncode:
        fails.append('assemble %s: %s' % (name, r.stderr[-400:]))
    return o


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


def asm_obj(name, funcs, section):
    t = '\t.text\n' + ''.join('\t.global %s\n\t.type %s,%%function\n%s:\n\tret\n\t.size %s,.-%s\n' % (f, f, f, f, f) for f in funcs)
    if section is not None:
        t += '\t.section .fnvnx.addr,"",%progbits\n\t.ascii "' + section + '"\n'
    return asm(name, t)


SRC = '''
extern "C" void sink(void*);
int r1(unsigned x) { return *(int*)(x + 0x40u); }
int r3(int* p, int i) { return p[i]; }
int r6(char* b, unsigned o) { return *(int*)(b + o); }
int r9() { return *(int*)0x1234; }
struct A { virtual void f(); int a; }; struct B { virtual void g(); int b; };
struct C : A, B { void g() override; int c; };
void C::g() { sink(this); }
struct E { E(); ~E(); int x; }; E::E() { x = 1; } E::~E() { sink(this); }
'''
plug = build_plugin()
if plug:
    good = compile_cxx(SRC, 'good', plug)
    plain = compile_cxx(SRC, 'plain', None)
    rows = addrsec.read(good)
    case('section present', got=rows is not None, want=True)
    byname = {s: (a, b) for s, a, b in rows or []}
    case('r1 routed', got=byname.get('_Z2r1j', (0, 0))[0] >= 1 and byname['_Z2r1j'][0] == byname['_Z2r1j'][1], want=True)
    case('r6 routed', got=byname.get('_Z2r6Pcj', (0, 0))[0] >= 1 and byname['_Z2r6Pcj'][0] == byname['_Z2r6Pcj'][1], want=True)
    case('constant not counted', got=byname.get('_Z2r9v'), want=(0, 0))
    case('every record seen == routed', got=all(a == b for _, a, b in rows or []), want=True)
    case('sorted records', got=[s for s, _, _ in rows or []], want=sorted(s for s, _, _ in rows or []))
    names = sorted(n for _, _, _, ns in a64obj.read(good).functions for n in ns)
    case('record for every function symbol', got=sorted(set(names) - set(byname)), want=[])
    case('thunk or alias record', got=any(n.startswith('_ZThn') for n in names) and byname.get(
        [n for n in names if n.startswith('_ZThn')][0]), want=(0, 0))
    code = run(['aarch64-linux-gnu-objdump', '-d', '--no-show-raw-insn', good]).stdout
    r1 = code.split('<_Z2r1j>:')[1].split('\n\n')[0]
    case('r1 adds in 32 bits, no displacement addressing', got=('add\tw' in r1) and ('#0x40]' not in r1), want=True)
    r, j = gate(good)
    case('green on plugin object', got=(r.returncode, j['status'], j['certified'] == j['universe']), want=(0, 'green', True))
    r, j = gate(plain)
    case('no section: red', got=(r.returncode, j['status']), want=(2, 'red'))
    case('no section: every function named', got=sorted(j['uncertified']), want=sorted([plain] + [plain + ':' + n for n in names]))
    case('no section: object item named', got=plain in j['uncertified'], want=True)

o = asm_obj('norec', ['fa', 'fb'], 'fa\\t0\\t0\\n')
r, j = gate(o)
case('function without a record', got=(r.returncode, j['uncertified']), want=(2, [o + ':fb']))
o = asm_obj('two', ['fa'], 'fa\\t0\\t0\\nfa\\t0\\t0\\n')
r, j = gate(o)
case('two records', got=(r.returncode, j['uncertified']), want=(2, [o + ':fa']))
o = asm_obj('skew', ['fa', 'fb'], 'fa\\t2\\t1\\nfb\\t1\\t1\\n')
r, j = gate(o)
case('seen != routed', got=(r.returncode, j['uncertified']), want=(2, [o + ':fa']))
for k, bad in enumerate(['fa\\t0\\n', 'fa\\t0\\t0', 'fa\\t-1\\t0\\n', 'fa\\t0\\t0\\t0\\n', '\\t0\\t0\\n', 'fa\\t0x1\\t0\\n']):
    o = asm_obj('mal%d' % k, ['fa', 'fb'], bad)
    r, j = gate(o)
    case('malformed %r: all functions red', got=(r.returncode, sorted(j['uncertified'])), want=(2, sorted([o, o + ':fa', o + ':fb'])), key=bad)
o = asm_obj('ok', ['fa', 'fb'], 'fb\\t3\\t3\\nfa\\t0\\t0\\nzz\\t1\\t0\\n')
r, j = gate(o)
case('record of a non-function is ignored; green', got=(r.returncode, j['status'], j['universe']), want=(0, 'green', 4))
o = asm_obj('empty', [], '')
r, j = gate(o)
case('no functions, empty section: green', got=(r.returncode, j['status']), want=(0, 'green'))
junk = os.path.join(tmp, 'junk.o')
open(junk, 'wb').write(b'not an object')
r, j = gate(junk)
case('unreadable object', got=(r.returncode, j['uncertified']), want=(2, [junk]))
r1, j1 = gate(o, junk)
r2, j2 = gate(junk, o)
case('deterministic', got=j1, want=j2)

# layout
case('GUARD equals the plugin LIMIT', got=('LIMIT = 0x%x;' % gate_addr.GUARD) in open(TOOL).read(), want=True)
case('layout item is certified with a good file', got=[x for x in gate_addr.rows([o], LAY) if x[1] == 'layout'], want=[('', 'layout', '')])
r, j = gate(layout=None)
case('zero objects, no layout: universe 0, green', got=(r.returncode, j['status'], j['universe']), want=(0, 'green', 0))
r, j = gate(layout=LAY)
case('zero objects with layout: one item, green', got=(r.returncode, j['status'], j['universe']), want=(0, 'green', 1))
r, j = gate(o, layout=None)
case('objects without layout: red on layout', got=(r.returncode, j['uncertified']), want=(2, ['layout']))
case('missing layout file', got=gate_addr.layout_reason(os.path.join(tmp, 'absent.json'))[:14], want='layout: file: ')
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
    case('layout %s: %s', got=gate_addr.layout_reason(layout_file('b_' + nm.replace(' ', '_'), doc, raw)).startswith('layout: ' + what), want=True, key=(what, nm))


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
    case('layout in a hand-written table %s: %s', got=gate_addr.layout_reason(layout_file('c_' + nm.replace(' ', '_'), doc)).startswith('layout: ' + what + ':'), want=True, key=(what, nm))
case('layout unsorted regions still a partition', got=gate_addr.layout_reason(layout_file('uns', mut(lambda d: d['regions'].reverse()))), want='')
case('layout a guard larger than 0x10000 is fine', got=gate_addr.layout_reason(layout_file('big', {'schema': 'fnvnx-layout/1', 'regions': [
    reg('g0', 0, 0x100000, '---', 'guard'), reg('image', 0x100000, 0xFFF00000, 'rw-'), reg('g1', 0xFFF00000, 1 << 32, '---', 'guard')]})), want='')
r, j = gate(o, layout=layout_file('badgate', mut(lambda d: d['regions'][0].update(prot='rw-'))))
case('gate red on a bad layout', got=(r.returncode, j['uncertified']), want=(2, ['layout']))
# rows shape
rs = gate_addr.rows([o], LAY)
case('rows shape', got=rs, want=sorted(rs))
case('rows tuples', got=all(len(x) == 3 and all(isinstance(y, str) for y in x) for x in rs), want=True)
case('rows object item', got=('empty', '', '') in rs, want=True)
try:
    gate_addr.rows([o, o], LAY)
    case('duplicate stem raises', got=False, want=True)
except ValueError:
    pass

# plugin: displacements of 2^16 or more and run-time field-array sums are routed (32-bit add, no 64-bit sum)
if plug:
    dsrc = '''
struct S { int pad[40000]; int f[8]; };
int big(S* s) { return s->pad[39000]; }
int fld(S* s, unsigned i) { return s->f[i]; }
int small(int* p) { return p[100]; }
'''
    d = compile_cxx(dsrc, 'disp', plug)
    rr = {s: (a, b) for s, a, b in addrsec.read(d) or []}
    case('big displacement routed', got=rr.get('_Z3bigP1S', (0, 1))[0] >= 1 and rr['_Z3bigP1S'][0] == rr['_Z3bigP1S'][1], want=True)
    case('field-array index routed', got=rr.get('_Z3fldP1Sj', (0, 1))[0] >= 1 and rr['_Z3fldP1Sj'][0] == rr['_Z3fldP1Sj'][1], want=True)
    case('small displacement not routed', got=rr.get('_Z5smallPi'), want=(0, 0))
    dis = run(['aarch64-linux-gnu-objdump', '-d', '--no-show-raw-insn', d]).stdout
    for fn in ('_Z3bigP1S', '_Z3fldP1Sj'):
        body = dis.split('<%s>:' % fn)[1].split('\n\n')[0]
        case('%s: no 64-bit add or uxtw', got=('add\tx' in body) or ('uxtw' in body), want=False, key=fn)
    rg, jg = gate(d)
    case('gate green on the displacement object', got=(rg.returncode, jg['status']), want=(0, 'green'))
shutil.rmtree(tmp, ignore_errors=True)
if fails:
    print('FAIL\n' + '\n'.join(fails))
    sys.exit(1)
print('ok')
