"""Integer instruction family: moves, ALU, shifts, multiply/divide, stack, setcc/cmovcc, bit tests."""
from capstone import x86 as X
from .core import op, SKIP, Unsupported, MASK, CC, reg


SEGSEL = {'cs': 'SEL_CS', 'ss': 'SEL_DS', 'ds': 'SEL_DS', 'es': 'SEL_DS', 'fs': 'SEL_FS', 'gs': '0u'}    # initial selectors of a Win32 x86 user process (fnvl_seg_init; GS is null); the current values live in c->sel (SEG_STATE)


SEG_STATE = True       # struct cpu holds the six selector values: `mov r, Sreg` / `push Sreg` read the state (rt/fnvl.h fnvl_seg_get) instead of the initial constants above


def segread(sg):
    return 'fnvl_seg_get(c,%d)' % {'es': 0, 'cs': 1, 'ss': 2, 'ds': 3, 'fs': 4, 'gs': 5}[sg] if SEG_STATE else SEGSEL[sg]


def segname(ins, o):
    """the segment register name of a register operand, else None"""
    if o.type == X.X86_OP_REG:
        n = ins.reg_name(o.reg)
        if n in SEGSEL:
            return n
    return None


@op('mov')
def h_mov(cx, ins):
    ops = cx.ops
    from . import f_sys
    sg = segname(ins, ops[0])
    if sg:      # mov Sreg, r/m16: f_sys (class 3)
        f_sys.mov_to_sreg(cx, ins, sg)
        return
    if any(o.type == X.X86_OP_REG and ins.reg_name(o.reg)[:2] in ('cr', 'dr') for o in ops):      # mov to/from CRn / DRn: CPL 0 only (0F 20..23)
        f_sys.gp0(cx, ins)
        return
    sg = segname(ins, ops[1])
    if sg:      # mov r/m16, Sreg (RtlCaptureContext style): the fixed user-mode selector; a 32-bit register destination gets it zero-extended
        cx.em(cx.wr(ins, ops[0], segread(sg)))
        return
    cx.em(cx.wr(ins, ops[0], cx.rd(ins, ops[1], ops[0].size)))


@op('lea')
def h_lea(cx, ins):
    cx.em(cx.wr(ins, cx.ops[0], cx.ea(ins, cx.ops[1])))


@op('movzx')
def h_movzx(cx, ins):
    cx.em(cx.wr(ins, cx.ops[0], cx.rd(ins, cx.ops[1])))


@op('movsx')
def h_movsx(cx, ins):
    ops = cx.ops
    cx.em(cx.wr(ins, ops[0], '(u32)(s32)(s%d)%s' % (ops[1].size * 8, cx.rd(ins, ops[1]))))


@op('add', 'adc', 'sub', 'sbb', 'cmp')
def h_arith(cx, ins):
    m, ops, s, em = cx.m, cx.ops, cx.s, cx.em
    em('{ u32 a=%s, b=%s, r; u64 t;' % (cx.rd(ins, ops[0]), cx.rd(ins, ops[1], ops[0].size)))
    M = MASK[s]
    if m == 'add':
        em('r=(a+b)&%s; cf=r<a; of=(((a^r)&(b^r))>>%d)&1;' % (M, s - 1))
    elif m == 'adc':
        em('t=(u64)a+b+cf; r=(u32)t&%s; of=(((a^r)&(b^r))>>%d)&1; cf=(t>>%d)&1;' % (M, s - 1, s))
    elif m in ('sub', 'cmp'):
        em('r=(a-b)&%s; cf=a<b; of=(((a^b)&(a^r))>>%d)&1;' % (M, s - 1))
    else:
        em('t=(u64)a-(u64)b-cf; r=(u32)t&%s; of=(((a^b)&(a^r))>>%d)&1; cf=(t>>%d)&1;' % (M, s - 1, s))
    em('af=((a^b^r)>>4)&1; ' + cx.zsp(s))
    if m != 'cmp':
        em(cx.wr(ins, ops[0], 'r'))
    em('}')


@op('and', 'or', 'xor', 'test')
def h_logic(cx, ins):
    m, ops, s, em = cx.m, cx.ops, cx.s, cx.em
    o = {'and': '&', 'test': '&', 'or': '|', 'xor': '^'}[m]
    em('{ u32 r=(%s)%s(%s); cf=of=af=0; %s' % (cx.rd(ins, ops[0]), o, cx.rd(ins, ops[1], ops[0].size), cx.zsp(s)))
    if m != 'test':
        em(cx.wr(ins, ops[0], 'r'))
    em('}')


