#!/usr/bin/env python3
"""x87form: the part (i) set is derived from the ast of x87spec.py; the Form of an x87 class is the spec's own DAG translated to the DSL and is proved against execute(); a class outside the
translator's table or the Form's effects is uncertified with a reason that names it; a defect of the translator makes the proof fail."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import inspect
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, ".."), os.path.join(HERE, "..", "..")]
import fnvnx.lift.proof.tests.declared_x86sys as declared_x86sys  # noqa: E402
import fnvnx.lift.proof.x87spec as X  # noqa: E402
from fnvnx.lift.fnvlift import dsl, inputs  # noqa: E402
from fnvnx.lift.proof import forms, prove, shape, x87form as XF  # noqa: E402

declared_x86sys.configure()
inputs.configure(cpumodel=os.path.join(HERE, "..", "..", "..", "verify", "cpuid_model.json"))
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

    def test_slice_1b_classes(self):
        for raw, name in ((b"\xd9\x28", "fldcw"), (b"\xd9\x20", "fldenv"), (b"\xd9\x30", "fnstenv"), (b"\xdd\x20", "frstor"), (b"\xdd\x30", "fnsave"), (b"\xdf\x20", "fbld"),
                          (b"\xda\xd9", "fcmovu"), (b"\xd9\xc0", "fld st(0)")):
            r = prove.prove_one(None, build(raw), None, None, None)
            self.assertEqual(r[0], "proved", (name, r))

    def test_domain_and_effects(self):
        F = build(b"\xd9\x28")                                 # fldcw m16
        self.assertIn("fcw", [k for k, _, _ in F.xs])
        self.assertEqual(len(F.domain), 4)                       # masks, not trap, pre, nd_pre
        self.assertEqual(F.faults, [])                           # the domain excludes every x87 exception; a store adds only forms.mstore's code-segment fault
        self.assertEqual(len(build(b"\xdd\x30").faults), 1)
        self.assertEqual(len(build(b"\xdd\x30").stores), 7 + 8)  # fnsave: seven 32-bit words of the environment image, eight 80-bit registers
        self.assertEqual(len(build(b"\xd9\x30").stores), 7)
        self.assertEqual(dsl.xc_lines("fcw", None, None, lambda n: "w"), ["FLDCW(w);"])

    def test_es_follows_from_the_invariant(self):
        # ES is STORED state (a bit of the DSL fsw input), recomputed by every writing instruction (x87spec.sync_es); the masks alone do not give ES == 0, the masks and the invariant do
        s0 = X.from_dsl(XF.pre_vars())
        es = XF.z3.Extract(X.ES, X.ES, s0.fsw) == X.K(1, 1)
        masks = XF.z3.Extract(5, 0, s0.fcw) == XF.z3.BitVecVal(0x3f, 6)
        inv = es == X.es_of(s0.fsw, s0.fcw)
        sv = XF.z3.Solver(); sv.add(masks, es); self.assertEqual(sv.check(), XF.z3.sat)
        sv = XF.z3.Solver(); sv.add(masks, inv, es); self.assertEqual(sv.check(), XF.z3.unsat)

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


    COMPARES = ((b"\xd8\xd1", "fcom st(1)"), (b"\xd8\xd9", "fcomp st(1)"), (b"\xd9\xe4", "ftst"), (b"\xda\xe9", "fucompp"), (b"\xdd\xe1", "fucom st(1)"),
                (b"\xdd\xe9", "fucomp st(1)"), (b"\xde\xd9", "fcompp"), (b"\xdb\xe9", "fucomi"), (b"\xdb\xf1", "fcomi"), (b"\xdf\xe9", "fucomip"), (b"\xdf\xf1", "fcomip"),
                (b"\xdc\x10", "fcom m64"), (b"\xda\x10", "ficom m32"), (b"\xde\x10", "ficom m16"))

    def test_compares_are_uncertified_until_the_carrier_kind(self):
        """ruling 3b: DE of a register operand is at its carrier type; the spec state has no carrier kind, so every compare (DE on ST(0)'s image) is refused with that reason"""
        for raw, name in self.COMPARES:
            self.assertIn("carrier type", self.reason(raw), name)

    def with_carrier_de_open(self, f):
        """the rest of a compare's proof, with only the carrier-DE refusal lifted (what the carrier-kind state will lift by rule)"""
        old = XF.carrier_de_refusal
        XF.carrier_de_refusal = lambda o: None
        try: return f()
        finally: XF.carrier_de_refusal = old

    def test_compare_classes_prove_but_for_the_carrier_de(self):
        for raw, name in self.COMPARES:
            r = self.with_carrier_de_open(lambda: prove.prove_one(None, build(raw), None, None, None))
            self.assertEqual(r[0], "proved", (name, r))

    def test_compare_emits_the_carrier_op_and_no_handler(self):
        """the emitted C of a compare Form is the DSL text (the SDM IE / DE / C1 terms and the carrier op): no FCMP, FCMPI, fv_cmp or FXF of the handler routing is called"""
        txt = "\n".join(self.with_carrier_de_open(lambda: build(b"\xdb\xe9")).to_c_inline(lambda n: "v_" + n, lambda c, n, r: "", lambda v: "", (), "M")[0])
        self.assertIn("fx_dsl_x_cmp(", txt)
        for h in ("FCMP", "fv_cmp", "FXF"): self.assertNotIn(h, txt)

    def test_eflags_translation_defect_is_caught(self):
        # a defect of the flag order: the Form writes PF where the spec says CF
        F = self.with_carrier_de_open(lambda: build(b"\xdb\xe9"))
        flag = type(F).flag
        swap = {"pf": "cf", "cf": "pf"}
        type(F).flag = lambda self, f, t: flag(self, swap.get(f, f), t)
        try:
            F = self.with_carrier_de_open(lambda: build(b"\xdb\xe9"))
            r = prove.prove_one(None, F, None, None, None)
        finally:
            type(F).flag = flag
        self.assertNotEqual(r[0], "proved")

    def test_dropped_ie_is_caught(self):
        """a compare Form whose FSW lacks IE (bit 0) does not prove: the proof compares the whole FSW with the spec's"""
        F = self.with_carrier_de_open(lambda: build(b"\xd8\xd1"))
        fsw = type(F).fsw
        type(F).fsw = lambda self, n: fsw(self, dsl.bin_("and", n, dsl.C(0xfffe, 16)))
        try:
            F2 = self.with_carrier_de_open(lambda: build(b"\xd8\xd1"))
            r = prove.prove_one(None, F2, None, None, None)
        finally:
            type(F).fsw = fsw
        self.assertNotEqual(r[0], "proved")


if __name__ == "__main__":
    unittest.main()
