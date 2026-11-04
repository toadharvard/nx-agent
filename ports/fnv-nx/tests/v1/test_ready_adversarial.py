#!/usr/bin/env python3
"""test_ready_adversarial.py - V1 (D14) against R0's tools/gates/ready.py (J-0135): adversarial and producer-shaped
reports fed to the scorecard.

    python3 -I tests/v1/test_ready_adversarial.py     -> one line per case, exit 1 when any case shows a defect

Each case takes tests/gates' world in which every gate passes (tests/gates/fixtures, made-up numbers only), changes
one thing and reads the JSON scorecard. A case is a defect when ready.py crashes, or when a gate passes on evidence
that does not show what the gate claims, or when a producer's real output can never reach the gate.
  R1  S16 scene "pass": "false" (a string)                    frames.compare must not pass
  R2  S16's own gate.json (tools/compare/compare.py, fnvnx-gate/1) with all five scenes passing
                                                             frames.compare must pass (the producer's format)
  R3  frameprof: a frame of 0 instructions                    no crash
  R4  render report: frame_ms main/render negative            perf.render.* must not pass
  R5  render report: frame_ms 0                               no crash
  R6  compile shard "3/2" instead of "2/2"                    compile.objects must not pass (shard 2 is missing)
  R7  equivalence shard "2/1" instead of "1/1"                equiv.code must not pass
  R8  gamedata/exe checksum file names another file (README) with the boot image's hash
                                                             boot.real_exe must not pass (J-0135 G1 for the gate)
  R9  the full report's boot stage failed, meta.json still has an image hash (published before V1's meta fix)
                                                             boot.real_exe must not pass
  R10 boot_peak_heap_bytes where S5 writes it (boot/milestones.json, not meta.json)
                                                             perf.memory must have data
  R11 S5's real milestones.json: raw boot reached winmain only, the stub boot reached first_cell
                                                             boot.first_cell must not pass
  R12 boot/summary.txt says "fnvnx milestone: first_cell", milestones.json says first_cell false
                                                             boot.first_cell must not pass
Round 2 (V1, J-0147 follow-up) and R0's own additions:
  R13  no hash; meta.json "synthesized", milestones.json "player exe"     boot.real_exe must not pass
  R13b no hash; milestones.json "player exe", the full report's data image synth.exe (R0)
  R14  a try farm's all-pass S16 file, newer than S16's                  frames.compare must not pass
  R14b, R14c the same for frameprof and the frame budget (R0)           perf.frame.boost / perf.budget.boost
  R15  the checksum file names FalloutNV.exe twice, another exe first   boot.real_exe must not pass
"""
import io
import json
import os
import sys
import tempfile
import contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'gates'))
import test_ready  # noqa: E402  (World, C)

C = test_ready.C
F = 'reports/full1/%s-full.md' % C
FAIL = 0


def case(label, change, check):
    global FAIL
    with tempfile.TemporaryDirectory() as tmp:
        w = test_ready.World(tmp)
        change(w)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                card = w.run()
        except Exception as e:  # noqa: BLE001
            print('%s: CRASH: %s: %s' % (label, type(e).__name__, e))
            FAIL = 1
            return
        bad = check(card['by_id'])
        if bad:
            FAIL = 1
        print('%s: %s' % (label, bad or 'ok'))


def must_not_pass(*gids):
    def chk(g):
        p = [i for i in gids if g[i]['status'] == 'pass']
        return ('PASSES: ' + ', '.join('%s=%s' % (i, g[i]['value']) for i in p)) if p else ''
    return chk


def frames(w, doc):
    w.write('reports/s16/%s-frames.json' % C, doc)


# R1
case('R1 scene pass "false"', lambda w: frames(w, {'commit': C, 'scenes': [
    {'name': 'main_menu', 'metric': 'ssim', 'value': 0.2, 'threshold': 0.98, 'pass': 'false'}]}),
    must_not_pass('frames.compare'))