@op('inc', 'dec')
def h_incdec(cx, ins):
    m, ops, s = cx.m, cx.ops, cx.s
    o = '+' if m == 'inc' else '-'
    cx.em('{ u32 a=%s, r=(a%s1)&%s; of=%s; af=((a^1^r)>>4)&1; %s %s }' % (
        cx.rd(ins, ops[0]), o, MASK[s],
        ('r==0x%xu' % (1 << (s - 1))) if m == 'inc' else ('a==0x%xu' % (1 << (s - 1))),
        cx.zsp(s), cx.wr(ins, ops[0], 'r')))


@op('neg')
def h_neg(cx, ins):
    ops, s = cx.ops, cx.s
    cx.em('{ u32 a=%s, r=(0-a)&%s; cf=a!=0; of=a==0x%xu; af=((a^r)>>4)&1; %s %s }' % (
        cx.rd(ins, ops[0]), MASK[s], 1 << (s - 1), cx.zsp(s), cx.wr(ins, ops[0], 'r')))


@op('not')
def h_not(cx, ins):
    cx.em(cx.wr(ins, cx.ops[0], '~' + cx.rd(ins, cx.ops[0])))


def shcount(cx, ins, ops):
    """the count operand of a shift/rotate: capstone reports the implicit 1 of `rcl/rol [mem]` (d1 /r) as an immediate of size 0, which rd() would mask to 0"""
    if len(ops) < 2:
        return '1'
    if ops[1].type == X.X86_OP_IMM:
        return '0x%xu' % (ops[1].imm & 0xff)
    return cx.rd(ins, ops[1])


@op('shl', 'sal', 'shr', 'sar')
def h_shift(cx, ins):
    m, ops, s, em = cx.m, cx.ops, cx.s, cx.em
    cnt = shcount(cx, ins, ops)
    em('{ u32 a=%s, n=(%s)&31, r, p; if(n){' % (cx.rd(ins, ops[0]), cnt))
    if m in ('shl', 'sal'):
        em('p=a<<(n-1); r=(p<<1)&%s; cf=(p>>%d)&1; of=((p^r)>>%d)&1;' % (MASK[s], s - 1, s - 1))
    elif m == 'shr':
        em('p=a>>(n-1); r=p>>1; cf=p&1; of=((p^r)>>%d)&1;' % (s - 1))
    else:
        em('s32 sa=(s%d)a; p=(u32)(sa>>(n-1)); r=(u32)(sa>>n)&%s; cf=p&1; of=0;' % (s, MASK[s]))
    em('af=0; %s %s }}' % (cx.zsp(s), cx.wr(ins, ops[0], 'r')))


@op('rol', 'ror')
def h_rotate(cx, ins):
    m, ops, s, em = cx.m, cx.ops, cx.s, cx.em
    cnt = shcount(cx, ins, ops)
    em('{ u32 a=%s, n=(%s)&31, k=n%%%d, r=a; if(n){ if(k) r=' % (cx.rd(ins, ops[0]), cnt, s))
    if m == 'rol':
        em('((a<<k)|(a>>(%d-k)))&%s; cf=r&1; of=((r>>%d)&1)^cf;' % (s, MASK[s], s - 1))
    else:
        em('((a>>k)|(a<<(%d-k)))&%s; cf=(r>>%d)&1; of=((r>>%d)^(r>>%d))&1;' % (s, MASK[s], s - 1, s - 1, s - 2))
    em(cx.wr(ins, ops[0], 'r') + ' }}')


