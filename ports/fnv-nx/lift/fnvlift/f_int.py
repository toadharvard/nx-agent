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




















def shcount(cx, ins, ops):
    """the count operand of a shift/rotate: capstone reports the implicit 1 of `rcl/rol [mem]` (d1 /r) as an immediate of size 0, which rd() would mask to 0"""
    if len(ops) < 2:
        return '1'
    if ops[1].type == X.X86_OP_IMM:
        return '0x%xu' % (ops[1].imm & 0xff)
    return cx.rd(ins, ops[1])


@op('sal')
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
























@op('wait', 'fnclex', 'fwait')
def h_nop(cx, ins):
    pass








def bitstr(cx, ins, body):
    """bt/bts/btr/btc [mem], reg: bit-string addressing. The (signed) index selects the operand-size unit at ea + (idx >> log2(bits))*bytes, the bit inside it is idx mod bits.
    body: C text over a (the unit), b (its bit mask), writing r (new unit) -> cf set, store when r differs by operation"""
    ops, s = cx.ops, cx.s
    sh = {16: 4, 32: 5}[s]
    cx.em('{ s32 i=(s32)(s%d)%s; u32 ad=%s+(u32)((i>>%d)*%d); u32 a=%s%d(ad), b=1u<<(i&%d), r; cf=(a&b)!=0; %s' % (s, cx.rd(ins, ops[1]), cx.ea(ins, ops[0]), sh, s // 8, cx.macc, s, s - 1, body))
















@op('salc')
def h_salc(cx, ins):
    cx.em('eax=(eax&~0xffu)|(cf?0xffu:0u);')








@op('cmpxchg8b')
def h_cmpxchg8b(cx, ins):
    ea = cx.ea(ins, cx.ops[0])
    cx.em('{ u32 ad=%s; u64 d=%s64(ad); if(d==(((u64)edx<<32)|eax)){ zf=1; %s64(ad)=((u64)ecx<<32)|ebx; } else { zf=0; eax=(u32)d; edx=(u32)(d>>32); %s64(ad)=d; } }' % (ea, cx.macc, cx.macc, cx.macc))




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


