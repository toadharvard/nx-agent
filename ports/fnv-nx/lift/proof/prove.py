#!/usr/bin/env python3
"""The obligations of one shape against the reference model, as a library: shapeprove.py is the one prover (one per shape key, over the pinned IR) and calls prove_one().
Per encoding: exists a pre-state with DSL post-state != reference post-state; unsat = proved (the obligations: field equalities, DEF0 for the undefined flags, fault conditions).
(The key-by-key table mode, `--backends` and their encoding tables were deleted: by-construction row rule 3, one path.)
Result: proved | FAILED(dsl) | FAILED(model-defect:ids) | model-gap | timeout.
Reference model plug-in: a module of the MODELS table below (proof/model_sail.py) with one function
    model_post(code: bytes, pre: dict) -> dict
  pre  = {'regs': {'eax'..'edi': z3 BitVec(name,32)}, 'flags': {'cf','pf','af','zf','sf','of': z3 BitVec(name,1)},
          'mem': z3 Array(BV32->BV8) named 'mem', 'eip': BitVecVal(0x401000,32)}      (code is ONE instruction)
  ret  = {'regs': {8 names: BV32}, 'flags': {6 names: BV1}, 'mem': post-state Array, 'eip': BV32, optional `assume`: Bool (pre-states the model is defined on)}
  Other pre-state names shared with the model by name: fs_base, gs_base (BV32), rflags_other (BV32; DF = bit 10) which prove.py maps onto the DSL inputs df / flo.
  A model that cannot express the instruction raises an exception whose class is named ModelGap (result model-gap).
Faults: a form with fault(DE) is compared on the pre-states where the DSL does not fault; additionally (1) assume => DSL does not fault, and (2) for forms
  without a memory operand, DSL does not fault => assume (the model faults exactly where the DSL does).  The model's DE paths are tolerated for such forms only.
Unobserved: the model interface returns neither DF nor the other EFLAGS bits; a form writing them is certified on its other fields only and reported model-gap."""
import hashlib, json, os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import z3
from proof import defects, rewrite, forms, model_sail
MODELS = {'model_sail': model_sail}      # the reference models --model can name
ADDR = 0x401000
REGS = ('eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi'); FLAGS = ('cf', 'pf', 'af', 'zf', 'sf', 'of')
RFO = z3.BitVec('rflags_other', 32)
SUBS = [(z3.BitVec('df', 1), z3.Extract(10, 10, RFO)), (z3.BitVec('flo', 32), RFO & 0xFFFFF328)]    # DSL inputs df / flo = bits of the model's free rflags_other
OBLIGATIONS = ('def0',)      # what a record of this prover certifies beyond the field equalities; certs_build refuses a record without it (stale-obligations)
SEVERITY = ['FAILED(dsl)', 'FAILED(def0)', 'timeout', 'FAILED(model-defect)', 'model-gap', 'proved']

MODEL_FAULT_BY_NAME = {'DE': r'\(!!FAULT-FRESH :DE |:(UN)?SIGNED-DIVIDE',            # DSL fault code -> the model's messages of that fault (divide error paths)
                       'AV': r':NON-WRITABLE-SEGMENT$'}                     # a store through CS (memory_accessors.sail store_bytes_to_ea): #GP(0)
from fnvlift import dsl as _dsl
MODEL_FAULT = {k: MODEL_FAULT_BY_NAME[n] for k, n in _dsl.FAULTS.items() if n in MODEL_FAULT_BY_NAME}      # the kind numbers are the DSL's (dsl.FAULTS), the model's messages are keyed by kind name
class Perm(set):                      # tolerated model faults of a form: exactly the messages of the fault codes that the form declares (a fault the DSL does not declare stays a model-gap)
    declared = True
    def __init__(self, base, codes): super().__init__(base); self.rx = '|'.join(MODEL_FAULT.get(c, r'(?!)') for c in sorted(codes))
    def __ge__(self, other): return all(re.match(self.rx, m) or m in self for m in other)

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

def _nodes(roots):
    seen, st = set(), list(roots)
    while st:
        n = st.pop()
        if id(n) in seen: continue
        seen.add(id(n)); yield n; st.extend(n.a)

FREE = re.compile(r'^[pf]\d+_')     # the fresh constants of a model path (model_sail.Env.fresh: tag_name): isla's undefined / nondeterministic values; every other constant is pre-state or a shape field
def _free(terms):
    """{name: z3 constant} of the model's fresh (undefined / nondeterministic) constants in the terms"""
    out, seen, st = {}, set(), list(terms)
    while st:
        e = st.pop()
        if e.get_id() in seen: continue
        seen.add(e.get_id())
        if z3.is_const(e) and e.decl().kind() == z3.Z3_OP_UNINTERPRETED and FREE.match(e.decl().name()): out[e.decl().name()] = e
        st.extend(e.children())
    return out

