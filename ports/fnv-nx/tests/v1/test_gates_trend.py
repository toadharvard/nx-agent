#!/usr/bin/env python3
"""test_gates_trend.py - V1 (D14) against R0's regression detector e446506 (tools/gates/ready.py regressions(),
trend_update(), scorecard gaps; fnvnx-trend/1).

    python3 -I tests/v1/test_gates_trend.py     -> one line per case, exit 1 when a case shows a defect

Synthetic trends (made-up heads, commits and gaps) fed straight to regressions() / trend_update(), plus the
scorecard's direction: every row's gap must grow when its stage gets worse.
  TR1  gap 2 -> 5 between two heads: one open "rose" regression, before 2, after 5, source range = the earlier
       entry's oldest commit .. the later entry's newest
  TR2  a head whose scorecard lacks the metric (missing head), then 5: still a regression against the last 2
  TR3  2 -> None (lost) -> 7: "lost" opened, then "rose" against the last measured 2; both open
  TR4  creep 1 -> 2 -> 3: two regressions, both open; then back to 1: both closed
  TR5  a re-score of the same head with a worse gap is appended, not merged, and flagged
  TR6  no change, an improvement, None -> None: no regression (no false alarm)
  TR7  direction: in scorecard(), more census errors, more undefined names, fewer milestones reached, fewer
       scenes matching and a slower worst frame each give a larger gap (lower is better everywhere)
  D18  21867b6 (D18: 60 fps on the worst frame, 720p handheld, 1.02 GHz stock clock, native renderer): the gates'
       thresholds say the same: fps target 60, the budget gate's target target_60 (worst frame), stock 1.02 GHz
       required and boost not, the scorecard's GPU row handheld_720p, and fps_levels judges the worst frame
       (16 ms worst / 10 ms median passes, 20 ms worst / 10 ms median fails)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'gates'))
import ready  # noqa: E402

BAD = 0


def case(cid, ok, why):
    global BAD
    print('%s %s' % (cid, 'ok' if ok else 'DEFECT: ' + why))
    BAD += not ok


def E(head, **gaps):
    """one trend entry: metric -> gap, the sources c<head>a (oldest) .. c<head>b (newest)"""
    return dict(head=head, generated=0, metrics={k: dict(gap=v, value=v, **{'from': head + 'a', 'to': head + 'b'})
                                                 for k, v in gaps.items()})


def main():
    r = ready.regressions([E('h1', m=2), E('h2', m=5)])
    case('TR1', len(r) == 1 and r[0]['kind'] == 'rose' and r[0]['open'] and (r[0]['before'], r[0]['after']) == (2, 5)
         and r[0]['range'] == {'from': 'h1a', 'to': 'h2b'} and r[0]['heads'] == {'from': 'h1', 'to': 'h2'}, repr(r))
    r = ready.regressions([E('h1', m=2), E('h2', other=1), E('h3', m=5)])
    case('TR2', [(x['metric'], x['kind'], x['open']) for x in r] == [('m', 'rose', True)], repr(r))
    r = ready.regressions([E('h1', m=2), E('h2', m=None), E('h3', m=7)])
    case('TR3', sorted((x['kind'], x['open']) for x in r) == [('lost', True), ('rose', True)]
         and next(x for x in r if x['kind'] == 'rose')['before'] == 2, repr(r))
    r = ready.regressions([E('h1', m=1), E('h2', m=2), E('h3', m=3)])
    r2 = ready.regressions([E('h1', m=1), E('h2', m=2), E('h3', m=3), E('h4', m=1)])
    case('TR4', len(r) == 2 and all(x['open'] for x in r) and len(r2) == 2 and not any(x['open'] for x in r2),
         '%r / %r' % (r, r2))
    tr = ready.trend_update([E('h1', m=2)], E('h1', m=4))
    r = ready.regressions(tr)
    same = ready.trend_update([E('h1', m=2)], E('h1', m=2))
    case('TR5', len(tr) == 2 and len(r) == 1 and r[0]['open'] and len(same) == 1, '%r %r' % (tr, r))
    r = ready.regressions([E('h1', m=3, n=None), E('h2', m=3, n=None), E('h3', m=1, n=None)])
    case('TR6', r == [], repr(r))

    # TR7: scorecard gaps grow with a worse stage
    th = {'scorecard': {'frame_fps': 60, 'frame_clock': 'stock'}, 'perf': {'clocks': [{'name': 'stock', 'hz': 1020000000}]},
          'boot_milestones': ['winmain', 'first_cell']}

    def gates(err, undef, reached, ok, wf):
        g = [dict(id='compile.census', status='pass', value=0, errors=err, failing=0, sources=[]),
             dict(id='link.undefined', status='pass', value=undef, sources=[]),
             dict(id='frames.compare', status='pass', scenes_ok=ok, scenes=5, note='', sources=[]),
             dict(id='perf.budget.stock', status='pass', worst_frame_ms={'lo': wf, 'mid': wf, 'hi': wf}, sources=[])]
        g += [dict(id='boot.' + n, status='pass' if i < reached else 'fail', sources=[])
              for i, n in enumerate(th['boot_milestones'])]
        return g

    def gap(rows, rid):
        return next(x['gap'] for x in rows if x['id'] == rid)
    good = ready.scorecard(gates(0, 0, 2, 5, 15.0), th)
    worse = ready.scorecard(gates(3, 4, 1, 3, 25.0), th)
    ids = ['compile.errors', 'link.undefined', 'boot.milestones', 'render.scenes', 'frame.budget']
    bad = [(i, gap(good, i), gap(worse, i)) for i in ids if not (gap(worse, i) is not None and gap(good, i) is not None
                                                                and gap(worse, i) > gap(good, i))]
    case('TR7', not bad, 'gap does not grow with a worse stage: %s' % bad)

    import json
    t = json.load(open(os.path.join(HERE, '..', '..', 'tools', 'gates', 'thresholds.json')))
    pf, sc = t['perf'], t['scorecard']
    clk = {c['name']: c for c in pf['clocks']}
    g = ready.Card.__new__(ready.Card)
    lv = None
    if g is not None:
        g.th = t
        lv = (g.fps_levels(16.0, 10.0)[2], g.fps_levels(20.0, 10.0)[2])
    case('D18', pf['fps']['target'] == 60 and pf['budget']['target'] == 'target_60' and clk['stock']['hz'] == 1020000000
         and clk['stock'].get('required') and not clk['boost'].get('required') and sc['frame_fps'] == 60
         and sc['frame_clock'] == 'stock' and sc['gpu_config'] == 'handheld_720p' and lv == (ready.PASS, ready.FAIL),
         'thresholds %s / scorecard %s / levels %s' % (pf['fps'], sc, lv))
    print('%d defects' % BAD)
    return 1 if BAD else 0


if __name__ == '__main__':
    sys.exit(main())
