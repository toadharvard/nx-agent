#!/usr/bin/env python3
"""tests/s13/test_owners.py - owners.py groups the shards' hard causes by D24 owner; an unknown cause is kept (`?`)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools', 'difffuzz'))
import owners  # noqa: E402

s = [{'hard': {'by_cause': {'call-flow': 3, 'oracle-fault': 2, 'nan-bits': 1, 'brand-new': 4}}},
     {'hard': {'by_cause': {'call-flow': 1, 'uninit-arm': 5}}}, None]
t, g, total = owners.table(s)
fails = 0
for cond, what in ((total == 16, 'total 16'), (g['F1']['call-flow'] == 4, 'F1 call-flow summed over shards'),
                   (g['S1-rules']['oracle-fault'] == 2 and g['F3']['uninit-arm'] == 5 and g['F4']['nan-bits'] == 1,
                    'owners S1-rules, F3, F4'), (g['?']['brand-new'] == 4, 'an unknown cause stays visible'),
                   ('missing 3' in t, 'a missing shard is named')):
    print(('ok   ' if cond else 'FAIL ') + what)
    fails += not cond
print('%d failures' % fails)
sys.exit(1 if fails else 0)
