"""Instruction handlers generated from the proven DSL forms (R2: one source for the proof and for the shipped code). Lives in proof/ (the root that holds the Forms; the root graph is
acyclic, proof above fnvlift): the lifter takes it as a function, core.set_emit(proof.lift_dsl.emit_form).

emit_form(cx, ins) builds the Form of the instruction (proof/forms.py: the same object the SMT proofs are about) and emits its C inline through
Form.to_c_inline (fnvlift/dsl.py).  core.dispatch calls it first for every instruction when an emitter is set (core.set_emit); it returns SKIP for an instruction the
forms do not cover (their mnemonics, or a register operand that is not a general register: xmm/mm/segment), which then goes to the hand handlers.

One block per instruction:  `{ snapshots of the pre-state; const temporaries (every load, shared sub-expressions); faults; register writes;
flag writes; stores; control transfer }`, i.e. the Form's parallel semantics: all expressions read the pre-state, effects commit in the Form's order.
Mapping of the Form's state to the lifter's:  eax..edi, cf pf af zf sf of df = the C registers / function-local flag values (0 or 1);
fs_base = `fsb`; flo (the other EFLAGS bits) = c->eflx | IF (EFL_PUSH/EFL_POP keep AC and ID there; user mode has IF set);
memory = M8/M16/M32 (core's macros).  DEF0: a flag a form declares undefined is assigned 0, and a flag write with `when` w is `w ? v : 0`; DEFPRIOR: a register write with `when` is conditional, so a register the model leaves free keeps its prior value.

Faults (Form.faults) raise through f_flow.raise_site like int3/ud2 and the hand div: code 1 (#DE) is FNVL_DE0 when the failing disjunct is the
divisor-zero test `eq(x, 0)` and FNVL_DEOVF otherwise (the quotient overflow); every other kind's runtime code is the same number, a row of fnvlift/faults.json.

Control transfer is derived from the Form's eip node by structure: constant == next address -> falls through; another constant -> `call` pushes
(the Form's esp/store effects) and uses f_flow.call_xfer, anything else f_flow.jump_to; ite(c, constant T, next) -> `if (c) jump_to(T)` (jcc, jecxz,
loop*, and the step repeat of rep string forms, whose eip is `ite(more, this address, next)`: a goto to the instruction's own label); any other
expression (indirect jmp / call, ret) -> f_flow.jmp_indirect / call_xfer / ret_xfer with the C text of the node, the kind chosen by the mnemonic."""
from fnvlift.core import CC, SKIP, SUB, Unsupported
from fnvlift import f_flow
from fnvlift import dsl, faults

from proof import forms, forms_alu, forms_flow, forms_move, forms_simd             # noqa: F401  (proof/forms.py: the Form builders; forms_* register their handlers in forms.H)

FAULT = {1: 'DE', 2: 'BP', 3: 'UD', 4: 'BR'}
STATE = {'df': 'df', 'fs_base': 'fsb', 'flo': '(c->eflx|0x200u)'}      # Form pre-state variables besides the registers and the five flags
# the x87 / MMX / SSE pre-state (dsl.XW) as C expressions over struct cpu (rt/fnvl_x87.h fx_dsl_*); c0 c2 c3 are the lifted function's own condition-code locals
XSTATE = {**{'fr%d' % i: 'fx_dsl_fr(%d)' % i for i in range(8)}, 'ftop': 'c->ftop', 'ftag': 'fx_dsl_ftag()', 'fcw': '(c->fcw & 0xffffu)', 'fsw': 'fx_dsl_fsw(c0, c2, c3)',
          'fip': 'c->fip', 'fdp': 'c->fdp', 'fop': 'fx_fop()', 'fcs': '(c->fcs & 0xffffu)', 'fds': '(c->fds & 0xffffu)',
          **{'xmm%d' % i: 'fx_dsl_xmm(%d)' % i for i in range(8)}, 'mxcsr': 'fx_dsl_mxcsr()'}
assert set(XSTATE) == set(dsl.XW)
SIXFLAGS = ('cf', 'pf', 'af', 'zf', 'sf', 'of')


def supported(ins):
    """the Form builder has a handler for the mnemonic (forms.build's own lookup: forms.H, then the jcc / setcc / cmovcc families) and every register
    operand is a general register (the string forms' mnemonics movsd/cmpsd are also SSE2 instructions)"""
    m = ins.mnemonic.split()[-1]
    ok = m in forms.H or (m[0] == 'j' and m[1:] in CC) or (m.startswith('set') and m[3:] in CC) or (m.startswith('cmov') and m[4:] in CC)
    return ok and all(ins.reg_name(o.reg) in SUB or forms.isvec(forms.Op(ins, o)) for o in ins.operands if o.type == forms.X.X86_OP_REG)


def varc(cx):
    def v(name):
        if name == 'gs_base':      # the GS base is a function of the CURRENT GS selector (core.Ctx.ea); the null / code-selector #GP is the guard core.gs_guard emits before the instruction
            return 'FNVL_GS_BASE()'
        if name == 'fs_base':
            cx.usesfs = True
        if name in XSTATE:
            return XSTATE[name]
        if name in dsl.REGS or name in SIXFLAGS or name in STATE:
            return STATE.get(name, name)
        raise Unsupported('form variable ' + name)
    return v