# R2
def r2(w):
    sc = [{'id': s, 'title': s, 'class': 'static', 'reasons': [], 'status': 'pass', 'frames': [], 'worst': {}}
          for s in ('menu', 'docmitchell', 'goodsprings', 'saloon', 'quarry')]
    frames(w, {'format': 'fnvnx-gate/1', 'gate': 'S16-compare', 'status': 'pass', 'generated': '2026-11-04T00:00:00Z',
               'reference': {'kind': 'wine-wined3d-llvmpipe'}, 'candidate': {'kind': 'port', 'commit': C},
               'scene_set': 'fnvnx-scenes/1', 'summary': {'scenes': 5, 'pass': 5, 'fail': 0, 'incomplete': 0,
                                                          'unstable': 0}, 'scenes': sc})


case('R2 S16 gate.json, all pass', r2,
     lambda g: '' if g['frames.compare']['status'] == 'pass' and not g['frames.compare']['stale'] else
     'NEVER PASSES: %s %s%s (ready.py reads scenes[].pass and top-level commit; compare.py writes scenes[].status '
     'and candidate.commit)' % (g['frames.compare']['status'], g['frames.compare']['value'],
                                ' stale' if g['frames.compare']['stale'] else ''))

# R3
case('R3 frameprof frame of 0 instructions', lambda w: w.write('reports/prof1/%s-frameprof.json' % C, [
    {'label': 'boot until WinMain', 'instructions': 800000000}, {'label': 'frame 1', 'instructions': 0}]),
    lambda g: '')


def render(w, fm):
    w.edit_json('_render/report.json', lambda d: d['summary'].__setitem__('frame_ms', fm))


# R4, R5
case('R4 render frame_ms negative', lambda w: render(w, {'main': -1.0, 'render': -1.0}),
     must_not_pass('perf.render.stock', 'perf.render.boost'))     # J-0150: the stock clock counts
case('R5 render frame_ms 0', lambda w: render(w, {'main': 0, 'render': 0}), lambda g: '')

# R6
case('R6 compile shard 3/2 for 2/2', lambda w: w.sub('reports/farm3/%s-compile.md' % C, 'shard 2/2', 'shard 3/2'),
     must_not_pass('compile.objects'))


# R7
def r7(w):
    t = w.read('reports/eq1/%s-equiv.md' % C)
    first = t.splitlines()[0]
    w.sub('reports/eq1/%s-equiv.md' % C, first, first.replace('1/1', '2/1'))


case('R7 equivalence shard 2/1 for 1/1', r7, must_not_pass('equiv.code'))

# R8
case('R8 checksum file names README', lambda w: w.write('gamedata/exe/FalloutNV.exe.sha256',
                                                       'ab' * 32 + '  README\n'), must_not_pass('boot.real_exe'))


# R9
def r9(w):
    t = w.read(F)
    assert '| boot | ok |' in t, 'fixture full report has no "| boot | ok |" row'
    w.sub(F, '| boot | ok |', '| boot | fail |')
    w.remove('link/%s/boot/milestones.json' % C)


case('R9 boot stage failed, meta has an image hash', r9, must_not_pass('boot.real_exe'))


# R10
def r10(w):
    w.edit_json('link/%s/meta.json' % C, lambda d: d.pop('boot_peak_heap_bytes'))
    w.edit_json('link/%s/boot/milestones.json' % C, lambda d: d.__setitem__('boot_peak_heap_bytes', 1 << 30))


case('R10 peak heap in milestones.json (S5)', r10,
     lambda g: '' if g['perf.memory']['status'] != 'no-data' else
     'NEVER MEASURED: perf.memory reads meta.json boot_peak_heap_bytes; S5 writes it to boot/milestones.json only')


# R11
def r11(w):
    stub = dict(winmain=True, renderer_init=True, main_menu=True, first_frame=True, first_cell=True,
                milestone='first-cell', boot_peak_heap_bytes=1 << 29, stop='x')
    w.write('link/%s/boot/milestones.json' % C, {
        'schema': 'fnvnx-boot-milestones/1', 'image': 'player exe', 'winmain': True, 'renderer_init': False,
        'main_menu': False, 'first_frame': False, 'first_cell': False, 'milestone': 'winmain',
        'boot_peak_heap_bytes': 1 << 29, 'stop': 'fault', 'stub': stub})


