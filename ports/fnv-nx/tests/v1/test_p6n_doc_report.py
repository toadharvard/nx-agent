#!/usr/bin/env python3
"""test_p6n_doc_report.py - V1 (D14) against P6n b374619 ("every hotspot row names its thread and its share of
16.7 ms; fallback rows marked") and 91710d3 ("RENDER_PERF.md ... with the measured perf round": the doc's numbers are
report.json's).
  H1  every row of every report.json hotspots list (top10, cpu_by_function, cpu_by_family_pass, gpu_by_pass/*,
      gpu_by_program/*, engine_d3d9_path_by_function) has thread in {main, render, GPU} and share_of_16_7ms equal to
      ms / 16.7 (4 decimals); cpu_by_family_pass rows may say "main+render" (the commit: "family marginals say both
      threads")
  H2  engine_d3d9_path_by_function: every row has applies_to; the rows of the replaced D3D9 path say "Nine fallback
      only", the kept engine part does not
  H3  RENDER_PERF.md section 0, resource table: GPU handheld, main, main with the kept engine part, render thread
      (ms and share) equal report.json summary (frame_ms, targets_budget_fraction['60']) and the GPU model frame
  H4  RENDER_PERF.md section 0, hotspot table: each of the 10 rows (ms, share %, thread) equals hotspots.top10
  H5  the doc's baseline (443daf3: main / render front / backend instructions, GPU docked / handheld) equals the
      optimizations[] baseline entry
The doc and report.json are read at --rev (default: the working tree); with --rev the files are read from git.
  python3 -I tests/v1/test_p6n_doc_report.py [--rev <commit>]
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
FILES = {'doc': 'docs/RENDER_PERF.md', 'rep': 'render/native/perf/report.json'}


def read(rel, rev):
    if not rev:
        return open(os.path.join(ROOT, rel)).read()
    r = subprocess.run(['git', '-C', ROOT, 'show', '%s:./%s' % (rev, rel)], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit('git show %s:%s failed: %s' % (rev, rel, r.stderr))
    return r.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rev')
    o = ap.parse_args()
    doc, rep = read(FILES['doc'], o.rev), json.loads(read(FILES['rep'], o.rev))
    fails = 0
    h = rep['hotspots']
    # H1
    bad, n = [], 0
    for k, rows in h.items():
        if not isinstance(rows, list):
            continue
        for x in rows:
            if not isinstance(x, dict) or 'ms' not in x:
                continue
            n += 1
            fam = k == 'cpu_by_family_pass' and str(x.get('thread', '')).startswith('main+render')   # b374619: "family marginals say both threads"
            if x.get('thread') not in ('main', 'render', 'GPU') and not fam:
                bad.append('%s/%s: thread %r' % (k, x.get('name'), x.get('thread')))
            elif x.get('share_of_16_7ms') is None or abs(x['share_of_16_7ms'] - round(x['ms'] / 16.7, 4)) > 1.5e-4:
                bad.append('%s/%s: share %r for %.3f ms' % (k, x.get('name'), x.get('share_of_16_7ms'), x['ms']))
    fails += bool(bad)
    print('H1: %s - %d hotspot rows with ms: thread and share of 16.7 ms%s' % ('DEFECT' if bad else 'ok', n,
          ('; %d bad: %s' % (len(bad), '; '.join(bad[:5]))) if bad else ''))
    # H2
    e = h.get('engine_d3d9_path_by_function', [])
    d3d9 = [x for x in e if 'replaced' in str(x.get('path', ''))]
    unmarked = [x['name'] for x in e if not x.get('applies_to') or
                ('fallback only' in x['applies_to'].lower()) != (x in d3d9) or (x not in d3d9 and 'native path' not in x['applies_to'])]
    fails += bool(unmarked) or not e
    print('H2: %s - engine_d3d9_path_by_function: %d rows, %d on the replaced D3D9 path; %d without a matching applies_to%s' %
          ('DEFECT' if unmarked or not e else 'ok', len(e), len(d3d9), len(unmarked), (': ' + ', '.join(unmarked[:4])) if unmarked else ''))
    # H3
    s = rep['summary']
    f60 = s['targets_budget_fraction']['60']
    gpu = h['gpu_frame_ms/mix_hi/handheld_720p']
    gpu_ms = gpu if isinstance(gpu, (int, float)) else gpu.get('ms', gpu.get('frame_ms')) if isinstance(gpu, dict) else None
    want = [('GPU, handheld 720p', gpu_ms, f60['gpu_handheld_720p']),
            ('main thread: native front', s['frame_ms']['main']['gate'], s['frame_ms']['main']['gate'] / 16.7),
            ('main thread with the kept engine part', s['frame_ms']['main_with_engine_kept']['gate'], f60['1.02_with_engine_kept']),
            ('render thread', s['frame_ms']['render']['gate'], s['frame_ms']['render']['gate'] / 16.7)]
    bad = []
    for label, ms, share in want:
        m = re.search(r'^\| %s[^|]*\| ([0-9.]+) \| ([0-9.]+) \|' % re.escape(label), doc, re.M)
        if not m:
            bad.append('%s: row not found' % label)
            continue
        if ms is None or abs(float(m.group(1)) - ms) > 0.0051 or abs(float(m.group(2)) - share) > 0.0051:
            bad.append('%s: doc %s ms / %s, report %s ms / %.3f' % (label, m.group(1), m.group(2), ms, share))
    fails += bool(bad)
    print('H3: %s - section 0 resource table vs summary%s' % ('DEFECT' if bad else 'ok', ('; ' + '; '.join(bad)) if bad else ''))
    # H4
    rows = re.findall(r'^\| (\d+) \| (GPU pass|CPU function) \| ([^|]+) \| (\w+) \| ([0-9.]+) \| ([0-9.]+) % \|', doc, re.M)
    bad = []
    top = h['top10']
    if len(rows) != len(top):
        bad.append('%d doc rows, %d top10 rows' % (len(rows), len(top)))
    for (i, kind, what, thread, ms, pct), t in zip(rows, top):
        ok = thread == t.get('thread') and abs(float(ms) - t['ms']) <= 0.0051 and \
            abs(float(pct) - 100 * t.get('share_of_16_7ms', -1)) <= 0.051 and (kind == 'GPU pass') == (t['kind'] == 'gpu_pass')
        if not ok:
            bad.append('row %s: doc %s %s %s ms %s %%, report %s %s %.3f ms %s' % (i, what.strip(), thread, ms, pct, t['name'][:40],
                       t.get('thread'), t['ms'], t.get('share_of_16_7ms')))
    fails += bool(bad)
    print('H4: %s - section 0 hotspot table, %d rows vs hotspots.top10%s' % ('DEFECT' if bad else 'ok', len(rows), ('; ' + '; '.join(bad[:4])) if bad else ''))
    # H5
    base = [x for x in rep.get('optimizations', []) if x.get('name') == 'baseline']
    m = re.search(r'Baseline: 443daf3 \(main ([0-9.]+) M,\s*render ([0-9.]+) M \+ ([0-9.]+) M instructions; GPU ([0-9.]+) / ([0-9.]+) ms', doc)
    if not base or not m:
        bad = ['baseline entry %s, doc sentence %s' % (bool(base), bool(m))]
    else:
        b = base[0]['after']
        got = [b['main'] / 1e6, b['render_front'] / 1e6, b['render_backend'] / 1e6, b['gpu_ms']['docked_720p_eff_0.6'], b['gpu_ms']['handheld_720p_eff_0.6']]
        bad = [] if all(abs(float(m.group(i + 1)) - g) <= 0.0051 for i, g in enumerate(got)) else \
            ['doc %s, report %s' % (m.groups(), ['%.2f' % g for g in got])]
    fails += bool(bad)
    print('H5: %s - baseline 443daf3 numbers%s' % ('DEFECT' if bad else 'ok', ('; ' + '; '.join(bad)) if bad else ''))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
