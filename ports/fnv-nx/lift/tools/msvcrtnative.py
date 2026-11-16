#!/usr/bin/env python3
"""The recipe of the root lift/tests/guest/msvcrttest/native: the only writer of its build.json. One image per terminal case of the test plus one image for the cases that
end in a return, so no image takes an argument: the implicit variant `msvcrttest` runs every return-ending case, the variant `termN` (N = 1..the number of terminal cases,
the `index` of terminal.json) runs terminal case N, which main.cc reads as the constant FNVL_TERMINAL_CASE (a define of the variant, so it is in that image's key). The
number of terminal cases is the one tools/guestgen_msvcrttest.py computes (cases(): every case whose stop is not `ret`).
    msvcrtnative.py LIFT_DIR            (writes LIFT_DIR/tests/guest/msvcrttest/native/build.json)"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = 'tests/guest/msvcrttest/native/build.json'
ROOT = 'ports/fnv-nx/lift/tests/guest/msvcrttest'
ENTRY = 'fnvl_msvcrttest_entry'


def terminal_cases(lift):
    sys.argv = ['guestgen_msvcrttest.py', '', lift]      # the generator reads the lifter package's directory from argv[2] at import
    sys.path.insert(0, HERE)
    import guestgen_msvcrttest as G
    P = G.program(); P.build()
    return sum(1 for _root, _what, _want, stop, _mem in G.cases(P) if stop != 'ret')


def recipe(n_terminal):
    link = lambda out: {'output': out, 'flags': ['-m', 'aarch64linux32'], 'entry': ENTRY}
    return {'format': 'fnvnx-recipe/1', 'profile': 'guest',
            'flags': {'c': ['-O2', '-Werror=attributes'], 'cxx': ['-std=gnu++17', '-O2', '-DFNVNX_SDD=1', '-Werror=attributes'], 'asm': []},
            'include': ['ports/fnv-nx/shim/include'], 'units': 'all',
            'uses': [{'root': ROOT + '/lifted', 'output': 'image.inc'}, {'root': 'ports/fnv-nx/lift/rt', 'output': 'GEN'}, {'root': 'ports/fnv-nx/shim', 'output': 'INCLUDE'}],
            'link': link('msvcrttest.elf'),
            'variants': {'term%d' % i: {'defines_add': ['FNVL_TERMINAL_CASE=%d' % i], 'link': link('msvcrttest_term%d.elf' % i)} for i in range(1, n_terminal + 1)},
            'external': [{'name': 'fnvnx-a64-msvcmodel', 'repo': 'fnv-nx-build', 'ref': 'tools/fnvnx-a64-msvcmodel', 'commit': '00e3d42acd10593d534b9a70bb8940cee7991436',
                          'tree_sha256': '3f25152cbc5a729d9e0a12fc5c62b6b912b6ab1571b6b3494de8f35e3904207d', 'at': '/ext/a64'}],
            'include_uses': [{'root': 'ports/fnv-nx/lift/rt', 'output': 'GEN'}]}


if __name__ == '__main__':
    if len(sys.argv) != 2: sys.exit('usage: msvcrtnative.py LIFT_DIR')
    lift = os.path.abspath(sys.argv[1])
    with open(os.path.join(lift, OUT), 'w') as f:
        json.dump(recipe(terminal_cases(lift)), f, indent=1); f.write('\n')
