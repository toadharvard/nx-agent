#!/usr/bin/env python3
"""Synthetic check of link/gate_disp.py (typed dispatch tables, rows format fnvnx-disp-rows/1): ELF32 fixtures built with struct (no toolchain, no
private data) around a real hash-and-displace table found here by search.  Cases: rows equal (green); a slot differs; a row missing; a stray non-empty
slot; the rows file missing; an unrelocated entry; wrong entry word; undefined/ambiguous symbol; wrong hash parameters; sizes; two tables; the universe."""
import json
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, '..', 'gate_disp.py')
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.append(os.path.join(HERE, '..', '..', 'verify'))
import scratch
scratch.install()

fails = []
M = 0xffffffff
K1, K2, NB, TB = 0x9e3779b1, 0x85ebca6b, 2, 3        # 4 buckets, 8 slots
N = 1 << TB
ADDRS = [0x401000, 0x401020, 0x40a000, 0xff0000]
BODY = {a: 0x1000 + 0x40 * i for i, a in enumerate(ADDRS)}
SYM = {a: 'L_%08x' % a for a in ADDRS}


def check(name, got, want):
    if got != want:
        fails.append('%s: got %r, want %r' % (name, got, want))


def hslot(a, g, k1=K1, k2=K2, nb=NB, tb=TB):
    return ((((a ^ g[((a * k1) & M) >> (32 - nb)]) & M) * k2) & M) >> (32 - tb)


def phf(keys):
    g, used, buckets = [0] * (1 << NB), set(), {}
    for k in sorted(keys):
        buckets.setdefault(((k * K1) & M) >> (32 - NB), []).append(k)
    for b in sorted(buckets, key=lambda b: (-len(buckets[b]), b)):
        for v in range(1 << 16):
            g[b] = v
            sl = [hslot(k, g) for k in buckets[b]]
            if len(set(sl)) == len(sl) and not set(sl) & used:
                used |= set(sl)
                break
        else:
            raise SystemExit('no g for bucket %d' % b)
    return g


G = phf(ADDRS)


def layout(prefix='fnvl', addrs=ADDRS, g=G):
    """(table words, entries words, rows) for the addresses in the order of `addrs`: entry index = position, slot by the hash."""
    table = [0] * (2 * N)
    for s in range(N):
        a = 0xfeed0000 + s
        while hslot(a, g) == s:
            a += 1
        table[2 * s] = a
    rows = []
    for j, a in enumerate(addrs):
        s = hslot(a, g)
        table[2 * s], table[2 * s + 1] = a, j + 1
        rows.append(('%s_disp_entries' % prefix, j, a, SYM[a], s))
    return table, [BODY[a] for a in addrs], rows


def elf(prefix='fnvl', table=None, entries=None, g=G, params=(K1, K2, NB, TB), relr='auto', syms=None, sizes=None, nrows=None):
    """One PT_LOAD [0, 0x4000): table at 0x2000, entries at 0x2200, g at 0x2300, params at 0x2400; relr: vaddrs (SHT_RELR) or None (fixed image)."""
    T, E, GG, PP = 0x2000, 0x2200, 0x2300, 0x2400
    t, e, rows = layout(prefix)
    table = t if table is None else table
    entries = e if entries is None else entries
    img = bytearray(0x4000)
    struct.pack_into('<%dI' % len(table), img, T, *table)
    struct.pack_into('<%dI' % len(entries), img, E, *entries)
    struct.pack_into('<%dH' % len(g), img, GG, *g)
    struct.pack_into('<IIII', img, PP, *params)
    sz = sizes or dict(table=4 * len(table), entries=4 * max(len(entries), 1), g=2 * len(g), params=16)
    allsyms = [('%s_disp_table' % prefix, T, sz['table']), ('%s_disp_entries' % prefix, E, sz['entries']), ('%s_g' % prefix, GG, sz['g']),
               ('%s_phf_params' % prefix, PP, sz['params'])] + (list(BODY_SYMS) if syms is None else syms)
    if relr == 'auto':
        relr = [E + 4 * i for i in range(len(entries))]
    strtab, recs = b'\0', [struct.pack('<IIIBBH', 0, 0, 0, 0, 0, 0)]
    for n, v, size in [(x[0], x[1], x[2] if len(x) > 2 else 0) for x in allsyms]:
        recs.append(struct.pack('<IIIBBH', len(strtab), v, size, (1 << 4) | 1, 0, 1))
        strtab += n.encode() + b'\0'
    symtab = b''.join(recs)
    relrb = b''.join(struct.pack('<I', a) for a in sorted(relr)) if relr is not None else b''
    shstr = b'\0.text\0.symtab\0.strtab\0.shstrtab\0.relr.dyn\0'
    o_sym = 0x4000 + 52
    o_str = o_sym + len(symtab)
    o_shs = o_str + len(strtab)
    o_rel = o_shs + len(shstr)
    shoff = o_rel + len(relrb)

    def sh(name, typ, off, size, link=0, ent=0):
        return struct.pack('<IIIIIIIIII', name, typ, 0, 0, off, size, link, 0, 4, ent)
    shdrs = sh(0, 0, 0, 0) + sh(1, 1, 0, 0x4000) + sh(7, 2, o_sym, len(symtab), 3, 16) + sh(15, 3, o_str, len(strtab)) + sh(23, 3, o_shs, len(shstr))
    if relr is not None:
        shdrs += sh(33, 19, o_rel, len(relrb), 0, 4)
    img[52:84] = struct.pack('<IIIIIIII', 1, 0, 0, 0, 0x4000, 0x4000, 5, 0x1000)
    img[0:52] = b'\x7fELF' + bytes([1, 1, 1, 0]) + bytes(8) + struct.pack('<HHIIIIIHHHHHH', 3, 183, 1, 0, 52, shoff, 0, 52, 32, 1, 40, 6 if relr is not None else 5, 4)
    return bytes(img) + bytes(52) + symtab + strtab + shstr + relrb + shdrs


