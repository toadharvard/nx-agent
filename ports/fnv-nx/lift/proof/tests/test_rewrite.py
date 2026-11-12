"""proof/rewrite.py and model_sail.zext_add_pairs: every rewrite is an identity of the bit-vector semantics; checked here by z3 on 8/16-bit instances (all inputs)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
import z3
from proof import rewrite, model_sail


def equal(a, b):
    s = z3.Solver()
    s.add(a != b)
    return s.check() == z3.unsat


class Rewrites(unittest.TestCase):
    def test_pow2_mul(self):
        x = z3.BitVec('x', 16)
        for k in (1, 3, 8, 15):
            t = x * (1 << k)
            r = rewrite.pow2_mul(t)
            self.assertNotEqual(r.decl().kind(), z3.Z3_OP_BMUL)
            self.assertTrue(equal(t, r))
        self.assertEqual(rewrite.pow2_mul(x * 3).decl().kind(), z3.Z3_OP_BMUL)

    def test_urem_def(self):
        a, b = z3.BitVec('a', 8), z3.BitVec('b', 8)
        t = z3.URem(a, b)
        r = rewrite.div_norm(t)
        self.assertTrue(equal(t, r))
        self.assertTrue(equal(z3.Extract(3, 0, z3.URem(z3.ZeroExt(8, a), z3.ZeroExt(8, b))), rewrite.div_norm(z3.Extract(3, 0, z3.URem(z3.ZeroExt(8, a), z3.ZeroExt(8, b))))))

    def test_low_extract(self):
        a, b = z3.BitVec('a', 16), z3.BitVec('b', 16)
        for t in (a + b, a - b, a * b):
            e = z3.Extract(7, 0, t)
            r = rewrite.div_norm(e)
            self.assertNotEqual(r.decl().kind(), z3.Z3_OP_EXTRACT)
            self.assertTrue(equal(e, r))

    def test_zext_add(self):
        e = z3.BitVec('e', 8)
        t = z3.ZeroExt(16, z3.ZeroExt(8, e) + 5)
        pairs = model_sail.zext_add_pairs([t])
        self.assertEqual(len(pairs), 1)
        (old, new), = pairs.values()
        self.assertTrue(equal(old, new))
        wrap = z3.ZeroExt(8, z3.ZeroExt(8, e) + 0xFFFF)      # constant too large: the sum can wrap, no rewrite
        self.assertEqual(model_sail.zext_add_pairs([wrap]), {})


if __name__ == '__main__':
    unittest.main()
