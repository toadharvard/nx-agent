"""Exact rewrites of model terms before they meet the DSL term in the solver.
narrow_div: the Sail model divides 128-bit zero-/sign-extended copies of the 64-bit (or 32/16-bit) operands (bvsdiv_i on 128 bits).  Equivalence of a 128-bit and a
64-bit divider is out of reach of the bit-blaster, so every bvudiv/bvsdiv whose operands are (provably, one small z3 check each) extensions of their low half is replaced by
    udiv:  If(b == 0, ones, ZeroExt(udiv_n(a, b)))        sdiv:  If(b == 0, <bvsdiv(A,0)>, If(a == MIN and b == -1, <bvsdiv(MIN,-1)>, SignExt(sdiv_n(a, b))))
which are identities of the SMT-LIB bit-vector semantics (the special cases keep the original term with the operands substituted, so nothing is assumed)."""
import z3
K = {z3.Z3_OP_BUDIV: 'u', z3.Z3_OP_BUDIV_I: 'u', z3.Z3_OP_BSDIV: 's', z3.Z3_OP_BSDIV_I: 's'}
def is_ext(a, n, signed):
    ext = z3.SignExt(a.size() - n, z3.Extract(n - 1, 0, a)) if signed else z3.ZeroExt(a.size() - n, z3.Extract(n - 1, 0, a))
    s = z3.Solver(); s.set('timeout', 5000); s.add(a != ext); return s.check() == z3.unsat
def narrow_div(t, memo=None):
    memo = {} if memo is None else memo
    r = memo.get(t.get_id())
    if r is not None: return r
    ch = [narrow_div(c, memo) for c in t.children()]
    r = t if all(a.eq(b) for a, b in zip(ch, t.children())) else t.decl()(*ch)
    k = K.get(t.decl().kind() if z3.is_app(t) else None)
    if k and r.num_args() == 2 and r.arg(0).size() >= 16:
        a, b = r.arg(0), r.arg(1); W = a.size(); n = W // 2
        if is_ext(a, n, k == 's') and is_ext(b, n, k == 's'):
            x, y = z3.Extract(n - 1, 0, a), z3.Extract(n - 1, 0, b); zero = z3.BitVecVal(0, n)
            if k == 'u': r = z3.If(y == 0, z3.simplify(z3.substitute(r, (b, z3.BitVecVal(0, W)))), z3.ZeroExt(W - n, z3.UDiv(x, y)))
            else:
                mn, m1 = z3.BitVecVal(1 << (n - 1), n), z3.BitVecVal((1 << n) - 1, n)
                spec = lambda av, bv: z3.simplify(z3.substitute(r, (a, av), (b, bv)))
                r = z3.If(y == zero, z3.simplify(z3.substitute(r, (b, z3.BitVecVal(0, W)))),
                          z3.If(z3.And(x == mn, y == m1), spec(z3.SignExt(W - n, mn), z3.SignExt(W - n, m1)), z3.SignExt(W - n, x / y)))
    memo[t.get_id()] = r; return r
