#!/usr/bin/env python3
"""vtbind.py - the native bodies of the exe functions in the short and null vtable slots of S2's all-tables gate
(reports/vtgate/<link>.tsv, classlayout.py --report), for S2's VTFILL. VTFILL owns the table emission; this file
only supplies the data: exe address -> native symbol.

    python3 -I tools/link/vtbind.py make --vtgate <link>.tsv --addrmap <link>/out/addrmap.tsv --exe FalloutNV.exe \\
        --out bind.tsv --needs needs.tsv
    python3 -I tools/link/vtbind.py check bind.tsv --objects <dir of .o> [--nm aarch64-linux-gnu-nm]

make: for every short row (the first native slot past a native table's end; one row per table) the exe functions
from that slot, minus one for D26's destructor pair (exevft.exe_slot_index), to the exe table's end (exevft.scan's
slot count; a word without a base relocation is not a slot, which drops string bytes past a table's end); for
every null row the exe slot and the one before it. Each address is then
  fn       the address map's native (kind F, G for a bound deleting destructor, C's __fnvnx_cthis_<addr>)
  adj      an MSVC adjustor thunk (`sub ecx, n; jmp T`, exevft.thunk) whose target T has a native: VTFILL emits
           fnvlink's emit_adj_stub(ADJ_SYM % address, native of T, n)
  needs    none of these (a deleting destructor with its body inlined, a function no unit defines): a recon (REC1)
bind.tsv (fnvnx-vtbind/1): exe_address, kind, symbol (the slot's symbol), target (adj: T's native), n, tables (how
many short/null tables name the address). needs.tsv: exe_address, shape (deldtor / function), tables. Both are
exe-derived with the decomp's names, so they go to fnv-nx-build (recon/vtbind), never here.

check: every symbol bind.tsv names (fn symbols, adj targets) is defined in one of the objects (nm --defined-only);
adj symbols themselves are VTFILL's to emit. Exit 0 when all are defined, 1 with the missing ones listed.
"""
import argparse
import collections
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ADJ_SYM = '__fnvnx_vadj_%08x'   # fnvlink.ADJ_SYM
TEXT = (0x00401000, 0x00fdf000)


def load_vtgate(path):
    """[(exe_vftable, slot, kind)] of the short and null rows"""
    out = []
    for line in open(path):
        if line.startswith('#'):
            continue
        f = line.rstrip('\n').split('\t')
        if len(f) >= 8 and f[7] in ('short', 'null'):
            out.append((int(f[2], 16), int(f[3]), f[7]))
    return out


def load_natives(path):
    """{exe address: native symbol} of the address map's functions (F), bound deleting destructors (G) and
    constructor-shaped natives (C: the decomp's flag-taking "C1" that is the exe's deleting destructor; the slot takes
    the link's own __fnvnx_cthis_<addr>, the address symbol, never the C1 name)"""
    out = {}
    for line in open(path):
        if line.startswith('#'):
            continue
        f = line.rstrip('\n').split('\t')
        if len(f) > 3 and f[1] in ('F', 'G'):
            out.setdefault(int(f[0], 16), f[3] or f[2])
        elif len(f) > 2 and f[1] == 'C':
            out.setdefault(int(f[0], 16), f[2])
    return out


def wanted(rows, slots_of, is_slot=None):
    """{exe address: tables naming it} for the short/null rows; slots_of(vftable) -> the exe table's slot words;
    is_slot(va) -> whether the word at va is a pointer (it has a base relocation: string bytes past a table's end
    that happen to look like .text addresses do not)"""
    cnt = collections.Counter()
    for vt, j, kind in rows:
        slots = slots_of(vt) or []
        idx = range(max(j - 1, 0), len(slots)) if kind == 'short' else (j - 1, j)
        seen = set()
        for i in idx:
            if 0 <= i < len(slots) and TEXT[0] <= slots[i] < TEXT[1] and slots[i] not in seen and \
                    (is_slot is None or is_slot(vt + 4 * i)):
                seen.add(slots[i])
                cnt[slots[i]] += 1
    return cnt


