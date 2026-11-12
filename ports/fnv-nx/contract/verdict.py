#!/usr/bin/env python3
"""verdict - the L1 verdict: every whole-binary gate on one link, one line each, green or red (owner rule:
a merge into the clean branch needs a green verdict; budget 5 minutes, enforced by the L4 verdict).

    verdict.py --exe FalloutNV.exe --units units.json --msvc <pristine objects> --link <link dir> --universe <L3 universe.tsv>
                [--nm <aarch64 nm> --readelf <aarch64 readelf>: both required] --work <dir>

<link dir> holds fixed/*.o (the game objects are src_*.o), out/addrmap.tsv, out/aliases.ld, guest/fnvnx.elf.
Stages run in order; each is a pure function of its listed inputs (no fuzz, boot or scenario output is read):
  exefacts  N1  objfacts  gates (data, call, vcall, xc)  xunit  rtti  pcrel  bodysel
Each stage also writes its gate file to $GATE_OUT when that is set (gatefile.py): n1, n2_data, n2_call, n2_vcall, xc, xunit,
rtti, pcrel, bodysel - the gate set of the L4 merge verdict (verify/verdict.py), which measures the time budget from
outside; this runner keeps no clock.
Red when: N1's fixed point drops a function for a symbol conflict; a registry gate has a row that BODYSEL does
not turn into LIFTED (never, by construction: reported as rows); xunit, rtti or pcrel count is not 0; the
selector is not total.  Prints one line per stage; exit status 1 when red.
"""
import argparse, glob, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    for k in ('exe', 'units', 'msvc', 'link', 'universe', 'nm', 'readelf', 'work'):
        ap.add_argument('--' + k, required=True)
    a = ap.parse_args()
    w = a.work
    os.makedirs(os.path.join(w, 'game'), exist_ok=True)
    for p in sorted(glob.glob(os.path.join(a.link, 'fixed', 'src_*.o'))):
        d = os.path.join(w, 'game', os.path.basename(p))
        if not os.path.lexists(d):
            os.symlink(os.path.abspath(p), d)
    py = lambda s, *x: [sys.executable, os.path.join(HERE, s)] + list(x)
    stages = [
        ('exefacts', py('exefacts.py', '--exe', a.exe, '--units', a.units, '--out', w + '/facts.json'), False),
        ('n1', py('n1.py', '--exe', a.exe, '--units', a.units, '--msvc', a.msvc, '--out', w + '/n1.json'), False),
        ('objfacts', py('objfacts.py', '--link', a.link, '--facts', w + '/facts.json', '--out', w + '/obj.json'), False),
        ('gates', py('registry.py', '--facts', w + '/facts.json', '--obj', w + '/obj.json', '--n1', w + '/n1.json',
                     '--link', a.link, '--out', w + '/gates'), False),
        ('xunit', py('gate_xunit.py', '--objects', w + '/game', '--nm', a.nm), True),
        ('rtti', py('gate_rtti.py', '--objects', w + '/game', '--readelf', a.readelf), True),
        ('pcrel', py('gate_pcrel.py', '--elf', os.path.join(a.link, 'guest', 'fnvnx.elf'), '--objects', os.path.join(a.link, 'fixed'),
                      '--work', w + '/pcrel', '--readelf', a.readelf, '--nm', a.nm), True),
        ('bodysel', py('bodysel.py', '--universe', a.universe, '--n1', w + '/n1.json', '--gates', w + '/gates',
                       '--out', w + '/bodysel.tsv'), False),
    ]
    red = []
    for name, cmd, gate in stages:
        r = subprocess.run(cmd, capture_output=True, text=True)
        ok = r.returncode == 0
        if not ok:
            red.append(name)
        tail = (r.stdout.strip().splitlines() or [''])[-1][:110]
        print('%-8s %s  %s' % (name, 'green' if ok else ('RED' if gate else 'FAILED'), tail))
    print('L1 verdict: %s%s' % ('RED' if red else 'GREEN', '  red: ' + ', '.join(red) if red else ''))
    sys.exit(1 if red else 0)


if __name__ == '__main__':
    main()
