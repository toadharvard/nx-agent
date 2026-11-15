#!/usr/bin/env python3
"""abi_msvcname - the Microsoft Visual C++ decorated-name grammar, parsed into the declaration it encodes (L2 SOURCE, ABI1).

    parse(name) -> Decl                 ValueError('<name>: <what>') when the name is not in the grammar subset below
    parse_qualified_type_name(text)     '.?AVFoo@Bar@@' (a TypeDescriptor name, as RTTI stores it) -> ('class'|'struct'|'union', qualified)

This is a PARSE of a defined format (MSVC's decoration, VC 2008 era with the later `$$Q` and `_N`/`_W` letters), not a
guess from the spelling: every character is consumed by a production or the name is rejected.  Nothing in it reads a
word of the name as a hint; the identifiers are opaque fragments that are compared whole.

What a name encodes (the facts the ABI rule needs):
  * the scope, as a tuple of fragments, innermost LAST (`?f@Bar@Foo@@` -> ('Foo', 'Bar', 'f')); a fragment is a string or
    ('tpl', name, args) for a template instance, ('op', code) for an operator / ctor / dtor name, ('anon',) for an
    anonymous namespace;
  * for a function: the access/kind letter (`member`, `static`, `virtual`, `thunk`), the `this` qualifiers, the calling
    convention, the return type (None for a constructor, destructor), the parameter types, `varargs`;
  * types as nested tuples: ('b', 'int') builtin; ('ptr', '*'|'&'|'&&', cv, pointee) pointer or reference (cv of the pointer
    itself; the pointee carries its own); ('tag', 'class'|'struct'|'union'|'enum', qualified); ('arr', dims, element);
    ('fn', cc, ret, params, varargs) a function type (only behind a pointer); ('memfn', qualified-class, cv, fn) a pointer
    to member function.
Back-references follow the grammar: a digit in a name position is the n-th simple name memorised so far (at most 10), a
digit in a parameter position is the n-th parameter type memorised (at most 10; a type is memorised when its decoration is
longer than one character).  A template argument list has its own name table, seeded with the template's own name.

Supported: free and member functions (all access letters, static, virtual, `W`/`G`/`O` this-adjustor thunks), constructors,
destructors, operators (kept as ('op', code)), data symbols (kind 'data', no further parse).  Template arguments: types,
`$0` integer constants and `$D` template-parameter indexes.  Everything else (`$1` pointer constants, `$$V`, local scope
`?1?`, `__ptr64` markers, ...) is a ValueError: the caller treats such a name as undecided, never as passed.
Pure: str in, tuple out.
"""
import collections

Decl = collections.namedtuple('Decl', 'kind scope access member static virtual thunk this_cv cc ret params varargs structor')

CALLCONV = {'A': 'cdecl', 'B': 'cdecl', 'C': 'pascal', 'D': 'pascal', 'E': 'thiscall', 'F': 'thiscall', 'G': 'stdcall',
            'H': 'stdcall', 'I': 'fastcall', 'J': 'fastcall', 'K': 'none', 'L': 'none', 'M': 'clrcall', 'N': 'clrcall',
            'O': 'eabi', 'P': 'eabi', 'Q': 'vectorcall'}
