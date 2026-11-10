"""Control flow: call, jmp (direct, jump table, indirect), jcc, jecxz, ret, int3. The control-transfer rule of README
("Guest registers and control transfer"); `goto nonlocal` / `goto after_tail` target the loop core.lift appends to the function."""
from capstone import x86 as X
from .core import op, CC


def hexs(t):
    return '%08x' % t


def is_import(cx, o):
    """call/jmp [absolute address]: an import-address-table style call (the bench excludes them)"""
    return o.type == X.X86_OP_MEM and not o.mem.base and not o.mem.index and not o.mem.segment


@op('call')
def h_call(cx, ins):
    o = cx.ops[0]
    cx.callrets.append(cx.nxt)
    cx.nonlocal_ = True
    if o.type == X.X86_OP_IMM:
        t = o.imm & 0xffffffff
        if cx.lo <= t < cx.hi:
            cx.selfcalls[cx.a] = (t, cx.nxt)
            cx.kinds.add('selfcall')
        cx.calls.add(t)
        cx.em('{ esp-=4; M32(esp)=0x%xu; SYNC_OUT; REGS(); CALLX(%s); REGS(); SYNC_IN; if(c->ra!=0x%xu) goto nonlocal; }' % (cx.nxt, hexs(t), cx.nxt))
    else:
        cx.kinds.add('import' if is_import(cx, o) else 'indcall')
        cx.em('{ u32 t=%s; esp-=4; M32(esp)=0x%xu; SYNC_OUT; REGS(); fnvl_dispatch(t)(); REGS(); SYNC_IN; if(c->ra!=0x%xu) goto nonlocal; }' % (cx.rd(ins, o), cx.nxt, cx.nxt))


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
            cx.nonlocal_ = True
            cx.tails.add(t)
            em('{ SYNC_OUT; REGS(); CALLX(%s); REGS(); SYNC_IN; goto after_tail; }' % hexs(t))
            kinds.add('tailjmp')
    else:
        cx.nonlocal_ = True
        cands = cx.tables.get(cx.a)
        if cands is None:
            kinds.add('import' if is_import(cx, o) else 'jmp-indirect-untabled')
            em('{ c->ra=%s; goto nonlocal; }' % cx.rd(ins, o))
            return
        kinds.add('jumptable')
        em('{ u32 t=%s; switch(t){' % cx.rd(ins, o))
        for x in sorted(set(cands)):
            em('case 0x%xu: goto %s;' % (x, lab(x)))
        em('default: c->ra=t; goto nonlocal; } }')


@op(*['j' + c for c in CC])
def h_jcc(cx, ins):
    t = cx.ops[0].imm & 0xffffffff
    c = CC[cx.m[1:]]
    if cx.lo <= t < cx.hi:
        cx.em('if(%s) goto %s;' % (c, cx.lab(t)))
    else:
        cx.nonlocal_ = True
        cx.tails.add(t)
        cx.kinds.add('tailjmp')
        cx.em('if(%s){ SYNC_OUT; REGS(); CALLX(%s); REGS(); SYNC_IN; goto after_tail; }' % (c, hexs(t)))


@op('jecxz')
def h_jecxz(cx, ins):
    cx.em('if(!ecx) goto %s;' % cx.lab(cx.ops[0].imm & 0xffffffff))


@op('ret')
def h_ret(cx, ins):
    n = cx.ops[0].imm if cx.ops else 0
    if n:
        cx.kinds.add('retn')
    cx.em('{ u32 ra=M32(esp); esp+=%du; c->ra=ra; SYNC_OUT; return; }' % (4 + n))
    cx.term = True


@op('int3')
def h_int3(cx, ins):
    cx.em('SYNC_OUT; fnvl_raise(3,0x%xu); return;' % cx.a)
    cx.term = True
