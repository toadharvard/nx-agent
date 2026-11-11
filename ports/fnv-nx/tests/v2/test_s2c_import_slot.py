#!/usr/bin/env python3
"""V2 check of S2c-abi ced8846 / e4fcbdc (tools/difffuzz/causes.py): a call-target divergence where the native shows
an IAT slot and the oracle `imp:<api>` is runtime-equiv exactly when the slot's api (tests/imports_synth.tsv, synthetic) is
that import.

    python3 tests/v2/test_s2c_import_slot.py

  I1  imports.tsv names every api at one slot only: name equality can never equate two different slots
  I2  VirtualAlloc's slot vs imp:VirtualAlloc -> runtime-equiv; VirtualFree's slot vs imp:VirtualAlloc -> stays
  I3  EnterCriticalSection (e4fcbdc's c11 rows) at its slot -> runtime-equiv
  I4  a slot outside imports.tsv vs any import -> stays import-slot-called
"""
import collections
import os
import sys

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
sys.path.insert(0, os.path.join(PORT, 'tools', 'difffuzz'))
import causes  # noqa: E402


SYN = os.path.join(PORT, 'tests', 'imports_synth.tsv')
causes._IAT = {int(l.split('\t')[0], 16): l.split('\t')[2] for l in open(SYN) if l.startswith('0x')}


def refined(x86, arm):
    rec = dict(source='src/t.cpp', address='00401000', symbol='?f@@YAXXZ', case={}, hint=dict(cause='call-flow'),
               divergence=dict(kind='call-target', index=0, x86=x86, arm=arm))
    return causes.refine(rec, 'call-flow')


def main():
    fails = 0

    def check(c, what):
        nonlocal fails
        print('%s %s' % ('ok' if c else 'FAIL', what))
        fails += not c
    slots = collections.defaultdict(set)
    for line in open(SYN):
        f = line.rstrip('\n').split('\t')
        if len(f) >= 3 and f[0].startswith('0x'):
            slots[f[2]].add(f[0])
    dup = {k: v for k, v in slots.items() if len(v) > 1}
    check(not dup, 'I1 no api at two slots (%d apis) %s' % (len(slots), sorted(dup)[:5]))
    va, vf, ec = (sorted(slots[n])[0][2:] for n in ('VirtualAlloc', 'VirtualFree', 'EnterCriticalSection'))
    check(refined('imp:VirtualAlloc', va) == 'runtime-equiv', 'I2 same import at its slot: runtime-equiv')
    check(refined('imp:VirtualAlloc', vf) == 'import-slot-called', 'I2 another import\'s slot: stays')
    check(refined('imp:EnterCriticalSection', ec) == 'runtime-equiv', 'I3 EnterCriticalSection slot')
    check(refined('imp:VirtualAlloc', '00fdfffc') == 'import-slot-called', 'I4 unknown slot: stays')
    print('ok' if not fails else 'FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
