#!/usr/bin/env python3
"""gate_disp - the typed dispatch tables of the lifted guest hold exactly the rows the generator declared (part of the L1 verdict).

    gate_disp.py --elf guest.elf --rows disp_rows.tsv [--rows native_disp_rows.tsv ...] [--universe universe.tsv]

The generator (L3a: lift/tools/dispgen.py, phf.table_c) writes, per table and in the same run, a rows file `fnvnx-disp-rows/1` (a declared output of the
generator stage, in the store and in the keys):
    # fnvnx-disp-rows/1
    # table_id <hex>                                   (only when the entries file has one)
    # hash <P>_disp_table slots=<N> empty=<E>
    # table<TAB>index<TAB>addr<TAB>symbol<TAB>slot
    <P>_disp_entries<TAB>index<TAB>addr (hex)<TAB>symbol<TAB>slot       one row per element of the entries array, in array order
and the typed data of the prefix P (ILP32 image): `P_disp_table` u32[2N] pairs {key, index+1}; `P_disp_entries` fnvnx_entry_fn[max(count,1)];
`P_g` u16[2^NBITS]; `P_phf_params` u32[4] = {K1, K2, NBITS, TBITS}.  The lookup of rt.c / dispatch.c is
    i = ((a ^ g[(a * K1) >> (32 - NBITS)]) * K2) >> (32 - TBITS)     (arithmetic modulo 2^32)       found iff table[2i] == a: entries[table[2i+1] - 1].

Rule, for EVERY rows file, EVERY row and EVERY slot (no sampling).  A rows file is certified when all hold (else the item `P:<reason>`):
  - the header is `fnvnx-disp-rows/1` with a `# hash` line naming `P_disp_table`; the four symbols are defined exactly once, in one PT_LOAD
    (`symbol-undefined`, `symbol-ambiguous`, `table-unreadable`); `P_disp_table` has 8*N bytes, `P_phf_params` 16, `P_g` 2 << NBITS, N == 1 << TBITS,
    `P_disp_entries` 4*max(count,1) (`table-size`, `params`); the rows name `P_disp_entries` with the indexes 0..count-1 once, distinct slots below N,
    N - count == E (`rows-shape`, `duplicate-row`, `index-gap`, `slot-count`);
A row (item `P_disp_entries[index]`) is certified when:
  - its symbol is defined exactly once, and the word entries[index] is its address, covered by a load-time relocation when the image has any
    (SHT_RELR / RELA / REL: the R_AARCH64_ABS32 of the source; `wrong-target`, `unrelocated`, `symbol-undefined`, `symbol-ambiguous`); with count 0 the one
    element is 0 and no relocation covers it (`empty-element`);
  - table[2*slot] == addr and table[2*slot+1] == index+1 (`wrong-slot`), and the LOOKUP with the stored parameters hashes addr to `slot` (`hash-miss`);
A slot no row names (item `P_disp_table[slot]`) is certified when its index half is 0 and its key does not hash to it (`stray-slot`, `empty-slot-hits`).
With --universe (the code entries: first column the address as hex) every address needs a row in some rows file (`no-entry`).
A missing or malformed rows file, or a missing ELF, is red (`rows:no-rows`, `rows:format`, `elf:no-elf`).

Gate file `disp` ($GATE_OUT/disp.json, gatefile.py; full list in $GATE_OUT/disp.ids): items = per rows file 1 + rows + empty slots, plus the universe addresses;
uncertified = the ids above `:<reason>`.  Counts only on stdout.  Exit 1 when red.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'contract'))
import gatefile
import gateids

SHT_SYMTAB, SHT_RELA, SHT_REL, SHT_RELR, PT_LOAD, SHN_UNDEF = 2, 4, 9, 19, 1, 0
M32 = 0xffffffff


def read_elf(path):
    """(symbols {name: set of addresses}, sizes {name: size}, loads [(vaddr, offset, filesz)], bytes, relocated None or set of covered vaddrs) of an
    ELF32 LE file.  `relocated` is None when the image has no relocation section at all (a fixed-address image)."""
    with open(path, 'rb') as fh:
        d = fh.read()
    if d[:4] != b'\x7fELF' or d[4] != 1 or d[5] != 1:
        raise ValueError('%s: not ELF32 little-endian' % path)
    phoff, shoff = struct.unpack_from('<II', d, 28)
    phentsize, phnum, shentsize, shnum = struct.unpack_from('<HHHH', d, 42)
    loads = []
    for i in range(phnum):
        typ, off, va, _, filesz = struct.unpack_from('<IIIII', d, phoff + i * phentsize)
        if typ == PT_LOAD:
            loads.append((va, off, filesz))
    syms, sizes, cov, anyrel = {}, {}, set(), False
    sh = [struct.unpack_from('<IIIIIIIIII', d, shoff + i * shentsize) for i in range(shnum)]
    for s in sh:
        typ, addr, off, size, link, ent = s[1], s[3], s[4], s[5], s[6], s[9]
        if typ == SHT_SYMTAB:
            stroff = sh[link][4]
            for o in range(off + ent, off + size, ent):
                name, value, sz, _, _, shndx = struct.unpack_from('<IIIBBH', d, o)
                if shndx == SHN_UNDEF:
                    continue
                n = d[stroff + name:d.index(b'\0', stroff + name)].decode()
                if n:
                    syms.setdefault(n, set()).add(value)
                    sizes[n] = max(sizes.get(n, 0), sz)
        elif typ == SHT_RELA:
            anyrel = True
            for o in range(off, off + size, ent or 12):
                cov.add(struct.unpack_from('<I', d, o)[0])
        elif typ == SHT_REL:
            anyrel = True
            for o in range(off, off + size, ent or 8):
                cov.add(struct.unpack_from('<I', d, o)[0])
        elif typ == SHT_RELR:
            anyrel = True
            base = 0
            for o in range(off, off + size, 4):
                (e,) = struct.unpack_from('<I', d, o)
                if not e & 1:                       # an address entry: relocates the word at e, the next bitmap starts after it
                    cov.add(e)
                    base = e + 4
                else:                               # a bitmap entry: bit i (1..31) relocates the word at base + 4 * (i - 1)
                    for i in range(1, 32):
                        if e >> i & 1:
                            cov.add(base + 4 * (i - 1))
                    base += 4 * 31
    return syms, sizes, loads, d, (cov if anyrel else None)


def read_at(loads, d, addr, n):
    """The n bytes at virtual address addr, from the file image of the PT_LOAD that holds them (None when none does)."""
    for va, off, filesz in loads:
        if va <= addr and addr + n <= va + filesz:
            return d[off + addr - va:off + addr - va + n]
    return None


class RowsError(Exception):
    pass


FORMAT = 'fnvnx-disp-rows/1'


def read_rows(path):
    """(prefix, slots, empty, table_id or None, [(index, addr, symbol, slot)]); RowsError when the file does not parse."""
    tid, hdr, fmt, rows, prefix = None, None, False, [], None
    with open(path) as fh:
        for n, ln in enumerate(fh, 1):
            ln = ln.rstrip('\n')
            w = ln.split()
            if ln.startswith('# ') and w[1:2] == [FORMAT]:
                fmt = True
            elif len(w) == 3 and w[:2] == ['#', 'table_id']:
                tid = w[2].lower()
            elif len(w) == 5 and w[:2] == ['#', 'hash']:
                try:
                    kv = dict(x.split('=') for x in w[3:])
                    hdr = (w[2], int(kv['slots']), int(kv['empty']))
                except (ValueError, KeyError):
                    raise RowsError('%s:%d: bad hash line' % (path, n))
            elif ln and ln[0] != '#':
                p = ln.split('\t')
                try:
                    if len(p) != 5:
                        raise ValueError
                    rows.append((p[0], int(p[1], 10), int(p[2], 16), p[3], int(p[4], 10)))
                except ValueError:
                    raise RowsError('%s:%d: not `table<TAB>index<TAB>addr<TAB>symbol<TAB>slot`' % (path, n))
    if not fmt or hdr is None or not hdr[0].endswith('_disp_table'):
        raise RowsError('%s: no `# %s` line or no `# hash <P>_disp_table slots=N empty=E` line' % (path, FORMAT))
    return hdr[0][:-len('_disp_table')], hdr[1], hdr[2], tid, rows


def read_universe(path):
    out = set()
    with open(path) as fh:
        for ln in fh:
            ln = ln.rstrip('\n')
            if ln and ln[0] != '#':
                out.add(int(ln.split('\t')[0], 16))
    return out


def slot_of(a, g, par):
    """The slot rt.c / dispatch.c compute for address a."""
    k1, k2, nb, tb = par
    return ((((a ^ g[((a * k1) & M32) >> (32 - nb)]) & M32) * k2) & M32) >> (32 - tb)


def one_name(syms, n):
    return None if len(syms.get(n, ())) == 1 else ('symbol-undefined' if n not in syms else 'symbol-ambiguous')


def check_file(elf, rowsdoc):
    """-> (items, {item id: reason}, covered addresses) for one rows file."""
    syms, sizes, loads, d, cov = elf
    P, N, E, _, rows = rowsdoc
    T, EN, G, PP = P + '_disp_table', P + '_disp_entries', P + '_g', P + '_phf_params'
    n = len(rows)
    ids = ['%s[%d]' % (EN, r[1]) for r in rows]

    def whole(why):
        bad = {P: why}
        bad.update({i: 'no-table' for i in ids})
        return 1 + n, bad, set()
    for nm in (T, EN, G, PP):
        w = one_name(syms, nm)
        if w:
            return whole(w)
    if sizes[PP] != 16:
        return whole('params')
    raw = read_at(loads, d, next(iter(syms[PP])), 16)
    if raw is None:
        return whole('table-unreadable')
    k1, k2, nb, tb = par = struct.unpack('<IIII', raw)
    if not (1 <= nb <= 31 and 1 <= tb <= 31) or N != 1 << tb:
        return whole('params')
    if sizes[T] != 8 * N or sizes[G] != 2 << nb or sizes[EN] != 4 * max(n, 1):
        return whole('table-size')
    tr, er, gr = (read_at(loads, d, next(iter(syms[x])), sz) for x, sz in ((T, 8 * N), (EN, 4 * max(n, 1)), (G, 2 << nb)))
    if tr is None or er is None or gr is None:
        return whole('table-unreadable')
    if any(r[0] != EN for r in rows):
        return whole('rows-shape')
    if len({r[1] for r in rows}) != n:
        return whole('duplicate-row')
    if sorted(r[1] for r in rows) != list(range(n)):
        return whole('index-gap')
    if len({r[4] for r in rows}) != n or any(not 0 <= r[4] < N for r in rows) or N - n != E:
        return whole('slot-count')
    table = struct.unpack('<%dI' % (2 * N), tr)
    words = struct.unpack('<%dI' % max(n, 1), er)
    g = struct.unpack('<%dH' % (1 << nb), gr)
    ea = next(iter(syms[EN]))
    bad, named, keys = {}, {r[4] for r in rows}, set()
    for _, i, a, sym, slot in sorted(rows, key=lambda r: r[1]):
        item = '%s[%d]' % (EN, i)
        ad = syms.get(sym, ())
        covered = cov is not None and ((ea + 4 * i) & M32) in cov
        if not ad:
            bad[item] = 'symbol-undefined'
        elif len(ad) > 1:
            bad[item] = 'symbol-ambiguous'
        elif words[i] != next(iter(ad)) & M32:
            bad[item] = 'wrong-target'
        elif cov is not None and not covered:
            bad[item] = 'unrelocated'
        elif table[2 * slot] != a or table[2 * slot + 1] != i + 1:
            bad[item] = 'wrong-slot'
        elif slot_of(a, g, par) != slot:
            bad[item] = 'hash-miss'
        else:
            keys.add(a)
    if n == 0 and (words[0] != 0 or (cov is not None and ea & M32 in cov)):
        bad[EN + '[0]'] = 'empty-element'
    for s in range(N):
        if s in named:
            continue
        if table[2 * s + 1] != 0:
            bad['%s[%d]' % (T, s)] = 'stray-slot'
        elif slot_of(table[2 * s], g, par) == s:
            bad['%s[%d]' % (T, s)] = 'empty-slot-hits'
    return 1 + n + (N - n), bad, keys


def finish(items, bad, detail):
    unc = ['%s:%s' % (k, v) for k, v in sorted(bad.items())]
    gatefile.write('disp', items, items - len(unc), unc, detail)
    gateids.write_ids('disp', unc)
    print(detail)
    sys.exit(1 if unc else 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--elf', required=True)
    ap.add_argument('--rows', action='append', required=True)
    ap.add_argument('--universe')
    a = ap.parse_args()
    universe = read_universe(a.universe) if a.universe else None
    nu = len(universe or ())
    docs = []
    for p in a.rows:
        if not os.path.exists(p):
            finish(max(1, nu), {'rows': 'no-rows'}, 'dispatch: there is no rows file %s' % os.path.basename(p))
        try:
            docs.append(read_rows(p))
        except RowsError as e:
            finish(max(1, nu), {'rows': 'format'}, 'dispatch: %s' % e)
    if not os.path.exists(a.elf):
        finish(max(1, nu + sum(1 + len(x[4]) for x in docs)), {'elf': 'no-elf'}, 'dispatch: there is no ELF %s (the link stage makes none when it is red)' % os.path.basename(a.elf))
    elf = read_elf(a.elf)
    items, bad, keyed = 0, {}, set()
    for doc in docs:
        n, b, k = check_file(elf, doc)
        items += n
        bad.update(b)
        keyed |= k
    for u in sorted(universe or ()):
        items += 1
        if u not in keyed:
            bad['%08x' % u] = 'no-entry'
    counts = {}
    for r in bad.values():
        counts[r] = counts.get(r, 0) + 1
    detail = 'dispatch: %d tables, %d rows, %d items, %d certified%s%s' % (
        len(docs), sum(len(x[4]) for x in docs), items, items - len(bad), ''.join(', %s %d' % (r, counts[r]) for r in sorted(counts)),
        ''.join(', table_id %s' % x[3][:12] for x in docs if x[3]))
    finish(items, bad, detail)


if __name__ == '__main__':
    main()
