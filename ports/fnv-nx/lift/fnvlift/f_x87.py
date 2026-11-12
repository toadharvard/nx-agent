"""x87 (opcode bytes 0xd8..0xdf): data movement, arithmetic, compares (R8, W13). Stack model and control instructions: f_x87_ctl.py; the carrier
semantics (what each load/store/arithmetic does to the value) are in rt/fnvl_x87.h. The transcendental / remainder forms (fsin fcos fsincos fptan fpatan f2xm1 fyl2x
fyl2xp1 fscale fprem fprem1 fxtract) call rt/fmath.c: correctly rounded binary64 (R8, one reference CPU model); fsincos and fptan always use the dynamic stack because
their stack effect depends on the operand (C2 = 1: |x| >= 2^63, nothing pushed). fbld / fbstp (packed BCD, rt/x87.c) and fstpnce (D9 D8+i, the undocumented alias of fstp st(i)) are here;
fldenv / frstor / fnstenv / fnsave (dynamic stack only) are in f_x87_ctl.py. h_x87 is the one entry of every x87 opcode: it records FIP / FDP (ptrs) for the non-control ones."""
from capstone import x86 as X
from .core import fallback, op, Unsupported, X87Dyn
from .f_x87_ctl import ctl, x87_flaggy, fown_emit
from . import fpfacts

AR = {0: '+', 1: '*', 4: '-', 5: '-r', 6: '/', 7: '/r'}                    # d8 /g: st0 = st0 o src (r: src o st0)
ARD = {0: '+', 1: '*', 4: '-r', 5: '-', 6: '/r', 7: '/'}                   # dc/de reg forms: sti = sti o st0 (encodings E0/E8 and F0/F8 are swapped)
CONST = {0xe8: '0x3ff0000000000000ull', 0xe9: '0x400a934f0979a371ull', 0xea: '0x3ff71547652b82feull', 0xeb: '0x400921fb54442d18ull',
         0xec: '0x3fd34413509f79ffull', 0xed: '0x3fe62e42fefa39efull', 0xee: '0x0ull'}      # fld1 fldl2t fldl2e fldpi fldlg2 fldln2 fldz: the nearest double
INT_ST = {(0xdb, 1): (32, 1, 1), (0xdb, 2): (32, 0, 0), (0xdb, 3): (32, 0, 1), (0xdf, 1): (16, 1, 1), (0xdf, 2): (16, 0, 0), (0xdf, 3): (16, 0, 1),
          (0xdf, 7): (64, 0, 1), (0xdd, 1): (64, 1, 1)}       # (bits, truncate (fisttp), pop)
# d9 /reg forms served by rt/fmath.c: modrm -> mnemonic
FM = {0xf0: 'f2xm1', 0xf1: 'fyl2x', 0xf2: 'fptan', 0xf3: 'fpatan', 0xf4: 'fxtract', 0xf5: 'fprem1', 0xf8: 'fprem', 0xf9: 'fyl2xp1', 0xfb: 'fsincos', 0xfd: 'fscale',
      0xfe: 'fsin', 0xff: 'fcos'}


# ---- stack addressing: static = C locals s<j>, dynamic = c->fx through the runtime top ----
def rd(cx, i):
    if cx.xm == 's':
        if i >= cx.xdp:
            raise X87Dyn()
        return 's%d' % (cx.xdp - 1 - i)
    return 'dyn_rd(%d)' % i


def wr(cx, i):
    return rd(cx, i) if cx.xm == 's' else 'c->fx[(c->ftop+%d)&7]' % i


def push(cx, v):
    if cx.xm == 's':
        if cx.xdp >= 8:
            raise X87Dyn()
        return 's%d=%s;' % (cx.xdp, v)
    return 'dyn_push(%s);' % v


def pop(cx, n=1):
    return '' if cx.xm == 's' else ' dyn_pop();' * n


def arith(cx, dst, x, y, o):
    """st(dst) = x o y; x and y are C double expressions ('r' suffix: operands swapped)"""
    if o.endswith('r'):
        x, y, o = y, x, o[0]
    cx.em('{ double x_=(%s), y_=(%s); %s=fv_f64(FXN(x_%sy_,x_,y_)); }' % (x, y, wr(cx, dst), o))


