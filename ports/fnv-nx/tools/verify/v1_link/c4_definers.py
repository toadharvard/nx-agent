#!/usr/bin/env python3
"""c4_definers.py - V1-link: which rewritten objects define given symbols (vtable groups, typeinfo), in trial-link
order (sorted file names, as `fnvlink.py check` links them), with binding and size; and an audit of every class
whose kept vtable group and kept typeinfo come from objects that disagree on the class.

    c4_definers.py <fixed dir> <symbol>...                  # list definers
    c4_definers.py --audit <fixed dir> <trial.elf> <out tsv> # every _ZTV<C> / _ZTI<C> pair of the image

Kept copy (GNU ld): the first strong definition in link order, else the first weak one (COMDAT copies of one group
are dropped after the first). The audit compares the size of the kept _ZTI<C> with the _ZTI<C> of the object whose
_ZTV<C> was kept (that object's own view of C's bases): a different size is a different base list.
"""
import collections
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def scan(fixed, pred):
    objs = sorted(n for n in os.listdir(fixed) if n.endswith(('.o', '.a')))
    order = {n: k for k, n in enumerate(objs)}
    hits = collections.defaultdict(list)
    for i in range(0, len(objs), 200):
        chunk = [os.path.join(fixed, n) for n in objs[i:i + 200]]
        out = subprocess.run(['llvm-nm', '-A', '--defined-only', '-S', *chunk], capture_output=True,
                             text=True).stdout
        for line in out.splitlines():
            f = line.rsplit(None, 4)               # object paths may hold blanks
            if len(f) == 5 and pred(f[-1]):
                obj = os.path.basename(f[0].rstrip(':'))
                hits[f[-1]].append((order[obj], obj, f[-2], int(f[-3], 16)))
    for v in hits.values():
        v.sort()
    return hits


def ti_refs(path, name):
    """{offset: symbol} of the relocations inside the object's definition of name."""
    import elf32
    o = elf32.Obj(path)
    out = {}
    for sy in o.syms:
        if sy.name == name and isinstance(sy.shndx, int):
            for r in o.rels:
                if r.sec == sy.shndx and sy.value <= r.offset < sy.value + sy.size:
                    out[r.offset - sy.value] = r.sym.name
            break
    o.close()
    return out


def kept(defs):
    strong = [d for d in defs if d[2] in ('D', 'R', 'B', 'd', 'r')]
    return strong[0] if strong else (defs[0] if defs else None)


def audit(fixed, elf, out):
    import elf32
    hits = scan(fixed, lambda n: n.startswith(('_ZTV', '_ZTI')))
    img = elf32.Image(elf)
    rows = []
    cnt = collections.Counter()
    for name, defs in sorted(hits.items()):
        if not name.startswith('_ZTV'):
            continue
        cls = name[4:]
        ti = '_ZTI' + cls
        kv = kept(defs)
        tdefs = hits.get(ti, [])
        kt = kept(tdefs)
        cnt['vtable groups'] += 1
        if kv is None or kt is None:
            continue
        own = [d for d in tdefs if d[1] == kv[1]]
        isz = img.by_name.get(ti, (0, 0))[1]
        if not own:
            cnt['vtable object defines no typeinfo'] += 1
            continue
        if own[0][3] != kt[3] or (isz and isz != own[0][3]):
            cnt['typeinfo from an object that disagrees on the bases'] += 1
            rows.append((cls, kv[1], '%d' % own[0][3], kt[1], '%d' % kt[3], '%d' % isz,
                         ','.join(sorted({str(d[3]) for d in tdefs})), 'size'))
        elif kt[1] != kv[1]:
            # same size: compare the base typeinfo names (the vtable object's relocations vs the image's words)
            mine = ti_refs(os.path.join(fixed, kv[1]), ti)
            a = img.by_name.get(ti)
            got = []
            if a:
                for off in sorted(mine):
                    w = img.u32(a[0] + off)
                    nm = [n for n in img.names_at(w) if n.startswith(('_ZTI', '_ZTS'))]
                    got.append(nm[0] if nm else hex(w or 0))
            want = [mine[o] for o in sorted(mine)]
            if [x for x in want if x.startswith('_ZTI')] != [x for x in got if x.startswith('_ZTI')] and a:
                cnt['typeinfo from another object, same size, other bases'] += 1
                rows.append((cls, kv[1], '%d' % own[0][3], kt[1], '%d' % kt[3], '%d' % isz,
                             'want %s got %s' % ([x for x in want if x.startswith('_ZTI')],
                                                 [x for x in got if x.startswith('_ZTI')]), 'bases'))
            else:
                cnt['typeinfo from another object, same bases'] += 1
    with open(out, 'w') as f:
        f.write('#class\tvtable object\tits typeinfo size\tkept typeinfo object\tkept size\timage size\tall sizes / bases\twhy\n')
        for r in rows:
            f.write('\t'.join(r) + '\n')
    for k, v in sorted(cnt.items()):
        print('%-55s %d' % (k, v))
    for r in rows[:15]:
        print('  ', r)
    return 1 if rows else 0


def main():
    if sys.argv[1] == '--audit':
        return audit(*sys.argv[2:5])
    fixed = sys.argv[1]
    want = set(sys.argv[2:])
    hits = scan(fixed, lambda n: n in want)
    for name in sorted(want):
        print(name)
        for k, obj, typ, size in hits.get(name, []):
            print('    %-5d %-3s size %-6d %s' % (k, typ, size, obj))
    return 0


if __name__ == '__main__':
    sys.exit(main())
