#!/usr/bin/env python3
"""x87carrier: the value ops of the runtime's carrier boundary (rt/fnvl_x87.h, rt/x87.c) as z3 terms, derived from the C by ctrans.

Input: the preprocessed units (.i) of the lift/rt root, made by the recipe's PP stage with the guest compiler and the root's flags (`FNVNX_PP`, os.pathsep-separated paths, until the
run-step input {"pp": ROOT, "unit": PATH} of L1 mounts them), the header text for the macro names the closure follows, and the emitters.  Output: the term table (`table`) with its sha256.

Which functions: the call closure of what the emitter (fnvlift/f_x87.py, f_x87_ctl.py) emits.  `emitted_identifiers` reads every identifier of every string literal of the emitter
sources (ast), `closure` expands the macros it finds and follows the call graph through the header and x87.c.  The op symbol of a Form is the helper's own name; nothing is listed.
Each function of the closure is `translated` (ctrans executes it into a term) or `refused` with the reason the executor gives; a refused function is outside the value boundary:
inline asm over the host FPSR (fnvl_fp_*), memory macros (M16 ...), the register file indexed by a symbolic TOP (dyn_*), a C++ template.

    python3 -I x87carrier.py        prints the closure, the translated / refused counts and the refusals
"""
import hashlib, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE]
import ctrans

RT = os.path.join(os.path.dirname(HERE), 'rt')
EMITTERS = [os.path.join(os.path.dirname(HERE), 'fnvlift', n) for n in ('f_x87.py', 'f_x87_ctl.py')]
MACRO_FILES = [os.path.join(RT, n) for n in ('fnvl.h', 'x87.h', 'fnvl_x87.h', 'x87.c')]
_C = {}


class NoInput(Exception):
    """the preprocessed units are not available"""


def pp_inputs():
    v = os.environ.get('FNVNX_PP')
    if not v: raise NoInput('FNVNX_PP (the .i of the lift/rt root from the recipe PP stage) is not set')
    ps = [p for p in v.split(os.pathsep) if p]
    for p in ps:
        if not os.path.isfile(p): raise NoInput('%s: not a file' % p)
    return ps


def carrier(ipaths=None, macro_files=MACRO_FILES, emitters=EMITTERS, mutate=None):
    ipaths = ipaths or pp_inputs()
    k = (tuple(ipaths), tuple(macro_files), tuple(emitters), tuple(sorted((mutate or {}).items())))
    if k not in _C:
        mc, src, refused = ctrans.load(ipaths, macro_files, mutate)
        fs, ms = ctrans.closure(src, ctrans.emitted_identifiers(emitters))
        parsed = sorted(f for f in fs if f in mc.funcs)
        ok, bad = ctrans.translate_all(mc, parsed)
        bad = dict(bad)
        for f in fs:
            if f not in mc.funcs: bad[f] = refused.get(f, 'not a function of the C sources')
        _C[k] = dict(machine=mc, source=src, closure=sorted(fs), macros=sorted(ms), translated=ok, refused=bad,
                     kinds={n: v for n, v in mc.enums.items() if n.startswith('FK_')})
    return _C[k]


def table(c):
    """the term table: per translated function, the sha256 of its result terms (z3 sexpr of every output on fresh inputs) and its obligations; and the sha256 of the table"""
    import z3
    rows = {}
    for fn in c['translated']:
        mc = c['machine']; a, cells = mc.inputs(fn); mc.G.pop('c', None); r = mc.apply(fn, a)
        outs = []
        if isinstance(r.ret, ctrans.V): outs.append(r.ret.e)
        elif isinstance(r.ret, dict): outs.extend(v.e for v in r.ret.values())
        for _, cell in cells: outs.extend(x.e for x in cell)
        h = hashlib.sha256()
        for x in outs + list(r.ub) + list(r.dom): h.update(z3.simplify(x).sexpr().encode())
        rows[fn] = h.hexdigest()
    return rows, hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def term(fn, args):
    """the z3 result of the helper `fn` on `args` (ctrans values): ctrans.Result"""
    c = carrier(); assert fn in c['translated'], fn
    return c['machine'].apply(fn, args)


if __name__ == '__main__':
    c = carrier()
    print('closure %d functions, %d macros; translated %d, refused %d' % (len(c['closure']), len(c['macros']), len(c['translated']), len(c['refused'])))
    print('carrier kinds', c['kinds'])
    for k, v in sorted(c['refused'].items()): print('  refused', k, v)
