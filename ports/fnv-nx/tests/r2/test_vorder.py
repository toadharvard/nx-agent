"""VORDER1 (tools/rules/r2_vorder.py): same-name virtual overloads that the decomp declares in MSVC's reversed order
are sorted into ascending exe slot order for GCC, and the check reports any that stay out of order (S2 seamcheck
VCALL, MobileObject::Revert @00932f96)."""
import os, sys, tempfile, unittest
from collections import Counter
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools', 'rules'))
import r2_vorder as v

HDR = '''class P {
public:
    virtual ~P();                                   // slot 0x0
    virtual void Revert(unsigned int, Mob*);        // slot 0x15B
    virtual void Revert(Buf*);                      // slot 0x15A
    virtual void Other();                           // slot 0x15C
    virtual void Load(unsigned int, Mob*);          // slot 0x16
    virtual void Load(Buf*);                        // slot 0x17
};
'''


class VOrder(unittest.TestCase):
    def test_sorts_reversed_pair_only(self):
        st = Counter()
        out = v.r_virtual_overload_order('P.h', HDR, st)
        self.assertEqual(st['VORDER1'], 1)
        lines = out.splitlines()
        self.assertIn('Revert(Buf*)', lines[3]); self.assertIn('Revert(unsigned int', lines[4])
        self.assertIn('Load(unsigned int', lines[6])          # already ascending: untouched
        self.assertEqual(sorted(out.splitlines()), sorted(HDR.splitlines()))   # lines moved, not changed
        self.assertEqual(v.descents(out), [])

    def test_check_reports(self):
        self.assertEqual([(r[1], r[2], r[3]) for r in v.descents(HDR)], [('Revert', 0x15B, 0x15A)])
        split = HDR.replace('    virtual void Other();                           // slot 0x15C\n', '')
        split = split.replace('    virtual void Revert(Buf*);                      // slot 0x15A\n', '') \
                     .replace('};', '    virtual void Revert(Buf*);  // slot 0x15A\n};')
        self.assertEqual(len(v.descents(split)), 1)            # not adjacent: the rule cannot sort it, the check sees it
        self.assertEqual(v.r_virtual_overload_order('P.h', split, Counter()), split)

    def test_separate_classes(self):
        two = 'struct A {\n  virtual void f(int); // slot 0x5\n};\nstruct B {\n  virtual void f(int); // slot 0x2\n};\n'
        self.assertEqual(v.descents(two), [])

    def test_post_check(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'include'))
            open(os.path.join(d, 'include', 'P.h'), 'w').write(HDR)
            st = Counter(); os.environ['FNVNX_VORDER_LAX'] = '1'
            try:
                v.post_check(d, st, Counter())
            finally:
                del os.environ['FNVNX_VORDER_LAX']
            self.assertEqual(st['VORDER1-LEFT'], 1)
            self.assertRaises(SystemExit, v.post_check, d, Counter(), Counter())


if __name__ == '__main__':
    unittest.main()
