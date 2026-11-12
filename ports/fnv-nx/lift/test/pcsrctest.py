#!/usr/bin/env python3
"""pcsrc.py on synthetic bytes: a positive per source (get-pc thunk, call $+5; pop, SEH-prolog style moved return address, fnstenv FIP, fxsave FIP) and negatives (escapes)."""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
import pcsrc
T0 = 0x1000
def build(funcs):
    """funcs: [(addr, bytes)] -> text, entries, ranges"""
    end = max(a + len(b) for a, b in funcs); text = bytearray(b'\xcc' * (end - T0))
    for a, b in funcs: text[a - T0:a - T0 + len(b)] = b
    return bytes(text), {a: {'ptr'} if kind else set() for a, b, kind in [(a, b, False) for a, b in funcs]}, [(a, a + len(b)) for a, b in funcs]
def rel(frm, to): return (to - frm - 5) & 0xffffffff
def call(frm, to): return b'\xe8' + rel(frm, to).to_bytes(4, 'little')
def run(funcs, indirect=()):
    text, ent, rng = build(funcs)
    for a in indirect: ent[a] = {'ptr'}
    d = tempfile.mkdtemp()
    red, res = pcsrc.run(text, T0, ent, rng, d, 't')
    rows = [l.rstrip('\n').split('\t') for l in open(d + '/pcsources.tsv') if not l.startswith('#')]
    return red, res, rows
ok = True
def check(name, cond, info=None):
    global ok
    print(('ok   ' if cond else 'FAIL ') + name + ('' if cond else '  ' + repr(info)))
    ok &= bool(cond)
THUNK = 0x1100
# 1 get-pc thunk: caller does `call thunk; mov ecx,[eax+0x20]` : the pc value is named data
c = call(0x1000, THUNK) + b'\x8b\x48\x20' + b'\x31\xc0\xc3'
t = b'\x8b\x04\x24\xc3'                         # mov eax,[esp]; ret
red, res, rows = run([(0x1000, c), (THUNK, t)])
check('thunk positive: red=0', red == 0, rows); check('thunk positive: source ra read', any(r[2] == 'ra' and r[3] == 'read' for r in rows), rows)
check('thunk positive: consumer mem', any(r[3] == 'mem' and r[2] == 'abs' for r in rows), rows)
# 2 call $+5; pop ebx; jmp ebx+... : here pop ebx; add ebx,imm; jmp ebx to an entry
body = call(0x1000, 0x1005) + b'\x5b'            # call $+5; pop ebx
body += b'\x83\xc3\x08' + b'\xff\xe3'            # add ebx,8; jmp ebx -> 0x1005+8 = 0x100d
tgt = 0x100d
red, res, rows = run([(0x1000, body + b'\xcc' * (tgt - 0x1000 - len(body))), (tgt, b'\xc3')])
check('call$+5 positive: transfer to an entry', red == 0 and any(r[3] == 'code-target' for r in rows), rows)
# 2b the same jump to a non-entry is red
red, res, rows = run([(0x1000, body + b'\xcc' * (tgt + 4 - 0x1000 - len(body))), (tgt + 3, b'\xc3')])
check('call$+5 negative: target not entry', red >= 1 and any(r[3] == 'target-not-entry' for r in rows), rows)
# 3 SEH-prolog style: callee moves its return address into a stack slot below and returns through push/ret style
f = b'\x58\x50\xc3'                              # pop eax; push eax; ret : ra consumed by ret
red, res, rows = run([(0x1000, call(0x1000, 0x1100) + b'\x31\xc0\xc3'), (0x1100, f)])
check('moved-ra positive: red=0', red == 0, rows)
# 4 negative: store the return address to memory
red, res, rows = run([(0x1000, call(0x1000, 0x1100) + b'\xc3'), (0x1100, b'\x8b\x04\x24\xa3\x00\x20\x00\x00\xc3')])
check('store negative: red', red >= 1 and any(r[3] == 'store' for r in rows), rows)
# 5 negative: pass to a callee (push eax; call)
red, res, rows = run([(0x1000, call(0x1000, 0x1100) + b'\xc3'), (0x1100, b'\x8b\x04\x24\x50' + call(0x1104, 0x1200) + b'\x58\xc3'), (0x1200, b'\xc3')])
check('pass negative: red', red >= 1 and any(r[3] == 'passed' for r in rows), rows)
# 6 negative: merge with non-constant (add eax,ecx)
red, res, rows = run([(0x1000, call(0x1000, 0x1100) + b'\xc3'), (0x1100, b'\x8b\x04\x24\x01\xc8\xc3')])
check('merge negative: red', red >= 1 and any(r[3] == 'merge' for r in rows), rows)
# 7 negative: ra read in an entry that is reached indirectly
red, res, rows = run([(0x1000, call(0x1000, 0x1100) + b'\xc3'), (0x1100, t)], indirect=[0x1100])
check('indirect-callers negative: red', red >= 1 and any(r[3] == 'indirect-callers' for r in rows), rows)
# 8 FPU env: fldz; fnstenv [esp-12]; mov eax,[esp] ... ; FIP at +12
fp = b'\xd9\xee' + b'\xd9\x74\x24\xf4' + b'\x8b\x44\x24\x00' + b'\x31\xc0\xc3'      # fldz; fnstenv [esp-12]; mov eax,[esp+0]  (= env+12)
red, res, rows = run([(0x1000, fp)])
check('fnstenv positive: FIP read', red == 0 and any(r[2] == 'fip' and r[3] == 'read' for r in rows), rows)
# 8b store of FIP is red
fp2 = b'\xd9\xee' + b'\xd9\x74\x24\xf4' + b'\x8b\x44\x24\x00' + b'\xa3\x00\x20\x00\x00' + b'\xc3'
red, res, rows = run([(0x1000, fp2)])
check('fnstenv negative: store red', red >= 1, rows)
# 9 fxsave FIP at +8
fx = b'\x83\xec\x20' + b'\x0f\xae\x04\x24' + b'\x8b\x44\x24\x08' + b'\x83\xc4\x20' + b'\x31\xc0\xc3'
red, res, rows = run([(0x1000, fx)])
check('fxsave positive: FIP read', red == 0 and any(r[2] == 'fip' and r[3] == 'read' for r in rows), rows)
# 10 plain ret consuming its own ra: no source
red, res, rows = run([(0x1000, call(0x1000, 0x1100) + b'\xc3'), (0x1100, b'\xc3')])
check('plain ret: no rows', red == 0 and not any(r[3] == 'read' for r in rows), rows)
# 11 determinism
a = run([(0x1000, call(0x1000, THUNK) + b'\xc3'), (THUNK, t)])[2]; b = run([(0x1000, call(0x1000, THUNK) + b'\xc3'), (THUNK, t)])[2]
check('deterministic', a == b)
sys.exit(0 if ok else 1)
