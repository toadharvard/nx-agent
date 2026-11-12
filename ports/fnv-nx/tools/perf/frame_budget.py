#!/usr/bin/env python3
"""frame_budget.py - the whole-frame CPU budget for the readiness gates (docs/PERF.md section 11).

    frame_budget.py [--model tools/perf/budget_model.json] [--frameprof prof.json ...] [--render report.json]
                    [--out budget.json] [--quiet]

Reads the work model (instructions per frame per item, each with the thread role that runs it, [E] until measured),
replaces items by measurements when given:

  * --frameprof: frameprof.py --json output. Intervals labelled "frame N (...)" with a per-thread breakdown
    ("threads": {tid: {"role", "ideal", "mask", "instructions"}}) replace the model's CPU items by the measured
    per-role counts (median frame as mid, the largest as hi, the smallest as lo). Without frame intervals (a boot that
    has not reached the main loop yet) the model stays and the profile's intervals are listed under measured.boot;
  * --perf: P0-perfmodel's fnvnx-perf/1: the hot rows' A57 cycle prices (per_call_cycles) as corrections;
  * --render: the render machine's cost report replaces the renderer items and adds its GPU time: schema
    fnvnx-render-cost/1 (RENDER_SCHEMA below), or the render machine's own render/native/perf/report.json ("fnv-nx
    P6n report v1", RENDER_PERF.md 5), read through p6n_to_cost (mix_lo..mix_hi frames, docked 720p GPU);

then places the work on the cores with toolchain/host/placement.h (main core 0, render worker core 1, AI linear
ideal 2/1 mask 0b110, the rest 0b110; 0b1110 with the core 3 opt-in): fixed-core work first, then each role's work
spread over its mask onto the least loaded core (the scheduler balances inside a mask). For each scenario of the
model (levers applied) and each clock it gives per-core and frame times as lo/mid/hi, and a verdict per target:

  pass       the hi case (most work, lowest IPC, worst frame factor) fits
  fail       even the lo case does not fit
  uncertain  in between: needs a measurement (frameprof on a frame, B1/B2 on hardware)

Targets (J-0150; every target is judged on the worst frame): fail below 30 fps (33.3 ms), 45 fps (22.2 ms,
informational), 60 fps stable (target_60: the worst frame at or under 16.7 ms, the owner's target at 1.02 GHz).
'level' (fail, pass_30, pass_45, target_60) is the highest target the mid worst frame meets. Output schema fnvnx-frame-budget/1, keys:

  ai2_critical_minsn per scenario: AI linear thread 2's path to event 6 (items' ai2_path shares, PERF.md 6.11);
  schema, inputs{...}, assumptions[{id,value,range,unit,basis,note}], targets[...], clocks_mhz[...],
  budget_minsn{clock: {target: M instructions per core in the target's frame time at the assumed IPC}},
  budget_mean_minsn{clock: {target: the same for the mean frame that keeps the worst frame inside it}},
  placement{role: {ideal, mask}}, items[{id, role, minsn[lo,hi], basis, ...}],
  measured{frameprof, render}, scenarios[{id, name, levers, core3, cores{core: {lo, mid, hi M instructions,
  items{item: mid M}}}, main_minsn{lo,mid,hi}, results{clock: {core_ms{core: {lo,mid,hi}}, cpu_ms{lo,mid,hi},
  gpu_ms, frame_ms{lo,mid,hi}, worst_frame_ms{lo,mid,hi}, critical_core, verdicts{target: pass|fail|uncertain},
  level}}}], needs_hardware[...], gates{"<scenario>@<clock>": level},
  hotspots[{rank, kind: subsystem|function|boot, id, name, subsystem, role, critical (runs on the main thread),
  basis (M|E), minsn{lo,mid,hi} (per frame; per boot for kind boot), ms{clock: {lo,mid,hi}} (IPC hi/mid/lo),
  share_45{clock: mid ms / 22.2 ms}, fix, gain_main_minsn[lo,hi], owner, needs_hardware, detail}],
  built_levers[...] (the 'placed' scenario's levers: in the build; hotspots and queue are after them, a fix marked
  done leaves the queue), queue[{rank, subsystem, fix, gain_main_minsn{lo,mid,hi}, gain_ms_45{clock: mid ms}, owner, needs_hardware}]

Hotspots (docs/PERF.md 11.4): subsystem rows are the frame's items by subsystem and thread role (frameprof's
per-subsystem median of measured frames when there are any, else the model: [E] unless every item is measured);
function rows are frameprof's top functions of measured frames, else the model's hotspot_functions (A64
instructions per call from a measured count [M] x calls per frame [E] = [E]); boot rows are the top functions of the
profiled intervals before the main loop [M], with their device time instead of a frame share. Rows rank by mid
instructions within their kind; the queue ranks the fixes by main-thread instructions saved (mid).
"""
import argparse
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'tests', 's5'))