@op('rcl', 'rcr')
def h_rcx(cx, ins):
    """rotate through carry: count masked to 5 bits, then modulo width+1 for 8/16 bits; the (width+1)-bit value cf:a is rotated; CF/OF per SDM (OF only for count 1)"""
    m, ops, s, em = cx.m, cx.ops, cx.s, cx.em
    cnt = shcount(cx, ins, ops)
    em('{ u32 a=%s, n5=(%s)&31, n=n5%%%d; if(n){ u64 v=((u64)cf<<%d)|a; u32 r;' % (cx.rd(ins, ops[0]), cnt, s + 1, s))
    if m == 'rcl':
        em('v=((v<<n)|(v>>(%d-n)))&0x%xull; r=(u32)v&%s; cf=(v>>%d)&1; of=((r>>%d)&1)^cf;' % (s + 1, (1 << (s + 1)) - 1, MASK[s], s, s - 1))
    else:
        em('v=((v>>n)|(v<<(%d-n)))&0x%xull; r=(u32)v&%s; cf=(v>>%d)&1; of=((r>>%d)^(r>>%d))&1;' % (s + 1, (1 << (s + 1)) - 1, MASK[s], s, s - 1, s - 2))
    em(cx.wr(ins, ops[0], 'r') + ' }}')


@op('shld', 'shrd')
def h_shd(cx, ins):
    m, ops, em = cx.m, cx.ops, cx.em
    if cx.s == 16:
        # count > 16 (result undefined in the SDM): the 48-bit string a:b:a is shifted, which equals the SDM result for counts <= 16
        em('{ u32 a=%s, b=%s, n=(%s)&31, r; if(n){ u64 X=((u64)a<<32)|((u64)b<<16)|a;' % (cx.rd(ins, ops[0]), cx.rd(ins, ops[1]), cx.rd(ins, ops[2])))
        if m == 'shld':
            em('r=(u32)((X<<n)>>32)&0xffffu; cf=(X>>(48-n))&1; of=((a^r)>>15)&1;')
        else:
            em('r=(u32)(X>>n)&0xffffu; cf=(X>>(n-1))&1; of=((a^r)>>15)&1;')
        em('af=0; %s %s }}' % (cx.zsp(16), cx.wr(ins, ops[0], 'r')))
        return
    if cx.s != 32:
        raise Unsupported(m + str(cx.s))
    em('{ u32 a=%s, b=%s, n=(%s)&31, r; if(n){' % (cx.rd(ins, ops[0]), cx.rd(ins, ops[1]), cx.rd(ins, ops[2])))
    if m == 'shld':
        em('r=(a<<n)|(b>>(32-n)); cf=(a>>(32-n))&1; of=((a^r)>>31)&1;')
    else:
        em('r=(a>>n)|(b<<(32-n)); cf=(a>>(n-1))&1; of=((a^r)>>31)&1;')
    em('af=0; %s %s }}' % (cx.zsp(32), cx.wr(ins, ops[0], 'r')))


@op('imul')
def h_imul(cx, ins):
    ops, s, em = cx.ops, cx.s, cx.em
    if len(ops) == 1:
        if s == 32:
            em('{ s64 t=(s64)(s32)eax*(s64)(s32)%s; eax=(u32)t; edx=(u32)(t>>32); cf=of=(t!=(s64)(s32)eax); u32 r=eax; af=0; %s }' % (cx.rd(ins, ops[0]), cx.zsp(32)))
        elif s == 16:
            em('{ s32 t=(s32)(s16)eax*(s32)(s16)%s; eax=(eax&0xffff0000u)|((u32)t&0xffffu); edx=(edx&0xffff0000u)|(((u32)t>>16)&0xffffu); cf=of=(t!=(s32)(s16)t); u32 r=(u32)t&0xffffu; af=0; %s }' % (cx.rd(ins, ops[0]), cx.zsp(16)))
        else:
            em('{ s32 t=(s32)(s8)eax*(s32)(s8)%s; eax=(eax&0xffff0000u)|((u32)t&0xffffu); cf=of=(t!=(s32)(s8)t); u32 r=(u32)t&0xffu; af=0; %s }' % (cx.rd(ins, ops[0]), cx.zsp(8)))
    else:
        a_ = cx.rd(ins, ops[1] if len(ops) == 3 else ops[0], ops[0].size)
        b_ = cx.rd(ins, ops[2] if len(ops) == 3 else ops[1], ops[0].size)
        em('{ s64 t=(s64)(s%d)%s*(s64)(s%d)%s; u32 r=(u32)t&%s; cf=of=(t!=(s64)(s%d)r); af=0; %s %s }' % (
            s, a_, s, b_, MASK[s], s, cx.zsp(s), cx.wr(ins, ops[0], 'r')))


