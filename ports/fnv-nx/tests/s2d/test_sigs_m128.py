"""sigs._canon_type maps GCC's spellings of MSVC's __m128 (`float __vector(4)`, `__Float32x4_t`) to `__m128`, so a
native definition with __m128 parameters pairs with its MSVC key (R2, 3f7a6c35e923 rank 145: FUN_00cec7f0, whose
addr-host emitted 0 symbols because the keys never matched)."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools', 'link'))
import sigs


class CanonM128(unittest.TestCase):
    def test_vector_spellings(self):
        self.assertEqual(sigs._canon_type('float __vector(4) const*'), sigs._canon_type('__m128 const*'))
        self.assertEqual(sigs._canon_type('__Float32x4_t*'), sigs._canon_type('__m128*'))

    def test_other_types_unchanged(self):
        self.assertEqual(sigs._canon_type('float*'), 'float*')
        self.assertNotEqual(sigs._canon_type('float __vector(2)*'), sigs._canon_type('__m128*'))


if __name__ == '__main__':
    unittest.main()
