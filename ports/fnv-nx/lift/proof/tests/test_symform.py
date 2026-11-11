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
    ('0x67 mov r32,[bx+si]', '678b00'),
    ('0x67 mov r32,[bp+disp8]', '678b4605'),
    ('0x67 mov r32,[disp16]', '678b062211'),
    ('0x67 add m8,imm8 [bx+di+disp16]', '678081221180'),
    ('0x67 movsb', '67a4'),
    ('0x67 rep movsb', '67f3a4'),
    ('0x67 loop', '67e2fb'),
    ('0x67 xlat', '67d7'),
    ('0x67 mov moffs16', '67a12211'),
    ('push r16', '6650'),
    ('push imm16', '66683412'),
    ('pop r16', '6659'),
    ('pop m16', '668f00'),
    ('bound r32,m64', '6200'),
    ('bound r16,m32', '666200'),
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


class AddressSize16(unittest.TestCase):
    """prefix 0x67 (SDM Vol. 2 2.1.5, Table 2-1): the effective address is the 16-bit sum, zero-extended; the string / loop registers are SI, DI, CX"""
    def setUp(self):
        import z3
        self.z3 = z3

    def form(self, h):
        return forms.build(symform.decode(bytes.fromhex(h), 0), dsl.C(0, 32))

    def valid(self, a, b):
        z3 = self.z3
        s = z3.Solver()
        s.add(dsl.to_smt(a) != b)
        return s.check() == z3.unsat

    def test_modrm_address_wraps_at_16_bits(self):
        z3 = self.z3
        f = self.form('678b0b')                   # mov ecx, [bp+di]
        load = f.regs[0][1]
        ebp, edi = z3.BitVec('ebp', 32), z3.BitVec('edi', 32)
        self.assertTrue(self.valid(load.a[0], z3.ZeroExt(16, z3.Extract(15, 0, ebp + edi))))

    def test_moffs16(self):
        z3 = self.z3
        f = self.form('678b0e2211')              # mov ecx, [0x1122] (16-bit absolute: mod 0 rm 6)
        self.assertTrue(self.valid(f.regs[0][1].a[0], z3.BitVecVal(0x1122, 32)))

    def test_string_registers_are_16_bit_and_upper_halves_kept(self):
        z3 = self.z3
        f = self.form('67a4')                    # movsb es:[di], [si]
        regs = dict((r, v) for r, v, _ in f.regs)
        esi, df = z3.BitVec('esi', 32), z3.BitVec('df', 1)
        step = z3.If(df == 1, z3.BitVecVal(-1, 16), z3.BitVecVal(1, 16))
        self.assertTrue(self.valid(regs['esi'], z3.Concat(z3.Extract(31, 16, esi), z3.Extract(15, 0, esi) + step)))
        self.assertEqual(len(f.stores), 1)

    def test_loop_counts_cx(self):
        z3 = self.z3
        f = self.form('67e2fb')
        ecx = z3.BitVec('ecx', 32)
        regs = dict((r, v) for r, v, _ in f.regs)
        self.assertTrue(self.valid(regs['ecx'], z3.Concat(z3.Extract(31, 16, ecx), z3.Extract(15, 0, ecx) - 1)))

    def test_prefix_changes_the_form_only_where_the_address_size_matters(self):
        def effects(h):
            f = self.form(h)
            return [(r, dsl.to_smt(v).sexpr()) for r, v, _ in f.regs] + [(fl, dsl.to_smt(v).sexpr()) for fl, v, _ in f.flags]
        self.assertEqual(effects('6783c001'), effects('83c001'))          # register-only: the prefix is ignored
        self.assertNotEqual(effects('678b00'), effects('8b00'))           # a memory operand: 16-bit addressing


class Stack16AndBound(unittest.TestCase):
    def setUp(self):
        import z3
        self.z3 = z3

    def form(self, h):
        return forms.build(symform.decode(bytes.fromhex(h), 0), dsl.C(0, 32))

    def valid(self, a, b):
        s = self.z3.Solver()
        s.add(dsl.to_smt(a) != b)
        return s.check() == self.z3.unsat

    def test_push_r16(self):
        z3 = self.z3
        f = self.form('6650')                    # push ax: ESP - 2, 16-bit store of the pre-state AX
        esp, eax = z3.BitVec('esp', 32), z3.BitVec('eax', 32)
        regs = dict((r, v) for r, v, _ in f.regs)
        self.assertTrue(self.valid(regs['esp'], esp - 2))
        (addr, val), = f.stores
        self.assertTrue(self.valid(addr, esp - 2))
        self.assertEqual(val.w, 16)
        self.assertTrue(self.valid(val, z3.Extract(15, 0, eax)))

    def test_pop_r16_keeps_the_upper_half(self):
        z3 = self.z3
        f = self.form('6659')                    # pop cx
        regs = dict((r, v) for r, v, _ in f.regs)
        esp = z3.BitVec('esp', 32); ecx = z3.BitVec('ecx', 32)
        self.assertTrue(self.valid(regs['esp'], esp + 2))
        self.assertEqual(regs['ecx'].w, 32)
        self.assertEqual(regs['ecx'].op, 'concat')
        self.assertTrue(self.valid(regs['ecx'].a[0], z3.Extract(31, 16, ecx)))

    def test_bound_is_signed_and_faults_with_the_bounds_exception(self):
        z3 = self.z3
        f = self.form('6200')                    # bound eax, [eax]
        (code, cond), = f.faults
        self.assertEqual(code, 4)
        self.assertEqual((f.regs, f.flags, f.stores), ([], [], []))
        eax = z3.BitVec('eax', 32)
        mem = z3.Array('mem', z3.BitVecSort(32), z3.BitVecSort(8))
        rd = lambda a: z3.Concat(*[z3.Select(mem, a + i) for i in (3, 2, 1, 0)])
        want = z3.Or(eax < rd(eax), rd(eax + 4) < eax)       # signed comparisons
        self.assertTrue(self.valid(cond, z3.If(want, z3.BitVecVal(1, 1), z3.BitVecVal(0, 1))))


if __name__ == '__main__':
    unittest.main()
