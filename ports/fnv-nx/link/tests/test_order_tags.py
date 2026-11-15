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


def scan(name, body, frame='f\t\n', redir=None):
    t = '\t.text\n\t.global f\n\t.type f,%function\nf:\n' + ''.join('\t' + l + '\n' for l in body) + '\t.size f,.-f\n'
    t += '\t.section .fnvnx.frame,"",%progbits\n\t.ascii "' + frame.replace('\n', '\\n').replace('\t', '\\t') + '"\n'
    if redir is not None:
        t += '\t.section .fnvnx.redir,"",%progbits\n\t.ascii "' + redir.replace('\n', '\\n').replace('\t', '\\t') + '"\n'
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
# rule D (dominance): a store to an address-taken slot before any instruction forms its address is certified; a back edge from the forming instruction is a path
PRO = ['stp x29, x30, [sp, #-32]!', 'mov x29, sp']
EPI = ['ldp x29, x30, [sp], #32', 'ret']
R = 'f\t-16:8\n'
rows, o = scan('dom1', PRO + ['add x1, sp, #16', 'ldar x2, [x1]', 'str w0, [sp, #16]', 'add x0, sp, #16', 'bl g'] + EPI, R)
check('D1: forming the address and loading through it is no escape: the store is certified', rows.get('f+0xc'), '')
rows, o = scan('dom1b', PRO + ['str w0, [sp, #16]', 'add x0, sp, #16', 'bl g', 'ldr w2, [sp, #16]'] + EPI, R)
check('D1: an access before the escaping call is certified', rows.get('f+0x8'), '')
check('D1: the same access after the call is not', bool(rows.get('f+0x14')), True)
rows, o = scan('dom2', PRO + ['1: str w0, [sp, #16]', 'add x0, sp, #16', 'bl g', 'cbnz x3, 1b'] + EPI, R)
check('D1: a loop back edge from the escape makes the store shared', bool(rows.get('f+0x8')), True)
rows, o = scan('dom3', PRO + ['add x1, sp, #16', 'str x1, [x3]', 'str w0, [sp, #16]'] + EPI, R)
check('D1: a store of the address, then an access: shared', bool(rows.get('f+0x10')), True)
rows, o = scan('dom4', PRO + ['add x1, sp, #16', 'cbz x4, 1f', 'str x1, [x3]', '1: str w0, [sp, #16]'] + EPI, R)
check('D1: the address stored on one branch makes the join point uncertified', bool(rows.get('f+0x14')), True)
rows, o = scan('dom5', PRO + ['add x1, sp, #16', 'cbz x4, 1f', 'nop', '1: str w0, [sp, #16]'] + EPI, R)
check('D1: no branch stores it: the join point is certified', rows.get('f+0x14'), '')
rows, o = scan('dom6', PRO + ['add x1, sp, #16', 'add x2, x1, #4', 'str x2, [x3]', 'ldr w0, [sp, #16]'] + EPI, R)
check('D1: a derived address stored escapes the variable', bool(rows.get('f+0x10')), True)
# rule A: an access in the incoming stack-argument area is a frame access iff the row has args:N covering it and it overlaps no recorded range
rows, o = scan('arg1', ['ldr w0, [sp, #8]', 'ret'], 'f\t\targs:16\n')
check('A: in the argument area: certified', {k: v for k, v in rows.items() if v}, {})
rows, o = scan('arg2', ['ldr w0, [sp, #16]', 'ret'], 'f\t\targs:16\n')
check('A: past the argument area: red', bool(rows.get('f+0x0')), True)
rows, o = scan('arg3', ['ldr w0, [sp, #8]', 'ret'], 'f\t\n')
check('A: a row without args: red', bool(rows.get('f+0x0')), True)
rows, o = scan('arg4', ['ldr w0, [sp, #8]', 'ret'], 'f\t8:4\targs:16\n')
check('A: onto a recorded address-taken parameter: not F (its address is not formed: rule D certifies it, never F)', rows.get('f+0x0'), '')
rows, o = scan('unk', ['stp x29, x30, [sp, #-32]!', 'mov x29, sp', 'str w0, [sp, #16]', 'ldp x29, x30, [sp], #32', 'ret'], 'f\tunk:mem\targs:0\n')
check('unk token: the row is dynamic, a frame store is red', bool(rows.get('f+0x8')), True)
# `OFF:SIZE:n` (escapes=0): plain accesses to it are certified by its range, via sp or via a register holding the formed address; an escaping pair is not
rows, o = scan('pn1', ['stp x29, x30, [sp, #-32]!', 'mov x29, sp', 'add x1, sp, #16', 'str w0, [x1]', 'ldr w2, [sp, #16]', 'ldp x29, x30, [sp], #32', 'ret'], 'f\t-16:8:n\n')
check('n: private variable, accesses through x1 and sp are certified', {k: v for k, v in rows.items() if v}, {})
rows, o = scan('pn2', ['stp x29, x30, [sp, #-32]!', 'mov x29, sp', 'add x1, sp, #16', 'str x1, [x3]', 'ldr w2, [sp, #16]', 'ldp x29, x30, [sp], #32', 'ret'], 'f\t-16:8\n')
check('n: the same variable without :n, its access after the escape is red', bool(rows.get('f+0xc')), True)
rows, o = scan('pn3', ['stp x29, x30, [sp, #-32]!', 'mov x29, sp', 'add x1, sp, #18', 'str x1, [x3]', 'ldr w2, [sp, #18]', 'ldp x29, x30, [sp], #32', 'ret'], 'f\t-16:2:n -14:4\n')
check('n: an access overlapping an escaping pair is not certified by a :n pair', bool(rows.get('f+0x10')), True)
import framesec as FS
check('parse :n', [(tuple(q), q.priv) for q in FS.parse_frame(b'f\t-16:8:n -8:4\n', 'x')[0][1]], [((-16, 8), True), ((-8, 4), False)])
for bad in (b'f\t-16:8:m\n', b'f\t-16:8:n:n\n', b'f\t-16:8: \n'):
    try:
        FS.parse_frame(bad, 'x')
        fails.append('parse accepted %r' % bad)
    except ValueError:
        pass