BODY_SYMS = [(SYM[a], BODY[a], 0x40) for a in ADDRS]
d = tempfile.mkdtemp()


def write(name, text):
    p = os.path.join(d, name)
    with open(p, 'w', newline='\n') as fh:
        fh.write(text)
    return p


def rows_text(rows, prefix='fnvl', slots=N, empty=None, tid=None, fmt=True):
    empty = slots - len(rows) if empty is None else empty
    return ('# fnvnx-disp-rows/1\n' if fmt else '') + ('# table_id %s\n' % tid if tid else '') + '# hash %s_disp_table slots=%d empty=%d\n' % (prefix, slots, empty) + \
        '# table\tindex\taddr\tsymbol\tslot\n' + ''.join('%s\t%d\t%08x\t%s\t%d\n' % r for r in rows)


def run(elfbytes=None, rows=None, text=None, universe=None, extra=(), elf_file=True):
    with open(os.path.join(d, 'g.elf'), 'wb') as fh:
        fh.write(elfbytes if elfbytes is not None else elf())
    if text is None:
        text = rows_text(layout()[2] if rows is None else rows)
    cmd = [sys.executable, '-I', GATE, '--elf', os.path.join(d, 'g.elf' if elf_file else 'none.elf'), '--rows', write('r.tsv', text)] + list(extra)
    if universe is not None:
        cmd += ['--universe', write('u.tsv', '# base\n' + ''.join('%08x\tdecomp\n' % a for a in universe))]
    go = os.path.join(d, 'gate')
    os.makedirs(go, exist_ok=True)
    r = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ, GATE_OUT=go))
    if not os.path.exists(os.path.join(go, 'disp.json')):
        sys.exit('gate_disp failed: ' + r.stderr[-600:])
    with open(os.path.join(go, 'disp.json')) as fh:
        j = json.load(fh)
    with open(os.path.join(go, 'disp.ids')) as fh:
        j['ids'] = fh.read().split('\n')[:-1]
    os.remove(os.path.join(go, 'disp.json'))
    return r.returncode, j


rc, g = run()
check('rows equal', (rc, g['status'], g['universe'], g['certified'], g['uncertified'], g['ids']), (0, 'green', 1 + 4 + 4, 9, [], []))
rc, g = run(elf(relr=None))
check('fixed image is green', (rc, g['status']), (0, 'green'))
check('the fixture hash is a real perfect hash', len({hslot(a, G) for a in ADDRS}), 4)
# a slot differs: the table slot of row 1 holds the index of row 2
t, e, rows = layout()
s1 = rows[1][4]
t[2 * s1 + 1] = 3
rc, g = run(elf(table=t))
check('slot differs', (rc, g['uncertified']), (1, ['fnvl_disp_entries[1]:wrong-slot']))
# a slot with a different key
t, e, rows = layout()
t[2 * s1] ^= 0x10
rc, g = run(elf(table=t))
check('slot key differs', (rc, g['uncertified']), (1, ['fnvl_disp_entries[1]:wrong-slot']))
# an entries word that points elsewhere
t, e, rows = layout()
e[2] = BODY[ADDRS[3]]
rc, g = run(elf(entries=e))
check('entry word wrong', (rc, g['uncertified']), (1, ['fnvl_disp_entries[2]:wrong-target']))
# a row is missing: the generator declared 3 rows, the table and the entries hold 4
rc, g = run(rows=layout()[2][:3])
check('row missing', (rc, g['uncertified'][:1]), (1, ['fnvl:table-size']))
# a row missing in the middle of the index space
rc, g = run(rows=[r if r[1] != 1 else r[:1] + (5,) + r[2:] for r in layout()[2]])
check('row missing (index gap)', (rc, g['uncertified'][:1]), (1, ['fnvl:index-gap']))
# a stray non-empty slot: an empty slot holds index 2 (and the key is not a row's)
t, e, rows = layout()
sn = next(s for s in range(N) if s not in {r[4] for r in rows})
t[2 * sn + 1] = 2
rc, g = run(elf(table=t))
check('stray slot', (rc, g['uncertified']), (1, ['fnvl_disp_table[%d]:stray-slot' % sn]))
# an empty slot whose key hashes to it
t, e, rows = layout()
a = 0xfeed0000
while hslot(a, G) != sn:
    a += 1
