#!/usr/bin/env python3
"""Unit test of fnvlink.abi_manual_trap (D24): a `manual` ABI pair that differs in an aggregate or the result
pointer stays unbound (cause abi-manual); varargs-only manual pairs keep their binding.

    python3 tools/link/test_abimanual.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import abi      # noqa: E402
import fnvlink  # noqa: E402

FAILS = []


def v(*classes):
    x = abi.Verdict()
    x.kind, x.classes = 'manual', list(classes)
    return x


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


check(fnvlink.abi_manual_trap(v('arity(thiscall-member)', 'sret-explicit')), 'sret-explicit: unbound')
check(fnvlink.abi_manual_trap(v('ret-aggregate')), 'ret-aggregate: unbound')
check(fnvlink.abi_manual_trap(v('aggregate-param', 'ref-passes-fewer')), 'aggregate-param: unbound')
check(not fnvlink.abi_manual_trap(v('arity(free-cdecl)', 'varargs')), 'varargs only: kept')
print('%d failed' % len(FAILS))
sys.exit(1 if FAILS else 0)
