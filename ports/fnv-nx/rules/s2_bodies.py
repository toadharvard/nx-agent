"""Prepare rules of stream S2-bodies (tools/rules/README.md).

HARN1 - the decomp's harness-only code gets internal linkage.

The decomp keeps byte-matching harnesses next to the matched code: convention callers that exist only so MSVC emits
a call with the right registers (`int stub_caller() { return FUN_x(stub_a(), stub_b()); }`, `void wblk_caller()`,
`extern "C" void Ent(void)`), whose stubs are declared and never defined. No exe function is theirs and
nothing calls them, but as external definitions GCC emits them, and their stub references become trap stubs at
link time (bodycause: `unresolved`, `c function harness-only`; the variables they read: `c variable harness-only`).

HARN1 makes such a definition `static`. GCC then drops it when nothing in the unit uses it, together with its
references. Gates: a free function definition at namespace scope (only namespaces and extern "C" blocks around it),
with an unqualified name (no member, no out-of-line `C::f`); not virtual, inline, static, a template, a friend or an
operator; untagged (no `// @fnv` above it, no FNVNX_KEEP); and its name is referenced by no unit: no MSVC name in
any unit's units.json `symbols` or `functions` has it as its identifier, and no other unit declares it without
defining it (a variant copy that calls the harness or takes its address through its own prototype). A prototype of the name in the file before
the definition, at namespace scope, becomes `static` too; any other mention before it (a block-scope declaration, a
friend, a use in a member body) leaves the definition alone. `extern "C"` on the definition or its prototypes goes:
GCC gives an extern "C" function external linkage even in an anonymous namespace, and an internal function's
language linkage changes nothing (no other unit names it). Line numbers stay.
"""
import collections
import os
import re

import core                        # noqa: E402
import s1_compile                  # noqa: E402  (the units.json readers)

CLASS_HEAD = re.compile(r'^(?:template\s*<(?:[^<>]|<(?:[^<>]|<[^<>]*>)*>)*>\s*)?(?:typedef\s+)?(?:class|struct|union)\s+'
                        r'(?:__declspec\s*\([^()]*\)\s*)?([A-Za-z_]\w*)')


def _scope_name(kind, head):
    """The name a scope_chain entry introduces: a namespace's last word, a class's name (past a base clause)."""
    if kind == 'class':
        m = CLASS_HEAD.match(head.strip())
        return m[1] if m else None
    return head.split()[-1] if head.split() else None


# ---------------------------------------------------------------------------------------------------------- HARN1

_HEAD = (r'((?:extern[ \t]+"C"[ \t]+)?)((?:__declspec\s*\(\s*\w+\s*\)[ \t]+|[\w:<>,\*&]+[ \t\*&]+)+?)'
         r'([A-Za-z_]\w*)\s*\(([^;{}()]*(?:\([^;{}()]*\)[^;{}()]*)*)\)\s*')
FREE_DEF = re.compile(r'^([ \t]*)' + _HEAD + r'(?:throw\s*\(\s*\)\s*)?\{', re.M)
FREE_PROTO = re.compile(r'^([ \t]*)' + _HEAD + r'(?:throw\s*\(\s*\)\s*)?;', re.M)
HARN_SKIP_HEAD = re.compile(r'\b(?:virtual|static|inline|__inline|__forceinline|constexpr|template|friend|typedef|'
                            r'operator|return|else|case|goto|new|delete|throw|using|extern|FNVNX_KEEP|dllexport|dllimport)\b|::\s*$')
HARN_KEEP_NAMES = {'main', 'wmain', 'WinMain', 'wWinMain', 'DllMain'}
_REF_IDENTS = None


def _msvc_ident(name):
    """The identifier an MSVC / C symbol names: ?f@C@@... -> f, ??$f@... -> f, _f@8 / @f@8 / _f -> f (and _f)."""
    if name.startswith('__imp_'):
        name = name[6:]
    if name.startswith('??$'):
        return {name[3:].split('@', 1)[0]}
    if name.startswith('??'):
        return set()
    if name.startswith('?'):
        return {name[1:].split('@', 1)[0]}
    base = re.sub(r'@\d+$', '', name)
    return {base, base.lstrip('_@')}


def _referenced_identifiers(path):
    """Every identifier some unit's units.json `symbols` or `functions` (with fold aliases) names."""
    global _REF_IDENTS
    if _REF_IDENTS is None:
        import json
        _REF_IDENTS = set()
        root, uj = core._units_json(path)
        if uj:
            for u in json.load(open(uj))['units']:
                for n in u.get('symbols') or {}:
                    _REF_IDENTS |= _msvc_ident(n)
                for f in u.get('functions') or ():
                    for n in [f.get('symbol', '')] + list(f.get('fold_aliases') or ()):
                        _REF_IDENTS |= _msvc_ident(n)
    return _REF_IDENTS


_DECL_NAME = re.compile(r'^[ \t]*(?:extern[ \t]+"C"[ \t]+)?(?:[\w:<>,\*&]+[ \t\*&]+)+?([A-Za-z_]\w*)\s*\([^;{}]*\)\s*'
                        r'(?:throw\s*\(\s*\)\s*)?([;{])', re.M)
_FOREIGN = None


