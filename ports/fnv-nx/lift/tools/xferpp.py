#!/usr/bin/env python3
"""The preprocessed text of the same units of two lifted packs must have the same tokens (rule 2: the control-transfer forms expand to the statements they replace).
Both packs are built by the ONE build, verify/recipe.py, on the root lift/pack, with the units this tool selects and PP only (the recipe's `--units FILE --prof-pp-only`); this tool calls no compiler:
    xferpp.py select PACK --modules MODULES_TSV --per-image K --out UNITS_FILE
        the units to preprocess: for each image, the K units of PACK (u/NNN.cpp) with the most transfer sites (the forms of rt/fnvl.h plus the legacy sequences, xfertally), ties by unit name; the union, one per line
    recipe.py build --root ports/fnv-nx/lift/pack --store S1 --out O1 --ext E --units UNITS_FILE --prof-pp-only      (the pack external pinned to the before pack)
    recipe.py build --root ports/fnv-nx/lift/pack --store S2 --out O2 --ext E --units UNITS_FILE --prof-pp-only      (the pack external pinned to the after pack)
    xferpp.py diff --repo REPO --before-out O1 --before-store S1 --after-out O2 --after-store S2 --units UNITS_FILE
        for each unit of the file: the unit.ii of the PP stage of both builds (objects.json pp_keys -> unitstage.result_file), line markers dropped, compared token by token. Exit 0 iff every unit
        has a PP result in both builds and the token streams are equal; a unit without a result or with different tokens is listed and exit 1.
The unit file is the same for both builds: the selection comes from one pack and applies to the other by unit name (the rule u/<(A >> 4) & 0xfff>.cpp holds for every pack)."""
import argparse, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import xfertally

TOK = re.compile(r'[A-Za-z_0-9]+|->|!=|==|<=|>=|&&|\|\||<<|>>|\S')


def tokens(ii):
    """the tokens of a preprocessed text without its line markers (`# N "file" flags` lines, whose numbers depend on the size of the text above them)"""
    return TOK.findall('\n'.join(l for l in ii.split('\n') if not re.match(r'# \d+ "', l)))


def select(pack, rows, per_image):
    score = {}
    for f in xfertally.units(pack):
        imgs = {}
        import collections
        imgs = collections.defaultdict(xfertally.new_image)
        xfertally.tally_text(open(f, encoding='latin1').read(), rows, imgs)
        for im, r in imgs.items():
            if im != 'unit_head': score.setdefault(im, {})['u/' + os.path.splitext(os.path.basename(f))[0] + '.cpp'] = sum(r['forms'].values()) + sum(r['legacy'].values())
    pick = set()
    for im, d in score.items(): pick.update(u for u, n in sorted(d.items(), key=lambda t: (-t[1], t[0]))[:per_image] if n)
    return sorted(pick)


def ii_of(repo, out, store, unit):
    sys.path.insert(0, os.path.join(repo, 'verify'))
    import unitstage
    res = json.load(open(os.path.join(out, 'objects.json')))
    key = res.get('pp_keys', {}).get(unit)
    return None if key is None else open(unitstage.result_file(store, key, 'unit.ii'), encoding='latin1').read()


def diff(repo, bo, bs, ao, as_, units):
    bad = []
    for u in units:
        b, a = ii_of(repo, bo, bs, u), ii_of(repo, ao, as_, u)
        if b is None or a is None: bad.append((u, 'no PP result in the %s build' % ('before' if b is None else 'after'))); continue
        tb, ta = tokens(b), tokens(a)
        if tb != ta:
            i = next((k for k, (x, y) in enumerate(zip(tb, ta)) if x != y), min(len(tb), len(ta)))
            bad.append((u, 'tokens differ at %d: before %s / after %s (%d vs %d tokens)' % (i, ' '.join(tb[i:i + 8]), ' '.join(ta[i:i + 8]), len(tb), len(ta))))
        else: print('%s: %d tokens equal' % (u, len(tb)))
    for u, why in bad: print('DIFF %s: %s' % (u, why))
    print('units %d, equal %d, differ %d' % (len(units), len(units) - len(bad), len(bad)))
    return 1 if bad else 0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0]); sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('select'); s.add_argument('pack'); s.add_argument('--modules', required=True); s.add_argument('--per-image', type=int, required=True); s.add_argument('--out', required=True)
    d = sub.add_parser('diff')
    for n in ('repo', 'before-out', 'before-store', 'after-out', 'after-store', 'units'): d.add_argument('--' + n, required=True)
    a = ap.parse_args(argv)
    if a.cmd == 'select':
        u = select(a.pack, xfertally.modules(a.modules), a.per_image); open(a.out, 'w').write(''.join(x + '\n' for x in u)); print('%d units' % len(u)); return 0
    return diff(a.repo, a.before_out, a.before_store, a.after_out, a.after_store, [l.strip() for l in open(a.units) if l.strip()])


if __name__ == '__main__': sys.exit(main(sys.argv[1:]))
