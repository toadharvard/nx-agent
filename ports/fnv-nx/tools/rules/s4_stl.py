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

STL2 - the members of the decomp's in-unit iostream and vector models it declares and never defines.

tesobject_ob1_variant.cpp models VC9's ifstream for its CSV reader (ios_base, basic_ios, basic_istream,
basic_ifstream over the unit's basic_filebuf) and modelloader_ob1_variant.cpp models vector<float> as an explicit
specialization. Each declares members VC9 compiled inline (or that live in msvcp90 as _Init / _Fiopen), so they become
trap stubs (R2's s2d runtime list). STL2 appends, each gated on the declaration below and on no definition in the unit:
  ios_base::setstate(int s)        class ios_base with `int state, exceptions` and `void setstate(int);`:
                                   s != 0: state = (state | s) & 0x17 (VC9 clear(rdstate() | s), _Statmask). The copy
                                   has no exceptions() setter, so exceptions stays 0 and clear never throws
  basic_ios<E, Tr>::init(sb, std)  `void init(B*, bool);` in class template basic_ios: S4's fnvnx_basic_ios_init
                                   (platform/crt/cpplib.cpp: ios_base::_Init with the global locale, then basic_ios::init)
  <F>UseFacet<E>(const L&)          a `template <class E> const C<E>& <F>UseFacet(const L&);` whose struct C holds only
                                   `E widen(char) const;`: a static C<E>, and C<E>::widen(c) = c (ctype<char> of the
                                   classic locale, the only one the game uses)
  basic_ifstream<E, Tr>::open      `FB* open(const char*, int, int);` with a member `FB filebuf;` whose class has
                                   `_Init(struct _iobuf*, _Initfl)` and `_Myfile` (any scope): VC9 basic_filebuf::open: NULL when
                                   already open, else _Fiopen (S4's fnvnx_fiopen), NULL when that fails, else
                                   _Init(file, _Openfl) and &filebuf (char's codecvt is always_noconv: _Pcvt = 0)
  vector<T, allocator<T> >()/~     `template <> class vector<T, allocator<T> >` declaring `vector();` and `~vector();`
                                   with fields `T* first; T* last; T* end;`: VC9's empty vector (every field 0; with
                                   _SECURE_SCL's aux pointer 0 the unit's iterators compare equal owners); the destructor
                                   frees first (operator delete, as VC9's allocator<T>::deallocate)

Both rules leave a name to a recon (REC1, FNVNX_RECON) that covers it for the unit: its symbols, append or replace
text name it (RECON_KEYS). R2's recons are matched against the exe; STL only stands in where there is none, and the
two together would define a name twice. Counted as STL-RECON.
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


_RECON_CACHE = {}


def _recon_text(path):
    """the text of every recon FNVNX_RECON holds for this unit (REC1, tools/rules/s1_compile.py): its append file,
    its replace `new` texts and its symbol names. A STL body whose name occurs there is left to the recon: R2's recons
    are matched against the exe (MSVC all bytes equal, bound to the exe's function), STL's bodies only stand in where
    no recon exists, and both together would define the name twice (REC1-DUP-DEF would then drop the recon). A stale
    recon counts too (the trap then stays visible until the recon is refreshed, never a duplicate)."""
    import json
    import os
    top = os.environ.get('FNVNX_RECON')
    if not top or not os.path.isdir(top):
        return ''
    if top not in _RECON_CACHE:
        got = []
        for dp, _ds, ns in os.walk(top):
            if 'recon.json' not in ns:
                continue
            try:
                r = json.load(open(os.path.join(dp, 'recon.json')))
            except (OSError, ValueError):
                continue
            if not isinstance(r, dict) or r.get('format') != 'fnvnx-recon/1' or not r.get('unit'):
                continue
            parts = list((r.get('symbols') or {}).keys())
            for e in r.get('replace') or []:
                if isinstance(e, dict) and isinstance(e.get('new'), str):
                    parts.append(e['new'])
            if isinstance(r.get('append'), str):
                try:
                    parts.append(open(os.path.join(dp, r['append']), errors='replace').read())
                except OSError:
                    pass
            got.append((r['unit'], '\n'.join(parts)))
        _RECON_CACHE[top] = got
    p = path.replace(os.sep, '/')
    return '\n'.join(x for unit, x in _RECON_CACHE[top] if p == unit or p.endswith('/' + unit))


RECON_KEYS = {   # what in a recon's text means it covers the STL body of that key (GCC or MSVC spellings)
    '_Allocate': r'_Allocate', '_Destroy': r'_Destroy', 'fill': r'\bfill\s*[<(]|St4fill|\?\?\$fill@',
    'swap': r'\bswap\s*[<(]|St4swap|\?\?\$swap@', '_Xlen': r'_Xlen', 'setstate': r'setstate',
    'init': r'\binit\s*\(|4initE|\?init@', 'facet': r'UseFacet|widen',
    'open': r'\bopen\s*\(|4openE|\?open@',
    'vector()': r'\bvector\s*\(\s*\)\s*[:{]|vectorI\w*EC[12]Ev|\?\?0\?\$vector@',
    '~vector()': r'~vector\s*\(|vectorI\w*ED[012]Ev|\?\?1\?\$vector@',
}


def _covered(rtext, key, stats):
    if rtext and re.search(RECON_KEYS[key], rtext):
        stats['STL-RECON'] += 1
        return True
    return False


def _code_depths(text):
    """brace depth before each `namespace std {` match start, skipping comments and string / char literals"""
    starts = {m.start(): m for m in re.finditer(r'(?m)^namespace\s+std\s*\{', text)}
    out, depth, i, n = {}, 0, 0, len(text)
    while i < n:
        if i in starts:
            out[i] = depth
        c = text[i]
        if c == '/' and text.startswith('//', i):
            j = text.find('\n', i)
            i = n if j < 0 else j
            continue
        if c == '/' and text.startswith('/*', i):
            j = text.find('*/', i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in '"\'':
            j = i + 1
            while j < n and text[j] != c and text[j] != '\n':
                j += 2 if text[j] == '\\' else 1
            i = j + 1
            continue
        depth += c == '{'
        depth -= c == '}'
        i += 1
    return starts, out


def _std_blocks(text):
    """spans of the top-level `namespace std {` blocks (brace depth 0: not the decomp's `namespace fnv_<a> {
    namespace std {` copies, whose members are another class's)"""
    starts, depths = _code_depths(text)
    out = []
    for s, m in sorted(starts.items()):
        if depths.get(s) != 0:
            continue
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
    adds, rt = [], _recon_text(path)
    for key, head, body in DECLS:
        if any(re.search(head, text[a:b]) for a, b in blocks) and not re.search(DEFINED[key], text) and \
                not _covered(rt, key, stats):
            adds.append(body)
            stats['STL1'] += 1
    if _vector_declares_xlen(text, blocks) and not XLEN_DEFINED.search(text) and not _covered(rt, '_Xlen', stats):
        adds.append(XLEN_BODY)
        stats['STL1'] += 1
    if not adds:
        return text
    return text + ('' if text.endswith('\n') else '\n') + \
        '/* fnv-nx STL1 (tools/rules/s4_stl.py): VC9 helper bodies for the declarations above */\nnamespace std {\n' + \
        ''.join(adds) + '}\n'


def _class_body(text, blocks, head):
    """(head match, body text) of the first class matching `head` (ending in `{`) inside a top-level std block"""
    rx = re.compile(head, re.S)
    for a, b in blocks:
        m = rx.search(text, a, b)
        if not m:
            continue
        depth, i = 1, m.end()
        while i < b and depth:
            depth += text[i] == '{'
            depth -= text[i] == '}'
            i += 1
        return m, text[m.end():i]
    return None, ''


STL2_DECL = 'extern "C" void fnvnx_basic_ios_init(void*, void*, int);\nextern "C" void* fnvnx_fiopen(const char*, int, int);\n'


def r_stl_stream_bodies(path, text, stats):
    """STL2: the in-unit iostream / vector<T> models' declared members get VC9's bodies."""
    blocks = _std_blocks(text)
    if not blocks:
        return text
    adds, rt = [], _recon_text(path)
    m, body = _class_body(text, blocks, r'\bclass\s+ios_base\s*\{')
    if m and re.search(r'\bint\s+state\s*,\s*exceptions\b', body) and \
            re.search(r'\bvoid\s+setstate\s*\(\s*int\s*\)\s*;', body) and \
            not re.search(r'ios_base\s*::\s*setstate\s*\(', text) and not _covered(rt, 'setstate', stats):
        adds.append('void ios_base::setstate(int _S)\n{\n    if (_S != 0)\n        state = (state | _S) & 0x17;\n}\n')
    m, body = _class_body(text, blocks, r'template\s*<\s*class\s+\w+\s*,\s*class\s+\w+\s*>\s*class\s+basic_ios\s*:[^{;]*\{')
    mi = re.search(r'\bvoid\s+init\s*\(\s*(\w+)\s*\*\s*,\s*bool\s*\)\s*;', body) if m else None
    if mi and not re.search(r'basic_ios\s*<[^>]*>\s*::\s*init\s*\(', text) and not _covered(rt, 'init', stats):
        adds.append('template <class _E, class _Tr> void basic_ios<_E, _Tr>::init(%s* _Sb, bool _Isstd)\n'
                    '{\n    fnvnx_basic_ios_init(this, _Sb, _Isstd);\n}\n' % mi.group(1))
    for a, b in blocks:
        for mf in re.finditer(r'template\s*<\s*class\s+(\w+)\s*>\s*const\s+(\w+)\s*<\s*\1\s*>\s*&\s*(\w*UseFacet)'
                              r'\s*\(\s*const\s+(\w+)\s*&\s*\)\s*;', text[a:b]):
            fac, fn, loc = mf.group(2), mf.group(3), mf.group(4)
            ms = re.search(r'template\s*<\s*class\s+(\w+)\s*>\s*struct\s+%s\s*\{\s*\1\s+widen\s*\(\s*char\s*\)\s*const\s*;'
                           r'\s*\}\s*;' % fac, text[a:b])
            if ms and not re.search(r'\b%s\s*\([^;)]*\)\s*\{' % fn, text) and not _covered(rt, 'facet', stats):
                adds.append('template <class _E> const %s<_E>& %s(const %s&)\n{\n    static const %s<_E> _Facet = %s<_E>();\n'
                            '    return _Facet;\n}\n'
                            'template <class _E> _E %s<_E>::widen(char _C) const\n{\n    return (_E)_C;\n}\n'
                            % (fac, fn, loc, fac, fac, fac))
    m, body = _class_body(text, blocks, r'template\s*<\s*class\s+\w+\s*,\s*class\s+\w+\s*>\s*class\s+basic_ifstream\s*:[^{;]*\{')
    mo = re.search(r'\b(\w+)\s*\*\s*open\s*\(\s*const\s+char\s*\*\s*,\s*int\s*,\s*int\s*\)\s*;', body) if m else None
    if mo and re.search(r'\b%s\s+filebuf\s*;' % mo.group(1), body) and \
            not re.search(r'basic_ifstream\s*<[^>]*>\s*::\s*open\s*\(', text) and not _covered(rt, 'open', stats):
        fb = mo.group(1)
        mfb, fbody = _class_body(text, [(0, len(text))], r'\bclass\s+%s\s*:[^{;]*\{' % fb)   # any scope
        if mfb and re.search(r'void\s+_Init\s*\(\s*struct\s+_iobuf\s*\*', fbody) and \
                re.search(r'\b_Openfl\b', fbody) and re.search(r'struct\s+_iobuf\s*\*\s*_Myfile\s*;', fbody):
            adds.append('template <class _E, class _Tr> %s* basic_ifstream<_E, _Tr>::open(const char* _Name, int _Mode, int _Prot)\n'
                        '{\n    if (filebuf._Myfile != 0)\n        return 0;\n'
                        '    struct _iobuf* _File = (struct _iobuf*)fnvnx_fiopen(_Name, _Mode, _Prot);\n'
                        '    if (_File == 0)\n        return 0;\n'
                        '    filebuf._Init(_File, %s::_Openfl);\n    return &filebuf;\n}\n' % (fb, fb))
    for a, b in blocks:
        for mv in re.finditer(r'template\s*<\s*>\s*class\s+vector\s*<\s*(\w+)\s*,\s*allocator\s*<\s*\1\s*>\s*>\s*\{',
                              text[a:b]):
            ty = mv.group(1)
            s = a + mv.end()
            depth, i = 1, s
            while i < b and depth:
                depth += text[i] == '{'
                depth -= text[i] == '}'
                i += 1
            vb = text[s:i]
            names = re.findall(r'^\s*[\w:][\w:\s*]*[\s*](\w+)\s*;\s*$', vb, re.M)
            if not all(re.search(r'\b%s\s*\*\s*%s\s*;' % (ty, f), vb) for f in ('first', 'last', 'end')):
                continue
            if not (re.search(r'^\s*vector\s*\(\s*\)\s*;', vb, re.M) and re.search(r'^\s*~vector\s*\(\s*\)\s*;', vb, re.M)):
                continue
            q = r'vector\s*<\s*%s\s*,\s*allocator\s*<\s*%s\s*>\s*>\s*::\s*' % (ty, ty)
            if not re.search(q + r'vector\s*\(\s*\)\s*[:{]', text) and not _covered(rt, 'vector()', stats):
                adds.append('vector<%s, allocator<%s> >::vector()\n    : %s\n{}\n'
                            % (ty, ty, ', '.join('%s(0)' % n for n in names)))
            if not re.search(q + r'~vector\s*\(\s*\)\s*[:{]', text) and not _covered(rt, '~vector()', stats):
                adds.append('vector<%s, allocator<%s> >::~vector()\n{\n    if (first != 0)\n        ::operator delete(first);\n'
                            '    first = last = end = 0;\n}\n' % (ty, ty))
    if not adds:
        return text
    stats['STL2'] += len(adds)
    return text + ('' if text.endswith('\n') else '\n') + \
        '/* fnv-nx STL2 (tools/rules/s4_stl.py): bodies of the stream / vector members declared above */\n' + STL2_DECL + \
        'namespace std {\n' + ''.join(adds) + '}\n'


RULES = [r_stl_helper_bodies, r_stl_stream_bodies]
