"""_msvcsig.py - the subset of MSVC 2008 decorated function names the S2-bodies call-site rule reads (POP2:
tools/rules/s2_bodies.py): scopes, member kind, convention, result, parameter codes and the stack words a call pushes.
A helper module of tools/rules (prepare loads no RULES from a name starting with `_`)."""

SIMPLE = {'C': 1, 'D': 1, 'E': 1, 'F': 1, 'G': 1, 'H': 1, 'I': 1, 'J': 1, 'K': 1, 'M': 1, 'N': 2, 'O': 2}
SIMPLE_ = {'N': 1, 'J': 2, 'K': 2, 'W': 1, 'D': 1, 'E': 1, 'F': 1, 'G': 1, 'H': 1, 'I': 1}
INTLIKE = set('CDEFGHIJK')        # integer types of at most 4 bytes (fastcall register candidates)


class Bad(Exception):
    pass


class Fn:
    """A parsed name: params are (literal code or None, words, kind); a type back-reference stands for the code it
    names, name back-references are spelled out (the span in the name stays the original's)."""
    __slots__ = ('ident', 'scopes', 'special', 'member', 'static', 'virtual', 'cc', 'const', 'ret', 'ret_class',
                 'params', 'varargs', 'head_end', 'ret_span', 'param_spans', 'tail', 'name_refs')


class _St:
    def __init__(self, s):
        self.s, self.table, self.poisoned, self.name_refs = s, [], False, False


def _name(st, i):
    """A qualified name's fragments from s[i] up to its terminating '@' -> (fragments innermost first, end, literal
    spelling or None). MSVC's name back-reference table: each new identifier is added, a digit names an earlier one;
    the literal spelling writes every fragment out (valid in any other name, where the table differs)."""
    s, frags, lit = st.s, [], True
    while True:
        c = s[i]
        if c == '@':
            return frags, i + 1, (''.join(f + '@' for f in frags) + '@' if lit else None)
        if c.isdigit():
            n = int(c)
            if st.poisoned or n >= len(st.table):
                raise Bad('name backref')
            frags.append(st.table[n])
            st.name_refs = True
            i += 1
        elif s.startswith('?$', i):
            j = s.index('@', i + 2)
            nm = s[i + 2:j]
            i = j + 1
            while s[i] != '@':
                if s[i] == '$':
                    raise Bad('template value')
                i = _type(st, i)[0]
            st.poisoned = True        # template names enter the table as a whole: indices after it are not modelled
            frags.append(nm + '<>')
            lit = False
            i += 1
        elif c == '?':
            raise Bad('nested special')
        else:
            j = s.index('@', i)
            frags.append(s[i:j])
            if s[i:j] not in st.table and len(st.table) < 10:
                st.table.append(s[i:j])
            i = j + 1


def _type(st, i):
    """-> (end, words or None, kind, literal spelling or None) of the type encoded at s[i]."""
    s = st.s
    c = s[i]
    if c in SIMPLE:
        return i + 1, SIMPLE[c], 'int' if c in INTLIKE else ('float' if c == 'M' else 'double'), c
    if c == '_':
        d = s[i + 1]
        if d not in SIMPLE_:
            raise Bad('_' + d)
        return i + 2, SIMPLE_[d], 'int' if SIMPLE_[d] == 1 else 'int64', s[i:i + 2]
    if c in 'PQRS' or c in 'AB':
        if c in 'PQRS' and s[i + 1] in '6$':
            raise Bad('function pointer')
        if s[i + 1] not in 'ABCD':
            raise Bad('pointer cv')
        e, _, _, sub = _type(st, i + 2)
        return e, 1, 'ptr', (s[i:i + 2] + sub if sub is not None else None)
    if c in 'UVT':
        _, e, lit = _name(st, i + 1)
        return e, None, 'class', (c + lit if lit is not None else None)
    if c == 'W':
        if s[i + 1] != '4':
            raise Bad('enum width')
        _, e, lit = _name(st, i + 2)
        return e, 1, 'int', ('W4' + lit if lit is not None else None)
    if c == 'X':
        return i + 1, 0, 'void', 'X'
    if c.isdigit():
        return i + 1, None, 'ref' + c, None
    raise Bad('type ' + c)


