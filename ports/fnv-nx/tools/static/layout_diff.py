#!/usr/bin/env python3
"""layout_diff.py - compare the record layouts of the two clang configurations (fnvcheck --layout rows).

    tools/static/layout_diff.py <out dir>

Reads <out>/clang/{msvc,gnu}/rows/*.tsv (rows `L key size align dsize fields bases flags`, written by clang_pass.sh)
and writes <out>/layout/diff.tsv - one row per record defined in the tree whose MSVC x86 layout differs from the
GCC/Itanium ILP32 layout - and <out>/layout/summary.tsv (counts per cause).

Causes (first that applies):
  vbase        virtual bases (MSVC vbptr vs Itanium vtable offsets; never byte-compatible)
  bitfield     a bit-field moves (MSVC: a new unit for each change of declared type size; GCC: packs across types)
  empty-base-offset  benign: same size and fields, only an empty base sits at another offset (MSVC puts a new vfptr
               before a vfptr-less base); a cast to that empty base adjusts the pointer differently, nothing else
  empty-base   an empty base gets its own byte under MSVC (not the first one) but overlaps at 0 under Itanium
  pack-align   #pragma pack with an over-aligned member (MSVC: __declspec(align) / __m128 win over pack; GCC: pack wins)
  parse        the two configurations see a different number of members: one of them dropped members after a parse
               error (a proxy artefact, not a layout difference; the probe checks the gnu side against GCC)
  align-ignored  MSVC aligns more than GCC with the same members and no pack: an alignment GCC ignores, typically
               `__declspec(align(16)) struct S {...};` - before the struct keyword GCC drops the attribute
               ("attribute ignored in declaration of 'struct S'", -Wattributes), MSVC aligns the type; also every
               record that holds such a member at a 16-aligned MSVC offset
  member-type  only because a member's type (another record) differs: not a root cause
  base-type    only because a base's layout differs: not a root cause
  other        anything else (look at the row)
"""
import os
import sys
from collections import Counter, defaultdict


def parse_fields(s):
    out = []
    for f in s.split(';'):
        if not f:
            continue
        parts = f.split(':', 4)
        name, off, sz = parts[0], int(parts[1]), int(parts[2])
        w = int(parts[3]) if len(parts) > 3 else None
        ty = parts[4] if len(parts) > 4 else ''
        out.append((name, off, sz, w, ty))
    return out


def parse_bases(s):
    out = []
    for b in s.split(';'):
        if not b:
            continue
        if b.startswith('v:'):
            out.append((b[2:], None, None, False))
            continue
        empty = b.endswith(':empty')
        if empty:
            b = b[:-len(':empty')]
        ty, off, sz = b.rsplit(':', 2)
        out.append((ty, int(off), int(sz), empty))
    return out


def load(d):
    recs = {}
    rows = os.path.join(d, 'rows')
    if not os.path.isdir(rows):
        return recs
    for name in sorted(os.listdir(rows)):
        if not name.endswith('.tsv'):
            continue
        unit = name[:-4]
        with open(os.path.join(rows, name), encoding='utf-8', errors='replace') as f:
            for line in f:
                if not line.startswith('L\t'):
                    continue
                p = line.rstrip('\n').split('\t')
                if len(p) < 8 or p[1] in recs:
                    continue
                try:
                    recs[p[1]] = dict(size=int(p[2]), align=int(p[3]), dsize=int(p[4]), fields=parse_fields(p[5]),
                                      bases=parse_bases(p[6]), flags=p[7].split(), unit=unit)
                except ValueError:
                    continue
    return recs


