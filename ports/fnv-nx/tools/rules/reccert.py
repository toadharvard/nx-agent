#!/usr/bin/env python3
"""reccert.py - REC-CERT: a reconstruction is applied only with a certificate for the current rule set.

    python3 tools/rules/reccert.py hash
    python3 tools/rules/reccert.py stamp <recon dir> --objects <objects.tsv> --match <dir> [--report out.tsv]
    python3 tools/rules/reccert.py stamp-patches --stats <tree>/prepare_stats.tsv --objects <objects.tsv>
                                                 --msvc <msvc_check report.jsonl> [--out patches/certs.json]

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

PATCH-CERT is the same rule for the site patches (patches/*.s1b, tools/rules/s1b_patches.py): `stamp-patches` writes
patches/certs.json from the same certifying run (FNVNX_PATCH_CERTIFY=1) and tests/s1b/msvc_check.py's report.
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


def stamp_patches(stats_tsv, objects, msvc_report, out=None, now=None, report=None):
    """PATCH-CERT: patches/certs.json for the rule set of this checkout, from a certifying run.
    A site (tools/rules/s1b_patches.py) is certified when
      - it applied as a whole in the certifying prepare (FNVNX_PATCH_CERTIFY=1: `S1B-SITE/<origin>/applied` in the
        tree's prepare_stats.tsv),
      - GCC built its unit in that tree (objects.tsv `ok`), and
      - MSVC compiles the patched function to the bytes of the pristine one (tests/s1b/msvc_check.py --report: the
        row of the site's address, or every row of the unit for a `file` site, is `identical`); the gated classes
        are exempt from the byte check by their definition.
    A site in a file that is not a unit (a header) has no unit of its own in either report, so nothing certifies
    it here. Everything not certified is listed with the reason."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('fnvnx_s1b_patches', os.path.join(TOOLS, 'rules', 's1b_patches.py'))
    sys.path.insert(0, TOOLS)
    pm = importlib.util.module_from_spec(spec); spec.loader.exec_module(pm)
    cur = rules_hash()
    now = now or datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    applied, missed = set(), set()
    for l in open(stats_tsv):
        k = l.split('\t')[0]
        if k.startswith('S1B-SITE/'):
            _, origin, res = k.split('/', 2)
            (applied if res == 'applied' else missed).add(origin)
    built = {}
    for l in open(objects):
        p = l.rstrip('\n').split('\t')
        if len(p) >= 3: built[p[0]] = p[2]
    msvc = {}
    for l in open(msvc_report):
        l = l.strip()
        if l:
            r = json.loads(l)
            msvc.setdefault(r.get('source'), []).append((str(r.get('addr', '')).lower(), r.get('status')))
    sites, rows = {}, []
    for st in pm.SITES:
        why, m = '', None
        if st.origin in missed or st.origin not in applied: why = 'did not apply as a whole in the certifying prepare'
        elif st.path not in built: why = 'its file is not a unit that was compiled (a header, or outside the run)'
        elif built[st.path] != 'ok': why = 'GCC does not build its unit'
        elif st.rule in pm.GATED: m = 'exempt (gated class)'
        else:
            got = msvc.get(st.path, [])
            want = [x for x in got if st.addr is None or x[0] == '0x%08x' % st.addr]
            if not want: why = 'no MSVC row for the site'
            elif any(x[1] != 'identical' for x in want): why = 'MSVC: ' + ','.join(sorted({str(x[1]) for x in want if x[1] != 'identical'}))
            else: m = 'identical'
        if not why: sites[st.origin] = {'site': pm.site_hash(st), 'gcc': 'ok', 'msvc': m}
        rows.append((st.origin, st.rule, st.path, 'certified' if not why else 'not certified', why or m))
    out = out or pm.CERTS
    with open(out, 'w') as o:
        json.dump({'format': 'fnvnx-patchcert/1', 'rules': cur, 'certified': now, 'sites': dict(sorted(sites.items()))}, o, indent=1)
        o.write('\n')
    if report:
        with open(report, 'w') as o:
            o.write('#site\trule\tfile\tresult\twhy\n')
            for row in rows: o.write('\t'.join(row) + '\n')
    return cur, len(sites), rows


def main(argv):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('hash')
    p = sub.add_parser('stamp'); p.add_argument('recon'); p.add_argument('--objects', required=True)
    p.add_argument('--match', required=True); p.add_argument('--report'); p.add_argument('--now')
    p = sub.add_parser('stamp-patches'); p.add_argument('--stats', required=True); p.add_argument('--objects', required=True)
    p.add_argument('--msvc', required=True); p.add_argument('--out'); p.add_argument('--report'); p.add_argument('--now')
    a = ap.parse_args(argv)
    if a.cmd == 'hash':
        print(rules_hash()); return 0
    if a.cmd == 'stamp-patches':
        cur, n, rows = stamp_patches(a.stats, a.objects, a.msvc, a.out, a.now, a.report)
        print('reccert: rule set %s: %d of %d patch sites certified' % (cur, n, len(rows)))
        by = {}
        for r in rows:
            if r[3] != 'certified': by[r[4].split(':')[0]] = by.get(r[4].split(':')[0], 0) + 1
        for why, k in sorted(by.items(), key=lambda kv: -kv[1]): print('%6d  %s' % (k, why))
        return 0
    cur, n, rows = stamp(a.recon, a.objects, a.match, a.now, a.report)
    print('reccert: rule set %s: %d certified, %d not certified' % (cur, n['certified'], n['not certified']))
    by = {}
    for _, _, res, why in rows:
        if res != 'certified': by[why.split(':')[0]] = by.get(why.split(':')[0], 0) + 1
    for why, k in sorted(by.items(), key=lambda kv: -kv[1]): print('%6d  %s' % (k, why))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