def _foreign_declarations(path):
    """{name: {unit sources}}: names a unit declares as a function (a prototype, at any scope) and does not define.
    Another unit that calls or takes the address of a harness through its own prototype (a variant copy's
    `probe_fn volatile g_keep = probe_caller;`) links against the definition; the MSVC objects do not show that
    reference when the name has no exe address, so units.json does not either."""
    global _FOREIGN
    if _FOREIGN is None:
        import json
        _FOREIGN = collections.defaultdict(set)
        root, uj = core._units_json(path)
        if uj:
            for src in sorted({u['source'] for u in json.load(open(uj))['units']}):
                p = os.path.join(root, src)
                try:
                    with open(p, encoding='utf-8', errors='surrogateescape') as fh:
                        t = fh.read()
                except OSError:
                    continue
                decl, defn = collections.Counter(), set()
                for m in _DECL_NAME.finditer(t):
                    if m[2] == ';':
                        decl[m[1]] += 1
                    else:
                        defn.add(m[1])
                words = collections.Counter(re.findall(r'[A-Za-z_]\w*', t)) if decl else {}
                for n, k in decl.items():
                    # used beyond its prototypes: a leftover prototype alone links nothing
                    if n not in defn and words[n] > k:
                        _FOREIGN[n].add(os.path.normpath(p))
    return _FOREIGN


def _params_key(params):
    """Parameter types of a declaration, for matching a prototype with its definition."""
    ps, depth, cur = [], 0, ''
    for ch in params:
        depth += ch in '(<'
        depth -= ch in ')>'
        if ch == ',' and depth == 0:
            ps.append(cur); cur = ''
        else:
            cur += ch
    ps.append(cur)
    ps = [re.sub(r'\s*([\*&])\s*', r'\1', s1_compile._param_type(p)) for p in ps if p.strip()]
    return tuple(p for p in ps if p != 'void')


_PP_OPEN, _PP_CLOSE = re.compile(r'^[ \t]*#[ \t]*if'), re.compile(r'^[ \t]*#[ \t]*endif')


def _conditional_depth(text, pos):
    """How many #if blocks are open at `pos`. A definition inside one is not known to be the one GCC compiles: a
    recon body defines the function under `#if defined(_MSC_VER)` and forwards it by assembler on the GCC side, so
    a `static` there would turn the unit's own prototype (and its call) internal while the definition is external."""
    depth = 0
    for line in text[:pos].split('\n'):
        if _PP_OPEN.match(line):
            depth += 1
        elif _PP_CLOSE.match(line):
            depth = max(0, depth - 1)
    return depth


def r_harness_internal(path, text, stats):
    """HARN1: an untagged free definition whose name no unit references (a decomp harness) becomes static."""
    if not path.endswith(('.cpp', '.c')):
        return text
    if s1_compile._unit_info(path) is None:
        return text
    refs = _referenced_identifiers(path)
    foreign, me = _foreign_declarations(path), os.path.normpath(os.path.abspath(path))
    masked = s1_compile._mask_code(text)
    spans = None
    defs = collections.defaultdict(list)     # name -> [(match, chain heads)]
    bad = set()
    for m in FREE_DEF.finditer(text):
        name, head = m[4], m[3]
        if masked[m.start(4):m.end(4)] != name or masked[m.end() - 1] != '{':
            continue                          # in a comment or a literal
        if name in s1_compile.NOT_FUNCTIONS or name in s1_compile.CXX_KEYWORDS:
            continue
        if spans is None:
            spans = core.scope_spans(text)
        chain = core.scope_chain(spans, m.start(4))
        if any(k not in ('namespace', 'extern') for k, _ in chain):
            continue                          # members, local classes, bodies
        if _conditional_depth(masked, m.start(4)):
            bad.add(name)                     # maybe not this compiler's definition (a recon body's MSVC branch)
            continue
        if (name in refs or foreign.get(name, set()) - {me} or name in HARN_KEEP_NAMES or name.startswith(('__fnv', '_fnvnx', 'fnvnx', 'FNVNX'))
                or HARN_SKIP_HEAD.search(head) or not re.search(r'\w', head)):
            bad.add(name)
            continue
        line0 = text.rfind('\n', 0, m.start(2)) + 1
        prev = text[max(0, line0 - 400):line0]
        if (re.search(r'//[ \t]*@fnv[ \t]+0x[0-9a-fA-F]+[^\n]*\n(?:[ \t]*//[^\n]*\n)*[ \t]*$', prev)
                or re.search(r'(?:template\s*<[^;{}]*>|__declspec\s*\([^)]*\)|\]\])\s*$', prev)):
            bad.add(name)
            continue
        defs[name].append((m, tuple(h for _, h in chain)))
    if not defs:
        return text
    edits = []
    for name, ds in sorted(defs.items()):
        if name in bad:
            continue
        keys = {(_params_key(m[5]), ch) for m, ch in ds}
        protos, uses, ok = [], [], True
        first = min(m.start(4) for m, _ in ds)
        for o in re.finditer(r'\b%s\b' % re.escape(name), masked):
            if any(m.start(4) == o.start() for m, _ in ds):
                continue
            line0 = masked.rfind('\n', 0, o.start()) + 1
            p = FREE_PROTO.match(text, line0)
            if p and masked[p.end() - 1] != ';':
                p = None
            chain = core.scope_chain(spans, o.start())
            if (p and p.start(4) == o.start() and all(k in ('namespace', 'extern') for k, _ in chain)
                    and not re.search(r'\b(?:return|else|case|goto|new|delete|throw)\b', p[3])):
                if ((_params_key(p[5]), tuple(h for _, h in chain)) not in keys or re.search(
                        r'\b(?:friend|virtual|inline|__inline|__forceinline|template|typedef|FNVNX_KEEP)\b',
                        p[3])):
                    ok = False                # a prototype of an overload this file does not define here
                    break
                first = min(first, o.start())
                if not re.search(r'\bstatic\b', p[3]):
                    protos.append(p)
            else:
                uses.append(o.start())
        # a mention before the first namespace-scope declaration is a block-scope declaration or a friend: it gave the
        # name external linkage, which a later `static` contradicts
        if not ok or any(u < first for u in uses):
            continue
        for m in [m for m, _ in ds]:
            edits.append((m.start(2), m.start(3), 'static '))
        for p in protos:
            edits.append((p.start(2), p.start(4), 'static ' + re.sub(r'\bextern\s+', '', text[p.start(3):p.start(4)])))
    if not edits:
        return text
    out, pos = [], 0
    for a, b, r in sorted(edits):
        out.append(text[pos:a]); out.append(r); pos = b
    out.append(text[pos:])
    stats['HARN1'] += sum(1 for a, b, r in edits)
    return ''.join(out)


