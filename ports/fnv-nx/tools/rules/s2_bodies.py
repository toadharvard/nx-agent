"""Prepare rules of stream S2-bodies (tools/rules/README.md).

STAND5 - in-class stand-in bodies MSVC called out of line.

STAND1/STAND2 (s1_compile.py) drop untagged out-of-class definitions bound to another function's address. The same
happens with a member defined in its class body: blk_00c60000_od_ob1_variant.cpp's `List62::Add62` is a list append
written inline in `struct List62`, while the unit's units.json `symbols` bind ?Add62@List62@@... to 0x00c62cc0, which
the exe's code calls and which is an array Add (grow through 0x00c628c0, then SetAt 0x00c62b90; R6, recon6-002). A
name in `symbols` is one the MSVC object references out of line, so the original called the address there. GCC
inlines the local body instead, which references List62::NewItem and Item62::SetElement: names no exe function
has.

STAND5 turns such an in-class body into its declaration, so the call stays a call of the name the link binds to the
address. Gates: a member function body directly inside a class (only namespaces and classes around it); not virtual,
not a destructor or operator; untagged (no `// @fnv` above it); every spelling of its MSVC name in the unit's symbols
binds an address that another unit's `functions` define and this unit does not.

Also (R6 class 5): a constructor without an initializer list (menu.cpp's `FUN_00a0ac80()` builds four entries
through Init, where the exe's 0x00a0ac80 builds a list: `??0` bound there); a head with `__declspec(noinline)`
(bhkrigidbody.cpp's ObjectListC8E::AddTail, 0x00c80a30); and a member of a class template whose head says
`__declspec(noinline)`, so MSVC called every instantiation out of line and each is in the symbols
(blk_00c80000_o2.cpp's BSSimpleListC9A<..>::Remove, 0x00631620), when the unit defines none of them. The class name
comes from its head, past a base clause (`struct D : public B`).

Also (s2d crt-import): in the decomp's unit copies of the MSVC STL (`namespace fnv_<a> { namespace std`), members of
class templates and of classes nested in them, and constructors with an initializer list, under the same symbol
gate (`_stl_copy_edits`): MSVC called `basic_ofstream(const char*, int, int)` (0x00acdc90), `basic_filebuf::close`
(0x00acfb90) and `basic_ostream::sentry::sentry` out of line, where GCC inlined the decomp's partial model of them
and referenced members it never defines (`basic_filebuf::open`, `_Init`, `basic_streambuf::_Lock`).

HARN1 - the decomp's harness-only code gets internal linkage.

The decomp keeps byte-matching harnesses next to the matched code: convention callers that exist only so MSVC emits
a call with the right registers (`int stub_caller() { return FUN_x(stub_a(), stub_b()); }`, `void wblk_caller()`,
`extern "C" void S00db0aa0_Entry(void)`), whose stubs are declared and never defined. No exe function is theirs and
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
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import prepare                     # noqa: E402
import s1_compile                  # noqa: E402  (the units.json readers STAND1/2 use)

INCLASS_DEF = re.compile(r'^([ \t]*)((?:(?:__declspec\s*\(\s*\w+\s*\)|[\w:<>,\*&]+)[ \t\*&]+)*?)([A-Za-z_]\w*)\s*'
                         r'\(([^;{}()]*(?:\([^;{}()]*\)[^;{}()]*)*)\)\s*((?:const\s*)?)\{', re.M)
CLASS_HEAD = re.compile(r'^(?:template\s*<(?:[^<>]|<(?:[^<>]|<[^<>]*>)*>)*>\s*)?(?:typedef\s+)?(?:class|struct|union)\s+'
                        r'(?:__declspec\s*\([^()]*\)\s*)?([A-Za-z_]\w*)')
NOINLINE = re.compile(r'__declspec\s*\(\s*noinline\s*\)')


def _scope_name(kind, head):
    """The name a scope_chain entry introduces: a namespace's last word, a class's name (past a base clause)."""
    if kind == 'class':
        m = CLASS_HEAD.match(head.strip())
        return m.group(1) if m else None
    return head.split()[-1] if head.split() else None


SKIP_HEAD = re.compile(r'\b(?:virtual|operator|return|else|case|new|delete|throw|typedef|friend)\b')


TEMPLATE_HEAD = re.compile(r'template\s*<')
_FUNCTION_NAMES = {}


def _unit_function_names(path):
    """The MSVC names the unit's units.json `functions` define (with their fold aliases)."""
    if not _FUNCTION_NAMES:
        import json
        root, uj = prepare._units_json(path)
        _FUNCTION_NAMES[None] = True
        if uj:
            for u in json.load(open(uj))['units']:
                _FUNCTION_NAMES[os.path.normpath(os.path.join(root, u['source']))] = {
                    n for f in u.get('functions') or () for n in [f['symbol']] + list(f.get('fold_aliases') or ())}
    return _FUNCTION_NAMES.get(os.path.normpath(os.path.abspath(path)), set())


