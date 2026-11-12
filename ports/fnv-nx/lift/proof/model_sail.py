#!/usr/bin/env python3
"""Reference-model plug-in for prove.py: the Sail x86 model (ACL2 x86isa translation) executed symbolically by isla.
  model_post(code, pre) -> {'regs','flags','mem','eip'}   (interface: see the docstring of prove.py)

How one instruction is run (details in README.md, section Sail):
  isla-execute-function  -A x86_proof.ir -C x86.toml  x86_fetch_decode_execute '()'  with registers set by -I:
  CS/DS/ES/SS/FS/GS = flat 4 GiB (base 0, limit 0xffffffff, attrs 0xc9a: D/B=1, L=0, P=1, writable), EFER.LMA=0 so the model's
  mode predicate gives Mode_Compatibility (= 32-bit protected mode semantics, default operand/address/stack size 32), app_view
  (flat linear memory, no paging), and the instruction bytes in the register window code_bytes/code_base (splice x86_fetch.sail);
  general registers, rflags and data memory stay symbolic.  Every feasible path of the step function comes back as an isla trace
  (SMT-LIB definitions + register/memory events); the traces are turned into z3 terms over the prover's pre-state variables and
  the paths are merged with If(path condition, ...).  A path that ends in a model exception (unimplemented opcode, #UD, ...)
  makes the encoding a ModelGap: the model does not define the behaviour.
Upper halves of the 64-bit registers are free z3 variables <reg>_hi (rax_hi ...); the results are the low 32 bits, so
a result that depended on an upper half would show up as a prover counter-example, which is the independence check."""
import hashlib, os, re, subprocess, sys
import z3

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get('SAIL_WORK', '/home/user/l3work/proof')
IR = os.environ.get('SAIL_IR', os.path.join(WORK, 'sail', 'x86_proof.ir'))
CFG = os.path.join(HERE, 'x86.toml')
ISLA = os.environ.get('ISLA_EXEC', '/home/user/rems-project/isla/target/release/isla-execute-function')
Z3LIB = os.environ.get('Z3LIB', '/usr/local/lib/python3.13/site-packages/z3/lib')
CACHE = os.path.join(WORK, 'sail_cache')
ADDR = 0x401000
GPR = {'rax': 'eax', 'rcx': 'ecx', 'rdx': 'edx', 'rbx': 'ebx', 'rsp': 'esp', 'rbp': 'ebp', 'rsi': 'esi', 'rdi': 'edi'}
FLAGBIT = {'cf': 0, 'pf': 2, 'af': 4, 'zf': 6, 'sf': 7, 'of': 11}
# functions made branch-free (isla -L) so that flag helpers do not fork; the list is part of the cache key
LINEARIZE = ['bool_to_bits', 'bits_to_bool', 'zf_spec']
# functions whose calls are printed (-D p) so that a model exception can be reported with its message
ERRFNS = ['x86_fault<b>', 'x86_fault<u>', 'x86_model_error<B128>', 'x86_model_error<B16>', 'x86_model_error<B32>',
          'x86_model_error<B48>', 'x86_model_error<B64>', 'x86_model_error<B80>', 'x86_model_error<u>']
SEG = ('-I', 'msrs=[#x0000000000000000; 7]', '-I', 'ctrs=[#x0000000000000000; 17]', '-I', 'seg_visibles=[#x002b; 6]', '-I', 'seg_hidden_attrs=[#x0c9a; 6]',
       '-I', 'seg_hidden_bases=[#x0000000000000000; 6]', '-I', 'seg_hidden_limits=[#xffffffff; 6]', '-I', 'ms_reg=false', '-I', 'fault_reg=false',
       '-I', 'app_view=true', '-I', 'marking_view=false', '-I', 'log_register_writes=false')

# architectural faults the DSL forms do not model (state outside the form's domain): the obligation is restricted to the non-faulting states
FAULT_OK = {':SEGMENT-LIMIT-FAIL', ':OUT-OF-SEGMENT-INSTRUCTION-POINTER'}     # the second: a branch target whose unwrapped (64-bit) value leaves the 4 GiB code segment

class ModelGap(Exception):
    """the Sail model does not define the behaviour of this encoding (prove.py reports model-gap)"""

