#!/usr/bin/env python3
"""frames.py - per-frame time, per-thread busy %, top functions and the critical-path thread from linhost's profile
(tests/s15/mtqemu/linhost.c --profile, D25 #12 groundwork for 60 fps at 1.02 GHz). tools/perf/FRAMES.md.

    python3 -I tools/perf/frames.py <profile>... [--elf guest.elf] [--log boot.log] [--frame-tid TID]
                                    [--scale X] [--budget-ms 16.7] [--top 15] [--skip 1] --out report.md

Input records (one per line): `p <hz> <t0>`, `t <tid> <slot> <mask>`, `s <tid> <t ns> <pc> [<w>]`,
`f <tid> <t ns> <k>`. A sample is w (default 1: the timer's overruns merged into it, + 1) ticks of 1/hz s of its
thread's CPU time (a CPU-time timer per thread), so per thread and frame:
    cpu ms = weighted samples * 1000 / hz,  busy % = cpu ms / the frame's wall ms.
Frames: the marks of the frame thread (--frame-tid, default the thread with the most marks) cut the run into frames
[mark k, mark k+1); the first --skip frames (default 1: the warm-up) are dropped. The critical-path thread of a frame
is the one with the most CPU in it: with perfect overlap the frame cannot be shorter than that thread's CPU time.
Top functions: each pc is mapped to the start address of its function (nm of --elf); by address only.
Emulated time is not device time. --scale X multiplies the critical thread's CPU time into estimated device ms (X
from a calibration, e.g. tools/perf's IPC model at 1.02 GHz over the emulator's measured rate); then frames over
--budget-ms are counted. Without --scale the report gives shares and emulated ms only.
--log: linhost's stderr (boot.log), for each thread slot's placement role ("linhost: thread <slot> role <r>").
Writes <report.md> and <report>.json (fnvnx-frames/1). Exit 0; 3 when there are no samples.
"""
import argparse
import bisect
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'threads'))
from report import Symbols  # noqa: E402  (tools/threads/report.py: function starts from nm)


def parse(paths):
    hz, threads, samples, marks = None, {}, [], []
    for path in paths:
        for line in open(path, errors='replace'):
            p = line.split()
            if len(p) < 3:
                continue
            try:
                if p[0] == 'p':
                    hz = int(p[1])
                elif p[0] == 't' and len(p) >= 4:
                    slot = int(p[2])
                    threads[int(p[1])] = {'slot': slot - (1 << 64) if slot >= 1 << 63 else slot, 'mask': int(p[3])}
                elif p[0] == 's' and len(p) >= 4:
                    samples.append((int(p[2]), int(p[1]), int(p[3], 16), int(p[4]) if len(p) > 4 else 1))
                elif p[0] == 'f' and len(p) >= 4:
                    marks.append((int(p[2]), int(p[1])))
            except ValueError:
                continue   # a line cut by a kill
    samples.sort()
    marks.sort()
    return hz or 1000, threads, samples, marks


