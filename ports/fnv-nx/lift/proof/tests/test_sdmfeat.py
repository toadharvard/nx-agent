"""sdmfeat.py: the declared CPU is the record and equals the generated tables."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
from fnvnx.lift.proof import sdmfeat as S  # noqa: E402

class DeclaredCpu(unittest.TestCase):
    def test_declared_cpu_is_the_record_and_equals_the_generated_tables(self):
        """declared_cpu(cpumodel_bits, record) reads the record (verify/cpuid_model.json) through the one derivation (cpumodel_bits.model_words); when the generated cpumodel_tab.inc / fnvl_cpuref.h of root lift/rt are available
        (env GATE_CACHE) the same values are in them"""
        import json, re
        from fnvnx.lift.fnvlift import cpumodel_bits
        rec = os.path.join(os.path.dirname(os.path.abspath(S.__file__)), '..', '..', 'verify', 'cpuid_model.json')
        cpu = S.declared_cpu(cpumodel_bits, rec)
        mod = json.load(open(rec))
        self.assertEqual(cpu[(0, 'EAX')], mod['max_basic'])
        self.assertEqual(cpu[(0x80000000, 'EAX')], int(mod['max_ext'], 0))
        self.assertEqual(cpu[(1, 'EDX')], int(mod['leaf1_edx_raw'], 0) & ~int(mod['leaf1_edx_clear'], 0) & 0xffffffff)
        self.assertEqual(cpu[(1, 'ECX')], int(mod['leaf1_ecx_raw'], 0) & int(mod['leaf1_ecx_keep'], 0))
        self.assertEqual(cpu[(0x80000001, 'EDX')], int(mod['ext1_edx_raw'], 0) & int(mod['ext1_edx_keep'], 0))
        if not os.environ.get('GATE_CACHE'):
            self.skipTest('GATE_CACHE not set: no generated output to compare')
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(S.__file__))))
        import fnvnx.lift.proof.genout as genout
        gd = genout.gen_dir(os.environ['GATE_CACHE'])
        h = open(os.path.join(gd, 'fnvl_cpuref.h')).read()
        d = dict(re.findall(r'#define (FNVL_\w+) (0x[0-9a-f]+)u', h))
        self.assertEqual(cpu[(1, 'ECX')], int(d['FNVL_CPUID1_ECX'], 16)); self.assertEqual(cpu[(1, 'EDX')], int(d['FNVL_CPUID1_EDX'], 16))
        e1 = re.search(r'E1\[4\] = \{ ([^}]*) \}', open(os.path.join(gd, 'cpumodel_tab.inc')).read()).group(1)
        e1 = [int(x.strip()[:-1], 16) for x in e1.split(',')]
        self.assertEqual((cpu[(0x80000001, 'ECX')], cpu[(0x80000001, 'EDX')]), (e1[2], e1[3]))


if __name__ == '__main__':
    unittest.main()