RENDER_SCHEMA = '''fnvnx-render-cost/1 (render machine, render/native/perf/report.json):
  {"schema": "fnvnx-render-cost/1", "scene": "...", "draws": 2500,
   "per_draw_insn": {"main": [lo, hi], "worker": [lo, hi]},      # CPU instructions per draw, either side
   "frame_minsn": {"main": [lo, hi], "worker": [lo, hi]},         # optional: per-frame totals instead
   "gpu_ms": {"720p_docked": [lo, hi], ...}, "basis": "M"|"E"}'''

CHUNK = 0.05   # M instructions per balancing step


def load_placement():
    """{role name: (ideal, mask, mask3)} from placement.h (the emulator's parser, so all three agree)"""
    import emu_guest   # noqa: E402 - tests/s5
    out = {}
    for _, (name, ideal, mask, _prio, mask3) in emu_guest.placement_table().items():
        out[name] = (ideal, mask & 0x7, (mask3 & 0xF) if mask3 else (mask & 0x7))
    return out


def cores_of(role, placement, core3):
    if role == 'ai-linear':
        role = 'ai-linear-1'
    ideal, mask, mask3 = placement.get(role, placement['game'])
    m = mask3 if core3 else mask
    if ideal >= 0:
        m |= 1 << ideal
    return [c for c in range(4) if m >> c & 1], ideal


def mid(r):
    return (r[0] + r[1]) / 2.0


def assume(model, key):
    for a in model['assumptions']:
        if a['id'] == key:
            return a
    raise KeyError(key)


def frame_intervals(prof):
    return [iv for iv in prof if str(iv.get('label', '')).startswith('frame ')]


def apply_frameprof(model, profs):
    """measured per-role counts replace the model's CPU items (renderer items stay: the render report owns them)"""
    info = {'files': len(profs), 'frames': 0, 'boot': []}
    frames = []
    for prof in profs:
        frames += frame_intervals(prof)
        for iv in prof:
            if not str(iv.get('label', '')).startswith('frame '):
                info['boot'].append({'label': iv.get('label'), 'minsn': round(iv.get('instructions', 0) / 1e6, 3),
                                     'threads': {r: round(n / 1e6, 3) for r, n in per_role(iv).items()}})
    frames = [f for f in frames if f.get('threads')]
    info['frames'] = len(frames)
    if not frames:
        info['used'] = False
        return model['items'], info
    roles = sorted({r for f in frames for r in per_role(f)})
    items = [it for it in model['items'] if it.get('source') == 'render']
    for r in roles:
        vals = sorted(per_role(f).get(r, 0) / 1e6 for f in frames)
        lo, md, hi = vals[0], statistics.median(vals), vals[-1]
        items.append({'id': 'measured_' + r, 'name': 'frameprof: thread role %s' % r, 'role': r,
                      'minsn': [lo, hi], 'mid': md, 'basis': 'M'})
    info['used'] = True
    info['subsystems_median'] = {}
    subs = sorted({s for f in frames for s in f.get('subsystems', {})})
    for s in subs:
        info['subsystems_median'][s] = round(statistics.median(f.get('subsystems', {}).get(s, 0) for f in frames) / 1e6, 3)
    return items, info


def per_role(iv):
    out = {}
    for t in (iv.get('threads') or {}).values():
        out[t.get('role', '?')] = out.get(t.get('role', '?'), 0) + t.get('instructions', 0)
    return out


def p6n_to_cost(rep):
    """the render machine's report.json (fnv-nx P6n report v1, RENDER_PERF.md 5) -> fnvnx-render-cost/1: CPU per
    frame from its measured frames (mix_lo .. mix_hi: main = packet build, worker = Frame::Finish + deko3d
    recording, [M]), GPU from its docked 720p model (mix_lo at efficiency 1.0 .. mix_hi at 0.6, [E])"""
    fr = rep['frame']
    lo, hi = fr['mix_lo'], fr['mix_hi']
    out = {'schema': 'fnvnx-render-cost/1', 'scene': 'P6n synthetic mix (mix_lo %d .. mix_hi %d draws)' % (
        lo['draws'], hi['draws']), 'draws': hi['draws'], 'basis': 'M',
        'frame_minsn': {'main': [lo['instr']['main'] / 1e6, hi['instr']['main'] / 1e6],
                        'worker': [lo['instr']['render'] / 1e6, hi['instr']['render'] / 1e6]},
        'source_commit': (rep.get('summary') or {}).get('commit')}
    g = (rep.get('gpu') or {}).get('summary_ms') or {}
    try:
        out['gpu_ms'] = {'docked_720p': [g['mix_lo']['docked_720p']['eff_1.0'], g['mix_hi']['docked_720p']['eff_0.6']]}
        out['gpu_basis'] = 'E'
    except KeyError:
        pass
    return out


