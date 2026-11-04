#!/usr/bin/env python3
"""NATIVE1 (render REQUESTS.md R3): decomp units the native renderer replaces leave the build.

    tools/rules/native1.py <units.json> <drop list> <out units.json> [--overrides <tsv>]

The drop list (render/native/drop_units.txt) has one unit source path per line, `#` comments, glob patterns allowed
(`src/gamebryo2.2/corelibs/niconsolerenderer/*`). The units stay in the decomp; they leave the unit list of
units.json, so they are neither compiled nor linked, and references to their functions resolve to the native
overrides (fnvlink --override). The tool reports the `@fnv` addresses the dropped units defined that no plain
address row of the overrides table covers (they would become unresolved).

prepare.py runs the same drop on the prepared tree's build/units.json (drop_native_units, after the copy). This
module exports no RULES: it is not a text rule."""
import fnmatch
import json
import os
import sys


def load_list(path):
    """The drop patterns of a list file ([] when there is none)."""
    if not path or not os.path.exists(path):
        return []
    out = []
    for line in open(path, encoding='utf-8'):
        line = line.split('#', 1)[0].strip()
        if line:
            out.append(line)
    return out


def _source(u):
    return u.get('source', u.get('src', ''))


def dropped(source, patterns):
    return any(source == p or fnmatch.fnmatchcase(source, p) for p in patterns)


def drop_units(data, patterns):
    """-> (units.json object without the matching units, [the dropped unit objects]). data is not modified."""
    keep, gone = [], []
    for u in data.get('units', []):
        (gone if dropped(_source(u), patterns) else keep).append(u)
    out = dict(data)
    out['units'] = keep
    return out, gone


def override_addresses(path):
    """Plain address rows of an overrides table (render/native/overrides.tsv); vslot rows name no address here."""
    out = set()
    if path and os.path.exists(path):
        for line in open(path, encoding='utf-8'):
            col = line.split('\t', 1)[0].strip()
            if col and not col.startswith('#'):
                try:
                    out.add(int(col, 16))
                except ValueError:
                    pass
    return out


def uncovered(gone, overrides):
    """Addresses defined by the dropped units that the overrides table does not cover, sorted."""
    have = override_addresses(overrides)
    addrs = {int(f['address'], 16) for u in gone for f in u.get('functions', []) if 'address' in f}
    return sorted(addrs - have)


def main(argv):
    args = list(argv)
    overrides = None
    if '--overrides' in args:
        i = args.index('--overrides')
        overrides = args[i + 1]
        del args[i:i + 2]
    if len(args) != 3:
        sys.exit(__doc__.split('\n\n')[1])
    src, lst, dst = args
    data, gone = drop_units(json.load(open(src)), load_list(lst))
    json.dump(data, open(dst, 'w'))
    miss = uncovered(gone, overrides)
    print('NATIVE1: %d units dropped, %d kept; %d addresses they defined without an override row%s'
          % (len(gone), len(data['units']), len(miss), (': ' + ' '.join('%08x' % a for a in miss[:20]) +
                                                       (' ...' if len(miss) > 20 else '')) if miss else ''))


if __name__ == '__main__':
    main(sys.argv[1:])
