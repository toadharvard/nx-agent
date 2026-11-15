#!/usr/bin/env python3
"""s23_codeview - proof inputs for DATAMODEL row S23 (empty-base placement), parts (a) and (c).

(a) SELECTION by rule, from the cl /Z7 CodeView type tables of the pristine MSVC objects (`.debug$T`, read by link/codeview.py):
    a record is EMPTY iff it has no data member, no vfptr, no virtual base and every base is empty (the class size cl gives it is then 1);
    a class is SELECTED iff it is complete and has at least one EMPTY base subobject at any depth (a direct base, or a base of a base,
    through non-virtual and virtual bases alike). For each selected class the tool records what cl says: size, data members (name, bit
    offset), direct bases (key, offset or None, flag), and the offsets in the complete object of every non-virtual base subobject at any
    depth (a path product of the direct offsets). Records are never merged across objects: the decomp declares some classes differently in different translation units, so a
    selected class is a (key, fingerprint of its closure) pair, counted once however many objects define it.
(c) JOIN with the exe's own RTTI facts (BCD rows of rtti_bcd.tsv, fnv-nx-build l3a-lift/rtti-bcd): a selected class whose type descriptor has
    a COL is `rtti`; its BCD rows with pdisp == -1 are the non-virtual base subobjects with their mdisp in the exe's real layout. The class is
    `bcd-equal` iff the multiset {(base key, offset)} of the CodeView path offsets equals the multiset of the BCD rows (mdisp), `offset-differs` iff some base type that both sides list is at different offsets (a layout statement), `chain-differs` iff the base sets differ with every common base at equal offsets (the decomp's class is not the exe's: not evidence either way); a class without a COL is `no-rtti` (no exe-side fact exists for it).
Output (--out DIR, private data: not committed to the public repository): selected.tsv (key size nbases empty-bases depth-max rtti verdict),
summary.json (global counts). Pure reader of two pinned inputs. Run: python3 -I s23_codeview.py --obj DIR [--obj DIR...] --bcd FILE --out DIR
"""
import argparse
import collections
import glob
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'link'))
import coffobj  # noqa: E402
import codeview  # noqa: E402


def tables(objdirs):
    """-> yields (path, {key: CRec} of that object's complete named records), the unnamed keys left out (an unnamed type's key, `<unnamed-tag>`
    or `<lambda_...>`, names no single type). The records of ONE object are one consistent type universe; the decomp declares some classes
    differently in different translation units (a stub in one, the full class in another), so records are never merged across objects."""
    for f in sorted(f for d in objdirs for f in glob.glob(os.path.join(d, '*.obj'))):
        recs, un = {}, 0
        for b in coffobj.read(f).blobs(b'.debug$T'):
            t = codeview.Table(b, f)
            for k in t.keys():
                if '<unnamed' in k or '<lambda' in k:
                    un += 1
                    continue
                for r in t.record(k):
                    recs.setdefault(k, r)
        yield f, recs, un