# ---------------------------------------------------------------------------------------------------------- POP2
#
# POP2 - pop-mismatch chains, decided from the units' MSVC names; a POST hook on the prepared tree, so it sees the
# S1b patches and every rule's text (S2-bodies, from EQ's fuzz rows `pop-mismatch,ref-passes-fewer`). The decomp sometimes gives a receiver's stack arguments to the call
# before it: `buffer->Ent(2)` used as a bool, where 0xADDR takes only its result buffer (`ret 4`) and
# the conversion 0xADDR is `bool (unsigned int flags) const` (`ret 4`). On x86 the pushes are the same either
# way: the word the getter leaves on the stack is the flags word the conversion pops. Natively the conversion reads a
# register nobody set. S1b's popgen (tests/s1b/pop) writes such fixes as patch files from popscan's x86 analysis;
# it leaves out callers whose declarations live in a header or in more than one class body of the file (the
# `Ent` family in 27 files, the decomp's per-function namespaces). POP2 is the same fix as a rule, with no
# decomp text kept in this repository:
#
#     D(a.., x..)            used as a bool                 ->  D(a..).FUN_<R>(x..)      (R = D's result class's
#                                                                                          `operator bool`)
#     D(a.., x..) . R()      (or ->, or popgen's casts and member hops between)  ->  D(a..) . R(x..)
#     R(l.., D(a.., x..), r..)                               ->  R(l.., D(a..), x.., r..)
#
# Gates, all from build/units.json (no x86 code is read): the unit's references D and R are member functions bound
# to addresses whose definitions are callee-pop (thiscall, stdcall, fastcall) with known stack words; D's
# reference pushes s > 0 words more than its definition pops, R's reference s fewer; every reference of the unit
# with D's identifier and the call's arity (likewise R's) has those same facts; D's trailing reference parameters of
# s words are the moved ones and are plain scalars or pointers (every candidate class moving the same MSVC types);
# the moved arguments have no side effects (x86 evaluated them before D, GCC's C++17 order evaluates D first); a
# chained receiver takes no arguments of its own; an implicit conversion is read as a bool (a condition, `&&`, `||`,
# `?:`, or `.operator bool()` spelled out). The candidates are the references a call there can name: an unqualified
# call in a member of C names C's (or a direct base's) member, and inside a per-function namespace fnv_<a> its own
# class hides the global one of the same name; a class of some other namespace counts only when no class of the
# global or an enclosing namespace has the member (a wrong pick fails to compile: the overload is not in its class).
# Nothing then sits between D's return and R's call on x86 (eax moves to ecx; for the nested shape R's own
# arguments), so the words D leaves are the words R pops, in this order. The MSVC pushes stay the same (S1b's pilot
# and popgen's 222 callers compiled identical).
#
# Declarations: the overloads go next to the decomp's in the class body MSVC's name places (the unit file or a local
# header it includes, the class found by its enclosing namespaces and classes): D without the moved parameters, R
# with them (their spelling from D's declaration); an `operator bool` receiver becomes `bool FUN_<R>(x..) const`. A
# declaration that already exists (an S1b patch's) is not added twice; an overload of the same arity with other
# parameter types, a virtual, static or defaulted declaration, an inline-defined donor, or a class body found 0 or 2+
# times leave the family alone. An inline-defined RECEIVER is allowed when it is an untagged stand-in (W25: a
# unit-local copy's `__forceinline operator bool() const { return unit != 0; }`, fnv_ADDR): the exe's receiver
# is the function the unit's symbols bind that MSVC name to (R's address, its `ret N` popping the moved words, the same
# gates as for a declared-only receiver), and the stand-in's body would compute `flags != 0` on a register nobody set.
# The new overload is declared without a body (and without inline / __forceinline), so the moved call reaches the exe
# function through the new decorated name the link binds; the stand-in stays for the calls POP2 leaves alone (a body
# with its own `@fnv` tag is a decompiled function and stays skipped).
# A call whose receiver is cast to a class written with namespaces (`((N::Ent*)x)->Ent(m)`,
# `reinterpret_cast<N::C*>(x)->`, unit.cpp 0xADDR) names that class: its donor and its result class's
# `operator bool` are looked up in exactly that class, where the name-hiding rule above would let a global class with
# a member of the same name (Ent::Ent) hide the namespace's. The POST hook adds the new decorated reference names to the unit's units.json `symbols`
# (D's name without the moved parameter codes, R's with them), so the link compares and binds them like the
# decomp's own. Line numbers stay. Families left alone are counted (POP2-SKIP-<why>) and listed with their reason
# in <tree>/build/pop2_skipped.tsv, the sites done in <tree>/build/pop2_sites.tsv (derived, local only).

import _msvcsig                    # noqa: E402
import _popgen                     # noqa: E402

POP2_NOTE = ' /* fnv-nx POP2: overloads with the words the callees pop */'
pg = _popgen


def _pop_args(s):
    """Top-level arguments of a call's argument text, spelled as in s. popgen's split_args counts `<` / `>` as
    brackets, so the `>` of `->` would close one: it is read as `..` here (same length)."""
    out, at = [], 0
    for x in pg.split_args(s.replace('->', '..')):
        out.append(s[at:at + len(x)])
        at += len(x) + 1
    return out


def _pop_arity(s):
    return 0 if _pop_void(s) else len(_pop_args(s))


def _pop_void(s):
    return re.sub(r'\s+', '', s) in ('', 'void')


