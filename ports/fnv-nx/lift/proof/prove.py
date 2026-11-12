#!/usr/bin/env python3
"""Gate for the DSL forms (fnvlift/dsl.py, proof/forms*.py).  Two independent checks:
  prove.py --model MODULE   per encoding: exists a pre-state with DSL post-state != reference post-state;  unsat = proved.  One line per FORM KEY
                            (all its encodings: the real ones of instest/result.json plus enc_table.py), then totals and gate_forms.json.
  prove.py --backends       emitted C++ (g++ -std=c++17 -Wall -Wextra -Werror -fsanitize=undefined) agrees with the z3 term on N inputs (backends.py).
Options: --only SUBSTR  --n 1000  --cert DIR  --timeout SEC  --all   (a deliberately broken copy of forms*.py is proved by running the prove.py of a copy of the tree)
Result per key: proved | FAILED(dsl) | FAILED(model-defect:ids) | model-gap | timeout  (worst over its encodings in that order of severity).
Reference model plug-in: a module of the MODELS table below (proof/model_sail.py) with one function
    model_post(code: bytes, pre: dict) -> dict
  pre  = {'regs': {'eax'..'edi': z3 BitVec(name,32)}, 'flags': {'cf','pf','af','zf','sf','of': z3 BitVec(name,1)},
          'mem': z3 Array(BV32->BV8) named 'mem', 'eip': BitVecVal(0x401000,32)}      (code is ONE instruction)
  ret  = {'regs': {8 names: BV32}, 'flags': {6 names: BV1}, 'mem': post-state Array, 'eip': BV32, optional 'assume': Bool (pre-states the model is defined on)}
  Other pre-state names shared with the model by name: fs_base, gs_base (BV32), rflags_other (BV32; DF = bit 10) which prove.py maps onto the DSL inputs df / flo.
  A model that cannot express the instruction raises an exception whose class is named ModelGap (result model-gap).
Faults: a form with fault(DE) is compared on the pre-states where the DSL does not fault; additionally (1) assume => DSL does not fault, and (2) for forms
  without a memory operand, DSL does not fault => assume (the model faults exactly where the DSL does).  The model's DE paths are tolerated for such forms only.
Unobserved: the model interface returns neither DF nor the other EFLAGS bits; a form writing them is certified on its other fields only and reported model-gap."""
import argparse, hashlib, json, os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import z3
from capstone import Cs, CS_ARCH_X86, CS_MODE_32
from proof import universe, defects, rewrite, forms, enc_table, model_sail, backends
MODELS = {'model_sail': model_sail}      # the reference models --model can name
ADDR = 0x401000
REGS = ('eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi'); FLAGS = ('cf', 'pf', 'af', 'zf', 'sf', 'of')
RFO = z3.BitVec('rflags_other', 32)
SUBS = [(z3.BitVec('df', 1), z3.Extract(10, 10, RFO)), (z3.BitVec('flo', 32), RFO & 0xFFFFF328)]    # DSL inputs df / flo = bits of the model's free rflags_other
SEVERITY = ['FAILED(dsl)', 'timeout', 'FAILED(model-defect)', 'model-gap', 'proved']

MODEL_FAULT = {1: r'\(!!FAULT-FRESH :DE |:(UN)?SIGNED-DIVIDE',            # DSL fault code -> the model's messages of that fault (divide error paths)
               7: r':NON-WRITABLE-SEGMENT$'}                                # a store through CS (memory_accessors.sail store_bytes_to_ea): #GP(0)
class Perm(set):                      # tolerated model faults of a form: exactly the messages of the fault codes that the form declares (a fault the DSL does not declare stays a model-gap)
    declared = True
    def __init__(self, base, codes): super().__init__(base); self.rx = '|'.join(MODEL_FAULT.get(c, r'(?!)') for c in sorted(codes))
    def __ge__(self, other): return all(re.match(self.rx, m) or m in self for m in other)

