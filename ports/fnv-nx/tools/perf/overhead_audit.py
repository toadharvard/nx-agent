#!/usr/bin/env python3
"""overhead_audit.py - the port-overhead audit, part 1 (stream S15-simd, docs/PERF.md 11.8): per function, the A57
cycles of our build against the work the x86 original does, ranked by the worst ratio.

    overhead_audit.py --units REGEX [--shard K/N] [--seeds N] [--tsv OUT] [--max-funcs N]

For every function of the units whose source matches REGEX (units.json of FNVNX_TREE), on S13's harness with the
profile's link (FNVNX_DFPROFILE; dyncount.py's counting engines): the median x86 instructions and A64 instructions
per call over the same fuzzed cases (callees intercepted on both sides), and the oracle's agreement. A64 cycles per call =
A64 instructions x the function's A57 CPI (tools/perf/a57model/funcmodel.py, llvm-mca cortex-a57 on the harness's
own linked unit image): steady CPI and one-call CPI give the lo / hi, their mid the ratio's numerator. The x86 side's
work is its instruction count (an x86 instruction of this MSVC 2008 code is about one fused micro-op on a Core-class
CPU; no x86 timing is claimed). ratio = A64 cycles (mid) / x86 instructions: 1.0 means one A57 cycle per x86
instruction of work. Functions without a clean case on both machines are listed as such.

Writes TSV rows: source, address, symbol, x86 insns, A64 insns, insn ratio, CPI steady / once, A64 cycles mid, cycle
ratio, cases ok, agree. Prints the ranking. Counts and names only; the harness inputs stay in local scratch.
"""
import argparse
import json
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'a57model'))
import dyncount  # noqa: E402  (sets up the counting engines and the harness modules)
import config  # noqa: E402
import engine  # noqa: E402
import cost  # noqa: E402
import funcmodel  # noqa: E402


def unit_functions(rx):
    units = json.load(open(os.path.join(str(config.TREE), 'build', 'units.json')))['units']
    seen, out = set(), []
    for u in units:
        if u['source'] in seen or not re.search(rx, u['source']):
            continue
        seen.add(u['source'])
        for f in u.get('functions', []):
            out.append((u['source'], int(f['address'], 16), f.get('symbol', '')))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--units', required=True)
    ap.add_argument('--shard', default='1/1')
    ap.add_argument('--seeds', type=int, default=4)
    ap.add_argument('--max-steps', type=int, default=400000)
    ap.add_argument('--max-funcs', type=int, default=0)
    ap.add_argument('--tsv')
    a = ap.parse_args()
    k, n = map(int, a.shard.split('/'))
    funcs = [f for i, f in enumerate(unit_functions(a.units)) if i % n == k - 1]
    if a.max_funcs:
        funcs = funcs[:a.max_funcs]
    uc = engine.UnitCache(str(config.LINK), str(config.A64WORK))
    mca, mca1 = cost.MCA(None), cost.MCA(None, iterations=1)
    rows = []
    for source, addr, sym in funcs:
        try:
            rs = dyncount.run_target(uc, source, addr, a.seeds, a.max_steps)
        except Exception as e:  # noqa: BLE001 - one function must not stop the audit
            rows.append(dict(source=source, addr=addr, sym=sym, note='setup: %r' % e))
            continue
        good = [r for r in rs if not r.get('err') and r.get('x86') and r.get('a64')]
        if not good:
            rows.append(dict(source=source, addr=addr, sym=sym, note='no clean case'))
            continue
        x = statistics.median(r['x86'] for r in good)
        y = statistics.median(r['a64'] for r in good)
        cpi = cpi1 = None
        try:
            elf = uc.arm_image(source).elf if hasattr(uc.arm_image(source), 'elf') else None
            if elf:
                pr = funcmodel.price_funcs(elf, ['0x%08x' % addr], mca, mca1)[0]
                cpi, cpi1 = pr.get('cpi'), pr.get('cpi_once')
        except Exception as e:  # noqa: BLE001
            pass
        cyc = y * (cpi + cpi1) / 2 if cpi and cpi1 else None
        rows.append(dict(source=source, addr=addr, sym=sym, x86=x, a64=y, cpi=cpi, cpi1=cpi1, cyc=cyc,
                         ok=len(good), agree=sum(1 for r in good if not r.get('div'))))
    if a.tsv:
        with open(a.tsv, 'w') as f:
            f.write('source\taddr\tsymbol\tx86\ta64\tinsn_ratio\tcpi\tcpi_once\ta64_cycles_mid\tcycle_ratio\tok\tagree\tnote\n')
            for r in rows:
                if 'x86' in r:
                    f.write('%s\t0x%08x\t%s\t%d\t%d\t%.2f\t%s\t%s\t%s\t%s\t%d\t%d\t\n' % (
                        r['source'], r['addr'], r['sym'], r['x86'], r['a64'], r['a64'] / r['x86'], r['cpi'],
                        r['cpi1'], '%.1f' % r['cyc'] if r['cyc'] else '',
                        '%.2f' % (r['cyc'] / r['x86']) if r['cyc'] else '', r['ok'], r['agree']))
                else:
                    f.write('%s\t0x%08x\t%s\t\t\t\t\t\t\t\t\t\t%s\n' % (r['source'], r['addr'], r['sym'], r['note']))
    # functions under 10 x86 instructions are all call overhead on both sides: listed in the TSV, not ranked
    ranked = sorted((r for r in rows if r.get('cyc') and r['x86'] >= 10), key=lambda r: -r['cyc'] / r['x86'])
    print('%-44s %10s %6s %6s %6s %8s %6s' % ('function', 'addr', 'x86', 'a64', 'CPI', 'cycles', 'c/x86'))
    for r in ranked[:40]:
        print('%-44s 0x%08x %6d %6d %6.2f %8.1f %6.2f' % (r['sym'][-44:], r['addr'], r['x86'], r['a64'],
                                                          (r['cpi'] + r['cpi1']) / 2, r['cyc'], r['cyc'] / r['x86']))
    print('functions: %d, priced: %d, no clean case / setup: %d' % (
        len(rows), len(ranked), sum(1 for r in rows if 'note' in r)))


if __name__ == '__main__':
    main()
