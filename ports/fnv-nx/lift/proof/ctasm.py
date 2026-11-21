#!/usr/bin/env python3
"""ctasm: the two C++/GNU constructs of the lifted code's memory boundary that pycparser cannot parse, read from the runtime's own text (rt/fnvl.h), for ctrans.

1. `__asm__ volatile("template" : outputs : inputs : clobbers)` (GCC manual, Extended Asm).  `convert_asm` turns each statement into the call `__asm_N(out exprs..., in exprs...)`; N indexes
   ASMS, the record of the statement (template string, operand names and constraints).  ctrans executes the call by `run_asm`: the A64 instructions of the template, one by one, with the
   semantics of the pinned Arm A64 ISA XML (asl.py, asmenc.py); a mnemonic asmenc has no row for is Unsupported (the function is refused, no instruction is guessed).
2. The guest memory accessors: `GM<T>{a}`, `GMF<F,T>{a}`, `GMK<T,A>{}`, `GMKF<F,T,A>{}` (the classes of rt/fnvl.h, which the access macros M8..MF64, MK8..MKF64 expand to).  `accessor_c` reads the
   class definitions from the preprocessed header text and writes each operator as a C function `NAME_ld(a)` / `NAME_st(a, v)` (NAME = class and type arguments, e.g. GM_u32, GMF_float_u32); `rewrite`
   turns a class temporary `CLS<args>{init}[.g(lkwin_)]` into the call `NAME(init)`, and `macro_defs` does the same for the macro bodies.  ctrans reads the call `NAME(x)` as `NAME_ld(x)` and the
   assignment `NAME(x) = v` as `NAME_st(x, v)`: the operator body is the only definition of what the access does, and its value effect is the asm it contains.
"""
import re
import z3

ASMS = []


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


def mnemonics():
    """the set of mnemonics that occur in the asm templates of the statements converted so far (the closure's), derived from ASMS"""
    out = set()
    for r in ASMS:
        for ln in r['template'].replace(';', '\n').split('\n'):
            ln = re.sub(r'^\s*\d+:\s*', '', ln).strip()
            if ln and not ln.startswith('.'): out.add(ln.split()[0].lower())
    return out


