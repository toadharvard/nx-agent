#!/usr/bin/env python3
"""test_p6n_engine_path.py - V1 (D14) against P6n b34a429 / bbe6e74: engine_path.py's static count of the engine's
D3D9 draw path ("2,303 AArch64 instructions per draw, 2,280-2,355") and engine_fold.py's 60 fps budget in
report.json.

The count itself needs the prepared objects c/b6585b87bb73 and the LOCAL spec (exe vtables), which are not in this
container; so the counter runs on a synthetic object, and the committed report.json is re-derived by arithmetic.
  L1  nested loops in one function (outer 4 trips, inner 4 trips, no calls): the dynamic count is 66 instructions.
      Counter.count adds (trips - 1) x the loop body's OWN words per header, so the inner loop's extra trips are
      not repeated for each outer trip (the outer body's expectation uses own[b], not own[b] + extra_exp[b]);
      exp and max both under-count. Any spec loop that holds another loop of the same function (pass loop around
      a stage or constant-entry loop) is under-counted the same way
  L2  control: a single loop of 4 trips: exact
  B1  report.json engine_d3d9_path: per-draw x draws = per-frame instructions, ms at IPC 0.6 / 1.02 GHz,
      us per draw, the replaced assumption 10-20 us x 3710 draws
  B2  the 60 fps budget: 16.7 ms, gate on mix_hi (the larger mix, the worst frame of the two, J-0150) at the hi
      end, '1.02_with_engine_kept' = max(main gate + kept, render gate) / 16.7
  python3 -I tests/v1/test_p6n_engine_path.py   -> one line per case, exit 1 when a case shows a defect
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PERF = os.path.join(HERE, '..', '..', 'render', 'native', 'perf')
sys.path.insert(0, PERF)

NESTED = """
    .text
    .globl f
    .type f, %function
f:
    mov w1, #0
1:  mov w2, #0
2:  add w2, w2, #1
    cmp w2, #4
    b.ne 2b
    add w1, w1, #1
    cmp w1, #4
    b.ne 1b
    ret
    .size f, .-f
    .globl g
    .type g, %function
g:
    mov w2, #0
3:  add w2, w2, #1
    cmp w2, #4
    b.ne 3b
    ret
    .size g, .-g
"""


def main():
    for t in ('aarch64-linux-gnu-as', os.environ.get('OBJDUMP', 'llvm-objdump')):
        if not shutil.which(t):
            print('skip: %s missing' % t)
            return 0
    import engine_path
    try:
        work = tempfile.mkdtemp(prefix='v1-p6n-')
    except OSError as e:
        print('mktemp failed: %s' % e)
        return 2
    bad = 0
    try:
        src = os.path.join(work, 't.s')
        open(src, 'w').write(NESTED)
        od = os.path.join(work, 'obj'); os.makedirs(od)
        subprocess.run(['aarch64-linux-gnu-as', '-o', os.path.join(od, 't.o'), src], check=True)
        lo = os.path.join(work, 'link'); os.makedirs(lo)
        open(os.path.join(lo, 'addrmap.tsv'), 'w').write('00401000\tF\t-\tf\tt.o\n00402000\tF\t-\tg\tt.o\n')
        open(os.path.join(lo, 'refs.tsv'), 'w').write('')
        ov = os.path.join(work, 'overrides.tsv'); open(ov, 'w').write('')
        # f: header of the outer loop at +0x4 (mov w2), of the inner at +0x8; g: header at +0x4
        spec = {'loops': {'401000': {'4': 4, '8': 4}, '402000': {'4': 4}}}
        C = engine_path.Counter(od, lo, spec, ov)
        mn, ex, mx, _ = C.count(('t.o', 'f'))
        truth = 1 + 4 * (1 + 4 * 3 + 3) + 1        # mov; 4 x (mov, 4 x inner body, outer tail); ret = 66
        d = abs(ex - truth) > 0.5
        bad += d
        print('L1: %s - nested loops 4 x 4: dynamic %d instructions, counted exp %.1f, max %d (%.0f %% of it)' % (
            'DEFECT' if d else 'ok', truth, ex, mx, 100.0 * ex / truth))
        mn, ex, mx, _ = C.count(('t.o', 'g'))
        truth = 1 + 4 * 3 + 1
        print('L2: %s - single loop of 4 trips: dynamic %d, counted exp %.1f' % (
            'ok' if abs(ex - truth) < 0.5 else 'note (single loop off)', truth, ex))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    rep = json.load(open(os.path.join(PERF, 'report.json')))
    e = rep.get('engine_d3d9_path')
    if not e:
        print('B1: skip - no engine_d3d9_path in report.json')
        return 1 if bad else 0
    a = rep['assumptions']; ipc, clk = a['ipc'], a['clock_ghz']
    pd = e['per_draw_instr']['exp']; n = e['frame']['mix_hi']['draws']
    ms = pd * n / (ipc[0] * clk[0] * 1e9) * 1e3
    us = pd / (ipc[0] * clk[0] * 1e3)
    ok1 = (e['frame']['mix_hi']['instr']['total'] == round(pd * n) and abs(e['frame']['mix_hi']['ms_gate']['total'] - ms) < 0.01
           and abs(e['per_draw_us']['gate'] - us) < 0.01 and abs(n * 10e-3 - 37.1) < 1e-9 and abs(n * 20e-3 - 74.2) < 1e-9)
    print('B1: %s - %d instr/draw x %d draws = %d; %.3f ms at IPC %s / %s GHz; %.3f us/draw; assumed 10-20 us x %d = '
          '%.1f-%.1f ms' % ('ok' if ok1 else 'DEFECT', pd, n, pd * n, ms, ipc[0], clk[0], us, n, n * 1e-2, n * 2e-2))
    bad += not ok1
    s = rep['summary']; fm = s['frame_ms']; b = a['targets_ms']['60']
    worst_mix = max(('mix_lo', 'mix_hi'), key=lambda m: rep['frame'][m]['draws'])
    w = max(fm['main']['gate'] + e['frame']['mix_hi']['ms_gate']['kept'], fm['render']['gate'])
    ok2 = (b == 16.7 and worst_mix == 'mix_hi' and fm['main']['gate'] == fm['main']['hi']
           and abs(s['targets_budget_fraction']['60']['1.02_with_engine_kept'] - round(w / b, 3)) < 1e-9)
    print('B2: %s - 60 fps = %.1f ms (1000/60 = %.2f); gate on %s hi; worst thread %.3f ms -> %.3f of the budget; '
          'frame_handheld_720p %.3f (GPU, > 1: 60 fps not met)' % (
              'ok' if ok2 else 'DEFECT', b, 1000 / 60, worst_mix, w, w / b,
              s['targets_budget_fraction']['60']['frame_handheld_720p']))
    bad += not ok2
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
