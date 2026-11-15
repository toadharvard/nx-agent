"""stringspec: the SDM spec of REP/REPE/REPNE x MOVS/STOS/LODS/CMPS/SCAS at 8/16/32 bits proves the DSL Forms; a mutated spec is refused."""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
from proof import stringspec  # noqa: E402

OPS = ('a4', 'a5', 'aa', 'ab', 'ac', 'ad', 'a6', 'a7', 'ae', 'af')
WORD = ('66a5', '66ab', '66ad', '66a7', '66af')


class T(unittest.TestCase):
    def test_every_defined_combination_is_proved(self):
        n = 0
        for op in OPS + WORD:
            for p in ('', 'f3', 'f2'):
                r, why = stringspec.check(p + op + '/0.0.0.0')
                if r == 'uncertified': self.assertIn('rep-zf only', why); continue
                self.assertEqual(r, 'proved', (p + op, why)); n += 1
        self.assertGreaterEqual(n, 30)

    def test_ecx0_and_df_are_in_the_spec(self):
        import z3
        regs, flags, store, e = stringspec.spec('rep', 'movs', 32, 2)
        ecx, esi = stringspec.V('ecx', 32), stringspec.V('esi', 32)
        s = z3.Solver(); s.add(ecx == 0, z3.Or(regs['esi'] != esi, regs['ecx'] != ecx, e != stringspec.V('eip', 32) + 2)); self.assertEqual(s.check(), z3.unsat)
        s = z3.Solver(); s.add(stringspec.V('df', 1) == 1, ecx == 1, regs['esi'] != esi - 4); self.assertEqual(s.check(), z3.unsat)

    def test_a_mutated_spec_is_refused(self):
        orig = stringspec.spec
        def bad(rep, op, w, size):
            r, f, s, e = orig(rep, op, w, size)
            if 'ecx' in r: r['ecx'] = r['ecx'] + 1
            return r, f, s, e
        stringspec.spec = bad
        try: self.assertEqual(stringspec.check('f3a4/0.0.0.0')[0], 'FAILED(dsl)')
        finally: stringspec.spec = orig

    def test_cites_check_against_the_pinned_sdm(self):
        sdm = os.environ.get('SDM_TXT')
        if not sdm: self.skipTest('SDM_TXT not set')
        r = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', 'sdm_cite_check.py'), sdm, os.path.join(HERE, '..', 'stringspec.py'), os.path.join(HERE, '..', 'stringspec_pages.tsv')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == '__main__':
    unittest.main()
