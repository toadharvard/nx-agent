"""vendormap / sdmfeat vendor rule: (1) synthetic cells (the rule on fixed values); (2) over the PINNED AMD APM (fnv-nx-build tools/amd-apm, commit and sha256 in pins.json `external`) the parsed Appendix E CPUID bit rows, map cells and subsets equal the committed
reference, proof/tests/vendor_cells.tsv, the cells of the declared-CPU-cleared feature (3DNow) are 0F 0E (Vol. 5) and 0F 0F (Vol. 3), no bit carries two features, and every row sits under its own function table."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
from proof import sdmfeat as S, vendormap as V  # noqa: E402

PINS = [e for e in json.load(open(os.path.join(HERE, '..', 'pins.json')))['external'] if e['name'].startswith('amd-apm')]     # every pinned AMD volume (Vol. 3, Vol. 5)
PIN = PINS[0]


class Synthetic(unittest.TestCase):
    def test_canonical_precedence_and_aliases(self):
        recs = S.unify([('SSE4.1', 1, 0, 'ECX', 19, 's')], [('SSE4_1', 1, 0, 'ECX', 19, 'a'), ('AESNI', 1, 0, 'ECX', 25, 'a')])
        by = {(r['leaf'], r['reg'], r['bit']): r for r in recs}
        r = by[(1, 'ECX', 19)]
        self.assertEqual((r['canonical'], r['sdm_name'], r['apm_name'], r['sdm_cite'], r['apm_cite']), ('SSE4.1', 'SSE4.1', 'SSE4_1', 's', 'a'))
        self.assertEqual(by[(1, 'ECX', 25)]['canonical'], 'AESNI')               # no SDM name: the APM's

    def test_one_vendor_two_names_at_one_address_is_an_error(self):
        with self.assertRaises(SystemExit):
            S.unify([('A', 1, 0, 'ECX', 1, 's'), ('B', 1, 0, 'ECX', 1, 's')], [])

    def test_reused_name_is_address_qualified_except_the_apm_stated_mirror(self):
        rows = [('FPU', 1, 0, 'EDX', 0, 'a'), ('FPU', 0x80000001, 0, 'EDX', 0, 'a'), ('X', 1, 0, 'EAX', 3, 'a'), ('X', 0xD, 0, 'EAX', 3, 'a')]
        recs = S.unify([], rows, [(0x80000001, 0, 'EDX', 0, 1, 0, 'EDX', 'FPU')])      # the APM text says Fn8000_0001_EDX[FPU] is the bit of Fn0000_0001_EDX[FPU]
        names = sorted(r['canonical'] for r in recs)
        self.assertEqual(names.count('FPU'), 2)                                   # one fact, two addresses
        self.assertTrue(all(n.startswith('X@') for n in names if n.startswith('X')))
        bare = sorted(r['canonical'] for r in S.unify([], rows))                  # equal names alone are not a mirror: two facts, two qualified names
        self.assertTrue(all('@' in n for n in bare))

    def test_mirror_statement_must_match_the_rows(self):
        with self.assertRaises(SystemExit):
            S.unify([], [('A', 1, 0, 'EDX', 0, 'a'), ('A', 0x80000001, 0, 'EDX', 0, 'a')], [(0x80000001, 0, 'EDX', 0, 1, 0, 'EDX', 'B')])

    def test_mirror_statement_text(self):
        self.assertTrue(V.MIRROR.search('FXSAVE and FXRSTOR instructions. Same as CPUID Fn0000_0001_EDX[FXSR].'))


SAMPLE = """Table of contents

Appendix E Obtaining Processor Information Via
the CPUID Instruction
\f
CPUID Fn0000_0001_EDX Feature Identifiers
 Bits Field Name        Description
31:29 \u2014                 Reserved
                        SSE instruction support. See Appendix D.
 25    SSE
 24    FXSR             FXSAVE and FXRSTOR instructions.
\f
 Bits Field Name        Description
  8:4  Family           a field, not a bit
  0    FPU              x87
CPUID Fn0000_0007_EBX_x1 Extended
  3    BMI9             sub-leaf 1 flag
CPUID Fn8000_001D_EAX_x[N:0] Cache Properties
  1    NotAFeature      the sub-leaf is a variable, so the row has no address
CPUID Fn8000_001D_EDX Next function
 25    MOVBE:           the colon is not part of the name
\f
Appendix F Something
 25    Stray            after the appendix
