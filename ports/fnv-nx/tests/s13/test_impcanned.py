#!/usr/bin/env python3
"""tests/s13/test_impcanned.py - d26-imp: a native call of an import keeps the raw canned word, as x86emu gives it
(it was normalised as an untyped bool: TlsGetValue 0xffffffff vs 0xffffff01); no decomp content."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13imp'))
import arm64emu  # noqa: E402
import fnkeys    # noqa: E402
import libcalls  # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


E = arm64emu.A64Engine if hasattr(arm64emu, 'A64Engine') else None
is_import = [v for k, v in vars(arm64emu).items() if isinstance(v, type) and hasattr(v, '_is_import')][0]._is_import
check(is_import('TlsGetValue', libcalls.import_id('TlsGetValue')), 'an import by name is an import')
check(is_import('__imp__TlsGetValue@4', libcalls.import_id('TlsGetValue')), 'its decorated IAT spelling too')
check(not is_import('_ZN3Foo3barEv', -0x1234) and not is_import(None, libcalls.import_id('TlsGetValue')) and
      not is_import('TlsGetValue', 0x00401000), 'a C++ name, no name, an image address: not an import')
ch = [c for c in fnkeys.CHANGES if c.tag == 'd26-imp']
check(len(ch) == 1 and fnkeys.SCOPED >= 'd26' and
      ch[0].recheck({'divergence': {'kind': 'error', 'x86': 'fault:r:00000003', 'arm': None}}) and
      not ch[0].recheck({'divergence': {'kind': 'error', 'x86': None, 'arm': 'fault:r:00000003'}}),
      'd26-imp rechecks the records where only the oracle faulted; SCOPED d26')
print('%d failures' % len(FAIL))
sys.exit(1 if FAIL else 0)
