#!/usr/bin/env python3
"""proof/simdsplit.py: the completeness theorem of its partitions (z3), and the per-lane split on a two-lane flag function whose reference is known (a sum of lane flags is split per lane and proved; a differing lane is not proved).
python3 -I tests/test_simdsplit.py"""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import os, sys, unittest
import z3
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from fnvnx.lift.proof import bounds, simdsplit as P


CALLS = []


def solver(asserts, rlimit):
    CALLS.append(len(asserts)); s = z3.Solver(); s.set('rlimit', rlimit); s.add(*asserts); return s


class T(unittest.TestCase):
    def test_completeness(self):
        r = P.completeness_theorem()
        self.assertEqual(sorted(r), sorted(['classes32:complete', 'classes32:disjoint', 'classes64:complete', 'classes64:disjoint', 'rc:complete', 'rc:disjoint', 'ftz:complete', 'ftz:disjoint']))
        self.assertTrue(all(r.values()), r)

    def setup_lanes(self, spec_ne):
        mx = z3.BitVec('mxcsr', 32); xa, xb = z3.BitVec('xa', 64), z3.BitVec('xb', 64)
        l0 = (z3.Extract(31, 0, xa), z3.Extract(31, 0, xb)); l1 = (z3.Extract(63, 32, xa), z3.Extract(63, 32, xb))
        f = lambda a, b: z3.If(a == b, z3.BitVecVal(1, 1), z3.BitVecVal(0, 1))
        g = lambda a, b: z3.If(a != b, z3.BitVecVal(1, 1), z3.BitVecVal(0, 1)) if spec_ne else z3.If((a ^ b) + 0 == 0, z3.BitVecVal(1, 1), z3.BitVecVal(0, 1))
        old = z3.Extract(0, 0, mx)
        form = old | f(*l0) | f(*l1); spec = g(*l1) | old | g(*l0)                 # the same disjuncts in another order (and, when spec_ne, a different lane function)
        env = z3.And(63 == z3.Extract(12, 7, mx), 0 == mx & 0xFFFF1FC0)
        return form == spec, env, mx, [(32, list(l0)), (32, list(l1))]

    def test_lane_split_proves(self):
        g, env, mx, fpl = self.setup_lanes(False)
        del CALLS[:]
        self.assertIs(P.prove_flags(g, z3.BoolVal(True), env, mx, fpl, solver, bounds.Z3_RLIMIT), True)
        self.assertTrue(CALLS)                                         # the lane pieces were asked (it was not true by simplification alone)

    def test_lane_split_does_not_prove_a_different_lane(self):
        g, env, mx, fpl = self.setup_lanes(True)
        self.assertIsNot(P.prove_flags(g, z3.BoolVal(True), env, mx, fpl, solver, bounds.Z3_RLIMIT), True)


if __name__ == '__main__': unittest.main()
