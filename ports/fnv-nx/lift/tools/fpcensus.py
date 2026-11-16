#!/usr/bin/env python3
"""Item 29 (a)(b)(c) census over one image's fpstate facts (tools/fpstate.py scan has written <facts>/fpstate/*.tsv).

    fpcensus.py <facts dir>        -> <facts dir>/fpstate/census.tsv, counts on stdout

(a) FPMASK, per control-word load site.  Value forms of fcw_loads.tsv / mxcsr_loads.tsv: c:V constant, t:A:O new=(cur&A)|O, u:reason unknown.
    The mask field is FCW bits 0-5 (0x3f), MXCSR bits 7-12 (0x1f80).  The start word has the field all-ones (FCW 0x027F, MXCSR 0x1F80).
    By induction over the loads (the base case is the start, the step is each load's new field given a field all-ones): the field of a constant is
    V&M; of a transform (A|O)&M, since cur&M = M.  proven-masked iff the field is M, proven-unmasking iff it is not M, unproven (reason) for u:.
    A single proven-unmasking or unproven load makes the image's I-FPMASK not hold; the fixed point R is fpstate.py's.
(b) FPSTICKY, per reader row of readers.tsv (class `listed` = a clear instruction fnclex, not a reader): cc-only (observes C0-C3 only, no sticky bit flows out) or escapes (with the reason column).
(c) RC at import call sites: the rounding-control field (FCW bits 10-11, MXCSR bits 13-14) over the reachable words is the scan's RC_X87 / RC_SSE row;
    every row of impcalls.tsv inherits it (the reachable-word set is image-wide: a context-insensitive over-approximation)."""
import os, sys
from collections import Counter


def rows(p):
    return [l.rstrip('\n').split('\t') for l in open(p)] if os.path.exists(p) else []


def classify(val, M):
    """a site may carry several alternative values (space separated): unproven if any is unknown, else proven-unmasking if any alternative is, else proven-masked"""
    cs = [classify1(v, M) for v in val.split()]
    for want in ('unproven', 'proven-unmasking'):
        for c, why in cs:
            if c == want:
                return c, why
    return 'proven-masked', ''


def classify1(val, M):
    k = val.split(':')
    if k[0] == 'c':
        f = int(k[1], 16) & M
    elif k[0] == 't':
        f = (int(k[1], 16) | int(k[2], 16)) & M
    else:
        return 'unproven', k[1] if len(k) > 1 else ''
    return ('proven-masked' if f == M else 'proven-unmasking'), ''


def main():
    d = os.path.join(sys.argv[1], 'fpstate')
    out = []; cnt = Counter()
    for f, M, tag in (('fcw_loads.tsv', 0x3f, 'FCW'), ('mxcsr_loads.tsv', 0x1f80, 'MXCSR')):
        for r in rows(os.path.join(d, f)):
            c, why = classify(r[3], M)
            out.append((r[0], 'FPMASK', tag, r[1], c, why)); cnt[('FPMASK', tag, c)] += 1
    for r in rows(os.path.join(d, 'readers.tsv')):
        cl = r[2]
        out.append((r[0], 'FPSTICKY', '-', r[1], cl, r[3])); cnt[('FPSTICKY', '-', cl)] += 1
    sc = {r[0]: r[1:] for r in rows(os.path.join(d, 'scan.tsv'))}
    imps = rows(os.path.join(d, 'impcalls.tsv'))
    for k in ('RC_X87', 'RC_SSE'):
        for r in imps:
            out.append((r[0], k, '-', r[1], sc.get(k, ['?'])[0], ''))
        cnt[(k, '-', sc.get(k, ['?'])[0])] = len(imps)
    with open(os.path.join(d, 'census.tsv'), 'w') as fh:
        for r in sorted(out):
            fh.write('\t'.join(r) + '\n')
    for k in sorted(cnt):
        print('%s %s %s %d' % (k + (cnt[k],)))
    print('I-FPMASK %s  I-FPSTICKY %s  import call sites %d' % (sc.get('I-FPMASK', ['?'])[0], sc.get('I-FPSTICKY', ['?'])[0], len(imps)))


main()