def apply_render(items, rep):
    if str(rep.get('schema', '')).startswith('fnv-nx P6n report'):
        rep = p6n_to_cost(rep)
    if rep.get('schema') != 'fnvnx-render-cost/1':
        raise SystemExit('render report: schema %r, want fnvnx-render-cost/1' % rep.get('schema'))
    out = []
    for it in items:
        side = {'renderer_main': 'main', 'renderer_worker': 'worker'}.get(it['id'])
        if side:
            it = dict(it)
            if rep.get('frame_minsn', {}).get(side):
                it['minsn'] = list(rep['frame_minsn'][side])
            elif rep.get('per_draw_insn', {}).get(side):
                d = rep.get('draws', it.get('draws', 2500))
                lo, hi = rep['per_draw_insn'][side]
                it['minsn'] = [lo * d / 1e6, hi * d / 1e6]
                it['draws'] = d
            it['basis'] = rep.get('basis', 'M')
            it.pop('mid', None)
        out.append(it)
    return out


def scenario_work(items, levers, sc):
    """items -> [(item id, role, [lo, mid, hi])] after the scenario's levers (moves and scales)"""
    work = []
    follows = {it['id']: it.get('follows') for it in items}   # corrections move and scale with the item they correct
    for it in items:
        lo, hi = it['minsn']
        md = it.get('mid', mid(it['minsn']))
        work.append([it['id'], it['role'], [lo, md, hi], it.get('split')])
    core3 = False
    for lid in sc['levers']:
        lv = levers[lid]
        core3 |= bool(lv.get('core3'))
        for iid, (flo, fhi) in lv.get('scales', {}).items():
            for w in work:
                if w[0] == iid or follows.get(w[0]) == iid:   # lo case: the larger saving
                    w[2] = [w[2][0] * flo, w[2][1] * mid((flo, fhi)), w[2][2] * fhi]
        for iid, (flo, fhi) in lv.get('moves', {}).items():
            for w in list(work):
                if (w[0] == iid or follows.get(w[0]) == iid) and w[1] == lv.get('from', 'main') and len(w) < 5:
                    fm = mid((flo, fhi))
                    moved = [w[2][0] * fhi, w[2][1] * fm, w[2][2] * flo]   # lo case: more moved
                    w[2] = [w[2][0] - moved[0], w[2][1] - moved[1], w[2][2] - moved[2]]
                    work.append([w[0] + '>' + lid, lv['to'], moved, None, True])
    return work, core3


def place_work(work, placement, core3, eff):
    """3 cases (lo, mid, hi) -> per case {core: M instructions}, {core: {item: M}}"""
    cases = []
    for k in range(3):
        load = {c: 0.0 for c in range(4)}
        per = {c: {} for c in range(4)}
        flexible = []
        for w in work:
            amount = w[2][k]
            if len(w) > 4 and w[4]:   # moved off main: runs at parallel efficiency
                amount /= eff[k]
            split = w[3]
            parts = list(split.items()) if split else [(w[1], 1.0)]
            for role, frac in parts:
                cores, ideal = cores_of(role, placement, core3)
                a = amount * frac
                if len(cores) == 1:
                    load[cores[0]] += a
                    per[cores[0]][w[0]] = per[cores[0]].get(w[0], 0) + a
                else:
                    flexible.append((w[0], cores, a))
        for iid, cores, a in flexible:   # water-fill: the scheduler moves threads to the idle core of their mask
            n = int(round(a / CHUNK))
            for i in range(n):
                c = min(cores, key=lambda x: (load[x], x))
                load[c] += a / n
                per[c][iid] = per[c].get(iid, 0) + a / n
        cases.append((load, per))
    return cases


def verdict(lo, hi, target):
    return 'pass' if hi <= target else 'fail' if lo > target else 'uncertain'


