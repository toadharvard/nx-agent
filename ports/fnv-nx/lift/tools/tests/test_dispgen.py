#!/usr/bin/env python3
"""Unit test of tools/dispgen.py, the one dispatcher generator: disp.cc is a pure function of the entry list, and rt/rt.c's lookup over the generated table
finds every key (its symbol) and sends every other address to fnvl_nobody.
    dispgentest.py
The lookup is rt.c fnvl_dispatch() re-read from the generated C++ text: i = ((a ^ g[(a*k1) >> (32-nbits)]) * k2) >> (32-tbits); a slot hits iff its key == a and its
offset != 0. Prints one line per check; exit status 1 on any failure."""
import os, random, re, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(LIFT, 'tools'))
import dispgen, phf
M = 0xffffffff
ROWS = ['%08x\tL_%08x\tcode\n' % (a, a) for a in (0x401000, 0x401005, 0x40100a, 0x4010ff, 0x402000, 0x5fffff0, 0x7fffffff, 0xdeadbeef)] + \
       ['000fffd0\tfnvl_msvcrt_eh_nested_catch\truntime\n', '00100000\tI_00100000\timport\n', '# table_id 00\n', '\n']


def read(asm, prefix='fnvl'):
    """-> (slots [(key, symbol or None)], g, (k1, k2, nbits, tbits)) parsed back from the generated C++: slot i = {key, index + 1 into the entries array of `&symbol,` rows}"""
    def body(name):
        m = re.search(r'extern const [\w ]+ %s\[\d+\] [^=]*= (\{.*?\});' % re.escape(name), asm, re.S)
        return m.group(1)
    raw = [int(x[:-1] if x.endswith('u') else x, 0) for x in re.findall(r'0x[0-9a-f]+u|\b\d+u?\b', body(prefix + '_disp_table'))]
    ents = re.findall(r'&(\w+),', body(prefix + '_disp_entries'))
    slots = [(raw[i], ents[raw[i + 1] - 1] if raw[i + 1] else None) for i in range(0, len(raw), 2)]
    g = [int(x) for x in re.findall(r'\d+', body(prefix + '_g'))]
    params = tuple(int(x) for x in re.findall(r'\d+', body(prefix + '_phf_params')))
    return slots, g, params


def dispatch(tab, a):
    slots, g, (k1, k2, nb, tb) = tab
    i = (((a ^ g[((a * k1) & M) >> (32 - nb)]) * k2) & M) >> (32 - tb)
    key, sym = slots[i]
    return sym if key == a and sym else 'fnvl_nobody'


