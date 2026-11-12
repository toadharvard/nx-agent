#!/usr/bin/env python3
"""zero_targets.py - one table of the zero targets (D24's three, D25's twelve) for one head, from the newest reports of
every lane, with the delta against the previous head. The orchestrator's view each cycle (farm job `zero`).

    python3 -I tools/report/zero_targets.py <reports dir> --heads <file> [--head C] --out zero.md

<reports dir>: one subdirectory per farm (the tree of the artifacts repository's branch reports/<farm>), holding
<h12>-<job>.md and their <h12>-<job>.json sidecars (tools/farm/runner.sh). --heads: the integration branch's
commits, newest first, one per line (git log --format=%H); --head: the head of the table (default the first).
For a target the value is taken from the newest head at or before --head that has its report ("stale" when that is
not --head itself); the delta compares it with the next older head that has one.

Sources (the number each row shows):
  D24 trap stubs           <h>-full.md: the sum of the "undefined symbols" table (the guest's trap stubs)
  D24 link errors          <h>-full.md: the trial link's undefined + multiple definitions
  D24 wrong shaders        <h>-sweep.json (sweep_report.py shard): distinct failing programs (over / ill) + programs
                           vacuous in some parameter set, over the shards seen
  D25 #1..#12              <h>-scenario.json (fnvnx-scenario-metric/1, S18): the target's count when measured in
                           at least one scenario; otherwise the lane that measures it, when its report exists:
                           #1 the fault groups of <h>-faults.json (fnvnx-faults/1, tools/report/faults.py: new /
                           known / fixed in the note); #3 unimpl, #4 open, #6 decode + input of <h>-plat.json (fnvnx-plat/1); #7 the metric of
                           <h>-threads.json (fnvnx-threads-job/1); #8 failed allocations + 1 if the peak is over the
                           budget, <h>-mem.json (fnvnx-mem/1); #10 the metric of <h>-scripterr.json
                           (fnvnx-scripterr/1: distinct (form, kind, caller) groups not in the original's run;
                           or the same object under "scripterr" in <h>-scenario.json, where S18's job embeds it);
                           #11 the metric of <h>-nrocheck.json (fnvnx-nrocheck/1, tools/report/nro_check.py:
                           automated NRO_CHECKLIST.md items failing or open; or "nrocheck" in <h>-full.json, where
                           full_job's package step embeds it); #12 frames over the budget, <h>-frames.json
                           (fnvnx-frames/1; only with a calibrated --scale). The lane's number is shown beside the
                           scenario's when both exist.
Regressions column: the regression ratchet's ledger, <reports dir>/ratchet/ledger.json (tools/farm/ratchet.py,
branch reports/ratchet): per target, the items of its ledger metrics that the last check found worse than their
best (traps: "full: trap stubs"; link: "full: trial-link undefined"; shaders:
"sweep <shard>: ..."; D25 #n: "scenario D25 #n ..."); "-" without a ledger.
Writes <out> and <out>.json (fnvnx-zero/1). Numbers and our own names only. Exit 0.
"""
import argparse
import glob
import json
import os
import re
import sys

D24 = [('trap stubs', 'traps'), ('link errors', 'link'),
       ('wrong shaders', 'shaders')]
D25 = [(1, 'faults'), (2, 'lockstep divergences'), (3, 'unimplemented platform calls'), (4, 'failed file opens'),
       (5, 'skipped draws / unsupported states'), (6, 'audio decode / input mapping failures'),
       (7, 'races, deadlocks, hangs'), (8, 'allocation failures / over budget'), (9, 'save/load differences'),
       (10, 'game-script errors'), (11, 'open NRO checklist items'), (12, 'frames over 16.7 ms')]


class Reports:
    def __init__(self, root):
        self.files = {}   # (h12, job) -> [paths]
        for p in glob.glob(os.path.join(root, '*', '*')):
            m = re.match(r'^([0-9a-f]{12})-([A-Za-z0-9_]+)\.(md|json)$', os.path.basename(p))
            if m:
                self.files.setdefault((m.group(1), m.group(2), m.group(3)), []).append(p)

    def get(self, h12, job, ext):
        return sorted(self.files.get((h12, job, ext), []))


def rel(p, root):
    return os.path.relpath(p, root)


def load(p):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return None


def num(s):
    return int(s.replace(',', '')) if s and re.fullmatch(r'[\d,]+', s) else None


# ---- per source: value(h12) -> (value, [source paths], note) or None when the head has no such report
def full_traps(R, h):
    ps = R.get(h, 'full', 'md')
    if not ps:
        return None
    text = open(ps[0], errors='replace').read()
    m = re.search(r'^\| undefined symbols \| category \| owner \|\n\|[-|]+\|\n((?:\|.*\|\n)*)', text, re.M)
    if not m:
        # 0 only for a guest that linked (its stage row ok/warn); a guest that failed or never ran measured nothing:
        # the next older head with a value is used (dc5c0627023e's table showed 0 from c46cd30231d9, whose link failed)
        g = re.search(r'^\| guest \| (\w+) \|', text, re.M)
        if not g or g.group(1) not in ('ok', 'warn'):
            return None
        return (0, ps[:1], 'no undefined symbols table: the guest linked with 0 trap stubs')
    total = sum(num(r.split('|')[1].strip()) or 0 for r in m.group(1).splitlines())
    return (total, ps[:1], '')


