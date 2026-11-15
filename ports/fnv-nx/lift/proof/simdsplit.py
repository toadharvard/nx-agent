"""The per-lane split of the MXCSR status-flag obligation of packed SSE FP arithmetic (called by simdcheck._discharge).

Problem: bit k (k < 6) of the post-MXCSR is  mxcsr_k | f(lane 0) | f(lane 1) | ...  on both sides (Form and spec), and each f depends on the two operands of ITS lane.  Asked whole, the bit depends on 4 (pd) or 8 (ps)
operands and the SAT core times out.  The rule, in four steps (each a sound restriction or an equivalence, never a heuristic):
  1. environment: the spec's environment `env` fixes MXCSR bits (the conjuncts  K == Extract(h, l, mxcsr)  and  0 == mxcsr & M); mxcsr is replaced by the same value with those bits substituted
     (a restriction to the states satisfying env, which is a hypothesis of every query).
  2. no-fault: `nf` (the Form raises no fault) simplified under step 1 is a conjunction of  Extract(h, l, v) == 0  atoms (the alignment of the address); v is replaced by itself with those bits cleared
     (a restriction to the states satisfying nf, a hypothesis of the state parts).  Any other shape: the rule does not apply (None) and the part is asked whole.
  3. abstraction: every FP lane operand of the row (fpl) becomes a fresh variable `opvN` (the same substitution on both sides, as simdcheck._canonical).
  4. disjuncts: both sides, simplified, are flattened over bit-or; identical disjuncts cancel; the remaining disjuncts are matched by the set of variables they read.  The two sides are equal if the
     matching is a bijection and every matched pair is equal (or is commutative and associative, so equal disjunct multisets are equal terms).  Each pair reads at most two operands and MXCSR; it is decided by the
     case product  RC (4) x FTZ (1 bit) x class(operand) per operand  (classes(): exponent zero / ones / other x fraction zero / nonzero), a complete and disjoint partition of every bit pattern.
A pair that is not proved leaves the part to the whole query (None): matching is sufficient, not necessary, so a failed pair is never a counter-example.  completeness_theorem() proves the partitions with z3."""
import hashlib, os
import z3

_PROVED, _UNDECIDED = set(), set()          # canonical piece queries of this process: proved (unsat) / not decided within the limit

EXP = {32: (8, 23), 64: (11, 52)}


def classes(x, w):
    """the five classes of a binary w operand, a complete and disjoint partition of its bit patterns: exponent all zeros / all ones / other  x  fraction zero / nonzero"""
    eb, fb = EXP[w]; ex = z3.Extract(w - 2, fb, x); fr = z3.Extract(fb - 1, 0, x)
    z, ones = z3.BitVecVal(0, eb), z3.BitVecVal((1 << eb) - 1, eb); fz = fr == z3.BitVecVal(0, fb)
    return [z3.And(ex == z, fz), z3.And(ex == z, z3.Not(fz)), z3.And(ex == ones, fz), z3.And(ex == ones, z3.Not(fz)), z3.And(z3.Not(ex == z), z3.Not(ex == ones))]


def rc_ftz(mx):
    """MXCSR.RC (bits 14:13, 4 values) and MXCSR.FTZ (bit 15, 2 values): two complete, disjoint partitions"""
    return [[z3.Extract(14, 13, mx) == z3.BitVecVal(v, 2) for v in range(4)], [z3.Extract(15, 15, mx) == z3.BitVecVal(v, 1) for v in range(2)]]


def completeness_theorem():
    """-> {name: True}: z3 proves, for each width, that the classes of one operand cover every bit pattern (the negation of the union is unsat) and are pairwise disjoint, and the same for RC and FTZ.
    A factor list that is complete and disjoint gives a complete and disjoint product (every state satisfies exactly one case)."""
    out = {}
    def unsat(f):
        s = z3.Solver(); s.add(f); return s.check() == z3.unsat
    def part(name, cs):
        out[name + ':complete'] = unsat(z3.Not(z3.Or(*cs)))
        out[name + ':disjoint'] = all(unsat(z3.And(cs[i], cs[j])) for i in range(len(cs)) for j in range(i + 1, len(cs)))
    for w in (32, 64): part('classes%d' % w, classes(z3.BitVec('x', w), w))
    rc, ftz = rc_ftz(z3.BitVec('mxcsr', 32)); part('rc', rc); part('ftz', ftz)
    return out


def _consts(e):
    acc, seen, st = set(), set(), [e]
    while st:
        x = st.pop()
        if x.get_id() in seen: continue
        seen.add(x.get_id())
        if z3.is_const(x) and x.decl().kind() == z3.Z3_OP_UNINTERPRETED: acc.add(x.decl().name())
        st.extend(x.children())
    return acc


