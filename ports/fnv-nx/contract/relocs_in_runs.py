#!/usr/bin/env python3
"""relocs_in_runs.py - the census of the data-in-code class (L1 CONTRACT).

    relocs_in_runs.py --image NAME=path ... --facts DIR --facts-tree SHA [--list]

For every image: the HIGHLOW relocations inside a run of the function facts (`in_run`), and of those the words that are NOT the immediate or the
displacement of a decoded instruction (imagerec.ImageRec.word raises CodeRead for them: bytes that are data, such as jump tables, inside a code
run), with the merged runs that hold them. Exit 1 when any word is left; the acceptance of the data-in-code fact is 0 words for every image.
--list prints every word as `image<TAB>va<TAB>run start<TAB>run end`.
"""
import argparse
import bisect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import imagerec


def census(rec):
    inrun, words = 0, []
    for r in sorted(rec.relocs):
        if rec.in_run(r, 4):
            inrun += 1
            try:
                rec.word(r)
            except imagerec.CodeRead:
                words.append(r)
    runs = sorted({tuple(rec._merged[bisect.bisect_right(rec._starts, w) - 1]) for w in words})
    return len(rec.relocs), inrun, words, runs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--image', action='append', required=True, help='NAME=image path')
    ap.add_argument('--facts', required=True)
    ap.add_argument('--facts-tree', required=True)
    ap.add_argument('--list', action='store_true')
    a = ap.parse_args(argv)
    tw = tr = 0
    for nm, rec in imagerec.load_images(a.image, a.facts, a.facts_tree).items():
        n, inrun, words, runs = census(rec)
        print('%s\trelocs %d\tin runs %d\twords not an operand %d\truns %d' % (nm, n, inrun, len(words), len(runs)))
        if a.list:
            for w in words:
                k = bisect.bisect_right(rec._starts, w) - 1
                print('%s\t%x\t%x\t%x' % (nm, w, rec._merged[k][0], rec._merged[k][1]))
        tw += len(words)
        tr += len(runs)
    print('total\twords %d\truns %d' % (tw, tr))
    return 1 if tw else 0


if __name__ == '__main__':
    sys.exit(main())
