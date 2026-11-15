"""Invariance proof of an undefined flag bit that a pushf / lahf copied into a word and that the taint walk of gate_flags.py cannot show unobserved.

CLAIM. Let S be the pushf or lahf site and U the set of flags that may be undefined there. Two runs of the code from S start from the SAME state, except that
every flag of U has its own free value in each run (ua_f, ub_f). The proof walks both runs side by side (the product program), one instruction at a time, with the
certified Form of the instruction (proof/forms.build, its z3 post-state `post()` of fnvlift/dsl.py) applied to each run's own state. A path of the walk is PROVED
when its state is identical in the two runs: every register, every flag (the six and DF), the other EFLAGS bits, and the memory at or above ESP. From identical
states the two runs execute identically, so nothing the program does afterwards can depend on the bits of U: the bit was dead. The site is proved when every
path from it is proved.
RULES (each is what makes a stop red, never green):
  - a branch (the eip of the Form is an ite of constants) is followed only when its condition is PROVED equal in the two runs under the path condition; both
    directions that are satisfiable are walked, each with its condition added to the path condition;
  - an instruction whose Form has a fault term is followed only when no fault is PROVED possible (a fault delivers the context, which includes the flags);
  - every other control transfer (call, ret, indirect jmp, a jmp out of the decoded body, an eip that is not a constant) with states that still differ is red: the
    differing value leaves the walked code;
  - an instruction without a Form is red; a value that the Form leaves undefined is a fresh value shared by the two runs (its own origin is judged by the flag
    dataflow of gate_flags.py, which makes every read of it red; the proof is about the OTHER origin, the bit of U);
  - a stack slot (an address at a constant offset from the ESP at the start of the walk) strictly below the final ESP is not part of the observable state (Win32
    x86 has no red zone and the system may overwrite it at any time: a read below ESP is already undefined by the documented ABI, ASM-003); a load of such a
    slot below the current ESP is red; every other address (absolute, through a pointer) is observable memory and its bytes must be equal;
  - the walk is bounded (MAXI instructions per path, MAXP paths); a bound reached is red; a solver answer other than unsat is red. A bound or a timeout can only
    cost a green, never give one.
Assumptions: ASM-003 (Win32 as documented: no red zone; ESP lies in user space, 0x10000 <= ESP <= 0xFFFF0000, so a push never wraps the address space), ASM-009 (the Forms, certified against the Sail model, are the instruction semantics; z3 is among the
pinned formal tools)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import z3

from fnvlift import dsl
from proof import forms

MAXI, MAXP, TIMEOUT_MS = 48, 64, 4000
REGS = dsl.REGS
FL = tuple(dsl.FLAGS) + ('df',)
_POST = {}


class Red(Exception):
    pass


def _solver(pc, extra):
    s = z3.Solver()
    s.set('timeout', TIMEOUT_MS)
    for c in pc: s.add(c)
    s.add(extra)
    return s


def _proved(pc, goal):
    """True only when pc => goal is valid (the negation is unsat)"""
    return _solver(pc, z3.Not(goal)).check() == z3.unsat


def _sat(pc, c):
    return _solver(pc, c).check() != z3.unsat


def _loads(F):
    """the address nodes of every `load` node of the Form"""
    seen, out = set(), []

    def walk(n):
        if n.u in seen: return
        seen.add(n.u)
        if n.op == 'load': out.append(n.a[0])
        for c in n.a: walk(c)
    roots = [v for _, v, _ in F.regs] + [w for _, _, w in F.regs if w is not None] + [x for st in F.stores for x in st] + \
            [v for _, v, _ in F.flags] + [w for _, _, w in F.flags if w is not None] + [c for _, c in F.faults] + ([F.flo] if F.flo is not None else []) + [F.eip]
    for r in roots: walk(r)
    return out


def _form(ins, addr):
    k = (bytes(ins.bytes), addr)
    r = _POST.get(k)
    if r is None:
        try: F = forms.build(ins, addr)
        except Exception as e: raise Red('no Form for %s %s' % (ins.mnemonic, ins.op_str)) from e
        dsl._M.clear()
        r = _POST[k] = (F, F.post(), [dsl.to_smt(a) for a in _loads(F)])
    return r


def _init(mask):
    base = dict(regs={r: z3.BitVec('i_' + r, 32) for r in REGS}, flags={f: z3.BitVec('i_' + f, 1) for f in FL}, flo=z3.BitVec('i_flo', 32), mem=z3.Array('i_mem', z3.BitVecSort(32), z3.BitVecSort(8)))
    a = dict(base, flags=dict(base['flags'])); b = dict(base, flags=dict(base['flags']))
    for f in mask:
        a['flags'][f] = z3.BitVec('ua_' + f, 1)
        b['flags'][f] = z3.BitVec('ub_' + f, 1)
    return a, b


def _pairs(st):
    p = [(z3.BitVec(r, 32), st['regs'][r]) for r in REGS] + [(z3.BitVec(f, 1), st['flags'][f]) for f in FL] + [(z3.BitVec('flo', 32), st['flo'])]
    return p + [(dsl.mem_array(), st['mem'])]


def _step(st, P, step, addr):
    """-> (next state, eip, fault, load address expressions are checked by the caller) of ONE run: the post-state of the Form on the pre-state ST"""
    sub = lambda e: z3.simplify(z3.substitute(e, *_pairs(st)))
    nx = dict(regs={}, flags={}, flo=sub(P['flo']), mem=sub(P['mem']))
    for r in REGS:
        v, d = sub(P['regs'][r]), z3.simplify(z3.substitute(P['rdefined'][r], *_pairs(st)))
        nx['regs'][r] = v if z3.is_true(d) else z3.If(d, v, z3.BitVec('x%d_%x_%s' % (step, addr, r), 32))
    for f in FL:
        v, d = sub(P['flags'][f]), z3.simplify(z3.substitute(P['defined'][f], *_pairs(st))) if f in P['defined'] else z3.BoolVal(True)
        nx['flags'][f] = v if z3.is_true(d) else z3.If(d, v, z3.BitVec('x%d_%x_%s' % (step, addr, f), 1))
    return nx, sub(P['eip']), sub(P['fault'])


def _equal_goal(a, b, esp_bound=None):
    """the formula 'the observable states are identical': registers, flags, the other EFLAGS bits, and the memory at or above ESP (the stores of the two runs are
    the only places the two memories can differ: both derive from one base array)"""
    g = [a['regs'][r] == b['regs'][r] for r in REGS] + [a['flags'][f] == b['flags'][f] for f in FL] + [a['flo'] == b['flo']]
    return z3.And(*g)


def _rel(e, esp0):
    """the signed offset of the address expression E from the ESP of the start of the walk, when it is a constant offset (a push, call or esp-relative address), else None"""
    d = z3.simplify(e - esp0)
    if not z3.is_bv_value(d): return None
    v = d.as_long()
    return v - (1 << 32) if v >= 1 << 31 else v


def _mem_goal(a, b, esp0):
    """memory equal where it is observable. Both memories derive from one base array, so they differ only at the addresses stored to. A store whose address is a
    constant offset from the initial ESP is a stack slot: it is dead (not observable) when it lies strictly below the final ESP (ASM-003: no red zone); at or
    above it, the slots must be equal. A store at any other address (absolute, through a pointer) is observable memory: its bytes must be equal."""
    addrs = []

    def walk(m):
        while z3.is_app_of(m, z3.Z3_OP_STORE):
            addrs.append(m.arg(1))
            m = m.arg(0)
    walk(a['mem']); walk(b['mem'])
    k = _rel(a['regs']['esp'], esp0)
    g = []
    for ad in addrs:
        c = _rel(ad, esp0)
        if c is not None and k is not None and c < k: continue
        g.append(z3.Select(a['mem'], ad) == z3.Select(b['mem'], ad))
    return z3.And(*g) if g else z3.BoolVal(True)


def identical(pc, a, b, esp0):
    g = z3.And(_equal_goal(a, b), a['regs']['esp'] == b['regs']['esp'], _mem_goal(a, b, esp0))
    return _proved(pc, g)


def prove(objs, site, mask):
    """-> (True, '') when the bits of the flags MASK (flag names) copied at SITE are proved dead; (False, reason) otherwise. OBJS: {address: capstone insn} of the
    decoded body"""
    try:
        a0, b0 = _init(mask)
        esp0 = a0['regs']['esp']
        paths = [(site, a0, b0, [z3.UGE(esp0, 0x10000), z3.ULE(esp0, 0xFFFF0000)], 0)]        # the stack pointer is in user space (ASM-003: the first and last 64 KiB are never mapped)
        npaths = 0
        while paths:
            addr, a, b, pc, n = paths.pop()
            npaths += 1
            if npaths > MAXP: raise Red('more than %d paths' % MAXP)
            while True:
                if n > 0 and identical(pc, a, b, esp0): break
                if n >= MAXI: raise Red('more than %d instructions on a path' % MAXI)
                ins = objs.get(addr)
                if ins is None: raise Red('the walk leaves the decoded body at %08x with differing states' % addr)
                F, P, ld = _form(ins, addr)
                for st in (a, b):
                    for la in ld:
                        c = _rel(z3.substitute(la, *_pairs(st)), esp0)
                        k = _rel(st['regs']['esp'], esp0)
                        if c is not None and k is not None and c < k: raise Red('a load at %08x reads below ESP' % addr)
                (na, ea, fa), (nb, eb, fb) = _step(a, P, n, addr), _step(b, P, n, addr)
                if F.faults:
                    for ft in (fa, fb):
                        if not _proved(pc, ft == 0): raise Red('%s at %08x may fault with differing states' % (ins.mnemonic, addr))
                a, b = na, nb
                n += 1
                if z3.is_bv_value(ea) and z3.is_bv_value(eb) and ea.as_long() == eb.as_long():
                    addr = ea.as_long()
                    continue
                if z3.is_app_of(ea, z3.Z3_OP_ITE) and z3.is_app_of(eb, z3.Z3_OP_ITE):
                    (ca, ta, fa_), (cb, tb, fb_) = ea.children(), eb.children()
                    if not all(z3.is_bv_value(x) for x in (ta, fa_, tb, fb_)) or ta.as_long() != tb.as_long() or fa_.as_long() != fb_.as_long():
                        raise Red('a branch at %08x with different targets' % (addr,))
                    if not _proved(pc, ca == cb): raise Red('the branch at %08x depends on the bit' % (addr,))
                    legs = [(ca, ta.as_long()), (z3.Not(ca), fa_.as_long())]
                    live = [(c, t) for c, t in legs if _sat(pc, c)]
                    for c, t in live[1:]: paths.append((t, a, b, pc + [c], n))
                    if not live: break
                    addr, pc = live[0][1], pc + [live[0][0]]
                    continue
                raise Red('a transfer at %08x leaves the walked code with differing states' % addr)
        return True, ''
    except Red as e:
        return False, str(e)