def _bits(v, known):
    """v with the bits of `known` {bit: 0/1} replaced by their values (the Concat of its bits)"""
    return z3.Concat(*[z3.BitVecVal(known[b], 1) if b in known else z3.Extract(b, b, v) for b in reversed(range(v.size()))])


def _env_bits(env, mx):
    """the bits of mxcsr that env fixes: the conjuncts  K == Extract(h, l, mxcsr)  (a value K) and  0 == mxcsr & M  (the bits of M are zero)"""
    known = {}
    for c in (env.children() if z3.is_and(env) else [env]):
        if not z3.is_eq(c): continue
        a, b = c.arg(0), c.arg(1)
        if z3.is_bv_value(b): a, b = b, a
        if not z3.is_bv_value(a): continue
        if z3.is_app_of(b, z3.Z3_OP_EXTRACT) and b.arg(0).eq(mx):
            hi, lo = b.params()
            for i in range(lo, hi + 1): known[i] = (a.as_long() >> (i - lo)) & 1
        elif a.as_long() == 0 and z3.is_app_of(b, z3.Z3_OP_BAND) and b.num_args() == 2 and b.arg(0).eq(mx) and z3.is_bv_value(b.arg(1)):
            for i in range(32):
                if (b.arg(1).as_long() >> i) & 1: known[i] = 0
        elif a.as_long() == 0 and z3.is_app_of(b, z3.Z3_OP_BAND) and b.num_args() == 2 and b.arg(1).eq(mx) and z3.is_bv_value(b.arg(0)):
            for i in range(32):
                if (b.arg(0).as_long() >> i) & 1: known[i] = 0
    return known


def _nf_atoms(nf2):
    """nf (simplified) as {const: bits cleared}: a conjunction of  Extract(h, l, v) == 0  atoms; None for any other shape"""
    out = {}
    for c in (nf2.children() if z3.is_and(nf2) else [nf2]):
        if not z3.is_eq(c): return None
        a, b = c.arg(0), c.arg(1)
        if z3.is_bv_value(a): a, b = b, a
        if not (z3.is_bv_value(b) and b.as_long() == 0 and z3.is_app_of(a, z3.Z3_OP_EXTRACT) and z3.is_const(a.arg(0))): return None
        hi, lo = a.params(); out.setdefault(a.arg(0), {}).update({i: 0 for i in range(lo, hi + 1)})
    return out


def _flatten(t):
    st, out = [t], []
    while st:
        x = st.pop()
        if z3.is_app_of(x, z3.Z3_OP_BOR) or z3.is_or(x): st.extend(x.children())
        else: out.append(x)
    return out


def _no(why):
    """the split does not apply (reason code `why`: the n-th exit of prove_flags; printed under SIMD_TRACE)"""
    if os.environ.get('SIMD_TRACE'): print('  simdsplit: not applicable, exit', why, flush=True)
    return None


def _leaf_subst(pairs_ops):
    """the substitution that makes every FP lane operand a fresh variable, at the LEAVES: the Form and the spec build the same operand with different term shapes (Extract(63, 0, Concat of bytes of mem[disp + k]) against mem[disp + 8 + k]),
    so replacing the operand term itself matches only one side.  An operand is either  Extract(h, l, v)  of a register constant v: v gets bits h..l replaced by the variable; or (simplified) a Concat of bytes Select(M, a_k) of one
    memory array M: M gets the bytes of the variable stored at the a_k.  The result is a generalisation (the original state is the instance of the fresh variables equal to the operand values), so a proof for all of them proves the
    original; the stored addresses must be distinct terms.  -> [(old, new)] or None (an operand of another shape)."""
    regs, stores = {}, {}
    for opv, (w, x) in pairs_ops:
        if z3.is_app_of(x, z3.Z3_OP_EXTRACT) and z3.is_const(x.arg(0)):
            hi, lo = x.params(); regs.setdefault(x.arg(0), []).append((hi, lo, opv)); continue
        xs = z3.simplify(x)
        bs = xs.children() if z3.is_app_of(xs, z3.Z3_OP_CONCAT) else [xs]
        if len(bs) * 8 != w or not all(z3.is_app_of(b, z3.Z3_OP_SELECT) and z3.is_const(b.arg(0)) for b in bs): return None
        arr = bs[0].arg(0)
        if any(not b.arg(0).eq(arr) for b in bs): return None
        for j, b in enumerate(reversed(bs)): stores.setdefault(arr, []).append((b.arg(1), z3.Extract(8 * j + 7, 8 * j, opv)))
    sub = []
    for v, ps in regs.items():
        ps.sort(key=lambda p: -p[0]); parts, top = [], v.size() - 1
        for hi, lo, opv in ps:
            if hi > top: return None                                    # overlapping pieces
            if hi < top: parts.append(z3.Extract(top, hi + 1, v))
            parts.append(opv); top = lo - 1
        if top >= 0: parts.append(z3.Extract(top, 0, v))
        sub.append((v, parts[0] if len(parts) == 1 else z3.Concat(*parts)))
    for arr, ws in stores.items():
        if len({a.get_id() for a, _ in ws}) != len(ws): return None
        m = arr
        for a, b in ws: m = z3.Store(m, a, b)
        sub.append((arr, m))
    return sub


