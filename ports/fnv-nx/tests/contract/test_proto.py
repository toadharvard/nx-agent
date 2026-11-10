#!/usr/bin/env python3
"""test_proto.py - synthetic facts (invented addresses and symbols) for contract/proto.py.  Exit 0 = pass."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'contract'))
import proto  # noqa: E402

ECX, EDX, EBX = 2, 4, 8
FAILS = []


def eq(what, got, want):
    if got != want:
        FAILS.append('%s: got %r want %r' % (what, got, want))


def fn(ret, live=0, sym='', calls=(), cleanup=(), tails=(), ctx=False):
    return dict(ret=tuple(ret), live=live, pushin=0, ctx=ctx, calls=list(calls), tails=list(tails),
                cleanup=list(cleanup), sym=sym, decomp=bool(sym), x87=0)


A, B, C, F, G, H, I, J, K, L, M, N, Q, R, T = (0x1000 + 0x10 * i for i in range(15))
facts = {'fn': {
    A: fn([4], ECX, '?a@C@@QAEHH@Z'),                    # thiscall, 1 word, reads ecx
    B: fn([4], 0, '?b@C@@QAEXH@Z'),                      # thiscall that never reads ecx
    C: fn([4], ECX, '?c@@YGXH@Z'),                       # free stdcall that reads ecx
    F: fn([0], ECX | EBX, '?f@@YAXXZ'),                  # custom registers
    G: fn([0], 0, '?g@@YAXHH@Z'),                        # cdecl, callers pop 8
    H: fn([0], 0, '?h@@YAXXZ'),                          # callers pop 0
    I: fn([], 0, '', tails=[(0x1100, G)]),               # only exit is a tail jump to G
    J: fn([0], 0, '?j@@YAXH@Z'),                         # no callers: unknown
    K: fn([8], 0, '?k@@YGXH@Z'),                         # ret 8 but declared 1 word
    L: fn([0], ECX | EDX, '?l@@YIXHH@Z'),                # fastcall
    M: fn([0], 0, '', calls=[(0x2001, G), (0x2002, G), (0x2003, H), (0x2004, H), (0x2005, L), (0x2006, L),
                             (0x2007, N), (0x2008, N), (0x2009, Q)],
          cleanup=[(0x2001, 8), (0x2002, 8), (0x2003, 0), (0x2004, 0), (0x2005, 0), (0x2006, 0),
                   (0x2007, 4), (0x2008, 8), (0x2009, 4)]),
    N: fn([0], 0, ''),                                   # callers disagree: unknown
    Q: fn([0], 0, ''),                                   # one site only: unknown
    R: fn([], 0, '', tails=[(0x1200, J)]),               # tail to an unknown target: unknown
    T: fn([0], 0, '?t@@YAXVFoo@@@Z'),                    # by-value class: words unknown
}, 'vft': {}, 'ptrs': [], 'xc': []}

p = proto.build(facts)
for a, src, words, conv in [(A, 'ret', 1, 'thiscall'), (B, 'ret', 1, 'stdcall'), (C, 'ret', 1, 'thiscall'),
                            (F, 'unknown', None, 'custom'), (G, 'caller', 2, 'cdecl-or-noargs'),
                            (H, 'caller0', 0, 'cdecl-or-noargs'), (I, 'tail', 2, 'cdecl-or-noargs'),
                            (J, 'unknown', None, 'cdecl-or-noargs'), (L, 'caller0', 0, 'fastcall'),
                            (N, 'unknown', None, 'cdecl-or-noargs'), (Q, 'unknown', None, 'cdecl-or-noargs'),
                            (R, 'unknown', None, 'cdecl-or-noargs')]:
    eq('stack_src %x' % a, p[a]['stack_src'], src)
    eq('stack_words %x' % a, p[a]['stack_words'], words)
    eq('conv %x' % a, p[a]['conv'], conv)
eq('regs A', p[A]['regs'], ('ecx',))
eq('regs L', p[L]['regs'], ('ecx', 'edx'))
eq('every function', set(p), set(facts['fn']))
eq('ctx ignores regs', proto.build({'fn': {A: fn([4], ECX, ctx=True)}})[A]['regs'], ())

d = proto.declared
eq('member', d('?f@C@@QAEHH@Z'), dict(kind='member', cc='thiscall', words=1, varargs=False, sret=False, args=[1]))
eq('void', d('?g@@YAXXZ')['words'], 0)
eq('wide', d('?h@@YAXPAXN_J@Z')['words'], 5)
eq('varargs', (d('?v@@YAXPBDZZ')['words'], d('?v@@YAXPBDZZ')['varargs']), (1, True))
eq('byvalue', d('?t@@YAXVFoo@@@Z')['words'], None)
eq('backref', d('?r@@YAXPAVFoo@@0H@Z')['words'], 3)
eq('funcptr', d('?p@@YAXP6AXH@ZH@Z')['words'], 2)
eq('enum', d('?e@@YAXW4Col@@@Z')['words'], 1)
eq('template', d('?u@@YAXAAV?$vec@H@@H@Z')['words'], 2)
eq('static', d('?s@C@@SAXH@Z')['kind'], 'static')
eq('ctor', d('??0C@@QAE@H@Z')['words'], 1)
eq('special', d('_notmsvc'), None)

counts, rows = proto.check(facts, p)
got = {a: v for a, v in ((r[0], r[1]) for r in rows)}
for a, v in [(A, 'agree'), (B, 'thiscall_ecx_unused'), (C, 'ecx_read_but_declared_free'), (F, 'custom_regs'),
             (G, 'agree'), (H, 'agree'), (J, 'unknown'), (K, 'words_differ'), (L, 'agree'), (T, 'unknown')]:
    eq('verdict %x' % a, got.get(a), v)
eq('counts', counts['agree'], 4)

if FAILS:
    print('\n'.join(FAILS))
    sys.exit(1)
print('ok')