def full_link(R, h):
    ps = R.get(h, 'full', 'md')
    if not ps:
        return None
    m = re.search(r'trial link: ([\d,]+|-) objects, ([\d,]+|-) undefined, ([\d,]+|-) multiple definitions',
                  open(ps[0], errors='replace').read())
    if not m:
        return (None, ps[:1], 'no trial link line')
    u, d = num(m.group(2)), num(m.group(3))
    return ((u or 0) + (d or 0) if u is not None or d is not None else None, ps[:1], 'undefined %s + multiple %s' %
            (m.group(2), m.group(3)))


def sweep_shaders(R, h):
    ps = R.get(h, 'sweep', 'json')
    if not ps:
        return None
    failing, vac, n = set(), 0, 0
    for p in ps:
        j = load(p) or {}
        if 'failing_programs' not in j:
            continue
        n += 1
        failing |= {f.get('hash') for f in j['failing_programs']}
        vac += j.get('programs_vacuous_in_some_set') or 0
    if not n:
        return (None, ps, 'no shard summary')
    return (len(failing) + vac, ps, 'failing %d + vacuous in some set %d, %d shards' % (len(failing), vac, n))


def scenario_target(n):
    def f(R, h):
        ps = R.get(h, 'scenario', 'json')
        if not ps:
            return None
        j = load(ps[0]) or {}
        t = (j.get('targets') or {}).get(str(n))
        if j.get('schema') != 'fnvnx-scenario-metric/1' or not t:
            return (None, ps[:1], 'no metric')
        if not t.get('measured_in'):
            return (None, ps[:1], 'not measured in %s scenarios' % t.get('of', '?'))
        return (t.get('count'), ps[:1], 'measured in %d/%d scenarios' % (t['measured_in'], t.get('of', 0)))
    return f


def lane(job, schema, pick, why, embedded_in=None):
    def f(R, h):
        ps = R.get(h, job, 'json')
        j = load(ps[0]) or {} if ps else {}
        if not ps and embedded_in:   # a lane that runs inside another job: its object under the job's sidecar
            ps = R.get(h, embedded_in, 'json')
            j = (load(ps[0]) or {}).get(job) or {} if ps else {}
            if not j:
                return None
        if not ps:
            return None
        inner = j.get('report') if schema == 'fnvnx-threads-job/1' else j
        if j.get('schema') != schema or inner is None:
            return (None, ps[:1], 'no %s data' % job)
        v = pick(j)
        return (v, ps[:1], (why(j) if callable(why) else why) if v is not None else 'not measured')
    return f


LANES = {
    1: lane('faults', 'fnvnx-faults/1', lambda j: j.get('total_groups'),
            lambda j: 'faults lane: groups (new %s, known %s, fixed %s)' % (j.get('new'), j.get('known'), j.get('fixed'))),
    3: lane('plat', 'fnvnx-plat/1', lambda j: j['parts']['unimpl'], 'plat lane: unimplemented entries'),
    4: lane('plat', 'fnvnx-plat/1', lambda j: j['parts']['open'], 'plat lane: failed opens'),
    6: lane('plat', 'fnvnx-plat/1', lambda j: j['parts']['decode'] + j['parts']['input'], 'plat lane: decode + input'),
    7: lane('threads', 'fnvnx-threads-job/1', lambda j: j.get('metric'), 'threads lane: races + cycles + long waits'),
    8: lane('mem', 'fnvnx-mem/1', lambda j: j.get('fails', 0) + (1 if j.get('over_budget') else 0) if j.get('runs')
            else None, 'mem lane: failures + over budget'),
    10: lane('scripterr', 'fnvnx-scripterr/1', lambda j: j.get('metric'),
             lambda j: 'scripterr lane: groups not in the original (%s in it)' % j.get('in_original'), 'scenario'),
    11: lane('nrocheck', 'fnvnx-nrocheck/1', lambda j: j.get('metric'),
             lambda j: 'nrocheck lane: automated checklist items fail %s, open %s of %s; manual %s' % (
                 j.get('fail'), j.get('open'), j.get('automated'), j.get('manual')), 'full'),
    12: lane('frames', 'fnvnx-frames/1', lambda j: (j.get('device_ms') or {}).get('over_budget'),
             'frames lane: frames over budget (calibrated)'),
}


RATCHET = {'traps': ('full: trap stubs',), 'link': ('full: trial-link undefined',),
           'shaders': ('sweep ',)}