@op('mul')
def h_mul(cx, ins):
    if cx.s == 16:
        cx.em('{ u32 t=(eax&0xffffu)*%s; eax=(eax&0xffff0000u)|(t&0xffffu); edx=(edx&0xffff0000u)|(t>>16); cf=of=(t>>16)!=0; u32 r=t&0xffffu; af=0; %s }' % (cx.rd(ins, cx.ops[0]), cx.zsp(16)))
        return
    if cx.s == 8:
        cx.em('{ u32 t=(eax&0xffu)*%s; eax=(eax&0xffff0000u)|t; cf=of=(t>>8)!=0; u32 r=t&0xffu; af=0; %s }' % (cx.rd(ins, cx.ops[0]), cx.zsp(8)))
        return
    cx.em('{ u64 t=(u64)eax*(u64)%s; eax=(u32)t; edx=(u32)(t>>32); cf=of=edx!=0; u32 r=eax; af=0; %s }' % (cx.rd(ins, cx.ops[0]), cx.zsp(32)))


@op('div', 'idiv')
def h_div(cx, ins):
    m, a = cx.m, cx.a
    cx.nonlocal_ = True; cx.raises.add(a)
    if cx.s != 32:
        s = cx.s
        rz = 'SYNC_OUT; REGS(); FNVL_RAISE(d==0?FNVL_DE0:FNVL_DEOVF,0x%xu); REGS(); SYNC_IN; goto nonlocal;' % a
        if s == 16:
            if m == 'div':
                cx.em('{ u32 d=%s; u32 n=((edx&0xffffu)<<16)|(eax&0xffffu); if(d==0||n/d>0xffffu){ %s } eax=(eax&0xffff0000u)|(n/d); edx=(edx&0xffff0000u)|(n%%d); }' % (cx.rd(ins, cx.ops[0]), rz))
            else:
                cx.em('{ s32 d=(s16)%s; s32 n=(s32)(((edx&0xffffu)<<16)|(eax&0xffffu)); if(d==0||(n==(s32)0x80000000u&&d==-1)||n/d>0x7fff||n/d<-0x8000){ %s } eax=(eax&0xffff0000u)|((u32)(n/d)&0xffffu); edx=(edx&0xffff0000u)|((u32)(n%%d)&0xffffu); }' % (cx.rd(ins, cx.ops[0]), rz))
        else:
            if m == 'div':
                cx.em('{ u32 d=%s; u32 n=eax&0xffffu; if(d==0||n/d>0xffu){ %s } eax=(eax&0xffff0000u)|((n%%d)<<8)|(n/d); }' % (cx.rd(ins, cx.ops[0]), rz))
            else:
                cx.em('{ s32 d=(s8)%s; s32 n=(s16)eax; if(d==0||n/d>0x7f||n/d<-0x80){ %s } eax=(eax&0xffff0000u)|(((u32)(n%%d)&0xffu)<<8)|((u32)(n/d)&0xffu); }' % (cx.rd(ins, cx.ops[0]), rz))
        return
    if m == 'div':
        cx.em('{ u32 d=%s; u64 n=((u64)edx<<32)|eax; if(d==0||n/d>0xffffffffull){ SYNC_OUT; REGS(); FNVL_RAISE(d==0?FNVL_DE0:FNVL_DEOVF,0x%xu); REGS(); SYNC_IN; goto nonlocal; } eax=(u32)(n/d); edx=(u32)(n%%d); }' % (cx.rd(ins, cx.ops[0]), a))
    else:
        cx.em('{ s32 d=(s32)%s; s64 n=(s64)(((u64)edx<<32)|eax); if(d==0||(n==(s64)0x8000000000000000ull&&d==-1)||n/d>0x7fffffffll||n/d<-0x80000000ll){ SYNC_OUT; REGS(); FNVL_RAISE(d==0?FNVL_DE0:FNVL_DEOVF,0x%xu); REGS(); SYNC_IN; goto nonlocal; } eax=(u32)(n/d); edx=(u32)(n%%d); }' % (cx.rd(ins, cx.ops[0]), a))


@op('cdq')
def h_cdq(cx, ins):
    cx.em('edx=(u32)((s32)eax>>31);')


@op('cwde')
def h_cwde(cx, ins):
    cx.em('eax=(u32)(s32)(s16)eax;')


@op('cwd')
def h_cwd(cx, ins):
    cx.em('edx=(edx&0xffff0000u)|((eax&0x8000u)?0xffffu:0u);')


@op('cbw')
def h_cbw(cx, ins):
    cx.em('eax=(eax&0xffff0000u)|(u16)(s16)(s8)eax;')


