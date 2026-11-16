#!/usr/bin/env python3
"""x87kind: the carrier kind of an x87 register as state, and the obligation "the emitted compare macro equals the spec" over EVERY kind (ruling of the lead, 3b / OWNER 8, OWNER 10).

The kind is the `k` field of struct fv and its value set is the enum FK_* constants, both read through ctrans from the .i (x87carrier); nothing here restates them.  The state the macro acts on
is (kind, value bits v, e) per register; the spec's reading of it is the 80-bit image fv_x(r) (OWNER 10) with the exception flags read AT THE CARRIER TYPE (OWNER 8, SDM 1:8.5.2).

Spec of the compare exception flags (cites are the pinned SDM text, FNVNX_SDM_TXT):
  IE   SDM 2A:FCOM/FUCOM/FCOMI "Floating-Point Exceptions": ordered compares (q = 0): any NaN or an unsupported operand; unordered (q = 1): an SNaN or an unsupported operand.
  DE   SDM 1:8.5.2: an arithmetic instruction operating on a denormal operand, read at the operand's own type (binary32 / binary64 carrier by its own format, an 80-bit operand by its image,
       a pseudo-denormal included, SDM 1:8.2.2); integers have none.  SDM 1:4.9.2 (page 4-24) priority: invalid-operation (item 1, unsupported format and SNaN) and a QNaN operand (item 2:
       "the handling of a QNaN operand has precedence over lower-priority exceptions") come before the denormal-operand exception (item 4), so DE is not reported with them.
  C1   cleared (SDM 2A:FCOM "C1 Set to 0").
The kind invariant (the constructors' postcondition, lemma (b) of the ruling): the fields that the kind does not use are 0, F32 holds a 32-bit value, I16/I32 are sign-extended, an M80 exponent word is 16 bits
and a binary32/binary64 carrier holds no SNaN (fv_ld32 / fv_ld64 quiet what they load; `ctor_no_snan` proves it for them).
"""
import os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]
import z3
import ctrans, x87carrier as XC

_KIND = None


def kinds():
    global _KIND
    if _KIND is None:
        _KIND = XC.carrier()['kinds']
    return _KIND


def kind_invariant(v, e, k):
    """the constructors' postcondition on one register (v, e, k): see the module docstring"""
    K = kinds(); c = lambda name: z3.BitVecVal(K[name], 32)
    f64e, f64f, f64q = z3.Extract(62, 52, v), z3.Extract(51, 0, v), z3.Extract(51, 51, v)
    f32e, f32f, f32q = z3.Extract(30, 23, v), z3.Extract(22, 0, v), z3.Extract(22, 22, v)
    snan64 = z3.And(f64e == 0x7ff, f64f != 0, f64q == 0)
    snan32 = z3.And(f32e == 0xff, f32f != 0, f32q == 0)
    return z3.And(z3.ULE(k, max(K.values())), z3.Implies(k != c('FK_M80'), e == 0),
                  z3.Implies(k == c('FK_F32'), z3.And(z3.Extract(63, 32, v) == 0, z3.Not(snan32))),
                  z3.Implies(k == c('FK_F64'), z3.Not(snan64)),
                  z3.Implies(k == c('FK_I16'), z3.SignExt(48, z3.Extract(15, 0, v)) == v),
                  z3.Implies(k == c('FK_I32'), z3.SignExt(32, z3.Extract(31, 0, v)) == v),
                  z3.Implies(k == c('FK_M80'), z3.Extract(31, 16, e) == 0))


