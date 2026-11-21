#!/usr/bin/env python3
"""x87exec: the obligations that tie the x87 spec (x87spec, OWNER 10) to the EMITTED lifted text (x87emit executes it under ctrans over symbolic carriers, memory and registers).

State.  Per physical register p the carrier (k, v, e) is free, constrained by the kind-domain invariant (x87kind); TOP is a constant 0..7 (every access is `(ftop + j) & 7`, so the eight values cover the
whole domain exactly); memory is the array `mem0` of the executor; the guest registers are free 32-bit inputs; c->fex is clear at entry.

The spec reading of a state (x87kind.fvx, the view64/bits lemma): register p is the image fv_x(carrier p); the memory operand is the little-endian bytes of `mem0` at the effective address, which is
decoded from the instruction bytes by capstone (the lifter's own decoder: proof/shape.py), never read from the emitted text.

DE (SDM 1:8.5.2, 1:4.9.2, OWNER 8): `spec_de`.  The DE bit that the emitted text puts in c->fex must equal it for every state of the kind domain.
"""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", ".."))
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]
import z3
import fnvnx.lift.proof.ctrans as ctrans
import fnvnx.lift.proof.x87carrier as XC
import fnvnx.lift.proof.x87kind as K
import fnvnx.lift.proof.x87emit as XE
import fnvnx.lift.proof.x87class as x87class
import fnvnx.lift.proof.x87form as XF
import fnvnx.lift.proof.x87spec as X
from fnvnx.lift.proof import shape

MEMFMT = {0xd8: 'f32', 0xd9: 'f32', 0xda: 'i32', 0xdc: 'f64', 0xdd: 'f64', 0xde: 'i16'}      # SDM Vol 2 Table A-6: the memory operand format of an arithmetic, compare or load escape opcode (mod != 3)
BINARY = {(0xd9, 6, 1), (0xd9, 6, 3), (0xd9, 6, 5), (0xd9, 7, 0), (0xd9, 7, 5), (0xde, 3, None), (0xda, 5, 1)}      # (opcode, ModRM.reg, rm or None): the instruction pages whose source operands are ST(0) and ST(1) (SDM Vol 2: FYL2X, FPATAN, FPREM1, FPREM, FSCALE, FCOMPP, FUCOMPP)


def sources(raw):
    """-> (physical register offsets from TOP read as operands, memory operand format or None) of the x87 instruction RAW, for the instructions whose pages list a denormal-operand exception (#D)"""
    op, kind, reg, rm = x87class.x87_class(raw)
    if kind == 'mem':
        if op in (0xd9, 0xdd): return [], MEMFMT[op]      # FLD m32fp / m64fp
        return [0], MEMFMT[op]
    i = x87class.encoding(raw)[1] & 7
    if (op, reg, rm) in BINARY or (op, reg, None) in BINARY: return [0, 1], None
    if op == 0xd9: return [0], None
    return [0, i], None


def ea_term(mc, raw):
    """the effective address of the memory operand of RAW, a 32-bit z3 term over the machine's guest-register inputs, from the decoder's operand (SDM Vol 2 2.1.5: base + index * scale + disp, modulo 2^32); None without a memory operand"""
    from pycparser import c_ast
    ins = next(shape.decoder().disasm(raw, 0))
    ops = [o for o in ins.operands if o.type == 3]
    if not ops: return None
    m = ops[0].mem
    if ins.addr_size != 4 or ins.reg_name(m.segment) in ('fs', 'gs'): raise ctrans.Unsupported('segment base / 16-bit address of the memory operand')
    g = lambda r: mc.eval(c_ast.ID(ins.reg_name(r)), ctrans.Frame('ea', z3.BoolVal(True))).e
    a = z3.BitVecVal(m.disp & 0xffffffff, 32)
    if m.base: a = a + g(m.base)
    if m.index: a = a + g(m.index) * z3.BitVecVal(m.scale, 32)
    return a


def load_bytes(mem, addr32, nbytes):
    a = z3.ZeroExt(32, addr32)
    bs = [z3.Select(mem, a + z3.BitVecVal(i, 64)) for i in range(nbytes)]
    return z3.Concat(*reversed(bs)) if nbytes > 1 else bs[0]


