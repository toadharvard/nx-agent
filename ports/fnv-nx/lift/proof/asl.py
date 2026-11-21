#!/usr/bin/env python3
"""asl: an interpreter for the Arm ASL pseudocode of the pinned Arm A-profile A64 ISA XML, over z3 bit-vectors.

Source (pinned, one record): ISA_A64_xml_A_profile-2023-09.tar.gz, sha256 7f2092a1416fa1f8b784e96fbc7cdd67b2ce81f84426968279694f96f1b11596 (the file named by FNVNX_A64_ISA_TGZ; the hash is checked on load).
  - the instruction files (e.g. movz.xml): the encoding's field boxes, and the `Decode` / `Execute` pseudocode of the encoding class;
  - shared_pseudocode.xml: the library functions the pseudocode calls (DecodeBitMasks, ConditionHolds, ShiftReg, ROR, ...), parsed on demand, never transcribed.
The interpreter evaluates ASL exactly: bit-vectors are z3 BV, integers/booleans of the decode are concrete Python values (the fields come from the assembler's operands), a branch on a symbolic
boolean evaluates both arms and merges the state with If.  A construct this module does not translate raises Unsupported (nothing is paraphrased).

The system boundary, the only values the pseudocode reads that the XML leaves to the machine (one declared record, the A64 application model of the console, owner record OWNER 4):
  - the general registers `_R[n]`, `_PC`, the flags PSTATE.<N,Z,C,V>, guest memory (z3 Array BV64 -> BV8, little-endian: SCTLR_EL1.EE = 0, a Horizon process is little-endian);
  - PSTATE.EL = EL0, every FEAT_* absent (IsFeatureImplemented = FALSE: the Cortex-A57 feature set is Armv8.0);
  - `Mem[]` is the byte access of `Mem[]` in aarch64/functions/memory/Mem restricted to what the pseudocode text says for non-pair, non-SIMD, non-LSE2 accesses: an access whose address is not aligned to its size
    faults when `AArch64.UnalignedAccessFaults` holds, i.e. for an ordered (acqsc/relsc/acqpc/limitedordered) or exclusive access (SCTLR_EL1.A = 0: no other unaligned fault); the fault is an obligation `ub`
    on the path (an alignment fault does not occur in a lifted run), not a behaviour;
  - DataMemoryBarrier / BranchNotTaken / Hint_Branch / SPEBranch have no value effect.  The ordering of ldar/stlr/dmb is NOT part of the value semantics here: the obligation of this executor is single-thread
    value equality; the memory ordering of the emitted accesses is certified by the access gate (tools/gate_order.py).
"""
import hashlib, html, io, os, re, tarfile
import z3

PIN_SHA256 = '7f2092a1416fa1f8b784e96fbc7cdd67b2ce81f84426968279694f96f1b11596'
PIN_NAME = 'ISA_A64_xml_A_profile-2023-09'


class Unsupported(Exception):
    pass


class NoInput(Exception):
    pass


# ----------------------------------------------------------------------------------------------------------------------------------- the pinned source
_FILES = {}


def _load():
    if _FILES: return
    p = os.environ.get('FNVNX_A64_ISA_TGZ')
    if not p or not os.path.exists(p): raise NoInput('FNVNX_A64_ISA_TGZ (the pinned ISA_A64_xml_A_profile-2023-09.tar.gz) is not set')
    raw = open(p, 'rb').read()
    if hashlib.sha256(raw).hexdigest() != PIN_SHA256: raise NoInput('FNVNX_A64_ISA_TGZ is not the pinned archive (sha256 %s)' % PIN_SHA256)
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as t:
        for m in t.getmembers():
            if m.isfile() and m.name.endswith('.xml') and '/' + PIN_NAME + '/' in '/' + m.name:
                _FILES[os.path.basename(m.name)] = t.extractfile(m).read().decode('utf-8')


def xml(name):
    _load()
    if name not in _FILES: raise Unsupported('no such file in the pinned XML: ' + name)
    return _FILES[name]


def text(frag): return html.unescape(re.sub(r'<[^>]+>', '', frag))


# ----------------------------------------------------------------------------------------------------------------------------------- lexer
KEYW = {'if', 'then', 'else', 'elsif', 'case', 'of', 'when', 'otherwise', 'for', 'to', 'downto', 'while', 'do', 'repeat', 'until', 'return', 'assert', 'UNDEFINED', 'UNPREDICTABLE', 'SEE', 'IN',
        'AND', 'OR', 'EOR', 'DIV', 'MOD', 'TRUE', 'FALSE', 'constant', 'enumeration', 'type', 'is'}
TOK = re.compile(r"\s*(?:(?P<bits>'[01x ]*')|(?P<hex>0x[0-9a-fA-F_]+)|(?P<num>\d+(?:\.\d+)?)|(?P<str>\"[^\"]*\")|(?P<id>[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*)|(?P<op>\+:|==|!=|<=|>=|<<|>>|&&|\|\||[-+*/^:;,()\[\]{}=<>!.]))")


class Tok:
    __slots__ = ('k', 'v', 'sp')

    def __init__(s, k, v, sp): s.k, s.v, s.sp = k, v, sp

    def __repr__(s): return '%s:%s' % (s.k, s.v)


def lex(line):
    out, i, n = [], 0, len(line)
    while True:
        m = TOK.match(line, i)
        if not m:
            if line[i:].strip(): raise Unsupported('lex: ' + line[i:i + 20])
            break
        sp = m.start() < m.start(m.lastgroup)
        k, v = m.lastgroup, m.group(m.lastgroup)
        if k == 'id' and v in KEYW: k = 'kw'
        if k == 'id' and '.' in v:      # PSTATE.N, accdesc.acqsc, FPCR.RMode: kept as one name; `PSTATE.<N,Z,C,V>` is the `.` operator followed by a slice
            pass
        out.append(Tok(k, v, sp)); i = m.end()
    return out


