#!/usr/bin/env python3
"""abi_layout_equal.tsv (fnvlink resolve): proved layout-equal manual pairs keep their direct binding. The loader reads
(address, reference) rows, skips comments and malformed rows; the checked-in file names R2's two proved pairs. Our own
data only."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fnvlink  # noqa: E402

fails = 0


def ok(name, cond, info=''):
    global fails
    print(('ok   ' if cond else 'FAIL ') + name + ('' if cond else ': ' + str(info)[:400]))
    fails += not cond


def main():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'l.tsv')
        open(p, 'w').write('# c\n# address\treference\tevidence\n00401000\t_Z1fv\tproof\nzz\t_Z1gv\tx\n00402000\n')
        ok('rows parsed, comments and malformed rows skipped', fnvlink.abi_layout_equal(p) == {('00401000', '_Z1fv')},
           fnvlink.abi_layout_equal(p))
    fnvlink._ABI_LAYOUT_EQUAL = None
    s = fnvlink.abi_layout_equal()
    ok('the checked-in pairs', ('00dcd4d0', '_Z12FUN_00dcd4d08AgentKey') in s and len(s) == 2, s)
    print('test_layout_equal: %d failed' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
