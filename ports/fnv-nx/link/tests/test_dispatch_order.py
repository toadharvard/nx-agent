#!/usr/bin/env python3
"""Test of the rule "every memory access a plugin creates is created ordered" for toolchain/dispatch/fnvnx_dispatch.cc (R9, docs/ORDERING.md).
The call-site slot `site` of an indirect call is shared memory (fnvnx_dispatch stores it, other threads call through it); the load that
fnvnx_dispatch builds before RTL expansion is `__atomic_load_8(&site, ACQUIRE)` (ldar x).  Fixture fixtures/sdd_order.cc (virtual calls,
deletes through a virtual destructor, a call through a pointer), built by the recipe with the full plugin set (realobj).  Checks on the
real objects: in every function that holds an indirect call, the 8-byte load that feeds the key compare (the instruction before the
`cmp w, w`) is `ldar x`, and no 8-byte load through a non-sp base is plain; gate_order certifies every `ldar`.
No network, no game data.  Run: python3 -I <this file> from the checkout root."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'contract'))
import gate_order  # noqa: E402
import realobj  # noqa: E402
import test_sdd_order as T  # noqa: E402


def main():
    obj = realobj.objects(['sdd_order'])['sdd_order']
    fns = T.functions(obj)
    rows = {item: kind for item, kind in gate_order.scan_object(obj)[0]}
    fails = []
    sites = 0
    for fn, ins in sorted(fns.items()):
        cmps = [k for k, (o, m, a) in enumerate(ins) if m == 'cmp' and a.startswith('w') and ', w' in a]
        for k in cmps:
            # the load of the slot word is the nearest 8-byte load before the compare
            ld = [x for x in ins[:k] if x[1] in ('ldr', 'ldar') and x[2].startswith('x')]
            if not ld:
                continue
            o, m, a = ld[-1]
            sites += 1
            if m != 'ldar':
                fails.append('%s+0x%x: slot load is %s %s, want ldar' % (fn, o, m, a))
            elif rows.get('%s+0x%x' % (fn, o)) != '':
                fails.append('%s+0x%x: ldar not certified: %r' % (fn, o, rows.get('%s+0x%x' % (fn, o))))
        plain = [(o, a) for o, m, a in ins if m == 'ldr' and a.startswith('x') and '[sp' not in a and '[x29' not in a]
        if plain:
            fails.append('%s: plain 8-byte load(s) through a non-frame base: %r' % (fn, plain))
    if sites < 7:
        fails.append('expected at least 7 call sites with a slot compare in the fixture, found %d' % sites)
    if fails:
        print('FAIL', *fails, sep='\n  ')
        return 1
    print('ok: %d dispatch slot loads are ldar, certified by gate_order' % sites)
    return 0


if __name__ == '__main__':
    sys.exit(main())
