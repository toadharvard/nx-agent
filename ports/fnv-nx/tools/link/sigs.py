"""sigs.py - parse demangled MSVC (llvm-undname) and Itanium (c++filt) names into comparable signatures.

Both sides are reduced to the same canonical spelling so that a declaration seen by MSVC (units.json) and the same
declaration seen by GCC (object symbol) produce equal keys:

  * qualified name: components joined by '::', 'class/struct/union/enum' tags dropped, `anonymous namespace' and
    (anonymous namespace) both '{anon}', constructor/destructor names '{ctor}' / '{dtor}';
  * parameter types: tags and calling conventions dropped, MSVC __int64 spelled 'long long', blanks only between
    two identifier characters, top-level cv of by-value parameters dropped (Itanium drops it, MSVC keeps it),
    '(void)' -> '()';
  * loose levels for declarations that differ in ways that do not change the address they name:
    L1 - 'long' == 'int', 'wchar_t' == 'unsigned short' (ILP32 / MSVC typedef differences: size_t, DWORD, ...),
    L2 - L1 and every cv-qualifier dropped.

Sig.keys() gives the lookup keys from strict to loose; the matcher (fnvlink.py) tries them in that order.
"""
import os
import re

CC = ('__cdecl', '__stdcall', '__fastcall', '__thiscall', '__clrcall', '__vectorcall')
OPS = sorted(['()', '[]', '->*', '->', '<<=', '>>=', '<=>', '<<', '>>', '<=', '>=', '==', '!=', '&&', '||', '++', '--',
              '+=', '-=', '*=', '/=', '%=', '&=', '|=', '^=', '-', '+', '*', '/', '%', '^', '&', '|', '~', '!', '=',
              '<', '>', ','], key=len, reverse=True)
OPEN, CLOSE = '(<[', ')>]'
WORD = re.compile(r'\w')


def _operator_len(s, i):
    """If s[i:] starts the keyword 'operator' (as a name), return the length of the whole operator token
    ('operator<<', 'operator new[]', 'operator ' for a conversion operator), else 0."""
    if not s.startswith('operator', i) or (i > 0 and WORD.match(s[i - 1])):
        return 0
    j = i + 8
    if j < len(s) and WORD.match(s[j]):
        return 0
    k = j
    while k < len(s) and s[k] == ' ':
        k += 1
    for kw in ('new[]', 'delete[]', 'new', 'delete'):
        if s.startswith(kw, k) and not (k + len(kw) < len(s) and WORD.match(s[k + len(kw)])):
            k += len(kw)
            if s.startswith(' []', k):
                k += 3
            return k - i
    for op in OPS:
        if s.startswith(op, k):
            return k + len(op) - i
    return j - i  # conversion operator: the type follows as ordinary text


def top_level(s):
    """Yield (index, char) of the characters of s that are at bracket depth 0 (operator tokens skipped)."""
    depth, i, n = 0, 0, len(s)
    while i < n:
        ol = _operator_len(s, i)
        if ol:
            if depth == 0:
                for k in range(i, i + ol):
                    yield k, None  # inside an operator token: never a separator
            i += ol
            continue
        c = s[i]
        if c in OPEN:
            if depth == 0:
                yield i, c
            depth += 1
        elif c in CLOSE:
            depth -= 1
            if depth == 0:
                yield i, c
        elif depth == 0:
            yield i, c
        i += 1


def _split_top(s, sep=','):
    out, last = [], 0
    for i, c in top_level(s):
        if c == sep:
            out.append(s[last:i]); last = i + 1
    out.append(s[last:])
    return [p.strip() for p in out]


def _split_qual(s):
    """'A<B::C>::D::f' -> ['A<B::C>', 'D', 'f'] (top-level '::' only)."""
    out, last, prev = [], 0, -2
    for i, c in top_level(s):
        if c == ':' and prev == i - 1 and s[i - 1] == ':':
            out.append(s[last:i - 1]); last = i + 1
        prev = i if c == ':' else -2
    out.append(s[last:])
    return [p.strip() for p in out if p.strip()]


