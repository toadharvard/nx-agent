#!/usr/bin/env python3
"""verdict - the L1 verdict: every whole-binary gate on one link, one line each, green or red (owner rule:
a merge into the clean branch needs a green verdict; budget 5 minutes).

    verdict.py --exe FalloutNV.exe --units units.json --msvc <pristine objects> --link <link dir> --work <dir>

<link dir> holds fixed/*.o (the game objects are src_*.o), out/addrmap.tsv, out/aliases.ld, guest/fnvnx.elf.
Stages run in order; each is a pure function of its listed inputs (no fuzz, boot or scenario output is read):
  exefacts  N1  objfacts  gates (data, call, vcall, xc)  xunit  rtti  pcrel  bodysel
Red when: N1's fixed point drops a function for a symbol conflict; a registry gate has a row that BODYSEL does
not turn into LIFTED (never, by construction: reported as rows); xunit, rtti or pcrel count is not 0; the
selector is not total.  Prints the seconds of every stage and the total; exit status 1 when red or over budget.
"""
import argparse, glob, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
BUDGET = 300


def main():
    ap = argparse.ArgumentParser()
    for k in ('exe', 'units', 'msvc', 'link', 'work'):
        ap.add_argument('--' + k, required=True)
    a = ap.parse_args()
    w = a.work
    os.makedirs(os.path.join(w, 'game'), exist_ok=True)
    for p in glob.glob(os.path.join(a.link, 'fixed', 'src_*.o')):
        d = os.path.join(w, 'game', os.path.basename(p))
        if not os.path.lexists(d):
            os.symlink(os.path.abspath(p), d)
    py = lambda s, *x: [sys.executable, os.path.join(HERE, s)] + list(x)
    stages = [
        ('exefacts', py('exefacts.py', '--exe', a.exe, '--units', a.units, '--out', w + '/facts.pkl'), False),
        ('n1', py('n1.py', '--exe', a.exe, '--units', a.units, '--msvc', a.msvc, '--out', w + '/n1.pkl'), False),
        ('objfacts', py('objfacts.py', '--link', a.link, '--facts', w + '/facts.pkl', '--out', w + '/obj.pkl'), False),
        ('gates', py('registry.py', '--facts', w + '/facts.pkl', '--obj', w + '/obj.pkl', '--n1', w + '/n1.pkl',
                     '--link', a.link, '--out', w + '/gates'), False),
        ('xunit', py('gate_xunit.py', '--objects', w + '/game'), True),
        ('rtti', py('gate_rtti.py', '--objects', w + '/game'), True),
        ('pcrel', py('gate_pcrel.py', '--elf', os.path.join(a.link, 'guest', 'fnvnx.elf'), '--objects', os.path.join(a.link, 'fixed')), True),
        ('bodysel', py('bodysel.py', '--facts', w + '/facts.pkl', '--n1', w + '/n1.pkl', '--gates', w + '/gates',
                       '--out', w + '/bodysel.tsv'), False),
    ]
    red, total = [], 0.0
    for name, cmd, gate in stages:
        t = time.time()
        r = subprocess.run(cmd, capture_output=True, text=True)
        dt = time.time() - t
        total += dt
        ok = r.returncode == 0
        if not ok:
            red.append(name)
        tail = (r.stdout.strip().splitlines() or [''])[-1][:110]
        print('%-8s %5.1fs  %s  %s' % (name, dt, 'green' if ok else ('RED' if gate else 'FAILED'), tail))
    if total > BUDGET:
        red.append('budget')
    print('L1 verdict: %s  (%.0f s of %d)%s' % ('RED' if red else 'GREEN', total, BUDGET, '  red: ' + ', '.join(red) if red else ''))
    sys.exit(1 if red else 0)


if __name__ == '__main__':
    main()
