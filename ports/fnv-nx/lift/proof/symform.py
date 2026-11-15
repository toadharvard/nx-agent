#!/usr/bin/env python3
"""Symbolic DSL Forms of whole ENCODING SHAPES (shape.py): the immediate / displacement / relative-target bytes (and the instruction address eip) are
DSL variables instead of constants.  No z3 is needed to build or hash one (the gate recomputes dsl_sha with `shape_sha(key)`).

  symbuild(key)            -> SymForm(form, fields, widths, imm_ops, disp_ops): forms.build() run on an ADAPTER instruction whose immediate operands, memory displacement and
                              address are DSL nodes (fnvlift/dsl.py: F('imm', w), F('disp', w), V('eip', 32)); forms*.py is not touched
  instantiate(sf, i, addr) -> the concrete Form of an instruction i of the shape (field bytes of i, address addr): substitution + constant folding
  canon(form)              -> serialization of the constant-folded Form; the LINK of the certificate is
                              canon(forms.build(i, addr)) == canon(symbuild(shape(i)) [fields := bytes of i, eip := addr])      (symcheck.py, test_symform.py)
  shape_sha(key)           -> sha256 of the serialization of the symbolic Form (the certificate's dsl_sha)

How the adapter is derived (no tables by mnemonic): capstone IS the decoder the lifter uses, so the map  field bytes -> operand value  is read from it by
probing the decoder on the representative encoding with the field bytes set to chosen values (per byte: does the operand change?  which slice?  zero or
sign extension to 32 bits, or to 16 bits (a 66-prefixed imm8: capstone reports 0xff80)?  does the value move with the address = relative target?).  The hypothesis set is closed ({zext, sext32, sext16} of a byte slice, plus
addr + size when relative); a decoder whose value fits none of them raises FieldDecodeError (result class `field-decode`, never a guess).  The
derivation is a hypothesis about capstone; its soundness for every instruction of the exe is established exhaustively by symcheck.py.
Builders may only compute with the field (DSL nodes).  A builder that BRANCHES on it (compares, truth value, int()) raises SymbolicBranch(file:line):
that builder cannot be made symbolic by substitution (class `builder-branches-on-field`)."""
import os, sys, traceback
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import hashlib
from fnvlift import dsl
from fnvlift.dsl import N, mk, C, V, F, mask
from capstone import x86 as X
from proof import forms, shape as shp
from proof.detrand import Rng

M32 = 0xffffffff
def eip(): return V('eip', 32)

class FieldDecodeError(Exception): pass
class SymbolicBranch(Exception): pass

# ------------------------------------------------------------------------------------------------ key
def parse_key(key):
    """shape key -> (rep bytes, {'imm': (off, size) | None, 'disp': (off, size) | None})"""
    hexs, tail = key.split('/'); io, isz, do, dsz = [int(x) for x in tail.split('.')]
    return bytes.fromhex(hexs), {'imm': (io, isz) if isz else None, 'disp': (do, dsz) if dsz else None}

def with_fields(rep, fields, vals):
    b = bytearray(rep)
    for n, v in vals.items():
        off, sz = fields[n]; b[off:off + sz] = (v & mask(8 * sz)).to_bytes(sz, 'little')
    return bytes(b)

def field_values(raw, fields):
    return {n: int.from_bytes(raw[f[0]:f[0] + f[1]], 'little') for n, f in fields.items() if f}

# ------------------------------------------------------------------------------------------------ the symbolic operand value
class SV:
    """a 32-bit DSL node posing as the Python integer value of an operand: arithmetic builds nodes, every Python-level decision raises SymbolicBranch"""
    def __init__(s, node, what): s._node = node; s._what = what
    def _b(s, *a): raise SymbolicBranch(s._what)
    __eq__ = __ne__ = __lt__ = __le__ = __gt__ = __ge__ = __bool__ = __int__ = __index__ = __hash__ = _b
    def __and__(s, o): return s._node & o
    __rand__ = __and__
    def __or__(s, o): return s._node | o
    __ror__ = __or__
    def __xor__(s, o): return s._node ^ o
    __rxor__ = __xor__
    def __add__(s, o): return s._node + o
    def __radd__(s, o): return dsl.bin_('add', o, s._node)
    def __sub__(s, o): return s._node - o
    def __rsub__(s, o): return dsl.bin_('sub', o, s._node)
    def __mul__(s, o): return s._node * o
    __rmul__ = __mul__
    def __lshift__(s, o): return dsl.bin_('shl', s._node, o)
    def __rshift__(s, o): return dsl.bin_('lshr', s._node, o)
    def __neg__(s): return -s._node
    def __invert__(s): return ~s._node

