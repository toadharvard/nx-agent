#!/usr/bin/env python3
"""gate_disp - the typed dispatch tables of the lifted guest hold exactly the rows the generator declared (part of the L1 verdict).

    gate_disp.py --elf guest.elf --rows dispatch_rows.tsv [--universe universe.tsv]

The generator stage (L3a: phf.table_c, disp.cc, native_disp.cc) writes typed C++ arrays `fnvnx_entry_fn NAME[N]` and, as a declared output of
the same stage (in the store and in the keys), the rows file the arrays were generated from:
    table<TAB>index<TAB>guest_addr_hex<TAB>entry_symbol          (`#` lines skipped; the last `# table_id <hex>` line is the table id)
`entry_symbol` is `-` for a null slot.  The ELF is a 32-bit (ILP32) AArch64 image: slot `index` of table NAME is the u32 word at NAME + 4*index.

Rule, for EVERY table and EVERY row (no sampling).  A row is certified when all hold:
  - the table symbol is defined exactly once, its st_size is 4 * (number of rows of the table), its rows are the indexes 0..n-1 each once
    (`table-undefined`, `table-ambiguous`, `table-size`, `index-gap`, `duplicate-row`), and its bytes are inside one PT_LOAD segment (`table-unreadable`);
  - a non-null row: the entry symbol is defined exactly once (`symbol-undefined`, `symbol-ambiguous`), the slot word is that symbol's address
    (`wrong-target`), and the slot is covered by a load-time relocation when the image has any (SHT_RELR / SHT_RELA / SHT_REL; `unrelocated`):
    the slot is the R_AARCH64_ABS32 (P32) of the source, resolved by the link to the word and kept by the relocation;
  - a null row: the word is 0 (`null-slot-nonzero`) and no relocation covers it (`null-slot-relocated`);
  - one guest address names one symbol within a table (`entry-conflict`).
With --universe (the code entries: first column the address as hex, `#` lines skipped) every address needs a non-null row in some table (`no-entry`).
A rows file that is missing or does not parse, or a missing ELF, is red (`rows:no-rows`, `rows:format`, `elf:no-elf`) with the universe counted as the
rows it could not read (at least 1).  An extra slot cannot exist: the size of the table equals its rows, so a slot without a row is `table-size`.

Gate file `disp` ($GATE_OUT/disp.json, gatefile.py; full list in $GATE_OUT/disp.ids): items = every row, every table, every universe address;
uncertified = `<table>[<index>]:<reason>` / `<table>:<reason>` / `<addr 8 hex>:no-entry` / `rows:<reason>`.  Counts only on stdout.  Exit 1 when red.
That the lookup from a guest address to its slot index is the one the generator chose is the generator's contract; this gate proves the slots.
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


def read_rows(path):
    """([(table, index, guest address, symbol or None)], table_id or None); RowsError for a row that does not parse."""
    rows, tid = [], None
    with open(path) as fh:
        for n, ln in enumerate(fh, 1):
            ln = ln.rstrip('\n')
            w = ln.split()
            if len(w) == 3 and w[0] == '#' and w[1] == 'table_id':
                tid = w[2].lower()
            if not ln or ln[0] == '#':
                continue
            p = ln.split('\t')
            try:
                if len(p) != 4 or not p[0] or not p[3]:
                    raise ValueError
                rows.append((p[0], int(p[1], 10), int(p[2], 16), None if p[3] == '-' else p[3]))
            except ValueError:
                raise RowsError('%s:%d: not `table<TAB>index<TAB>guest_addr_hex<TAB>symbol`' % (path, n))
    return rows, tid


def read_universe(path):
    out = set()
    with open(path) as fh:
        for ln in fh:
            ln = ln.rstrip('\n')
            if ln and ln[0] != '#':
                out.add(int(ln.split('\t')[0], 16))
    return out


def check(elf, rows, universe=None):
    """-> (number of items, {item id: reason}); elf = read_elf's tuple, rows = read_rows' list, universe a set of addresses or None."""
    syms, sizes, loads, d, cov = elf
    tables = {}
    for r in rows:
        tables.setdefault(r[0], []).append(r)
    bad, items = {}, 0
    keyed = set()
    for t in sorted(tables):
        rs = sorted(tables[t], key=lambda r: (r[1], r[2], r[3] or ''))
        n = len(rs)
        items += 1 + n
        why = None
        if len(syms.get(t, ())) == 0:
            why = 'table-undefined'
        elif len(syms[t]) > 1:
            why = 'table-ambiguous'
        elif len({r[1] for r in rs}) != n:
            why = 'duplicate-row'
        elif sorted(r[1] for r in rs) != list(range(n)):
            why = 'index-gap'
        elif sizes[t] != 4 * n:
            why = 'table-size'
        else:
            taddr = next(iter(syms[t]))
            raw = read_at(loads, d, taddr, 4 * n)
            if raw is None:
                why = 'table-unreadable'
        if why:
            bad[t] = why
            for r in rs:
                bad['%s[%d]' % (t, r[1])] = 'no-table'
            continue
        words = struct.unpack('<%dI' % n, raw)
        named = {}
        for _, i, a, sym in rs:
            item = '%s[%d]' % (t, i)
            slot = (taddr + 4 * i) & M32
            covered = cov is not None and slot in cov
            if sym is None:
                if words[i] != 0:
                    bad[item] = 'null-slot-nonzero'
                elif covered:
                    bad[item] = 'null-slot-relocated'
                continue
            if named.setdefault(a, sym) != sym:
                bad[item] = 'entry-conflict'
                continue
            ad = syms.get(sym, ())
            if not ad:
                bad[item] = 'symbol-undefined'
            elif len(ad) > 1:
                bad[item] = 'symbol-ambiguous'
            elif words[i] != next(iter(ad)) & M32:
                bad[item] = 'wrong-target'
            elif cov is not None and not covered:
                bad[item] = 'unrelocated'
            else:
                keyed.add(a)
    for a in sorted(universe or ()):
        items += 1
        if a not in keyed:
            bad['%08x' % a] = 'no-entry'
    return items, bad


def finish(items, bad, detail):
    unc = ['%s:%s' % (k, v) for k, v in sorted(bad.items())]
    gatefile.write('disp', items, items - len(unc), unc, detail)
    gateids.write_ids('disp', unc)
    print(detail)
    sys.exit(1 if unc else 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--elf', required=True)
    ap.add_argument('--rows', required=True)
    ap.add_argument('--universe')
    a = ap.parse_args()
    universe = read_universe(a.universe) if a.universe else None
    nu = len(universe or ())
    if not os.path.exists(a.rows):
        finish(max(1, nu), {'rows': 'no-rows'}, 'dispatch: there is no rows file %s' % os.path.basename(a.rows))
    try:
        rows, tid = read_rows(a.rows)
    except RowsError as e:
        finish(max(1, nu), {'rows': 'format'}, 'dispatch: %s' % e)
    if not os.path.exists(a.elf):
        finish(max(1, nu + len(rows)), {'elf': 'no-elf'}, 'dispatch: there is no ELF %s (the link stage makes none when it is red)' % os.path.basename(a.elf))
    items, bad = check(read_elf(a.elf), rows, universe)
    counts = {}
    for r in bad.values():
        counts[r] = counts.get(r, 0) + 1
    detail = 'dispatch: %d tables, %d rows, %d items, %d certified%s%s' % (
        len({r[0] for r in rows}), len(rows), items, items - len(bad), ''.join(', %s %d' % (r, counts[r]) for r in sorted(counts)),
        ', table_id %s' % tid[:12] if tid else '')
    finish(items, bad, detail)


if __name__ == '__main__':
    main()