def prove_flags(g0, nf, env, mx, fpl, mk_solver, timeout):
    """-> True iff the equality g0 (one bit of the post-MXCSR, Form == spec) is proved by the per-lane split; 'unknown' when a piece ran out of the time limit; None when the split does not apply or a piece is not proved (the caller asks the whole part).
    mk_solver(assertions, timeout) -> a z3 solver for them (the caller's tactic)."""
    try:
        ops, seen = [], set()
        for w, xs in fpl:
            for x in xs:
                if x.get_id() not in seen: seen.add(x.get_id()); ops.append((w, x))
        pairs = [(x, z3.BitVec('opv%d' % i, w)) for i, (w, x) in enumerate(ops)]
        ls = _leaf_subst([(v, wx) for (_, v), wx in zip(pairs, ops)])          # step 3 first: an operand (a memory load) mentions the address variables that step 2 restricts
        if ls is None: return _no(9)
        g = z3.simplify(z3.substitute(g0, *ls))                         # reads of the stored bytes fold here, while the addresses still have their own variable
        nf = z3.substitute(nf, *ls)
        known = _env_bits(env, mx)
        if not known: return _no(1)
        m2 = _bits(mx, known)
        g = z3.substitute(g, (mx, m2))
        nf2 = z3.simplify(z3.substitute(nf, (mx, m2)))
        at = _nf_atoms(nf2) if not z3.is_true(nf2) else {}
        if at is None: return _no(2)
        for v, kb in at.items(): g = z3.substitute(g, (v, _bits(v, kb)))
        g = z3.simplify(g)
        if z3.is_true(g): return True
        if not z3.is_eq(g):
            if os.environ.get('SIMD_TRACE'): print('  simdsplit exit3', g.decl().name(), str(g)[:300])
            return _no(3)
        allowed = {'mxcsr'} | {v.decl().name() for _, v in pairs}
        L, R = _flatten(g.arg(0)), _flatten(g.arg(1))
        ids = {x.get_id() for x in R}; Lr = [x for x in L if x.get_id() not in ids]
        idl = {x.get_id() for x in L}; Rr = [x for x in R if x.get_id() not in idl]
        if any(not _consts(x) <= allowed for x in Lr + Rr): return _no(4)
        gl, gr = {}, {}
        for x in Lr: gl.setdefault(frozenset(_consts(x) - {'mxcsr'}), []).append(x)
        for x in Rr: gr.setdefault(frozenset(_consts(x) - {'mxcsr'}), []).append(x)
        if gl.keys() != gr.keys(): return _no(5)
        byname = {v.decl().name(): (w, v) for (_, v), (w, _) in zip(pairs, ops)}
        for key in gl:
            ls, rs = gl[key], gr[key]
            # several disjuncts reading the same variables: their bit-or, compared as one term
            def orr(xs):
                t = xs[0]
                for y in xs[1:]: t = t | y
                return t
            lt, rt = orr(ls), orr(rs)
            ovs = sorted(key)
            if len(ovs) > 2: return _no(6)
            facs = rc_ftz(mx)
            for k in ovs: w, v = byname[k]; facs.append(classes(v, w))
            cases = [[]]
            for f in facs: cases = [c + [h] for c in cases for h in f]
            for cs in cases:
                q = list(cs) + [z3.Not(lt == rt)]; qk = hashlib.sha256(z3.And(*q).sexpr().encode()).hexdigest()
                if qk in _PROVED: continue
                if qk in _UNDECIDED: return 'unknown'                 # the same query already ran out of time in this process (same solver, same limit): not asked again
                res = mk_solver(q, timeout).check()
                if res == z3.unsat: _PROVED.add(qk); continue
                if os.environ.get('SIMD_TRACE'): print('  simdsplit piece', sorted(key), res, [str(c)[:50] for c in cs])
                if res == z3.unknown: _UNDECIDED.add(qk); return 'unknown'          # a piece that is not decided leaves the shape timeout (the whole query reads more operands and is no easier)
                return _no(7)                                         # sat: the matching of disjuncts is not the structure of the terms; the caller asks the whole part
        return True
    except z3.Z3Exception: return _no(8)
