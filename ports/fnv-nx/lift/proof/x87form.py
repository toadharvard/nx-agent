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
from fnvlift import dsl  # noqa: E402
from fnvlift.dsl import C, V, mk, bin_, ite, eq, ult, slt, zext, sext, extract, concat  # noqa: E402

Z = z3
_BIN = {Z.Z3_OP_BADD: "add", Z.Z3_OP_BSUB: "sub", Z.Z3_OP_BMUL: "mul", Z.Z3_OP_BAND: "and", Z.Z3_OP_BOR: "or", Z.Z3_OP_BXOR: "xor", Z.Z3_OP_BSHL: "shl",
        Z.Z3_OP_BLSHR: "lshr", Z.Z3_OP_BASHR: "ashr", Z.Z3_OP_BUDIV: "udiv", Z.Z3_OP_BUREM: "urem", Z.Z3_OP_BUDIV_I: "udiv", Z.Z3_OP_BUREM_I: "urem"}
MEM_VAR, EF_VARS = "x87_m", ("x87_ef0", "x87_ef1", "x87_ef2")


class Tr:
    """translates the z3 terms of one execute() run; ENV maps a z3 variable name to its DSL node (the DSL state inputs by their own names, the memory operand, the EFLAGS booleans)"""

    def __init__(s, env):
        s.env, s.memo = env, {}

    def arr(s, t):
        """an array term over BV3 -> a list of eight DSL nodes (the value at physical index 0..7)"""
        k = t.decl().kind()
        if k == Z.Z3_OP_CONST_ARRAY:
            return [s.t(t.arg(0))] * 8
        if k == Z.Z3_OP_STORE:
            a, i, v = s.arr(t.arg(0)), s.t(t.arg(1)), s.t(t.arg(2))
            return [ite(eq(i, C(j, 3)), v, a[j]) for j in range(8)]
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
            r = ite(eq(i, C(j, 3)), a[j], r)
        return r

    def t(s, e):
        r = s.memo.get(e.get_id())
        if r is None:
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
            raise Unsupported("x87 op symbol %s" % e.decl().name())
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
            return bin_(_BIN[k], a(0), a(1))
        if k in (Z.Z3_OP_AND, Z.Z3_OP_OR):
            r = a(0)
            for j in range(1, e.num_args()):
                r = bin_("and" if k == Z.Z3_OP_AND else "or", r, a(j))
            return r
        if k == Z.Z3_OP_NOT:
            return mk("not", 1, (a(0),)) if e.arg(0).sort().kind() == Z.Z3_BOOL_SORT else ~a(0)
        if k == Z.Z3_OP_BNOT:
            return ~a(0)
        if k == Z.Z3_OP_BNEG:
            return -a(0)
        if k == Z.Z3_OP_XOR:
            return a(0) ^ a(1)
        if k == Z.Z3_OP_EQ:
            x, y = a(0), a(1)
            return eq(x, y)
        if k == Z.Z3_OP_DISTINCT and e.num_args() == 2:
            return mk("not", 1, (eq(a(0), a(1)),))
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
FLTS = ("FLT_SF", "FLT_IE", "FLT_DE", "FLT_ZE", "FLT_OE", "FLT_UE", "FLT_PE")


def spec_key(raw):
    """(REGISTRY key, register index i or None) of the x87 instruction RAW: the class of x87class, in the spec's key spelling.  None when RAW is no x87 instruction"""
    import x87class
    from proof import shape
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


_RUN = {}


def run(key, i):
    """-> (pre-state vars, Out, memory var or None, ef vars or None) of the spec entry on the symbolic DSL pre-state; one run per (class, i), with the op symbols in place"""
    r = _RUN.get((key, i))
    if r is None:
        e = X.REGISTRY[key]
        d = pre_vars()
        m = z3.BitVec(MEM_VAR, e["mem_bits"]) if e["mem_bits"] else None
        ef = tuple(z3.Bool(n) for n in EF_VARS) if e["uses_ef"] else None
        with instrument():
            o = X.execute(key, X.from_dsl(d), m, i, ef)
            fk = X.fault_kind(o) if o.fcw_in is not None else z3.BitVecVal(0, 8)
            s0 = X.from_dsl(d)

            class P:      # the pending exception of a waiting instruction: the flags and SF in FSW, under the control word in force (fault_kind's docstring)
                raised = z3.Extract(5, 0, s0.fsw)
                sf_raised = z3.Extract(X.SF, X.SF, s0.fsw) == X.K(1, 1)
                fcw_in = s0.fcw
            tk = X.fault_kind(P)
        r = _RUN[(key, i)] = (d, o, m, ef, z3.If(o.trap, tk, fk))
    return r


