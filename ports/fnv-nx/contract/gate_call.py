"""gate_call - call targets of the exe function versus each native copy, and the declaration of every edge (L1 CONTRACT).

Two conditions; a function with a row of either is not N2 (bodysel takes LIFTED).

1. Address sets (verdicts none/equal/subset/superset/different; red row only for `different`).  Native targets are `calls`
   plus any 8-hex-digit address inside an `other` symbol name that the exe function also calls (bridges).
2. Declaration equality (verdict `undeclared`).  Rule: for EVERY cross-boundary edge of a native copy - a call/tail target
   (`calls`, bridge addresses) or a data reference (`data`; the /GS cookie is the compiler's own, dropped as in gate_data) to
   an exe address Y - the copy's object has a `.fnvnx.sympair` row of direction `ref` whose MSVC name is byte-equal to the COFF
   symbol name of Y in the pristine MSVC objects (facts['pristine'], built from the N1 certificate by `pristine_names`), and
   that name is decorated (starts with `?`).  Not provable, by rule and with no name table: no sympair section in the object;
   Y has no pristine COFF symbol; the pristine symbol is undecorated (extern "C", every asm/x87/register CRT helper such as
   _ftol2 or _CI*: `_f` carries no types); no `ref` row of a named kind (not `unnamed:*`) has the pristine name; a covering
   row with an undecorated name.  Edge and row are matched by the exe address (objfacts keeps no ELF symbol of an edge): the
   rows that "cover" Y are the ref rows whose name is a pristine name of Y; all of them must be decorated and one must exist.
   Proof argument: proto.py (R5) derives cc, stack words, varargs, sret and the argument classes of a boundary only from the
   decorated name.  The caller's declaration was written by the `names` plugin as `msvc_name`; byte-equality of the full
   decorated names implies equality of every one of these (and of the scope and the data type), so the call convention and the
   by-value/sret shape the caller compiled against are the callee's exe contract.  A name that carries no types gives no such
   equality: the edge is not provable and the caller is LIFTED, never guessed.  Several native copies of one address: each is
   judged, the worst verdict stands.
Assumptions: the `names` plugin ran on every native unit (the recipe's guest-compiler/plugin rule) (ASM-050); the pristine cl symbol
names are the exe's contract (N1: byte-equal, one symbol map) (ASM-053); an external decorated name denotes one entity link-wide (ASM-054).
Zero edges: provable.  An object with no sympair section: every function of it with an edge is not N2.
"""
import collections, re

import store

import store

NAME = 'call'
ORDER = ('none', 'equal', 'subset', 'superset', 'different')   # best .. worst
AN = re.compile(r'([0-9a-f]{8})')


def pristine_names(n1):
    """{exe address: frozenset of pristine COFF symbol names} from the N1 certificate: the symbol of every certified function at
    its address, and every external (unit, name) symbol the certificate pins to an address.  Static symbols (3-key) are excluded."""
    out = collections.defaultdict(set)
    for a, F in n1['fn'].items():
        if F['n1']:
            out[a].add(F['sym'])
    for k, a in n1['sym'].items():
        if len(k) == 2 and not a >> 32:
            out[a].add(k[1])
    return {a: frozenset(v) for a, v in out.items()}


def edges(v, x, cookie):
    """Exe addresses a native copy (obj value v) reaches: call targets and bridge addresses (as in the set verdict), data."""
    e = set(v[2])
    for s in v[3]:
        e |= {int(m, 16) for m in AN.findall(s)} & x
    return e | set(v[1]) - {cookie}


def undeclared(ob, v, pristine, objs, x, cookie, own):
    """None when every edge of the copy is declared as the rule says, else (address, reason) of the lowest failing edge."""
    refs = [(ms, k) for _, ms, k, d in objs.get((ob, 'sympair'), ()) if d == 'ref']
    for y in sorted(e for e in edges(v, x, cookie) if e not in own):
        if (ob, 'sympair') not in objs:
            return y, 'object has no .fnvnx.sympair section'
        names = pristine.get(y)
        if not names:
            return y, 'no pristine COFF symbol at the target'
        if not any(n[:1] == '?' for n in names):
            return y, 'pristine symbol is undecorated (carries no types): %s' % sorted(names)[0]
        cover = [(ms, k) for ms, k in refs if ms in names]
        named = [ms for ms, k in cover if k[:8] != 'unnamed:']
        if not named:
            return y, ('only an unnamed:* ref row has the pristine name' if cover else
                       'no ref row has the pristine name %s' % sorted(names)[0])
        if any(ms[:1] != '?' for ms in named):
            return y, 'a ref row for the target has an undecorated name'
    return None


def run(facts, obj, link):
    copies = collections.defaultdict(list)
    for k, v in obj.items():
        a = k[1]
        if store.is_function_key(k) and a in facts['fn']:
            copies[a].append((k[0], v))
    if copies and 'pristine' not in facts:
        raise ValueError("facts['pristine'] missing: the caller must set it from pristine_names(n1)")
    pristine, cookie = facts.get('pristine', {}), facts.get('cookie')
    counts, rows = collections.Counter(), []
    for a, vs in sorted(copies.items()):
        f = facts['fn'][a]
        end = a + f['size']
        x = {t for _, t in f['calls'] + f['tails'] if not (a <= t < end)}
        worst = (-1, None, None)
        decl = None
        for ob, v in vs:
            if decl is None:
                decl = undeclared(ob, v, pristine, obj, x, cookie, range(a, end))
                if decl:
                    decl = decl + (ob,)
            n = {t for t in v[2] if not (a <= t < end)}
            for s in v[3]:
                n |= {int(m, 16) for m in AN.findall(s)} & x
            if not x and not n and not v[3]:
                k = 0
            elif x == n:
                k = 1
            elif n < x:
                k = 2
            else:
                k = 3 if n > x else 4
            if k > worst[0]:
                worst = (k, x, n)
        k, _, n = worst
        counts[ORDER[k]] += 1
        counts['declared' if decl is None else 'undeclared'] += 1
        if decl:
            rows.append((a, 'undeclared', '%s: edge 0x%08x: %s' % (decl[2], decl[0], decl[1])))
        if ORDER[k] == 'different':
            rows.append((a, 'different', 'exe_only=%d native_only=%d first=0x%08x' % (len(x - n), len(n - x), min(x ^ n))))
    return {'counts': dict(counts), 'rows': rows, 'fail': ['different', 'undeclared'], 'judged': len(copies)}
