"""Control flow: call, jmp (direct, jump table, indirect), jcc, jecxz, ret, int3. The control-transfer rule of README
("Guest registers and control transfer"); `goto nonlocal` / `goto after_tail` target the loop core.lift appends to the function."""
from capstone import x86 as X
from .core import op, CC


def hexs(t):
    return '%08x' % t


def is_import(cx, o):
    """call/jmp [absolute address]: an import-address-table style call (the bench excludes them)"""
    return o.type == X.X86_OP_MEM and not o.mem.base and not o.mem.index and not o.mem.segment


def jump_out(cx, t, cond=None):
    """a direct jump (cond None) or conditional jump to address t outside the function's range. t is an entry of the universe: the C call of its
    body, then the tail rule. t is not an entry (the universe is closed under direct targets inside executable sections, so t lies in no executable
    section): the transfer goes to the dispatcher like any computed one: one rule for every address that has no body (rt/rt.c)."""
    cx.nonlocal_ = True
    pre = 'if(%s)' % cond if cond else ''
    if t in cx.uent:
        cx.tails.add(t)
        cx.kinds.add('tailjmp')
        cx.em('%s{ SYNC_OUT; REGS(); CALLX(%s); REGS(); SYNC_IN; goto after_tail; }' % (pre, hexs(t)))
    else:
        cx.kinds.add('jmp-direct-noentry')
        cx.noentry.add(t)
        cx.em('%s{ c->ra=0x%xu; goto nonlocal; }' % (pre, t))


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
        if t in cx.uent:
            cx.calls.add(t)
            cx.em('{ esp-=4; M32(esp)=0x%xu; SYNC_OUT; REGS(); CALLX(%s); REGS(); SYNC_IN; if(c->ra!=0x%xu) goto nonlocal; }' % (cx.nxt, hexs(t), cx.nxt))
        else:       # not an entry (see jump_out): through the dispatcher
            cx.kinds.add('call-direct-noentry')
            cx.noentry.add(t)
            cx.em('{ esp-=4; M32(esp)=0x%xu; SYNC_OUT; REGS(); fnvl_dispatch(0x%xu)(); REGS(); SYNC_IN; if(c->ra!=0x%xu) goto nonlocal; }' % (cx.nxt, t, cx.nxt))
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
            jump_out(cx, t)
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
        jump_out(cx, t, c)


@op('jecxz')
def h_jecxz(cx, ins):
    t = cx.ops[0].imm & 0xffffffff
    if cx.lo <= t < cx.hi:
        cx.em('if(!ecx) goto %s;' % cx.lab(t))
    else:
        jump_out(cx, t, '!ecx')


@op('ret')
def h_ret(cx, ins):
    n = cx.ops[0].imm if cx.ops else 0
    if n:
        cx.kinds.add('retn')
    cx.em('{ u32 ra=M32(esp); esp+=%du; c->ra=ra; SYNC_OUT; return; }' % (4 + n))
    cx.term = True


def raise_site(cx, code):
    """a hardware exception at this instruction (R4): fnvl_raise runs the OS dispatch on the guest state and leaves the resume address in c->ra
    (ContinueExecution) with esp restored; the loop then continues there. The instruction's own address is a label of the loop (retry)."""
    cx.nonlocal_ = True
    cx.raises.add(cx.a)
    cx.raises.add(cx.nxt)       # #BP reports the address after the int3 as Context.Eip
    return 'SYNC_OUT; REGS(); fnvl_raise(%s,0x%xu); REGS(); SYNC_IN; goto nonlocal;' % (code, cx.a)


@op('int3')
def h_int3(cx, ins):
    cx.em(raise_site(cx, 'FNVL_BP'))
    cx.term = True


@op('ud2')
def h_ud2(cx, ins):
    cx.em(raise_site(cx, 'FNVL_UD'))
    cx.term = True
