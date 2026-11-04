#!/usr/bin/env python3
"""perf_report.py - fnvnx-perf/1: the A57 model's per-function CPU time of one head (docs/PERF.md 12).

    perf_report.py <stats.txt> --elf fnvnx.elf --out <head12>-perf.json [--md report.md] [--calib calib.json]
                   [--head C12] [--link C12] [--workload TEXT] [--frame-ms 16.7] [--top 40] [--mca-cache F]

<stats.txt> is uctrace.py's (main thread). Prices it with cost.py at 1.02 and 1.785 GHz and writes:

  {"schema": "fnvnx-perf/1", "head", "link", "workload", "model": {calibration error, method},
   "totals": {instructions, cycles_1020, ms_1020, ms_1785, ipc_1020, coverage_pct, ...},
   "hotspots": [{"function", "orig", "instructions", "cycles_est", "cycles_lo", "cycles_hi", "ms_1020",
                 "ms_1785", "cache_misses": {"l1d", "l2", "l1i"}, "mispredicts", "ipc", "pct_of_frame",
                 "pct_of_budget", "bound"}, ...]}

pct_of_frame: share of the workload's main-thread cycles (when the workload is one frame, of that frame);
pct_of_budget: share of --frame-ms (default 16.7 ms, 60 fps) at 1.02 GHz. cycles_lo/hi: the calibration's check
error (calib.json's check_mape_pct) either way. coverage_pct: main-thread instructions whose block llvm-mca priced
(the rest fall back to the flat IPC 0.8). bound: what limits the function in the model (core, memory, frontend,
bandwidth). S15-threads, S15-simd and the render machine read hotspots[]. Names and counts only.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cost  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('stats')
    ap.add_argument('--elf', action='append', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--md')
    ap.add_argument('--calib')
    ap.add_argument('--params', default=os.path.join(HERE, 'params.json'))
    ap.add_argument('--head', default='')
    ap.add_argument('--link', default='')
    ap.add_argument('--workload', default='')
    ap.add_argument('--frame-ms', type=float, default=16.7)
    ap.add_argument('--top', type=int, default=40)
    ap.add_argument('--mca-cache')
    ap.add_argument('--region')
    ap.add_argument('--funcs', help="funcmodel.py's JSON: the per-frame function rows (frame_hotspots[])")
    ap.add_argument('--mt', help="mtmodel.py's JSON: the 3-core frame model (frame_model)")
    a = ap.parse_args(argv)
    params = cost.load_params(a.params)
    mca = cost.MCA(a.mca_cache)
    syms = cost.Symbols(a.elf)
    regs = cost.parse_stats(a.stats)
    tag = a.region or next(iter(regs))
    reg = regs[tag]
    meta = {}
    with open(a.stats) as f:
        for ln in f:
            if ln.startswith('# uctrace: main '):
                p = ln.split()
                meta.update({'main': int(p[3]), 'other': int(p[5])})
            elif ln.startswith('# uctrace: stopped: '):
                meta['stopped'] = ln.split(': ', 2)[2].strip()
    f1, t1 = cost.price(reg, params, 1.02, mca, syms)
    f2, t2 = cost.price(reg, params, 1.785, mca, syms)
    mca.save()
    err = None
    calib = {}
    if a.calib and os.path.exists(a.calib):
        with open(a.calib) as f:
            calib = json.load(f)
        err = calib.get('check_mape_pct')
    e = (err or 0) / 100
    tot_c = t1['cycles'] or 1
    rows = []
    for name, r in sorted(f1.items(), key=lambda kv: -kv[1]['cycles'])[:a.top]:
        c1, c2 = r['cycles'], f2[name]['cycles']
        rows.append({
            'function': name, 'orig': syms.original(name), 'instructions': int(r['insns']),
            'cycles_est': round(c1), 'cycles_lo': round(c1 * (1 - e)), 'cycles_hi': round(c1 * (1 + e)),
            'ms_1020': round(c1 / 1.02e6, 4), 'ms_1785': round(c2 / 1.785e6, 4),
            'cache_misses': {'l1d': int(r['l1d_miss']), 'l2': int(r['l2_miss']), 'l1i': 0},
            'mispredicts': int(r['mispred']), 'ipc': round(r['insns'] / c1, 3) if c1 else 0,
            'pct_of_frame': round(100 * c1 / tot_c, 3), 'pct_of_budget': round(100 * c1 / 1.02e6 / a.frame_ms, 3),
            'bound': cost.bound_of(r)})
    # L1I misses per function (blocks), kept apart from the data lines
    il1 = {}
    for b in reg['B']:
        n = syms.name(b['pc'])
        il1[n] = il1.get(n, 0) + b['icl1'] + b['icl2']
    for row in rows:
        row['cache_misses']['l1i'] = il1.get(row['function'], 0)
    priced = t1['insns'] - t1['unpriced_insns']
    out = {
        'schema': 'fnvnx-perf/1', 'head': a.head, 'link': a.link, 'workload': a.workload,
        'model': {'method': 'a57sim (QEMU TCG plugin / Unicorn) + llvm-mca -mcpu=cortex-a57, docs/PERF.md 12',
                  'check_mape_pct': err, 'check_max_pct': calib.get('check_max_pct'),
                  'flat_check_mape_pct': calib.get('flat_check_mape_pct'),
                  'note': 'the error bar is the calibration error on published A57 microbenchmarks; on compiled game '
                          'code the composition error is larger and not yet measured on hardware'},
        'totals': {'instructions': int(t1['insns']), 'cycles_1020': round(t1['cycles']),
                   'ms_1020': round(t1['cycles'] / 1.02e6, 3), 'ms_1785': round(t2['cycles'] / 1.785e6, 3),
                   'ipc_1020': round(t1['insns'] / tot_c, 3), 'coverage_pct': round(100 * priced / max(t1['insns'], 1), 2),
                   'main_instructions_traced': meta.get('main', int(t1['insns'])),
                   'other_threads_instructions': meta.get('other', 0), 'stopped': meta.get('stopped', ''),
                   'breakdown_1020': {k: round(t1[k]) for k in ('core', 'branch', 'ifetch', 'memlat')},
                   'l1d_misses': int(t1['l1d_miss']), 'l2_misses': int(t1['l2_miss']), 'mispredicts': int(t1['mispred'])},
        'hotspots': rows,
    }
    if a.funcs and os.path.exists(a.funcs):
        with open(a.funcs) as f:
            fm = json.load(f)
        out['frame_hotspots'] = [{'id': r['id'], 'functions': r['funcs'], 'minsn_frame': r.get('minsn_frame'),
                                  'cpi': [r.get('cpi'), r.get('cpi_once')], 'cycles_per_call': r.get('cycles_per_call'),
                                  'mcycles_frame': r.get('mcycles_frame'), 'ms_1020': r.get('ms_1020'),
                                  'basis': 'static A57 model (funcmodel.py) x instructions of PERF.md 11.4', 'src': r.get('src')}
                                 for r in fm.get('rows', [])]
        out['frame_coverage'] = fm.get('coverage')
        # per_call_cycles proposals for budget_model.json's hotspot_functions (tools/perf/hotspot_metric.py reads them)
        out['per_call_cycles'] = [r for r in fm.get('budget_rows', []) if r.get('per_call_cycles')]
    with open(a.out, 'w') as f:
        json.dump(out, f, indent=1)
    T = out['totals']
    lines = [f"# A57 model {a.head} ({a.workload})", '',
             f"- main thread: {T['instructions']:,} instructions, {T['cycles_1020']:,} cycles, IPC {T['ipc_1020']}, "
             f"{T['ms_1020']} ms at 1.02 GHz, {T['ms_1785']} ms at 1.785 GHz; stopped: {T['stopped']}",
             f"- coverage: {T['coverage_pct']} % of the main thread's instructions priced by llvm-mca",
             f"- model error (calibration, check points): {err} % mean, max {calib.get('check_max_pct')} % "
             f"(flat IPC 0.8: {calib.get('flat_check_mape_pct')} %)",
             f"- cycles at 1.02 GHz: core {T['breakdown_1020']['core']:,}, branch {T['breakdown_1020']['branch']:,}, "
             f"ifetch {T['breakdown_1020']['ifetch']:,}, memory latency {T['breakdown_1020']['memlat']:,}", '',
             '| # | function | orig | instructions | cycles | ms 1.02 | IPC | L1D miss | L2 miss | mispred | % | bound |',
             '|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|']
    for i, r in enumerate(rows[:25], 1):
        lines.append(f"| {i} | `{r['function'][:60]}` | {r['orig']} | {r['instructions']:,} | {r['cycles_est']:,} | "
                     f"{r['ms_1020']} | {r['ipc']} | {r['cache_misses']['l1d']:,} | {r['cache_misses']['l2']:,} | "
                     f"{r['mispredicts']:,} | {r['pct_of_frame']} | {r['bound']} |")
    if out.get('frame_hotspots'):
        fc = out.get('frame_coverage') or {}
        lines += ['', f"Per-frame function rows (PERF.md 11.4 instructions x the static A57 model): {fc.get('covered_minsn')} M "
                      f"of {fc.get('main_minsn_frame')} M main-thread instructions ({fc.get('covered_pct')} %), "
                      f"{fc.get('covered_ms_1020')} ms at 1.02 GHz (flat IPC 0.8: {fc.get('covered_ms_1020_flat_ipc08')} ms)", '',
                  '| row | M insns/frame | CPI (steady-once) | cycles/call | ms 1.02 |', '|---|---:|---|---|---|']
        for r in out['frame_hotspots']:
            lines.append(f"| {r['id']} | {r['minsn_frame']} | {r['cpi'][0]}-{r['cpi'][1]} | {r['cycles_per_call']} | {r['ms_1020']} |")
    if a.mt and os.path.exists(a.mt):
        with open(a.mt) as f:
            mt = json.load(f)
        out['frame_model'] = {'schema': mt['schema'], 'cpi': mt['cpi'], 'unpriced_items': mt['unpriced_items'],
                              'wake_us': mt['wake_us'],
                              'scenarios': [{'id': s['id'], 'results': s['results']} for s in mt['scenarios']]}
        lines += ['', f"3-core frame model (mtmodel.py; A57 CPI for {', '.join(mt['cpi'])}; 1/IPC 0.8 for the rest):", '',
                  '| scenario | MHz | frame ms lo / mid / hi | main path | main waits | cores (mid) | critical |',
                  '|---|---:|---|---:|---:|---|---|']
        for s in mt['scenarios']:
            for clk, r in s['results'].items():
                m = r['mid']
                lines.append(f"| {s['id']} | {clk} | {r['lo']['frame_ms']} / {m['frame_ms']} / {r['hi']['frame_ms']} | "
                             f"{m['main_path_ms']} | {m['main_wait_ms']} | {m['core_ms']} | {m['critical']} |")
    if out.get('per_call_cycles'):
        lines += ['', 'per_call_cycles for budget_model.json hotspot_functions (static A57 model; overrides on their own code):', '',
                  '| id | per_call_insn | CPI | per_call_cycles |', '|---|---|---|---|']
        for r in out['per_call_cycles']:
            lines.append(f"| {r['id']} | {r['per_call_insn']} | {r['cpi']} | {r['per_call_cycles']} |")
    text = '\n'.join(lines) + '\n'
    if a.md:
        with open(a.md, 'w') as f:
            f.write(text)
    print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
