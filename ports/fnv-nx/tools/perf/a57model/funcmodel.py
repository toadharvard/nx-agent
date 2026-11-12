#!/usr/bin/env python3
"""funcmodel.py - static A57 price of named guest functions (docs/PERF.md 12.5): for each function, llvm-mca
-mcpu=cortex-a57 over its body as one straight-line pass (every block once, in address order, branches not taken)
and over each basic block as a loop body. Gives cycles per instruction (CPI) with its spread, which turns a function's
measured instructions per call (measured, PERF.md 7 / 11.4) into cycles per call when no trace of it exists yet.

    funcmodel.py --elf fnvnx.elf FUNC... [--json OUT] [--mca-cache FILE]
    funcmodel.py --elf fnvnx.elf --rows rows.json [--json OUT]     rows: [{"id", "funcs": [...], "insns_per_call"}]

FUNC: a symbol or an original address 0xXXXXXXXX (S2's __fnv_ alias). Limits (stated in the output): data in L1,
branches predicted, the callee's work not included (a call is one instruction). CPI = mca cycles / instructions of
the straight-line pass; cpi_lo/cpi_hi = the smallest and largest block-as-loop CPI (weighted by size) bracket it.
Prints counts and names only.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cost  # noqa: E402

from elftools.elf.elffile import ELFFile  # noqa: E402


class Image:
    def __init__(self, path):
        self.f = open(path, 'rb')
        self.elf = ELFFile(self.f)
        self.segs = [(s['p_vaddr'], s['p_filesz'], s['p_offset']) for s in self.elf.iter_segments()
                     if s['p_type'] == 'PT_LOAD']

        self.sizes = {}
        tab = self.elf.get_section_by_name('.symtab')
        if tab is not None:
            for sym in tab.iter_symbols():
                if sym['st_info']['type'] == 'STT_FUNC' and sym['st_size']:
                    self.sizes[sym['st_value'] & ~1] = sym['st_size']

    def read(self, addr, n):
        for va, sz, off in self.segs:
            if va <= addr < va + sz:
                self.f.seek(off + addr - va)
                return self.f.read(min(n, va + sz - addr))
        return b''


def func_range(syms, spec, img=None):
    if spec.startswith('0x'):
        want = '__fnv_%08x' % int(spec, 16)
        names = [i for i, o in enumerate(syms.orig) if o == '0x%08x' % int(spec, 16)]
        if not names:
            raise SystemExit(f'funcmodel: no function for {spec} ({want})')
        i = names[0]
    else:
        try:
            i = syms.names.index(spec)
        except ValueError:
            raise SystemExit(f'funcmodel: no symbol {spec}')
    lo = syms.addrs[i]
    if img is not None and img.sizes.get(lo):
        return syms.names[i], lo, lo + img.sizes[lo]
    end = syms.addrs[i + 1] if i + 1 < len(syms.addrs) else lo + 4096
    return syms.names[i], lo, min(end, lo + 64 * 1024)


def blocks_of(il):
    """split a function's instructions into basic blocks: after each branch and at each branch target inside it"""
    addrs = {a for a, *_ in il}
    starts = {il[0][0]} if il else set()
    for a, mn, ops, ins in il:
        if cost.BRANCH_RE.match(mn) or mn in ('br', 'blr', 'ret'):
            starts.add(a + 4)
            if mn not in ('bl', 'blr', 'br', 'ret'):
                t = ops.split(',')[-1].strip().lstrip('#')
                try:
                    ta = int(t, 16)
                    if ta in addrs:
                        starts.add(ta)
                except ValueError:
                    pass
    out, cur = [], []
    for x in il:
        if x[0] in starts and cur:
            out.append(cur)
            cur = []
        cur.append(x)
    if cur:
        out.append(cur)
    return out


