#!/usr/bin/env python3
"""Unit test of tools/dispgen.py, the one dispatcher generator: disp.S is a pure function of the entry list, and rt/rt.c's lookup over the generated table
finds every key (its symbol) and sends every other address to fnvl_nobody.
    dispgentest.py
The lookup is rt.c fnvl_dispatch() re-read from the generated assembly text: i = ((a ^ g[(a*k1) >> (32-nbits)]) * k2) >> (32-tbits); a slot hits iff its key == a and its
offset != 0. Prints one line per check; exit status 1 on any failure."""
import os, random, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(LIFT, 'tools'))
import dispgen, phf
M = 0xffffffff
ROWS = ['%08x\tL_%08x\tcode\n' % (a, a) for a in (0x401000, 0x401005, 0x40100a, 0x4010ff, 0x402000, 0x5fffff0, 0x7fffffff, 0xdeadbeef)] + \
       ['000fffd0\tfnvl_msvcrt_eh_nested_catch\truntime\n', '00100000\tI_00100000\timport\n', '# table_id 00\n', '\n']


def read(asm):
    """-> (slots [(key, symbol or None)], g, (k1, k2, nbits, tbits)) parsed back from the generated assembly."""
    slots, g, params, sec = [], [], None, None
    for ln in asm.splitlines():
        t = ln.strip()
        if t.startswith('fnvl_disp_table:'): sec = 'T'
        elif t.startswith('fnvl_g:'): sec = 'G'
        elif t.startswith('fnvl_phf_params:'): sec = 'P'
        elif t.startswith('.long') and sec == 'T':
            key, rest = [x.strip() for x in t[5:].split(',', 1)]
            slots.append((int(key, 16), None if rest == '0' else rest.split(' - ')[0]))
        elif t.startswith('.short') and sec == 'G': g.append(int(t[6:]))
        elif t.startswith('.long') and sec == 'P': params = tuple(int(x) for x in t[5:].split(','))
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
    check('deterministic: same rows, same bytes', dispgen.table(ROWS)[0] == asm)
    rr = random.Random(7); sh = ROWS[:]; rr.shuffle(sh)
    check('row order does not change the bytes', dispgen.table(sh)[0] == asm)
    check('tuple rows and file lines give the same bytes', dispgen.table([tuple(l.rstrip('\n').split('\t')) for l in ROWS if l.strip() and l[0] != '#'])[0] == asm)
    with tempfile.TemporaryDirectory() as d:
        open(d + '/e.tsv', 'w').write(''.join(ROWS))
        r = subprocess.run([sys.executable, '-I', os.path.join(LIFT, 'tools', 'dispgen.py'), d + '/e.tsv', d + '/disp.S'], capture_output=True, text=True, env={})
        check('command line: exit 0, file equals table()', r.returncode == 0 and open(d + '/disp.S').read() == asm)
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
    check('prefix: the symbols and the section carry the prefix, none of the lifted names remain', all(x in nasm for x in ('fnvnx_native_disp_table:', 'fnvnx_native_g:', 'fnvnx_native_phf_params:', '.rodata.fnvnx_native_disp')) and 'fnvl_' not in nasm)
    easm, eh = dispgen.table([], 'fnvnx_native', True)
    check('empty list with allow_empty: 8 empty slots, every key misses', easm.count(', 0\n') == 8 and all(phf.lookup(eh, k) is None for k in (0, 1, 0x401000, 0xffffffff)))
    try: dispgen.table([], 'fnvnx_native'); ok = False
    except SystemExit: ok = True
    check('empty list without allow_empty exits', ok)
    for name, rows in (('a duplicate address exits', ROWS + ['401000\tX\n']), ('a row without a symbol exits', ['401000\n']), ('an empty list exits', ['# x\n']), ('a non-hex address exits', ['zz\tX\n'])):
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
    print('dispgentest: %d failed' % bad); sys.exit(1 if bad else 0)


if __name__ == '__main__': main()