def lines_of(src):
    """[(indent, tokens)] with comments and blank lines removed and bracket-continued lines joined"""
    res, cur, depth = [], None, 0
    for raw in src.split('\n'):
        l = raw.split('//')[0].rstrip()
        if not l.strip() and cur is None: continue
        if cur is None:
            ind = len(l) - len(l.lstrip(' ')); cur = [ind, []]
        toks = lex(l)
        cur[1] += toks
        for t in toks:
            if t.v in ('(', '[', '{'): depth += 1
            elif t.v in (')', ']', '}'): depth -= 1
        if depth <= 0 and cur[1]:
            res.append((cur[0], cur[1])); cur, depth = None, 0
        elif depth <= 0: cur = None
    return res


# ----------------------------------------------------------------------------------------------------------------------------------- parser (AST = tuples)
class P:
    def __init__(s, toks): s.t, s.i = toks, 0

    def peek(s, o=0): return s.t[s.i + o] if s.i + o < len(s.t) else None

    def eat(s, v=None, k=None):
        t = s.peek()
        if t is None or (v is not None and t.v != v) or (k is not None and t.k != k): raise Unsupported('parse: expected %s got %s' % (v or k, t))
        s.i += 1; return t

    def at(s, v): t = s.peek(); return t is not None and t.v == v and t.k in ('op', 'kw')

    def done(s): return s.i >= len(s.t)

    # expressions
    def expr(s):
        if s.at('if'):
            s.eat('if'); c = s.expr(); s.eat('then'); a = s.expr(); s.eat('else'); b = s.expr(); return ('ite', c, a, b)
        return s.oror()

    def oror(s):
        a = s.andand()
        while s.at('||'): s.eat(); a = ('||', a, s.andand())
        return a

    def andand(s):
        a = s.cmp()
        while s.at('&&'): s.eat(); a = ('&&', a, s.cmp())
        return a

    def cmp(s):
        a = s.bitor()
        t = s.peek()
        while t is not None and ((t.k == 'op' and t.v in ('==', '!=', '<=', '>=', '<', '>')) or (t.k == 'kw' and t.v == 'IN')):
            s.eat()
            if t.v == 'IN':
                s.eat('{'); items = [s.expr()]
                while s.at(','): s.eat(); items.append(s.expr())
                s.eat('}'); a = ('in', a, items)
            else: a = (t.v, a, s.bitor())
            t = s.peek()
        return a

    def bitor(s):
        a = s.concat()
        while s.peek() is not None and s.peek().k == 'kw' and s.peek().v in ('OR', 'EOR', 'AND'):
            o = s.eat().v; a = (o, a, s.concat())
        return a

    def concat(s):
        a = s.shift()
        while s.at(':'): s.eat(); a = (':', a, s.shift())
        return a

    def shift(s):
        a = s.add()
        while s.at('<<') or s.at('>>'): o = s.eat().v; a = (o, a, s.add())
        return a

    def add(s):
        a = s.mul()
        while s.at('+') or s.at('-'): o = s.eat().v; a = (o, a, s.mul())
        return a

    def mul(s):
        a = s.unary()
        while s.at('*') or s.at('/') or s.at('DIV') or s.at('MOD'): o = s.eat().v; a = (o, a, s.unary())
        return a

    def unary(s):
        if s.at('!'): s.eat(); return ('!', s.unary())
        if s.at('-'): s.eat(); return ('neg', s.unary())
        a = s.post()
        if s.at('^'): s.eat(); a = ('^', a, s.unary())
        return a

    def slice_items(s):
        items = []
        while True:
            lo = s.add()
            if s.at(':'): s.eat(); hi = lo; lo = s.add(); items.append(('range', hi, lo))
            elif s.at('+:'): s.eat(); w = s.add(); items.append(('width', lo, w))
            else: items.append(('bit', lo))
            if s.at(','): s.eat(); continue
            break
        s.eat('>')
        return items

    def post(s):
        a = s.primary()
        while True:
            t = s.peek()
            if t is None: break
            if t.v == '[' and t.k == 'op' and a[0] == 'name':
                s.eat('['); args = []
                if not s.at(']'):
                    args.append(s.expr())
                    while s.at(','): s.eat(); args.append(s.expr())
                s.eat(']'); a = ('index', a[1], args)
            elif t.v == '<' and t.k == 'op' and not t.sp:
                s.eat('<'); a = ('slice', a, s.slice_items())
            elif t.v == '.' and t.k == 'op' and not t.sp and s.peek(1) is not None and s.peek(1).v == '<':
                s.eat('.'); s.eat('<'); a = ('fields', a, s.slice_items())
            else: break
        return a

    def primary(s):
        t = s.eat()
        if t.k == 'num': return ('int', int(t.v) if '.' not in t.v else float(t.v))
        if t.k == 'hex': return ('int', int(t.v.replace('_', ''), 16))
        if t.k == 'bits': return ('bits', t.v[1:-1].replace(' ', ''))
        if t.k == 'str': return ('str', t.v[1:-1])
        if t.k == 'kw' and t.v in ('TRUE', 'FALSE'): return ('bool', t.v == 'TRUE')
        if t.k == 'op' and t.v == '(':
            a = s.expr()
            if s.at(','):
                items = [a]
                while s.at(','): s.eat(); items.append(s.expr())
                s.eat(')'); return ('tuple', items)
            s.eat(')'); return a
        if t.k == 'id':
            if s.at('(') and not s.peek().sp or (s.at('(') and t.v[0].isupper()):
                s.eat('('); args = []
                if not s.at(')'):
                    args.append(s.expr())
                    while s.at(','): s.eat(); args.append(s.expr())
                s.eat(')'); return ('call', t.v, args)
            return ('name', t.v)
        if t.k == 'kw' and t.v in ('UNDEFINED', 'UNPREDICTABLE'): return (t.v.lower(),)
        raise Unsupported('parse: primary %s' % t)


TYPE_WORDS = {'integer', 'boolean', 'bit', 'real'}


