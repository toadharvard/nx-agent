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


def _load(M0, a, w):
    """the memory operand of width w bits at a (little endian): None when the row reads no memory (mw None: an address-only operand), a single byte for w = 8, else the concatenation (a rule over mem_width)"""
    if w is None: return None
    bs = [z3.Select(M0, a + i) for i in reversed(range(w // 8))]
    return bs[0] if len(bs) == 1 else z3.Concat(*bs)


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
        m = _load(M0, a, w)
    try: r = S.run(rep, d, m, a, gpr, imm)
    except S.F.Uncertified as ex: return 'uncertified', 'uncertified: ' + str(ex)
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
    if r.flags:
        for fn in ('cf', 'pf', 'af', 'zf', 'sf', 'of'): eq.append(P['flags'][fn] == r.flags[fn])          # a row that writes EFLAGS (comis/ucomis): the six status flags; (rows that do not write it leave them to the DSL's own frame rule)
    mem = M0
    if r.store:
        sa, sv, sb = r.store
        for k in range(sb // 8): mem = z3.Store(mem, sa + k, z3.Extract(8 * k + 7, 8 * k, sv))
    eq.append(P['mem'] == mem)
    return _discharge(r.env, nf, goals, eq, timeout, r.fpl, d['mxcsr'])


def _components(e, bits=False):
    """one equality `lhs == rhs` as the list of (equality, chunk) that together are equivalent to it (a complete partition of the bits: 32-bit chunks of a 64..128-bit value; with `bits`, every bit of a 32-bit value: the six exception flags of MXCSR are independent bits): the lanes of a packed
    operation do not depend on each other's bits of the DESTINATION, so each chunk is its own, smaller, query.  An equality of another sort stays whole (chunk None)."""
    lhs, rhs = e.arg(0), e.arg(1)
    if not z3.is_bv(lhs): return [(e, None)]
    w = lhs.size()
    if w == 32 and bits: return [(z3.Extract(k, k, lhs) == z3.Extract(k, k, rhs), None) for k in range(32)]
    if 64 <= w <= 128: return [(z3.Extract(32 * k + 31, 32 * k, lhs) == z3.Extract(32 * k + 31, 32 * k, rhs), k) for k in range(w // 32)]
    return [(e, None)]


_EXP = {32: (8, 23), 64: (11, 52)}
_TAC = z3.Then('simplify', 'propagate-values', 'solve-eqs', 'fpa2bv', 'simplify', 'bit-blast', 'sat')


def _classes(x, w):
    """the five classes of a binary w operand, a complete and disjoint partition of its bit patterns: exponent all zeros / all ones / other  x  fraction zero / nonzero"""
    eb, fb = _EXP[w]; ex = z3.Extract(w - 2, fb, x); fr = z3.Extract(fb - 1, 0, x)
    z, ones = z3.BitVecVal(0, eb), z3.BitVecVal((1 << eb) - 1, eb); fz = fr == z3.BitVecVal(0, fb)
    return [z3.And(ex == z, fz), z3.And(ex == z, z3.Not(fz)), z3.And(ex == ones, fz), z3.And(ex == ones, z3.Not(fz)), z3.And(z3.Not(ex == z), z3.Not(ex == ones))]


def _case_partition(fpl, chunk, mx):
    """the case split of an FP row, with its completeness stated: MXCSR.RC (bits 14:13: 4 values) x MXCSR.FTZ (bit 15: 2 values) x the class of every operand of the lanes the part depends on (5 classes each,
    _classes).  Each factor is a complete, disjoint partition of its variable, so the product is one: the union of the cases is every state, and the part holds iff it holds in each case.  A part that depends
    on more than two operands (the MXCSR flags of a packed operation) is split by RC and FTZ only."""
    facs = [[z3.Extract(14, 13, mx) == z3.BitVecVal(v, 2) for v in range(4)], [z3.Extract(15, 15, mx) == z3.BitVecVal(v, 1) for v in range(2)]]
    ops = [(w, x) for k, (w, xs) in enumerate(fpl) if chunk is None or k == (chunk if w == 32 else chunk // 2) for x in xs]
    if len(ops) <= 2: facs += [_classes(x, w) for w, x in ops]
    cases = [[]]
    for f in facs: cases = [c + [g] for c in cases for g in f]
    return cases


def _consts(e):
    """names of the uninterpreted constants (state variables, arrays) of a term"""
    acc, seen, st = set(), set(), [e]
    while st:
        x = st.pop()
        if x.get_id() in seen: continue
        seen.add(x.get_id())
        if z3.is_const(x) and x.decl().kind() == z3.Z3_OP_UNINTERPRETED: acc.add(x.decl().name())
        st.extend(x.children())
    return acc


_BUDGET = [0, 0.0]                  # [seconds per shape (0: none), deadline of the shape being checked]
_QC = {}                              # canonical query -> proved (unsat): sha256 of the SMT-LIB text of the canonical assertions
_QCFILE = os.environ.get('SIMD_QCACHE')


def _qc_load():
    if _QCFILE and os.path.exists(_QCFILE) and not _QC:
        for ln in open(_QCFILE): _QC[ln.strip()] = True


def _canonical(g, cs, env, fpl, mx):
    """the lane function with its operands abstracted: every FP lane operand of the row (fpl) is replaced by a fresh variable (the same term on both sides of the equality, so this is a substitution of equal
    terms, not an assumption), and only the hypotheses on MXCSR and those operands are kept (weaker hypotheses: a proof is still a proof).  -> (assertions, ok); ok is False when the part still depends on
    any other state (a preserved register, memory): it is then not a pure lane function and is asked whole."""
    ops, seen = [], set()
    for w, xs in fpl:
        for x in xs:
            if x.get_id() not in seen: seen.add(x.get_id()); ops.append((w, x))
    pairs = [(x, z3.BitVec('opv%d' % i, w)) for i, (w, x) in enumerate(ops)]
    sub = lambda t: z3.substitute(t, *pairs)
    allowed = {'mxcsr'} | {v.decl().name() for _, v in pairs}
    g2 = z3.simplify(sub(g)); cs2 = [sub(c) for c in cs]
    keep = [sub(c) for c in (env.children() if z3.is_and(env) else [env])]
    keep = [c for c in keep if _consts(c) <= allowed]
    if not _consts(g2) <= allowed: return None
    return keep + cs2 + [z3.Not(g2)]


def _ask(asserts, timeout):
    s = _TAC.solver(); s.set('timeout', timeout); s.add(*asserts)
    return s


def _discharge(env, nf, goals, eq, timeout, fpl=(), mx=None):
    """proved iff every part of every goal is unsat under env (and nf for the state parts), in every case of its case partition (FP rows: _case_partition; others: one case).  Parts that simplify to true
    (a register the instruction does not touch) are not asked.  An FP part that is a pure function of its lane operands and MXCSR is asked in its canonical form (_canonical), and a proved canonical query
    is remembered (the same lane function of the builder is proved once, whatever register or address the shape names); anything the canonical query does not prove is asked whole."""
    import hashlib, time
    _qc_load()
    parts = [(g, False, None) for g in goals] + [(c, True, k) for e in eq for c, k in _components(e, bool(fpl))]
    unknown = False
    for g0, state, chunk in parts:
        g = z3.simplify(g0)
        if z3.is_true(g): continue
        cases = _case_partition(fpl, chunk, mx) if fpl else [[]]
        for cs in cases:
            if _BUDGET[0] and time.time() > _BUDGET[1]: unknown = True; break          # the shape's wall-clock budget is spent: the rest is not decided (timeout)
            if fpl and state:
                can = _canonical(g0, cs, env, fpl, mx)
                if can is not None:
                    key = hashlib.sha256(z3.And(*can).sexpr().encode()).hexdigest()
                    if key in _QC: continue
                    if _ask(can, timeout).check() == z3.unsat:
                        _QC[key] = True
                        if _QCFILE: open(_QCFILE, 'a').write(key + '\n')
                        continue
            s = _ask([env, *cs, z3.And(nf, z3.Not(g)) if state else z3.Not(g)], timeout)          # FP terms are bit-blasted (fpa2bv) and decided by the SAT core
            t0 = time.time(); res = s.check()
            if os.environ.get('SIMD_TRACE') and (res != z3.unsat or time.time() - t0 > 2): print('  part', str(g)[:70].replace('\n', ' '), res, round(time.time() - t0, 1), flush=True)
            if res == z3.sat: return 'FAILED', str(s.model())[:400]
            if res != z3.unsat: unknown = True
    return ('timeout', None) if unknown else ('proved', None)


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
    except symform.FieldDecodeError:
        if imm is None: return 'no-form', None
        return _concrete(rep, timeout)
    except (symform.SymbolicBranch, TypeError, AssertionError) as ex:
        if imm is None: return 'builder-branches-on-field', str(ex)[:120]
        return _by_cases(key, rep, timeout)
    return _obligation(rep, sf.form, symform.symins(key)[0], imm, timeout)


def _concrete(rep, timeout):
    """a shape whose immediate byte is part of the mnemonic (the cmpeqps ... cmpnleps / cmpeqpd ... pseudo-ops: one predicate each): the immediate is the shape's fixed byte, not a field"""
    md = shape.decoder(); ins = list(md.disasm(rep, 0))[0]
    try: F = forms.build(ins, 0)
    except KeyError: return 'no-form', None
    return _obligation(rep, F, ins, z3.BitVecVal(rep[-1], 8), timeout)


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
    ud = set()
    if '--cleared' in a:                                   # the partition of sdmfeat.py (`partition`): the shapes of the cleared sites, #UD on the declared CPU (CPUREF), each by its SDM-derived reason
        ud = {l.split('\t')[1] for l in open(a[a.index('--cleared') + 1]) if l.strip() and not l.startswith('#')}
    keys = [l.split()[0] for l in open(a[0]) if l.strip() and not l.startswith('#')]
    cnt = {}
    for k in keys:
        if only not in k: continue
        if '--budget' in a: import time; _BUDGET[0] = int(a[a.index('--budget') + 1]); _BUDGET[1] = time.time() + _BUDGET[0]
        r, info = check(k, int(a[a.index('--timeout') + 1]) if '--timeout' in a else 60000)
        if r == 'no-row' and k in ud: r = 'ud-class'                  # no spec row by rule: the opmap's cleared class (Form.fault(UD) is its own certificate), not a SIMD row
        cnt[r] = cnt.get(r, 0) + 1
        if '--progress' in a: print('PROGRESS', k, r, flush=True)
        if r in ('FAILED', 'timeout'): print(r, k, info)
    print(cnt)
    return 0 if not cnt.get('FAILED') and not cnt.get('timeout') else 1


if __name__ == '__main__': sys.exit(main(sys.argv[1:]))
