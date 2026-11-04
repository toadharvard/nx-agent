"""v1names.py - V1-link: compare an Itanium name (c++filt text) with MSVC names (llvm-undname text) by qualified name
and parameter types. Written for V1 from the demanglers' output formats; shares nothing with tools/link/sigs.py.

    key_itanium(text) / key_msvc(text) -> Key(qname, params tuple or None for variables, const method flag)
    match(itanium_key, [(msvc_key, address)]) -> (address or None, how)
"""
import re
import subprocess

TAGS = re.compile(r'\b(class|struct|union|enum)\s+')
CCS = re.compile(r'\b__(cdecl|stdcall|fastcall|thiscall|clrcall|vectorcall|ptr64|restrict|unaligned)\b')
ACCESS = re.compile(r'^(\[thunk\]:\s*)?(public|protected|private):\s*')


class Key:
    __slots__ = ('qname', 'params', 'const', 'text')

    def __init__(self, qname, params, const, text):
        self.qname, self.params, self.const, self.text = qname, params, const, text

    def __repr__(self):
        return '%s(%s)%s' % (self.qname, ', '.join(self.params) if self.params is not None else '-',
                             ' const' if self.const else '')


def tok_norm(t):
    """Canonical spelling of a type: tags and conventions dropped, MSVC int names mapped, blanks only between words."""
    t = TAGS.sub('', t)
    t = CCS.sub('', t)
    t = t.replace('`anonymous namespace\'', '{anon}').replace('(anonymous namespace)', '{anon}')
    t = re.sub(r'\b__int64\b', 'long long', t)
    t = re.sub(r'\bunsigned __int64\b', 'unsigned long long', t)
    t = re.sub(r'\b__int(8|16|32)\b', lambda m: {'8': 'char', '16': 'short', '32': 'int'}[m.group(1)], t)
    t = re.sub(r'\bsigned char\b', '<schar>', t)
    t = re.sub(r'\bsigned\s+', '', t).replace('<schar>', 'signed char')
    parts = re.findall(r'[A-Za-z_0-9{}~$`\']+|[^\sA-Za-z_0-9{}~$`\']', t)
    out = ''
    prev_word = False
    for p in parts:
        w = bool(re.match(r'[A-Za-z_0-9{}~$`\']', p))
        if w and prev_word:
            out += ' '
        out += p
        prev_word = w
    out = re.sub(r'(?<=[*&])const$', '', out)        # top-level const of a pointer parameter
    out = re.sub(r'^const (?=[^*&]*$)', '', out)       # top-level const of a value parameter
    out = re.sub(r' const$', '', out) if not re.search(r'[*&]', out) else out
    return out


def split_top(s):
    out, depth, cur = [], 0, ''
    for c in s:
        if c in '(<[':
            depth += 1
        elif c in ')>]':
            depth -= 1
        if c == ',' and depth == 0:
            out.append(cur.strip())
            cur = ''
        else:
            cur += c
    if cur.strip():
        out.append(cur.strip())
    return out


def _func_parts(s):
    """'A::f(int, char*) const' -> ('A::f', 'int, char*', ' const') at the last top-level parameter list."""
    depth = 0
    end = None
    for i in range(len(s) - 1, -1, -1):
        c = s[i]
        if c == ')':
            if depth == 0:
                end = i
            depth += 1
        elif c == '(':
            depth -= 1
            if depth == 0 and end is not None:
                head = s[:i]
                # an operator() name
                if head.endswith('operator') or head.endswith('operator()'):
                    pass
                return head, s[i + 1:end], s[end + 1:]
    return None


def key_itanium(text):
    s = text.strip()
    s = re.sub(r'^(non-virtual thunk to|virtual thunk to|covariant return thunk to)\s+', '', s)
    fp = _func_parts(s)
    if fp is None or s.startswith(('vtable for', 'typeinfo', 'VTT', 'guard variable', 'construction vtable')):
        return Key(tok_norm(s), None, False, text)
    head, params, tail = fp
    head = re.sub(r'\[abi:[^\]]*\]', '', head)
    ps = tuple(tok_norm(p) for p in split_top(params) if p not in ('void',))
    # templates with return type: 'int f<int>(int)' -> drop the leading return type
    q = head.strip()
    m = re.match(r'^(.*\S)\s+((?:[\w{}~]+(?:<.*>)?::)*[\w{}~]+(?:<.*>)?)$', q)
    if m and '<' in q and not q.startswith('operator'):
        q = m.group(2)
    return Key(qnorm(tok_norm(q)), ps, 'const' in tail, text)


