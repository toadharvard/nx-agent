#!/usr/bin/env python3
"""Self-tests of the S15-perf tools (tools/perf): object census, flag diff, frame profiler attribution.

    python3 -I tests/s15/test_perf.py        (needs an AArch64 cross gcc: $CC or aarch64-linux-gnu-gcc; skips without)
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'perf'))
import objcensus  # noqa: E402
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools'))
import tables  # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


PROBE = r'''
extern int g_ext;
int g_def;
extern void callee(int);
int probe(int *p) { char buf[64]; for (int i = 0; i < 64; i++) buf[i] = (char)p[i]; callee(buf[p[1] & 63]);
                    return g_ext + g_def; }
int leaf(int a) { return a * 3; }
'''


def test_census(cc, tmp):
    src = os.path.join(tmp, 'p.c')
    open(src, 'w').write(PROBE)
    objs = {}
    for v, flags in (('base', ['-fPIC', '-fstack-protector-strong']), ('noprot', ['-fPIC', '-fno-stack-protector'])):
        os.makedirs(os.path.join(tmp, v))
        o = os.path.join(tmp, v, 'p.o')
        subprocess.run([cc, '-mabi=ilp32', '-O2', '-c', src, '-o', o] + flags, check=True)
        objs[v] = o

    class A:
        disasm, only, funcs = True, None, None
    tot, rows, mix = objcensus.census([objs['base']], A)
    check(tot['protected_funcs'] == 1, 'one stack-protected function (probe), leaf unprotected: %d'
          % tot['protected_funcs'])
    check(tot['got_stack_guard'] >= 2, 'canary read through the GOT twice: %d' % tot['got_stack_guard'])
    check(tot['got_other'] >= 2, 'g_ext and g_def through the GOT: %d' % tot['got_other'])
    check(tot['call_stack_chk_fail'] == 1 and tot['calls'] >= 2, 'calls classified: %s' % dict(
        (k, v) for k, v in tot.items() if k.startswith('call')))
    check(mix['total'] == tot['text_insns'], 'instruction mix covers the text')
    import flagdiff
    b = flagdiff.load(objs['base'])
    n = flagdiff.load(objs['noprot'])
    check(b['probe']['insns'] > n['probe']['insns'] and b['leaf']['insns'] == n['leaf']['insns'],
          'flagdiff: the protector costs probe %d -> %d instructions, leaf unchanged'
          % (b['probe']['insns'], n['probe']['insns']))


def test_attribution(tmp):
    import frameprof
    m = os.path.join(tmp, 'g.map')
    open(m, 'w').write(' .text          0x0000000030001000      0x100 /x/obj/src_havok_physics_a.cpp.o\n'
                       ' .text.fnvnx_lock\n                0x0000000030001100       0x40 /x/obj/toolchain_guest_threads.c.o\n'
                       ' .text          0x0000000030001200      0x80 /x/obj/platform_win32_sync.cpp.o\n')
    secs = frameprof.parse_map(m)
    check(len(secs) == 3 and secs[1][0] == 0x30001100, 'link map: 3 text input sections (%d)' % len(secs))

    class E:
        relocatable, base, link_base = False, 0, 0
        sym_addrs = [0x30001000, 0x30001100, 0x30001200]
        sym_names = [['hk_fn', '__fnv_00d2d110'], ['fnvnx_lock'], ['InterlockedIncrement']]
    at = frameprof.Attributor(E, {0x00d2d110: 'src/havok/common/base/thread/x.cpp'}, secs)
    lab, orig, obj = at.func(0x30001010)
    check(orig == 0x00d2d110 and at.subsystem(lab, orig, obj) == 'havok', 'image function -> unit -> havok')
    lab, orig, obj = at.func(0x30001104)
    check(orig is None and at.subsystem(lab, orig, obj) == 'guest-runtime', 'native lock -> guest-runtime')
    lab, orig, obj = at.func(0x30001210)
    check(at.subsystem(lab, orig, obj) == 'platform-win32', 'Interlocked -> platform-win32')


def test_budget(tmp):
    import json
    import frame_budget
    model = json.load(open(tables.path('budget_model.json')))
    model_cpi = model
    model = json.loads(json.dumps(model))   # rows without their own CPI take --perf's prices (the checks below)
    for h in model['hotspot_functions']:
        h.pop('cpi', None)
    b = frame_budget.build(model)
    check(b['schema'] == 'fnvnx-frame-budget/1', 'budget: schema')
    # a row with its own CPI is priced from it and its current instructions per call, whatever --perf says (V1 P1)
    bc = frame_budget.build(model_cpi, perf={'schema': 'fnvnx-perf/1', 'per_call_cycles': []})
    sc_ = next(h for h in model_cpi['hotspot_functions'] if h['id'] == 'fn_slerp')
    rc = next((r for r in bc['measured']['a57']['rows'] if r['id'] == 'fn_slerp'), None)
    want_c = frame_budget.mid([sc_['per_call_insn'][0] * sc_['cpi']['lo'], sc_['per_call_insn'][1] * sc_['cpi']['hi']]) \
        * frame_budget.mid(sc_['calls'])
    check(rc is not None and abs(rc['cycles_per_frame_mid'] - want_c) <= 1,
          'budget: a row with its own CPI is priced from it (slerp %.0f cycles per frame)' % want_c)
    # --perf (P0's fnvnx-perf/1): a priced row adds cycles x IPC - instructions on its thread; fn_interlocked is skipped
    sl = next(h for h in model['hotspot_functions'] if h['id'] == 'fn_slerp')
    perf = {'schema': 'fnvnx-perf/1', 'per_call_cycles': [
        {'id': 'fn_slerp', 'per_call_cycles': [sl['per_call_insn'][0] * 2, sl['per_call_insn'][1] * 2]},
        {'id': 'fn_interlocked', 'per_call_cycles': [100, 100]}]}
    bp = frame_budget.build(model, perf=perf)
    want = (frame_budget.mid(sl['per_call_insn']) * 2 * 0.8 - frame_budget.mid(sl['per_call_insn'])) * \
        frame_budget.mid(sl['calls']) / 1e6
    a57 = bp['measured']['a57']
    d_main = bp['scenarios'][0]['main_minsn']['mid'] - b['scenarios'][0]['main_minsn']['mid']
    check([r['id'] for r in a57['rows']] == ['fn_slerp'] and abs(a57['delta_minsn_mid'] - want) < 1e-3 and
          abs(d_main - want) < 2e-3 and bp['inputs']['a57'],
          'budget: --perf prices a row on its thread (slerp at CPI 2: +%.3f M main), interlocked skipped' % d_main)
    # V1 45e8724 T1: in every case the priced row costs its cycles at the clock, not cycles x 0.8 / that case's IPC
    ipc = next(a for a in model['assumptions'] if a['id'] == 'ipc')
    cyc_hi = sl['per_call_insn'][1] * 2 * sl['calls'][1]
    ins_hi = sl['per_call_insn'][1] * sl['calls'][1]
    want_ms = (cyc_hi - ins_hi / ipc['range'][0]) / (1020 * 1e3)
    got_ms = bp['scenarios'][0]['results']['1020']['core_ms']['0']['hi'] - b['scenarios'][0]['results']['1020']['core_ms']['0']['hi']
    check(abs(got_ms - want_ms) < 0.02, 'budget: --perf hi case = the row\'s cycles at 1020 MHz (+%.2f ms, want +%.2f)'
          % (got_ms, want_ms))
    check(abs(b['budget_minsn']['1020']['pass_45'] - 18.1) < 0.1 and abs(b['budget_minsn']['1785']['pass_45'] - 31.7) < 0.1,
          'budget: 45 fps per-core budget 18.1 M at 1020 MHz, 31.7 M at 1785 MHz (IPC 0.8)')
    want = {'%s@%d' % (s['id'], c) for s in model['scenarios'] for c in (1020, 1785)}
    check(set(b['gates']) == want and all(v in ('fail', 'pass_30', 'pass_45', 'target_60') for v in b['gates'].values()),
          'budget: a level per scenario and clock (%d)' % len(b['gates']))
    placed = {s['id']: s for s in b['scenarios']}
    main_items = set(placed['placed']['cores']['0']['items'])
    check('renderer_worker' in placed['placed']['cores']['1']['items'] and 'renderer_worker' not in main_items
          and 'ai_process_lists' not in main_items and 'cull' in main_items and 'scene_update' in main_items,
          'budget: render worker on core 1, AI off core 0, cull on core 0 without levers')
    check('3' not in placed['placed']['cores'] and '3' in placed['plan60']['cores'], 'budget: core 3 only with the opt-in')
    check(placed['plan45']['main_minsn']['mid'] < placed['placed']['main_minsn']['mid'], 'budget: plan45 moves work off main')
    r = placed['placed']['results']['1020']
    check(r['frame_ms']['lo'] <= r['frame_ms']['mid'] <= r['frame_ms']['hi'] and r['critical_core'] == 0,
          'budget: lo <= mid <= hi, main thread critical')
    # a measured profile: 3 frames, main + one AI linear thread
    prof = [{'label': 'until main', 'instructions': 5000000, 'subsystems': {}, 'threads': {'1': {'role': 'main', 'instructions': 5000000}}}]
    for n, (m, ai) in enumerate(((10e6, 4e6), (12e6, 5e6), (11e6, 6e6))):
        prof.append({'label': 'frame %d (x)' % (n + 1), 'instructions': m + ai, 'subsystems': {'havok': m / 2},
                     'threads': {'1': {'role': 'main', 'instructions': m}, '5': {'role': 'ai-linear-1', 'instructions': ai}}})
    bm = frame_budget.build(model, [prof])
    sc = {s['id']: s for s in bm['scenarios']}['placed']
    fp = bm['measured']['frameprof']
    check(fp['used'] and fp['frames'] == 3 and len(fp['boot']) == 1, 'budget: frameprof frames used, boot interval listed')
    items0 = sc['cores']['0']['items']
    check(abs(items0.get('measured_main', 0) - 11.0) < 1e-6 and 'cull' not in items0,
          'budget: measured main median 11.0 M replaces the modeled main items')
    rep = {'schema': 'fnvnx-render-cost/1', 'draws': 1000, 'per_draw_insn': {'main': [100, 100], 'worker': [2000, 2000]},
           'gpu_ms': {'720p_docked': [30, 40]}, 'basis': 'M'}
    br = frame_budget.build(model, None, rep)
    scr = {s['id']: s for s in br['scenarios']}['placed']
    check(abs(scr['cores']['0']['items']['renderer_main'] - 0.1) < 1e-6 and scr['results']['1785']['frame_ms']['lo'] >= 30,
          'budget: render report replaces the renderer items, GPU time bounds the frame')
    check(scr['results']['1785']['verdicts']['pass_45'] == 'fail', 'budget: a 30-40 ms GPU fails 45 fps')
    # hotspots: the model's subsystems and function proxies, ranked; the queue by main-thread gain
    hs = b['hotspots']
    subs = [h for h in hs if h['kind'] == 'subsystem']
    fns = [h for h in hs if h['kind'] == 'function']
    check({'cull', 'scene-graph update', 'animation', 'havok', 'render accumulation', 'ai'} <= {h['subsystem'] for h in subs},
          'hotspots: one row per subsystem of the model')
    check([h['rank'] for h in subs] == list(range(1, len(subs) + 1)) and
          all(subs[i]['minsn']['mid'] >= subs[i + 1]['minsn']['mid'] for i in range(len(subs) - 1)),
          'hotspots: subsystem rows ranked by mid instructions')
    cu = next(h for h in subs if h['subsystem'] == 'cull')
    cm = sum((i['minsn'][0] + i['minsn'][1]) / 2 for i in model['items'] if i.get('subsystem') == 'cull') + sum(
        ((h['per_call_insn'][0] + h['per_call_insn'][1]) / 2 - (h['before']['per_call_insn'][0] + h['before']['per_call_insn'][1]) / 2)
        * (h['calls'][0] + h['calls'][1]) / 2 / 1e6 for h in model['hotspot_functions']
        if h.get('before') and h.get('subsystem') == 'cull')   # the item plus S15-simd's override correction
    want = cm / (0.8 * 1020) * 1e3 / 22.2
    check(cu['critical'] and abs(cu['share_45']['1020'] - want) < 2e-3 and cu['fix'] and cu['basis'] == 'E',
          'hotspots: share of 22.2 ms at the assumed IPC, fix attached (cull %.3f)' % cu['share_45']['1020'])
    sl = next(h for h in fns if h['id'] == 'fn_slerp')
    hs = next(h for h in model['hotspot_functions'] if h['id'] == 'fn_slerp')   # per call [M] x calls [E]
    check(abs(sl['minsn']['lo'] - hs['per_call_insn'][0] * hs['calls'][0] / 1e6) < 1e-6 and
          abs(sl['minsn']['hi'] - hs['per_call_insn'][1] * hs['calls'][1] / 1e6) < 1e-6 and sl['basis'] == 'E',
          'hotspots: function proxy = per-call A64 count x calls per frame')
    # S15-simd overrides: a row with `before` becomes an item of (after - before) x calls on its subsystem
    mo = json.loads(json.dumps(model))
    mo['hotspot_functions'] = [{'id': 'fn_t', 'name': 't', 'subsystem': 'cull', 'role': 'main', 'per_call_insn': [10, 10],
                                'calls': [1000, 3000], 'before': {'per_call_insn': [30, 50]}}]
    bo = frame_budget.build(mo, None)
    ov = bo['measured']['overrides']
    check(ov['rows'] and abs(ov['rows'][0]['delta_minsn_mid'] - (10 - 40) * 2000 / 1e6) < 1e-6,
          'budget: an override row corrects its subsystem by (after - before) x calls')
    # content presets: the base preset scales nothing; a smaller fade multiplier scales the cull down
    import content_presets
    fbase, _ = content_presets.factors(model['content'], 'base')
    fa, _ = content_presets.factors(model['content'], 'switch_a')
    check(all(abs(v - 1) < 1e-9 for v in fbase.values()) and fa['cull'] < 1 and fa['render_accumulation'] < 1,
          'content: base preset is the model, switch_a scales cull and accumulation down')
    q = b['queue']
    check(q and q[0]['rank'] == 1 and all(q[i]['gain_main_minsn']['mid'] >= q[i + 1]['gain_main_minsn']['mid']
                                          for i in range(len(q) - 1)), 'hotspots: queue ranked by main-thread gain')
    hm = {h['id'] for h in bm['hotspots'] if h['kind'] == 'subsystem'}
    check('sub_havok' in hm and all(h['basis'] == 'M' for h in bm['hotspots'] if h['kind'] == 'subsystem'),
          'hotspots: measured frames give [M] subsystem rows')
    boot = [h for h in frame_budget.build(model, [[{'label': 'until main', 'instructions': 3e6, 'subsystems': {},
            'top': [['fnvnx_scan', 2e6], ['x', 1e6]], 'threads': {}}]])['hotspots'] if h['kind'] == 'boot']
    check([h['name'] for h in boot] == ['fnvnx_scan', 'x'] and 'share_45' not in boot[0], 'hotspots: boot rows [M]')
    # the render machine's own report (fnv-nx P6n report v1)
    p6 = {'schema': 'fnv-nx P6n report v1 (docs/RENDER_PERF.md §5)', 'summary': {'commit': 'abc'},
          'frame': {'mix_lo': {'draws': 1490, 'instr': {'main': 2.0e6, 'render': 3.0e6}},
                    'mix_hi': {'draws': 3710, 'instr': {'main': 5.0e6, 'render': 6.0e6}}},
          'gpu': {'summary_ms': {'mix_lo': {'docked_720p': {'eff_1.0': 7.0}}, 'mix_hi': {'docked_720p': {'eff_0.6': 12.0}}}}}
    b6 = frame_budget.build(model, None, p6)
    it6 = {i['id']: i for i in b6['items']}
    check(it6['renderer_main']['minsn'] == [2.0, 5.0] and it6['renderer_worker']['minsn'] == [3.0, 6.0] and
          b6['measured']['render']['gpu_ms'] == {'docked_720p': [7.0, 12.0]}, 'budget: P6n report.json read as fnvnx-render-cost/1')

    class E:
        threads = {}
    import frameprof

    class T:
        place = ('ai-linear-1', 2, 6, 0x2c)
    E.threads = {1: object(), 5: T()}
    check(frameprof.Prof.thread_info(E, 1) == ('main', 0, 1) and frameprof.Prof.thread_info(E, 5) == ('ai-linear-1', 2, 6),
          'frameprof: thread roles from the emulated placement')


def main():
    tmp = tempfile.mkdtemp(prefix='s15-')
    try:
        cc = os.environ.get('CC') or shutil.which('aarch64-linux-gnu-gcc')
        if cc:
            test_census(cc, tmp)
        else:
            print('skip census tests: no AArch64 cross gcc')
        test_attribution(tmp)
        test_budget(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('%d failed' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
