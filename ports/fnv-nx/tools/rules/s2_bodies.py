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

And the converse (`_stl_string_bodies`): `~basic_string()` and `operator=(const basic_string&)` of those copies, declared and
defined nowhere because VC9 wrote both inline, get the bodies the exe's inlined sites show: `_Tidy(true, 0)` and
`assign(r, 0, npos)`.

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
    spans = spans or prepare.scope_spans(text)
    edits += _stl_copy_edits(path, text, syms, tagged, own_names, spans)
    edits += _stl_string_bodies(text, spans)
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


STRING_DTOR = re.compile(r'^([ \t]*)~basic_string\s*\(\s*\)\s*;', re.M)
STRING_ASSIGN = re.compile(r'^([ \t]*)basic_string\s*&\s*operator\s*=\s*\(\s*const\s+basic_string\s*&\s*\)\s*;', re.M)
STRING_TIDY = re.compile(r'\b_Tidy\s*\(\s*bool\b[^()]*\)\s*[;{]')
STRING_ASSIGN3 = re.compile(r'\bassign\s*\(\s*const\s+basic_string\s*&\s*,\s*unsigned(?:\s+int)?\s*,\s*unsigned(?:\s+int)?\s*\)\s*;')
STRING_NPOS = re.compile(r'\bstatic\s+const\s+unsigned(?:\s+int)?\s+npos\s*;')


