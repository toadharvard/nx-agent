#!/usr/bin/env python3
"""Check of link/gate_addr.py and toolchain/addr/fnvnx_addr.cc.  Compiles small C++ fixtures with the distro cross compiler and the
plugin built from the tracked source (the argv of verify/recipe.py's plugin stage), and hand-assembles negative objects.  Cases:
the plugin writes `.fnvnx.addr` with seen == routed and a barrier asm at every address sum (the code has a 32-bit add and no
`uxtw` addressing mode); thunks and constructor aliases get records; the gate is green on those objects; red and named: an
object without the section, a function without a record, two records, seen != routed, a malformed line (every function of
the object), an unreadable object; the gate file and exit status.  No network, no game data.  Run: python3 -I <this file>."""
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

TOOL = os.path.join(HERE, '..', '..', 'toolchain', 'addr', 'fnvnx_addr.cc')
CXX = 'aarch64-linux-gnu-g++-13'
FLAGS = ['-mabi=ilp32', '-std=gnu++17', '-O2', '-fwrapv', '-fno-strict-aliasing', '-march=armv8-a+crc+crypto', '-mtune=cortex-a57',
         '-fno-stack-protector', '-fno-stack-clash-protection', '-mno-outline-atomics', '-fno-exceptions', '-fno-rtti']
fails = []
tmp = tempfile.mkdtemp(prefix='gate-addr-')


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r, want %r' % (what, got, want))


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


def gate(*objs):
    d = tempfile.mkdtemp(dir=tmp)
    r = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', 'gate_addr.py')] + list(objs), capture_output=True, text=True,
                       env={'GATE_OUT': d, 'PATH': os.environ.get('PATH', '')})
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
    code = run(['aarch64-linux-gnu-objdump', '-d', '--no-show-raw-insn', good]).stdout
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
o = asm_obj('two', ['fa'], 'fa\\t0\\t0\\nfa\\t0\\t0\\n')
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
check('record of a non-function is ignored; green', (r.returncode, j['status'], j['universe']), (0, 'green', 3))
o = asm_obj('empty', [], '')
r, j = gate(o)
check('no functions, empty section: green', (r.returncode, j['status']), (0, 'green'))
junk = os.path.join(tmp, 'junk.o')
open(junk, 'wb').write(b'not an object')
r, j = gate(junk)
check('unreadable object', (r.returncode, j['uncertified']), (2, [junk]))
r1, j1 = gate(o, junk)
r2, j2 = gate(junk, o)
check('deterministic', j1, j2)
shutil.rmtree(tmp, ignore_errors=True)
if fails:
    print('FAIL\n' + '\n'.join(fails))
    sys.exit(1)
print('ok')
