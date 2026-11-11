"""Exact rewrites of model terms before they meet the DSL term in the solver.
narrow_div: the Sail model divides 128-bit zero-/sign-extended copies of the 64-bit (or 32/16-bit) operands (bvsdiv_i on 128 bits).  Equivalence of a 128-bit and a
64-bit divider is out of reach of the bit-blaster, so every bvudiv/bvsdiv whose operands are (provably, one small z3 check each) extensions of their low half is replaced by
    udiv:  If(b == 0, ones, ZeroExt(udiv_n(a, b)))        sdiv:  If(b == 0, <bvsdiv(A,0)>, If(a == MIN and b == -1, <bvsdiv(MIN,-1)>, SignExt(sdiv_n(a, b))))
which are identities of the SMT-LIB bit-vector semantics (the special cases keep the original term with the operands substituted, so nothing is assumed).
pow2_mul: x * 2^k (0 < k < width) = concat(x[w-1-k:0], 0^k) (a shift by a constant is a wiring, not a multiplier; the DSL writes `base + index * scale`, the model
shifts), an identity of the bit-vector semantics; applied to the terms of BOTH sides before the query, it makes the two address computations of a SIB operand
syntactically equal (z3 otherwise bit-blasts the multiplier against the wiring, seconds per query).
rem_def: bvurem(a, b) = If(b = 0, a, a - b * bvudiv(a, b)): the SMT-LIB definition of the remainder (nat(a) mod nat(b), a - b * (a div b) over the naturals, for b != 0; a for b = 0).
The model computes the remainder as `a - b * floor(a / b)` (Sail `mod` = floor2 identity), the DSL with bvurem; after the rewrite both sides contain the SAME bvudiv term
(z3 cannot relate a divider and a remainder circuit: even the 16-bit instance of this identity is `unknown` after 60 s).
low_extract: Extract(h, 0, a + b | a - b | a * b) = Extract(h, 0, a) op Extract(h, 0, b) (arithmetic modulo 2^n commutes with truncation: the reduction mod 2^(h+1) is a ring
homomorphism), so a 32-bit remainder taken from the low half of a 64-bit expression meets the 32-bit expression of the other side term by term."""
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
        zx = is_ext(a, n, False) and is_ext(b, n, False)          # both operands non-negative extensions: sdiv = udiv
        if zx and k == 's': k = 'u'
        if zx or (k == 's' and is_ext(a, n, True) and is_ext(b, n, True)):
            x, y = z3.Extract(n - 1, 0, a), z3.Extract(n - 1, 0, b); zero = z3.BitVecVal(0, n)
            if k == 'u': r = z3.If(y == 0, z3.simplify(z3.substitute(r, (b, z3.BitVecVal(0, W)))), z3.ZeroExt(W - n, z3.UDiv(x, y)))
            else:
                mn, m1 = z3.BitVecVal(1 << (n - 1), n), z3.BitVecVal((1 << n) - 1, n)
                spec = lambda av, bv: z3.simplify(z3.substitute(r, (a, av), (b, bv)))
                r = z3.If(y == zero, z3.simplify(z3.substitute(r, (b, z3.BitVecVal(0, W)))),
                          z3.If(z3.And(x == mn, y == m1), spec(z3.SignExt(W - n, mn), z3.SignExt(W - n, m1)), z3.SignExt(W - n, x / y)))
    memo[t.get_id()] = r; return r

def pow2_mul(t, memo=None):
    memo = {} if memo is None else memo
    r = memo.get(t.get_id())
    if r is not None: return r
    ch = [pow2_mul(c, memo) for c in t.children()]
    r = t if all(a.eq(b) for a, b in zip(ch, t.children())) else t.decl()(*ch)
    if z3.is_app(t) and t.decl().kind() == z3.Z3_OP_BMUL and r.num_args() == 2:
        a, b = r.arg(0), r.arg(1)
        if z3.is_bv_value(a): a, b = b, a
        if z3.is_bv_value(b):
            c, w = b.as_long(), b.size()
            if c > 1 and c & (c - 1) == 0 and c.bit_length() - 1 < w:
                k = c.bit_length() - 1; r = z3.Concat(z3.Extract(w - 1 - k, 0, a), z3.BitVecVal(0, k))
    memo[t.get_id()] = r; return r

def div_norm(t, memo=None):
    memo = {} if memo is None else memo
    r = memo.get(t.get_id())
    if r is not None: return r
    ch = [div_norm(c, memo) for c in t.children()]
    r = t if all(a.eq(b) for a, b in zip(ch, t.children())) else t.decl()(*ch)
    k = t.decl().kind() if z3.is_app(t) else None
    if k == z3.Z3_OP_BUREM and r.num_args() == 2:
        a, b = r.arg(0), r.arg(1); r = z3.If(b == 0, a, a - b * z3.UDiv(a, b))
    elif k == z3.Z3_OP_EXTRACT and t.params()[1] == 0 and r.arg(0).decl().kind() in (z3.Z3_OP_BADD, z3.Z3_OP_BSUB, z3.Z3_OP_BMUL) and r.arg(0).size() > t.size():
        h = t.params()[0]; x = r.arg(0); xs = [z3.Extract(h, 0, c) for c in x.children()]
        r = {z3.Z3_OP_BADD: lambda v: sum(v[1:], v[0]), z3.Z3_OP_BSUB: lambda v: v[0] - v[1], z3.Z3_OP_BMUL: lambda v: v[0] * v[1] if len(v) == 2 else None}[x.decl().kind()](xs)
    memo[t.get_id()] = r; return r
