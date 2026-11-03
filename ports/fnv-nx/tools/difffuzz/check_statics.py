#!/usr/bin/env python3
"""check_statics.py - unit-local (static / anonymous-namespace) variables that the oracle keeps at an image address.

MSVC placed a file-static variable like any other in the image (units.json names it with its address); GCC keeps
it local to the object, and tools/link can only rebind global symbols to their image address. Code of the unit uses
a private copy while everything else (other units, image data pointers) sees the image copy.

    check_statics.py <objects dir> [--list]

Matches local OBJECT symbols `_ZL<n><name>` (and plain C names) against `?<name>@@3...` / `_<name>` entries of the
unit's units.json symbols with a data-window address.
"""
import json
import os
import re
import sys

import config

sys.path.insert(0, str(config.LINKTOOLS))
import elfsyms  # noqa: E402

ZL = re.compile(r'^_ZL(\d+)(\w+)$')
MS = re.compile(r'^\?(\w+)@@3')


def main():
    d = sys.argv[1]
    units = json.load(open(config.DECOMP / 'build/units.json'))['units']
    names = {}
    for u in units:
        m = names.setdefault(config.obj_name(u['source']), {})
        for n, a in u['symbols'].items():
            a = int(a, 16)
            if not 0x00fdf000 <= a < 0x02100000:
                continue
            mm = MS.match(n)
            if mm:
                m.setdefault(mm.group(1), a)
            elif n.startswith('_') and n[1:].isidentifier():
                m.setdefault(n[1:], a)
    total, objs = 0, 0
    for n in sorted(os.listdir(d)):
        if not n.endswith('.o') or n.startswith('zz_') or n not in names:
            continue
        hits = []
        for s in elfsyms.symbols(os.path.join(d, n)):
            if s.bind != 'local' or s.type != 'object' or not s.shndx:
                continue
            m = ZL.match(s.name)
            base = m.group(2)[:int(m.group(1))] if m else s.name
            a = names[n].get(base)
            if a is not None:
                hits.append((base, a))
        if hits:
            objs += 1
            total += len(hits)
            if '--list' in sys.argv:
                for base, a in hits:
                    print('%s\t%s\t%08x' % (n, base, a))
    print('unit-local variables with an image address: %d in %d objects' % (total, objs))


if __name__ == '__main__':
    main()
