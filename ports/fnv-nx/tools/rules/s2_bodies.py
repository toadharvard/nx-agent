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
"""
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


SKIP_HEAD = re.compile(r'\b(?:virtual|static|operator|return|else|case|new|delete|throw|typedef|friend)\b')


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
        if re.search(r'\b(?:virtual|static|operator)\b', text[max(0, m.start() - 80):m.start()].split('\n')[-1]):
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


RULES = [r_stand_in_class_bodies]
