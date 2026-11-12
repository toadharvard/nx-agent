#!/usr/bin/env python3
"""argcensus - the value set of every stdcall argument of every call of the imports of chosen dlls, over the image set (L1 CONTRACT).

    argcensus.py --images-file images.tsv --bridge bridge.tsv --dll wsock32.dll [--dll ...] [--gpa gpa_names.tsv] [--loadlib loadlib.tsv] --out DIR

Rule (argflow, the same engine and may-write rule as gpa_names.py / gate_reach.py).  For every bridge row (dll D in --dll, name N, args
letters = the stdcall argument count) and every image of the set: the uses of the slots of D!N (argflow.imports_called: call [slot], a
thunk, a register-loaded call) and, per use and argument k (0 = pushed last), argflow.derive_arg:
  closed   every leaf is an immediate value: the argument is one of those values on every path (argflow walks all paths of the
           function backwards and expands own parameters to every caller, or reports a break);
  open     a leaf is a string / a call result / an import slot, or the derivation broke: the argument is of class `any`.
An import by ordinal is the bridge row of that ordinal. An escape of a slot (uses_of_slot_ref's open list) or an import of D by an ordinal no bridge row has makes the import's rows open for all its arguments: its
uses are not all known.  Aggregate per (D, N, k): closed iff every use is closed; values = the union (hex, sorted).  A closed aggregate
is the complete value set of the argument for the exe's own calls; any value outside it is excluded by a certificate (`excluded` rows
of the --gpa/--loadlib check below), provided no dynamic path reaches D at all (below).
Reach of D by dynamic names (--gpa gpa_names.tsv, --loadlib loadlib.tsv): `reach: none` iff no gpa row is open, no pair of gpa_names
names a module of D, and no loadlib site is open or names D; else `reach: possible` with the open items (the aggregate then covers the
static uses only; a dynamic use is outside it).
Outputs: argcensus.tsv (dll, name, argc, image, ref, call, k, status, values, other, breaks), argcensus.agg.tsv (dll, name, k, status,
values, uses), argcensus.reach.txt.  Exe-derived: container and fnv-nx-build only."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import argflow
import gpa_names
import iat


def bridge_rows(path, dlls):
    """[(dll, name, argc, ordinal text)] of the stdcall function rows of the bridge for the dlls, sorted."""
    rows, _ = iat.bridge_rows(path)
    return sorted((iat.norm_dll(r['dll']), r['name'], len(r['args']), r['ordinal']) for r in rows if iat.norm_dll(r['dll']) in dlls and r['conv'] == 'stdcall')


def uses(img, dll, name, ordinal=''):
    """([(ref, call)] of the uses of D!N in the image, [(va, why)] open items).  An import by ordinal is the bridge row of that ordinal."""
    slots = sorted(s for s, (d, n) in img.imports.items() if (n == name or (ordinal and n == '#' + ordinal)) and iat.norm_dll(d) == dll)
    out, open_ = [], []
    for ref in sorted(r for r in img.relocs if img.word(r) in slots):
        calls, o = argflow.uses_of_slot_ref(img, ref)
        out += [(ref, c) for c in sorted(set(calls))]
        open_ += o
    return out, sorted(set(open_))


def leaves(res):
    """-> (closed, [values], [other leaves text], [breaks text]) of a derive_arg result."""
    vals, other = [], []
    brk = sorted('%x %s' % b if isinstance(b, tuple) else str(b) for b in res.breaks)
    for kind, val, _ in res.sorted_leaves():
        if kind == 'imm':
            vals.append(val)
        else:
            other.append('%s %r' % (kind, val))
    return not brk and not other, sorted(dict.fromkeys(vals)), other, brk


def census(images, brows):
    """-> (rows, agg): rows per use and argument, agg {(dll, name, k): [closed, set of values, uses]}."""
    rows, agg = [], {}
    for dll, name, argc, ordinal in brows:
        known = sorted(dict.fromkeys('#' + o for d, n, a, o in brows if d == dll and o))
        for nm in sorted(images):
            img = images[nm]
            ul, open_ = uses(img, dll, name, ordinal)
            by_ordinal = [1 for s, (d, n) in sorted(img.imports.items()) if iat.norm_dll(d) == dll and str(n).startswith('#') and n not in known]
            for k in range(argc):
                a = agg.setdefault((dll, name, k), [True, [], 0])
                if open_ or by_ordinal:
                    a[0] = False
                    rows.append((dll, name, argc, nm, 0, 0, k, 'open', [], [], ['%x %s' % x for x in open_] + ['import of %s by an ordinal no bridge row has' % dll] * len(by_ordinal[:1])))
                for ref, call in ul:
                    c, vals, other, brk = leaves(argflow.derive_arg(img, call, k))
                    a[2] += 1
                    a[1] = sorted(dict.fromkeys(a[1] + vals))
                    a[0] = a[0] and c
                    rows.append((dll, name, argc, nm, ref, call, k, 'closed' if c else 'open', vals, other, brk))
    return rows, agg


def reach(dlls, gpa_text, loadlib_text):
    """-> (none?, [items]): the dynamic ways to a dll of DLLS in the gpa rows and loadlib rows."""
    items = []
    for line in gpa_text.splitlines()[1:]:
        f = line.split('\t')
        if len(f) < 12:
            continue
        if f[4] != 'closed':
            items.append('gpa open %s %s' % (f[0], f[1]))
        for p in f[8].split(';'):
            if p and iat.norm_dll(p.split('!')[0]) in dlls:
                items.append('gpa pair %s %s %s' % (f[0], f[1], p))
    for line in loadlib_text.splitlines()[1:]:
        f = line.split('\t')
        if len(f) < 7:
            continue
        if f[4] != 'closed':
            items.append('loadlib open %s %s' % (f[0], f[1]))
        for n in f[5].split(','):
            if n in dlls:
                items.append('loadlib name %s %s %s' % (f[0], f[1], n))
    return not items, sorted(dict.fromkeys(items))


def hx(v):
    return ','.join('0x%x' % x for x in v) or '-'


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--images-file', required=True)
    ap.add_argument('--bridge', required=True)
    ap.add_argument('--dll', action='append', required=True)
    ap.add_argument('--gpa')
    ap.add_argument('--loadlib')
    ap.add_argument('--escape', action='append', default=[])
    ap.add_argument('--writes')
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    dlls = sorted(iat.norm_dll(d) for d in a.dll)
    imgs = gpa_names.load_images(gpa_names.specs_of(a.images_file), a.bridge, a.escape, a.writes)
    rows, agg = census(imgs, bridge_rows(a.bridge, dlls))
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, 'argcensus.tsv'), 'w', newline='\n') as fh:
        fh.write('dll\tname\targc\timage\tref\tcall\tk\tstatus\tvalues\tother\tbreaks\n')
        for r in rows:
            fh.write('\t'.join([r[0], r[1], str(r[2]), r[3], '0x%x' % r[4], '0x%x' % r[5], str(r[6]), r[7], hx(r[8]), ' | '.join(r[9]) or '-', ' / '.join(r[10]) or '-']) + '\n')
    with open(os.path.join(a.out, 'argcensus.agg.tsv'), 'w', newline='\n') as fh:
        fh.write('dll\tname\tk\tstatus\tvalues\tuses\n')
        for (d, n, k) in sorted(agg):
            c, v, u = agg[(d, n, k)]
            fh.write('%s\t%s\t%d\t%s\t%s\t%d\n' % (d, n, k, 'closed' if c else 'open', hx(v), u))
    none, items = reach(dlls, open(a.gpa).read() if a.gpa else '', open(a.loadlib).read() if a.loadlib else '')
    with open(os.path.join(a.out, 'argcensus.reach.txt'), 'w', newline='\n') as fh:
        fh.write('reach: %s\n' % ('none' if none else 'possible') + ''.join(i + '\n' for i in items))
    print('argcensus: %d (service, argument) pairs, %d closed; dynamic reach %s (%d items)' % (
        len(agg), sum(1 for v in agg.values() if v[0]), 'none' if none else 'possible', len(items)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
