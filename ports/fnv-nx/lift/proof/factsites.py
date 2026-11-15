#!/usr/bin/env python3
"""factsites: the site universe of the #UD classification (sdmfeat.py rows/partition), derived from L3a's code universe and from nothing else.

    python3 -I factsites.py --exe FalloutNV.exe --modules modules.tsv --dir DIR [--dir DIR ...] --facts FACTS_DIR --out SITES.tsv

Rule (one source, CLAUDE.md "defects impossible by construction" 1, 2, 4): a site is an instruction that the lift facts decode. The decoded instructions of an image are the nodes of
L3a's graph over the facts (tools/udcert.py Graph: the facts' ranges.tsv, recursive descent from every entry, one decoder); this tool imports that graph and adds only the shape key
and family of each node (proof/shape.py, the census's own functions). The image set is the loader's module table (imageset.py). A site list that is not this one cannot hold an address
that the facts do not decode, so the certificate's U-NODEC row class is empty by construction. No lifted pack, no marker file and no sweep of ours is read.
Facts layout: FACTS_DIR/{ranges,universe}.tsv for the exe, FACTS_DIR/<module>/{ranges,universe}.tsv for a module (addresses at the image's load base).
SITES.tsv: `module:address<TAB>shape key<TAB>family` (the format sdmfeat.py reads); header line with the sha256 of every input."""
import argparse
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools')); sys.path.insert(0, HERE)
import imageset  # noqa: E402
from proof import shape  # noqa: E402
import udcert as U  # noqa: E402


def sha(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest()


def image_sites(pe_path, facts):
    """-> [(address, shape key, family)] of every node of the facts' graph of the image, sorted by address"""
    base, ranges = U.load_ranges(os.path.join(facts, 'ranges.tsv'))
    uni = U.load_universe(os.path.join(facts, 'universe.tsv'))
    g = U.Graph(U.Pe(pe_path, base), ranges, uni, lambda i: False)
    md = shape.decoder(); out = []
    for a in sorted(g.ins):
        i = next(iter(md.disasm(g.pe.read(a, 15), a, 1)))
        out.append((a, shape.shape_key(i), shape.family(i)))
    return out


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', required=True); ap.add_argument('--modules', required=True); ap.add_argument('--dir', action='append', required=True)
    ap.add_argument('--facts', required=True); ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    ims = imageset.images(a.exe, a.modules, a.dir)
    rows, shas = [], []
    for name, path, sub in ims:
        fd = a.facts if sub is None else os.path.join(a.facts, sub)
        s = image_sites(path, fd)
        rows += [('%s:%08x' % (name, x), k, f) for x, k, f in s]
        shas.append('%s:%s:%s' % (name, sha(path)[:16], sha(os.path.join(fd, 'ranges.tsv'))[:16]))
        sys.stdout.write('%s: %d instructions\n' % (name, len(s))); sys.stdout.flush()
    with open(a.out, 'w', newline='\n') as fh:
        fh.write('# fnvnx-factsites/1 images=%d sites=%d modules_sha256=%s inputs=%s\n' % (len(ims), len(rows), sha(a.modules), ' '.join(shas)))
        fh.write('# module:address\tshape\tfamily\n')
        for r in rows:
            fh.write('%s\t%s\t%s\n' % r)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