_irsha = None
def ir_sha():
    global _irsha
    if _irsha is None: _irsha = hashlib.sha256(open(IR, 'rb').read()).hexdigest()
    return _irsha

EIP_LIMIT = 0xfffffff0       # symbolic-eip runs: the instruction (at most 16 bytes) lies below 2^32 (assumption ASM-013)

def sym_constraint(code, ranges, eip_sym):
    """isla --reset-constraint text of a shape run: the instruction bytes outside `ranges` (the symbolic field byte ranges (off, size)) are the bytes of
    `code`, the bytes beyond the instruction are 0 (the window the fetch splice reads from), the field bytes are left free; with eip_sym, code_base = rip
    (the two registers stay symbolic) and rip < EIP_LIMIT"""
    sym = set(i for o, n in ranges for i in range(o, o + n)); cs = []
    for i in range(16):
        if i in sym: continue
        cs.append('(= ((_ extract %d %d) code_bytes) #x%02x)' % (8 * i + 7, 8 * i, code[i] if i < len(code) else 0))
    if eip_sym: cs += ['(= code_base ((_ extract 47 0) rip))', '(bvult rip #x%016x)' % EIP_LIMIT]
    return '(and %s)' % ' '.join(cs)

SEG_FREE = tuple(x for i, x in enumerate(SEG) if x != 'seg_visibles=[#x002b; 6]' and not (i + 1 < len(SEG) and SEG[i + 1] == 'seg_visibles=[#x002b; 6]'))
SYMSEL = False      # set by prove.prove_one: the Form reads the segment selectors (sel_*), so the six selectors of the initial state are free (isla gives each element a fresh symbol) instead of the literal 0x2b
SELREG = ('es', 'cs', 'ss', 'ds', 'fs', 'gs')      # isla prints the register seg_visibles (vector(6, dec, bits(16))) as (_ vec v0 .. v5), element i = rt/fnvl.h SEG_* i (checked: push ds / es / gs proved only in this order)

def run_isla(code, timeout=600, sym=None):
    """raw isla output (stdout, stderr) for one encoding, from the on-disk cache when present.  sym = (ranges, eip_sym): a SHAPE run, see sym_constraint"""
    if not os.path.exists(IR): raise RuntimeError('missing %s: run ports/fnv-nx/farm/sail_build.sh' % IR)
    args = list(SEG_FREE if SYMSEL else SEG) + sum([['--probe', f] for f in ERRFNS], []) + sum([['-L', f] for f in LINEARIZE], [])
    cons = sym_constraint(code, *sym) if sym else ''
    key = hashlib.sha256(('|'.join([ir_sha(), hashlib.sha256(open(CFG, 'rb').read()).hexdigest(), ' '.join(args), code.hex(), str(ADDR), 'v1'] + ([str(timeout)] if timeout != 600 else []) + ([cons] if sym else []))).encode()).hexdigest()
    os.makedirs(CACHE, exist_ok=True); base = os.path.join(CACHE, key)
    if os.path.exists(base + '.out') and os.path.exists(base + '.err'): return open(base + '.out').read(), open(base + '.err').read()
    if sym:
        regs = [] if sym[1] else ['-I', 'code_base=#x%012x' % ADDR, '-I', 'rip=#x%016x' % ADDR]
        regs += ['--reset-constraint', cons]
    else: regs = ['-I', 'code_base=#x%012x' % ADDR, '-I', 'code_bytes=#x' + code.ljust(16, b'\0')[::-1].hex(), '-I', 'rip=#x%016x' % ADDR]
    cmd = [ISLA, '-A', IR, '-C', CFG, '-T', '1', '--timeout', str(timeout), 'x86_fetch_decode_execute', '()'] + regs + args + ['-D', 'p', '--traces', '--error-traces', '-s']
    env = dict(os.environ, LD_LIBRARY_PATH=Z3LIB + ':' + os.environ.get('LD_LIBRARY_PATH', ''))
    try: r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout + 20)     # isla's own --timeout is not honoured while z3 is busy
    except subprocess.TimeoutExpired:
        return 'Error Timeout\n(trace\n  (write-reg |Final result| nil (_ poison)))\n', 'hard timeout %ds\n' % (timeout + 20)
    if r.returncode not in (0, 1): raise RuntimeError('isla failed (%d): %s' % (r.returncode, r.stderr[-400:]))
    open(base + '.out.tmp', 'w').write(r.stdout); open(base + '.err.tmp', 'w').write(r.stderr)
    os.replace(base + '.err.tmp', base + '.err'); os.replace(base + '.out.tmp', base + '.out')
    return r.stdout, r.stderr