def r_stand_in_class_bodies(path, text, stats):
    """STAND5: an in-class body whose MSVC name the unit binds to another unit's function becomes a declaration."""
    if not path.endswith('.cpp'):
        return text
    syms = s1_compile._unit_symbols(path)
    info = s1_compile._unit_info(path)
    if not syms or info is None:
        return text
    elsewhere = s1_compile._all_unit_functions(path) - info[1]
    tagged = {int(a, 16) for a in s1_compile.FNV_TAG_ADDR.findall(text)}
    own_names = _unit_function_names(path)
    spans, edits = None, []
    for m in INCLASS_DEF.finditer(text):
        name, head = m.group(3), m.group(2) or ''
        if name in s1_compile.NOT_FUNCTIONS or name in s1_compile.CXX_KEYWORDS or SKIP_HEAD.search(head):
            continue
        if re.search(r'\b(?:virtual|operator)\b', text[max(0, m.start() - 80):m.start()].split('\n')[-1]):
            continue
        before = text[max(0, m.start() - 400):m.start()]
        if re.search(r'//[ \t]*@fnv[ \t]+0x[0-9a-fA-F]+[^\n]*\n(?:[ \t]*//[^\n]*\n)*[ \t]*$', before):
            continue
        if spans is None:
            spans = prepare.scope_spans(text)
        chain = prepare.scope_chain(spans, m.start())
        if not chain or chain[-1][0] != 'class' or any(k not in ('namespace', 'class') for k, _ in chain):
            continue
        scope = [_scope_name(k, h) for k, h in reversed(chain) if h.split() and h.split()[-1] != 'namespace']
        if not scope or None in scope or name == '~' + scope[0] or any('<' in s for s in scope):
            continue
        template = TEMPLATE_HEAD.match(chain[-1][1].strip()) is not None
        if template and (len(scope) != 1 or not NOINLINE.search(head)):
            continue
        if any(TEMPLATE_HEAD.match(h.strip()) for _, h in chain[:-1]):
            continue
        if name == scope[0]:   # a constructor: one without an initializer list (INCLASS_DEF ends at `)`)
            prefix = '??0' + ('?$' + scope[0] + '@' if template else '@'.join(scope) + '@@')
        else:
            prefix = '?' + name + '@' + ('?$' + scope[0] + '@' if template else '@'.join(scope) + '@@')
        bound = [int(a, 16) for k, a in syms.items() if k.startswith(prefix)]
        if not bound or not all(a in elsewhere and a not in tagged for a in bound):
            continue
        if template and any(n.startswith(prefix) for n in own_names):
            continue
        o = m.end() - 1
        c = prepare.match_brace(text, o)
        decl = text[m.start():o].rstrip()
        decl = re.sub(r'\b(?:inline|__inline|__forceinline)\s+', '', decl)
        nl = '\n' * (text.count('\n', m.start(), c) - decl.count('\n'))   # line numbers stay
        edits.append((m.start(), c, decl + '; /* fnv-nx STAND5: bound to the address MSVC called */' + nl))
    edits += _stl_copy_edits(path, text, syms, tagged, own_names, spans or prepare.scope_spans(text))
    if not edits:
        return text
    out, pos = [], 0
    for a, b, r in sorted(edits):
        if a < pos:
            continue
        out.append(text[pos:a]); out.append(r); pos = b
        stats['STAND5'] += 1
    out.append(text[pos:])
    return ''.join(out)


