#!/usr/bin/env python3
"""Gate rtasm (verify/gate_rtasm.py): hand-assembled .S objects, one per class and red ones: (a) frame D1, (d) atomic ordered forms, (c) kernel call, and the
accesses no class covers. These are inputs of the real gate. Run: python3 -I <this file>."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, '..'), os.path.join(HERE, '..', '..', 'link'), os.path.join(HERE, '..', '..', 'contract')]
import gate_rtasm  # noqa: E402

tmp = tempfile.mkdtemp(prefix='rtasm-')
__import__('atexit').register(__import__('shutil').rmtree, tmp, True)   # a test leaves no scratch behind
fails = []


def judge(name, body):
    s, o = os.path.join(tmp, name + '.S'), os.path.join(tmp, name + '.o')
    open(s, 'w').write('\t.text\n\t.global f\n\t.type f,%function\nf:\n' + ''.join('\t' + l + '\n' for l in body) + '\t.size f,.-f\n')
    r = subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', '-march=armv8.1-a', '-g', s, '-o', o], capture_output=True, text=True)
    if r.returncode:
        fails.append('assemble %s: %s' % (name, r.stderr[-200:]))
        return None
    n, c, bad, _ = gate_rtasm.judge_objects([(name, o)])
    return n, c, bad, o


def want(what, got, green, n=None):
    if got is None:
        return
    got = got[:3]
    ok = (not got[2]) if green else bool(got[2])
    if n is not None and got[0] != n:
        ok = False
    if not ok:
        fails.append('%s: green=%s got %r' % (what, green, got))


want('(a) frame: spill and reload of the own frame, no escape', judge('a1', ['stp x29, x30, [sp, #-32]!', 'mov x29, sp', 'str x0, [sp, #16]', 'ldr x1, [sp, #16]', 'ldp x29, x30, [sp], #32', 'ret']), True, 4)
want('(a) frame: an access after a frame address is passed to a call is red', judge('a2', ['stp x29, x30, [sp, #-32]!', 'mov x29, sp', 'add x0, sp, #16', 'bl g', 'ldr x1, [sp, #16]', 'ldp x29, x30, [sp], #32', 'ret']), False)
want('(a) frame: the access before the escaping call is certified, the one after is not', judge('a3', ['stp x29, x30, [sp, #-32]!', 'mov x29, sp', 'str x0, [sp, #16]', 'add x0, sp, #16', 'bl g', 'ldr x1, [sp, #16]', 'ldp x29, x30, [sp], #32', 'ret']), False)
want('(d) atomic: ldaxr/stlxr and ldar/stlr', judge('d1', ['1: ldaxr w1, [x0]', 'add w1, w1, #1', 'stlxr w2, w1, [x0]', 'cbnz w2, 1b', 'ldar w3, [x0]', 'stlr w3, [x0]', 'ret']), True, 4)
want('(d) atomic: LSE acquire-release', judge('d2', ['casal w1, w2, [x0]', 'ldaddal w1, w2, [x0]', 'ret']), True, 2)
want('(d) atomic: a relaxed exclusive pair is not an ordered form', judge('d3', ['1: ldxr w1, [x0]', 'stxr w2, w1, [x0]', 'cbnz w2, 1b', 'ret']), False)
want('(c) kernel call: svc and moves only', judge('c1', ['mov x8, x0', 'mov x0, x1', 'svc #0x0b', 'ret']), True, 0)
want('a plain load through an argument register is red', judge('r1', ['ldr x0, [x1]', 'ret']), False, 1)
want('a plain store through an argument register is red', judge('r2', ['str x0, [x1, #8]', 'ret']), False, 1)
want('a pair copy loop (a memcpy body) is red', judge('r3', ['ldp x2, x3, [x1], #16', 'stp x2, x3, [x0], #16', 'ret']), False, 2)
# the universe: assembled objects by their DWARF origin; the recipe's suffix fact wins for recipe units
o = judge('u1', ['ret'])[3]
if gate_rtasm.origin(o) != 'asm':
    fails.append('origin of a -g assembled object is asm: %r' % gate_rtasm.origin(o))
s2 = os.path.join(tmp, 'ng.s')
open(s2, 'w').write('\t.text\n\t.global f\nf:\n\tldr x0, [x1]\n\tret\n')
subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', s2, '-o', os.path.join(tmp, 'ng.o')], check=True)
r = gate_rtasm.judge_objects([('ng', os.path.join(tmp, 'ng.o'))])
if r[0] != 1 or r[1] != 0 or 'origin unknown' not in r[2][0]:
    fails.append('no DWARF: red origin unknown: %r' % (r,))
if gate_rtasm.judge_objects([('ng', os.path.join(tmp, 'ng.o'))], {'ng': True})[:2] != (1, 0):
    fails.append('a recipe .S unit without -g is judged by the recipe suffix (its access is red)')
c = os.path.join(tmp, 'c.c')
open(c, 'w').write('int g(int *p){return *p;}\n')
subprocess.run(['aarch64-linux-gnu-gcc', '-mabi=ilp32', '-g', '-c', c, '-o', os.path.join(tmp, 'c.o')], check=True)
if gate_rtasm.judge_objects([('c', os.path.join(tmp, 'c.o'))])[:2] != (0, 0):
    fails.append('a compiled C object is not an assembly object')
if fails:
    print('FAIL\n' + '\n'.join(fails))
    sys.exit(1)
print('ok')