def _stl_string_bodies(text, spans):
    """Members of the decomp's unit copies of `basic_string` (`namespace fnv_<a> { namespace std { template <..> class
    basic_string`) that the model declares and nothing defines: VC9 wrote both inline, so MSVC never called them out of
    line and the unit's symbols hold no address for them (crt-import: `~basic_string`, `operator=` of fnv_00ac9790,
    fnv_00ad60c0, fnv_00ad61e0). Their bodies are what the exe's inlined copies do, read from its call sites:
    - `~basic_string() { _Tidy(true, 0); }`: the destructor sites push 0, push 1 and call `_Tidy` (0x0044a730, bound by
      the unit) - `6a 00 6a 01 lea ecx, [..]; call 0x0044a730`, 21 + 20 + 18 + ... sites in the exe;
    - `operator=(const basic_string& r) { return assign(r, 0, npos); }`: std::_Copy_opt's element copy (0x00ad5a80)
      pushes `[0x010173f0]` (npos), 0 and the source, then calls `assign(const string&, size_t, size_t)` (0x0044a580).
    Gates: the class declares the member without a body, the helper it calls with the exact signature (and `npos` for
    the assignment), and the unit has no out-of-line definition of the member."""
    edits = []
    for o, c, kind, head in spans:
        if kind != 'class' or _scope_name('class', head) != 'basic_string' or not TEMPLATE_HEAD.match(head.strip()):
            continue
        chain = [(k, h) for k, h in prepare.scope_chain(spans, o)]
        nss = [_scope_name(k, h) for k, h in chain if k == 'namespace']
        if len(chain) != len(nss) or not any(UNIT_NS_HEAD.match(a or '') and b in STL_NS for a, b in zip(nss, nss[1:])):
            continue
        body = text[o:c]
        for rx, need, repl, out_of_class in (
                (STRING_DTOR, (STRING_TIDY,), '~basic_string() { _Tidy(true, 0); }', r'basic_string\s*(?:<[^;{]*?>)?\s*::\s*~'),
                (STRING_ASSIGN, (STRING_ASSIGN3,), 'basic_string& operator=(const basic_string& _Right) '
                 '{ return assign(_Right, 0, %s); }', r'basic_string\s*(?:<[^;{]*?>)?\s*::\s*operator\s*=')):
            m = rx.search(body)
            if not m or not all(n.search(body) for n in need) or re.search(out_of_class, text):
                continue
            a = o + m.start() + len(m.group(1))
            if '%s' in repl:   # npos where the model declares it, else its own spelling of it (assign(rhs, 0, -1))
                repl = repl % ('npos' if STRING_NPOS.search(body) else '-1')
            edits.append((a, o + m.end(), repl + ' /* fnv-nx STAND5: the exe\'s inlined body */'))
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
# parameter types, a virtual, static or defaulted declaration, an inline-defined donor, or a class body found 0 or 2+
# times leave the family alone. An inline-defined RECEIVER is allowed when it is an untagged stand-in (W25: a
# unit-local copy's `__forceinline operator bool() const { return m_flags != 0; }`, fnv_005807a0): the exe's receiver
# is the function the unit's symbols bind that MSVC name to (R's address, its `ret N` popping the moved words, the same
# gates as for a declared-only receiver), and the stand-in's body would compute `flags != 0` on a register nobody set.
# The new overload is declared without a body (and without inline / __forceinline), so the moved call reaches the exe
# function through the new decorated name the link binds; the stand-in stays for the calls POP2 leaves alone (STAND3,
# 5 and 6 skip operators, forceinline and non-/Od units, so none of them touches it; a body with its own `@fnv` tag is a
# decompiled function and stays skipped).
# A call whose receiver is cast to a class written with namespaces (`((N::BGSLoadFormBuffer*)x)->GetChangeFlags(m)`,
# `reinterpret_cast<N::C*>(x)->`, tesobjectrefr.cpp 0x00562660) names that class: its donor and its result class's
# `operator bool` are looked up in exactly that class, where the name-hiding rule above would let a global class with
# a member of the same name (BGSSaveFormBuffer::GetChangeFlags) hide the namespace's. The POST hook adds the new decorated reference names to the unit's units.json `symbols`
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
                vw = _PopVisible(refs, vis, None if qualified else own)
                vw.cast = _pop_cast(before) if qualified else None
                site = _pop_site(seg, o, m.start(), p, e, dn, vw, pg, why, text)
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
        self.refs, self.vis, self.own, self.cast = refs, vis, own, None

    def named(self, name, scopes):
        """The references of name in exactly the class MSVC's scopes name (innermost first), as a per-function
        namespace the call sees allows."""
        return [r for r in self.refs.get(name, ()) if r.fn.scopes == scopes
                and all(sc in self.vis for sc in scopes if UNIT_NS_HEAD.match(sc))]

    def donor(self, name):
        """The references an unqualified call of name in a member of class `own` can be (its own class's); for a
        receiver cast to a written class (`((N::C*)x)->D(..)`) those of exactly that class."""
        if self.cast:
            cls, quals = self.cast
            return [r for r in self.refs.get(name, ()) if r.fn.scopes[0] == cls
                    and r.fn.scopes[1:1 + len(quals)] == quals
                    and all(sc in self.vis for sc in r.fn.scopes[1 + len(quals):] if UNIT_NS_HEAD.match(sc))
                    and all(UNIT_NS_HEAD.match(sc) for sc in r.fn.scopes[1 + len(quals):])
                    and all(sc in self.vis for sc in r.fn.scopes if UNIT_NS_HEAD.match(sc))]
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
            q = [x.strip() for x in m.group(1).split('::') if x.strip()]
            return m.group(2), q[::-1]
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


_POP_INLINE_RET = re.compile(r'\b(?:__forceinline|FNV_FORCEINLINE|__inline|inline)\b')


def _pop_plain_ret(ret):
    """A stand-in's return type as a declaration-only overload spells it: no comments, no inline specifier."""
    ret = re.sub(r'//[^\n]*|/\*.*?\*/', ' ', ret, flags=re.S)
    return re.sub(r'\s+', ' ', _POP_INLINE_RET.sub('', ret)).strip()


def _pop_inline_stand_in(t, d):
    """True when the receiver declaration d (popgen member_decls row of the class text t) is an untagged in-class body
    and nothing else is wrong with it: a unit-local copy's stand-in for the exe function the unit's symbols bind the
    receiver's MSVC name to (`__forceinline operator bool() const { return m_flags != 0; }`). The overload POP2
    adds is declared without a body, so the call reaches the exe function through the new name; the stand-in stays
    for the calls POP2 leaves alone. A body with its own `// @fnv` tag is a decompiled function and stays skipped."""
    if set(d['bad']) - {'inline body', 'no ;'} or 'inline body' not in d['bad']:
        return False
    return '@fnv' not in d['ret']          # the member's head text holds the comment lines above it


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