CONV_OP = re.compile(r'(?:^|::)\s*operator (?!new\b|delete\b)(?=[A-Za-z_])')


def _split_qual_conv(qual):
    """split_qual for a conversion operator too: 'A::B::operator struct N::T *' -> ['A', 'B', 'operator N::T*'] (the
    target type is one component, canonical, so MSVC and Itanium spell it the same)."""
    m = CONV_OP.search(qual)
    if not m:
        return split_qual(qual)
    at = qual.index('operator', m.start())
    return split_qual(qual[:at].rstrip(':').strip()) + ['operator ' + canon_type(qual[at + len('operator '):])]


def matching_open(s, close_at):
    depth = 0
    for i in range(close_at, -1, -1):
        if s[i] in CLOSE:
            depth += 1
        elif s[i] in OPEN:
            depth -= 1
            if depth == 0:
                return i
    return -1


def matching_close(s, open_at):
    depth = 0
    for i in range(open_at, len(s)):
        if s[i] in OPEN:
            depth += 1
        elif s[i] in CLOSE:
            depth -= 1
            if depth == 0:
                return i
    return -1


# ---------------------------------------------------------------------------------------------------- canonical types

TAGS = re.compile(r'\b(?:class|struct|union|enum)\s+')
NOISE = re.compile(r'\b(?:__cdecl|__stdcall|__fastcall|__thiscall|__clrcall|__vectorcall|__ptr64|__ptr32|__restrict|'
                   r'__w64|__unaligned|__sptr|__uptr)\b')
INTS = [(re.compile(r'\bunsigned __int64\b'), 'unsigned long long'), (re.compile(r'\b__int64\b'), 'long long'),
        (re.compile(r'\bunsigned __int32\b'), 'unsigned int'), (re.compile(r'\b__int32\b'), 'int'),
        (re.compile(r'\bunsigned __int16\b'), 'unsigned short'), (re.compile(r'\b__int16\b'), 'short'),
        (re.compile(r'\bunsigned __int8\b'), 'unsigned char'), (re.compile(r'\b__int8\b'), 'char')]
LIT_SUFFIX = re.compile(r'\b(\d+)(?:ull|ul|ll|u|l)\b', re.I)
LIT_CAST = re.compile(r'\((?:unsigned |signed )?(?:char|short|int|long|long long|bool)\)(-?\d+)')
SPACE_NONWORD = re.compile(r' (?=\W)|(?<=\W) ')
LONG = re.compile(r'\b(?<!long )long\b(?! long| double)')


def _canon_type(t, level=0):
    t = t.replace('`anonymous namespace\'', '{anon}').replace('(anonymous namespace)', '{anon}')
    t = TAGS.sub('', t)
    t = NOISE.sub('', t)
    for rx, rep in INTS:
        t = rx.sub(rep, t)
    t = ' '.join(t.split())
    t = SPACE_NONWORD.sub('', t)
    t = LIT_CAST.sub(r'\1', t)
    t = LIT_SUFFIX.sub(r'\1', t)
    t = t.replace('(void)', '()')
    if level >= 1:
        t = LONG.sub('int', t)
        t = re.sub(r'\bwchar_t\b', 'unsigned short', t)
        t = re.sub(r'\bsigned char\b', 'char', t)
    if level >= 2:
        t = re.sub(r'\b(?:const|volatile)\b', '', t)
        t = ' '.join(t.split()); t = SPACE_NONWORD.sub('', t)
    return t


def _canon_param(t, level=0):
    t = canon_type(t, level)
    while True:  # top-level cv of a by-value parameter is not part of the type (Itanium drops it)
        m = re.match(r'^(.*?)(?:\s*\b(?:const|volatile))$', t)
        if not m or not m.group(1) or m.group(1).endswith(('(', ',')):
            return t
        t = m.group(1).rstrip()


def _canon_qual(parts):
    """Canonical qualified-name components; ctor/dtor names become {ctor}/{dtor}."""
    parts = [canon_type(p) for p in parts]
    if len(parts) >= 2:
        cls = re.sub(r'<.*$', '', parts[-2])
        last = re.sub(r'<.*$', '', parts[-1])
        if last == cls:
            parts[-1] = '{ctor}'
        elif last == '~' + cls:
            parts[-1] = '{dtor}'
    return parts


