"""SSE subset, entered when any operand is an xmm register (and no earlier handler took the insn).
Quirk kept from the PoC: movsd is registered in core for the string op too; here it is the SSE form."""
from capstone import x86 as X
from .core import fallback, Unsupported

SSE = {}
SS = {'addss': '+', 'subss': '-', 'mulss': '*', 'divss': '/'}
SD = {'addsd': '+', 'subsd': '-', 'mulsd': '*', 'divsd': '/'}
PS = {'addps': '+', 'subps': '-', 'mulps': '*', 'divps': '/'}
LG = {'xorps': '^', 'andps': '&', 'orps': '|', 'pxor': '^', 'pand': '&', 'por': '|', 'xorpd': '^', 'andpd': '&'}
CMP = ('comiss', 'ucomiss', 'comisd', 'ucomisd')
REG, MEM = X.X86_OP_REG, X.X86_OP_MEM


def sse_op(*names):
    def deco(fn):
        for n in names:
            SSE[n] = fn
        return fn
    return deco


def xs(cx, ins, op, fld, n):
    """scalar/packed source as expression list"""
    if op.type == REG:
        return ['%s.%s[%d]' % (cx.xr(ins, op), fld, k) for k in range(n)]
    ty = {'u': 'M32', 'f': 'MF32', 'd': 'MF64', 'q': 'M64'}[fld]
    st = 8 if fld in 'dq' else 4
    return ['%s(%s+%du)' % (ty, cx.ea(ins, op), k * st) for k in range(n)]


@fallback(lambda cx, ins: cx.ops and any(o.type == REG and ins.reg_name(o.reg).startswith('xmm') for o in cx.ops))
def h_sse(cx, ins):
    cx.kinds.add('sse')
    h = SSE.get(cx.m)
    if h is None:
        raise Unsupported('sse ' + cx.m)
    h(cx, ins, cx.ops[0], cx.ops[1] if len(cx.ops) > 1 else None)


@sse_op('movss')
def h_movss(cx, ins, d0, s1):
    xr, em = cx.xr, cx.em
    if d0.type == REG and s1.type == MEM:
        em('{ u32 v=M32(%s); %s.u[0]=v; %s.u[1]=%s.u[2]=%s.u[3]=0; }' % ((cx.ea(ins, s1),) + (xr(ins, d0),) * 4))
    elif d0.type == MEM:
        em('M32(%s)=%s.u[0];' % (cx.ea(ins, d0), xr(ins, s1)))
    else:
        em('%s.u[0]=%s.u[0];' % (xr(ins, d0), xr(ins, s1)))


@sse_op('movsd')
def h_movsd(cx, ins, d0, s1):
    xr, em = cx.xr, cx.em
    if d0.type == REG and s1.type == MEM:
        em('{ u64 v=M64(%s); %s.q[0]=v; %s.q[1]=0; }' % (cx.ea(ins, s1), xr(ins, d0), xr(ins, d0)))
    elif d0.type == MEM:
        em('M64(%s)=%s.q[0];' % (cx.ea(ins, d0), xr(ins, s1)))
    else:
        em('%s.q[0]=%s.q[0];' % (xr(ins, d0), xr(ins, s1)))


@sse_op('movaps', 'movups', 'movdqa', 'movdqu', 'movapd', 'movupd')
def h_movaps(cx, ins, d0, s1):
    sv = xs(cx, ins, s1, 'u', 4)
    if d0.type == REG:
        cx.em('{ u32 a0=%s,a1=%s,a2=%s,a3=%s; X.u[0]=a0;X.u[1]=a1;X.u[2]=a2;X.u[3]=a3; }'.replace('X', cx.xr(ins, d0)) % tuple(sv))
    else:
        cx.em(' '.join('M32(%s+%du)=%s;' % (cx.ea(ins, d0), 4 * k, sv[k]) for k in range(4)))