def main():
    bad = 0
    def check(name, ok):
        nonlocal bad
        print('%-60s %s' % (name, 'ok' if ok else 'FAIL')); bad += not ok
    asm, h = dispgen.table(ROWS)
    check('the entry typedef of the table is a line of platform/include/fnvl_host.h', phf.ENTRY_TYPEDEF in open(os.path.join(LIFT, '..', 'platform', 'include', 'fnvl_host.h')).read().splitlines())
    check('deterministic: same rows, same bytes', dispgen.table(ROWS)[0] == asm)
    rr = random.Random(7); sh = ROWS[:]; rr.shuffle(sh)
    check('row order does not change the bytes', dispgen.table(sh)[0] == asm)
    check('tuple rows and file lines give the same bytes', dispgen.table([tuple(l.rstrip('\n').split('\t')) for l in ROWS if l.strip() and l[0] != '#'])[0] == asm)
    with tempfile.TemporaryDirectory() as d:
        open(d + '/e.tsv', 'w').write(''.join(ROWS))
        r = subprocess.run([sys.executable, '-I', os.path.join(LIFT, 'tools', 'dispgen.py'), d + '/e.tsv', d + '/disp.cc'], capture_output=True, text=True, env={})
        check('command line: exit 0, file equals table()', r.returncode == 0 and open(d + '/disp.cc').read() == asm)
    syms = dispgen.parse(ROWS); tab = read(asm)
    check('every key is found with its own symbol (%d keys)' % len(syms), all(dispatch(tab, a) == s for a, s in syms.items()))
    r2 = random.Random(1); non = [a for a in (r2.getrandbits(32) for _ in range(20000)) if a not in syms]
    check('non-keys resolve to fnvl_nobody (%d addresses)' % len(non), all(dispatch(tab, a) == 'fnvl_nobody' for a in non))
    check('neighbours of keys (+-1) resolve to fnvl_nobody', all(dispatch(tab, a + d) == 'fnvl_nobody' for a in syms for d in (-1, 1) if (a + d) & M not in syms))
    def home(a):
        slots, g, (k1, k2, nb, tb) = tab
        return (((a ^ g[((a * k1) & M) >> (32 - nb)]) * k2) & M) >> (32 - tb)
    check('an empty slot holds a key that does not hash to that slot', all(home(k) != i for i, (k, s) in enumerate(tab[0]) if s is None))
    # the table of native bodies (rt/dispatch.c): the same generator under another prefix, and an empty list is a table that every lookup misses
    nasm, nh = dispgen.table(ROWS[:3], 'fnvnx_native')
    check('prefix: the symbols and the section carry the prefix, none of the lifted names remain', all(x in nasm for x in ('fnvnx_native_disp_table', 'fnvnx_native_disp_entries', 'fnvnx_native_g', 'fnvnx_native_phf_params', '.rodata.fnvnx_native_disp')) and not any(x in nasm for x in ('fnvl_disp', 'fnvl_g', 'fnvl_phf')))
    easm, eh = dispgen.table([], 'fnvnx_native')
    check('empty list: 8 empty slots, every key misses', easm.count(', 0u,') == 8 and all(phf.lookup(eh, k) is None for k in (0, 1, 0x401000, 0xffffffff)))
    check('empty list, lifted prefix: the same generator, every lookup misses', all(phf.lookup(dispgen.table([])[1], k) is None for k in (0, 0x401000)))
    for name, rows in (('a duplicate address exits', ROWS + ['401000\tX\n']), ('a row without a symbol exits', ['401000\n']), ('a non-hex address exits', ['zz\tX\n'])):
        try: dispgen.table(rows); ok = False
        except SystemExit: ok = True
        check(name, ok)
    # COMPLETENESS: a closure target with no entry makes the generator fail (build red); a complete closure passes
    a0 = ROWS[0].split('\t')[0] if isinstance(ROWS[0], str) else '%08x' % ROWS[0][0]
    allc = ['%s\n' % (r.split('\t')[0] if isinstance(r, str) else '%08x' % r[0]) for r in ROWS]
    try: dispgen.table(ROWS, closure=allc); ok = True
    except SystemExit: ok = False
    check('closure: every target has an entry, the table is generated', ok)
    try: dispgen.table(ROWS, closure=allc + ['deadbee0\n']); ok = False
    except SystemExit as e: ok = 'deadbee0' in str(e) and 'no dispatch entry' in str(e)
    check('closure: a target with no entry exits naming it', ok)
    # the rows file (fnvnx-disp-rows/1): generated from the emitted arrays, slot by slot, never re-sorted
    with tempfile.TemporaryDirectory() as d:
        open(d + '/e.tsv', 'w').write(''.join(ROWS))
        r = subprocess.run([sys.executable, '-I', os.path.join(LIFT, 'tools', 'dispgen.py'), '--prefix', 'fnvnx_native', '--rows', d + '/r.tsv', d + '/e.tsv', d + '/n.cc'], capture_output=True, text=True, env={})
        rl = open(d + '/r.tsv').read().split('\n') if r.returncode == 0 else []
        ncc = open(d + '/n.cc').read() if r.returncode == 0 else ''
    hd = [l for l in rl if l.startswith('#')]
    body = [l.split('\t') for l in rl if l and not l.startswith('#')]
    tbl = [(int(k, 0), int(i)) for k, i in re.findall(r'^  (0x[0-9a-f]+)u, (\d+)u,$', ncc, re.M)]
    ents = re.findall(r'^  &(\w+),$', ncc, re.M)
    check('rows: format line, the entries file table_id, the hash line, the column line, one row per entry', hd[:2] == ['# fnvnx-disp-rows/1', '# table_id 00'] and hd[2] == '# hash fnvnx_native_disp_table slots=%d empty=0' % len(tbl) and hd[3].startswith('# table') and len(hd) == 4 and len(body) == len(syms))
    check('rows: rows[j] is entries[j] of the emitted array: index, address, symbol', [(b[0], int(b[1]), b[3]) for b in body] == [('fnvnx_native_disp_entries', j, e) for j, e in enumerate(ents)]
          and [int(b[2], 16) for b in body] == sorted(syms) and [b[3] for b in body] == [syms[a] for a in sorted(syms)])
    check('rows: table[slot] of the emitted array is (addr, index+1) for every row, and every other slot is empty (index half 0)',
          all(tbl[int(b[4])] == (int(b[2], 16), int(b[1]) + 1) for b in body) and sorted(int(b[4]) for b in body) == [i for i, t in enumerate(tbl) if t[1]] and len(set(b[4] for b in body)) == len(body))
    nt = [l for l in ROWS if not l.startswith('# table_id')]
    check('rows: the table_id trailer of the entries file is line 2 of the rows file, a file without one has none',
          dispgen.table_id(ROWS) == '00' and dispgen.table_id(nt) is None and dispgen.rows_text(syms, dispgen.table(nt)[1], 'fnvl').split('\n')[1].startswith('# hash'))
    eh = dispgen.table([])[1]
    check('rows: an empty list has the header and no row', dispgen.rows_text({}, eh, 'fnvl') == '# fnvnx-disp-rows/1\n# hash fnvl_disp_table slots=%d empty=0\n# table\tindex\taddr\tsymbol\tslot\n' % len(eh['slots']))
    print('dispgentest: %d failed' % bad); sys.exit(1 if bad else 0)


if __name__ == '__main__': main()
