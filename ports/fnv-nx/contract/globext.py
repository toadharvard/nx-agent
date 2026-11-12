#!/usr/bin/env python3
"""globext - the global-extents fact: exe VA ranges of the global data objects that the gates certify (L2).

    globext.py --rows rows.json --out fact.txt [--audit audit.tsv] [--range LO HI] [--probe VA ...]

rows.json: a list of {"image", "va", "size", "certified_by": [gate ids], "type", "symbol", "unit"}. Every row is a
certified object: the caller (the gate-output reader) passes only objects whose address, type layout and size the
namegate, layout and abi gates certified. This tool never certifies, guesses or fills in; an uncertified object is
not a row.
Output fact, one line per object: `image va_start size certified_by` (hex va, decimal size, gate ids joined by '+',
no spaces), sorted by image then va_start. The audit file holds `image va_start size type symbol unit`.
A row with size < 1, a missing field, a non-hex-able va, or an empty certified_by is a ValueError. Two objects of
one image whose ranges overlap are a ValueError naming both. Rows of one (image, va) are one object when size, gates, type and symbol agree (units are joined with `,`), else a ValueError.
--range prints the object count inside [LO, HI); --probe prints the object containing each VA, or `none certified`.
"""
import argparse, json, sys

FIELDS = ('image', 'va', 'size', 'certified_by', 'type', 'symbol', 'unit')


def _row(r):
    for f in FIELDS:
        if f not in r:
            raise ValueError('row %r: missing %s' % (r, f))
    va, size = r['va'], r['size']
    if isinstance(va, str):
        va = int(va, 16)
    if not isinstance(va, int) or isinstance(va, bool) or not isinstance(size, int) or isinstance(size, bool):
        raise ValueError('row %r: va/size not integers' % (r,))
    if va < 0 or size < 1:
        raise ValueError('row %r: bad va/size' % (r,))
    gates = r['certified_by']
    if not gates or any((not g) or any(c.isspace() or c == '+' for c in g) for g in gates):
        raise ValueError('row %r: certified_by empty or has a bad id' % (r,))
    return (r['image'], va, size, '+'.join(sorted(set(gates))), r['type'], r['symbol'], r['unit'])


def build(rows):
    """-> sorted list of (image, va, size, certified_by, type, symbol, unit); overlap is a ValueError."""
    byva = {}
    for r in rows:
        o = _row(r)
        k = (o[0], o[1])
        if k not in byva:
            byva[k] = o
            continue
        p = byva[k]
        if p[2:6] != o[2:6]:   # the same object in several units (an inline or COMDAT variable) must agree in everything but the unit
            raise ValueError('conflict at %s %#x: %s (size %d, %s) and %s (size %d, %s)' % (o[0], o[1], p[5], p[2], p[4], o[5], o[2], o[4]))
        if o[6] not in p[6].split(','):
            byva[k] = p[:6] + (','.join(sorted(p[6].split(',') + [o[6]])),)
    objs = sorted(byva.values())
    for a, b in zip(objs, objs[1:]):
        if a[0] == b[0] and b[1] < a[1] + a[2]:
            raise ValueError('overlap in %s: %s [%#x,%#x) and %s [%#x,%#x)' %
                             (a[0], a[5], a[1], a[1] + a[2], b[5], b[1], b[1] + b[2]))
    return objs


def fact_text(objs):
    return ''.join('%s %#x %d %s\n' % (o[0], o[1], o[2], o[3]) for o in objs)


def audit_text(objs):
    return ''.join('%s %#x %d %s %s %s\n' % (o[0], o[1], o[2], o[4], o[5], o[6]) for o in objs)


def count_in(objs, image, lo, hi):
    """Objects of the image lying inside [lo, hi) completely."""
    return sum(1 for o in objs if o[0] == image and lo <= o[1] and o[1] + o[2] <= hi)


def containing(objs, image, va):
    for o in objs:
        if o[0] == image and o[1] <= va < o[1] + o[2]:
            return o
    return None


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--rows', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--audit')
    ap.add_argument('--image', default='exe')
    ap.add_argument('--range', nargs=2, metavar=('LO', 'HI'))
    ap.add_argument('--probe', nargs='*', default=[])
    a = ap.parse_args(argv)
    with open(a.rows) as f:
        objs = build(json.load(f))
    with open(a.out, 'w') as f:
        f.write(fact_text(objs))
    if a.audit:
        with open(a.audit, 'w') as f:
            f.write(audit_text(objs))
    print('objects %d' % len(objs))
    if a.range:
        lo, hi = int(a.range[0], 16), int(a.range[1], 16)
        print('range %#x..%#x certified objects %d' % (lo, hi, count_in(objs, a.image, lo, hi)))
    for p in a.probe:
        o = containing(objs, a.image, int(p, 16))
        print('%s %s' % (p, 'none certified' if o is None else '%s [%#x,%#x)' % (o[5], o[1], o[1] + o[2])))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
