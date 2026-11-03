#!/usr/bin/env python3
"""mkxctab.py - the guest's dynamic initializer table (stream S10, docs/STARTUP.md).

Collects one native per original initializer, keyed by its original address, and writes xctab.c for the
guest link (startup/guest/fnvnx_xc.h: fnvnx_xc_table, sorted by address):

  * plain initializer functions the decomp defines inside the original initializer group (.text$yc), from
    S2's address map: kind F entries in [group_lo, group_hi), native = their link symbol __fnv_<addr>;
  * the generated ??__E objects, one extern "C" __fnvnx_dyninit_<addr> each (dyninit_rewrite.py), read from
    the symbol tables of the compiled objects (ELF32 or ELF64).

    mkxctab.py --out xctab.c [--tsv xctab.tsv] [--addrmap out/addrmap.tsv] [--objects objects.tsv | OBJ ...]
               [--units build/units.json] [--group 0x00f34eb0:0x00fc7a70]

With --units it also reports coverage against the decomp: initializers in the group without a native (unit
not compiled, rewrite not applied), byte ranges of the group no unit defines (undecompiled initializers),
and objects of rewritten units that still carry .init_array (GCC would construct their objects itself).
Output is derived from the decomp: local build output, never committed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys

# The initializer group of FalloutNV.exe 1.4.0.525: .text$yc (??__E and the generated InitializeRuntime*
# families), followed by .text$yd (??__F) at 0x00fc7a70. See docs/STARTUP.md "Why address order".
GROUP_LO, GROUP_HI = 0x00F34EB0, 0x00FC7A70
DYNINIT = re.compile(r'^__fnvnx_dyninit_([0-9a-f]{8})$')


# ---- minimal ELF reader (relocatable objects; 32- and 64-bit little endian) ------------------------------

def elf_sections_and_symbols(path: str):
    with open(path, 'rb') as f:
        data = f.read()
    if data[:4] != b'\x7fELF' or data[5] != 1:
        raise ValueError('%s: not a little-endian ELF file' % path)
    is64 = data[4] == 2
    if is64:
        shoff, = struct.unpack_from('<Q', data, 0x28)
        shentsize, shnum, shstrndx = struct.unpack_from('<HHH', data, 0x3A)
    else:
        shoff, = struct.unpack_from('<I', data, 0x20)
        shentsize, shnum, shstrndx = struct.unpack_from('<HHH', data, 0x2E)
    secs = []
    for i in range(shnum):
        o = shoff + i * shentsize
        if is64:
            name, typ, _flags, _addr, off, size, link, _info, _align, entsize = struct.unpack_from('<IIQQQQIIQQ', data, o)
        else:
            name, typ, _flags, _addr, off, size, link, _info, _align, entsize = struct.unpack_from('<IIIIIIIIII', data, o)
        secs.append(dict(name_off=name, type=typ, off=off, size=size, link=link, entsize=entsize))

    def cstr(off):
        end = data.index(b'\0', off)
        return data[off:end].decode('utf-8', 'replace')

    shstr = secs[shstrndx]['off'] if shstrndx < len(secs) else 0
    for s in secs:
        s['name'] = cstr(shstr + s['name_off']) if shstr else ''
    syms = []
    for s in secs:
        if s['type'] != 2:  # SHT_SYMTAB
            continue
        strtab = secs[s['link']]['off']
        ent = s['entsize'] or (24 if is64 else 16)
        for k in range(s['size'] // ent):
            o = s['off'] + k * ent
            if is64:
                name, info, _other, shndx, value, size = struct.unpack_from('<IBBHQQ', data, o)
            else:
                name, value, size, info, _other, shndx = struct.unpack_from('<IIIBBH', data, o)
            syms.append(dict(name=cstr(strtab + name), bind=info >> 4, type=info & 15, shndx=shndx))
    return [s['name'] for s in secs], syms


def defined_globals(syms):
    return [s['name'] for s in syms if s['shndx'] != 0 and s['bind'] in (1, 2)]


# ---- inputs ---------------------------------------------------------------------------------------------

def read_addrmap(path: str, lo: int, hi: int):
    """S2's addrmap.tsv (docs/LINK.md): kind F entries inside the group -> {addr: link_symbol}."""
    out = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue
            col = line.rstrip('\n').split('\t')
            if len(col) < 3:
                continue
            a = int(col[0], 16)
            if col[1] == 'F' and lo <= a < hi and col[2]:
                out[a] = col[2]
    return out


def read_objects(args_objects, objects_tsv):
    objs = list(args_objects or [])
    if objects_tsv:
        with open(objects_tsv, encoding='utf-8') as f:
            for line in f:
                col = line.rstrip('\n').split('\t')
                if len(col) >= 3 and col[2] == 'ok':
                    objs.append(col[1])
                elif len(col) == 1 and col[0]:
                    objs.append(col[0])
    return objs


def scan_objects(objs):
    """-> ({addr: (symbol, object)}, [objects with .init_array that define dyninit symbols], errors)."""
    found, still_init, errors = {}, [], []
    for o in objs:
        try:
            secnames, syms = elf_sections_and_symbols(o)
        except (OSError, ValueError, struct.error) as e:
            errors.append('%s: %s' % (o, e))
            continue
        mine = 0
        for name in defined_globals(syms):
            m = DYNINIT.match(name)
            if not m:
                continue
            a = int(m.group(1), 16)
            if a in found and found[a][1] != o:
                errors.append('0x%08x defined by %s and %s' % (a, found[a][1], o))
                continue
            found[a] = (name, o)
            mine += 1
        if mine and any(n == '.init_array' or n.startswith('.init_array.') for n in secnames):
            still_init.append(o)
    return found, still_init, errors


