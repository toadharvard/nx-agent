#!/usr/bin/env python3
"""test_budget_built_levers.py - V1 (D14) against S15-threads 40a8d98 (tools/perf: the as-built scenario `placed`
carries barrier_relax, the lever S12's ORDER_REFCOUNT=weak put in the build).

    python3 -I tests/v1/test_budget_built_levers.py      -> one line per case, exit 1 when a case shows a defect

B1 compile.sh builds with ORDER_REFCOUNT=weak by default and fnvnx_win32.h has the relaxed/release helpers: the
   lever is in the build (what 40a8d98 claims).
B2 every plan scenario starts from the build: its levers include the built ones (`placed`'s). A plan without a lever
   that is in the build forecasts a build that does not exist; plan45 lacks barrier_relax, and in the model-only
   budget (frame_budget.build with no measured inputs) adding it moves plan45@1020 from pass_30 to pass_45.
"""
import copy
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(PORT, 'tools', 'perf'))
import frame_budget  # noqa: E402
sys.path.insert(0, os.path.join(PORT, 'tools'))
import tables  # noqa: E402

BAD = 0


def case(cid, ok, why):
    global BAD
    print('%s %s' % (cid, 'ok' if ok else 'DEFECT: ' + why))
    BAD += not ok


def main():
    cs = open(os.path.join(PORT, 'tools', 'link', 'compile.sh')).read()
    h = open(os.path.join(PORT, 'shim', 'include', 'fnvnx_win32.h')).read()
    case('B1', re.search(r'ORDER_REFCOUNT=\$\{ORDER_REFCOUNT:-weak\}', cs) and 'fnvnx_refcount_inc' in h
         and '__ATOMIC_RELAXED' in h, 'barrier_relax is not what compile.sh builds')
    model = json.load(open(tables.path('budget_model.json')))
    built = set(next(s for s in model['scenarios'] if s['id'] == 'placed')['levers'])
    lacking = {s['id']: sorted(built - set(s['levers'])) for s in model['scenarios'] if s['id'] != 'placed'}
    lacking = {k: v for k, v in lacking.items() if v}
    detail, changed = '', False
    if lacking:
        m2 = copy.deepcopy(model)
        for s in m2['scenarios']:
            s['levers'] = list(s['levers']) + lacking.get(s['id'], [])
        a, b = frame_budget.build(model), frame_budget.build(m2)
        lv = lambda o, sid: next(s for s in o['scenarios'] if s['id'] == sid)['results']['1020']['level']  # noqa
        changed = any(lv(a, k) != lv(b, k) or next(s for s in a['scenarios'] if s['id'] == k)['results']['1020'][
            'worst_frame_ms'] != next(s for s in b['scenarios'] if s['id'] == k)['results']['1020']['worst_frame_ms']
            for k in lacking)
        detail = '; model-only level @1020 as built -> with the built levers listed: ' + ', '.join(
            '%s %s -> %s' % (k, lv(a, k), lv(b, k)) for k in lacking)
    # the defect is a plan costed without a lever the build has. Since e04253d frame_budget.build() adds the
    # 'placed' levers to every scenario itself, so a model file that lists fewer gives the same result
    case('B2', not lacking or not changed, 'scenarios listing fewer than the built levers: %s%s%s' % (
        lacking, detail, ' (build() adds them: same result)' if lacking and not changed else ''))
    print('%d defects' % BAD)
    return 1 if BAD else 0


if __name__ == '__main__':
    sys.exit(main())
