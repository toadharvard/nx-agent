#!/usr/bin/env python3
"""Rule R12 gate for tools/memclass.py: the stage is a pure function of its declared inputs (exe, facts root), so three runs that differ in everything
the result must not depend on give byte-identical outputs: hash seed 0 / 1 / random x process count 2 / 1 / 3.
    memclass_det.py [--facts DIR] [--out DIR] [--exe PATH]      (default facts /home/user/l3facts2, out <facts>/memclass_det)
Prints sha256 of entries.tsv, plain_insns.bin, class_totals.tsv per run; exit 0 iff all three runs agree on all three files."""
import argparse, hashlib, os, random, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); TOOL = os.path.join(os.path.dirname(HERE), 'tools', 'memclass.py')
FILES = ('entries.tsv', 'plain_insns.bin', 'class_totals.tsv')


def main():
    ap = argparse.ArgumentParser(); A = ap.add_argument
    A('--facts', default=os.environ.get('FNVNX_FACTS', '/home/user/l3facts2')); A('--out'); A('--exe', default=os.environ.get('FNVNX_EXE', '/home/user/game/FalloutNV.exe'))
    a = ap.parse_args(); out = a.out or a.facts + '/memclass_det'
    runs = [('0', 2), ('1', 1), (str(random.SystemRandom().randrange(2, 2 ** 31)), 3)]       # PYTHONHASHSEED, processes
    sums = []
    for k, (seed, procs) in enumerate(runs):
        d = '%s/run%d' % (out, k); os.makedirs(d, exist_ok=True)
        env = dict(os.environ, PYTHONHASHSEED=seed, FNVNX_PROCS=str(procs), FNVNX_FACTS=a.facts, FNVNX_MEMCLASS_OUT=d, FNVNX_EXE=a.exe)
        with open(d + '/stdout.log', 'w') as lg:
            subprocess.run([sys.executable, TOOL], env=env, stdout=lg, stderr=subprocess.STDOUT, check=True)
        sums.append([hashlib.sha256(open('%s/%s' % (d, f), 'rb').read()).hexdigest() for f in FILES])
        print('run %d  PYTHONHASHSEED=%s procs=%d  ' % (k, seed, procs) + '  '.join('%s %s' % (f, h[:16]) for f, h in zip(FILES, sums[-1])), flush=True)
    ok = all(s == sums[0] for s in sums)
    print('memclass_det: %s (3 runs, hash seeds %s, processes %s)' % ('PASS' if ok else 'FAIL', '/'.join(r[0] if r[0] in '01' else 'random' for r in runs), '/'.join(str(r[1]) for r in runs)))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