class _PopRef:
    __slots__ = ('name', 'fn', 'addr', 'delta')

    def __init__(self, name, fn, addr, delta):
        self.name, self.fn, self.addr, self.delta = name, fn, addr, delta


def _pop_def_words(units):
    """{address: stack words its callee-pop definition pops} (one value per address, else left out)."""
    seen = collections.defaultdict(set)
    for u in units:
        for f in u.get('functions') or ():
            fn = _msvcsig.parse(f['symbol'])
            a = int(f['address'], 16)
            if fn is None:
                seen[a].add(None)
            else:
                seen[a].add(_msvcsig.stack_words(fn) if _msvcsig.callee_pop(fn) else None)
    return {a: next(iter(v)) for a, v in seen.items() if len(v) == 1 and None not in v}


def _pop_unit_refs(u, words):
    """{identifier or 'operator bool': [_PopRef]} for the member references of unit u (delta = the words the
    reference pushes minus the words the definition pops; None when not comparable)."""
    out = collections.defaultdict(list)
    for name, a in (u.get('symbols') or {}).items():
        if not name.startswith('?'):
            continue
        fn = _msvcsig.parse(name)
        if fn is None or not fn.member or fn.static:
            continue
        a = int(a, 16)
        rw = _msvcsig.stack_words(fn) if _msvcsig.callee_pop(fn) else None
        dw = words.get(a)
        delta = rw - dw if rw is not None and dw is not None else None
        out[fn.special or fn.ident].append(_PopRef(name, fn, a, delta))
    return out


def _pop_world(root):
    """The POP2 facts of the tree at root (its build/units.json): {'units': {unit file: source}, 'refs': {unit file:
    _pop_unit_refs}} for the C++ units with a reference whose words differ from its definition's."""
    import json
    w = dict(root=root, units={}, refs={})
    uj = os.path.join(root, 'build', 'units.json')
    if not os.path.exists(uj):
        return w
    with open(uj) as f:
        units = json.load(f)['units']
    words = _pop_def_words(units)
    for u in units:
        if not u['source'].endswith('.cpp'):
            continue
        refs = _pop_unit_refs(u, words)
        if any(r.delta for rs in refs.values() for r in rs):
            k = os.path.normpath(os.path.join(root, u['source']))
            w['units'][k] = u['source']
            w['refs'][k] = refs
    return w


def _pop_same(cands):
    """The one (address, delta) every candidate reference has, else None."""
    s = {(r.addr, r.delta) for r in cands}
    return next(iter(s)) if len(s) == 1 else None


def _pop_split(fn, s):
    """Number k of fn's trailing parameters whose words add up to s (all plain scalars / pointers), else None."""
    acc, k = 0, 0
    while acc < s and k < len(fn.params):
        code, w, kd = fn.params[-1 - k]
        if w is None or kd not in ('int', 'ptr', 'float', 'double', 'int64') or code is None:
            return None
        acc += w
        k += 1
    return k if acc == s else None


def _pop_moved_codes(fn, k):
    """The parameter codes of fn's last k parameters, type back-references spelled out (valid in any name)."""
    return ''.join(code for code, _, _ in fn.params[len(fn.params) - k:])


def _pop_function_bodies(text):
    """(open, close) of every outermost function body."""
    out, last = [], -1
    for o, c, k, _ in core.scope_spans(text):
        if k == 'function' and o > last:
            out.append((o, c))
            last = c
    return out


def _pop_sites(text, refs, why):
    """The POP2 sites of a unit: [dict(fam, edits=[(start, end, replacement)], decls=[...])]. why: Counter of the
    reasons a candidate chain is left alone."""
    masked = s1_compile._mask_code(text)
    donors = sorted(n for n, rs in refs.items() if n != 'operator bool' and any((r.delta or 0) > 0 for r in rs))
    if not donors:
        return []
    out = []
    spans = core.scope_spans(masked)
    # the namespaces in which the text defines functions (a body there sees that namespace's classes only from inside)
    nsall = set()
    for fo, _ in _pop_function_bodies(masked):
        ch = core.scope_chain(spans, fo)
        if ch and ch[-1][0] == 'namespace' and ch[-1][1].split():
            nsall.add(ch[-1][1].split()[-1])
    bases = collections.defaultdict(set)
    for hb in re.finditer(r'\b(?:struct|class)\s+([A-Za-z_]\w*)\s*:\s*([^;{}()]*)\{', masked):
        bases[hb[1]] |= set(re.findall(r'[A-Za-z_]\w*', hb[2])) - {'public', 'private', 'protected',
                                                                                 'virtual'}
    for o, c in _pop_function_bodies(masked):
        seg = masked[o:c]
        # the namespaces of this unit's text a call here can see (a class of a sibling namespace cannot be the
        # object's type); nsall: the namespaces the text defines functions in
        chain = core.scope_chain(spans, o)
        vis = {h.split()[-1] for k, h in chain if k == 'namespace' and h.split()}
        # the class whose member this body defines (`R C::f(..) {` or a body inside `struct C {`): an unqualified call
        # of a member name calls C's member
        hm = re.search(r'([A-Za-z_]\w*)\s*::\s*~?[A-Za-z_]\w*\s*\([^;{}]*\)\s*(?:const\s*)?$',
                       masked[max(0, o - 600):o])
        own = hm[1] if hm else (_scope_name(chain[-1][0], chain[-1][1]) if chain and chain[-1][0] == 'class'
                                      else None)
        if own:   # with its direct bases (`struct C : B {`): the member may be inherited
            own = {own} | bases.get(own, set())
        for dn in donors:
            for m in re.finditer(r'(?<![\w~])%s\s*\(' % re.escape(dn), seg):
                p = m.end() - 1
                e = pg.match_paren(seg, p)
                if e is None:
                    continue
                before = seg[:m.start()].rstrip()
                qualified = before.endswith(('.', '->', '::'))
                vw = _PopVisible(refs, vis, nsall, None if qualified else own)
                vw.cast = _pop_cast(before) if qualified else None
                site = _pop_site(seg, o, m.start(), p, e, dn, vw, pg, why, text)
                if site:
                    tags = list(s1_compile.FNV_TAG_ADDR.finditer(text, max(0, o - 2000), o))
                    site['caller'] = tags[-1][1].lower().zfill(8) if tags else '-'
                    out.append(site)
    return out


