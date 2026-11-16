#!/usr/bin/env python3
"""x87carrier: the value ops of the runtime's carrier boundary (rt/fnvl_x87.h, rt/x87.c) as z3 terms, derived from the C by ctrans.

Which functions: the call closure of what the emitter (fnvlift/f_x87.py, f_x87_ctl.py) emits.  `emitted_identifiers` reads every identifier of every string literal of the emitter
sources (ast), `closure` expands the macros it finds and follows the call graph through the header and x87.c.  The op symbol of a Form is the helper's own name; nothing is listed.
Each function of the closure is `translated` (ctrans executes it into a term) or `refused` with the reason the executor gives; a refused function is outside the value boundary:
inline asm over the host FPSR (fnvl_fp_*), memory macros (M16 ...), the register file indexed by a symbolic TOP (dyn_*), a C++ template.

    python3 -I x87carrier.py        prints the closure, the translated / refused counts and the refusals
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE]
import ctrans

RT = os.path.join(os.path.dirname(HERE), 'rt')
EMITTERS = [os.path.join(os.path.dirname(HERE), 'fnvlift', n) for n in ('f_x87.py', 'f_x87_ctl.py')]
_C = {}


def carrier(rt=RT, emitters=EMITTERS):
    k = (rt, tuple(emitters))
    if k not in _C:
        mc, src, refused = ctrans.load(rt)
        fs, ms = ctrans.closure(src, ctrans.emitted_identifiers(emitters))
        parsed = sorted(f for f in fs if f in mc.funcs)
        ok, bad = ctrans.translate_all(mc, parsed)
        bad = dict(bad)
        for f in fs:
            if f not in mc.funcs: bad[f] = refused.get(f, 'not a function of the C sources')
        _C[k] = dict(machine=mc, source=src, closure=sorted(fs), macros=sorted(ms), translated=ok, refused=bad,
                     kinds={n: v for n, v in mc.enums.items() if n.startswith('FK_')})
    return _C[k]


def term(fn, args):
    """the z3 result of the helper `fn` on `args` (ctrans values): ctrans.Result"""
    c = carrier(); assert fn in c['translated'], fn
    return c['machine'].apply(fn, args)


if __name__ == '__main__':
    c = carrier()
    print('closure %d functions, %d macros; translated %d, refused %d' % (len(c['closure']), len(c['macros']), len(c['translated']), len(c['refused'])))
    print('carrier kinds', c['kinds'])
    for k, v in sorted(c['refused'].items()): print('  refused', k, v)