"""


class AppendixE(unittest.TestCase):
    def test_rule_on_synthetic_text(self):
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False, encoding='utf-8') as fh:
            fh.write(SAMPLE)
        try:
            rows = V.cpuid_rows(fh.name)
        finally:
            os.unlink(fh.name)
        self.assertEqual([r[:5] for r in rows], [('SSE', 1, 0, 'EDX', 25), ('FXSR', 1, 0, 'EDX', 24), ('FPU', 1, 0, 'EDX', 0), ('BMI9', 7, 1, 'EBX', 3), ('MOVBE', 0x8000001D, 0, 'EDX', 25)])


class Pinned(unittest.TestCase):
    def test_cells_equal_the_reference(self):
        B = os.environ.get('FNVNX_BUILD')
        if not B:
            self.skipTest('FNVNX_BUILD (a clone of fnv-nx-build, the declared input of this test) is not set')
        import pdfplumber
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(['git', '-C', B, 'fetch', '-q', 'origin', PIN['ref']], check=True)
            self.assertEqual(subprocess.run(['git', '-C', B, 'rev-parse', 'FETCH_HEAD'], check=True, capture_output=True, text=True).stdout.strip(), PIN['commit'], 'the pinned branch moved')
            vols = []
            for i, pin in enumerate(PINS):
                self.assertEqual(pin['commit'], PIN['commit'])
                pdf = os.path.join(d, 'v%d.pdf' % i)
                open(pdf, 'wb').write(subprocess.run(['git', '-C', B, 'show', 'FETCH_HEAD:' + pin['file']], check=True, capture_output=True).stdout)
                self.assertEqual(hashlib.sha256(open(pdf, 'rb').read()).hexdigest(), pin['sha256'])
                subprocess.run(['pdftotext', '-layout', pdf, pdf[:-4] + '.txt'], check=True)
                vols.append((pdf, pdf[:-4] + '.txt'))
            f, cells = V.layer(vols)
            txt = vols[0][1]; p = pdfplumber.open(vols[0][0]); t31 = V.table31(txt); fn = V.gating(txt, f); sub = V.subsets(p, txt, fn)
            recs = S.unify([], f, sorted({m for _, tx in vols for m in V.cpuid_mirrors(tx)}))     # the APM rows alone
            addr = [(r['leaf'], r['sub'], r['reg'], r['bit']) for r in recs]
            self.assertEqual(len(addr), len(set(addr)), 'address -> canonical is not a function: an address has two records')
            canon = [r['canonical'] for r in recs if not r['mirror_of']]      # the only extra addresses of a name are the APM's own `Same as CPUID ...` mirrors
            self.assertEqual(len(canon), len(set(canon)), 'canonical -> address is not one-to-one: a canonical name labels two addresses')
            got = V.dump(f) + ''.join('cell\t0F%02X\t%s\t%s\n' % (k[0], k[1], v[0]) for k, v in sorted(cells.items())) + ''.join('subset\t%s\t%s\n' % (m, v[0]) for m, v in sorted(sub.items()))
        self.assertEqual(got, open(os.path.join(HERE, 'vendor_cells.tsv')).read())
        self.assertEqual({k[0] for k, v in cells.items() if v[0] == '3DNow'}, {0x0e, 0x0f})            # 0F 0F from Vol. 3 Appendix A (map cell), 0F 0E FEMMS from Vol. 5 (instruction reference); no hand entry
        self.assertIn(('3DNow', 0x80000001, 0, 'EDX', 31), {x[:5] for x in f})
        # one bit, one feature; every row's leaf and register are those of the table that the row was read under (the citation names the section)
        seen = {}
        for name, leaf, sub_, reg, bit, cite in f:
            self.assertEqual(seen.setdefault((leaf, sub_, reg, bit), name), name, 'two features at one CPUID bit')
            self.assertIn('"CPUID Fn%04X_%04X_%s' % (leaf >> 16, leaf & 0xffff, reg), cite)
        # the four that the Table 3-1 reader missed, and FXSR at its own bit (EDX[24] of both functions; EDX[25] is SSE)
        for r in (('3DNowExt', 0x80000001, 0, 'EDX', 30), ('SSE4A', 0x80000001, 0, 'ECX', 6), ('XOP', 0x80000001, 0, 'ECX', 11), ('FMA4', 0x80000001, 0, 'ECX', 16), ('FXSR', 1, 0, 'EDX', 24), ('FXSR', 0x80000001, 0, 'EDX', 24), ('SSE', 1, 0, 'EDX', 25)):
            self.assertIn(r, {x[:5] for x in f})
        self.assertEqual({k for k, v in seen.items() if v == 'FXSR'}, {(1, 0, 'EDX', 24), (0x80000001, 0, 'EDX', 24)})
        # Table 3-1 against Appendix E: a flag that Appendix E defines by the same name sits at the register and bit that Table 3-1 prints
        for flag, regbits in t31.items():
            have = {(x[3], x[4]) for x in f if x[0] == flag}
            if have:
                self.assertTrue(regbits <= have, '%s: Table 3-1 %s, Appendix E %s' % (flag, sorted(regbits), sorted(have)))


if __name__ == '__main__':
    unittest.main()
