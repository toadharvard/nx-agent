#!/usr/bin/env python3
"""proto - the prototype of the native side of every boundary (MASTER_PLAN4 R5).

    proto.py --n1 n1.json --out proto.tsv

The prototype of a function is read from the MSVC decorated name of the symbol whose bytes equal the exe (N1):
convention, stack words, varargs, struct return, return class and argument classes.  Nothing is inferred from the exe body (no arity arithmetic,
no register-use analysis, no `ret N` guess).  A function that is not N1, or whose declaration this decoder does
not fully decode, has no native prototype: it is LIFTED and reached through its guest entry (R5).

proto.tsv (decomp-derived: container and fnv-nx-build only):  address, kind, cc, stack words, varargs, sret, ret, args
(ret: n v i q f d m?; args: comma list of i q f d, suffixed @ecx / @edx for fastcall register args, '-' when empty)
"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store

REGS = ('eax', 'ecx', 'edx', 'ebx', 'esi', 'edi', 'ebp')
CC = {'A': 'cdecl', 'C': 'pascal', 'E': 'thiscall', 'G': 'stdcall', 'I': 'fastcall'}
# type classes: i int/pointer/ref/bool/enum <=32 bit, q 64-bit int, f float, d double, s? class/struct/union by value
# (size unknown), v void, y array (only behind a pointer).  Stack words of a class; s? has no known size.
WORDS = {'i': 1, 'q': 2, 'f': 1, 'd': 2, 's?': None}
SIMPLE = dict(C='i', D='i', E='i', F='i', G='i', H='i', I='i', J='i', K='i', M='f', N='d', O='d', X='v')
UNDER = dict(D='i', E='i', F='i', G='i', H='i', I='i', J='q', K='q', N='i', S='i', U='i', W='i')


class _Fail(Exception):
    pass


class _Parser:
    """The MSVC decoration grammar, as far as the number of parameter words needs; anything else raises _Fail."""

    def __init__(self, s):
        self.s, self.i, self.names, self.table, self.func_seen = s, 0, [], [], False

    def ch(self, n=0):
        if self.i + n >= len(self.s):
            raise _Fail('syntax')
        return self.s[self.i + n]

    def number(self):
        neg = self.ch() == '?'
        self.i += neg
        c = self.ch()
        self.i += 1
        if c.isdigit():
            return int(c) + 1
        v = 0
        while c != '@':
            if not 'A' <= c <= 'P':
                raise _Fail('syntax')
            v = v * 16 + ord(c) - 65
            c = self.ch()
            self.i += 1
        return -v if neg else v

    def frag(self):
        c = self.ch()
        if c.isdigit():
            self.i += 1
            if int(c) >= len(self.names):
                raise _Fail('syntax')
            return self.names[int(c)]
        if c == '?' and self.ch(1) == '$':
            self.i += 2
            outer = self.names, self.table
            self.names, self.table = [], []
            n = self.plain()
            while self.ch() != '@':
                if self.ch() == '$':
                    if self.ch(1) != '0':
                        raise _Fail('syntax')
                    self.i += 2
                    self.number()
                else:
                    self.type()
            self.i += 1
            self.names, self.table = outer
            self.names.append(n)
            return n
        if c == '?':
            raise _Fail('syntax')
        n = self.plain()
        self.names.append(n)
        return n

    def plain(self):
        j = self.s.find('@', self.i)
        if j < 0:
            raise _Fail('syntax')
        n, self.i = self.s[self.i:j], j + 1
        return n

    def qname(self):
        n = 0
        while self.ch() != '@':
            self.frag()
            n += 1
        self.i += 1
        return n

    def ret(self):
        """Return class: n (no type written: ctor/dtor), v, i, q, f, d, or m? (class by value: hidden pointer)."""
        if self.ch() == '@':
            self.i += 1
            return 'n'
        if self.ch() == '?':                     # cv-qualified return type
            self.i += 1
            if self.ch() not in 'ABCD':
                raise _Fail('return qualifier')
            self.i += 1
        c = self.type()
        if c == 'y':
            raise _Fail('array return')
        return 'm?' if c == 's?' else c

    def params(self):
        if self.ch() == 'X':
            self.i += 1
            return [], False
        args = []
        while True:
            c = self.ch()
            if c in '@Z':
                self.i += 1
                break
            j = self.i
            if c.isdigit():
                # the back-reference table of a nested function type is not proven to be separate from the outer one
                if self.func_seen:
                    raise _Fail('back-reference in a name with a function type')
                self.i += 1
                if int(c) >= len(self.table):
                    raise _Fail('back-reference beyond the table')
                w = self.table[int(c)]
            else:
                w = self.type()
                if w in ('v', 'y'):
                    raise _Fail('void or array argument')
                if self.i - j > 1 and len(self.table) < 10:
                    self.table.append(w)
            args.append(w)
        return args, c == 'Z'

    def func(self):
        self.func_seen = True
        self.i += 1                              # calling convention
        self.ret()
        self.params()
        if self.ch() != 'Z':
            raise _Fail('function type end')
        self.i += 1

    def type(self):
        c = self.ch()
        self.i += 1
        if c in SIMPLE:
            return SIMPLE[c]
        if c in 'PQRSAB':
            if self.ch() == '6':
                self.i += 1
                self.func()
            elif self.ch() in 'ABCD':
                self.i += 1
                self.type()
            else:
                raise _Fail('pointer form')
            return 'i'
        if c in 'UVT':
            self.qname()
            return 's?'
        if c == 'W':
            if self.ch() not in '01234567':
                raise _Fail('enum base')
            self.i += 1
            self.qname()
            return 'i'
        if c == 'Y':
            for _ in range(self.number()):
                self.number()
            self.type()
            return 'y'
        if c == '_' and self.ch() in UNDER:
            self.i += 1
            return UNDER[self.s[self.i - 1]]
        if c == '$' and self.s.startswith('$Q', self.i) or c == '$' and self.s.startswith('$R', self.i):
            self.i += 2
            if self.ch() not in 'ABCD':
                raise _Fail('reference qualifier')
            self.i += 1
            self.type()
            return 'i'
        if c == '$' and self.s.startswith('$T', self.i):
            self.i += 2
            return 'i'
        raise _Fail('type ' + c)


def _decode(msvc):
    """(dict or None, reason): dict(kind, cc, words, varargs, sret, ret, args, regs, why)."""
    if not msvc or not msvc.startswith('?'):
        return None, 'not a mangled name'
    p = _Parser(msvc)
    p.i = 1
    try:
        if msvc.startswith('??') and msvc[2] != '$':   # operator / ctor / dtor code, then the scope
            p.i = 5 if msvc[2:4] == '__' else 4 if msvc[2] == '_' else 3
        p.qname()
        t = p.ch()
        if t == 'Y':
            kind, p.i = 'free', p.i + 1
        elif t in 'CDKLST':
            kind, p.i = 'static', p.i + 1
        elif t in 'ABEFIJMNQRUV':
            kind, p.i = 'member', p.i + 2
        else:
            return None, 'data or special name'
        cc = CC.get(p.ch())
        if cc is None:
            return None, 'calling convention ' + p.ch()
        p.i += 1
    except _Fail:
        return None, 'name or scope'
    d = dict(kind=kind, cc=cc, words=None, varargs=False, sret=False, ret=None, args=[], regs=[], why='')
    try:
        ret = p.ret()
        args, va = p.params()
        regs, n = [], 0
        for a in args:                           # fastcall: first two integer/pointer args <= 4 bytes
            r = ''
            if cc == 'fastcall' and a == 'i' and n < 2:
                r, n = ('ecx', 'edx')[n], n + 1
            regs.append(r)
        if cc == 'fastcall' and ret == 'm?':
            raise _Fail('fastcall with a hidden return pointer')
        d.update(ret=ret, args=args, regs=regs, varargs=va, sret=ret == 'm?')
        d['words'] = None if 's?' in args else sum(WORDS[a] for a, r in zip(args, regs) if not r)
        if d['words'] is None:
            d['why'] = 'argument class by value of unknown size'
    except _Fail as e:
        d['why'] = str(e) or 'declaration'
    return d, d['why']


def declared(msvc):
    """MSVC decorated name -> dict(kind, cc, words, varargs, sret, ret, args, regs, why) or None for data/special names.
    words is the number of 32-bit stack words (register args excluded), None when undecoded or any arg is s?."""
    return _decode(msvc)[0]


def build(n1):
    """{address: declared(symbol)} for every N1 function with a fully decoded declaration."""
    out = {}
    for a, F in n1['fn'].items():
        if F['n1']:
            d = declared(F['sym'])
            if d is not None and d.get('words') is not None:
                out[a] = d
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n1', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    n1 = store.load(a.n1, 'n1')
    P = build(n1)
    with open(a.out, 'w') as fh:
        for addr, d in sorted(P.items()):
            args = ','.join(x + ('@' + r if r else '') for x, r in zip(d['args'], d['regs'])) or '-'
            fh.write('%08x\t%s\t%s\t%d\t%d\t%d\t%s\t%s\n' % (addr, d['kind'], d['cc'], d['words'], bool(d.get('varargs')),
                                                           bool(d.get('sret')), d['ret'], args))
    D = [_decode(F['sym']) for F in n1['fn'].values() if F['n1']]
    print('N1 functions %d; with a decoded prototype %d; without (LIFTED by R5) %d' % (len(D), len(P), len(D) - len(P)))
    print('by convention:', dict(collections.Counter(d['cc'] for d in P.values())))
    print('by ret class:', dict(collections.Counter(d['ret'] for d in P.values())))
    print('with an s? argument: %d; ret m?: %d' % (sum(1 for d, _ in D if d and 's?' in d['args']),
                                                  sum(1 for d, _ in D if d and d['ret'] == 'm?')))
    print('not decoded, by reason:', collections.Counter(w or 'ok' for d, w in D if d is None or d['words'] is None).most_common(8))


if __name__ == '__main__':
    main()
