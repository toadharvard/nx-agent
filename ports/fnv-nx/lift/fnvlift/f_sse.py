"""SSE / SSE2 floating point, SSE data movement and the shared vector plumbing (W6).  Entered when any operand is an xmm or mm register
(and no earlier handler took the insn); mnemonics without such an operand (prefetch, ldmxcsr, movnti, emms) register with @op.
Integer SIMD and MMX are in f_simd_int.py and register in the same SSE table.  The semantics are in rt/fnvl.h (sse_* helpers).
Vector ops are emitted as `{ [u32 mx=c->mxcsr;] xmm_t d_=DEST, s_=SOURCE, r_=d_; <lane statements> WRITEBACK }`: d_/s_ are copies, so
aliasing operands are safe; an mm operand is an xmm_t whose low half is the significand of the physical x87 register k (MMR / MMW, rt/fnvl_x87.h: MMX aliases the x87 file)."""
import re
from capstone import x86 as X
from .core import op, fallback, Unsupported, SKIP
from .f_x87_ctl import sse_flaggy, fown_emit

SSE = {}
REG, MEM, IMM = X.X86_OP_REG, X.X86_OP_MEM, X.X86_OP_IMM
CT = {'f': 'float', 'd': 'double', 'u': 'u32', 'q': 'u64', 'b': 'u8', 'sb': 's8', 'w': 'u16', 'sw': 's16', 'sd': 's32', 'sq': 's64'}
ESZ = {'f': 4, 'd': 8, 'u': 4, 'q': 8, 'b': 1, 'sb': 1, 'w': 2, 'sw': 2, 'sd': 4, 'sq': 8}


def sse_op(*names):
    def deco(fn):
        for n in names:
            SSE[n] = fn
        return fn
    return deco


def isx(ins, o):
    return o.type == REG and ins.reg_name(o.reg).startswith('xmm')


def ismm(ins, o):
    return o.type == REG and ins.reg_name(o.reg).startswith('mm')


def vreg(ins, o):
    return int(ins.reg_name(o.reg)[3 if isx(ins, o) else 2:])


def vsrc(cx, ins, o):
    """C expression of type xmm_t for a vector source operand (registers, or memory of 4, 8 or 16 bytes)"""
    if isx(ins, o):
        return 'x%d' % vreg(ins, o)
    if ismm(ins, o):
        return 'mmx_x(MMR(%d))' % vreg(ins, o)
    if o.type != MEM:
        raise Unsupported('vector source operand')
    return 'MX(%s)' % cx.ea(ins, o) if o.size == 16 else 'mmx_x(M%d(%s))' % (o.size * 8, cx.ea(ins, o))


def vec(cx, ins, d0, s1, body):
    """one vector op: body works on d_ (dest copy), s_ (source copy; = d_ without a source operand) and r_ (result, starts as d_)"""
    if isx(ins, d0):
        dr, dw = 'x%d' % vreg(ins, d0), 'x%d=r_;' % vreg(ins, d0)
    else:
        dr, dw = 'mmx_x(MMR(%d))' % vreg(ins, d0), 'MMW(%d,r_.q[0]);' % vreg(ins, d0)
    cx.em('{ %sxmm_t d_=%s, s_=%s, r_=d_; %s %s }' % ('u32 mx=c->mxcsr; ' if re.search(r'\bmx\b', body) else '', dr,
                                                      vsrc(cx, ins, s1) if s1 is not None else 'd_', body, dw))


def lanes(cx, ins, d0, s1, n, stmt, pre='', tail=''):
    """stmt once per lane i_ in 0..n-1 (unrolled up to 4 lanes)"""
    body = ('{ const int i_=%d; %s }' * n % tuple(v for k in range(n) for v in (k, stmt))) if n <= 4 else 'for(int i_=0;i_<%d;i_++){ %s }' % (n, stmt)
    vec(cx, ins, d0, s1, pre + body + tail)


def L(f, expr, wf=None):
    """lane statement: a, b = lane i_ of dest, source (field f); the result goes to field wf (default f)"""
    return '%s a=d_.%s[i_], b=s_.%s[i_]; r_.%s[i_]=(%s);' % (CT[f], f, f, wf or f, expr)


def fld(m):
    return 'f' if m.endswith(('ss', 'ps')) else 'd'


