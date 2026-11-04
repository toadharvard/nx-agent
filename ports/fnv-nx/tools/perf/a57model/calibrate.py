#!/usr/bin/env python3
"""calibrate.py - the error of the A57 model against published Cortex-A57 numbers (docs/PERF.md 12, METRIC of P0).

    calibrate.py [--out DIR] [--stats FILE --log FILE] [--json OUT] [--params params.json]

Builds the kernels and the plugin (build.sh), runs calib/bench.c under qemu-system-aarch64 -M virt -cpu cortex-a57
with a57trace.so (one region per kernel), prices each region with cost.py at the clock of the published number,
and compares with calib/truth.json. The error of a point is |model - truth| / truth. Reported:
  check MAPE  - mean over the points no parameter was fitted to and whose model value does not echo a parameter
                (the METRIC); check RMS - the same as a root mean square (the error bar perf_report.py applies);
  fit MAPE    - the same over the fit points (should be ~0);
  flat MAPE   - the error the old flat model (instructions / IPC 0.8, PERF.md 11) has on the same check points.
A point with `tol` counts as exact inside its tolerance (published as a bound, e.g. "over 50 cycles").
Runs in about a minute. Needs qemu-system-aarch64 (with -plugin), llvm-mca, aarch64-linux-gnu-gcc, gcc, glib.
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cost  # noqa: E402

FLAT_IPC = 0.8


def run_kernels(out, params):
    subprocess.run(['bash', os.path.join(HERE, 'build.sh'), out], check=True, capture_output=True)
    stats = os.path.join(out, 'stats.txt')
    if os.path.exists(stats):
        os.remove(stats)
    sim_args = ','.join(f'{k}={v}' for k, v in params['sim'].items())
    plugin = f"{os.path.join(out, 'a57trace.so')},out={stats},regions=on,{sim_args}"
    p = subprocess.run(['qemu-system-aarch64', '-M', 'virt', '-cpu', 'cortex-a57', '-m', '256M', '-nographic',
                        '-semihosting', '-kernel', os.path.join(out, 'bench.elf'), '-plugin', plugin],
                       capture_output=True, text=True, timeout=900)
    log = os.path.join(out, 'bench.log')
    with open(log, 'w') as f:
        f.write(p.stdout + p.stderr)
    if 'done' not in p.stdout + p.stderr:
        raise SystemExit(f'calibrate: kernels did not finish (rc {p.returncode}); see {log}')
    return stats, log


def kernels_of(log):
    ks = {}
    with open(log) as f:
        for i, m in enumerate(re.finditer(r'^K (\S+) (\d+)', f.read(), re.M)):
            ks[m.group(1)] = (f'r{i}', int(m.group(2)))
    return ks


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', default=os.path.join(HERE, 'build'))
    ap.add_argument('--stats')
    ap.add_argument('--log')
    ap.add_argument('--json')
    ap.add_argument('--params', default=os.path.join(HERE, 'params.json'))
    ap.add_argument('--truth', default=os.path.join(HERE, 'calib', 'truth.json'))
    a = ap.parse_args(argv)
    params = cost.load_params(a.params)
    os.makedirs(a.out, exist_ok=True)
    if a.stats:
        stats, log = a.stats, a.log
    else:
        stats, log = run_kernels(a.out, params)
    regions = cost.parse_stats(stats)
    ks = kernels_of(log)
    with open(a.truth) as f:
        truth = json.load(f)
    mca = cost.MCA(os.path.join(a.out, 'mca-cache.json'))
    cache = {}

    def per_unit(kernel, clock):
        key = (kernel, clock)
        if key not in cache:
            tag, units = ks[kernel]
            _f, tot = cost.price(regions[tag], params, clock, mca)
            cache[key] = (tot['cycles'] / units, tot['insns'] / FLAT_IPC / units, tot)
        return cache[key]

    rows = []
    for pt in truth['points']:
        clk = pt['clock']
        m, flat, tot = per_unit(pt['kernel'], clk)
        if pt['kind'] == 'diff':
            m2, flat2, _ = per_unit(pt['minus'], clk)
            m, flat = (m - m2) / pt['scale'], (flat - flat2) / pt['scale']
        elif pt['kind'] == 'bpc':
            m, flat = 1 / m, 1 / flat
        elif pt['kind'] == 'mbps':
            m, flat = clk * 1e3 / m, clk * 1e3 / flat
        v = pt['value']
        err = abs(m - v) / v
        ferr = abs(flat - v) / v
        tol = pt.get('tol', 0)
        err_t = max(0.0, err - tol)
        ferr_t = max(0.0, ferr - tol)
        rows.append({'name': pt['name'], 'kernel': pt['kernel'], 'role': pt['role'], 'truth': v, 'model': m,
                     'flat': flat, 'err': err_t, 'flat_err': ferr_t, 'clock': clk, 'src': pt['src']})
    mca.save()

    def mape(sel, key):
        xs = [r[key] for r in rows if sel(r)]
        return (100 * sum(xs) / len(xs), 100 * max(xs), len(xs)) if xs else (0, 0, 0)

    chk = mape(lambda r: r['role'] == 'check', 'err')
    cx = [r['err'] for r in rows if r['role'] == 'check']
    rms = 100 * (sum(x * x for x in cx) / len(cx)) ** 0.5 if cx else 0
    fit = mape(lambda r: r['role'] == 'fit', 'err')
    flt = mape(lambda r: r['role'] == 'check', 'flat_err')
    print(f"{'point':22} {'role':5} {'truth':>9} {'model':>9} {'err%':>7} {'flat':>9} {'err%':>7}")
    for r in rows:
        print(f"{r['name']:22} {r['role']:5} {r['truth']:9.2f} {r['model']:9.2f} {100 * r['err']:7.1f} "
              f"{r['flat']:9.2f} {100 * r['flat_err']:7.1f}")
    print(f"check MAPE {chk[0]:.1f} % (RMS {rms:.1f} %, max {chk[1]:.1f} %, {chk[2]} points); fit MAPE {fit[0]:.1f} % "
          f"({fit[2]} points); flat model on the checks {flt[0]:.1f} % (max {flt[1]:.1f} %)")
    preds = []
    for pr in truth.get('predictions', []):
        if pr['kernel'] in ks:
            m, flat, _ = per_unit(pr['kernel'], 1.02)
            preds.append({'name': pr['name'], 'units': pr['units'], 'cycles': m, 'flat': flat, 'note': pr['note']})
            print(f"predict {pr['name']:18} {m:8.2f} cycles {pr['units']} (flat {flat:.2f})")
    rep = {'schema': 'fnvnx-a57calib/1', 'check_mape_pct': round(chk[0], 2), 'check_max_pct': round(chk[1], 2),
           'check_rms_pct': round(rms, 2), 'check_points': chk[2],
           'fit_mape_pct': round(fit[0], 2), 'flat_check_mape_pct': round(flt[0], 2), 'points': rows,
           'predictions': preds}
    if a.json:
        with open(a.json, 'w') as f:
            json.dump(rep, f, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
