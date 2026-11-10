"""Integer instruction family: moves, ALU, shifts, multiply/divide, stack, setcc/cmovcc, bit tests."""
from capstone import x86 as X
from .core import op, SKIP, Unsupported, MASK, CC, reg


@op('mov')
def h_mov(cx, ins):
    ops = cx.ops
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


@op('shl', 'sal', 'shr', 'sar')
def h_shift(cx, ins):
    m, ops, s, em = cx.m, cx.ops, cx.s, cx.em
    cnt = cx.rd(ins, ops[1]) if len(ops) > 1 else '1'
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
    cnt = cx.rd(ins, ops[1]) if len(ops) > 1 else '1'
    em('{ u32 a=%s, n=(%s)&31, k=n%%%d, r=a; if(n){ if(k) r=' % (cx.rd(ins, ops[0]), cnt, s))
    if m == 'rol':
        em('((a<<k)|(a>>(%d-k)))&%s; cf=r&1; of=((r>>%d)&1)^cf;' % (s, MASK[s], s - 1))
    else:
        em('((a>>k)|(a<<(%d-k)))&%s; cf=(r>>%d)&1; of=((r>>%d)^(r>>%d))&1;' % (s, MASK[s], s - 1, s - 1, s - 2))
    em(cx.wr(ins, ops[0], 'r') + ' }}')


@op('shld', 'shrd')
def h_shd(cx, ins):
    m, ops, em = cx.m, cx.ops, cx.em
    if cx.s != 32:
        raise Unsupported(m + '16')
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
        else:
            raise Unsupported('imul r/m%d' % s)
    else:
        a_ = cx.rd(ins, ops[1] if len(ops) == 3 else ops[0], ops[0].size)
        b_ = cx.rd(ins, ops[2] if len(ops) == 3 else ops[1], ops[0].size)
        em('{ s64 t=(s64)(s%d)%s*(s64)(s%d)%s; u32 r=(u32)t&%s; cf=of=(t!=(s64)(s%d)r); af=0; %s %s }' % (
            s, a_, s, b_, MASK[s], s, cx.zsp(s), cx.wr(ins, ops[0], 'r')))


@op('mul')
def h_mul(cx, ins):
    if cx.s != 32:
        raise Unsupported('mul r/m%d' % cx.s)
    cx.em('{ u64 t=(u64)eax*(u64)%s; eax=(u32)t; edx=(u32)(t>>32); cf=of=edx!=0; u32 r=eax; af=0; %s }' % (cx.rd(ins, cx.ops[0]), cx.zsp(32)))


@op('div', 'idiv')
def h_div(cx, ins):
    m, a = cx.m, cx.a
    if cx.s != 32:
        raise Unsupported(m + ' r/m%d' % cx.s)
    if m == 'div':
        cx.em('{ u32 d=%s; u64 n=((u64)edx<<32)|eax; if(d==0||n/d>0xffffffffull){SYNC_OUT; fnvl_raise(1,0x%xu); return;} eax=(u32)(n/d); edx=(u32)(n%%d); }' % (cx.rd(ins, cx.ops[0]), a))
    else:
        cx.em('{ s32 d=(s32)%s; s64 n=(s64)(((u64)edx<<32)|eax); if(d==0||(n==(s64)0x8000000000000000ull&&d==-1)||n/d>0x7fffffffll||n/d<-0x80000000ll){SYNC_OUT; fnvl_raise(1,0x%xu); return;} eax=(u32)(n/d); edx=(u32)(n%%d); }' % (cx.rd(ins, cx.ops[0]), a))


@op('cdq')
def h_cdq(cx, ins):
    cx.em('edx=(u32)((s32)eax>>31);')


@op('cwde')
def h_cwde(cx, ins):
    cx.em('eax=(u32)(s32)(s16)eax;')


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
    if cx.s != 32:
        raise Unsupported('push16')
    cx.em('{ u32 v=%s; esp-=4; M32(esp)=v; }' % cx.rd(ins, cx.ops[0]))


@op('pop')
def h_pop(cx, ins):
    if cx.s != 32:
        raise Unsupported('pop16')
    cx.em('{ u32 v=M32(esp); esp+=4; %s }' % cx.wr(ins, cx.ops[0], 'v'))


@op('leave')
def h_leave(cx, ins):
    cx.em('esp=ebp; ebp=M32(esp); esp+=4;')


@op(*['set' + c for c in CC])
def h_setcc(cx, ins):
    cx.em(cx.wr(ins, cx.ops[0], '(u32)(%s?1:0)' % CC[cx.m[3:]]))


@op(*['cmov' + c for c in CC])
def h_cmovcc(cx, ins):
    ops = cx.ops
    cx.em('if(%s){ %s }' % (CC[cx.m[4:]], cx.wr(ins, ops[0], cx.rd(ins, ops[1]))))


@op('bt')
def h_bt(cx, ins):
    ops = cx.ops
    if not (ops[0].type == X.X86_OP_REG or ops[1].type == X.X86_OP_IMM):
        return SKIP
    cx.em('cf=((%s)>>((%s)&%d))&1;' % (cx.rd(ins, ops[0]), cx.rd(ins, ops[1], ops[0].size), cx.s - 1))


@op('bts', 'btr', 'btc')
def h_btx(cx, ins):
    m, ops = cx.m, cx.ops
    if not (ops[0].type == X.X86_OP_REG or ops[1].type == X.X86_OP_IMM):
        return SKIP
    o = {'bts': 'a|b', 'btr': 'a&~b', 'btc': 'a^b'}[m]
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