@sse_op('movd')
def h_movd(cx, ins, d0, s1):
    if d0.type == REG and ins.reg_name(d0.reg).startswith('xmm'):
        cx.em('{ u32 v=%s; X.u[0]=v; X.u[1]=X.u[2]=X.u[3]=0; }'.replace('X', cx.xr(ins, d0)) % cx.rd(ins, s1))
    else:
        cx.em(cx.wr(ins, d0, cx.xr(ins, s1) + '.u[0]'))


@sse_op('movq')
def h_movq(cx, ins, d0, s1):
    if d0.type == REG:
        cx.em('{ u64 v=%s; X.q[0]=v; X.q[1]=0; }'.replace('X', cx.xr(ins, d0)) % xs(cx, ins, s1, 'q', 1)[0])
    else:
        cx.em('M64(%s)=%s.q[0];' % (cx.ea(ins, d0), cx.xr(ins, s1)))


@sse_op(*SS)
def h_ss(cx, ins, d0, s1):
    xr = cx.xr
    cx.em('{ float x_=%s.f[0], y_=%s; %s.f[0]=fxnf(x_%sy_,x_,y_); }' % (xr(ins, d0), xs(cx, ins, s1, 'f', 1)[0], xr(ins, d0), SS[cx.m]))


@sse_op(*SD)
def h_sd(cx, ins, d0, s1):
    xr = cx.xr
    cx.em('{ double x_=%s.d[0], y_=%s; %s.d[0]=fxn(x_%sy_,x_,y_); }' % (xr(ins, d0), xs(cx, ins, s1, 'd', 1)[0], xr(ins, d0), SD[cx.m]))


@sse_op(*PS)
def h_ps(cx, ins, d0, s1):
    xr, m = cx.xr, cx.m
    sv = xs(cx, ins, s1, 'f', 4)
    cx.em('{ float a0=%s,a1=%s,a2=%s,a3=%s; ' % tuple(sv) + ' '.join(
        '%s.f[%d]=fxnf(%s.f[%d]%sa%d,%s.f[%d],a%d);' % (xr(ins, d0), k, xr(ins, d0), k, PS[m], k, xr(ins, d0), k, k) for k in range(4)) + ' }')


@sse_op(*LG)
def h_logic(cx, ins, d0, s1):
    sv = xs(cx, ins, s1, 'u', 4)
    cx.em('{ u32 a0=%s,a1=%s,a2=%s,a3=%s; ' % tuple(sv) + ' '.join('%s.u[%d]%s=a%d;' % (cx.xr(ins, d0), k, LG[cx.m], k) for k in range(4)) + ' }')


@sse_op('andnps', 'pandn')
def h_andn(cx, ins, d0, s1):
    sv = xs(cx, ins, s1, 'u', 4)
    cx.em('{ u32 a0=%s,a1=%s,a2=%s,a3=%s; ' % tuple(sv) + ' '.join('X.u[%d]=~X.u[%d]&a%d;'.replace('X', cx.xr(ins, d0)) % (k, k, k) for k in range(4)) + ' }')


@sse_op('sqrtss')
def h_sqrtss(cx, ins, d0, s1):
    cx.em('{ float x_=%s; %s.f[0]=fxnf(__builtin_sqrtf(x_),x_,x_); }' % (xs(cx, ins, s1, 'f', 1)[0], cx.xr(ins, d0)))


@sse_op('minss', 'maxss')
def h_minmax(cx, ins, d0, s1):
    xr = cx.xr
    cx.em('{ float a=%s.f[0], b=%s; %s.f[0]=(a%sb)?a:b; }' % (xr(ins, d0), xs(cx, ins, s1, 'f', 1)[0], xr(ins, d0), '<' if cx.m == 'minss' else '>'))


@sse_op(*CMP)
def h_comis(cx, ins, d0, s1):
    f = 'f' if cx.m.endswith('ss') else 'd'
    cx.em('{ double x=%s.%s[0], y=%s; of=sf=af=0; if(x!=x||y!=y){zf=pf=cf=1;} else {zf=x==y; pf=0; cf=x<y;} }' % (cx.xr(ins, d0), f, xs(cx, ins, s1, f, 1)[0]))


