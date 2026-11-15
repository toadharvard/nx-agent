#!/usr/bin/env python3
"""The control-word load sites of one decoded entry and the value of each (x87 FCW, MXCSR): the one place that reads them (fpstate.py and fpmask.py call it).
control_loads(F, x) -> (fcw_rows, mx_rows) for the instruction at x: rows (x, mnemonic, effects); effects = fpmodes.val value set as ('c', v) | ('t', A, O) | ('u', reason)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import x86flow as W
import fpmodes as FP
from x86flow import X, M32, mn

IMP_CTL = ('_controlfp', '_control87', '_fpreset')


# ---- the MSVC CRT encoding of _controlfp(new, mask) as a transform of the x87 word / MXCSR ----
def crt_to_x87(v):
    r = 0
    for crt, x in ((0x10, 1), (0x80000, 2), (0x08, 4), (0x04, 8), (0x02, 16), (0x01, 32)):
        r |= x if v & crt else 0
    return r | ((v >> 8) & 3) << 10 | {2: 0, 1: 2, 0: 3, 3: 1}[(v >> 16) & 3] << 8 | (0x1000 if v & 0x40000 else 0)


def crt_mask_x87(m):
    r = crt_to_x87(m) & 0x1c3f
    r |= 0x0300 if m & 0x30000 else 0
    return r


def crt_to_mx(v, m):
    """(A, O) for MXCSR: EM -> mask bits 7-12 (same order as x87 0-5), RC -> 13-14, DN (0x03000000) -> FTZ / DAZ"""
    xm, xv = crt_mask_x87(m) & 0x3f, crt_to_x87(v) & 0x3f
    am = ~((xm << 7) | ((m >> 8 & 3) << 13) | (0x8040 if m & 0x03000000 else 0)) & M32
    om = (xv & xm) << 7 | ((v >> 8) & 3 & (m >> 8 & 3)) << 13 | (0x8040 if (m & 0x03000000 and v & 0x03000000) else 0)
    return am, om


def crt_to_cw(v, m):
    xm = crt_mask_x87(m)
    return (~xm) & M32, crt_to_x87(v) & xm


def push_consts(F, x, n):
    """constants pushed for the call at x, top of stack first (a pred walk through single-predecessor straight-line code; None = not a constant)"""
    out, pos = [], x
    while len(out) < n:
        ps = F.preds.get(pos, [])
        if len(ps) != 1:
            break
        q = F.ins[ps[0]]
        if mn(q) == 'push':
            v = FP.srcval(F, ps[0], q, q.operands[0], 0)
            out.append(next(iter(v))[1] if len(v) == 1 and next(iter(v))[0] == 'c' else None)
        elif FP.espd(q) != 0 or mn(q) == 'call':
            break
        pos = ps[0]
    return (out + [None] * n)[:n]


def effects(vs):
    """value set of fpmodes.val -> list of ('c', v) | ('t', A, O) | ('u', reason)"""
    r = []
    for v in sorted(vs, key=repr):
        if v[0] == 'c':
            r.append(('c', v[1]))
        elif v[0] == 't':
            r.append(('t', v[1], v[2]))
        elif v[0] == 'arg':
            r.append(('u', 'arg'))
        elif v[0] == 'glob':
            r.append(('u', 'global'))
        else:
            r.append(('u', v[1] if len(v) > 1 else '?'))
    return r



def is_ctl_import(i):
    """the import name if the instruction is a call through an IAT slot of one of the CRT control imports, else None"""
    o = i.operands
    if mn(i) == 'call' and o[0].type == X.X86_OP_MEM and not o[0].mem.base and not o[0].mem.index and not o[0].mem.segment:
        nm = W.IATADDR.get(o[0].mem.disp & M32)
        return nm if nm in IMP_CTL else None
    return None


def control_loads(F, x):
    """-> (fcw rows, mxcsr rows) of the instruction at x: (x, mnemonic, effects); empty lists for an instruction that loads no control state"""
    i = F.ins[x]; m = mn(i); o = i.operands
    fcw, mxr = [], []
    if m in ('fldcw', 'ldmxcsr'):
        mx = m == 'ldmxcsr'; F.w = 4 if mx else 2
        l = FP.memslot(i, o[0], F.w)
        if l is None:
            eff = [('u', 'ptr')]
        elif l[0] == 'g':
            eff = [('u', 'global:%08x' % l[1])]
        else:
            eff = effects(FP.val(F, x, l))
        (mxr if mx else fcw).append((x, m, eff))
    elif m in ('fninit', 'finit'):
        fcw.append((x, m, [('c', 0x37f)]))
    elif m in ('frstor', 'fldenv', 'fxrstor'):
        (mxr if m == 'fxrstor' else fcw).append((x, m, [('u', 'env-load')]))
        if m == 'fxrstor':
            fcw.append((x, m, [('u', 'env-load')]))
    else:
        nm = is_ctl_import(i)
        if nm:
            a1, a2 = push_consts(F, x, 2)
            if nm == '_fpreset':
                e1 = e2 = [('u', 'import:_fpreset')]
            elif a1 is None or a2 is None:
                e1 = e2 = [('u', 'import:%s:arg' % nm)]
            else:
                e1, e2 = [('t',) + crt_to_cw(a1, a2)], [('t',) + crt_to_mx(a1, a2)]
            fcw.append((x, 'imp:' + nm, e1)); mxr.append((x, 'imp:' + nm, e2))
    return fcw, mxr