case('R11 S5 milestones.json, only the stub boot reached first_cell', r11,
     must_not_pass('boot.main_menu', 'boot.first_frame', 'boot.first_cell'))


# R12
def r12(w):
    w.edit_json('link/%s/boot/milestones.json' % C, lambda d: d.__setitem__('first_cell', 0))
    w.write('link/%s/boot/summary.txt' % C, 'fnvnx milestone: first_cell\n')


case('R12 summary.txt overrides milestones.json', r12, must_not_pass('boot.first_cell'))


# Round 2 (after R0's fix of R1-R12): contradicting evidence, unmerged work
# R13: no image hash; meta.json says the boot ran on the synthesized image, milestones.json says "player exe"
def r13(w):
    w.edit_json('link/%s/meta.json' % C, lambda d: (d.pop('boot_image_sha256'), d.__setitem__('boot_image', 'synthesized')))


case('R13 image labels disagree, no hash', r13, must_not_pass('boot.real_exe'))


# R14: a try farm (an agent's unmerged head, tools/farm/push_wip.sh) publishes an all-pass S16 gate file, newer than
# the s16 farm's: ready.py's other gates skip reports/try<k>of<N> ("try farms run unmerged work")
def r14(w):
    frames(w, {'commit': C, 'scenes': [{'name': 'main_menu', 'metric': 'ssim', 'value': 0.2, 'threshold': 0.98,
                                        'pass': False}]})
    w.write('reports/try1of4/%s-frames.json' % C, {'commit': C, 'scenes': [
        {'name': 'main_menu', 'metric': 'ssim', 'value': 0.99, 'threshold': 0.98, 'pass': True}]},
        published=1900000000)


case('R14 all-pass frames from a try farm (unmerged work)', r14, must_not_pass('frames.compare'))


# R15: two lines for FalloutNV.exe in the checksum file, another exe's first (the pinned hash second)
def r15(w):
    w.write('gamedata/exe/FalloutNV.exe.sha256', 'cd' * 32 + '  FalloutNV.exe\n' + 'ab' * 32 + '  FalloutNV.exe\n')
    w.edit_json('link/%s/meta.json' % C, lambda d: d.__setitem__('boot_image_sha256', 'ab' * 32))


case('R15 checksum file names FalloutNV.exe twice, differently', r15, must_not_pass('boot.real_exe'))

# R13b (R0): the report's data image contradicts S5's label
def r13b(w):
    w.edit_json('link/%s/meta.json' % C, lambda d: d.pop('boot_image_sha256'))
    w.sub(F, "- data image: the owner's FalloutNV.exe", '- data image: build/boot/synth.exe')


case('R13b no hash, milestones.json "player exe", report data image synth.exe', r13b, must_not_pass('boot.real_exe'))


# R14b, R14c (R0): try farms for frameprof and the frame budget
def r14b(w):
    w.write('reports/prof1/%s-frameprof.json' % C, [{'label': 'boot until WinMain', 'instructions': 800000000},
                                                   {'label': 'frame 1', 'instructions': 90000000}])
    w.write('reports/try2of4/aaaaaaaaaaaa-frameprof.json', [{'label': 'boot until WinMain', 'instructions': 800000000},
                                                           {'label': 'frame 1', 'instructions': 1000000}],
            published=w.latest() + 60)


def r14c(w):
    b = json.loads(w.read('reports/prof1/%s-frame-budget.json' % C))
    good = json.dumps(b)
    for s in b.get('scenarios', []):
        for r in (s.get('results') or {}).values():
            if isinstance(r, dict) and isinstance(r.get('verdicts'), dict):
                r['verdicts'] = {k: 'fail' for k in r['verdicts']}
    w.write('reports/prof1/%s-frame-budget.json' % C, b)
    w.write('reports/try3of4/aaaaaaaaaaaa-frame-budget.json', good, published=w.latest() + 60)


case('R14b try farm frameprof newer than the merged one', r14b,
     must_not_pass('perf.frame.stock', 'perf.frame.boost'))
case('R14c try farm frame budget newer than the merged one', r14c,
     must_not_pass('perf.budget.stock', 'perf.budget.boost'))
sys.exit(FAIL)