def items(forms, args):
    """[(key, source, F or None, bytes, width, error)] : real encodings of every in-scope key, then enc_table.py extras"""
    cs = Cs(CS_ARCH_X86, CS_MODE_32); cs.detail = True; keys, real = universe.load(); out = []
    def add(b, src, key=None):
        ins = list(cs.disasm(b, ADDR))
        if len(ins) != 1 or ins[0].size != len(b): out.append((key or '?', src, None, b, 0, 'decode')); return
        k = forms.form_key(ins[0]); w = ins[0].operands[0].size * 8 if ins[0].operands and ins[0].operands[0].size else 32
        if key and k != key: out.append((key, src, None, b, w, 'key mismatch ' + k)); return
        try: out.append((k, src, forms.build(ins[0], ADDR), b, w, None))
        except (KeyError, AssertionError) as e: out.append((k, src, None, b, w, 'no form: %r' % (e,)))
    for k in keys:
        for b in real[k]: add(b, 'real', k)
        if not real[k]: out.append((k, 'real', None, b'', 0, 'no real encoding in result.json'))
    for b in enc_table.encodings(): add(b, 'table')
    if args.only: out = [o for o in out if args.only in o[0] or args.only in o[3].hex()]
    return keys, out

def zser(terms):
    ids, out = {}, []
    def go(e):
        if e.get_id() in ids: return ids[e.get_id()]
        a = [go(c) for c in e.children()]; ids[e.get_id()] = i = len(ids)
        out.append('n%d=%s %s %s' % (i, e.decl().name(), ','.join('n%d' % j for j in a), e.sexpr() if not a else '')); return i
    for k, t in terms: out.append('%s n%d' % (k, go(t)))
    return '\n'.join(out)

def pre_state():
    return {'regs': {r: z3.BitVec(r, 32) for r in REGS}, 'flags': {f: z3.BitVec(f, 1) for f in FLAGS},
            'mem': z3.Array('mem', z3.BitVecSort(32), z3.BitVecSort(8)), 'eip': z3.BitVecVal(ADDR, 32)}
PRE = pre_state()

def subst(t): return z3.substitute(t, *SUBS)
def post_of(F):
    D = F.post(); r = {k: subst(v) for k, v in D.items() if not isinstance(v, dict)}
    for k in ('regs', 'flags', 'defined', 'rdefined'): r[k] = {n: subst(v) for n, v in D[k].items()}
    return r

def check(conds, assume, args):
    """assume AND (c1 OR ... OR cn) is checked as the n queries assume AND ci, one after the other (a complete case split of the disjunction: the disjunction is unsat iff
    every ci is; a sat ci gives a model of the whole).  Each query is much smaller than the disjunction (one register / flag / the memory), which the solver
    decides in seconds where the disjunction takes minutes.  -> (sat, solver with the model) | (unknown, s) at the first query that times out | (unsat, s)"""
    s = z3.Solver(); s.set('timeout', int(args.timeout * 1000)); s.add(assume)
    for c in conds:
        s.push(); s.add(c); r = s.check()
        if r != z3.unsat: return r, s
        s.pop()
    return z3.unsat, s

