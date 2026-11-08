"""D26 (b) widening (R2, S2 seamcheck VCALL 00932b70 / V4 g10): a call-only class whose slot-0 placeholder is a letter
and zeros (`r0`), or one that defines one of its members, takes `virtual ~C()` only when a `callonly_exe` row of a
d22.json confirms it from the exe (tools/rules/_r2_callonly_exe.py). Our own snippets, nothing of the decomp."""
import importlib.util, json, os, sys, tempfile, unittest
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.normpath(os.path.join(HERE, '..', '..', 'tools'))
sys.path.insert(0, TOOLS)
import prepare  # noqa: E402
sys.modules['prepare'] = prepare
spec = importlib.util.spec_from_file_location('s1_compile_r2', os.path.join(TOOLS, 'rules', 's1_compile.py'))
s1 = importlib.util.module_from_spec(spec); spec.loader.exec_module(s1)

REF = ('struct Ref\n{\n    virtual void r0();\n    virtual void r1();\n    virtual int GetNode();\n};\n'
       'int f(Ref* r) { return r->GetNode(); }\n')
OWN = ('struct Owner\n{\n    virtual void v00();\n    virtual void SetObject(void* p);\n    virtual void Apply();\n'
       '    void Run(int k);\n};\nvoid Owner::SetObject(void* p) { Apply(); }\n')
PATH = '/nonexistent/src/ai/unit.cpp'


def rule(text, rows, path=PATH):
    d = tempfile.mkdtemp()
    json.dump({'format': 'fnvnx-d22/1', 'classes': [], 'callonly_exe': rows}, open(os.path.join(d, 'd22.json'), 'w'))
    os.environ['FNVNX_D22'] = os.path.join(d, 'd22.json')
    for k in ('_fnvnx_d22', '_fnvnx_d22_slots', '_fnvnx_d22_cxe'):
        prepare.__dict__.pop(k, None)
    st = Counter()
    return s1.r_vtable_dtor_slot(path, text, st), st


class CallOnlyExe(unittest.TestCase):
    def tearDown(self):
        os.environ.pop('FNVNX_D22', None)
        for k in ('_fnvnx_d22', '_fnvnx_d22_slots', '_fnvnx_d22_cxe'):
            prepare.__dict__.pop(k, None)

    def test_letter_zero_needs_exe_row(self):
        out, _ = rule(REF, [])
        self.assertNotIn('FNVNX_D22(Ref', out)
        out, st = rule(REF, [{'class': 'Ref', 'file': 'src/ai/unit.cpp'}])
        self.assertIn('FNVNX_D22(Ref, virtual void r0();)', out)
        self.assertEqual(st['D22-CALLONLY'], 1)

    def test_row_of_another_file(self):
        out, _ = rule(REF, [{'class': 'Ref', 'file': 'src/other/unit.cpp'}])
        self.assertNotIn('FNVNX_D22(Ref', out)

    def test_defined_member_needs_exe_row(self):
        out, _ = rule(OWN, [])
        self.assertNotIn('FNVNX_D22(Owner', out)
        out, _ = rule(OWN, [{'class': 'Owner', 'file': 'src/ai/unit.cpp'}])
        self.assertIn('FNVNX_D22(Owner, virtual void v00();)', out)

    def test_other_names_unchanged(self):
        # a real name at slot 0 stays, row or not
        t = REF.replace('r0()', 'Init()')
        out, _ = rule(t, [{'class': 'Ref', 'file': 'src/ai/unit.cpp'}])
        self.assertNotIn('FNVNX_D22(Ref', out)


if __name__ == '__main__':
    unittest.main()
