#!/usr/bin/env python3
"""Symbolic immediates (CERTS.md "Oracle rules" 2): for EVERY shape key whose Form has a symbolic immediate field, the shape's Form instantiated at immediate v equals the Form built from the
concrete instruction with immediate v (symform.link_ok), for every v of the field's width at which the concrete instruction has a Form (a builder that raises KeyError/AssertionError, e.g. reserved cmpps predicates, has nothing to equal).
The universe is a rule over the decoder, not a list: every byte string  [prefix none/66/f2/f3] [map none/0f/0f38/0f3a] [opcode 00..ff] [ModRM c0/00/40/80] 00*8  that capstone decodes
(proof/shape.decoder) is reduced with shape.shape_key; the keys with an immediate field (symform.parse_key) that have a symbolic Form (symform.symbuild; a shape without a builder or with a symbolic
branch is not in the universe: formgate reports those as `unlinked`) are the universe, whatever the family (SIMD pshufd/shufps/pextrw/pslldq, ALU, shifts, imul, enter, far pointers ...).
Values: a 1-byte field takes all 256; a field of 2, 4 or 6 bytes takes (a) every byte lane x all 256 values with the other lanes 0x00 and (b) every lane at 00/7f/80/ff with the other lanes 0xff
(2^16 / 2^32 / 2^48 instances are not enumerated: the Form reads the field only through byte-wise slices and zext/sext of them, see symform.derive).  The counts are printed.
test_forms_immsel.py  (exit 0 = all pass; ~2 min on one core)"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import declared_x86sys; declared_x86sys.configure()
from proof import forms, symform, shape as shp  # noqa: E402

PREFIXES = ('', '66', 'f2', 'f3'); MAPS = ('', '0f', '0f38', '0f3a'); MODRM = ('c0', '00', '40', '80')


def immediate_shape_keys():
    """the shape keys with an immediate field of the decoder's encoding space (rule above)"""
    dec, keys = shp.decoder(), set()
    for pre in PREFIXES:
        for mp in MAPS:
            for op in range(256):
                for mrm in MODRM:
                    raw = bytes.fromhex('%s%s%02x%s' % (pre, mp, op, mrm) + '00' * 8)
                    for ins in dec.disasm(raw, 0x401000):
                        try: k = shp.shape_key(ins)
                        except shp.ShapeError: break
                        if symform.parse_key(k)[1]['imm']: keys.add(k)
                        break
    return sorted(keys)


def universe():
    """[(key, SymForm)] of the immediate shapes that have a symbolic Form, and the count of those that have none"""
    out, none = [], 0
    for k in immediate_shape_keys():
        try: out.append((k, symform.symbuild(k)))
        except (KeyError, symform.FieldDecodeError, symform.SymbolicBranch, AssertionError): none += 1
    return out, none


def values(width):
    """the immediate values of a field of `width` bytes: all (1 byte) or the lane rule (wider)"""
    if width == 1: return list(range(256))
    out = set()
    for lane in range(width):
        for v in range(256): out.add(v << (8 * lane))
        for v in (0x00, 0x7f, 0x80, 0xff): out.add(((1 << (8 * width)) - 1) & ~(0xff << (8 * lane)) | (v << (8 * lane)))
    return sorted(out)


class T(unittest.TestCase):
    def test_every_immediate_shape_links_at_every_value(self):
        uni, none = universe(); bad = n = noconc = 0; width = {}
        for k, sf in uni:
            rep, f = symform.parse_key(k); off, sz = f['imm']; width[sz] = width.get(sz, 0) + 1
            for v in values(sz):
                raw = bytearray(rep); raw[off:off + sz] = v.to_bytes(sz, 'little'); n += 1
                try: forms.build(list(shp.decoder().disasm(bytes(raw), 0x401000))[0], 0x401000)
                except (KeyError, AssertionError): noconc += 1; continue          # no concrete Form at this value (cmpps imm >= 8 reserved, an int vector undecided on the declared OS): nothing to equal
                try: ok, why = symform.link_ok(sf, bytes(raw), 0x401000)
                except Exception as e: ok, why = False, repr(e)
                if not ok: bad += 1; print('FAIL', k, '%x' % v, why)
        print('immediate shapes with a Form %d (field widths %s), without a Form %d, instances %d (no concrete Form at the value, not compared: %d), fails %d' % (len(uni), sorted(width.items()), none, n, noconc, bad))
        self.assertGreater(len(uni), 0)
        self.assertEqual(bad, 0)


if __name__ == '__main__':
    unittest.main()
