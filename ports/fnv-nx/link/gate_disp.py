#!/usr/bin/env python3
"""gate_disp - the dispatcher of the lifted guest holds exactly the code entries (part of the L1 verdict).

    gate_disp.py --elf guest.elf --universe universe.tsv --entries entries.tsv [--keys keys.tsv] [--missing missing.tsv]

The lifted code reaches every code entry of the exe through `fnvl_dispatch(a)` (lift/rt/rt.c, FNVL_PHF_REL), which looks the
address up in the table `fnvl_disp_table` (lift/tools/phf.py `table_asm`):
    i = ((a ^ fnvl_g[(a * K1) >> (32 - NBITS)]) * K2) >> (32 - TBITS)          (all arithmetic modulo 2^32)
    slot i = {u32 key, s32 offset}; the entry is found iff key == a and offset != 0; the body is `(char *)fnvl_disp_table + offset`.
A slot is OCCUPIED iff its offset is not 0 (the key of an empty slot is arbitrary).  {K1, K2, NBITS, TBITS} are the four u32 words of
`fnvl_phf_params`, stored with the table (disp.o), the one source rt.c reads.  Sizes come from the symbols' st_size: the table has
8 << TBITS bytes, `fnvl_g` (u16) 2 << NBITS bytes, `fnvl_phf_params` 16; a table whose size or parameters do not fit is red.

Inputs:  --universe  the code entries (universe.tsv: first column the address as hex, `#` lines skipped);
         --entries   `address<TAB>symbol[<TAB>kind]` (liftall's entries.tsv), kind `code` (default) or `import`: an import row is a
                     dispatcher key that is not a universe address (its symbol is `I_<key>` or a runtime symbol); the last
                     line may be `# table_id <hex>`;
         --keys      optional, the import key set of the bridge (keys.tsv: first column the key as hex, other columns ignored, `#` lines
                     skipped); each key must be named by an entries.tsv row (the symbol of its body is never taken from this file);
                     it may carry `# table_id <hex>`.  When both files carry a table_id the two must be equal (else the item
                     `table_id` is red);
         --missing   optional, `address` rows of universe addresses the lifter reports as without a body.  REPORTING only: it never
                     certifies anything; a universe address without an entry stays `no-entry` and red.  The detail line says how
                     the list and the entries agree.
Rule, for EVERY key k of the universe, of the import rows and of --keys (no sampling), certified when all hold:
  - a symbol is named for k by entries.tsv (two rows naming different symbols for k are a violation);
  - the ELF defines that symbol, at one address (a symbol defined at two addresses is a violation);
  - the LOOKUP: the slot i the hash computes for k is occupied, holds key k, and `table_address + stored_offset == symbol address`
    modulo 2^32 (`hash-miss` when slot i is not k's slot, `wrong-target` when it points elsewhere);
  - no other occupied slot holds k.
Every occupied slot whose key is neither in the universe nor an import key is a violation (`extra-key`).
That the parameters in the ELF are those the build chose is not this gate's business: the lookup is proven with them, for every key.

Gate file `disp` ($GATE_OUT/disp.json, gatefile.py; full list in $GATE_OUT/disp.ids): universe = the keys of universe + import keys +
the extra table keys (+ the item `table_id` when both files carry one), certified = those satisfying the rule, uncertified =
`<key as 8 hex digits>:<reason>`.  Counts only on stdout.  Exit status 1 when the gate is red.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'contract'))
import gatefile
import gateids

SHT_SYMTAB, PT_LOAD, SHN_UNDEF = 2, 1, 0
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
    """(table address, [(key, offset)], g [u16], (K1, K2, NBITS, TBITS)) or a string naming why the table cannot be read."""
    for n in ('fnvl_disp_table', 'fnvl_g', 'fnvl_phf_params'):
        if len(syms.get(n, ())) != 1:
            return '%s is not defined exactly once' % n
    if sizes['fnvl_phf_params'] != 16:
        return 'fnvl_phf_params has size %d, not 4 u32 words' % sizes['fnvl_phf_params']
    pp = read_at(loads, d, next(iter(syms['fnvl_phf_params'])), 16)
    if pp is None:
        return 'fnvl_phf_params is not inside one PT_LOAD segment of the file image'
    k1, k2, nb, tb = struct.unpack('<IIII', pp)
    if not (1 <= nb <= 31 and 1 <= tb <= 31):
        return 'the hash parameters NBITS %d, TBITS %d are outside 1..31' % (nb, tb)
    t, g = next(iter(syms['fnvl_disp_table'])), next(iter(syms['fnvl_g']))
    if sizes['fnvl_disp_table'] != 8 << tb:
        return 'the size of fnvl_disp_table is %d, not 8 << TBITS = %d' % (sizes['fnvl_disp_table'], 8 << tb)
    if sizes['fnvl_g'] != 2 << nb:
        return 'the size of fnvl_g is %d, not 2 << NBITS = %d' % (sizes['fnvl_g'], 2 << nb)
    raw, graw = read_at(loads, d, t, 8 << tb), read_at(loads, d, g, 2 << nb)
    if raw is None or graw is None:
        return 'the table or fnvl_g is not inside one PT_LOAD segment of the file image'
    return (t, [struct.unpack_from('<Ii', raw, o) for o in range(0, len(raw), 8)], struct.unpack('<%dH' % (1 << nb), graw), (k1, k2, nb, tb))


def slot_of(a, g, par):
    """The slot rt.c fnvl_dispatch computes for address a."""
    k1, k2, nb, tb = par
    return ((((a ^ g[((a * k1) & M32) >> (32 - nb)]) & M32) * k2) & M32) >> (32 - tb)


def tsv(path, kinds=True):
    """[(address, symbol or None, kind)] of a tab-separated file (kind: third column, `code` when absent), `#` and empty lines skipped."""
    out = []
    with open(path) as fh:
        for ln in fh:
            ln = ln.rstrip('\n')
            if ln and ln[0] != '#':
                p = ln.split('\t')
                kind = p[2] if kinds and len(p) > 2 else 'code'
                if kind not in ('code', 'import'):
                    sys.exit('gate_disp: %s: unknown kind %r (address %s)' % (path, kind, p[0]))
                out.append((int(p[0], 16), p[1] if len(p) > 1 else None, kind))
    return out


def table_id(path):
    """The id of a `# table_id <hex>` line of a file (the last one), or None."""
    out = None
    with open(path) as fh:
        for ln in fh:
            w = ln.split()
            if len(w) == 3 and w[0] == '#' and w[1] == 'table_id':
                out = w[2].lower()
    return out


def check(syms, table, universe, named):
    """{key: reason or None} over universe keys, named keys and the occupied table keys.
    syms {name: set of addresses}; table (address, [(key, offset)], g, params); universe set of keys (code and import);
    named {key: set of symbols}."""
    taddr, slots, g, par = table
    where = {}
    for i, (k, off) in enumerate(slots):
        if off:
            where.setdefault(k, []).append(i)
    res = {}
    for k in sorted(set(universe) | set(named) | set(where)):
        names = named.get(k, set())
        if k not in universe and k not in named:
            res[k] = 'extra-key'
        elif not names:
            res[k] = 'no-entry'
        elif len(names) > 1:
            res[k] = 'entry-conflict'
        else:
            n = next(iter(names))
            a = syms.get(n, ())
            hit = slot_of(k, g, par)
            if len(a) != 1:
                res[k] = 'symbol-undefined' if not a else 'symbol-ambiguous'
            elif k not in where:
                res[k] = 'no-slot'
            elif len(where[k]) > 1:
                res[k] = 'duplicate-key'
            elif where[k][0] != hit:
                res[k] = 'hash-miss'
            elif (taddr + slots[hit][1]) & M32 != next(iter(a)) & M32:
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
    ap.add_argument('--missing')
    a = ap.parse_args()
    universe = {k for k, _, _ in tsv(a.universe, False)}
    named, extra = {}, set()
    for k, sym, kind in tsv(a.entries):
        if sym is None:
            sys.exit('gate_disp: %s: a row without a symbol (address %08x)' % (a.entries, k))
        named.setdefault(k, set()).add(sym)
        if kind == 'import':
            extra.add(k)
    if a.keys:
        extra |= {k for k, _, _ in tsv(a.keys, False)}
    ids = [table_id(p) for p in [a.entries] + ([a.keys] if a.keys else [])]
    ids = [i for i in ids if i]
    id_bad = len(ids) == 2 and ids[0] != ids[1]
    keys = universe | extra
    if not os.path.exists(a.elf):
        table = 'there is no ELF %s (the link stage makes none when it is red)' % os.path.basename(a.elf)
    else:
        syms, sizes, loads, d = read_elf(a.elf)
        table = table_slots(syms, sizes, loads, d)
    if isinstance(table, str):
        n = len(keys)
        unc = ['%08x:no-table' % k for k in sorted(keys)]
        gatefile.write('disp', n, 0, unc, table)
        gateids.write_ids('disp', unc)
        print('dispatcher: %s' % table)
        sys.exit(1)
    # an entries.tsv row for an address that is neither in the universe nor an import key is not a key (it is an extra key only if the table holds it)
    named = {k: v for k, v in named.items() if k in keys}
    res = check(syms, table, keys, named)
    bad = sorted((k, r) for k, r in res.items() if r)
    counts = {}
    for _, r in bad:
        counts[r] = counts.get(r, 0) + 1
    unc = ['%08x:%s' % (k, r) for k, r in bad] + (['table_id'] if id_bad else [])
    detail = 'dispatcher: %d keys, %d certified, %d slots, %d occupied%s' % (
        len(res), len(res) - len(bad), len(table[1]), sum(1 for _, o in table[1] if o), ''.join(', %s %d' % (r, counts[r]) for r in sorted(counts)))
    if id_bad:
        detail += ', table_id differs (%s vs %s)' % (ids[0][:12], ids[1][:12])
    if a.missing:
        miss = {k for k, _, _ in tsv(a.missing, False)}
        noentry = {k for k in universe if k not in named}
        detail += '; missing list %d: %d without entry, %d with an entry; without entry and not listed %d' % (
            len(miss), len(miss & noentry), len(miss - noentry), len(noentry - miss))
    gatefile.write('disp', len(res) + len(ids) // 2, len(res) - len(bad) + len(ids) // 2 - (1 if id_bad else 0), unc, detail)
    gateids.write_ids('disp', unc)
    print(detail)
    sys.exit(1 if unc else 0)


if __name__ == '__main__':
    main()
