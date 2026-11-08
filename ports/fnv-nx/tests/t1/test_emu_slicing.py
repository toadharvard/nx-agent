#!/usr/bin/env python3
"""T1: the boot stage's scheduling arguments (tools/pipeline/build.py emu_slicing). S5 620572bc: threads switch at
host calls (--hc-slice), deterministic and about 10x faster than an instruction count; the timer slices of
2d1afaeb..6da559aa were nondeterministic. With an emu_guest.py that has --hc-slice: --budget 0 and nothing of ours
(no --slice-ms: emu_guest.py's 60 s fallback stays; EMU_SLICE_MS passes one only when set). Without it (a
timer-sliced or older emu_guest.py): the old --budget 6000000000. An explicit EMU_BUDGET is kept. Stand-in files."""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools', 'pipeline'))
import build  # noqa: E402

fails = 0


def ok(name, cond):
    global fails
    print(('ok   ' if cond else 'FAIL ') + name)
    fails += 0 if cond else 1


class B:
    def __init__(self, **cfg):
        self.cfg = cfg


d = tempfile.mkdtemp()
os.makedirs(os.path.join(d, 'tests', 's5'))
emu = os.path.join(d, 'tests', 's5', 'emu_guest.py')
build.ROOT = d
open(emu, 'w').write('ap.add_argument("--budget")\nap.add_argument("--slice-ms")\nap.add_argument("--hc-slice")\n')
ok('host-call preemption (620572bc): --budget 0, no timer of ours',
   build.emu_slicing(B(EMU_BUDGET='auto', EMU_SLICE_MS='')) == ['--budget', '0'])
ok('EMU_SLICE_MS given: passed', build.emu_slicing(B(EMU_BUDGET='auto', EMU_SLICE_MS='60000'))[-2:] == ['--slice-ms', '60000'])
ok('an explicit EMU_BUDGET kept', build.emu_slicing(B(EMU_BUDGET='1000', EMU_SLICE_MS=''))[:2] == ['--budget', '1000'])
ok('a config without the keys (older callers): the defaults', build.emu_slicing(B()) == ['--budget', '0'])
open(emu, 'w').write('ap.add_argument("--budget")\nap.add_argument("--slice-ms")\n')
ok('timer slices only (2d1afaeb..6da559aa, nondeterministic): the instruction count',
   build.emu_slicing(B(EMU_BUDGET='auto', EMU_SLICE_MS='')) == ['--budget', '6000000000'])
open(emu, 'w').write('ap.add_argument("--budget")\n')
ok('an older emu_guest.py: the instruction count', build.emu_slicing(B()) == ['--budget', '6000000000'])
cfg = dict((v[0], v[1]) for v in build.CONFIG)
ok('defaults: EMU_BUDGET auto, EMU_SLICE_MS empty', cfg['EMU_BUDGET'] == 'auto' and cfg['EMU_SLICE_MS'] == '')
print('test_emu_slicing: all ok' if not fails else 'test_emu_slicing: %d failed' % fails)
sys.exit(1 if fails else 0)