class Slice:
    """operand value = ext32(field[lo:lo+n bytes]) (+ eip + insn size when relative)"""
    def __init__(s, field, lo, n, ext, rel): s.field, s.lo, s.n, s.ext, s.rel = field, lo, n, ext, rel
    def node(s, width, size):
        f = F(s.field, width); v = dsl.extract(f, 8 * (s.lo + s.n) - 1, 8 * s.lo)
        v = dsl.sext(v, 32) if s.ext == 's' else dsl.zext(dsl.sext(v, 16), 32) if s.ext == 's16' else dsl.zext(v, 32)
        return v + (eip() + size) if s.rel else v
    def value(s, fieldval, addr, size):
        v = (fieldval >> (8 * s.lo)) & mask(8 * s.n)
        if s.ext in ('s', 's16') and v >> (8 * s.n - 1): v -= 1 << (8 * s.n)
        return (v & 0xffff if s.ext == 's16' else v + (addr + size if s.rel else 0)) & M32

class SymMem:
    """a capstone memory operand whose displacement is a symbolic value; the other members forms*.py reads (base, index, scale, segment) are the decoder's"""
    def __init__(s, mem, disp): s._m, s.disp = mem, disp
    @property
    def base(s): return s._m.base
    @property
    def index(s): return s._m.index
    @property
    def scale(s): return s._m.scale
    @property
    def segment(s): return s._m.segment
class SymOp:
    """a capstone operand whose immediate / memory operand may be symbolic; the other members forms*.py reads (type, size, reg) are the decoder's"""
    def __init__(s, op, imm=None, mem=None): s._o, s._imm, s._mem = op, imm, mem
    @property
    def type(s): return s._o.type
    @property
    def size(s): return s._o.size
    @property
    def reg(s): return s._o.reg
    @property
    def mem(s): return s._mem if s._mem is not None else s._o.mem
    @property
    def imm(s): return s._imm if s._imm is not None else s._o.imm
class SymIns:
    """a capstone instruction with symbolic operands; the other members forms*.py reads (bytes, size, mnemonic, reg_name) are the decoder's"""
    def __init__(s, ins, ops): s._i, s.operands = ins, ops
    @property
    def bytes(s): return s._i.bytes
    @property
    def size(s): return s._i.size
    @property
    def mnemonic(s): return s._i.mnemonic
    def reg_name(s, r): return s._i.reg_name(r)

# ------------------------------------------------------------------------------------------------ derivation of the adapter from the decoder
_md = shp.decoder()
def decode(raw, addr):
    l = list(_md.disasm(raw, addr))
    if len(l) != 1 or l[0].size != len(raw): raise FieldDecodeError('does not decode as one instruction of %d bytes: %s' % (len(raw), raw.hex()))
    return l[0]

def _probes(width, seed):
    rnd = Rng(seed); top = 1 << (width - 1)
    return sorted(set([0, 1, top, top - 1, top + 1, mask(width), mask(width) - 1] + [rnd.getrandbits(width) for _ in range(6)]))
ADDRS = (0, 0x401000, 0x7ffffff0)

def _find_slice(read, rep, fields, fname, nbytes):
    """slice [a, b) of the field bytes that `read(raw, addr)` depends on, and the (ext, rel) hypothesis that fits every probe"""
    off, sz = fields[fname]
    def val(fv, addr): return read(decode(with_fields(rep, fields, {fname: fv}), addr), addr)
    base = val(0, 0); dep = []
    for k in range(sz):
        if any(val(v << (8 * k), 0) != base for v in (1, 0x80, 0xff)): dep.append(k)
    if not dep: return None
    a, b = dep[0], dep[-1] + 1
    if dep != list(range(a, b)): raise FieldDecodeError('operand depends on non-contiguous bytes %s of %s' % (dep, fname))
    n = b - a; ins0 = decode(rep, 0); size = ins0.size
    pv = _probes(8 * n, 1000 * a + n)
    for ext in ('z', 's', 's16'):
        for rel in (False, True):
            sl = Slice(fname, a, n, ext, rel)
            if all(val(v << (8 * a), ad) == sl.value(v << (8 * a), ad, size) for v in pv for ad in ADDRS): return sl
    raise FieldDecodeError('value of the operand fits none of zext / sext32 / sext16 (+ address) of bytes [%d,%d) of %s' % (a, b, fname))

