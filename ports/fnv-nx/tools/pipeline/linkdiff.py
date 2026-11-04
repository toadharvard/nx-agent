#!/usr/bin/env python3
"""linkdiff.py - compare the link-by-address verdicts of two fnvlink runs on the units they share (stream S9: does
the pipeline's link stage reproduce a hand-driven run, e.g. on a UNITS subset?).

    tools/pipeline/linkdiff.py <reference out dir> <candidate out dir> --objects REF.tsv CAND.tsv
                               [--units units.json] [--fixed REF_DIR CAND_DIR] [--show N]

For every unit that compiled in both runs (compile.sh objects.tsv) it compares the compiled object (by content),
the refs.tsv rows (class, tier, address, target, note of each (object, symbol)), the abi.tsv rows (verdict,
classes, moves, notes), resolve's per-object objcopy lists (args, redefine, rebind, undefine) and the rewritten
object (--fixed). The candidate is expected to be a subset of the reference, so some differences follow from the
units it does not have; they are counted per reason and not as errors:

  addr-outside    the address's definition is in an object the candidate does not link (reference addrmap.tsv),
                  or no unit defining the address (--units) compiled in the candidate: `code` -> `code-pending`,
                  no bridge, no D1/D2 alias, no import-thunk provider found in the defining unit, ...
  name-outside    the reference bound the name to a native definition by name, and no candidate object defines it
  dup-outside     `--localize-symbol` of a duplicate strong name the candidate defines only once
  vslot-host      `unresolved` -> `vslot-unresolved`: the reference emits the class's vtable in an object the
                  candidate lacks; the candidate synthesizes it, so the same slot reference now goes to a trap
  fixed           a rewritten object differs and its objcopy lists differ for one of the reasons above

Synthesized vtable objects (`<host unit>#vtables`) depend on which classes the whole set requests and are only
counted. ABI verdicts of references that have a definition in both runs must be identical. Prints counts and the
first unexplained differences; exit status 1 if there are any. Reads decomp-derived files at run time and writes
nothing. Needs `nm` for the target (NM, default aarch64-linux-gnu-nm) when --fixed is given.
"""
import argparse
import collections
import hashlib
import json
import os
import re
import subprocess
import sys

ADDR = re.compile(r'__fnv(?:nx_br)?_([0-9a-f]{8})(?![0-9a-f])')
NM = os.environ.get('NM', 'aarch64-linux-gnu-nm')


