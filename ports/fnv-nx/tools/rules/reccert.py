#!/usr/bin/env python3
"""reccert.py - REC-CERT: a reconstruction is applied only with a certificate for the current rule set.

    python3 tools/rules/reccert.py hash
    python3 tools/rules/reccert.py pristine-overlay <decomp checkout> <recon dir> <out dir>
    python3 tools/rules/reccert.py stamp <recon dir> --stats <tree>/prepare_stats.tsv --objects <objects.tsv>
                                         --match <build_and_match report.json | dir of them>
                                         --overlay <the pristine-overlay out dir the reports were made in> [--report out.tsv]
    python3 tools/rules/reccert.py stamp-patches --stats <tree>/prepare_stats.tsv --objects <objects.tsv>
                                                 --msvc <msvc_check report.jsonl> [--out patches/certs.json]

A reconstruction (fnv-nx-build recon/*, rule REC1) is text written against the text prepare produced when it was
accepted. When a rule changes, that text may change under it: a body that defined what a dropped rule had declared
no longer compiles. So REC1 applies a recon only when its recon.json carries

    "cert": {"rules": <hash>, "compiles": true, "match": <what matched>}

and <hash> is the hash of the rule set of the running prepare (`hash`): the bytes of tools/prepare.py and of every
tools/rules/*.py, and the rules FNVNX_PREPARE_SKIP leaves out. Without it the recon is not applied (REC-NOCERT) and
its functions go the general way: the unit's own native body if it passes N1-N4, else the lifted one. A unit that
does not compile because of a recon is then impossible by construction: an applied recon's unit compiled with it.

Certifying (a farm job, whenever the rule set changes). A certificate has two halves:
  - the recon's text is the exe's code: MSVC compiles the PRISTINE unit with the recon applied (its replace pairs,
    its append, its recipe entries) to the bytes of the exe, as the recon was accepted (D20, the decomp's
    tools/build_and_match.py). This half does not depend on the rules. `pristine-overlay` writes the checkout to
    run build_and_match in: the decomp with every recon applied to its unit and build/units.json cut to those
    units; its tools/ is a real copy (build_and_match finds its checkout through Path(__file__).resolve()).
    `stamp` takes the overlay too and certifies nothing unless the units of the reports are exactly the units of
    the overlay's build/units.json (the reports were made from the overlay, not from the pristine checkout);
  - the recon fits the text the rules produce: prepare with FNVNX_RECON=<recon dir> FNVNX_RECON_CERTIFY=1 (every
    recon is tried; the ones REC1 applies are named `REC-SITE/<recon>/applied` in the tree's prepare_stats.tsv) and
    the same FNVNX_PREPARE_SKIP, then tools/link/compile.sh over the units that have recons.
  `stamp` gives a recon the certificate when prepare applied it, its unit is `ok` in objects.tsv, and the match
  report compared its unit (status ok or mismatch) and names none of its placed functions among the mismatches (a
  recon that places no function has nothing to match: it is certified by the two compiles). Any other recon loses
  its certificate. All recons of a unit stand or fall with the unit's compile: which of them broke it is not
  decided here. The certificate carries no time, so stamping twice changes nothing.
The stamped recon.json files go back to their recon/* branches.

PATCH-CERT is the same rule for the site patches (patches/*.s1b, tools/rules/s1b_patches.py): `stamp-patches` writes
patches/certs.json from the same certifying run (FNVNX_PATCH_CERTIFY=1) and tests/s1b/msvc_check.py's report.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
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


COMPARED = ('ok', 'mismatch')        # build_and_match statuses of a unit whose functions were compared


def _unit_report(reports, unit):
    """(addresses named among the unit's mismatches, the unit's entry) from the reports that compared the unit;
    (None, entry or None) when no report did (not built, compare error, no entry)"""
    for rep in reports:
        for u in rep.get('units', []):
            if u.get('source') != unit: continue
            if u.get('status') not in COMPARED or not isinstance(u.get('mismatches'), list): return None, u
            return {a.lower() for a in re.findall(r'0x[0-9a-fA-F]{6,8}', json.dumps(u['mismatches']))}, u
    return None, None


def _load_reports(path):
    files = [path] if os.path.isfile(path) else sorted(os.path.join(path, n) for n in os.listdir(path) if n.endswith('.json'))
    out = []
    for f in files:
        try:
            out.append(json.load(open(f)))
        except (OSError, ValueError):
            pass
    return out


def _recons(recon_dir):
    for dp, _, names in sorted(os.walk(recon_dir)):
        if 'recon.json' not in names: continue
        f = os.path.join(dp, 'recon.json')
        try:
            r = json.load(open(f))
        except (OSError, ValueError):
            continue
        if r.get('format') == 'fnvnx-recon/1' and r.get('unit'):
            yield os.path.relpath(dp, recon_dir).replace(os.sep, '/'), f, r


class OverlayMismatch(Exception):
    pass


def _check_overlay(reports, overlay):
    """The reports were made from the overlay iff the set of their unit sources is the set of the overlay's units
    (a report of the pristine checkout names every unit of the decomp, the overlay's has only the recons' units)."""
    want = {u['source'] for u in json.load(open(os.path.join(overlay, 'build', 'units.json')))['units']}
    got = {u.get('source') for rep in reports for u in rep.get('units', [])}
    if got != want:
        raise OverlayMismatch('match reports hold %d units, the overlay %s holds %d (%d only in the reports, %d only in the overlay):'
                              ' they were not made from it; nothing certified' % (
                                  len(got), overlay, len(want), len(got - want), len(want - got)))


def stamp(recon_dir, stats_tsv, objects, match, overlay, report=None):
    reports = _load_reports(match)
    _check_overlay(reports, overlay)
    cur = rules_hash()
    applied = set()
    for l in open(stats_tsv):
        k = l.split('\t')[0]
        if k.startswith('REC-SITE/') and k.endswith('/applied'): applied.add(k[len('REC-SITE/'):-len('/applied')])
    built = {}
    for l in open(objects):
        p = l.rstrip('\n').split('\t')
        if len(p) >= 3: built[p[0]] = p[2]
    rows, n = [], {'certified': 0, 'not certified': 0}
    for name, f, r in _recons(recon_dir):
        unit, why, m = r['unit'], '', None
        if name not in applied: why = 'the certifying prepare did not apply it'
        elif built.get(unit) != 'ok':
            why = 'the unit does not compile with the recons applied' if unit in built else 'the unit was not compiled'
        else:
            placed = [fn['address'].lower() for fn in r.get('functions', []) if fn.get('address')]
            bad, u = _unit_report(reports, unit)
            if bad is None:
                why = 'no match report compared the unit' + (' (status %s)' % u.get('status') if u else '')
            elif any(a in bad for a in placed): why = 'placed functions mismatch: ' + ' '.join(a for a in placed if a in bad)
            elif not placed: m = 'no placed function (the unit was compared: %s)' % u.get('status')
            else: m = '%d placed functions equal (unit: %s of %s functions matched)' % (
                len(placed), u.get('matched_functions', '?'), u.get('functions', '?'))
        old = r.get('cert')
        if why:
            r.pop('cert', None); n['not certified'] += 1
        else:
            r['cert'] = {'rules': cur, 'compiles': True, 'match': m}; n['certified'] += 1
        if r.get('cert') != old:
            with open(f, 'w') as o:
                json.dump(r, o, indent=1); o.write('\n')
        rows.append((name, unit, 'certified' if not why else 'not certified', why or m))
    if report:
        with open(report, 'w') as o:
            o.write('#recon\tunit\tresult\twhy\n')
            for row in rows: o.write('\t'.join(row) + '\n')
    return cur, n, rows


def pristine_overlay(decomp, recon_dir, out):
    """<out>: the decomp checkout with every recon applied to the pristine text of its unit (replace pairs, each
    exactly once, then the append) and build/units.json cut to the units that have recons, with the recons' recipe
    entries. tools/ is a real copy: build_and_match resolves its checkout root through Path(__file__).resolve(), so a
    link to the decomp's tools/ would run it on the pristine checkout (an assert fails if a link remains under it).
    Every other top-level entry is data read by path, never a root a tool resolves (code is only under tools/), so
    it is a link to the checkout. A recon whose pairs do not fit the pristine text is left
    out and listed (<out>/overlay_rejected.tsv): its unit is then compared without it, so its functions mismatch."""
    decomp, out = os.path.abspath(decomp), os.path.abspath(out)
    os.makedirs(out, exist_ok=True)
    for name in os.listdir(decomp):
        if name in ('src', 'build', '.git'): continue
        dst = os.path.join(out, name)
        if name == 'tools':
            if os.path.islink(dst) or os.path.isfile(dst): os.unlink(dst)
            elif os.path.isdir(dst): shutil.rmtree(dst)
            shutil.copytree(os.path.join(decomp, name), dst, symlinks=False, ignore=shutil.ignore_patterns('__pycache__'))
            links = [os.path.join(dp, n) for dp, dns, fns in os.walk(dst) for n in dns + fns if os.path.islink(os.path.join(dp, n))]
            assert not links, 'link in the overlay tools/: %s' % links[:3]
        elif not os.path.lexists(dst): os.symlink(os.path.join(decomp, name), dst)
    data = json.load(open(os.path.join(decomp, 'build', 'units.json')))
    by_unit, rejected = {}, []
    for name, f, r in _recons(recon_dir):
        by_unit.setdefault(r['unit'], []).append((name, os.path.dirname(f), r))
    units = []
    for u in data['units']:
        if u['source'] not in by_unit: continue
        src = os.path.join(decomp, u['source'])
        text = open(src, encoding='utf-8', errors='surrogateescape').read()
        u = json.loads(json.dumps(u))
        for name, d, r in by_unit[u['source']]:
            edited, bad = text, None
            for k, e in enumerate(r.get('replace') or []):
                old, new = (e.get('old'), e.get('new')) if isinstance(e, dict) else (None, None)
                if not isinstance(old, str) or not old or not isinstance(new, str) or edited.count(old) != 1:
                    bad = 'replace[%d] does not occur exactly once in the pristine text' % k; break
                edited = edited.replace(old, new)
            if bad:
                rejected.append((name, u['source'], bad)); continue
            if r.get('append'):
                body = open(os.path.join(d, r['append']), encoding='utf-8', errors='surrogateescape').read()
                edited = edited.rstrip('\n') + '\n\n' + body + ('' if body.endswith('\n') else '\n')
            text = edited
            have = {f['address'] for f in u.get('functions', [])}
            for fn in r.get('functions', []):
                if fn['address'] not in have: u.setdefault('functions', []).append(dict(fn))
            for k, v in r.get('symbols', {}).items(): u.setdefault('symbols', {}).setdefault(k, v)
            for rt in r.get('retarget_functions', []):
                for fnc in u.get('functions', []):
                    if fnc['address'].lower() == rt.get('address', '').lower() and fnc['symbol'] == rt.get('old') and rt.get('new'):
                        fnc['symbol'] = rt['new']
                        if rt['old'] in u.get('symbols', {}): u['symbols'].setdefault(rt['new'], u['symbols'].pop(rt['old']))
        dst = os.path.join(out, u['source'])
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, 'w', encoding='utf-8', errors='surrogateescape') as o: o.write(text)
        units.append(u)
    # the rest of src/ as links, directory by directory, so that includes beside a unit resolve as in the checkout
    for dp, dns, fns in os.walk(os.path.join(decomp, 'src')):
        rel = os.path.relpath(dp, decomp)
        os.makedirs(os.path.join(out, rel), exist_ok=True)
        for fn in fns:
            dst = os.path.join(out, rel, fn)
            if not os.path.lexists(dst): os.symlink(os.path.join(dp, fn), dst)
    os.makedirs(os.path.join(out, 'build'), exist_ok=True)
    for name in os.listdir(os.path.join(decomp, 'build')):
        dst = os.path.join(out, 'build', name)
        if name != 'units.json' and not os.path.lexists(dst): os.symlink(os.path.join(decomp, 'build', name), dst)
    data['units'] = units
    with open(os.path.join(out, 'build', 'units.json'), 'w') as o: json.dump(data, o)
    with open(os.path.join(out, 'overlay_rejected.tsv'), 'w') as o:
        o.write('#recon\tunit\twhy\n')
        for row in rejected: o.write('\t'.join(row) + '\n')
    return len(units), rejected


def stamp_patches(stats_tsv, objects, msvc_report, out=None, report=None):
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
        json.dump({'format': 'fnvnx-patchcert/1', 'rules': cur, 'sites': dict(sorted(sites.items()))}, o, indent=1)
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
    p = sub.add_parser('pristine-overlay'); p.add_argument('decomp'); p.add_argument('recon'); p.add_argument('out')
    p = sub.add_parser('stamp'); p.add_argument('recon'); p.add_argument('--stats', required=True)
    p.add_argument('--objects', required=True); p.add_argument('--match', required=True)
    p.add_argument('--overlay', required=True); p.add_argument('--report')
    p = sub.add_parser('stamp-patches'); p.add_argument('--stats', required=True); p.add_argument('--objects', required=True)
    p.add_argument('--msvc', required=True); p.add_argument('--out'); p.add_argument('--report')
    a = ap.parse_args(argv)
    if a.cmd == 'hash':
        print(rules_hash()); return 0
    if a.cmd == 'stamp-patches':
        cur, n, rows = stamp_patches(a.stats, a.objects, a.msvc, a.out, a.report)
        print('reccert: rule set %s: %d of %d patch sites certified' % (cur, n, len(rows)))
        by = {}
        for r in rows:
            if r[3] != 'certified': by[r[4].split(':')[0]] = by.get(r[4].split(':')[0], 0) + 1
        for why, k in sorted(by.items(), key=lambda kv: -kv[1]): print('%6d  %s' % (k, why))
        return 0
    if a.cmd == 'pristine-overlay':
        n, rejected = pristine_overlay(a.decomp, a.recon, a.out)
        print('reccert: %d units with recons written to %s; %d recons do not fit the pristine text' % (n, a.out, len(rejected)))
        return 0
    try:
        cur, n, rows = stamp(a.recon, a.stats, a.objects, a.match, a.overlay, a.report)
    except OverlayMismatch as e:
        print('reccert: %s' % e, file=sys.stderr); return 1
    print('reccert: rule set %s: %d certified, %d not certified' % (cur, n['certified'], n['not certified']))
    by = {}
    for _, _, res, why in rows:
        if res != 'certified': by[why.split(':')[0]] = by.get(why.split(':')[0], 0) + 1
    for why, k in sorted(by.items(), key=lambda kv: -kv[1]): print('%6d  %s' % (k, why))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
