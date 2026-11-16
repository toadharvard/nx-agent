#!/usr/bin/env python3
"""R9: inside a locked instruction (core.Ctx.macc == 'LK') every memory access of the DSL-emitted Form is the LK accessor, never plain M (the atomic rule works on the private copy LK_BEGIN makes).
    dsl_lock_test.py     (exit 0 = all pass)"""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fnvnx.lift.proof.tests.declared_x86sys as declared_x86sys; declared_x86sys.configure()
declared_x86sys.configure_segwrites()      # emit_form specialises the code-selector store guard on the SEGWRITES sets
import capstone
from fnvnx.lift.fnvlift import core
from fnvnx.lift.proof import lift_dsl as f_dsl
from fnvnx.lift.proof import forms
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); md.detail = True
A0, FAILS = 0x400000, []


def ins(hx):
    return next(md.disasm(bytes.fromhex(hx), A0))


def check(n, ok):
    print('%-66s %s' % (n, 'ok' if ok else 'FAIL'))
    if not ok: FAILS.append(n)


for hx in ('0108', '870a', '0fab08'):
    for macc, other in (('M', 'LK'), ('LK', 'M')):
        L, _ = forms.build(ins(hx), A0).to_c_inline(lambda n: n, lambda c, n, r: '', lambda v: '', (), macc)
        t = '\n'.join(L)
        check('%s accessor %s: loads and stores use only it' % (hx, macc), re.search(r'(?<![A-Za-z])%s(8|16|32)\(' % macc, t) is not None and re.search(r'(?<![A-Za-z])%s(8|16|32)\(' % other, t) is None)
i = ins('0108')
cx = core.Ctx(A0, i.size, {A0: i}, {}, [])
cx.start, cx.a, cx.nxt, cx.m, cx.pfx, cx.ops, cx.term, cx.xm, cx.xdp = A0, A0, A0 + i.size, 'add', '', i.operands, False, None, 0
cx.uent, cx.b0, cx.modrm, cx.s, cx.macc = set(), 1, i.modrm, 32, 'LK'
f_dsl.emit_form(cx, i)
t = '\n'.join(cx.L)
check('f_dsl.emit_form passes cx.macc (LK in the emitted lines)', 'LK32(' in t and 'M32(' not in t.replace('LK32(', ''))
sys.exit(1 if FAILS else 0)
