#!/usr/bin/env python3
"""x87 Forms generated from x87spec (X87FORMS.md). Part 1 here: the op-symbol mechanism, the translator z3 term -> DSL DAG, the Form of an x87 class, and its proof.

OP SYMBOLS (ruling after the op census). The part (i) machinery of x87spec is the set of module-level functions that the entry functions of part (ii) call, directly or through
each other, and that are not themselves entries. `part_one()` derives it from the ast of x87spec.py: the entry functions are the module-level names that the lambdas of `_mk`
call; the closure is taken over the module-level functions defined in the file; nothing is listed by hand. `instrument()` replaces each of them in the x87spec namespace by a wrapper.
The wrapper calls the original; when every argument is a z3 term and the result is a z3 term, whose expansion contains a floating-point or Real kind (the part that the DSL has no
value type for), it returns an application of the z3 uninterpreted function of the SAME NAME and sorts instead (one symbol per name and sorts = one DSL op). A function whose
expansion is bit-vector, array and logic only stays inline: those kinds are the DSL's own ops (already proved by opmap), so a symbol for them would be a second formulation of the same
fact (CLAUDE.md 2026-11-15 rule 2). A function that takes the state object (St) or returns a tuple is composition, not a value op: it stays inline. A Real or FP kind that
reaches the DAG through such a function is `leak(...)`, and the translator raises Unsupported naming it.
"""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import ast
import contextlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), HERE]
import z3  # noqa: E402
import x87spec as X  # noqa: E402

SPEC = os.path.join(HERE, "x87spec.py")


class Unsupported(Exception):
    pass


