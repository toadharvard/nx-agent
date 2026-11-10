#!/usr/bin/env python3
"""proto - an exe-derived prototype for every function (L1 CONTRACT), and the arity arithmetic against the declaration.

    proto.py --facts facts.pkl --out proto.pkl [--addrmap addrmap.tsv --abi abi.tsv]    (prints counts only)

build(facts) -> {addr: P}, P = dict(stack_words, stack_src, regs, conv, confidence)
  stack_src  'ret'     the single `ret N>0`: N/4 words, the callee pops
             'caller'  `ret 0`; the most common nonzero clean-up of the direct call sites (>= 2 agree, none differ)
             'caller0' `ret 0` and every direct call site cleans 0 (at least one site)
             'tail'    no `ret`, the only exits are tail jumps: inherited from the tail target(s)
             'unknown' stack_words None
  regs       registers live on entry (ecx, edx ...; empty for context-dependent entries)
  conv       thiscall (ecx; callee-pop or 0 words) | fastcall (ecx+edx) | stdcall (no regs, ret N) |
             cdecl-or-noargs (no regs, ret 0) | custom (anything else)
declared(msvc) -> dict(kind free|member|static, cc, words, varargs, sret, args) or None.  words = stack words of the
  declared parameters (this and the hidden sret pointer not counted); None when a type is not trivially known.
check(facts, protos) -> counts, rows[(addr, verdict, declared, proto)]; the arity arithmetic is: declared words
  (+1 this unless thiscall, +1 sret) == exe stack words + exe input registers.
"""
import argparse
import collections
import pickle
import sys

REGS = ('eax', 'ecx', 'edx', 'ebx', 'esi', 'edi', 'ebp')
CC = {'A': 'cdecl', 'C': 'pascal', 'E': 'thiscall', 'G': 'stdcall', 'I': 'fastcall'}
SIMPLE = dict(C=1, D=1, E=1, F=1, G=1, H=1, I=1, J=1, K=1, M=1, N=2, O=2, X=0)
UNDER = dict(D=1, E=1, F=1, G=1, H=1, I=1, J=2, K=2, L=1, M=1, N=1, O=1, S=1, T=1, U=1, W=1)


def build(facts):
    fn = facts['fn']
    sites = collections.defaultdict(list)      # target -> clean-up bytes of each direct call site
    for f in fn.values():
        cl = dict(f['cleanup'])
        for site, tgt in f['calls']:
            if site in cl:
                sites[tgt].append(cl[site])
    out = {}

    def one(a, seen=()):
        if a in out:
            return out[a]
        f = fn.get(a)
        if f is None or a in seen:
            return dict(stack_words=None, stack_src='unknown')
        rets = set(f['ret'])
        s = sites.get(a, [])
        nz = collections.Counter(x for x in s if x)
        if len(rets) == 1 and next(iter(rets)) > 0:
            r = dict(stack_words=next(iter(rets)) // 4, stack_src='ret', confidence='high')
        elif len(rets) > 1:
            r = dict(stack_words=None, stack_src='unknown', confidence='none')
        elif rets:
            if len(nz) == 1 and sum(nz.values()) >= 2:
                n = next(iter(nz))
                r = dict(stack_words=n // 4, stack_src='caller', confidence='high' if nz[n] >= 3 else 'medium')
            elif s and not nz:
                r = dict(stack_words=0, stack_src='caller0', confidence='medium' if len(s) >= 3 else 'low')
            else:
                r = dict(stack_words=None, stack_src='unknown', confidence='none')
        else:
            ts = [one(t, seen + (a,)) for _, t in f['tails']]
            w = {t['stack_words'] for t in ts}
            if ts and len(w) == 1 and None not in w:
                r = dict(stack_words=w.pop(), stack_src='tail', confidence='low')
            else:
                r = dict(stack_words=None, stack_src='unknown', confidence='none')
        return r

    for a, f in fn.items():
        r = one(a)
        regs = () if f['ctx'] else tuple(n for i, n in enumerate(REGS) if f['live'] >> i & 1)
        r['regs'] = regs
        w = r['stack_words']
        if f['ctx']:
            r['conv'] = 'custom'
        elif regs == ('ecx',):
            r['conv'] = 'thiscall' if r['stack_src'] == 'ret' or w == 0 else 'custom'
        elif regs == ('ecx', 'edx'):
            r['conv'] = 'fastcall'
        elif not regs:
            r['conv'] = 'stdcall' if r['stack_src'] == 'ret' else 'cdecl-or-noargs'
        else:
            r['conv'] = 'custom'
        r.setdefault('confidence', 'none')
        out[a] = r
    return out


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


def _expect(d):
    """(words that must be popped or read, registers the native passes) -> (stack, regs) or None if unknown."""
    if d['words'] is None:
        return None
    n = d['words'] + (d['kind'] == 'member' and d['cc'] != 'thiscall') + d['sret']
    if d['cc'] == 'thiscall':
        return n, ('ecx',)
    if d['cc'] == 'fastcall':
        r = []
        for w in d['args']:
            if w == 1 and len(r) < 2:
                r.append(('ecx', 'edx')[len(r)])
        return n - len(r), tuple(r)
    return n, ()


def check(facts, protos):
    counts, rows = collections.Counter(), []
    for a, f in facts['fn'].items():
        if not f['decomp']:
            continue
        d, p = declared(f['sym']), protos[a]
        if d is None:
            continue
        e = _expect(d)
        if p['conv'] == 'custom':
            v = 'custom_regs'
        elif e is None or p['stack_words'] is None:
            v = 'unknown'
        else:
            stack, regs = e
            same = stack == p['stack_words']
            if d['sret'] and not same:
                v = 'unknown'                    # small classes may come back in registers
            elif d['cc'] == 'thiscall':
                v = ('agree' if p['regs'] else 'thiscall_ecx_unused') if same else 'words_differ'
            elif d['cc'] == 'fastcall':
                v = 'agree' if same and set(p['regs']) <= set(regs) else 'words_differ'
            elif p['regs']:
                v = 'ecx_read_but_declared_free' if p['regs'] == ('ecx',) else 'custom_regs'
            else:
                v = 'agree' if same else 'words_differ'
        counts[v] += 1
        rows.append((a, v, d, p))
    return counts, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--facts', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--addrmap')
    ap.add_argument('--abi')
    a = ap.parse_args()
    facts = pickle.load(open(a.facts, 'rb'))
    protos = build(facts)
    pickle.dump(protos, open(a.out, 'wb'))
    print('functions', len(protos))
    for k, v in sorted(collections.Counter(p['stack_src'] for p in protos.values()).items()):
        print('stack_src', k, v)
    for k, v in sorted(collections.Counter(p['conv'] for p in protos.values()).items()):
        print('conv', k, v)
    for k, v in sorted(check(facts, protos)[0].items()):
        print('check', k, v)
    return 0


if __name__ == '__main__':
    sys.exit(main())
