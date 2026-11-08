#!/usr/bin/env python3
"""FNVNX_VSLOT (DBUG1, V1 boot triage): GCC adds one behind the D1/D0 pair, both MSVC check headers keep the slot, and
patches/dbug1_v1_boot_vslots.s1b uses the macro (not a bare 0x12f) at its two sites.

    python3 -I tests/s1b/test_vslot.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fails = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        fails.append(what)


def text(*p):
    return open(os.path.join(ROOT, *p), encoding='utf-8').read()


gcc = re.search(r'#define FNVNX_VSLOT\(k\) (.*)', text('shim', 'include', 'fnvnx_compat.h'))
check(gcc is not None and gcc.group(1).strip() == '((k) + 1)', 'fnvnx_compat.h: FNVNX_VSLOT(k) is k + 1')
for p in (('tools', 'equiv', 'msvc', 'fnvnx_equiv_fi.h'), ('tests', 's1b', 'msvc', 's1b_check_fi.h')):
    m = re.search(r'#define FNVNX_VSLOT\(k\) (.*)', text(*p))
    check(m is not None and m.group(1).strip() == '(k)', '%s: FNVNX_VSLOT(k) is k' % p[-1])
pt = text('patches', 'dbug1_v1_boot_vslots.s1b')
check(pt.count('FNVNX_VSLOT(0x12') == 2, 'patch: two sites use FNVNX_VSLOT(0x12e)')
check('0x12f' not in pt.split('<<<', 1)[1], 'patch: no hunk spells the native index')
sys.exit(1 if fails else 0)