# ---------------------------------------------------------------------------------------------------- memoized
# T1 (incremental link): the link calls these pure string functions millions of times on far fewer distinct strings
# (resolve of 31971038: canon_type 3.3M calls, 49 s of 255 s under the profiler). Memoized per process; the
# list-returning ones keep a tuple and give every caller its own list, so a caller that edits the list cannot change
# what the next caller gets. Same results, by construction.
_memo = {}


def _cached(fn, key, *args):
    try:
        return _memo[key]
    except KeyError:
        v = _memo[key] = fn(*args)
        return v


def split_top(s, sep=','):
    return list(_cached(lambda: tuple(_split_top(s, sep)), ('st', s, sep)))


def split_qual(s):
    return list(_cached(lambda: tuple(_split_qual(s)), ('sq', s)))


def split_qual_conv(qual):
    return list(_cached(lambda: tuple(_split_qual_conv(qual)), ('sqc', qual)))


def canon_type(t, level=0):
    return _cached(_canon_type, ('ct', t, level), t, level)


def canon_param(t, level=0):
    return _cached(_canon_param, ('cp', t, level), t, level)


def canon_qual(parts):
    parts = tuple(parts)
    return list(_cached(lambda: tuple(_canon_qual(list(parts))), ('cq', parts)))


# ---------------------------------------------------------------------------------------------------- signatures

class Sig:
    """kind: func | data | cname (unmangled: C function/variable or namespace-scope C++ variable) | special."""
    __slots__ = ('kind', 'qual', 'params', 'cv', 'ret', 'cc', 'member', 'static', 'virtual', 'variant', 'raw_params',
                 'raw_ret', 'special', 'stack_bytes', 'text')

    def __init__(self, kind, text=''):
        self.kind, self.text = kind, text
        self.qual, self.params, self.cv, self.ret, self.cc = [], None, '', None, None
        self.member = self.static = self.virtual = None
        self.variant = self.special = self.stack_bytes = None
        self.raw_params, self.raw_ret = None, None

    @property
    def name(self):
        return '::'.join(self.qual)

    def keys(self):
        """Lookup keys, strict -> loose: (tier, key)."""
        if self.kind == 'func' and self.params is not None:
            p0 = tuple(canon_param(p, 0) for p in self.params)
            p1 = tuple(canon_param(p, 1) for p in self.params)
            p2 = tuple(canon_param(p, 2) for p in self.params)
            return [('exact', ('f', self.name, p0, self.cv)), ('l1', ('f', self.name, p1)),
                    ('l2', ('f', self.name, p2)), ('arity', ('n', self.name, len(self.params))),
                    ('name', ('q', self.name))]
        if self.kind in ('data', 'func', 'cname'):
            return [('name', ('q', self.name))]
        return []

    def __repr__(self):
        return 'Sig(%s %s %r %s)' % (self.kind, self.name, self.params, self.cc or '')


def _parse_params(s):
    """s = text between the parameter parentheses."""
    s = s.strip()
    if s in ('', 'void'):
        return []
    return split_top(s)


# --- MSVC (llvm-undname output)

MS_SPECIAL = {"`scalar deleting dtor'": '{sdd}', "`vector deleting dtor'": '{vdd}'}


DATA_DECO = re.compile(r'^\?([A-Za-z_]\w*)((?:@[A-Za-z_]\w*)*)@@[0-4]')   # ?name@scope...@@<storage class>


