#!/usr/bin/env python3
"""Check: the lifted set of tools/lifted.py equals what liftgen lifted, byte for byte at the census: the per-shape counts of lifted() are exactly
the rows of liftgen's shapes_K.tsv of the same image (K = 0, one shard).
    FNVNX_EXE=IMAGE FNVNX_BASE=.. FNVNX_UNIVERSE=universe.tsv python3 -I lifted_vs_shapes.py SHAPES_0.tsv EMIT JTAB      (EMIT: '' or dsl, as liftgen --emit; JTAB: ehtables.json or '')
The proof is liftgen's own output: the shapes file is written by liftgen through lifted.lift_with_instructions, lifted() runs the same step again and counts
shape_key/family of the instructions it yields (decoded again from the image bytes at (va, length))."""
import collections, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(HERE)
for p in ('tools', ''):
    sys.path.insert(0, os.path.join(LIFT, p))
import fnvlift as L, lifted
from proof import shape


def main(argv):
    shapes, emit, jtab = argv[0], argv[1], argv[2] or None
    img = L.Image(os.environ['FNVNX_EXE'])
    got = lifted.lifted(img, os.environ['FNVNX_UNIVERSE'], emit, jtab)
    cnt = collections.Counter(); n = 0
    for va, ln in got:
        ins = next(iter(L.core.md.disasm(img.bytes(va, 16), va, 1)), None)
        if ins is None:
            from fnvlift import f_sys
            ins = f_sys.nop_gap(img.bytes(va, 16))
        assert ins.size == ln, (hex(va), ins.size, ln)
        cnt[(shape.shape_key(ins), shape.family(ins))] += 1; n += 1
    mine = ''.join('\t'.join(map(str, x)) + '\n' for x in [(k, fam, c) for (k, fam), c in sorted(cnt.items(), key=lambda t: (t[0][0].encode(), t[0][1]))])
    want = open(shapes).read()
    ok = mine == want
    print('lifted instructions %d, shape rows %d: %s' % (n, len(cnt), 'EQUAL to ' + shapes if ok else 'DIFFERENT from ' + shapes))
    if not ok:
        a, b = set(mine.splitlines()), set(want.splitlines())
        for l in sorted(a ^ b)[:10]: print(('only lifted(): ' if l in a else 'only shapes: ') + l)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
