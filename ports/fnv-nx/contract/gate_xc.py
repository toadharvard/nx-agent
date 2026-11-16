"""gate_xc - does every C++ static initializer (facts['xc']) have an address-bound native function? (L1 CONTRACT)"""
import collections

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # ports/fnv-nx: the siblings are imported by their qualified name (contract.X)
from contract import store

NAME = 'xc'


def run(facts, obj, link):
    bound = {k[1] for k in obj if store.is_function_key(k)}
    counts, rows = collections.Counter(), []
    for a in facts['xc']:
        if a in bound:
            counts['native'] += 1
            continue
        counts['missing'] += 1
        rows.append((a, 'missing', 'no native body'))
    return {'counts': dict(counts), 'rows': rows, 'fail': ['missing'], 'judged': len(facts['xc'])}