# STAND5 for the decomp's unit copies of the MSVC STL (`namespace fnv_<a> { namespace std { ... } }`)
UNIT_NS_HEAD = re.compile(r'^fnv_[0-9a-f]{8}$')
STL_NS = ('std', 'stdext')
INCLASS_HEAD = re.compile(r'^([ \t]*)((?:(?:__declspec\s*\(\s*\w+\s*\)|[\w:<>,\*&]+)[ \t\*&]+)*?)([A-Za-z_]\w*)\s*\(',
                          re.M)


def _close_paren(text, i):
    """Index of the ')' matching text[i] == '(', or -1."""
    d = 0
    for j in range(i, min(len(text), i + 4000)):
        c = text[j]
        if c == '(':
            d += 1
        elif c == ')':
            d -= 1
            if d == 0:
                return j
        elif c in ';{}' and d:
            return -1
    return -1


def _stl_copy_edits(path, text, syms, tagged, own_names, spans):
    """STAND5's edits for members of the decomp's unit-local copies of MSVC STL classes, where its general gates are
    narrower: a member of a class template or of a class nested in one (`basic_ofstream<C, T>::basic_ofstream`,
    `basic_ostream<C, T>::sentry::sentry`, `basic_filebuf<C, T>::close`), and a constructor with an initializer list.
    The unit's symbols name such a member under the copy's MSVC name (`??0?$basic_ofstream@...@std@fnv_00ac9790@@...`)
    only because MSVC called it out of line there: the body is the library's (VC9 STL, instantiated in the exe once),
    and GCC inlining the decomp's partial model of it references members the model declares and never defines
    (`basic_filebuf::open`, `_Init`, `_Endwrite`, `basic_streambuf::_Lock`: the bodycause `crt-import` class). Gates:
    the class chain is inside `namespace fnv_<a> { namespace std` (or stdext); untagged; not virtual, a destructor or
    an operator; every symbol of the member's MSVC name (any instantiation) binds an address some unit's `functions`
    define, none tagged here, and this unit defines none of those names itself."""
    if not syms:
        return []
    every = s1_compile._all_unit_functions(path)
    edits = []
    for m in INCLASS_HEAD.finditer(text):
        name, head = m.group(3), m.group(2) or ''
        if name in s1_compile.NOT_FUNCTIONS or name in s1_compile.CXX_KEYWORDS or SKIP_HEAD.search(head):
            continue
        if re.search(r'\b(?:virtual|operator)\b', text[max(0, m.start() - 80):m.start()].split('\n')[-1]):
            continue
        close = _close_paren(text, m.end() - 1)
        if close < 0:
            continue
        rest = re.match(r'\s*((?:const\s*)?)(\{|:)', text[close + 1:close + 400])
        if not rest:
            continue
        o = close + 1 + rest.end() - 1
        if rest.group(2) == ':':   # an initializer list: the body opens at the first `{` outside parentheses
            d, o = 0, -1
            for j in range(close + 1 + rest.end(), min(len(text), close + 4000)):
                c = text[j]
                if c == '(':
                    d += 1
                elif c == ')':
                    d -= 1
                elif c == ';' and not d:
                    break
                elif c == '{' and not d:
                    o = j
                    break
            if o < 0:
                continue
        before = text[max(0, m.start() - 400):m.start()]
        if re.search(r'//[ \t]*@fnv[ \t]+0x[0-9a-fA-F]+[^\n]*\n(?:[ \t]*//[^\n]*\n)*[ \t]*$', before):
            continue
        chain = prepare.scope_chain(spans, m.start())
        if not chain or chain[-1][0] != 'class' or any(k not in ('namespace', 'class') for k, _ in chain):
            continue
        names = [_scope_name(k, h) for k, h in chain]
        if None in names:
            continue
        nss = [n for (k, _), n in zip(chain, names) if k == 'namespace']
        if not any(UNIT_NS_HEAD.match(a) and b in STL_NS for a, b in zip(nss, nss[1:])):
            continue
        templ = [k == 'class' and TEMPLATE_HEAD.match(h.strip()) is not None for k, h in chain]
        ctor = name == names[-1]
        if not (any(templ) or (ctor and rest.group(2) == ':')):
            continue   # the general STAND5 above handles the rest
        comps = ''.join((r'\?\$' + re.escape(n) + r'@.+?@') if t else re.escape(n) + '@'
                        for n, t in reversed(list(zip(names, templ))))
        rx = re.compile('^' + (r'\?\?0' if ctor else r'\?' + re.escape(name) + '@') + comps + '@')
        bound = [int(a, 16) for k, a in syms.items() if rx.match(k)]
        if not bound or not all(a in every and a not in tagged for a in bound):
            continue
        if any(rx.match(n) for n in own_names):
            continue
        c = prepare.match_brace(text, o)
        decl = (text[m.start():close + 1] + (' const' if rest.group(1).strip() else '')).rstrip()
        decl = re.sub(r'\b(?:inline|__inline|__forceinline)\s+', '', decl)
        nl = '\n' * (text.count('\n', m.start(), c) - decl.count('\n'))   # line numbers stay
        edits.append((m.start(), c, decl + '; /* fnv-nx STAND5: bound to the address MSVC called */' + nl))
    return edits


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
        root, uj = prepare._units_json(path)
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
        root, uj = prepare._units_json(path)
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
                    if m.group(2) == ';':
                        decl[m.group(1)] += 1
                    else:
                        defn.add(m.group(1))
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
        name, head = m.group(4), m.group(3)
        if masked[m.start(4):m.end(4)] != name or masked[m.end() - 1] != '{':
            continue                          # in a comment or a literal
        if name in s1_compile.NOT_FUNCTIONS or name in s1_compile.CXX_KEYWORDS:
            continue
        if spans is None:
            spans = prepare.scope_spans(text)
        chain = prepare.scope_chain(spans, m.start(4))
        if any(k not in ('namespace', 'extern') for k, _ in chain):
            continue                          # members, local classes, bodies
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
        keys = {(_params_key(m.group(5)), ch) for m, ch in ds}
        protos, uses, ok = [], [], True
        first = min(m.start(4) for m, _ in ds)
        for o in re.finditer(r'\b%s\b' % re.escape(name), masked):
            if any(m.start(4) == o.start() for m, _ in ds):
                continue
            line0 = masked.rfind('\n', 0, o.start()) + 1
            p = FREE_PROTO.match(text, line0)
            if p and masked[p.end() - 1] != ';':
                p = None
            chain = prepare.scope_chain(spans, o.start())
            if (p and p.start(4) == o.start() and all(k in ('namespace', 'extern') for k, _ in chain)
                    and not re.search(r'\b(?:return|else|case|goto|new|delete|throw)\b', p.group(3))):
                if ((_params_key(p.group(5)), tuple(h for _, h in chain)) not in keys or re.search(
                        r'\b(?:friend|virtual|inline|__inline|__forceinline|template|typedef|FNVNX_KEEP)\b',
                        p.group(3))):
                    ok = False                # a prototype of an overload this file does not define here
                    break
                first = min(first, o.start())
                if not re.search(r'\bstatic\b', p.group(3)):
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