def control(b0, mr):
    """the x87 control instructions that leave the instruction / data pointers alone (SDM Vol 1 8.1.8: FNINIT FNCLEX FLDCW FNSTCW FNSTSW FNSTENV FLDENV FNSAVE FRSTOR; FWAIT is its own handler)"""
    g = (mr >> 3) & 7
    if mr < 0xc0:
        return b0 == 0xd9 and g in (4, 5, 6, 7) or b0 == 0xdd and g in (4, 6, 7)
    return b0 == 0xdb and mr in (0xe2, 0xe3) or b0 == 0xdf and mr == 0xe0


def ptrs(cx, ins):
    """the x87 last-instruction state (rt/fnvl_x87.h): FIP = address of this instruction (first byte, prefixes included), FDP = offset of its memory operand (no segment base) when it has one.
    The one place for every non-control x87 instruction, static and dynamic stack alike."""
    cx.em('c->fip=0x%xu;' % ins.address)
    if cx.modrm < 0xc0:
        a = cx.ea(ins, cx.ops[0])
        if ins.operands[0].mem.segment == X.X86_REG_FS:                     # the offset, not fs base + offset
            a = '(u32)(0u)' if a == '(u32)(fsb)' else a.replace('(u32)(fsb+', '(u32)(', 1)
        cx.em('c->fdp=%s;' % a)


@fallback(lambda cx, ins: 0xd8 <= cx.b0 <= 0xdf)
def h_x87(cx, ins):
    cx.kinds.add('x87')
    g = (cx.modrm >> 3) & 7
    if not control(cx.b0, cx.modrm):
        ptrs(cx, ins)
    if x87_flaggy(ins):
        fown_emit(cx, 1)                                      # sticky flags: the FPSR belongs to the x87 from here (rt/fnvl_x87.h)
    if ctl(cx, ins, cx.b0, cx.modrm, g):
        return
    (x87_reg if cx.modrm >= 0xc0 else x87_mem)(cx, ins, cx.b0, cx.modrm, g)


@op('fwait', 'wait')
def h_fwait(cx, ins):
    """FWAIT (9B): raises #MF iff an UNMASKED x87 exception is pending (SDM Vol 1 8.6, Vol 2 FWAIT). Invariant ASM-305 (fnvlift/fpfacts.py, certified by tools/fpstate.py over every module): no
    exception is ever unmasked, so none is ever pending and the instruction has no effect. The lifter READS the fact file: where ASM-305 is not proven for the program being lifted, the
    form is Unsupported (a lift error), not a silent no-op."""
    inv = fpfacts.require('ASM-305', 'fwait')
    cx.kinds.add('fwait:' + inv)


@op('fnclex')
def h_fnclex(cx, ins):
    """FNCLEX (DB E2): clears the x87 exception flags, ES, B and the stack fault (SDM Vol 2). f_int.py registers `fnclex` as a NOP (h_nop): this later registration replaces it, so the
    instruction reaches the control-instruction table (ctl: FNCLEX, rt/fnvl_x87.h: the x87 sticky set := 0, the SSE set untouched)."""
    h_x87(cx, ins)


