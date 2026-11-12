#!/usr/bin/env python3
"""Check of link/gate_abs.py (H3a): a relocation to an absolute symbol is red, in a loose object and in an archive member; so are an absolute
definition and a reference to a name the linked image defines as absolute; clean inputs are green.  Needs the AArch64 cross toolchain
(as test_link_synth.py)."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.append(os.path.join(HERE, '..', '..', 'verify'))
import scratch
scratch.install()
import gate_abs
import relocs
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

X = 'aarch64-linux-gnu-'
fails = testcase.FAILED
def sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError('%s: %s' % (cmd[0], r.stderr[-800:]))


d = tempfile.mkdtemp()


def obj(name, src):
    s = os.path.join(d, name + '.s')
    with open(s, 'w') as fh:
        fh.write(src)
    o = os.path.join(d, name + '.o')
    sh([X + 'gcc', '-mabi=ilp32', '-c', s, '-o', o])
    return o


clean = obj('clean', '.text\n.globl f\nf: adrp x0, g\n add x0, x0, :lo12:g\n ret\n.data\n.globl g\ng: .word 1\n')
absref = obj('absref', '.set absv, 0x1000\n.text\n.globl h\nh: adrp x0, absv\n ldr w1, [x0, :lo12:absv]\n ret\n')
scriptref = obj('scriptref', '.text\n.globl k\nk: adrp x0, scr\n ldr w1, [x0, :lo12:scr]\n ret\n')
absdef = obj('absdef', '.globl scr\n.set scr, 0x1000\n')

uni, red = gate_abs.check([clean], [])
case('clean is green', got=red, want=[])
case('clean universe: 2 relocations + 3 defined symbols (f, g and the mapping symbol)', got=uni, want=5)
uni, red = gate_abs.check([absref], [])
case('absolute reference and definition', got=red, want=['absref.o:.text+00000000:*ABS*', 'absref.o:.text+00000004:*ABS*', 'absref.o:def:absv'])
# an archive member: the id names archive(member)
lib = os.path.join(d, 'lib.a')
sh([X + 'ar', 'Dcr', lib, clean, absref])
case('archive member', got=gate_abs.check([lib], [])[1], want=['lib.a(absref.o):.text+00000000:*ABS*', 'lib.a(absref.o):.text+00000004:*ABS*', 'lib.a(absref.o):def:absv'])
# a global absolute symbol an object defines (a `.set` constant) and the reference to it from another object
elf = os.path.join(d, 'x.elf')
sh([X + 'ld', '-m', 'aarch64linux32', '-pie', '--no-dynamic-linker', '-e', 'k', scriptref, absdef, '-o', elf])
names = relocs.abs_names(elf)
case('abs names of the linked image', got='scr' in names, want=True)
case('definition is red', got=gate_abs.check([absdef], [])[1], want=['absdef.o:def:scr'])
case('reference to a name the image defines as absolute', got=gate_abs.check([scriptref], names)[1], want=['scriptref.o:.text+00000000:scr', 'scriptref.o:.text+00000004:scr'])
case('same input, nothing absolute in the image', got=gate_abs.check([scriptref], [])[1], want=[])
# the gate file: format, name and the full id list
go = os.path.join(d, 'go')
os.makedirs(go)
rsp = os.path.join(d, 'in.rsp')
with open(rsp, 'w') as fh:
    fh.write('"%s"\n"%s"\n' % (clean, lib))
r = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', 'gate_abs.py'), '--inputs', rsp], env=dict(os.environ, GATE_OUT=go), capture_output=True, text=True)
case('exit status red', got=r.returncode, want=1)
with open(os.path.join(go, 'abs.json')) as fh:
    j = json.load(fh)
case('gate file', got=(j['name'], j['status'], j['format'], j['universe'] - j['certified'], len(j['uncertified'])), want=('abs', 'red', 'fnvnx-gate/1', 3, 3))
with open(os.path.join(go, 'abs.ids')) as fh:
    case('ids file', got=fh.read().split('\n')[:-1], want=sorted(j['uncertified']))
if fails:
    print('\n'.join(fails))
    sys.exit(1)
print('ok')
