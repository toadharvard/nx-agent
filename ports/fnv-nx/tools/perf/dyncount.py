#!/usr/bin/env python3
"""dyncount.py - dynamic instruction counts of one function on the original x86 code and on our AArch64 code
(stream S15-perf, docs/PERF.md).

    dyncount.py <source>:<0xaddr> [...] [--seeds N] [--targets FILE] [--max-steps N]

Runs each function through S13's differential harness (tools/difffuzz, docs/DIFFFUZZ.md): the MSVC 2008 build of the
pristine decomp (byte-identical to FalloutNV.exe) under Unicorn x86 and our AArch64 ILP32 object, linked by address,
under Unicorn AArch64, on the same fuzzed inputs (seeds 1..N), callees intercepted on both sides. Counted: the
instructions each machine executes inside the function body (x86: decoded per block; AArch64: block bytes / 4),
excluding intercepted callees; the background memory of the world is zero and random (--bg). Reported per
function: median x86 and A64 counts over the cases that ran on both
without error, the ratio A64/x86, and whether the two machines agreed on the case.

The object set is chosen as for difffuzz (FNVNX_DFPROFILE / FNVNX_A64OBJ / FNVNX_DFLINK, tools/difffuzz/config.py);
pointing FNVNX_A64OBJ at a directory of the same units compiled with other flags (and FNVNX_DFA64 at a fresh cache
directory) gives the dynamic cost of a flag. Prints counts only.
"""
import argparse
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'difffuzz'))

import capstone  # noqa: E402
from unicorn import UC_HOOK_BLOCK  # noqa: E402

import config  # noqa: E402,F401  (reads the environment)
import engine  # noqa: E402
import world  # noqa: E402
import x86emu  # noqa: E402
import arm64emu  # noqa: E402
import a64image  # noqa: E402

CX = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
COUNT = {'x86': 0, 'a64': 0, 'x86_blocks': 0, 'a64_blocks': 0}
_xcache = {}


class CountingX86(x86emu.X86Engine):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.uc.hook_add(UC_HOOK_BLOCK, self._count_block)

    def _count_block(self, uc, address, size, _):
        if not self._in_loaded(address):
            return
        key = (address, size)
        n = _xcache.get(key)
        if n is None:
            code = bytes(uc.mem_read(address, size))
            n = sum(1 for _ in CX.disasm_lite(code, address))
            _xcache[key] = n
        COUNT['x86'] += n
        COUNT['x86_blocks'] += 1


class CountingA64(arm64emu.Arm64Engine):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.uc.hook_add(UC_HOOK_BLOCK, self._count_block, begin=a64image.A64_BASE,
                         end=a64image.A64_BASE + 0x01000000 - 1)

    def _count_block(self, uc, address, size, _):
        COUNT['a64'] += size // 4
        COUNT['a64_blocks'] += 1


x86emu.X86Engine = CountingX86
arm64emu.Arm64Engine = CountingA64


def run_target(uc, source, addr, seeds, max_steps, bgs=('zero', 'rand')):
    pair, t = uc.pair(source, addr)
    rows = []
    for s, bg in [(s, bg) for bg in bgs for s in range(1, seeds + 1)]:
        for k in COUNT:
            COUNT[k] = 0
        case = world.Case(seed=s, bg=bg)
        try:
            rx, ra, div = pair.run(case, max_steps=max_steps)
        except Exception as e:                  # noqa: BLE001 - one bad case must not stop the census
            rows.append(dict(seed=s, err='harness: %r' % e))
            continue
        rows.append(dict(seed=s, x86=COUNT['x86'], a64=COUNT['a64'], err=rx.error or ra.error,
                         div=(div or {}).get('kind'), xcalls=len(rx.calls), acalls=len(ra.calls)))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('targets', nargs='*', help='<source>:<0xaddr>')
    ap.add_argument('--targets-file', help='one <source>:<0xaddr> per line (# comments)')
    ap.add_argument('--seeds', type=int, default=8)
    ap.add_argument('--max-steps', type=int, default=400000)
    ap.add_argument('--bg', default='zero,rand', help="background memory of the fuzzed world: zero, rand or both")
    a = ap.parse_args()
    tl = list(a.targets)
    if a.targets_file:
        tl += [ln.split('#')[0].strip() for ln in open(a.targets_file) if ln.split('#')[0].strip()]
    uc = engine.UnitCache(str(config.LINK), str(config.A64WORK))
    print('%-58s %10s %8s %8s %6s %5s %5s %s' % ('function', 'addr', 'x86', 'a64', 'ratio', 'ok', 'agree',
                                                 'calls x86/a64'))
    allx = alla = 0
    for spec in tl:
        source, _, addr = spec.rpartition(':')
        try:
            rows = run_target(uc, source, int(addr, 16), a.seeds, a.max_steps, tuple(a.bg.split(',')))
        except Exception as e:                  # noqa: BLE001
            print('%-58s %10s  setup failed: %r' % (source[-58:], addr, e))
            continue
        good = [r for r in rows if not r.get('err') and r.get('x86') and r.get('a64')]
        agree = sum(1 for r in good if not r.get('div'))
        if not good:
            errs = sorted({str(r.get('err'))[:60] for r in rows})
            print('%-58s %10s  no clean case: %s' % (source[-58:], addr, '; '.join(errs)))
            continue
        mx = statistics.median(r['x86'] for r in good)
        ma = statistics.median(r['a64'] for r in good)
        allx += mx
        alla += ma
        print('%-58s %10s %8.0f %8.0f %6.2f %5d %5d %s/%s' % (
            source[-58:], addr, mx, ma, ma / mx, len(good), agree,
            statistics.median(r['xcalls'] for r in good), statistics.median(r['acalls'] for r in good)))
    if allx:
        print('sum of medians: x86 %d  a64 %d  ratio %.2f' % (allx, alla, alla / allx))


if __name__ == '__main__':
    main()
