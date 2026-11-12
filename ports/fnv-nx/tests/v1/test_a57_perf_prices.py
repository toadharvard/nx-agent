#!/usr/bin/env python3
"""test_a57_perf_prices.py - V1 (D14) against the commits that move S15's and P6n's numbers onto P0-perfmodel's A57
model: 48a9b4f / ce64150 / 17873de (S15-simd, per_call_cycles and the cycle decisions of PERF.md 11.6), 45e8724
(S15-threads, frame_budget.py --perf) and 2f5880c (P6n report.json, the 60 fps gate).

    python3 -I tests/v1/test_a57_perf_prices.py     (FNV_DECOMP=<decomp checkout>, default ext/fnv_decomp)

The decomp bodies are copied into a tempfile.mkdtemp directory at run time (test_s15simd_decomp.build,
test_slerp_decomp.extract; no decomp text in git) and compiled for AArch64 ILP32; the overrides with build.sh's flags.
Every price below is funcmodel.price_funcs (llvm-mca -mcpu=cortex-a57, the method of PERF.md 12.5 / 12.6).
  P1  48a9b4f "per_call_insn x [steady CPI, one-call CPI] of the code that runs now", values "taken as sent":
      fn_pass_bucket's and fn_gsk's per_call_cycles are priced at 82 and 571 instructions (12.6) while the rows run
      54 and 539; implied CPI outside the 12.6 CPI of the same code
  S2  48a9b4f (12.6 / 11.3 "latency-bound"): the steady price is llvm-mca's loop over a function body, so a register
      the body reads and writes (Slerp's t in s0) chains call i+1 to call i. Redefining only the live-in registers
      from xzr at the top (a prefix of a few cycles) drops the override's steady price by a third
  S1  17873de "Slerp ... not worse in cycles": the decomp (Slerp + FUN_00a6dc30 + normalisation, 47 / 29 / 58 per call,
      PERF.md 11.6) and the override (88) on the model's two bounds: steady +39 %, one call -23 %. The -4 % "tie" is
      the mid of two bounds that disagree in sign by far more than the bar
  CE1 ce64150 "fewer instructions, more A57 cycles": the transpose product's steady price is the same on both sides
      and its mid +4.7 % is inside the 5.7 % bar 17873de applies; NiBound::Update's steady price drops (the bounds
      disagree in sign, as for S1). Only the one-call bound is worse for both (NiBound control in S2: no false chain)
  T1  45e8724 "priced rows run at their own A57 cost": apply_perf converts cycles at the mid IPC, then the lo / hi
      cases divide by IPC 1.0 / 0.6, so a priced row's cycles are x0.8 / x1.33 in the cases the verdicts read
  R1  2f5880c: report.json's gate field is "owner: 60 fps for the whole frame at 720p handheld" but its CPU part is
      the renderer only; the whole-frame worst frame of docs/PERF_budget.json (which reads this report) misses 16.7 ms
  N   notes: 952d893's vtcover.py counts every addrmap F row as native, including CRT providers such as _purecall
      (os_fatal); report.json's fractions recomputed from its own ms
Exit 1 when a case shows a defect.
"""
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
MODEL = os.path.join(PORT, 'tools', 'perf', 'a57model')
sys.path[:0] = [MODEL, os.path.join(PORT, 'tools', 'perf'), HERE]
os.environ.setdefault('FNV_DECOMP', os.path.join(PORT, 'ext', 'fnv_decomp'))
OVR = os.path.join(PORT, 'tools', 'link', 'overrides')
CPI_126 = {'fn_pass_bucket': (82, 0.83, 2.01), 'fn_gsk': (571, 1.11, 1.45)}   # PERF.md 12.6: insn, CPI steady - once
bad = 0


def say(case, defect, text):
    global bad
    bad += bool(defect)
    print('%s: %s - %s' % (case, 'DEFECT' if defect else 'ok', text))