# ADDRFN1 - a code address the decomp writes as an integer (`readHandler = reinterpret_cast<void*>(0x00aff9d0);`,
# BSFile::SetEndianSwap and the archive's handler pairs): natively the raw exe address is stored and later called -
# the stub boot's first WinMain-path fault (link d1ed15e7: the BSA archive constructor 0x00af7eb0 calls 0x00aff9d0).
# When some unit defines the address (units.json `functions`), the cast takes the address symbol instead, which the
# link binds to the native definition: a GCC block-scope asm label in a statement expression, so line numbers stay
# and nothing at file scope changes. Only .text addresses (0x00401000 .. 0x00fdf000) cast to `void*`.
ADDRFN_CAST = re.compile(r'reinterpret_cast<\s*void\s*\*\s*>\(\s*0x(00[0-9a-fA-F]{6})\s*\)')


def r_code_address_casts(path, text, stats):
    """ADDRFN1: `reinterpret_cast<void*>(0x00<a>)` of a defined code address -> the address symbol __fnv_<a>."""
    if 'reinterpret_cast' not in text or not path.endswith(('.cpp', '.h', '.inl')):
        return text
    defined = s1_compile._all_unit_functions(path)

    def sub(m):
        a = int(m.group(1), 16)
        if not (0x00401000 <= a < 0x00fdf000) or a not in defined:
            return m.group(0)
        stats['ADDRFN1'] += 1
        sym = '__fnv_%08x' % a
        return ('({ extern char fnvnx_addrfn_%08x[] __asm__("%s"); (void*)fnvnx_addrfn_%08x; })'
                ' /* fnv-nx ADDRFN1 */' % (a, sym, a))
    return ADDRFN_CAST.sub(sub, text)


RULES = [r_stand_in_class_bodies, r_harness_internal, r_noinline_stand_ins, r_code_address_casts]
POST = [pop_chains]

# ---------------------------------------------------------------------------------------------------------- THIS2
#
# THIS2 - a caller that hands its own ecx to a thiscall callee (S2-bodies, from EQ's fuzz rows `this-not-passed`).
# The decomp declares the callee a free `__stdcall` function and the caller either a free `__stdcall` function too
# (the Gamebryo GetViewerStrings overrides: `void __stdcall FUN_00c2b1b0(StrArr* pkStrings) { FUN_00c32f60(pkStrings);
# ...`, a vtable slot that calls the base class's version first) or a member function. MSVC pushes the same words
# for both conventions and the callee pops the same, so the x86 code is byte-identical; the callee reads `this` from
# ecx, which still holds the caller's incoming ecx: the object. Natively the caller passes nothing in x0 for it.
#
# THIS2 makes the call a member call on the caller's object (S1b's XINC1 shapes A-C, decided here without patch
# text):
#   * free stdcall caller -> member of a shell `struct FUN_<caller>_this2 { <caller>; <callee>; };` declared on the
#     definition's first line; the body's unqualified call then names the shell's member (`this` passed); the
#     definition's units.json symbol becomes the member's (thiscall pops the same words);
#   * member caller -> the call becomes `((FUN_<caller>_this2*)this)->callee(..)`.
# The callee's new decorated reference name is added to the unit's units.json `symbols`, so the link compares it with
# the definition (bridges for parameter types the decomp spelled differently, e.g. 0x00a57050's floats as ints).
#
# Gates: the site is in THIS2_SITES (x86 evidence below: the exe's caller code, read with capstone, writes no ecx and
# calls nothing else between its entry and the call or tail jump to the callee); the unit's reference to the callee
# is a free stdcall function whose definition is a non-static thiscall member popping the same stack words; the
# body calls the callee once, unqualified; a free caller is `__stdcall` (a cdecl caller would change who pops); the
# decorated names carry no name back-reference the inserted shell would shift. Line numbers stay.

