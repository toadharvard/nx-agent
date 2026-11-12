"""Every address-like literal of rules/tests lies in the synthetic window of support.py (outside every guest image range): `0x` + 5..8 hex
digits and bare 8-digit tokens, in every test file.  Enum constants that are not addresses (>= 0x40000000) are values, not addresses."""
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: E402

HEX = re.compile(r'(?<![A-Za-z0-9_])(?:0[xX]([0-9a-fA-F]{5,8})|([0-9a-fA-F]{8}))(?![A-Za-z0-9_])')
support.assert_outside()
bad = []
for f in sorted(x for x in glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_*.py')) if os.path.basename(x) not in support.RULE_WINDOW_FILES):
    for i, ln in enumerate(open(f).read().splitlines(), 1):
        for a, b in HEX.findall(ln):
            v = int(a or b, 16)
            if v < 0x40000000 and not (support.SYNTH_BASE <= v < support.SYNTH_LIMIT) and 'FNVNX_ADDR_OK' not in ln:
                bad.append('%s:%d %#x' % (os.path.basename(f), i, v))
support.check('fixture addresses lie in the synthetic window', '' if not bad else '\n'.join(bad))
if bad:
    print('\n'.join(bad))
sys.exit(1 if support.FAILS or bad else 0)