def _parse_msvc(mangled, dem):
    """Parse one llvm-undname line. dem is None when undname failed."""
    if not mangled.startswith('?'):
        return parse_msvc_c(mangled)
    if dem is None or dem.startswith('"'):
        return Sig('special', mangled)
    s = dem.replace("`anonymous namespace'", '{anon}')
    for k, v in MS_SPECIAL.items():
        s = s.replace(k, v)
    sig = Sig('func', mangled)
    if s.startswith('[thunk]:') or '`' in s:
        sig.kind, sig.special = 'special', s
        return sig
    m = re.match(r'^(?:(public|protected|private): )?((?:static |virtual )*)', s)
    access, quals = m.group(1), m.group(2)
    s = s[m.end():]
    cc_at = None
    for i, c in top_level(s):
        if c == '_':
            for cc in CC:
                if s.startswith(cc, i) and (i == 0 or s[i - 1] == ' ') and not WORD.match(s[i + len(cc):i + len(cc) + 1] or ' '):
                    cc_at = (i, cc); break
            if cc_at:
                break
    if cc_at is None:  # data
        sig.kind = 'data'
        # a function-pointer variable: 'void (__cdecl *g_fn)(int)' / '(__stdcall *const ns::p)(void)' / a pointer to
        # one ('void (__cdecl **g_apfn)(void *)', S5: tables of handlers), or a
        # pointer to an array: 'T *(*ns::g_x)[10]' (one character per repetition: a nested '+' backtracks
        # exponentially on long names that do not match)
        fp = re.match(r'^(.*?)\(\s*(?:__\w+\s+)?(?:\*\s*(?:const\s+)?)+((?:[\w`\'{}]|::|<[^()]*>)+)\s*\)\s*'
                      r'(?:\(.*\)|\[[^\]]*\])\s*$', s)
        if fp:
            sig.qual = canon_qual(split_qual(fp.group(2)))
            sig.raw_ret = s[:fp.start(2)].strip()
            sig.static = access is not None
            return sig
        name_at = 0
        for i, c in top_level(s):
            if c in (' ', '*', '&'):
                name_at = i + 1
        sig.qual = canon_qual(split_qual(s[name_at:]))
        sig.raw_ret = s[:name_at].strip()
        sig.static = access is not None
        # the decoration of a plain variable spells its qualified name (`?name@inner@outer@@3...`); llvm-undname
        # drops the space after a type whose name ends in '_' ('class Setting_x' for `?x@@3VSetting_@@A`), so the
        # name read from the decoration wins (S5: the settings, bKeepLowDetailTerrain)
        dm = DATA_DECO.match(mangled)
        if dm:
            sig.qual = canon_qual(list(reversed(dm.group(2).split('@')[1:])) + [dm.group(1)])
        return sig
    i, cc = cc_at
    sig.cc = cc[2:]
    pre, post = s[:i].strip(), s[i + len(cc):].strip()
    popen = None
    for j, c in top_level(post):
        if c == '(':
            popen = j; break
    if popen is None:
        sig.kind = 'special'; return sig
    pclose = matching_close(post, popen)
    qual = post[:popen].strip()
    # an operator ends in '*' too: operator*, operator*=, operator->*, a conversion operator 'operator T *'
    op = (split_qual_conv(qual) or [''])[-1].startswith('operator')
    # a function returning a function pointer is unsupported; `X::operator()` ends in ')' too and is a plain name
    # (R2 recon2-005: `??RFUN_006592f0@@QAEXPAX@Z` parsed `special`, its references stayed trap stubs)
    if (qual.endswith('*') and not op) or (qual.endswith(')') and not re.search(r'(?:^|::)operator\(\)$', qual)):
        sig.kind = 'special'; return sig
    sig.qual = canon_qual(split_qual_conv(qual))
    sig.raw_params = _parse_params(post[popen + 1:pclose])
    sig.params = sig.raw_params
    tail = post[pclose + 1:]
    cv = [w for w in ('const', 'volatile') if re.search(r'\b%s\b' % w, tail)]
    sig.cv = ' '.join(cv)
    sig.raw_ret = pre or None
    sig.ret = canon_type(pre) if pre else None
    sig.static = 'static' in quals
    sig.virtual = 'virtual' in quals
    sig.member = access is not None and not sig.static
    last = sig.qual[-1] if sig.qual else ''
    sig.variant = {'{ctor}': 'ctor', '{dtor}': 'dtor', '{sdd}': 'sdd', '{vdd}': 'vdd'}.get(last)
    return sig