# caller -> callee. Evidence (FalloutNV.exe 1.4.0.525): every caller below starts `push esi; mov esi, [esp+8];
# push edi; push esi; call <callee>` (the GetViewerStrings overrides: 0x00c2b1b0, 0x00c2b5d0, 0x00c2b920, 0x00bc31f0,
# 0x00a44f40 ...), is `jmp <callee>` (0x00a6e540), or keeps ecx in esi before the call (0x00a497b0: `mov esi, ecx`).
# The list is the `kept` rows of a scan of the this-not-passed rows with a free stdcall reference at link
# 0540aff0d154 (36 of 79 calls; the others write ecx or call something first, and stay listed).
THIS2_SITES = {
    0x00a42f10: 0x00a576c0, 0x00a56b60: 0x00a576c0, 0x00a4d5a0: 0x00a57d10, 0x00a4d100: 0x00a576c0,
    0x00a497b0: 0x00a57050, 0x00a46ac0: 0x00a576c0, 0x00a44f40: 0x00a57800, 0x00a6e540: 0x00a84c70,
    0x00a99270: 0x00a9a730, 0x00c31210: 0x00c255b0, 0x00c30880: 0x00c32f60, 0x00c30740: 0x00c32f60,
    0x00c2f490: 0x00c32f60, 0x00c2b920: 0x00c32f60, 0x00c2b820: 0x00c32f60, 0x00c2b6d0: 0x00c32f60,
    0x00c2b5d0: 0x00c32f60, 0x00c2b4d0: 0x00c32f60, 0x00c2b3d0: 0x00c32f60, 0x00c2b2e0: 0x00c32f60,
    0x00c2b1b0: 0x00c32f60, 0x00c2b0c0: 0x00c32f60, 0x00c2a260: 0x00c32f60, 0x00c29f00: 0x00c32f60,
    0x00e14b30: 0x00e230f0, 0x00bc31f0: 0x00a576c0, 0x00bc3450: 0x00a576c0, 0x00bc3700: 0x00a576c0,
    0x00e5c5b0: 0x00e5c540, 0x00e5c5d0: 0x00e5c540, 0x00e5c5f0: 0x00e5c540, 0x00e5c610: 0x00e5c540,
    0x00e5c630: 0x00e5c540, 0x00e5c650: 0x00e5c540, 0x00e5c670: 0x00e5c540, 0x00e5c690: 0x00e5c540,
    # W12 (EQ fuzz 0540aff0d154, this-not-passed rows missing from the first scan): a re-scan of the exe for every
    # direct call of a callee the link references both as a free `__stdcall` and as a thiscall member, from a caller
    # whose unit makes the free reference, with ecx untouched and no call or branch between the caller's entry and
    # that call, and the caller calling it once (capstone: no instruction before the call writes ecx).
    0x00c2a960: 0x00c32f60, 0x00a3ed90: 0x00aa0ac0, 0x00a40360: 0x00aa0ab0, 0x00a87520: 0x00a71d10,
    0x00b70270: 0x00c9a420, 0x00c8c3d0: 0x00c84ec0, 0x00cb9d80: 0x00a89af0, 0x00cc1610: 0x00a89af0,
    0x00cd2f10: 0x00c84ec0, 0x00e07c50: 0x00e06b20, 0x00e07ca0: 0x00e06b20,
}
THIS2_NOTE = '/* fnv-nx THIS2: the caller passes its ecx */ '