def qnorm(q):
    """A ctor/dtor of a template spelled with its arguments (MSVC: X<int>::X<int>) -> X<int>::X."""
    parts = split_scope(q)
    if len(parts) >= 2:
        last, cls = parts[-1], parts[-2]
        base = re.sub(r'<.*$', '', cls)
        lb = last.lstrip('~')
        if re.sub(r'<.*$', '', lb) == base and '<' in lb:
            parts[-1] = ('~' if last.startswith('~') else '') + base
    return '::'.join(parts)


def split_scope(q):
    out, depth, cur, i = [], 0, '', 0
    while i < len(q):
        ch = q[i]
        if ch in '<(':
            depth += 1
        elif ch in '>)':
            depth -= 1
        if depth == 0 and q.startswith('::', i):
            out.append(cur)
            cur = ''
            i += 2
            continue
        cur += ch
        i += 1
    out.append(cur)
    return out


def key_msvc(text):
    s = ACCESS.sub('', text.strip())
    s = re.sub(r'^(static|virtual)\s+', '', s)
    s = re.sub(r'^(static|virtual)\s+', '', s)
    fp = _func_parts(s)
    if fp is None or not re.search(r'__(cdecl|stdcall|fastcall|thiscall)\b', fp[0] if fp else ''):
        # variable: '<type> <qualified name>': the last blank-separated piece outside template brackets
        t = re.sub(r'\[\d*\]', '', s).strip()
        depth, cut = 0, 0
        for i, ch in enumerate(t):
            if ch in '<(':
                depth += 1
            elif ch in '>)':
                depth -= 1
            elif ch == ' ' and depth == 0:
                cut = i + 1
        return Key(qnorm(tok_norm(t[cut:].lstrip('*&'))), None, False, text)
    head, params, tail = fp
    m = re.search(r'__(?:cdecl|stdcall|fastcall|thiscall)\s+(.*)$', head)
    q = m.group(1) if m else head
    q = re.sub(r"`adjustor\{\d+\}'", '', q)
    ps = tuple(tok_norm(p) for p in split_top(params) if p not in ('void',))
    return Key(qnorm(tok_norm(q)), ps, 'const' in tail, text)


L1 = [(re.compile(r'\blong\b(?! long)'), 'int'), (re.compile(r'\bwchar_t\b'), 'unsigned short'),
      (re.compile(r'\bunsigned int\b'), 'int'), (re.compile(r'\bunsigned char\b'), 'char'),
      (re.compile(r'\bunsigned short\b'), 'short')]


def loose(p):
    for rx, r in L1:
        p = rx.sub(r, p)
    return re.sub(r'\bconst\b', '', p).replace(' ', '')


def match(ik, cands):
    """cands: [(Key, address)]. Strict -> loose types -> arity -> name. Returns (address, how) or (None, why)."""
    same = [(k, a) for k, a in cands if k.qname == ik.qname]
    if not same:
        # unqualified C names vs qualified MSVC statics / vice versa
        base = ik.qname.split('::')[-1]
        same = [(k, a) for k, a in cands if k.qname.split('::')[-1] == base and ('::' not in k.qname or
                                                                                   '::' not in ik.qname)]
        if not same:
            return None, 'no-name'
    if ik.params is None:
        addrs = {a for k, a in same if k.params is None} or {a for k, a in same}
        return (addrs.pop(), 'var') if len(addrs) == 1 else (None, 'var-ambiguous')
    for how, f in (('exact', lambda k: k.params == ik.params and k.const == ik.const),
                   ('types', lambda k: k.params == ik.params),
                   ('loose', lambda k: k.params is not None and tuple(map(loose, k.params)) ==
                    tuple(map(loose, ik.params))),
                   ('arity', lambda k: k.params is not None and len(k.params) == len(ik.params)),
                   ('name', lambda k: True)):
        addrs = {a for k, a in same if f(k)}
        if len(addrs) == 1:
            return addrs.pop(), how
        if len(addrs) > 1:
            return None, how + '-ambiguous'
    return None, 'none'


def cxxfilt(names):
    if not names:
        return {}
    p = subprocess.run(['c++filt'], input='\n'.join(names) + '\n', capture_output=True, text=True)
    return dict(zip(names, p.stdout.split('\n')))


def undname(names):
    names = [n for n in names if n]
    if not names:
        return {}
    p = subprocess.run(['llvm-undname'], input='\n'.join(names) + '\n', capture_output=True, text=True)
    lines, out, i = p.stdout.split('\n'), {}, 0
    for n in names:
        while i < len(lines) and lines[i] != n:
            i += 1
        out[n] = lines[i + 1] if i + 1 < len(lines) else ''
        i += 2
    return out