# ---------------------------------------------------------------------------------------------------------- STAND6
#
# STAND2 for a stand-in marked `__declspec(noinline)`, in any unit. STAND2 leaves units compiled with /O1 /O2 /Ox
# /Ob2 alone because MSVC may have inlined the stand-in there, so the exe may run its body. A noinline definition
# MSVC never inlined: its callers called the name, and the matcher bound that name to the address another unit
# decompiles (units.json `symbols`). blk_00c60000_od_o2_nood_variant_rtm_variant.cpp (/O2) defines
# `__declspec(noinline) P064Quad* bhkWorldObject::FUN_00560e10()` untagged, a probe over `probe_vectors` (declared,
# defined nowhere); the unit binds its MSVC name to 0x00560e10, whose code (a getter through 0x004ae750 and
# 0x00560e50) tesobjectrefr.cpp decompiles. GCC compiled the probe body and the link bound its call there, so the
# guest ran the probe and read a zero placeholder for `probe_vectors`.
#
# Gates: an out-of-class member or free definition at namespace scope whose head has `__declspec(noinline)`;
# untagged; not virtual in the unit (the native vtable stays as it is), not inline / static / a template; every
# spelling of its MSVC name in the unit's symbols binds an address another unit defines and this unit does not. A
# member's definition goes (its in-class declaration stays); a free function keeps a declaration. Line numbers stay.

