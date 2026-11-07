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
not a constructor, destructor or operator; untagged (no `// @fnv` above it); every spelling of its MSVC name in the
unit's symbols binds an address that another unit's `functions` define and this unit does not.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import prepare                     # noqa: E402
import s1_compile                  # noqa: E402  (the units.json readers STAND1/2 use)

INCLASS_DEF = re.compile(r'^([ \t]*)((?:[\w:<>,\*&]+[ \t\*&]+)*?)([A-Za-z_]\w*)\s*'
                         r'\(([^;{}()]*(?:\([^;{}()]*\)[^;{}()]*)*)\)\s*((?:const\s*)?)\{', re.M)
SKIP_HEAD = re.compile(r'\b(?:virtual|static|operator|return|else|case|new|delete|throw|typedef|friend)\b')


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
        scope = [h.split()[-1] for _, h in reversed(chain) if h.split() and h.split()[-1] != 'namespace']
        if not scope or name == scope[0] or name == '~' + scope[0] or any('<' in s for s in scope):
            continue
        prefix = '?' + '@'.join([name] + scope) + '@@'
        bound = [int(a, 16) for k, a in syms.items() if k.startswith(prefix)]
        if not bound or not all(a in elsewhere and a not in tagged for a in bound):
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