# ---------------------------------------------------------------------------------------------- s-expressions
TOK = re.compile(r'\(|\)|\|[^|]*\||"[^"]*"|[^\s()]+')
def sexp(text):
    """parse one s-expression (comments are removed first; none of the strings contains a semicolon)"""
    toks = TOK.findall(re.sub(r';[^\n]*', '', text)); pos = 0
    def go():
        nonlocal pos
        t = toks[pos]; pos += 1
        if t != '(': return t
        l = []
        while toks[pos] != ')': l.append(go())
        pos += 1; return l
    return go()

def split_blocks(out):
    """[(header line, trace text)] from isla-execute-function output"""
    res, hdr, buf, depth = [], None, [], 0
    for line in out.split('\n'):
        if depth == 0:
            if line.startswith('(trace'): buf = [line]; depth = line.count('(') - line.count(')')
            elif line.strip(): hdr = line
            if depth == 0 and buf: res.append((hdr, '\n'.join(buf))); buf = []
        else:
            buf.append(line); depth += re.sub(r';.*', '', line).count('(') - re.sub(r';.*', '', line).count(')')
            if depth == 0: res.append((hdr, '\n'.join(buf))); buf = []
    return res

def isla_errors(err):
    return sorted(set(re.sub(r'\\+"', '"', m).strip('"') for m in re.findall(r'Calling x86_(?:fault|model_error)<\w+>\(\["(.*?)"\]\)', err)))

# ---------------------------------------------------------------------------------------------- SMT-LIB subset -> z3
BV = z3.BitVecSort
def bvlit(tok):
    if tok.startswith('#x'): return z3.BitVecVal(int(tok[2:], 16), 4 * (len(tok) - 2))
    if tok.startswith('#b'): return z3.BitVecVal(int(tok[2:], 2), len(tok) - 2)
    raise ModelGap('unsupported literal ' + tok)
def sort_of(s):
    if s == 'Bool': return z3.BoolSort()
    if isinstance(s, list) and s[0] == '_' and s[1] == 'BitVec': return BV(int(s[2]))
    raise ModelGap('unsupported sort %s' % (s,))
BIN = {'bvadd': lambda a, b: a + b, 'bvsub': lambda a, b: a - b, 'bvmul': lambda a, b: a * b, 'bvand': lambda a, b: a & b, 'bvor': lambda a, b: a | b,
       'bvxor': lambda a, b: a ^ b, 'bvshl': lambda a, b: a << b, 'bvlshr': z3.LShR, 'bvashr': lambda a, b: a >> b, 'bvudiv': z3.UDiv, 'bvurem': z3.URem,
       'bvsdiv': lambda a, b: a / b, 'bvsrem': z3.SRem, 'bvsmod': lambda a, b: z3.SRem(a, b) if False else _smod(a, b),
       'bvult': z3.ULT, 'bvule': z3.ULE, 'bvugt': z3.UGT, 'bvuge': z3.UGE, 'bvslt': lambda a, b: a < b, 'bvsle': lambda a, b: a <= b,
       'bvsgt': lambda a, b: a > b, 'bvsge': lambda a, b: a >= b, 'bvnand': lambda a, b: ~(a & b), 'bvnor': lambda a, b: ~(a | b),
       'bvxnor': lambda a, b: ~(a ^ b), 'bvcomp': lambda a, b: z3.If(a == b, z3.BitVecVal(1, 1), z3.BitVecVal(0, 1)), '=>': z3.Implies,
       'concat': z3.Concat, 'xor': z3.Xor}
def _smod(a, b):                      # SMT-LIB bvsmod
    r = z3.SRem(a, b); z = z3.BitVecVal(0, a.size())
    return z3.If(b == z, a, z3.If(r == z, r, z3.If((r < z) == (b < z), r, r + b)))

