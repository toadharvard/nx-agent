#!/usr/bin/env python3
"""Lay out the hand-written replacements of shaders/native.tsv as an nx_render --native directory.

    python3 -I native_dir.py <built shaders dir (build.py output)> <dir with the player's shaderpackage*.sdp>
                             <translated dir (sdp2dksh -o <dir> --glsl: <hash>.json)> <out dir>

For every program in the player's packages whose name is in native.tsv, <out>/<hash>.glsl is the native GLSL (the
expanded .glsl build.py wrote). A program is taken only when its constant table (from sdp2dksh's <hash>.json) is
exactly the one the row assumes, so a package whose register layout differs stays on the translated path. The
output names hashes of the player's files: it stays local (MACHINES.md), like the packages.
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nxd9_census  # noqa: E402

TSV = os.path.join(HERE, '..', 'shaders', 'native.tsv')


def rows(path=TSV):
    out = {}
    for line in open(path):
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        orig, native, ctab = line.split()
        want = set()
        for item in ctab.split(','):
            name, _, reg = item.partition('@')
            reg, _, cnt = reg.partition('x')
            want.add((name, int(reg), int(cnt or 1)))
        out[orig.upper()] = (native, want)
    return out


def main(argv):
    if len(argv) != 4:
        raise SystemExit(__doc__)
    built, sdp, tr, out = argv
    os.makedirs(out, exist_ok=True)
    table = rows()
    names = nxd9_census.sdp_names(sdp)
    taken, refused = 0, []
    for h, ns in sorted(names.items()):
        hits = sorted({table[n.upper()] [0] for n in ns if n.upper() in table})
        if not hits:
            continue
        if len(hits) > 1:
            refused.append((h, ns, 'names map to different natives %s' % hits))
            continue
        native = hits[0]
        want = next(table[n.upper()][1] for n in ns if n.upper() in table)
        jp = os.path.join(tr, '%016x.json' % h)
        if not os.path.exists(jp):
            refused.append((h, ns, 'no translation json'))
            continue
        have = {(c['name'], c['reg'], c['count']) for c in json.load(open(jp))['ctab']}
        if have != want:
            refused.append((h, ns, 'constant table differs'))
            continue
        src = os.path.join(built, native + '.glsl')
        open(os.path.join(out, '%016x.glsl' % h), 'w').write(open(src).read())
        taken += 1
    print('native_dir: %d programs native, %d refused' % (taken, len(refused)))
    for h, ns, why in refused:
        print('  refused %s: %s' % ('/'.join(sorted(ns))[:60], why))


if __name__ == '__main__':
    main(sys.argv[1:])
