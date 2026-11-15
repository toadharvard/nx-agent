#!/usr/bin/env python3
"""simdcheck.py is a refinement CHECK: it must prove the Forms that agree with the spec and refute a Form or a spec row that differs.  Mutations of the spec rows
(a wrong lane operation, a missing alignment fault, a wrong register write) must give FAILED for the shapes of that row; unmutated rows prove.
python3 -I tests/test_simdcheck.py"""
import os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, os.path.join(HERE, '..', '..'))
import simdcheck as C
S = C.S

KEYS = {'pxor': '0fef02/0.0.0.0', 'pxor_disp': '0fef0500000000/0.0.3.4', 'movaps_m': '0f2800/0.0.0.0', 'movaps_r': '0f28c1/0.0.0.0', 'paddb_sse': '660ffcc1/0.0.0.0',
        'movd_g': '0f6ec1/0.0.0.0', 'movss_m': 'f30f1000/0.0.0.0', 'movups_st': '0f1100/0.0.0.0', 'pcmpgtw': '0f65c1/0.0.0.0', 'movmskps': '0f50c1/0.0.0.0'}


class Check(unittest.TestCase):
    def test_proves(self):
        for n, k in KEYS.items(): self.assertEqual(C.check(k)[0], 'proved', n)

    def mutate(self, key, mut, kkey):
        old = S.ROWS[key]; S.ROWS[key] = mut(old)
        try: return C.check(kkey)[0]
        finally: S.ROWS[key] = old

    def test_wrong_lane_op_is_refuted(self):
        self.assertEqual(self.mutate(('', 0xEF, None), lambda t: t._replace(fn=lambda e, d, m, a, g: S._w(e, a, m, d, False, lambda x, y: x | y)), KEYS['pxor']), 'FAILED')

    def test_missing_alignment_fault_is_refuted(self):
        self.assertEqual(self.mutate(('', 0x28, None), lambda t: t._replace(fn=S._mov_load128(False)), KEYS['movaps_m']), 'FAILED')

    def test_wrong_register_write_is_refuted(self):
        self.assertEqual(self.mutate(('', 0x6E, None), lambda t: t._replace(fn=lambda e, d, m, a, g: S.Res(S.put_mm(d, e.reg, S.ZeroExt(32, g[3])))), KEYS['movd_g']), 'FAILED')

    def test_no_row_is_reported(self):
        self.assertEqual(C.check('0f0e/0.0.0.0')[0], 'no-row')                      # femms (3DNow): no spec row


if __name__ == '__main__': unittest.main()
