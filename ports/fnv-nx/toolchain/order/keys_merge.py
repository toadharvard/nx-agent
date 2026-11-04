#!/usr/bin/env python3
"""keys_merge.py - the whole-program ordering key list from the per-unit discover= files (S12, docs/ORDERING.md
"Whole-program keys").

    toolchain/order/keys_merge.py <unit.keys>... > order.keys
    toolchain/order/keys_merge.py --list <file of unit.keys paths> > order.keys

Each unit's file holds its own keys (`field Class::member`, `var name`) and the facts another unit may complete:
    fwd <fn> <i>            fn hands (the pointee of) its parameter i to Interlocked*
    pcall <f> <j> <g> <i>   f passes its parameter j on to g's parameter i
    call <g> <i> <key>      a call of g binds <key> to g's parameter i
A forwarder defined in one unit and called in another (or a forwarder of a forwarder across units) is resolved here,
to a fixed point. The output is the sorted `field` / `var` / `pfield` lines only, which the plugin reads with keys=.
"""
import sys


def main(argv):
    paths = argv[1:]
    if paths[:1] == ['--list']:
        paths = [l.strip() for l in open(paths[1]) if l.strip()]
    keys, fwd, pcalls, calls = set(), set(), set(), set()
    for p in paths:
        try:
            lines = open(p, encoding='utf-8', errors='replace').read().splitlines()
        except OSError:
            continue
        for l in lines:
            if l.startswith('field ') or l.startswith('var ') or l.startswith('pfield '):
                keys.add(l)
                continue
            w = l.split(' ', 3)
            if w[0] == 'fwd' and len(w) == 3:
                fwd.add((w[1], w[2]))
            elif w[0] == 'pcall' and len(w) == 4:
                f, j, rest = w[1], w[2], w[3].split(' ')
                if len(rest) == 2:
                    pcalls.add((f, j, rest[0], rest[1]))
            elif w[0] == 'call' and len(w) == 4:
                calls.add((w[1], w[2], w[3]))
    grew = True
    while grew:
        grew = False
        for f, j, g, i in pcalls:
            if (g, i) in fwd and (f, j) not in fwd:
                fwd.add((f, j))
                grew = True
    for g, i, k in calls:
        if (g, i) in fwd:
            keys.add(k)
    sys.stdout.write(''.join(k + '\n' for k in sorted(keys)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