A57_SKIP = ('fn_interlocked',)   # its price leaves out the dmb drain; the interlocked_barriers item models that


def apply_perf(items, model, perf, ipcs):
    """P0-perfmodel's A57 prices (fnvnx-perf/1 per_call_cycles[], docs/PERF.md 12.6): for each hotspot_functions row
    with a price, the difference between its cycles and its instructions, both per frame, as an item on the row's
    thread in IPC-equivalent instructions: per case, cycles x that case's IPC - instructions, so that the frame's
    division by the case's IPC gives exactly the row's cycles (V1 45e8724 T1: converting at the mid IPC and dividing
    by the lo / hi IPC scaled the cycles by 0.8 / 1.33). The unpriced work keeps the assumed IPC. ipcs = the IPC of
    the lo, mid and hi cases (1.0, 0.8, 0.6); the lo case takes the lo cycles at the lo calls. minsn is in case order
    (lo case first), not sorted."""
    rows = {h['id']: h for h in model.get('hotspot_functions', [])}
    used = []
    # a row with its own A57 CPI (PERF.md 11.9: lo = throughput bound, hi = one call) is priced from it and the
    # model's current instructions per call, so a re-measured count re-prices it (V1 P1); the others take the
    # report's cycles per call
    priced = [{'id': h['id'], 'per_call_cycles': [h['per_call_insn'][0] * h['cpi']['lo'],
                                                   h['per_call_insn'][1] * h['cpi']['hi']]}
              for h in rows.values() if 'cpi' in h]
    have = {r['id'] for r in priced}
    for r in priced + [r for r in perf.get('per_call_cycles') or [] if r.get('id') not in have]:
        h = rows.get(r.get('id'))
        if not h or h['id'] in A57_SKIP or not r.get('per_call_cycles'):
            continue
        pc, ca, cy = h['per_call_insn'], h['calls'], r['per_call_cycles']
        ins = (pc[0] * ca[0], mid(pc) * mid(ca), pc[1] * ca[1])
        cyc = (cy[0] * ca[0], mid(cy) * mid(ca), cy[1] * ca[1])
        d = [(c * ipc - i) / 1e6 for c, ipc, i in zip(cyc, ipcs, ins)]
        items.append({'id': 'a57_' + h['id'], 'name': 'A57 price of %s (cycles vs instructions)' % h['name'],
                      'role': h.get('role', 'main'), 'subsystem': h.get('subsystem'),
                      'minsn': [d[0], d[2]], 'mid': d[1], 'basis': 'model',
                      'follows': follows_item(model, h.get('subsystem'))})
        used.append({'id': h['id'], 'cycles_per_frame_mid': round(cyc[1]), 'insns_per_frame_mid': round(ins[1]),
                     'delta_minsn_mid': round(d[1], 3)})
    return items, {'source': perf.get('head') or perf.get('schema'), 'rows': used,
                   'delta_minsn_mid': round(sum(u['delta_minsn_mid'] for u in used), 3)}


def follows_item(model, subsystem):
    """the model item a correction on `subsystem` follows through the levers (the first item of that subsystem)"""
    return next((it['id'] for it in model['items'] if it.get('subsystem') == subsystem), None)


def apply_overrides(items, model):
    """S15-simd's native overrides (tools/link/overrides, docs/PERF.md 11.6) as corrections to their subsystems'
    items: for each hotspot_functions row with a `before` (the decomp's own instructions per call, measured before the
    override), (after - before) instructions per call x calls per frame, on the row's thread and subsystem. The items
    are estimates of the decomp's code, so the frame then carries the code that runs; with --perf, apply_perf prices
    the after side in A57 cycles on top. lo pairs the lo per-call counts with the lo calls, hi the hi ones."""
    used = []
    for h in model.get('hotspot_functions', []):
        b = h.get('before')
        if not b:
            continue
        pa, pb, ca = h['per_call_insn'], b['per_call_insn'], h['calls']
        d = [(pa[0] - pb[0]) * ca[0] / 1e6, (mid(pa) - mid(pb)) * mid(ca) / 1e6, (pa[1] - pb[1]) * ca[1] / 1e6]
        items.append({'id': 'simd_' + h['id'], 'name': 'S15-simd override of %s (after - before)' % h['name'],
                      'role': h.get('role', 'main'), 'subsystem': h.get('subsystem'),
                      'minsn': [min(d[0], d[2]), max(d[0], d[2])], 'mid': d[1], 'basis': 'M',
                      'follows': follows_item(model, h.get('subsystem'))})
        used.append({'id': h['id'], 'delta_minsn_mid': round(d[1], 3)})
    return items, {'rows': used, 'delta_minsn_mid': round(sum(u['delta_minsn_mid'] for u in used), 3)}

