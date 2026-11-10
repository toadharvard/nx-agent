#!/usr/bin/env python3
"""gate_xunit - no name of the game binds across a unit boundary (condition of N1's per-unit symbol scope).

    gate_xunit.py --objects <dir of game objects> --nm <aarch64 nm> [--generated <dir of generated/platform objects>]

N1 proves `symbol -> address` is a function inside one unit.  That is only usable if the link resolves a
game name inside its unit, by address, and never globally by name.  So over the game objects:
  merged      a global or weak name defined in two or more units (the linker would keep one definition);
  referenced  a name defined in one unit and undefined in another (the linker would bind them by name).
By-address symbols (`__fnv_<address>` and the generated `__fnvnx_*`) are not names of the game.  A name
defined only in generated/platform objects is the platform's interface and is not counted.
Gate: merged == 0 and referenced == 0.  Exit status 1 otherwise.  Counts only on stdout.
Gate file `xunit` ($GATE_OUT/xunit.json, gatefile.py): universe = the game names defined in the objects, certified =
universe - merged - referenced, uncertified = the offending names (sorted).
"""
import argparse, collections, glob, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gatefile

LINE = re.compile(r'(\S+?\.o):(?:[0-9a-f]+ )?\s*(\S) (\S+)$')


def scan(files, nm):
    defs, undef = collections.defaultdict(set), collections.defaultdict(set)
    for i in range(0, len(files), 200):
        out = subprocess.run([nm, '-A', '-g'] + files[i:i + 200], capture_output=True, text=True).stdout
        for ln in out.splitlines():
            m = LINE.match(ln)
            if m and not m[3].startswith(('__fnv_', '__fnvnx')):
                (undef if m[2] in 'Uwv' else defs)[m[3]].add(m[1])
    return defs, undef


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--objects', required=True)
    ap.add_argument('--generated')
    ap.add_argument('--nm', required=True, help='path of the aarch64 nm (no PATH lookup)')
    a = ap.parse_args()
    defs, undef = scan(sorted(glob.glob(os.path.join(a.objects, '*.o'))), a.nm)
    c = collections.Counter()
    bad = []
    for name, obs in sorted(defs.items()):
        if len(obs) > 1:
            c['merged'] += 1
            bad.append(name)
        elif undef.get(name, set()) - obs:
            c['referenced'] += 1
            bad.append(name)
        else:
            c['global, unused across units'] += 1
    for k in ('merged', 'referenced', 'global, unused across units'):
        print('%7d  %s' % (c[k], k))
    gatefile.write('xunit', len(defs), len(defs) - len(bad), bad,
                   'merged %d, referenced %d, global unused across units %d' % (
                       c['merged'], c['referenced'], c['global, unused across units']))
    sys.exit(1 if c['merged'] or c['referenced'] else 0)


if __name__ == '__main__':
    main()