def sha(path):
    try:
        with open(path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def rows(path, keep):
    """tsv rows (header '#' skipped) whose first column is in keep -> {(object, symbol): rest}."""
    out = {}
    with open(path, encoding='utf-8', errors='surrogateescape') as f:
        for line in f:
            if line.startswith('#'):
                continue
            p = line.rstrip('\n').split('\t')
            if len(p) > 1 and p[0] in keep:
                out[(p[0], p[1])] = p[2:]
    return out


def objects_of(path):
    """refs.tsv object column (unit sources and synthesized objects) -> set."""
    s = set()
    with open(path, encoding='utf-8', errors='surrogateescape') as f:
        for line in f:
            if not line.startswith('#'):
                s.add(line.split('\t', 1)[0])
    return s


def addrmap(out):
    """address -> object file the run took the definition from ('' when none)."""
    m = {}
    with open(os.path.join(out, 'addrmap.tsv'), encoding='utf-8', errors='surrogateescape') as f:
        for line in f:
            if line.startswith('#'):
                continue
            p = line.rstrip('\n').split('\t')
            if len(p) > 4:
                m[p[0]] = os.path.basename(p[4])
    return m


def objects_tsv(path):
    """compile.sh objects.tsv -> {unit: object path} of the units that compiled."""
    out = {}
    for line in open(path, errors='surrogateescape'):
        p = line.rstrip('\n').split('\t')
        if len(p) >= 3 and p[2] == 'ok':
            out[p[0]] = p[1]
    return out


def unit_addrs(path):
    """units.json -> {address (8 hex digits): set of units defining it}."""
    m = collections.defaultdict(set)
    for u in json.load(open(path))['units']:
        for f in u.get('functions', []):
            m['%08x' % int(f['address'], 16)].add(u['source'])
    return m


def defined_names(fixed_dir, names):
    """name -> number of objects in fixed_dir that define it (global or weak)."""
    files = sorted(os.path.join(fixed_dir, n) for n in os.listdir(fixed_dir) if n.endswith('.o'))
    cnt = collections.Counter()
    for i in range(0, len(files), 200):
        p = subprocess.run([NM, '--defined-only', '-A'] + files[i:i + 200], capture_output=True, text=True,
                           errors='surrogateescape')
        for line in p.stdout.splitlines():
            f = line.split()
            if len(f) >= 3 and f[-2] in 'TDBRVWGSiu' and f[-1] in names:
                cnt[f[-1]] += 1
    return cnt


def objfile(unit):
    return unit.replace('/', '_') + '.o'


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('ref')
    ap.add_argument('cand')
    ap.add_argument('--objects', nargs=2, metavar=('REF', 'CAND'), required=True)
    ap.add_argument('--units', help='units.json of the candidate tree (address -> defining units)')
    ap.add_argument('--fixed', nargs=2, metavar=('REF', 'CAND'))
    ap.add_argument('--show', type=int, default=12)
    a = ap.parse_args(argv)
    ro, co = objects_tsv(a.objects[0]), objects_tsv(a.objects[1])
    units = sorted(set(ro) & set(co))
    keep = set(units)
    ram = addrmap(a.ref)
    cand_files = {os.path.basename(p) for p in co.values()}
    ua = unit_addrs(a.units) if a.units else {}

    def outside(addr):
        if not addr:
            return False
        o = ram.get(addr)
        if o and o not in cand_files and not o.startswith('zz_'):
            return True
        return bool(ua.get(addr)) and not (ua[addr] & set(co))

    rr, cr = rows(os.path.join(a.ref, 'refs.tsv'), keep), rows(os.path.join(a.cand, 'refs.tsv'), keep)
    names = {k[1] for k in set(rr) | set(cr)}
    cdef = collections.Counter()
    if a.fixed:
        loc = set()
        for u in units:
            f = os.path.join(a.ref, 'objcopy', objfile(u) + '.args')
            if os.path.isfile(f):
                loc |= {l.split('=', 1)[1] for l in open(f, errors='surrogateescape').read().split('\n')
                        if l.startswith('--localize-symbol=')}
        cdef = defined_names(a.fixed[1], names | loc)
    print('units compiled in both runs: %d (reference %d, candidate %d)' % (len(units), len(ro), len(co)))
    report = collections.OrderedDict()
    unexplained = collections.OrderedDict()
    why = collections.defaultdict(collections.Counter)

    # compiled objects
    same, diff = 0, []
    for u in units:
        if sha(ro[u]) == sha(co[u]):
            same += 1
        else:
            diff.append(u)
    report['compiled object'] = (same, 0, len(diff))
    unexplained['compiled object'] = diff
    # refs.tsv
    same = expl = 0
    bad = []
    trans = collections.Counter()
    for k in sorted(set(rr) | set(cr)):
        x, y = rr.get(k), cr.get(k)
        if x == y:
            same += 1
            continue
        if x and y:
            trans[(x[0], y[0])] += 1
        addr = next((r[2] for r in (x, y) if r and len(r) > 2 and r[2]), '')
        reason = None
        if outside(addr):
            reason = 'addr-outside'
        elif x and x[0] == 'native' and not cdef.get(k[1]):
            reason = 'name-outside'
        elif x and y and x[0] == 'unresolved' and y[0] == 'vslot-unresolved':
            reason = 'vslot-host'
        if reason:
            expl += 1
            why['refs.tsv row'][reason] += 1
        else:
            bad.append('%s %s: %s -> %s' % (k[0], k[1], x, y))
    report['refs.tsv row'] = (same, expl, len(bad))
    unexplained['refs.tsv row'] = bad
    # abi.tsv
    ra, ca = rows(os.path.join(a.ref, 'abi.tsv'), keep), rows(os.path.join(a.cand, 'abi.tsv'), keep)
    same = expl = 0
    bad = []
    verdicts = collections.Counter()
    for k in sorted(set(ra) | set(ca)):
        x, y = ra.get(k), ca.get(k)
        if x == y:
            same += 1
            verdicts[x[3] if len(x) > 3 else '?'] += 1
            continue
        if outside((x or y)[0]):
            expl += 1
            why['abi.tsv verdict']['addr-outside'] += 1
        else:
            bad.append('%s %s: %s -> %s' % (k[0], k[1], x and x[3:6], y and y[3:6]))
    report['abi.tsv verdict'] = (same, expl, len(bad))
    unexplained['abi.tsv verdict'] = bad
    # objcopy lists
    listdiff = collections.defaultdict(lambda: [0, 0])     # unit -> [explained, unexplained]
    for ext in ('args', 'redefine', 'rebind', 'undefine'):
        same = expl = 0
        bad = []
        for u in units:
            fx = os.path.join(a.ref, 'objcopy', objfile(u) + '.' + ext)
            fy = os.path.join(a.cand, 'objcopy', objfile(u) + '.' + ext)
            lx = set(open(fx, errors='surrogateescape').read().split('\n')) if os.path.isfile(fx) else set()
            ly = set(open(fy, errors='surrogateescape').read().split('\n')) if os.path.isfile(fy) else set()
            same += len(lx & ly)
            for line in sorted(lx ^ ly):
                m = ADDR.search(line)
                sym = line.split('=', 1)[1] if line.startswith('--') and '=' in line else line.split(' ', 1)[0]
                rx, cx = rr.get((u, sym)), cr.get((u, sym))     # the reference row of the renamed symbol
                raddr = next((r[2] for r in (rx, cx) if r and len(r) > 2 and r[2]), '')
                reason = None
                if (m and outside(m.group(1))) or outside(raddr):
                    reason = 'addr-outside'
                elif line.startswith('--localize-symbol=') and line in lx and cdef.get(sym, 0) < 2:
                    reason = 'dup-outside'
                elif rx and rx[0] == 'native' and not cdef.get(sym):
                    reason = 'name-outside'
                elif rx and cx and rx[0] == 'unresolved' and cx[0] == 'vslot-unresolved':
                    reason = 'vslot-host'
                if reason:
                    expl += 1
                    why['objcopy %s line' % ext][reason] += 1
                    listdiff[u][0] += 1
                else:
                    listdiff[u][1] += 1
                    bad.append('%s.%s %s %s' % (u, ext, 'ref-only' if line in lx else 'cand-only', line))
        report['objcopy %s line' % ext] = (same, expl, len(bad))
        unexplained['objcopy %s line' % ext] = bad
    # rewritten objects
    if a.fixed:
        same = expl = 0
        bad = []
        for u in units:
            if sha(os.path.join(a.fixed[0], objfile(u))) == sha(os.path.join(a.fixed[1], objfile(u))):
                same += 1
            elif listdiff[u][0] and not listdiff[u][1] and u not in diff:
                expl += 1
                why['fixed object']['fixed'] += 1
            else:
                bad.append(u)
        report['fixed object'] = (same, expl, len(bad))
        unexplained['fixed object'] = bad
    synth = (sorted(o for o in objects_of(os.path.join(a.ref, 'refs.tsv')) if '#' in o),
             sorted(o for o in objects_of(os.path.join(a.cand, 'refs.tsv')) if '#' in o))
    print()
    print('%-24s %10s %10s %12s   explained by' % ('', 'identical', 'explained', 'unexplained'))
    for k, (s_, e, n) in report.items():
        print('%-24s %10d %10d %12d   %s' % (k, s_, e, n, ', '.join('%s %d' % kv for kv in why[k].most_common())))
    print('\nsynthesized vtable objects (set-dependent, not compared): reference %d, candidate %d, same host %d'
          % (len(synth[0]), len(synth[1]), len(set(synth[0]) & set(synth[1]))))
    if trans:
        print('\nrefs.tsv class changes (reference -> candidate):')
        for (x, y), n in trans.most_common():
            print('  %6d  %s -> %s' % (n, x, y))
    if verdicts:
        print('\nABI verdicts identical in both runs: ' + ', '.join('%s %d' % kv for kv in verdicts.most_common()))
    total = 0
    for kind, items in unexplained.items():
        total += len(items)
        if items:
            print('\nunexplained %s differences (%d), first %d:' % (kind, len(items), min(a.show, len(items))))
            for it in items[:a.show]:
                print('  ' + it[:300])
    print('\nunexplained differences: %d' % total)
    return 1 if total else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