class SymForm:
    def __init__(s, form, fields, widths, imm_ops, disp_ops):
        s.form, s.fields, s.widths, s.imm_ops, s.disp_ops = form, fields, widths, imm_ops, disp_ops
    def used(s):
        """names of the fields / variables the Form actually depends on"""
        out, seen, st = set(), set(), list(s.form.roots()) + ([s.form.addr] if isinstance(s.form.addr, N) else [])
        while st:
            n = st.pop()
            if n.u in seen: continue
            seen.add(n.u)
            if n.op in ('fld', 'var'): out.add(n.x)
            st.extend(n.a)
        return out

def derive(rep, fields):
    """-> (imm slices {operand index: Slice}, disp slices {operand index: Slice})"""
    ins = decode(rep, 0); imm, disp = {}, {}
    for j, o in enumerate(ins.operands):
        if o.type == X.X86_OP_IMM and fields['imm']:
            rd_ = lambda i, ad, j=j: i.operands[j].imm & M32
            imm[j] = _find_slice(rd_, rep, fields, 'imm', 4)
        elif o.type == X.X86_OP_MEM and fields['disp']:
            rd_ = lambda i, ad, j=j: i.operands[j].mem.disp & M32
            disp[j] = _find_slice(rd_, rep, fields, 'disp', 4)
    imm = {j: v for j, v in imm.items() if v is not None}; disp = {j: v for j, v in disp.items() if v is not None}
    if fields['imm'] and not imm: raise FieldDecodeError('no immediate operand depends on the immediate bytes (%s)' % ins.mnemonic)
    if fields['disp'] and not disp: raise FieldDecodeError('no memory operand depends on the displacement bytes (%s)' % ins.mnemonic)
    return imm, disp

def symbuild(key):
    """SymForm of a shape.  Raises KeyError (no DSL builder for the mnemonic), FieldDecodeError, SymbolicBranch, AssertionError (builder precondition)"""
    rep, fields = parse_key(key); ins = decode(rep, 0)
    imm, disp = derive(rep, fields); size = ins.size; widths = {n: 8 * f[1] for n, f in fields.items() if f}
    ops = []
    for j, o in enumerate(ins.operands):
        so = SymOp(o)
        if j in imm: so = SymOp(o, imm=SV(imm[j].node(widths['imm'], size), '%s operand %d (imm)' % (ins.mnemonic, j)))
        if j in disp: so = SymOp(o, mem=SymMem(o.mem, SV(disp[j].node(widths['disp'], size), '%s operand %d (disp)' % (ins.mnemonic, j))))
        ops.append(so)
    sins = SymIns(ins, ops)
    try: f = forms.build(sins, eip())
    except SymbolicBranch as e:
        tb = [t for t in traceback.extract_tb(sys.exc_info()[2]) if os.path.basename(t.filename).startswith('forms')]
        raise SymbolicBranch('%s at %s:%d (%s)' % (e, os.path.basename(tb[-1].filename), tb[-1].lineno, ins.mnemonic)) from None
    sf = SymForm(f, fields, widths, imm, disp); sf.key = key
    return sf

def shape_sha(key):
    """certificate field dsl_sha: sha256 of the serialization of the symbolic Form of the shape (needs capstone + fnvlift/dsl.py + forms*.py, no z3)"""
    return symbuild(key).form.sha()

def eip_dependence(sf):
    """'none' | 'target' (the eip effect is not eip + constant: a relative transfer) | 'data' (eip is read by a register / flag / store / fault effect: a
    return address).  By the structure of the Form, not by mnemonic."""
    f = sf.form; data = [v for _, v, _ in f.regs] + [w for _, _, w in f.regs if w is not None] + [v for _, v, _ in f.flags] + [x for st in f.stores for x in st] + [f.swhen[i] for i in sorted(f.swhen)] + \
        [c for _, c in f.faults] + ([f.flo] if f.flo is not None else [])
    def reach(roots):
        seen, st = set(), list(roots)
        while st:
            n = st.pop()
            if n.u in seen: continue
            seen.add(n.u)
            if n.op == 'var' and n.x == 'eip': return True
            st.extend(n.a)
        return False
    if reach(data): return 'data'
    e = f.eip
    return 'none' if e.op == 'add' and {e.a[0].op, e.a[1].op} == {'var', 'const'} and any(x.op == 'var' and x.x == 'eip' for x in e.a) else 'target'