def parse_type_expr(p):
    """`integer` | `boolean` | `bit` | `bits(expr)` | `TypeName`: returns ('bits', expr) or the word"""
    t = p.eat()
    if t.v == 'bits' and p.at('('):
        p.eat('('); e = p.expr(); p.eat(')'); return ('bits', e)
    return t.v


def is_decl(toks):
    i = 0
    if toks and toks[0].v == 'constant': i = 1
    if i >= len(toks): return False
    t = toks[i]
    if t.v == 'bits' and i + 1 < len(toks) and toks[i + 1].v == '(':
        d, j = 0, i + 1
        while j < len(toks):
            if toks[j].v == '(': d += 1
            elif toks[j].v == ')':
                d -= 1
                if d == 0: break
            j += 1
        return j + 1 < len(toks) and toks[j + 1].k == 'id'
    if t.v in TYPE_WORDS and i + 1 < len(toks) and toks[i + 1].k == 'id': return True
    return t.k == 'id' and i + 1 < len(toks) and toks[i + 1].k == 'id' and '.' not in t.v


class Parser:
    def __init__(s, lines): s.L, s.n = lines, 0

    def block(s, ind):
        """the statements of the lines at indentation > ind (a block body); returns (list, next index)"""
        body = []
        if s.n >= len(s.L) or s.L[s.n][0] <= ind: return body
        bi = s.L[s.n][0]
        while s.n < len(s.L) and s.L[s.n][0] == bi: body.append(s.stmt())
        if s.n < len(s.L) and s.L[s.n][0] > bi: raise Unsupported('parse: indentation')
        return body

    def inline_or_block(s, p, ind):
        if not p.done():
            q = Parser([(ind + 1, p.t[p.i:])]); st = q.stmt(); return [st]
        return s.block(ind)

    def stmt(s):
        ind, toks = s.L[s.n]; s.n += 1
        p = P(toks); t = p.peek()
        if t.v == 'if' and t.k == 'kw':
            p.eat(); c = p.expr(); p.eat('then'); arms = [(c, s.inline_or_block(p, ind))]; els = None
            while s.n < len(s.L) and s.L[s.n][0] == ind and s.L[s.n][1][0].v in ('elsif', 'else') and s.L[s.n][1][0].k == 'kw':
                _, tk = s.L[s.n]; s.n += 1; q = P(tk); w = q.eat().v
                if w == 'elsif': c2 = q.expr(); q.eat('then'); arms.append((c2, s.inline_or_block(q, ind)))
                else: els = s.inline_or_block(q, ind)
            return ('if', arms, els)
        if t.v == 'case' and t.k == 'kw':
            p.eat(); e = p.expr(); p.eat('of'); whens, other = [], None
            if s.n < len(s.L) and s.L[s.n][0] > ind:
                bi = s.L[s.n][0]
                while s.n < len(s.L) and s.L[s.n][0] == bi:
                    _, tk = s.L[s.n]; s.n += 1; q = P(tk); w = q.eat().v
                    if w == 'otherwise': other = s.inline_or_block(q, bi)
                    else:
                        pats = [q.expr()]
                        while q.at(','): q.eat(); pats.append(q.expr())
                        if q.at('&&'): raise Unsupported('parse: case guard')
                        whens.append((pats, s.inline_or_block(q, bi)))
            return ('case', e, whens, other)
        if t.v == 'for' and t.k == 'kw':
            p.eat(); v = p.eat(k='id').v; p.eat('='); a = p.expr(); d = p.eat().v; b = p.expr()
            if p.at('do'): p.eat()
            return ('for', v, a, b, d == 'downto', s.inline_or_block(p, ind))
        if t.v == 'while' and t.k == 'kw':
            p.eat(); c = p.expr(); p.eat('do'); return ('while', c, s.inline_or_block(p, ind))
        if t.v == 'repeat' and t.k == 'kw':
            body = s.block(ind); _, tk = s.L[s.n]; s.n += 1; q = P(tk); q.eat('until'); c = q.expr(); return ('repeat', body, c)
        if t.k == 'kw' and t.v in ('return', 'assert', 'UNDEFINED', 'UNPREDICTABLE', 'SEE'):
            p.eat()
            if t.v == 'return': return ('return', None if p.at(';') or p.done() else p.expr())
            if t.v == 'assert': return ('assert', p.expr())
            if t.v == 'SEE': return ('see',)
            return ('undefined',)
        if is_decl(toks):
            if p.at('constant'): p.eat()
            ty = parse_type_expr(p); names = [p.eat(k='id').v]
            while p.at(','): p.eat(); names.append(p.eat(k='id').v)
            init = None
            if p.at('='): p.eat(); init = p.expr()
            return ('decl', ty, names, init)
        # assignment (lvalue = expr) or call statement
        if p.at('('):       # tuple lvalue (a, -)
            save = p.i; p.eat('('); lv = []
            while True:
                if p.at('-'): p.eat(); lv.append(('ignore',))
                else: lv.append(p.post())
                if p.at(','): p.eat(); continue
                break
            if p.at(')'):
                p.eat()
                if p.at('='): p.eat(); return ('assign', ('tuple', lv), p.expr())
            p.i = save
        a = p.expr()
        if p.at('='): p.eat(); return ('assign', a, p.expr())
        return ('expr', a)


# ----------------------------------------------------------------------------------------------------------------------------------- values
class B:
    """a bit-string: width w, z3 BV expression e (w = 0: e is None)"""
    __slots__ = ('w', 'e')

    def __init__(s, w, e): s.w, s.e = w, e

    def __repr__(s): return 'B%d(%s)' % (s.w, s.e)


def bv(w, v): return B(w, z3.BitVecVal(v & ((1 << w) - 1), w) if w else None)


