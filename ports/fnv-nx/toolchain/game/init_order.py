#!/usr/bin/env python3
"""Check the static-constructor order of the game guest: no game unit's constructor before the platform's, the
runtime libraries' or the game boot's.

    python3 -I toolchain/game/init_order.py <fnvnx.map> <game_objects.list | -> [--list]

A game unit's constructor (e.g. S1's native NiMainSDM static) may use the clock, the ini functions, the MSVC
character tables, the registered modules and C++ exceptions. So everything that sets those up must come first in
.init_array:
  - the prioritized constructors: platform/os/os.h FNVNX_INIT_* (clocks 101, modules 103), the game boot 110;
  - the runtime libraries' unprioritized ones (crtbegin, libstdc++'s exception pools, newlib's stack guard), which
    toolchain/guest/guest.ld places before the rest.
Then the rest, in link order. The map (ld -Map) gives each .init_array input section's object in placement order;
game objects are the ones in game_objects.list (toolchain/game/objlist.py; `-`: objects under a /fixed/ directory).
Any object that is neither game nor runtime library (platform/, toolchain/, render/) is "own". The check fails when
an own or runtime entry comes after the first game entry, i.e. when one of them is unprioritized and linked after
the game. Exit 0 / 1; --list prints the order.
"""
import os
import re
import sys

RUNTIME = re.compile(r'(?:^|/)(?:crtbegin\w*\.o|crti\.o|crtend\w*\.o|lib(?:stdc\+\+|supc\+\+|c|g|m|gcc\w*|sysbase)\.a\()')


def init_array_entries(map_path):
    """[(address, size, section, object)] of the .init_array output section, in placement order."""
    out, inside, pend = [], False, None
    for line in open(map_path, errors='replace'):
        line = line.rstrip('\n')
        if not inside:
            inside = line.startswith('.init_array ') or line == '.init_array'
            continue
        if line and not line[0].isspace():   # the next output section
            break
        m = re.match(r'^ (\.init_array\S*|\.ctors\S*)(?:\s+(0x[0-9a-f]+)\s+(0x[0-9a-f]+)\s+(\S.*))?$', line)
        if m:
            if m.group(2):
                out.append((int(m.group(2), 16), int(m.group(3), 16), m.group(1), m.group(4)))
            else:
                pend = m.group(1)   # the name is long: address, size and object on the next line
            continue
        m = re.match(r'^\s+(0x[0-9a-f]+)\s+(0x[0-9a-f]+)\s+(\S.*)$', line)
        if m and pend:
            out.append((int(m.group(1), 16), int(m.group(2), 16), pend, m.group(3)))
        pend = None
    return [e for e in out if e[1]]


def classify(obj, game):
    if RUNTIME.search(obj):
        return 'runtime'
    path = obj.split('(')[0]
    if game is None:
        return 'game' if '/fixed/' in path else 'own'
    return 'game' if os.path.abspath(path) in game or os.path.basename(path) in game else 'own'


def check(entries, game):
    """-> (rows [(index, kind, priority, object)], violations [rows])"""
    rows, first_game, bad = [], None, []
    k = 0
    for addr, size, sec, obj in entries:
        prio = sec.split('.')[-1] if re.match(r'^\.(?:init_array|ctors)\.\d+$', sec) else ''
        kind = classify(obj, game)
        for _ in range(size // 4):
            row = (k, kind, prio, obj)
            rows.append(row)
            if kind == 'game' and first_game is None:
                first_game = k
            elif kind != 'game' and first_game is not None:
                bad.append(row)
            k += 1
    return rows, bad


def read_game_list(path):
    if path == '-':
        return None
    names = set()
    for line in open(path, errors='replace'):
        p = line.strip().strip('"')
        if p:
            names.add(os.path.abspath(p))
            names.add(os.path.basename(p))
    return names


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    rows, bad = check(init_array_entries(argv[0]), read_game_list(argv[1]))
    if '--list' in argv:
        for k, kind, prio, obj in rows:
            print('%3d  %-7s %-6s %s' % (k, kind, prio or '-', os.path.basename(obj)))
    counts = {}
    for _, kind, _, _ in rows:
        counts[kind] = counts.get(kind, 0) + 1
    desc = ', '.join('%d %s' % (counts[k], k) for k in ('runtime', 'own', 'game') if k in counts)
    if bad:
        print('init order: FAIL (%s): after the first game constructor: %s' % (
            desc, '; '.join('#%d %s %s' % (k, kind, os.path.basename(obj)) for k, kind, _, obj in bad)))
        return 1
    print('init order: OK (%d constructors: %s; every game one after the platform, runtime and boot ones)' % (
        len(rows), desc))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