# rule D2: a redirected string-op call between `dmb ish` fences
SO = 'memcpy\tstring-op\n'
F2 = ['dmb ish', 'bl memcpy', 'dmb ish']
rows, o = scan('d2a', PRO + F2 + EPI, redir=SO)
check('D2: dmb ish; bl memcpy; dmb ish with a string-op record: certified', rows.get('f+0xc/call'), '')
rows, o = scan('d2b', PRO + ['mov x2, #8', 'dmb ish', 'mov x1, x3', 'bl memcpy', 'mov x5, x0', 'dmb ish'] + EPI, redir=SO)
check('D2: argument set-up and moves between a fence and the call are no memory access', rows.get('f+0x14/call'), '')
rows, o = scan('d2c', PRO + ['dmb ish', 'ldr x1, [sp, #8]', 'bl memcpy', 'dmb ish'] + EPI, 'f\t\n', SO)
check('D2: a spill reload between the fence and the call is red', bool(rows.get('f+0x10/call')), True)
rows, o = scan('d2d', PRO + ['dmb ish', 'bl memcpy'] + EPI, redir=SO)
check('D2: no fence after the call is red', bool(rows.get('f+0xc/call')), True)
rows, o = scan('d2e', PRO + F2 + EPI, redir='memcpy\tlibcall\n')
check('D2: a libcall kind is red', bool(rows.get('f+0xc/call')), True)
rows, o = scan('d2f', PRO + F2 + EPI)
check('D2: fences but no record is red', bool(rows.get('f+0xc/call')), True)
rows, o = scan('d2g', PRO + ['bl memcpy'] + EPI, redir=SO)
check('D2: a call of a redirect symbol without fences is red', bool(rows.get('f+0x8/call')), True)
rows, o = scan('d2h', PRO + ['bl g'] + EPI, redir=SO)
check('D2: a call to another function is no D2 item', 'f+0x8/call' in rows, False)
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
