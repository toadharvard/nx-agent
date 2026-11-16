#!/usr/bin/env python3
"""x87form: the part (i) set is derived from the ast of x87spec.py; the Form of an x87 class is the spec's own DAG translated to the DSL and is proved against execute(); a class outside the
translator's table or the Form's effects is uncertified with a reason that names it; a defect of the translator makes the proof fail."""
import inspect
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, ".."), os.path.join(HERE, "..", ".."), os.path.join(HERE, "..", "..", "fnvlift", "tests")]
import _inputs  # noqa: E402
import x87spec as X  # noqa: E402
from fnvlift import dsl  # noqa: E402
from proof import forms, prove, shape, x87form as XF  # noqa: E402

f = os.path.join(tempfile.mkdtemp(), "dummy")
open(f, "w").write("{}")
_inputs.configure(**{p: f for p in inspect.signature(_inputs.configure).parameters})
DEC = shape.decoder()


def build(raw):
    return forms.build(next(DEC.disasm(raw, 0x1000)), 0x1000)


class T(unittest.TestCase):
    def test_part_one_is_derived(self):
        ent, p1 = XF.part_one()
        self.assertTrue(ent and p1)
        self.assertFalse(ent & p1)
        self.assertIn("finish", p1)            # the machinery every instruction function calls
        self.assertTrue(all(callable(getattr(X, n)) for n in ent | p1))

    def test_proved_classes(self):
        for raw, name in ((b"\xd9\xc9", "fxch"), (b"\xd9\xe0", "fchs"), (b"\xd9\xe8", "fld1"), (b"\xdb\x28", "fld m80"), (b"\xdd\xd9", "fstp st(1)"), (b"\xdf\xe0", "fnstsw ax"), (b"\x9b", "fwait")):
            F = build(raw)
            r = prove.prove_one(None, F, None, None, None)
            self.assertEqual(r[0], "proved", (name, r))

    def test_translator_defect_is_caught(self):
        one = XF.Tr.one

        def bad(s, e):
            if e.decl().kind() == XF.z3.Z3_OP_ITE and e.sort().kind() == XF.z3.Z3_BV_SORT:      # swapped branches
                return XF.ite(s.t(e.arg(0)), s.t(e.arg(2)), s.t(e.arg(1)))
            return one(s, e)
        XF.Tr.one = bad
        try:
            r = prove.prove_one(None, build(b"\xd9\xc9"), None, None, None)
        finally:
            XF.Tr.one = one
        self.assertNotEqual(r[0], "proved")

    def reason(self, raw):
        with self.assertRaises(KeyError) as c:
            build(raw)
        return str(c.exception.__cause__)

    def test_unsupported_names_the_thing(self):
        self.assertIn("without a resolution: nd.trig.c2", self.reason(b"\xd9\xfe"))      # fsin: the SDM leaves a choice, no sourced resolution (slice 3)
        self.assertIn("nd.nan.tie", self.reason(b"\xd8\xc1"))                          # fadd: a NaN tie, and it needs op symbols (slice 2)
        self.assertIn("FPA_", self.reason(b"\xdd\x10"))                                # fst m64: floating-point kind
        self.assertIn("EFLAGS", self.reason(b"\xdb\xf1"))                              # fcomi

    def test_slice_1b_classes(self):
        for raw, name in ((b"\xd9\x28", "fldcw"), (b"\xd9\x20", "fldenv"), (b"\xd9\x30", "fnstenv"), (b"\xdd\x20", "frstor"), (b"\xdd\x30", "fnsave"), (b"\xdf\x20", "fbld"),
                          (b"\xda\xd9", "fcmovu"), (b"\xd9\xc0", "fld st(0)")):
            r = prove.prove_one(None, build(raw), None, None, None)
            self.assertEqual(r[0], "proved", (name, r))

    def test_domain_and_effects(self):
        F = build(b"\xd9\x28")                                 # fldcw m16
        self.assertIn("fcw", [k for k, _, _ in F.xs])
        self.assertEqual(len(F.domain), 4)                       # masks, not trap, pre, nd_pre
        self.assertEqual(F.faults, [])                           # the domain excludes every x87 exception
        self.assertEqual(len(build(b"\xdd\x30").stores), 7 + 8)  # fnsave: seven 32-bit words of the environment image, eight 80-bit registers
        self.assertEqual(len(build(b"\xd9\x30").stores), 7)
        self.assertEqual(dsl.xc_lines("fcw", None, None, lambda n: "w"), ["FLDCW(w);"])

    def test_nd_table_is_keyed_by_spec_names(self):
        self.assertTrue(set(XF.NDRES) <= XF.nd_names())
        self.assertIn("nd.nan.tie", XF.nd_names())

    def test_nary_ops(self):          # z3's simplifier makes bvadd n-ary: the translator folds every argument
        x = [XF.z3.BitVec("fr%d" % i, 80) for i in range(3)]
        t = XF.z3.simplify(x[0] + x[1] + x[2] + 5)
        self.assertGreater(t.num_args(), 2)
        n = XF.Tr({"fr0": XF.V("fr0", 80), "fr1": XF.V("fr1", 80), "fr2": XF.V("fr2", 80)}).t(t)
        sv = XF.z3.Solver()
        sv.add(dsl.to_smt(n) != t)
        self.assertEqual(sv.check(), XF.z3.unsat)


if __name__ == "__main__":
    unittest.main()
