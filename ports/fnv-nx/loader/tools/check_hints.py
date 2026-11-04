#!/usr/bin/env python3
"""Confirm vftable hints on an exe: does each hinted address look like a vftable without an RTTI locator?

usage: check_hints.py FalloutNV.exe HINTS [--s2-addrmap out/addrmap.tsv] [--out verdicts.tsv]

Pass S2's map: without it slot 0 only has to lie in .text, and the tail of a string ("xyz\0" = 0x0078797a) can.

HINTS is S3's hint format (one original VA per line, '#' comments), e.g. loader/synth's --hints-out list of the
vftables whose [-1] word belongs to the preceding item in the decomp's data (classes built without C++ RTTI,
docs/DATAIMAGE.md "COL-less vftables"). Verdict per hint, with the rules of S3's scanner (scan_codeptrs.py):

  colless        slot 0 holds a .text address (known to the address map, when given) and [-1] is no valid COL:
                 the hint is needed, S3's COL search cannot find this vftable
  has-col        [-1] is a valid COL: the class has RTTI after all; the hint is harmless (the COL search finds the
                 vftable) but the decomp's evidence was wrong (check the ??_7 address)
  col-at-plus4   no COL at [-1] but a valid one at [0] (vftable one slot later): the decomp's ??_7 is 4 too low
  not-code       slot 0 is no .text address: not a vftable start in this exe (drop the hint)
  outside        not in a mapped data section

Prints counts and addresses only.
"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fnvnx_formats import AddrMap  # noqa: E402
from fnvnx_pe import PE, Image  # noqa: E402
from scan_codeptrs import Scanner  # noqa: E402


def read_hints(path):
    return sorted({int(x, 0) for x in open(path) if x.strip() and not x.startswith('#')})


def verdicts(img, hints, amap=None):
    sc = Scanner(img, amap or AddrMap())
    out = []
    for h in hints:
        va = (h + img.delta) & 0xffffffff
        m = sc.mapping_of(va)
        if m is None or va & 3 or not sc.eligible_in(m, va):
            out.append((h, 'outside', 0))
            continue
        slots = 0
        w = va
        while sc.eligible_in(m, w) and sc.is_text(sc.word(m, w)):
            slots += 1
            w += 4
        v0 = sc.word(m, va)
        if img.ptr(v0, 20) is not None and sc.valid_col(v0) and sc.eligible_in(m, va + 4) and \
                sc.is_text(sc.word(m, va + 4)):
            out.append((h, 'col-at-plus4', slots))
            continue
        if not sc.is_text(v0) or (amap is not None and len(amap) and sc.known_entry(v0) is None):
            out.append((h, 'not-code', slots))
            continue
        col = sc.word(m, va - 4) if va - 4 >= m.va else None
        if col is not None and img.ptr(col, 20) is not None and sc.valid_col(col):
            out.append((h, 'has-col', slots))
            continue
        out.append((h, 'colless', slots))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('exe')
    ap.add_argument('hints')
    ap.add_argument('--s2-addrmap', help="S2's addrmap.tsv: slot 0 must be an address it knows")
    ap.add_argument('--out', help='TSV: hint, verdict, code slots from the hint')
    a = ap.parse_args(argv)
    img = Image(PE(open(a.exe, 'rb').read()))
    amap = AddrMap.read_s2_tsv(a.s2_addrmap) if a.s2_addrmap else None
    res = verdicts(img, read_hints(a.hints), amap)
    if a.out:
        with open(a.out, 'w') as f:
            f.write('# hint\tverdict\tcode_slots\n')
            for h, v, n in res:
                f.write('0x%08x\t%s\t%d\n' % (h, v, n))
    c = collections.Counter(v for _, v, _ in res)
    print('hints=%d %s' % (len(res), ' '.join('%s=%d' % kv for kv in sorted(c.items()))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
