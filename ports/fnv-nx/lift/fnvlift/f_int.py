"""Integer instruction family: moves, ALU, shifts, multiply/divide, stack, setcc/cmovcc, bit tests."""
from capstone import x86 as X
from .core import op, shape_handler, SKIP, Unsupported, MASK, CC, reg


SEGSEL = {'cs': 'SEL_CS', 'ss': 'SEL_DS', 'ds': 'SEL_DS', 'es': 'SEL_DS', 'fs': 'SEL_FS', 'gs': 'SEL_GS'}    # initial selectors of a Win32 x86 user process (fnvl_seg_init; GS is null); the current values live in c->sel (SEG_STATE)


SEG_STATE = True       # struct cpu holds the six selector values: `mov r, Sreg` / `push Sreg` read the state (rt/fnvl.h fnvl_seg_get) instead of the initial constants above


def segread(sg):
    from . import x86sys
    if sg == 'gs' and 'gs' not in x86sys.SEL:     # the initial gs is undecided in the record (a later load sets the state, but the read cannot tell which): no body until L5's row decides it
        raise Unsupported('read of gs: the initial gs is undecided in x86sys selectors')
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


def _segshape(cx, ins):
    """the shapes of mov/push/pop that carry a segment register or a CRn/DRn operand"""
    return cx.m in ('mov', 'push', 'pop') and any(o.type == X.X86_OP_REG and (segname(ins, o) or ins.reg_name(o.reg)[:2] in ('cr', 'dr')) for o in cx.ops)


@shape_handler(_segshape)
def h_segshapes(cx, ins):
    """the shapes no Form covers: a segment register operand (class 3, f_sys: MOV/POP Sreg load through fnvl_seg_set after the fnvl_seg_ok check; reads are the state, fnvl_seg_get) and MOV to/from CRn/DRn (class 2:
    #GP(0) at CPL 3). A class of shapes by operand kind, tried after the Forms (EMIT) and the OPS handlers and before f_sys.h_default, which would make them #UD; no OPS entry, so no mnemonic has both a Form and a hand handler."""
    from . import f_sys
    ops = cx.ops
    if cx.m == 'mov':
        if any(o.type == X.X86_OP_REG and ins.reg_name(o.reg)[:2] in ('cr', 'dr') for o in ops):
            f_sys.gp0(cx, ins)
            return
        sg = segname(ins, ops[0])
        if sg:      # mov Sreg, r/m16
            f_sys.mov_to_sreg(cx, ins, sg)
            return
        sg = segname(ins, ops[1]) if len(ops) > 1 else None
        if sg:      # mov r/m16, Sreg: the selector in the state; a 32-bit register destination gets it zero-extended
            cx.em(cx.wr(ins, ops[0], segread(sg)))
            return
    sg = segname(ins, ops[0])
    if cx.m == 'push':      # push Sreg: 32-bit operand size pushes the selector zero-extended; the 66 prefix pushes the word
        cx.em('{ esp-=2; M16(esp)=(u16)(%s); }' % segread(sg) if ins.prefix[2] == 0x66 else '{ esp-=4; M32(esp)=%s; }' % segread(sg))
        return
    f_sys.pop_sreg(cx, ins, sg)


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
















@op('cmpxchg8b')
def h_cmpxchg8b(cx, ins):
    ea = cx.ea(ins, cx.ops[0])
    cx.em('{ u32 ad=%s; u64 d=%s64(ad); if(d==(((u64)edx<<32)|eax)){ zf=1; %s64(ad)=((u64)ecx<<32)|ebx; } else { zf=0; eax=(u32)d; edx=(u32)(d>>32); %s64(ad)=d; } }' % (ea, cx.macc, cx.macc, cx.macc))