def x87_reg(cx, ins, b0, mr, g):
    em, i = cx.em, mr & 7
    R = lambda k: rd(cx, k)
    if b0 == 0xd8 and g in AR:
        arith(cx, 0, 'fv_d(%s)' % R(0), 'fv_d(%s)' % R(i), AR[g])
    elif b0 in (0xdc, 0xde) and g in ARD:
        arith(cx, i, 'fv_d(%s)' % R(i), 'fv_d(%s)' % R(0), ARD[g])
        em(pop(cx) if b0 == 0xde else '')
    elif b0 == 0xd8 and g in (2, 3) or b0 == 0xdd and g in (4, 5):                 # fcom fcomp / fucom fucomp st(i)
        em('FCMP(%s,%s);%s' % (R(0), R(i), pop(cx) if g in (3, 5) else ''))
    elif mr == 0xd9 and b0 == 0xde or mr == 0xe9 and b0 == 0xda:                  # fcompp / fucompp
        em('FCMP(%s,%s);%s' % (R(0), R(1), pop(cx, 2)))
    elif b0 in (0xdb, 0xdf) and g in (5, 6):                                       # fucomi fcomi (p)
        em('FCMPI(%s,%s);%s' % (R(0), R(i), pop(cx) if b0 == 0xdf else ''))
    elif b0 in (0xda, 0xdb) and g < 4:                                             # fcmovcc st0, sti
        cond = [('cf', 'zf', '(cf|zf)', 'pf'), ('!cf', '!zf', '!(cf|zf)', '!pf')][b0 == 0xdb][g]
        em('if(%s) %s=%s;' % (cond, wr(cx, 0), R(i)))
    elif b0 == 0xd9 and g == 0:                                                    # fld st(i)
        em(push(cx, R(i)))
    elif b0 == 0xd9 and g == 1:                                                    # fxch
        em('{ fv t_=%s; %s=%s; %s=t_; }' % (R(0), wr(cx, 0), R(i), wr(cx, i)))
    elif b0 == 0xdd and g in (2, 3) or b0 == 0xd9 and g == 3:                      # fst fstp st(i); D9 D8+i = fstpnce: the same operation as DD D8+i (alias)
        em('%s=%s;%s' % (wr(cx, i), R(0), pop(cx) if g == 3 else ''))
    elif b0 == 0xd9 and mr == 0xe0:
        em('%s=fv_sg(%s,1);' % (wr(cx, 0), R(0)))
    elif b0 == 0xd9 and mr == 0xe1:
        em('%s=fv_sg(%s,0);' % (wr(cx, 0), R(0)))
    elif b0 == 0xd9 and mr == 0xe4:                                                # ftst
        em('FCMP(%s,fv_f64(0.0));' % R(0))
    elif b0 == 0xd9 and mr == 0xe5:                                                # fxam
        em('FXAM(%s);' % ('c->fx[c->ftop&7]' if cx.xm == 'd' else R(0)))
    elif b0 == 0xd9 and mr in CONST:
        em(push(cx, 'fvmk(FK_F64,%s,0)' % CONST[mr]))
    elif b0 == 0xd9 and mr == 0xfa:                                                # fsqrt
        em('{ double x_=fv_d(%s); %s=fv_f64(FXN(__builtin_sqrt(x_),x_,x_)); }' % (R(0), wr(cx, 0)))
    elif b0 == 0xd9 and mr == 0xfc:                                                # frndint
        em('%s=fv_rnd(%s);' % (wr(cx, 0), R(0)))
    elif b0 == 0xd9 and mr in FM:
        fmath(cx, mr)
    else:
        raise Unsupported('x87 ' + cx.m)


def fmath(cx, mr):
    """fsin fcos fsincos fptan f2xm1 fyl2x fyl2xp1 fpatan fscale fxtract fprem fprem1: macros FM_* of rt/fnvl_x87.h (the same text on the static and the dynamic stack)"""
    em, R, W = cx.em, lambda k: rd(cx, k), lambda k: wr(cx, k)
    nm = FM[mr]
    if nm in ('fsin', 'fcos'):
        em('FM_TRIG(fnvl_m_%s,%s,%s);' % (nm[1:], W(0), R(0)))
    elif nm == 'f2xm1':
        em('FM_UN(fnvl_m_2xm1,%s,%s);' % (W(0), R(0)))
    elif nm in ('fyl2x', 'fyl2xp1', 'fpatan'):                                     # ST(1) = f(ST(1), ST(0)), pop
        em('FM_BIN(fnvl_m_%s,%s,%s,%s);%s' % ({'fyl2x': 'yl2x', 'fyl2xp1': 'yl2xp1', 'fpatan': 'atan2'}[nm], W(1), R(1), R(0), pop(cx)))
    elif nm == 'fscale':
        em('FM_BIN(fnvl_m_scale,%s,%s,%s);' % (W(0), R(0), R(1)))
    elif nm in ('fprem', 'fprem1'):
        em('FM_PREM(%s,%s,%s,%d);' % (W(0), R(0), R(1), nm == 'fprem1'))
    elif nm == 'fxtract':                                                          # ST0 = exponent, push the significand
        em('{ u32 f_=0; double e_, s_; fnvl_m_xtract(fv_d(%s),&e_,&s_,&f_); %s=fv_f64(e_); %s FM_FL(f_); }' % (R(0), W(0), push(cx, 'fv_f64(s_)')))
    else:                                                                          # fptan fsincos: push only when C2 = 0: data dependent depth
        if cx.xm == 's':
            raise X87Dyn()
        em('FM_TAN(%s,%s);' % (W(0), R(0)) if nm == 'fptan' else 'FM_SINCOS(%s,%s);' % (W(0), R(0)))