def _this2_member_name(name, fn, shell):
    """The decorated name of free function `name` (parsed fn) as a thiscall member of `shell`, or None. `shell` is
    declared in fn's own scopes (the caller's namespace, checked by _this2_site), so it enters MSVC's name
    back-reference table right after the identifier and every later entry moves up by one: `StrArr@1@` (StrArr in
    namespace fnv_00c2a960, table [FUN_00c32f60, fnv_00c2a960]) becomes `StrArr@2@`. A candidate is accepted only when
    it parses to exactly the original's parameters and result (_msvcsig spells back-references out), so a digit that
    is not a name back-reference can never slip through."""
    if fn is None or fn.member or fn.cc != 'stdcall':
        return None
    head = '?%s@%s@%s@QAE' % (fn.ident, shell, ''.join(x + '@' for x in fn.scopes))
    tail = name[fn.head_end:]
    cands = [tail]
    if fn.name_refs:
        cands.append(re.sub(r'(?<=[@UVT])([1-8])(?=@|[A-Za-z_])', lambda m: str(int(m.group(1)) + 1), tail))
    for t in cands:
        new = head + t
        g = _msvcsig.parse(new)
        if g is not None and g.member and g.cc == 'thiscall' and g.ident == fn.ident and \
                g.scopes == [shell] + list(fn.scopes) and g.params == fn.params and g.ret == fn.ret and \
                getattr(g, 'ret_class', None) == getattr(fn, 'ret_class', None):
            return new
    return None


def _this2_site(text, caller, callee, u, words, why):
    """-> (edits, (old def symbol, new def symbol) or None, [(new reference name, address)]) or None."""
    pg = _pop_popgen()
    shell = 'FUN_%08x_this2' % caller
    dsym = [f['symbol'] for f in u.get('functions') or () if int(f['address'], 16) == caller]
    refs = [(n, _msvcsig.parse(n)) for n, a in (u.get('symbols') or {}).items() if int(a, 16) == callee]
    refs = [(n, f) for n, f in refs if f is not None and not f.member and f.cc == 'stdcall']
    if len(dsym) != 1 or len(refs) != 1:
        why['no single definition / free stdcall reference'] += 1
        return None
    rname, rf = refs[0]
    if words.get(callee) is None or words[callee] != _msvcsig.stack_words(rf):
        why['callee words differ'] += 1
        return None
    df = _msvcsig.parse(dsym[0])
    if df is None:
        why['definition name unparsed'] += 1
        return None
    tags = [m for m in s1_compile.FNV_TAG_ADDR.finditer(text) if int(m.group(1), 16) == caller]
    if len(tags) != 1:
        why['no single tag'] += 1
        return None
    masked = s1_compile._mask_code(text)
    o = masked.find('{', tags[0].end())
    hs = masked.find('\n', tags[0].end()) + 1
    while 0 < hs < o and (not masked[hs:masked.find('\n', hs)].strip() or masked[hs:].lstrip().startswith('#')):
        hs = masked.find('\n', hs) + 1                  # the head's first line: past comments and preprocessor lines
    if not 0 < hs < o:
        why['no definition head'] += 1
        return None
    head = text[hs:o]
    c = prepare.match_brace(masked, o)
    body = masked[o:c]
    calls = list(re.finditer(r'(?<![\w~.>:])%s\s*\(' % re.escape(rf.ident), body))
    if len(calls) != 1:
        why['callee called %d times' % len(calls)] += 1
        return None
    proto = None
    for m in re.finditer(r'(?:^|[;{}])\s*((?:extern\s+)?[^;{}()=]*?)\b(?:__stdcall\s+)%s\s*\(' % re.escape(rf.ident),
                         masked[:hs]):
        p = m.end() - 1
        q = pg.match_paren(masked, p)
        if q is not None and masked[q:q + 40].lstrip().startswith(';'):
            proto = (re.sub(r'\bextern\b', '', text[m.start(1):m.end(1)]).strip(), text[p + 1:q - 1])
    if proto is None or not proto[0]:
        why['no __stdcall prototype of the callee'] += 1
        return None
    cdecl = '%s %s(%s);' % (proto[0], rf.ident, proto[1])
    if list((df.scopes[1:] if df.member else df.scopes) or []) != list(rf.scopes or []):
        why['caller not in the callee reference scope'] += 1   # the shell must share the reference's scopes
        return None
    newref = _this2_member_name(rname, rf, shell)
    if newref is None:
        why['reference name not re-encodable'] += 1
        return None
    edits = []
    if not df.member:
        mh = re.match(r'(\s*)(.*?)\b(?:__stdcall\s+)%s\s*\((.*)\)\s*$' % re.escape(df.ident), head, re.S)
        if mh is None or df.cc != 'stdcall' or re.search(r'\b(?:static|inline|template)\b', mh.group(2)):
            why['caller head not a free __stdcall definition'] += 1
            return None
        newdef = _this2_member_name(dsym[0], df, shell)
        if newdef is None:
            why['definition name not re-encodable'] += 1
            return None
        ret = re.sub(r'\b(?:FNVNX_KEEP|__declspec\s*\(\s*\w+\s*\))\s*', '', mh.group(2)).strip()
        struct = 'struct %s { %s %s(%s); %s }; ' % (shell, ret, df.ident, mh.group(3), cdecl)
        edits.append((hs, o, mh.group(1) + THIS2_NOTE + struct + '%s%s::%s(%s)' % (
            mh.group(2), shell, df.ident, mh.group(3)) + head[len(head.rstrip()):]))
        return edits, (dsym[0], newdef), [(newref, '0x%08x' % callee)]
    if df.static:
        why['static member caller'] += 1
        return None
    edits.append((hs, hs, THIS2_NOTE + 'struct %s { %s }; ' % (shell, cdecl)))
    a = o + calls[0].start()
    edits.append((a, a, '((%s*)this)->' % shell))
    return edits, None, [(newref, '0x%08x' % callee)]