def corrected_items(model):
    """the model's items with S15-simd's override corrections (apply_overrides), the work the code that runs does:
    the one place other models (a57model/mtmodel.py) take their items from, so the two agree"""
    items, _ = apply_overrides([dict(it) for it in model['items']], model)
    # fold each correction into the item it follows: a scheduler (mtmodel) cannot run negative work, and the sums
    # (lo + lo, mid + mid, hi + hi, the pairing build's scenario_work uses) stay the same
    by = {it['id']: it for it in items}
    out = []
    for it in items:
        f = by.get(it.get('follows'))
        if f is None or not it['id'].startswith('simd_'):
            out.append(it)
            continue
        fm = f.get('mid', mid(f['minsn']))
        f['minsn'] = [f['minsn'][0] + it['minsn'][0], f['minsn'][1] + it['minsn'][1]]
        f['mid'] = fm + it.get('mid', mid(it['minsn']))
    return out

def build(model, profs=None, render=None, perf=None):
    placement = load_placement()
    ipc = assume(model, 'ipc')
    clocks = assume(model, 'clocks_mhz')['value']
    eff = assume(model, 'parallel_efficiency')
    sync = assume(model, 'sync_overhead_minsn')
    wff = assume(model, 'worst_frame_factor')
    ipcs = (ipc['range'][1], ipc['value'], ipc['range'][0])          # lo case: high IPC
    effs = (eff['range'][1], eff['value'], eff['range'][0])
    wffs = (wff['range'][0], wff['value'], wff['range'][1])
    syncs = (sync['range'][0], sync['value'], sync['range'][1])
    items = [dict(it) for it in model['items']]
    measured = {}
    if profs:
        items, measured['frameprof'] = apply_frameprof(model, profs)
    if render:
        if str(render.get('schema', '')).startswith('fnv-nx P6n report'):
            render = p6n_to_cost(render)
        items = apply_render(items, render)
        measured['render'] = {k: render.get(k) for k in ('scene', 'draws', 'per_draw_insn', 'frame_minsn', 'gpu_ms',
                                                         'basis', 'gpu_basis', 'source_commit') if k in render}
    if not measured.get('frameprof', {}).get('used'):   # a measured frame already runs the overrides
        items, measured['overrides'] = apply_overrides(items, model)
    if perf and not measured.get('frameprof', {}).get('used'):   # measured frames are priced by P0 already
        items, measured['a57'] = apply_perf(items, model, perf, ipcs)
    if not measured.get('frameprof', {}).get('used'):   # a measured main thread already contains its hand-offs
        items.append({'id': 'sync', 'name': 'per-frame hand-offs on main', 'role': 'main',
                      'minsn': [syncs[0], syncs[2]], 'mid': syncs[1], 'basis': sync['basis']})
    levers = {lv['id']: lv for lv in model['levers']}
    targets = model['targets']
    gpu = None
    if render and render.get('gpu_ms'):
        k = sorted(render['gpu_ms'])[0]
        gpu = {'case': k, 'lo': render['gpu_ms'][k][0], 'hi': render['gpu_ms'][k][1]}
    out = {
        'schema': 'fnvnx-frame-budget/1',
        'inputs': {'model': model.get('schema'), 'scene': model.get('scene'),
                   'frameprof': bool(profs), 'render': bool(render), 'a57': bool(perf)},
        'assumptions': model['assumptions'], 'targets': targets, 'clocks_mhz': clocks,
        'budget_minsn': {}, 'budget_mean_minsn': {}, 'placement': {r: {'ideal': v[0], 'mask': v[1], 'mask_core3': v[2]} for r, v in placement.items()},
        'items': items, 'measured': measured, 'scenarios': [], 'gates': {},
        'needs_hardware': [
            'IPC of this code on the A57 (B1): every ms figure scales with it',
            'thread placement and migration under Horizon (B2), hand-off latencies (B3)',
            'GPU time per frame (B7) and the native renderer per-draw cost on device (render machine)',
        ] + ['lever %s: %s' % (lv['id'], lv['status']) for lv in model['levers'] if lv.get('needs_hardware')],
    }
    for clk in clocks:   # M instructions one core runs in the target's frame time at the assumed IPC
        out['budget_minsn'][str(clk)] = {t['id']: round(t['ms'] * clk * 1e3 * ipc['value'] / 1e6, 2) for t in targets}
        # the mean frame that keeps the worst frame inside the target (worst_frame_factor)
        out['budget_mean_minsn'][str(clk)] = {
            t['id']: round(t['ms'] / wff['value'] * clk * 1e3 * ipc['value'] / 1e6, 2)
            for t in targets}
    # every plan starts from the build: the 'placed' scenario's levers are in all scenarios (V1 B2: plan45 left out
    # barrier_relax)
    built_ids = next((list(sc['levers']) for sc in model['scenarios'] if sc['id'] == 'placed'), [])
    for sc in model['scenarios']:
        sc = dict(sc, levers=built_ids + [lid for lid in sc['levers'] if lid not in built_ids])
        work, core3 = scenario_work(items, levers, sc)
        cases = place_work(work, placement, core3, effs)
        cores = sorted(c for c in range(4) if any(cases[k][0][c] for k in range(3)))
        rs = {'id': sc['id'], 'name': sc['name'], 'levers': sc['levers'], 'core3': core3, 'cores': {}, 'results': {}}
        for c in cores:
            rs['cores'][str(c)] = {'lo': round(cases[0][0][c], 3), 'mid': round(cases[1][0][c], 3),
                                   'hi': round(cases[2][0][c], 3),
                                   'items': {i: round(v, 3) for i, v in sorted(cases[1][1][c].items())}}
        rs['main_minsn'] = rs['cores'].get('0', {'lo': 0, 'mid': 0, 'hi': 0})
        rs['main_minsn'] = {k: rs['main_minsn'][k] for k in ('lo', 'mid', 'hi')}
        # AI linear thread 2's critical path (PERF.md 6.11): what it runs before it signals event 6, which the world
        # render waits for under the MT1 cull sync: each item's ai2_path share of what stays on an AI linear thread
        share = {it['id']: it.get('ai2_path', 0) for it in items}
        base = lambda wid: wid.split('>')[0]
        rs['ai2_critical_minsn'] = {n: round(sum(w[2][k] * share.get(base(w[0]), 0) for w in work
                                                 if w[1] == 'ai-linear'), 3) for k, n in enumerate(('lo', 'mid', 'hi'))}
        for clk in clocks:
            core_ms = {}
            for c in cores:
                core_ms[str(c)] = {n: round(cases[k][0][c] * 1e6 / (ipcs[k] * clk * 1e6) * 1e3, 2)
                                   for k, n in enumerate(('lo', 'mid', 'hi'))}
            cpu = {n: max(v[n] for v in core_ms.values()) for n in ('lo', 'mid', 'hi')}
            crit = max(core_ms, key=lambda c: core_ms[c]['mid'])
            frame = dict(cpu)
            if gpu:
                frame = {'lo': max(cpu['lo'], gpu['lo']), 'mid': max(cpu['mid'], mid((gpu['lo'], gpu['hi']))),
                         'hi': max(cpu['hi'], gpu['hi'])}
            worst = {n: round(frame[n] * wffs[k], 2) for k, n in enumerate(('lo', 'mid', 'hi'))}
            verdicts, level = {}, 'fail'
            for t in targets:   # J-0150: every target on the worst frame (60 fps stable, not a mean)
                verdicts[t['id']] = verdict(worst['lo'], worst['hi'], t['ms'])
            if worst['mid'] <= 33.3:
                level = 'pass_30'
            if worst['mid'] <= 22.2:
                level = 'pass_45'
            if worst['mid'] <= 16.7:
                level = 'target_60'
            rs['results'][str(clk)] = {'core_ms': core_ms, 'cpu_ms': cpu, 'gpu_ms': gpu, 'frame_ms': frame,
                                       'worst_frame_ms': worst, 'critical_core': int(crit), 'verdicts': verdicts,
                                       'level': level}
            out['gates']['%s@%d' % (sc['id'], clk)] = level
        out['scenarios'].append(rs)
    # hotspots and the queue describe the build as it is: the items after the levers that are in it (the 'placed'
    # scenario's), and no queue row for a fix that is done
    built = next((sc for sc in model['scenarios'] if sc['id'] == 'placed'), {'levers': []})
    hot_items = []
    for it in items:
        it = dict(it)
        for lid in built['levers']:
            if it['id'] in levers[lid].get('scales', {}):
                flo, fhi = levers[lid]['scales'][it['id']]
                md = it.get('mid', mid(it['minsn']))
                it['minsn'] = [it['minsn'][0] * flo, it['minsn'][1] * fhi]
                it['mid'] = md * mid((flo, fhi))
        hot_items.append(it)
    out['built_levers'] = list(built['levers'])
    out['hotspots'], out['queue'] = hotspots(model, hot_items, profs or [], clocks, (ipcs[2], ipc['value'], ipcs[0]))
    return out