def prove_one(mod, F, code, key, args, pre=None, model=None, fields=None):
    """-> (result, note, certificate extras).  Shape proofs (shapeprove.py) pass pre = a pre-state with a symbolic eip, model = a thunk returning the model
    post-state of the shape, fields = {name: (offset, size)} of the symbolic encoding bytes (z3 variables fld_<name>, free = universally quantified)"""
    pre = PRE if pre is None else pre
    base = getattr(mod, 'FAULT_OK_ORIG', None) or set(mod.FAULT_OK); mod.FAULT_OK_ORIG = base; mod.FAULT_OK = Perm(base, {k for k, _ in F.faults}) if F.faults else base
    M = model() if model else mod.model_post(code, pre); memo = {}; domain = M.pop('domain', z3.BoolVal(True)); complete = M.pop('complete', True)    # domain: restriction of the pre-state by construction (shape runs: eip range), not a model fault
    M = {k: (rewrite.narrow_div(v, memo) if not isinstance(v, dict) else {n: rewrite.narrow_div(t, memo) for n, t in v.items()}) for k, v in M.items()}
    D = post_of(F); assume = M.get('assume', z3.BoolVal(True))
    mm2 = {}; mm3 = {}; fx = lambda v: rewrite.div_norm(rewrite.pow2_mul(v, mm2), mm3)
    M = {k: (fx(v) if not isinstance(v, dict) else {n: fx(t) for n, t in v.items()}) for k, v in M.items()}; D = {k: (fx(v) if not isinstance(v, dict) else {n: fx(t) for n, t in v.items()}) for k, v in D.items()}
    assume = M.get('assume', z3.BoolVal(True))
    side = M.pop('side', z3.BoolVal(True)); nofault = M.pop('nofault', None)
    assume_d = z3.And(assume, D['fault'] == 0, domain) if F.faults else z3.And(assume, domain)
    probe = z3.BitVec('probe', 32); diffs = []                     # (field, dsl, model, strict difference)
    for r in REGS: diffs.append((r, D['regs'][r], M['regs'][r], z3.And(D['rdefined'][r], D['regs'][r] != M['regs'][r])))
    for f in FLAGS: diffs.append((f, D['flags'][f], M['flags'][f], z3.And(D['defined'][f], D['flags'][f] != M['flags'][f])))
    dm, mm = z3.Select(D['mem'], probe), z3.Select(M['mem'], probe)
    diffs += [('mem', dm, mm, dm != mm), ('eip', D['eip'], M['eip'], D['eip'] != M['eip'])]
    extra = {'model_sha256': hashlib.sha256(zser([(k, m) for k, _, m, _ in diffs]).encode()).hexdigest(), 'fault_checks': ''}
    if not complete:                                             # every model path faults (a declared fault, prove.Perm): nothing to compare, but the DSL must fault in every pre-state of the domain
        r0, _ = check([D['fault'] == 0], domain, args); extra['fault_checks'] = 'model never completes => DSL faults in every state: %s' % r0
        return ('proved', '', extra) if r0 == z3.unsat else ('timeout', '', extra) if r0 != z3.sat else ('FAILED(dsl)', 'the model faults on every path, the DSL does not: ' + extra['fault_checks'], extra)
    r, s = check([d for *_, d in diffs], assume_d, args); notes = []; ids = []
    if r == z3.sat:                                              # does a declared model defect explain every disagreement?
        mo = s.model(); ev = lambda e: mo.eval(e, model_completion=True)
        regs = defects.regions(key, (code, pre), fields)
        def excl(field, cond):
            for _, _, fr, other in regs:
                reg = fr.get(field, other)
                if reg is not None: cond = z3.And(cond, z3.Not(reg))
            return cond
        r2, _ = check([excl(f, d) for f, _, _, d in diffs], assume_d, args)
        if r2 == z3.unsat:
            for i, sdm, fr, other in regs:                       # which declared defects are actually exercised
                c = [z3.And(d, fr.get(f, other)) for f, _, _, d in diffs if fr.get(f, other) is not None]
                if c and check(c, assume_d, args)[0] == z3.sat: ids.append(i)
            return 'FAILED(model-defect:%s)' % ','.join(ids), 'defects: ' + '; '.join(sdm[:90] for i, sdm, _, _ in regs if i in ids), extra
        fld = next(k for k, a, c, d in diffs if z3.is_true(ev(d)))
        inp = ' '.join('%s=%x' % (v, ev(z3.BitVec(v, 32)).as_long()) for v in REGS) + ' ' + ' '.join('%s=%d' % (v, ev(z3.BitVec(v, 1)).as_long()) for v in FLAGS) + ''.join(' fld_%s=%x' % (n, ev(z3.BitVec('fld_' + n, 8 * f[1])).as_long()) for n, f in (fields or {}).items() if f)
        d = [(a, c) for k, a, c, _ in diffs if k == fld][0]
        return 'FAILED(dsl)', 'CE %s: %s dsl=%s model=%s' % (fld, inp, ev(d[0]), ev(d[1])), extra
    if r != z3.unsat: return 'timeout', '', extra
    if F.faults:                                                 # the model faults exactly where the DSL does
        r1, _ = check([D['fault'] != 0], z3.And(assume, domain), args); has_mem = 'm' in key.split('|')[1].replace('imm', '')
        r2 = z3.unsat if has_mem else check([z3.Not(assume if nofault is None else nofault)], z3.And(D['fault'] == 0, domain), args)[0]
        if not has_mem and nofault is not None and z3.Solver().check(side) != z3.sat: r2 = z3.unknown       # exists free values with `side` (closed): one satisfiability check
        extra['fault_checks'] = 'assume=>no DSL fault: %s; no DSL fault=>assume: %s' % (r1, 'not applicable (memory operand: segment-limit faults)' if has_mem else r2)
        if r1 != z3.unsat or r2 != z3.unsat: return 'FAILED(dsl)', 'fault condition differs: ' + extra['fault_checks'], extra
    if F.writes_unobserved(): return 'model-gap', 'post-state of df / other EFLAGS bits is not exposed by the model interface (other fields proved)', extra
    return 'proved', '', extra

