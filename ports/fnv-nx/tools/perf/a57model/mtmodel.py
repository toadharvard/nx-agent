#!/usr/bin/env python3
"""mtmodel.py - the 3-core frame model (docs/PERF.md 12.7): the frame's work of tools/perf/budget_model.json, priced
with the A57 model's CPI where it has one, scheduled on the Switch's application cores with placement.h's masks and
the frame's hand-offs, in 1 us steps. Gives the frame time as the critical path, not only as the busiest core.

    mtmodel.py [--model tools/perf/budget_model.json] [--funcs funcmodel.json] [--scenario ID]... [--json OUT]
               [--wake-us 15] [--flat]

Threads and cores (toolchain/host/placement.h through frame_budget.load_placement): main on core 0, the render worker
on core 1, AI linear 1/2 and game threads on mask 0b110 (0b1110 with the core 3 lever). Priorities: main, render
worker, AI linear and workers, background.
Main runs its phases in the main loop's order (PHASES). Work a lever moves off main (frame_budget.scenario_work)
is forked to a worker of the lever's role at the start of its phase, after --wake-us, at the model's
parallel_efficiency. Main joins it at the end of the phase and waits if the worker is late (mt_cull's
non-overlapping share, the water records, is joined at once). The AI linear threads are signalled before animation
and joined at the frame's end, their two halves staggered; threaded_anim's work queues behind the AI and comes back
at the same join (STRUCT, from the decomp). The render worker is pipelined (it runs the previous frame's commands)
and is never joined. Background work fills idle time. Locks: the model's sync_overhead_minsn on main plus one wake per hand-off.
Frame = max(main's critical path, every core's busy time).
CPI per item: the mean of funcmodel.py's budget_rows for the item's subsystem (SUBSYS), steady CPI for the lo case
and one-call CPI for the hi case; other items keep 1/ipc (0.8) and are flagged. --flat prices everything at 1/ipc
(the frame_budget baseline). Prints numbers only.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PERF = os.path.join(HERE, '..')
sys.path.insert(0, os.path.join(PERF, '..'))
import tables  # noqa: E402  (the decomp-derived budget model is a private table: tools/tables.py)
sys.path.insert(0, PERF)
import frame_budget  # noqa: E402

PHASES = ['game_logic', 'havok', 'animation', 'scene_update', 'cull', 'render_accumulation', 'renderer_main',
          'interlocked_barriers']
# The frame's hand-off structure (S15-threads, from the decomp: src/fallout/misc/main.cpp ~9029-9084,
# ailineartaskthreadmanager.cpp, mtrenderingsystem_o2_variant.cpp):
# - main signals the AI linear threads before the per-frame body (scene graph, cull, render)
#   and joins them (a counting semaphore) after it; then main drains the packed task queue,
#   where threaded_anim's controller updates come back;
# - the two AI linear threads stagger each other (SetEvent / WaitForSingleObject): their halves run one after the other;
# - Rendering Task Thread 2 (mt_cull), as MT1 ships it (S15-threads, patches/mt1_cull.s1b, PERF.md 6.8): every record
#   but 21 runs inline on main through the renderer's non-MT branch (the worker passes their stages at once), so the
#   lever's moved work is record 21 only, the main-scene split: main signals stage 21 , culls its
#   own lists, and joins at the join right after them - the end of its own share of the phase. The whole
#   moved share overlaps (mt_cull_overlap 1.0); the lever's size is budget_model.json's;
# - Havok MT: the calling thread processes jobs too, then waits for the pool (fork / join around the phase).
STRUCT = {'ai_fork_before': 'animation', 'ai_join_after': 'interlocked_barriers', 'ai_stagger': True,
          'mt_cull_overlap': 1.0,
          # where main joins a lever's overlapping work when not at the end of its own phase (none under MT1; the
          # stock PC path joined the shadow records: {'mt_cull': 'render_accumulation'})
          'join_after': {}}
SUBSYS = {'scene_update': ['fn_nibound_update', 'fn_world_transform', 'fn_node_update'], 'cull': ['fn_cull_test'],
          'animation': ['fn_slerp', 'fn_quat_from_matrix'], 'havok': ['fn_gsk'],
          'render_accumulation': ['fn_pass_bucket']}
PRIO = {'main': 0, 'render-worker': 1, 'ai-linear-1': 2, 'ai-linear-2': 2, 'worker': 2, 'game': 4}


class Thread:
    def __init__(self, name, cores, prio):
        self.name, self.cores, self.prio = name, cores, prio
        self.ops = []        # ('run', cycles, label) | ('fork', key, thread, cycles, label) | ('join', key)
        self.busy = 0.0
        self.last_core = None


def simulate(threads, cycles_per_us, wake_us, max_us=200000):
    """-> (main end us, {thread: busy us}, {core: busy us}, main wait us, {key: (fork us, done us)})"""
    done, ready_at, started = {}, {}, {}
    waits = {}
    queues = {t.name: [] for t in threads}  # forked tasks waiting on a worker: (ready us, key, cycles)
    by = {t.name: t for t in threads}
    core_busy = {}
    main_wait = 0.0
    t_us = 0
    main_end = None
    pos = {t.name: 0 for t in threads}
    rem = {t.name: None for t in threads}   # cycles left of the current run op
    cur_task = {t.name: None for t in threads}
    while t_us < max_us:
        # advance non-run ops (fork / join) and pick the next work of each thread
        runnable = []
        for t in threads:
            while True:
                if rem[t.name] is not None:
                    runnable.append(t)
                    break
                if cur_task[t.name] is None and queues[t.name]:
                    q = queues[t.name]
                    if q[0][0] <= t_us:
                        _r, key, cyc = q.pop(0)
                        cur_task[t.name] = key
                        started[key] = t_us
                        rem[t.name] = cyc
                        continue
                if pos[t.name] >= len(t.ops):
                    break
                op = t.ops[pos[t.name]]
                if op[0] == 'run':
                    rem[t.name] = op[1]
                    pos[t.name] += 1
                elif op[0] == 'fork':
                    _o, key, target, cyc, _l = op
                    queues[target].append((t_us + wake_us, key, cyc))
                    ready_at[key] = t_us
                    pos[t.name] += 1
                elif op[0] == 'join':
                    if op[1] in done:
                        pos[t.name] += 1
                    else:
                        if t.name == 'main':
                            main_wait += 1
                            base = op[1].split('#')[0].split('>')[-1] if '>' in op[1] else op[1].rstrip('0123456789')
                            waits[base] = waits.get(base, 0) + 1
                        break
        if main_end is None and pos['main'] >= len(by['main'].ops) and rem['main'] is None:
            main_end = t_us
        if main_end is not None and not any(rem[x.name] is not None or queues[x.name] or
                                            (pos[x.name] < len(x.ops) and x.name != 'main') for x in threads):
            break
        # cores pick the highest-priority runnable thread allowed on them (stay on the last core when possible)
        taken = set()
        for c in sorted({c for t in threads for c in t.cores}):
            cand = [t for t in runnable if c in t.cores and t.name not in taken]
            if not cand:
                continue
            t = min(cand, key=lambda x: (x.prio, 0 if x.last_core == c else 1, x.name))
            taken.add(t.name)
            t.last_core = c
            rem[t.name] -= cycles_per_us
            t.busy += 1
            core_busy[c] = core_busy.get(c, 0) + 1
            if rem[t.name] <= 0:
                rem[t.name] = None
                if cur_task[t.name] is not None:
                    done[cur_task[t.name]] = t_us + 1
                    cur_task[t.name] = None
        t_us += 1
    simulate.waits = waits
    return main_end if main_end is not None else t_us, {t.name: t.busy for t in threads}, core_busy, main_wait, \
        {k: (ready_at.get(k), done.get(k)) for k in ready_at}


def cpi_of(funcs, flat_cpi):
    rows = {r['id']: r for r in (funcs or {}).get('budget_rows', []) if r.get('cpi')}
    out = {}
    for item, ids in SUBSYS.items():
        rs = [rows[i] for i in ids if i in rows]
        if rs:
            out[item] = (sum(r['cpi'][0] for r in rs) / len(rs), sum(r['cpi'][1] for r in rs) / len(rs))
    return out


def build_threads(work, placement, core3, eff, k, cpi, flat_cpi, sync_minsn):
    def cores(role):
        return frame_budget.cores_of(role, placement, core3)[0]

    def cyc(iid, minsn, moved=False):
        base = iid.split('>')[0]
        c = cpi.get(base)
        x = flat_cpi if c is None else (c[0] if k == 0 else c[1] if k == 2 else (c[0] + c[1]) / 2)
        return minsn * 1e6 * x / (eff if moved else 1.0)

    main = Thread('main', cores('main'), PRIO['main'])
    rw = Thread('render-worker', cores('render-worker'), PRIO['render-worker'])
    ai = [Thread('ai-linear-1', cores('ai-linear-1'), PRIO['ai-linear-1']),
          Thread('ai-linear-2', cores('ai-linear-2'), PRIO['ai-linear-2'])]
    wk = [Thread('worker-1', cores('game'), PRIO['worker']), Thread('worker-2', cores('game'), PRIO['worker'])]
    bg = Thread('background', cores('game'), PRIO['game'])
    by_item, moved = {}, {}
    for w in work:
        if len(w) > 4 and w[4]:
            moved.setdefault(w[0].split('>')[0], []).append((w[0], w[1], w[2][k]))
        else:
            by_item[w[0]] = w
    ai_item = by_item.get('ai_process_lists')
    ai_keys = []

    def fork_ai():
        if not ai_item:
            return
        parts = [(t, (ai_item[3] or {}).get(t.name, 0.5)) for t in ai]
        if STRUCT['ai_stagger']:   # the halves run one after the other: one task on one AI thread
            parts = [(ai[0], 1.0)]
        for i, (t, frac) in enumerate(parts):
            key = f'ai{i}'
            main.ops.append(('fork', key, t.name, cyc('ai_process_lists', ai_item[2][k] * frac), 'ai'))
            ai_keys.append(key)

    order = [p for p in PHASES if p in by_item] + [i for i, w in by_item.items() if w[1] == 'main' and i not in PHASES]
    if STRUCT['ai_fork_before'] not in order:
        fork_ai()
    late = []   # moved work joined with the AI (threaded_anim's queued updates)
    pending = {}  # phase -> joins deferred to its end
    for iid in order:
        w = by_item[iid]
        if w[1] != 'main':
            continue
        if iid == STRUCT['ai_fork_before']:
            fork_ai()
        keys, serial = [], []
        for j, (mid_, role, m) in enumerate(moved.get(iid, [])):
            if role == 'ai-linear':   # queued behind the AI on its threads, back at the AI join
                key = f'{mid_}#ai'
                main.ops.append(('fork', key, ai[0].name, cyc(mid_, m, True), mid_))
                late.append(key)
                continue
            share = STRUCT['mt_cull_overlap'] if mid_.endswith('>mt_cull') else 1.0
            # the serial share first (signalled and waited at once: no overlap with main), then the overlapping one
            for part, frac in (('s', 1.0 - share), ('o', share)):
                if frac <= 0:
                    continue
                for n, tn in enumerate(t.name for t in wk):
                    key = f'{mid_}#{part}{n}'
                    main.ops.append(('fork', key, tn, cyc(mid_, m * frac / len(wk), True), mid_))
                    (keys if part == 'o' else serial).append(key)
                if part == 's':
                    main.ops += [('join', key) for key in serial]
                    serial = []
        main.ops.append(('run', cyc(iid, w[2][k]), iid))
        for key in keys:
            lever = key.split('>')[1].split('#')[0]
            at = STRUCT['join_after'].get(lever, iid)
            pending.setdefault(at if at in order[order.index(iid):] else iid, []).append(key)
        main.ops += [('join', key) for key in pending.pop(iid, [])]
        if iid == STRUCT['ai_join_after']:
            main.ops += [('join', key) for key in ai_keys + late]
    if STRUCT['ai_join_after'] not in order:
        main.ops += [('join', key) for key in ai_keys + late]
    main.ops.append(('run', sync_minsn[k] * 1e6 * flat_cpi, 'sync'))
    for iid, w in by_item.items():
        if w[1] == 'render-worker':
            rw.ops.append(('run', cyc(iid, w[2][k]), iid))
        elif w[1] == 'game':
            bg.ops.append(('run', cyc(iid, w[2][k]), iid))
    return [main, rw] + ai + wk + [bg]


def run(model, funcs, scenarios, clocks, wake_us, flat):
    ipc = frame_budget.assume(model, 'ipc')['value']
    flat_cpi = 1.0 / ipc
    eff = frame_budget.assume(model, 'parallel_efficiency')
    effs = (eff['range'][1], eff['value'], eff['range'][0])
    sync = frame_budget.assume(model, 'sync_overhead_minsn')
    syncs = (sync['range'][0], sync['value'], sync['range'][1])
    placement = frame_budget.load_placement()
    levers = {lv['id']: lv for lv in model['levers']}
    cpi = {} if flat else cpi_of(funcs, flat_cpi)
    out = {'schema': 'fnvnx-mtframe/1', 'flat': flat, 'wake_us': wake_us, 'cpi': cpi,
           'unpriced_items': [it['id'] for it in model['items'] if it['id'] not in cpi], 'scenarios': []}
    for sc in model['scenarios']:
        if scenarios and sc['id'] not in scenarios:
            continue
        work, core3 = frame_budget.scenario_work(frame_budget.corrected_items(model), levers, sc)
        rs = {'id': sc['id'], 'core3': core3, 'results': {}}
        for clk in clocks:
            cases = []
            for k in range(3):
                th = build_threads(work, placement, core3, effs[k], k, cpi, flat_cpi, syncs)
                end, busy, core_busy, wait, tasks = simulate(th, clk, wake_us)
                frame = max([end] + list(core_busy.values()))
                cases.append({'frame_ms': round(frame / 1e3, 2), 'main_path_ms': round(end / 1e3, 2),
                              'main_wait_ms': round(wait / 1e3, 2),
                              'main_wait_by_join_ms': {j: round(v / 1e3, 2) for j, v in sorted(simulate.waits.items())},
                              'core_ms': {str(c): round(v / 1e3, 2) for c, v in sorted(core_busy.items())},
                              'critical': 'main path' if end >= max(core_busy.values()) else
                              'core %s' % max(core_busy, key=core_busy.get)})
            rs['results'][str(clk)] = dict(zip(('lo', 'mid', 'hi'), cases))
        out['scenarios'].append(rs)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--model', default=None)
    ap.add_argument('--funcs', help="funcmodel.py's JSON (budget_rows)")
    ap.add_argument('--scenario', action='append')
    ap.add_argument('--clock', type=int, action='append', help='MHz (default 1020 and 1785)')
    ap.add_argument('--wake-us', type=float, default=15.0)
    ap.add_argument('--flat', action='store_true')
    ap.add_argument('--no-stagger', action='store_true', help='the two AI linear halves in parallel (sensitivity)')
    ap.add_argument('--overlap', type=float, help="mt_cull's overlapping share (default %s)" % STRUCT['mt_cull_overlap'])
    ap.add_argument('--json')
    a = ap.parse_args(argv)
    a.model = a.model or tables.path('budget_model.json')
    with open(a.model) as f:
        model = json.load(f)
    funcs = None
    if a.funcs:
        with open(a.funcs) as f:
            funcs = json.load(f)
    if a.no_stagger:
        STRUCT['ai_stagger'] = False
    if a.overlap is not None:
        STRUCT['mt_cull_overlap'] = a.overlap
    out = run(model, funcs, a.scenario, a.clock or [1020, 1785], a.wake_us, a.flat)
    out['struct'] = dict(STRUCT)
    print(f"CPI from the A57 model: {', '.join(f'{k} {v[0]:.2f}-{v[1]:.2f}' for k, v in out['cpi'].items()) or 'none'}"
          f"; at 1/IPC: {', '.join(out['unpriced_items'])}")
    for s in out['scenarios']:
        for clk, r in s['results'].items():
            m = r['mid']
            print(f"{s['id']:8} {clk} MHz: frame {r['lo']['frame_ms']}/{m['frame_ms']}/{r['hi']['frame_ms']} ms "
                  f"(lo/mid/hi), main path {m['main_path_ms']} ms, main waits {m['main_wait_ms']} ms, "
                  f"cores {m['core_ms']}, critical {m['critical']}, waits by join {m['main_wait_by_join_ms']}")
    if a.json:
        with open(a.json, 'w') as f:
            json.dump(out, f, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