GOAL_MS = 22.2


def _ms(minsn, clk, ipcs):
    """M instructions (lo, mid, hi) -> ms at clk MHz; ipcs = (lo, mid, hi) IPC: the lo case runs at the highest"""
    return {n: round(minsn[k] * 1e6 / (ipcs[2 - k] * clk * 1e6) * 1e3, 3) for k, n in enumerate(('lo', 'mid', 'hi'))}


def hotspots(model, items, profs, clocks, ipcs):
    """ranked hotspot rows (subsystem, function, boot) and the optimization queue"""
    fixes = model.get('fixes', {})
    frames = [f for p in profs for f in frame_intervals(p)]
    rows = []

    def row(kind, rid, name, sub, role, basis, mm, detail=''):
        r = {'kind': kind, 'id': rid, 'name': name, 'subsystem': sub, 'role': role, 'critical': role == 'main',
             'basis': basis, 'minsn': {'lo': round(mm[0], 3), 'mid': round(mm[1], 3), 'hi': round(mm[2], 3)},
             'ms': {str(c): _ms(mm, c, ipcs) for c in clocks}}
        if kind != 'boot':
            r['share_45'] = {str(c): round(r['ms'][str(c)]['mid'] / GOAL_MS, 3) for c in clocks}
        fx = fixes.get(sub if kind != 'boot' else 'boot', {})
        r.update({'fix': fx.get('fix', ''), 'gain_main_minsn': fx.get('gain_main_minsn', [0, 0]),
                  'owner': fx.get('owner', ''), 'needs_hardware': fx.get('needs_hardware', False)})
        if detail:
            r['detail'] = detail
        rows.append(r)

    if frames:   # measured frames: frameprof's subsystems and top functions [M]
        subs = sorted({s_ for f in frames for s_ in f.get('subsystems', {})})
        for s_ in subs:
            v = sorted(f.get('subsystems', {}).get(s_, 0) / 1e6 for f in frames)
            row('subsystem', 'sub_' + s_, s_, s_, 'all threads', 'M', (v[0], statistics.median(v), v[-1]),
                '%d frames' % len(frames))
        fn = {}
        for f in frames:
            for name, n in f.get('top', []):
                fn.setdefault(name, []).append(n / 1e6)
        for name, v in fn.items():
            v = sorted(v + [0.0] * (len(frames) - len(v)))
            row('function', 'fn_' + name, name, '', 'all threads', 'M', (v[0], statistics.median(v), v[-1]),
                'top functions of %d frames' % len(frames))
    else:        # the model: items by subsystem, and the function proxies
        by = {}
        for it in items:
            sub = it.get('subsystem') or it['id']
            lo, hi = it['minsn']
            md = it.get('mid', mid(it['minsn']))
            b = by.setdefault((sub, it['role']), {'mm': [0.0, 0.0, 0.0], 'basis': set(), 'ids': []})
            for k, v in enumerate((lo, md, hi)):
                b['mm'][k] += v
            b['basis'].add(it.get('basis', 'E'))
            b['ids'].append(it['id'])
        for (sub, role), b in by.items():
            row('subsystem', 'sub_' + sub, sub, sub, role, 'M' if b['basis'] == {'M'} else 'E', b['mm'],
                'items: ' + ', '.join(b['ids']))
        for h in model.get('hotspot_functions', []):
            pc, ca = h['per_call_insn'], h['calls']
            mm = (pc[0] * ca[0] / 1e6, mid(pc) * mid(ca) / 1e6, pc[1] * ca[1] / 1e6)
            row('function', h['id'], h['name'], h['subsystem'], h.get('role', 'main'), 'E', mm,
                '%s-%s A64 instructions per call [%s] x %s-%s calls per frame [E]: %s%s' % (
                    pc[0], pc[1], h.get('per_call_basis', 'E'), ca[0], ca[1], h.get('calls_note', ''),
                    ('; ' + h['per_call_note']) if h.get('per_call_note') else ''))
    for p in profs:   # the profiled intervals before any frame [M]
        for iv in p:
            if str(iv.get('label', '')).startswith('frame ') or iv.get('instructions', 0) < 1e6:
                continue
            for name, n in iv.get('top', [])[:10]:
                v = n / 1e6
                row('boot', 'boot_' + name, name, 'boot', 'main', 'M', (v, v, v), 'interval: %s' % iv.get('label'))
    order = {'subsystem': 0, 'function': 1, 'boot': 2}
    rows.sort(key=lambda r: (order[r['kind']], -r['minsn']['mid'], r['id']))
    rank = {}
    for r in rows:
        rank[r['kind']] = rank.get(r['kind'], 0) + 1
        r['rank'] = rank[r['kind']]
    queue = []
    for sub, fx in fixes.items():
        g = fx.get('gain_main_minsn') or [0, 0]
        if max(g) <= 0 or fx.get('done'):
            continue
        gm = (g[0], mid(g), g[1])
        queue.append({'subsystem': sub, 'fix': fx.get('fix', ''), 'owner': fx.get('owner', ''),
                      'needs_hardware': fx.get('needs_hardware', False),
                      'gain_main_minsn': {'lo': g[0], 'mid': round(mid(g), 3), 'hi': g[1]},
                      'gain_ms_45': {str(c): round(_ms(gm, c, ipcs)['mid'], 2) for c in clocks}})
    queue.sort(key=lambda q: -q['gain_main_minsn']['mid'])
    for i, q in enumerate(queue):
        q['rank'] = i + 1
    return rows, queue