class Env:
    """evaluator for the SMT-LIB subset isla emits; names map to z3 terms"""
    def __init__(self, tag): self.v = {}; self.pending = {}; self.tag = tag
    def decl(self, name, sort): self.pending[name] = sort
    def fresh(self, name):
        if name not in self.v: self.v[name] = z3.Const('%s_%s' % (self.tag, name), sort_of(self.pending[name]))
        return self.v[name]
    def ev(self, e):
        if isinstance(e, str):
            if e == 'true': return z3.BoolVal(True)
            if e == 'false': return z3.BoolVal(False)
            if e[0] == '#': return bvlit(e)
            if e in self.v: return self.v[e]
            if e in self.pending: return self.fresh(e)
            raise ModelGap('unbound symbol %s' % e)
        h = e[0]
        if isinstance(h, list):                        # indexed operator
            assert h[0] == '_'; op = h[1]; a = [self.ev(x) for x in e[1:]]
            if op == 'extract': return z3.Extract(int(h[2]), int(h[3]), a[0])
            if op == 'zero_extend': return z3.ZeroExt(int(h[2]), a[0]) if int(h[2]) else a[0]
            if op == 'sign_extend': return z3.SignExt(int(h[2]), a[0]) if int(h[2]) else a[0]
            if op == 'rotate_left': return z3.RotateLeft(a[0], int(h[2]))
            if op == 'rotate_right': return z3.RotateRight(a[0], int(h[2]))
            if op == 'repeat': return z3.RepeatBitVec(int(h[2]), a[0])
            raise ModelGap('unsupported indexed op ' + op)
        if h == '_':                                    # (_ bvN w)
            digits = e[1][3:] if e[1].startswith('bv-') else e[1][2:] if e[1].startswith('bv') else ''      # bv<N> | bv-<N> (decimal digits)
            if not digits.isdecimal(): raise ModelGap('unsupported constant %s' % (e,))
            return z3.BitVecVal(int(e[1][2:]) % (1 << int(e[2])), int(e[2]))
        a = [self.ev(x) for x in e[1:]]
        if h == 'ite': return z3.If(a[0], a[1], a[2])
        if h == 'not': return z3.Not(a[0]) if z3.is_bool(a[0]) else ~a[0]
        if h == 'and': return z3.And(*a)
        if h == 'or': return z3.Or(*a)
        if h == '=': return a[0] == a[1]
        if h == 'distinct': return z3.Distinct(*a)
        if h == 'bvneg': return -a[0]
        if h == 'bvnot': return ~a[0]
        if h == 'select': return z3.Select(a[0], a[1])
        if h == 'store': return z3.Store(a[0], a[1], a[2])
        if h in BIN:
            r = a[0]
            for x in a[1:]: r = BIN[h](r, x)
            return r
        raise ModelGap('unsupported SMT operator %s' % h)

def regname(x):
    return x.strip('|')
def unwrap(v):
    """value of a register event: a symbol/literal, or (_ struct (|bits| X)) -> X, else None"""
    if isinstance(v, str): return v
    if isinstance(v, list) and v[0] == '_' and v[1] == 'struct' and len(v) == 3 and isinstance(v[2], list) and len(v[2]) == 2: return v[2][1]
    return None

# ---------------------------------------------------------------------------------------------- one path -> z3
class Path: pass

def consts(t):
    """names of the uninterpreted constants in a z3 term"""
    seen, out, st = set(), set(), [t]
    while st:
        e = st.pop()
        if e.get_id() in seen: continue
        seen.add(e.get_id())
        if z3.is_const(e) and e.decl().kind() == z3.Z3_OP_UNINTERPRETED: out.add(e.decl().name())
        st.extend(e.children())
    return out