def this_members(out_root, stats, files=None):
    """THIS2: a caller that hands its own ecx to a thiscall callee the decomp declares free: the call becomes a member
    call on the caller's object (see above). A POST hook, like POP2."""
    import json
    p = os.path.join(out_root, 'build', 'units.json')
    if not os.path.exists(p):
        return
    with open(p) as f:
        data = json.load(f)
    words = _pop_def_words(data['units'])
    by_addr = {}
    for u in data['units']:
        for f in u.get('functions') or ():
            by_addr.setdefault(int(f['address'], 16), u)
    edits, why, changed = collections.defaultdict(list), collections.Counter(), False
    for caller, callee in sorted(THIS2_SITES.items()):
        u = by_addr.get(caller)
        fp = os.path.join(out_root, u['source']) if u else None
        if fp is None or not os.path.exists(fp):
            why['caller not in the tree'] += 1
            continue
        with open(fp, encoding='utf-8', errors='surrogateescape') as f:
            text = f.read()
        got = _this2_site(text, caller, callee, u, words, why)
        if got is None:
            continue
        ed, ren, refs = got
        # one file at a time: every site's edits are positions in the file as prepare wrote it
        edits[fp] += ed
        if ren:
            for f in u['functions']:
                if int(f['address'], 16) == caller and f['symbol'] == ren[0]:
                    f['symbol'] = ren[1]
                    stats['THIS2-DEF'] += 1
        for name, a in refs:
            if name not in u['symbols']:
                u['symbols'][name] = a
                stats['THIS2-REF'] += 1
        stats['THIS2'] += 1
        changed = True
    for fp, ed in sorted(edits.items()):
        with open(fp, encoding='utf-8', errors='surrogateescape') as f:
            text = f.read()
        out, at = [], 0
        for a, b, r in sorted(ed, key=lambda x: (x[0], x[1])):
            out.append(text[at:a]); out.append(r); at = b
        out.append(text[at:])
        with open(fp, 'w', encoding='utf-8', errors='surrogateescape') as f:
            f.write(''.join(out))
        if files is not None:
            files['THIS2'] += 1
    if changed:
        with open(p, 'w') as f:
            json.dump(data, f)
    for k, v in sorted(why.items()):
        stats['THIS2-SKIP-' + re.sub(r'\W+', '-', k).strip('-')] += v


POST.append(this_members)
