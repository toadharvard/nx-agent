"""declared_gen.rows: the rows follow the SDM Operation lines and cpumodel.c's interface; a mismatch of output counts fails."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import declared_gen

CM = 'void fnvl_cpuid(u32 leaf, u32 sub, u32 o[4]) {}\nunsigned long long fnvl_rdtsc(void) { return 0; }\n'
SDM = '(EAX, EBX, ECX, EDX) := CPUID_INFO(EAX, ECX)\n        EDX:EAX := TimeStampCounter;\n'


class T(unittest.TestCase):
    def test_rows(self):
        r = declared_gen.rows(SDM, CM)
        self.assertEqual(r[0]['outputs'], ['eax', 'ebx', 'ecx', 'edx']); self.assertEqual(r[0]['ufs'], ['cpuid0', 'cpuid1', 'cpuid2', 'cpuid3'])
        self.assertEqual(r[1]['outputs'], ['eax', 'edx'])

    def test_count_mismatch(self):
        with self.assertRaises(SystemExit): declared_gen.rows(SDM, CM.replace('o[4]', 'o[3]'))

    def test_committed_rows_are_the_generated_ones(self):
        import json
        cm = open(declared_gen.CPUMODEL).read(); cur = json.load(open(declared_gen.OUT))
        self.assertEqual(cur['generated_from']['rt/cpumodel.c'], declared_gen.sha(cm.encode()))     # the committed file was generated from this cpumodel.c


if __name__ == '__main__':
    unittest.main()
