"""Control flow: call, jmp (direct, jump table, indirect), jcc, jecxz, ret, int3. The control-transfer rule of README
("Guest registers and control transfer"); `goto nonlocal` / `goto after_tail` target the loop core.lift appends to the function."""
from capstone import x86 as X
from .core import op, CC


def hexs(t):
    return '%08x' % t


def is_import(cx, o):
    """call/jmp [absolute address]: an import-address-table style call (the bench excludes them)"""
    return o.type == X.X86_OP_MEM and not o.mem.base and not o.mem.index and not o.mem.segment


def jump_stmt(cx, t):
    """C statement of a direct transfer to address t outside the function's range. t is an entry of the universe: the C call of its body, then the
    tail rule. t is not an entry (the universe is closed under direct targets inside executable sections, so t lies in no executable section): the
    transfer goes to the dispatcher like any computed one: one rule for every address that has no body (rt/rt.c)."""
    cx.nonlocal_ = True
    if t in cx.uent:
        cx.tails.add(t)
        cx.kinds.add('tailjmp')
        return '{ SYNC_OUT; REGS(); CALLX(%s); REGS(); SYNC_IN; goto after_tail; }' % hexs(t)
    cx.kinds.add('jmp-direct-noentry')
    cx.noentry.add(t)
    return '{ c->ra=0x%xu; goto nonlocal; }' % t


def jump_out(cx, t, cond=None):
    """a direct jump (cond None) or conditional jump to address t outside the function's range"""
    cx.em(('if(%s)' % cond if cond else '') + jump_stmt(cx, t))


def jump_to(cx, t):
    """control transfer to the constant address t (jmp, jcc, loop, jecxz, rep step): a label of this function, else jump_stmt. -> C statement"""
    return 'goto %s;' % cx.lab(t) if cx.lo <= t < cx.hi else jump_stmt(cx, t)


def call_xfer(cx, o, t):
    """transfer half of the call at cx.a (the state effect, esp-=4 and the return address store, is the caller's): `t` is a constant target (int) or
    the name of a C u32 holding it; operand `o` only classifies an indirect call (import or not). -> C text"""
    cx.callrets.append(cx.nxt)
    cx.nonlocal_ = True
    if isinstance(t, int):
        if cx.lo <= t < cx.hi:
            cx.selfcalls[cx.a] = (t, cx.nxt)
            cx.kinds.add('selfcall')
        if t in cx.uent:
            cx.calls.add(t)
            return 'SYNC_OUT; REGS(); CALLX(%s); REGS(); SYNC_IN; if(c->ra!=0x%xu) goto nonlocal;' % (hexs(t), cx.nxt)
        cx.kinds.add('call-direct-noentry')     # not an entry (see jump_stmt): through the dispatcher
        cx.noentry.add(t)
        return 'SYNC_OUT; REGS(); fnvl_dispatch(0x%xu)(); REGS(); SYNC_IN; if(c->ra!=0x%xu) goto nonlocal;' % (t, cx.nxt)
    cx.kinds.add('import' if is_import(cx, o) else 'indcall')
    return 'SYNC_OUT; REGS(); fnvl_dispatch(%s)(); REGS(); SYNC_IN; if(c->ra!=0x%xu) goto nonlocal;' % (t, cx.nxt)


def jmp_indirect(cx, o, t):
    """jmp to the C u32 expression t: through the instruction's jump table when it has one, else the non-local loop. -> list of C lines"""
    cx.nonlocal_ = True
    cands = cx.tables.get(cx.a)
    if cands is None:
        cx.kinds.add('import' if is_import(cx, o) else 'jmp-indirect-untabled')
        return ['{ c->ra=%s; goto nonlocal; }' % t]
    cx.kinds.add('jumptable')
    return (['{ u32 t=%s; switch(t){' % t] + ['case 0x%xu: goto %s;' % (x, cx.lab(x)) for x in sorted(set(cands))] +
            ['default: c->ra=t; goto nonlocal; } }'])


def ret_xfer(cx, ra):
    """return to the C u32 expression ra (esp already popped) -> C text"""
    if cx.ops and cx.ops[0].imm:
        cx.kinds.add('retn')
    return 'c->ra=%s; SYNC_OUT; return;' % ra


@op('call')
def h_call(cx, ins):
    o = cx.ops[0]
    if o.type == X.X86_OP_IMM:
        x = call_xfer(cx, o, o.imm & 0xffffffff)
        cx.em('{ esp-=4; M32(esp)=0x%xu; %s }' % (cx.nxt, x))
    else:
        tv = cx.rd(ins, o)
        x = call_xfer(cx, o, 't')
        cx.em('{ u32 t=%s; esp-=4; M32(esp)=0x%xu; %s }' % (tv, cx.nxt, x))


@op('jmp')
def h_jmp(cx, ins):
    o = cx.ops[0]
    cx.term = True
    if o.type == X.X86_OP_IMM:
        cx.em(jump_to(cx, o.imm & 0xffffffff))
    else:
        for l in jmp_indirect(cx, o, cx.rd(ins, o)):
            cx.em(l)


@op(*['j' + c for c in CC])
def h_jcc(cx, ins):
    t = cx.ops[0].imm & 0xffffffff
    c = CC[cx.m[1:]]
    st = jump_to(cx, t)
    cx.em('if(%s)%s' % (c, st if st[0] == '{' else ' ' + st))


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


def raise_site(cx, code, trap=True, at=None):
    """a hardware exception at this instruction (R4): fnvl_raise runs the OS dispatch on the guest state and leaves the resume address in c->ra
    (ContinueExecution) with esp restored; the loop then continues there. The instruction's own address is a label of the loop (retry)."""
    cx.nonlocal_ = True
    cx.raises.add(cx.a)
    if trap:
        cx.raises.add(cx.nxt)       # #BP reports the address after the int3 as Context.Eip
    return 'SYNC_OUT; REGS(); FNVL_RAISE(%s,0x%xu); REGS(); SYNC_IN; goto nonlocal;' % (code, cx.a if at is None else at)


@op('int3')
def h_int3(cx, ins):
    cx.em(raise_site(cx, 'FNVL_BP'))
    cx.term = True


@op('ud2')
def h_ud2(cx, ins):
    cx.em(raise_site(cx, 'FNVL_UD'))
    cx.term = True
