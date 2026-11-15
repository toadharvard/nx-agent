"""imageset: the image set is the rows of the module table (synthetic table and directories)."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import imageset


class T(unittest.TestCase):
    def test_rows_and_files(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as e:
            open(os.path.join(d, 'A_One.DLL'), 'w').close(); open(os.path.join(e, 'two.dll'), 'w').close(); open(os.path.join(d, 'Game.exe'), 'w').close()
            t = os.path.join(d, 'modules.tsv')
            open(t, 'w').write('# name\tsha\tsize\tpref\tassigned\n' + 'a_one.dll\t-\t1\t00400000\t01410000\n' + 'two.dll\t-\t1\t10000000\t01440000\n')
            self.assertEqual(imageset.load(t), [('a_one', 'a_one.dll', 0x400000, 0x1410000), ('two', 'two.dll', 0x10000000, 0x1440000)])
            ims = imageset.images(os.path.join(d, 'Game.exe'), t, [d, e])
            self.assertEqual([(n, s) for n, p, s in ims], [('Game', None), ('a_one', 'a_one'), ('two', 'two')])
            self.assertEqual(os.path.basename(ims[1][1]), 'A_One.DLL')             # file names compare case-insensitively
            open(t, 'w').write('missing.dll\t-\t1\t0\t0\n')
            with self.assertRaises(SystemExit):
                imageset.images(os.path.join(d, 'Game.exe'), t, [d, e])


if __name__ == '__main__':
    unittest.main()
