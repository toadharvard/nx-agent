#!/usr/bin/env python3
"""proto - the prototype of the native side of every boundary (MASTER_PLAN4 R5).

    proto.py --n1 n1.pkl --out proto.tsv

The prototype of a function is read from the MSVC decorated name of the symbol whose bytes equal the exe (N1):
convention, stack words, varargs, struct return.  Nothing is inferred from the exe body (no arity arithmetic,
no register-use analysis, no `ret N` guess).  A function that is not N1, or whose declaration this decoder does
not fully decode, has no native prototype: it is LIFTED and reached through its guest entry (R5).

proto.tsv (decomp-derived: container and fnv-nx-build only):  address, kind, cc, stack words, varargs, sret
"""
import argparse
import collections
import pickle
import sys

REGS = ('eax', 'ecx', 'edx', 'ebx', 'esi', 'edi', 'ebp')
CC = {'A': 'cdecl', 'C': 'pascal', 'E': 'thiscall', 'G': 'stdcall', 'I': 'fastcall'}
SIMPLE = dict(C=1, D=1, E=1, F=1, G=1, H=1, I=1, J=1, K=1, M=1, N=2, O=2, X=0)
UNDER = dict(D=1, E=1, F=1, G=1, H=1, I=1, J=2, K=2, L=1, M=1, N=1, O=1, S=1, T=1, U=1, W=1)


class _Fail(Exception):
    pass


class _Parser:
    """The MSVC decoration grammar, as far as the number of parameter words needs; anything else raises _Fail."""

    def __init__(self, s):
        self.s, self.i, self.names, self.table = s, 0, [], []

    def ch(self, n=0):
        if self.i + n >= len(self.s):
            raise _Fail()
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
                raise _Fail()
            v = v * 16 + ord(c) - 65
            c = self.ch()
            self.i += 1
        return -v if neg else v

    def frag(self):
        c = self.ch()
        if c.isdigit():
            self.i += 1
            if int(c) >= len(self.names):
                raise _Fail()
            return self.names[int(c)]
        if c == '?' and self.ch(1) == '$':
            self.i += 2
            outer = self.names, self.table
            self.names, self.table = [], []
            n = self.plain()
            while self.ch() != '@':
                if self.ch() == '$':
                    if self.ch(1) != '0':
                        raise _Fail()
                    self.i += 2
                    self.number()
                else:
                    self.type()
            self.i += 1
            self.names, self.table = outer
            self.names.append(n)
            return n
        if c == '?':
            raise _Fail()
        n = self.plain()
        self.names.append(n)
        return n

    def plain(self):
        j = self.s.find('@', self.i)
        if j < 0:
            raise _Fail()
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
        """Return type; True when it is a class returned by value (hidden pointer)."""
        if self.ch() == '@':
            self.i += 1
            return False
        if self.ch() == '?':
            self.i += 2
            sret = self.ch() in 'UVT'
            self.type()
            return sret
        self.type()
        return False

    def params(self):
        if self.ch() == 'X':
            self.i += 1
            return [], False
        args, outer, self.table = [], self.table, []
        while True:
            c = self.ch()
            if c in '@Z':
                self.i += 1
                break
            j = self.i
            if c.isdigit():
                self.i += 1
                if int(c) >= len(self.table):
                    raise _Fail()
                w = self.table[int(c)]
            else:
                w = self.type()
                if self.i - j > 1:
                    self.table.append(w)
            args.append(w)
        self.table = outer
        return args, c == 'Z'

    def func(self):
        self.i += 1                              # calling convention
        self.ret()
        _, va = self.params()
        if va:
            self.i += 1                          # the second Z of 'ZZ'
        if self.ch() != 'Z':
            raise _Fail()
        self.i += 1

    def type(self):
        c = self.ch()
        self.i += 1
        if c in SIMPLE:
            return SIMPLE[c]
        if c in 'PQRS':
            if self.ch() == '6':
                self.i += 1
                self.func()
            elif self.ch() in 'ABCD':
                self.i += 1
                self.type()
            else:
                raise _Fail()
            return 1
        if c in 'AB':
            self.i += 1
            if self.ch() == '6':
                self.i += 1
                self.func()
            else:
                self.type()
            return 1
        if c in 'UVT':
            self.qname()
            return None
        if c == 'W':
            self.i += 1
            self.qname()
            return 1
        if c == 'Y':
            for _ in range(self.number() + 1):
                self.number()
            self.type()
            return None
        if c == '_' and self.ch() in UNDER:
            self.i += 1
            return UNDER[self.s[self.i - 1]]
        if c == '$' and self.s.startswith('$Q', self.i):
            self.i += 2
            self.type()
            return 1
        if c == '$' and self.s.startswith('$T', self.i):
            self.i += 2
            return 1
        raise _Fail()


def declared(msvc):
    """MSVC decorated name -> dict(kind, cc, words, varargs, sret, args) or None for data and special names."""
    if not msvc or not msvc.startswith('?'):
        return None
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
            return None
        cc = CC.get(p.ch())
        if cc is None:
            return None
        p.i += 1
    except _Fail:
        return None
    d = dict(kind=kind, cc=cc, words=None, varargs=False, sret=False, args=[])
    try:
        d['sret'] = p.ret()
        args, d['varargs'] = p.params()
        d['args'] = args
        d['words'] = None if None in args else sum(args)
    except _Fail:
        pass
    return d


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
    import argparse, pickle, collections
    ap = argparse.ArgumentParser()
    ap.add_argument('--n1', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    n1 = pickle.load(open(a.n1, 'rb'))
    P = build(n1)
    with open(a.out, 'w') as fh:
        for addr, d in sorted(P.items()):
            fh.write('%08x\t%s\t%s\t%d\t%d\t%d\n' % (addr, d['kind'], d['cc'], d['words'], bool(d.get('varargs')), bool(d.get('sret'))))
    n = sum(1 for F in n1['fn'].values() if F['n1'])
    print('N1 functions %d; with a decoded prototype %d; without (LIFTED by R5) %d' % (n, len(P), n - len(P)))
    print('by convention:', dict(collections.Counter(d['cc'] for d in P.values())))


if __name__ == '__main__':
    main()
