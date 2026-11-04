#!/usr/bin/env python3
"""merge_reports.py - one summary of the shard reports of the MSVC equivalence gate (tools/farm/equiv_job.sh).

    merge_reports.py REPORT.md... [--out summary.md]
    merge_reports.py --git <clone of the artifacts repository> [--farms eq1,...,eq6] [--commit <commit12>] [--out ...]

With --git, each farm's report branch (reports/<farm>) is fetched (read-only) and its `<commit12>-equiv.md` read; the
commit is --commit, or else the one most farms have a report for (the newest of those on a tie). The summary sums the
unit and function counts and the per-rule table, lists every differing function and every unit the prepared side does
not compile, and names the shards that are missing or FAILED (with the last line of their log). Numbers, unit names,
addresses and short diagnostics only, as the shard reports.
"""
import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

TITLE = re.compile(r'^# MSVC equivalence gate - (\S+) shard (\d+)/(\d+)')
FAILED = re.compile(r'^# equiv (\S+) FAILED')
UNITS = re.compile(r'^- units: (\d+) compared; (\d+) identical, (\d+) differ, (\d+) prepared side does not compile, '
                   r'(\d+) pristine does not compile')
FUNCS = re.compile(r'^- functions: (\d+) compared, (\d+) differ \(EH-only (\d+), retarget (\d+), code (\d+)')
ROW = re.compile(r'^\| (\S.*?) \| (\d+) \| (\d+) \| (\d+) \| (\d+) \|$')
RUN = re.compile(r'shard (\d+)/(\d+) \((\d+) units\)')
START = re.compile(r'start equiv (\d+) (\d+) at')   # the farm runner's log line, in a FAILED report
UNIT_KEYS = ('compared', 'identical', 'differ', 'no-build', 'base-failed')
FUNC_KEYS = ('compared', 'differ', 'eh', 'retarget', 'code')
KINDS = ('eh', 'retarget', 'code', 'no-build')


def parse(text, name='?'):
    """{commit, shard, of, failed, tail, units, functions, table, differing, nobuild, run} of one shard report."""
    r = dict(name=name, commit=None, shard=None, of=None, failed=False, tail='', units=Counter(), functions=Counter(),
             table={}, differing=[], nobuild=[], run=[])
    lines = text.splitlines()
    block, section = None, None
    for ln in lines:
        m = TITLE.match(ln) or FAILED.match(ln)
        if m and r['commit'] is None:
            r['commit'] = m.group(1)
            if m.re is TITLE:
                r['shard'], r['of'] = int(m.group(2)), int(m.group(3))
            else:
                r['failed'] = True
            continue
        if ln.startswith('## '):
            section = ln[3:]
            continue
        if ln.startswith('```'):
            block = None if block else (section or 'log')
            continue
        if block:
            if block.startswith('Differing'):
                r['differing'].append(ln)
            elif block.startswith('Units the prepared'):
                r['nobuild'].append(ln)
            elif r['failed'] and ln.strip():
                r['tail'] = ln.strip()[:200]
                m = START.search(ln)
                if m:
                    r['shard'], r['of'] = int(m.group(1)), int(m.group(2))
            continue
        m = UNITS.match(ln)
        if m:
            r['units'].update(dict(zip(UNIT_KEYS, map(int, m.groups()))))
        m = FUNCS.match(ln)
        if m:
            r['functions'].update(dict(zip(FUNC_KEYS, map(int, m.groups()))))
        m = ROW.match(ln)
        if m and m.group(1) != 'rule':
            r['table'][m.group(1)] = Counter(dict(zip(KINDS, map(int, m.groups()[1:]))))
        if section == 'Run' and ln.startswith('- '):
            r['run'].append(ln[2:])
            m = RUN.search(ln)
            if m and r['shard'] is None:
                r['shard'], r['of'] = int(m.group(1)), int(m.group(2))
    return r


def git(clone, *args):
    return subprocess.run(['git', '-C', clone] + list(args), capture_output=True, text=True)


def from_git(clone, farms, commit):
    """[(farm, report text or None)] for the chosen commit, and that commit."""
    have = {}
    for f in farms:
        ref = 'refs/remotes/origin/reports/%s' % f
        if git(clone, 'fetch', '-q', 'origin', '+refs/heads/reports/%s:%s' % (f, ref)).returncode:
            have[f] = (None, {})
            continue
        names = git(clone, 'ls-tree', '--name-only', ref).stdout.split()
        files = {}
        for n in names:
            m = re.match(r'^([0-9a-f]{7,40})-equiv\.md$', n)
            if m:
                t = git(clone, 'log', '-1', '--format=%ct', ref, '--', n).stdout.strip()
                files[m.group(1)] = int(t or 0)
        have[f] = (ref, files)
    if not commit:
        count = Counter(c for _, files in have.values() for c in files)
        newest = Counter()
        for _, files in have.values():
            for c, t in files.items():
                newest[c] = max(newest[c], t)
        if not count:
            sys.exit('merge_reports: no reports in %s' % ', '.join('reports/' + f for f in farms))
        commit = max(count, key=lambda c: (count[c], newest[c]))
    out = []
    for f in farms:
        ref, files = have[f]
        hit = [c for c in files if c.startswith(commit) or commit.startswith(c)]
        text = git(clone, 'show', '%s:%s-equiv.md' % (ref, hit[0])).stdout if ref and hit else None
        out.append((f, text))
    return out, commit


