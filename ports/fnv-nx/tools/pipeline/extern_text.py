#!/usr/bin/env python3
"""extern_text.py - the list `fnvlink.py resolve --extern-text` takes: every image `.text` address the units
reference but no unit defines, with the identifiers the units call it by (S4's `addr_ids.txt`, J-0009; this is
the committed form of the scratch scripts it was made with).

    tools/pipeline/extern_text.py <units.json> <out.txt> [--names out_names.txt]

units.json: the decomp's `build/units.json` (the pristine checkout's, as S4 used; a prepared tree's works too).
Every unit lists the functions it defines (`functions`: address, MSVC symbol, size) and the names it binds to
addresses (`symbols`: MSVC name -> address). An address in the image's code range 0x00401000..0x00fde577 that is
bound by some unit and defined by none is written as one line

    0x<address> <identifier> <identifier> ...

where an identifier is the C/C++ name inside each MSVC name bound to it (the function name, or the class name of
a constructor / destructor; `__ehhandler$` and `__unwindfunclet$` funclets are skipped), sorted, without
duplicates. `--names` also writes the intermediate list (address, number of units, MSVC names), S4's
`undef_text.txt`. The output is derived from the decomp: local only, never committed.
"""
import collections
import json
import re
import sys

TEXT_LO, TEXT_HI = 0x00401000, 0x00fde577      # the image's .text (J-0002)


def ident(n):
    if n.startswith(('__ehhandler$', '__unwindfunclet$', '___ehhandler$')):
        return None
    if n.startswith('??'):
        m = re.match(r'\?\?[0-9]([A-Za-z_][A-Za-z0-9_]*)@', n)   # constructor / destructor: the class name
        return m.group(1) if m else None
    if n.startswith('?'):
        m = re.match(r'\?([A-Za-z_][A-Za-z0-9_]*)@', n)
        return m.group(1) if m else None
    if n.startswith('@'):
        return n[1:].split('@')[0]                               # __fastcall C name
    if n.startswith('_'):
        return n[1:].split('@')[0]                               # __cdecl / __stdcall C name
    return n


def extern_text(units):
    defined = set()
    refs = collections.defaultdict(set)
    users = collections.defaultdict(set)
    for u in units:
        for f in u.get('functions', []):
            defined.add(int(f['address'], 16))
        for name, addr in (u.get('symbols') or {}).items():
            a = int(addr, 16)
            refs[a].add(name.replace(' ', '\\x20'))
            users[a].add(u['source'])
    text = sorted(a for a in refs if TEXT_LO <= a <= TEXT_HI and a not in defined)
    return [(a, len(users[a]), sorted(refs[a])) for a in text]


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    rows = extern_text(json.load(open(argv[0]))['units'])
    if '--names' in argv:
        with open(argv[argv.index('--names') + 1], 'w') as f:
            for a, n, names in rows:
                f.write('0x%08x %d %s\n' % (a, n, ' '.join(names)))
    with open(argv[1], 'w') as f:
        for a, _, names in rows:
            ids = sorted({i for i in (ident(n) for n in names) if i})
            f.write('0x%08x %s\n' % (a, ' '.join(ids)))
    print('extern text: %d addresses referenced and defined by no unit' % len(rows))


if __name__ == '__main__':
    main(sys.argv[1:])
