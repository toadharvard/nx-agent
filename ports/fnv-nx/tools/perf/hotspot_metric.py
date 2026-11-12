#!/usr/bin/env python3
"""hotspot_metric.py - S15-simd's metric: estimated cycles per frame of the top-20 CPU hotspot functions at 1.02 GHz
(docs/PERF.md 11.6).

    hotspot_metric.py [--model tools/perf/budget_model.json] [--top 20] [--json]

Per row of the model's `hotspot_functions`: A64 instructions per call (mid of `per_call_insn`; a measured count [M] or
an estimate [E]) x calls per frame (mid of `calls`, [E]) / IPC (the model's `ipc` assumption, 0.8) = cycles per
frame. A row with `per_call_cycles` (an A57 pipeline model's cycles per call, P0-perfmodel) uses that instead of
instructions / IPC. Rows ranked by cycles; the metric is the sum of the top N, in M cycles per frame, and its time
at 1020 MHz. The calls per frame stay the model's estimate until frameprof profiles real frames, so the metric
moves only with the per-call costs: that is what this stream changes.
"""
import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def mid(v):
    return (v[0] + v[1]) / 2.0 if isinstance(v, (list, tuple)) else float(v)


def rows(model):
    ipc = next(a['value'] for a in model['assumptions'] if a['id'] == 'ipc')
    out = []
    for h in model.get('hotspot_functions', []):
        calls = mid(h['calls'])
        insn = mid(h['per_call_insn'])
        if 'cpi' in h:   # the A57 CPI of the code that runs: lo = throughput bound, hi = one call (PERF.md 11.9)
            pc = h['per_call_insn']
            cyc = (pc[0] * h['cpi']['lo'] + pc[1] * h['cpi']['hi']) / 2
        else:
            cyc = mid(h['per_call_cycles']) if 'per_call_cycles' in h else insn / ipc
        out.append(dict(id=h['id'], name=h['name'], per_call_insn=insn, per_call_cycles=round(cyc, 2), calls=calls,
                        mcycles=cyc * calls / 1e6, basis=h.get('per_call_basis', 'E'),
                        cycles_basis='cpi' if 'cpi' in h else ('model' if 'per_call_cycles' in h else 'insn/ipc %.2f' % ipc)))
    out.sort(key=lambda r: -r['mcycles'])
    return out


def metric(model, top=20):
    rs = rows(model)[:top]
    return sum(r['mcycles'] for r in rs), rs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--model', default=os.path.join(HERE, 'budget_model.json'))
    ap.add_argument('--top', type=int, default=20)
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    total, rs = metric(json.load(open(a.model)), a.top)
    if a.json:
        print(json.dumps(dict(schema='fnvnx-hotspot-metric/1', mcycles=round(total, 4),
                              ms_1020=round(total / 1.020, 3), rows=rs), indent=1))
        return
    print('%-4s %-24s %10s %10s %8s %10s' % ('rank', 'id', 'insn/call', 'cyc/call', 'calls', 'M cycles'))
    for i, r in enumerate(rs, 1):
        print('%-4d %-24s %10.1f %10.1f %8.0f %10.3f' % (i, r['id'], r['per_call_insn'], r['per_call_cycles'],
                                                        r['calls'], r['mcycles']))
    print('metric: %.3f M cycles per frame (top %d) = %.2f ms at 1020 MHz' % (total, a.top, total / 1.020))


if __name__ == '__main__':
    main()
