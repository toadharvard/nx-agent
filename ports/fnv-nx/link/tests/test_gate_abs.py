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

X = 'aarch64-linux-gnu-'
fails = []


def check(name, got, want):
    if got != want:
        fails.append('%s: got %r, want %r' % (name, got, want))


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
check('clean is green', red, [])
check('clean universe: 2 relocations + 3 defined symbols (f, g and the mapping symbol)', uni, 5)
uni, red = gate_abs.check([absref], [])
check('absolute reference and definition', red, ['absref.o:.text+00000000:*ABS*', 'absref.o:.text+00000004:*ABS*', 'absref.o:def:absv'])
# an archive member: the id names archive(member)
lib = os.path.join(d, 'lib.a')
sh([X + 'ar', 'Dcr', lib, clean, absref])
check('archive member', gate_abs.check([lib], [])[1], ['lib.a(absref.o):.text+00000000:*ABS*', 'lib.a(absref.o):.text+00000004:*ABS*', 'lib.a(absref.o):def:absv'])
# a global absolute symbol an object defines (a `.set` constant) and the reference to it from another object
elf = os.path.join(d, 'x.elf')
sh([X + 'ld', '-m', 'aarch64linux32', '-pie', '--no-dynamic-linker', '-e', 'k', scriptref, absdef, '-o', elf])
names = relocs.abs_names(elf)
check('abs names of the linked image', 'scr' in names, True)
check('definition is red', gate_abs.check([absdef], [])[1], ['absdef.o:def:scr'])
check('reference to a name the image defines as absolute', gate_abs.check([scriptref], names)[1], ['scriptref.o:.text+00000000:scr', 'scriptref.o:.text+00000004:scr'])
check('same input, nothing absolute in the image', gate_abs.check([scriptref], [])[1], [])
# the gate file: format, name and the full id list
go = os.path.join(d, 'go')
os.makedirs(go)
rsp = os.path.join(d, 'in.rsp')
with open(rsp, 'w') as fh:
    fh.write('"%s"\n"%s"\n' % (clean, lib))
r = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', 'gate_abs.py'), '--inputs', rsp], env=dict(os.environ, GATE_OUT=go), capture_output=True, text=True)
check('exit status red', r.returncode, 1)
with open(os.path.join(go, 'abs.json')) as fh:
    j = json.load(fh)
check('gate file', (j['name'], j['status'], j['format'], j['universe'] - j['certified'], len(j['uncertified'])), ('abs', 'red', 'fnvnx-gate/1', 3, 3))
with open(os.path.join(go, 'abs.ids')) as fh:
    check('ids file', fh.read().split('\n')[:-1], sorted(j['uncertified']))
if fails:
    print('\n'.join(fails))
    sys.exit(1)
print('ok')
