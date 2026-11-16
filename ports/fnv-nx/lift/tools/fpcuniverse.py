#!/usr/bin/env python3
"""fpcuniverse.py --lift LIFTDIR --gen RTGENDIR --out FILE: the helper universe of the lifted runtime's floating-point helpers, derived (never listed).
Rows {helper, input_bits, callers, covered_by}:
  helpers      = every function defined in rt GEN fnvl_fpc.h, the FNVL_F2I-marked helpers of fnvl_x87.h, and the fnvl_tab_* accessors of fnvl.h (the definitions themselves);
  input_bits   = the widths of the parameter types that carry the operand (float / u32 = 32, double / u64 = 64, the 80-bit fv = 80, template UT = 32 and 64);
  callers      = the mnemonics of the Form registry (proof.forms.H) of proof/forms_ssefp.py whose handler code reaches the DSL node constructor that the emitter (fnvlift/dsl.py) maps to the helper
                 (cvt -> fx_dsl_cvt, fp -> fx_dsl_fp, tab -> fnvl_tab_*), and the emitter functions of fnvlift/f_sse.py / f_x87.py whose code names the helper;
  covered_by   = "rcptab.cert.json" for the fnvl_tab_* accessors (every one of the 2^32 inputs on the reference CPU is certified), otherwise "open": no pinned prover judges the C body against the Form.
Output only; the file is the declared output HELPERS of the root lift/proof/fpchost."""
import argparse, json, os, re, sys, types
ap = argparse.ArgumentParser(); ap.add_argument('--lift', required=True); ap.add_argument('--gen', required=True); ap.add_argument('--out', required=True); a = ap.parse_args()
sys.path.insert(0, a.lift)
from proof import forms, forms_ssefp
from fnvlift import f_sse, f_x87
rd = lambda n: open(os.path.join(a.gen, n)).read()
BITS = {'float': [32], 'double': [64], 'u32': [32], 'u64': [64], 'fv': [80], 'UT': [32, 64], 'int': [32], 'u128': [128]}
DEF = re.compile(r'^(?:template<[^>]*>\s*)?(FNVL_F2I\s+)?static inline\s+([\w ]+?)\s+(\w+)\(([^)]*)\)\s*\{', re.M)
def helpers(text, only_f2i=False, prefix=None):
    for m in DEF.finditer(text):
        f2i, name, params = m.group(1), m.group(3), m.group(4)
        if (only_f2i and not f2i) or (prefix and not name.startswith(prefix)): continue
        bits = sorted({b for p in params.split(',') for w in p.split() if w in BITS for b in BITS[w]}) if params.strip() else []
        yield name, bits
def reach(fn, mod, seen, found):
    stack = [fn]
    while stack:
        f = stack.pop()
        if f in seen: continue
        seen.add(f)
        todo = [f.__code__]
        for cell in (f.__closure__ or ()):
            try: v = cell.cell_contents
            except ValueError: continue
            if isinstance(v, types.FunctionType): stack.append(v)
        while todo:
            co = todo.pop()
            for n in co.co_names:
                found.add(n); g = mod.__dict__.get(n)
                if isinstance(g, types.FunctionType) and g.__module__ == mod.__name__: stack.append(g)
            todo += [k for k in co.co_consts if isinstance(k, types.CodeType)]
    return found
def strings(co):
    out = []; todo = [co]
    while todo:
        c = todo.pop()
        for k in c.co_consts:
            if isinstance(k, str): out.append(k)
            elif isinstance(k, types.CodeType): todo.append(k)
    return out
NODE = {'cvt': 'fx_dsl_cvt', 'fp': 'fx_dsl_fp', 'tab': 'fnvl_tab_'}
calls = {}
for m, fn in sorted(forms.H.items()):
    if fn.__module__ != 'proof.forms_ssefp': continue
    for n in reach(fn, forms_ssefp, set(), set()) & set(NODE): calls.setdefault(NODE[n], set()).add(m)
rows = []
fpc = rd('fnvl_fpc.h')
for name, bits in list(helpers(fpc)) + list(helpers(rd('fnvl_x87.h'), only_f2i=True)) + list(helpers(rd('fnvl.h'), prefix='fnvl_tab_')):
    cs = set(calls.get(name, ())) | {m for k, ms in calls.items() if k.endswith('_') and name.startswith(k) for m in ms}
    for mod in (f_sse, f_x87):
        for fname, f in sorted(vars(mod).items()):
            if isinstance(f, types.FunctionType) and f.__module__ == mod.__name__ and any(name + '(' in s or ((name == 'fv_i') and re.search(r'\bFISTT?\b', s)) for s in strings(f.__code__)): cs.add('%s.%s' % (mod.__name__.split('.')[-1], fname))
    rows.append({'helper': name, 'input_bits': bits, 'callers': sorted(cs), 'covered_by': 'rcptab.cert.json' if name.startswith('fnvl_tab_') else 'open'})
os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
open(a.out, 'w').write(json.dumps({'format': 'fnvnx-fpc-helpers/1', 'helpers': sorted(rows, key=lambda r: r['helper'])}, indent=1, sort_keys=True) + '\n')
