"""Instruction-semantics DSL: expressions over fixed-width bit-vectors, written ONCE, with two backends:
  to_smt(node)  -> z3 BitVec term (pre-state = z3 variables eax..edi, cf pf af zf sf of, array 'mem' BV32->BV8)
  Form.to_c()   -> C++ function in the lifter's style (u32 locals, M8/M16/M32), unsigned fixed-width operations only.
Semantics (identical in both backends): value of width w is an unsigned integer < 2^w; add/sub/mul wrap; shifts take a
count of the SAME width and are defined for every count (count >= w gives 0, ashr gives the sign fill); udiv by 0 = all
ones, urem by 0 = the dividend (SMT-LIB); eq/ult/slt give a 1-bit value; ite takes a 1-bit condition; load is little endian
and the address wraps modulo 2^32.
A Form is an ordered list of effects.  All expressions read the PRE-state (parallel semantics, loads see the pre-state
memory); effects commit in order, so for two writes to one target the later wins.  Flags not written keep their value.
A flag the SDM leaves undefined is declared with undefined(name, sdm_sentence) and is excluded from any obligation;
flag(name, e, when=c) is a flag defined only when c holds (e.g. OF of a shift only for count 1)."""
import hashlib
import itertools

REGS = ('eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi')
FLAGS = ('cf', 'pf', 'af', 'zf', 'sf', 'of')
_I = {}
_SERIAL = itertools.count()      # node serial numbers: assigned at construction, in construction order (a deterministic key for per-node tables)
def mask(w): return (1 << w) - 1

class N:
    __slots__ = ('op', 'w', 'a', 'x', 'u')
    def __init__(s, op, w, a, x): s.op, s.w, s.a, s.x, s.u = op, w, a, x, next(_SERIAL)
    def __add__(s, o): return bin_('add', s, o)
    def __radd__(s, o): return bin_('add', o, s)
    def __sub__(s, o): return bin_('sub', s, o)
    def __rsub__(s, o): return bin_('sub', o, s)
    def __mul__(s, o): return bin_('mul', s, o)
    def __and__(s, o): return bin_('and', s, o)
    def __or__(s, o): return bin_('or', s, o)
    def __xor__(s, o): return bin_('xor', s, o)
    def __lshift__(s, o): return bin_('shl', s, o)
    def __rshift__(s, o): return bin_('lshr', s, o)
    def __invert__(s): return mk('not', s.w, (s,))
    def __neg__(s): return mk('neg', s.w, (s,))

def mk(op, w, a=(), x=None):
    k = (op, w, tuple(i.u for i in a), x)
    n = _I.get(k)
    if n is None: n = _I[k] = N(op, w, tuple(a), x)
    return n
def _unwrap(v):
    """an object that stands for a DSL node (attribute `_node`, e.g. the symbolic instruction field of proof/symform.py) -> that node"""
    n = getattr(v, '_node', None)
    return v if n is None else n
def C(v, w):
    """constant of width w; a DSL node (an instruction field computed by a decode adapter) is resized to w instead (truncate / zero-extend)"""
    v = _unwrap(v)
    if isinstance(v, N): return v if v.w == w else (extract(v, w - 1, 0) if v.w > w else mk('zext', w, (v,)))
    return mk('const', w, (), v & mask(w))
def V(name, w): return mk('var', w, (), name)
def F(name, w):
    """instruction FIELD variable (the immediate / displacement bytes of an encoding shape): free in a proof, a constant per concrete instruction"""
    return mk('fld', w, (), name)
def bin_(op, x, y):
    x, y = _unwrap(x), _unwrap(y)
    if not isinstance(x, N): x = C(x, y.w)
    if not isinstance(y, N): y = C(y, x.w)
    assert x.w == y.w, (op, x.w, y.w)
    return mk(op, 1 if op in ('eq', 'ult', 'slt') else x.w, (x, y))
def shl(a, c): return bin_('shl', a, c)
def lshr(a, c): return bin_('lshr', a, c)
def ashr(a, c): return bin_('ashr', a, c)
def udiv(a, b): return bin_('udiv', a, b)
def urem(a, b): return bin_('urem', a, b)
def eq(a, b): return bin_('eq', a, b)
def ult(a, b): return bin_('ult', a, b)
def slt(a, b): return bin_('slt', a, b)
def ite(c, a, b):
    assert c.w == 1 and a.w == b.w
    return mk('ite', a.w, (c, a, b))
