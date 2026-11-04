#!/usr/bin/env python3
"""V2 check of S2c-abi b9299ca (text-addr): an array or char label the decomp places at a .text address nothing
defines binds to an absolute symbol at that address, the x86 value. That is right only when the address is a value
(an immediate the generated initializers pass), never code a native caller would run: no unit may define a function
there, and every such reference must be a data use.

    FNVNX_LINK_OUT=<link out dir with refs.tsv> FNVNX_UNITS=<build/units.json> python3 tests/v2/test_s2c_textaddr.py
    (skip without them: both are decomp-derived and stay in scratch)

  X1  no `@@3Q` / `@@3D` extern-text or text-addr reference names an address some unit defines as a function
  X2  no such label is a callback: its unit's source never passes `&label` to atexit / _onexit (FNVNX_DECOMP: the
      decomp checkout). Fails at decomp 87eb9373: blk_00c00000_o2.cpp 0x00c1d8b0 calls
      `FUN_00ec658f(&UNK_00fdd3b0)` (0x00ec658f = _atexit), and 0x00fdd3b0 is x86 code (the static's exit thunk):
      bound absolute, the native atexit table gets an x86 address and exit jumps into it
"""
import json
import os
import re
import sys


def main():
    out, units = os.environ.get('FNVNX_LINK_OUT'), os.environ.get('FNVNX_UNITS')
    if not (out and units and os.path.isfile(os.path.join(out, 'refs.tsv')) and os.path.isfile(units)):
        print('skip: no FNVNX_LINK_OUT / FNVNX_UNITS')
        return 0
    funcs = {int(f['address'], 16) for u in json.load(open(units))['units'] for f in u.get('functions') or ()}
    rows = set()
    for l in open(os.path.join(out, 'refs.tsv')):
        c = l.rstrip('\n').split('\t')
        if len(c) >= 7 and c[2] in ('extern-text', 'text-addr') and ('@@3Q' in c[6] or '@@3D' in c[6]) and c[4]:
            rows.add((c[0].split('#')[0], c[1], int(c[4], 16)))
    code = sorted((s, n, '%08x' % a) for s, n, a in rows if a in funcs)
    dec = os.environ.get('FNVNX_DECOMP') or os.path.dirname(os.path.dirname(os.path.abspath(units)))
    atexit_names = {k.split('@')[0].lstrip('?') for u in json.load(open(units))['units']
                    for k, v in (u.get('symbols') or {}).items() if v == '0x00ec658f'} | {'atexit', '_onexit'}
    other = []
    for src, name, a in sorted(rows):
        p = os.path.join(dec, src)
        text = open(p, errors='replace').read() if os.path.isfile(p) else ''
        for f in atexit_names:
            if re.search(r'\b%s\s*\(\s*&\s*%s\b' % (re.escape(f), re.escape(name)), text):
                other.append((src, name, '%08x' % a))
    fails = 0
    for case, ok, what in (('X1', not code, '%d references, at a function start: %s' % (len(rows), code[:5])),
                           ('X2', not other, 'labels passed to atexit: %s' % other[:5])):
        print('%s %s %s' % ('ok' if ok else 'FAIL', case, what))
        fails += not ok
    print('ok' if not fails else 'FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
