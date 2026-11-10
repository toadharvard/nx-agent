"""x87 (opcode bytes 0xd8..0xdf), register-stack and memory forms. Chain order is significant."""
from .core import fallback, Unsupported

AR = {0: '+', 1: '*', 4: '-', 5: '-r', 6: '/', 7: '/r'}
POP = ' FPOP();'


def ar(dst, x, y, o):  # dst = x o y
    if o.endswith('r'):
        x, y = y, x
        o = o[0]
    return '{ double x_=(%s), y_=(%s); %s=FXN(x_%sy_,x_,y_); }' % (x, y, dst, o)


@fallback(lambda cx, ins: 0xd8 <= cx.b0 <= 0xdf)
def h_x87(cx, ins):
    cx.kinds.add('x87')
    if cx.modrm >= 0xc0:
        x87_reg(cx, ins)
    else:
        x87_mem(cx, ins)


def x87_reg(cx, ins):
    b0, modrm, em, m = cx.b0, cx.modrm, cx.em, cx.m
    i = modrm & 7
    g = (modrm >> 3) & 7
    if b0 == 0xd8 and g in AR:
        em(ar('ST(0)', 'ST(0)', 'ST(%d)' % i, AR[g]))
    elif b0 in (0xdc, 0xde) and g in AR:
        o = {0: '+', 1: '*', 4: '-r', 5: '-', 6: '/r', 7: '/'}[g]
        em(ar('ST(%d)' % i, 'ST(%d)' % i, 'ST(0)', o) + (POP if b0 == 0xde else ''))
    elif b0 == 0xd8 and g in (2, 3):
        em('FCOM(ST(0),ST(%d));%s' % (i, POP if g == 3 else ''))
    elif b0 == 0xde and modrm == 0xd9:
        em('FCOM(ST(0),ST(1)); FPOP(); FPOP();')
    elif b0 == 0xdd and g in (4, 5):
        em('FCOM(ST(0),ST(%d));%s' % (i, POP if g == 5 else ''))
    elif b0 == 0xda and modrm == 0xe9:
        em('FCOM(ST(0),ST(1)); FPOP(); FPOP();')
    elif b0 == 0xdb and g in (5, 6) or b0 == 0xdf and g in (5, 6):
        em('{ double x=ST(0), y=ST(%d); of=sf=af=0; if(x!=x||y!=y){zf=pf=cf=1;} else {zf=x==y; pf=0; cf=x<y;} }%s' % (i, POP if b0 == 0xdf else ''))
    elif b0 == 0xd9 and g == 0:
        em('{ double v=ST(%d); FPUSH(v); }' % i)
    elif b0 == 0xd9 and g == 1:
        em('{ double v=ST(0); ST(0)=ST(%d); ST(%d)=v; }' % (i, i))
    elif b0 == 0xdd and g == 2:
        em('ST(%d)=ST(0);' % i)
    elif b0 == 0xdd and g == 3:
        em('ST(%d)=ST(0); FPOP();' % i)
    elif b0 == 0xdd and g == 0:
        em('ftag&=~(1u<<((ftop+%d)&7));' % i)
    elif b0 == 0xd9 and modrm == 0xe0:
        em('ST(0)=-ST(0);')
    elif b0 == 0xd9 and modrm == 0xe1:
        em('ST(0)=__builtin_fabs(ST(0));')
    elif b0 == 0xd9 and modrm == 0xe4:
        em('FCOM(ST(0),0.0);')
    elif b0 == 0xd9 and modrm == 0xe8:
        em('FPUSH(1.0);')
    elif b0 == 0xd9 and modrm == 0xee:
        em('FPUSH(0.0);')
    elif b0 == 0xd9 and modrm == 0xfa:
        em('{ double x_=ST(0); ST(0)=FXN(__builtin_sqrt(x_),x_,x_); }')
    elif b0 == 0xd9 and modrm == 0xfc:
        em('ST(0)=fx_round(ST(0),(fcw>>10)&3);')
    elif b0 == 0xdf and modrm == 0xe0:
        em('eax=(eax&0xffff0000u)|FSW;')
    elif b0 == 0xdb and modrm in (0xe2, 0xe3):
        pass
    else:
        raise Unsupported('x87 ' + m)


def x87_mem(cx, ins):
    b0, modrm, em, m = cx.b0, cx.modrm, cx.em, cx.m
    g = (modrm >> 3) & 7
    A = cx.ea(ins, cx.ops[0])

    def src():
        if b0 == 0xd8:
            return '(double)MF32(%s)' % A
        if b0 == 0xdc:
            return 'MF64(%s)' % A
        if b0 == 0xda:
            return '(double)(s32)M32(%s)' % A
        return '(double)(s16)M16(%s)' % A

    if b0 in (0xd8, 0xdc, 0xda, 0xde) and g in AR:
        o = AR[g]
        x, y = ('ST(0)', src())
        if o.endswith('r'):
            x, y = y, x
            o = o[0]
        em('{ double x_=(%s), y_=(%s); ST(0)=FXN(x_%sy_,x_,y_); }' % (x, y, o))
    elif b0 in (0xd8, 0xdc, 0xda, 0xde) and g in (2, 3):
        em('FCOM(ST(0),%s);%s' % (src(), POP if g == 3 else ''))
    elif b0 == 0xd9 and g == 0:
        em('FPUSH((double)MF32(%s));' % A)
    elif b0 == 0xdd and g == 0:
        em('FPUSH(MF64(%s));' % A)
    elif b0 == 0xd9 and g in (2, 3):
        em('MF32(%s)=(float)ST(0);%s' % (A, POP if g == 3 else ''))
    elif b0 == 0xdd and g in (2, 3):
        em('MF64(%s)=ST(0);%s' % (A, POP if g == 3 else ''))
    elif b0 == 0xdb and g == 0:
        em('FPUSH((double)(s32)M32(%s));' % A)
    elif b0 == 0xdf and g == 0:
        em('FPUSH((double)(s16)M16(%s));' % A)
    elif b0 == 0xdf and g == 5:
        em('FPUSH((double)(s64)M64(%s));' % A)
    elif b0 == 0xdb and g in (2, 3):
        em('M32(%s)=fx_i32(ST(0),(fcw>>10)&3);%s' % (A, POP if g == 3 else ''))
    elif b0 == 0xdf and g in (2, 3):
        em('M16(%s)=fx_i16(ST(0),(fcw>>10)&3);%s' % (A, POP if g == 3 else ''))
    elif b0 == 0xdf and g == 7:
        em('M64(%s)=fx_i64(ST(0),(fcw>>10)&3); FPOP();' % A)
    elif b0 == 0xdb and g == 1:
        em('M32(%s)=fx_i32(ST(0),3); FPOP();' % A)
    elif b0 == 0xdd and g == 1:
        em('M64(%s)=fx_i64(ST(0),3); FPOP();' % A)
    elif b0 == 0xd9 and g == 5:
        em('fcw=M16(%s);' % A)
    elif b0 == 0xd9 and g == 7:
        em('M16(%s)=fcw;' % A)
    elif b0 == 0xdd and g == 7:
        em('M16(%s)=FSW;' % A)
    else:
        raise Unsupported('x87 ' + m)
