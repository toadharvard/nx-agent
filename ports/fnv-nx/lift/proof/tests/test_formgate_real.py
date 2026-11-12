"""Test of proof/formgate.py on the real declared input (no doubles): the model_sha256 of formgate_inputs.json equals the x86_proof.ir pin of sail_pins.txt.
References (tests/oracle.json): the two lines of those files. The certs and universe cases belong to the gate `forms` itself, not to a selftest."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.normpath(os.path.join(HERE, '..', '..'))
REPO = os.path.normpath(os.path.join(LIFT, '..', '..', '..'))
sys.path.insert(0, LIFT)
sys.path.insert(0, os.path.join(REPO, 'ports', 'fnv-nx', 'contract'))
sys.path.insert(0, os.path.join(REPO, 'ports', 'fnv-nx', 'verify'))
from proof import formgate as F


class Real(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = F.load_inputs()

    def test_inputs_are_valid_and_the_model_pin_matches(self):
        self.assertEqual(self.doc['model_sha256'], F.model_pin())


if __name__ == '__main__':
    unittest.main()