def parse_msvc_c(mangled):
    """Undecorated (C) names: _f (cdecl or data), _f@N (stdcall), @f@N (fastcall), __imp__f@N (import)."""
    s = mangled
    sig = Sig('cname', mangled)
    if s.startswith('__imp_'):
        sig.special = 'import'
        s = s[6:]
        if s.startswith('?'):
            sig.kind = 'special'; return sig
    if s.startswith(('$', '__ehhandler$', '__real@', '__xmm@', '__unwindfunclet$', '__catch$', '__tryblocktable')):
        sig.kind, sig.special = 'special', 'compiler'
        return sig
    m = re.match(r'^@(\w+)@(\d+)$', s)
    if m:
        sig.qual, sig.cc, sig.stack_bytes = [m.group(1)], 'fastcall', int(m.group(2))
        return sig
    m = re.match(r'^_(\w+?)@(\d+)$', s)
    if m:
        sig.qual, sig.cc, sig.stack_bytes = [m.group(1)], 'stdcall', int(m.group(2))
        return sig
    if s.startswith('_'):
        sig.qual = [s[1:]]
        return sig
    sig.qual = [s]
    return sig


# --- Itanium (c++filt output)

IT_SPECIAL = ('vtable for ', 'typeinfo for ', 'typeinfo name for ', 'VTT for ', 'construction vtable for ',
              'guard variable for ', 'non-virtual thunk to ', 'virtual thunk to ', 'covariant return thunk to ',
              'transaction clone for ', 'TLS init function for ', 'TLS wrapper function for ', 'reference temporary ',
              'hidden alias for ', 'java ')
CLONE = re.compile(r'(?: \[clone [^\]]*\])+$')
ABI_TAG = re.compile(r'\[abi:[^\]]*\]')
CTOR_VARIANT = re.compile(r'(C[1-5]|D[0-5])(?=E|I|B)')


ITANIUM_INT_LITERAL = re.compile(r'L[abcdhijlmstwxy]n?\d+E')


def itanium_variant(mangled):
    """Constructor/destructor variant of an Itanium name ('C1', 'C2', 'D0', 'D1', 'D2') or None (heuristic: the last
    C<n>/D<n> token followed by E, I or B, not inside a <length><identifier> source name)."""
    if not mangled.startswith('_Z'):
        return None
    best, i, n = None, 2, len(mangled)
    while i < n:
        c = mangled[i]
        if c.isdigit():  # <source-name> ::= <length> <identifier>: skip the identifier
            j = i
            while j < n and mangled[j].isdigit():
                j += 1
            i = j + int(mangled[i:j])
            continue
        lit = ITANIUM_INT_LITERAL.match(mangled, i) if c == 'L' else None
        if lit:  # <expr-primary> ::= L <builtin type> <number> E (`Lj1024E`): the number is no <length>
            i = lit.end()
            continue
        if c in 'CD' and i + 2 < n and mangled[i + 1] in '012345' and mangled[i + 2] in 'EIB':
            best = mangled[i:i + 2]
        i += 1
    return best


def _parse_itanium(mangled, dem):
    if not mangled.startswith('_Z'):
        sig = Sig('cname', mangled)
        sig.qual = [mangled]
        return sig
    if dem is None or dem == mangled:
        return Sig('special', mangled)
    s = CLONE.sub('', dem)
    s = ABI_TAG.sub('', s).replace('(anonymous namespace)', '{anon}')
    for p in IT_SPECIAL:
        if s.startswith(p):
            sig = Sig('special', mangled)
            sig.special = p.strip()
            sig.qual = canon_qual(split_qual(s[len(p):]))
            return sig
    sig = Sig('func', mangled)
    tail = re.search(r'\)((?:\s*(?:const|volatile|&&|&))*)$', s)
    if not tail:
        sig.kind = 'data'
        sig.qual = canon_qual(split_qual(s))
        return sig
    pclose = tail.start()
    popen = matching_open(s, pclose)
    if popen < 0:
        return Sig('special', mangled)
    head = s[:popen]
    # template functions carry their return type first: 'int f<int>(int)' - split at the last top-level blank that
    # does not belong to an operator token.
    cut, conv = 0, re.search(r'(?:^|::|\s)operator \w', head)  # conversion operator: 'X::operator int'
    for i, c in top_level(head):
        if conv and i >= conv.start():
            break
        if c == ' ' and i + 1 < len(head):
            cut = i + 1
    qual = head[cut:].strip() if cut else head.strip()
    if not qual or qual.startswith(('*', '&')):
        qual = head.strip(); cut = 0
    sig.qual = canon_qual(split_qual_conv(qual))
    sig.params = _parse_params(s[popen + 1:pclose])
    sig.raw_params = sig.params
    sig.cv = ' '.join(w for w in ('const', 'volatile') if re.search(r'\b%s\b' % w, tail.group(1)))
    sig.ret = canon_type(head[:cut]) if cut else None
    sig.variant = itanium_variant(mangled)
    return sig


