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


def layout_shift(pair, case, max_steps=200000):
    """(F-uncls, difffuzz-20) d when the two machines read the same fields of the argument buffers at offsets that
    differ by one constant d: of the words only the oracle reads and the words only the native reads, at least 3
    pair up under +d and those are at least half of each side. A member placed d bytes elsewhere (a base subobject
    or a member the native lays out differently), not a difference of the function's work. None otherwise."""
    rx, ra = _read_sets(pair, case, max_steps)
    ox, oa = sorted(rx - ra), sorted(ra - rx)
    ox = [a for a in ox if not world.WINDOW[0] <= a < world.WINDOW[1]]
    oa = [a for a in oa if not world.WINDOW[0] <= a < world.WINDOW[1]]
    best = None
    for d in {b - a for a in ox for b in oa if (a & ~(world.BUF_SIZE - 1)) == (b & ~(world.BUF_SIZE - 1)) and
              0 < abs(b - a) <= 64}:
        n = sum(1 for a in ox if a + d in oa)
        if n >= 3 and 2 * n >= len(ox) and 2 * n >= len(oa) and (best is None or n > best[0]):
            best = (n, d)
    return best[1] if best else None


def _read_sets(pair, case, max_steps):
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
    return sets


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
