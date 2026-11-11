#!/usr/bin/env python3
"""V2 check of S2c-abi af48fa3 (tools/link/xexit.py: native stand-ins of the exe exit thunks a unit hands to atexit,
V2 X2's follow-up).

    FNVNX_EXE=<FalloutNV.exe> python3 tests/v2/test_s2c_xexit.py     (skip without the exe; nothing is copied; imports.tsv comes from FNVNX_TABLES, the private pin)

  X1  the real site: xexit.decode(0x00fdd3b0) is a NiPointer release of the static at 0x012024f8 through MSVC slot 1
      (DeleteThis; Itanium slot 2 after D1/D0), and the `call [slot]` in those bytes is InterlockedDecrement's slot
      (imports.tsv). Note: decode itself never checks which import the slot is
  X2  tools/link/test_bindings.py passes with FNVNX_EXE set, as farms run the link (link_job: job_gamedata). It does
      not: its synthetic unit names 0x00fdd3b0, which then decodes from the player's exe, and the check still wants
      extern-text (af48fa3 added text-callback / __fnvnx_xexit_ without updating test_bindings). Without the exe it
      passes.
"""
import os
import struct
import subprocess
import sys

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
sys.path.insert(0, os.path.join(PORT, 'tools', 'link'))
sys.path.insert(0, os.path.join(PORT, 'loader', 'tools'))
sys.path.insert(0, os.path.join(PORT, 'tools'))


def main():
    exe = os.environ.get('FNVNX_EXE')
    if not exe or not os.path.isfile(exe):
        print('skip: no FNVNX_EXE')
        return 0
    import xexit
    fails = 0
    shape = xexit.decode(exe, 0x00fdd3b0)
    raw = xexit._text(exe, 0x00fdd3b0, 48)
    i = raw.find(b'\xff\x15') if raw else -1
    slot = struct.unpack_from('<I', raw, i + 2)[0] if i >= 0 else None
    api = None
    import tables
    for line in open(tables.path('imports.tsv')):
        f = line.split('\t')
        if slot is not None and f[0] == '0x%08x' % slot:
            api = f[2]
    ok = shape == ('niptr', 0x012024f8, 1) and api == 'InterlockedDecrement'
    print('%s X1 decode(0x00fdd3b0) = %s, call [%s] = %s' % ('ok' if ok else 'FAIL', shape,
                                                             '%08x' % slot if slot else '?', api))
    fails += not ok
    r = subprocess.run([sys.executable, os.path.join(PORT, 'tools', 'link', 'test_bindings.py')], capture_output=True,
                       text=True, env=dict(os.environ, FNVNX_EXE=exe), timeout=1500)
    bad = [l for l in r.stdout.splitlines() if l.startswith('FAIL')]
    ok = r.returncode == 0 and not bad
    print('%s X2 test_bindings with FNVNX_EXE set: %s' % ('ok' if ok else 'FAIL', bad[0][:160] if bad else 'ok'))
    fails += not ok
    print('ok' if not fails else 'FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
