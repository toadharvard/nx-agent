#!/usr/bin/env python3
"""Test of the FOLD SET rule of toolchain/order/fnvnx_order.cc: a builtin of GCC's fold set (gimple-fold.cc gimple_fold_builtin) or any
non-atomic builtin that reads/writes through a pointer calls its library function, so GCC cannot fold it into plain loads/stores.
Fixture fixtures/order_fold.cc, built by the recipe with the full plugin set (realobj): copy16, zero8, cmp4 and len hold no plain
ldr/str/ldp/stp of data and call memcpy/memset/memcmp/strlen.
Run: python3 -I <this file> from the checkout root."""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'contract'))
import realobj  # noqa: E402
import test_sdd_order as T  # noqa: E402

fails = []


def main():
    obj = realobj.objects(['order_fold'])['order_fold']
    fns = T.functions(obj)
    want = {'_Z6copy16P1PPKS_': 'memcpy', '_Z5zero8Pc': 'memset', '_Z4cmp4PKcS0_': 'memcmp', '_Z3lenPKc': 'strlen'}
    for fn, callee in want.items():
        ins = fns.get(fn)
        if ins is None:
            fails.append('%s: missing (have %s)' % (fn, sorted(fns)))
            continue
        mem = [m for o, m, a in ins if re.match(r'(ldr|str|ldp|stp|ldur|stur|ldrb|strb)', m) and not re.search(r'\[(sp|x29)\b', a)]   # frame spills are not data
        calls = [a for o, m, a in ins if m in ('bl', 'b') and callee in a]
        # the GOT/PLT call carries the symbol in its relocation or operand text
        if mem:
            fails.append('%s: plain access %s' % (fn, mem))
        if not calls:
            fails.append('%s: no call to %s: %s' % (fn, callee, ins))
    if fails:
        print('FAIL', *fails, sep='\n  ')
        return 1
    print('ok: builtins on shared operands are library calls')
    return 0


if __name__ == '__main__':
    sys.exit(main())