def x87_mem(cx, ins, b0, mr, g):
    em = cx.em
    A = cx.ea(ins, cx.ops[0])
    r0 = lambda: rd(cx, 0)
    a = '{ u32 a_=%s; ' % A
    if b0 in (0xd8, 0xdc, 0xda, 0xde):                                             # arithmetic and compares with m32 / m64 / m32int / m16int
        src = {0xd8: ('(double)MF32(a_)', 'fv_ld32(M32(a_))'), 0xdc: ('MF64(a_)', 'fv_ld64(M64(a_))'),
               0xda: ('(double)(s32)M32(a_)', 'fv_ldi(FK_I32,(s32)M32(a_))'), 0xde: ('(double)(s16)M16(a_)', 'fv_ldi(FK_I16,(s16)M16(a_))')}[b0]
        if g in AR:
            em(a + '')
            arith(cx, 0, 'fv_d(%s)' % r0(), src[0], AR[g])
            em('}')
        elif g in (2, 3):
            em(a + 'FCMP(%s,%s); }%s' % (r0(), src[1], pop(cx) if g == 3 else ''))
        else:
            raise Unsupported('x87 ' + cx.m)
        return
    if b0 == 0xd9 and g == 0:
        em(a + push(cx, 'fv_ld32(M32(a_))') + ' }')
    elif b0 == 0xdd and g == 0:
        em(a + push(cx, 'fv_ld64(M64(a_))') + ' }')
    elif b0 == 0xdb and g == 0:
        em(a + push(cx, 'fv_ldi(FK_I32,(s32)M32(a_))') + ' }')
    elif b0 == 0xdf and g == 0:
        em(a + push(cx, 'fv_ldi(FK_I16,(s16)M16(a_))') + ' }')
    elif b0 == 0xdf and g == 5:
        em(a + push(cx, 'fv_ldi(FK_I64,(s64)M64(a_))') + ' }')
    elif b0 == 0xdb and g == 5:
        em(a + push(cx, 'fv_ld80(M64(a_),M16(a_+8u))') + ' }')
    elif b0 == 0xd9 and g in (2, 3):
        em(a + 'M32(a_)=fv_s32(%s); }%s' % (r0(), pop(cx) if g == 3 else ''))
    elif b0 == 0xdd and g in (2, 3):
        em(a + 'M64(a_)=fv_s64(%s); }%s' % (r0(), pop(cx) if g == 3 else ''))
    elif b0 == 0xdb and g == 7:
        em(a + 'fnvl_f80 t_=fv_x(%s); M64(a_)=t_.m; M16(a_+8u)=t_.se; }%s' % (r0(), pop(cx)))
    elif b0 == 0xdf and g == 4:                                                    # fbld m80: packed BCD -> ST(0) (rt/x87.c fnvl_x_from_bcd: ASM-317)
        em(a + push(cx, 'fv_ldbcd(M64(a_),M16(a_+8u))') + ' }')
    elif b0 == 0xdf and g == 6:                                                    # fbstp m80: round per RC, 18 digits, indefinite on NaN / inf / out of range; pop
        em(a + 'u64 lo_, hi_; fv_stbcd(%s,&lo_,&hi_); M64(a_)=lo_; M16(a_+8u)=(u16)hi_; }%s' % (r0(), pop(cx)))
    elif (b0, g) in INT_ST:
        bits, tt, pp = INT_ST[(b0, g)]
        em(a + 'M%d(a_)=(u%d)%s(%s,%d); }%s' % (bits, bits, 'FISTT' if tt else 'FIST', r0(), bits, pop(cx) if pp else ''))
    else:
        raise Unsupported('x87 ' + cx.m)
