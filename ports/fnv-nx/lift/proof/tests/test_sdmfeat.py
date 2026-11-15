"""sdmfeat.py: opcode-cell parsing, flag expressions and form matching on fixed SDM cells (no SDM text needed)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
from proof import sdmfeat as S, shape  # noqa: E402

T = S.Table(S.unify([(n, l, s, r, b, 'c') for n, l, s, r, b in (('SSE2', 1, 0, 'EDX', 26), ('SSE3', 1, 0, 'ECX', 0), ('AVX', 1, 0, 'ECX', 28), ('AVX512F', 7, 0, 'EBX', 16), ('AVX512VL', 7, 0, 'EBX', 31))], []))
CPU = {(0, 'EAX'): 4, (0x80000000, 'EAX'): 0x80000004, (1, 'EDX'): 0x078bfbff, (1, 'ECX'): 0}


class Parse(unittest.TestCase):
    def test_opcode_cells(self):
        p = S.parse_opcode('66 0F 38 00 /r'); self.assertEqual((p['pfx'], p['bytes'], p['digit']), ('66', [0x0f, 0x38, 0x00], None))
        p = S.parse_opcode('58+ rd'); self.assertEqual((p['bytes'], p['plus_r']), ([0x58], True))
        p = S.parse_opcode('0F B0/r'); self.assertEqual((p['bytes'], p['digit']), ([0x0f, 0xb0], None))     # typo in the SDM text: no space before /r
        p = S.parse_opcode('DF /1'); self.assertEqual((p['bytes'], p['digit']), ([0xdf], 1))
        self.assertTrue(S.parse_opcode('VEX.128.66.0F.WIG E3 /r')['vex'])

    def test_flag_expressions(self):
        self.assertEqual(S.eval_flag('SSE2', T, CPU), (True, []))
        self.assertEqual(S.eval_flag('SSE3', T, CPU), (False, []))
        self.assertEqual(S.eval_flag('(AVX512VL AND AVX512F) OR AVX', T, CPU), (False, []))     # leaf 7 is above the declared max basic leaf 4
        self.assertEqual(S.flag_expr('SSE2 Add packed single', T), 'SSE2')                       # Description text bleeding into the flag cell

    def test_match_prefixes(self):
        rows = [{'op': 'NP 0F 58 /r', 'p': S.parse_opcode('NP 0F 58 /r'), 'flag': 'SSE'}, {'op': '66 0F 58 /r', 'p': S.parse_opcode('66 0F 58 /r'), 'flag': 'SSE2'},
                {'op': 'F3 0F 58 /r', 'p': S.parse_opcode('F3 0F 58 /r'), 'flag': 'SSE'}]
        md = shape.decoder()
        for code, flag in ((b'\x0f\x58\xc1', 'SSE'), (b'\x66\x0f\x58\xc1', 'SSE2'), (b'\xf3\x0f\x58\xc1', 'SSE')):
            ins = S.decode(code, md); mand, body, _ = S.form_of(ins)
            cand, _ = S.match_rows(mand, body, rows)
            self.assertEqual([r['flag'] for r in cand], [flag])


class DeclaredCpu(unittest.TestCase):
    def test_declared_cpu_is_the_record_and_equals_the_generated_tables(self):
        """declared_cpu() reads verify/cpuid_model.json through the one derivation (cpumodel_bits.model_words); when the generated cpumodel_tab.inc / fnvl_cpuref.h of root lift/rt are available
        (env GATE_CACHE) the same values are in them"""
        import json, re
        cpu = S.declared_cpu()
        mod = json.load(open(os.path.join(os.path.dirname(os.path.abspath(S.__file__)), '..', '..', 'verify', 'cpuid_model.json')))
        self.assertEqual(cpu[(0, 'EAX')], mod['max_basic'])
        self.assertEqual(cpu[(0x80000000, 'EAX')], int(mod['max_ext'], 0))
        self.assertEqual(cpu[(1, 'EDX')], int(mod['leaf1_edx_raw'], 0) & ~int(mod['leaf1_edx_clear'], 0) & 0xffffffff)
        self.assertEqual(cpu[(1, 'ECX')], int(mod['leaf1_ecx_raw'], 0) & int(mod['leaf1_ecx_keep'], 0))
        self.assertEqual(cpu[(0x80000001, 'EDX')], int(mod['ext1_edx_raw'], 0) & int(mod['ext1_edx_keep'], 0))
        if not os.environ.get('GATE_CACHE'):
            self.skipTest('GATE_CACHE not set: no generated output to compare')
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(S.__file__))))
        import genout
        gd = genout.gen_dir(os.environ['GATE_CACHE'])
        h = open(os.path.join(gd, 'fnvl_cpuref.h')).read()
        d = dict(re.findall(r'#define (FNVL_\w+) (0x[0-9a-f]+)u', h))
        self.assertEqual(cpu[(1, 'ECX')], int(d['FNVL_CPUID1_ECX'], 16)); self.assertEqual(cpu[(1, 'EDX')], int(d['FNVL_CPUID1_EDX'], 16))
        e1 = re.search(r'E1\[4\] = \{ ([^}]*) \}', open(os.path.join(gd, 'cpumodel_tab.inc')).read()).group(1)
        e1 = [int(x.strip()[:-1], 16) for x in e1.split(',')]
        self.assertEqual((cpu[(0x80000001, 'ECX')], cpu[(0x80000001, 'EDX')]), (e1[2], e1[3]))


if __name__ == '__main__':
    unittest.main()