t[2 * sn] = a
rc, g = run(elf(table=t))
check('empty slot hits', (rc, g['uncertified']), (1, ['fnvl_disp_table[%d]:empty-slot-hits' % sn]))
# the rows file is missing / malformed, no ELF
rc = subprocess.run([sys.executable, '-I', GATE, '--elf', os.path.join(d, 'g.elf'), '--rows', os.path.join(d, 'absent.tsv')], capture_output=True,
                    env=dict(os.environ, GATE_OUT=os.path.join(d, 'gate'))).returncode
check('rows file missing', rc, 1)
with open(os.path.join(d, 'gate', 'disp.json')) as fh:
    check('rows file missing: item', json.load(fh)['uncertified'], ['rows:no-rows'])
rc, g = run(text=rows_text(layout()[2], fmt=False))
check('rows file without the format line', (rc, g['uncertified']), (1, ['rows:format']))
rc, g = run(elf_file=False)
check('no elf', (rc, g['uncertified']), (1, ['elf:no-elf']))
# relocation: an entry not covered
rc, g = run(elf(relr=[0x2200, 0x2204, 0x220c]))
check('unrelocated', (rc, g['uncertified']), (1, ['fnvl_disp_entries[2]:unrelocated']))
# symbols
rc, g = run(elf(syms=[(SYM[a], BODY[a], 0x40) for a in ADDRS[1:]]))
check('undefined symbol', (rc, g['uncertified']), (1, ['fnvl_disp_entries[0]:symbol-undefined']))
rc, g = run(elf(syms=BODY_SYMS + [(SYM[ADDRS[0]], 0x9000, 0x40)]))
check('ambiguous symbol', (rc, g['uncertified']), (1, ['fnvl_disp_entries[0]:symbol-ambiguous']))
# parameters that do not match the table: the lookup of the stored parameters misses
rc, g = run(elf(params=(K1, K2 ^ 0x10000, NB, TB)))
check('K2 differs', (rc, g['status'], g['certified'] < 9, all(u.endswith(':hash-miss') or u.endswith(':empty-slot-hits') for u in g['uncertified'])), (1, 'red', True, True))
# sizes and parameter ranges
for name, kw, why in (('table size', dict(sizes=dict(table=8 * N - 8, entries=16, g=2 << NB, params=16)), 'table-size'),
                      ('g size', dict(sizes=dict(table=8 * N, entries=16, g=(2 << NB) - 2, params=16)), 'table-size'),
                      ('params size', dict(sizes=dict(table=8 * N, entries=16, g=2 << NB, params=12)), 'params'),
                      ('TBITS differs from N', dict(params=(K1, K2, NB, TB + 1)), 'params'), ('NBITS 0', dict(params=(K1, K2, 0, TB)), 'params')):
    rc, g = run(elf(**kw))
    check('red: %s' % name, (rc, g['status'], g['uncertified'][:1]), (1, 'red', ['fnvl:' + why]))
rc, g = run(text=rows_text(layout()[2], empty=3))
check('empty count', (rc, g['uncertified'][:1]), (1, ['fnvl:slot-count']))
# an empty table: one element 0 that is not a row, no relocation covers it
rc, g = run(elf(entries=[0], table=layout('fnvl', [])[0], relr=[]), rows=[])
check('empty table is green', (rc, g['status']), (0, 'green'))
rc, g = run(elf(entries=[0x1000], table=layout('fnvl', [])[0], relr=[]), rows=[])
check('empty table with a nonzero element', (rc, g['uncertified']), (1, ['fnvl_disp_entries[0]:empty-element']))
# universe
rc, g = run(universe=ADDRS)
check('universe covered', (rc, g['status'], g['universe']), (0, 'green', 13))
rc, g = run(universe=ADDRS + [0x123456])
check('universe address without a row', (rc, g['uncertified']), (1, ['00123456:no-entry']))
# two tables, one rows file each (the native prefix)
nt, ne, nrows = layout('fnvnx_native', ADDRS[:2], phf(ADDRS[:2]))
with open(os.path.join(d, 'g.elf'), 'wb') as fh:
    fh.write(elf())
r1 = write('r1.tsv', rows_text(layout()[2]))
r2 = write('r2.tsv', rows_text(nrows, prefix='fnvnx_native'))
rc = subprocess.run([sys.executable, '-I', GATE, '--elf', os.path.join(d, 'g.elf'), '--rows', r1, '--rows', r2], capture_output=True,
                    env=dict(os.environ, GATE_OUT=os.path.join(d, 'gate'))).returncode
with open(os.path.join(d, 'gate', 'disp.json')) as fh:
    gj = json.load(fh)
check('second table that the ELF does not hold', (rc, gj['uncertified'][:1]), (1, ['fnvnx_native:symbol-undefined']))

if fails:
    print('\n'.join(fails))
    sys.exit(1)
print('ok')