def classify(addrs, natives, thunk, deldtor):
    """(bind rows, needs rows) for {address: tables}; thunk(a) -> (n, target) or None (through chained thunks),
    deldtor(a) -> bool"""
    bind, needs = [], []
    for a in sorted(addrs):
        if a in natives:
            bind.append(('%08x' % a, 'fn', natives[a], '', '0', str(addrs[a])))
            continue
        th = thunk(a)
        if th and th[1] in natives and th[0]:
            bind.append(('%08x' % a, 'adj', ADJ_SYM % a, natives[th[1]], str(th[0]), str(addrs[a])))
            continue
        needs.append(('%08x' % a, 'deldtor' if deldtor(a) else 'function', str(addrs[a])))
    return bind, needs


def chained_thunk(exevft, exe):
    def f(a):
        n, t = 0, a
        for _ in range(3):
            th = exevft.thunk(exe, t)
            if th is None:
                break
            n, t = n + th[0], th[1]
        return (n, t) if t != a else None
    return f


def cmd_make(a):
    import exevft
    exev = exevft.scan(a.exe)
    rows = load_vtgate(a.vtgate)
    natives = load_natives(a.addrmap)
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), '..', 'loader', 'tools'))
    sys.path.insert(0, os.path.join(HERE, '..', '..', 'loader', 'tools'))
    import fnvnx_pe
    img = fnvnx_pe.Image(fnvnx_pe.PE(open(a.exe, 'rb').read()))
    img.load_relocs()
    rel = set(img.relocs)
    addrs = wanted(rows, lambda vt: exev[vt].slots if vt in exev else None, lambda va: va in rel)
    bind, needs = classify(addrs, natives, chained_thunk(exevft, a.exe), lambda x: exevft.deleting_dtor(a.exe, x))
    with open(a.out, 'w') as f:
        f.write('# fnvnx-vtbind/1\n# exe_address\tkind\tsymbol\ttarget\tn\ttables\n')
        f.writelines('\t'.join(r) + '\n' for r in bind)
    with open(a.needs, 'w') as f:
        f.write('# fnvnx-vtbind-needs/1\n# exe_address\tshape\ttables\n')
        f.writelines('\t'.join(r) + '\n' for r in needs)
    k = collections.Counter(r[1] for r in bind)
    print('vtbind: %d rows (%d short/null tables), %d addresses: %d fn, %d adj, %d need a recon (%d deldtor)'
          % (len(rows), len({r[0] for r in rows}), len(addrs), k['fn'], k['adj'], len(needs),
             sum(r[1] == 'deldtor' for r in needs)))
    return 0


def defined_symbols(objdir, nm):
    out = set()
    objs = [os.path.join(dp, n) for dp, _, ns in os.walk(objdir) for n in ns if n.endswith('.o')]
    for i in range(0, len(objs), 200):
        r = subprocess.run([nm, '--defined-only'] + objs[i:i + 200], capture_output=True, text=True)
        for line in r.stdout.splitlines():
            p = line.split()
            if len(p) >= 3:
                out.add(p[2])
    return out


def check(bind_path, have):
    """the symbols bind.tsv needs that `have` lacks: fn symbols, adj targets"""
    missing = []
    for line in open(bind_path):
        if line.startswith('#') or not line.strip():
            continue
        f = line.rstrip('\n').split('\t')
        need = f[2] if f[1] == 'fn' else f[3]
        if need not in have:
            missing.append((f[0], f[1], need))
    return missing


def cmd_check(a):
    miss = check(a.bind, defined_symbols(a.objects, a.nm))
    for m in miss:
        print('missing\t%s\t%s\t%s' % m)
    print('vtbind check: %d missing' % len(miss))
    return 1 if miss else 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)
    p = sp.add_parser('make')
    p.add_argument('--vtgate', required=True)
    p.add_argument('--addrmap', required=True)
    p.add_argument('--exe', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--needs', required=True)
    p.set_defaults(fn=cmd_make)
    p = sp.add_parser('check')
    p.add_argument('bind')
    p.add_argument('--objects', required=True)
    p.add_argument('--nm', default='aarch64-linux-gnu-nm')
    p.set_defaults(fn=cmd_check)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == '__main__':
    sys.exit(main())
