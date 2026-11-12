#!/usr/bin/env python3
"""The dispatcher generator: ONE implementation of the table that rt/rt.c reads (fnvl_disp_table, fnvl_g, fnvl_phf_params in disp.S).
    dispgen.py [--prefix P [--allow-empty]] ENTRIES OUT.S          ENTRIES = rows `addr<TAB>sym[<TAB>...]` (the format of the pack's entries.tsv; addr is hex; `#` lines and blank lines are ignored)
RULE: disp.S is a pure function of the entry list. The keys are the set of addresses, the symbol of a key is the second column; the row order does not matter
(phf.build sorts the keys). Same rows, same bytes: no clock, no environment variable, no path in the output. The hash is tools/phf.py (the minimal perfect hash,
hash-and-displace); the only inputs of the program are this file, tools/phf.py and the entries file, so it is usable as a recipe `generated`-stage tool.
A key that is not in the list resolves to fnvl_nobody (rt/rt.c): no row is added for it. Two rows with one address, a bad address or a row without a symbol exit with an error.
--prefix P writes the same table under the names of phf.table_asm(prefix=P) (the table of native bodies, rt/dispatch.c: prefix fnvnx_native, rows `exe address<TAB>native
symbol` from contract/nativerows.py); --allow-empty accepts an empty list for it (no native body yet: a table with no key, every lookup misses). The lifted
dispatcher's table is never empty: its list holds the universe.
--closure F: COMPLETENESS of the table over the closure (the list of every code address a call can reach, one hex address per row, from the closure gate): a closure address with no entry is an error, so the build is red. There is no run-time miss path that invents behaviour (CLAUDE.md option iii: an unmatched key at run time is proven unreachable).
Used by liftgen.py --finish (rows of the pack it writes) and by test roots that build their own disp.S from their entry list."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))      # the hash lives in this tool root: a tool imports only from its own root
import phf


def parse(lines, allow_empty=False):
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
    if not syms and not allow_empty: sys.exit('dispgen: the entry list is empty')
    return syms


def check_closure(syms, lines):
    """COMPLETENESS: every address of the closure (rows `addr` in hex, `#` and blank lines ignored; the first column is read) must be a key. Returns nothing; exits with the list of missing addresses."""
    miss = []
    for n, ln in enumerate(lines, 1):
        ln = ln.rstrip('\n')
        if not ln.strip() or ln.startswith('#'): continue
        try: a = int(ln.split('\t')[0], 16)
        except ValueError: sys.exit('dispgen: closure line %d: %r is not a hex address' % (n, ln.split('\t')[0]))
        if a not in syms: miss.append('%08x' % a)
    if miss: sys.exit('dispgen: %d closure target(s) have no dispatch entry: %s' % (len(miss), ' '.join(sorted(set(miss)))))


def table(rows, prefix='fnvl', allow_empty=False, closure=None):
    """rows: iterable of lines of an entries file, or of tuples (addr hex string or int, sym, ...) -> (disp.S text, phf table)."""
    syms = parse(('\t'.join(str(c) if not isinstance(c, int) else '%08x' % c for c in r) if not isinstance(r, str) else r for r in rows), allow_empty)
    if closure is not None: check_closure(syms, closure)
    h = phf.build(list(syms))
    return phf.table_asm(syms, h, prefix), h


def main(argv):
    prefix, empty, closure = 'fnvl', False, None
    while argv[:1] and argv[0].startswith('--'):
        if argv[0] == '--prefix' and len(argv) >= 2: prefix, argv = argv[1], argv[2:]
        elif argv[0] == '--allow-empty': empty, argv = True, argv[1:]
        elif argv[0] == '--closure' and len(argv) >= 2:
            with open(argv[1]) as f: closure = f.readlines()
            argv = argv[2:]
        else: break
    if len(argv) != 2: sys.exit('usage: dispgen.py [--prefix P] [--allow-empty] [--closure CLOSURE] ENTRIES OUT.S')
    with open(argv[0]) as f: text, h = table(f, prefix, empty, closure)
    with open(argv[1], 'w', newline='') as f: f.write(text)
    print('dispgen: %d slots, %d hash attempts' % (len(h['slots']), h['attempts']))


if __name__ == '__main__': main(sys.argv[1:])
