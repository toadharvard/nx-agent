"""Integer SIMD, SSE2 (xmm) and MMX (mm0-7 = the x87 physical registers, rt/fnvl_x87.h) (W6).  One lane-statement table serves both register files: an mm operand is
an xmm_t whose upper half is zero and only its low 64 bits are written back (see f_sse.vec); handlers register in f_sse.SSE.
Lane-wise semantics are identical for 64- and 128-bit operands; the ops where they differ (unpack high, pack, shuffles) are explicit."""
from .core import op, Unsupported
from .f_sse import SSE, sse_op, L, ESZ, REG, MEM, IMM, isx, ismm, vreg, vsrc, vec, lanes


def il(cx, ins, d0, s1, f, expr, wf=None, pre='', tail=''):
    """lane op over the whole register: 16 bytes for xmm, 8 for mm"""
    lanes(cx, ins, d0, s1, (8 if ismm(ins, d0) else 16) // ESZ[f], L(f, expr, wf) if expr else '', pre, tail)


TAB = {  # mnemonic: (lane field, expression of a (dest lane) and b (source lane)[, result field])
    'paddb': ('b', 'a+b'), 'paddw': ('w', 'a+b'), 'paddd': ('u', 'a+b'), 'paddq': ('q', 'a+b'),
    'psubb': ('b', 'a-b'), 'psubw': ('w', 'a-b'), 'psubd': ('u', 'a-b'), 'psubq': ('q', 'a-b'),
    'paddusb': ('b', 'a+b>255?255:a+b'), 'paddusw': ('w', 'a+b>65535?65535:a+b'),
    'psubusb': ('b', 'a>b?a-b:0'), 'psubusw': ('w', 'a>b?a-b:0'),
    'paddsb': ('sb', 'sse_sat(a+b,-128,127)'), 'paddsw': ('sw', 'sse_sat(a+b,-32768,32767)'),
    'psubsb': ('sb', 'sse_sat(a-b,-128,127)'), 'psubsw': ('sw', 'sse_sat(a-b,-32768,32767)'),
    'pmullw': ('w', 'a*b'), 'pmulhw': ('sw', '(a*b)>>16'), 'pmulhuw': ('w', '((u32)a*b)>>16'),
    'pmaxsw': ('sw', 'a>b?a:b'), 'pminsw': ('sw', 'a<b?a:b'), 'pmaxub': ('b', 'a>b?a:b'), 'pminub': ('b', 'a<b?a:b'),
    'pavgb': ('b', '(a+b+1)>>1'), 'pavgw': ('w', '(a+b+1)>>1'),
    'pcmpeqb': ('b', '-(a==b)'), 'pcmpeqw': ('w', '-(a==b)'), 'pcmpeqd': ('u', '-(a==b)'),
    'pcmpgtb': ('sb', '-(a>b)'), 'pcmpgtw': ('sw', '-(a>b)'), 'pcmpgtd': ('sd', '-(a>b)'),
    'pand': ('q', 'a&b'), 'pandn': ('q', '~a&b'), 'por': ('q', 'a|b'), 'pxor': ('q', 'a^b'),
    'pmuludq': ('q', '(u64)(u32)d_.u[2*i_]*(u32)s_.u[2*i_]'),
    'pmaddwd': ('sd', '(s32)d_.sw[2*i_]*s_.sw[2*i_]+(s32)d_.sw[2*i_+1]*s_.sw[2*i_+1]'),
    'psadbw': ('q', 'sse_sad(d_.b+8*i_,s_.b+8*i_)'),
}
for _m, (_f, _e) in TAB.items():
    SSE[_m] = (lambda f, e: lambda cx, ins, d0, s1: il(cx, ins, d0, s1, f, e))(_f, _e)

SHIFT = {  # count n_ is a 64-bit value; counts above the lane width give 0 (sign fill for the arithmetic shifts)
    'psllw': ('w', 'n_>15?0:a<<n_'), 'psrlw': ('w', 'n_>15?0:a>>n_'), 'psraw': ('sw', 'a>>(n_>15?15:n_)'),
    'pslld': ('u', 'n_>31?0:a<<n_'), 'psrld': ('u', 'n_>31?0:a>>n_'), 'psrad': ('sd', 'a>>(n_>31?31:n_)'),
    'psllq': ('q', 'n_>63?0:a<<n_'), 'psrlq': ('q', 'n_>63?0:a>>n_'),
}


def h_shift(cx, ins, d0, s1):
    f, e = SHIFT[cx.m]
    if s1.type == IMM:
        il(cx, ins, d0, None, f, e, pre='u64 n_=%du;' % (s1.imm & 0xff))
    else:
        il(cx, ins, d0, s1, f, e, pre='u64 n_=s_.q[0];')


for _m in SHIFT:
    SSE[_m] = h_shift


@sse_op('pslldq', 'psrldq')
def h_bshift(cx, ins, d0, s1):
    n = s1.imm & 0xff
    if n > 15:
        vec(cx, ins, d0, None, 'r_.q[0]=r_.q[1]=0;')
    else:
        vec(cx, ins, d0, None, 'for(int i_=0;i_<16;i_++) r_.b[i_]=%s;' % ('i_>=%d?d_.b[i_-%d]:0' % (n, n) if cx.m == 'pslldq' else 'i_+%d<16?d_.b[i_+%d]:0' % (n, n)))


for _k, _e in (('bw', 'b'), ('wd', 'w'), ('dq', 'u'), ('qdq', 'q')):
    for _h in 'lh':
        def _u(cx, ins, d0, s1, _e=_e, _h=_h):
            mm = ismm(ins, d0)
            if mm and _e == 'q':
                raise Unsupported('punpck qdq on mm')
            n = (8 if mm else 16) // ESZ[_e]
            base = n // 2 if (_h == 'h' and not mm) else 0
            pre = 'd_.q[0]>>=32; s_.q[0]>>=32;' if (mm and _h == 'h') else ''
            lanes(cx, ins, d0, s1, n, 'r_.%s[i_]=(i_&1)?s_.%s[(i_>>1)+%d]:d_.%s[(i_>>1)+%d];' % (_e, _e, base, _e, base), pre)
        SSE['punpck%s%s' % (_h, _k)] = _u


@sse_op('packsswb', 'packuswb', 'packssdw')
def h_pack(cx, ins, d0, s1):
    if cx.m == 'packssdw':
        st = ''.join('r_.sw[%d]=sse_sat(d_.sd[%d],-32768,32767); r_.sw[%d]=sse_sat(s_.sd[%d],-32768,32767);' % (i, i, i + 4, i) for i in range(4))
    else:
        f, lo, hi = ('sb', -128, 127) if cx.m == 'packsswb' else ('b', 0, 255)
        st = ''.join('r_.%s[%d]=sse_sat(d_.sw[%d],%d,%d); r_.%s[%d]=sse_sat(s_.sw[%d],%d,%d);' % (f, i, i, lo, hi, f, i + 8, i, lo, hi) for i in range(8))
    vec(cx, ins, d0, s1, st + ('r_.u[1]=r_.u[2];' if ismm(ins, d0) else ''))


@sse_op('pshufd', 'pshuflw', 'pshufhw', 'pshufw')
def h_pshuf(cx, ins, d0, s1):
    im, m = cx.ops[2].imm, cx.m
    sel = lambda i: (im >> 2 * i) & 3
    if m == 'pshufd':
        st = ''.join('r_.u[%d]=s_.u[%d];' % (i, sel(i)) for i in range(4))
    elif m == 'pshufhw':
        st = 'r_.q[0]=s_.q[0];' + ''.join('r_.w[%d]=s_.w[%d];' % (4 + i, 4 + sel(i)) for i in range(4))
    else:
        st = ('r_.q[1]=s_.q[1];' if m == 'pshuflw' else '') + ''.join('r_.w[%d]=s_.w[%d];' % (i, sel(i)) for i in range(4))
    vec(cx, ins, d0, s1, st)


@sse_op('pextrw')
def h_pextrw(cx, ins, d0, s1):
    k = cx.ops[2].imm & (3 if ismm(ins, s1) else 7)
    cx.em('{ xmm_t s_=%s; %s }' % (vsrc(cx, ins, s1), cx.wr(ins, d0, '(u32)s_.w[%d]' % k)))


@sse_op('pinsrw')
def h_pinsrw(cx, ins, d0, s1):
    k = cx.ops[2].imm & (3 if ismm(ins, d0) else 7)
    vec(cx, ins, d0, None, 'r_.w[%d]=(u16)(%s);' % (k, cx.rd(ins, s1, 2)))


@sse_op('pmovmskb')
def h_pmovmskb(cx, ins, d0, s1):
    cx.em('{ xmm_t s_=%s; %s }' % (vsrc(cx, ins, s1), cx.wr(ins, d0, 'sse_pmovmskb(s_,%d)' % (8 if ismm(ins, s1) else 16))))


@sse_op('movntq')
def h_movntq(cx, ins, d0, s1):
    cx.em('M64(%s)=MMR(%d);' % (cx.ea(ins, d0), vreg(ins, s1)))


@sse_op('maskmovq', 'maskmovdqu')
def h_maskmov(cx, ins, d0, s1):      # byte store to [edi] where the mask byte's top bit is set
    n = 8 if cx.m == 'maskmovq' else 16
    cx.em('{ xmm_t d_=%s, s_=%s; for(int i_=0;i_<%d;i_++) if(s_.b[i_]&0x80) M8(edi+i_)=d_.b[i_]; }' % (vsrc(cx, ins, d0), vsrc(cx, ins, s1), n))


@op('emms')
def h_emms(cx, ins):
    cx.kinds.add('mmx')
    cx.em('MMX_EMMS();')
