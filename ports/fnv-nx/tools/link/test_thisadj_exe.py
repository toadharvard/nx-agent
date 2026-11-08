#!/usr/bin/env python3
"""Unit test: thisadj.from_env takes resolve's own --exe (the full build passes the exe that way, without FNVNX_EXE);
FNVNX_SECONDARY_ENTRIES still wins; no exe gives {}.

    python3 tools/link/test_thisadj_exe.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import thisadj  # noqa: E402

FAILS = []


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


seen = []
thisadj.secondary_entries = lambda p: seen.append(p) or {0x00d089f0: 28}
for k in ('FNVNX_EXE', 'PLAYER_EXE', 'FNVNX_SECONDARY_ENTRIES'):
    os.environ.pop(k, None)
with tempfile.NamedTemporaryFile(suffix='.exe') as exe:
    check(thisadj.from_env(exe.name) == {0x00d089f0: 28} and seen == [os.path.abspath(exe.name)],
          'an explicit exe path (resolve --exe) is used without FNVNX_EXE')
    check(thisadj.from_env() == {}, 'no exe anywhere: {}')
    os.environ['FNVNX_EXE'] = exe.name
    check(thisadj.from_env() == {0x00d089f0: 28}, 'FNVNX_EXE still works')
    os.environ.pop('FNVNX_EXE')
    with tempfile.NamedTemporaryFile('w', suffix='.tsv', delete=False) as t:
        t.write('00401000\t4\n')
    os.environ['FNVNX_SECONDARY_ENTRIES'] = t.name
    check(thisadj.from_env(exe.name) == {0x00401000: 4}, 'the test table wins over the exe')
    os.environ.pop('FNVNX_SECONDARY_ENTRIES')
    os.unlink(t.name)
print('%d failed' % len(FAILS))
sys.exit(1 if FAILS else 0)
