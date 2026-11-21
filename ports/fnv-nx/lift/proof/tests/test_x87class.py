#!/usr/bin/env python3
"""x87class.x87_class reads the class from the encoding bytes (SDM Vol 2 A.2.1 escape opcode map), real bytes, no doubles."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, ".."), os.path.join(HERE, "..", "..")]
import fnvnx.lift.proof.x87class as X  # noqa: E402


class T(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(X.x87_class(b"\xd9\x45\x08"), (0xd9, "mem", 0, None))        # FLD m32fp
        self.assertEqual(X.x87_class(b"\xd9\xc1"), (0xd9, "reg", 0, None))            # FLD ST(1): rm is a register operand, not a refinement
        self.assertEqual(X.x87_class(b"\xd9\xe8"), (0xd9, "reg", 5, 0))               # FLD1: D9 /5 mod3 rm0
        self.assertEqual(X.x87_class(b"\xd9\xee"), (0xd9, "reg", 5, 6))               # FLDZ
        self.assertEqual(X.x87_class(b"\x66\xdd\x18"), (0xdd, "mem", 3, None))        # a prefix is skipped: FSTP m64fp
        self.assertEqual(X.x87_class(b"\xdf\xe0"), (0xdf, "reg", 4, 0))               # FNSTSW AX
        self.assertEqual(X.x87_class(b"\x9b"), (0x9b, "op", None, None))              # FWAIT: not an escape, the opcode byte is the class


if __name__ == "__main__":
    unittest.main()