class _PopVisible:
    """A unit's references as seen from one function: those of a class in a namespace the unit's text defines functions in count
    only inside that namespace, and there a class of that namespace hides the global class of the same name (the
    decomp declares one `Holder` per namespace)."""

    def __init__(self, refs, vis, ns, own=None):
        self.refs, self.vis, self.ns, self.own, self.cast = refs, vis, ns, own, None

    def named(self, name, scopes):
        """The references of name in exactly the class MSVC's scopes name (innermost first), as a per-function
        namespace the call sees allows."""
        return [r for r in self.refs.get(name, ()) if r.fn.scopes == scopes
                and all(sc in self.vis for sc in scopes if sc in self.ns)]

    def donor(self, name):
        """The references an unqualified call of name in a member of class `own` can be (its own class's); for a
        receiver cast to a written class (`((N::C*)x)->D(..)`) those of exactly that class."""
        if self.cast:
            cls, quals = self.cast
            return [r for r in self.refs.get(name, ()) if r.fn.scopes[0] == cls
                    and r.fn.scopes[1:1 + len(quals)] == quals
                    and all(sc in self.vis for sc in r.fn.scopes[1 + len(quals):] if sc in self.ns)
                    and all(sc in self.ns for sc in r.fn.scopes[1 + len(quals):])
                    and all(sc in self.vis for sc in r.fn.scopes if sc in self.ns)]
        rs = self.get(name)
        return [r for r in rs if r.fn.scopes[0] in self.own] if self.own else rs

    def get(self, name, default=()):
        seen = [r for r in self.refs.get(name, default)
                if all(sc in self.vis for sc in r.fn.scopes if sc in self.ns)]
        # classes declared in the global namespace or one around the call come before those of other scopes
        near = [r for r in seen if all(sc in self.vis for sc in r.fn.scopes[1:])]
        seen = near or seen
        depth = collections.defaultdict(int)
        for r in seen:
            d = sum(1 for sc in r.fn.scopes if sc in self.ns)
            depth[r.fn.scopes[0]] = max(depth[r.fn.scopes[0]], d)
        return [r for r in seen if sum(1 for sc in r.fn.scopes if sc in self.ns) == depth[r.fn.scopes[0]]]

    def __contains__(self, name):
        return bool(self.get(name))


_POP_CAST = (re.compile(r'\(\s*(?:const\s+)?((?:[A-Za-z_]\w*\s*::\s*)+)([A-Za-z_]\w*)\s*\*\s*\)\s*[\w.>-]+\s*\)\s*(?:->|\.)$'),
             re.compile(r'(?:reinterpret_cast|static_cast)\s*<\s*(?:const\s+)?((?:[A-Za-z_]\w*\s*::\s*)+)([A-Za-z_]\w*)\s*\*\s*>'
                        r'\s*\([^()]*\)\s*(?:->|\.)$'))


def _pop_cast(before):
    """(class, qualifiers innermost first) when the receiver of the call that follows `before` is a pointer cast to a
    class written with namespaces (`((N::C*)x)->`, `reinterpret_cast<N::C*>(x)->`), else None: the written class is
    the receiver's, whatever other classes of the name the unit has."""
    for rx in _POP_CAST:
        m = rx.search(before)
        if m:
            q = [x.strip() for x in m[1].split('::') if x.strip()]
            return m[2], q[::-1]
    return None