@op('xchg')
def h_xchg(cx, ins):
    ops = cx.ops
    cx.em('{ u32 t0=%s, t1=%s; %s %s }' % (cx.rd(ins, ops[0]), cx.rd(ins, ops[1]), cx.wr(ins, ops[0], 't1'), cx.wr(ins, ops[1], 't0')))


@op('nop', 'wait', 'fnclex', 'fwait')
def h_nop(cx, ins):
    pass


@op('push')
def h_push(cx, ins):
    sg = segname(ins, cx.ops[0])
    if sg:      # push Sreg: 32-bit operand size pushes the selector zero-extended; the 66 prefix (16-bit operand size) pushes the word
        if ins.prefix[2] == 0x66:
            cx.em('{ esp-=2; M16(esp)=(u16)(%s); }' % segread(sg))
        else:
            cx.em('{ esp-=4; M32(esp)=%s; }' % segread(sg))
        return
    if cx.s == 16:
        cx.em('{ u32 v=%s; esp-=2; M16(esp)=(u16)v; }' % cx.rd(ins, cx.ops[0]))
        return
    cx.em('{ u32 v=%s; esp-=4; M32(esp)=v; }' % cx.rd(ins, cx.ops[0]))


@op('pop')
def h_pop(cx, ins):
    sg = segname(ins, cx.ops[0])
    if sg:      # pop Sreg: f_sys (class 3)
        from . import f_sys
        f_sys.pop_sreg(cx, ins, sg)
        return
    if cx.s == 16:
        cx.em('{ u32 v=M16(esp); esp+=2; %s }' % cx.wr(ins, cx.ops[0], 'v'))
        return
    cx.em('{ u32 v=M32(esp); esp+=4; %s }' % cx.wr(ins, cx.ops[0], 'v'))


@op('leave')
def h_leave(cx, ins):
    if ins.prefix[2] == 0x66:       # 16-bit operand size (stack size 32): ESP = EBP; BP = pop16, the upper half of EBP is kept (SDM LEAVE)
        cx.em('esp=ebp; ebp=(ebp&0xffff0000u)|M16(esp); esp+=2;')
        return
    cx.em('esp=ebp; ebp=M32(esp); esp+=4;')


@op(*['set' + c for c in CC])
def h_setcc(cx, ins):
    cx.em(cx.wr(ins, cx.ops[0], '(u32)(%s?1:0)' % CC[cx.m[3:]]))


@op(*['cmov' + c for c in CC])
def h_cmovcc(cx, ins):
    ops = cx.ops
    cx.em('if(%s){ %s }' % (CC[cx.m[4:]], cx.wr(ins, ops[0], cx.rd(ins, ops[1]))))


