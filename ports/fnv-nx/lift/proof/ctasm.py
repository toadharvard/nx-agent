#!/usr/bin/env python3
"""ctasm: the two C++/GNU constructs of the lifted code's memory boundary that pycparser cannot parse, read from the runtime's own text (rt/fnvl.h), for ctrans.

1. `__asm__ volatile("template" : outputs : inputs : clobbers)` (GCC manual, Extended Asm).  `convert_asm` turns each statement into the call `__asm_N(out exprs..., in exprs...)`; N indexes
   ASMS, the record of the statement (template string, operand names and constraints).  ctrans executes the call by `run_asm`: the A64 instructions of the template, one by one, with the
   semantics of the Arm ARM (DDI 0487) for exactly the mnemonics MNEMONICS lists; any other mnemonic is Unsupported (the function is refused, no instruction is guessed).
2. The guest memory accessors: `GM<T>{a}`, `GMF<F,T>{a}`, `GMK<T,A>{}`, `GMKF<F,T,A>{}` (the classes of rt/fnvl.h, which the access macros M8..MF64, MK8..MKF64 expand to).  `accessor_c` reads the
   class definitions from the preprocessed header text and writes each operator as a C function `NAME_ld(a)` / `NAME_st(a, v)` (NAME = class and type arguments, e.g. GM_u32, GMF_float_u32); `rewrite`
   turns a class temporary `CLS<args>{init}[.g(lkwin_)]` into the call `NAME(init)`, and `macro_defs` does the same for the macro bodies.  ctrans reads the call `NAME(x)` as `NAME_ld(x)` and the
   assignment `NAME(x) = v` as `NAME_st(x, v)`: the operator body is the only definition of what the access does, and its value effect is the asm it contains.
"""
import re
import z3

ASMS = []
MNEMONICS = ('mov', 'movz', 'movk', 'tst', 'b', 'b.ne', 'b.eq', 'ldar', 'ldarb', 'ldarh', 'ldr', 'ldrb', 'ldrh', 'stlr', 'stlrb', 'stlrh', 'str', 'strb', 'strh', 'dmb', 'bfxil')


def _match(t, i, o, c):
    """index of the bracket that closes the one at t[i] (strings and char constants skipped)"""
    d, n = 0, len(t)
    while i < n:
        ch = t[i]
        if ch == '"':
            i += 1
            while t[i] != '"': i += 2 if t[i] == '\\' else 1
        elif ch == o: d += 1
        elif ch == c:
            d -= 1
            if d == 0: return i
        i += 1
    raise ValueError('unbalanced ' + o)


def _split_top(t, sep):
    out, d, cur, i = [], 0, [], 0
    while i < len(t):
        ch = t[i]
        if ch == '"':
            j = i + 1
            while t[j] != '"': j += 2 if t[j] == '\\' else 1
            cur.append(t[i:j + 1]); i = j + 1; continue
        if ch in '([{': d += 1
        elif ch in ')]}': d -= 1
        if ch == sep and d == 0: out.append(''.join(cur)); cur = []
        else: cur.append(ch)
        i += 1
    out.append(''.join(cur))
    return out


def _strings(t):
    """the concatenation of the adjacent string literals of t (C11 5.1.1.2 phase 6), escapes \\n \\t \\\\ \\" decoded"""
    s = ''
    for m in re.finditer(r'"((?:[^"\\]|\\.)*)"', t):
        s += re.sub(r'\\(.)', lambda k: {'n': '\n', 't': '\t'}.get(k.group(1), k.group(1)), m.group(1))
    return s


def convert_asm(text):
    """every `__asm__ [volatile] (...)` statement of text -> `__asm_N(outs..., ins...)`"""
    out, i = [], 0
    while True:
        j = text.find('__asm__', i)
        if j < 0: out.append(text[i:]); break
        out.append(text[i:j]); k = j + len('__asm__')
        m = re.compile(r'\s*(?:volatile\s*)?').match(text, k); k = m.end()
        if text[k] != '(': raise ValueError('asm without parenthesis')
        e = _match(text, k, '(', ')'); body = text[k + 1:e]
        parts = _split_top(body, ':')
        tmpl = _strings(parts[0]); ops = []
        for sec, kind in ((1, 'out'), (2, 'in')):
            if len(parts) > sec and parts[sec].strip():
                for o in _split_top(parts[sec], ','):
                    mm = re.match(r'\s*(?:\[(\w+)\])?\s*"([^"]*)"\s*\((.*)\)\s*$', o, re.S)
                    if not mm: raise ValueError('asm operand ' + o)
                    ops.append((kind, mm.group(1), mm.group(2), mm.group(3)))
        n = len(ASMS); ASMS.append(dict(template=tmpl, ops=[(k_, nm, c_) for k_, nm, c_, _ in ops]))
        out.append('__asm_%d(%s)' % (n, ', '.join(x[3] for x in ops))); i = e + 1
    return ''.join(out)


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# the accessor classes
CLASSES = ('GMKF', 'GMK', 'GMF', 'GM')