def _pop_site(seg, base, ds, p, e, dn, refs, pg, why, text):
    args = _pop_args(seg[p + 1:e - 1])
    n = _pop_arity(seg[p + 1:e - 1])
    dc = [r for r in refs.donor(dn) if len(r.fn.params) == n]
    f = _pop_same(dc)
    if not dc or f is None or not f[1] or f[1] <= 0:
        if dc and any((r.delta or 0) > 0 for r in dc):
            why['donor references disagree'] += 1
        return None
    daddr, s = f
    ks = {_pop_split(r.fn, s) for r in dc}
    if len(ks) != 1 or None in ks:
        why['donor parameters do not split'] += 1
        return None
    k = ks.pop()
    codes = {_pop_moved_codes(r.fn, k) for r in dc}
    rest = seg[e:]
    shape, rn, rspan, nest, explicit = None, None, None, None, None
    mm = re.match(r'\s*(?:\.|->)\s*([A-Za-z_]\w*)\s*\(', rest)
    mo = re.match(r'\s*\.\s*operator\s+bool\s*\(\s*\)', rest)
    m2 = re.search(r'(?:\.|->)\s*([A-Za-z_]\w*)\s*\(', rest)
    if mm:
        shape, rn, rp = 'chain', mm[1], e + mm.end() - 1
    elif mo:
        explicit = (e, e + mo.end())             # `D(..).operator bool()` spelled out
    elif m2 and pg.reach_receiver(rest, m2[1]) and not re.search(r'[)\]]\s*[A-Za-z_]', rest[:m2.start()]):
        # casts and member hops between D's result and R (popgen's reach); an identifier right after a `)` starts
        # another expression (`if (b->D(1)) x->R(..)`)
        shape, rn = 'chain', m2[1]
        rp = e + pg.reach_receiver(rest, rn).end() - 1
    elif re.match(r'\s*(?:\.|->|\[)', rest):
        why['result used otherwise'] += 1
        return None
    if shape == 'chain':
        re_ = pg.match_paren(seg, rp)
        if re_ is None:
            return None
        if _pop_arity(seg[rp + 1:re_ - 1]):
            why['chained receiver has arguments'] += 1
            return None
        rc = [r for r in refs.get(rn, ()) if not r.fn.params]
        rspan = (rp, re_)
    else:
        # nested: D's call is a whole argument of a call `rn(..)`
        k0, depth = ds - 1, 0
        while k0 >= 0:
            ch = seg[k0]
            if ch in ')]':
                depth += 1
            elif ch in '([':
                if depth == 0:
                    break
                depth -= 1
            elif ch in ';{}':
                k0 = -1
                break
            k0 -= 1
        mn = re.search(r'([A-Za-z_]\w*)\s*$', seg[:k0]) if k0 > 0 and seg[k0] == '(' else None
        if mn and mn[1] in refs and mn[1] != dn and explicit is None:
            nest = pg.enclosing_arg(seg.replace('->', '..'), ds, e, mn[1])
        if nest is not None:
            shape, rn = 'nested', mn[1]
            unq = not seg[:mn.start(1)].rstrip().endswith(('.', '->', '::'))
            rc = [r for r in (refs.donor(rn) if unq else refs.get(rn))
                  if len(r.fn.params) == _pop_arity(seg[nest[0] + 1:nest[1] - 1])]
        else:
            shape, rn = 'bool', 'operator bool'
            rc = []
            for r in dc:
                if r.fn.ret_class is None:
                    rc = None
                    break
                rc += [x for x in (refs.named('operator bool', r.fn.ret_class) if refs.cast else
                                   refs.get('operator bool', ())) if x.fn.scopes == r.fn.ret_class]
            if not rc:
                if rc is not None and any(r.fn.ret_class for r in dc):
                    why['no operator bool reference'] += 1
                return None
            if explicit is None and not re.match(r'\s*[)&|?]', rest):
                # converted only where a bool is read (a condition, &&, ||, ?:); a discarded or stored result
                # never called the conversion on x86
                why['result not read as a bool'] += 1
                return None
    if shape != 'bool' and len(codes) != 1:
        # the call's donor class is one of several with other moved types: the receiver's overload could not keep
        # the words the x86 caller pushed (an int read as a float would be converted)
        why['donor references disagree on the moved types'] += 1
        return None
    g = _pop_same(rc) if rc else None
    if g is None or g[1] != -s:
        if rc:
            why['receiver words differ'] += 1
        return None
    raddr = g[0]
    moved = [x.strip() for x in args[len(args) - k:]]
    if any(pg.IMPURE.search(x) for x in moved):
        why['moved argument with side effects'] += 1
        return None
    raw = _pop_args(text[base + p + 1:base + e - 1])            # the original spelling (literals unmasked)
    if len(raw) != len(args):
        return None
    keep = ','.join(raw[:len(raw) - k]).rstrip()
    edits = [(base + p + 1, base + e - 1, keep)]
    mv = ', '.join(x.strip() for x in raw[len(raw) - k:])
    if shape == 'chain':
        edits.append((base + rspan[0] + 1, base + rspan[1] - 1, mv))
        ins = 0
    elif shape == 'nested':
        edits.append((base + e, base + e, ', ' + mv))
        ins = nest[2] + 1
    else:
        x0, x1 = explicit or (e, e)
        edits.append((base + x0, base + x1, '.FUN_%08x(%s)' % (raddr, mv)))
        ins = 0
    return dict(fam=(dn, n, shape, rn, daddr, raddr, s), k=k, ins=ins, dc=dc, rc=rc, edits=edits)


def _pop_includes(path, text, depth=0, seen=None):
    """path and the local headers it includes (quoted, relative to the file), recursively."""
    seen = set() if seen is None else seen
    path = os.path.normpath(path)
    if path in seen or depth > 6:
        return []
    seen.add(path)
    out = [path]
    for m in re.finditer(r'^[ \t]*#[ \t]*include[ \t]*"([^"]+)"', text, re.M):
        q = os.path.normpath(os.path.join(os.path.dirname(path), m[1]))
        if os.path.exists(q) and q not in seen:
            try:
                with open(q, encoding='utf-8', errors='surrogateescape') as f:
                    t = f.read()
            except OSError:
                continue
            out += _pop_includes(q, t, depth + 1, seen)
    return out


def _pop_class_bodies(text, scopes):
    """(open, close + 1) of the class bodies MSVC's scopes (innermost first) name in text."""
    out = []
    spans = core.scope_spans(text)
    for o, c, kind, head in spans:
        if kind != 'class' or _scope_name(kind, head) != scopes[0]:
            continue
        chain = core.scope_chain(spans, o)
        if any(k not in ('namespace', 'class', 'extern') for k, _ in chain):
            continue
        names = [_scope_name(k, h) for k, h in reversed(chain) if k != 'extern' and h.split()[-1] != 'namespace']
        if names == list(scopes[1:]):
            out.append((o, c + 1))
    return out


_POP_INLINE_RET = re.compile(r'\b(?:__forceinline|FNV_FORCEINLINE|__inline|inline)\b')


def _pop_plain_ret(ret):
    """A stand-in's return type as a declaration-only overload spells it: no comments, no inline specifier."""
    ret = re.sub(r'//[^\n]*|/\*.*?\*/', ' ', ret, flags=re.S)
    return re.sub(r'\s+', ' ', _POP_INLINE_RET.sub('', ret)).strip()


def _pop_inline_stand_in(t, d):
    """True when the receiver declaration d (popgen member_decls row of the class text t) is an untagged in-class body
    and nothing else is wrong with it: a unit-local copy's stand-in for the exe function the unit's symbols bind the
    receiver's MSVC name to (`__forceinline operator bool() const { return unit != 0; }`). The overload POP2
    adds is declared without a body, so the call reaches the exe function through the new name; the stand-in stays
    for the calls POP2 leaves alone. A body with its own `// @fnv` tag is a decompiled function and stays skipped."""
    if set(d['bad']) - {'inline body', 'no ;'} or 'inline body' not in d['bad']:
        return False
    return '@fnv' not in d['ret']          # the member's head text holds the comment lines above it


