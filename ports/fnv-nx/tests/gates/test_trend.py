#!/usr/bin/env python3
"""test_trend.py - the regression detector of tools/gates/ready.py (fnvnx-trend/1, R0): a synthetic set of
regressions between consecutive farm-verified heads, and the stale-pass rules.

    python3 -I tests/gates/test_trend.py        -> one line per case, then `undetected N, false alarms M`; exit 1
                                                   when either is not 0

Each case starts from tests/gates' all-pass world. Run 1 scores it, and its JSON sidecar is published the way the
gates farm publishes it (reports/gates1/<head12>-gates.json). The case then changes the world as a newer farm
report would (a new commit, a new report published later) and scores it again; ready.py reads the earlier trend
from the store. A case expects a set of regressed metric ids (empty: no regression may be flagged), and optionally
gates that must not count as a pass.
"""
import contextlib
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_ready  # noqa: E402

C = test_ready.C
C2 = 'c0ffee000002'
C3 = 'c0ffee000003'
UNDETECTED = FALSE = 0


def publish_card(w, card, head):
    """the gates farm's sidecar of the run on head, published after everything else"""
    doc = {k: v for k, v in card.items() if k not in ('by_id', 'md')}
    w.write('reports/gates1/%s-gates.json' % head[:12], doc, published=w.latest() + 60)


def run(w, head):
    out = os.path.join(w.tmp, 'card.json')
    md = os.path.join(w.tmp, 'card.md')
    with contextlib.redirect_stdout(io.StringIO()):
        rc = test_ready.ready.main(['--fixtures', w.w, '--port', w.p, '--thresholds',
                                    w.path('thresholds.json', True), '--head', head, '--json', out, '--md', md])
    assert rc == 0
    card = json.loads(test_ready.readf(out))
    card['by_id'] = {g['id']: g for g in card['gates']}
    card['md'] = test_ready.readf(md)
    return card


def new_commit(w, c, behind=0):
    """c is the new integration head (0 behind), the older ones move back by one"""
    w.edit_json('_commits.json', lambda d: (d.update({k: v + 1 for k, v in d.items() if k != '4e4de4000001'}),
                                            d.__setitem__(c, behind)))


def census(w, c, errors, clean=4, failing=0):
    w.write('reports/farm1/%s-census.md' % c, '# census %s\n\n- units: 4  clean: %d  failing: %d\n- errors total: %d\n'
            % (c, clean, failing, errors), published=w.latest() + 60)


def case(label, steps, expect, not_pass=(), check=None, start=None):
    """steps: [(head, change)] applied in order, each scored after its change and published; expect: the metric
    ids that must be flagged as regressions at the end (open), nothing else"""
    global UNDETECTED, FALSE
    with tempfile.TemporaryDirectory() as tmp:
        w = test_ready.World(tmp)
        if start:
            start(w)
        card = run(w, C)
        publish_card(w, card, C)
        for head, change in steps:
            change(w)
            card = run(w, head)
            publish_card(w, card, head)
        got = {r['metric'] for r in card.get('regressions', []) if r.get('open')}
        miss, extra = set(expect) - got, got - set(expect)
        bad = [g for g in not_pass if card['by_id'][g]['status'] == 'pass' and not card['by_id'][g]['stale']]
        UNDETECTED += len(miss) + len(bad)
        FALSE += len(extra)
        more = check(card) if check else ''
        if more:
            UNDETECTED += 1
        msg = '; '.join(x for x in (
            'undetected %s' % ','.join(sorted(miss)) if miss else '',
            'false alarm %s' % ','.join(sorted(extra)) if extra else '',
            'counts as pass: %s' % ','.join(bad) if bad else '', more) if x)
        print('%s: %s' % (label, msg or 'ok'))
        return card


def range_is(metric, lo, hi):
    def chk(card):
        r = [x for x in card.get('regressions', []) if x['metric'] == metric and x.get('open')]
        if not r:
            return ''
        return '' if (r[-1]['range']['from'], r[-1]['range']['to']) == (lo, hi) else 'range %s..%s, want %s..%s' % (
            r[-1]['range']['from'], r[-1]['range']['to'], lo, hi)
    return chk


