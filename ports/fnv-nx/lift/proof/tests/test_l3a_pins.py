"""every file of l3a_pins.json is byte-identical to the L3a commit it was taken from (git blob id and sha256 recorded there, the only place)."""
import hashlib
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..', '..', '..', '..'))


class T(unittest.TestCase):
    def test_pinned_copies(self):
        pins = json.load(open(os.path.join(HERE, '..', 'l3a_pins.json')))
        self.assertTrue(pins['files'])
        for f in pins['files']:
            d = open(os.path.join(ROOT, f['path']), 'rb').read()
            self.assertEqual(hashlib.sha256(d).hexdigest(), f['sha256'], '%s differs from L3a %s: merge L3a, never edit here' % (f['path'], f['l3a_commit'][:12]))
            self.assertEqual(hashlib.sha1(b'blob %d\0' % len(d) + d).hexdigest(), f['blob'], f['path'])


if __name__ == '__main__':
    unittest.main()