def spans(ks):
    """'1, 3-5, 9' for [1, 3, 4, 5, 9]."""
    out = []
    for k in ks:
        if out and out[-1][1] == k - 1:
            out[-1][1] = k
        else:
            out.append([k, k])
    return ', '.join(str(a) if a == b else '%d-%d' % (a, b) for a, b in out)


def merge(reports, commit=None):
    """Markdown summary of parsed shard reports."""
    ok = [r for r in reports if r.get('commit') and not r['failed']]
    commit = commit or (ok[0]['commit'] if ok else (reports[0].get('commit') if reports else '?'))
    of = Counter(r['of'] for r in ok if r['of']).most_common(1)
    n = of[0][0] if of else len(reports)
    units, funcs, table, differing, nobuild = Counter(), Counter(), {}, [], []
    for r in ok:
        units.update(r['units']); funcs.update(r['functions'])
        for rule, t in r['table'].items():
            table.setdefault(rule, Counter()).update(t)
        differing += r['differing']; nobuild += r['nobuild']
    done = sorted(r['shard'] for r in ok if r['shard'])
    missing = [k for k in range(1, n + 1) if k not in done]
    out = ['# MSVC equivalence gate - %s (%d of %d shards)' % (commit, len(done), n), '']
    out.append('- units: %d compared; %d identical, %d differ, %d prepared side does not compile, %d pristine does not '
               'compile' % tuple(units[k] for k in UNIT_KEYS))
    out.append('- functions: %d compared, %d differ (EH-only %d, retarget %d, code %d)' % tuple(funcs[k] for k in FUNC_KEYS))
    if missing:
        out.append('- shards without a report: %s' % spans(missing))
    out += ['', '| rule | eh | retarget | code | no-build | total |', '|---|---:|---:|---:|---:|---:|']
    for rule in sorted(table, key=lambda r: (r.startswith('(') or r == '-', -sum(table[r].values()), r)):
        t = table[rule]
        out.append('| %s | %d | %d | %d | %d | %d |' % ((rule,) + tuple(t[k] for k in KINDS) + (sum(t.values()),)))
    out += ['', 'Kinds and rules as in the shard reports (tools/equiv/equiv.py report); no-build counts units.', '',
            '## Shards', '']
    for r in sorted(reports, key=lambda r: (r['shard'] or n + 1, r['name'])):
        if r.get('missing'):
            out.append('- %s: no report for %s' % (r['name'], commit))
        elif r['failed'] or not r.get('commit'):
            out.append('- %s: FAILED%s' % (r['name'], (' - `%s`' % r['tail'].replace('`', "'")) if r['tail'] else ''))
        else:
            run = next((x for x in r['run'] if x.startswith('pristine')), '')
            out.append('- %s: shard %s/%s, %d units, %d differ, %d do not compile%s' % (
                r['name'], r['shard'], r['of'], r['units']['compared'], r['units']['differ'], r['units']['no-build'],
                ('; ' + run) if run else ''))
    out += ['', '## Differing functions (unit, address, kind, rule)', '', '```'] + sorted(differing) + ['```', '']
    out += ['## Units the prepared side does not compile (unit, rule, first error)', '', '```'] + sorted(nobuild) + \
        ['```', '']
    return '\n'.join(out) + '\n'


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('reports', nargs='*', help='shard report files')
    ap.add_argument('--git', help='a clone of the artifacts repository (reports/<farm> branches are fetched)')
    ap.add_argument('--farms', default='eq1,eq2,eq3,eq4,eq5,eq6')
    ap.add_argument('--commit', help='the commit (12 hex) to merge; default: the one most farms reported')
    ap.add_argument('--out', help='write the summary here (default: stdout)')
    a = ap.parse_args()
    reports, commit = [], a.commit
    if a.git:
        got, commit = from_git(a.git, a.farms.split(','), a.commit)
        for farm, text in got:
            reports.append(parse(text, farm) if text is not None else dict(parse('', farm), missing=True))
    for p in a.reports:
        reports.append(parse(Path(p).read_text(errors='replace'), Path(p).name))
    if not reports:
        ap.error('no reports (give files or --git)')
    text = merge(reports, commit)
    if a.out:
        Path(a.out).write_text(text)
        print('\n'.join(text.splitlines()[:6]))
    else:
        sys.stdout.write(text)


if __name__ == '__main__':
    main()
