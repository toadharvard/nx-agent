"""rangecheck.py FACTS_DIR SITES... : where does each named code site lie? A site list is lines `ADDR [text]` (hex; for a module the address at the PREFERRED base, translated by the universe.tsv header
`base=` / `preferred base`). Classes: in-range (an entry's ranges.tsv row covers the byte), reached-no (bytes no entry reaches: the codemap class of the byte is printed), outside-image.
Exit 1 if any site is not in-range. A site that is not in a range is never added to a range: it is code no entry reaches, and the answer is the codemap class of its bytes (tools/residue.py)."""
import sys, re, bisect, os
def load(d):
    hd = open(d + '/universe.tsv').readline(); m = re.search(r'base=0x([0-9a-f]+)', hd); p = re.search(r'preferred image base|preferred base 0x([0-9a-f]+)', hd)
    base = int(m.group(1), 16); pref = int(p.group(1), 16) if p and p.group(1) else base
    R = []
    for l in open(d + '/ranges.tsv'):
        if l[0] == '#': continue
        e, r = l.rstrip('\n').split('\t')[:2]
        for x in r.split(','):
            if x: lo, hi = (int(y, 16) for y in x.split('-')); R.append((lo, hi, int(e, 16)))
    R.sort(); C = []
    for l in open(d + '/codemap.tsv'):
        if l[0] == '#': continue
        p_ = l.rstrip('\n').split('\t'); C.append((int(p_[0], 16), int(p_[1]), p_[2]))
    C.sort(); return base, pref, R, C
def classify(d, sites):
    base, pref, R, C = load(d); los = [x[0] for x in R]; cs = [x[0] for x in C]; out = []
    for a0 in sites:
        a = a0 - pref + base; i = bisect.bisect_right(los, a) - 1; hit = None
        for j in range(max(0, i - 40), i + 1):         # ranges overlap between entries: scan back over the neighbours that can still cover a
            if R[j][0] <= a < R[j][1]: hit = R[j][2]; break
        if hit is not None: out.append((a0, a, 'in-range', '%08x' % hit)); continue
        k = bisect.bisect_right(cs, a) - 1
        cl = C[k][2] if k >= 0 and a < C[k][0] + C[k][1] else 'outside-image'
        out.append((a0, a, 'not-in-range', cl))
    return out
if __name__ == '__main__':
    d = sys.argv[1]; sites = [int(l.split()[0], 16) for f in sys.argv[2:] for l in open(f) if l.strip() and l[0] != '#']
    r = classify(d, sites); bad = 0
    for a0, a, st, x in r:
        print('%08x -> %08x %s %s' % (a0, a, st, x)); bad += st != 'in-range'
    print('sites %d, in range %d, not in range %d' % (len(r), len(r) - bad, bad)); sys.exit(1 if bad else 0)
