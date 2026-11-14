#!/usr/bin/env python3
"""Check of the register-lattice value type of link/a64order.py (THE TAG): every shape (None, 'RO', ('D', c), ('FP', c), ('X', origins), ('X', None))
goes through origins/mkx/frame_offsets without an exception, and the machine-code cases that produce each shape scan to rows: a derived frame
address with known origins is judged for EVERY origin (F only if every one passes), an unknown origin is never F; a crash of the scan is a red
row naming the object (gate_order.scan_object). Hand-assembled ILP32 objects, no network. Run: python3 -I <this file>."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'contract'))
import a64order as A
import gate_order

fails = []
tmp = tempfile.mkdtemp(prefix='ordertags-')


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r want %r' % (what, got, want))


class Mem:
    def __init__(self, base, disp):
        self.base, self.index, self.disp = base, 0, disp


class Ins:
    def __init__(self, base, disp):
        self.mem = Mem(base, disp)


X1 = next(k for k, v in A.REG.items() if v == 1)
SP = next(k for k, v in A.REG.items() if v == A.SP)
# shapes of the tag through the one definition
check('origins None', A.origins(None), ())
check('origins RO', A.origins('RO'), ())
check('origins D', A.origins(('D', -16)), (-16,))
check('origins FP', A.origins(('FP', -8)), (-8,))
check('origins X', A.origins(('X', (-24, -8))), (-24, -8))
check('origins unknown X', A.origins(A.UNKNOWN_X), None)
check('mkx D+D', A.mkx(('D', -8), ('D', -24)), ('X', (-24, -8)))
check('mkx X+RO', A.mkx(('X', (-8,)), 'RO', None), ('X', (-8,)))
check('mkx unknown', A.mkx(('D', -8), A.UNKNOWN_X), A.UNKNOWN_X)
fo = lambda tag, disp=4, sp=-32: A.Func.frame_offsets(None, Ins(X1, disp), sp, {1: tag} if tag is not None else {})
check('frame_offsets D', fo(('D', -16)), (-12,))
check('frame_offsets FP', fo(('FP', -8)), (-4,))
check('frame_offsets X: every origin', fo(('X', (-24, -8))), (-20, -4))
check('frame_offsets unknown X is not a frame access', fo(A.UNKNOWN_X), None)
check('frame_offsets RO', fo('RO'), None)
check('frame_offsets none', fo(None), None)
check('frame_offsets sp base', A.Func.frame_offsets(None, Ins(SP, 8), -32, {}), (-24,))
check('frame_offsets sp unknown', A.Func.frame_offsets(None, Ins(SP, 8), None, {}), None)


def scan(name, body, frame='f\t\n'):
    t = '\t.text\n\t.global f\n\t.type f,%function\nf:\n' + ''.join('\t' + l + '\n' for l in body) + '\t.size f,.-f\n'
    t += '\t.section .fnvnx.frame,"",%progbits\n\t.ascii "' + frame.replace('\n', '\\n').replace('\t', '\\t') + '"\n'
    s, o = os.path.join(tmp, name + '.s'), os.path.join(tmp, name + '.o')
    open(s, 'w').write(t)
    r = subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', '-march=armv8.1-a', s, '-o', o], capture_output=True, text=True)
    if r.returncode:
        fails.append('assemble %s: %s' % (name, r.stderr[-300:]))
        return {}
    return dict(A.scan(o).rows), o


# machine code that makes each shape, then a plain access through it
rows, o = scan('xset', ['sub sp, sp, #32', 'add x1, sp, #8', 'add x2, sp, #16', 'add x3, x1, x2', 'ldr w0, [x3, #4]', 'add sp, sp, #32', 'ret'])
check('derived frame address with known origins: scanned, the access judged', any(k.startswith('f+0x') for k in rows), True)
rows, o = scan('xunk', ['sub sp, sp, #32', 'sub sp, sp, x5', 'mov x3, sp', 'ldr w0, [x3, #4]', 'ret'])
acc = {k: v for k, v in rows.items() if k.startswith('f+0x')}
check('unknown origin: the access is not certified as frame', bool(acc) and all(v for v in acc.values()), True)
rows, o = scan('fp', ['stp x29, x30, [sp, #-32]!', 'mov x29, sp', 'ldr w0, [x29, #16]', 'ldp x29, x30, [sp], #32', 'ret'])
check('frame pointer shape scanned', 'f' in rows and not rows[''], True)
# rule KC: a frame size held in a register (mov xN,#imm; sub sp,sp,xN) keeps the stack pointer known, so a frame access after it is F
rows, o = scan('kc', ['stp x29, x30, [sp, #-16]!', 'mov x29, sp', 'mov x12, #0x4060', 'sub sp, sp, x12', 'str w0, [sp, #8]', 'ldr w1, [sp, #8]',
                      'add sp, sp, x12', 'ldp x29, x30, [sp], #16', 'ret'])
check('KC: constant frame size, every access certified', {k: v for k, v in rows.items() if v}, {})
rows, o = scan('kcunk', ['stp x29, x30, [sp, #-16]!', 'mov x29, sp', 'sub sp, sp, x12', 'str w0, [sp, #8]', 'ldp x29, x30, [sp], #16', 'ret'])
check('KC: a frame size in an unknown register is not certified', bool(rows.get('f+0xc', '')), True)
# rule BR: a tail-call `br` with sp == CFA is an exit (the access after the epilogue is certified); a br inside a frame is a jump to every instruction
rows, o = scan('brtail', ['ldar x16, [x0]', 'br x16'])
check('BR: tail call, no red row', {k: v for k, v in rows.items() if v}, {})
rows, o = scan('brin', ['sub sp, sp, #16', 'ldar x16, [x0]', 'br x16', 'str w0, [sp, #4]', 'add sp, sp, #16', 'ret'])
check('BR: br inside a frame scanned without an exception', 'f' in rows, True)
# rule LP: code reached only by an unwind edge (after a call, no other edge) keeps the call's stack pointer
rows, o = scan('lp', ['stp x29, x30, [sp, #-32]!', 'mov x29, sp', 'bl g', 'ldp x29, x30, [sp], #32', 'ret', 'str w0, [sp, #8]', 'ldp x29, x30, [sp], #32', 'ret'])
check('LP: landing pad keeps sp, its frame access certified', {k: v for k, v in rows.items() if v}, {})
# a crash of the scan is a red row naming the object, never an exception
orig = A.scan
A.scan = lambda p: (_ for _ in ()).throw(TypeError('can only concatenate tuple (not "int") to tuple'))
try:
    rs, st = gate_order.scan_object('some.o')
finally:
    A.scan = orig
check('crash is a red row naming the failure', rs and rs[0][0] == '' and 'scan failed (TypeError' in rs[0][1], True)
if fails:
    print('FAIL\n' + '\n'.join(fails))
    sys.exit(1)
print('ok')
