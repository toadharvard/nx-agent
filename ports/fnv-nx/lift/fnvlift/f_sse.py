"""SSE / SSE2 floating point, SSE data movement and the shared vector plumbing (W6).  Entered when any operand is an xmm or mm register
(and no earlier handler took the insn); mnemonics without such an operand (prefetch, ldmxcsr, movnti, emms) register with @op.
Integer SIMD and MMX are in f_simd_int.py and register in the same SSE table.  The semantics are in rt/fnvl.h (sse_* helpers).
Vector ops are emitted as `{ [u32 mx=c->mxcsr;] xmm_t d_=DEST, s_=SOURCE, r_=d_; <lane statements> WRITEBACK }`: d_/s_ are copies, so
aliasing operands are safe; an mm operand is an xmm_t whose low half is the significand of the physical x87 register k (MMR / MMW, rt/fnvl_x87.h: MMX aliases the x87 file)."""
import re
from capstone import x86 as X
from .core import op, fallback, Unsupported, SKIP
from .f_x87_ctl import sse_flaggy, fown_emit
from .f_sys import gp_cond

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
@sse_op('lddqu')
def h_mov128(cx, ins, d0, s1):
    if d0.type == MEM:
        cx.em('MX(%s)=%s;' % (cx.ea(ins, d0), vsrc(cx, ins, s1)))
    else:
        cx.em('x%d=%s;' % (vreg(ins, d0), vsrc(cx, ins, s1)))








def scal(cx, ins, o, bits):
    """a 32/64-bit scalar value: general register, memory, xmm low part or mm"""
    if isx(ins, o):
        return 'x%d.%s[0]' % (vreg(ins, o), 'u' if bits == 32 else 'q')
    if ismm(ins, o):
        return ('(u32)MMR(%d)' if bits == 32 else 'MMR(%d)') % vreg(ins, o)
    return cx.rd(ins, o, bits // 8) if o.type == MEM else cx.rd(ins, o)




@sse_op('movdq2q', 'movq2dq')
def h_movdq2(cx, ins, d0, s1):
    cx.em('x%d=mmx_x(MMR(%d));' % (vreg(ins, d0), vreg(ins, s1)) if cx.m == 'movq2dq' else 'MMW(%d,x%d.q[0]);' % (vreg(ins, d0), vreg(ins, s1)))


@op('movnti')
def h_movnti(cx, ins):
    cx.em('M32(%s)=%s;' % (cx.ea(ins, cx.ops[0]), cx.rd(ins, cx.ops[1])))


@op('prefetchw')
def h_prefetch(cx, ins):
    cx.em(';')      # a hint: no architectural effect, and the address is not even computed






# ---- arithmetic, logic, min/max, sqrt, reciprocal, compare: the DSL Forms (proof/forms_ssefp.py) are the only path ----
# ---- conversions ----






@sse_op('cvtss2sd')
def h_cvtss2sd(cx, ins, d0, s1):
    lanes(cx, ins, d0, s1, 1, 'r_.d[0]=sse_f2d(s_.f[0],mx);')


@sse_op('cvtsd2ss')
def h_cvtsd2ss(cx, ins, d0, s1):
    lanes(cx, ins, d0, s1, 1, 'r_.f[0]=sse_d2f(s_.d[0],mx);')


@sse_op('cvtps2pd', 'cvtpd2ps', 'cvtpi2pd', 'cvtps2pi', 'cvttps2pi', 'cvtpd2pi', 'cvttpd2pi')
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