def fvvar(n):
    """-> (the fv as one bit-vector k:e:v, (v, e, k), the invariant over every kind)"""
    K = kinds(); mc = XC.carrier()['machine']
    assert [(n_, t[1]) for n_, t in mc.structs['fv']] == [('v', 64), ('e', 32), ('k', 32)]
    v, e, k = z3.BitVec(n + 'v', 64), z3.BitVec(n + 'e', 32), z3.BitVec(n + 'k', 32)
    f64e, f64f, f64q = z3.Extract(62, 52, v), z3.Extract(51, 0, v), z3.Extract(51, 51, v)
    f32e, f32f, f32q = z3.Extract(30, 23, v), z3.Extract(22, 0, v), z3.Extract(22, 22, v)
    snan64 = z3.And(f64e == 0x7ff, f64f != 0, f64q == 0)
    snan32 = z3.And(f32e == 0xff, f32f != 0, f32q == 0)
    c = lambda name: z3.BitVecVal(K[name], 32)
    inv = z3.And(z3.ULE(k, max(K.values())), z3.Implies(k != c('FK_M80'), e == 0),
                 z3.Implies(k == c('FK_F32'), z3.And(z3.Extract(63, 32, v) == 0, z3.Not(snan32))),
                 z3.Implies(k == c('FK_F64'), z3.Not(snan64)),
                 z3.Implies(k == c('FK_I16'), z3.SignExt(48, z3.Extract(15, 0, v)) == v),
                 z3.Implies(k == c('FK_I32'), z3.SignExt(32, z3.Extract(31, 0, v)) == v),
                 z3.Implies(k == c('FK_M80'), z3.Extract(31, 16, e) == 0))
    return z3.Concat(k, e, v), (v, e, k), inv


def _fvx(b):
    mc = XC.carrier()['machine']; d, lo = {}, 0
    for n, ft in mc.structs['fv']:
        d[n] = ctrans.V(z3.Extract(lo + ft[1] - 1, lo, b), ft); lo += ft[1]
    mc.G.pop('c', None); r = mc.apply('fv_x', [d])
    return z3.Concat(r.ret['se'].e, r.ret['m'].e)


def _carrier_flags(v, k):
    K = kinds(); is64, is32 = k == K['FK_F64'], k == K['FK_F32']
    e64, f64, q64 = z3.Extract(62, 52, v), z3.Extract(51, 0, v), z3.Extract(51, 51, v)
    e32, f32, q32 = z3.Extract(30, 23, v), z3.Extract(22, 0, v), z3.Extract(22, 22, v)
    nan = z3.Or(z3.And(is64, e64 == 0x7ff, f64 != 0), z3.And(is32, e32 == 0xff, f32 != 0))
    snan = z3.Or(z3.And(is64, e64 == 0x7ff, f64 != 0, q64 == 0), z3.And(is32, e32 == 0xff, f32 != 0, q32 == 0))
    den = z3.Or(z3.And(is64, e64 == 0, f64 != 0), z3.And(is32, e32 == 0, f32 != 0))
    return nan, snan, den


def _image_flags(x):
    se, m = z3.Extract(79, 64, x), z3.Extract(63, 0, x)
    e, ib, fr = z3.Extract(14, 0, se), z3.Extract(63, 63, m), z3.Extract(62, 0, m)
    nan = z3.And(e == 0x7fff, ib == 1, fr != 0)
    snan = z3.And(nan, z3.Extract(62, 62, m) == 0)
    unsup = z3.Or(z3.And(e == 0x7fff, ib == 0), z3.And(e != 0, e != 0x7fff, ib == 0))
    den = z3.And(e == 0, m != 0)
    return nan, snan, unsup, den


def spec_fex(fex0, ops, q):
    """the compare's c->fex on the kinds: ops = [(v, k, image)] of the two operands"""
    K = kinds(); F = []
    for v, k, x in ops:
        n1, s1, d1 = _carrier_flags(v, k); n2, s2, u2, d2 = _image_flags(x)
        m80 = z3.Or(k == K['FK_M80'], k == K['FK_E'])
        fl = z3.Or(k == K['FK_F64'], k == K['FK_F32'])
        F.append((z3.If(fl, n1, z3.If(m80, n2, z3.BoolVal(False))), z3.If(fl, s1, z3.If(m80, s2, z3.BoolVal(False))),
                  z3.If(fl, d1, z3.If(m80, d2, z3.BoolVal(False))), z3.If(m80, u2, z3.BoolVal(False))))
    (na, sa, da, ua), (nb, sb, db, ub) = F
    ie = z3.If(q == 0, z3.Or(na, nb, ua, ub), z3.Or(sa, sb, ua, ub))
    de = z3.And(z3.Or(da, db), z3.Not(ie), z3.Not(z3.Or(na, nb)))
    bit = lambda c, v: z3.If(c, z3.BitVecVal(v, 32), z3.BitVecVal(0, 32))
    return (fex0 & ~z3.BitVecVal(0x200, 32)) | bit(ie, 1) | bit(de, 2)