def _flat(cls, targs):
    return '_'.join([cls] + [re.sub(r'\W', '', a) for a in targs])


def rewrite(text):
    """class temporaries of the accessor classes -> calls NAME(init): see the module docstring"""
    out, i, n = [], 0, len(text)
    pat = re.compile(r'\b(GMKF|GMK|GMF|GM)\s*<')
    while True:
        m = pat.search(text, i)
        if not m: out.append(text[i:]); break
        out.append(text[i:m.start()])
        lt = m.end() - 1
        d, k = 0, lt
        while True:      # the template-argument list: the matching `>` (arguments hold parentheses but no comparison)
            ch = text[k]
            if ch == '(': k = _match(text, k, '(', ')')
            elif ch == '<': d += 1
            elif ch == '>':
                d -= 1
                if d == 0: break
            k += 1
        targs = [a.strip() for a in _split_top(text[lt + 1:k], ',')]
        j = k + 1
        mm = re.compile(r'\s*\{').match(text, j)
        if not mm: out.append(text[m.start():k + 1]); i = k + 1; continue      # not a temporary (a declaration, a type use)
        b = mm.end() - 1; e = _match(text, b, '{', '}'); init = text[b + 1:e].strip(); j = e + 1
        cls = m.group(1)
        if cls in ('GMK', 'GMKF'): name, arg = _flat(cls, targs[:-1]), targs[-1]
        else: name, arg = _flat(cls, targs), init
        mg = re.compile(r'\s*\.g\(lkwin_\)').match(text, j)
        if mg: j = mg.end()
        out.append('%s(%s)' % (name, arg)); i = j
    return ''.join(out)


def macro_defs(text, names=None):
    """the `#define NAME(args) body` lines of text (the header, comments removed) whose body holds an accessor temporary: -> {NAME: (params, rewritten body)}"""
    r = {}
    for m in re.finditer(r'^[ \t]*#define[ \t]+(\w+)\(([^)]*)\)[ \t]*(.*)$', text.replace('\\\n', ' '), re.M):
        if re.search(r'\b(GMKF|GMK|GMF|GM)\s*<', m.group(3)) and not re.search(r'\bstruct\b', m.group(3)): r[m.group(1)] = (m.group(2), rewrite(m.group(3)))
    return r


def _struct_body(o, head):
    i = o.index(head); b = o.index('{', i); return o[b + 1:_match(o, b, '{', '}')]


def accessor_c(o, uses):
    """the C functions of the accessor instances USES (the set of (class, [type args]) that the macro bodies name, from `rewrite_names`), from the preprocessed header text O"""
    out = []
    for cls, targs in sorted(uses):
        if cls == 'GM': body = _struct_body(o, 'template<> struct GM<%s>' % targs[0]); a = 'a'
        elif cls == 'GMK': body = _struct_body(o, 'template<u32 A> struct GMK<%s, A>' % targs[0]); a = 'A'
        elif cls == 'GMF':
            body = _struct_body(o, 'template<class F,class T> struct GMF'); a = 'a'
            body = re.sub(r'\bF\b', targs[0], re.sub(r'\bT\b', targs[1], body))
        else:
            body = _struct_body(o, 'template<class F,class T,u32 A> struct GMKF'); a = 'A'
            body = re.sub(r'\bF\b', targs[0], re.sub(r'\bT\b', targs[1], body))
        name = _flat(cls, targs)
        for mm in re.finditer(r'operator', body):
            s = mm.start(); k = mm.end()
            if body[k] == '=':      # R operator=(V v) const { ... }: the value store (the copy assignment `const X &o` is the load followed by this store, and is not used by the emitters)
                p = body.index('(', k); q = _match(body, p, '(', ')'); par = body[p + 1:q]
                if '&' in par: continue
                ret = re.search(r'(\w+)\s*$', body[:s]).group(1); pt = par.split()[0]
                b = body.index('{', q); e = _match(body, b, '{', '}')
                out.append('%s %s_st(u32 %s, %s v) %s' % (ret, name, a, pt, rewrite(body[b:e + 1])))
            else:
                p = body.index('(', k); ty = body[k:p].strip(); q = _match(body, p, '(', ')')
                b = body.index('{', q); e = _match(body, b, '{', '}')
                out.append('%s %s_ld(u32 %s) %s' % (ty, name, a, rewrite(body[b:e + 1])))
    return '\n'.join(out) + '\n'


