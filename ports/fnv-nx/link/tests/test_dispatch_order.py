#!/usr/bin/env python3
"""Test of the rule "every memory access a plugin creates is created ordered" for toolchain/dispatch/fnvnx_dispatch.cc (R9, docs/ORDERING.md).
The call-site slot `site` of an indirect call is shared memory (fnvnx_dispatch stores it, other threads call through it); the load that
fnvnx_dispatch builds before RTL expansion is `__atomic_load_8(&site, ACQUIRE)` (ldar x).  Fixture fixtures/sdd_order.cc (virtual calls,
deletes through a virtual destructor, a call through a pointer), built by the recipe with the full plugin set (realobj).  Checks on the
real objects: in every function that holds an indirect call, the 8-byte load that feeds the key compare (the instruction before the
`cmp w, w`) is `ldar x`, and no 8-byte load through a non-sp base is plain; gate_order certifies every `ldar`.
No network, no game data.  Run: python3 -I <this file> from the checkout root."""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'contract'))
import gate_order  # noqa: E402
import realobj  # noqa: E402
import test_sdd_order as T  # noqa: E402


def last_writer(ins, k, reg):
    """the instruction before index K that last wrote register number REG (as its first operand, or one of the two of an ldp)"""
    for j in range(k - 1, -1, -1):
        o, m, a = ins[j]
        if m in ('str', 'stp', 'cmp', 'cmn', 'tst', 'b', 'bl', 'blr', 'br', 'ret', 'nop') or m.startswith('b.') or m.startswith('cb') or m.startswith('tb'):
            continue
        ops = a.split(',')
        dests = ops[:2] if m == 'ldp' else ops[:1]
        if any(re.fullmatch(r'\s*[wx]%s\b.*' % reg, d) for d in dests):
            return ins[j]
    return None


def main():
    obj = realobj.objects(['sdd_order'])['sdd_order']
    fns = T.functions(obj)
    rows = {item: kind for item, kind in gate_order.scan_object(obj)[0]}
    fails = []
    sites = 0
    for fn, ins in sorted(fns.items()):
        cmps = [k for k, (o, m, a) in enumerate(ins) if m == 'cmp' and a.startswith('w') and ', w' in a]
        for k in cmps:
            # the slot word is the 8-byte load that last wrote one of the compare's registers (the compare is `fp == record.fp`; the register
            # holds the whole 64-bit record, whose low half is compared); a compare of other values (a spill reload is `ldr x, [sp]`) is no slot compare
            regs = [r for r in re.findall(r'\bw(\d+)\b', ins[k][2])]
            ld = None
            for r in regs:
                w = last_writer(ins, k, r)
                if w and w[1] in ('ldr', 'ldar') and w[2].startswith('x') and '[sp' not in w[2] and '[x29' not in w[2]:
                    ld = w
                    break
            if not ld:
                continue
            o, m, a = ld
            sites += 1
            if m != 'ldar':
                fails.append('%s+0x%x: slot load is %s %s, want ldar' % (fn, o, m, a))
            elif rows.get('%s+0x%x' % (fn, o)) != '':
                fails.append('%s+0x%x: ldar not certified: %r' % (fn, o, rows.get('%s+0x%x' % (fn, o))))
        plain = [(o, a) for o, m, a in ins if m == 'ldr' and a.startswith('x') and '[sp' not in a and '[x29' not in a]
        if plain:
            fails.append('%s: plain 8-byte load(s) through a non-frame base: %r' % (fn, plain))
    # every call site has one miss-path call of fnvnx_dispatch and one slot load: the two counts are equal (the fixture has call sites)
    calls = sum(1 for ins in fns.values() for o, m, a in ins if m == 'bl' and 'fnvnx_dispatch' in a)
    if not calls or sites != calls:
        fails.append('%d call(s) of fnvnx_dispatch in the object, %d slot loads found before a compare' % (calls, sites))
    if fails:
        print('FAIL', *fails, sep='\n  ')
        return 1
    print('ok: %d dispatch slot loads are ldar, certified by gate_order' % sites)
    return 0


if __name__ == '__main__':
    sys.exit(main())
