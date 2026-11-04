#!/usr/bin/env python3
"""V2 check of S4 ad728b2: FUN_00401460 (CopyMemoryBlock) = memmove(d, s, n) and FUN_00406d50 (AppendPreview) =
strcat_s(d, n, s), read from the player's exe and units.json (nothing copied).

    FNVNX_EXE=<FalloutNV.exe> FNVNX_UNITS=<build/units.json> python3 tests/v2/test_s4_forwarders.py   (skip without)

  W1  both bodies push their three stack arguments in order, call one target, pop 12 and return its eax (cdecl)
  W2  the targets: 0x00ec44d0 is named memcpy/memmove by the units, and its body branches to a backward copy when
      src < dst < src + n (memmove semantics); 0x00ec6bd2 is named strcat_s
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'v1'))
from test_s1b_pop_boot import PE  # noqa: E402

WANT = {0x00401460: (0x00ec44d0, ('memmove', 'memcpy')), 0x00406d50: (0x00ec6bd2, ('strcat_s',))}


def main():
    exe, units = os.environ.get('FNVNX_EXE'), os.environ.get('FNVNX_UNITS')
    if not (exe and os.path.isfile(exe) and units and os.path.isfile(units)):
        print('skip: no FNVNX_EXE / FNVNX_UNITS')
        return 0
    import capstone
    pe, md = PE(exe), capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    names = {}
    for u in json.load(open(units))['units']:
        for k, v in (u.get('symbols') or {}).items():
            names.setdefault(int(v, 16), set()).add(k)
    fails = 0
    for a, (tgt, want) in sorted(WANT.items()):
        ins = []
        for i in md.disasm(pe.read(a, 64), a):
            ins.append((i.mnemonic, i.op_str))
            if i.mnemonic == 'ret':
                break
        pushes = [op for m, op in ins if m == 'mov' and '[ebp +' in op]
        ok = (pushes == ['eax, dword ptr [ebp + 0x10]', 'ecx, dword ptr [ebp + 0xc]', 'edx, dword ptr [ebp + 8]']
              and ('call', hex(tgt)) in ins and ('add', 'esp, 0xc') in ins and ins[-1] == ('ret', '')
              and not any(m in ('mov', 'xor') and op.startswith('eax') for m, op in ins[ins.index(('call', hex(tgt))):]))
        print('%s W1 %#x forwards (d, s, n) in order to %#x' % ('ok' if ok else 'FAIL', a, tgt))
        fails += not ok
        nm = names.get(tgt, set())
        ok = any(w in n for n in nm for w in want)
        if tgt == 0x00ec44d0:
            body = [(i.mnemonic, i.op_str) for _, i in zip(range(16), md.disasm(pe.read(tgt, 96), tgt))]
            ok = ok and ('cmp', 'edi, esi') in body and ('cmp', 'edi, eax') in body and ('add', 'eax, esi') in body
        print('%s W2 %#x is %s (%d unit names)' % ('ok' if ok else 'FAIL', tgt, '/'.join(want), len(nm)))
        fails += not ok
    print('ok' if not fails else 'FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
