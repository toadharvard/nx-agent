"""Unit tests of proof/symform.py on synthetic encodings (nothing derived from game data):
the symbolic Form of a shape, instantiated with the field bytes and the address of a concrete instruction, equals the concrete Form of that instruction."""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
from fnvlift import dsl
from proof import forms, forms_alu, forms_flow, forms_move, shape, symform  # noqa: F401  (the forms_* modules register the builders before a test patches one)
from proof.detrand import Rng

MD = shape.decoder()

# (name, encoding bytes (hex) with arbitrary field values)
CASES = [
    ('add r/m32,imm8', '83c105'),
    ('add m32,imm32', '81857856341278563412'),
    ('mov r32,[disp32]', '8b0d78563412'),
    ('lea r32,[ecx+eax*4+disp8]', '8d4c8110'),
    ('lea r32,[ebp+disp32]', '8d8578563412'),
    ('jcc rel32', '0f8420000000'),
    ('jcc rel8', '7410'),
    ('jmp rel32', 'e9f0ffffff'),
    ('call rel32', 'e878563412'),
    ('loop rel8', 'e2fb'),
    ('shl r32,imm8', 'c1e003'),
    ('sar m32,imm8', 'c17d0805'),
    ('push imm32', '6878563412'),
    ('push imm8', '6af0'),
    ('mov moffs32', 'a178563412'),
    ('mov moffs store', 'a378563412'),
    ('mov m8,imm8', 'c6450401'),
    ('mov r16,imm16', '66b83412'),
    ('imul r32,r/m32,imm8', '6bc90a'),
    ('ret imm16', 'c20800'),
    ('bt r/m32,imm8', '0fba2005'),
    ('test r/m8,imm8', 'f6c101'),
    ('or m16,imm8 (sign-extended to 16 bits)', '66830880'),
    ('add r16,imm16', '6681c03412'),
    ('rep movsd', 'f3a5'),
]

def decode(h, addr):
    b = bytes.fromhex(h)
    ins = list(MD.disasm(b, addr))
    assert len(ins) == 1 and ins[0].size == len(b), h
    return ins[0]

def instances(h, rnd, n=6):
    """the encoding with n random values of its symbolic bytes, at random addresses (and the extreme field values)"""
    key = shape.shape_key(decode(h, 0))
    rep, fields = symform.parse_key(key)
    for i in range(n + 3):
        vals = {}
        for name, f in fields.items():
            if f:
                w = 8 * f[1]
                vals[name] = [0, (1 << w) - 1, 1 << (w - 1)][i] if i < 3 else rnd.getrandbits(w)
        addr = rnd.choice([0, 0x1000, 0x401000, 0x7ffffff0, rnd.getrandbits(31)])
        yield key, symform.with_fields(rep, fields, vals), addr


class SymFormTest(unittest.TestCase):
    def test_link_on_synthetic_encodings(self):
        rnd = Rng(7)
        for name, h in CASES:
            key = shape.shape_key(decode(h, 0))
            sf = symform.symbuild(key)
            for key, raw, addr in instances(h, rnd):
                ok, why = symform.link_ok(sf, raw, addr)
                self.assertTrue(ok, '%s %s at %x: %s' % (name, raw.hex(), addr, why))

    def test_every_imm8_value(self):
        for name, h in (('shl', 'c1e003'), ('add', '83c105'), ('ror', 'c1c802'), ('push', '6af0')):
            key = shape.shape_key(decode(h, 0)); sf = symform.symbuild(key); rep, fields = symform.parse_key(key)
            for v in range(256):
                raw = symform.with_fields(rep, fields, {'imm': v})
                self.assertTrue(symform.link_ok(sf, raw, 0x401000)[0], '%s imm=%#x' % (name, v))

    def test_fields_and_eip_dependence(self):
        sf = symform.symbuild(shape.shape_key(decode('83c105', 0)))
        self.assertEqual(sf.used() & {'imm', 'disp', 'eip'}, {'imm', 'eip'})
        self.assertEqual(symform.eip_dependence(sf), 'none')
        self.assertEqual(symform.eip_dependence(symform.symbuild(shape.shape_key(decode('0f8420000000', 0)))), 'target')
        self.assertEqual(symform.eip_dependence(symform.symbuild(shape.shape_key(decode('e878563412', 0)))), 'data')
        sf = symform.symbuild(shape.shape_key(decode('8d4c8110', 0)))
        self.assertEqual(sf.used() & {'imm', 'disp'}, {'disp'})

    def test_slices_found_from_the_decoder(self):
        sf = symform.symbuild(shape.shape_key(decode('6af0', 0)))              # push imm8: sign-extended to 32 bits
        (sl,) = sf.imm_ops.values()
        self.assertEqual((sl.n, sl.ext, sl.rel), (1, 's', False))
        sf = symform.symbuild(shape.shape_key(decode('b80000f000', 0)))        # mov eax,imm32
        (sl,) = sf.imm_ops.values()
        self.assertEqual((sl.n, sl.rel), (4, False))
        sf = symform.symbuild(shape.shape_key(decode('eb10', 0)))              # jmp rel8: relative to the end of the instruction
        (sl,) = sf.imm_ops.values()
        self.assertEqual((sl.n, sl.ext, sl.rel), (1, 's', True))

    def test_builder_that_branches_on_a_field_is_reported(self):
        with self.assertRaises(symform.SymbolicBranch) as cm:                  # enter: the builder asserts the nesting level (a Python comparison)
            symform.symbuild(shape.shape_key(decode('c8100000', 0)))
        self.assertIn('forms_flow.py', str(cm.exception))

    def test_a_wrong_builder_is_caught_by_the_link(self):
        h = '83c1f0'; key = shape.shape_key(decode(h, 0)); orig = forms.H['add']
        try:
            def bad(c):                                                        # ADD whose immediate is zero-extended instead of sign-extended in the symbolic build only
                orig(c)
                if hasattr(c.ops[1].val, '_node'):
                    c.F.regs.clear(); c.F.flags.clear(); c.F.reg('ecx', dsl.V('ecx', 32) + dsl.zext(dsl.C(c.ops[1].val, 8), 32))
            forms.H['add'] = bad
            sf = symform.symbuild(key)
        finally:
            forms.H['add'] = orig
        self.assertFalse(symform.link_ok(sf, bytes.fromhex(h), 0x401000)[0])

    def test_hash_needs_no_z3(self):
        code = ("import sys; sys.path.insert(0, %r); from proof import symform, shape\n"
                "k = shape.shape_key(list(shape.decoder().disasm(bytes.fromhex('83c105'), 0))[0])\n"
                "print(symform.shape_sha(k)); assert 'z3' not in sys.modules" % os.path.join(HERE, '..', '..'))
        out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), symform.shape_sha(shape.shape_key(decode('83c105', 0))))


if __name__ == '__main__':
    unittest.main()
