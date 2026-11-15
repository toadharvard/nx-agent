"""ctypes_compat - C11 6.2.7 compatibility of the declared types of one external symbol across translation units (gate abi, the `c` records).

C records have no linkage ([6.2.2]p6: a tag is never an entity with linkage), so they are never paired by tag name.  C's cross-unit rule is 6.2.7p2: all
declarations of one object or function with external linkage have compatible types.  The record `.fnvnx.decltype` (non-loaded, written by the
layout plugin of a C unit) has one line per external symbol the unit declares or defines:  `symbol<TAB>type`.  Type grammar (prefix, no blanks):
    scalar        a word of [A-Za-z0-9_] (`int`, `unsigned_int`, `float`, `void`, `_Bool`)
    qualified     `const:`T | `volatile:`T | `restrict:`T | `atomic:`T   (any order, each once, canonical order const, volatile, restrict, atomic)
    host          `host:`W   a scalar word carrying the fnvnx_host attribute (the HOST1 rule): a distinct constructor, compatible only with itself
    entry         `entry:`T  T a pointer carrying the fnvnx_entry attribute (ENTRY1): a distinct constructor, compatible only with itself (and a compatible T)
    vector        `vec:`N`:`S`:`A`:`T   N elements (a decimal or `c0+c1x` poly), S bytes, A byte alignment, element T (VEC1)
    reference     `ref:`S`:`A`:`T   a reference type (REF1): S bytes, A byte alignment, pointee T
    complex       `complex:`S`:`A`:`T   `_Complex T`: S bytes, A byte alignment, component T (CPLX1; layout_type case COMPLEX_TYPE)
    pointer       `*`T
    array         `[N]`T (N decimal) | `[]`T (incomplete)
    function      `(`P,P,...`)`T  with P a type, `...` last for varargs; `(?)`T has no prototype (K&R)
    struct/union  `struct:`TAG`{`m:T;m:T:W;...`}`  complete (member name, type, bit-field width W; an unnamed member has name `-`); `struct:`TAG  incomplete;
                  TAG is `-` for an unnamed tag; `union:` likewise
    enum          `enum:`TAG`=`T   (T the compatible integer type of the enumeration)
RULE (6.2.7p1/p2, 6.7.2.1, 6.7.6.2, 6.7.6.3): two types are compatible iff the same constructor with compatible parts: qualifiers equal; pointee compatible; arrays
compatible element types and equal sizes when both are known; functions compatible return type, same parameter count, compatible parameters (qualifiers of
parameters dropped by the front end), same varargs; a function without prototype matches a prototyped one when the return types are compatible and no
parameter of the prototype is changed by the default argument promotions; struct/union: the same kind; if both are complete the same tag when both
are tagged, the same member count, and in order the same names, the same bit-field widths and compatible types; a complete and an incomplete type are
compatible when the tags are equal (6.2.7p1); enum: the same tag (or both unnamed) and compatible integer types.  A recursive type is compared by
coinduction: a pair already under comparison is compatible (the greatest fixed point).  Pure: strings in, verdicts out."""
import os
import re

WORD = re.compile(r'[A-Za-z0-9_]+')
PROMOTED = frozenset(('char', 'signed_char', 'unsigned_char', 'short', 'unsigned_short', 'short_int', 'unsigned_short_int', 'float', '_Bool'))