def regressions(ledger, key):
    """items regressed now among the ratchet metrics of a zero row (None without a ledger)"""
    if ledger is None:
        return None
    pre = RATCHET.get(key) or (('scenario D25 %s ' % key,) if key.startswith('#') else ())
    n = 0
    for name, e in (ledger.get('metrics') or {}).items():
        rg = e.get('regression')
        if rg and not rg.get('wip') and any(name == p or name.startswith(p) for p in pre):
            n += rg.get('items') or 1
    return n


def newest(R, fn, heads, start):
    """(index into heads, result) of the newest head at or after start (older) with a value"""
    for i in range(start, len(heads)):
        r = fn(R, heads[i])
        if r is not None and r[0] is not None:
            return i, r
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('reports')
    ap.add_argument('--heads', required=True)
    ap.add_argument('--head')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    heads = [l.strip()[:12] for l in open(a.heads) if re.match(r'^[0-9a-f]{12}', l.strip())]
    if a.head:
        h0 = a.head[:12]
        heads = heads[heads.index(h0):] if h0 in heads else [h0] + heads
    if not heads:
        print('zero_targets: no heads', file=sys.stderr)
        return 2
    R = Reports(a.reports)
    rows = []
    led = load(os.path.join(a.reports, 'ratchet', 'ledger.json'))
    led = led if isinstance(led, dict) and led.get('schema') == 'fnvnx-ratchet/1' else None

    def row(area, key, name, fn, extra=None):
        i, r = newest(R, fn, heads, 0)
        d = {'area': area, 'key': key, 'target': name, 'value': None, 'head': None, 'stale': None, 'source': None,
             'note': '', 'prev': None, 'prev_head': None, 'delta': None}
        if r:
            d.update(value=r[0], head=heads[i], stale=i > 0, source=[rel(p, a.reports) for p in r[1]], note=r[2])
            j, pr = newest(R, fn, heads, i + 1)
            if pr:
                d.update(prev=pr[0], prev_head=heads[j], delta=r[0] - pr[0])
        else:
            here = fn(R, heads[0])
            if here:
                d['note'] = here[2]
        if extra:
            k, er = newest(R, extra, heads, 0)
            if er:
                d['lane'] = {'value': er[0], 'head': heads[k], 'source': [rel(p, a.reports) for p in er[1]], 'note': er[2]}
                if d['value'] is None:   # the scenarios did not measure it: the lane's number stands
                    d.update(value=er[0], head=heads[k], stale=k > 0, source=d['lane']['source'], note=er[2])
                    j, pr = newest(R, extra, heads, k + 1)
                    if pr:
                        d.update(prev=pr[0], prev_head=heads[j], delta=er[0] - pr[0])
        d['regressions'] = regressions(led, key)
        rows.append(d)

    srcs = {'traps': full_traps, 'link': full_link, 'shaders': sweep_shaders}
    for name, k in D24:
        row('D24', k, name, srcs[k])
    for n, name in D25:
        row('D25', '#%d' % n, name, scenario_target(n), LANES.get(n))
    measured = [r for r in rows if r['value'] is not None]
    total = sum(r['value'] for r in measured)
    o = open(a.out, 'w')
    o.write('# zero targets at %s\n\n' % heads[0])
    o.write('- measured %d of %d targets; sum %d; at zero: %d; stale (older head): %d\n' % (
        len(measured), len(rows), total, sum(1 for r in measured if r['value'] == 0),
        sum(1 for r in measured if r['stale'])))
    if led is not None:
        o.write('- regressions (the ratchet ledger, ratchet/ledger.json): %d items\n' % sum(r['regressions'] or 0 for r in rows))
    o.write('\n| area | target | number | delta | regressions | head | source | note |\n|---|---|---:|---:|---:|---|---|---|\n')
    for r in rows:
        v = '-' if r['value'] is None else str(r['value'])
        dl = '' if r['delta'] is None else ('%+d (vs %s)' % (r['delta'], r['prev_head']))
        hd = '-' if not r['head'] else r['head'] + (' (stale)' if r['stale'] else '')
        note = r['note']
        if r.get('lane') and r['lane']['source'] != r['source']:
            note += ('; ' if note else '') + 'lane: %s at %s' % (r['lane']['value'], r['lane']['head'])
        o.write('| %s | %s%s | %s | %s | %s | %s | %s | %s |\n' % (
            r['area'], (r['key'] + ' ') if r['area'] == 'D25' else '', r['target'], v, dl,
            '-' if r['regressions'] is None else r['regressions'], hd,
            ', '.join(r['source'] or [])[:120] or '-', note.replace('|', '/')[:160]))
    o.close()
    json.dump({'schema': 'fnvnx-zero/1', 'head': heads[0], 'measured': len(measured), 'targets': len(rows),
               'sum': total, 'rows': rows},
              open(a.out[:-3] + '.json' if a.out.endswith('.md') else a.out + '.json', 'w'), indent=1)
    print('zero: %d of %d measured, sum %d' % (len(measured), len(rows), total))
    return 0


if __name__ == '__main__':
    sys.exit(main())
