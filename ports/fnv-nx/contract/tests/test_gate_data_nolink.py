#!/usr/bin/env python3
"""gate_data reads a link output (out/aliases.ld) only for a root that has a link: link=None is a root without one (no alias map, no file asked for),
a link directory without its aliases.ld is still an error, and registry.py takes no --link for a root without one."""
import os, subprocess, sys, tempfile

C = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, C)
import gate_data, store

fails = []
VA = 0x700000
facts = {'secs': {'.data': (VA, 0x1000, 0)}, 'cookie': 0, 'fn': {0x401000: {'data': [(0, VA + 8), (0, VA + 0x10)]}}}
obj = {('a.o', 0x401000): ([], {VA + 8, VA + 0x10})}
r = gate_data.run(facts, obj, None)
if r['counts'].get('equal') != 1 or r['rows'] or r['judged'] != 1:
    fails.append('no link: %r' % r)
with tempfile.TemporaryDirectory() as d:
    try:
        gate_data.run(facts, obj, d)
        fails.append('a link dir without out/aliases.ld was accepted')
    except FileNotFoundError:
        pass
    os.makedirs(d + '/out')
    open(d + '/out/aliases.ld', 'w').write('')
    if gate_data.run(facts, obj, d)['counts'].get('equal') != 1:
        fails.append('empty alias map of a real link')
if fails:
    print('\n'.join('FAIL ' + f for f in fails))
    sys.exit(1)
print('gate_data no link: ok')
