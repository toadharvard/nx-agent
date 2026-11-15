"""CERTS.md "Certificate key K (closure rule)", obligations of item 5.  Differential test of the K key (irdeps.py): the closure_sha of an encoding changes under an edit of a function its run executed (isla call events,
model_sail.LAST['fns']), and under no edit of a function the run did not call, nor of the position annotations."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from proof import irdeps, model_sail  # noqa: E402
from proof.tests.test_model_patches import run  # noqa: E402


def mutate(text, irname, how):
    """text with the body of `fn irname(...) {` changed: how = 'edit' (a real token change) | 'pos' (only the source position annotations)"""
    m = re.search(r'^fn %s\(.*\{$' % re.escape(irname), text, re.M)
    assert m, irname
    end = text.index('\n}\n', m.end()) + 3
    body = text[m.start():end]
    if how == 'edit':
        new = body.replace('{\n', '{\n  zzedit = 1;\n', 1)
    else:
        new = re.sub(r' `(\d+)', lambda x: ' `%d' % (int(x.group(1)) + 1), body) or body
        assert new != body or ' `' not in body
    return text[:m.start()] + new + text[end:]


class Closure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        r, _, _ = run('85c0')
        cls.fns = list(model_sail.LAST['fns'])
        cls.text = open(model_sail.ir_file()).read()
        cls.ir = irdeps.IR(cls.text)
        cls.base = irdeps.closure_sha(cls.ir, cls.fns, 'e')
        cls.found, _ = irdeps.resolve(cls.ir, cls.fns)

    def test_the_run_called_functions_and_they_resolve(self):
        self.assertGreater(len(self.fns), 50)
        self.assertEqual(len(self.found), len(self.fns))

    def test_edit_of_an_executed_function_changes_the_key(self):
        for n in [x for x in self.found if x in self.ir.fns][:6]:
            self.assertNotEqual(irdeps.closure_sha(irdeps.IR(mutate(self.text, n, 'edit')), self.fns, 'e'), self.base, n)

    def test_edit_of_a_function_the_run_did_not_call_keeps_the_key(self):
        other = [n for n in self.ir.fns if n not in set(self.found)]
        self.assertGreater(len(other), 100)
        for n in other[:6]:
            self.assertEqual(irdeps.closure_sha(irdeps.IR(mutate(self.text, n, 'edit')), self.fns, 'e'), self.base, n)

    def test_position_annotations_do_not_matter(self):
        n = [x for x in self.found if x in self.ir.fns][0]
        self.assertEqual(irdeps.closure_sha(irdeps.IR(mutate(self.text, n, 'pos')), self.fns, 'e'), self.base)

    def test_the_run_environment_is_part_of_the_key(self):
        self.assertNotEqual(irdeps.closure_sha(self.ir, self.fns, 'f'), self.base)



class Refusal(unittest.TestCase):
    def test_an_ir_that_does_not_match_the_pin_is_refused(self):      # item 4: the record's IR is fetched by sha, not trusted: a record whose IR is not provided is REFUSED (never a silent stale); the IR-edit case (an executed function edited in the record's IR -> invalid) is test_certs_build.test_closure_key_keeps_a_record_valid_across_an_ir_change
        from proof import certs_build
        certs_build._K.clear()
        rec = {'model_sha': '1' * 64, 'model_fns': ['f'], 'closure_sha': '2' * 64}
        with self.assertRaises(certs_build.Refused):
            certs_build.model_valid(rec, '0' * 64)
        certs_build._K.clear()

if __name__ == '__main__':
    unittest.main()