def run_path(tag, trace, pre, fault=False, extra=None):
    env = Env(tag); P = Path(); P.pc = []; P.side = []
    his = {r: z3.BitVec(r + '_hi', 32) for r in GPR}
    rfl_other = z3.BitVec('rflags_other', 32)
    bits = []
    for i in range(31, -1, -1):
        fl = [f for f, b in FLAGBIT.items() if b == i]
        bits.append(pre['flags'][fl[0]] if fl else z3.BitVecVal(1, 1) if i == 1 else z3.Extract(i, i, rfl_other))
    cur = {r: z3.Concat(his[r], pre['regs'][GPR[r]]) for r in GPR}
    cur['rflags'] = z3.Concat(*bits); cur['rip'] = z3.Concat(z3.BitVecVal(0, 32), pre['eip'])
    cur['fs_base_reg'] = z3.BitVec('fs_base', 32); cur['gs_base_reg'] = z3.BitVec('gs_base', 32)      # the symbolic segment bases (sail_patches/segment_bases.sail): each access carries its own segment's base
    cur.update(extra or {})                          # shape runs: code_bytes / code_base are symbolic registers bound to terms over the field variables
    written = set(); mem = pre['mem']; P.reads = []
    for ev in sexp(trace)[1:]:
        h = ev[0]
        if h == 'declare-const': env.decl(ev[1], ev[2])
        elif h == 'define-const': env.v[ev[1]] = env.ev(ev[2])
        elif h == 'assert':
            t = env.ev(ev[1]); fv = consts(t)
            if fv and all(c.startswith(tag + '_') for c in fv): P.side.append(t)       # constrains only a free (undefined/nondeterministic) value
            else: P.pc.append(t)
        elif h in ('branch', 'cycle', 'define-enum', 'branch-address'): pass
        elif h == 'assume': pass                      # the --reset-constraint of a shape run: holds by construction of `extra` (sym_constraint)
        elif h == 'read-reg':
            r = regname(ev[1]); v = unwrap(ev[3])
            if r == 'seg_visibles' and isinstance(ev[3], list) and ev[3][:2] == ['_', 'vec']:       # free selectors: element i of the pre-state = the DSL variable sel_<segment>
                for s, e in zip(SELREG, ev[3][2:]):
                    if e in env.pending and e not in env.v: env.v[e] = z3.BitVec('sel_' + s, 16)
                continue
            if r in cur and v in env.pending and v not in env.v: env.v[v] = cur[r]       # first use of the pre value (a re-read is skipped)
        elif h == 'write-reg':
            r = regname(ev[1]); v = unwrap(ev[3])
            if r == 'Final result': continue
            if r in cur and v is not None: cur[r] = env.ev(v); written.add(r)
            else: raise ModelGap('model writes register %s (outside the proved state)' % r)
        elif h == 'read-mem':
            n = int(ev[4]); a = z3.Extract(31, 0, env.ev(ev[3]))
            env.v[ev[1]] = z3.Concat(*[z3.Select(mem, a + i) for i in reversed(range(n))]) if n > 1 else z3.Select(mem, a)
        elif h == 'write-mem':
            n = int(ev[5]); a = z3.Extract(31, 0, env.ev(ev[3])); d = env.ev(ev[4])
            for i in range(n): mem = z3.Store(mem, a + i, z3.Extract(8 * i + 7, 8 * i, d))
            env.v[ev[1]] = z3.BoolVal(True)
        else: raise ModelGap('unsupported isla event ' + h)
    if not fault and 'rip' not in written: raise ModelGap('model does not update rip')
    P.fault = fault
    if fault: P.pc = z3.And(*P.pc) if P.pc else z3.BoolVal(True); return P
    P.regs = {GPR[r]: z3.Extract(31, 0, cur[r]) for r in GPR}
    P.flags = {f: z3.Extract(b, b, cur['rflags']) for f, b in FLAGBIT.items()}
    P.mem = mem; P.eip = z3.Extract(31, 0, cur['rip']); P.pc = z3.And(*P.pc) if P.pc else z3.BoolVal(True)
    return P

def merge(paths, pick):
    r = pick(paths[-1])
    for p in reversed(paths[:-1]): r = z3.If(p.pc, pick(p), r)
    return r

class ModelTimeout(Exception):
    """isla did not finish within the timeout (prove-side result class `timeout`)"""
LAST = {}

def model_post(code, pre):
    return _post(code, pre, run_isla(code), None)