# ------------------------------------------------------------------------------------------------ substitution + constant folding
def _ev(n, a):
    o, w = n.op, n.w; m = mask(w); x = n.a[0].w if n.a else 0
    sg = lambda v, ww: v - (1 << ww) if v >> (ww - 1) else v
    if o == 'add': return (a[0] + a[1]) & m
    if o == 'sub': return (a[0] - a[1]) & m
    if o == 'mul': return (a[0] * a[1]) & m
    if o == 'and': return a[0] & a[1]
    if o == 'or': return a[0] | a[1]
    if o == 'xor': return a[0] ^ a[1]
    if o == 'shl': return (a[0] << a[1]) & m if a[1] < w else 0
    if o == 'lshr': return a[0] >> a[1] if a[1] < w else 0
    if o == 'ashr': return (sg(a[0], w) >> min(a[1], w)) & m
    if o == 'udiv': return a[0] // a[1] if a[1] else m
    if o == 'urem': return a[0] % a[1] if a[1] else a[0]
    if o == 'not': return a[0] ^ m
    if o == 'neg': return (-a[0]) & m
    if o == 'eq': return int(a[0] == a[1])
    if o == 'ult': return int(a[0] < a[1])
    if o == 'slt': return int(sg(a[0], x) < sg(a[1], x))
    if o == 'ite': return a[1] if a[0] else a[2]
    if o == 'zext': return a[0]
    if o == 'sext': return sg(a[0], x) & m
    if o == 'extract': return (a[0] >> n.x[1]) & m
    if o == 'concat': return (a[0] << n.a[1].w) | a[1]
    raise ValueError(o)

class Subst:
    """bottom-up substitution {('fld'|'var', name): int} + folding of the nodes whose children are all constants (semantics-preserving: the evaluation
    is the DSL's own: wrap-around, shifts >= width give 0, udiv by 0 = all ones, ...).  Nothing else is simplified, so two Forms are equal as terms
    after folding iff their serializations are equal."""
    def __init__(s, env): s.env, s.memo = env, {}
    def __call__(s, n):
        r = s.memo.get(n.u)
        if r is not None: return r
        if n.op in ('fld', 'var') and (n.op, n.x) in s.env: r = C(s.env[(n.op, n.x)], n.w)
        elif n.op in ('const', 'var', 'fld'): r = n
        else:
            a = [s(c) for c in n.a]
            r = C(_ev(n, [c.x for c in a]), n.w) if n.op != 'load' and all(c.op == 'const' for c in a) else mk(n.op, n.w, a, n.x)
        s.memo[n.u] = r; return r

def normalize(form, env=None):
    """a copy of the Form with env substituted and constants folded; env: {'imm': v, 'disp': v, 'eip': v}"""
    import copy
    e = {}
    for k, v in (env or {}).items(): e[('var' if k == 'eip' else 'fld', k)] = v
    S = Subst(e); g = copy.copy(form)
    g.regs = [(r, S(v), None if w is None else S(w)) for r, v, w in form.regs]; g.flags = [(f, S(v), None if w is None else S(w)) for f, v, w in form.flags]
    g.stores = [(S(a), S(v)) for a, v in form.stores]; g.swhen = {i: S(w) for i, w in form.swhen.items()}; g.faults = [(k, S(c)) for k, c in form.faults]; g.undef = dict(form.undef)
    g.eip = S(form.eip); g.flo = None if form.flo is None else S(form.flo); g.pend = {}
    if isinstance(form.addr, N):
        a = S(form.addr); g.addr = a.x if a.op == 'const' else a
    return g

def canon(form): return normalize(form).serialize()

def instantiate(sf, raw, addr):
    """the symbolic Form with the field bytes of the instruction `raw` and its address substituted"""
    env = dict(field_values(raw, sf.fields)); env['eip'] = addr
    return normalize(sf.form, env)

def link_ok(sf, raw, addr):
    """the certificate's link for ONE instruction: canon(forms.build(i, addr)) == canon(symbuild(shape(i))[fields := i, eip := addr]).  -> (equal, why)"""
    ins = list(shp.decoder().disasm(raw, addr))[0]
    if shp.shape_key(ins) != sf.key: return False, 'shape differs'
    return canon(forms.build(ins, addr)) == canon(instantiate(sf, raw, addr)), ''