def zext(a, w): return a if a.w == w else mk('zext', w, (a,))
def sext(a, w): return a if a.w == w else mk('sext', w, (a,))
def extract(a, hi, lo): return a if (hi, lo) == (a.w - 1, 0) else mk('extract', hi - lo + 1, (a,), (hi, lo))
def trunc(a, w): return extract(a, w - 1, 0)
def concat(hi, lo): return mk('concat', hi.w + lo.w, (hi, lo))
def load(addr, w):
    assert addr.w == 32 and w in (8, 16, 32)
    return mk('load', w, (addr,))
def msb(a): return extract(a, a.w - 1, a.w - 1)
def bit(a, i): return extract(a, i, i)

# ---------------------------------------------------------------- z3 backend
_M = {}
def mem_array():
    import z3
    return z3.Array('mem', z3.BitVecSort(32), z3.BitVecSort(8))
def to_smt(n):
    import z3
    r = _M.get(n.u)
    if r is not None: return r
    A = [to_smt(c) for c in n.a]; o = n.op; one = z3.BitVecVal(1, 1); zero = z3.BitVecVal(0, 1)
    if o == 'const': r = z3.BitVecVal(n.x, n.w)
    elif o == 'var': r = z3.BitVec(n.x, n.w)
    elif o == 'fld': r = z3.BitVec('fld_' + n.x, n.w)
    elif o in ('add', 'sub', 'mul', 'and', 'or', 'xor', 'shl'):
        r = {'add': lambda a, b: a + b, 'sub': lambda a, b: a - b, 'mul': lambda a, b: a * b, 'and': lambda a, b: a & b,
             'or': lambda a, b: a | b, 'xor': lambda a, b: a ^ b, 'shl': lambda a, b: a << b}[o](*A)
    elif o == 'lshr': r = z3.LShR(*A)
    elif o == 'ashr': r = A[0] >> A[1]
    elif o == 'udiv': r = z3.UDiv(*A)
    elif o == 'urem': r = z3.URem(*A)
    elif o == 'not': r = ~A[0]
    elif o == 'neg': r = -A[0]
    elif o == 'eq': r = z3.If(A[0] == A[1], one, zero)
    elif o == 'ult': r = z3.If(z3.ULT(*A), one, zero)
    elif o == 'slt': r = z3.If(A[0] < A[1], one, zero)
    elif o == 'ite': r = z3.If(A[0] == one, A[1], A[2])
    elif o == 'zext': r = z3.ZeroExt(n.w - n.a[0].w, A[0])
    elif o == 'sext': r = z3.SignExt(n.w - n.a[0].w, A[0])
    elif o == 'extract': r = z3.Extract(n.x[0], n.x[1], A[0])
    elif o == 'concat': r = z3.Concat(*A)
    elif o == 'load':
        m = mem_array(); bs = [z3.Select(m, A[0] + i) for i in range(n.w // 8)]
        r = z3.Concat(*reversed(bs)) if len(bs) > 1 else bs[0]
    else: raise ValueError(o)
    _M[n.u] = r
    return r

# ---------------------------------------------------------------- C backend
def T(w): return 'u32' if w <= 32 else 'u64'
def K(v, w): return '%#x%s' % (v, 'u' if w <= 32 else 'ull')
class Emit:
    def __init__(s, roots):
        s.cnt, s.order, s.names, s.used = {}, [], {}, set()
        for r in roots: s.walk(r)
        for n in s.order:
            if n.op == 'load' or (s.cnt[n.u] > 1 and n.op not in ('const', 'var')): s.names[n.u] = 't%d' % len(s.names)
    def walk(s, n):
        s.cnt[n.u] = s.cnt.get(n.u, 0) + 1
        if s.cnt[n.u] > 1: return
        for c in n.a: s.walk(c)
        if n.op == 'var': s.used.add(n.x)
        s.order.append(n)
    def ref(s, n):
        if n.u in s.names: return s.names[n.u]
        return s.ex(n)
    def ex(s, n):
        o, w = n.op, n.w; M = K(mask(w), w); t = T(w)
        if o == 'const': return K(n.x, w)
        if o == 'var': return 'in_' + n.x
        a = [s.ref(c) for c in n.a]
        full = w in (32, 64)
        mk_ = (lambda e: '(%s)' % e) if full else (lambda e: '((%s)&%s)' % (e, M))
        if o in ('add', 'sub', 'mul'): return mk_('%s %s %s' % (a[0], {'add': '+', 'sub': '-', 'mul': '*'}[o], a[1]))
        if o in ('and', 'or', 'xor'): return '(%s %s %s)' % (a[0], {'and': '&', 'or': '|', 'xor': '^'}[o], a[1])
        if o == 'not': return '(%s ^ %s)' % (a[0], M)
        if o == 'neg': return mk_('%s - %s' % (K(0, w), a[0]))
        if o == 'shl': return mk_('shl_<%s>(%s,%s,%d)' % (t, a[0], a[1], w))
        if o == 'lshr': return 'lshr_<%s>(%s,%s,%d)' % (t, a[0], a[1], w)
        if o == 'ashr': return 'ashr_<%s>(%s,%s,%d,%s)' % (t, a[0], a[1], w, M)
        if o == 'udiv': return '(%s ? %s / %s : %s)' % (a[1], a[0], a[1], M)
        if o == 'urem': return '(%s ? %s %% %s : %s)' % (a[1], a[0], a[1], a[0])
        sw = n.a[0].w if n.a else 0
        if o in ('eq', 'ult', 'slt') and n.a[0] is n.a[1]: return '(u32)%d' % (o == 'eq')       # x == x / x < x (and -Wtautological-compare)
        if o == 'eq': return '(u32)(%s == %s)' % tuple(a)
        if o == 'ult': return '(u32)0' if n.a[1].op == 'const' and n.a[1].x == 0 else '(u32)(%s < %s)' % tuple(a)      # x < 0 is false (and -Wtype-limits)
        if o == 'slt': S = K(1 << (sw - 1), sw); return '(u32)((%s ^ %s) < (%s ^ %s))' % (a[0], S, a[1], S)
        if o == 'ite': return '(%s ? %s : %s)' % tuple(a)
        if o == 'zext': return '((%s)%s)' % (t, a[0])
        if o == 'sext':
            S = K(1 << (sw - 1), w); e = '((((%s)%s) ^ %s) - %s)' % (t, a[0], S, S)
            return e if full else '(%s & %s)' % (e, M)
        if o == 'extract':
            hi, lo = n.x; e = a[0] if lo == 0 else '(%s >> %d)' % (a[0], lo)
            return '((%s)%s)' % (t, e) if hi == sw - 1 else '((%s)(%s & %s))' % (t, e, M)
        if o == 'concat': return '((((%s)%s) << %d) | %s)' % (t, a[0], n.a[1].w, a[1])
        if o == 'load': return '((u32)M%d(%s))' % (w, a[0])
        raise ValueError(o)

class InlineEmit(Emit):
    """Emit for code inlined into a lifted function (Form.to_c_inline): the shifts are expanded in place (the lifter's runtime has no shl_/lshr_/ashr_
    templates), so the count (and the shifted value of ashr) is named once, as a temporary, when it is not a constant or a variable."""
    def __init__(s, roots, force=()):
        super().__init__(roots)
        pick = [n.a[1] for n in s.order if n.op in ('shl', 'lshr', 'ashr')] + [n.a[0] for n in s.order if n.op == 'ashr'] + list(force)
        for n in pick:
            if n.op not in ('const', 'var') and n.u not in s.names: s.names[n.u] = 't%d' % len(s.names)
    def ex(s, n):
        if n.op not in ('shl', 'lshr', 'ashr'): return super().ex(n)
        w = n.w; M = K(mask(w), w); t = T(w); a, c = s.ref(n.a[0]), s.ref(n.a[1]); Z = '(%s)0' % t
        if n.op == 'shl': e = '(%s >= %du ? %s : (%s)(%s << %s))' % (c, w, Z, t, a, c); return e if w in (32, 64) else '(%s & %s)' % (e, M)
        if n.op == 'lshr': return '(%s >= %du ? %s : (%s)(%s >> %s))' % (c, w, Z, t, a, c)
        sg = '(((%s >> %d) & 1) != 0)' % (a, w - 1)
        return '(%s >= %du ? (%s ? %s : %s) : (%s)((%s >> %s) | (%s ? (%s ^ (%s >> %s)) : %s)))' % (c, w, sg, M, Z, t, a, c, sg, M, M, c, Z)

PRELUDE = '''typedef unsigned char u8; typedef unsigned short u16; typedef unsigned int u32; typedef unsigned long long u64;
template<class T> static inline T shl_(T a, T c, unsigned w) { return c >= w ? (T)0 : (T)(a << c); }
template<class T> static inline T lshr_(T a, T c, unsigned w) { return c >= w ? (T)0 : (T)(a >> c); }
template<class T> static inline T ashr_(T a, T c, unsigned w, T m) { T s = (a >> (w - 1)) & 1; if (c >= w) return s ? m : (T)0; T r = a >> c; if (s) r |= m ^ (m >> c); return r; }
'''
XIN = ('df', 'flo', 'fs_base', 'gs_base')          # other pre-state: DF (1 bit), the other EFLAGS bits (32, masked), segment bases
SIG = ','.join('[[maybe_unused]] u32& ' + r for r in REGS + FLAGS + XIN) + ',u32& eip'
FAULTS = {1: 'DE', 2: 'BP', 3: 'UD'}
INW = {'df': 1, 'flo': 32, 'fs_base': 32, 'gs_base': 32}

class Form:
    def __init__(s, key, addr, size):
        s.key, s.regs, s.flags, s.undef, s.stores, s.faults, s.addr = key, [], [], {}, [], [], addr     # addr: int, or a node (V('eip', 32)) for a form of a whole shape
        s.eip = C(addr + size, 32); s.pend = {}; s.flo = None
    def cur(s, name): return s.pend.get(name, V(name, 32))      # pending value, only for composing partial-register writes
    def reg(s, name, v, when=None): assert v.w == 32; s.regs.append((name, v, when)); s.pend[name] = v
    def flag(s, name, v, when=None): assert v.w == 1 and name in FLAGS + ('df',); s.flags.append((name, v, when))
    def setflo(s, v): assert v.w == 32; s.flo = v
    def undefined(s, name, sdm): s.undef[name] = sdm
    def store(s, addr, v): s.stores.append((addr, v))
    def fault(s, code, cond): assert cond.w == 1; s.faults.append((code, cond))
    def roots(s):
        return [v for _, v, _ in s.regs] + [w for _, _, w in s.regs if w is not None] + [v for _, v, _ in s.flags] + \
               [w for _, _, w in s.flags if w is not None] + [x for st in s.stores for x in st] + [c for _, c in s.faults] + \
               ([s.flo] if s.flo is not None else []) + [s.eip]
    def serialize(s):
        """canonical DAG text (hash input of the certificates)"""
        ids, out = {}, []
        def go(n):
            if n.u in ids: return ids[n.u]
            a = [go(c) for c in n.a]; ids[n.u] = i = len(ids)
            out.append('n%d=%s/%d %s %s' % (i, n.op, n.w, ','.join('n%d' % j for j in a), n.x if n.x is not None else ''))
            return i
        nw = lambda w: 'n%d' % go(w) if w is not None else '-'
        for r, v, w in s.regs: out.append('reg %s n%d %s' % (r, go(v), nw(w)))
        for f, v, w in s.flags: out.append('flag %s n%d %s' % (f, go(v), nw(w)))
        for f in sorted(s.undef): out.append('undef ' + f)
        for a, v in s.stores: out.append('store n%d n%d' % (go(a), go(v)))
        for k, c in s.faults: out.append('fault %d n%d' % (k, go(c)))
        if s.flo is not None: out.append('flo n%d' % go(s.flo))
        out.append('eip n%d @%s' % (go(s.eip), '%x' % s.addr if isinstance(s.addr, int) else 'n%d' % go(s.addr)))
        return '\n'.join(out)
    def sha(s): return hashlib.sha256(s.serialize().encode()).hexdigest()
    def fault_term(s):
        import z3
        r = z3.BitVecVal(0, 8)
        for k, c in reversed(s.faults): r = z3.If(to_smt(c) == 1, z3.BitVecVal(k, 8), r)
        return r
    def post(s):
        """z3 post-state over the pre-state variables.  {'regs','flags'(six+df),'flo','mem','eip','defined'(flags),'rdefined'(regs),'fault'}.
        On a fault the state is unchanged and eip = the instruction address (fields are guarded by fault == 0)."""
        import z3
        regs = {r: z3.BitVec(r, 32) for r in REGS}; flags = {f: z3.BitVec(f, 1) for f in FLAGS + ('df',)}
        defined = {f: z3.BoolVal(f not in s.undef) for f in flags}; rdef = {r: z3.BoolVal(True) for r in REGS}
        for r, v, w in s.regs:
            regs[r] = to_smt(v)
            if w is not None: rdef[r] = to_smt(w) == 1
        for f, v, w in s.flags:
            flags[f] = to_smt(v)
            if w is not None: defined[f] = to_smt(w) == 1
        mem = mem_array(); flo = to_smt(s.flo) if s.flo is not None else z3.BitVec('flo', 32)
        for a, v in s.stores:
            sv = to_smt(v)
            for i in range(v.w // 8): mem = z3.Store(mem, to_smt(a) + i, z3.Extract(8 * i + 7, 8 * i, sv))
        eip = to_smt(s.eip); ft = s.fault_term()
        if s.faults:
            nf = ft == 0; g = lambda new, old: z3.If(nf, new, old)
            regs = {r: g(regs[r], z3.BitVec(r, 32)) for r in regs}; flags = {f: g(flags[f], z3.BitVec(f, 1)) for f in flags}
            flo = g(flo, z3.BitVec('flo', 32)); mem = g(mem, mem_array()); eip = g(eip, z3.BitVecVal(s.addr, 32) if isinstance(s.addr, int) else to_smt(s.addr))
        return {'regs': regs, 'flags': flags, 'flo': flo, 'mem': mem, 'eip': eip, 'defined': defined, 'rdefined': rdef, 'fault': ft}
    def writes_unobserved(s): return s.flo is not None or any(f == 'df' for f, _, _ in s.flags)
    def to_c_inline(s, varc, fault_fn, flo_fn, force=()):
        """C lines of ONE instruction to be placed inside a block of a lifted function: -> (lines, ref) where ref(node) is the C text of a node
        usable AFTER the lines (a named temporary or an expression over the `in_` snapshots).  Order = the Form's: snapshots of the used pre-state
        (varc(name) -> the C expression of a pre-state variable), temporaries for loads and shared sub-expressions (and the `force`d nodes), faults
        (fault_fn(code, cond_node, ref) -> C line), register writes, flag writes (unconditional, also with `when`; an undefined flag gets none),
        the other-EFLAGS write (flo_fn(ref of the value) -> C line), stores.  The eip node is not emitted: the caller derives the control transfer from it."""
        e = InlineEmit(s.roots(), force); L = []
        for u in sorted(e.used): L.append('const u32 in_%s = %s;' % (u, varc(u)))
        for n in e.order:
            if n.u in e.names: L.append('const %s %s = %s;' % (T(n.w), e.names[n.u], e.ex(n)))
        for k, c in s.faults: L.append(fault_fn(k, c, e.ref))
        cw = lambda w: '' if w is None else 'if (%s) ' % e.ref(w)
        for r, v, w in s.regs: L.append('%s%s = %s;' % (cw(w), r, e.ref(v)))
        for f, v, w in s.flags: L.append('%s = %s;' % (f, e.ref(v)))     # `when` only bounds the proof obligation: v is a deterministic value (e.g. SHL CF for count >= size)
        if s.flo is not None: L.append(flo_fn(e.ref(s.flo)))
        for a, v in s.stores: L.append('M%d(%s) = %s;' % (v.w, e.ref(a), e.ref(v)))
        return L, e.ref
    def to_c(s, fname):
        e = Emit(s.roots()); L = []
        L.append('static u32 %s(%s) {' % (fname, SIG))
        for u in sorted(e.used): L.append('  const u32 in_%s = %s;' % (u, u))
        for n in e.order:
            if n.u in e.names: L.append('  const %s %s = %s;' % (T(n.w), e.names[n.u], e.ex(n)))
        for k, c in s.faults: L.append('  if (%s) { eip = %s; return %d; }' % (e.ref(c), K(s.addr, 32), k))
        for r, v, _ in s.regs: L.append('  %s = %s;' % (r, e.ref(v)))
        for f, v, _ in s.flags: L.append('  %s = %s;' % (f, e.ref(v)))
        if s.flo is not None: L.append('  flo = %s;' % e.ref(s.flo))
        for a, v in s.stores: L.append('  M%d(%s) = %s;' % (v.w, e.ref(a), e.ref(v)))
        L.append('  eip = %s;\n  return 0;\n}' % e.ref(s.eip))
        return '\n'.join(L)
