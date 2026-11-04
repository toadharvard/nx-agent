"""readset.py - which world addresses each machine reads for one case (triage probe for unexplained divergences).

Run once more with read hooks on the argument buffers and the data window. A field the oracle reads at offset A of
a buffer and the native code at A+d (same d for several fields) is a layout difference; addresses read on one side
only point at the field/global the two programs disagree about. Returns a small dict for the triage record.
"""
import collections

from unicorn import UC_HOOK_MEM_READ

import world

REGIONS = ((world.BUF, world.BUF + world.NBUF * world.BUF_SIZE), (world.WINDOW[0], world.WINDOW[1]))


def _hook(uc, access, address, size, value, acc):
    for i in range(0, size, 4 if size >= 4 else size):
        acc.add(address + i)


def probe(pair, case, max_steps=200000):
    pair.run(case, max_steps)          # make sure both engines exist for this case
    sets = []
    for eng in (pair.xe, pair.ae):
        acc = set()
        hs = [eng.uc.hook_add(UC_HOOK_MEM_READ, _hook, user_data=acc, begin=lo, end=hi - 1) for lo, hi in REGIONS]
        sets.append(acc)
        eng._probe_hooks = hs
    try:
        pair.run(case, max_steps)
    finally:
        for eng in (pair.xe, pair.ae):
            for h in getattr(eng, '_probe_hooks', []):
                eng.uc.hook_del(h)
    rx, ra = sets
    ox, oa = sorted(rx - ra), sorted(ra - rx)
    out = dict(x86_only=['%08x' % a for a in ox[:12]], arm_only=['%08x' % a for a in oa[:12]],
               common=len(rx & ra))
    # consistent shift between one-sided buffer reads of the same buffer -> layout difference
    shifts = collections.Counter()
    for a in ox:
        if a < world.WINDOW[0] or a >= world.WINDOW[1]:
            for b in oa:
                if (a & ~(world.BUF_SIZE - 1)) == (b & ~(world.BUF_SIZE - 1)) and abs(b - a) <= 64:
                    shifts[b - a] += 1
    if shifts:
        d, n = shifts.most_common(1)[0]
        if n >= 2:
            out['shift'] = d
    out['x86_only_window'] = sum(1 for a in ox if world.WINDOW[0] <= a < world.WINDOW[1])
    out['arm_only_window'] = sum(1 for a in oa if world.WINDOW[0] <= a < world.WINDOW[1])
    return out
