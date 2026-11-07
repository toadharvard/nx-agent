#!/usr/bin/env python3
"""overhead_audit.py - the port-overhead audit, part 1 (stream S15-simd, docs/PERF.md 11.8): per function, the A57
cycles of our build against the work the x86 original does, ranked by the worst ratio.

    overhead_audit.py --units REGEX [--shard K/N] [--seeds N] [--tsv OUT] [--max-funcs N]
                      [--catalog CAT --cache DIR --cache-out FILE]

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

Incremental (T1-fastbench): with --catalog (difffuzz's catalog.jsonl) and --cache, a function's row is reused while its
audit key is unchanged: key = sha1(AUDIT_HARNESS, seeds, max steps, llvm-mca's version, the function's difffuzz key
(tools/difffuzz/fnkeys.py: its MSVC code, its rewritten A64 code and relocations and those of what it reaches, the
difffuzz harness version)). --cache is a directory of *.jsonl rows (every shard's cache, read as one union, so a
change of N or of the shard split keeps the rows); new rows go to --cache-out. Cached rows take no time from
--deadline. Rows without a key (no native definition) and setup failures are not cached; pending rows never are.
"""
import argparse
import json
import os
import re
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'a57model'))
import dyncount  # noqa: E402  (sets up the counting engines and the harness modules)
import config  # noqa: E402
import engine  # noqa: E402
import cost  # noqa: E402
import funcmodel  # noqa: E402

AUDIT_HARNESS = 'audit-1'   # bump when the measurement changes for every function (dyncount, funcmodel, pricing, rows)
BRIDGE = [0, 0]   # [lo, hi) of the current image's ABI bridges (__fnvnx_br_*: zz_bridges.o, linked contiguously)
_count = dyncount.CountingA64._count_block


def _count_block(self, uc, address, size, user):
    _count(self, uc, address, size, user)
    if BRIDGE[0] <= address < BRIDGE[1]:
        dyncount.COUNT['a64_bridge'] = dyncount.COUNT.get('a64_bridge', 0) + size // 4


dyncount.CountingA64._count_block = _count_block


def bridge_range(img):
    br = sorted(a for n, a in img.syms.items() if n.startswith('__fnvnx_br_'))
    if not br:
        return [0, 0]
    after = sorted(a for a in img.syms.values() if a > br[-1])
    return [br[0], after[0] if after else br[-1] + 256]


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


def audit_keys(funcs, catalog, seeds, max_steps):
    """{address: audit key} for the functions difffuzz can key (fnkeys.compute over these functions as targets)"""
    import hashlib, subprocess, tempfile
    import fnkeys
    with tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False) as f:
        for source, addr, _ in funcs:
            f.write(json.dumps({'source': source, 'address': '%08x' % addr}) + '\n')
    try:
        keys, _, _ = fnkeys.compute(f.name, catalog)
    finally:
        os.unlink(f.name)
    mca = ''
    for exe in ('llvm-mca-18', 'llvm-mca'):
        try:
            mca = subprocess.run([exe, '--version'], capture_output=True, text=True).stdout
            mca = ' '.join(l.strip() for l in mca.splitlines() if 'version' in l.lower())[:80]
            break
        except OSError:
            continue
    salt = '%s|%d|%d|%s' % (AUDIT_HARNESS, seeds, max_steps, mca)
    return {int(a, 16): hashlib.sha1(('%s|%s' % (salt, k)).encode()).hexdigest()[:20] for a, k in keys.items()}


