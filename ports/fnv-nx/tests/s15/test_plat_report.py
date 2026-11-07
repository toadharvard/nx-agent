#!/usr/bin/env python3
"""tools/perf/plat.py on synthetic logs (D25 #3 #4 #6, tools/perf/PLAT.md): the metric (distinct unimplemented entries
+ imports with no export + failed opens by path + decode + input; no-ops not counted), distinct by name not caller,
cumulative flushes, the open classes and the ci scan, no data (exit 3). No guest, no game data.

    python3 -I tests/s15/test_plat_report.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLAT = os.path.join(HERE, '..', '..', 'tools', 'perf', 'plat.py')
fails = []


def ok(name, cond, detail=''):
    print(('ok   ' if cond else 'FAIL ') + name + (': ' + str(detail) if detail and not cond else ''))
    if not cond:
        fails.append(name)


def run(d, *logs):
    paths = []
    for i, t in enumerate(logs):
        p = os.path.join(d, 'r%d.log' % i)
        open(p, 'w').write(t)
        paths.append(p)
    out = os.path.join(d, 'p.md')
    r = subprocess.run([sys.executable, '-I', PLAT] + paths + ['--out', out], capture_output=True, text=True)
    j = json.load(open(out[:-3] + '.json')) if os.path.isfile(out[:-3] + '.json') else None
    return r, j, open(out).read() if os.path.isfile(out) else ''


LOG = """\
fnvnx boot: imports: 210 slots, 208 resolved, 2 unresolved
fnvnx boot: import d3dx9_38.dll!D3DXCreateFontA: no export
fnvnx boot: import WSOCK32.dll!#115: no export
[fnvnx] plat: unimpl BinkOpen caller 0x20001000
plat: unimpl Graph::EnumFilters caller 0x20002000
plat: unimpl Graph::EnumFilters caller 0x20002100
plat: noop SteamUser caller 0x20003000
plat: open CreateFileA caller 0x20004000 bsa err 2 ci no path 11111111
plat: open fopen caller 0x20004100 save err 2 ci yes path 22222222
plat: open CreateFileA caller 0x20004200 bsa err 2 ci no path 11111111
plat: decode ov_open_callbacks caller 0x20005000 ov -132
plat: input fnvnx_hid_usage_to_dik caller 0x00000000 hid usage 112
plat: count unimpl BinkOpen caller 0x20001000 n 2
plat: count unimpl Graph::EnumFilters caller 0x20002000 n 1
plat: summary unimpl 3 4 noop 1 1 open 3 3 decode 1 1 input 1 1 dropped 0
plat: count unimpl BinkOpen caller 0x20001000 n 7
plat: count unimpl Graph::EnumFilters caller 0x20002000 n 1
plat: count unimpl Graph::EnumFilters caller 0x20002100 n 1
plat: summary unimpl 3 9 noop 1 1 open 3 3 decode 1 1 input 1 4 dropped 0
"""


def main():
    with tempfile.TemporaryDirectory() as d:
        r, j, md = run(d, LOG)
        ok('runs', r.returncode == 0 and j, r.stderr[-300:])
        if j:
            # unimpl: BinkOpen + Graph::EnumFilters (2 callers, one entry) + 2 imports = 4; opens: 2 paths; 1 + 1
            ok('metric 8', j['metric'] == 8, j['parts'])
            ok('distinct by name, not caller', j['unimpl_entries'] == 2)
            ok('imports with no export', len(j['imports_no_export']) == 2 and j['imports'] == [210, 208, 2])
            ok('opens by path hash and class', j['parts']['open'] == 2 and j['open_by_class'] == {'bsa': 1, 'save': 1})
            ok('ci scan counted', j['ci_yes'] == 1)
            ok('no-op not counted', j['noop_entries'] == 1)
            ok('the last flush holds (BinkOpen 7 calls)', '| BinkOpen | 7 | 1 |' in md, md)
        r, j, md = run(d, 'nothing here\n')
        ok('no data: exit 3', r.returncode == 3)
    print('test_plat_report: %d failed' % len(fails))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
