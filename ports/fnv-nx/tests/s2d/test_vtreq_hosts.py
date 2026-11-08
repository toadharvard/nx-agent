#!/usr/bin/env python3
"""tests/s2d - vtable_requests.tsv keeps a host unit path that holds a space (src/fallout_shared/region system/x.cpp),
alone or among others, and still reads an older file whose hosts are space-separated. Synthetic names.

    python3 -I tests/s2d/test_vtreq_hosts.py
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'link'))
import vtables   # noqa: E402


class RequestHosts(unittest.TestCase):
    def test_roundtrip(self):
        reqs = {'A': ('::A', {'vtable-ref'}, ['src/r s/a.cpp']),
                'B': ('::B', {'typeinfo-ref'}, ['src/r s/b.cpp', 'src/c.cpp', 'src/d e.cpp']),
                'C': ('::C', {'vftable'}, ['src/c.cpp'])}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'vtable_requests.tsv')
            vtables.write_requests(p, reqs)
            self.assertEqual(vtables.load_requests(p), reqs)
            open(p, 'w').write('#class\tspelling\twhy\thosts\nO\t::O\tvtable-ref\tsrc/a.cpp src/b.cpp\n')
            self.assertEqual(vtables.load_requests(p)['O'][2], ['src/a.cpp', 'src/b.cpp'])


if __name__ == '__main__':
    unittest.main()
