#!/usr/bin/env python3
"""Test of toolchain/order/rtlpasses.py: the committed rtlpasses.json and rtlpasses.md are exactly what the generator derives from the
pinned GCC tarball (verify/guest_compiler.json "provenance"), every pass of the RTL universe has a reviewed row, every cited file has the
row's sha256 and every cited line holds the cited identifier.
The tarball is named by the environment variable GCC_TARBALL (a path to the file named in the provenance); there is no default and no skip.
Run: GCC_TARBALL=<path>/gcc-13.3.0.tar.xz python3 -I <this file> from the checkout root."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plugbuild as pb  # noqa: E402

check = pb.Checks('test_rtlpasses')
tb = os.environ.get('GCC_TARBALL')
check('GCC_TARBALL names the pinned tarball', bool(tb) and os.path.isfile(tb), 'set GCC_TARBALL')
if tb and os.path.isfile(tb):
    r = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', 'order', 'rtlpasses.py'), tb, '--check'], capture_output=True, text=True)
    check('rtlpasses.py --check: every RTL pass classified, citations and rtlpasses.md current', r.returncode == 0, (r.stderr or '')[-1500:])
check.done()