def compare(m, g):
    diffs, cause = [], None
    if m['size'] != g['size']:
        diffs.append('size %d/%d' % (m['size'], g['size']))
    if m['align'] != g['align']:
        diffs.append('align %d/%d' % (m['align'], g['align']))
    mf = [f for f in m['fields'] if not (f[3] == 0 and not f[0])]   # unnamed zero-width (TAIL1) carry no data
    gf = [f for f in g['fields'] if not (f[3] == 0 and not f[0])]
    member_type = base_type = bitfield = False
    for a, b in zip(mf, gf):
        if a[1] != b[1] or a[3] != b[3]:
            what = 'bitfield' if a[3] is not None else 'field'
            diffs.append('%s %s @%s/@%s' % (what, a[0] or '?', fmt_off(a), fmt_off(b)))
            if a[3] is not None or b[3] is not None:
                bitfield = True
        if a[2] != b[2] and a[3] is None:
            diffs.append('field %s size %d/%d' % (a[0] or '?', a[2], b[2]))
            member_type = True
    if len(mf) != len(gf):
        diffs.append('fields %d/%d' % (len(mf), len(gf)))
    for a, b in zip(m['bases'], g['bases']):
        if a[1] is None:
            continue
        if a[1] != b[1]:
            diffs.append('base %s @%d/@%d' % (a[0], a[1], b[1]))
        if a[2] != b[2]:
            base_type = True
    if not diffs:
        return None, None
    # a bit-field difference is first-order only when the bit-field moves within the same bytes budget
    if 'vbase' in m['flags'] or any(b[1] is None for b in m['bases']):
        cause = 'vbase'
    elif len(mf) != len(gf):
        cause = 'parse'
    elif all(d.startswith('base ') for d in diffs) and all(
            b[3] for a, b in zip(m['bases'], g['bases']) if a[1] is not None and a[1] != b[1]):
        cause = 'empty-base-offset'   # same size and fields; only an empty base sits elsewhere (MSVC: after the vfptr)
    elif any(b[3] for b in g['bases']) and any(a[1] != b[1] for a, b in zip(m['bases'], g['bases']) if a[1] is not None):
        cause = 'empty-base'
    elif base_type:
        cause = 'base-type'
    elif member_type:
        cause = 'member-type'
    elif bitfield or 'bitfield' in m['flags']:
        cause = 'bitfield'
    elif any(f.startswith('pack') for f in m['flags']):
        cause = 'pack-align'
    elif m['align'] > g['align'] or any(a[1] != b[1] and a[1] % 128 == 0 for a, b in zip(mf, gf)):
        cause = 'align-ignored'
    else:
        cause = 'other'
    return cause, diffs


def fmt_off(f):
    off = f[1]
    s = '%d' % (off // 8) if off % 8 == 0 else '%d.%d' % (off // 8, off % 8)
    if f[3] is not None:
        s += '+%d' % f[3]
    return s


def main():
    out = sys.argv[1]
    m = load(os.path.join(out, 'clang', 'msvc'))
    g = load(os.path.join(out, 'clang', 'gnu'))
    os.makedirs(os.path.join(out, 'layout'), exist_ok=True)
    causes = Counter()
    rows = []
    both = 0
    for key in sorted(set(m) & set(g)):
        both += 1
        cause, diffs = compare(m[key], g[key])
        if cause:
            causes[cause] += 1
            rows.append((cause, key, m[key]['unit'], '; '.join(diffs)))
    with open(os.path.join(out, 'layout', 'diff.tsv'), 'w') as f:
        f.write('cause\trecord\tunit\tmsvc/gnu\n')
        for r in sorted(rows):
            f.write('\t'.join(r) + '\n')
    with open(os.path.join(out, 'layout', 'summary.tsv'), 'w') as f:
        f.write('records_msvc\t%d\nrecords_gnu\t%d\nrecords_compared\t%d\n' % (len(m), len(g), both))
        for c, n in causes.most_common():
            f.write('diff_%s\t%d\n' % (c, n))
    print('layouts: msvc %d, gnu %d, compared %d, differing %d (%s)' % (
        len(m), len(g), both, len(rows), ', '.join('%s %d' % kv for kv in causes.most_common())))


if __name__ == '__main__':
    main()