def _calls(node):
    return {n.func.id for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


def part_one():
    """-> (entry function names, part (i) function names) derived from the ast of x87spec.py"""
    with open(SPEC, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    mk = funcs["_mk"]
    entries = {c for c in _calls(mk) if c in funcs and c != "_mk"}
    seen, stack = set(), list(entries)
    while stack:
        f = stack.pop()
        if f in seen:
            continue
        seen.add(f)
        stack.extend(c for c in _calls(funcs[f]) if c in funcs and c not in seen)
    return entries, seen - entries


def _is_real(t):
    return t.sort().kind() == z3.Z3_REAL_SORT or t.sort().kind() == z3.Z3_INT_SORT


def _fp_kind(k):
    return KINDNAME.get(k, "").startswith("FPA_") or k in REALKINDS


KINDNAME = {getattr(z3, n): n[len("Z3_OP_"):] for n in dir(z3) if n.startswith("Z3_OP_") and isinstance(getattr(z3, n), int)}
REALKINDS = {getattr(z3, n) for n in ("Z3_OP_ADD", "Z3_OP_SUB", "Z3_OP_MUL", "Z3_OP_DIV", "Z3_OP_IDIV", "Z3_OP_MOD", "Z3_OP_REM", "Z3_OP_TO_REAL", "Z3_OP_TO_INT",
                                       "Z3_OP_IS_INT", "Z3_OP_ANUM", "Z3_OP_LT", "Z3_OP_LE", "Z3_OP_GT", "Z3_OP_GE", "Z3_OP_UMINUS", "Z3_OP_POWER")}


def _fp_node(e):
    return _is_real(e) or e.sort().kind() in (z3.Z3_FLOATING_POINT_SORT, z3.Z3_ROUNDING_MODE_SORT) or _fp_kind(e.decl().kind())


def introduces_fp(args, res):
    """the function's own contribution has a floating-point or Real/Int kind or sort: the expansion of RES contains such a node that is not already inside an argument (an
    argument's own FP terms are the callee's business, not this function's)"""
    inside, st = set(), list(args)
    while st:
        e = st.pop()
        if e.get_id() in inside:
            continue
        inside.add(e.get_id())
        st.extend(e.children())
    seen, st = set(), [res]
    while st:
        e = st.pop()
        i = e.get_id()
        if i in seen or i in inside:
            continue
        seen.add(i)
        if _fp_node(e):
            return True
        st.extend(e.children())
    return False


def _is_term(v):
    return isinstance(v, z3.ExprRef)


_UF = {}


def _symbol(name, args, res):
    key = (name, tuple(a.sort().sexpr() for a in args), res.sort().sexpr())
    f = _UF.get(key)
    if f is None:
        f = _UF[key] = z3.Function(name, *[a.sort() for a in args], res.sort())
    return f


@contextlib.contextmanager
def instrument():
    """patch x87spec so that every part (i) function with an FP / Real expansion is an op symbol (module docstring); restored on exit. Yields the list of symbol keys created"""
    _, p1 = part_one()
    saved = {n: getattr(X, n) for n in p1}
    made = set()

    def wrap(name, orig):
        def w(*a, **kw):
            r = orig(*a, **kw)
            if not kw and a and all(_is_term(x) for x in a) and _is_term(r) and introduces_fp(a, r):
                f = _symbol(name, a, r)
                made.add((name, tuple(x.sort().sexpr() for x in a), r.sort().sexpr()))
                return f(*a)
            return r
        w.__name__ = name
        return w
    for n in p1:
        setattr(X, n, wrap(n, saved[n]))
    try:
        yield made
    finally:
        for n, f in saved.items():
            setattr(X, n, f)


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# Translator: z3 term -> DSL DAG.  One table, K2OP: z3 decl kind -> how the DSL node is made.  Anything else raises Unsupported naming the kind (no fallback).
from fnvnx.lift.fnvlift import dsl  # noqa: E402
from fnvnx.lift.fnvlift.dsl import C, V, mk, bin_, ite, eq, ult, slt, zext, sext, extract, concat  # noqa: E402

Z = z3
_BIN = {Z.Z3_OP_BADD: "add", Z.Z3_OP_BSUB: "sub", Z.Z3_OP_BMUL: "mul", Z.Z3_OP_BAND: "and", Z.Z3_OP_BOR: "or", Z.Z3_OP_BXOR: "xor", Z.Z3_OP_BSHL: "shl",
        Z.Z3_OP_BLSHR: "lshr", Z.Z3_OP_BASHR: "ashr", Z.Z3_OP_BUDIV: "udiv", Z.Z3_OP_BUREM: "urem", Z.Z3_OP_BUDIV_I: "udiv", Z.Z3_OP_BUREM_I: "urem"}
MEM_VAR, EF_VARS = "x87_m", ("x87_ef0", "x87_ef1", "x87_ef2")


def _simp(t):
    """z3's simplifier on T, as a pure function of T's structure: z3 orders operands and picks between equal rewrites by AST ids, a counter of the context, so the same term simplified after other work gives another
    shape.  A fresh context numbers its ASTs by the translation of T alone (a fixed traversal), so its simplifier's result depends on T only."""
    return z3.simplify(t.translate(z3.Context())).translate(t.ctx)


class Tr:
    """translates the z3 terms of one execute() run; ENV maps a z3 variable name to its DSL node (the DSL state inputs by their own names, the memory operand, the EFLAGS booleans)"""

    def __init__(s, env):
        s.env, s.memo, s.keep = env, {}, []      # keep: the z3 terms stay alive, so that an id is never reused for another term

    def arr(s, t):
        """an array term over BV3 -> a list of eight DSL nodes (the value at physical index 0..7)"""
        k = t.decl().kind()
        if k == Z.Z3_OP_CONST_ARRAY:
            return [s.t(t.arg(0))] * 8
        if k == Z.Z3_OP_STORE:
            a, i, v = s.arr(t.arg(0)), s.t(t.arg(1)), s.t(t.arg(2))
            return [ite(dsl.comm2("eq", i, C(j, 3)), v, a[j]) for j in range(8)]
        if k == Z.Z3_OP_ITE:
            c, a, b = s.t(t.arg(0)), s.arr(t.arg(1)), s.arr(t.arg(2))
            return [ite(c, a[j], b[j]) for j in range(8)]
        raise Unsupported("x87 op %s (array)" % KINDNAME.get(k, k))

    def sel(s, t):
        a, i = s.arr(t.arg(0)), s.t(t.arg(1))
        if i.op == "const":
            return a[i.x]
        r = a[7]
        for j in range(6, -1, -1):
            r = ite(dsl.comm2("eq", i, C(j, 3)), a[j], r)
        return r

    def t(s, e):
        r = s.memo.get(e.get_id())
        if r is None:
            s.keep.append(e)
            r = s.memo[e.get_id()] = s.one(e)
        return r

    def one(s, e):
        k = e.decl().kind()
        if e.sort().kind() == Z.Z3_ARRAY_SORT:
            raise Unsupported("x87 op %s (array value reaches an output)" % KINDNAME.get(k, k))
        if k == Z.Z3_OP_UNINTERPRETED:
            if Z.is_const(e):
                n = e.decl().name()
                if n not in s.env:
                    raise Unsupported("x87 variable %s" % n)
                return s.env[n]
            name = e.decl().name()
            if name in X.CARRIER_OPS:      # a carrier op (x87spec.CARRIER_OPS): the DSL node of the same name
                ws, rw = X.CARRIER_OPS[name]
                assert tuple(e.arg(j).size() for j in range(e.num_args())) == ws and e.size() == rw, name
                return dsl.cop(name, rw, *[s.t(e.arg(j)) for j in range(e.num_args())])
            raise Unsupported("x87 op symbol %s" % name)
        if _fp_node(e):
            raise Unsupported("x87 op %s" % KINDNAME.get(k, k))
        a = lambda j: s.t(e.arg(j))
        if k == Z.Z3_OP_BNUM:
            return C(e.as_long(), e.size())
        if k == Z.Z3_OP_TRUE:
            return C(1, 1)
        if k == Z.Z3_OP_FALSE:
            return C(0, 1)
        if k in _BIN:
            if _BIN[k] in dsl.COMM: return dsl.comm(_BIN[k], [a(j) for j in range(e.num_args())])      # z3's simplifier makes bvadd, bvmul, bvand, bvor and bvxor n-ary and orders the operands by its AST ids: canonical order by structure (dsl.comm)
            return bin_(_BIN[k], a(0), a(1))
        if k in (Z.Z3_OP_AND, Z.Z3_OP_OR):
            return dsl.comm("and" if k == Z.Z3_OP_AND else "or", [a(j) for j in range(e.num_args())])
        if k == Z.Z3_OP_NOT:
            return mk("not", 1, (a(0),)) if e.arg(0).sort().kind() == Z.Z3_BOOL_SORT else ~a(0)
        if k == Z.Z3_OP_BNOT:
            return ~a(0)
        if k == Z.Z3_OP_BNEG:
            return -a(0)
        if k == Z.Z3_OP_XOR:
            return dsl.comm("xor", [a(j) for j in range(e.num_args())])
        if k == Z.Z3_OP_EQ:
            return dsl.comm2("eq", a(0), a(1))
        if k == Z.Z3_OP_DISTINCT and e.num_args() == 2:
            return mk("not", 1, (dsl.comm2("eq", a(0), a(1)),))
        if k == Z.Z3_OP_ULT:
            return ult(a(0), a(1))
        if k == Z.Z3_OP_UGT:
            return ult(a(1), a(0))
        if k == Z.Z3_OP_ULEQ:
            return mk("not", 1, (ult(a(1), a(0)),))
        if k == Z.Z3_OP_UGEQ:
            return mk("not", 1, (ult(a(0), a(1)),))
        if k == Z.Z3_OP_SLT:
            return slt(a(0), a(1))
        if k == Z.Z3_OP_SGT:
            return slt(a(1), a(0))
        if k == Z.Z3_OP_SLEQ:
            return mk("not", 1, (slt(a(1), a(0)),))
        if k == Z.Z3_OP_SGEQ:
            return mk("not", 1, (slt(a(0), a(1)),))
        if k == Z.Z3_OP_ITE:
            return ite(a(0), a(1), a(2))
        if k == Z.Z3_OP_EXTRACT:
            hi, lo = e.params()
            return extract(a(0), hi, lo)
        if k == Z.Z3_OP_CONCAT:
            r = a(0)
            for j in range(1, e.num_args()):
                r = concat(r, a(j))
            return r
        if k == Z.Z3_OP_ZERO_EXT:
            return zext(a(0), e.size())
        if k == Z.Z3_OP_SIGN_EXT:
            return sext(a(0), e.size())
        if k == Z.Z3_OP_SELECT:
            return s.sel(e)
        raise Unsupported("x87 op %s" % KINDNAME.get(k, k))


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# The Form of an x87 class.
# Nondeterminism: the SDM leaves some results to the implementation (x87spec: Out.nd, the free variables, Out.nd_pre their admissible range).  The Form resolves each one to the value its
# implementation produces, ONE table keyed by the spec's nd names: NDRES[name] = (resolution(m), source).  `nd_names()` derives the names from the spec (every REGISTRY entry on a symbolic
# state); a name without a row leaves its classes uncertified, naming it.  The resolution is proved admissible (nd_pre(res) valid on the domain) and the Form equals spec[nd := res].
NDRES = {
    # SDM 2A:FCMOVcc Operation reads ST(i) only inside THEN, so a false condition with an empty ST(i) does nothing: no underflow.  The implementation's own definition is the same: fnvlift/f_x87.py
    # emits `if(cond) st0 = sti;` and the host registers are carriers that never fault.
    "nd.fcmov.uf_false": (lambda m: z3.BoolVal(False), "SDM 2A:FCMOVcc Operation (ST(i) is read only inside THEN); fnvlift/f_x87.py fcmovcc: if(cond) ST0=STi"),
    # SDM 2A:FBLD: a digit above 9 gives an undefined result.  rt/x87.c fnvl_x_from_bcd (ASM-216) computes the sum of d_k*10^k (digits taken as 0..15, sign from bit 79, -0 keeps the sign):
    # exactly x87spec.bcd_to_img, whose Horner form is the same arithmetic on every digit value.
    "nd.fbld.img": (lambda m: X.bcd_to_img(m), "rt/x87.c fnvl_x_from_bcd (ASM-216: digits above 9 follow d*10^k) = x87spec.bcd_to_img"),
}


def nd_names():
    """the nd variable names of every REGISTRY entry, derived by running the spec"""
    out = set()
    for key in X.REGISTRY:
        e = X.REGISTRY[key]
        m = z3.BitVec("nm", e["mem_bits"]) if e["mem_bits"] else None
        ef = tuple(z3.Bool("ne%d" % j) for j in range(3)) if e["uses_ef"] else None
        for i in (range(8) if e["uses_i"] else (None,)):
            o = X.execute(key, X.from_dsl(pre_vars()), m, i, ef)
            out.update(str(v) for v in o.nd)
    return out


def spec_key(raw):
    """(REGISTRY key, register index i or None) of the x87 instruction RAW: the class of x87class, in the spec's key spelling.  None when RAW is no x87 instruction"""
    import x87class
    from fnvnx.lift.proof import shape
    i = 0
    while i < len(raw) and raw[i] in shape.PFX:
        i += 1
    if i >= len(raw) or not (0xd8 <= raw[i] <= 0xdf or raw[i] == 0x9b):
        return None
    op, kind, reg, rm = x87class.x87_class(raw)
    key = ("%02x" % op, "-" if kind == "op" else kind, reg, rm)
    return key, ((raw[i + 1] & 7) if kind == "reg" else None)


def pre_vars():
    return {n: z3.BitVec(n, w) for n, w in X.DSL_WIDTH.items()}


def mem_pieces(bits):
    """[(bit offset, width)] of the memory operand: one piece when it fits the DSL's 128-bit values; otherwise the layout of the SDM figure the spec follows (1:8.1.10, Figure 8-9, x87spec.ENV_BITS):
    the 32-bit words of the environment image, then the 80-bit registers that follow it (FRSTOR / FNSAVE, x87spec.SAVE_BITS)"""
    if bits <= 128:
        return [(0, bits)]
    return [(o, 32) for o in range(0, X.ENV_BITS, 32)] + [(o, 80) for o in range(X.ENV_BITS, bits, 80)]


_RUN = {}


def run(key, i):
    """-> (pre-state vars, Out, memory pieces [(offset, width, z3 var)] or None, ef vars or None) of the spec entry on the symbolic DSL pre-state; one run per (class, i), op symbols in place"""
    r = _RUN.get((key, i))
    if r is None:
        e = X.REGISTRY[key]
        d = pre_vars()
        pcs = [(o, w, z3.BitVec("%s_%d" % (MEM_VAR, o), w)) for o, w in mem_pieces(e["mem_bits"])] if e["mem_bits"] else None
        m = None if pcs is None else (pcs[0][2] if len(pcs) == 1 else z3.Concat(*[v for _, _, v in reversed(pcs)]))
        ef = tuple(z3.Bool(n) for n in EF_VARS) if e["uses_ef"] else None
        with instrument():
            o = X.execute(key, X.from_dsl(d), m, i, ef)
        r = _RUN[(key, i)] = (d, o, pcs, ef, m)
    return r


def resolve(o, m):
    """-> (substitution list nd := resolution, names without a row)"""
    sub, miss = [], []
    for v in o.nd:
        row = NDRES.get(str(v))
        if row is None:
            miss.append(str(v))
        else:
            sub.append((v, row[0](m)))
    return sub, miss


def mem_nodes(c, pcs):
    """the DSL node of every memory piece: loads of 64/32/16/8 bits, the lowest address first.  -> (address node, [node])"""
    from fnvnx.lift.proof import forms
    a = forms.ea(c.ops[0])
    out = []
    for off, w, _ in pcs:
        parts, o2, left = [], off, w
        while left:
            k = next(k for k in (64, 32, 16, 8) if k <= left)
            parts.append(forms.mload(a if o2 == 0 else forms.addoff(a, C(o2 // 8, 32)), k))
            o2, left = o2 + k, left - k
        n = parts[0]
        for p in parts[1:]:
            n = concat(p, n)
        out.append(n)
    return a, out


def domain_terms(o, sub):
    """the domain of the instruction (the ruling after slice 1): no x87 exception is unmasked (FCW[5:0] all set: the masks fact, every FLDCW / FLDENV / FRSTOR / FXRSTOR site keeps them), so no FLT fault and
    no unmasked-PE store; the pending-exception trap (ES) is not set; the entry's own precondition Out.pre; the admissible range of its nondeterminism, nd_pre, at the resolution"""
    S = (lambda t: z3.substitute(t, *sub)) if sub else (lambda t: t)
    s0 = X.from_dsl(pre_vars())
    fcw = z3.BitVec("fcw", 16)
    inv = (z3.Extract(X.ES, X.ES, s0.fsw) == X.K(1, 1)) == X.es_of(s0.fsw, s0.fcw)      # ES is stored state, recomputed by every writing instruction (sync_es): the invariant that it holds the derived value
    d = [("masks", z3.Extract(5, 0, fcw) == z3.BitVecVal(0x3f, 6)), ("ES invariant", inv), ("pre", S(o.pre)), ("nd_pre", S(o.nd_pre))]
    if o.eflags is not None:
        d.append(("eflags written", S(o.eflags[3])))      # the EFLAGS write happens unless an unmasked IE / DE aborts it (SDM 2A:FCOMI Operation); the masks fact excludes that
    return d


def carrier_de_refusal(o):
    """the reason an Out has no Form (ruling 3b), or None: its DE is evaluated on a register image, whose carrier kind the spec state does not hold (x87spec CARRIER_DE)"""
    if getattr(o, "carrier_de", False):
        return "x87 DE of a register operand is defined at its carrier type (OWNER 8): the carrier kind is not in the spec state (x87spec CARRIER_DE)"
    return None


def effects(c, key, i):
    """fill c.F from the spec entry (class KEY, register index I).  Raises Unsupported naming the first thing outside the Form"""
    from fnvnx.lift.proof import forms
    F = c.F
    c.x87_mem = (None, None)
    d, o, pcs, ef, m = run(key, i)
    why = carrier_de_refusal(o)
    if why:
        raise Unsupported(why)
    env = {n: V(n, w) for n, w in X.DSL_WIDTH.items()}
    a = None
    if pcs is not None:
        a, nodes = mem_nodes(c, pcs)
        env.update({v.decl().name(): n for (_, _, v), n in zip(pcs, nodes)})
        c.x87_mem = (a, [(v, n) for (_, _, v), n in zip(pcs, nodes)])
    if ef is not None:
        env.update(zip(EF_VARS, (V("zf", 1), V("pf", 1), V("cf", 1))))
    tr = Tr(env)
    sub, miss = resolve(o, m)
    if miss:
        raise Unsupported("x87 nondeterminism without a resolution: %s" % ",".join(miss))
    S = (lambda t: z3.substitute(t, *sub)) if sub else (lambda t: t)
    # o.undef / o.mem_undef (the bits the SDM leaves Undefined / reserved): the Form takes the spec's own value for them, a refinement of "any value"; the proof compares exactly
    post = {k: _simp(S(v)) for k, v in X.to_dsl(o.s).items()}
    unch = {k: z3.eq(v, d[k]) for k, v in post.items()}      # the field the instruction leaves as it was (z3's own simplifier: the spec's finish() ites)
    node = {k: tr.t(v) for k, v in post.items() if not unch[k]}
    if "fcw" in node:
        F.fcw(node["fcw"])
    for j in range(8):
        if "fr%d" % j in node:
            F.fr(j, node["fr%d" % j])
    for k, f in (("ftop", F.ftop), ("ftag", F.ftag), ("fsw", F.fsw)):
        if k in node:
            f(node[k])
    if any(k in node for k in ("fip", "fdp", "fop", "fcs", "fds")):
        F.fptr(*[node[k] if k in node else V(k, X.DSL_WIDTH[k]) for k in ("fip", "fdp", "fop", "fcs", "fds")])
    if o.mem is not None:
        w = tr.t(_simp(S(o.wr)))
        wn = None if w.op == "const" and w.x == 1 else w
        mv = _simp(S(o.mem))
        for off, wd in mem_pieces(o.mem.size()):
            forms.mstore(F, a if off == 0 else forms.addoff(a, C(off // 8, 32)), tr.t(_simp(z3.Extract(off + wd - 1, off, mv))), wn)
    if o.ax is not None:
        F.reg("eax", concat(extract(V("eax", 32), 31, 16), tr.t(_simp(S(o.ax)))))
    if o.eflags is not None:      # FCOMI / FUCOMI (SDM 2A:FCOMI Flags): ZF, PF, CF from the compare, OF, SF, AF cleared; the spec's value is the Form's where the SDM leaves them undefined (stack underflow)
        for f, t in zip(("zf", "pf", "cf"), o.eflags[:3]):
            F.flag(f, tr.t(_simp(S(t))))
        for f in ("of", "sf", "af"):
            F.flag(f, C(0, 1))
    for _, t in domain_terms(o, sub):
        F.require(tr.t(_simp(t)))


def build(c):
    """the Form of the x87 instruction of the build context C (proof/forms.build), or None when the instruction is not x87.  Raises Unsupported when the class has no Form"""
    r = spec_key(c.raw)
    if r is None:
        return None
    key, i = r
    if key not in X.REGISTRY:
        raise Unsupported("no spec entry %s" % (key,))
    effects(c, key, i)
    c.F.x87 = (key, i) + c.x87_mem
    return c.F


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# Proof: on the Form's domain, the Form's post-state (dsl.Form.post, z3) equals execute()[nd := resolution] on every field; the resolution is admissible; the domain is satisfiable.
def prove_form(F):
    """-> (result, note, extras).  F.x87 = (class key, i, address node or None, [(piece var, piece node)] or None).  Every query is unsat at bounds.Z3_RLIMIT, under the Form's own domain
    (F.domain, translated by dsl.to_smt): nd_pre at the resolution holds; then each field of the Form's z3 post-state equals the spec's: the x87 registers, TOP, tags, FSW, FCW, the pointers,
    memory, EAX, the other registers, the flags, EIP, the SSE state.  The Form has no fault: the domain excludes every x87 exception (domain_terms)."""
    from fnvnx.lift.fnvlift import dsl as D
    from fnvnx.lift.proof import bounds
    key, i, a, pn = F.x87
    d, o, pcs, ef, m = run(key, i)
    P = F.post()
    sub0, _ = resolve(o, m)
    sub = list(sub0)
    if pn is not None:
        sub.extend((v, D.to_smt(n)) for v, n in pn)
    if ef is not None:
        sub.extend((v, z3.BitVec(n, 1) == 1) for v, n in zip(ef, ("zf", "pf", "cf")))
    rest = sub[len(sub0):]      # the resolution may mention the memory variable: substitute the resolution first, then the pieces and the EFLAGS booleans
    def S(t):
        t = z3.substitute(t, *sub0) if sub0 else t
        return z3.substitute(t, *rest) if rest else t
    post = {k: S(v) for k, v in X.to_dsl(o.s).items()}
    mem0 = D.mem_array()
    mem = mem0
    if o.mem is not None:
        mv = S(o.mem)
        m2 = mem0
        for b in range(o.mem.size() // 8):
            m2 = z3.Store(m2, D.to_smt(a) + b, z3.Extract(8 * b + 7, 8 * b, mv))
        mem = z3.If(S(o.wr), m2, mem0)
    pre = lambda n: z3.BitVec(n, X.DSL_WIDTH[n])
    want = {}
    for j in range(8):
        want["fr%d" % j] = (P["fr"][j], post["fr%d" % j])
    for k in ("ftop", "ftag", "fsw", "fcw"):
        want[k] = (P[k], post[k])
    for k, v in zip(("fip", "fdp", "fop", "fcs", "fds"), P["fptr"]):
        want[k] = (v, post[k])
    want["mem"] = (P["mem"], mem)
    for r in D.REGS:
        want[r] = (P["regs"][r], z3.Concat(z3.Extract(31, 16, z3.BitVec(r, 32)), S(o.ax)) if (r == "eax" and o.ax is not None) else z3.BitVec(r, 32))
    one, zero = z3.BitVecVal(1, 1), z3.BitVecVal(0, 1)
    ef_post = {}
    if o.eflags is not None:
        ef_post = {f: z3.If(S(t), one, zero) for f, t in zip(("zf", "pf", "cf"), o.eflags[:3])}
        ef_post.update({f: zero for f in ("of", "sf", "af")})
    for f in D.FLAGS + ("df",):
        want[f] = (P["flags"][f], ef_post.get(f, z3.BitVec(f, 1)))
    want["eip"] = (P["eip"], z3.BitVecVal(F.eip.x, 32))
    for j in range(8):
        want["xmm%d" % j] = (P["xmm"][j], z3.BitVec("xmm%d" % j, 128))
    want["mxcsr"] = (P["mxcsr"], z3.BitVec("mxcsr", 32))
    dom = z3.And(*[D.to_smt(n) == 1 for n in F.domain]) if F.domain else z3.BoolVal(True)
    from fnvnx.lift.fnvlift import faults as FT
    av = FT.BYNAME["AV"]["code"]      # the only fault a Form may carry: forms.mstore's code-segment store fault (selector-based, the state is then the pre-state)
    nf = P["fault"] == 0
    es_post = z3.Extract(X.ES, X.ES, P["fsw"]) == X.K(1, 1)
    checks = [("nd_pre", dom, z3.Not(S(o.nd_pre))), ("trap", dom, S(o.trap)), ("faults", dom, z3.And(P["fault"] != 0, P["fault"] != av)),
              ("ES invariant preserved", z3.And(dom, nf), es_post != X.es_of(P["fsw"], P["fcw"]))] + [(k, z3.And(dom, nf), x != y) for k, (x, y) in want.items()]
    for name, hyp, bad in checks:
        sv = z3.Solver()
        sv.set("rlimit", bounds.Z3_RLIMIT)
        sv.add(hyp, bad)
        r = sv.check()
        if r != z3.unsat:
            return ("FAILED(x87)" if r == z3.sat else bounds.BOUND), "x87 %s: the Form %s the spec on its domain (%s)" % (name, "differs from" if r == z3.sat else "is unknown against", r), {}
    return "proved", "", {"x87_fields": len(checks)}