def compare_obligations(budget_ms=240000):
    """-> {(kind a, kind b): {"codes": r, "IE+C1": r, "DE": r}} with r in unsat (proved) / sat (counterexample) / unknown; one solver query per field and kind pair, over every pair of the enum"""
    sys.path.insert(0, os.path.join(HERE, 'tests'))
    from test_x87ops import ref_x_cmp
    K = kinds(); A, (av, ae, ak), ia = fvvar('a'); B, (bv, be, bk), ib = fvvar('b'); q = z3.BitVec('q', 32)
    ret, outs, ub, dom = XC.call('fv_cmp', A, B, q)
    cpu = dict(XC.carrier()['machine'].G['c']); fex1 = cpu['fex'].e
    xa, xb = _fvx(A), _fvx(B); f0 = z3.BitVec('c.fex', 32)
    want = spec_fex(f0, [(av, ak, xa), (bv, bk, xb)], q)
    codes = ref_x_cmp(xa, xb)
    res = {}
    for kn1 in sorted(K, key=K.get):
        for kn2 in sorted(K, key=K.get):
            if K[kn2] < K[kn1]: continue
            r = {}
            for name, bad in (("codes", ret != codes), ("IE+C1", (fex1 & ~z3.BitVecVal(2, 32)) != (want & ~z3.BitVecVal(2, 32))), ("DE", (fex1 & 2) != (want & 2))):
                s = z3.Solver(); s.set('timeout', budget_ms)
                s.add(ia, ib, z3.ULE(q, 1), ak == K[kn1], bk == K[kn2], bad)
                r[name] = str(s.check())
            res[(kn1, kn2)] = r
    return res


def ctor_no_snan():
    """lemma (b) for the loaders: fv_ld32 / fv_ld64 return a carrier of the kind they name that holds no SNaN, for every input"""
    K = kinds(); out = {}
    for fn, kn, w in (('fv_ld32', 'FK_F32', 32), ('fv_ld64', 'FK_F64', 64)):
        r = XC.call_struct(fn, z3.BitVec('x', w))
        k, v = r['k'], r['v']
        v32 = z3.Extract(31, 0, v)
        snan = (z3.And(z3.Extract(30, 23, v32) == 0xff, z3.Extract(22, 0, v32) != 0, z3.Extract(22, 22, v32) == 0) if w == 32
                else z3.And(z3.Extract(62, 52, v) == 0x7ff, z3.Extract(51, 0, v) != 0, z3.Extract(51, 51, v) == 0))
        s = z3.Solver(); s.set('timeout', 120000); s.add(z3.Or(k != K[kn], snan))
        out[fn] = str(s.check())
    return out


def compare_de_defects(budget_ms=120000):
    """the DE defect of the compare macro: {kind pair: counterexample} for every pair of the enum where the emitted fv_cmp's DE differs from spec_fex's DE (the counterexample: the operands' v, e, q and the DE each gives)"""
    K = kinds(); A, (av, ae, ak), ia = fvvar('a'); B, (bv, be, bk), ib = fvvar('b'); q = z3.BitVec('q', 32)
    ret, outs, ub, dom = XC.call('fv_cmp', A, B, q)
    fex1 = dict(XC.carrier()['machine'].G['c'])['fex'].e
    xa, xb = _fvx(A), _fvx(B); f0 = z3.BitVec('c.fex', 32)
    want = spec_fex(f0, [(av, ak, xa), (bv, bk, xb)], q)
    out = {}
    for n1 in sorted(K, key=K.get):
        for n2 in sorted(K, key=K.get):
            if K[n2] < K[n1]: continue
            s = z3.Solver(); s.set('timeout', budget_ms); s.add(ia, ib, z3.ULE(q, 1), ak == K[n1], bk == K[n2], (fex1 & 2) != (want & 2))
            r = s.check()
            if r == z3.sat:
                m = s.model(); g = lambda x: m.eval(x, True).as_long()
                out[(n1, n2)] = dict(a_v=hex(g(av)), a_e=g(ae), b_v=hex(g(bv)), b_e=g(be), q=g(q), emitted_DE=(g(fex1) >> 1) & 1, spec_DE=(g(want) >> 1) & 1)
            elif r != z3.unsat:
                out[(n1, n2)] = str(r)
    return out


if __name__ == '__main__':
    t = time.time(); res = compare_obligations()
    for p, r in sorted(res.items()): print(p, r)
    print('ctor_no_snan', ctor_no_snan())
    for p, c in sorted(compare_de_defects().items()): print('DE defect', p, c)