class Parse:
    def __init__(self, s):
        self.s, self.i = s, 0

    def peek(self):
        return self.s[self.i:self.i + 1]

    def eat(self, t):
        if not self.s.startswith(t, self.i):
            raise ValueError('expected %r at %d of %r' % (t, self.i, self.s))
        self.i += len(t)

    def word(self):
        m = WORD.match(self.s, self.i)
        if not m:
            raise ValueError('expected a word at %d of %r' % (self.i, self.s))
        self.i = m.end()
        return m.group(0)

    def tag(self):
        if self.peek() == '-':
            self.i += 1
            return '-'
        return self.word()

    def num(self):
        w = self.word()
        if not w.isdigit() or (w != '0' and w[0] == '0'):
            raise ValueError('not a decimal size %r' % w)
        return int(w)

    def ty(self):
        c = self.peek()
        if c == '*':
            self.i += 1
            return ('ptr', self.ty())
        if c == '[':
            self.i += 1
            n = None if self.peek() == ']' else self.num()
            self.eat(']')
            return ('arr', n, self.ty())
        if c == '(':
            self.i += 1
            if self.s.startswith('?)', self.i):
                self.i += 2
                return ('fn', None, False, self.ty())
            ps, va = [], False
            if self.peek() != ')':
                while True:
                    if self.s.startswith('...', self.i):
                        self.i += 3
                        va = True
                        break
                    ps.append(self.ty())
                    if self.peek() == ',':
                        self.i += 1
                        continue
                    break
            self.eat(')')
            return ('fn', tuple(ps), va, self.ty())
        w = self.word()
        if w == 'host' and self.peek() == ':':
            self.i += 1
            return ('host', self.word())
        if w == 'entry' and self.peek() == ':':
            self.i += 1
            return ('entry', self.ty())
        if w in ('vec', 'complex', 'ref') and self.peek() == ':':
            self.i += 1
            fields = []
            for _ in range(3 if w == 'vec' else 2):
                m = re.compile(r'[0-9]+(?:\+[0-9]+[xy][0-9]*)?').match(self.s, self.i)
                if not m:
                    raise ValueError('expected a size at %d of %r' % (self.i, self.s))
                self.i = m.end()
                fields.append(m.group(0))
                self.eat(':')
            return (w, tuple(fields), self.ty())
        if w in ('const', 'volatile', 'restrict', 'atomic') and self.peek() == ':':
            self.i += 1
            q = [w]
            t = self.ty()
            if t[0] == 'q':
                q += list(t[1])
                t = t[2]
            return ('q', tuple(sorted(set(q))), t)
        if w in ('struct', 'union') and self.peek() == ':':
            self.i += 1
            tag = self.tag()
            if self.peek() != '{':
                return (w, tag, None)
            self.i += 1
            ms = []
            while self.peek() != '}':
                if ms:
                    self.eat(';')
                n = self.tag()
                self.eat(':')
                t = self.ty()
                bw = None
                if self.peek() == ':':
                    self.i += 1
                    bw = self.num()
                ms.append((n, t, bw))
            self.eat('}')
            return (w, tag, tuple(ms))
        if w == 'enum' and self.peek() == ':':
            self.i += 1
            tag = self.tag()
            self.eat('=')
            return ('enum', tag, self.ty())
        return ('sc', w)


def parse(s):
    p = Parse(s)
    t = p.ty()
    if p.i != len(s):
        raise ValueError('trailing text at %d of %r' % (p.i, s))
    return t


def compatible(a, b, seen=None):
    seen = set() if seen is None else seen
    if a[0] != b[0]:
        return False
    k = a[0]
    if k == 'sc':
        return a[1] == b[1]
    if k == 'host':
        return a[1] == b[1]
    if k in ('vec', 'complex', 'ref'):
        return a[1] == b[1] and compatible(a[2], b[2], seen)
    if k == 'q':
        return a[1] == b[1] and compatible(a[2], b[2], seen)
    if k in ('ptr', 'entry'):
        return compatible(a[1], b[1], seen)
    if k == 'arr':
        return (a[1] is None or b[1] is None or a[1] == b[1]) and compatible(a[2], b[2], seen)
    if k == 'fn':
        if not compatible(a[3], b[3], seen):
            return False
        if a[1] is None or b[1] is None:
            ps = b[1] if a[1] is None else a[1]
            if ps is None:
                return True
            va = b[2] if a[1] is None else a[2]
            return not va and all(not (p[0] == 'sc' and p[1] in PROMOTED) for p in ps)
        return len(a[1]) == len(b[1]) and a[2] == b[2] and all(compatible(x, y, seen) for x, y in zip(a[1], b[1]))
    if k in ('struct', 'union'):
        if a[1] != '-' and b[1] != '-' and a[1] != b[1]:
            return False
        if a[2] is None or b[2] is None:
            return a[1] == b[1] and a[1] != '-'
        key = (id(a), id(b), a[1], b[1])
        if key in seen:
            return True
        seen.add(key)
        return len(a[2]) == len(b[2]) and all(x[0] == y[0] and x[2] == y[2] and compatible(x[1], y[1], seen) for x, y in zip(a[2], b[2]))
    if k == 'enum':
        return a[1] == b[1] and compatible(a[2], b[2], seen)
    raise ValueError('unknown type %r' % (a,))