def units_coverage(units_path: str, lo: int, hi: int):
    with open(units_path, encoding='utf-8') as f:
        units = json.load(f)
    fns = {}
    for u in units['units']:
        for fn in u['functions']:
            a = int(fn['address'], 16)
            fns.setdefault(a, (fn['symbol'], u['source'], fn.get('size') or 0))
    group = sorted(a for a in fns if lo <= a < hi)
    holes, prev_end = [], lo
    for a in group:
        if a - prev_end >= 16:
            holes.append((prev_end, a - prev_end))
        prev_end = max(prev_end, a + fns[a][2])
    if hi - prev_end >= 16:
        holes.append((prev_end, hi - prev_end))
    outside_e = sorted(a for a, v in fns.items() if v[0].startswith('??__E') and not lo <= a < hi)
    odd = [a for a in group if not (fns[a][0].startswith('??__E') or fns[a][0].endswith('@@YAXXZ'))]
    return fns, group, holes, outside_e, odd


# ---- output ---------------------------------------------------------------------------------------------

def write_c(path: str, entries, lo: int, hi: int):
    with open(path, 'w', encoding='utf-8') as f:
        f.write('/* xctab.c - generated by startup/tools/mkxctab.py: the dynamic initializers in original address\n'
                ' * order (docs/STARTUP.md). Derived from the decomp: local build output, never committed. */\n')
        f.write('#include "fnvnx_xc.h"\n\n')
        for a, sym, _src in entries:
            f.write('extern void %s(void);\n' % sym)
        f.write('\nconst struct fnvnx_xc_entry fnvnx_xc_table[] = {\n')
        for a, sym, _src in entries:
            f.write('    { 0x%08xu, %s },\n' % (a, sym))
        if not entries:
            f.write('    { 0u, 0 },\n')
        f.write('};\n')
        f.write('const uint32_t fnvnx_xc_table_count = %du;\n' % len(entries))
        f.write('const uint32_t fnvnx_xc_group_lo = 0x%08xu;\n' % lo)
        f.write('const uint32_t fnvnx_xc_group_hi = 0x%08xu;\n' % hi)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--out', required=True, help='xctab.c to write')
    ap.add_argument('--tsv', help='also write orig, kind, symbol, source per entry')
    ap.add_argument('--addrmap', help="S2's addrmap.tsv")
    ap.add_argument('--objects', help='objects.tsv (compile.sh format: source, object, ok) or one path per line')
    ap.add_argument('--units', help='build/units.json, for the coverage report')
    ap.add_argument('--group', help='LO:HI original initializer group (default 0x%08x:0x%08x)' % (GROUP_LO, GROUP_HI))
    ap.add_argument('--strict', action='store_true', help='fail on duplicate natives or unreadable objects')
    ap.add_argument('obj', nargs='*', help='more objects to scan for __fnvnx_dyninit_<addr>')
    a = ap.parse_args(argv)
    lo, hi = GROUP_LO, GROUP_HI
    if a.group:
        l, h = a.group.split(':')
        lo, hi = int(l, 0), int(h, 0)

    plain = read_addrmap(a.addrmap, lo, hi) if a.addrmap else {}
    dyn, still_init, errors = scan_objects(read_objects(a.obj, a.objects))
    table = {}
    for addr, sym in plain.items():
        table[addr] = (sym, 'addrmap')
    both = 0
    for addr, (sym, obj) in dyn.items():
        if addr in table:
            both += 1
        table[addr] = (sym, os.path.basename(obj))
    entries = sorted((addr, sym, src) for addr, (sym, src) in table.items())
    write_c(a.out, entries, lo, hi)
    if a.tsv:
        with open(a.tsv, 'w', encoding='utf-8') as f:
            f.write('# orig\tkind\tsymbol\tsource\n')
            for addr, sym, src in entries:
                kind = 'dyninit' if DYNINIT.match(sym) else 'plain'
                f.write('0x%08x\t%s\t%s\t%s\n' % (addr, kind, sym, src))

    in_group = sum(1 for e in entries if lo <= e[0] < hi)
    print('mkxctab: %d natives (%d plain from the address map, %d dyninit from %d objects; %d in both), '
          '%d inside the group 0x%08x..0x%08x' % (len(entries), len(plain), len(dyn),
                                                  len(set(o for _, o in dyn.values())), both, in_group, lo, hi))
    for o in still_init:
        print('mkxctab: warning: %s defines dyninit functions but still has .init_array' % o)
    for e in errors[:20]:
        print('mkxctab: error: %s' % e)
    if a.units:
        fns, group, holes, outside_e, odd = units_coverage(a.units, lo, hi)
        missing = [x for x in group if x not in table]
        by_unit = {}
        for x in missing:
            by_unit[fns[x][1]] = by_unit.get(fns[x][1], 0) + 1
        print('mkxctab: decomp defines %d functions in the group; %d have a native, %d do not' % (
            len(group), len(group) - len(missing), len(missing)))
        for u, n in sorted(by_unit.items(), key=lambda kv: -kv[1])[:12]:
            print('   %6d without native  %s' % (n, u))
        print('mkxctab: %d byte ranges of the group defined by no unit (%d bytes): undecompiled initializers '
              '(IMAGE mode reports them as missing)' % (len(holes), sum(h[1] for h in holes)))
        if outside_e:
            print('mkxctab: ??__E outside the group (not in the table): %s' % ' '.join('0x%08x' % x for x in outside_e))
        if odd:
            print('mkxctab: %d group functions are neither ??__E nor void(void): e.g. %s' % (
                len(odd), ', '.join('0x%08x %s' % (x, fns[x][0]) for x in odd[:5])))
    if a.strict and (errors or still_init):
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
