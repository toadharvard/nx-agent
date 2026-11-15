#!/usr/bin/env python3
"""Refinement of the SIMD Forms (forms_simd.py, via symform.symbuild) by the spec (simdspec.py), per encoding SHAPE.   simdcheck.py SHAPES.tsv [--only SUBSTR] [--out FILE]
For a shape key the symbolic Form's z3 post-state (fnvlift/dsl.py Form.post(): pre-state variables xmm0-7, fr0-7, ftop, ftag, mxcsr, the 8 general registers, the memory
array, the displacement / immediate field variables free) is compared with simdspec.run on the SAME pre-state:
  * the memory operand value the spec reads is the Form's own load: m = the bytes of the memory array at the effective address (the address expression is read from
    the same adapter operand as the Form, symform.symins + forms.ea: the address is the integer path's certified fact, not restated);
  * unsat of  NOT(fault_form == (fault_spec ? AV : 0)  AND  [no fault ->  every xmm, fr, ftop, ftag, mxcsr, the 8 general registers, the memory array equal ])
    is the proof; sat gives a counter-model (a defect of the Form or of the spec, reported with the shape);
  * results: proved | FAILED | no-row (the spec has no row for the encoding) | no-form (no DSL builder).
Environment: the spec's Res.env (CR0.EM = CR0.TS = 0, CR4.OSFXSR = 1) is assumed; it is L5's x86sys row (journal inbox), named in CERTS.md 'SIMD environment'."""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, '..')); sys.path.insert(0, HERE)
import z3
from fnvlift import dsl
from proof import forms, shape, symform, simdspec as S
REG = ['eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi']


def _obligation(rep, F, sins_ops, imm, timeout):
    """refinement query of one Form F (symbolic or concrete) against the spec on the encoding rep; sins_ops: the instruction whose operands give the memory operand"""
    e = S.Enc(rep)
    P = F.post()
    M0 = dsl.mem_array()
    d = {'xmm%d' % i: z3.BitVec('xmm%d' % i, 128) for i in range(8)}; d.update({'fr%d' % i: z3.BitVec('fr%d' % i, 80) for i in range(8)})
    d['ftop'] = z3.BitVec('ftop', 3); d['ftag'] = z3.BitVec('ftag', 16); d['mxcsr'] = z3.BitVec('mxcsr', 32)
    gpr = {i: z3.BitVec(r, 32) for i, r in enumerate(REG)}
    a = m = None
    if e.mem:
        j = next(i for i, o in enumerate(sins_ops.operands) if o.type == 3)
        op = forms.Op(sins_ops, sins_ops.operands[j]); a = dsl.to_smt(forms.ea(op))
        w = S.mem_width(rep)
        m = z3.Concat(*[z3.Select(M0, a + i) for i in reversed(range(w // 8))])
    try: r = S.run(rep, d, m, a, gpr, imm)
    except KeyError: return 'no-row', None
    nf = P['fault'] == 0
    spec_fault = z3.If(r.fault, z3.BitVecVal(S.AV, 8), z3.BitVecVal(0, 8))
    goals = [P['fault'] == spec_fault]
    eq = []
    for i in range(8):
        eq.append(P['xmm'][i] == r.d['xmm%d' % i]); eq.append(P['fr'][i] == r.d['fr%d' % i])
    eq += [P['ftop'] == r.d['ftop'], P['ftag'] == r.d['ftag'], P['mxcsr'] == r.d['mxcsr']]
    for i, rn in enumerate(REG):
        v = gpr[i]
        if r.gpr and r.gpr[0] == i: v = r.gpr[1]
        eq.append(P['regs'][rn] == v)
    mem = M0
    if r.store:
        sa, sv, sb = r.store
        for k in range(sb // 8): mem = z3.Store(mem, sa + k, z3.Extract(8 * k + 7, 8 * k, sv))
    eq.append(P['mem'] == mem)
    s = z3.Solver(); s.set('timeout', timeout)
    s.add(r.env, z3.Not(z3.And(*goals, z3.Implies(nf, z3.And(*eq)))))
    res = s.check()
    if res == z3.unsat: return 'proved', None
    if res == z3.sat: return 'FAILED', str(s.model())[:400]
    return 'timeout', None


def check(key, timeout=60000):
    """-> (result, info).  proved: the symbolic Form of the shape refines the spec.  proved-by-cases: the builder branches on the immediate (symform.SymbolicBranch or a
    Python index by it: the Form of the shape cannot be made symbolic), so every one of the 256 immediates was checked with its concrete Form (a complete case split of a
    finite field; the shape's certificate needs L3a to make the builder symbolic in the immediate, or the case split as the certificate's `split`)."""
    rep = symform.parse_key(key)[0]
    try: S.Enc(rep)
    except (AssertionError, IndexError): return 'no-row', None
    try: S.mem_width(rep)
    except KeyError: return 'no-row', None
    imm = z3.BitVec('fld_imm', 8) if S.mem_imm(rep) else None
    try: sf = symform.symbuild(key)
    except KeyError: return 'no-form', None
    except (symform.SymbolicBranch, TypeError, AssertionError) as ex:
        if imm is None: return 'builder-branches-on-field', str(ex)[:120]
        return _by_cases(key, rep, timeout)
    return _obligation(rep, sf.form, symform.symins(key)[0], imm, timeout)


def _by_cases(key, rep, timeout):
    md = shape.decoder()
    for v in range(256):
        code = rep[:-1] + bytes([v]); ins = list(md.disasm(code, 0))[0]
        try: F = forms.build(ins, 0)
        except KeyError: return 'no-form', None
        r, info = _obligation(code, F, ins, z3.BitVecVal(v, 8), timeout)
        if r != 'proved': return r, 'imm=%d %s' % (v, info)
    return 'proved-by-cases', None


def main(a):
    only = a[a.index('--only') + 1] if '--only' in a else ''
    keys = [l.split()[0] for l in open(a[0]) if l.strip() and not l.startswith('#')]
    cnt = {}
    for k in keys:
        if only not in k: continue
        r, info = check(k); cnt[r] = cnt.get(r, 0) + 1
        if r in ('FAILED', 'timeout'): print(r, k, info)
    print(cnt)
    return 0 if not cnt.get('FAILED') and not cnt.get('timeout') else 1


if __name__ == '__main__': sys.exit(main(sys.argv[1:]))
