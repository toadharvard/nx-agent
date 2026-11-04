#!/usr/bin/env python3
"""cost.py - prices a57sim statistics (a57trace.so or uctrace.py) in Cortex-A57 cycles (docs/PERF.md 12).

    cost.py <stats.txt> [--params params.json] [--clock GHZ] [--region TAG] [--elf ELF]... [--json OUT]
            [--top N] [--mca-cache FILE]

Per block: llvm-mca -mcpu=cortex-a57 over the block as a loop body (steady-state cycles per iteration), at least
ceil(n / dispatch width); plus the front end the simulator saw (mispredicts, L2-BTB and BTB-miss bubbles, L1I and
ITLB misses). Per memory instruction: the load misses the simulator saw, at L2 / DRAM latency; a load whose address
depends on a loaded value (static dataflow over its block, loop-carried) pays every miss in full, any other pays per
miss group (the misses that fit in the out-of-order window share one latency, up to the miss buffers). TLB misses
and store-to-load forwarding add their latency. Stores cost bandwidth, not latency. Bandwidth: lines from L2 at
l2_bytes_per_cycle, lines to and from DRAM at dram_mlp_lines in flight over the DRAM latency (Little's law); each
function takes max(core + latency, bandwidth).

--elf: symbols (nm -n) for the per-function table, which is the hotspots[] of fnvnx-perf/1 (perf_report.py).
Prints counts and names only.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))

try:
    import capstone
    _ARCH = getattr(capstone, 'CS_ARCH_ARM64', None) or getattr(capstone, 'CS_ARCH_AARCH64')
    CS = capstone.Cs(_ARCH, capstone.CS_MODE_ARM)
    CS.detail = True
    MEM_OP = getattr(capstone, 'CS_OP_MEM', None) or capstone.arm64.ARM64_OP_MEM
    REG_OP = getattr(capstone, 'CS_OP_REG', None) or capstone.arm64.ARM64_OP_REG
except ImportError:  # pragma: no cover
    CS = None

MARKERS = {0xd5032e1f, 0xd5032e3f}
BRANCH_RE = re.compile(r'^(b(\.\w+)?|bl|cbn?z|tbn?z)$')


def load_params(path):
    with open(path) as f:
        raw = json.load(f)
    out = {}
    for sec in ('sim', 'cost'):
        out[sec] = {k: (v[0] if isinstance(v, list) else v) for k, v in raw.get(sec, {}).items()}
    return out


def parse_stats(path):
    """-> {tag: {'R': {...}, 'B': [...], 'M': [...]}} in file order"""
    regions = {}
    cur = None
    with open(path) as f:
        for line in f:
            p = line.split()
            if not p:
                continue
            if p[0] == 'R':
                cur = {'R': dict(zip(('icount', 'dram_rd', 'dram_wr', 'l2fill', 'blocks', 'accesses'),
                                     map(int, p[2:8]))), 'B': [], 'M': []}
                regions[p[1]] = cur
            elif p[0] == 'B':
                cur['B'].append({'pc': int(p[1], 16), 'n': int(p[2]), 'count': int(p[3]), 'mispred': int(p[4]),
                                 'taken': int(p[5]), 'btb_l2': int(p[6]), 'btb_miss': int(p[7]), 'icl1': int(p[8]),
                                 'icl2': int(p[9]), 'itlb': int(p[10]),
                                 'bytes': bytes.fromhex(p[11]) if len(p) > 11 and p[11] != '-' else b''})
            elif p[0] == 'M':
                k = ('pc', 'count', 'st', 'l1m', 'l2m', 'eff1', 'eff2', 'tlb1', 'tlb2', 'pf_dram', 'pf_l2', 'pf_hit',
                     'fwd', 'dram_wb')
                d = dict(zip(k, p[1:]))
                for x in k:
                    d[x] = int(d[x], 16) if x == 'pc' else (float(d[x]) if x.startswith('eff') else int(d[x]))
                cur['M'].append(d)
    return regions


# ---------------------------------------------------------------- disassembly, dataflow, llvm-mca

def disasm(pc, code):
    """-> list of (address, mnemonic, op_str, insn) for the block, markers dropped"""
    out = []
    if CS is None:
        return out
    for ins in CS.disasm(code, pc):
        w = int.from_bytes(ins.bytes, 'little')
        if w in MARKERS:
            continue
        out.append((ins.address, ins.mnemonic, ins.op_str, ins))
    return out


def mca_text(ins_list):
    lines = []
    for _, mn, ops, _ins in ins_list:
        if mn in ('udf', 'brk', 'hlt'):  # padding and traps: never executed on the priced path; llvm-mca rejects them
            continue
        if BRANCH_RE.match(mn):
            parts = [x.strip() for x in ops.split(',')]
            parts[-1] = 'L0'
            ops = ', '.join(parts)
        lines.append(f'{mn} {ops}'.strip())
    return lines


def _regname(r):
    try:
        n = CS.reg_name(r)
    except Exception:
        return str(r)
    n = n or str(r)
    if re.match(r'^[wx]\d+$', n):
        return 'x' + n[1:]
    if re.match(r'^[bhsdqv]\d+$', n):
        return 'v' + n[1:]
    return n


def _mem_and_regs(ins):
    """-> (base, {address registers}, [register operands], (base, disp) or None)"""
    base, mem_regs, regs, loc = None, set(), [], None
    for op in ins.operands:
        if op.type == MEM_OP:
            if op.mem.base:
                base = _regname(op.mem.base)
                mem_regs.add(base)
                loc = (base, op.mem.disp)
            if op.mem.index:
                mem_regs.add(_regname(op.mem.index))
        elif op.type == REG_OP:
            regs.append(_regname(op.reg))
    return base, mem_regs, regs, loc


def dependent_loads(ins_list):
    """-> (dep, memchain): addresses of loads whose address registers carry a loaded value, and of loads that read
    what a store of the same block wrote from a value of the same chain (a dependence through memory that llvm-mca
    does not see). Two passes, so dependences carried around the block as a loop count."""
    if CS is None:
        return set(), set()
    tainted, dep, memchain = set(), set(), set()
    stores = {}  # (base, disp) -> data register of the last store there
    for pass_ in range(2):
        for addr, mn, _ops, ins in ins_list:
            try:
                rd, wr = ins.regs_access()
            except Exception:
                continue
            rd = {_regname(r) for r in rd}
            wr = {_regname(r) for r in wr}
            base, mem_regs, regs, loc = _mem_and_regs(ins)
            is_load = (mn.startswith('ld') or mn.startswith('cas') or mn.startswith('swp')) and base is not None
            is_store = mn.startswith('st') and base is not None
            if is_load:
                if pass_ == 1 and mem_regs & tainted:
                    dep.add(addr)
                data = set(regs)
                if pass_ == 1 and loc in stores and stores[loc] in data:
                    memchain.add(addr)
                tainted |= data
            elif is_store:
                if loc is not None and regs:
                    stores[loc] = regs[0]
            else:
                if rd & tainted:
                    tainted |= wr
                else:
                    tainted -= wr
    return dep, memchain


# Cortex-A57 r1p0 and later reduce the latency of ASIMD integer multiply (ARM Cortex-A57 Software Optimization
# Guide UAN 0015B, 3.14 note 4: 'N/M' = r0pX / r1p0). llvm-mca's A57 model has the r0pX numbers; the Tegra X1's A57 is
# r1p1 (MIDR variant 1, revision 1). Q-form 6 -> 5, D-form 5 -> 4.
R1_MUL = {'mul', 'pmul', 'sqdmulh', 'sqrdmulh'}
Q_ARR = re.compile(r'\.(4s|8h|16b)\b')
D_ARR = re.compile(r'\.(2s|4h|8b)\b')


def r1_latency(mn, ops):
    if mn not in R1_MUL or not re.search(r'\bv\d+\.', ops):
        return None
    if Q_ARR.search(ops):
        return 5
    if D_ARR.search(ops):
        return 4
    return None


# Divides and square roots are iterative on the A57 and their latency depends on the operands (ARM Cortex-A57
# Software Optimization Guide 3.5, 3.8, 3.15): [lowest, highest]. llvm-mca prices the highest. No published
# measurement narrows them, so a latency-bound chain is priced at the middle and carries +- half the range as a band.
DIV_RANGE = {('sdiv', 'w'): (4, 20), ('udiv', 'w'): (4, 20), ('sdiv', 'x'): (4, 36), ('udiv', 'x'): (4, 36),
             ('fdiv', 's'): (7, 17), ('fsqrt', 's'): (7, 17), ('fdiv', 'd'): (7, 32), ('fsqrt', 'd'): (7, 32),
             ('fdiv', '2s'): (7, 17), ('fdiv', '4s'): (14, 34), ('fdiv', '2d'): (14, 64),
             ('fsqrt', '2s'): (7, 17), ('fsqrt', '4s'): (14, 34), ('fsqrt', '2d'): (14, 64)}


def div_range(mn, ops):
    if mn not in ('sdiv', 'udiv', 'fdiv', 'fsqrt'):
        return None
    m = re.search(r'\bv\d+\.(2s|4s|2d)\b', ops)
    if m:
        return DIV_RANGE.get((mn, m.group(1)))
    m = re.match(r'\s*([wxsd])\d+', ops)
    return DIV_RANGE.get((mn, m.group(1))) if m else None


def chain_per_iteration(ins_list, lats):
    """loop-carried dependency chain of the block as a loop: cycles per iteration, from register dataflow with
    the given per-instruction latencies (two passes, the second minus the first)"""
    ready = {}
    ends = []
    for _pass in range(3):
        top = 0.0
        for (addr, mn, ops, ins), lat in zip(ins_list, lats):
            try:
                rd, wr = ins.regs_access()
            except Exception:
                continue
            start = max([ready.get(_regname(r), 0.0) for r in rd] or [0.0])
            end = start + lat
            for r in wr:
                ready[_regname(r)] = end
            top = max(top, end)
        ends.append(max(ready.values() or [0.0]))
    return max(0.0, ends[2] - ends[1])


class MCA:
    def __init__(self, cache_path=None, iterations=300):
        self.cache_path = cache_path
        self.it = iterations
        self.cache = {}
        if cache_path and os.path.exists(cache_path):
            with open(cache_path) as f:
                self.cache = json.load(f)

    def save(self):
        if self.cache_path:
            tmp = self.cache_path + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(self.cache, f)
            os.replace(tmp, self.cache_path)

    def _run(self, regions):
        """regions: {key: [lines]} -> {key: cycles per iteration}"""
        text = []
        for k, lines in regions.items():
            text.append(f'# LLVM-MCA-BEGIN {k}')
            text += lines
            text.append('# LLVM-MCA-END')
        p = subprocess.run(['llvm-mca', '-mtriple=aarch64', '-mcpu=cortex-a57', f'-iterations={self.it}',
                            '-all-stats=false', '-instruction-info=false', '-resource-pressure=false'],
                           input='\n'.join(text) + '\n', capture_output=True, text=True)
        if p.returncode:
            return None, p.stderr
        out = {}
        keys = list(regions)
        for m in re.finditer(r'\[(\d+)\] Code Region - (\S+).*?Total Cycles:\s+(\d+)', p.stdout, re.S):
            out[m.group(2)] = int(m.group(3)) / self.it
        if len(regions) == 1 and not out:
            m = re.search(r'Total Cycles:\s+(\d+)', p.stdout)
            if m:
                out[keys[0]] = int(m.group(1)) / self.it
        return out, ''

    def info(self, lines):
        """per-instruction latencies llvm-mca assumes for one block (its instruction-info table)"""
        k = hashlib.sha1('\n'.join(lines).encode()).hexdigest()[:20] + '@info'
        if k not in self.cache:
            p = subprocess.run(['llvm-mca', '-mtriple=aarch64', '-mcpu=cortex-a57', '-iterations=1',
                                '-resource-pressure=false'], input='\n'.join(lines) + '\n',
                               capture_output=True, text=True)
            lat = []
            if not p.returncode:
                for m in re.finditer(r'^\s*(\d+)\s+(\d+)\s+(\d+\.\d+)', p.stdout.split('Instructions:')[-1], re.M):
                    lat.append(int(m.group(2)))
            self.cache[k] = lat if len(lat) == len(lines) else None
        return self.cache[k]

    def cycles(self, blocks):
        """blocks: {name: [lines]} -> {name: cycles per iteration}; the cache is keyed by the text (and the iteration
        count), so names are free; failing blocks are retried alone, then without the lines llvm-mca rejects
        (None if nothing is left)"""
        sfx = '' if self.it == 300 else f'@{self.it}'
        hkey = {n: hashlib.sha1('\n'.join(v).encode()).hexdigest()[:20] + sfx for n, v in blocks.items()}
        todo = {}
        for n, v in blocks.items():
            if v and hkey[n] not in self.cache:
                todo[hkey[n]] = v
        keys = list(todo)
        for i in range(0, len(keys), 400):
            chunk = {k: todo[k] for k in keys[i:i + 400]}
            res, _err = self._run(chunk)
            if res is None:
                res = {}
                for k, lines in chunk.items():
                    r, err = self._run({k: lines})
                    if r is None:
                        bad = {int(m.group(1)) for m in re.finditer(r'<stdin>:(\d+):', err)}
                        keep = [ln for j, ln in enumerate(lines) if (j + 2) not in bad]
                        r, _ = self._run({k: keep}) if keep and len(keep) < len(lines) else (None, '')
                        if r:
                            r = {k: r[k] * len(lines) / max(1, len(keep))}
                    if r:
                        res.update(r)
            for k in chunk:
                self.cache[k] = res.get(k)
        return {n: self.cache.get(hkey[n]) for n in blocks}


# ---------------------------------------------------------------- symbols

class Symbols:
    """nm -n of the guest ELF: per address the display name (the first non-alias name) and the original
    FalloutNV.exe address (S2's __fnv_<addr> alias at the same address), if any"""
    def __init__(self, elfs):
        self.addrs, self.names, self.orig = [], [], []
        rows = []
        for e in elfs or []:
            p = subprocess.run(['nm', '-n', '--defined-only', e], capture_output=True, text=True)
            if p.returncode or not p.stdout:
                p = subprocess.run(['aarch64-linux-gnu-nm', '-n', '--defined-only', e], capture_output=True, text=True)
            for ln in p.stdout.splitlines():
                parts = ln.split()
                if len(parts) >= 3 and parts[1] in 'tTwW' and not parts[2].startswith('$'):
                    rows.append((int(parts[0], 16), parts[2]))
        rows.sort()
        for a, n in rows:
            fnv = re.match(r'^__fnv_([0-9a-f]{8})$', n)
            if self.addrs and self.addrs[-1] == a:
                if fnv and not self.orig[-1]:
                    self.orig[-1] = '0x' + fnv.group(1)
                elif not fnv and self.names[-1].startswith('__fnv_'):
                    self.names[-1] = n
                continue
            self.addrs.append(a)
            self.names.append(n)
            self.orig.append('0x' + fnv.group(1) if fnv else '')

    def _i(self, pc):
        return bisect.bisect_right(self.addrs, pc) - 1

    def name(self, pc):
        i = self._i(pc)
        return self.names[i] if i >= 0 else f'0x{pc:x}'

    def original(self, name_or_pc):
        if isinstance(name_or_pc, int):
            i = self._i(name_or_pc)
            return self.orig[i] if i >= 0 else ''
        try:
            return self.orig[self.names.index(name_or_pc)]
        except ValueError:
            return ''


# ---------------------------------------------------------------- pricing

def price(region, params, clock, mca, syms=None):
    c = params['cost']
    l1, l2 = c['l1_lat'], c['l2_lat']
    dram = c['dram_fixed_cycles'] + c['dram_ns'] * clock
    l2_line = 64.0 / c['l2_bytes_per_cycle']
    dram_line = dram / c['dram_mlp_lines']
    width = c['dispatch_width']

    # blocks: llvm-mca
    keys, lines_of, dis = {}, {}, {}
    for b in region['B']:
        il = disasm(b['pc'], b['bytes'])
        dis[(b['pc'], b['n'])] = il
        lines = mca_text(il)
        k = hashlib.sha1(('\n'.join(lines)).encode()).hexdigest()[:20]
        keys[(b['pc'], b['n'])] = k
        lines_of[k] = lines
    cyc = mca.cycles(lines_of)
    dep, memchain = set(), set()
    for il in dis.values():
        d, mc = dependent_loads(il)
        dep |= d
        memchain |= mc

    funcs = defaultdict(lambda: defaultdict(float))
    tot = defaultdict(float)
    unpriced = 0
    for b in region['B']:
        k = keys[(b['pc'], b['n'])]
        nm = len(dis[(b['pc'], b['n'])])
        per = cyc.get(k)
        f = syms.name(b['pc']) if syms else 'all'
        if per is None:
            unpriced += b['count'] * b['n']
            funcs[f]['unpriced_insns'] += b['count'] * b['n']
            per = b['n'] / 0.8  # the old flat model, for what llvm-mca could not read
        per = max(per, math.ceil(max(nm, 1) / width)) if nm else per
        il = [x for x in dis[(b['pc'], b['n'])] if x[1] not in ('udf', 'brk', 'hlt')]
        band = 0.0
        if any(r1_latency(x[1], x[2]) or div_range(x[1], x[2]) for x in il):
            lat0 = mca.info(lines_of[k])
            if lat0:
                floor = math.ceil(max(nm, 1) / width)
                lat1 = [r1_latency(x[1], x[2]) or l0 for x, l0 in zip(il, lat0)]
                c0, c1 = chain_per_iteration(il, lat0), chain_per_iteration(il, lat1)
                if c0 > c1 and per <= 1.1 * c0:   # latency-bound on the chain: the shorter latencies shorten it
                    per = max(per - (c0 - c1), floor)
                rng = [div_range(x[1], x[2]) for x in il]
                if any(rng) and per <= 1.1 * c1:
                    lmid = [(r[0] + r[1]) / 2 if r else l for r, l in zip(rng, lat1)]
                    llo = [r[0] if r else l for r, l in zip(rng, lat1)]
                    cm, cl = chain_per_iteration(il, lmid), chain_per_iteration(il, llo)
                    top = per
                    per = max(per - (c1 - cm), floor)
                    band = max(0.0, (top - max(top - (c1 - cl), floor)) / 2)
        row = funcs[f]
        row['div_band'] += b['count'] * band
        row['insns'] += b['count'] * b['n']
        row['core'] += b['count'] * per
        row['branch'] += b['mispred'] * c['mispredict'] + b['btb_l2'] * c['btb_l2_bubble'] + \
            b['btb_miss'] * c['btb_miss_bubble']
        row['ifetch'] += b['icl1'] * c['ic_l2_pen'] + b['icl2'] * (dram - l1) + b['itlb'] * c['itlb_pen']
        row['mispred'] += b['mispred']
        row['l2_lines'] += b['icl1'] + b['icl2']
        row['dram_lines'] += b['icl2']
    for m in region['M']:
        f = syms.name(m['pc']) if syms else 'all'
        row = funcs[f]
        row['l1d_miss'] += m['l1m'] + m['l2m']
        row['l2_miss'] += m['l2m']
        row['l2_lines'] += m['l1m'] + m['l2m'] + m['pf_l2'] + m['pf_dram']
        row['dram_lines'] += m['l2m'] + m['pf_dram'] + m['dram_wb']
        if m['st']:
            continue
        if m['pc'] in dep:
            lat = m['l1m'] * (l2 - l1) + m['l2m'] * (dram - l1) + m['tlb1'] * c['tlb1_pen'] + m['tlb2'] * c['walk_pen']
        else:
            lat = m['eff1'] * (l2 - l1) + m['eff2'] * (dram - l1) + \
                c['indep_tlb_factor'] * (m['tlb1'] * c['tlb1_pen'] + m['tlb2'] * c['walk_pen'])
        # forwarding: llvm-mca prices the load at l1_lat on its register chain; a chain through memory it does not
        # see at all, so there the whole forwarding latency is added but the one cycle llvm-mca gives the pair
        lat += m['fwd'] * ((c['stlf_lat'] - 1) if m['pc'] in memchain else (c['stlf_lat'] - l1))
        row['memlat'] += lat
    # region-level DRAM writes the simulator could not charge to an instruction (write streaming)
    attributed_wr = sum(m['dram_wb'] for m in region['M'])
    stream_wr = max(0, region['R']['dram_wr'] - attributed_wr)
    if stream_wr:
        st = [m for m in region['M'] if m['st']]
        totst = sum(m['count'] for m in st) or 1
        for m in st:
            funcs[syms.name(m['pc']) if syms else 'all']['dram_lines'] += stream_wr * m['count'] / totst
    for f, row in funcs.items():
        lat_bound = row['core'] + row['branch'] + row['ifetch'] + row['memlat']
        bw = max(row['l2_lines'] * l2_line, row['dram_lines'] * dram_line)
        row['cycles'] = max(lat_bound, bw)
        row['bw_bound'] = 1.0 if bw > lat_bound else 0.0
        for k2, v in row.items():
            tot[k2] += v
    tot['unpriced_insns'] = unpriced
    return funcs, tot


def bound_of(row):
    """what limits a function: bandwidth, memory latency, front end (branches, fetch) or the core"""
    if row.get('bw_bound'):
        return 'bandwidth'
    parts = {'core': row.get('core', 0), 'memory': row.get('memlat', 0),
             'frontend': row.get('branch', 0) + row.get('ifetch', 0)}
    return max(parts, key=parts.get)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('stats')
    ap.add_argument('--params', default=os.path.join(HERE, 'params.json'))
    ap.add_argument('--clock', type=float, default=1.02, help='GHz (1.02 stock, 1.785 boost)')
    ap.add_argument('--region', action='append')
    ap.add_argument('--elf', action='append')
    ap.add_argument('--json')
    ap.add_argument('--top', type=int, default=20)
    ap.add_argument('--mca-cache')
    a = ap.parse_args(argv)
    params = load_params(a.params)
    mca = MCA(a.mca_cache)
    syms = Symbols(a.elf) if a.elf else None
    regs = parse_stats(a.stats)
    out = {}
    for tag, r in regs.items():
        if a.region and tag not in a.region:
            continue
        funcs, tot = price(r, params, a.clock, mca, syms)
        out[tag] = {'total': dict(tot), 'functions': {k: dict(v) for k, v in funcs.items()}}
        ins = tot['insns'] or 1
        print(f"{tag}: {int(tot['insns'])} insns, {tot['cycles']:.0f} cycles, IPC {ins / max(tot['cycles'], 1):.2f}, "
              f"{tot['cycles'] / a.clock / 1e6:.3f} ms at {a.clock} GHz (core {tot['core']:.0f}, branch "
              f"{tot['branch']:.0f}, ifetch {tot['ifetch']:.0f}, memlat {tot['memlat']:.0f})")
        if syms:
            for f, row in sorted(funcs.items(), key=lambda kv: -kv[1]['cycles'])[:a.top]:
                print(f"  {row['cycles']:12.0f} {row['insns']:12.0f}  {f}")
    mca.save()
    if a.json:
        with open(a.json, 'w') as f:
            json.dump(out, f, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
