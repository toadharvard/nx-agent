#!/usr/bin/env python3
"""tests/s2d - exe_call_targets with search=True on a body the exe holds several times (COMDAT folding left no
unique copy): the call sites on which every copy reaches one address bind; a site whose copies disagree does not.
Hand-made object and image, no game bytes.

    python3 -I tests/s2d/test_copies.py
"""
import os
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'link'))
sys.path.insert(0, HERE)
import bodies   # noqa: E402
from test_bodies import coff, pe   # noqa: E402


def run(copies_f, copies_s):
    """copies_f: [(offset, callee address)] of copies of body f (call ?g); copies_s the same for body s (call ?k)."""
    f = b'\x55\x8b\xec\x6a\x01\x6a\x02\xe8\0\0\0\0\x83\xc4\x08\x5d\xc3'    # 17 bytes: call ?g at 8
    s = b'\x55\x8b\xec\x6a\x07\x6a\x09\xe8\0\0\0\0\x83\xc4\x08\x5d\xc3'    # a different body: call ?k at 8
    obj = coff({'?f@@YAXXZ': (f, {8: '?g@@YAXXZ'}), '?s@@YAXXZ': (s, {8: '?k@@YAXXZ'})},
               ['?g@@YAXXZ', '?k@@YAXXZ'])
    text = bytearray(b'\xcc' * 0x1000)
    for body, copies in ((f, copies_f), (s, copies_s)):
        for off, target in copies:
            text[off:off + len(body)] = body
            struct.pack_into('<i', text, off + 8, target - (0x401000 + off + 12))
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, 'u0.obj'), 'wb') as fo:
            fo.write(obj)
        with open(os.path.join(d, 'x.exe'), 'wb') as fo:
            fo.write(pe(0x400000, 0x401000, bytes(text)))
        exe = bodies.Pe(os.path.join(d, 'x.exe'))
        got = bodies.exe_call_targets(d, 0, {'functions': []}, {'?g@@YAXXZ', '?k@@YAXXZ'}, exe, search=True)
    return {k: dict(v) for k, v in got.items()}


class Copies(unittest.TestCase):
    G, K1, K2 = 0x4010a0, 0x4010c0, 0x4010e0

    def test_agreeing_copies_bind(self):
        got = run([(0x00, self.G), (0x40, self.G), (0x80, self.G)], [])
        self.assertEqual(got, {'?g@@YAXXZ': {self.G: 1}})   # one site, however many copies

    def test_disagreeing_copies_do_not(self):
        got = run([(0x00, self.G), (0x40, self.G)], [(0x100, self.K1), (0x140, self.K2)])
        self.assertEqual(got, {'?g@@YAXXZ': {self.G: 1}})   # ?k: its copies reach two addresses: nothing

    def test_unique_copy_still_places(self):
        self.assertEqual(run([(0x00, self.G)], [(0x100, self.K1)]),
                         {'?g@@YAXXZ': {self.G: 1}, '?k@@YAXXZ': {self.K1: 1}})

    def test_too_many_copies_are_ignored(self):
        n = bodies.SEARCH_COPIES + 1
        self.assertEqual(run([(i * 20, self.G) for i in range(n)], []), {})


if __name__ == '__main__':
    unittest.main()