@sse_op('cvtsi2ss')
def h_cvtsi2ss(cx, ins, d0, s1):
    cx.em('%s.f[0]=(float)(s32)%s;' % (cx.xr(ins, d0), cx.rd(ins, s1)))


@sse_op('cvtsi2sd')
def h_cvtsi2sd(cx, ins, d0, s1):
    cx.em('%s.d[0]=(double)(s32)%s;' % (cx.xr(ins, d0), cx.rd(ins, s1)))


@sse_op('cvttss2si', 'cvttsd2si')
def h_cvtt(cx, ins, d0, s1):
    cx.em(cx.wr(ins, d0, 'fx_i32((double)%s,3)' % xs(cx, ins, s1, 'f' if 'ss' in cx.m else 'd', 1)[0]))


@sse_op('cvtss2si', 'cvtsd2si')
def h_cvt(cx, ins, d0, s1):
    cx.em(cx.wr(ins, d0, 'fx_i32((double)%s,0)' % xs(cx, ins, s1, 'f' if 'ss' in cx.m else 'd', 1)[0]))


@sse_op('cvtss2sd')
def h_cvtss2sd(cx, ins, d0, s1):
    cx.em('%s.d[0]=(double)%s;' % (cx.xr(ins, d0), xs(cx, ins, s1, 'f', 1)[0]))


@sse_op('cvtsd2ss')
def h_cvtsd2ss(cx, ins, d0, s1):
    cx.em('%s.f[0]=(float)%s;' % (cx.xr(ins, d0), xs(cx, ins, s1, 'd', 1)[0]))


@sse_op('shufps')
def h_shufps(cx, ins, d0, s1):
    im = cx.ops[2].imm
    sv = xs(cx, ins, s1, 'u', 4)
    D = cx.xr(ins, d0)
    cx.em('{ u32 a0=%s.u[0],a1=%s.u[1],a2=%s.u[2],a3=%s.u[3]; u32 b0=%s,b1=%s,b2=%s,b3=%s; ' % ((D,) * 4 + tuple(sv)) +
          '%s.u[0]=a%d; %s.u[1]=a%d; %s.u[2]=b%d; %s.u[3]=b%d; }' % (D, im & 3, D, (im >> 2) & 3, D, (im >> 4) & 3, D, (im >> 6) & 3))


@sse_op('unpcklps', 'unpckhps')
def h_unpck(cx, ins, d0, s1):
    sv = xs(cx, ins, s1, 'u', 4)
    D = cx.xr(ins, d0)
    h = 2 if cx.m == 'unpckhps' else 0
    cx.em('{ u32 a0=%s.u[%d],a1=%s.u[%d]; u32 b0=%s,b1=%s; %s.u[0]=a0; %s.u[1]=b0; %s.u[2]=a1; %s.u[3]=b1; }' % (D, h, D, h + 1, sv[h], sv[h + 1], D, D, D, D))


@sse_op('movlhps')
def h_movlhps(cx, ins, d0, s1):
    cx.em('%s.q[1]=%s.q[0];' % (cx.xr(ins, d0), cx.xr(ins, s1)))


@sse_op('movhlps')
def h_movhlps(cx, ins, d0, s1):
    cx.em('%s.q[0]=%s.q[1];' % (cx.xr(ins, d0), cx.xr(ins, s1)))


@sse_op('movlps', 'movhps')
def h_movlhps_mem(cx, ins, d0, s1):
    k = 1 if cx.m == 'movhps' else 0
    if d0.type == REG:
        cx.em('%s.q[%d]=M64(%s);' % (cx.xr(ins, d0), k, cx.ea(ins, s1)))
    else:
        cx.em('M64(%s)=%s.q[%d];' % (cx.ea(ins, d0), cx.xr(ins, s1), k))
