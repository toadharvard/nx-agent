#!/usr/bin/env python3
"""Census of the emission paths: per image and path, the distinct shape keys and the sites (instruction count), and the share of the total.
    pathcensus.py DIR [DIR ...]      DIR holds the shard outputs of one image (shapes_<K>.tsv rows: shape key, family, count, path; liftgen --shard)
Prints one table per image, then the total over all given images. Reads only; every number is a count over the whole file set."""
import collections, glob, os, sys


def load(d):
    keys, sites = collections.defaultdict(set), collections.Counter()
    for f in sorted(glob.glob(d + '/shapes_*.tsv')):
        for l in open(f):
            k, fam, n, path = l.rstrip('\n').split('\t')
            keys[path].add(k); sites[path] += int(n)
    return keys, sites


def show(name, keys, sites):
    tk = len(set().union(*keys.values())) if keys else 0; ts = sum(sites.values())
    print('%s: shape keys %d, sites %d' % (name, tk, ts))
    for p in sorted(sites, key=lambda p: (-sites[p], p)):
        print('  %-34s keys %7d (%5.1f%%)  sites %9d (%5.1f%%)' % (p, len(keys[p]), 100.0 * len(keys[p]) / max(1, tk), sites[p], 100.0 * sites[p] / max(1, ts)))


def main(dirs):
    allk, alls = collections.defaultdict(set), collections.Counter()
    for d in dirs:
        k, s = load(d); show(os.path.basename(d.rstrip('/')), k, s)
        for p in s: allk[p] |= {(d, x) for x in k[p]}; alls[p] += s[p]
    show('TOTAL (keys counted per image)', allk, alls)


if __name__ == '__main__':
    main(sys.argv[1:])
