#!/usr/bin/env python3
"""simdcheck's class order: a shape whose f_cpufeat.missing() is non-empty is ud-class, before any Form or row step, and no other shape is.  The universe is the keys file in $FNV_SIMD_KEYS (one shape key per line; the SIMD shape keys
of the pack / census, private) and, always, the keys named below.   python3 -I tests/test_simdclass.py     (the CPUID feature-bit table is derived from the pins by declared_cpufeat, as lift/cpufeat does; an input absent: skipped with the reason)"""
import os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, '..', '..')); sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import declared_cpufeat
CPUFEAT, WHY = declared_cpufeat.path()               # derived from the pins (declared_cpufeat); absent input: the test skips with the reason
if CPUFEAT:
    from fnvlift import inputs
    inputs.configure(cpufeat=CPUFEAT)                 # before the imports below: f_cpufeat reads the table when it is loaded
    from fnvlift import f_cpufeat
    from proof import shape, symform, simdcheck as C

NAMED = ['c5d1e300/0.0.0.0', '0f0f009a/0.0.0.0', '0f58c1/0.0.0.0']       # vpavgw (VEX), a 3DNow shape, addps


def universe():
    keys = list(NAMED)
    p = os.environ.get('FNV_SIMD_KEYS')
    if p: keys += [l.split()[0] for l in open(p) if l.strip() and not l.startswith('#')]
    return keys


@unittest.skipUnless(CPUFEAT, WHY)
class T(unittest.TestCase):
    def test_order(self):
        n = 0
        for k in universe():
            rep = symform.parse_key(k)[0]
            ins = list(shape.decoder().disasm(rep, 0))
            miss = bool(ins) and bool(f_cpufeat.missing(ins[0]))
            r = C.check(k, 1000)[0] if miss else None
            if miss: self.assertEqual(r, 'ud-class', k); n += 1
            else: self.assertFalse(C.cleared(rep), k)
        self.assertGreaterEqual(n, 2)                              # the named 3DNow and VEX shapes
        print('cleared shapes:', n, 'of', len(universe()))


if __name__ == '__main__': unittest.main()