def bitstr(cx, ins, body):
    """bt/bts/btr/btc [mem], reg: bit-string addressing. The (signed) index selects the operand-size unit at ea + (idx >> log2(bits))*bytes, the bit inside it is idx mod bits.
    body: C text over a (the unit), b (its bit mask), writing r (new unit) -> cf set, store when r differs by operation"""
    ops, s = cx.ops, cx.s
    sh = {16: 4, 32: 5}[s]
    cx.em('{ s32 i=(s32)(s%d)%s; u32 ad=%s+(u32)((i>>%d)*%d); u32 a=%s%d(ad), b=1u<<(i&%d), r; cf=(a&b)!=0; %s' % (s, cx.rd(ins, ops[1]), cx.ea(ins, ops[0]), sh, s // 8, cx.macc, s, s - 1, body))


@op('bt')
def h_bt(cx, ins):
    ops = cx.ops
    if not (ops[0].type == X.X86_OP_REG or ops[1].type == X.X86_OP_IMM):
        bitstr(cx, ins, 'r=a; (void)r; }')
        return
    cx.em('cf=((%s)>>((%s)&%d))&1;' % (cx.rd(ins, ops[0]), cx.rd(ins, ops[1], ops[0].size), cx.s - 1))


@op('bts', 'btr', 'btc')
def h_btx(cx, ins):
    m, ops = cx.m, cx.ops
    o = {'bts': 'a|b', 'btr': 'a&~b', 'btc': 'a^b'}[m]
    if not (ops[0].type == X.X86_OP_REG or ops[1].type == X.X86_OP_IMM):
        bitstr(cx, ins, 'r=%s; %s%d(ad)=r; }' % (o, cx.macc, cx.s))
        return
    cx.em('{ u32 a=%s, b=1u<<((%s)&%d); cf=(a&b)!=0; %s }' % (
        cx.rd(ins, ops[0]), cx.rd(ins, ops[1], ops[0].size), cx.s - 1, cx.wr(ins, ops[0], o)))


@op('bswap')
def h_bswap(cx, ins):
    r = reg(ins.reg_name(cx.ops[0].reg))[0]
    cx.em('%s=__builtin_bswap32(%s);' % (r, r))


@op('cld', 'std')
def h_df(cx, ins):
    cx.em('df=%d;' % (cx.m == 'std'))


@op('sahf')
def h_sahf(cx, ins):
    cx.em('{ u32 h=eax>>8; cf=h&1; pf=(h>>2)&1; af=(h>>4)&1; zf=(h>>6)&1; sf=(h>>7)&1; }')


@op('lahf')
def h_lahf(cx, ins):
    cx.em('eax=(eax&~0xff00u)|((((sf&1)<<7)|((zf&1)<<6)|((af&1)<<4)|((pf&1)<<2)|2u|(cf&1))<<8);')


@op('cmc', 'clc', 'stc')
def h_cf(cx, ins):
    cx.em({'cmc': 'cf=!cf;', 'clc': 'cf=0;', 'stc': 'cf=1;'}[cx.m])


@op('salc')
def h_salc(cx, ins):
    cx.em('eax=(eax&~0xffu)|(cf?0xffu:0u);')


@op('xlatb')
def h_xlatb(cx, ins):
    if ins.prefix[3] == 0x67:       # 16-bit address size: [BX + AL], the sum wraps in 16 bits (SDM XLAT: "uses BX if the address-size attribute is 16")
        cx.em('eax=(eax&~0xffu)|M8(((ebx&0xffffu)+(eax&0xffu))&0xffffu);')
        return
    cx.em('eax=(eax&~0xffu)|M8(ebx+(eax&0xffu));')


@op('xadd')
def h_xadd(cx, ins):
    ops, s, em = cx.ops, cx.s, cx.em
    M = MASK[s]
    em('{ u32 a=%s, b=%s, r=(a+b)&%s; cf=r<a; of=(((a^r)&(b^r))>>%d)&1; af=((a^b^r)>>4)&1; %s' % (cx.rd(ins, ops[0]), cx.rd(ins, ops[1]), M, s - 1, cx.zsp(s)))
    em(cx.wr(ins, ops[1], 'a') + ' ' + cx.wr(ins, ops[0], 'r') + ' }')      # the register operand gets the old destination first: with the same register the sum wins


@op('cmpxchg')
def h_cmpxchg(cx, ins):
    ops, s, em = cx.ops, cx.s, cx.em
    M = MASK[s]
    acc = {8: '(eax&0xffu)', 16: '(eax&0xffffu)', 32: 'eax'}[s]
    wacc = {8: 'eax=(eax&~0xffu)|d;', 16: 'eax=(eax&~0xffffu)|d;', 32: 'eax=d;'}[s]
    em('{ u32 a=%s, d=%s, r=(a-d)&%s; cf=a<d; of=(((a^d)&(a^r))>>%d)&1; af=((a^d^r)>>4)&1; %s' % (acc, cx.rd(ins, ops[0]), M, s - 1, cx.zsp(s)))
    em('if(a==d){ %s } else { %s %s } }' % (cx.wr(ins, ops[0], cx.rd(ins, ops[1])), wacc, cx.wr(ins, ops[0], 'd')))


@op('cmpxchg8b')
def h_cmpxchg8b(cx, ins):
    ea = cx.ea(ins, cx.ops[0])
    cx.em('{ u32 ad=%s; u64 d=%s64(ad); if(d==(((u64)edx<<32)|eax)){ zf=1; %s64(ad)=((u64)ecx<<32)|ebx; } else { zf=0; eax=(u32)d; edx=(u32)(d>>32); %s64(ad)=d; } }' % (ea, cx.macc, cx.macc, cx.macc))


@op('bsf', 'bsr')
def h_bscan(cx, ins):
    """source 0: ZF=1 and the destination is left unchanged (Intel hardware; the SDM calls it undefined); else ZF=0 and the index; CF OF SF AF PF undefined (kept)"""
    ops = cx.ops
    idx = '__builtin_ctz(a)' if cx.m == 'bsf' else '(31-__builtin_clz(a))'
    cx.em('{ u32 a=%s; if(!a) zf=1; else { zf=0; %s } }' % (cx.rd(ins, ops[1]), cx.wr(ins, ops[0], idx)))


@op('aaa', 'aas')
def h_aaa(cx, ins):
    if cx.m == 'aaa':
        cx.em('{ if((eax&0xfu)>9||af){ eax=(eax&0xffff0000u)|((eax+0x106u)&0xffffu); af=cf=1; } else af=cf=0; eax&=~0xf0u; { u32 r=eax&0xffu; zf=r==0; sf=(r>>7)&1; pf=PAR(r); of=0; } }')
    else:
        cx.em('{ if((eax&0xfu)>9||af){ eax=(eax&0xffff0000u)|((eax-0x106u)&0xffffu); af=cf=1; } else af=cf=0; eax&=~0xf0u; { u32 r=eax&0xffu; zf=r==0; sf=(r>>7)&1; pf=PAR(r); of=0; } }')


@op('daa', 'das')
def h_daa(cx, ins):
    if cx.m == 'daa':
        cx.em('{ u32 al=eax&0xffu, ol=al, oc=cf; cf=0; if((al&0xfu)>9||af){ al=al+6; cf=oc||(al>0xffu); af=1; } else af=0; al&=0xffu; if(ol>0x99u||oc){ al=(al+0x60u)&0xffu; cf=1; } else cf=0; eax=(eax&~0xffu)|al; zf=al==0; sf=(al>>7)&1; pf=PAR(al); of=0; }')
    else:
        cx.em('{ u32 al=eax&0xffu, ol=al, oc=cf; cf=0; if((al&0xfu)>9||af){ cf=oc||(al<6); al=(al-6)&0xffu; af=1; } else af=0; if(ol>0x99u||oc){ al=(al-0x60u)&0xffu; cf=1; } eax=(eax&~0xffu)|al; zf=al==0; sf=(al>>7)&1; pf=PAR(al); of=0; }')


@op('aam', 'aad')
def h_aam(cx, ins):
    imm = ins.bytes[1]
    if cx.m == 'aad':
        cx.em('{ u32 al=((eax&0xffu)+((eax>>8)&0xffu)*0x%xu)&0xffu; eax=(eax&0xffff0000u)|al; zf=al==0; sf=(al>>7)&1; pf=PAR(al); cf=of=af=0; }' % imm)
        return
    if imm == 0:
        cx.nonlocal_ = True
        cx.raises.add(cx.a)
        cx.em('SYNC_OUT; REGS(); FNVL_RAISE(FNVL_DE0,0x%xu); REGS(); SYNC_IN; goto nonlocal;' % cx.a)
        cx.term = True
        return
    cx.em('{ u32 al=eax&0xffu; u32 q=al/0x%xu, rm=al%%0x%xu; eax=(eax&0xffff0000u)|(q<<8)|rm; zf=rm==0; sf=(rm>>7)&1; pf=PAR(rm); cf=of=af=0; }' % (imm, imm))


@op('enter')
def h_enter(cx, ins):
    size, lvl = cx.ops[0].imm & 0xffff, cx.ops[1].imm & 31
    if ins.prefix[2] == 0x66:       # 16-bit operand size, 32-bit stack: words are pushed, EBP (32-bit) walks the frames and receives the whole frame pointer (SDM ENTER: "IF StackSize = 32 THEN EBP := FrameTemp")
        cx.em('{ u32 fp; esp-=2; M16(esp)=(u16)ebp; fp=esp;')
        for _ in range(lvl - 1):
            cx.em('ebp-=2; esp-=2; M16(esp)=M16(ebp);')
        if lvl:
            cx.em('esp-=2; M16(esp)=(u16)fp;')
        cx.em('ebp=fp; esp-=0x%xu; }' % size)
        return
    cx.em('{ u32 fp; esp-=4; M32(esp)=ebp; fp=esp;')
    for _ in range(lvl - 1):
        cx.em('ebp-=4; esp-=4; M32(esp)=M32(ebp);')
    if lvl:
        cx.em('esp-=4; M32(esp)=fp;')
    cx.em('ebp=fp; esp-=0x%xu; }' % size)
