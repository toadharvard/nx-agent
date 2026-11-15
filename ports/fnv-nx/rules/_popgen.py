"""_popgen.py - text helpers of rule POP2 (s2_bodies.py): balanced-paren and argument splitting, receiver chains,
class-body member declarations, parameter spelling. Pure functions of their text arguments."""
import re

ID = r'[A-Za-z_]\w*'

IMPURE = re.compile(r'[\w\]]\s*\(|\+\+|--|(?<![=!<>])=(?!=)|\bnew\b|\bdelete\b')

def match_paren(t, i):
    """t[i] == '(' -> index after the matching ')', or None. Skips string and char literals and comments."""
    depth, k, n = 0, i, len(t)
    while k < n:
        c = t[k]
        if c in '"\'':
            q = c
            k += 1
            while k < n and t[k] != q:
                k += 2 if t[k] == '\\' else 1
        elif t.startswith('//', k):
            e = t.find('\n', k)
            k = n if e < 0 else e
            continue
        elif t.startswith('/*', k):
            e = t.find('*/', k + 2)
            k = n if e < 0 else e + 2
            continue
        elif c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return k + 1
        elif c in ';{}' and depth:
            return None
        k += 1
    return None

def split_args(s):
    """Top-level comma split of an argument / parameter list text (without the parens). Parens, brackets and braces
    nest; `<` opens a template argument list only after a name and when a `>` closes it at the same depth before the
    list ends (`std::pair<int, int>`), so `->`, `<<`, `>>`, `<=`, `>=` and a lone comparison (`a < b, c`) are not
    brackets. (`f(a < b, c > d)` stays ambiguous, as it is to a C++ parser without knowing whether `a` is a template.)"""
    out, depth, angle, cur, k = [], 0, [], '', 0
    n = len(s)
    while k < n:
        c = s[k]
        if c in '"\'':
            q, j = c, k + 1
            while j < n and s[j] != q:
                j += 2 if s[j] == '\\' else 1
            cur += s[k:j + 1]
            k = j + 1
            continue
        two = s[k:k + 2]
        if two in ('->', '<<', '<=', '>=') or (two == '>>' and not angle):
            cur += two
            k += 2
            continue
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
            while angle and angle[-1] > depth:
                angle.pop()
        elif c == '<' and re.search(r'\w\s*$', cur) and _closes_angle(s, k):
            angle.append(depth)
        elif c == '>' and angle and angle[-1] == depth:
            angle.pop()
        if c == ',' and depth == 0 and not angle:
            out.append(cur)
            cur = ''
        else:
            cur += c
        k += 1
    if cur.strip() or out:
        out.append(cur)
    return out

def _closes_angle(s, k):
    """True when the `<` at s[k] has a matching `>` at its own paren depth before that depth ends"""
    depth, ang, j, n = 0, 1, k + 1, len(s)
    while j < n:
        c, two = s[j], s[j:j + 2]
        if c in '"\'':
            q, j = c, j + 1
            while j < n and s[j] != q:
                j += 2 if s[j] == '\\' else 1
            j += 1
            continue
        if two in ('->', '<=', '>=', '<<'):
            j += 2
            continue
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
            if depth < 0:
                return False
        elif depth == 0 and c == '<' and re.search(r'\w\s*$', s[:j]):
            ang += 1
        elif depth == 0 and c == '>':
            ang -= 1
            if ang == 0:
                return True
        elif depth == 0 and c in ';{':
            return False
        j += 1
    return False

def enclosing_arg(seg, ds, de, rn):
    """D's call seg[ds:de] as a whole argument of a call `rn(..)`: (receiver '(' index, end after its ')', argument
    index), else None. The argument may have an object expression or cast before D's name, nothing after D's ')'."""
    depth, k = 0, ds - 1
    while k >= 0:
        c = seg[k]
        if c in ')]':
            depth += 1
        elif c in '([':
            if depth == 0:
                break
            depth -= 1
        elif c in ';{}':
            return None
        k -= 1
    if k < 0 or seg[k] != '(':
        return None
    if not re.search(r'(?<![\w~])%s\s*$' % re.escape(rn), seg[:k]):
        return None
    re_ = match_paren(seg, k)
    if re_ is None:
        return None
    args = split_args(seg[k + 1:re_ - 1])
    pos, idx = k + 1, None
    for i, x in enumerate(args):
        if pos <= ds < pos + len(x):
            idx = i
            if seg[de:pos + len(x)].strip():
                return None
            break
        pos += len(x) + 1
    if idx is None:
        return None
    return (k, re_, idx)

GAP = re.compile(r'^(?:[\s)\]*]|\.|->|\+\s*(?:0x[0-9a-fA-F]+|\d+)|%s|\(\s*\))*$' % ID)