MEMBER = {}
for _l in 'ABIJQR':
    MEMBER[_l] = ('member', False)
for _l in 'CDKLST':
    MEMBER[_l] = ('static', False)
for _l in 'EFMNUV':
    MEMBER[_l] = ('member', True)
CC = {'A': 'cdecl', 'C': 'pascal', 'E': 'thiscall', 'G': 'stdcall', 'I': 'fastcall'}


def parse(s):
    """MSVC decorated function name (the subset the decomp's call-site rules need) -> Fn, or None."""
    try:
        return _parse(s)
    except (Bad, IndexError, ValueError):
        return None


def _parse(s):
    f, st = Fn(), _St(s)
    if s.startswith('??B'):
        f.special, f.ident, i = 'operator bool', None, 3
    elif s.startswith('??') or not s.startswith('?'):
        return None
    else:
        j = s.index('@', 1)
        f.special, f.ident = None, s[1:j]
        st.table.append(f.ident)
        i = j + 1
    f.scopes, i, _ = _name(st, i)               # innermost first
    if any(x.endswith('<>') for x in f.scopes):
        raise Bad('template scope')
    k = s[i]
    f.const = False
    if k in 'YZ':
        f.member, f.static, f.virtual = False, False, False
        i += 1
    elif k in MEMBER:
        kind, f.virtual = MEMBER[k]
        f.member, f.static = True, kind == 'static'
        i += 1
        if not f.static:
            if s[i] == 'E':
                i += 1
            if s[i] not in 'ABCD':
                raise Bad('this cv')
            f.const = s[i] in 'BD'
            i += 1
    else:
        raise Bad('function class ' + k)
    f.cc = CC.get(s[i])
    if f.cc is None:
        raise Bad('cc')
    i += 1
    f.head_end = r0 = i
    f.ret_class = None
    if s[i] == '?':
        if s[i + 1] not in 'AB':
            raise Bad('ret cv')
        if s[i + 2] in 'UVT':
            f.ret_class, i, _ = _name(st, i + 3)
        else:
            i = _type(st, i + 2)[0]
    elif s[i] == '@':
        raise Bad('ctor')
    else:
        i = _type(st, i)[0]
    f.ret_span, f.ret = (r0, i), s[r0:i]
    f.params, f.param_spans, f.varargs = [], [], False
    memo = []
    if s[i] == 'X':
        i += 1
    else:
        while s[i] not in '@Z':
            e, w, kd, code = _type(st, i)
            if kd.startswith('ref'):         # a type back-reference: the code it stands for
                n = int(kd[3:])
                if n >= len(memo):
                    raise Bad('type backref')
                w, kd, code = memo[n]
            elif e - i > 1:
                memo.append((w, kd, code))
            f.params.append((code, w, kd))
            f.param_spans.append((i, e))
            i = e
        f.varargs = s[i] == 'Z'
        i += 1
    if s[i:] != 'Z':
        raise Bad('tail %r' % s[i:])
    f.tail, f.name_refs = i, st.name_refs
    return f


def callee_pop(f):
    return f.cc in ('thiscall', 'stdcall', 'fastcall') and not f.varargs


def stack_words(f):
    """Stack words a call of f pushes (and a callee-pop f pops), the hidden result pointer included; None when a
    size is unknown (an aggregate by value, a free function's aggregate result)."""
    n = 0
    if f.ret_class is not None:
        if not f.member or f.static:
            return None
        n += 1
    regs = 2 if f.cc == 'fastcall' else 0
    if f.member and not f.static and f.cc != 'thiscall':
        if f.cc == 'fastcall':
            regs -= 1
        else:
            n += 1
    for code, w, kd in f.params:
        if w is None:
            return None
        if regs and w == 1 and kd in ('int', 'ptr'):
            regs -= 1
            continue
        n += w
    return n


if __name__ == '__main__':
    import sys
    for s in sys.argv[1:]:
        f = parse(s)
        if f is None:
            print(s, None)
            continue
        print(s, f.ident or f.special, f.scopes, f.cc, f.member, f.const, f.ret, f.ret_class, f.params, stack_words(f))
