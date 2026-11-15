"""sysspec: class (a)/(b) shapes prove against the SDM spec under the declared facts; a conditional fault on a privileged instruction fails."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
from proof import shape, sysspec


def key(h):
    return shape.shape_key(next(iter(shape.decoder().disasm(bytes.fromhex(h), 0))))


class T(unittest.TestCase):
    def test_classes(self):
        for h in ('fa', 'f4', 'ec', 'cc', 'ce', 'f1'):
            self.assertEqual(sysspec.check(key(h + '00000000'))[0], 'proved', h)

    def test_uncovered_is_not_proved(self):
        self.assertEqual(sysspec.check(key('0fa2'))[0], 'not-covered')


if __name__ == '__main__':
    unittest.main()