@fallback(lambda cx, ins: cx.ops and any(isx(ins, o) or ismm(ins, o) for o in cx.ops))
def h_sse(cx, ins):
    if any(isx(ins, o) for o in cx.ops):
        cx.kinds.add('sse')
    if any(ismm(ins, o) for o in cx.ops):
        cx.kinds.add('mmx')
        if cx.a not in cx.mmx_ok:               # f_x87_ctl.mmx_prepass: not proven canonical on every path to here
            cx.em('MMX_ENTER();')
    if sse_flaggy(ins):
        fown_emit(cx, 2)                                          # sticky flags: the FPSR belongs to the SSE family from here (rt/fnvl_x87.h)
    h = SSE.get(cx.m)
    if h is None:
        raise Unsupported('sse ' + cx.m)
    h(cx, ins, cx.ops[0], cx.ops[1] if len(cx.ops) > 1 else None)


# ---- data movement ----
@sse_op('movaps', 'movups', 'movdqa', 'movdqu', 'movapd', 'movupd', 'lddqu', 'movntps', 'movntpd', 'movntdq')
def h_mov128(cx, ins, d0, s1):
    if d0.type == MEM:
        cx.em('MX(%s)=%s;' % (cx.ea(ins, d0), vsrc(cx, ins, s1)))
    else:
        cx.em('x%d=%s;' % (vreg(ins, d0), vsrc(cx, ins, s1)))


@sse_op('movss', 'movsd')
def h_movs(cx, ins, d0, s1):
    k, bits = ('u', 32) if cx.m == 'movss' else ('q', 64)
    if d0.type == MEM:
        cx.em('M%d(%s)=%s.%s[0];' % (bits, cx.ea(ins, d0), cx.xr(ins, s1), k))
    elif s1.type == MEM:
        cx.em('x%d=mmx_x(M%d(%s));' % (vreg(ins, d0), bits, cx.ea(ins, s1)))
    else:
        cx.em('x%d.%s[0]=x%d.%s[0];' % (vreg(ins, d0), k, vreg(ins, s1), k))


@sse_op('movlps', 'movhps', 'movlpd', 'movhpd')
def h_movlh(cx, ins, d0, s1):
    k = 1 if cx.m[3] == 'h' else 0
    if d0.type == MEM:
        cx.em('M64(%s)=%s.q[%d];' % (cx.ea(ins, d0), cx.xr(ins, s1), k))
    else:
        cx.em('%s.q[%d]=M64(%s);' % (cx.xr(ins, d0), k, cx.ea(ins, s1)))


@sse_op('movlhps', 'movhlps')
def h_movlhps(cx, ins, d0, s1):
    cx.em('%s.q[%d]=%s.q[%d];' % (cx.xr(ins, d0), cx.m == 'movlhps', cx.xr(ins, s1), cx.m == 'movhlps'))