def _decltype_reasons():
    """the closed set of no-row reasons of `.fnvnx.decltype`, read from where the plugin defines it once (toolchain/layout/decltype_spell.inc, FNVNX_DECLTYPE_REASONS)"""
    inc = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'toolchain', 'layout', 'decltype_spell.inc')
    m = re.search(r'#define FNVNX_DECLTYPE_REASONS\(X\)((?: X\(\w+\))+)', open(inc).read())
    if not m:
        raise ValueError('%s: no FNVNX_DECLTYPE_REASONS' % inc)
    return frozenset(re.findall(r'X\((\w+)\)', m.group(1)))


def parse_decltype_lines(data, where):
    """-> ({symbol: type string}, {symbol: reason}) of the `.fnvnx.decltype` section: LF-terminated lines `symbol<TAB>type` or `symbol<TAB>-reason`, each symbol once
    (RULE DECLTYPE-COMPLETE: every symbol of the object has exactly one of the two); every type must parse and every reason must be in the plugin's closed set"""
    text = data.decode('utf-8')
    if text and not text.endswith('\n'):
        raise ValueError('%s: no final LF' % where)
    reasons = _decltype_reasons()
    out, why = {}, {}
    for n, ln in enumerate(text.split('\n')[:-1], 1):
        f = ln.split('\t')
        if len(f) != 2 or not f[0]:
            raise ValueError('%s: line %d: not symbol<TAB>type' % (where, n))
        if f[0] in out or f[0] in why:
            raise ValueError('%s: line %d: %s twice' % (where, n, f[0]))
        if f[1].startswith('-') and len(f[1]) > 1:
            if f[1][1:] not in reasons:
                raise ValueError('%s: line %d: reason %r is not in the closed set %s' % (where, n, f[1][1:], sorted(reasons)))
            why[f[0]] = f[1][1:]
            continue
        try:
            parse(f[1])
        except ValueError as e:
            raise ValueError('%s: line %d: %s' % (where, n, e))
        out[f[0]] = f[1]
    return out, why


def parse_decltype(data, where):
    """-> {symbol: type string} of the rows of the `.fnvnx.decltype` section (the symbols with a reason instead of a type are not compared: parse_decltype_lines)"""
    return parse_decltype_lines(data, where)[0]


def check(per_object):
    """PER_OBJECT {object id: {symbol: type string}} -> (items [symbol...] seen in two or more objects, {symbol: reason} of incompatible ones).  Each declaration is
    compared with every other one (6.2.7 compatibility with an incomplete type is not transitive, so every PAIR is judged)."""
    bysym = {}
    for oid, d in sorted(per_object.items()):
        for s, t in d.items():
            bysym.setdefault(s, []).append((oid, t))
    items, red = [], {}
    for s, ds in sorted(bysym.items()):
        if len(ds) < 2:
            continue
        items.append(s)
        ts = [(o, t, parse(t)) for o, t in ds]
        for i, (o0, t0, a) in enumerate(ts):
            bad = next(((o, t) for o, t, b in ts[i + 1:] if not compatible(a, b)), None)
            if bad:
                red[s] = 'declared types incompatible (C11 6.2.7p2): %s has %s, %s has %s' % (o0, t0[:120], bad[0], bad[1][:120])
                break
    return items, red
