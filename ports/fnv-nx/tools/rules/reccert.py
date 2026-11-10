#!/usr/bin/env python3
"""reccert.py - REC-CERT: a reconstruction is applied only with a certificate for the current rule set.

    python3 tools/rules/reccert.py hash
    python3 tools/rules/reccert.py stamp <recon dir> --objects <objects.tsv> --match <dir> [--report out.tsv]

A reconstruction (fnv-nx-build recon/*, rule REC1) is text written against the text prepare produced when it was
accepted. When a rule changes, that text may change under it: a body that defined what a dropped rule had declared
no longer compiles. So REC1 applies a recon only when its recon.json carries

    "cert": {"rules": <hash>, "compiles": true, "match": <what matched>, "certified": <UTC time>}

and <hash> is the hash of the rule set of the running prepare (`hash`): the bytes of tools/prepare.py and of every
tools/rules/*.py, and the rules FNVNX_PREPARE_SKIP leaves out. Without it the recon is not applied (REC-NOCERT) and
its functions go the general way: the unit's own native body if it passes N1-N4, else the lifted one. A unit that
does not compile because of a recon is then impossible by construction: an applied recon's unit compiled with it.

Certifying (a farm job, whenever the rule set changes):
  1. prepare with FNVNX_RECON=<recon dir> FNVNX_RECON_CERTIFY=1 (every recon is applied) and the same
     FNVNX_PREPARE_SKIP; compile the units that have recons (tools/link/compile.sh);
  2. build and match those units as a recon is accepted (the decomp's tools/build_and_match.py, MSVC side), one report
     per unit in <match dir>/<unit path with / replaced by _>.json;
  3. `stamp`: a recon gets the certificate when its unit is `ok` in objects.tsv and none of its placed functions is
     among the unit's mismatches in the report (a recon that places no function has nothing to match: it is certified
     by the compile). Any other recon loses its certificate. All recons of a unit stand or fall with the unit's
     compile: which of them broke it is not decided here.
The stamped recon.json files go back to their recon/* branches.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def rules_hash(skip=None):
    """the rule set of this checkout: prepare.py, every tools/rules/*.py, and the rules left out"""
    if skip is None:
        skip = os.environ.get('FNVNX_PREPARE_SKIP', '')
    h = hashlib.sha256()
    files = [os.path.join(TOOLS, 'prepare.py')] + sorted(
        os.path.join(TOOLS, 'rules', n) for n in os.listdir(os.path.join(TOOLS, 'rules')) if n.endswith('.py'))
    for f in files:
        h.update(os.path.relpath(f, TOOLS).replace(os.sep, '/').encode() + b'\0')
        h.update(open(f, 'rb').read() + b'\0')
    h.update(('skip=' + ','.join(sorted(filter(None, skip.split(','))))).encode())
    return h.hexdigest()[:32]


def certified(recon, current=None):
    c = recon.get('cert')
    return isinstance(c, dict) and c.get('compiles') is True and c.get('rules') == (current or rules_hash())


def _mismatched_addresses(report, unit):
    """every address the report names among the unit's mismatches; None when the report has no entry for the unit"""
    for u in report.get('units', []):
        if u.get('source') == unit:
            return {a.lower() for a in re.findall(r'0x[0-9a-fA-F]{6,8}', json.dumps(u.get('mismatches', [])))}, u
    return None, None


def stamp(recon_dir, objects, match_dir, now=None, report=None):
    cur = rules_hash()
    now = now or datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    built = {}
    for l in open(objects):
        p = l.rstrip('\n').split('\t')
        if len(p) >= 3: built[p[0]] = p[2]
    rows, n = [], {'certified': 0, 'not certified': 0}
    for dp, _, names in sorted(os.walk(recon_dir)):
        if 'recon.json' not in names: continue
        f = os.path.join(dp, 'recon.json')
        try:
            r = json.load(open(f))
        except (OSError, ValueError):
            continue
        if r.get('format') != 'fnvnx-recon/1' or not r.get('unit'): continue
        unit, why, match = r['unit'], '', None
        if built.get(unit) != 'ok':
            why = 'the unit does not compile with the recons applied' if unit in built else 'the unit was not compiled'
        else:
            placed = [fn['address'].lower() for fn in r.get('functions', []) if fn.get('address')]
            if not placed:
                match = 'no placed function'
            else:
                mf = os.path.join(match_dir, unit.replace('/', '_') + '.json')
                try:
                    bad, u = _mismatched_addresses(json.load(open(mf)), unit)
                except (OSError, ValueError):
                    bad, u = None, None
                if bad is None: why = 'no match report for the unit'
                elif any(a in bad for a in placed): why = 'placed functions mismatch: ' + ' '.join(a for a in placed if a in bad)
                else: match = '%d placed functions equal (unit: %s of %s functions matched)' % (
                    len(placed), u.get('matched_functions', '?'), u.get('functions', '?'))
        old = r.get('cert')
        if why:
            r.pop('cert', None); n['not certified'] += 1
        else:
            r['cert'] = {'rules': cur, 'compiles': True, 'match': match, 'certified': now}; n['certified'] += 1
        if r.get('cert') != old:
            with open(f, 'w') as o:
                json.dump(r, o, indent=1); o.write('\n')
        rows.append((os.path.relpath(dp, recon_dir), unit, 'certified' if not why else 'not certified', why or match))
    if report:
        with open(report, 'w') as o:
            o.write('#recon\tunit\tresult\twhy\n')
            for row in rows: o.write('\t'.join(row) + '\n')
    return cur, n, rows


def main(argv):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('hash')
    p = sub.add_parser('stamp'); p.add_argument('recon'); p.add_argument('--objects', required=True)
    p.add_argument('--match', required=True); p.add_argument('--report'); p.add_argument('--now')
    a = ap.parse_args(argv)
    if a.cmd == 'hash':
        print(rules_hash()); return 0
    cur, n, rows = stamp(a.recon, a.objects, a.match, a.now, a.report)
    print('reccert: rule set %s: %d certified, %d not certified' % (cur, n['certified'], n['not certified']))
    by = {}
    for _, _, res, why in rows:
        if res != 'certified': by[why.split(':')[0]] = by.get(why.split(':')[0], 0) + 1
    for why, k in sorted(by.items(), key=lambda kv: -kv[1]): print('%6d  %s' % (k, why))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