class _Reach:
    """re.match-like result of reach_receiver: end() is the index after the receiver's '(' in `rest`."""
    def __init__(self, end):
        self._e = end

    def end(self):
        return self._e

def reach_receiver(rest, rn):
    """The receiver R further along D's result: casts, dereferences, a constant offset, member access or calls
    without arguments between D's ')' and `.R(` / `->R(` (`((T*)x->D(a))->R()`, `static_cast<T*>(X.D(a))->R()`,
    `D(a)->B()->R()`). x86: none of them pushes or pops a word, so the words pushed for D stay for R (popscan's stack
    simulation names D the donor of R). Same statement, no comma or other argument between."""
    for m in re.finditer(r'(?:\.|->)\s*%s\s*\(' % re.escape(rn), rest):
        gap = rest[:m.start()]
        if re.search(r'[;{},]', gap):
            return None
        if GAP.match(gap):
            return _Reach(m.end())
        return None
    return None

def member_decls(text, body, name):
    """Top-level declarations of `name` in a class body: [(stmt start, stmt end incl ';', ret text, params text,
    const, virtual/static/inline-body flags)]."""
    b, e = body
    out = []
    depth = 0
    k = b + 1
    starts = [b + 1]
    # statement boundaries at depth 0
    while k < e - 1:
        c = text[k]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                starts.append(k + 1)
        elif c == ';' and depth == 0:
            starts.append(k + 1)
        elif c == ':' and depth == 0 and re.search(r'\b(?:public|private|protected)\s*$', text[starts[-1]:k]):
            starts.append(k + 1)
        k += 1
    starts.append(e - 1)
    for s0, s1 in zip(starts, starts[1:]):
        st = text[s0:s1]
        m = re.search(r'(?<![\w~])%s\s*\(' % re.escape(name), st)
        if not m:
            continue
        p = s0 + m.end() - 1
        q = match_paren(text, p)
        if q is None:
            continue
        tail = text[q:s1]
        head = st[:m.start()]
        bad = []
        if re.search(r'\bvirtual\b', head):
            bad.append('virtual')
        if re.search(r'\bstatic\b', head):
            bad.append('static')
        if '{' in tail:
            bad.append('inline body')
        if '=' in text[p:q]:
            bad.append('default argument')
        if not tail.strip().endswith(';'):
            bad.append('no ;')
        lead = len(head) - len(head.lstrip())
        out.append(dict(start=s0 + lead, end=s1, ret=head.strip(), params=text[p + 1:q - 1],
                        const=bool(re.match(r'\s*const\b', tail)), bad=bad))
    return out

def norm(s):
    return re.sub(r'\s+', ' ', s).strip()

def param_type(p):
    """'unsigned int flags' -> 'unsigned int' (drops a trailing identifier that is not a type keyword)."""
    p = norm(p)
    m = re.match(r'^(.*?[\w*&>\s])\s*\b(%s)$' % ID, p)
    kw = {'int', 'char', 'short', 'long', 'float', 'double', 'bool', 'unsigned', 'signed', 'const', 'void', 'wchar_t',
          '__int64'}
    if m and m[2] not in kw and m[1].strip() and not m[1].strip().endswith('::'):
        if re.search(r'[\w*&>]\s*$', m[1]):
            return norm(m[1])
    return p

def pname(p):
    """'unsigned int flags' -> 'flags'; None for an unnamed parameter."""
    t = param_type(p)
    n = norm(p)[len(t):].strip()
    return n or None

def same_params(a, b):
    ta = [param_type(x) for x in split_args(a)]
    tb = [param_type(x) for x in split_args(b)]
    ta = [] if ta == ['void'] else ta
    tb = [] if tb == ['void'] else tb
    return ta == tb

def arity(params):
    ps = [x for x in split_args(params) if x.strip()]
    return 0 if ps in ([], ['void']) or [norm(x) for x in ps] == ['void'] else len(ps)

def elaborate(text, pos, p):
    """A moved parameter `T*` / `T&` whose class T is first declared after pos (the receiver's class body) gets the
    class key of that declaration (`struct T*`): an elaborated type in a member's parameter list declares T in the
    enclosing namespace, as the later definition does. Other parameters are returned unchanged."""
    m = re.match(r'^(const\s+)?(%s)(\s*[*&].*)$' % ID, p)
    if not m or m[2] in ('void', 'char', 'int', 'short', 'long', 'float', 'double', 'bool', 'unsigned',
                                'signed', 'wchar_t', '__int64', 'struct', 'class', 'union', 'enum'):
        return p
    d = re.search(r'\b(struct|class|union|enum)\s+(?:__declspec\([^)]*\)\s*)?%s\b|\btypedef\b[^;]*\b%s\s*;' %
                  (re.escape(m[2]), re.escape(m[2])), text)
    if d is None or d.start() < pos or not d[1]:
        return p
    return '%s%s %s%s' % (m[1] or '', d[1], m[2], m[3])