def conc(v):
    """the concrete Python value of an int / bool / B, or None when it is symbolic"""
    if isinstance(v, (int, bool, str)): return v
    if isinstance(v, B):
        if v.w == 0: return 0
        e = z3.simplify(v.e)
        return e.as_long() if z3.is_bv_value(e) else None
    if isinstance(v, z3.BoolRef):
        e = z3.simplify(v)
        return True if z3.is_true(e) else False if z3.is_false(e) else None
    return None


def cat(a, b):
    if a.w == 0: return b
    if b.w == 0: return a
    return B(a.w + b.w, z3.Concat(a.e, b.e))


def need_int(v, what):
    c = conc(v)
    if not isinstance(c, int) or isinstance(c, bool): raise Unsupported('symbolic or non-integer value where the pseudocode needs a decode-time integer: ' + what)
    return c


def asbool(v):
    if isinstance(v, bool): return v
    if isinstance(v, z3.BoolRef): c = conc(v); return v if c is None else c
    if isinstance(v, B) and v.w == 1: return z3.simplify(v.e == 1)
    raise Unsupported('boolean expected: %r' % (v,))


class Return(Exception):
    def __init__(s, v): s.v = v


class Undefined(Exception):
    pass


class State:
    """the architectural state the pseudocode reads and writes"""

    def __init__(s):
        s.R = {}                        # n -> BV64
        s.mem = None
        s.nzcv = {'N': bv(1, 0), 'Z': bv(1, 0), 'C': bv(1, 0), 'V': bv(1, 0)}
        s.pc = None                     # BV64 of the current instruction; _PC after a branch
        s.nextpc = None
        s.ub = []                       # obligations (alignment faults)
        s.cond = z3.BoolVal(True)
        s.log = []

    def copy(s):
        t = State.__new__(State); t.R = dict(s.R); t.mem = s.mem; t.nzcv = dict(s.nzcv); t.pc = s.pc; t.nextpc = s.nextpc; t.ub = s.ub; t.cond = s.cond; t.log = s.log
        return t

    def merge(s, c, o):
        """state := If(c, s, o) component-wise (o is the else state)"""
        r = State.__new__(State)
        r.R = {k: z3.If(c, s.R[k], o.R[k]) if (s.R.get(k) is not o.R.get(k)) else s.R[k] for k in s.R}
        r.mem = z3.If(c, s.mem, o.mem) if s.mem is not o.mem else s.mem
        r.nzcv = {k: (B(1, z3.If(c, s.nzcv[k].e, o.nzcv[k].e)) if s.nzcv[k] is not o.nzcv[k] else s.nzcv[k]) for k in s.nzcv}
        r.pc = s.pc
        r.nextpc = z3.If(c, s.nextpc, o.nextpc) if s.nextpc is not o.nextpc else s.nextpc
        r.ub = s.ub; r.cond = s.cond; r.log = s.log
        return r


# ----------------------------------------------------------------------------------------------------------------------------------- library
_LIB = {}       # function name -> [(params, body_lines_parser_result)]
_LIBSRC = None


def _libsrc():
    global _LIBSRC
    if _LIBSRC is None:
        s = xml('shared_pseudocode.xml'); _LIBSRC = []
        for m in re.finditer(r'<ps name="([^"]*)"[^>]*>\s*<pstext[^>]*>(.*?)</pstext>', s, re.S): _LIBSRC.append((m.group(1), text(m.group(2))))
    return _LIBSRC


def parse_functions(src):
    """the function definitions of a pseudocode text: [(name, [(type, pname)], body)]; prototypes without a body are skipped"""
    L = lines_of(src); out, i = [], 0
    while i < len(L):
        ind, toks = L[i]
        if ind != 0: i += 1; continue
        j = i + 1
        while j < len(L) and L[j][0] > 0: j += 1
        body = L[i + 1:j]
        # signature: the last top-level IDENT '(' whose ')' ends the line
        d, cand = 0, None
        for k, t in enumerate(toks):
            if t.v in ('(', '['): d += 1
            elif t.v in (')', ']'): d -= 1
            elif t.k == 'id' and k + 1 < len(toks) and toks[k + 1].v == '(' and d == 0: cand = k
        if cand is not None and body and toks[-1].v == ')' and not any(t.v == '[' for t in toks[:cand + 1]) and toks[0].v not in ('type', 'enumeration', 'constant'):
            params, cur, d = [], [], 0
            for t in toks[cand + 2:-1]:
                if t.v in ('(', '['): d += 1
                if t.v in (')', ']'): d -= 1
                if t.v == ',' and d == 0: params.append(cur); cur = []
                else: cur.append(t)
            if cur: params.append(cur)
            ps = []
            for pt in params:
                q = P(pt); ty = parse_type_expr(q); ps.append((ty, pt[-1].v))
            try:
                bp = Parser([(a - 1, b) for a, b in body]); stmts = []
                while bp.n < len(bp.L): stmts.append(bp.stmt())
                out.append((toks[cand].v, ps, stmts))
            except Unsupported as e:
                out.append((toks[cand].v, ps, e))
        i = j
    return out


_CONST = {}


def libconst(name):
    """the initialiser expression of a global `constant TYPE name = expr;` of the shared pseudocode, or None"""
    if not _CONST:
        for pn, src in _libsrc():
            for m in re.finditer(r'(?m)^constant\s+(?:bits\(\d+\)|integer|boolean|bit)\s+(\w+)\s*=\s*([^;]+);', src):
                try: _CONST.setdefault(m.group(1), P(lex(m.group(2))).expr())
                except Unsupported: pass
        _CONST['_'] = None
    return _CONST.get(name)


def lib(name):
    if name not in _LIB:
        _LIB[name] = []
        for pn, src in _libsrc():
            if not re.search(r'(^|\n)[^\n/]*\b' + re.escape(name) + r'\(', src): continue
            for fn, ps, body in parse_functions(src):
                if fn == name: _LIB[name].append((ps, body))
    return _LIB[name]