def model_post_shape(code, fields, pre, eip_sym=True, timeout=600):
    """model_post for a whole encoding shape: `code` = representative bytes, fields = {name: (offset, size) | None} (imm / disp byte ranges), the bytes of
    the fields are the z3 variables fld_<name> (BitVec 8*size), eip is pre['eip'] (a variable when eip_sym: then code_base = rip = pre['eip'] and the
    assumption eip < EIP_LIMIT (ASM-013) is part of `assume`; otherwise the constant 0x401000, assumption A-EIP in the certificate)"""
    rg = [(o, n) for o, n in (f for f in fields.values() if f)]; var = {nm: z3.BitVec('fld_' + nm, 8 * f[1]) for nm, f in fields.items() if f}
    byte = {}
    for nm, (o, n) in ((nm, f) for nm, f in fields.items() if f):
        for k in range(n): byte[o + k] = z3.Extract(8 * k + 7, 8 * k, var[nm])
    cb = z3.Concat(*[byte.get(i, z3.BitVecVal(code[i] if i < len(code) else 0, 8)) for i in reversed(range(16))])
    extra = {'code_bytes': cb, 'code_base': z3.ZeroExt(16, pre['eip'])}
    out = run_isla(code, timeout, (tuple(rg), eip_sym))
    r = _post(code, pre, out, extra, z3.ULT(pre['eip'], z3.BitVecVal(EIP_LIMIT, 32)) if eip_sym else None)
    if eip_sym:                                      # terms of the dict, flattened, rewritten, put back
        flat = [(k, None, v) for k, v in r.items() if not isinstance(v, dict) and k not in ('domain', 'complete')] + [(k, n, x) for k, v in r.items() if isinstance(v, dict) for n, x in v.items()]
        new = eip_nowrap([x for *_, x in flat], pre['eip']); r2 = {k: ({} if isinstance(v, dict) else v) for k, v in r.items()}
        for (k, n, _), t in zip(flat, new):
            if n is None: r2[k] = t
            else: r2[k][n] = t
        r = r2
    return r

def zext_base(t):
    """t = zero extension of a narrower term e (z3 ZeroExt, or Concat with a leading all-zero constant) -> (e, width of e), else None"""
    if not z3.is_bv(t): return None
    k = t.decl().kind(); ch = t.children()
    if k == z3.Z3_OP_ZERO_EXT: return ch[0], ch[0].size()
    if k == z3.Z3_OP_CONCAT and len(ch) == 2 and z3.is_bv_value(ch[0]) and ch[0].as_long() == 0: return ch[1], ch[1].size()
    return None

def zext_add_pairs(terms):
    """{id: (term, replacement)} for the sub-terms  Z_V(c + Z_W(e))  (Z = zero extension, c a constant, e of n < W bits, c < 2^W - 2^n).  Identity (unconditional):
    Z_W(e) < 2^n and c < 2^W - 2^n, so the W-bit sum cannot wrap, hence the zero extension of the sum is the sum of the zero extensions at any V >= W:
        Z_V(c + Z_W(e))  =  c + Z_V(e).
    The model computes instruction-fetch offsets as (code_base + K) - (rip + K) in 48/64/128 bits; without this identity z3 does not see that the difference is
    the constant K and bit-blasts the 128-bit shifts of code_bytes by a symbolic amount."""
    pairs, seen, st = {}, set(), list(terms)
    while st:
        t = st.pop()
        if t.get_id() in seen: continue
        seen.add(t.get_id()); ch = t.children(); st.extend(ch)
        zb = zext_base(t)
        if zb is None or not z3.is_bv(zb[0]) or zb[0].decl().kind() != z3.Z3_OP_BADD or zb[0].num_args() != 2: continue
        x = zb[0]; a, b = x.arg(0), x.arg(1)
        if z3.is_bv_value(a): a, b = b, a
        inner = zext_base(a)
        if inner is None or not z3.is_bv_value(b): continue
        e, n = inner; W = x.size(); V = t.size()
        if n < W and b.as_long() < (1 << W) - (1 << n): pairs[t.get_id()] = (t, z3.ZeroExt(V - n, e) + z3.BitVecVal(b.as_long(), V))
    return pairs

