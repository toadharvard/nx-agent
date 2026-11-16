#!/usr/bin/env python3
"""The jump-table resolver's worklist terminates: St equality is semantic (the alias PARTITION, not its fresh class labels), so the join of a converged state with a state it already covers is the same state.
Defect it pins: joinst / copy mint a new alias class id per call, St.__eq__ compared the ids, and a loop whose body copies a register (`mov ecx, eax`) changed its loop-head state on every pass, so
resolve never returned (d3dx9_31 site 0x15682f3: one resolve, hours, a 3-entry worklist cycling through one 48-byte range).   test_jtab_fixpoint.py  (exit 0 = pass)"""
import os, random, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tools import jtab as J
FAIL = []
def check(n, ok):
    if not ok: FAIL.append(n); print('FAIL', n)
LOCS = ['eax', 'ecx', 'edx', 'ebx', ('c', 'ebp', -4), ('c', 'ebp', -8)]
def rnd(rng):
    """a state with random intervals and a random alias partition under FRESH class labels"""
    st = J.St({k: (rng.randrange(0, 50), rng.randrange(0, 20)) for k in LOCS if rng.random() < .6}, None)
    for _ in range(rng.randrange(0, 4)):
        c = object(); ks = rng.sample(LOCS, rng.randrange(1, 4))
        for k in ks: st.al[k] = id(c)
    return st
rng = random.Random(7)
for t in range(300):
    a, b = rnd(rng), rnd(rng)
    m = J.joinst(a, b)
    b2 = J.St(dict(b.r), b.f, {k: v + 1 for k, v in b.al.items()}, {k: set(v) for k, v in b.nz.items()}, b.pk, b.espd)      # b again under other labels
    check('join idempotent under relabelling %d' % t, J.joinst(m, b2) == m and J.joinst(m, b) == m)
    check('join with itself %d' % t, J.joinst(m, m) == m)
    m2 = m.cp(); m2.al = {k: v * 3 + 1 for k, v in m.al.items()}
    check('equality is the partition %d' % t, m2 == m and J.parts(m2.al) == J.parts(m.al))
a = J.St({}, None); a.al = {'eax': 1, 'ecx': 1}; b = J.St({}, None); b.al = {'eax': 1, 'ecx': 2}
check('different partitions differ', a != b)
head = J.joinst(J.St({'eax': (0, 3)}, None), J.St({'eax': (0, 9)}, None))        # a loop head fed by `mov ecx, eax` from fresh states
for i in range(50):
    s = J.St({'eax': (0, 9)}, None); J.copy(s, 'ecx', 'eax')
    nxt = J.joinst(head, s)
    if i > 1: check('loop head stable at pass %d' % i, nxt == head)
    head = nxt
print('fails', len(FAIL)); sys.exit(1 if FAIL else 0)