# ----------------------------------------------------------------------------------------------------------------------------------- evaluator
def slice_bits(v, hi, lo):
    if isinstance(v, int) and not isinstance(v, bool):
        w = hi - lo + 1
        return bv(w, v >> lo)
    if hi < lo: return B(0, None)
    if hi >= v.w: raise Unsupported('slice beyond the width')
    return B(hi - lo + 1, z3.Extract(hi, lo, v.e))


def bits_binop(op, a, b):
    if isinstance(a, B) and isinstance(b, B):
        if a.w != b.w: raise Unsupported('width mismatch %d %d for %s' % (a.w, b.w, op))
        if a.w == 0: return a
        f = {'AND': lambda x, y: x & y, 'OR': lambda x, y: x | y, 'EOR': lambda x, y: x ^ y, '+': lambda x, y: x + y, '-': lambda x, y: x - y}[op]
        return B(a.w, f(a.e, b.e))
    if isinstance(a, B) and isinstance(b, int) and op in ('+', '-'):
        return B(a.w, a.e + b if op == '+' else a.e - b) if a.w else a
    raise Unsupported('operands of %s: %r %r' % (op, a, b))


class Interp:
    def __init__(s, st):
        s.st = st; s.depth = 0

    # --- environment: a dict name -> value; records are dicts
    def run_body(s, body, env):
        for stmt in body: s.exec(stmt, env)

    def exec(s, a, env):
        k = a[0]
        if k == 'decl':
            _, ty, names, init = a
            for n in names:
                if init is not None: env[n] = s.coerce(ty, s.ev(init, env), env)
                elif ty == 'integer': env[n] = 0
                elif ty == 'boolean': env[n] = False
                elif ty == 'bit': env[n] = bv(1, 0)
                elif isinstance(ty, tuple) and ty[0] == 'bits': env[n] = bv(need_int(s.ev(ty[1], env), 'declared width'), 0)
                else: env[n] = {}
        elif k == 'assign': s.assign(a[1], s.ev(a[2], env), env)
        elif k == 'expr': s.ev(a[1], env)
        elif k == 'if':
            _, arms, els = a
            s.if_chain(arms, els, env)
        elif k == 'case':
            _, e, whens, other = a; v = s.ev(e, env); done = False
            for pats, body in whens:
                for pt in pats:
                    if s.match(v, pt, env): s.run_body(body, env); done = True; break
                if done: break
            if not done and other is not None: s.run_body(other, env)
        elif k == 'for':
            _, var, lo, hi, down, body = a; lo, hi = need_int(s.ev(lo, env), 'for bound'), need_int(s.ev(hi, env), 'for bound')
            for i in (range(lo, hi - 1, -1) if down else range(lo, hi + 1)):
                env[var] = i; s.run_body(body, env)
        elif k == 'while':
            n = 0
            while asbool_c(s.ev(a[1], env)):
                s.run_body(a[2], env); n += 1
                if n > 4096: raise Unsupported('while loop bound')
        elif k == 'return': raise Return(None if a[1] is None else s.ev(a[1], env))
        elif k == 'assert':
            c = asbool(s.ev(a[1], env))
            if c is not True:
                if c is False: raise Unsupported('assert failed in pseudocode')
                s.st.ub.append(z3.Implies(s.st.cond, c))
        elif k == 'undefined': raise Undefined()
        elif k == 'see': raise Unsupported('SEE')
        else: raise Unsupported('stmt ' + k)

    def if_chain(s, arms, els, env):
        if not arms:
            if els is not None: s.run_body(els, env)
            return
        c = asbool(s.ev(arms[0][0], env))
        if c is True: s.run_body(arms[0][1], env); return
        if c is False: s.if_chain(arms[1:], els, env); return
        # symbolic condition: both arms on copies; merge the variables and the state
        e1, st1 = dict(env), s.st.copy(); e2, st2 = dict(env), s.st.copy()
        saved = s.st
        s.st = st1; s.st.cond = z3.And(saved.cond, c); s.run_body(arms[0][1], e1); st1 = s.st
        s.st = st2; s.st.cond = z3.And(saved.cond, z3.Not(c)); s.if_chain(arms[1:], els, e2); st2 = s.st
        m = st1.merge(c, st2); m.cond = saved.cond; s.st = m
        for n in set(e1) | set(e2):
            v1, v2 = e1.get(n), e2.get(n)
            if v1 is v2: env[n] = v1
            elif isinstance(v1, B) and isinstance(v2, B) and v1.w == v2.w and v1.w: env[n] = B(v1.w, z3.If(c, v1.e, v2.e))
            elif isinstance(v1, (bool, z3.BoolRef)) and isinstance(v2, (bool, z3.BoolRef)): env[n] = z3.If(c, z3.BoolVal(v1) if isinstance(v1, bool) else v1, z3.BoolVal(v2) if isinstance(v2, bool) else v2)
            elif v1 == v2: env[n] = v1
            elif v1 is None or v2 is None: env[n] = v1 if v2 is None else v2
            else: raise Unsupported('a variable that differs across a symbolic branch is not a bit-string or boolean: ' + n)

    def match(s, v, pt, env):
        if pt[0] == 'bits':
            pat = pt[1]
            if not isinstance(v, B) or v.w != len(pat): raise Unsupported('case pattern width')
            c = conc(v)
            if c is None: raise Unsupported('case on a symbolic bit-string')
            for i, ch in enumerate(pat):
                if ch != 'x' and ((c >> (len(pat) - 1 - i)) & 1) != int(ch): return False
            return True
        pv = s.ev(pt, env)
        return conc(v) == conc(pv) if not isinstance(v, str) else v == pv

    def coerce(s, ty, v, env):
        if isinstance(ty, tuple) and ty[0] == 'bits' and isinstance(v, B): return v
        return v

    # --- lvalues
    def assign(s, lv, v, env):
        k = lv[0]
        if k == 'name':
            if lv[1] == '_PC': s.st.nextpc = v.e if isinstance(v, B) else v; return
            if '.' in lv[1]:
                base, _, f = lv[1].partition('.')
                if base == 'PSTATE':
                    s.st.nzcv[f] = v; return
                env.setdefault(base, {})[f] = v; return
            env[lv[1]] = v
        elif k == 'index':
            name = lv[1]; args = [s.ev(x, env) for x in lv[2]]
            if name == 'X':
                n, w = need_int(args[0], 'register number'), need_int(args[1], 'register width')
                if n != 31: s.st.R[n] = z3.ZeroExt(64 - w, v.e) if w < 64 else v.e       # aarch64/functions/registers/X, assignment form: `_R[n] = ZeroExtend(value, 64)` unless n == 31
            elif name == 'Mem': s.mem_write(args[0], need_int(args[1], 'size'), args[2], v)
            else: raise Unsupported('assignment to ' + name + '[]')
        elif k == 'slice':
            base, items = lv[1], lv[2]; cur = s.ev(base, env)
            assert len(items) == 1
            it = items[0]
            if it[0] == 'range': hi, lo = need_int(s.ev(it[1], env), 'slice'), need_int(s.ev(it[2], env), 'slice')
            elif it[0] == 'bit': hi = lo = need_int(s.ev(it[1], env), 'slice')
            else: lo = need_int(s.ev(it[1], env), 'slice'); hi = lo + need_int(s.ev(it[2], env), 'slice') - 1
            parts = []
            if hi + 1 < cur.w: parts.append(slice_bits(cur, cur.w - 1, hi + 1))
            parts.append(v)
            if lo > 0: parts.append(slice_bits(cur, lo - 1, 0))
            r = parts[0]
            for q in parts[1:]: r = cat(r, q)
            s.assign(base, r, env)
        elif k == 'fields':
            base, items = lv[1], lv[2]
            if base != ('name', 'PSTATE'): raise Unsupported('field-list assignment to ' + str(base))
            names = [it[1][1] for it in items]
            for i, n in enumerate(names): s.st.nzcv[n] = slice_bits(v, len(names) - 1 - i, len(names) - 1 - i)
        elif k == 'tuple':
            for sub, x in zip(lv[1], v):
                if sub[0] != 'ignore': s.assign(sub, x, env)
        else: raise Unsupported('lvalue ' + k)

    # --- guest memory (the system boundary)
    def mem_read(s, addr, size, accdesc):
        a = addr.e
        s.alignment(a, size, accdesc)
        bs = [z3.Select(s.st.mem, a + i) for i in range(size)]
        return B(8 * size, z3.Concat(*reversed(bs)) if size > 1 else bs[0])

    def mem_write(s, addr, size, accdesc, v):
        a = addr.e
        s.alignment(a, size, accdesc)
        m = s.st.mem
        for i in range(size): m = z3.Store(m, a + i, z3.Extract(8 * i + 7, 8 * i, v.e))
        s.st.mem = m

    def alignment(s, a, size, accdesc):
        ordered = any(asbool(accdesc.get(f, False)) is not False for f in ('acqsc', 'acqpc', 'relsc', 'limitedordered', 'exclusive', 'atomicop'))
        if ordered and size > 1: s.st.ub.append(z3.Implies(s.st.cond, z3.URem(a, z3.BitVecVal(size, 64)) == 0))

    # --- expressions
    def ev(s, a, env):
        k = a[0]
        if k == 'int': return a[1]
        if k == 'bool': return a[1]
        if k == 'bits':
            pat = a[1]
            if 'x' in pat: raise Unsupported('bit pattern used as a value')
            return bv(len(pat), int(pat, 2) if pat else 0)
        if k == 'str': return a[1]
        if k == 'name':
            n = a[1]
            if n in env: return env[n]
            if '.' in n:
                base, _, f = n.partition('.')
                if base == 'PSTATE':
                    if f in s.st.nzcv: return s.st.nzcv[f]
                    if f == 'EL': return bv(2, 0)       # EL0 (declared application model)
                    if f == 'SP': return bv(1, 0)
                    raise Unsupported('PSTATE.' + f)
                if base in env and isinstance(env[base], dict) and f in env[base]: return env[base][f]
                if base == 'TSTATE': return 0
                raise Unsupported('field ' + n)
            if n == '_PC': return B(64, s.st.pc)
            c = libconst(n)
            if c is not None: return s.ev(c, {})
            if re.match(r'[A-Za-z][A-Za-z0-9]*_[A-Za-z_0-9]+$', n): return n       # an enumeration constant
            if n in ('PC64',): return B(64, s.st.pc)
            raise Unsupported('unbound name ' + n)
        if k == 'tuple': return tuple(s.ev(x, env) for x in a[1])
        if k == 'ite':
            c = asbool(s.ev(a[1], env))
            if c is True: return s.ev(a[2], env)
            if c is False: return s.ev(a[3], env)
            x, y = s.ev(a[2], env), s.ev(a[3], env)
            if isinstance(x, B): return B(x.w, z3.If(c, x.e, y.e))
            raise Unsupported('symbolic if-expression over non-bit-strings')
        if k == 'slice':
            v = s.ev(a[1], env); parts = []
            for it in a[2]:
                if it[0] == 'range': hi, lo = need_int(s.ev(it[1], env), 'slice'), need_int(s.ev(it[2], env), 'slice')
                elif it[0] == 'bit': hi = lo = need_int(s.ev(it[1], env), 'slice')
                else: lo = need_int(s.ev(it[1], env), 'slice'); hi = lo + need_int(s.ev(it[2], env), 'slice') - 1
                parts.append(slice_bits(v, hi, lo))
            r = parts[0]
            for q in parts[1:]: r = cat(r, q)
            return r
        if k == 'fields':
            base = a[1]
            if base != ('name', 'PSTATE'): raise Unsupported('field list of ' + str(base))
            r = B(0, None)
            for it in a[2]: r = cat(r, s.st.nzcv[it[1][1]])
            return r
        if k == 'index': return s.index(a[1], [s.ev(x, env) for x in a[2]], env)
        if k == 'call': return s.call(a[1], [s.ev(x, env) for x in a[2]], env, a[2])
        if k == '!':
            v = s.ev(a[1], env); c = asbool(v)
            return (not c) if isinstance(c, bool) else z3.Not(c)
        if k == 'neg':
            v = s.ev(a[1], env)
            if isinstance(v, int): return -v
            raise Unsupported('negation of a non-integer')
        if k in ('&&', '||'):
            x = asbool(s.ev(a[1], env))
            if k == '&&' and x is False: return False
            if k == '||' and x is True: return True
            y = asbool(s.ev(a[2], env))
            if isinstance(x, bool): return y if k == '&&' and x else (y if k == '||' and not x else x)
            if isinstance(y, bool): return (x if y else False) if k == '&&' else (True if y else x)
            return z3.And(x, y) if k == '&&' else z3.Or(x, y)
        if k in ('==', '!='):
            x, y = s.ev(a[1], env), s.ev(a[2], env)
            if a[2][0] == 'bits' and 'x' in a[2][1]: raise Unsupported('wildcard compare')
            if isinstance(x, B) and isinstance(y, B):
                if x.w != y.w: raise Unsupported('== width mismatch')
                r = z3.simplify(x.e == y.e) if x.w else z3.BoolVal(True)
            elif isinstance(x, (z3.BoolRef, bool)) or isinstance(y, (z3.BoolRef, bool)): r = z3.simplify((z3.BoolVal(x) if isinstance(x, bool) else x) == (z3.BoolVal(y) if isinstance(y, bool) else y))
            else: r = x == y
            if isinstance(r, bool): return r if k == '==' else not r
            c = conc(r)
            if c is not None: return c if k == '==' else not c
            return r if k == '==' else z3.Not(r)
        if k in ('<', '>', '<=', '>='):
            x, y = need_int(s.ev(a[1], env), 'comparison'), need_int(s.ev(a[2], env), 'comparison')
            return {'<': x < y, '>': x > y, '<=': x <= y, '>=': x >= y}[k]
        if k == 'in':
            x = s.ev(a[1], env)
            for it in a[2]:
                if it[0] == 'bits' and 'x' in it[1]:
                    if s.match(x, it, env): return True
                elif conc(x) == conc(s.ev(it, env)) and conc(x) is not None: return True
            return False
        if k in ('AND', 'OR', 'EOR'):
            x, y = s.ev(a[1], env), s.ev(a[2], env)
            if isinstance(x, (bool, z3.BoolRef)): raise Unsupported('boolean AND')
            return bits_binop(k, x, y)
        if k == ':': return cat(s.ev(a[1], env), s.ev(a[2], env))
        if k in ('+', '-', '*', 'DIV', 'MOD', '/', '<<', '>>', '^'):
            x, y = s.ev(a[1], env), s.ev(a[2], env)
            if isinstance(x, B) or isinstance(y, B):
                if k in ('+', '-'): return bits_binop(k, x, y)
                raise Unsupported('bit-string operand of ' + k)
            x, y = need_int(x, k), need_int(y, k)
            return {'+': lambda: x + y, '-': lambda: x - y, '*': lambda: x * y, 'DIV': lambda: x // y, '/': lambda: x // y, 'MOD': lambda: x % y, '<<': lambda: x << y, '>>': lambda: x >> y, '^': lambda: x ** y}[k]()
        raise Unsupported('expr ' + k)

    def index(s, name, args, env):
        if name == 'X':
            n, w = need_int(args[0], 'register number'), need_int(args[1], 'register width')
            if n == 31: return bv(w, 0)       # aarch64/functions/registers/X, read form: Zeros(width) for n == 31
            r = s.st.R[n]
            return B(w, z3.Extract(w - 1, 0, r) if w < 64 else r)
        if name == 'SP': raise Unsupported('SP[] (the stack pointer is not a template operand)')
        if name == 'Mem': return s.mem_read(args[0], need_int(args[1], 'size'), args[2])
        raise Unsupported('indexed read of ' + name)

    # --- calls
    def call(s, name, args, env, argast):
        n = len(args)
        if name == 'IsFeatureImplemented': return False
        if name in ('Hint_Branch', 'SPEBranch', 'BranchNotTaken', 'DataMemoryBarrier', 'CheckSPAlignment'):
            if name == 'CheckSPAlignment': raise Unsupported('CheckSPAlignment (SP-based access)')
            return None
        if name in ('SecurityStateAtEL', 'GenMPAMcurEL'): return 0
        if name == 'BranchTo':
            t = args[0]
            if t.w != 64: raise Unsupported('BranchTo width')
            s.st.nextpc = t.e; return None
        if name == 'ProcessorID': return 0
        if name == 'NOT' and n == 1 and isinstance(args[0], B): return B(args[0].w, ~args[0].e) if args[0].w else args[0]       # ASL language primitive: prototype `bits(N) NOT(bits(N) x);` in shared/functions/common/NOT, bitwise complement
        if name == 'Replicate':
            x = args[0]; m = need_int(args[1], 'Replicate count') if n == 2 else None
            r = B(0, None)
            for _ in range(m): r = cat(r, x)
            return r
        cands = lib(name)
        for ps, body in cands:
            if len(ps) != n: continue
            ok = True
            for (ty, pn), v in zip(ps, args):
                if ty == 'integer' and not (isinstance(v, int) and not isinstance(v, bool)): ok = False
                if isinstance(ty, tuple) and not isinstance(v, B): ok = False
            if not ok: continue
            if isinstance(body, Exception): raise Unsupported('library function %s: %s' % (name, body))
            fenv = {}
            for (ty, pn), v in zip(ps, args):
                fenv[pn] = v
                if isinstance(ty, tuple) and isinstance(v, B) and ty[1][0] == 'name' and ty[1][1] not in fenv: fenv[ty[1][1]] = v.w
            if name == 'Zeros' or name == 'Ones': pass
            s.depth += 1
            if s.depth > 64: raise Unsupported('recursion')
            try:
                s.run_body(body, fenv); r = None
            except Return as rv: r = rv.v
            finally: s.depth -= 1
            return r
        raise Unsupported('function %s/%d is not in the pinned shared pseudocode (or is a prototype)' % (name, n))


def asbool_c(v):
    c = conc(v) if not isinstance(v, bool) else v
    if c is None: raise Unsupported('symbolic loop condition')
    return bool(c)


# ----------------------------------------------------------------------------------------------------------------------------------- instruction files
def iclass_blocks(fname):
    """{iclass name: {'encodings': {enc_name: {box: value-string}}, 'boxes': {name: width}, 'decode': text, 'execute': text, 'regfixed': {name: str}}}"""
    s = xml(fname); out = {}
    pss = {}
    for pm in re.finditer(r'<ps name="([^"]*)" mylink="([^"]*)"[^>]*>\s*<pstext[^>]*\ssection="(\w+)"[^>]*>(.*?)</pstext>', s, re.S):
        pss.setdefault(pm.group(1), {})[pm.group(3)] = text(pm.group(4))
    for m in re.finditer(r'<iclass name="([^"]*)"(.*?)</iclass>', s, re.S):
        nm, body = m.group(1), m.group(2)
        boxes, regfixed = {}, {}
        rd = re.search(r'<regdiagram[^>]*>(.*?)</regdiagram>', body, re.S)
        for bm in re.finditer(r'<box hibit="(\d+)"(?: width="(\d+)")?(?: name="(\w+)")?[^>]*>(.*?)</box>', rd.group(1), re.S):
            w = int(bm.group(2) or 1)
            if bm.group(3):
                boxes[bm.group(3)] = w
                cs = re.findall(r'<c[^>]*>\(?([01x]?)\)?</c>', bm.group(4))
                if len(cs) == w: regfixed[bm.group(3)] = cs
        encs = {}
        for em in re.finditer(r'<encoding name="([^"]*)"(.*?)</encoding>', body, re.S):
            fixed = {}
            for bm in re.finditer(r'<box hibit="(\d+)" width="(\d+)" name="(\w+)"[^>]*>(.*?)</box>', em.group(2), re.S):
                cs = re.findall(r'<c[^>]*>\(?([01x]?)\)?</c>', bm.group(4))
                if len(cs) == int(bm.group(2)): fixed[bm.group(3)] = cs
            enc_dv = dict(re.findall(r'<docvar key="([^"]+)" value="([^"]*)"', em.group(2)))
            tmpl = re.search(r'<asmtemplate>(.*?)</asmtemplate>', em.group(2), re.S)
            fields = re.findall(r'\(field &quot;(\w+)&quot;\)', tmpl.group(1)) if tmpl else []
            encs[em.group(1)] = {'fixed': fixed, 'dv': enc_dv, 'fields': fields, 'template': text(tmpl.group(1)) if tmpl else ''}
        # regdiagram constants (bits of the diagram) of the encoding class
        psn = re.search(r'<regdiagram[^>]*psname="([^"]*)"', body).group(1)
        out[nm] = {'regfixed': regfixed, 'psname': psn, 'encodings': encs, 'boxes': boxes, 'ps': pss, 'regdiagram': rd.group(1)}
    return out


def encoding(fname, enc_name):
    """(iclass record, encoding record, decode text, execute text) of the named encoding in the instruction file"""
    for nm, ic in iclass_blocks(fname).items():
        if enc_name in ic['encodings']:
            e = ic['encodings'][enc_name]
            d = ic['ps'].get(ic['psname'])       # the Decode block of the encoding class (its regdiagram's psname)
            if d is None: raise Unsupported('no pseudocode for %s' % enc_name)
            execs = [v['Execute'] for v in ic['ps'].values() if 'Execute' in v]
            post = [v['Postdecode'] for v in ic['ps'].values() if 'Postdecode' in v]
            ex = d.get('Execute') if 'Execute' in d else (execs[0] if len(execs) == 1 else None)       # an instruction whose classes share one Execute (e.g. LDR immediate) keys it on its first class
            if ex is None: raise Unsupported('no unique Execute for %s' % enc_name)
            pd = d.get('Postdecode') if 'Postdecode' in d else (post[0] if len(post) == 1 else '')
            return ic, e, d.get('Decode', '') + '\n' + pd, ex
    raise Unsupported('encoding %s not in %s' % (enc_name, fname))


def run_encoding(fname, enc_name, fields, st, accdesc_env=None):
    """execute the pinned pseudocode of one encoding on state st with the decode fields `fields` ({name: int}); the result is the new state (st is updated in place when no symbolic branch merged)"""
    ic, e, dec, exe = encoding(fname, enc_name)
    env = {}
    for nm, w in ic['boxes'].items():
        if nm in fields: env[nm] = bv(w, fields[nm])
    for nm, w in ic['boxes'].items():       # constant bits: the encoding's override of the class diagram's
        if nm in fields: continue
        cs = [''] * w; rf = ic['regfixed'].get(nm); ef = e['fixed'].get(nm)
        for i in range(w):
            if ef and ef[i] in ('0', '1'): cs[i] = ef[i]
            elif rf and rf[i] in ('0', '1'): cs[i] = rf[i]
        if all(c in ('0', '1') for c in cs): env[nm] = bv(w, int(''.join(cs), 2))
    for nm in fields:
        if nm not in env: raise Unsupported('field %s is not a box of %s' % (nm, enc_name))
    it = Interp(st)
    try:
        for src in (dec, exe):
            L = lines_of(src); p = Parser(L); body = []
            while p.n < len(p.L): body.append(p.stmt())
            it.run_body(body, env)
    except Undefined:
        st.ub.append(z3.Implies(st.cond, z3.BoolVal(False)))     # an UNDEFINED encoding: not a behaviour (the assembler never produces it)
    return it.st


def reset_cache():
    _FILES.clear(); _LIB.clear()