NOINLINE_DEF = re.compile(r'^([ \t]*)((?:[\w:<>,\*&]+[ \t\*&]+)*?)__declspec\s*\(\s*noinline\s*\)[ \t]+'
                          r'((?:[\w:<>,\*&]+[ \t\*&]+)*?)((?:[A-Za-z_]\w*\s*::\s*)*~?[A-Za-z_]\w*)\s*'
                          r'\(([^;{}()]*(?:\([^;{}()]*\)[^;{}()]*)*)\)\s*(?:const\s*)?\{', re.M)


def r_noinline_stand_ins(path, text, stats):
    """STAND6: an untagged noinline definition bound to an address another unit defines (see above)."""
    if not path.endswith('.cpp') or 'noinline' not in text:
        return text
    syms = s1_compile._unit_symbols(path)
    info = s1_compile._unit_info(path)
    if not syms or info is None:
        return text
    elsewhere = s1_compile._all_unit_functions(path) - info[1]
    tagged = {int(a, 16) for a in s1_compile.FNV_TAG_ADDR.findall(text)}
    masked = s1_compile._mask_code(text)
    spans, edits = None, []
    for m in NOINLINE_DEF.finditer(text):
        if masked[m.end() - 1] != '{':
            continue
        head = (m.group(2) or '') + ' ' + (m.group(3) or '')
        if re.search(r'\b(?:inline|__inline|__forceinline|static|virtual|template|friend|FNVNX_KEEP|return|else)\b',
                     head):
            continue
        q = re.sub(r'\s+', '', m.group(4))
        parts = q.split('::')
        name = parts[-1]
        if name in s1_compile.NOT_FUNCTIONS or name in s1_compile.CXX_KEYWORDS or name.startswith('~'):
            continue
        if len(parts) >= 2 and name == parts[-2]:
            continue                                      # constructors: STAND2's business
        line0 = text.rfind('\n', 0, m.start(2)) + 1
        before = text[max(0, line0 - 400):line0]
        if re.search(r'//[ \t]*@fnv[ \t]+0x[0-9a-fA-F]+[^\n]*\n(?:[ \t]*//[^\n]*\n)*[ \t]*$', before) or \
                re.search(r'template\s*<[^;{}]*>\s*$', before):
            continue
        if spans is None:
            spans = prepare.scope_spans(text)
        chain = prepare.scope_chain(spans, m.start(4))
        if any(k != 'namespace' for k, _ in chain):
            continue
        ns = [h.split()[-1] for _, h in chain if h.split()[-1] != 'namespace']
        prefix = '?' + '@'.join([name] + list(reversed(parts[:-1])) + list(reversed(ns))) + '@@'
        bound = [int(a, 16) for k, a in syms.items() if k.startswith(prefix)]
        if not bound or not all(a in elsewhere and a not in tagged for a in bound):
            continue
        if re.search(r'\bvirtual\b[^;{}]*[\s\*&]%s\s*\(' % re.escape(name), text):
            continue
        o = m.end() - 1
        c = prepare.match_brace(text, o)
        nl = '\n' * text.count('\n', m.start(), c)
        if len(parts) == 1:
            decl = text[m.start():o].rstrip()
            edits.append((m.start(), c, decl + '; /* fnv-nx STAND6 */' + nl[decl.count('\n'):]))
        else:
            edits.append((m.start(), c, '/* fnv-nx STAND6: %s is the function at the bound address */%s' % (q, nl)))
    if not edits:
        return text
    out, pos = [], 0
    for a, b, r in sorted(edits):
        if a < pos:
            continue
        out.append(text[pos:a]); out.append(r); pos = b
        stats['STAND6'] += 1
    out.append(text[pos:])
    return ''.join(out)


