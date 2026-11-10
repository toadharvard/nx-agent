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
FAULT_OK = {':SEGMENT-LIMIT-FAIL'}

class ModelGap(Exception):
    """the Sail model does not define the behaviour of this encoding (prove.py reports model-gap)"""

_irsha = None
def ir_sha():
    global _irsha
    if _irsha is None: _irsha = hashlib.sha256(open(IR, 'rb').read()).hexdigest()
    return _irsha

def run_isla(code):
    """raw isla output (stdout, stderr) for one encoding, from the on-disk cache when present"""
    if not os.path.exists(IR): raise RuntimeError('missing %s: run proof/sail_build.sh' % IR)
    args = list(SEG) + sum([['--probe', f] for f in ERRFNS], []) + sum([['-L', f] for f in LINEARIZE], [])
    key = hashlib.sha256(('|'.join([ir_sha(), hashlib.sha256(open(CFG, 'rb').read()).hexdigest(), ' '.join(args), code.hex(), str(ADDR), 'v1'])).encode()).hexdigest()
    os.makedirs(CACHE, exist_ok=True); base = os.path.join(CACHE, key)
    if os.path.exists(base + '.out') and os.path.exists(base + '.err'): return open(base + '.out').read(), open(base + '.err').read()
    cb = '#x' + code.ljust(16, b'\0')[::-1].hex()
    cmd = [ISLA, '-A', IR, '-C', CFG, '-T', '1', '--timeout', '600', 'x86_fetch_decode_execute', '()',
           '-I', 'code_base=#x%012x' % ADDR, '-I', 'code_bytes=' + cb, '-I', 'rip=#x%016x' % ADDR] + args + ['-D', 'p', '--traces', '--error-traces', '-s']
    env = dict(os.environ, LD_LIBRARY_PATH=Z3LIB + ':' + os.environ.get('LD_LIBRARY_PATH', ''))
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
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
            m = re.fullmatch(r'bv(-?\d+)', e[1])
            if not m: raise ModelGap('unsupported constant %s' % (e,))
            return z3.BitVecVal(int(m.group(1)) % (1 << int(e[2])), int(e[2]))
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

def run_path(tag, trace, pre, seg_adj, fault=False):
    env = Env(tag); P = Path(); P.pc = []
    his = {r: z3.BitVec(r + '_hi', 32) for r in GPR}
    rfl_other = z3.BitVec('rflags_other', 32)
    bits = []
    for i in range(31, -1, -1):
        fl = [f for f, b in FLAGBIT.items() if b == i]
        bits.append(pre['flags'][fl[0]] if fl else z3.BitVecVal(1, 1) if i == 1 else z3.Extract(i, i, rfl_other))
    cur = {r: z3.Concat(his[r], pre['regs'][GPR[r]]) for r in GPR}
    cur['rflags'] = z3.Concat(*bits); cur['rip'] = z3.Concat(z3.BitVecVal(0, 32), pre['eip'])
    written = set(); mem = pre['mem']; P.reads = []
    for ev in sexp(trace)[1:]:
        h = ev[0]
        if h == 'declare-const': env.decl(ev[1], ev[2])
        elif h == 'define-const': env.v[ev[1]] = env.ev(ev[2])
        elif h == 'assert': P.pc.append(env.ev(ev[1]))
        elif h in ('branch', 'cycle', 'define-enum', 'branch-address'): pass
        elif h == 'read-reg':
            r = regname(ev[1]); v = unwrap(ev[3])
            if r in cur and v in env.pending and v not in env.v: env.v[v] = cur[r]       # first use of the pre value (a re-read is skipped)
        elif h == 'write-reg':
            r = regname(ev[1]); v = unwrap(ev[3])
            if r == 'Final result': continue
            if r in cur and v is not None: cur[r] = env.ev(v); written.add(r)
            else: raise ModelGap('model writes register %s (outside the proved state)' % r)
        elif h == 'read-mem':
            n = int(ev[4]); a = z3.Extract(31, 0, env.ev(ev[3])) + seg_adj
            env.v[ev[1]] = z3.Concat(*[z3.Select(mem, a + i) for i in reversed(range(n))]) if n > 1 else z3.Select(mem, a)
        elif h == 'write-mem':
            n = int(ev[5]); a = z3.Extract(31, 0, env.ev(ev[3])) + seg_adj; d = env.ev(ev[4])
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

def model_post(code, pre):
    out, err = run_isla(code); blocks = split_blocks(out); ok, bad = [], []
    for hdr, tr in blocks:
        (ok if hdr and hdr.startswith('Result: (_ unit)') else bad).append((hdr, tr))
    if bad:
        msgs = isla_errors(err); first = bad[0][0] or ''
        if ok and msgs and set(msgs) <= FAULT_OK and len(re.findall(r'Calling x86_', err)) == len(bad): faults = [tr for _, tr in bad]; bad = []
        else: faults = []
    else: faults = []
    if bad:
        raise ModelGap('model exception on %d of %d paths: %s' % (len(bad), len(blocks), '; '.join(msgs[:2]) if msgs else first[:80]))
    if not ok: raise ModelGap('isla returned no path: ' + err[-120:].replace('\n', ' '))
    pfx = [b for b in code[:4] if b in (0x64, 0x65)]
    seg_adj = z3.BitVecVal(0, 32) if not pfx else z3.BitVec('fs_base' if pfx[-1] == 0x64 else 'gs_base', 32)
    tagb = code.hex()
    paths = [run_path('p%d' % i, tr, pre, seg_adj) for i, (_, tr) in enumerate(ok)]
    ret = {'regs': {r: merge(paths, lambda p: p.regs[r]) for r in pre['regs']}, 'flags': {f: merge(paths, lambda p: p.flags[f]) for f in FLAGBIT},
            'mem': merge(paths, lambda p: p.mem), 'eip': merge(paths, lambda p: p.eip)}
    if faults: ret['assume'] = z3.Not(z3.Or(*[run_path('f%d' % i, tr, pre, seg_adj, True).pc for i, tr in enumerate(faults)]))
    return ret
