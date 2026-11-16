#!/usr/bin/env python3
"""x87ops.json is exactly what the generator x87ops_census.py derives from x87spec.REGISTRY now (a generated table is never edited by hand)."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, ".."), os.path.join(HERE, "..", "..")]
import x87ops_census as C  # noqa: E402


class T(unittest.TestCase):
    def test_table_is_generated_output(self):
        with open(os.path.join(HERE, "..", "x87ops.json")) as fh:
            self.assertEqual(json.load(fh), json.loads(json.dumps(C.census(), sort_keys=True)))


if __name__ == "__main__":
    unittest.main()
