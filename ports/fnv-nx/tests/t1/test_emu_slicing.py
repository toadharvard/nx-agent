#!/usr/bin/env python3
"""T1: the boot stage's scheduling arguments (tools/pipeline/build.py emu_slicing). S5 2d1afaeb: emu_start(count=N)
hooks every instruction (about 9 MIPS); time slices (--budget 0 --slice-ms 2000) are about 10x faster. With an
emu_guest.py that has --slice-ms: --budget 0 --slice-ms EMU_SLICE_MS; without it (budget 0 would mean no
preemption): the old --budget 6000000000; an explicit EMU_BUDGET is kept. A stand-in emu_guest.py."""
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
open(emu, 'w').write('ap.add_argument("--budget")\nap.add_argument("--slice-ms")\n')
ok('slices: --budget 0 --slice-ms 2000', build.emu_slicing(B(EMU_BUDGET='auto', EMU_SLICE_MS='2000')) ==
   ['--budget', '0', '--slice-ms', '2000'])
ok('slices: EMU_SLICE_MS taken', build.emu_slicing(B(EMU_BUDGET='auto', EMU_SLICE_MS='500'))[-1] == '500')
ok('an explicit EMU_BUDGET kept', build.emu_slicing(B(EMU_BUDGET='1000', EMU_SLICE_MS='2000'))[:2] == ['--budget', '1000'])
ok('a config without the keys (older callers): the defaults', build.emu_slicing(B()) == ['--budget', '0', '--slice-ms', '2000'])
open(emu, 'w').write('ap.add_argument("--budget")\n')
ok('no --slice-ms in emu_guest.py: the old instruction count', build.emu_slicing(B(EMU_BUDGET='auto', EMU_SLICE_MS='2000')) ==
   ['--budget', '6000000000'])
ok('the default in VARS is auto', dict((v[0], v[1]) for v in build.CONFIG)['EMU_BUDGET'] == 'auto')
print('test_emu_slicing: all ok' if not fails else 'test_emu_slicing: %d failed' % fails)
sys.exit(1 if fails else 0)
