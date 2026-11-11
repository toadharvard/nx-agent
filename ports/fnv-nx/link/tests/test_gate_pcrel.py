#!/usr/bin/env python3
"""Check of link/gate_pcrel.py over archives (H4): a PC-relative site against an absolute symbol inside an archive member is found
(id `archive(member):section+offset:symbol`), every member and every object of the response file is examined (the universe counts all
their PC-relative relocations), and a clean set is green.  Needs the AArch64 cross toolchain."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, '..', 'gate_pcrel.py')
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


main = obj('main', '.text\n.globl _start\n_start: bl member_a\n adrp x0, data\n ret\n.data\n.globl data\ndata: .word 1\n')
ma = obj('ma', '.text\n.globl member_a\nmember_a: bl member_b\n ret\n')
mb = obj('mb', '.text\n.globl member_b\nmember_b: adrp x0, fnv_abs\n add x0, x0, :lo12:fnv_abs\n ret\n')
mc = obj('mc', '.text\n.globl member_c\nmember_c: adrp x0, data\n ret\n')
absdef = obj('absdef', '.globl fnv_abs\n.set fnv_abs, 0x400000\n')
lib = os.path.join(d, 'lib.a')
sh([X + 'ar', 'Dcr', lib, ma, mb, mc])


def run(name, with_abs):
    elf = os.path.join(d, name + '.elf')
    sh([X + 'ld', '-m', 'aarch64linux32', '-no-pie', '-Ttext=0x100000', '-e', '_start', main, '--whole-archive', lib, '--no-whole-archive'] +
       ([absdef] if with_abs else []) + ['-o', elf])
    rsp = os.path.join(d, name + '.rsp')
    with open(rsp, 'w') as fh:
        fh.write('"%s"\n"%s"\n' % (main, lib))
    go = os.path.join(d, 'go_' + name)
    os.makedirs(go)
    r = subprocess.run([sys.executable, '-I', GATE, '--elf', elf, '--inputs', rsp], env=dict(os.environ, GATE_OUT=go), capture_output=True, text=True)
    with open(os.path.join(go, 'pcrel.json')) as fh:
        return r.returncode, json.load(fh), os.path.join(go, 'pcrel.ids')


rc, j, ids = run('red', True)
check('red: exit', rc, 1)
check('red: the site in the archive member is found', j['uncertified'], ['lib.a(mb.o):.text+00000000:fnv_abs'])
check('red: every member and object is examined (2 + 1 + 1 + 1 pc-relative relocations)', (j['universe'], j['certified']), (5, 4))
with open(ids) as fh:
    check('red: ids file', fh.read(), 'lib.a(mb.o):.text+00000000:fnv_abs\n')
# the same link without an absolute symbol: the reference binds to nothing absolute (the symbol is undefined in the image, so the link is not made)
if fails:
    print('\n'.join(fails))
    sys.exit(1)
print('ok')
