"""Prepare rules of stream S4 (tools/rules/README.md).

STL1 - definitions of the MSVC STL helpers the decomp's in-unit `std` copies declare and never define.

Some decomp units carry a partial copy of VC9's standard library in `namespace std` (SpeedTree's indexed geometry:
vector, _Vector_val, _Aux_cont). They declare VC9's helper templates the way <xmemory>/<xutility> do, but the decomp
copied no bodies, so GCC instantiates nothing and every use becomes an undefined reference, then a trap stub in the
guest (R2's s2d/list/97deacd38aec, `runtime`: std::_Allocate<T>, std::_Destroy<T>, std::fill<I, T>, std::swap<T>,
std::vector<T>::_Xlen). VC9 compiled these inline at every call, so no exe function is theirs.

STL1 appends the bodies with VC9's semantics, written here, not taken from the decomp:
  _Allocate(n, T *)     ::operator new(n * sizeof(T)); n == 0 asks for 0 bytes; an overflowing count throws
                        bad_alloc (VC9: _THROW_NCEE(std::bad_alloc, NULL)), here through the unit's bad_alloc if it
                        declares one, else operator new of the largest size, which fails the same way
  _Destroy(T *p)        p->~T()  (a no-op for scalars)
  fill(first, last, v)  every element of [first, last) = v
  swap(a, b)            T tmp = a; a = b; b = tmp
  vector<T, A>::_Xlen() throws length_error, as VC9's vector does: S4's fnvnx_xlength_error (the MSVC length_error
                        the game's handlers catch)
Gates: the unit has a namespace-scope `namespace std {` block declaring the template with VC9's spelling (the exact
head below); no definition of it anywhere in the unit (a `{` after the same head); `_Xlen` only when the unit's
`class vector` declares `static void _Xlen();` and nothing defines `vector<...>::_Xlen`. The definitions go at the
end of the unit, in `namespace std`, so they are instantiated for the unit's own types at the end of the
translation unit. Line numbers stay (only appended text).
"""
import re

DECLS = [   # (key, declaration head regex, definition)
    ('_Allocate', r'template\s*<\s*class\s+(\w+)\s*>\s*\1\s*\*\s*_Allocate\s*\(\s*size_t\s*,\s*\1\s*\*\s*\)\s*;',
     'template <class _T> _T* _Allocate(size_t _N, _T*)\n'
     '{\n'
     '    if (_N != 0 && ((size_t)-1) / _N < sizeof(_T))\n'
     '        return (_T*)::operator new((size_t)-1);   /* VC9: bad_alloc */\n'
     '    return (_T*)::operator new(_N * sizeof(_T));\n'
     '}\n'),
    ('_Destroy', r'template\s*<\s*class\s+(\w+)\s*>\s*void\s+_Destroy\s*\(\s*\1\s*\*\s*\)\s*;',
     'template <class _T> void _Destroy(_T* _P)\n{\n    _P->~_T();\n}\n'),
    ('fill', r'template\s*<\s*class\s+(\w+)\s*,\s*class\s+(\w+)\s*>\s*void\s+fill\s*\(\s*\1\s*,\s*\1\s*,\s*const\s+\2\s*&\s*\)\s*;',
     'template <class _I, class _T> void fill(_I _F, _I _L, const _T& _V)\n'
     '{\n    for (; _F != _L; ++_F)\n        *_F = _V;\n}\n'),
    ('swap', r'template\s*<\s*class\s+(\w+)\s*>\s*void\s+swap\s*\(\s*\1\s*&\s*,\s*\1\s*&\s*\)\s*;',
     'template <class _T> void swap(_T& _A, _T& _B)\n{\n    _T _Tmp = _A;\n    _A = _B;\n    _B = _Tmp;\n}\n'),
]
DEFINED = {   # a definition of the same template somewhere in the unit
    '_Allocate': r'\b_Allocate\s*\(\s*size_t\s+\w+\s*,[^;{)]*\)\s*\{',
    '_Destroy': r'template\s*<[^>]*>\s*void\s+_Destroy\s*\(\s*\w+\s*\*\s*\w+\s*\)\s*\{',
    'fill': r'template\s*<[^>]*,[^>]*>\s*void\s+fill\s*\(\s*\w+\s+\w+\s*,\s*\w+\s+\w+\s*,[^)]*\)\s*\{',
    'swap': r'template\s*<[^>]*>\s*void\s+swap\s*\(\s*\w+\s*&\s*\w+\s*,\s*\w+\s*&\s*\w+\s*\)\s*\{',
}
XLEN_DECL = re.compile(r'template\s*<\s*class\s+\w+\s*,\s*class\s+\w+\s*>\s*class\s+vector\s*:[^{;]*\{', re.S)
XLEN_DEFINED = re.compile(r'vector\s*<[^>]*>\s*::\s*_Xlen\s*\(\s*\)\s*\{')
XLEN_BODY = ('extern "C" void fnvnx_xlength_error(void) __attribute__((noreturn));\n'
             'template <class _Ty, class _Ax> void vector<_Ty, _Ax>::_Xlen()\n{\n    fnvnx_xlength_error();\n}\n')


def _std_blocks(text):
    """spans of top-level `namespace std {` blocks (brace matched, comments and strings not considered: the
    decomp's STL copies have none that hold braces)"""
    out = []
    for m in re.finditer(r'(?m)^namespace\s+std\s*\{', text):
        depth, i = 1, m.end()
        while i < len(text) and depth:
            c = text[i]
            depth += c == '{'
            depth -= c == '}'
            i += 1
        out.append((m.start(), i))
    return out


def _vector_declares_xlen(text, blocks):
    for a, b in blocks:
        m = XLEN_DECL.search(text, a, b)
        if not m:
            continue
        depth, i = 1, m.end()
        while i < b and depth:
            depth += text[i] == '{'
            depth -= text[i] == '}'
            i += 1
        if re.search(r'\bstatic\s+void\s+_Xlen\s*\(\s*\)\s*;', text[m.end():i]):
            return True
    return False


def r_stl_helper_bodies(path, text, stats):
    """STL1: VC9's helper templates the unit's std copy declares and never defines get their bodies."""
    blocks = _std_blocks(text)
    if not blocks:
        return text
    adds = []
    for key, head, body in DECLS:
        if any(re.search(head, text[a:b]) for a, b in blocks) and not re.search(DEFINED[key], text):
            adds.append(body)
            stats['STL1'] += 1
    if _vector_declares_xlen(text, blocks) and not XLEN_DEFINED.search(text):
        adds.append(XLEN_BODY)
        stats['STL1'] += 1
    if not adds:
        return text
    return text + ('' if text.endswith('\n') else '\n') + \
        '/* fnv-nx STL1 (tools/rules/s4_stl.py): VC9 helper bodies for the declarations above */\nnamespace std {\n' + \
        ''.join(adds) + '}\n'


RULES = [r_stl_helper_bodies]