def roles(log):
    out = {}
    if log and os.path.isfile(log):
        for m in re.finditer(r'^linhost: thread 0x([0-9a-f]+) role (\S+)', open(log, errors='replace').read(), re.M):
            out[int(m.group(1), 16)] = m.group(2)
    return out


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))] if v else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('profiles', nargs='+')
    ap.add_argument('--elf')
    ap.add_argument('--log')
    ap.add_argument('--frame-tid', type=int)
    ap.add_argument('--scale', type=float)
    ap.add_argument('--budget-ms', type=float, default=16.7)
    ap.add_argument('--top', type=int, default=15)
    ap.add_argument('--skip', type=int, default=1)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    hz, threads, samples, marks = parse(a.profiles)
    sym = Symbols(a.elf)
    role = roles(a.log)
    ms_per = 1000.0 / hz

    def tname(tid):
        t = threads.get(tid)
        if not t:
            return 'tid %d' % tid
        if t['slot'] < 0:
            return 'main'
        return 'slot %d (%s)' % (t['slot'], role.get(t['slot'], 'mask 0x%x' % t['mask']))

    by_tid = Counter(t for _, t in marks)
    ftid = a.frame_tid if a.frame_tid is not None else (by_tid.most_common(1)[0][0] if by_tid else None)
    cuts = [t for t, tid in marks if tid == ftid]
    frames = []   # (t0, t1, Counter tid -> samples)
    starts = cuts[:-1]
    for i, t0 in enumerate(starts):
        frames.append([t0, cuts[i + 1], Counter()])
    times = [s[0] for s in samples]
    for f in frames:
        lo, hi = bisect.bisect_left(times, f[0]), bisect.bisect_left(times, f[1])
        for _, tid, _, w in samples[lo:hi]:
            f[2][tid] += w
    frames = frames[a.skip:] if len(frames) > a.skip else frames
    tids = sorted({s[1] for s in samples} | set(threads))

    rows = []
    for t0, t1, c in frames:
        wall = (t1 - t0) / 1e6
        crit, cs = max(c.items(), key=lambda kv: (kv[1], -kv[0])) if c else (None, 0)
        rows.append({'wall_ms': wall, 'cpu_ms': {t: n * ms_per for t, n in c.items()}, 'critical': crit,
                     'critical_ms': cs * ms_per})
    walls = [r['wall_ms'] for r in rows]
    crit_count = Counter(r['critical'] for r in rows if r['critical'] is not None)
    per_thread = {}
    total = Counter()
    for s_ in samples:
        total[s_[1]] += s_[3]
    for tid in tids:
        busy = [r['cpu_ms'].get(tid, 0.0) / r['wall_ms'] * 100 for r in rows if r['wall_ms'] > 0]
        per_thread[tid] = {'name': tname(tid), 'samples': total.get(tid, 0), 'cpu_ms': total.get(tid, 0) * ms_per,
                           'busy_mean': statistics.mean(busy) if busy else 0.0, 'busy_p95': pct(busy, 0.95),
                           'critical_frames': crit_count.get(tid, 0)}
    funcs, funcs_by_t = Counter(), defaultdict(Counter)
    lo = bisect.bisect_left(times, frames[0][0]) if frames else 0
    hi = bisect.bisect_left(times, frames[-1][1]) if frames else len(samples)
    window = samples[lo:hi] if frames else samples
    for _, tid, pc, w in window:
        f = sym.func(pc)
        funcs[f] += w
        funcs_by_t[tid][f] += w
    nwin = sum(s_[3] for s_ in window)
    crit_tid = crit_count.most_common(1)[0][0] if crit_count else (total.most_common(1)[0][0] if total else None)
    dev = None
    if a.scale and rows:
        dev = [r['critical_ms'] * a.scale for r in rows]

    head = {'schema': 'fnvnx-frames/1', 'hz': hz, 'samples': len(samples), 'marks': len(marks), 'frame_tid': ftid,
            'frames': len(rows), 'wall_ms': {'median': statistics.median(walls) if walls else None,
                                             'p95': pct(walls, 0.95) if walls else None,
                                             'max': max(walls) if walls else None},
            'critical_thread': tname(crit_tid) if crit_tid is not None else None,
            'critical_tid': crit_tid,
            'threads': {str(t): v for t, v in per_thread.items()},
            'top': [{'func': '0x%08x' % f if f else '?', 'samples': n, 'share': n / max(1, nwin)}
                    for f, n in funcs.most_common(a.top)],
            'scale': a.scale, 'budget_ms': a.budget_ms}
    if dev is not None:
        head['device_ms'] = {'median': statistics.median(dev), 'p95': pct(dev, 0.95), 'max': max(dev),
                             'over_budget': sum(1 for d in dev if d > a.budget_ms)}
    o = open(a.out, 'w')
    o.write('# frames (per-thread CPU and frame pacing, emulated)\n\n')
    if not samples:
        o.write('- no samples: linhost ran without --profile, or the timers did not fire\n')
    o.write('- %d samples at %d Hz, %d frame marks; %d frames (frame thread %s, first %d skipped)\n' %
            (len(samples), hz, len(marks), len(rows), tname(ftid) if ftid is not None else '-', a.skip))
    if walls:
        o.write('- frame wall ms (emulated): median %.1f, p95 %.1f, max %.1f\n' %
                (head['wall_ms']['median'], head['wall_ms']['p95'], head['wall_ms']['max']))
    if crit_tid is not None:
        o.write('- critical-path thread: %s (critical in %d of %d frames)\n' %
                (tname(crit_tid), crit_count.get(crit_tid, 0), len(rows)))
    if dev is not None:
        o.write('- estimated device ms (critical thread x %.4g): median %.2f, p95 %.2f, max %.2f; over %.1f ms: %d of %d\n'
                % (a.scale, head['device_ms']['median'], head['device_ms']['p95'], head['device_ms']['max'],
                   a.budget_ms, head['device_ms']['over_budget'], len(rows)))
    else:
        o.write('- no --scale: emulated time only (no device estimate, no budget count)\n')
    o.write('\n## threads\n\n| thread | tid | CPU ms | busy % mean | busy % p95 | critical in frames |\n'
            '|---|---:|---:|---:|---:|---:|\n')
    for tid, v in sorted(per_thread.items(), key=lambda kv: -kv[1]['samples']):
        o.write('| %s | %d | %.0f | %.1f | %.1f | %d |\n' % (v['name'], tid, v['cpu_ms'], v['busy_mean'], v['busy_p95'],
                                                          v['critical_frames']))
    if funcs:
        o.write('\n## top functions (all threads, in the frames)\n\n| function | samples | share |\n|---|---:|---:|\n')
        for f, n in funcs.most_common(a.top):
            o.write('| %s | %d | %.1f%% |\n' % ('0x%08x' % f if f else '?', n, 100.0 * n / max(1, nwin)))
    if crit_tid is not None and funcs_by_t.get(crit_tid):
        c = funcs_by_t[crit_tid]
        o.write('\n## top functions of the critical thread (%s)\n\n| function | samples | share |\n|---|---:|---:|\n'
                % tname(crit_tid))
        for f, n in c.most_common(a.top):
            o.write('| %s | %d | %.1f%% |\n' % ('0x%08x' % f if f else '?', n, 100.0 * n / sum(c.values())))
    if rows:
        o.write('\n## slowest frames\n\n| frame | wall ms | critical thread | its CPU ms |\n|---:|---:|---|---:|\n')
        for i, r in sorted(enumerate(rows), key=lambda kv: -kv[1]['wall_ms'])[:10]:
            o.write('| %d | %.1f | %s | %.1f |\n' % (i + a.skip, r['wall_ms'], tname(r['critical']) if r['critical']
                                                     is not None else '-', r['critical_ms']))
    o.close()
    json.dump(head, open(a.out[:-3] + '.json' if a.out.endswith('.md') else a.out + '.json', 'w'), indent=1)
    print('frames: %d frames, critical %s, %d samples' % (len(rows), head['critical_thread'], len(samples)))
    return 0 if samples else 3


if __name__ == '__main__':
    sys.exit(main())
