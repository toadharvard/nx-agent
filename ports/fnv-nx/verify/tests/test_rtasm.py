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
fails = []


def judge(name, body):
    s, o = os.path.join(tmp, name + '.S'), os.path.join(tmp, name + '.o')
    open(s, 'w').write('\t.text\n\t.global f\n\t.type f,%function\nf:\n' + ''.join('\t' + l + '\n' for l in body) + '\t.size f,.-f\n')
    r = subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', '-march=armv8.1-a', s, '-o', o], capture_output=True, text=True)
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
# the universe is the objects without plugin record sections; the recipe's suffix fact must agree
o = judge('u1', ['ret'])[3]
if gate_rtasm.judge_objects([('u1', o)], {'u1': True})[:2] != (0, 0) or gate_rtasm.judge_objects([('u1', o)], {'u1': False})[2] != ['u1:class [recipe suffix says C/C++, the object has no plugin record sections]']:
    fails.append('suffix cross-check')
s2 = os.path.join(tmp, 'rec.s')
open(s2, 'w').write('\t.text\n\t.global f\nf:\n\tldr x0, [x1]\n\tret\n\t.section .fnvnx.frame,"",%progbits\n\t.ascii "f\\t\\n"\n')
subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', s2, '-o', os.path.join(tmp, 'rec.o')], check=True)
if gate_rtasm.judge_objects([('rec', os.path.join(tmp, 'rec.o'))])[:2] != (0, 0):
    fails.append('an object with a record section is not an assembly object')
if fails:
    print('FAIL\n' + '\n'.join(fails))
    sys.exit(1)
print('ok')
