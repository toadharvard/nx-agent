#!/usr/bin/env python3
"""test_a57_calib_circular.py - V1 (D14) against P0-perfmodel 6e0a34b: "check = independent of every parameter"
(calib/truth.json) and the error bar "check MAPE 4.9 % over 14 points" (calibrate.py, the METRIC of P0).

cost.price on synthetic regions (the counts a57sim reports for a kernel, written here), priced twice with one
params.json value changed. A check point whose model value moves one-for-one with a parameter that was set from the
same published number is not held out: its error is 0 by construction and it dilutes the MAPE.
  E1  l2_latency (check, chase_96k: every load an L1 miss, L2 hit, on a pointer chain): model = cost.l2_lat exactly,
      and l2_lat is CC's 22-cycle number, the point's own truth
  E2  taken_l2btb (check, taken_1024: every taken branch from the L2 BTB): model = 1 + cost.btb_l2_bubble exactly,
      and btb_l2_bubble is set from the same CC sentence ("1 cycle from the L1 BTB, 2 from the L2 BTB")
  Q   the bound this puts on the published number: 4.9 % over 14 points with E1, E2 at 0 is >= 4.9 * 14 / 12 over
      the 12 that remain (calibrate.py itself needs qemu-system-aarch64 with plugins, not run here)
  M   informational: llvm-mca's A57 latencies the model inherits for micro-kernels with no calibration point
      (FP add/mul, FMA, divide, load-use, dual integer issue); divides are priced at the top of their
      data-dependent range
  python3 -I tests/v1/test_a57_calib_circular.py   -> one line per case, exit 1 when a case shows a defect
"""
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, '..', '..', 'tools', 'perf', 'a57model')
sys.path.insert(0, MODEL)
N = 1000


def block(pc, words, count, **kw):
    b = dict(pc=pc, n=len(words), count=count, mispred=0, taken=0, btb_l2=0, btb_miss=0, icl1=0, icl2=0, itlb=0,
             bytes=b''.join(w.to_bytes(4, 'little') for w in words))
    b.update(kw)
    return b


def mem(pc, count, **kw):
    m = dict(pc=pc, count=count, st=0, l1m=0, l2m=0, eff1=0.0, eff2=0.0, tlb1=0, tlb2=0, pf_dram=0, pf_l2=0, pf_hit=0,
             fwd=0, dram_wb=0)
    m.update(kw)
    return m


def region(B, M):
    return {'R': dict(icount=0, dram_rd=0, dram_wr=0, l2fill=0, blocks=0, accesses=0), 'B': B, 'M': M}


def main():
    try:
        import cost
    except ImportError as e:
        print('skip: %s' % e)
        return 0
    if cost.CS is None or not shutil.which('llvm-mca'):
        print('skip: capstone or llvm-mca missing')
        return 0
    try:
        work = tempfile.mkdtemp(prefix='v1-a57c-')
    except OSError as e:
        print('mktemp failed: %s' % e)
        return 2
    bad = 0
    try:
        params = cost.load_params(os.path.join(MODEL, 'params.json'))
        truth = {p['name']: p for p in json.load(open(os.path.join(MODEL, 'calib', 'truth.json')))['points']}
        mca = cost.MCA(os.path.join(work, 'mca.json'))

        def per(reg, p):
            return cost.price(reg, p, 1.785, mca)[1]['cycles'] / N

        def vary(name, reg, key, vals):
            out = []
            for v in vals:
                p = copy.deepcopy(params)
                p['cost'][key] = v
                out.append(per(reg, p))
            return out

        # E1: ldr x0, [x0]; subs x1, x1, #1; b.ne -8 - a chase, every load L1 miss / L2 hit
        chase = region([block(0x1000, [0xf9400000, 0xf1000421, 0x54ffffc1], N, taken=N)],
                       [mem(0x1000, N, l1m=N, eff1=float(N))])
        vals = [22, 30, 40]
        got = vary('l2_latency', chase, 'l2_lat', vals)
        echo = all(abs(g - v) < 0.05 for g, v in zip(got, vals))
        t = truth['l2_latency']
        print('E1: %s - %s (role %s, truth %s): model %s for l2_lat %s' % (
            'DEFECT' if echo and t['role'] == 'check' else 'ok', t['name'], t['role'], t['value'],
            ', '.join('%.2f' % g for g in got), vals))
        bad += echo and t['role'] == 'check'
        # E2: b +4 - every taken branch from the L2 BTB
        taken = region([block(0x2000, [0x14000001], N, taken=N, btb_l2=N)], [])
        vals = [1, 2, 3]
        got = vary('taken_l2btb', taken, 'btb_l2_bubble', vals)
        echo = all(abs(g - (1 + v)) < 0.05 for g, v in zip(got, vals))
        t = truth['taken_l2btb']
        print('E2: %s - %s (role %s, truth %s): model %s for btb_l2_bubble %s' % (
            'DEFECT' if echo and t['role'] == 'check' else 'ok', t['name'], t['role'], t['value'],
            ', '.join('%.2f' % g for g in got), vals))
        bad += echo and t['role'] == 'check'
        nchk = sum(1 for p in truth.values() if p['role'] == 'check')
        print('Q: the published check MAPE 4.9 %% over %d points is >= %.1f %% over the %d held out (E1, E2 at 0); '
              'tests/s15 LIMIT_CHECK 6 %%' % (nchk, 4.9 * nchk / (nchk - 2), nchk - 2))
        # M: llvm-mca A57 latencies (cycles per iteration of a dependent chain / of 4 independent ops)
        ks = [('fadd d chain', 'fadd d0, d0, d1', 5), ('fmul d chain', 'fmul d0, d0, d1', 5),
              ('fmadd d chain', 'fmadd d0, d0, d1, d2', 9), ('ldr chain (load-use)', 'ldr x0, [x0]', 4),
              ('4 independent add (2 int pipes)', 'add x1,x2,x3\nadd x4,x5,x6\nadd x7,x8,x9\nadd x10,x11,x12', 2),
              ('fdiv d chain (TRM 17-32)', 'fdiv d0, d0, d1', None), ('fdiv s chain (TRM 7-17)', 'fdiv s0, s0, s1', None),
              ('sdiv x chain (TRM 4-36)', 'sdiv x0, x0, x1', None)]
        for name, src, want in ks:
            p = subprocess.run(['llvm-mca', '-mtriple=aarch64', '-mcpu=cortex-a57', '-iterations=100'], input=src + '\n',
                               capture_output=True, text=True)
            m = re.search(r'Total Cycles:\s+(\d+)', p.stdout)
            c = int(m.group(1)) / 100 if m else None
            print('M: %s - %s: %s cycles%s' % ('ok' if want is None or (c and abs(c - want) < 0.2) else 'note', name,
                                               '%.1f' % c if c else '?', '' if want is None else ' (expected %d)' % want))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