def _pop_decl_ops(site, files, texts, why):
    """The declarations a site's family needs: [(file, class body, insert position or None when the overload is
    already declared, name, parameter types, declaration)], or None (the reason counted in why)."""
    ops, dparams = [], {}
    for role, cands in (('D', site['dc']), ('R', site['rc'])):
        for r in cands:
            hits = [(fp, b) for fp in files for b in _pop_class_bodies(texts[fp], r.fn.scopes)]
            if len(hits) != 1:
                why['%s class body found %s' % (role, 'more than once' if hits else 'nowhere')] += 1
                return None
            fp, body = hits[0]
            t = texts[fp]
            name = 'operator bool' if site['fam'][2] == 'bool' and role == 'R' else (
                site['fam'][0] if role == 'D' else site['fam'][3])
            md = [x for x in pg.member_decls(t, body, name) if pg.arity(x['params']) == len(r.fn.params)]
            if role == 'R' and len(md) == 1 and _pop_inline_stand_in(t, md[0]):
                md[0] = dict(md[0], bad=[], ret=_pop_plain_ret(md[0]['ret']))
            if len(md) != 1 or md[0]['bad']:
                why['%s declaration %s' % (role, ','.join(md[0]['bad']) if len(md) == 1 else
                                           '%d candidates' % len(md))] += 1
                return None
            md = md[0]
            plist = [pg.norm(x) for x in pg.split_args(md['params']) if pg.norm(x) not in ('', 'void')]
            if role == 'D':
                k = site['k']
                dparams[tuple(r.fn.ret_class or ()) if site['fam'][2] == 'bool' else ()] = plist[len(plist) - k:]
                nm, ret, cst, ps = name, md['ret'], md['const'], plist[:len(plist) - k]
            else:
                if site['fam'][2] == 'bool':
                    moved = dparams.get(tuple(r.fn.scopes))
                else:
                    moved = dparams.get(())       # every donor candidate moves the same MSVC types (_pop_site)
                if moved is None:
                    why['donor parameter types differ'] += 1
                    return None
                taken = {pg.pname(x) for x in plist} - {None}
                mv = []
                for x in moved:
                    if pg.pname(x) in taken:
                        x = pg.param_type(x)
                    taken.add(pg.pname(x))
                    if re.match(r'^(?:const\s+)?%s\s*<' % pg.ID, x):
                        why['moved class template parameter'] += 1
                        return None
                    mv.append(pg.elaborate(t, body[0], x))
                ps = plist[:site['ins']] + mv + plist[site['ins']:]
                if name == 'operator bool':
                    nm, ret, cst = 'FUN_%08x' % site['fam'][5], 'bool', md['const']
                else:
                    nm, ret, cst = name, md['ret'], md['const']
            types = tuple(pg.param_type(x) for x in ps)
            same = [x for x in pg.member_decls(t, body, nm) if pg.arity(x['params']) == len(ps)]
            if same and not all(pg.same_params(x['params'], ', '.join(ps)) for x in same):
                why['overload clash'] += 1          # an overload of the new arity with other parameter types
                return None
            decl = '%s %s(%s)%s;' % (ret, nm, ', '.join(ps), ' const' if cst else '')
            ops.append((fp, body, None if same else md['end'], nm, types, decl))
    return ops


def _pop_new_names(site):
    """[(new decorated reference name, address)] for a site's family (POST adds them to the unit's symbols): D's
    names without the moved parameter codes, R's with them. None when a name cannot be re-encoded by splicing (a
    name back-reference the new identifier would shift, a type back-reference behind an inserted code)."""
    out = []
    k, ins = site['k'], site['ins']
    codes = {}
    for r in site['dc']:
        fn, s = r.fn, r.name
        cut = fn.param_spans[len(fn.params) - k][0]
        codes[tuple(fn.ret_class or ())] = _pop_moved_codes(fn, k)
        out.append((s[:cut] + ('XZ' if len(fn.params) == k else '@Z'), '0x%08x' % r.addr))
    for r in site['rc']:
        fn, s = r.fn, r.name
        if site['fam'][2] == 'bool':
            mc = codes.get(tuple(fn.scopes))
            if mc is None or fn.name_refs:
                return None
            new = '?FUN_%08x@' % r.addr + s[3:fn.ret_span[1]] + mc + '@Z'
        else:
            if len(set(codes.values())) != 1:
                return None
            mc = next(iter(codes.values()))
            if not fn.params:
                new = s[:fn.ret_span[1]] + mc + '@Z'
            else:
                if any(re.search(r'\d', s[a_:b_]) for a_, b_ in fn.param_spans[ins:]):
                    # a back-reference behind the inserted codes: the names they add would shift it
                    return None
                at = fn.param_spans[ins - 1][1] if ins else fn.param_spans[0][0]
                new = s[:at] + mc + s[at:]
        g = _msvcsig.parse(new)
        if g is None or len(g.params) != len(fn.params) + k:
            return None
        out.append((new, '0x%08x' % r.addr))
    return out