# ---------------------------------------------------------------------------------------------------------- POP2
#
# POP2 - pop-mismatch chains, decided from the units' MSVC names; a POST hook on the prepared tree, so it sees the
# S1b patches and every rule's text (S2-bodies, from EQ's fuzz rows `pop-mismatch,ref-passes-fewer`). The decomp sometimes gives a receiver's stack arguments to the call
# before it: `buffer->GetChangeFlags(2)` used as a bool, where 0x00428110 takes only its result buffer (`ret 4`) and
# the conversion 0x004280f0 is `bool (unsigned int flags) const` (`ret 4`). On x86 the pushes are the same either
# way: the word the getter leaves on the stack is the flags word the conversion pops. Natively the conversion reads a
# register nobody set. S1b's popgen (tests/s1b/pop) writes such fixes as patch files from popscan's x86 analysis;
# it leaves out callers whose declarations live in a header or in more than one class body of the file (the
# `GetChangeFlags` family in 27 files, the decomp's per-function namespaces). POP2 is the same fix as a rule, with no
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
# parameter types, a virtual, static, inline-defined or defaulted declaration, or a class body found 0 or 2+ times
# leave the family alone. The POST hook adds the new decorated reference names to the unit's units.json `symbols`
# (D's name without the moved parameter codes, R's with them), so the link compares and binds them like the
# decomp's own. Line numbers stay. Families left alone are counted (POP2-SKIP-<why>) and listed with their reason
# in <tree>/build/pop2_skipped.tsv, the sites done in <tree>/build/pop2_sites.tsv (derived, local only).

import _msvcsig                    # noqa: E402

POP2_NOTE = ' /* fnv-nx POP2: overloads with the words the callees pop */'
_POP = {}                          # 'popgen' -> S1b's popgen module (loaded once)


