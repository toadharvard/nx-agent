"""Integer SIMD, SSE2 (xmm) and MMX (mm0-7 = the x87 physical registers, rt/fnvl_x87.h) (W6).  One lane-statement table serves both register files: an mm operand is
an xmm_t whose upper half is zero and only its low 64 bits are written back (see f_sse.vec); handlers register in f_sse.SSE.
Lane-wise semantics are identical for 64- and 128-bit operands; the ops where they differ (unpack high, pack, shuffles) are explicit."""
from .core import op, Unsupported
from .f_sse import SSE, sse_op, L, ESZ, REG, MEM, IMM, isx, ismm, vreg, vsrc, vec, lanes


def il(cx, ins, d0, s1, f, expr, wf=None, pre='', tail=''):
    """lane op over the whole register: 16 bytes for xmm, 8 for mm"""
    lanes(cx, ins, d0, s1, (8 if ismm(ins, d0) else 16) // ESZ[f], L(f, expr, wf) if expr else '', pre, tail)














@sse_op('movntq')
def h_movntq(cx, ins, d0, s1):
    cx.em('M64(%s)=MMR(%d);' % (cx.ea(ins, d0), vreg(ins, s1)))


@sse_op('maskmovq', 'maskmovdqu')
def h_maskmov(cx, ins, d0, s1):      # byte store to [edi] where the mask byte's top bit is set
    n = 8 if cx.m == 'maskmovq' else 16
    cx.em('{ xmm_t d_=%s, s_=%s; for(int i_=0;i_<%d;i_++) if(s_.b[i_]&0x80) M8(edi+i_)=d_.b[i_]; }' % (vsrc(cx, ins, d0), vsrc(cx, ins, s1), n))


