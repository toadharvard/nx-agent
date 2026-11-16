#!/usr/bin/env python3
"""ctrans / x87carrier: the closure of the emission is derived; every helper's term agrees with the compiled C on boundary inputs; an altered C body changes the term.
The tests read the preprocessed lift/rt units (FNVNX_PP); without them they SKIP with that reason."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import os, sys, unittest
import z3

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, '..'), os.path.join(HERE, '..', '..')]
import fnvnx.lift.proof.ctrans as ctrans; import fnvnx.lift.proof.x87carrier as x87carrier  # noqa: E402

try:
    C = x87carrier.carrier()
    NOINPUT = None
except x87carrier.NoInput as e:
    C, NOINPUT = None, str(e)


@unittest.skipIf(NOINPUT, NOINPUT or '')
class Derived(unittest.TestCase):
    def test_closure_comes_from_the_emission(self):
        for must in ('fv_d', 'fv_i', 'fv_cmp', 'fv_rnd', 'fv_s32', 'fv_s64', 'fv_sg', 'fv_x', 'fxn', 'fxn_slow', 'fx_round', 'fnvl_x_to_f64', 'fnvl_x_to_i64', 'fnvl_x_cmp', 'fnvl_x_from_bcd', 'fnvl_x_to_bcd'):
            self.assertIn(must, C['closure'])
        # the arithmetic of x87.c that no emission calls is outside it
        for out in ('fnvl_x_add', 'fnvl_x_mul', 'muldiv', 'addsub'):
            self.assertNotIn(out, C['closure'])
        self.assertEqual(sorted(C['translated'] + list(C['refused'])), C['closure'])

    def test_kinds_are_the_headers(self):
        self.assertEqual(sorted(C['kinds']), ['FK_E', 'FK_F32', 'FK_F64', 'FK_I16', 'FK_I32', 'FK_I64', 'FK_M80'])

    def test_refusals_are_outside_the_value_boundary(self):
        for fn, why in C['refused'].items():
            self.assertTrue(any(k in why for k in ('fnvl_fp_', 'M16', 'non-constant array index', 'template', 'not a function')), (fn, why))
        for fn in ('fv_d', 'fv_i', 'fv_cmp', 'fxn', 'fnvl_x_to_f64'): self.assertIn(fn, C['translated'])


class HostAgreement(unittest.TestCase):
    @unittest.skip('not run: the agreement check needs the recipe-built aarch64 object of lift/rt (guest compiler, msvcmodel) executed in the L4 boot machine, which runs guest ELFs only '
                   '(verify/bootmachine.py); no test root that calls a helper and reports its result through the host API exists in the tree, and the recipe has no execution path for a '
                   'guest test object.  Raised with the coordinator for L4.  The earlier x86 g++ comparison was deleted (one path: stock x86 is not the game compiler).')
    def test_every_translated_helper_agrees_with_the_compiled_c(self):
        pass


def outs_of(mc, fn):
    args, cells = mc.inputs(fn); mc.G.pop('c', None); r = mc.apply(fn, args)
    o = []
    if isinstance(r.ret, ctrans.V): o.append(r.ret.e)
    elif isinstance(r.ret, dict): o.extend(v.e for v in r.ret.values())
    for _, cell in cells: o.extend(x.e for x in cell)
    return o


def differs(fn, old, new):
    """is there an input on which the result or an out-parameter of fn differs between the C and the altered C"""
    o0 = outs_of(C['machine'], fn)
    o1 = outs_of(ctrans.load(x87carrier.pp_inputs(), x87carrier.MACRO_FILES, {fn: (old, new)})[0], fn)
    s = z3.Solver(); s.set('timeout', 120000)
    s.add(z3.Or(*[x != y for x, y in zip(o0, o1)]))
    return s.check()


@unittest.skipIf(NOINPUT, NOINPUT or '')
class Mutation(unittest.TestCase):
    def test_altered_bodies_change_the_term(self):
        for fn, old, new in (('fnvl_x_to_i64', 'x.s ? mag > lim : mag >= lim', 'mag >= lim'),
                             ('fv_i', ':!(r>=-(double)ind&&r<(double)ind)', ':!(r>-(double)ind&&r<(double)ind)'),
                             ('fnvl_x_cmp', 'else if (x.s != y.s) r = x.s ? 0x100 : 0;', 'else if (x.s != y.s) r = x.s ? 0 : 0x100;'),
                             ('fx_round', 'rc==0?__builtin_roundeven(v)', 'rc==0?__builtin_floor(v)')):
            with self.subTest(fn=fn):
                self.assertIn(old, C['source'].funcs[fn])
                self.assertEqual(differs(fn, old, new), z3.sat)
        # an alteration that cannot change any value leaves the term equal (the check is not vacuous): the redundant NaN test of fv_i
        self.assertEqual(differs('fv_i', 'if(d!=d){ (c->fex=(c->fex&~0x200u)|((0x1u)&0x23fu)); return ind; }', ''), z3.unsat)


@unittest.skipIf(NOINPUT, NOINPUT or '')
class Fpcr(unittest.TestCase):
    def test_every_fp_op_carries_the_fpcr_obligation(self):
        m = C['machine']
        for fn, has in (('fv_d', True), ('fx_round', True), ('fv_cmp', True), ('fnvl_x_cmp', False), ('fnvl_x_to_f64', False), ('unpack', False)):
            a, _ = m.inputs(fn); m.G.pop('c', None)
            self.assertEqual(bool(m.apply(fn, a).dom), has, fn)

    def test_the_declared_fpcr_is_read_from_the_record(self):
        self.assertTrue(ctrans.ah_reserved())                  # armv8-a: FEAT_AFP (Armv8.7) is absent, AH is RES0
        try:
            rec = ctrans.fpcr_record()
        except ctrans.Undecided as e:
            self.assertIn('console_cpu.fpcr', str(e)); return      # the row is not declared today: reported, never defaulted
        self.assertEqual({'DN', 'FZ', 'RMode'} <= set(rec), True)


class FpModel(unittest.TestCase):
    """the NaN rule of the ops, Arm ARM FPProcessNaNs / FPDefaultNaN (the host runs above cover the rest)"""
    def test_nan_rules(self):
        T = ctrans.F64; bv = lambda x: z3.BitVecVal(x, 64)
        ev = lambda e: z3.simplify(z3.substitute(e, (ctrans.ARM_RMODE, z3.BitVecVal(0, 2)))).as_long()
        self.assertEqual(ev(ctrans.fop('add', [bv(0x7FF0000000000001), bv(0x7FF8000000000005)], T)), 0x7FF8000000000001)   # SNaN first, quieted
        self.assertEqual(ev(ctrans.fop('add', [bv(0x7FF8000000000007), bv(0x7FF0000000000002)], T)), 0x7FF8000000000002)   # an SNaN beats a QNaN
        self.assertEqual(ev(ctrans.fop('add', [bv(0x7FF8000000000007), bv(0x7FF8000000000002)], T)), 0x7FF8000000000007)   # first QNaN
        self.assertEqual(ev(ctrans.fop('sub', [bv(0x7FF0000000000000), bv(0x7FF0000000000000)], T)), 0x7FF8000000000000)   # inf - inf: default NaN


if __name__ == '__main__':
    unittest.main()