def _pop_popgen():
    """S1b's popgen text helpers (chain spellings, class bodies, member declarations)."""
    if 'popgen' not in _POP:
        import importlib.util
        p = os.path.normpath(os.path.join(HERE, '..', '..', 'tests', 's1b', 'pop', 'popgen.py'))
        spec = importlib.util.spec_from_file_location('fnvnx_popgen', p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _POP['popgen'] = mod
    return _POP['popgen']


def _pop_args(s):
    """Top-level arguments of a call's argument text, spelled as in s. popgen's split_args counts `<` / `>` as
    brackets, so the `>` of `->` would close one: it is read as `..` here (same length)."""
    pg = _pop_popgen()
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
    for o, c, k, _ in prepare.scope_spans(text):
        if k == 'function' and o > last:
            out.append((o, c))
            last = c
    return out


def _pop_sites(text, refs, why):
    """The POP2 sites of a unit: [dict(fam, edits=[(start, end, replacement)], decls=[...])]. why: Counter of the
    reasons a candidate chain is left alone."""
    pg = _pop_popgen()
    masked = s1_compile._mask_code(text)
    donors = sorted(n for n, rs in refs.items() if n != 'operator bool' and any((r.delta or 0) > 0 for r in rs))
    if not donors:
        return []
    out = []
    spans = prepare.scope_spans(masked)
    bases = collections.defaultdict(set)
    for hb in re.finditer(r'\b(?:struct|class)\s+([A-Za-z_]\w*)\s*:\s*([^;{}()]*)\{', masked):
        bases[hb.group(1)] |= set(re.findall(r'[A-Za-z_]\w*', hb.group(2))) - {'public', 'private', 'protected',
                                                                                 'virtual'}
    for o, c in _pop_function_bodies(masked):
        seg = masked[o:c]
        # the decomp's per-function namespaces a call here can see (a class of a sibling fnv_<a> namespace cannot be
        # the object's type)
        chain = prepare.scope_chain(spans, o)
        vis = {h.split()[-1] for k, h in chain if k == 'namespace' and h.split()}
        # the class whose member this body defines (`R C::f(..) {` or a body inside `struct C {`): an unqualified call
        # of a member name calls C's member
        hm = re.search(r'([A-Za-z_]\w*)\s*::\s*~?[A-Za-z_]\w*\s*\([^;{}]*\)\s*(?:const\s*)?$',
                       masked[max(0, o - 600):o])
        own = hm.group(1) if hm else (_scope_name(chain[-1][0], chain[-1][1]) if chain and chain[-1][0] == 'class'
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
                site = _pop_site(seg, o, m.start(), p, e, dn, _PopVisible(refs, vis, None if qualified else own),
                                 pg, why, text)
                if site:
                    tags = list(s1_compile.FNV_TAG_ADDR.finditer(text, max(0, o - 2000), o))
                    site['caller'] = tags[-1].group(1).lower().zfill(8) if tags else '-'
                    out.append(site)
    return out


class _PopVisible:
    """A unit's references as seen from one function: those of a class in a per-function namespace (fnv_<a>) count
    only inside that namespace, and there a class of that namespace hides the global class of the same name (the
    decomp declares one `Holder` per namespace)."""

    def __init__(self, refs, vis, own=None):
        self.refs, self.vis, self.own = refs, vis, own

    def donor(self, name):
        """The references an unqualified call of name in a member of class `own` can be (its own class's)."""
        rs = self.get(name)
        return [r for r in rs if r.fn.scopes[0] in self.own] if self.own else rs

    def get(self, name, default=()):
        seen = [r for r in self.refs.get(name, default)
                if all(sc in self.vis for sc in r.fn.scopes if UNIT_NS_HEAD.match(sc))]
        # classes declared in the global namespace or one around the call come before those of other scopes
        near = [r for r in seen if all(sc in self.vis for sc in r.fn.scopes[1:])]
        seen = near or seen
        depth = collections.defaultdict(int)
        for r in seen:
            d = sum(1 for sc in r.fn.scopes if UNIT_NS_HEAD.match(sc))
            depth[r.fn.scopes[0]] = max(depth[r.fn.scopes[0]], d)
        return [r for r in seen if sum(1 for sc in r.fn.scopes if UNIT_NS_HEAD.match(sc)) == depth[r.fn.scopes[0]]]

    def __contains__(self, name):
        return bool(self.get(name))


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
        shape, rn, rp = 'chain', mm.group(1), e + mm.end() - 1
    elif mo:
        explicit = (e, e + mo.end())             # `D(..).operator bool()` spelled out
    elif m2 and pg.reach_receiver(rest, m2.group(1)) and not re.search(r'[)\]]\s*[A-Za-z_]', rest[:m2.start()]):
        # casts and member hops between D's result and R (popgen's reach); an identifier right after a `)` starts
        # another expression (`if (b->D(1)) x->R(..)`)
        shape, rn = 'chain', m2.group(1)
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
        if mn and mn.group(1) in refs and mn.group(1) != dn and explicit is None:
            nest = pg.enclosing_arg(seg.replace('->', '..'), ds, e, mn.group(1))
        if nest is not None:
            shape, rn = 'nested', mn.group(1)
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
                rc += [x for x in refs.get('operator bool', ()) if x.fn.scopes == r.fn.ret_class]
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
        q = os.path.normpath(os.path.join(os.path.dirname(path), m.group(1)))
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
    spans = prepare.scope_spans(text)
    for o, c, kind, head in spans:
        if kind != 'class' or _scope_name(kind, head) != scopes[0]:
            continue
        chain = prepare.scope_chain(spans, o)
        if any(k not in ('namespace', 'class', 'extern') for k, _ in chain):
            continue
        names = [_scope_name(k, h) for k, h in reversed(chain) if k != 'extern' and h.split()[-1] != 'namespace']
        if names == list(scopes[1:]):
            out.append((o, c + 1))
    return out


def _pop_decl_ops(site, files, texts, why):
    """The declarations a site's family needs: [(file, class body, insert position or None when the overload is
    already declared, name, parameter types, declaration)], or None (the reason counted in why)."""
    pg = _pop_popgen()
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


RULES = [r_stand_in_class_bodies, r_harness_internal, r_noinline_stand_ins]
POST = [pop_chains]