def _zero(c): return z3.BoolVal(False) if c.sort().kind() == z3.Z3_BOOL_SORT else z3.BitVecVal(0, c.sort().size())

def def0(M, D, flags, assume_d, side, args, extra, pre=None):
    """DEF0 + DEFPRIOR (owner / orchestrator / L3a, 2026-11-12).  The lifter writes the constant 0 to every flag the Form declares architecturally undefined (DEF0) and leaves a non-flag register the Form
    declares undefined at its pre-state value (DEFPRIOR).  Certified as a refinement of the pinned model: the model's undefined values (the fresh constants of its paths) are free, and every one gets exactly
    ONE witness by the outputs it feeds:
      * feeds flag outputs only           -> 0 (DEF0);
      * feeds exactly one output, a 32-bit-or-narrower register r and no flag -> the low bits of r's pre-state value (DEFPRIOR);
      * anything else (feeds several outputs, mem, eip, an address; a boolean or wider constant) -> no witness: its own CLASS, reported (shape, outputs, free names), never chosen for.
    Obligations: for each flag in the Form's undefined set whose free values all have a witness, model[witness] == 0 (forall query); for each register the Form declares undefined whose free values all have
    a witness, model[witness] == the lifted value (forall).  A flag / register the model DEFINES and that disagrees is a mismatch (sat).  The witness must satisfy the model's side constraints.
    extra['def0'] = {'undef_flags', 'witness': 0, 'free', 'nonflag_free': [outputs whose term mentions a free value and are NOT proved under DEFPRIOR], 'defprior': [registers proved under DEFPRIOR],
                     'classes': [{'class': 'multi'|'mem'|'eip'|'sort', 'outputs': [...], 'free': [...]}]}"""
    pre = PRE if pre is None else pre
    und = [f for f in flags if z3.is_false(z3.simplify(D['defined'][f]))]
    outs = {('r', r): M['regs'][r] for r in M['regs']}
    outs.update({('f', f): M['flags'][f] for f in flags}); outs[('m', 'mem')] = M['mem']; outs[('e', 'eip')] = M['eip']
    users, free = {}, _free(list(outs.values()) + [side])
    for o, t in outs.items():
        for n in _free([t]): users.setdefault(n, set()).add(o)
    wit, classes, prior = {}, [], {}
    for n, c in sorted(free.items()):
        u = users.get(n, set())
        if all(k == 'f' for k, _ in u): wit[n] = _zero(c)                                           # flags only (or the side constraint only): DEF0
        elif len(u) == 1 and next(iter(u))[0] == 'r' and c.sort().kind() == z3.Z3_BV_SORT and c.sort().size() <= 32:
            r = next(iter(u))[1]; w = c.sort().size()
            wit[n] = z3.Extract(w - 1, 0, pre['regs'][r]) if w < 32 else pre['regs'][r]; prior.setdefault(r, []).append(n)   # DEFPRIOR
        else:
            kind = 'multi' if len(u) > 1 else 'mem' if next(iter(u))[0] == 'm' else 'eip' if next(iter(u))[0] == 'e' else 'sort'
            classes.append({'class': kind, 'outputs': sorted(r for _, r in u), 'free': [n]})
    bad = users_of(users, [n for cl in classes for n in cl['free']])                                  # outputs that mention a free value without a witness
    nonflag_all = {r for k, r in users_all(users) if k != 'f'}
    extra['def0'] = {'undef_flags': und, 'witness': 0, 'free': len(free), 'nonflag_free': sorted(nonflag_all), 'defprior': [], 'classes': classes}
    if not und and not prior: return 'proved', ''
    ws = list(wit.items()); sub = [(free[n], v) for n, v in ws]
    sw = z3.substitute(side, *sub) if sub else side
    r, _ = check([z3.Not(sw)], z3.BoolVal(True), args)
    if r != z3.unsat: return 'FAILED(def0)', 'the witness (0 for flags, the pre-state value for a register) does not satisfy the side constraints of the model on its undefined values: %s' % r
    aw = z3.substitute(assume_d, *sub) if sub else assume_d
    for f in und:
        if ('f', f) in bad: continue                                                                # its free value has no witness: a class, reported
        mw = z3.substitute(M['flags'][f], *sub) if sub else M['flags'][f]
        r, sv = check([mw != 0], aw, args)
        if r == z3.unsat: continue
        ce = ''
        if r == z3.sat:
            mo = sv.model(); ce = ' CE: ' + ' '.join('%s=%s' % (k, mo.eval(v, model_completion=True)) for k, v in [(q, pre['regs'][q]) for q in ('eax', 'ecx')] + [(q, pre['flags'][q]) for q in FLAGS])
        return ('FAILED(def0)' if r == z3.sat else 'timeout'), 'DEF0 flag %s: model[free := 0] %s the lifted 0 (%s)%s' % (f, 'differs from' if r == z3.sat else 'unknown against', r, ce)
    proved = []
    for rg in sorted(prior):
        if ('r', rg) in bad: continue
        mw = z3.substitute(M['regs'][rg], *sub)
        r, _ = check([mw != D['regs'][rg]], aw, args)
        if r == z3.unsat: proved.append(rg); continue
        return ('FAILED(def0)' if r == z3.sat else 'timeout'), 'DEFPRIOR register %s: model[free := pre-state %s] %s the lifted value (%s)' % (rg, rg, 'differs from' if r == z3.sat else 'unknown against', r)
    extra['def0']['defprior'] = proved
    extra['def0']['nonflag_free'] = sorted(nonflag_all - set(proved))
    return 'proved', ''

