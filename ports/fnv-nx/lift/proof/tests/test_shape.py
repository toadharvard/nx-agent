"""Unit tests of proof/shape.py on synthetic byte strings (nothing derived from game data)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from proof import shape

MD = shape.decoder()


def dec(h):
    b = bytes.fromhex(h)
    ins = list(MD.disasm(b, 0x1000))
    assert len(ins) == 1 and ins[0].size == len(b), h
    return ins[0]


# (encoding, expected key)
CASES = [
    ('c8100002', 'c8000000/1.3.0.0'),                       # ENTER imm16, imm8: capstone reports only the imm8
    ('9a785634121234', '9a000000000000/1.6.0.0'),              # CALL ptr16:32
    ('eaaaaaaaaabbbb', 'ea000000000000/1.6.0.0'),
    ('a178563412', 'a100000000/0.0.1.4'),                      # moffs
    ('a278563412', 'a200000000/0.0.1.4'),
    ('67a11234', '67a10000/0.0.2.2'),                          # moffs16 (0x67)
    ('8b0578563412', '8b0500000000/0.0.2.4'),                  # absolute disp32
    ('8b4510', '8b4500/0.0.2.1'),
    ('8b0424', '8b0424/0.0.0.0'),
    ('8b4424f8', '8b442400/0.0.3.1'),
    ('8b043d78563412', '8b043d00000000/0.0.3.4'),             # SIB base=5 mod=0: disp32
    ('8b84240000f0ff', '8b842400000000/0.0.3.4'),
    ('c7458011223344', 'c7450000000000/3.4.2.1'),
    ('6683c101', '6683c100/3.1.0.0'),
    ('66c7002211', '66c7000000/3.2.0.0'),
    ('e978563412', 'e900000000/1.4.0.0'),
    ('eb10', 'eb00/1.1.0.0'),
    ('0f8412345678', '0f8400000000/2.4.0.0'),
    ('d40a', 'd400/1.1.0.0'),
    ('c20800', 'c20000/1.2.0.0'),
    ('0f3a0fc105', '0f3a0fc100/4.1.0.0'),                      # palignr: imm8 after a 3-byte opcode
    ('0fa4c105', '0fa4c100/3.1.0.0'),
    ('69c011223344', '69c000000000/2.4.0.0'),
    ('6b451005', '6b450000/3.1.2.1'),
    ('0f0fc1b0', '0f0fc1b0/0.0.0.0'),                          # 3DNow!: the suffix byte is an opcode byte
    ('d9c1', 'd9c1/0.0.0.0'),                                  # register-form x87: the ModRM byte is not masked
    ('0f77', '0f77/0.0.0.0'),
    ('f30f1000', 'f30f1000/0.0.0.0'),
    ('f20f104508', 'f20f104500/0.0.4.1'),
    ('ff248578563412', 'ff248500000000/0.0.3.4'),
]


class ShapeKey(unittest.TestCase):
    def test_cases(self):
        for enc, key in CASES:
            self.assertEqual(shape.shape_key(dec(enc)), key, enc)

    def test_masking_is_total(self):
        """every masked field byte may take any value without changing the key; bytes outside the mask change it"""
        for enc, key in CASES:
            b = bytearray.fromhex(enc)
            ins = dec(enc)
            _, _, start, end, _ = shape._layout(ins)
            for k in range(start, end):
                for v in (0x00, 0x01, 0x7f, 0x80, 0xff):
                    c = bytearray(b)
                    c[k] = v
                    j = dec(bytes(c).hex())
                    self.assertEqual(shape.shape_key(j), key, (enc, k, v))
            for k in list(range(0, start)) + list(range(end, len(b))):
                c = bytearray(b)
                c[k] ^= 0x01
                d = list(MD.disasm(bytes(c), 0x1000))
                if len(d) == 1 and d[0].size == len(c):
                    self.assertNotEqual(shape.shape_key(d[0]), key, (enc, k))

    def test_rep_roundtrip(self):
        for enc, key in CASES:
            r = shape.rep_bytes(key)
            self.assertEqual(len(r), len(enc) // 2)
            self.assertEqual(shape.shape_key(dec(r.hex())), key)

    def test_capstone_gaps_are_not_used(self):
        # capstone 5.0 reports imm_size 86 for ptr16:32 and only the imm8 of ENTER; the key does not depend on those fields
        self.assertEqual(shape.shape_key(dec('9a785634121234')).split('/')[1], '1.6.0.0')
        self.assertEqual(shape.shape_key(dec('c8100002')).split('/')[1], '1.3.0.0')

    def test_modrm_exhaustive(self):
        """all 256 ModRM bytes (+ SIB when present) of `mov r32, r/m32` and `add r/m32, imm8`: the key's fields agree with capstone's disp_size"""
        n = 0
        for m in range(256):
            for sib in (0x00, 0x05, 0x25, 0x24, 0xff):
                for opc, tail in ((b'\x8b', b''), (b'\x83', b'\x01')):
                    b = opc + bytes([m, sib]) + b'\x00' * 8
                    for ins in MD.disasm(b, 0x1000):
                        if ins.size > len(opc) + 1 + 4 + 1 + 1 + 1 or ins.address != 0x1000:
                            break
                        key = shape.shape_key(ins)
                        self.assertEqual(int(key.split('/')[1].split('.')[3]), ins.disp_size, ins.bytes.hex())
                        n += 1
                        break
        self.assertGreater(n, 1000)


class Family(unittest.TestCase):
    def test_families(self):
        for enc, fam in (('01c8', 'int'), ('8b4510', 'int'), ('eb10', 'int'), ('d9c1', 'x87'), ('dd0500000000', 'x87'), ('9b', 'x87'),
                         ('f30f1000', 'simd'), ('0f6f00', 'simd'), ('0f77', 'simd'), ('0f0fc1b0', 'simd'), ('cd21', 'sys'), ('cc', 'sys'),
                         ('0fa2', 'sys'), ('0f31', 'sys'), ('0f01d0', 'sys'), ('f4', 'sys'), ('0f0b', 'sys')):
            self.assertEqual(shape.family(dec(enc)), fam, enc)


if __name__ == '__main__':
    unittest.main()
