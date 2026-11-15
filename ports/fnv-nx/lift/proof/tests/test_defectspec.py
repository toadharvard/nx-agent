"""defectspec: every declared defect id of defects.py has an SDM spec."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(HERE, '..'))
from proof import defects  # noqa: E402
from proof import defectspec  # noqa: E402


class T(unittest.TestCase):
    def test_every_defect_id_has_a_spec(self):
        self.assertEqual({d['id'] for d in defects.DEFECTS}, set(defectspec.SPECS))


if __name__ == '__main__':
    unittest.main()