def mem_node(c, bits):
    """the DSL value of the memory operand of BITS bits: loads of 64/32/16/8 bits, the lowest address first (little endian)"""
    from proof import forms
    a = forms.ea(c.ops[0])
    parts, off, left = [], 0, bits
    while left:
        w = next(w for w in (64, 32, 16, 8) if w <= left)
        parts.append(forms.mload(a if off == 0 else forms.addoff(a, C(off // 8, 32)), w))
        off, left = off + w, left - w
    n = parts[0]
    for p in parts[1:]:
        n = concat(p, n)
    return a, n


def effects(c, key, i):
    """fill c.F from the spec entry (class KEY, register index I).  Raises Unsupported naming the first thing outside this slice"""
    from proof import forms
    F = c.F
    c.x87_mem = (None, None)
    d, o, m, ef, tk = run(key, i)
    e = X.REGISTRY[key]
    env = {n: V(n, w) for n, w in X.DSL_WIDTH.items()}
    a = None
    if m is not None:
        if e["mem_bits"] > 128 and o.mem is not None:
            raise Unsupported("x87 store of %d bits: the DSL value type holds 128" % e["mem_bits"])
        a, env[MEM_VAR] = mem_node(c, e["mem_bits"])
        c.x87_mem = (a, env[MEM_VAR])
    if ef is not None:
        env.update(zip(EF_VARS, (V("zf", 1), V("pf", 1), V("cf", 1))))
    tr = Tr(env)
    if o.nd:
        raise Unsupported("x87 nondeterminism (the SDM leaves a choice: %s)" % ",".join(str(v) for v in o.nd))
    if not z3.is_true(z3.simplify(o.pre)):
        raise Unsupported("x87 domain: the entry has a precondition the Form does not carry")
    # o.undef / o.mem_undef (the bits the SDM leaves Undefined / reserved): the Form takes the spec's own value for them, a refinement of "any value"; the proof compares exactly
    if o.eflags is not None:
        raise Unsupported("x87 EFLAGS effect (fcomi): not in this slice")
    post = X.to_dsl(o.s)
    unch = {k: z3.eq(z3.simplify(v), d[k]) for k, v in post.items()}      # the field the instruction leaves as it was (z3's own simplifier: the spec's finish() ites)
    if not unch["fcw"]:
        raise Unsupported("x87 effect fcw: the Form has no control-word effect")
    node = {k: tr.t(v) for k, v in post.items() if not unch[k]}
    same = lambda k: unch[k]
    for j in range(8):
        if not same("fr%d" % j):
            F.fr(j, node["fr%d" % j])
    if not same("ftop"):
        F.ftop(node["ftop"])
    if not same("ftag"):
        F.ftag(node["ftag"])
    if not same("fsw"):
        F.fsw(node["fsw"])
    if not all(same(k) for k in ("fip", "fdp", "fop", "fcs", "fds")):
        F.fptr(*[node[k] for k in ("fip", "fdp", "fop", "fcs", "fds")])
    if o.mem is not None:
        w = tr.t(o.wr)
        forms.mstore(F, a, tr.t(o.mem), None if w.op == "const" and w.x == 1 else w)
    if o.ax is not None:
        F.reg("eax", concat(extract(V("eax", 32), 31, 16), tr.t(o.ax)))
    T = tr.t(tk)
    for name in FLTS:
        code = dsl._FBY[name]["code"]
        F.fault(code, eq(T, C(code, 8)))


def build(c):
    """the Form of the x87 instruction of the build context C (proof/forms.build), or None when the instruction is not x87.  Raises Unsupported when the class has no Form in this slice"""
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
# Proof: the Form's post-state (dsl.Form.post, z3) equals execute() on every field.
def prove_form(F):
    """-> (result, note, extras).  F.x87 = (class key, i, address node or None, memory-operand node or None).  The Form's z3 post-state is compared with the spec's, field by field,
    each one an unsat query at bounds.Z3_RLIMIT: the fault kind always (the trap first); every other field when the Form raises no fault (dsl.Form.post: on a fault the state is the
    pre-state): the x87 registers, TOP, tags, FSW, FCW, the pointers, memory, EAX, the other registers, the flags, EIP, the SSE state.  The spec's own state under an unmasked exception
    (the flags set in FSW, an unmasked PE stores the result) is outside that semantics: it is not proved here (see the report)."""
    from fnvlift import dsl as D
    from proof import bounds
    key, i, a, mn = F.x87
    d, o, m, ef, tk = run(key, i)
    P = F.post()
    sub = []
    if m is not None:
        sub.append((m, D.to_smt(mn)))
    if ef is not None:
        sub.extend((v, z3.BitVec(n, 1) == 1) for v, n in zip(ef, ("zf", "pf", "cf")))
    S = lambda t: z3.substitute(t, *sub) if sub else t
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
    for k in ("ftop", "ftag", "fsw"):
        want[k] = (P[k], post[k])
    for k, v in zip(("fip", "fdp", "fop", "fcs", "fds"), P["fptr"]):
        want[k] = (v, post[k])
    want["fcw"] = (pre("fcw"), post["fcw"])
    want["mem"] = (P["mem"], mem)
    for r in D.REGS:
        want[r] = (P["regs"][r], z3.Concat(z3.Extract(31, 16, z3.BitVec(r, 32)), S(o.ax)) if (r == "eax" and o.ax is not None) else z3.BitVec(r, 32))
    for f in D.FLAGS + ("df",):
        want[f] = (P["flags"][f], z3.BitVec(f, 1))
    want["eip"] = (P["eip"], z3.BitVecVal(F.eip.x, 32))
    for j in range(8):
        want["xmm%d" % j] = (P["xmm"][j], z3.BitVec("xmm%d" % j, 128))
    want["mxcsr"] = (P["mxcsr"], z3.BitVec("mxcsr", 32))
    nf = P["fault"] == 0
    checks = [("fault", z3.BoolVal(True), P["fault"], S(tk))] + [(k, nf, x, y) for k, (x, y) in want.items()]
    for name, hyp, x, y in checks:
        sv = z3.Solver()
        sv.set("rlimit", bounds.Z3_RLIMIT)
        sv.add(hyp, x != y)
        r = sv.check()
        if r != z3.unsat:
            return ("FAILED(x87)" if r == z3.sat else bounds.BOUND), "x87 field %s: the Form %s the spec (%s)" % (name, "differs from" if r == z3.sat else "is unknown against", r), {}
    return "proved", "", {"x87_fields": len(checks)}