def summary(b):
    lines = ['frame budget (%s; frameprof %s, render %s)' % (b['inputs']['scene'], b['inputs']['frameprof'],
                                                            b['inputs']['render'])]
    for clk, t in b['budget_minsn'].items():
        lines.append('  budget per core at %s MHz, IPC %.1f: %s (mean frame for a stable target: %s)' % (
            clk, assume(b, 'ipc')['value'], ', '.join('%s %.1f M' % kv for kv in t.items()),
            ', '.join('%.1f M' % v for v in b['budget_mean_minsn'][clk].values())))
    for sc in b['scenarios']:
        m = sc['main_minsn']
        lines.append('  %-7s main %.1f M (%.1f-%.1f)  cores %s' % (
            sc['id'], m['mid'], m['lo'], m['hi'], ' '.join('%s:%.1f' % (c, v['mid']) for c, v in sc['cores'].items())))
        for clk, r in sc['results'].items():
            lines.append('     %4s MHz  frame %.1f ms (%.1f-%.1f), worst %.1f (%.1f-%.1f), core %d critical: %s  [%s]' % (
                clk, r['frame_ms']['mid'], r['frame_ms']['lo'], r['frame_ms']['hi'], r['worst_frame_ms']['mid'],
                r['worst_frame_ms']['lo'], r['worst_frame_ms']['hi'], r['critical_core'], r['level'],
                ' '.join('%s=%s' % kv for kv in r['verdicts'].items())))
    hs = [h for h in b.get('hotspots', []) if h['kind'] != 'boot']
    if hs:
        clks = [str(c) for c in b['clocks_mhz']]
        lines.append('  hotspots per frame (mid M instructions; share of 22.2 ms at %s MHz):' % ' / '.join(clks))
        for h in hs:
            lines.append('    %-9s %2d %-40s %-13s %6.2f M  %s  [%s]' % (
                h['kind'], h['rank'], h['name'][:40], h['role'][:13], h['minsn']['mid'],
                ' / '.join('%4.1f %%' % (100 * h['share_45'][c]) for c in clks), h['basis']))
    for q in b.get('queue', []):
        lines.append('  queue %d: %s: -%.2f M on main (%s ms) - %s' % (
            q['rank'], q['subsystem'], q['gain_main_minsn']['mid'],
            ' / '.join('%.2f' % v for v in q['gain_ms_45'].values()), q['fix'][:70]))
    return '\n'.join(lines)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--model', default=os.path.join(HERE, 'budget_model.json'))
    ap.add_argument('--frameprof', action='append', default=[])
    ap.add_argument('--render')
    ap.add_argument('--perf', help="P0-perfmodel's fnvnx-perf/1 (<head12>-perf.json): A57 prices of the hot rows")
    ap.add_argument('--out')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args(argv)
    model = json.load(open(a.model))
    profs = [json.load(open(p)) for p in a.frameprof]
    render = json.load(open(a.render)) if a.render else None
    perf = json.load(open(a.perf)) if a.perf else None
    b = build(model, profs, render, perf)
    if a.out:
        with open(a.out, 'w') as f:
            json.dump(b, f, indent=1)
            f.write('\n')
    if not a.quiet:
        print(summary(b))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