def scal(cx, ins, o, bits):
    """a 32/64-bit scalar value: general register, memory, xmm low part or mm"""
    if isx(ins, o):
        return 'x%d.%s[0]' % (vreg(ins, o), 'u' if bits == 32 else 'q')
    if ismm(ins, o):
        return ('(u32)MMR(%d)' if bits == 32 else 'MMR(%d)') % vreg(ins, o)
    return cx.rd(ins, o, bits // 8) if o.type == MEM else cx.rd(ins, o)


@sse_op('movd', 'movq')
def h_movdq(cx, ins, d0, s1):
    bits = 32 if cx.m == 'movd' else 64
    v = scal(cx, ins, s1, bits)
    if isx(ins, d0):
        cx.em('x%d=mmx_x(%s);' % (vreg(ins, d0), v))
    elif ismm(ins, d0):
        cx.em('MMW(%d,%s);' % (vreg(ins, d0), v))
    else:
        cx.em(cx.wr(ins, d0, v) if bits == 32 else 'M64(%s)=%s;' % (cx.ea(ins, d0), v))


@sse_op('movdq2q', 'movq2dq')
def h_movdq2(cx, ins, d0, s1):
    cx.em('x%d=mmx_x(MMR(%d));' % (vreg(ins, d0), vreg(ins, s1)) if cx.m == 'movq2dq' else 'MMW(%d,x%d.q[0]);' % (vreg(ins, d0), vreg(ins, s1)))


@op('movnti')
def h_movnti(cx, ins):
    cx.em('M32(%s)=%s;' % (cx.ea(ins, cx.ops[0]), cx.rd(ins, cx.ops[1])))


@op('prefetchnta', 'prefetcht0', 'prefetcht1', 'prefetcht2', 'prefetchw')
def h_prefetch(cx, ins):
    cx.em(';')      # a hint: no architectural effect, and the address is not even computed


@op('ldmxcsr')
def h_ldmxcsr(cx, ins):
    cx.em('{ u32 v_=M32(%s); c->mxcsr=v_; sse_fpcr(v_); if(fown()==2u) fnvl_fp_x87_clear(); }' % cx.ea(ins, cx.ops[0]))      # the flags of the operand REPLACE the SSE set (SDM LDMXCSR)


@op('stmxcsr')
def h_stmxcsr(cx, ins):
    cx.em('M32(%s)=c->mxcsr|sse_live();' % cx.ea(ins, cx.ops[0]))      # the SSE set: the shadow bits plus the live FPSR while SSE owns it


# ---- arithmetic, logic, min/max, sqrt, reciprocal ----
for _s, _o in (('add', 0), ('sub', 1), ('mul', 2), ('div', 3)):
    for _x in ('ss', 'sd', 'ps', 'pd'):
        def _h(cx, ins, d0, s1, _o=_o):
            f = fld(cx.m)
            lanes(cx, ins, d0, s1, {'ss': 1, 'sd': 1, 'ps': 4, 'pd': 2}[cx.m[-2:]], L(f, 'sse_arith<%d>(a,b,mx)' % _o))
        SSE[_s + _x] = _h


@sse_op('minss', 'minsd', 'minps', 'minpd', 'maxss', 'maxsd', 'maxps', 'maxpd')
def h_minmax(cx, ins, d0, s1):
    lanes(cx, ins, d0, s1, {'ss': 1, 'sd': 1, 'ps': 4, 'pd': 2}[cx.m[-2:]], L(fld(cx.m), 'sse_%s(a,b,mx)' % cx.m[:3]))


@sse_op('sqrtss', 'sqrtsd', 'sqrtps', 'sqrtpd')
def h_sqrt(cx, ins, d0, s1):
    f = fld(cx.m)
    lanes(cx, ins, d0, s1, {'ss': 1, 'sd': 1, 'ps': 4, 'pd': 2}[cx.m[-2:]], 'r_.%s[i_]=sse_sqrt(s_.%s[i_],mx);' % (f, f))


@sse_op('rcpss', 'rcpps', 'rsqrtss', 'rsqrtps')
def h_rcp(cx, ins, d0, s1):
    lanes(cx, ins, d0, s1, 1 if cx.m.endswith('ss') else 4, 'r_.u[i_]=fnvl_%s32(s_.u[i_]);' % ('rcp' if cx.m[1] == 'c' else 'rsqrt'))


for _m, _e in (('andps', 'a&b'), ('andpd', 'a&b'), ('andnps', '~a&b'), ('andnpd', '~a&b'), ('orps', 'a|b'), ('orpd', 'a|b'),
               ('xorps', 'a^b'), ('xorpd', 'a^b')):
    SSE[_m] = (lambda e: lambda cx, ins, d0, s1: lanes(cx, ins, d0, s1, 2, L('q', e)))(_e)

PRED = ['eq', 'lt', 'le', 'unord', 'neq', 'nlt', 'nle', 'ord']


@sse_op(*['cmp%s%s' % (p, x) for p in PRED + [''] for x in ('ss', 'sd', 'ps', 'pd')])
def h_cmp(cx, ins, d0, s1):
    m = cx.m
    p = int(cx.ops[2].imm) & 7 if m[3:5] in ('ss', 'sd', 'ps', 'pd') and len(m) == 5 else PRED.index(m[3:-2])
    f = fld(m)
    t = 'u' if f == 'f' else 'q'
    lanes(cx, ins, d0, s1, {'ss': 1, 'sd': 1, 'ps': 4, 'pd': 2}[m[-2:]],
          'r_.%s[i_]=-(%s)sse_cmpb<%d>(d_.%s[i_],s_.%s[i_],mx);' % (t, CT[t], p, f, f))


@sse_op('comiss', 'ucomiss', 'comisd', 'ucomisd')
def h_comis(cx, ins, d0, s1):
    f = 'f' if cx.m.endswith('ss') else 'd'
    cx.em('{ u32 mx=c->mxcsr; xmm_t s_=%s; u32 k_=sse_comi(%s.%s[0],s_.%s[0],mx); cf=k_&1; pf=(k_>>2)&1; zf=(k_>>6)&1; of=sf=af=0; }'
          % (vsrc(cx, ins, s1), cx.xr(ins, d0), f, f))


@sse_op('movmskps', 'movmskpd')
def h_movmsk(cx, ins, d0, s1):
    n, k, sh = (4, 'u', 31) if cx.m.endswith('ps') else (2, 'q', 63)
    cx.em(cx.wr(ins, d0, '|'.join('((u32)(%s.%s[%d]>>%d)<<%d)' % (cx.xr(ins, s1), k, i, sh, i) for i in range(n))))


# ---- conversions ----
@sse_op('cvtsi2ss', 'cvtsi2sd')
def h_cvtsi2(cx, ins, d0, s1):
    f = 'f' if cx.m.endswith('ss') else 'd'
    cx.em('%s.%s[0]=(%s)(s32)%s;' % (cx.xr(ins, d0), f, CT[f], cx.rd(ins, s1)))


@sse_op('cvtss2si', 'cvttss2si', 'cvtsd2si', 'cvttsd2si')
def h_cvt2si(cx, ins, d0, s1):
    f = 'f' if 'ss2si' in cx.m else 'd'
    cx.em('{ u32 mx=c->mxcsr; xmm_t s_=%s; %s }' % (vsrc(cx, ins, s1), cx.wr(ins, d0, 'sse_f2i((double)sse_daz(s_.%s[0],mx),%s)' % (f, '3' if 'cvtt' in cx.m else '(mx>>13)&3'))))


@op('cvtss2si', 'cvttss2si', 'cvtsd2si', 'cvttsd2si')
def h_cvt2si_mem(cx, ins):      # r32, m32/m64: no xmm operand, so the fallback would not see it
    if cx.ops[1].type != MEM:
        return SKIP
    h_cvt2si(cx, ins, cx.ops[0], cx.ops[1])


@sse_op('cvtss2sd')
def h_cvtss2sd(cx, ins, d0, s1):
    lanes(cx, ins, d0, s1, 1, 'r_.d[0]=sse_f2d(s_.f[0],mx);')


@sse_op('cvtsd2ss')
def h_cvtsd2ss(cx, ins, d0, s1):
    lanes(cx, ins, d0, s1, 1, 'r_.f[0]=sse_d2f(s_.d[0],mx);')


@sse_op('cvtdq2ps', 'cvtdq2pd', 'cvtps2pd', 'cvtpd2ps', 'cvtps2dq', 'cvttps2dq', 'cvtpd2dq', 'cvttpd2dq', 'cvtpi2ps', 'cvtpi2pd',
        'cvtps2pi', 'cvttps2pi', 'cvtpd2pi', 'cvttpd2pi')
def h_cvtv(cx, ins, d0, s1):
    m = cx.m
    rc = '3' if 'cvtt' in m else '(mx>>13)&3'
    S = {'cvtdq2ps': (4, 'r_.f[i_]=(float)s_.sd[i_];'), 'cvtdq2pd': (2, 'r_.d[i_]=(double)s_.sd[i_];'), 'cvtpi2pd': (2, 'r_.d[i_]=(double)s_.sd[i_];'),
         'cvtpi2ps': (2, 'r_.f[i_]=(float)s_.sd[i_];'), 'cvtps2pd': (2, 'r_.d[i_]=sse_f2d(s_.f[i_],mx);'), 'cvtpd2ps': (2, 'r_.f[i_]=sse_d2f(s_.d[i_],mx);')}
    z = ''
    if m in S:
        n, st = S[m]
        z = 'r_.q[1]=0;' if m == 'cvtpd2ps' else ''
    elif 'ps2' in m:
        n, st = (4 if m.endswith('dq') else 2), 'r_.u[i_]=sse_f2i((double)sse_daz(s_.f[i_],mx),%s);' % rc
    else:
        n, st = 2, 'r_.u[i_]=sse_f2i((double)sse_daz(s_.d[i_],mx),%s);' % rc
        z = 'r_.q[1]=0;' if m.endswith('dq') else ''
    lanes(cx, ins, d0, s1, n, st, tail=z)


# ---- shuffles and unpacks ----
@sse_op('shufps')
def h_shufps(cx, ins, d0, s1):
    im = cx.ops[2].imm
    vec(cx, ins, d0, s1, 'r_.u[0]=d_.u[%d]; r_.u[1]=d_.u[%d]; r_.u[2]=s_.u[%d]; r_.u[3]=s_.u[%d];' % (im & 3, (im >> 2) & 3, (im >> 4) & 3, (im >> 6) & 3))


@sse_op('shufpd')
def h_shufpd(cx, ins, d0, s1):
    im = cx.ops[2].imm
    vec(cx, ins, d0, s1, 'r_.q[0]=d_.q[%d]; r_.q[1]=s_.q[%d];' % (im & 1, (im >> 1) & 1))


@sse_op('unpcklps', 'unpckhps', 'unpcklpd', 'unpckhpd')
def h_unpck(cx, ins, d0, s1):
    h = cx.m[5] == 'h'
    if cx.m.endswith('ps'):
        b = 2 if h else 0
        vec(cx, ins, d0, s1, 'r_.u[0]=d_.u[%d]; r_.u[1]=s_.u[%d]; r_.u[2]=d_.u[%d]; r_.u[3]=s_.u[%d];' % (b, b, b + 1, b + 1))
    else:
        vec(cx, ins, d0, s1, 'r_.q[0]=d_.q[%d]; r_.q[1]=s_.q[%d];' % (h, h))