def run_asm(m, fr, n, args, ctrans):
    """execute ASMS[n] on the argument values; writes the output operands back.  Each instruction is the `Decode`/`Execute` ASL of the pinned Arm A64 ISA XML (asl.py), the encoding chosen by asmenc.py.
    Single-thread value equality is the obligation: the acquire/release/barrier ordering of ldar/stlr/dmb is not value semantics (it is certified by the access gate, tools/gate_order.py)."""
    import asl, asmenc
    rec = ASMS[n]; names, imm, outs = {}, {}, []
    st0 = asl.State(); st0.mem = m.mem
    argv = list(zip(rec['ops'], args)); regs = {}
    for (kind, name, con), a in argv:
        if 'i' in con and kind == 'in':
            imm[name] = z3.simplify(a.e).as_long(); continue
        if kind == 'out' and '+' not in con:
            v = z3.BitVec('asm%d_%s_%d' % (n, name, m.fresh), 64); m.fresh += 1      # an output before its first write: any value
        else:
            hi = z3.BitVec('asm%d_%s_hi_%d' % (n, name, m.fresh), 32); m.fresh += 1
            v = _bv(m, a, 64) if a.e.size() == 64 else z3.Concat(hi, a.e)      # C passes a 32-bit value in a register whose upper 32 bits are unspecified
        names[name] = len(names); regs[name] = v
        if kind == 'out': outs.append((name, a))
    for nm, num in names.items(): st0.R[num] = regs[nm]
    for f in 'NZCV': st0.nzcv[f] = asl.B(1, z3.BitVec('asm%d_flag%s_%d' % (n, f, m.fresh), 1)); m.fresh += 1       # the flags before the first flag-setting instruction: any value
    lines = []
    for ln in rec['template'].replace(';', '\n').split('\n'):
        ln = ln.strip()
        if not ln: continue
        lab = re.match(r'(\d+):\s*(.*)$', ln)
        if lab: lines.append(('label', lab.group(1))); ln = lab.group(2).strip()
        if ln: lines.append(('ins', ln))
    BASE = 0x10000
    labels = {l[1]: i for i, l in enumerate(lines) if l[0] == 'label'}

    def sub(t):
        def f(mm):
            v, nm = mm.group(1), mm.group(2)
            if nm in imm: return '#%d' % imm[nm]
            return '%s:%s' % (v or 'w', nm)
        return re.sub(r'%([wx]?)\[(\w+)\]', f, t)

    def operand(t):
        t = t.strip()
        mm = re.fullmatch(r'\[(x):(\w+)\]', t)
        if mm: return ('mem', names[mm.group(2)])
        mm = re.fullmatch(r'([wx]):(\w+)', t)
        if mm: return ('reg', mm.group(1), names[mm.group(2)])
        mm = re.fullmatch(r'(\d+)[fb]', t)
        if mm: return ('label', BASE + 4 * labels[mm.group(1)])
        if re.fullmatch(r'#?-?\d+|#?0x[0-9a-fA-F]+', t): return ('imm', int(t.lstrip('#'), 0))
        if re.fullmatch(r'[a-z]+', t): return ('opt', t)
        raise ctrans.Unsupported('asm operand ' + t)

    def step(st, pc, results):
        while pc < len(lines):
            k, x = lines[pc]; pc += 1
            if k == 'label': continue
            mn, _, rest = x.partition(' '); mn = mn.strip().lower()
            if mn.startswith('.'): continue      # an assembler directive (GNU as: a name that starts with '.'): it emits a record into another section, no register or memory effect of the executed program
            ops = []
            for o in ([o.strip() for o in _split_top(sub(rest), ',')] if rest.strip() else []):
                mm = re.fullmatch(r'lsl\s+#?(\d+)', o)       # `movk w, #imm, lsl 16`
                ops.append(('imm', int(mm.group(1))) if mm else operand(o))
            ipc = BASE + 4 * (pc - 1)
            try: fname, enc, fields = asmenc.encode(mn, ops, ipc, labels)
            except asl.Unsupported as e: raise ctrans.Unsupported(str(e))
            st.pc = z3.BitVecVal(ipc, 64); st.nextpc = z3.BitVecVal(ipc + 4, 64)
            try: st = asl.run_encoding(fname, enc, fields, st)
            except asl.Unsupported as e: raise ctrans.Unsupported('asm %s: %s' % (mn, e))
            if mn == 'b': pc = labels[x.split()[-1].rstrip('fb')] + 0; continue
            if mn in ('b.ne', 'b.eq'):
                tgt = ops[0][1]; taken = z3.simplify(st.nextpc == z3.BitVecVal(tgt, 64))
                if z3.is_true(taken): pc = labels[x.split()[-1].rstrip('fb')]; continue
                if z3.is_false(taken): continue
                lab = labels[x.split()[-1].rstrip('fb')]
                if lab < pc: raise ctrans.Unsupported('asm backward branch')
                s2 = st.copy(); s2.cond = z3.And(st.cond, taken)
                step(s2, lab, results)
                st.cond = z3.And(st.cond, z3.Not(taken))
        results.append(st)

    st0.cond = fr.L
    res = []
    step(st0, 0, res)
    # merge the paths: a path's state where its condition (which includes the function's L) holds, else the later path's state; the paths partition L
    final = res[-1]
    for st in reversed(res[:-1]): final = st.merge(st.cond, final)
    for u in final.ub: m.ub.append(u)
    m.mem = z3.If(fr.L, final.mem, m.mem) if final.mem is not m.mem else m.mem
    return {nm: z3.Extract(63, 0, final.R[names[nm]]) for nm, _ in outs}, outs
