#!/usr/bin/env python3
"""gate_disp - the dispatcher of the lifted guest holds exactly the code entries (part of the L1 verdict).

    gate_disp.py --elf guest.elf --universe universe.tsv --entries entries.tsv [--keys keys.tsv]

The lifted code reaches every code entry of the exe through `fnvl_dispatch(a)` (lift/rt/rt.c, FNVL_PHF_REL), which looks the
address up in the table `fnvl_disp_table` (lift/bench/phf.py `table_asm`).  The table has 8-byte slots `{u32 key, s32 offset}`;
slot `i` holds key `a` and `offset = body - fnvl_disp_table`, an empty slot has key 0xffffffff.  The dispatcher returns
`(char *)fnvl_disp_table + offset`, a 32-bit sum in the ILP32 guest.  The table ends where `fnvl_g` (the displacement array
that follows it) begins, so its size is `fnvl_g - fnvl_disp_table` (the symbol's own size when the assembler gave it one).

Inputs:  --universe  the code entries (universe.tsv: first column the address as hex, `#` lines skipped);
         --entries   `address<TAB>symbol`, the symbol of the body of each entry (liftall's entries.tsv);
         --keys      optional, `key<TAB>symbol`: dispatcher keys that are not universe addresses (import keys).
Rule, for EVERY key k of the universe and of --keys (no sampling), certified when all hold:
  - a symbol is named for k (entries.tsv or keys.tsv; two files naming different symbols for k is a violation);
  - the ELF defines that symbol, at one address (a symbol defined at two addresses is a violation);
  - the table holds exactly one slot with key k, and `table_address + stored_offset == symbol address` modulo 2^32.
Every non-empty table key that is neither in the universe nor in --keys is a violation (an extra key).  The key 0xffffffff
is the empty-slot mark and cannot be a key.
The rule does not recompute the hash: that rt.c's compiled constants (FNVL_K1/K2/NBITS/TBITS) are those of the table is the
business of the build that produced both, not of this gate.

Gate file `disp` ($GATE_OUT/disp.json, gatefile.py): universe = the keys of universe + --keys + the extra table keys,
certified = those satisfying the rule, uncertified = the first 50 sorted as `<key as 8 hex digits>:<reason>`.
Counts only on stdout.  Exit status 1 when the gate is red.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'contract'))
import gatefile

EMPTY, SHT_SYMTAB, PT_LOAD, SHN_UNDEF = 0xffffffff, 2, 1, 0
M32 = 0xffffffff


def read_elf(path):
    """(symbols {name: set of addresses}, symbol sizes {name: size}, loads [(vaddr, offset, filesz)], bytes) of an ELF32 LE file."""
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
    syms, sizes = {}, {}
    sh = [struct.unpack_from('<IIIIIIIIII', d, shoff + i * shentsize) for i in range(shnum)]
    for s in sh:
        if s[1] != SHT_SYMTAB:
            continue
        stroff = sh[s[6]][4]
        for o in range(s[4] + s[9], s[4] + s[5], s[9]):
            name, value, size, _, _, shndx = struct.unpack_from('<IIIBBH', d, o)
            if shndx == SHN_UNDEF:
                continue
            n = d[stroff + name:d.index(b'\0', stroff + name)].decode()
            if n:
                syms.setdefault(n, set()).add(value)
                sizes[n] = max(sizes.get(n, 0), size)
    return syms, sizes, loads, d


def read_at(loads, d, addr, n):
    """The n bytes at virtual address addr, from the file image of the PT_LOAD that holds them (None when none does)."""
    for va, off, filesz in loads:
        if va <= addr and addr + n <= va + filesz:
            return d[off + addr - va:off + addr - va + n]
    return None


def table_slots(syms, sizes, loads, d):
    """(table address, [(key, offset)]) or a string naming why the table cannot be read."""
    if len(syms.get('fnvl_disp_table', ())) != 1:
        return 'fnvl_disp_table is not defined exactly once'
    t = next(iter(syms['fnvl_disp_table']))
    size = sizes['fnvl_disp_table']
    if size == 0:
        if len(syms.get('fnvl_g', ())) != 1:
            return 'fnvl_disp_table has no size and fnvl_g is not defined exactly once'
        size = next(iter(syms['fnvl_g'])) - t
    if size <= 0 or size % 8:
        return 'the table size %d is not a positive multiple of the 8-byte slot' % size
    raw = read_at(loads, d, t, size)
    if raw is None:
        return 'the table is not inside one PT_LOAD segment of the file image'
    return t, [struct.unpack_from('<Ii', raw, o) for o in range(0, size, 8)]


def tsv(path):
    """[(first column, second column or None)] of a tab-separated file, `#` and empty lines skipped."""
    out = []
    with open(path) as fh:
        for ln in fh:
            ln = ln.rstrip('\n')
            if ln and ln[0] != '#':
                p = ln.split('\t')
                out.append((int(p[0], 16), p[1] if len(p) > 1 else None))
    return out


def check(syms, table, universe, named):
    """{key: reason or None} over universe keys, named keys and the table keys.
    syms {name: set of addresses}; table (address, [(key, offset)]); universe set of keys; named {key: set of symbols}."""
    taddr, slots = table
    where = {}
    for k, off in slots:
        if k != EMPTY:
            where.setdefault(k, []).append(off)
    res = {}
    for k in sorted(set(universe) | set(named) | set(where)):
        names = named.get(k, set())
        if k == EMPTY:
            res[k] = 'reserved-key'
        elif k not in universe and k not in named:
            res[k] = 'extra-key'
        elif not names:
            res[k] = 'no-entry'
        elif len(names) > 1:
            res[k] = 'entry-conflict'
        else:
            n = next(iter(names))
            a = syms.get(n, ())
            if len(a) != 1:
                res[k] = 'symbol-undefined' if not a else 'symbol-ambiguous'
            elif k not in where:
                res[k] = 'no-slot'
            elif len(where[k]) > 1:
                res[k] = 'duplicate-key'
            elif (taddr + where[k][0]) & M32 != next(iter(a)) & M32:
                res[k] = 'wrong-target'
            else:
                res[k] = None
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--elf', required=True)
    ap.add_argument('--universe', required=True)
    ap.add_argument('--entries', required=True)
    ap.add_argument('--keys')
    a = ap.parse_args()
    universe = {k for k, _ in tsv(a.universe)}
    named, extra = {}, set()
    for path in [a.entries] + ([a.keys] if a.keys else []):
        for k, sym in tsv(path):
            if sym is None:
                sys.exit('gate_disp: %s: a row without a symbol (address %08x)' % (path, k))
            named.setdefault(k, set()).add(sym)
            if path == a.keys:
                extra.add(k)
    keys = universe | extra
    syms, sizes, loads, d = read_elf(a.elf)
    table = table_slots(syms, sizes, loads, d)
    if isinstance(table, str):
        n = len(keys)
        gatefile.write('disp', n, 0, ['%08x:no-table' % k for k in sorted(keys)], table)
        print('dispatcher: %s' % table)
        sys.exit(1)
    # an entries.tsv row for an address that is neither in the universe nor in --keys is not a key (it is an extra key only if the table holds it)
    named = {k: v for k, v in named.items() if k in keys}
    res = check(syms, table, keys, named)
    bad = sorted((k, r) for k, r in res.items() if r)
    counts = {}
    for _, r in bad:
        counts[r] = counts.get(r, 0) + 1
    detail = 'dispatcher: %d keys, %d certified, %d slots%s' % (len(res), len(res) - len(bad), len(table[1]),
                                                                 ''.join(', %s %d' % (r, counts[r]) for r in sorted(counts)))
    gatefile.write('disp', len(res), len(res) - len(bad), ['%08x:%s' % (k, r) for k, r in bad], detail)
    print(detail)
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
