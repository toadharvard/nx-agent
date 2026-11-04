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
  * --render: the render machine's cost report (schema fnvnx-render-cost/1, see RENDER_SCHEMA below) replaces the
    renderer items and adds its GPU time;

then places the work on the cores with toolchain/host/placement.h (main core 0, render worker core 1, AI linear
ideal 2/1 mask 0b110, the rest 0b110; 0b1110 with the core 3 opt-in): fixed-core work first, then each role's work
spread over its mask onto the least loaded core (the scheduler balances inside a mask). For each scenario of the
model (levers applied) and each clock it gives per-core and frame times as lo/mid/hi, and a verdict per target:

  pass       the hi case (most work, lowest IPC, worst frame factor) fits
  fail       even the lo case does not fit
  uncertain  in between: needs a measurement (frameprof on a frame, B1/B2 on hardware)

Targets (J-0132): fail below 30 fps (worst frame 33.3 ms), 45 fps stable (worst frame 22.2 ms), 60 fps stretch (mean
frame 16.7 ms). 'level' is the highest target the mid case meets. Output schema fnvnx-frame-budget/1, keys:

  schema, inputs{...}, assumptions[{id,value,range,unit,basis,note}], targets[...], clocks_mhz[...],
  budget_minsn{clock: {target: M instructions per core in the target's frame time at the assumed IPC}},
  budget_mean_minsn{clock: {target: the same for the mean frame, worst frame factor applied for floor/goal targets}},
  placement{role: {ideal, mask}}, items[{id, role, minsn[lo,hi], basis, ...}],
  measured{frameprof, render}, scenarios[{id, name, levers, core3, cores{core: {lo, mid, hi M instructions,
  items{item: mid M}}}, main_minsn{lo,mid,hi}, results{clock: {core_ms{core: {lo,mid,hi}}, cpu_ms{lo,mid,hi},
  gpu_ms, frame_ms{lo,mid,hi}, worst_frame_ms{lo,mid,hi}, critical_core, verdicts{target: pass|fail|uncertain},
  level}}}], needs_hardware[...], gates{"<scenario>@<clock>": level}
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


def apply_render(items, rep):
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
                if w[0] == iid:   # lo case: the larger saving
                    w[2] = [w[2][0] * flo, w[2][1] * mid((flo, fhi)), w[2][2] * fhi]
        for iid, (flo, fhi) in lv.get('moves', {}).items():
            for w in list(work):
                if w[0] == iid and w[1] == 'main':
                    fm = mid((flo, fhi))
                    moved = [w[2][0] * fhi, w[2][1] * fm, w[2][2] * flo]   # lo case: more moved
                    w[2] = [w[2][0] - moved[0], w[2][1] - moved[1], w[2][2] - moved[2]]
                    work.append([iid + '>' + lid, lv['to'], moved, None, True])
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


def build(model, profs=None, render=None):
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
        items = apply_render(items, render)
        measured['render'] = {k: render.get(k) for k in ('scene', 'draws', 'per_draw_insn', 'frame_minsn', 'gpu_ms', 'basis')}
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
                   'frameprof': bool(profs), 'render': bool(render)},
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
        # the mean frame that keeps the worst frame inside a floor/goal target (worst_frame_factor)
        out['budget_mean_minsn'][str(clk)] = {
            t['id']: round(t['ms'] / (wff['value'] if t['kind'] != 'stretch' else 1.0) * clk * 1e3 * ipc['value'] / 1e6, 2)
            for t in targets}
    for sc in model['scenarios']:
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
            for t in targets:
                f = frame if t['kind'] == 'stretch' else worst
                verdicts[t['id']] = verdict(f['lo'], f['hi'], t['ms'])
            if worst['mid'] <= 33.3:
                level = 'pass_30'
            if worst['mid'] <= 22.2:
                level = 'pass_45'
            if level == 'pass_45' and frame['mid'] <= 16.7:
                level = 'stretch_60'
            rs['results'][str(clk)] = {'core_ms': core_ms, 'cpu_ms': cpu, 'gpu_ms': gpu, 'frame_ms': frame,
                                       'worst_frame_ms': worst, 'critical_core': int(crit), 'verdicts': verdicts,
                                       'level': level}
            out['gates']['%s@%d' % (sc['id'], clk)] = level
        out['scenarios'].append(rs)
    return out


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
    return '\n'.join(lines)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--model', default=os.path.join(HERE, 'budget_model.json'))
    ap.add_argument('--frameprof', action='append', default=[])
    ap.add_argument('--render')
    ap.add_argument('--out')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args(argv)
    model = json.load(open(a.model))
    profs = [json.load(open(p)) for p in a.frameprof]
    render = json.load(open(a.render)) if a.render else None
    b = build(model, profs, render)
    if a.out:
        with open(a.out, 'w') as f:
            json.dump(b, f, indent=1)
            f.write('\n')
    if not a.quiet:
        print(summary(b))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
