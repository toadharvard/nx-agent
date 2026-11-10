"""Control flow: call, jmp (direct, jump table, indirect), jcc, jecxz, ret, int3."""
from capstone import x86 as X
from .core import op, CC


@op('call')
def h_call(cx, ins):
    o = cx.ops[0]
    t = ('0x%xu' % (o.imm & 0xffffffff)) if o.type == X.X86_OP_IMM else cx.rd(ins, o)
    if o.type == X.X86_OP_IMM and cx.lo <= (o.imm & 0xffffffff) < cx.hi:
        cx.selfcalls[cx.a] = (o.imm & 0xffffffff, cx.nxt)
        cx.kinds.add('selfcall')
    cx.callrets.append(cx.nxt)
    cx.em('{ u32 t=%s; esp-=4; M32(esp)=0x%xu; SYNC_OUT; fnvl_call(c,t); SYNC_IN; }' % (t, cx.nxt))


@op('jmp')
def h_jmp(cx, ins):
    o = cx.ops[0]
    em, lab, kinds = cx.em, cx.lab, cx.kinds
    cx.term = True
    if o.type == X.X86_OP_IMM:
        t = o.imm & 0xffffffff
        if cx.lo <= t < cx.hi:
            em('goto %s;' % lab(t))
        else:
            em('SYNC_OUT; fnvl_tail(c,0x%xu); return;' % t)
            kinds.add('tailjmp')
    else:
        cands = cx.tables.get(cx.a)
        if cands is None:
            cands = [x for x in cx.taken if x in cx.insns]
            kinds.add('jmp-indirect-untabled')
        else:
            kinds.add('jumptable')
        em('{ u32 t=%s; switch(t){' % cx.rd(ins, o))
        for x in sorted(set(cands)):
            em('case 0x%xu: goto %s;' % (x, lab(x)))
        em('default: SYNC_OUT; fnvl_tail(c,t); return; } }')


@op(*['j' + c for c in CC])
def h_jcc(cx, ins):
    t = cx.ops[0].imm & 0xffffffff
    c = CC[cx.m[1:]]
    cx.em('if(%s) goto %s;' % (c, cx.lab(t)) if cx.lo <= t < cx.hi else 'if(%s){ SYNC_OUT; fnvl_tail(c,0x%xu); return; }' % (c, t))


@op('jecxz')
def h_jecxz(cx, ins):
    cx.em('if(!ecx) goto %s;' % cx.lab(cx.ops[0].imm & 0xffffffff))


@op('ret')
def h_ret(cx, ins):
    cx.em('esp+=%du; SYNC_OUT; return;' % (4 + (cx.ops[0].imm if cx.ops else 0)))
    cx.term = True


@op('int3')
def h_int3(cx, ins):
    cx.em('SYNC_OUT; fnvl_trap(c,0x%xu,3); return;' % cx.a)
    cx.term = True