def de_code(n, ref):
    """C expression of the hardware code of a #DE whose condition node is n: the disjuncts are tried in order, a divisor-zero test `x == 0` is FNVL_DE0"""
    ds, st = [], [n]
    while st:
        d = st.pop()
        if d.op == 'or':
            st.extend(reversed(d.a))
        else:
            ds.append(d)
    k = ['FNVL_DE0' if d.op == 'eq' and d.a[1].op == 'const' and d.a[1].x == 0 else 'FNVL_DEOVF' for d in ds]
    e = k[-1]
    for d, c in zip(reversed(ds[:-1]), reversed(k[:-1])):
        e = '%s ? %s : %s' % (ref(d), c, e)
    return e


# The fault map: every fault code a Form can raise (dsl.FAULTS, the enum) -> (runtime code of rt/fnvl.h, the address the runtime is given relative to the instruction, trap).
# Each row follows the runtime's own statement in rt/fnvl.h (the status, fault or trap, ExceptionAddress / Context.Eip) and Wine 9.0 dlls/ntdll/signal_i386.c
# (segv_handler / trap handlers: TRAP_x86_DIVIDE -> STATUS_INTEGER_DIVIDE_BY_ZERO, TRAP_x86_BPTFLT -> STATUS_BREAKPOINT, TRAP_x86_OPNOEXT/ILLEGAL -> STATUS_ILLEGAL_INSTRUCTION,
# TRAP_x86_BOUND -> STATUS_ARRAY_BOUNDS_EXCEEDED, TRAP_x86_OFLOW -> STATUS_INTEGER_OVERFLOW, privileged instruction (#GP from CLI/HLT/...) -> STATUS_PRIVILEGED_INSTRUCTION,
# TRAP_x86_PROTFLT -> STATUS_ACCESS_VIOLATION, TRAP_x86_TRCTRAP -> STATUS_SINGLE_STEP). at: 'a' the instruction, 'nxt' the next instruction, 'a+1' the imm8 byte of a 2-byte int.
FAULT_MAP = faults.fault_map()      # generated from the one fault table (fnvlift/faults.json): kind -> (FNVL_* name or AV, at, trap)
DE = 1        # #DE: the status (divide by zero or overflow) is chosen by the condition (de_code), a fault
assert set(FAULT_MAP) | {DE} == set(dsl.FAULTS), 'a fault code of dsl.FAULTS has no row in FAULT_MAP: %s' % sorted(set(dsl.FAULTS) - set(FAULT_MAP) - {DE})


def fault_fn(cx):
    def f(code, n, ref):
        if code == DE:
            c, at, trap, av = de_code(n, ref), None, False, False
        else:
            c, w, trap = FAULT_MAP[code]; av = c == 'AV'; at = {'a': cx.a, 'nxt': cx.nxt, 'a+1': cx.a + 1}[w]
        st = f_flow.raise_site(cx, c, trap=trap, at=at, av=av)
        if n.op == 'const' and n.x == 1:
            cx.term = True
            return st
        return 'if (%s) { %s }' % (ref(n), st)
    return f


def flo_fn(v):
    return 'c->eflx = %s & ~0x200u;' % v


def transfer(cx, ins, F):
    """-> (nodes whose value is needed after the writes, function(ref) -> list of C lines); sets cx.term"""
    n, nxt, m = F.eip, cx.nxt, ins.mnemonic.split()[-1]      # the bare mnemonic (cx.m keeps `repz ret`)
    if n.op == 'const':
        if m == 'call':
            return [], lambda ref: [f_flow.call_xfer(cx, cx.ops[0], n.x)]
        if n.x == nxt:
            return [], lambda ref: []
        cx.term = True
        return [], lambda ref: [f_flow.jump_to(cx, n.x)]
    if n.op == 'ite' and n.a[1].op == 'const' and n.a[2].op == 'const' and n.a[2].x == nxt and m != 'call':
        t = n.a[1].x

        def cond(ref):
            st = f_flow.jump_to(cx, t)
            return ['if (%s)%s' % (ref(n.a[0]), st if st[0] == '{' else ' ' + st)]
        return [n.a[0]], cond
    if m == 'call':
        return [n], lambda ref: [f_flow.call_xfer(cx, cx.ops[0], ref(n))]
    if m == 'ret':
        cx.term = True
        return [n], lambda ref: [f_flow.ret_xfer(cx, ref(n))]
    if m == 'jmp':
        cx.term = True
        return [n], lambda ref: f_flow.jmp_indirect(cx, cx.ops[0], ref(n))
    raise Unsupported('eip form of ' + m)


def emit_form(cx, ins):
    """emit the C of one instruction from its Form -> None, or SKIP when the forms do not cover it. A build error propagates."""
    if not supported(ins):
        return SKIP
    F = forms.build(ins, cx.a)
    force, xfer = transfer(cx, ins, F)
    L, ref = F.to_c_inline(varc(cx), fault_fn(cx), flo_fn, force, cx.macc)
    L += xfer(ref)
    if L:
        cx.em('{')
        for l in L:
            cx.em(l)
        cx.em('}')