# function-class letter -> (access, static, virtual, thunk, member); Y / Z are free functions
FUNCLASS = {}
for _i, _c in enumerate('ABCDEFGHIJKLMNOPQRSTUVWX'):
    _acc = ('private', 'protected', 'public')[_i // 8]
    _k = _i % 8
    FUNCLASS[_c] = (_acc, _k in (2, 3), _k in (4, 5, 6, 7), _k in (6, 7), True)
FUNCLASS['Y'] = FUNCLASS['Z'] = ('global', False, False, False, False)
BUILTIN = {'X': 'void', 'D': 'char', 'C': 'signed char', 'E': 'unsigned char', 'F': 'short', 'G': 'unsigned short',
           'H': 'int', 'I': 'unsigned int', 'J': 'long', 'K': 'unsigned long', 'M': 'float', 'N': 'double',
           'O': 'long double'}
BUILTIN_US = {'N': 'bool', 'J': '__int64', 'K': 'unsigned __int64', 'W': 'wchar_t', 'D': '__int8', 'E': 'unsigned __int8',
              'F': '__int16', 'G': 'unsigned __int16', 'H': '__int32', 'I': 'unsigned __int32', 'L': '__int128',
              'M': 'unsigned __int128', 'S': 'char16_t', 'U': 'char32_t', 'Q': 'char8_t'}
CV = {'A': '', 'B': 'const', 'C': 'volatile', 'D': 'const volatile'}
PTRCV = {'P': '', 'Q': 'const', 'R': 'volatile', 'S': 'const volatile'}
TAGS = {'T': 'union', 'U': 'struct', 'V': 'class'}
MAXREF = 10


class _P:
    def __init__(self, s):
        self.s, self.i, self.names, self.args = s, 0, [], []

    def err(self, what):
        raise ValueError('%s: %s at %d' % (self.s, what, self.i))

    def peek(self, n=1):
        return self.s[self.i:self.i + n]

    def take(self, lit=None):
        if lit is None:
            if self.i >= len(self.s):
                self.err('unexpected end')
            c = self.s[self.i]
            self.i += 1
            return c
        if self.s.startswith(lit, self.i):
            self.i += len(lit)
            return True
        return False

    def need(self, lit):
        if not self.take(lit):
            self.err('expected %r' % lit)

    # ---- numbers and names
    def number(self):
        neg = self.take('?')
        c = self.peek()
        if c.isdigit():
            self.i += 1
            v = int(c) + 1
        else:
            v = 0
            while True:
                c = self.take()
                if c == '@':
                    break
                if not 'A' <= c <= 'P':
                    self.err('bad number digit %r' % c)
                v = v * 16 + ord(c) - 65
        return -v if neg else v

    def fragment(self, first):
        """One scope component; `first`: the innermost one (operator names and ctor/dtor live there only)."""
        c = self.peek()
        if c.isdigit():
            self.i += 1
            k = int(c)
            if k >= len(self.names):
                self.err('name back-reference %d beyond %d names' % (k, len(self.names)))
            return self.names[k]
        if self.take('?$'):
            return self.template()
        if self.peek() == '?':
            if self.take('?A'):                       # anonymous namespace: ?A0x<hex>@
                while self.take() != '@':
                    pass
                return ('anon',)
            if not first:
                self.err('operator or local-scope name outside the innermost component')
            self.i += 1
            code = self.take()
            if code == '_':
                code += self.take()
                if code == '__':
                    code += self.take()
            elif code == '?':
                self.err('local scope name')
            return ('op', code)
        j = self.s.find('@', self.i)
        if j <= self.i:
            self.err('empty or unterminated name')
        n = self.s[self.i:j]
        self.i = j + 1
        self.memo(n)
        return n

    def memo(self, n):
        if len(self.names) < MAXREF:
            self.names.append(n)

    def template(self):
        j = self.s.find('@', self.i)
        if j <= self.i:
            self.err('unterminated template name')
        name = self.s[self.i:j]
        self.i = j + 1
        outer_names, outer_args = self.names, self.args
        self.names, self.args = [name], []
        args = []
        while not self.take('@'):
            args.append(self.targ())
        self.names, self.args = outer_names, outer_args
        t = ('tpl', name, tuple(args))
        self.memo(t)
        return t

    def targ(self):
        if self.take('$0'):
            return ('int', self.number())
        if self.take('$D'):
            return ('tparam', self.number())
        if self.peek() == '$':
            self.err('template argument form %r' % self.peek(3))
        return self.type(False)

    def qualified(self):
        out = [self.fragment(True)]
        while not self.take('@'):
            out.append(self.fragment(False))
        return tuple(reversed(out))

    # ---- types
    def quals(self):
        c = self.take()
        if c not in CV:
            self.err('bad storage class %r' % c)
        return CV[c]

    def type(self, mangle_quals):
        if mangle_quals:
            self.quals()          # the cv of a pointee or returned value is consumed; it is not an ABI fact
        return self.type1()

    def type1(self):
        c = self.take()
        if c in TAGS:
            return ('tag', TAGS[c], self.qualified())
        if c == 'W':
            d = self.take()
            if d != '4':
                self.err('enum underlying code %r' % d)
            return ('tag', 'enum', self.qualified())
        if c in 'AB':
            return ('ptr', '&', '' if c == 'A' else 'volatile', self.type(True))
        if c in PTRCV:
            cv = PTRCV[c]
            if self.take('6'):
                return ('ptr', '*', cv, self.fntype())
            if self.take('8'):
                cls = self.qualified()
                fcv = self.quals()
                return ('ptr', '*', cv, ('memfn', cls, fcv, self.fntype()))
            return ('ptr', '*', cv, self.type(True))
        if c == '$':
            if self.take('$Q'):
                return ('ptr', '&&', '', self.type(True))
            self.err('type form $%s' % self.peek())
        if c == 'Y':
            n = self.number()
            if n < 1:
                self.err('array rank')
            dims = tuple(self.number() for _ in range(n))
            return ('arr', dims, self.type(False))
        if c == '_':
            d = self.take()
            if d not in BUILTIN_US:
                self.err('builtin _%s' % d)
            return ('b', BUILTIN_US[d])
        if c in BUILTIN:
            return ('b', BUILTIN[c])
        self.i -= 1
        self.err('type letter %r' % c)

    def fntype(self):
        cc = self.callconv()
        ret = self.rettype()
        params, var = self.paramlist()
        self.need('Z')
        return ('fn', cc, ret, params, var)

    def callconv(self):
        c = self.take()
        if c not in CALLCONV:
            self.err('calling convention %r' % c)
        return CALLCONV[c]

    def rettype(self):
        if self.take('?'):
            return self.type(True)
        return self.type(False)

    def paramlist(self):
        if self.take('X'):
            return (), False
        out = []
        while self.peek() not in ('@', 'Z', ''):
            c = self.peek()
            if c.isdigit():
                self.i += 1
                k = int(c)
                if k >= len(self.args):
                    self.err('parameter back-reference %d beyond %d' % (k, len(self.args)))
                out.append(self.args[k])
                continue
            start = self.i
            t = self.type(False)
            if self.i - start > 1 and len(self.args) < MAXREF:
                self.args.append(t)
            out.append(t)
        if self.take('@'):
            return tuple(out), False
        if self.take('Z'):
            return tuple(out), True
        self.err('unterminated parameter list')


def parse(name):
    p = _P(name)
    p.need('?')
    scope = p.qualified()
    c = p.peek()
    inner = scope[-1]
    structor = inner[1] if isinstance(inner, tuple) and inner[0] == 'op' and inner[1] in ('0', '1') else ''
    if c.isdigit():     # a data symbol: storage letter and type follow; no ABI of its own
        return Decl('data', scope, '', False, False, False, False, '', '', None, (), False, '')
    if c not in FUNCLASS:
        p.err('function class %r' % c)
    p.i += 1
    access, static, virtual, thunk, member = FUNCLASS[c]
    if thunk:
        p.number()
    this_cv = ''
    if member and not static:
        this_cv = p.quals()
    cc = p.callconv()
    if structor:
        p.need('@')
        ret = None
    else:
        ret = p.rettype()
    params, var = p.paramlist()
    if not p.take('Z') and not p.take('_E'):
        p.err('exception specification')
    if p.i != len(name):
        p.err('trailing characters')
    return Decl('function', scope, access, member and not static, static, virtual, thunk, this_cv, cc, ret, params, var, structor)


def parse_qualified_type_name(text):
    """A TypeDescriptor name `.?AV<qualified>@@` / `.?AU` / `.?AT` (how RTTI stores a class name) -> (tag, qualified)."""
    if text[:3] != '.?A' or len(text) < 5 or text[3] not in TAGS:
        raise ValueError('%s: not a TypeDescriptor name' % text)
    p = _P(text)
    p.i = 4
    q = p.qualified()
    if p.i != len(text):
        p.err('trailing characters')
    return TAGS[text[3]], q


if __name__ == '__main__':
    import sys
    for a in sys.argv[1:]:
        print(parse(a))
