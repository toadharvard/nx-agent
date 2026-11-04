#!/usr/bin/env python3
"""permcover.py - coverage-guided seeds for perm.py: the programs whose reference frame drew nothing (vacuous) are
built again with other seeds, and each keeps the first seed whose reference frame draws.

    permcover.py --sdp <pkg.sdp> --asm <s6b_asm> --sdp2dksh <sdp2dksh> --exe <nxd9play.exe> --prefix <wine prefix>
                 --manifest <perm dir>/manifest.json --ref <reference run> --out <salts.json> [--tries 6]

The selection looks at the reference only (does the frame draw anything), never at the candidate, so it does not
bias the comparison. A failed reference replay ends it with a non-zero exit and no output file, so a broken prefix
or a timeout is never cached as "unreached" (V1 SK-2). <salts.json> ({hash: salt}, plus "_unreached": hashes no
seed made draw) is passed to
`perm.py --salts`. It names the game's program hashes: keep it private with the captures.
"""
import argparse, json, os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from permreport import drew

CLEAR = (0x40, 0x40, 0x40)


def vacuous(man, ref):
    out = []
    for cap, lst in man['captures'].items():
        for e in lst:
            p = os.path.join(ref, cap, f"f{e['frame']}.png")
            if not os.path.isfile(p) or drew(p, CLEAR) == 0.0:
                out.append(e['hash'])
    return out


def main():
    ap = argparse.ArgumentParser()
    for k in ('sdp', 'asm', 'sdp2dksh', 'exe', 'prefix', 'manifest', 'ref', 'out'):
        ap.add_argument('--' + k, required=True)
    ap.add_argument('--tries', type=int, default=6)
    a = ap.parse_args()
    todo = vacuous(json.load(open(a.manifest)), a.ref)
    salts, left = {}, list(todo)
    with tempfile.TemporaryDirectory() as t:
        for salt in range(1, a.tries + 1):
            if not left: break
            sp = os.path.join(t, 'salts.json')
            json.dump({h: salt for h in left}, open(sp, 'w'))
            caps, run = os.path.join(t, f'c{salt}'), os.path.join(t, f'r{salt}')
            py = lambda *x: subprocess.run([sys.executable, '-I', *x], capture_output=True, text=True)
            r = py(os.path.join(HERE, 'perm.py'), 'build', '--sdp', a.sdp, '--asm', a.asm, '--sdp2dksh', a.sdp2dksh,
                   '--out', caps, '--only', ','.join(left), '--salts', sp)
            if r.returncode: sys.exit(f'permcover: perm.py: {r.stderr.strip()}')
            r = py(os.path.join(HERE, 'replay.py'), 'ref', '--exe', a.exe, '--prefix', a.prefix, '--out', run,
                   *sorted(os.path.join(caps, f) for f in os.listdir(caps) if f.endswith('.nxd9')))
            if r.returncode:   # V1 SK-2: a failed reference run says nothing about drawing; never write salts from it
                sys.exit(f'permcover: reference replay failed at salt {salt} (exit {r.returncode}): '
                         f'{(r.stdout + r.stderr).strip()[-300:]}')
            still = set(vacuous(json.load(open(os.path.join(caps, 'manifest.json'))), run))
            for h in left:
                if h not in still: salts[h] = salt
            left = [h for h in left if h in still]
            print(f'permcover: salt {salt}: {len(todo) - len(left)}/{len(todo)} drawing')
    salts['_unreached'] = left
    json.dump(salts, open(a.out, 'w'), indent=1)
    print(f'permcover: {len(todo)} vacuous, {len(todo) - len(left)} reached, {len(left)} unreached')
    return 0


if __name__ == '__main__':
    sys.exit(main())