def load_cache(d):
    rows = {}
    if d and os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.endswith('.jsonl'):
                for line in open(os.path.join(d, fn)):
                    try:
                        r = json.loads(line)
                        rows[r['key']] = r
                    except (ValueError, KeyError):
                        pass
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--units', required=True)
    ap.add_argument('--shard', default='1/1')
    ap.add_argument('--seeds', type=int, default=4)
    ap.add_argument('--max-steps', type=int, default=400000)
    ap.add_argument('--max-funcs', type=int, default=0)
    ap.add_argument('--tsv')
    ap.add_argument('--deadline', type=float, default=0, help='stop starting functions after this many seconds')
    ap.add_argument('--summary', help='fnvnx-overhead-audit/1 JSON: totals of the shard')
    ap.add_argument('--catalog', help="difffuzz's catalog.jsonl (function keys for --cache)")
    ap.add_argument('--cache', help='directory of cached rows (*.jsonl, any shard)')
    ap.add_argument('--cache-out', help='append the rows measured now to this file')
    a = ap.parse_args()
    k, n = map(int, a.shard.split('/'))
    funcs = [f for i, f in enumerate(unit_functions(a.units)) if i % n == k - 1]
    if a.max_funcs:
        funcs = funcs[:a.max_funcs]
    uc = engine.UnitCache(str(config.LINK), str(config.A64WORK))
    mca, mca1 = cost.MCA(None), cost.MCA(None, iterations=1)
    rows = []
    keys = audit_keys(funcs, a.catalog, a.seeds, a.max_steps) if a.catalog and a.cache else {}
    cache = load_cache(a.cache) if keys else {}
    cout = open(a.cache_out, 'a') if a.cache_out and keys else None
    hits = 0
    t0 = time.time()
    for source, addr, sym in funcs:
        key = keys.get(addr)
        if key and key in cache:
            r = dict(cache[key]); r.update(source=source, addr=addr, sym=sym, cached=True)
            rows.append(r); hits += 1
            continue
        if a.deadline and time.time() - t0 > a.deadline:
            rows.append(dict(source=source, addr=addr, sym=sym, note='pending (deadline)'))
            continue
        try:
            BRIDGE[:] = bridge_range(uc.arm_image(source))
            rs = dyncount.run_target(uc, source, addr, a.seeds, a.max_steps)
        except (Exception, SystemExit) as e:  # noqa: BLE001 - one function must not stop the audit (funcmodel and the
            # harness raise SystemExit for a missing function)
            rows.append(dict(source=source, addr=addr, sym=sym, note='setup: %r' % e))
            continue
        good = [r for r in rs if not r.get('err') and r.get('x86') and r.get('a64')]
        if not good:
            rows.append(dict(source=source, addr=addr, sym=sym, note='no clean case'))
            if cout and key:
                cout.write(json.dumps(dict(rows[-1], key=key)) + '\n'); cout.flush()
            continue
        x = statistics.median(r['x86'] for r in good)
        y = statistics.median(r['a64'] for r in good)
        yb = statistics.median(r.get('a64_bridge', 0) for r in good)
        cpi = cpi1 = None
        try:
            elf = uc.arm_image(source).elf if hasattr(uc.arm_image(source), 'elf') else None
            if elf:
                pr = funcmodel.price_funcs(elf, ['0x%08x' % addr], mca, mca1)[0]
                cpi, cpi1 = pr.get('cpi'), pr.get('cpi_once')
        except (Exception, SystemExit):  # noqa: BLE001 - funcmodel: SystemExit 'no function for ...' (shard 26/26)
            pass
        cyc = y * (cpi + cpi1) / 2 if cpi and cpi1 else None
        rows.append(dict(source=source, addr=addr, sym=sym, x86=x, a64=y, br=yb, cpi=cpi, cpi1=cpi1, cyc=cyc,
                         ok=len(good), agree=sum(1 for r in good if not r.get('div'))))
        if cout and key:
            cout.write(json.dumps(dict(rows[-1], key=key)) + '\n'); cout.flush()
    if cout:
        cout.close()
    if a.tsv:
        with open(a.tsv, 'w') as f:
            f.write('source\taddr\tsymbol\tx86\ta64\ta64_in_bridges\tinsn_ratio\tcpi\tcpi_once\ta64_cycles_mid\tcycle_ratio\tok\tagree\tnote\n')
            for r in rows:
                if 'x86' in r:
                    f.write('%s\t0x%08x\t%s\t%d\t%d\t%d\t%.2f\t%s\t%s\t%s\t%s\t%d\t%d\t\n' % (
                        r['source'], r['addr'], r['sym'], r['x86'], r['a64'], r['br'], r['a64'] / r['x86'], r['cpi'],
                        r['cpi1'], '%.1f' % r['cyc'] if r['cyc'] else '',
                        '%.2f' % (r['cyc'] / r['x86']) if r['cyc'] else '', r['ok'], r['agree']))
                else:
                    f.write('%s\t0x%08x\t%s\t\t\t\t\t\t\t\t\t\t\t%s\n' % (r['source'], r['addr'], r['sym'], r['note']))
    # functions under 10 x86 instructions are all call overhead on both sides: listed in the TSV, not ranked
    ranked = sorted((r for r in rows if r.get('cyc') and r['x86'] >= 10), key=lambda r: -r['cyc'] / r['x86'])
    print('%-44s %10s %6s %6s %6s %8s %6s' % ('function', 'addr', 'x86', 'a64', 'CPI', 'cycles', 'c/x86'))
    for r in ranked[:40]:
        print('%-44s 0x%08x %6d %6d %6.2f %8.1f %6.2f' % (r['sym'][-44:], r['addr'], r['x86'], r['a64'],
                                                          (r['cpi'] + r['cpi1']) / 2, r['cyc'], r['cyc'] / r['x86']))
    if a.summary:
        cl = [r for r in rows if 'x86' in r]
        tot = {k: sum(r[k] for r in cl) for k in ('x86', 'a64', 'br')}
        tot['cycles'] = sum(r['cyc'] for r in cl if r.get('cyc'))
        json.dump({'schema': 'fnvnx-overhead-audit/1', 'units': a.units, 'shard': a.shard, 'functions': len(rows),
                   'clean': len(cl), 'pending': sum(1 for r in rows if r.get('note', '').startswith('pending')),
                   'x86_insns': tot['x86'], 'a64_insns': tot['a64'], 'a64_in_bridges': tot['br'],
                   'a64_cycles_mid': round(tot['cycles'], 1), 'keyed': len(keys), 'cached': hits,
                   'measured': sum(1 for r in rows if not r.get('cached') and not r.get('note', '').startswith('pending')),
                   'top_excess': [{'addr': '0x%08x' % r['addr'], 'source': r['source'], 'x86': r['x86'],
                                   'a64': r['a64'], 'bridges': r['br'], 'cycles': round(r['cyc'], 1)}
                                  for r in sorted((r for r in cl if r.get('cyc')),
                                                  key=lambda r: -(r['cyc'] - r['x86']))[:30]]},
                  open(a.summary, 'w'), indent=1)
    print('functions: %d, priced: %d, no clean case / setup: %d, from the cache: %d of %d keyed' % (
        len(rows), len(ranked), sum(1 for r in rows if 'note' in r), hits, len(keys)))


if __name__ == '__main__':
    main()