def price_funcs(elf, specs, mca, mca1=None, mcat=None):
    """mcat: an MCA(metric='rthroughput'): adds cpi_tput, the chain-free steady bound (independent calls overlap;
    PERF.md 11.9). The chained 'cpi' feeds each pass's results into the next and overstates latency-bound code (V1 S2)."""
    syms = cost.Symbols([elf])
    img = Image(elf)
    res = []
    regions = {}
    meta = []
    for spec in specs:
        name, lo, hi = func_range(syms, spec, img)
        code = img.read(lo, hi - lo)
        il = cost.disasm(lo, code)
        # stop at the first ret followed by padding or a different function's prologue: the body is [lo, hi)
        bl = blocks_of(il)
        key = f'f{len(meta)}'
        regions[key] = cost.mca_text(il)
        for j, b in enumerate(bl):
            regions[f'{key}b{j}'] = cost.mca_text(b)
        meta.append((spec, name, lo, il, bl, key))
    cyc = mca.cycles(regions)
    once = mca1.cycles({k: v for k, v in regions.items() if 'b' not in k}) if mca1 else {}
    tput = mcat.cycles({k: v for k, v in regions.items() if 'b' not in k}) if mcat else {}
    for spec, name, lo, il, bl, key in meta:
        n = len(cost.mca_text(il))
        whole = cyc.get(key)
        cpis = []
        for j, b in enumerate(bl):
            c = cyc.get(f'{key}b{j}')
            nb = len(cost.mca_text(b))
            if c and nb:
                cpis.append((c / nb, nb))
        cpis.sort()
        res.append({'func': spec, 'symbol': name, 'orig': syms.original(lo), 'static_insns': n,
                    'blocks': len(bl), 'calls': sum(1 for _a, mn, *_ in il if mn in ('bl', 'blr')),
                    'cycles_pass': whole, 'cycles_once': once.get(key),
                    'cpi': round(whole / n, 3) if whole and n else None,
                    'cpi_once': round(once[key] / n, 3) if once.get(key) and n else None,
                    'cpi_tput': round(tput[key] / n, 3) if tput.get(key) and n else None,
                    'cpi_lo': round(cpis[0][0], 3) if cpis else None, 'cpi_hi': round(cpis[-1][0], 3) if cpis else None})
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--elf', required=True)
    ap.add_argument('funcs', nargs='*')
    ap.add_argument('--rows')
    ap.add_argument('--json')
    ap.add_argument('--mca-cache')
    ap.add_argument('--calib', help='calib.json: its ilk_inline prediction prices the Interlocked row')
    ap.add_argument('--budget-model', help="tools/perf/budget_model.json: price its hotspot_functions rows (the "
                    "addresses in each row's name), as per_call_cycles proposals for S15's hotspot_metric.py")
    ap.add_argument('--override-elf', help="an ELF of tools/link/overrides' objects (build.sh, then ld): a budget row "
                    "whose address has an override (fnvnx_*_<addr>) is priced on the override's code, which is what "
                    "its per_call_insn measures")
    ap.add_argument('--main-minsn', type=float, default=23.8,
                    help='main-thread M instructions per exterior frame (PERF.md 11, J-0148: 23.8)')
    a = ap.parse_args(argv)
    mca = cost.MCA(a.mca_cache)
    out = {'schema': 'fnvnx-a57funcs/1', 'limits': 'static: L1-resident data, predicted branches, callees excluded',
           'functions': [], 'rows': []}
    if a.rows:
        with open(a.rows) as f:
            rows = json.load(f)
        specs = [s for r in rows for s in r['funcs']]
    else:
        rows, specs = [], list(a.funcs)
    budget = []
    if a.budget_model:
        import re
        with open(a.budget_model) as f:
            bm = json.load(f)
        syms0 = cost.Symbols([a.elf])
        have = set(syms0.orig)
        ov = {}
        if a.override_elf:
            osy = cost.Symbols([a.override_elf])
            for n in osy.names:
                m = re.match(r'^fnvnx_\w+_([0-9a-f]{8})$', n)
                if m:
                    ov['0x' + m.group(1)] = n
        for h in bm.get('hotspot_functions', []):
            fs = [x for x in re.findall(r'0x00[0-9a-f]{6}', h.get('name', '')) if x in have or x in ov]
            budget.append((h, fs))
        ovspecs = sorted({ov[x] for _h, fs in budget for x in fs if x in ov})
        specs += [x for _h, fs in budget for x in fs if x not in ov and x not in specs]
    specs = list(dict.fromkeys(specs))
    mca1 = cost.MCA(a.mca_cache, iterations=1)
    fr = price_funcs(a.elf, specs, mca, mca1)
    if a.budget_model and a.override_elf and ovspecs:
        ofr = price_funcs(a.override_elf, ovspecs, mca, mca1)
        for r in ofr:
            r['override'] = True
        fr += ofr
    mca.save()
    out['functions'] = fr
    by = {r['func']: r for r in fr}
    if a.budget_model and a.override_elf:
        for x, n in ov.items():
            if n in by:
                by[x] = by[n]
    for r in rows:
        fs = [by[s] for s in r['funcs'] if by[s]['cpi']]
        w = sum(x['static_insns'] for x in fs) or 1
        cpi = sum(x['cpi'] * x['static_insns'] for x in fs) / w if fs else None
        cpi1 = sum((x['cpi_once'] or x['cpi']) * x['static_insns'] for x in fs) / w if fs else None
        row = dict(r)
        row['cpi'] = round(cpi, 3) if cpi else None       # back-to-back calls (steady state)
        row['cpi_once'] = round(cpi1, 3) if cpi1 else None  # one call alone (latency)
        if cpi and r.get('insns_per_call'):
            lo, hi = r['insns_per_call'] if isinstance(r['insns_per_call'], list) else (r['insns_per_call'],) * 2
            row['cycles_per_call'] = [round(lo * cpi, 1), round(hi * cpi1, 1)]
        if cpi and r.get('minsn_frame'):
            row['mcycles_frame'] = [round(r['minsn_frame'] * cpi, 3), round(r['minsn_frame'] * cpi1, 3)]
            row['ms_1020'] = [round(x / 1.02, 3) for x in row['mcycles_frame']]
        out['rows'].append(row)
    if a.calib and os.path.exists(a.calib):
        with open(a.calib) as f:
            preds = {p['name']: p for p in json.load(f).get('predictions', [])}
        p = preds.get('ilk_inline')
        if p:  # PERF.md 11.4 row 1: 8 instructions per refcount operation, 20k-100k operations per frame (mid 1.8 M)
            cpi = p['cycles'] / 8
            out['rows'].insert(0, {'id': 'interlocked_refcounts', 'funcs': ['inlined InterlockedIncrement/Decrement'],
                                   'insns_per_call': [8, 8], 'calls_per_frame': [20000, 100000], 'minsn_frame': 1.8,
                                   'src': 'PERF.md 11.4 row 1; cycles: calibrate.py ilk_inline (uncontended, no store in flight)',
                                   'cpi': round(cpi, 3), 'cpi_once': round(cpi, 3),
                                   'cycles_per_call': [round(p['cycles'], 1)] * 2,
                                   'mcycles_frame': [round(1.8 * cpi, 3)] * 2, 'ms_1020': [round(1.8 * cpi / 1.02, 3)] * 2})
    ilk_cpi = None
    if a.calib and os.path.exists(a.calib):
        with open(a.calib) as f:
            p = {q['name']: q for q in json.load(f).get('predictions', [])}.get('ilk_inline')
        ilk_cpi = p['cycles'] / 8 if p else None
    out['budget_rows'] = []
    for h, fs in budget:
        lo, hi = h['per_call_insn'] if isinstance(h['per_call_insn'], list) else (h['per_call_insn'],) * 2
        priced = [by[x] for x in fs if by[x]['cpi']]
        if h['id'] == 'fn_interlocked' and ilk_cpi:
            cpi = cpi1 = ilk_cpi
            basis = 'calibrate.py ilk_inline (13 cycles per 8-instruction operation) x per_call_insn'
        elif priced:
            w = sum(x['static_insns'] for x in priced)
            cpi = sum(x['cpi'] * x['static_insns'] for x in priced) / w
            cpi1 = sum((x['cpi_once'] or x['cpi']) * x['static_insns'] for x in priced) / w
            basis = 'funcmodel.py static A57 CPI of ' + ', '.join(
                (x + ' (override)') if by[x].get('override') else x for x in fs) + ' x per_call_insn'
        else:
            out['budget_rows'].append({'id': h['id'], 'per_call_cycles': None, 'why': 'no function of the row in this ELF'})
            continue
        out['budget_rows'].append({'id': h['id'], 'per_call_insn': [lo, hi], 'cpi': [round(cpi, 3), round(cpi1, 3)],
                                   'per_call_cycles': [round(lo * cpi, 1), round(hi * cpi1, 1)], 'basis': basis})
    covered = sum(r['minsn_frame'] for r in out['rows'] if r.get('mcycles_frame'))
    out['coverage'] = {'main_minsn_frame': a.main_minsn, 'covered_minsn': round(covered, 3),
                       'covered_pct': round(100 * covered / a.main_minsn, 2),
                       'covered_ms_1020': [round(sum(r['ms_1020'][i] for r in out['rows'] if r.get('ms_1020')), 3)
                                           for i in (0, 1)],
                       'covered_ms_1020_flat_ipc08': round(covered / 0.8 / 1.02, 3)}
    for x in fr:
        print(f"{x['func']:12} {x['symbol'][:56]:56} insns {x['static_insns']:5} blocks {x['blocks']:3} "
              f"calls {x['calls']:2} CPI {x['cpi']} once {x['cpi_once']} (blocks {x['cpi_lo']}-{x['cpi_hi']})")
    print(f"coverage: {out['coverage']}") if out.get('coverage') else None
    for r in out['budget_rows']:
        print(f"budget {r['id']}: per_call_cycles {r.get('per_call_cycles')} (CPI {r.get('cpi')})")
    for r in out['rows']:
        print(f"row {r['id']}: CPI {r['cpi']}-{r['cpi_once']}, cycles per call {r.get('cycles_per_call')}")
    if a.json:
        with open(a.json, 'w') as f:
            json.dump(out, f, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