def sh(argv):
    import subprocess
    r = subprocess.run(argv, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(' '.join(argv[:2]) + ': ' + r.stderr[-600:])


def p1(model):
    rows = {h['id']: h for h in model['hotspot_functions']}
    for rid, (n126, lo, hi) in CPI_126.items():
        h = rows[rid]
        pc, cy = h['per_call_insn'], h['per_call_cycles']
        imp = (cy[0] / pc[0], cy[1] / pc[1])
        fix = [round(pc[0] * lo, 1), round(pc[1] * hi, 1)]
        say('P1', imp[0] > lo * 1.03 or imp[1] > hi * 1.03,
            '%s: per_call_insn %s, per_call_cycles %s = %d x [%.3f, %.3f] (12.6 prices %d instructions); implied CPI '
            '%.3f / %.3f; at the row\'s own count %s' % (rid, pc, cy, n126, cy[0] / n126, cy[1] / n126, n126,
                                                         imp[0], imp[1], fix))


def reg_base(n):
    m = re.match(r'^[wx](\d+)$', n)
    if m:
        return 'x' + m.group(1)
    m = re.match(r'^[bhsdqv](\d+)$', n)
    return 'v' + m.group(1) if m else n


def steady_unchained(funcmodel, cost, elf, name):
    """llvm-mca steady cycles of the function body as priced, and with the registers it reads before writing (and
    also writes) redefined from xzr first: the next call's arguments do not depend on this call's results"""
    import capstone
    cs = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    cs.detail = True
    syms, img = cost.Symbols([elf]), funcmodel.Image(elf)
    _n, lo, hi = funcmodel.func_range(syms, name, img)
    code = img.read(lo, hi - lo)
    text = cost.mca_text(cost.disasm(lo, code))
    written, live, wr = set(), [], set()
    for ins in cs.disasm(code, lo):
        r, w = ins.regs_access()
        for x in r:
            b = reg_base(ins.reg_name(x))
            if b not in written and b not in live and b[0] in 'xv' and b != 'xzr':
                live.append(b)
        for x in w:
            b = reg_base(ins.reg_name(x))
            written.add(b)
            wr.add(b)
    brk = [b for b in live if b in wr]
    pre = [('mov %s, xzr' % b) if b[0] == 'x' else ('fmov d%s, xzr' % b[1:]) for b in brk]
    c = cost.MCA().cycles({'a': text, 'b': pre + text, 'p': pre})
    return len(text), c['a'], c['b'], c['p'], brk


def main():
    try:
        import cost
        import funcmodel
        import frame_budget
    except ImportError as e:
        print('skip: %s' % e)
        return 0
    model = json.load(open(os.path.join(PORT, 'tools', 'perf', 'budget_model.json')))
    p1(model)
    # T1 (no toolchain needed)
    sl = next(h for h in model['hotspot_functions'] if h['id'] == 'fn_slerp')
    ipc = next(a for a in model['assumptions'] if a['id'] == 'ipc')
    X = 300.0
    b0 = frame_budget.build(model)
    b1 = frame_budget.build(model, perf={'schema': 'fnvnx-perf/1',
                                          'per_call_cycles': [{'id': 'fn_slerp', 'per_call_cycles': [X, X]}]})
    out = []
    worst = 0
    for k, ip, ca, ins in (('lo', ipc['range'][1], sl['calls'][0], sl['per_call_insn'][0]),
                           ('mid', ipc['value'], frame_budget.mid(sl['calls']), frame_budget.mid(sl['per_call_insn'])),
                           ('hi', ipc['range'][0], sl['calls'][1], sl['per_call_insn'][1])):
        got = (b1['scenarios'][0]['main_minsn'][k] - b0['scenarios'][0]['main_minsn'][k]) * 1e6 / ip
        want = X * ca - ins * ca / ip
        worst = max(worst, abs(got - want) / abs(want))
        out.append('%s (IPC %.1f) %.3f M cycles, want %.3f' % (k, ip, got / 1e6, want / 1e6))
    say('T1', worst > 0.05, 'fn_slerp priced at %d cycles per call, main-core cycles added per frame: %s' % (X, '; '.join(out)))
    # R1 (report.json, PERF_budget.json)
    rep = json.load(open(os.path.join(PORT, 'render', 'native', 'perf', 'report.json')))
    s = rep['summary']
    fr = rep['frame']['mix_hi']['ms']['1.02']
    cpu = max(fr['main']['hi'], fr['render']['hi'])
    f60 = s['targets_budget_fraction']['60']
    print('N: report.json 60 fps fractions from its own ms: 1.02 %.3f (published %s), frame_handheld_720p %s, main '
          'hi %.3f ms' % (cpu / 16.7, f60['1.02'], f60['frame_handheld_720p'], fr['main']['hi']))
    pb = json.load(open(os.path.join(PORT, 'docs', 'PERF_budget.json')))
    pl = next(x for x in pb['scenarios'] if x['id'] == 'placed')['results']['1020']['worst_frame_ms']
    whole = 'whole frame' in s['frame_budget']['rule'] and 'renderer only' in s['targets_rule']
    say('R1', whole and f60['frame_handheld_720p'] <= 1.0 and pl['mid'] > 16.7,
        'gate field "%s" reads %s (pass) from a CPU term that "%s"; PERF_budget.json (render input: this report) '
        'as-built worst frame at 1020 MHz %s ms (mid %.1f > 16.7)' % (s['frame_budget']['rule'][:46],
                                                                         f60['frame_handheld_720p'],
                                                                         s['targets_rule'][s['targets_rule'].index('covers'):][:30], pl, pl['mid']))
    vt = open(os.path.join(PORT, 'tools', 'perf', 'vtcover.py')).read()
    print('N: vtcover.py accepts kinds %s' % re.search(r"not in (\([^)]*\))", vt).group(1))
    if not (cost.CS is not None and shutil.which('llvm-mca') and shutil.which('aarch64-linux-gnu-g++')):
        print('skip S1, S2, CE1: capstone, llvm-mca or aarch64-linux-gnu-g++ missing')
        return 1 if bad else 0
    if not os.path.isdir(os.path.join(os.environ['FNV_DECOMP'], 'src')):
        print('skip S1, S2, CE1: no decomp at %s' % os.environ['FNV_DECOMP'])
        return 1 if bad else 0
    try:
        work = tempfile.mkdtemp(prefix='v1-a57p-')
    except OSError as e:
        print('mktemp failed: %s' % e)
        return 2
    try:
        import test_s15simd_decomp as TD
        import test_slerp_decomp as TS
        # the overrides, as build.sh compiles them
        fl = TD.build_c_flags()
        objs = []
        for src in ('slerp_00a6e330.c', 'nibound_update_00a7fdd0.c', 'mat3_tmul_00a588a0.c'):
            o = os.path.join(work, src[:-2] + '.o')
            sh(['aarch64-linux-gnu-gcc', *fl, '-c', os.path.join(OVR, src), '-o', o])
            objs.append(o)
        ov = os.path.join(work, 'ov.elf')
        sh(['aarch64-linux-gnu-ld', '-m', 'aarch64linux32', '-e', '0', '--unresolved-symbols=ignore-all', '-o', ov, *objs])
        # the decomp's Slerp unit (test_slerp_decomp) and NiBound / matrix product (test_s15simd_decomp)
        with open(os.path.join(work, 'decomp_slerp.inc'), 'w') as f:
            f.write(TS.extract(open(TS.DECOMP).read()))
        u, sle = os.path.join(work, 'slerp_unit.o'), os.path.join(work, 'slerp.elf')
        sh(['aarch64-linux-gnu-g++', *TS.A32, '-std=gnu++17', '-fms-extensions', '-w', '-I' + work, '-c',
            os.path.join(HERE, 'slerp', 'dyn_unit.cpp'), '-o', u])
        sh(['aarch64-linux-gnu-ld', '-m', 'aarch64linux32', '-e', '0', '--unresolved-symbols=ignore-all', '-o', sle, u])
        dw = os.path.join(work, 'dec')
        os.mkdir(dw)
        dec = TD.build(dw)
        mca, mca1 = cost.MCA(), cost.MCA(iterations=1)

        def price(elf, names):
            return {r['func']: r for r in funcmodel.price_funcs(elf, names, mca, mca1)}
        o = price(ov, ['fnvnx_slerp_00a6e330', 'fnvnx_nibound_update_00a7fdd0', 'fnvnx_mat3_tmul_00a588a0'])
        d = price(sle, ['_ZN12NiQuaternion5SlerpEfRKS_S1_', '_Z12FUN_00a6dc30ff',
                        '_ZN12NiQuaternion19EnsureCanonicalSignEv'])
        e = price(dec, ['_ZN7NiBound6UpdateERKS_RK11NiTransform', '_ZNK11Matrix3A58812FUN_00a588a0ERKS_'])
        # S1: Slerp, PERF.md 11.6's dynamic counts 47 / 29 / 58 and 88
        parts = [(d['_ZN12NiQuaternion5SlerpEfRKS_S1_'], 47), (d['_Z12FUN_00a6dc30ff'], 29),
                 (d['_ZN12NiQuaternion19EnsureCanonicalSignEv'], 58)]
        ds, d1 = sum(r['cpi'] * n for r, n in parts), sum(r['cpi_once'] * n for r, n in parts)
        so = o['fnvnx_slerp_00a6e330']
        os_, o1 = 88 * so['cpi'], 88 * so['cpi_once']
        say('S1', os_ > ds * 1.057 and o1 < d1 * 0.943,
            'Slerp decomp -> override: steady %.1f -> %.1f (%+.0f %%), one call %.1f -> %.1f (%+.0f %%), mid %.1f -> '
            '%.1f (%+.1f %%); CPI decomp %s, override %.3f / %.3f' % (
                ds, os_, 100 * (os_ / ds - 1), d1, o1, 100 * (o1 / d1 - 1), (ds + d1) / 2, (os_ + o1) / 2,
                100 * ((os_ + o1) / (ds + d1) - 1), ', '.join('%.3f/%.3f' % (r['cpi'], r['cpi_once']) for r, _ in parts),
                so['cpi'], so['cpi_once']))
        # S2: the false loop-carried chain of the steady price
        for elf, nm in ((ov, 'fnvnx_slerp_00a6e330'), (sle, '_Z12FUN_00a6dc30ff'), (ov, 'fnvnx_nibound_update_00a7fdd0')):
            n, a, b, p, brk = steady_unchained(funcmodel, cost, elf, nm)
            say('S2', b < a * 0.9 and p < 0.1 * a,
                '%s (%d insns): steady %.1f cycles as priced, %.1f with %s redefined first (the prefix alone %.1f): '
                'CPI %.3f -> %.3f' % (nm, n, a, b, ','.join(brk), p, a / n, b / n))
        # CE1: NiBound::Update (52 static insns here, 46 per call in ce64150) and the transpose product (64 / 39)
        for dn, on, din, oin in (('_ZN7NiBound6UpdateERKS_RK11NiTransform', 'fnvnx_nibound_update_00a7fdd0', 46, 19),
                                 ('_ZNK11Matrix3A58812FUN_00a588a0ERKS_', 'fnvnx_mat3_tmul_00a588a0', 64, 39)):
            x, y = e[dn], o[on]
            a0, a1, b0_, b1_ = din * x['cpi'], din * x['cpi_once'], oin * y['cpi'], oin * y['cpi_once']
            mid_d = 100 * ((b0_ + b1_) / (a0 + a1) - 1)
            say('CE1', b0_ <= a0 * 1.01 and (mid_d < 5.7 or b0_ < a0),
                '%s: decomp %d x %.3f / %.3f = %.1f / %.1f, override %d x %.3f / %.3f = %.1f / %.1f: steady %+.0f %%, '
                'one call %+.0f %%, mid %+.1f %%' % (on, din, x['cpi'], x['cpi_once'], a0, a1, oin, y['cpi'],
                                                     y['cpi_once'], b0_, b1_, 100 * (b0_ / a0 - 1),
                                                     100 * (b1_ / a1 - 1), mid_d))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
