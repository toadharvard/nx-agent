#!/usr/bin/env python3
"""s23_codeview - proof inputs for DATAMODEL row S23 (empty-base placement), parts (a) and (c).

(a) SELECTION by rule, from the cl /Z7 CodeView type tables of the pristine MSVC objects (`.debug$T`, read by link/codeview.py):
    a record is EMPTY iff it has no data member, no vfptr, no virtual base and every base is empty (the class size cl gives it is then 1);
    a class is SELECTED iff it is complete and has at least one EMPTY base subobject at any depth (a direct base, or a base of a base,
    through non-virtual and virtual bases alike). For each selected class the tool records what cl says: size, data members (name, bit
    offset), direct bases (key, offset or None, flag), and the offsets in the complete object of every non-virtual base subobject at any
    depth (a path product of the direct offsets). A record defined in several objects must be defined identically (else a `conflict`).
(c) JOIN with the exe's own RTTI facts (BCD rows of rtti_bcd.tsv, fnv-nx-build l3a-lift/rtti-bcd): a selected class whose type descriptor has
    a COL is `rtti`; its BCD rows with pdisp == -1 are the non-virtual base subobjects with their mdisp in the exe's real layout. The class is
    `bcd-equal` iff the multiset {(base key, offset)} of the CodeView path offsets equals the multiset of the BCD rows (mdisp), `bcd-differs`
    otherwise; a class without a COL is `no-rtti` (no exe-side fact exists for it).
Output (--out DIR, private data: not committed to the public repository): selected.tsv (key size nbases empty-bases depth-max rtti verdict),
summary.json (global counts). Pure reader of two pinned inputs. Run: python3 -I s23_codeview.py --obj DIR [--obj DIR...] --bcd FILE --out DIR
"""
import argparse
import collections
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'link'))
import coffobj  # noqa: E402
import codeview  # noqa: E402


def read_records(objdirs):
    """-> ({key: CRec}, conflicts [key], objects, objects without types)"""
    recs, conflicts, n, notypes = {}, set(), 0, 0
    files = sorted(f for d in objdirs for f in glob.glob(os.path.join(d, '*.obj')))
    for f in files:
        n += 1
        blobs = coffobj.read(f).blobs(b'.debug$T')
        if not blobs:
            notypes += 1
        for b in blobs:
            t = codeview.Table(b, f)
            for k in t.keys():
                for r in t.record(k):
                    if k in recs and recs[k] != r:
                        conflicts.add(k)
                    recs.setdefault(k, r)
    return recs, sorted(conflicts), n, notypes


def is_empty(key, recs, memo):
    if key in memo:
        return memo[key]
    memo[key] = None   # a cycle cannot occur in a valid class graph; None never equals True
    r = recs.get(key)
    v = r is not None and not r.fields and not r.vf and not r.vbptr and all(
        flag != 'v' and flag != 'i' and is_empty(b, recs, memo) for b, _o, flag in r.bases)
    memo[key] = v
    return v


def subobjects(key, recs, base_off=0, depth=0, out=None):
    """every non-virtual base subobject of KEY at any depth -> [(base key, offset in the complete object, depth)]; None if a virtual base
    is on the way (its offset is not a path product: the class is then 'virtual')"""
    out = [] if out is None else out
    for b, o, flag in recs[key].bases:
        if flag != 'n' or o is None or b not in recs:
            return None
        out.append((b, base_off + o, depth + 1))
        if subobjects(b, recs, base_off + o, depth + 1, out) is None:
            return None
    return out


def has_empty_base(key, recs, memo, seen=None):
    """an empty base subobject at any depth, virtual bases included"""
    for b, _o, _f in recs[key].bases:
        if is_empty(b, recs, memo) or (b in recs and has_empty_base(b, recs, memo)):
            return True
    return False


def read_bcd(path):
    """-> {type descriptor key: [(base key, mdisp)] of the BCD rows with pdisp == -1, index >= 1} for the COL rows"""
    cols, bcd = {}, collections.defaultdict(list)
    for ln in open(path):
        if ln.startswith('#') or not ln.strip():
            continue
        f = ln.rstrip('\n').split('\t')
        if f[0] == 'COL':
            cols.setdefault(f[1], f[5])
        elif f[0] == 'BCD':
            bcd[f[1]].append((int(f[2]), f[3], int(f[5]), int(f[6])))
    out = {}
    for col, td in cols.items():
        rows = sorted(bcd[col])
        out.setdefault(td[3:], [])
        # the first BCD row is the class itself; the others are its base subobjects
        out[td[3:]] = [(r[1][3:], r[2], r[3]) for r in rows[1:]]
        out[td[3:]].append(('<n>', len(rows), 0))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--obj', action='append', required=True)
    ap.add_argument('--bcd', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    recs, conflicts, nobj, notypes = read_records(a.obj)
    bcd = read_bcd(a.bcd)
    memo = {}
    sel, rows = 0, []
    counts = collections.Counter()
    for k in sorted(recs):
        if not has_empty_base(k, recs, memo):
            continue
        r = recs[k]
        sel += 1
        subs = subobjects(k, recs)
        td = bcd.get(k)
        if subs is None:
            verdict = 'virtual'      # a virtual base on the way: its offset is a vbtable fact, outside S23's rule
        elif td is None:
            verdict = 'no-rtti'
        else:
            want = collections.Counter((b, o) for b, o, _d in subs)
            got = collections.Counter((b, m) for b, m, p in td if b != '<n>' and p == -1)
            verdict = 'bcd-equal' if want == got else 'bcd-differs'
        counts[verdict] += 1
        rows.append((k, r.size, len(r.bases), sum(1 for b, _o, _f in r.bases if is_empty(b, recs, memo)),
                     max([d for _b, _o, d in subs] or [0]) if subs else '-', 'rtti' if td is not None else '-', verdict))
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, 'selected.tsv'), 'w') as f:
        f.write('key\tsize\tdirect_bases\tdirect_empty_bases\tmax_depth\trtti\tverdict\n')
        for r in rows:
            f.write('\t'.join(str(x) for x in r) + '\n')
    summary = {'objects': nobj, 'objects_without_types': notypes, 'records': len(recs), 'conflicting_records': conflicts,
               'empty_records': sum(1 for k in recs if is_empty(k, recs, memo)), 'selected': sel, 'verdicts': dict(counts)}
    json.dump(summary, open(os.path.join(a.out, 'summary.json'), 'w'), indent=1, sort_keys=True)
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0 if not conflicts and counts['bcd-differs'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
