#!/usr/bin/env python3
"""tests/s2d - GC1 (tools/link/vtables.py gc_weaken): after `ld -r --gc-sections`, an undefined symbol that only a
discarded section used leaves the symbol table; one a kept section uses stays. Synthetic assembly, no decomp content.

    python3 -I tests/s2d/test_gc1.py        (skips without aarch64-linux-gnu-as / ld / objcopy)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'link'))
import vtables   # noqa: E402

SRC = '''    .section .text.keep,"ax",%progbits
    .globl keep
    .type keep, %function
keep:
    b used_elsewhere
    .section .text.dead,"ax",%progbits
    .globl dead
    .type dead, %function
dead:
    b only_dead_uses
'''


@unittest.skipUnless(shutil.which(vtables.AS) and shutil.which(vtables.LD) and shutil.which(vtables.OBJCOPY),
                     'no aarch64 binutils')
class Gc1(unittest.TestCase):
    def test_gc_weaken(self):
        with tempfile.TemporaryDirectory() as d:
            s, o = os.path.join(d, 'a.s'), os.path.join(d, 'a.o')
            with open(s, 'w') as fh:
                fh.write(SRC)
            subprocess.run([vtables.AS, '-mabi=ilp32', '-o', o, s], check=True)
            self.assertIn('only_dead_uses', {n for n, *_ in vtables.Obj(o).syms})
            r = vtables.gc_weaken(o, {'keep'}, os.path.join(d, 'x'))
            self.assertIsNotNone(r)
            x = vtables.Obj(r[0])
            names = {n for n, *_ in x.syms}
            self.assertIn('used_elsewhere', names)
            self.assertNotIn('only_dead_uses', names)
            self.assertNotIn('dead', x.defs)
            self.assertEqual(x.unused_undefs(), [])


if __name__ == '__main__':
    unittest.main()