def main():
    # T1 census errors rise at the next farm-verified head; the range is the two source commits
    case('T1 census errors 0 -> 3', [(C2, lambda w: (new_commit(w, C2), census(w, C2, 3, 3, 1)))],
         {'compile.errors'}, check=range_is('compile.errors', C, C2))
    # T2 a regression stays flagged while later heads keep it (the farm re-scores the same head)
    case('T2 census regression survives a re-score', [
        (C2, lambda w: (new_commit(w, C2), census(w, C2, 3, 3, 1))),
        (C2, lambda w: None)], {'compile.errors'})
    # T3 it is resolved once a newer head goes back down
    case('T3 census regression fixed at the next head', [
        (C2, lambda w: (new_commit(w, C2), census(w, C2, 3, 3, 1))),
        (C3, lambda w: (new_commit(w, C3), census(w, C3, 0)))], set())
    # T4 a fall is not a regression
    case('T4 census errors 3 -> 1 (improvement)', [(C2, lambda w: (new_commit(w, C2), census(w, C2, 1, 3, 1)))],
         set(), start=lambda w: census(w, C, 3, 3, 1))
    # T4b 0 -> 3 -> 1 is still a regression against 0
    case('T4b census errors 0 -> 3 -> 1', [
        (C2, lambda w: (new_commit(w, C2), census(w, C2, 3, 3, 1))),
        (C3, lambda w: (new_commit(w, C3), census(w, C3, 1, 3, 1)))], {'compile.errors'})
    # T5 a measurement that disappears is a regression too (never trade for an unmeasured error class)
    case('T5 census report gone (data -> no-data)', [
        (C2, lambda w: (new_commit(w, C2), w.remove('reports/farm1/%s-census.md' % C)))], {'compile.errors'})
    # T6 the S16 scenes: one stops matching
    case('T6 S16 scene fails', [
        (C2, lambda w: (new_commit(w, C2), w.write('reports/s16/%s-frames.json' % C2, {'commit': C2, 'scenes': [
            {'name': 'main_menu', 'pass': False}]}, published=w.latest() + 60)))], {'render.scenes'})
    # T7 the boot loses a milestone
    case('T7 boot loses first_cell', [
        (C2, lambda w: (new_commit(w, C2), w.edit_json('link/%s/boot/milestones.json' % C,
                                                       lambda d: d.update(first_cell=False))))],
         {'boot.milestones'})
    # T8 the frame budget grows past the target
    case('T8 S15 worst frame 12 -> 20 ms', [
        (C2, lambda w: (new_commit(w, C2), w.write('reports/prof1/%s-frame-budget.json' % C2, json.loads(
            w.read('reports/prof1/%s-frame-budget.json' % C).replace('"mid": 12.0', '"mid": 20.0')),
            published=w.latest() + 60)))], {'frame.budget'})
    # T9 nothing changes but the head: no regression, and no alarm from re-scoring
    case('T9 new head, same reports', [(C2, lambda w: new_commit(w, C2))], set())
    # T10 stale pass: the farm's newer report failed; the older pass must not count
    case('T10 newer census FAILED', [
        (C2, lambda w: (new_commit(w, C2), w.write('reports/farm1/%s-census.md' % C2, '# census %s FAILED\n' % C2,
                                                   published=w.latest() + 60)))],
         {'compile.errors'}, not_pass=('compile.census',))
    # T11 stale pass: the farm is overdue (its pass is older than due_s while the head moved on)
    case('T11 census overdue', [
        (C2, lambda w: (new_commit(w, C2), w.write('reports/prof1/zz-late.txt', 'x',
                                                   published=w.latest() + 7 * 86400)))],
         set(), not_pass=('compile.census',))
    print('undetected %d, false alarms %d' % (UNDETECTED, FALSE))
    return 1 if UNDETECTED or FALSE else 0


if __name__ == '__main__':
    sys.exit(main())