def users_of(users, names):
    return {o for n in names for o in users.get(n, ())}

def users_all(users):
    return {o for u in users.values() for o in u}

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'declared.json')) as _fh:
    DECLARED = json.load(_fh)                                   # the contract of the declared outputs: function -> platform source, SDM / OWNER basis, the outputs it may define
UFROW = {u: row for row in DECLARED['rows'] for u in row['ufs']}
RT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # the lift tree: sources are relative to it

def declared_outputs(F):
    """{register: platform function name} of the registers whose written value is EXACTLY a platform-function result (a `uf` node, or an extract / zext of one, unconditional) of a function in
    declared.json, written to an output that row allows.  A uf anywhere else in the outputs (another function, another output, mixed with other state) is not declared: the comparison with the model
    stays strict, and the form is reported by declared_findings."""
    out = {}
    for name, v, when in F.regs:
        n = v
        while n.op in ('extract', 'zext'): n = n.a[0]
        if n.op == 'uf' and when is None and n.x in UFROW and name in UFROW[n.x]['outputs']: out[name] = n.x
    return out

def declared_findings(F):
    """ufs of the Form's outputs that are not declared by contract (a platform function outside declared.json, or in an output it may not define)"""
    d = declared_outputs(F); bad = []
    for name, v, when in F.regs:
        if any(n.op == 'uf' for n in _nodes([v])) and name not in d: bad.append((name, sorted({n.x for n in _nodes([v]) if n.op == 'uf'})))
    return bad

def platform_sha(declared, root=RT):
    """sha256 over the source files of the declared.json rows the functions belong to (relative path + content): part of the certificate; formgate recomputes it from the tree it runs in"""
    h = hashlib.sha256()
    for src in sorted({UFROW[f]['source'] for f in declared.values()}):
        h.update(src.encode()); h.update(open(os.path.join(root, src), 'rb').read())
    return h.hexdigest()

def prove_one(mod, F, code, key, args, pre=None, model=None, fields=None):
    """-> (result, note, certificate extras).  Shape proofs (shapeprove.py) pass pre = a pre-state with a symbolic eip, model = a thunk returning the model
    post-state of the shape, fields = {name: (offset, size)} of the symbolic encoding bytes (z3 variables fld_<name>, free = universally quantified)"""
    pre = PRE if pre is None else pre
    model_sail.SYMSEL = any(n.op == 'var' and n.x.startswith('sel_') for n in _nodes(F.roots()))
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
    declared = declared_outputs(F)                                # outputs the Form takes from a platform function (uf): the model is not their reference
    extra_declared = {}
    for r in REGS:
        if r in declared: extra_declared[r] = declared[r]; continue
        diffs.append((r, D['regs'][r], M['regs'][r], z3.And(D['rdefined'][r], D['regs'][r] != M['regs'][r])))
    for f in FLAGS: diffs.append((f, D['flags'][f], M['flags'][f], z3.And(D['defined'][f], D['flags'][f] != M['flags'][f])))
    dm, mm = z3.Select(D['mem'], probe), z3.Select(M['mem'], probe)
    diffs += [('mem', dm, mm, dm != mm), ('eip', D['eip'], M['eip'], D['eip'] != M['eip'])]
    extra = {'model_sha256': hashlib.sha256(zser([(k, m) for k, _, m, _ in diffs]).encode()).hexdigest(), 'fault_checks': ''}
    if extra_declared: extra['declared'] = extra_declared; extra['platform_sha256'] = platform_sha(extra_declared)
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
    d0, dnote = def0(M, D, FLAGS, assume_d, side, args, extra, pre)      # DEF0: every architecturally undefined flag output is 0 in the lifted body; prove model[free := 0] == lifted
    if d0 != 'proved': return d0, dnote, extra
    if F.writes_unobserved(): return 'model-gap', 'post-state of df / other EFLAGS bits is not exposed by the model interface (other fields proved)', extra
    return ('agree-except-declared' if extra_declared else 'proved'), '', extra