# ---------------------------------------------------------------------------------------------------- persisted parses
# T1 (incremental link): every link process (resolve 1, resolve 2, bodies, vtables) parsed the same ~450k names
# again (14 s each on 31971038). persist(path) keeps the parses of one process for the next: per (kind, name,
# demangled text) the Sig's slot values (marshal: str, int, None, lists and tuples keep their types), under the
# sha256 of this file (any change to the parser: an empty store). A hit builds a new Sig from them each time, as a
# parse would, so callers never share one; a Sig with a value marshal cannot hold is simply not stored.
_P = {'path': None, 'map': None, 'used': None, 'new': 0, 'prune': False}
_SLOTS = Sig.__slots__


def _code_id():
    import hashlib
    return 'sigs-persist-2 ' + hashlib.sha256(open(os.path.abspath(__file__), 'rb').read()).hexdigest()


def persist(path, prune=False):
    """Use (and at exit write) the parse store at path. prune: keep only this process's entries (fnvlink resolve,
    which parses nearly every name of the link; the other steps add to the store). Off with FNVNX_SIGS_PERSIST=0."""
    import atexit, marshal
    if os.environ.get('FNVNX_SIGS_PERSIST', '1') == '0' or _P['path']:
        return
    m = {}
    try:
        with open(path, 'rb') as f:
            d = marshal.load(f)
        if isinstance(d, dict) and d.get('id') == _code_id() and isinstance(d.get('map'), dict):
            m = d['map']
    except (OSError, EOFError, ValueError, TypeError):
        m = {}
    _P.update(path=path, map=m, used=set(), new=0, prune=prune)
    atexit.register(_persist_save)


def _persist_save():
    import marshal
    path, m, used = _P['path'], _P['map'], _P['used']
    if not path or not (_P['new'] or (_P['prune'] and len(used) < len(m))):
        return
    keep = {k: m[k] for k in used if k in m} if _P['prune'] else m
    tmp = '%s.%d.tmp' % (path, os.getpid())
    try:
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(tmp, 'wb') as f:
            marshal.dump({'id': _code_id(), 'map': keep}, f)
        os.replace(tmp, path)
    except (OSError, ValueError):
        try:
            os.remove(tmp)
        except OSError:
            pass


def _persisted(kind, fn, mangled, dem):
    m = _P['map']
    if m is None:
        return fn(mangled, dem)
    import marshal
    k = (kind, mangled, dem)
    b = m.get(k)
    _P['used'].add(k)
    if b is not None:
        sig = Sig.__new__(Sig)
        for a, v in zip(_SLOTS, marshal.loads(b)):
            setattr(sig, a, v)
        return sig
    sig = fn(mangled, dem)
    try:
        m[k] = marshal.dumps(tuple(getattr(sig, a) for a in _SLOTS))
        _P['new'] += 1
    except ValueError:   # a value marshal cannot hold: not stored
        pass
    return sig


def parse_msvc(mangled, dem):
    """Parse one llvm-undname line. dem is None when undname failed."""
    return _persisted('m', _parse_msvc, mangled, dem)


def parse_itanium(mangled, dem):
    return _persisted('i', _parse_itanium, mangled, dem)

