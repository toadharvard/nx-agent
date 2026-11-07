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


RULES = [r_stand_in_class_bodies, r_harness_internal, r_noinline_stand_ins]
