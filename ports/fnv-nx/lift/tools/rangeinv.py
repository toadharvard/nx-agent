"""rangeinv.py FACTS_DIR IMAGE : the range-disjointness invariant of one image's facts (ranges.tsv).
Rule: within an image the decoded code ranges are consistent. Decode every range linearly from its start (a range is a run of adjacent instructions of one entry);
  (1) a range whose linear decode does not end exactly at its end has a start that is not an instruction boundary of the decode that reaches it (mid-instruction claim);
  (2) two ranges (of any entries) that share a byte must decode that byte as the same instruction (same start, same length): an overlap where the decodes differ is red.
Exit 1 when either count is not 0. Output: counts of overlapping range pairs with differing decodes and of mid-instruction claims, then the first offenders with the entry's kind (universe.tsv column 2).
The byte image is the PE file mapped by section; module addresses are at the assigned load base of the universe.tsv header."""
import sys, re, bisect
import pefile, capstone
def main(d, img):
    hd = open(d + '/universe.tsv').readline(); base = int(re.search(r'base=0x([0-9a-f]+)', hd).group(1), 16)
    pe = pefile.PE(img, fast_load=True); T0 = pe.OPTIONAL_HEADER.ImageBase
    data = pe.get_memory_mapped_image(); cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    kind = {}
    for l in open(d + '/universe.tsv'):
        if l[0] != '#': p = l.split('\t'); kind[int(p[0], 16)] = p[1]
    runs = []
    for l in open(d + '/ranges.tsv'):
        if l[0] == '#': continue
        e, r = l.rstrip('\n').split('\t')[:2]
        for x in r.split(','):
            if x: lo, hi = (int(y, 16) for y in x.split('-')); runs.append((lo, hi, int(e, 16)))
    runs = sorted(set(runs)); mid = []; ins = {}              # ins: start -> set of lengths (all decodes that claim it)
    cover = {}                                                # byte -> set of (start, len)
    for lo, hi, e in runs:
        a = lo - base; end = hi - base; pos = a
        while pos < end:
            i = next(cs.disasm(bytes(data[pos:pos + 15]), pos), None)
            if i is None: break
            for b in range(pos, pos + i.size): cover.setdefault(b, set()).add((pos, i.size))
            pos += i.size
        if pos != end: mid.append((lo, hi, e))
    bad = {}
    for b, s in cover.items():
        if len(s) > 1: bad.setdefault(frozenset(s), b)
    print('image %s: ranges %d, overlapping decodes that differ %d, mid-instruction range ends %d' % (img.split('/')[-1], len(runs), len(bad), len(mid)))
    for s, b in sorted(bad.items(), key=lambda x: x[1])[:10]: print('  overlap at %08x: %s' % (b + base, sorted(s)))
    for lo, hi, e in mid[:10]: print('  range %08x-%08x of entry %08x (%s) does not end on an instruction boundary' % (lo, hi, e, kind.get(e, '?')))
    return len(bad), len(mid)
if __name__ == '__main__':
    a, b = main(sys.argv[1], sys.argv[2]); sys.exit(1 if a or b else 0)