def eip_nowrap(terms, eip):
    """rewrite of model terms under the domain eip < EIP_LIMIT (every query of a shape run assumes it): the 32-bit sum eip + K (K a small constant: the
    offsets of the bytes of ONE instruction) zero-extended to a wider sort equals the wide sum zext(eip) + K, because eip + K < 2^32 cannot wrap.
    Without it z3 cannot see that the fetch index (rip + offset) - code_base is a constant and bit-blasts the 128-bit shifts of code_bytes.
    Also the unconditional zext_add_pairs identity.  terms -> simplified terms with every `zero_extend(eip + K)` / `concat(0, eip + K)` replaced"""
    terms = [z3.simplify(t) for t in terms]
    while True:                                      # each rewrite can expose the other's pattern; terminates: every round removes a zero-extended sum
        pairs, seen, st = {}, set(), list(terms)
        while st:
            t = st.pop()
            if t.get_id() in seen: continue
            seen.add(t.get_id()); ch = t.children(); st.extend(ch)
            k = t.decl().kind()
            if k in (z3.Z3_OP_CONCAT, z3.Z3_OP_ZERO_EXT) and z3.is_bv(t) and (len(ch) == 1 or (z3.is_bv_value(ch[0]) and ch[0].as_long() == 0)):
                x = ch[-1]
                if z3.is_bv(x) and x.decl().kind() == z3.Z3_OP_BADD and x.num_args() == 2:
                    a, b = x.arg(0), x.arg(1)
                    if z3.is_bv_value(a): a, b = b, a
                    if a.eq(eip) and z3.is_bv_value(b) and 0 <= b.as_long() <= 16:
                        pairs[t.get_id()] = (t, z3.ZeroExt(t.size() - 32, eip) + z3.BitVecVal(b.as_long(), t.size()))
        for i, pr in zext_add_pairs(terms).items(): pairs.setdefault(i, pr)
        if not pairs: return terms
        terms = [z3.simplify(z3.substitute(t, *pairs.values())) for t in terms]

def _post(code, pre, out_err, extra, extra_assume=None):
    out, err = out_err; blocks = split_blocks(out); ok, bad = [], []
    for hdr, tr in blocks:
        (ok if hdr and hdr.startswith('Result: (_ unit)') else bad).append((hdr, tr))
    if bad:
        msgs = isla_errors(err); first = bad[0][0] or ''
        if (ok or getattr(FAULT_OK, 'declared', False)) and msgs and set(msgs) <= FAULT_OK and len(re.findall(r'Calling x86_', err)) == len(bad): faults = [tr for _, tr in bad]; bad = []
        else: faults = []
    else: faults = []
    if bad and extra is not None and (any((h or '').startswith('Error Timeout') for h, _ in bad) or 'hard timeout' in err): raise ModelTimeout(err[-120:].replace('\n', ' '))
    if bad:
        raise ModelGap('model exception on %d of %d paths: %s' % (len(bad), len(blocks), '; '.join(msgs[:2]) if msgs else first[:80]))
    if not ok and not faults: raise ModelGap('isla returned no path: ' + err[-120:].replace('\n', ' '))
    tagb = code.hex()
    paths = [run_path('p%d' % i, tr, pre, False, extra) for i, (_, tr) in enumerate(ok)]
    if not paths:       # every path of the model faults, with faults the form declares (prove.Perm): the model never completes, so there is no post state to compare (prove.py requires that the DSL then always faults)
        ret = {'regs': dict(pre['regs']), 'flags': dict(pre['flags']), 'mem': pre['mem'], 'eip': pre['eip'], 'complete': False}
    else: ret = {'regs': {r: merge(paths, lambda p: p.regs[r]) for r in pre['regs']}, 'flags': {f: merge(paths, lambda p: p.flags[f]) for f in FLAGBIT},
            'mem': merge(paths, lambda p: p.mem), 'eip': merge(paths, lambda p: p.eip)}
    asm = [s for p in paths for s in p.side]
    LAST.clear(); LAST.update(paths=len(ok), fault_paths=len(faults))
    side = list(asm); nofault = None
    if faults: nofault = z3.Not(z3.Or(*[run_path('f%d' % i, tr, pre, True, extra).pc for i, tr in enumerate(faults)])); asm.append(nofault)
    if asm: ret['assume'] = z3.And(*asm)
    ret['side'] = z3.And(*side) if side else z3.BoolVal(True)       # closed constraints on free values (undefined / nondeterministic results): satisfiable for every pre-state iff satisfiable at all
    ret['nofault'] = nofault if nofault is not None else z3.BoolVal(True)       # the pre-state conditions under which the model does not fault
    if extra_assume is not None: ret['domain'] = extra_assume
    return ret