def prove(args):
    mod = MODELS[args.model]; os.makedirs(args.cert, exist_ok=True)
    base_keys, its = items(forms, args); per = {}; order = []
    for key, src, F, b, w, err in its:
        e = per.setdefault(key, {'src': src, 'enc': [], 'builds': 0, 'w': w, 'notes': []})
        if key not in order: order.append(key)
        if F is None: e['notes'].append('%s %s: %s' % (b.hex(), src, err)); continue
        e['builds'] += 1; extra = {}
        try: res, note, extra = prove_one(mod, F, b, key, args)
        except Exception as ex:
            if type(ex).__name__ != 'ModelGap': raise
            res, note = 'model-gap', 'GAP ' + str(ex)[:110]
        e['enc'].append((res, b.hex())); note and e['notes'].append('%s: %s' % (b.hex(), note[:230]))
        json.dump(dict(form=key, encoding=b.hex(), result=res, dsl_sha256=F.sha(), z3=z3.get_version_string(), model=args.model, note=note, **extra),
                  open(os.path.join(args.cert, '%s_%s.json' % (re.sub(r'[^\w-]', '_', key), b.hex())), 'w'))
    tot = {k: 0 for k in ('proved', 'FAILED(dsl)', 'FAILED(model-defect)', 'model-gap', 'timeout')}; status = {}; missing = []
    for key in order:
        e = per[key]
        if not e['builds']: missing.append(key); res = 'no-form'
        else:
            rs = [r for r, _ in e['enc']]
            sev = lambda r: SEVERITY.index('FAILED(model-defect)' if r.startswith('FAILED(model-defect') else r)
            res = max(rs, key=lambda r: -sev(r)); ids = sorted(set(i for r in rs if r.startswith('FAILED(model-defect:') for i in r[20:-1].split(',')))
            if res.startswith('FAILED(model-defect'): res = 'FAILED(model-defect:%s)' % ','.join(ids)
            tot[res if res in tot else 'FAILED(model-defect)'] += 1
        status[key] = res
        print('%s %d %s %s n=%d' % (key, e['w'], e['enc'][0][1] if e['enc'] else '-', res, len(e['enc'])))
        for n in e['notes'][:2]: res != 'proved' and print('   ' + n)
    scope = [k for k in order if k in base_keys]; hasf = [k for k in order if per[k]['builds']]
    print('TOTAL keys=%d with_dsl_form=%d proved=%d FAILED(dsl)=%d FAILED(model-defect)=%d model-gap=%d timeout=%d' % (
        len(order), len(hasf), tot['proved'], tot['FAILED(dsl)'], tot['FAILED(model-defect)'], tot['model-gap'], tot['timeout']))
    print('in-scope keys (result.json)=%d  without a DSL form: %s' % (len(scope), [k for k in scope if k in missing]))
    cert = [k for k in order if status[k] == 'proved']
    json.dump({'schema': 'fnvnx-gate/1', 'name': 'l3.forms.int', 'status': 'green' if not (tot['FAILED(dsl)'] or tot['timeout'] or [k for k in scope if k in missing]) else 'red',
               'universe': order, 'certified': cert, 'uncertified': [k for k in order if k not in cert], 'status_by_key': status},
              open(os.path.join(args.cert, 'gate_forms.json'), 'w'), indent=1)
    return tot['FAILED(dsl)'] + tot['timeout']

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--model', choices=sorted(MODELS)); ap.add_argument('--backends', action='store_true'); ap.add_argument('--only')
    ap.add_argument('--n', type=int, default=1000); ap.add_argument('--cert', default='/home/user/l3work/proof')
    ap.add_argument('--timeout', type=float, default=180); ap.add_argument('--all', action='store_true'); ap.add_argument('--work', default='/home/user/l3work/proof/backends')
    a = ap.parse_args()
    if a.backends:
        sys.exit(1 if backends.run(a, forms, items, post_of) else 0)
    if not a.model: sys.exit('prove.py: give --model MODULE (reference model plug-in, see the docstring) or --backends')
    sys.exit(1 if prove(a) else 0)
