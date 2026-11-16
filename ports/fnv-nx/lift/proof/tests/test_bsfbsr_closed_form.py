"""The closed forms of sail_patches/bump/bit_bsf_bsr.sail equal the ACL2 recursions of bit.lisp (acl2 master 3ccb342c, bsr :715-723, bsf :831-843), unrolled over all 64 bits, for every nonzero 64-bit value (z3)."""
import unittest

import z3

W = 64


def popcount(x):
    m = lambda v: z3.BitVecVal(v, W)
    x = (x & m(0x5555555555555555)) + (z3.LShR(x, 1) & m(0x5555555555555555))
    x = (x & m(0x3333333333333333)) + (z3.LShR(x, 2) & m(0x3333333333333333))
    x = (x & m(0x0f0f0f0f0f0f0f0f)) + (z3.LShR(x, 4) & m(0x0f0f0f0f0f0f0f0f))
    x = (x & m(0x00ff00ff00ff00ff)) + (z3.LShR(x, 8) & m(0x00ff00ff00ff00ff))
    x = (x & m(0x0000ffff0000ffff)) + (z3.LShR(x, 16) & m(0x0000ffff0000ffff))
    return (x & m(0xffffffff)) + (z3.LShR(x, 32) & m(0xffffffff))


def bsr_closed(x):
    for s in (1, 2, 4, 8, 16, 32):
        x = x | z3.LShR(x, s)
    return popcount(x) - 1


def bsf_closed(x):
    return popcount(~x & (x - 1))


def bsr_rec(x):                       # (bsr n) = 0 if n = 1 else 1 + (bsr (logcdr n)), unrolled: the index of the highest set bit
    r = z3.BitVecVal(0, W)
    for i in range(W):
        r = z3.If(z3.Extract(i, i, x) == 1, z3.BitVecVal(i, W), r)
    return r


def bsf_rec(x):                       # (bsf index x) = index if the low bit is 1 else (bsf (1+ index) (logtail 1 x)), unrolled: the index of the lowest set bit
    r = z3.BitVecVal(0, W)
    for i in reversed(range(W)):
        r = z3.If(z3.Extract(i, i, x) == 1, z3.BitVecVal(i, W), r)
    return r


class ClosedForms(unittest.TestCase):
    def check(self, a, b):
        x = z3.BitVec('x', W); s = z3.Solver(); s.add(x != 0, a(x) != b(x))
        self.assertEqual(s.check(), z3.unsat)

    def test_bsr(self): self.check(bsr_closed, bsr_rec)

    def test_bsf(self): self.check(bsf_closed, bsf_rec)


if __name__ == '__main__':
    unittest.main()
