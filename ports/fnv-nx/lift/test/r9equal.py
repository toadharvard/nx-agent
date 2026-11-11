#!/usr/bin/env python3
"""R9 full-equality proof over the whole entry universe (replaces a 1-in-40 sample). The class is bound by C++ scope (rt/fnvl_mem.h), so the lifted C text of an
instruction only differs from the pre-R9 text where fnvlift/memclass.apply rewrites it. Two lifts of every entry (FNVNX_UNIVERSE, default /home/user/l3facts/universe.tsv):
  PRE   fnvlift.core._r9.apply := identity (the handlers' own text: what the lifter emitted before R9)
  R9    the real apply, no plain set (every access ORDERED: the default)
compared as whole texts. For every instruction apply is also called with core.MEMCLASS = AllPlain (every access plain) and must return exactly
['{ enum { FNVL_R9 = 0 };'] + handler text + ['}'] (the plain wrapper, i.e. PRE text modulo that fixed pair of lines, instruction by instruction).
The sites where the R9 text differs from PRE (apply output != handler text) are recorded with their decoded mnemonic and LOCK prefix; an entry may differ only at
xchg-with-memory / LOCK-RMW sites. An entry with such a site whose alignment is not proven is a lift error (core.Unsupported 'alignment not proven') in R9 and lifts in PRE.
    r9equal.py [--jobs N]        prints: entries compared, equal, differing only at xchg/lock sites, lift error at an unproven xchg/lock site, other (must be 0)
Exit 1 iff other > 0."""
import os, sys, collections, multiprocessing as mp
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(HERE)
EXE = os.environ.get('FNVNX_EXE', '/home/user/game/FalloutNV.exe')
UNIVERSE = os.environ.get('FNVNX_UNIVERSE', '/home/user/l3facts/universe.tsv')
PLAINW = '{ enum { FNVL_R9 = 0 };'


def work(arg):
    k, n = arg
    sys.path.insert(0, LIFT)
    import fnvlift as L
    from fnvlift import core, memclass as MC
    from capstone import x86 as X
    real = MC.apply; img = L.Image(EXE); sites = []; bad_wrap = []
    def identity(cx, ins, n0, mc=None):
        hl = cx.L[n0:]
        try: out = real(cx, ins, n0, None)
        except core.Unsupported as e: sites.append((cx.a, cx.m, X.X86_PREFIX_LOCK in ins.prefix, 'unproven')); return hl      # alignment not proven: R9 lifts nothing for this entry
        if hl and real(cx, ins, n0, MC.AllPlain()) != [PLAINW] + hl + ['}']: bad_wrap.append(cx.a)
        if out != hl: sites.append((cx.a, cx.m, X.X86_PREFIX_LOCK in ins.prefix, 'atomic'))
        return hl
    rows = [l.split('\t')[0] for l in open(UNIVERSE) if l[0] != '#']
    res = collections.Counter(); other = []
    for a in rows[k::n]:
        a = int(a, 16); sites.clear(); del bad_wrap[:]
        core._r9.apply = identity
        try: pre = L.lift_entry(img, a)[0]
        except L.Unsupported as e: pre = 'unsupported: ' + str(e)
        finally: core._r9.apply = real
        try: r9 = L.lift_entry(img, a)[0]
        except L.Unsupported as e: r9 = 'unsupported: ' + str(e)
        ok_sites = all(m in ('xchg', 'add', 'adc', 'sub', 'sbb', 'and', 'or', 'xor', 'inc', 'dec', 'neg', 'not', 'xadd', 'cmpxchg', 'cmpxchg8b', 'bts', 'btr', 'btc') and (m == 'xchg' or lk) for _, m, lk, _k in sites)
        res['entries'] += 1; res['wrapper_instructions_bad'] += len(bad_wrap)
        if bad_wrap: other.append((a, 'plain wrapper'))
        elif pre == r9:
            res['equal'] += 1
            if sites: other.append((a, 'sites recorded but texts equal'))
        elif r9.startswith('unsupported:') and 'alignment not proven' in r9 and not pre.startswith('unsupported') and any(k == 'unproven' for *_, k in sites) and ok_sites:
            res['lift_error_unproven_site'] += 1
        elif sites and ok_sites and not r9.startswith('unsupported') and not pre.startswith('unsupported'):
            res['differ_only_at_xchg_lock'] += 1
        else: other.append((a, 'texts differ without an xchg/lock site' if not r9.startswith('unsupported') else r9[:80]))
    res['other'] = len(other)
    return res, other[:20]


if __name__ == '__main__':
    n = int(sys.argv[sys.argv.index('--jobs') + 1]) if '--jobs' in sys.argv else 2
    with mp.Pool(n) as p: rs = p.map(work, [(k, n) for k in range(n)])
    tot = sum((r for r, _ in rs), collections.Counter()); oth = [o for _, os_ in rs for o in os_]
    print('entries compared %d, equal %d, differing only at xchg/lock sites %d, lift error at an unproven xchg/lock site %d, plain-wrapper violations %d, other %d' % (
        tot['entries'], tot['equal'], tot['differ_only_at_xchg_lock'], tot['lift_error_unproven_site'], tot['wrapper_instructions_bad'], tot['other']))
    for a, why in oth[:20]: print('  other: %08x %s' % (a, why))
    sys.exit(1 if tot['other'] else 0)