def rewrite_names(text):
    """the accessor instances (class, [type args]) that text holds"""
    uses = set()
    for m in re.finditer(r'\b(GMKF|GMK|GMF|GM)\s*<', text):
        k = m.end() - 1; d = 0
        while True:
            ch = text[k]
            if ch == '(': k = _match(text, k, '(', ')')
            elif ch == '<': d += 1
            elif ch == '>':
                d -= 1
                if d == 0: break
            k += 1
        t = [a.strip() for a in _split_top(text[m.end():k], ',')]
        uses.add((m.group(1), tuple(t[:-1] if m.group(1) in ('GMK', 'GMKF') else t)))
    return uses


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# the asm executor (ctrans.Machine calls it)
def _bv(m, v, w):
    """the z3 bit-vector of ctrans value v, as a w-bit register (a narrower value is zero-extended: the upper bits of a 64-bit register hold the value of the C type, whatever the C type's own bits are)"""
    e = v.e
    return e if e.size() == w else (z3.ZeroExt(w - e.size(), e) if e.size() < w else z3.Extract(w - 1, 0, e))


def run_asm(m, fr, n, args, ctrans):
    """execute ASMS[n] on the argument values; writes the output operands back.  The A64 semantics are the Arm ARM's for each MNEMONIC."""
    rec = ASMS[n]; regs, imm, outs = {}, {}, []
    for (kind, name, con), a in zip(rec['ops'], args):
        if 'i' in con and kind == 'in':
            imm[name] = z3.simplify(a.e); continue
        w = 64
        if kind == 'out' and '+' not in con:
            regs[name] = z3.BitVec('asm%d_%s_%d' % (n, name, m.fresh), 64); m.fresh += 1      # an output before its first write: any value
        else:
            hi = z3.BitVec('asm%d_%s_hi_%d' % (n, name, m.fresh), 32); m.fresh += 1
            regs[name] = _bv(m, a, 64) if a.e.size() == 64 else z3.Concat(hi, a.e)      # C passes a 32-bit value in a register whose upper 32 bits are unspecified
        if kind == 'out': outs.append((name, a))
    tmpl = rec['template']
    lines = []
    for ln in tmpl.replace(';', '\n').split('\n'):
        ln = ln.strip()
        if not ln: continue
        lab = re.match(r'(\d+):\s*(.*)$', ln)
        if lab: lines.append(('label', lab.group(1))); ln = lab.group(2).strip()
        if ln: lines.append(('ins', ln))
    labels = {l[1]: i for i, l in enumerate(lines) if l[0] == 'label'}

    def sub(t):
        def f(mm):
            v, nm = mm.group(1), mm.group(2)
            if nm in imm: return '#%d' % imm[nm].as_long()
            return '%s:%s' % (v or 'w', nm)
        return re.sub(r'%([wx]?)\[(\w+)\]', f, t)

    def reg(t, st):
        mm = re.fullmatch(r'\[?([wx]):(\w+)\]?', t)
        if mm: return mm.group(1), mm.group(2)
        raise ctrans.Unsupported('asm operand ' + t)

    def val(t, st):
        t = t.strip()
        if t.startswith('#'): return int(t[1:], 0)
        if re.fullmatch(r'-?\d+|0x[0-9a-fA-F]+', t): return int(t, 0)
        w, nm = reg(t, st); r = st['regs'][nm]
        return z3.Extract(31, 0, r) if w == 'w' else r

    def setreg(st, t, e):
        w, nm = reg(t, st)
        st['regs'][nm] = z3.ZeroExt(64 - e.size(), e) if e.size() < 64 else e      # a write of a W register zeroes bits 63:32 (Arm ARM, "Register width in AArch64")

    def mem_addr(t, st):
        mm = re.fullmatch(r'\[(x:\w+)\]', t.strip())
        if not mm: raise ctrans.Unsupported('asm address ' + t)
        return st['regs'][reg(mm.group(1), st)[1]]

    SZ = {'': None, 'b': 1, 'h': 2}

    def step(st, pc, cond, results):
        while pc < len(lines):
            k, x = lines[pc]; pc += 1
            if k == 'label': continue
            mn, _, rest = x.partition(' '); mn = mn.strip().lower()
            ops = [o.strip() for o in _split_top(sub(rest), ',')] if rest.strip() else []
            if mn not in MNEMONICS: raise ctrans.Unsupported('asm mnemonic ' + mn)
            if mn == 'mov': setreg(st, ops[0], val(ops[1], st) if not isinstance(val(ops[1], st), int) else z3.BitVecVal(val(ops[1], st), 32))
            elif mn == 'movz':
                sh = int(ops[2].split()[-1]) if len(ops) > 2 else 0
                setreg(st, ops[0], z3.BitVecVal(val(ops[1], st) << sh, 32))
            elif mn == 'movk':
                sh = int(ops[2].split()[-1]) if len(ops) > 2 else 0
                cur = val(ops[0], st); mask = 0xffffffff & ~(0xffff << sh)
                setreg(st, ops[0], (cur & z3.BitVecVal(mask, 32)) | z3.BitVecVal(val(ops[1], st) << sh, 32))
            elif mn == 'tst':
                a = val(ops[0], st); b = val(ops[1], st); b = z3.BitVecVal(b, 32) if isinstance(b, int) else b
                st['Z'] = (a & b) == 0
            elif mn in ('b', 'b.ne', 'b.eq'):
                lab = ops[0].rstrip('fb')
                tgt = labels[lab]
                if tgt < pc: raise ctrans.Unsupported('asm backward branch')
                if mn == 'b': pc = tgt; continue
                c_ = st['Z'] if mn == 'b.eq' else z3.Not(st['Z'])
                s2 = {'regs': dict(st['regs']), 'Z': st['Z'], 'mem': st['mem']}
                step(s2, tgt, z3.And(cond, c_), results)      # taken
                cond = z3.And(cond, z3.Not(c_))                # fall through
            elif mn == 'dmb': pass      # a barrier orders accesses; it changes no value (the ordering is certified by the access gate: tools/gate_order.py)
            elif mn in ('ldar', 'ldarb', 'ldarh', 'ldr', 'ldrb', 'ldrh', 'stlr', 'stlrb', 'stlrh', 'str', 'strb', 'strh'):
                base = mn.rstrip('brh') if mn[:3] != 'str' else mn
                suf = mn[-1] if mn[-1] in 'bh' and mn not in ('ldr', 'str') else ''
                w, nm = reg(ops[0], st); nb = SZ[suf] or (4 if w == 'w' else 8)
                addr = mem_addr(ops[1], st)
                if mn.startswith(('ldar', 'stlr')):      # Arm ARM: a load-acquire / store-release of a misaligned address is an alignment fault: an obligation on the path
                    m.ub.append(z3.Implies(z3.And(fr.L, cond), z3.URem(addr, z3.BitVecVal(nb, 64)) == 0))
                if mn.startswith('ld'):
                    bs = [z3.Select(st['mem'], addr + i) for i in range(nb)]
                    e = z3.Concat(*reversed(bs)) if nb > 1 else bs[0]
                    setreg(st, ops[0], e)
                else:
                    v = val(ops[0], st)
                    mm = st['mem']
                    for i in range(nb): mm = z3.Store(mm, addr + i, z3.Extract(8 * i + 7, 8 * i, v))
                    st['mem'] = mm
            elif mn == 'bfxil':
                lsb, wd = val(ops[2], st), val(ops[3], st)
                d, s = val(ops[0], st), val(ops[1], st); mask = ((1 << wd) - 1)
                setreg(st, ops[0], (d & z3.BitVecVal(0xffffffff & ~mask, 32)) | ((z3.LShR(s, lsb)) & z3.BitVecVal(mask, 32)))
        results.append((cond, st))

    res = []
    step({'regs': regs, 'Z': z3.BoolVal(False), 'mem': m.mem}, 0, z3.BoolVal(True), res)
    # merge the paths: the last path's state under the others' conditions
    final = res[-1][1]
    for cnd, st in reversed(res[:-1]):
        final = {'regs': {k: z3.If(cnd, st['regs'][k], final['regs'][k]) for k in final['regs']}, 'Z': final['Z'], 'mem': z3.If(cnd, st['mem'], final['mem'])}
    m.mem = z3.If(fr.L, final['mem'], m.mem) if final['mem'] is not m.mem else m.mem
    return {nm: final['regs'][nm] for nm, _ in outs}, outs
