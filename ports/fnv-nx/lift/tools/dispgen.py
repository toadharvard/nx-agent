#!/usr/bin/env python3
"""The dispatcher generator: ONE implementation of the table that rt/rt.c reads (fnvl_disp_table, fnvl_g, fnvl_phf_params in disp.S).
    dispgen.py ENTRIES OUT.S          ENTRIES = rows `addr<TAB>sym[<TAB>...]` (the format of the pack's entries.tsv; addr is hex; `#` lines and blank lines are ignored)
RULE: disp.S is a pure function of the entry list. The keys are the set of addresses, the symbol of a key is the second column; the row order does not matter
(phf.build sorts the keys). Same rows, same bytes: no clock, no environment variable, no path in the output. The hash is tools/phf.py (the minimal perfect hash,
hash-and-displace); the only inputs of the program are this file, tools/phf.py and the entries file, so it is usable as a recipe `generated`-stage tool.
A key that is not in the list resolves to fnvl_nobody (rt/rt.c): no row is added for it. Two rows with one address, a bad address or a row without a symbol exit with an error.
Used by liftgen.py --finish (rows of the pack it writes) and by test roots that build their own disp.S from their entry list."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))      # the hash lives in this tool root: a tool imports only from its own root
import phf


def parse(lines):
    """rows (iterable of lines) -> {addr: sym}."""
    syms = {}
    for n, ln in enumerate(lines, 1):
        ln = ln.rstrip('\n')
        if not ln.strip() or ln.startswith('#'): continue
        f = ln.split('\t')
        if len(f) < 2 or not f[1]: sys.exit('dispgen: line %d: a row is `addr<TAB>sym[...]`' % n)
        try: a = int(f[0], 16)
        except ValueError: sys.exit('dispgen: line %d: %r is not a hex address' % (n, f[0]))
        if not 0 <= a < 1 << 32 or a == 0xffffffff: sys.exit('dispgen: line %d: address %s is not a 32-bit key' % (n, f[0]))
        if a in syms: sys.exit('dispgen: line %d: address %08x is in the list twice' % (n, a))
        syms[a] = f[1]
    if not syms: sys.exit('dispgen: the entry list is empty')
    return syms


def table(rows):
    """rows: iterable of lines of an entries file, or of tuples (addr hex string or int, sym, ...) -> (disp.S text, phf table)."""
    syms = parse('\t'.join(str(c) if not isinstance(c, int) else '%08x' % c for c in r) if not isinstance(r, str) else r for r in rows)
    h = phf.build(list(syms))
    return phf.table_asm(syms, h), h


def main(argv):
    if len(argv) != 2: sys.exit('usage: dispgen.py ENTRIES OUT.S')
    with open(argv[0]) as f: text, h = table(f)
    with open(argv[1], 'w', newline='') as f: f.write(text)
    print('dispgen: %d slots, %d hash attempts' % (len(h['slots']), h['attempts']))


if __name__ == '__main__': main(sys.argv[1:])