def operand_flags(fmt, bits):
    """(nan, denormal) of a memory operand of format FMT (SDM 1:4.2.2, T4-1: the binary32 / binary64 fields); an integer has neither"""
    if fmt == 'f32': e, f = z3.Extract(30, 23, bits), z3.Extract(22, 0, bits); return z3.And(e == 0xff, f != 0), z3.And(e == 0, f != 0)
    if fmt == 'f64': e, f = z3.Extract(62, 52, bits), z3.Extract(51, 0, bits); return z3.And(e == 0x7ff, f != 0), z3.And(e == 0, f != 0)
    return z3.BoolVal(False), z3.BoolVal(False)


class Ctx:
    """one class at one TOP: the executed fragment and the spec's reading of the entry state"""
    def __init__(self, raw, top):
        self.raw, self.top = raw, top
        self.mc, self.res = XE._frag(raw, top)
        self.cpu = self.mc.G['c']
        k = K.kinds(); self.kinds = k
        self.car = []      # [(v, e, k)] entry carriers of the physical registers
        for p in range(8):
            self.car.append((z3.BitVec('c.fx[%d].v' % p, 64), z3.BitVec('c.fx[%d].e' % p, 32), z3.BitVec('c.fx[%d].k' % p, 32)))
        self.dom = z3.And(*[K.kind_invariant(v, e, kk) for v, e, kk in self.car])
        self.img = [K._fvx(z3.Concat(kk, e, v)) for v, e, kk in self.car]
        self.addr = ea_term(self.mc, raw)
        self.fcw = z3.BitVec('c.fcw', 32)

    def phys(self, j): return (self.top + j) & 7

    def spec_state(self, o, pcs):
        """the substitution that reads the entry state as the spec's pre-state"""
        sub = [(z3.BitVec('fr%d' % p, 80), self.img[p]) for p in range(8)]
        sub.append((z3.BitVec('ftop', 3), z3.BitVecVal(self.top, 3)))
        tag = z3.Concat(*[z3.If(self.car[p][2] == 0, z3.BitVecVal(3, 2), z3.BitVecVal(0, 2)) for p in reversed(range(8))])
        sub.append((z3.BitVec('ftag', 16), tag))
        sub.append((z3.BitVec('fcw', 16), z3.Extract(15, 0, self.fcw)))
        if pcs:
            for off, w, var in pcs: sub.append((var, load_bytes(ctrans.MEM_IN, self.addr + z3.BitVecVal(off // 8, 32), w // 8)))
        return sub


_RAW = {}


def spec_run(key, i):
    """the spec entry executed on the symbolic DSL pre-state with its own functions (x87form.run's call without `instrument`: the op symbols view64 / bits ... stay the spec's definitions, not uninterpreted)"""
    if (key, i) not in _RAW:
        e = X.REGISTRY[key]; d = XF.pre_vars()
        pcs = [(o, w, z3.BitVec("%s_%d" % (XF.MEM_VAR, o), w)) for o, w in XF.mem_pieces(e["mem_bits"])] if e["mem_bits"] else None
        m = None if pcs is None else (pcs[0][2] if len(pcs) == 1 else z3.Concat(*[v for _, _, v in reversed(pcs)]))
        ef = tuple(z3.Bool(n) for n in XF.EF_VARS) if e["uses_ef"] else None
        _RAW[(key, i)] = (X.execute(key, X.from_dsl(d), m, i, ef), pcs)
    return _RAW[(key, i)]


def spec_de(cx):
    """-> (DE, IE, nan_any, domain): the spec's DE bit at the carrier type for the state of CX, a z3 Bool; IE the spec's invalid-operation flag on the state (x87spec Out.raised bit 0, stack faults excluded);
    DE = a source is a denormal at its own type, and neither IE nor a NaN source (SDM 1:8.5.2; 1:4.9.2 priority items 1, 2 over item 4)"""
    key, i = XF.spec_key(cx.raw)
    o, pcs = spec_run(key, i)
    regs, fmt = sources(cx.raw)
    sub = cx.spec_state(o, pcs)
    ie = z3.substitute(z3.Extract(0, 0, o.raised), *sub) == 1
    nofault = z3.Not(z3.substitute(o.sf_raised, *sub))
    nan, den = [], []
    for j in regs:
        v, e, k = cx.car[cx.phys(j)]
        n, s, dn = X.carrier_flags(K.kinds(), v, k)
        n2, s2, u2, d2 = X.image_flags(cx.img[cx.phys(j)])
        m80 = z3.Or(k == cx.kinds['FK_M80'], k == cx.kinds['FK_E'])
        nan.append(z3.If(m80, n2, n)); den.append(z3.If(m80, d2, dn))
    if fmt:
        bits = load_bytes(ctrans.MEM_IN, cx.addr, {'f32': 4, 'f64': 8, 'i16': 2, 'i32': 4}[fmt])
        n, dn = operand_flags(fmt, bits); nan.append(n); den.append(dn)
    de = z3.And(z3.Or(*den) if den else z3.BoolVal(False), z3.Not(ie), z3.Not(z3.Or(*nan) if nan else z3.BoolVal(False)))
    return de, ie, nofault


def emitted_de(cx):
    fex = cx.cpu['fex'].e; fpsr = cx.res.fpsr
    live = z3.If(z3.Extract(25, 24, fex) == 1, z3.Extract(7, 7, fpsr), z3.BitVecVal(0, 1)) if fpsr.size() >= 8 else z3.BitVecVal(0, 1)
    return z3.Extract(1, 1, fex) | live


def de_check(raw, tops=range(8), budget_ms=300000):
    """-> {top: ('unsat'|'sat'|'unknown', counterexample or None)}: the emitted DE differs from spec_de in some state of the kind domain (unsat = the DE of the emitted text equals the spec's)"""
    out = {}
    for t in tops:
        cx = Ctx(raw, t); de_s, ie, nofault = spec_de(cx); de_e = emitted_de(cx)
        s = z3.Solver(); s.set('timeout', budget_ms); s.add(cx.dom, nofault, z3.Extract(5, 0, cx.fcw) == 0x3f, z3.Extract(0, 0, de_e) != z3.If(de_s, z3.BitVecVal(1, 1), z3.BitVecVal(0, 1)))
        r = s.check(); out[t] = (str(r), None)
        if r == z3.sat:
            m = s.model(); out[t] = ('sat', {str(d): hex(m[d].as_long()) for d in m.decls() if str(d).startswith('c.fx') or str(d) == 'c.fcw'})
    return out


def key_bytes(key, rm_default=1):
    """instruction bytes of a spec class key (opcode, form, reg, rm): memory forms address [edx], register forms use ST(rm_default) unless the class fixes rm (x87class.REFINE_RM)"""
    op, kind, reg, rm = key
    o = int(op, 16)
    if kind == '-': return bytes([o])
    if kind == 'mem': return bytes([o, (reg << 3) | 2])
    return bytes([o, 0xc0 | (reg << 3) | (rm if rm is not None else rm_default)])


def de_universe():
    """the spec classes whose DE bit (x87spec Out.raised bit 1) is not the constant 0 at the pre-state variables: every REGISTRY entry, run through the spec's own functions"""
    out = []
    for key in sorted(X.REGISTRY, key=str):
        e = X.REGISTRY[key]
        o, pcs = spec_run(key, 0 if e["uses_i"] else None)
        b = z3.simplify(z3.Extract(1, 1, o.raised))
        if not (z3.is_bv_value(b) and b.as_long() == 0): out.append(key)
    return out


if __name__ == '__main__':
    import json
    if sys.argv[1] == 'batch':      # batch OUT.jsonl JOBS [TOPS...]: every class of the DE universe, one subprocess each (bounded memory), JOBS in parallel
        import subprocess, concurrent.futures as cf
        out, jobs, tops = sys.argv[2], int(sys.argv[3]), sys.argv[4:] or ['0']
        keys = de_universe()
        def one(k):
            try:
                p = subprocess.run([sys.executable, '-I', os.path.abspath(__file__), json.dumps(k)] + tops, capture_output=True, text=True, timeout=5400)
                line = p.stdout.strip().split('\n')[-1] if p.returncode == 0 and p.stdout.strip() else json.dumps({'key': k, 'error': (p.stderr or '')[-300:]})
            except subprocess.TimeoutExpired: line = json.dumps({'key': k, 'error': 'timeout'})
            r = json.loads(line); r['key'] = k; return json.dumps(r)
        with cf.ThreadPoolExecutor(jobs) as ex, open(out, 'w') as fh:
            for line in ex.map(one, keys): fh.write(line + '\n'); fh.flush()
    elif sys.argv[1] == 'universe':
        for k in de_universe(): print(json.dumps(k))
    else:
        raw = key_bytes(tuple(json.loads(sys.argv[1])))
        tops = [int(x) for x in sys.argv[2:]] or [0]
        print(json.dumps({'raw': raw.hex(), 'result': {str(t): r for t, r in de_check(raw, tops).items()}}))
