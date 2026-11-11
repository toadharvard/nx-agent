#!/usr/bin/env python3
"""tests/s2d - ADDRFN1 (tools/rules/s2_bodies.py) on a hand-written unit: `reinterpret_cast<void*>(0x00<a>)` of a
.text address some unit defines becomes FNVNX_ADDRFN(<a>) (GCC: the address symbol __fnv_<a>; cl: the cast itself); an undefined address, a data
address and other casts stay; line numbers stay.

    python3 -I rules/tests/test_addrfn1.py
"""
import collections
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import support  # noqa: E402
support.legacy_units_lookup()
import s1_compile  # noqa: E402
import s2_bodies   # noqa: E402

SRC = '''struct F { void* rh; void* wh; void* dh; void* uh; int n; };
void Set(F* f)
{
    f->rh = reinterpret_cast<void*>(0x00401040);
    f->wh = reinterpret_cast< void * >( 0x00401080 );
    f->dh = reinterpret_cast<void*>(0x01010000);
    f->uh = reinterpret_cast<void*>(0x004010c0);
    f->n = (int)reinterpret_cast<char*>(0x00401040);
}
'''


class AddrFn1(unittest.TestCase):
    def test_rule(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'build'))
            os.makedirs(os.path.join(d, 'src'))
            units = {'units': [{'source': 'src/a.cpp', 'flags': ['/Od'], 'symbols': {}, 'functions': [
                {'symbol': '?R@@YAXXZ', 'address': '0x00401040'}, {'symbol': '?W@@YAXXZ', 'address': '0x00401080'}]}]}
            json.dump(units, open(os.path.join(d, 'build', 'units.json'), 'w'))
            path = os.path.join(d, 'src', 'a.cpp')
            open(path, 'w').write(SRC)
            s1_compile._UNIT_INFO = s1_compile._ALL_FUNCTIONS = None   # another test's tree may be cached
            st = collections.Counter()
            out = s2_bodies.r_code_address_casts(path, SRC, st)
        self.assertEqual(st['ADDRFN1'], 2)
        self.assertIn('FNVNX_ADDRFN(00401040)', out)
        self.assertIn('FNVNX_ADDRFN(00401080)', out)
        self.assertNotIn('__asm__', out)            # the asm label is the GCC macro's (fnvnx_compat.h)
        self.assertIn('reinterpret_cast<void*>(0x01010000)', out)   # data address
        self.assertIn('reinterpret_cast<void*>(0x004010c0)', out)   # no unit defines it
        self.assertIn('reinterpret_cast<char*>(0x00401040)', out)   # not a void* cast
        self.assertEqual(out.count('\n'), SRC.count('\n'))


if __name__ == '__main__':
    unittest.main()
