#!/usr/bin/env python3
"""tests/s2d - ADDRFN1 (tools/rules/s2_bodies.py) on a hand-written unit: `reinterpret_cast<void*>(0x00<a>)` of a
.text address some unit defines becomes the address symbol __fnv_<a> (a GCC asm label); an undefined address, a data
address and other casts stay; line numbers stay.

    python3 -I tests/s2d/test_addrfn1.py
"""
import collections
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'rules'))
import s1_compile  # noqa: E402
import s2_bodies   # noqa: E402

SRC = '''struct F { void* rh; void* wh; void* dh; void* uh; int n; };
void Set(F* f)
{
    f->rh = reinterpret_cast<void*>(0x00401230);
    f->wh = reinterpret_cast< void * >( 0x00401260 );
    f->dh = reinterpret_cast<void*>(0x01010000);
    f->uh = reinterpret_cast<void*>(0x00409990);
    f->n = (int)reinterpret_cast<char*>(0x00401230);
}
'''


class AddrFn1(unittest.TestCase):
    def test_rule(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'build'))
            os.makedirs(os.path.join(d, 'src'))
            units = {'units': [{'source': 'src/a.cpp', 'flags': ['/Od'], 'symbols': {}, 'functions': [
                {'symbol': '?R@@YAXXZ', 'address': '0x00401230'}, {'symbol': '?W@@YAXXZ', 'address': '0x00401260'}]}]}
            json.dump(units, open(os.path.join(d, 'build', 'units.json'), 'w'))
            path = os.path.join(d, 'src', 'a.cpp')
            open(path, 'w').write(SRC)
            s1_compile._UNIT_INFO = s1_compile._ALL_FUNCTIONS = None   # another test's tree may be cached
            st = collections.Counter()
            out = s2_bodies.r_code_address_casts(path, SRC, st)
        self.assertEqual(st['ADDRFN1'], 2)
        self.assertIn('__asm__("__fnv_00401230")', out)
        self.assertIn('__asm__("__fnv_00401260")', out)
        self.assertIn('reinterpret_cast<void*>(0x01010000)', out)   # data address
        self.assertIn('reinterpret_cast<void*>(0x00409990)', out)   # no unit defines it
        self.assertIn('reinterpret_cast<char*>(0x00401230)', out)   # not a void* cast
        self.assertEqual(out.count('\n'), SRC.count('\n'))


if __name__ == '__main__':
    unittest.main()