def pop_chains(out_root, stats, files=None):
    """POP2: pop-mismatch chains: the trailing arguments the decomp gave to the call before move to the call that pops
    them, with the overloads declared and their decorated names added to build/units.json (see above). A POST hook:
    it reads the prepared tree after every rule, so it sees the S1b patches and the other rules' text."""
    w = _pop_world(out_root)
    texts, inserts, unit_edits = {}, collections.defaultdict(dict), collections.defaultdict(list)
    renames, skipped, done = collections.defaultdict(set), [], []

    def read(fp):
        if fp not in texts:
            with open(fp, encoding='utf-8', errors='surrogateescape') as f:
                texts[fp] = f.read()
        return texts[fp]
    for uf in sorted(w['units']):
        if not os.path.exists(uf):
            continue
        text = read(uf)
        why = collections.Counter()
        sites = _pop_sites(text, w['refs'][uf], why)
        fams = collections.defaultdict(list)
        for st in sites:
            # one family per candidate set: the same calls in two namespaces need their own declarations
            key = (st['fam'], tuple(sorted(r.name for r in st['dc'])), tuple(sorted(r.name for r in st['rc'])))
            fams[key].append(st)
        if fams:
            inc = _pop_includes(uf, text)
            for fp in inc:
                read(fp)
        for (fam, _, _), sts in sorted(fams.items(), key=lambda x: str(x[0])):
            ops = _pop_decl_ops(sts[0], inc, texts, why)
            if ops is None:
                continue
            names = _pop_new_names(sts[0])
            if names is None:
                why['reference name not re-encodable'] += 1
                continue
            clash = False
            for fp, body, pos, nm, types, decl in ops:
                for (b0, nm_, ar_), (_, types_, _) in inserts[fp].items():
                    if b0 == body[0] and nm_ == nm and ar_ == len(types) and types_ != types:
                        clash = True        # another family plans an overload of the same arity with other types
            if clash:
                why['overload clash'] += 1
                continue
            for fp, body, pos, nm, types, decl in ops:
                if pos is not None:
                    inserts[fp].setdefault((body[0], nm, len(types)), (pos, types, decl))
            for st in sts:
                unit_edits[uf] += st['edits']
                done.append((w['units'][uf], st['caller'], fam[0], fam[3], '%08x' % fam[4], '%08x' % fam[5],
                             fam[2], fam[6]))
            stats['POP2'] += len(sts)
            renames[w['units'][uf]].update(names)
        for k, v in sorted(why.items()):
            skipped.append((w['units'][uf], k, v))
    for fp in sorted(set(inserts) | set(unit_edits)):
        text = texts[fp]
        edits = list(unit_edits.get(fp, ()))
        for (b0, nm, ar), (pos, types, decl) in sorted(inserts.get(fp, {}).items(), key=lambda x: (x[1][0], x[0])):
            edits.append((pos, pos, ' ' + decl + POP2_NOTE))
            stats['POP2-DECL'] += 1
        out, at = [], 0
        for a, b, r in sorted(edits, key=lambda x: (x[0], x[1])):
            if a < at:
                stats['POP2-OVERLAP'] += 1
                continue
            out.append(text[at:a]); out.append(r); at = b
        out.append(text[at:])
        with open(fp, 'w', encoding='utf-8', errors='surrogateescape') as f:
            f.write(''.join(out))
        if files is not None:
            files['POP2'] += 1
    import json
    p = os.path.join(out_root, 'build', 'units.json')
    if renames and os.path.exists(p):
        with open(p) as f:
            data = json.load(f)
        for u in data['units']:
            for name, a in sorted(renames.get(u['source'], ())):
                if name not in u['symbols']:
                    u['symbols'][name] = a
                    stats['POP2-REF'] += 1
        with open(p, 'w') as f:
            json.dump(data, f)
    if os.path.isdir(os.path.join(out_root, 'build')):
        with open(os.path.join(out_root, 'build', 'pop2_skipped.tsv'), 'w') as f:
            f.write('# unit\treason\tcalls (POP2, tools/rules/s2_bodies.py; derived from the decomp: local only)\n')
            for row in skipped:
                f.write('\t'.join(str(x) for x in row) + '\n')
        with open(os.path.join(out_root, 'build', 'pop2_sites.tsv'), 'w') as f:
            f.write('# unit\tcaller\tdonor\treceiver\tdonor address\treceiver address\tshape\twords (POP2; local '
                    'only)\n')
            for row in done:
                f.write('\t'.join(str(x) for x in row) + '\n')
    for _, k, v in skipped:
        stats['POP2-SKIP-' + re.sub(r'\W+', '-', k).strip('-')] += v


# ADDRFN1 - a code address the decomp writes as an integer (`unit = reinterpret_cast<void*>(0xADDR);`,
# Ent::Ent and the archive's handler pairs): natively the raw exe address is stored and later called -
# the stub boot's first WinMain-path fault (link d1ed15e7: the Ent archive constructor 0xADDR calls 0xADDR).
# When some unit defines the address (units.json `functions`), the cast takes the address symbol instead, which the
# link binds to the native definition: FNVNX_ADDRFN(<a>) (fnvnx_compat.h: a GCC block-scope asm label in a
# statement expression, so line numbers stay and nothing at file scope changes; tools/equiv/msvc: the decomp's own
# `reinterpret_cast<void*>(0x<a>)`). Only .text addresses (0xADDR .. 0xADDR) cast to `void*`.
ADDRFN_CAST = re.compile(r'reinterpret_cast<\s*void\s*\*\s*>\(\s*0x(00[0-9a-fA-F]{6})\s*\)')


def r_code_address_casts(path, text, stats):
    """ADDRFN1: `reinterpret_cast<void*>(0x00<a>)` of a defined code address -> the address symbol __fnv_<a>."""
    if 'reinterpret_cast' not in text or not path.endswith(('.cpp', '.h', '.inl')):
        return text
    defined = s1_compile._all_unit_functions(path)

    def sub(m):
        a = int(m[1], 16)
        if not (0x00401000 <= a < 0x00fdf000) or a not in defined:
            return m[0]
        stats['ADDRFN1'] += 1
        return 'FNVNX_ADDRFN(%08x) /* fnv-nx ADDRFN1 */' % a
    return ADDRFN_CAST.sub(sub, text)


RULES = [r_harness_internal, r_code_address_casts]
POST = [pop_chains]
