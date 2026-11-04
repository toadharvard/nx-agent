#!/usr/bin/env python3
"""c4_vtti.py - V1-link C4, whole image: every vtable group's sub-vtables against the typeinfo the image keeps for
the same class, independent of which objects defined them (covers what the object-level audit c4_definers cannot:
a vtable object without its own typeinfo, typeinfo of a base kept from another unit's view).

    c4_vtti.py <trial.elf> <out tsv>       # exit 1 when a class's vtable names a subobject its typeinfo lacks

For each _ZTV<C> of the image: every (offset-to-top, typeinfo) header inside the group whose typeinfo word is
_ZTI<C> gives a subobject offset o = -offset-to-top; the kept _ZTI<C>, walked through the base typeinfos the image
holds (non-virtual paths), must have a base at +o. Classes with virtual bases are counted, not judged (vbase
offsets are not constants). Undefined base typeinfo pointers (0 in an incomplete trial link) are counted apart.
"""
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import c4_vtables  # noqa: E402
import elf32       # noqa: E402


def main():
    elf, out = sys.argv[1:3]
    img = elf32.Image(elf)
    ti = c4_vtables.TI(img)
    cnt = collections.Counter()
    rows = []
    for name, (val, size, typ, bind, shndx) in sorted(img.by_name.items()):
        if not name.startswith('_ZTV') or name.startswith('_ZTVN10__cxxabiv'):
            continue
        t = img.by_name.get('_ZTI' + name[4:])
        if not t:
            cnt['no typeinfo in the image'] += 1
            continue
        cnt['vtable groups'] += 1
        offs = []
        k = val
        while k + 8 <= val + size:
            if img.u32(k + 4) == t[0]:
                o = img.i32(k)
                if o is not None and o <= 0:
                    offs.append(-o)
            k += 4
        seen, virt, undef = set([0]), False, 0
        q, n = [(t[0], 0)], 0
        while q and n < 5000:
            a, off = q.pop(0)
            n += 1
            for b, o, v, pub in (ti.bases(a) or []):
                if not b:
                    undef += 1
                    continue
                if v:
                    virt = True
                    continue
                seen.add(off + o)
                q.append((b, off + o))
        if virt:
            cnt['with virtual bases (not judged)'] += 1
            continue
        missing = sorted(set(o for o in offs if o not in seen))
        if missing and undef:
            cnt['subobject offsets missing, base typeinfo undefined in this link'] += 1
            rows.append((name[4:], 'undefined-base', ','.join(map(str, missing))))
        elif missing:
            cnt['vtable names a subobject the kept typeinfo lacks'] += 1
            rows.append((name[4:], 'missing', ','.join(map(str, missing))))
        else:
            cnt['consistent'] += 1
    with open(out, 'w') as f:
        f.write('#class\tkind\tsubobject offsets without a base in the kept typeinfo\n')
        for r in rows:
            f.write('\t'.join(r) + '\n')
    for k in sorted(cnt):
        print('%-62s %d' % (k, cnt[k]))
    return 1 if any(r[1] == 'missing' for r in rows) else 0


if __name__ == '__main__':
    sys.exit(main())
