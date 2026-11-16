#!/usr/bin/env python3
"""The carrier ops of the x87 spec (x87spec.CARRIER_OPS).  Each op the spec applies by a helper's name is (1) a function of the C the lifter ships (x87carrier translates it, ctrans), (2) free of undefined behaviour on
every input of its sorts, (3) equal, on every input, to the SDM's own reading of the value as a z3 formula: REFERENCE below, one per op (the universe is x87spec.CARRIER_OPS, so a new op without a reference
fails here).  The value of a Form is then the SDM machinery of the spec around ops that are proved against the SDM text.  The tests read the preprocessed lift/rt units (FNVNX_PP, until the pp run-step input mounts
them); without them they SKIP with that reason."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import os, sys, unittest
import z3

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, '..'), os.path.join(HERE, '..', '..')]
import fnvnx.lift.proof.x87carrier as x87carrier; import fnvnx.lift.proof.x87spec as X  # noqa: E402

try:
    C = x87carrier.carrier()
    NOINPUT = None
except x87carrier.NoInput as e:
    C, NOINPUT = None, str(e)


def ref_x_cmp(a, b):
    """SDM 1:T8-6: ST(0) > src 000, < 001, equal 100 (+0 = -0, IEEE), unordered 111 (C3, C2, C0); an unsupported encoding is unordered (SDM 1:T8-10); read on FPSort(15,64)"""
    un = z3.Or(X.is_nan80(a), X.is_nan80(b), X.is_unsup80(a), X.is_unsup80(b))
    lt, eq = z3.fpLT(X.fp80(a), X.fp80(b)), z3.fpEQ(X.fp80(a), X.fp80(b))
    bit = lambda c, v: z3.If(c, z3.BitVecVal(v, 32), z3.BitVecVal(0, 32))
    return bit(z3.Or(un, eq), 0x4000) | bit(un, 0x400) | bit(z3.Or(un, z3.And(z3.Not(un), lt)), 0x100)


REFERENCE = {'fx_dsl_x_cmp': ref_x_cmp}


def valid(f, rlimit=50_000_000):
    s = z3.Solver()
    s.set('rlimit', rlimit)
    s.add(z3.Not(f))
    return s.check()


@unittest.skipIf(NOINPUT, NOINPUT or '')
class Ops(unittest.TestCase):
    def test_every_op_has_a_reference_and_a_helper(self):
        self.assertEqual(sorted(REFERENCE), sorted(X.CARRIER_OPS))
        for name in X.CARRIER_OPS:
            self.assertIn(name, C['translated'], name)

    def test_signatures_are_the_c_signatures(self):
        mc = C['machine']
        for name, (ws, rw) in X.CARRIER_OPS.items():
            fd = mc.funcs[name]
            ps = [mc.ctype(p.type) for p in fd.decl.type.args.params]
            self.assertEqual(len(ps), len(ws), name)
            for t, w in zip(ps, ws):
                self.assertTrue(t[0] in ('i', 's') and (t[1] if t[0] == 'i' else 80) >= w, (name, t, w))
            self.assertEqual(mc.ctype(fd.decl.type.type)[1], rw, name)

    def test_no_undefined_behaviour(self):
        for name, (ws, rw) in X.CARRIER_OPS.items():
            args = [z3.BitVec('a%d' % i, w) for i, w in enumerate(ws)]
            ret, outs, ub, dom = x87carrier.call(name, *args)
            self.assertFalse(outs, name)
            self.assertEqual(valid(z3.And(*ub)) if ub else z3.unsat, z3.unsat, name)

    def test_value_is_the_sdm_reading(self):
        for name, (ws, rw) in X.CARRIER_OPS.items():
            args = [z3.BitVec('a%d' % i, w) for i, w in enumerate(ws)]
            ret, outs, ub, dom = x87carrier.call(name, *args)
            self.assertEqual(ret.size(), rw)
            self.assertEqual(valid(ret == REFERENCE[name](*args)), z3.unsat, name)

    def test_a_wrong_value_is_caught(self):
        """the reference is not vacuous: the greater-than and less-than codes swapped does not equal the C"""
        a, b = z3.BitVec('a', 80), z3.BitVec('b', 80)
        ret, _, _, _ = x87carrier.call('fx_dsl_x_cmp', a, b)
        self.assertEqual(valid(ret == (ref_x_cmp(a, b) ^ z3.BitVecVal(0x100, 32))), z3.sat)


if __name__ == '__main__':
    unittest.main()