def fingerprint(key, recs, memo):
    """the closure of a record: its size, data members, vf/vbptr flags and bases (key, offset, flag) and, recursively, those of its bases"""
    if key in memo:
        return memo[key]
    r = recs.get(key)
    if r is None:
        memo[key] = ('?', key)
        return memo[key]
    v = (key, r.size, r.fields, r.vf, r.vbptr, tuple((b, o, f, fingerprint(b, recs, memo)) for b, o, f in r.bases))
    memo[key] = v
    return v


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
    ap.add_argument('--r7', help='part (b): TSV of link/gate_r7.rows() of the same units built by the patched compiler (stem, key, verdict, reason)')
    a = ap.parse_args()
    bcd = read_bcd(a.bcd)
    nobj = notypes = unnamed = nrec = nempty = 0
    sel = {}                      # (key, fingerprint hash) -> row; one per distinct selected class closure
    variants = collections.defaultdict(set)
    for f, recs, un in tables(a.obj):
        nobj += 1
        unnamed += un
        notypes += 0 if recs else 1
        nrec += len(recs)
        memo, fmemo = {}, {}
        nempty += sum(1 for k in recs if is_empty(k, recs, memo))
        for k in recs:
            if not has_empty_base(k, recs, memo):
                continue
            h = hashlib.sha256(repr(fingerprint(k, recs, fmemo)).encode()).hexdigest()[:16]
            variants[k].add(h)
            if (k, h) in sel:
                continue
            r = recs[k]
            subs = subobjects(k, recs)
            td = bcd.get(k)
            detail = ''
            eo = sorted({o for b, o, _d in (subs or []) if is_empty(b, recs, memo)})   # offsets of the empty base subobjects, any depth
            if subs is None:
                verdict = 'virtual'      # a virtual base on the way: its offset is a vbtable fact, outside S23's rule
            elif td is None:
                verdict = 'no-rtti'
            else:
                want = collections.Counter((b, o) for b, o, _d in subs)
                got = collections.Counter((b, m) for b, m, p in td if b != '<n>' and p == -1)
                wk, gk = collections.defaultdict(set), collections.defaultdict(set)
                for (b, o), n in want.items():
                    wk[b].add(o)
                for (b, o), n in got.items():
                    gk[b].add(o)
                if want == got:
                    verdict = 'bcd-equal'
                elif any(wk[b] != gk[b] for b in wk if b in gk):
                    verdict = 'offset-differs'     # a base type both sides have, at different offsets: a statement about layout
                else:
                    verdict = 'chain-differs'      # different base sets, common bases at equal offsets: the decomp's class is not the exe's
                if want != got:
                    detail = '%s | %s' % (sorted((want - got).items()), sorted((got - want).items()))
            sel[(k, h)] = (k, h, r.size, len(r.bases), sum(1 for b, _o, _f in r.bases if is_empty(b, recs, memo)),
                           max([d for _b, _o, d in subs] or [0]) if subs else '-', ','.join(map(str, eo)), 'rtti' if td is not None else '-', verdict, detail,
                           os.path.basename(f))
    counts = collections.Counter(v[8] for v in sel.values())
    nonzero = collections.Counter(v[8] for v in sel.values() if any(x != '0' for x in v[6].split(',') if x))
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, 'selected.tsv'), 'w') as f:
        f.write('key\tfingerprint\tsize\tdirect_bases\tdirect_empty_bases\tmax_depth\tempty_base_offsets\trtti\tverdict\tdetail\tfirst_object\n')
        for r in sorted(sel.values()):
            f.write('\t'.join(str(x) for x in r) + '\n')
    summary = {'objects': nobj, 'objects_without_types': notypes, 'unnamed_keys_left_out': unnamed, 'records_over_objects': nrec,
               'empty_records_over_objects': nempty, 'selected_classes': len({k for k, _h in sel}), 'selected_class_variants': len(sel),
               'keys_with_several_variants': sum(1 for v in variants.values() if len(v) > 1), 'verdicts': dict(counts), 'with_an_empty_base_at_a_nonzero_offset': dict(nonzero)}
    if a.r7:
        r7 = {}
        for ln in open(a.r7):
            f = ln.rstrip('\n').split('\t')
            if len(f) >= 3:
                r7[(f[0], f[1])] = f[2]
        b = collections.Counter()
        with open(os.path.join(a.out, 'gcc_vs_cl.tsv'), 'w') as g:
            for v in sorted(sel.values()):
                res = r7.get((v[10][:-4] if v[10].endswith('.obj') else v[10], v[0]), 'no-gcc-row')
                b[res] += 1
                g.write('%s\t%s\t%s\n' % (v[0], v[10], res))
        summary['gcc_vs_cl'] = dict(b)
    json.dump(summary, open(os.path.join(a.out, 'summary.json'), 'w'), indent=1, sort_keys=True)
    print(json.dumps(summary, indent=1, sort_keys=True))
    bad = summary.get('gcc_vs_cl', {}).get('differs', 0)
    return 0 if counts['offset-differs'] == 0 and not bad else 1


if __name__ == '__main__':
    sys.exit(main())
