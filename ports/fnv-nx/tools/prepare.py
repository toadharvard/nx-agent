#!/usr/bin/env python3
"""prepare.py - derive the port's build tree from a fnv_decomp checkout.

    tools/prepare.py <fnv_decomp checkout> <out tree>

Copies src/ and include/ into <out tree> and applies mechanical, deterministic rewrites (RULES below) that turn
MSVC/x86-only constructs into portable ones. The decomp checkout is never modified, and the output tree stays local:
it is derived from the decomp and must not be committed or shared (see docs/ARCHITECTURE.md, "Legal").

Every rule is a function (path, text) -> text, listed in RULES with a short id that the journal refers to.
Writes <out tree>/prepare_stats.tsv: rule id, files changed, edits.
"""
import os
import re
import shutil
import sys
from collections import Counter

# ---------------------------------------------------------------------------------------------------------- helpers

def _table(name):
    """a decomp-derived table of fnv-nx-build (tools/tables.py: FNVNX_TABLES, sha256 against tools/tables.pin.json)"""
    import importlib.util
    s = importlib.util.spec_from_file_location('fnvnx_tables', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tables.py'))
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m.path(name)


def match_brace(text, open_at):
    """Index just past the '}' matching text[open_at] == '{' (ignores braces in strings/comments, good enough)."""
    depth, i, n = 0, open_at, len(text)
    while i < n:
        c = text[i]
        if c == '"' or c == "'":
            q = c; i += 1
            while i < n and text[i] != q:
                i += 2 if text[i] == '\\' else 1
        elif text.startswith('//', i):
            i = text.find('\n', i)
            if i < 0: return n
        elif text.startswith('/*', i):
            i = text.find('*/', i)
            if i < 0: return n
            i += 1
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n

# ---------------------------------------------------------------------------------------------------------- rules

# `union __declspec(intrin_type) __declspec(align(16)) __m128 {...}`, also spelled as a struct (`{ float x, y, z, w; }`)
INTRIN_UNION = re.compile(r'(?:typedef\s+)?(?:union|struct)\s+(?:__declspec\([^)]*\(?[^)]*\)?\)\s*)*(__m128[id]?|__m64)\b[^;{]*\{')

# (also qualified, `typedef union ::__m128 hkQuadReal;`: SSE4 strips the `::` only later)
INTRIN_ELABORATED = re.compile(r'\b(?:union|struct)\s+(?:::\s*)?(?:\w+\s*::\s*)*(__m128[id]?|__m64)\b(?!\s*\{)(?=\s*[\w\*&;,)])')
INTRIN_ELAB_HINT = re.compile(r'\b(?:union|struct)\s+(?:::\s*)?(?:\w+\s*::\s*)*__m')

# a named struct of four lanes inside a local copy, and the element types it may use
NAMED_LANES = re.compile(r'\bstruct\s*\{([^{}]*)\}\s*(\w+)\s*;')
LANE_DECL = re.compile(r'((?:unsigned\s+|signed\s+)?(?:float|double|int|__int32|unsigned|long))\s+([\w\s,]+?)\s*;')
LANE_ELEM = {'float': 'float', 'int': 'int', '__int32': 'int', 'unsigned int': 'unsigned int', 'unsigned': 'unsigned int',
             'unsigned __int32': 'unsigned int', 'long': 'int'}

ANON_LANES = re.compile(r'\bstruct\s*\{([^{}]*)\}\s*;')
M128_ALIAS = re.compile(r'\btypedef\s+(?:const\s+)?(?:::\s*)?(?:\w+\s*::\s*)*__m128\s+(\w+)\s*;')
NOT_A_TYPE = {'return', 'else', 'case', 'goto', 'delete', 'throw', 'sizeof', 'new', 'typedef', 'using', 'const',
              'volatile', 'static', 'operator'}
ARRAY_DIM = r'(?:\[[^\]]*\]\s*)?'

def _lane_decl(fields):
    """{lane: (element type, index)} of `float x, y, z, w;` (four lanes of one element type), or None."""
    decl = [(ty, f) for ty, fs in LANE_DECL.findall(fields) for f in re.split(r'\s*,\s*', fs.strip())]
    if len(decl) == 4 and all(LANE_ELEM.get(re.sub(r'\s+', ' ', ty)) for ty, _ in decl):
        return {f: (LANE_ELEM[re.sub(r'\s+', ' ', ty)], i) for i, (ty, f) in enumerate(decl)}
    return None

def _sse_direct_lanes(text, fields, stats):
    """SSE1 for lanes that were members of the removed copy: `q.x` -> `((float*)&(q))[0]`, for each name q the unit
    declares (variable, member or parameter) only with __m128 or a typedef of it."""
    types = {'__m128'} | set(M128_ALIAS.findall(text))
    tx = '|'.join(sorted(map(re.escape, types)))
    names = set()
    for lst in re.findall(r'\b(?:%s)\s+(?:const\s+)?((?:[A-Za-z_]\w*\s*%s,\s*)*[A-Za-z_]\w*)\s*%s[;,=)]'
                          % (tx, ARRAY_DIM, ARRAY_DIM), text):    # `__m128 a, b[2];`, a parameter `__m128 a,`
        names |= {re.sub(r'\[.*', '', d).strip() for d in lst.split(',')}
    keep = []
    for n in sorted(names):
        others = {t for t in re.findall(r'\b([A-Za-z_]\w*)[\s\*&]+(?:[A-Za-z_]\w*\s*%s,[\s\*&]*)*%s\s*%s[;,=)]'
                                        % (ARRAY_DIM, re.escape(n), ARRAY_DIM), text) if t not in NOT_A_TYPE}
        if others <= types: keep.append(n)
    if not keep: return text
    rx = re.compile(r'\b(?:%s)(\s*\.\s*(%s))\b(?!\s*\()' % ('|'.join(map(re.escape, keep)),
                                                         '|'.join(map(re.escape, sorted(fields)))))
    out, pos = [], 0
    for m in rx.finditer(text):
        s = operand_start(text, m.start(1))
        if s >= m.start(1) or s < pos: continue
        ty, i = fields[m.group(2)]
        out.append(text[pos:s]); out.append('((%s*)&(%s))[%d]' % (ty, text[s:m.start(1)].strip(), i)); pos = m.end()
        stats['SSE1'] += 1
    out.append(text[pos:])
    return ''.join(out)

def r_sse_local_types(path, text, stats):
    """SSE1: drop local copies of the MSVC <xmmintrin.h> vector unions (or structs); the global sse2neon type is used
    instead. Lanes of the copy (`v.lanes.x`, or `q.x` when x..w were its own members or an anonymous struct's)
    become element access."""
    out, pos, lanes, direct = [], 0, {}, {}
    for m in INTRIN_UNION.finditer(text):
        if m.start() < pos: continue
        end = match_brace(text, m.end() - 1)
        body = text[m.end():end - 1]
        for fields, name in NAMED_LANES.findall(body):     # `struct { float x, y, z, w; } lanes;`
            decl = _lane_decl(fields)
            if decl: lanes[name] = decl
        # lanes that are members of the copy itself: `struct __m128 { float x, y, z, w; }`, or of an anonymous
        # struct in the union (`union __m128 { struct { float x, y, z, w; }; }`)
        for fields in ANON_LANES.findall(body) + ([body] if '{' not in body else []):
            decl = _lane_decl(fields)
            if decl: direct.update(decl)
        tail = re.match(r'\s*(?:__m128[id]?|__m64)?\s*;', text[end:])
        if tail: end += tail.end()
        out.append(text[pos:m.start()]); out.append('/* fnv-nx SSE1: local %s removed */' % m.group(1)); pos = end
        stats['SSE1'] += 1
    out.append(text[pos:])
    text = ''.join(out)
    # members of a named lane struct of the removed copy (`v.lanes.x`, `p->lanes.y`) -> element access as in SSE3,
    # when nothing else in the unit declares a member of that name
    for name, fields in lanes.items():
        if re.search(r'\b%s\s*(?:\[[^\]]*\]\s*)?[;,=]' % re.escape(name), text): continue
        rx = re.compile(r'\s*(\.|->)\s*%s\s*\.\s*(%s)\b' % (re.escape(name), '|'.join(map(re.escape, fields))))
        out, pos = [], 0
        for m in rx.finditer(text):
            s = operand_start(text, m.start())
            if s >= m.start() or s < pos: continue
            ty, i = fields[m.group(2)]
            out.append(text[pos:s])
            out.append('((%s*)%s(%s))[%d]' % (ty, '&' if m.group(1) == '.' else '', text[s:m.start()].strip(), i))
            pos = m.end()
            stats['SSE1'] += 1
        out.append(text[pos:])
        text = ''.join(out)
    # elaborated references to those types (`typedef union __m128 hkQuadReal;`): the global type is a typedef
    if INTRIN_ELAB_HINT.search(text):
        text, n0 = re.subn(r'\b(?:union|struct)\s+(?:::\s*)?(?:\w+\s*::\s*)*(?:__m128[id]?|__m64)\s*;', '', text)   # forward declarations
        text, n = INTRIN_ELABORATED.subn(r'\1', text)
        n += n0
        stats['SSE1'] += n
    if direct: text = _sse_direct_lanes(text, direct, stats)
    # MSVC's union initializer of a __m128 (first member m128_f32[4]): `= {{a, b, c, d}}` -> the vector's `{a, b, c, d}`
    if '{{' in text.replace(' ', '') and '__m128' in text:
        names = ['__m128'] + re.findall(r'\btypedef\s+(?:const\s+)?__m128\s+(\w+)\s*;', text)
        init = re.compile(r'\b((?:%s)\s+\w+\s*=\s*)\{\s*\{([^{}]*)\}\s*\}' % '|'.join(names))
        text, n = init.subn(r'\1{\2}', text)
        stats['SSE1'] += n
    return text

# one prototype; several may share a line (`extern"C"__m128 _mm_load_ss(float const*_A);extern"C"void ...`)
# (the return type may be the decomp's own name for __m128: `hkQuadReal _mm_load_ps(const float*);`)
MM_PROTO = re.compile(r'(?:^|(?<=[;{}]))[ \t]*(?:extern\s*(?:"C"\s*)?)?(?:static\s+)?(?:inline\s+)?(?:const\s+)?(?:::)?(?:\w+::)*'
                      r'(?:__m128[id]?|__m64|float|double|int|unsigned\s+int|void|'
                      r'(?!(?:return|else|case|throw|goto|delete|new|typedef|using|operator|sizeof)\b)[A-Za-z_]\w*)'
                      r'(?:\s+(?:__cdecl\s+)?(?:_mm_\w+|\(\s*_mm_\w+\s*\))|\s*\(\s*_mm_\w+\s*\))'     # also `(_mm_load_ps)(...)`,
                      r'\s*\([^;{)]*\)\s*;[ \t]*', re.M)                                      # `::__m128(_mm_load_ss)(...)`
EXTERN_C_BLOCK = re.compile(r'extern\s+"C"\s*\{')
# a local definition of an intrinsic (`static __m128 _mm_set1_ps(const float& f) {...}`, hkgpmesh): the target's
# intrinsic (sse2neon) is the same operation, and both overloads together are ambiguous
MM_DEF = re.compile(r'(?:^|(?<=[;{}]))[ \t]*(?:(?:static|inline|__inline|__forceinline)\s+)*(?:const\s+)?(?:::)?'
                    r'(?:__m128[id]?|__m64|float|double|int|void)\s+(?:__cdecl\s+)?_mm_\w+\s*\([^;{)]*\)\s*\{', re.M)

def r_sse_local_protos(path, text, stats):
    """SSE2: drop local prototypes of _mm_* intrinsics (and extern "C" blocks left empty by that), and local
    definitions of them at namespace scope (same line count)."""
    n = 1
    while n:        # again: a match takes the blank after its `;`, so a prototype right behind it is matched next time
        text, n = MM_PROTO.subn('', text)
        stats['SSE2'] += n
    if MM_DEF.search(text):
        spans, out, pos = scope_map(text), [], 0
        for m in MM_DEF.finditer(text):
            if m.start() < pos or innermost(spans, m.end() - 1) not in ('file', 'namespace', 'extern'): continue
            end = match_brace(text, m.end() - 1)
            out.append(text[pos:m.start()])
            out.append('/* fnv-nx SSE2: local intrinsic definition removed */' + '\n' * text.count('\n', m.start(), end))
            pos = end
            stats['SSE2'] += 1
        out.append(text[pos:])
        text = ''.join(out)
    out, pos = [], 0
    for m in EXTERN_C_BLOCK.finditer(text):
        if m.start() < pos: continue
        end = match_brace(text, m.end() - 1)
        if text[m.end():end - 1].strip() == '':
            out.append(text[pos:m.start()]); pos = end; stats['SSE2'] += 1
    out.append(text[pos:])
    return ''.join(out)

M128_MEMBER = re.compile(r'\s*(\.|->)\s*(m128[id]?|m64)_(f32|f64|u64|u32|u16|u8|i64|i32|i16|i8)\b')
ELEM = {'f32': 'float', 'f64': 'double', 'u64': 'unsigned long long', 'u32': 'unsigned int', 'u16': 'unsigned short',
        'u8': 'unsigned char', 'i64': 'long long', 'i32': 'int', 'i16': 'short', 'i8': 'signed char'}
CLOSERS = {')': '(', ']': '['}

def operand_start(text, end):
    """Start of the postfix expression that ends at text[end] (exclusive): a name or this, then any chain of
    (...), [...], .name, ->name, ::name; balanced brackets are skipped. Returns end when there is none."""
    i = end
    while True:
        j = i
        while j > 0 and text[j - 1] in ' \t': j -= 1
        if j > 0 and text[j - 1] in CLOSERS:
            depth, k = 0, j - 1
            while k >= 0:
                c = text[k]
                if c in CLOSERS: depth += 1
                elif c in '([':
                    depth -= 1
                    if depth == 0: break
                k -= 1
            if k < 0: return i
            i = k
            if text[i] == '(' and i > 0 and text[i - 1] == '>':     # reinterpret_cast<T*>(...), f<T>(...)
                depth, k = 0, i - 1
                while k >= 0:
                    if text[k] == '>': depth += 1
                    elif text[k] == '<':
                        depth -= 1
                        if depth == 0: break
                    elif text[k] in ';{}': k = -1; break
                    k -= 1
                if k > 0 and (text[k - 1].isalnum() or text[k - 1] == '_'): i = k
            n = i
            while n > 0 and (text[n - 1].isalnum() or text[n - 1] == '_'): n -= 1
            if n < i and not text[n].isdigit(): i = n
            elif text[i] == '(': return i      # parenthesized primary: (expr)
            elif text[i] == '[' and i > 0 and text[i - 1] in CLOSERS: continue   # (&a)[i], f(x)[i], m[i][j]
        elif j > 0 and (text[j - 1].isalnum() or text[j - 1] == '_'):
            n = j
            while n > 0 and (text[n - 1].isalnum() or text[n - 1] == '_'): n -= 1
            if text[n].isdigit(): return i
            i = n
        else:
            return i
        k = i
        while k > 0 and text[k - 1] in ' \t': k -= 1
        if text[max(0, k - 2):k] in ('->', '::'): i = k - 2
        elif k > 0 and text[k - 1] == '.':
            t = k - 1                           # a member access, unless the '.' belongs to a number (1.f)
            while t > 0 and (text[t - 1].isalnum() or text[t - 1] == '_'): t -= 1
            if t < k - 1 and text[t].isdigit(): return i
            i = k - 1
        else: return i

def r_sse_members(path, text, stats):
    """SSE3: MSVC union members -> element access through a typed pointer (lvalue-safe): v.m128_f32[i] ->
    ((float*)&(v))[i], p->m128_f32[i] -> ((float*)(p))[i]; unsubscripted v.m128_f32 (array decay) the same."""
    out, pos = [], 0
    for m in M128_MEMBER.finditer(text):
        s = operand_start(text, m.start())
        if s >= m.start() or s < pos: continue
        expr = text[s:m.start()].strip()
        out.append(text[pos:s])
        out.append('((%s*)%s(%s))' % (ELEM[m.group(3)], '&' if m.group(1) == '.' else '', expr))
        pos = m.end()
        stats['SSE3'] += 1
    out.append(text[pos:])
    return ''.join(out)

QUALIFIED_SSE = re.compile(r'\b(?:\w+::)+(?=(?:__m128[id]?|__m64|_mm_\w+)\b)')

GLOBAL_SSE = re.compile(r'(?<![\w>)\]:])::\s*(?=(?:__m128[id]?|__m64|_mm_\w+)\b)')
PAREN_SSE = re.compile(r'(\w)?\(\s*(_mm_\w+)\s*\)\s*\(')
# (also unqualified: `using ::_mm_add_ps;` the decomp wrote, or the same without `::`; sse2neon's macros have no name)
USING_SSE = re.compile(r'\busing\s+(?:::)?(?:\w+::)*(?:__m128[id]?|__m64|_mm_\w+)\s*;')

def r_sse_qualified(path, text, stats):
    """SSE4: N::__m128 / N::_mm_mul_ps (qualified names of the removed local copies) -> the global names;
    `using N::_mm_mul_ps;` declarations of them are dropped (the global names are visible everywhere)."""
    text, n = USING_SSE.subn('', text)
    stats['SSE4'] += n
    text, n = QUALIFIED_SSE.subn('', text)
    stats['SSE4'] += n
    text, n = GLOBAL_SSE.subn('', text)       # ::_mm_shuffle_ps(...) - sse2neon defines some as macros
    stats['SSE4'] += n
    # (_mm_shuffle_ps)(a, b, i) suppresses the macro; a name in front stays apart (`T(_mm_f)(..)` -> `T _mm_f(..)`)
    text, n = PAREN_SSE.subn(lambda m: (m.group(1) + ' ' if m.group(1) else '') + m.group(2) + '(', text)
    stats['SSE4'] += n
    return text

# ---- scopes: which kind of brace a position is inside (namespace / class / extern / function / other)

# comments and preprocessor lines before a '{' say nothing about it ('// end namespace X', '#pragma pack(push, 4)')
HEAD_NOISE = re.compile(r'//[^\n]*|/\*.*?\*/|^[ \t]*#[^\n]*', re.S | re.M)
# parenthesized decorations of a class head: __declspec(align(16)), __attribute__((x)), FNV_ALIGN(16), alignas(16)
HEAD_DECOR = re.compile(r'\b(?:__declspec|__attribute__|alignas|_*[A-Z][A-Z0-9_]*ALIGN[A-Z0-9_]*)\s*'
                        r'\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)')
TEMPLATE_PREFIX = re.compile(r'^\s*template\s*<(?:[^<>]|<(?:[^<>]|<[^<>]*>)*>)*>\s*')

def head_kind(head):
    """Kind of the brace that follows `head` (the text just before it) and the fragment that introduced it:
    namespace / extern / enum / class / init / function / other."""
    close = head.find('*/')
    if close >= 0 and not 0 <= head.find('/*') < close:  # the window began inside a /* comment
        head = head[close + 2:]
    frag = re.split(r'[;{}]', HEAD_NOISE.sub('', head))[-1].strip()
    bare = HEAD_DECOR.sub(' ', TEMPLATE_PREFIX.sub('', frag)).strip()
    if re.match(r'(?:inline\s+)?namespace\b', bare): kind = 'namespace'
    elif re.match(r'extern\s+"C(?:\+\+)?"$', bare): kind = 'extern'
    elif re.match(r'(?:typedef\s+)?enum\b[^()]*$', bare): kind = 'enum'
    elif re.match(r'(?:typedef\s+)?(?:class|struct|union)\b[^()]*$', bare): kind = 'class'
    elif bare.endswith('='): kind = 'init'
    elif ')' in bare: kind = 'function'
    else: kind = 'other'
    return kind, (re.sub(r'\s+', ' ', frag) if kind in ('namespace', 'extern') else frag)

def scope_spans(text):
    """Sorted list of (open, close, kind, head) for every brace pair.
    kind: namespace / class / extern / function / init / enum / other; head: the text that introduced the brace
    ('namespace std', 'extern "C"', ...)."""
    spans, stack = [], []
    for m in BRACE_TOKENS.finditer(text):
        c = m.group()
        if c == '{':
            i = m.start()
            # the head window starts at a line start, so a `//` comment is never cut in half (its tail would count)
            kind, h = head_kind(text[text.rfind('\n', 0, max(0, i - 300)) + 1:i])
            stack.append((i, kind, h))
        elif c == '}':
            if stack:
                o, k, h = stack.pop(); spans.append((o, m.start(), k, h))
    spans.sort()
    return spans

# braces outside string / character literals and comments (an unterminated comment runs to the end)
BRACE_TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?(?:\*/|\Z)|[{}]', re.S)

def scope_map(text):
    """Sorted list of (open, close, kind) for every brace pair (see scope_spans)."""
    return [(o, c, k) for o, c, k, _ in scope_spans(text)]

def innermost(spans, pos):
    kind = 'file'
    for sp in spans:
        if sp[0] > pos: break
        if sp[0] < pos < sp[1]: kind = sp[2]
    return kind

def scope_chain(spans, pos):
    """Enclosing scopes of pos, outermost first, as (kind, head) pairs (spans from scope_spans)."""
    chain = []
    for o, c, k, h in spans:
        if o > pos: break
        if o < pos < c: chain.append((k, h))
    return chain

# CRT functions the decomp re-declares, by the header that declares them on the target. MSVC-only names are
# declared by the shim's fnvnx_crt.h with their MSVC prototypes.
CRT_HEADERS = {
    '<string.h>': 'memset memcpy memmove memcmp memchr strlen strcmp strncmp strcpy strncpy strcat strncat strchr '
                  'strrchr strstr strtok',
    '"fnvnx_crt.h"': '_stricmp _strnicmp stricmp strnicmp _strdup _snprintf _vsnprintf _msize strcpy_s strcat_s '
                     'strncpy_s sprintf_s vsprintf_s memcpy_s memmove_s strtok_s _splitpath_s _strlwr_s _finite _isnan '
                     '_gcvt',
    '<stdio.h>': 'sprintf vsprintf sscanf printf fprintf',
    '<stdlib.h>': 'atoi atof atol strtol strtoul strtod malloc free calloc realloc abs labs qsort bsearch rand srand '
                  'exit abort',
    '<math.h>': 'fabs fabsf sqrt sqrtf sin sinf cos cosf tan tanf asin asinf acos acosf atan atanf atan2 atan2f exp '
                'expf log logf log10 log10f pow powf floor floorf ceil ceilf fmod fmodf modf frexp ldexp',
    '<ctype.h>': 'toupper tolower isspace isdigit isalpha isalnum isupper islower',
    '<wchar.h>': 'wmemcmp wmemcpy wmemmove wmemset wmemchr wcslen',
    '': '_alloca _ReadWriteBarrier _ReadBarrier _WriteBarrier _CxxThrowException operator_delete_sized '
          '_operator_delete_sized',   # in the force-included fnvnx_compat.h
}
CRT_NAMES = [n for v in CRT_HEADERS.values() for n in v.split()]
# stdio functions S4b implements on MSVC's struct _iobuf (platform/crt/fnvnx_iobuf.h): a decomp prototype of one of
# these that takes or returns _iobuf* is replaced by that header (its C++ overloads take _iobuf*). fopen has libc's
# parameters, so no overload can return _iobuf*: its prototype is replaced by "fnvnx_iobuf_fopen.h", that header plus
# `#define fopen fnvnx_iob_fopen` for the rest of the unit (fnvnx_iob_fopen is the provider of the CRT fopen that
# units.json binds, platform/crt/crt_symbols.tsv; DECL1-FOPEN)
IOBUF_NAMES = set('''_fclose_nolock _fdopen _fileno _fseeki64 _fsopen _ftelli64 clearerr fclose feof ferror fflush fgetc
    fgetpos fgets fopen fopen_s fprintf fputc fputs fread fseek fsetpos ftell fwrite getc putc rewind setvbuf ungetc
    vfprintf __iob_func'''.split())
IOBUF_MACRO = {'fopen': '"fnvnx_iobuf_fopen.h"'}
MACRO_NAMES = {'_alloca'}     # macros on the target (fnvnx_compat.h): no using-declaration possible

OWN_STD = re.compile(r'\b(?:class|struct)\s+(?:exception|bad_alloc|bad_cast|type_info)\s*[:{]')
PLACEMENT_PARAMS = re.compile(r'^\s*(?:(?:std::)?size_t|__SIZE_TYPE__|unsigned(?:\s+(?:int|long))?|void\s*\*)\s*\w*\s*,'
                              r'\s*void\s*\*\s*\w*\s*$')

def new_header(text):
    """The header that declares placement new for this file: <new>, or the shim's fnvnx_new.h where the file
    carries its own copy of MSVC's std::exception (libstdc++'s <new> would define it again)."""
    return '"fnvnx_new.h"' if OWN_STD.search(text) else '<new>'

def platform_names():
    """name -> the header that declares it on the target (CRT_HEADERS, and every function of fnvnx_win32.h)."""
    names = {n: h for h, v in CRT_HEADERS.items() for n in v.split()}
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shim', 'include', 'fnvnx_win32.h')
    for line in open(here):
        m = re.match(r'^[A-Za-z_][\w \*]*?\b([A-Z_][A-Za-z0-9_]+)\(', line)
        if m: names.setdefault(m.group(1), '"fnvnx_win32.h"')
    return names

# ---- DECL1 for <math.h> names: the decomp's float / double declarations
#
# MSVC compiled each unit against the math declarations the unit itself has: the decomp's own prototypes, plus the
# C++ float / long double overloads of MSVC's <math.h> only where the unit includes it. libstdc++'s <math.h> always
# brings those overloads into the global namespace, so including it for a unit that does not include it changes the
# callee and the precision of every call with a float argument (`sqrt(f)` was the double sqrt, `floor(f * 24.0)`
# went to the decomp's float floor). So for these names DECL1 declares exactly the C function the decomp prototype
# is, with the shim's one-function header "fnvnx_cmath/sqrt.h" (glibc's declaration, noexcept, so a later <math.h>
# agrees; a system header, so a later decomp redeclaration without noexcept is accepted), not <math.h>; and a
# decomp prototype with other types than the C function (`float floor(float)`, `float cos(float)`,
# `float fmod(float, float)`: MSVC's float overloads, out of line in the game at 0x00406cc0 / 0x004e4470, bound by
# the unit's recipe) is kept as it is, where no translation unit that sees the file includes <math.h> (there the
# overloads are MSVC's too and the decomp's copy would collide with libstdc++'s). One with C linkage
# (`extern "C" float floor(float)`) cannot sit next to the C function; GAME1 renames it (see there).
# In a namespace N, the decomp's prototype of the C function hides the global overloads: N's `fabs(f)` is the double
# C function, also where the unit includes <math.h> (MSVC). `using ::fabs;` would bring <math.h>'s float overload
# into N (S11: niobbox, bhkrigidbody, hkpworldviewerbase rounded through float), so N keeps a declaration of the C
# function alone: the decomp's own prototype where it has C linkage, else (a C++ spelling the unit binds to the CRT
# function) `extern "C" double fabs(double);` in its place; the shim's header goes on top of the file, so its noexcept
# declaration comes first and GCC takes the redeclaration (DECL1-MATHNS).

MATH_NAMES = set(CRT_HEADERS['<math.h>'].split())

def _c_math_sig(name):
    t = 'float' if name.endswith('f') and name[:-1] in MATH_NAMES else 'double'
    base = name[:-1] if t == 'float' else name
    second = {'atan2': t, 'pow': t, 'fmod': t, 'modf': t + '*', 'frexp': 'int*', 'ldexp': 'int'}.get(base)
    return (t, (t, second) if second else (t,))

C_MATH_SIG = {n: _c_math_sig(n) for n in MATH_NAMES}

def c_math_header(name):
    return '"fnvnx_cmath/%s.h"' % name

def _norm_type(t):
    t = re.sub(r'\[[^\]]*\]|"C"', ' ', t)
    t = re.sub(r'\b(?:__cdecl|__stdcall|_CRTIMP|_CRT_JIT_INTRINSIC|__declspec\s*\(\w+\)|extern|inline|__inline|static|'
               r'const|_In_|_Out_|_Inout_|register)\b', ' ', t).strip()
    if re.search(r'[\w\*&]\s*\b[A-Za-z_]\w*$', t) and len(re.findall(r'[A-Za-z_]\w*', t)) > 1:
        t = re.sub(r'\b[A-Za-z_]\w*$', '', t)               # the parameter name
    return re.sub(r'\s+', '', t)

def math_proto_sig(proto, name):
    """(return type, parameter types) of a decomp prototype of `name` (PROTO's match text), normalized."""
    i = re.search(r'\b%s\s*\(' % re.escape(name), proto)
    params = proto[i.end():proto.rindex(')')]
    params = re.sub(r'\)\s*throw\s*\([^)]*$', '', params)
    params = re.sub(r'\[[^\]]*\]', ' ', params)          # attribute SAL ([SA_Pre(Deref=1,Valid=SA_Yes)]) has commas
    ps = tuple(_norm_type(x) for x in params.split(',')) if params.strip() not in ('', 'void') else ()
    return (_norm_type(proto[:i.start()]), ps)

MATH_INCLUDE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*[<"](?:math\.h|cmath|d3dx9[a-z]*\.h)[>"]', re.M | re.I)
# the decomp's copies of MSVC <math.h>'s inline overloads (S1 MATH1 replaces them by <math.h>): the header's content
MATH_PASTED = re.compile(r'^[ \t]*(?:extern\s*"C"\s*)?(?:inline|__inline|__forceinline)\s+(?:float|long\s+double|double)\s+'
                         r'(?:__cdecl\s+)?(?:abs|(?:fabs|sqrt|sin|cos|tan|asin|acos|atan|atan2|exp|log|log10|pow|floor|'
                         r'ceil|fmod|modf|frexp|ldexp|sinh|cosh|tanh)f?)\s*\([^;{}()]*\)\s*\{', re.M)
_INC_GRAPH, _MATH_SEEN = None, {}

def _include_graph(root):
    """{file: ([files it includes], has a math include)} for the tree (quoted includes: the including file's directory,
    including case-insensitively as MSVC on Windows, then include/ and src/)."""
    graph, listing = {}, {}
    def ci(d, b):
        if d not in listing:
            try: listing[d] = {f.lower(): f for f in os.listdir(d)}
            except OSError: listing[d] = {}
        return listing[d].get(b.lower())
    for top in ('src', 'include'):
        for dp, _, fs in os.walk(os.path.join(root, top)):
            for f in fs:
                if not f.endswith(('.h', '.cpp', '.inl', '.hpp', '.c')): continue
                fp = os.path.normpath(os.path.join(dp, f))
                with open(fp, encoding='utf-8', errors='surrogateescape') as h: t = h.read()
                incs = []
                for inc in re.findall(r'^[ \t]*#[ \t]*include[ \t]*"([^"]+)"', t, re.M):
                    full = os.path.normpath(os.path.join(dp, inc))
                    real = full if os.path.isfile(full) else None
                    if not real:
                        hit = ci(os.path.dirname(full), os.path.basename(full))
                        if hit: real = os.path.join(os.path.dirname(full), hit)
                    if not real:
                        for base in ('include', 'src'):
                            g = os.path.normpath(os.path.join(root, base, inc))
                            if os.path.isfile(g): real = g; break
                    if real: incs.append(real)
                graph[fp] = (incs, bool(MATH_INCLUDE.search(t) or MATH_PASTED.search(t)), c_math_others(t))
    return graph

def c_math_others(text):
    """math names this text declares at file / namespace scope with C linkage and other types than the C function
    (`extern "C" float floor(float);`)"""
    if '"C"' not in text: return set()
    out, spans = set(), None
    for m in PROTO.finditer(text):
        name = m.group(1)
        if name not in MATH_NAMES or math_proto_sig(m.group(0), name) == C_MATH_SIG[name]: continue
        if spans is None: spans = scope_spans(text)
        chain = scope_chain(spans, m.start())
        if any(k not in ('namespace', 'extern') for k, _ in chain): continue
        if 'extern "C"' in m.group(0) or any(k == 'extern' and '"C"' in h for k, h in chain): out.add(name)
    return out

def include_graph(path):
    global _INC_GRAPH
    if _INC_GRAPH is None:
        root, _ = _units_json(path)
        _INC_GRAPH = _include_graph(root) if root else {}
    return _INC_GRAPH

def math_seen(path):
    """True when a translation unit that has this file (a .cpp including it, or the file itself) includes <math.h>
    (<cmath>, or the d3dx9 headers that include it) anywhere."""
    p = os.path.normpath(os.path.abspath(path))
    if p in _MATH_SEEN: return _MATH_SEEN[p]
    include_graph(path)
    rev = {}
    for f, (incs, _, _) in _INC_GRAPH.items():
        for i in incs: rev.setdefault(i, []).append(f)
    users, todo = set(), [p]
    while todo:                                      # the files that include p, transitively
        f = todo.pop()
        if f in users: continue
        users.add(f)
        todo.extend(rev.get(f, ()))
    seen = False
    for u in users:
        if not u.endswith(('.cpp', '.c')) and u != p: continue
        done, todo = set(), [u]
        while todo and not seen:
            f = todo.pop()
            if f in done: continue
            done.add(f)
            incs, has, _ = _INC_GRAPH.get(f, ((), False, ()))
            seen = has
            todo.extend(incs)
        if seen: break
    _MATH_SEEN[p] = seen
    return seen

PLATFORM = None
# path -> {name: indices of the reference parameters of the decomp prototype DECL1 dropped} (CALL1 passes addresses)
DECL1_REFS = {}
PROTO = re.compile(r'^[ \t]*(?:extern\s+"C"\s+)?(?:__declspec\(\w+\)\s+)*(?:static\s+)?(?:inline\s+)?'
                   r'[A-Za-z_][\w \t\*&:<>,]*?[\s\*&](?:(?:__cdecl|__stdcall|__fastcall|WINAPI|__declspec\(\w+\))\s*)*'
                   r'(\w+|operator\s+(?:new|delete)(?:\s*\[\s*\])?)\s*\([^;{}]*\)\s*(?:throw\s*\([^)]*\)\s*)?;[ \t]*(?://[^\n]*)?$',
                   re.M)

# the head of a prototype that goes on on the next line (`extern "C" __declspec(dllimport) HANDLE` / `__stdcall f(...);`)
PROTO_HEAD = re.compile(r'[ \t]*(?:extern\s+"C"\s+)?(?:__declspec\(\w+\)\s+)*'
                        r'(?!(?:return|else|case|goto|public|private|protected|typedef)\b)[A-Za-z_]\w*(?:[ \t]*[\*&]+)?[ \t]*\n$')

def r_platform_protos(path, text, stats):
    """DECL1: drop the decomp's own prototypes of CRT / Win32 functions (and namespace-scope operator new/delete)
    outside classes; the platform headers declare them with the target's types (size_t is unsigned long on
    AArch64 ILP32, unsigned int on MSVC x86). The header that declares the function on the target takes the place
    of the first such prototype at file scope (same line, so line numbers are kept), or goes on top of the file
    when the prototype was inside a namespace or extern block. A prototype that declares a different entity is
    kept: C++ linkage inside a namespace other than ::std (N::fmod is not ::fmod). <math.h> names get the C
    function's own declaration instead of the header, and the decomp's float overloads stay (see MATH_NAMES)."""
    global PLATFORM
    if PLATFORM is None: PLATFORM = platform_names()
    spans, out, pos, have, top, kept, dropped = None, [], 0, set(), [], set(), {}
    for m in PROTO.finditer(text):
        name = re.sub(r'\s+', ' ', m.group(1))
        is_op = name.startswith('operator')
        if not is_op and name not in PLATFORM and not (name in IOBUF_NAMES and '_iobuf' in m.group(0)): continue
        if spans is None: spans = scope_spans(text)
        chain = scope_chain(spans, m.start())
        if any(k not in ('namespace', 'extern') for k, _ in chain): continue
        spaces = [re.sub(r'\s+', ' ', h) for k, h in chain if k == 'namespace']
        c_linkage = 'extern "C"' in m.group(0) or any(k == 'extern' and '"C"' in h for k, h in chain)
        is_math = not is_op and name in MATH_NAMES
        if (is_math and not c_linkage and math_proto_sig(m.group(0), name) != C_MATH_SIG[name]
                and not math_seen(path)):
            stats['DECL1-MATHKEEP'] += 1             # the decomp's float / double types (see MATH_NAMES above)
            continue
        if (not is_op and spaces and spaces != ['namespace std'] and not c_linkage
                and not cxx_copy_is_crt(path, name)):
            kept.add(name)                           # N::name stays N's own function (DECL1-NSADL below)
            continue
        if is_math and spaces != ['namespace std']:  # the C function, by itself (no <math.h> overloads)
            start = m.start()
            if start > 0 and text[start - 1] == '\n':
                ls = text.rfind('\n', 0, start - 1) + 1
                if ls >= pos and PROTO_HEAD.match(text, ls, start): start = ls
            nl = '\n' * text.count('\n', start, m.end())
            key = 'math:' + name
            if spaces and math_proto_sig(m.group(0), name) == C_MATH_SIG[name]:
                # N keeps the C function alone (see MATH_NAMES above): no using-declaration of the overloads
                if key not in have: have.add(key); top.append(c_math_header(name))
                stats['DECL1-MATHNS'] += 1
                if c_linkage: continue                    # the decomp's prototype stays as it is
                ret, params = C_MATH_SIG[name]
                out.append(text[pos:start])
                out.append('extern "C" %s %s(%s); /* fnv-nx DECL1 */%s' % (ret, name, ', '.join(params), nl))
                pos = m.end()
                stats['DECL1'] += 1
                continue
            out.append(text[pos:start])
            alias = 'using ::%s; ' % name if spaces else ''
            if not chain and key not in have:
                out.append('#include %s /* fnv-nx DECL1 */%s' % (c_math_header(name), nl)); have.add(key)
            else:
                out.append(alias + '/* fnv-nx DECL1 */' + nl)
                if key not in have: have.add(key); top.append(c_math_header(name))
            pos = m.end()
            stats['DECL1'] += 1
            continue
        if is_op:   # the usual forms are declared implicitly in every unit; placement forms come from <new>
            params = re.search(r'\(([^;{}]*)\)', m.group(0)[m.group(0).index('operator'):]).group(1)
            hdr = new_header(text) if PLACEMENT_PARAMS.match(params) else ''
        elif name in IOBUF_NAMES and '_iobuf' in m.group(0):
            hdr = IOBUF_MACRO.get(name, '"fnvnx_iobuf.h"')
            if name in IOBUF_MACRO: stats['DECL1-FOPEN'] += 1
        elif name == 'abs' and spaces == ['namespace std']:
            hdr = '"fnvnx_cmath/std_abs.h"'           # DECL1-STDABS (the header says why)
            stats['DECL1-STDABS'] += 1
        else:
            hdr = PLATFORM[name]
        if not is_op:
            ps = re.search(r'\(([^;{}]*)\)', text[m.end(1):m.end()]).group(1).split(',')
            refs = [i for i, p in enumerate(ps) if re.search(r'&(?!&)', p)]
            if refs: DECL1_REFS.setdefault(path, {})[name] = refs
        start = m.start()
        if start > 0 and text[start - 1] == '\n':          # `extern "C" __declspec(dllimport) HANDLE` on the line above
            ls = text.rfind('\n', 0, start - 1) + 1
            if ls >= pos and PROTO_HEAD.match(text, ls, start): start = ls
        nl = '\n' * text.count('\n', start, m.end())
        if not is_op and spaces != ['namespace std']:
            dropped.setdefault(name, (m.group(0), {h.split()[-1] for h in spaces}))   # DECL1-CONV (below)
        out.append(text[pos:start])
        # in a namespace N other than std the name must stay visible as N::name (a local template or overload
        # would hide the global one): a using-declaration of the platform function takes the prototype's place
        alias = ('using ::%s; ' % name if not is_op and name not in MACRO_NAMES and spaces
                 and spaces != ['namespace std'] else '')
        if not hdr:
            out.append(alias + '/* fnv-nx DECL1 */' + nl)
        elif not chain and hdr not in have:
            out.append('#include %s /* fnv-nx DECL1 */%s' % (hdr, nl)); have.add(hdr)
        else:
            out.append(alias + '/* fnv-nx DECL1 */' + nl)
            if hdr not in have: have.add(hdr); top.append(hdr)
        pos = m.end()
        stats['DECL1'] += 1
    out.append(text[pos:])
    text = ''.join('#include %s /* fnv-nx DECL1 */\n' % h for h in top) + ''.join(out)
    if dropped:
        text = decl1_conversions(text, dropped, stats)
    return ns_own_calls(text, kept, stats) if kept and (have or top) else text


# DECL1-CONV. A dropped prototype can differ from the platform's declaration in kind, not only in spelling: the
# decomp declares `unsigned int CreateMutexA(...)`, `int FindWindowA(...)`, `int FindFirstFileA(...)` where the SDK /
# shim return a handle, `CallNextHookEx(void *, ...)` where the platform takes an HHOOK, and the C++ <string.h>
# overloads return `const char *` for `strrchr(const char *, int)` where the decomp declared `char *`. The decomp's
# code uses the values as it declared them (`int state = FindFirstFileA(...)`), which the target compiles only
# through -fpermissive's implicit conversions and MSVC 2008 refuses (C2440 / C2664). DECL1 writes those conversions
# out where the call's result is stored or returned: as the decomp's return type, where the two differ in kind (an
# integer against a pointer or handle, `char *` from a const-returning overload). On ILP32 and on x86 the bits are the
# same: the code the -fpermissive conversion made, and what the decomp's own prototype meant under MSVC. Arguments
# are CALL1's (pointer parameters of Win32 calls cast to the shim's types).

CONST_RESULT = {'strchr', 'strrchr', 'strstr', 'strpbrk', 'wcschr', 'wcsrchr', 'wcsstr', 'wcspbrk', 'memchr'}
_SHIM_PROTOS = None
CONV_WORDS = re.compile(r'__declspec\s*\(\s*\w+\s*\)|"C"|\b(?:extern|static|inline|__inline|__forceinline|__cdecl|'
                        r'__stdcall|__fastcall|WINAPI|APIENTRY|CALLBACK|WINAPIV|NTAPI)\b')
NOT_PTR = {'LPARAM', 'WPARAM', 'LRESULT', 'LONG_PTR', 'ULONG_PTR', 'UINT_PTR', 'INT_PTR', 'DWORD_PTR', 'SIZE_T', 'HRESULT',
           'LCID', 'LANGID', 'PROPID'}


def shim_protos():
    """{name: (return type, [parameter types])} of the target shim's fnvnx_win32.h prototypes"""
    global _SHIM_PROTOS
    if _SHIM_PROTOS is None:
        _SHIM_PROTOS = {}
        here = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shim', 'include', 'fnvnx_win32.h')
        t = re.sub(r'/\*.*?\*/|//[^\n]*', ' ', open(here).read(), flags=re.S)
        for m in re.finditer(r'^[ \t]*((?:const\s+)?[A-Za-z_]\w*(?:\s*\*+)?)\s+\**\s*([A-Za-z_]\w*)\s*\(([^;{}()]*)\)\s*;', t, re.M):
            ps = [p.strip() for p in m.group(3).split(',')] if m.group(3).strip() not in ('', 'void') else []
            _SHIM_PROTOS.setdefault(m.group(2), (m.group(1).strip(), [_ptype(p) for p in ps]))
    return _SHIM_PROTOS


def _ptype(p):
    """the type of a parameter declaration (drops the parameter name and default)"""
    p = re.sub(r'=.*$', '', p).strip()
    p = re.sub(r'\b(?:IN|OUT|OPTIONAL|_In_\w*|_Out_\w*|_Inout_\w*)\b', ' ', p).strip()
    m = re.match(r'^(.*?[\s\*&])([A-Za-z_]\w*)\s*(\[[^\]]*\])?$', p)
    if m and m.group(1).strip() and not re.fullmatch(r'(?:const\s+)?(?:unsigned|signed|struct|enum)', m.group(1).strip()):
        p = m.group(1) + ('*' if m.group(3) else '')
    return re.sub(r'\s+', ' ', re.sub(r'\s*\*', '*', p)).strip()


def _proto_types(proto, name):
    head, _, rest = proto.partition(name)
    ret = re.sub(r'\s+', ' ', CONV_WORDS.sub(' ', head)).strip()
    ret = re.sub(r'\s*\*', '*', ret)
    params = rest[rest.index('(') + 1:rest.rindex(')')]
    ps = [p for p in _split_args(params)] if params.strip() not in ('', 'void') else []
    return ret, [_ptype(p) for p in ps]


def _is_ptr(t):
    t = t.replace('const ', '').strip()
    if t in NOT_PTR:
        return False
    return '*' in t or bool(re.fullmatch(r'H[A-Z0-9_]+|LP[A-Z]\w*|P[A-Z][A-Z0-9_]*|HANDLE|LPVOID|PVOID|FARPROC', t))


def _split_args(a):
    out, depth, cur, q = [], 0, [], None
    for ch in a:
        if q:
            cur.append(ch)
            if ch == q and (len(cur) < 2 or cur[-2] != '\\'): q = None
            continue
        if ch in '"\'': q = ch
        elif ch in '([{': depth += 1
        elif ch in ')]}': depth -= 1
        elif ch == ',' and depth == 0:
            out.append(''.join(cur)); cur = []; continue
        cur.append(ch)
    out.append(''.join(cur))
    return out


CALL_PREV = re.compile(r'(?:[=(,;{}!&|?:+\-*/<>\[]|\breturn)\s*$')


def decl1_conversions(text, dropped, stats):
    """DECL1-CONV (see above): explicit conversions at the unqualified calls of the dropped names."""
    shim = shim_protos()
    plan, quals = {}, {}
    for name, (proto, spaces) in dropped.items():
        quals[name] = spaces
        dret, dps = _proto_types(proto, name)
        pret, pps = shim.get(name, (None, None))
        ret_cast = None
        if dret and dret != 'void':
            if pret is not None and _is_ptr(pret) != _is_ptr(dret):
                ret_cast = dret
            elif name in CONST_RESULT and dret.replace(' ', '') in ('char*', 'wchar_t*', 'void*'):
                ret_cast = dret
        arg_casts = {}       # arguments: CALL1 casts pointer arguments of Win32 calls to the shim's types
        if ret_cast or arg_casts:
            plan[name] = (ret_cast, arg_casts)
    if not plan:
        return text
    # unqualified, or qualified by the namespace the prototype was in (`InstalledPathDetail::RegOpenKeyExA(`, where
    # DECL1 left `using ::RegOpenKeyExA;`)
    rx = re.compile(r'(?<![\w.>:~])((?:[A-Za-z_]\w*\s*::\s*)?)(%s)\s*\(' % '|'.join(re.escape(n) for n in sorted(plan)))
    out, pos = [], 0
    for m in rx.finditer(text):
        if m.start() < pos: continue
        q = re.sub(r'\s*::\s*$', '', m.group(1))
        if q and q not in quals[m.group(2)]: continue
        line = text[text.rfind('\n', 0, m.start()) + 1:m.start()]
        if '//' in line or '#' in line.lstrip()[:1] or not CALL_PREV.search(text[max(0, m.start() - 40):m.start()]):
            continue
        close = match_paren(text, m.end() - 1)
        if close < 0: continue
        ret_cast, arg_casts = plan[m.group(2)]
        args = _split_args(text[m.end():close])
        if arg_casts and not (len(args) == 1 and not args[0].strip()):
            args = ['(%s)(%s)' % (arg_casts[i], a.strip()) if i in arg_casts and a.strip() else a for i, a in enumerate(args)]
        call = m.group(1) + m.group(2) + '(' + ','.join(args) + ')'
        # the result as the decomp's type only where it is stored or returned (`x = f(...)`, `T x = f(...)`,
        # `return f(...)`): as another call's argument it meets that call's declaration, the platform's
        as_value = re.search(r'(?:(?<![=!<>])=|\breturn)\s*$', text[max(0, m.start() - 40):m.start()])
        if not as_value:
            ret_cast = None
        if not ret_cast and args == _split_args(text[m.end():close]):
            continue
        out.append(text[pos:m.start()])
        out.append('((%s)%s)' % (ret_cast, call) if ret_cast else call)
        pos = close + 1
        stats['DECL1-CONV'] += 1
    out.append(text[pos:])
    return ''.join(out)


def match_paren(text, open_at):
    """index of the ')' matching text[open_at] == '(' (strings and char literals skipped), or -1"""
    depth, i, n = 0, open_at, len(text)
    while i < n:
        ch = text[i]
        if ch in '"\'':
            j = i + 1
            while j < n and text[j] != ch:
                j += 2 if text[j] == '\\' else 1
            i = j
        elif ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return i
        elif ch == ';' or ch == '{':
            return -1
        i += 1
    return -1

NS_CALL_PREV = re.compile(r'(?:[(=,;{}!?:+\-|<\[]|\b(?:return|else|do|case))\s*$')

def ns_own_calls(text, names, stats):
    """DECL1-NSADL: in a namespace N (not std) that keeps its own C++ prototype of a platform name (N::strcpy_s,
    bound to the game's wrapper), an unqualified call of that name becomes `(name)(args)`. The platform header DECL1
    added declares ::name too, and argument-dependent lookup finds it through an argument whose class has a base in
    the global namespace: `strcpy_s(buf, 260, fixedString)` is ambiguous on GCC and MSVC. The parenthesized name
    turns argument-dependent lookup off, so the call reaches N::name, as it did in the decomp."""
    spans = scope_spans(text)
    edits = []
    for o, c, k, h in spans:
        if k != 'namespace' or re.sub(r'\s+', ' ', h) == 'namespace std':
            continue
        if any(o2 < o and c < c2 and k2 == 'namespace' for o2, c2, k2, _ in spans):
            continue                                     # the outermost namespace covers its nested ones
        body = text[o:c]
        own = [n for n in names if re.search(r'^[ \t]*(?!extern\s+"C")[A-Za-z_][\w \t\*&:<>,]*?[\s\*&]%s\s*\([^;{}]*\)\s*;'
                                            % re.escape(n), body, re.M)]
        for n in own:
            for m in re.finditer(r'(?<![\w:.>~])%s(?=\s*\()' % re.escape(n), text[o:c]):
                at = o + m.start()
                line = text.rfind('\n', 0, at) + 1
                if '//' in text[line:at] or not NS_CALL_PREV.search(text[max(o, at - 40):at]):
                    continue                             # a declaration (`int strcpy_s(`) or a comment
                edits.append((at, at + len(n)))
    for a, b in sorted(set(edits), reverse=True):
        text = text[:a] + '(' + text[a:b] + ')' + text[b:]
        stats['DECL1-NSADL'] += 1
    return text

# ---- DECL3: the decomp's own typedefs of C library types that the target defines differently
#
# A unit that carries its own <stdio.h> (`typedef _iobuf FILE;`, `typedef __int64 fpos_t;`) clashes with the target's
# FILE (glibc's struct _IO_FILE) and fpos_t (a struct). Under MSVC FILE is _iobuf: the typedef goes and every FILE of
# the unit is spelled _iobuf, so DECL1 then sees its stdio prototypes as the _iobuf functions of fnvnx_iobuf.h (S4b).
# MSVC's fpos_t is __int64: the unit's typedef and its uses get the name fnvnx_msvc_fpos_t (the same type).
CRT_TYPEDEF_FILE = re.compile(r'^[ \t]*typedef[ \t]+(?:struct[ \t]+)?_iobuf[ \t]+FILE[ \t]*;', re.M)
CRT_TYPEDEF_FPOS = re.compile(r'^[ \t]*typedef[ \t]+(?:__int64|long[ \t]+long)[ \t]+fpos_t[ \t]*;', re.M)
CRT_CODE_MASK = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|//[^\n]*|/\*.*?(?:\*/|\Z)', re.S)

def _rename_code_word(text, old, new):
    """`old` -> `new` as a whole word outside comments and literals, not as a member (`.old`, `->old`)."""
    masked = CRT_CODE_MASK.sub(lambda m: re.sub(r'[^\n]', ' ', m.group()), text)
    out, pos, n = [], 0, 0
    for m in re.finditer(r'(?<![\w.>])%s\b' % re.escape(old), masked):
        out.append(text[pos:m.start()]); out.append(new); pos = m.end(); n += 1
    out.append(text[pos:])
    return ''.join(out), n

def r_crt_typedefs(path, text, stats):
    """DECL3: the unit's own FILE / fpos_t typedefs give way to the MSVC types they name (see above)."""
    if 'FILE' in text:
        m = CRT_TYPEDEF_FILE.search(text)
        if m and not prepare_in_scope(text, m.start()):
            text = text[:m.start()] + '/* fnv-nx DECL3: FILE is _iobuf */' + text[m.end():]
            text, n = _rename_code_word(text, 'FILE', '_iobuf')
            stats['DECL3'] += 1 + n
    if 'fpos_t' in text:
        m = CRT_TYPEDEF_FPOS.search(text)
        if m and not prepare_in_scope(text, m.start()):
            text, n = _rename_code_word(text, 'fpos_t', 'fnvnx_msvc_fpos_t')
            stats['DECL3'] += n
    return text

def prepare_in_scope(text, pos):
    """pos is inside a class, function or other non-namespace brace block."""
    return any(k not in ('namespace', 'extern') for k, _ in scope_chain(scope_spans(text), pos))

NEWDEL_PARAMS = re.compile(r'(\boperator\s+(?:new|delete)(?:\s*\[\s*\])?\s*\()([^;{}()]*)(\))')

def r_newdel_size(path, text, stats):
    """NEW1: operator new/delete size parameters spelled 'unsigned int' or plain 'unsigned' (MSVC x86 size_t) ->
    size_t. A parameter passed by reference or pointer (`unsigned int& total`, an out parameter of a class operator
    new) is the caller's unsigned int and keeps its type. (Plain `unsigned`: a class `operator delete[](void*,
    unsigned)` is MSVC's usual sized deallocation function; spelled unsigned int on the target it is a placement form
    and `delete[]` on the class finds no usual one.)"""
    def sub(m):
        params = re.sub(r'\bunsigned(?:\s+int)?\b(?!\s*(?:[&*]|(?:int|long|short|char|__int\d+)\b))', 'size_t',
                        m.group(2))
        if params != m.group(2): stats['NEW1'] += 1
        return m.group(1) + params + m.group(3)
    return NEWDEL_PARAMS.sub(sub, text)

# ---- GAME1: platform-looking names that the decomp binds to game code (D13)
#
# DECL1 replaces the decomp's prototype of a CRT / Win32 function by the platform's, which binds every call by name.
# That is only right where the unit's recipe (build/units.json `symbols`) binds the decorated name to the function's
# own import slot, or (CRT names) to the CRT function of that name. Some units bind a Win32 name to other code: a game
# function (GetCurrentThreadId -> 0x00830470, GetFileSize -> FileSizeInfo::GetFileSize, Sleep -> the spin lock's
# acquire) or another import's slot (EnterCriticalSection -> InterlockedIncrement's IAT entry in memorymanager.cpp).
# GAME1 renames such a name, in the unit and in the local headers it includes that declare it, to fnvnx_game_<name>
# (declaration and calls; class members of that name are left alone) so DECL1 / CALL1 no longer see a platform
# function and the link binds the call by the unit's address (S2: `fnvnx_game_X` is the decomp's X).

GAME_PREFIX = 'fnvnx_game_'
_GAME_FILES = None
IAT_RANGE = (0x00fdf000, 0x00fe0000)
_DECO = [re.compile(r'^__imp__([A-Za-z_]\w*?)(?:@\d+)?$'), re.compile(r'^__imp_\?([A-Za-z_]\w*)@@Y'),
         re.compile(r'^\?([A-Za-z_]\w*)@@Y'), re.compile(r'^@([A-Za-z_]\w*)@\d+$'),
         re.compile(r'^_([A-Za-z_]\w*?)(?:@\d+)?$')]

_CRT_BINDINGS = None
# file -> math names whose GAME1 prototype there gets the float return type of the game function (see GAME1)
_GAME_FLOAT_RET = {}

def crt_inline_float():
    """addresses of the game's out-of-line copies of MSVC's float math overloads (platform/crt/crt_symbols.tsv
    `crt_inline` rows `name(float) -> namef`)"""
    here = _table('crt_symbols.tsv')
    out = set()
    if os.path.exists(here):
        for line in open(here):
            f = line.rstrip('\n').split('\t')
            if len(f) > 2 and f[1] == 'crt_inline' and re.match(r'\w+\(float(?:, float)*\) -> \w+f\b', f[2]):
                out.add(int(f[0], 16))
    return out

def cxx_copy_is_crt(path, name):
    """True when the unit of `path` binds a namespace-qualified C++ spelling of the CRT function `name`
    (`?memcpy@fnv_00914c60@@...`) to an address that is that CRT function's (an address some unit binds `_name`
    to): the decomp's namespace copy of the prototype is then the CRT function itself (D13)."""
    global _CRT_BINDINGS
    if PLATFORM is None or PLATFORM.get(name) == '"fnvnx_win32.h"': return False
    if _CRT_BINDINGS is None:
        import json
        _CRT_BINDINGS = ({}, {})
        root, uj = _units_json(path)
        if uj:
            for u in json.load(open(uj))['units']:
                syms = u.get('symbols', {})
                _CRT_BINDINGS[1][os.path.normpath(os.path.join(root, u['source']))] = syms
                for k, v in syms.items():
                    m = re.match(r'^_([A-Za-z_]\w*?)(?:@\d+)?$', k)
                    if m and not IAT_RANGE[0] <= int(v, 16) < IAT_RANGE[1]:
                        _CRT_BINDINGS[0].setdefault(m.group(1), set()).add(int(v, 16))
    syms = _CRT_BINDINGS[1].get(os.path.normpath(os.path.abspath(path)))
    if not syms: return False
    addrs = {int(v, 16) for k, v in syms.items() if re.match(r'^\?%s@\w+@@' % re.escape(name), k)}
    return bool(addrs) and addrs <= _CRT_BINDINGS[0].get(name, set())

def _units_json(path):
    d = os.path.dirname(os.path.abspath(path))
    while d != os.path.dirname(d):
        f = os.path.join(d, 'build', 'units.json')
        if os.path.exists(f): return d, f
        d = os.path.dirname(d)
    return None, None

def game_bound_names(path):
    """{absolute file: {name}} - Win32 names to rename in that file (see above)."""
    global _GAME_FILES
    if _GAME_FILES is not None: return _GAME_FILES
    import json
    _GAME_FILES = {}
    root, uj = _units_json(path)
    if not uj: return _GAME_FILES
    global PLATFORM
    if PLATFORM is None: PLATFORM = platform_names()
    win32 = {n for n, h in PLATFORM.items() if h == '"fnvnx_win32.h"'}
    units = json.load(open(uj))['units']
    iat_owner = {}
    for u in units:
        for k, v in u.get('symbols', {}).items():
            m = _DECO[0].match(k)
            if m: iat_owner.setdefault(int(v, 16), set()).add(m.group(1))
    bad = {}                                         # unit source -> {name}
    for u in units:
        for k, v in u.get('symbols', {}).items():
            for rx in _DECO:
                m = rx.match(k)
                if not m: continue
                n, a = m.group(1), int(v, 16)
                if n in win32:
                    in_iat = IAT_RANGE[0] <= a < IAT_RANGE[1]
                    if not (in_iat and (n in iat_owner.get(a, {n}))):
                        bad.setdefault(u['source'], set()).add(n)
                break
    # a math function the unit declares with C linkage and other types than the C function (DECL1 math above) and
    # binds (`_floor` -> 0x00406cc0, the game's floor(float)): the unit's own function, under its own name
    graph = include_graph(path)
    others = {f: v[2] for f, v in graph.items() if v[2]}
    if others:
        names = set().union(*others.values())
        float_ret = crt_inline_float()
        for u in units:
            syms = u.get('symbols', {})
            want = {n for n in names if '_' + n in syms}
            if not want: continue
            todo, done = [os.path.normpath(os.path.join(root, u['source']))], set()
            while todo:
                f = todo.pop()
                if f in done: continue
                done.add(f)
                for n in others.get(f, set()) & want:
                    bad.setdefault(u['source'], set()).add(n)
                    if int(syms['_' + n], 16) in float_ret: _GAME_FLOAT_RET.setdefault(f, set()).add(n)
                todo.extend(graph.get(f, ((),))[0])
    inc = re.compile(r'^[ \t]*#[ \t]*include[ \t]*"([^"]+)"', re.M)
    for src, names in bad.items():
        todo, seen = [os.path.normpath(os.path.join(root, src))], set()
        while todo:                                  # the unit and the local headers it includes
            f = todo.pop()
            if f in seen or not os.path.isfile(f): continue
            seen.add(f)
            t = open(f, encoding='utf-8', errors='surrogateescape').read()
            hit = {n for n in names if re.search(r'\b%s\b' % n, t)}
            if hit: _GAME_FILES.setdefault(f, set()).update(hit)
            for i in inc.findall(t):
                for base in (os.path.dirname(f), os.path.join(root, 'include')):
                    g = os.path.normpath(os.path.join(base, i))
                    if os.path.isfile(g): todo.append(g); break
    return _GAME_FILES

def r_game_bound_names(path, text, stats):
    """GAME1: rename Win32-looking names that this unit binds to game code (D13, see above)."""
    key = os.path.normpath(os.path.abspath(path))
    names = game_bound_names(path).get(key)
    if not names: return text
    spans = scope_spans(text)
    out, pos = [], 0
    for m in re.finditer(r'(?<![\w.>:~])(%s)\b' % '|'.join(sorted(names)), text):
        chain = scope_chain(spans, m.start())
        if chain and chain[-1][0] == 'class': continue               # a member of that name
        out.append(text[pos:m.start()]); out.append(GAME_PREFIX + m.group(1)); pos = m.end()
        stats['GAME1'] += 1
    out.append(text[pos:])
    text = ''.join(out)
    # `extern "C" double floor(float);` bound to the game's floor(float) (0x00406cc0): on x86 both return in st0, so
    # the double declaration read the float result exactly; on AArch64 the function returns it in s0, so the
    # prototype says float (the call converts it to double, the value the x86 caller read)
    for n in _GAME_FLOAT_RET.get(key, ()):
        text, k = re.subn(r'\bdouble(\s+(?:__cdecl\s+)?%s%s\s*\(\s*float\b[^(),]*\)\s*;)' % (GAME_PREFIX, n),
                          r'float\1 /* fnv-nx GAME1: the game function returns float */', text)
        stats['GAME1'] += k
    return text

RULES = [r_sse_local_types, r_sse_local_protos, r_sse_members, r_sse_qualified, r_game_bound_names,
         r_crt_typedefs, r_platform_protos, r_newdel_size]

# ---------------------------------------------------------------------------------------------------------- driver

def load_stream_rules():
    """Rules owned by work streams live in tools/rules/<stream>.py, each exporting RULES (list of functions with the
    same signature as above). They run after the core rules, in file name order. Helpers here (match_brace,
    scope_map, innermost) are importable as `prepare`."""
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    sys.modules.setdefault('prepare', sys.modules[__name__])
    rules = []
    for name in sorted(os.listdir(os.path.join(here, 'rules'))):
        if not name.endswith('.py') or name.startswith('_'): continue
        spec = importlib.util.spec_from_file_location('fnvnx_rules_' + name[:-3], os.path.join(here, 'rules', name))
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        rules += list(getattr(mod, 'RULES', []))
        STREAM_POST.extend(getattr(mod, 'POST', []))
    return rules

# post-copy hooks of the stream modules (`POST = [fn(out_root, stats, files)]`, e.g. S1b's units.json renames): run
# after the tree and build/ are written
STREAM_POST = []

def drop_native_units(out_root, stats, files):
    """NATIVE1: units listed in render/native/drop_units.txt (source paths or glob patterns, `#` comments; D6, the
    native renderer replaces them) leave the build list (<out>/build/units.json, which census.sh and the link step
    read). The list is an input of prepare. The matching is tools/rules/native1.py's (also a command line tool)."""
    import json, importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location('fnvnx_native1', os.path.join(here, 'rules', 'native1.py'))
    native1 = importlib.util.module_from_spec(spec); spec.loader.exec_module(native1)
    drop = native1.load_list(os.path.join(here, '..', 'render', 'native', 'drop_units.txt'))
    stats['NATIVE1'] += 0
    if not drop: return
    p = os.path.join(out_root, 'build', 'units.json')
    data, gone = native1.drop_units(json.load(open(p)), drop)
    stats['NATIVE1'] += len(gone)
    files['NATIVE1'] += len({u.get('source') for u in gone})
    json.dump(data, open(p, 'w'))

class Memo:
    """FNVNX_PREPARE_MEMO=<dir> (T1: after a rule change prepare must stay incremental): each rule's result per file,
    keyed by (the rule's code, the file's path, the sha1 of the text it was given). The rule's code is its module
    (tools/rules/<m>.py, or prepare.py for the core rules) plus prepare.py and tools/rules/_*.py, whose helpers
    every rule may use. A rule whose code is unchanged gives back its recorded output and stats for the same input
    text without running; a changed rule runs on every file, and the rules after it hit again wherever it left the
    text as it was. Everything else the rules read is the context: every file opened for reading outside the output
    tree, every directory listed (an audit hook records them; the union over the runs that built the memo), the
    decomp's git head and the FNVNX_* environment. When any of it differs from the record, the memo is not used for
    the run (every rule runs, and the memo is rebuilt). Texts are content-addressed blobs (only changed texts are
    stored); entries and blobs not used by a run are dropped at its end. FNVNX_PREPARE_MEMO_VERIFY=1: every rule runs
    and a recorded result that differs is reported (exit 3). Decomp-derived: the memo stays beside the tree."""
    def __init__(self, d, src_root, out_root):
        import hashlib, marshal, subprocess
        self.h, self.marshal = hashlib, marshal
        self.d, self.out_root = d, os.path.abspath(out_root)
        os.makedirs(os.path.join(d, 'blobs'), exist_ok=True)
        here = os.path.dirname(os.path.abspath(__file__))
        self.here = here
        common = b''.join(open(os.path.join(here, 'rules', n), 'rb').read() for n in sorted(os.listdir(os.path.join(here, 'rules')))
                          if n.startswith('_') and n.endswith('.py'))
        self.base = hashlib.sha1(b'prepare-memo-1\0' + common).hexdigest()
        self.mod_ids, self.rule_ids = {}, {}
        r = subprocess.run(['git', '-C', src_root, 'rev-parse', 'HEAD'], capture_output=True, text=True)
        self.env = sorted([(k, v) for k, v in os.environ.items() if k.startswith('FNVNX_')
                           and not k.startswith('FNVNX_PREPARE_MEMO')] + [('decomp_head', r.stdout.strip())])
        self.read, self.listed = set(), set()
        self.verify = os.environ.get('FNVNX_PREPARE_MEMO_VERIFY') == '1'
        self.mismatch = []
        self.hits = self.runs = self.impure_runs = 0
        try:
            ctx = marshal.load(open(os.path.join(d, 'ctx.marshal'), 'rb'))
            self.map = marshal.load(open(os.path.join(d, 'map.marshal'), 'rb'))
        except (OSError, ValueError, EOFError):
            ctx, self.map = None, {}
        self.valid = ctx is not None and ctx.get('env') == self.env and self._ctx_same(ctx)
        self.ctx_files = dict(ctx['files']) if self.valid else {}
        self.ctx_dirs = dict(ctx['dirs']) if self.valid else {}
        if not self.valid:
            self.map = {}
        self.used = {}
        self.blobs_used = set()
        sys.addaudithook(self._audit)

    def _fh(self, p):
        try:
            return self.h.sha1(open(p, 'rb').read()).hexdigest()
        except OSError:
            return None

    def _names(self, d):
        try:
            return self.h.sha1('\0'.join(sorted(os.listdir(d))).encode('utf-8', 'surrogateescape')).hexdigest()
        except OSError:
            return None

    def _ctx_same(self, ctx):
        return all(self._fh(p) == h for p, h in ctx['files'].items()) and \
            all(self._names(p) == h for p, h in ctx['dirs'].items())

    def _audit(self, ev, args):
        if ev == 'open' and args and isinstance(args[0], (str, bytes)):
            mode = args[1] if len(args) > 1 and isinstance(args[1], str) else 'r'
            if 'w' in mode or 'a' in mode or '+' in mode or 'x' in mode:
                return
            p = os.path.abspath(os.fsdecode(args[0]))
            if not p.startswith(self.out_root + os.sep) and not p.startswith(os.path.abspath(self.d) + os.sep):
                self.read.add(p)
        elif ev in ('os.listdir', 'os.scandir') and args:
            a = args[0]
            if isinstance(a, (str, bytes)):
                self.listed.add(os.path.abspath(os.fsdecode(a)))

    def _module(self, f):
        """(top-level name -> (source segment, names it references), impure names) of the module in file f"""
        f = os.path.abspath(f)
        if f in self.mod_ids:
            return self.mod_ids[f]
        import ast
        src = open(f, encoding='utf-8', errors='surrogateescape').read()
        tree = ast.parse(src)
        top = {}
        for node in tree.body:
            seg = ast.get_source_segment(src, node) or ''
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | \
                    {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)
                     and isinstance(n.value, ast.Name) and n.value.id == 'prepare'}
            defined = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined = [node.name]
            elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                tg = node.targets if isinstance(node, ast.Assign) else [node.target]
                defined = [n.id for t in tg for n in ast.walk(t) if isinstance(n, ast.Name)]
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                defined = [(a.asname or a.name).split('.')[0] for a in node.names]
            for d in defined:
                old = top.get(d)
                top[d] = ((old[0] + '\n' if old else '') + seg, (old[1] if old else set()) | names)
        glob = set(top) | {'prepare'}
        impure = set()
        MUT = {'append', 'add', 'update', 'setdefault', 'extend', 'insert', 'pop', 'popitem', 'clear', 'remove',
               'discard', '__setitem__'}
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for n in ast.walk(node):
                bad = isinstance(n, ast.Global)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in MUT:
                    b = n.func.value
                    while isinstance(b, (ast.Attribute, ast.Subscript)):
                        b = b.value
                    bad = bad or (isinstance(b, ast.Name) and b.id in glob)
                if isinstance(n, (ast.Assign, ast.AugAssign)):
                    for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                        if isinstance(t, (ast.Subscript, ast.Attribute)):
                            b = t.value
                            while isinstance(b, (ast.Attribute, ast.Subscript)):
                                b = b.value
                            bad = bad or (isinstance(b, ast.Name) and b.id in glob)
                if bad:
                    impure.add(node.name)
                    break
        self.mod_ids[f] = (top, impure, f)
        return self.mod_ids[f]

    def rule_id(self, rule):
        """the rule's code: its function and, transitively, every top-level definition of its module it names
        (prepare.<name> for prepare.py's helpers), plus tools/rules/_*.py. None for an impure rule (its closure
        writes module or prepare state that a POST hook may read): it always runs."""
        code = getattr(rule, '__code__', None)
        if code is None:   # not a plain function: no closure to read, so it always runs
            return None
        key = (code.co_filename, rule.__name__)
        if key in self.rule_ids:
            return self.rule_ids[key]
        top, impure, f = self._module(code.co_filename)
        ptop, pimpure, _ = self._module(__file__)
        if rule.__name__ not in top:   # defined in a way the closure cannot follow: always runs
            self.rule_ids[key] = None
            return None
        seen, segs, todo, bad = set(), [], [('m', rule.__name__)], False
        while todo:
            where, n = todo.pop()
            if (where, n) in seen:
                continue
            seen.add((where, n))
            t, imp = (top, impure) if where == 'm' else (ptop, pimpure)
            if n not in t:
                continue
            bad = bad or n in imp
            seg, names = t[n]
            segs.append('%s:%s\n%s' % (where, n, seg))
            todo += [('m', x) for x in names if x in t] + [('p', x) for x in names if x in ptop and where == 'p' or
                                                           (where == 'm' and x in ptop and x not in t)]
        rid = None if bad else self.h.sha1((self.base + '\0'.join(sorted(segs))).encode('utf-8', 'surrogateescape')).hexdigest()[:20] + ':' + rule.__name__
        self.rule_ids[key] = rid
        return rid

    def tsha(self, text):
        return self.h.sha1(text.encode('utf-8', 'surrogateescape')).hexdigest()

    def blob(self, k):
        return os.path.join(self.d, 'blobs', k[:2], k)

    def apply(self, rule, path, text, in_sha, stats):
        """-> (text, sha, changed stat keys)"""
        rid = self.rule_id(rule)
        if rid is None:     # impure: runs every time (its side effects feed the POST hooks)
            before = Counter(stats)
            out = rule(path, text, stats)
            self.impure_runs += 1
            delta = [k for k in stats if stats[k] != before[k]]
            return out, (self.tsha(out) if out is not text else in_sha), delta
        key = (rid, path, in_sha)
        got = self.map.get(key) if self.valid else None
        if got is not None and not self.verify:
            out_sha, delta = got
            if out_sha != in_sha:
                try:
                    text = open(self.blob(out_sha), encoding='utf-8', errors='surrogateescape', newline='').read()
                except OSError:
                    got = None
            if got is not None:
                for k, v in delta:
                    stats[k] += v
                self.used[key] = got
                if out_sha != in_sha:
                    self.blobs_used.add(out_sha)
                self.hits += 1
                return text, out_sha, [k for k, v in delta if v]
        before = Counter(stats)
        out = rule(path, text, stats)
        self.runs += 1
        delta = tuple(sorted((k, stats[k] - before[k]) for k in stats if stats[k] != before[k]))
        out_sha = self.tsha(out) if out is not text else in_sha
        if self.verify and got is not None and tuple(got) != (out_sha, delta):
            self.mismatch.append('%s %s' % (key[0], path))
        if out_sha != in_sha:
            b = self.blob(out_sha)
            if not os.path.isfile(b):
                os.makedirs(os.path.dirname(b), exist_ok=True)
                with open(b + '.tmp', 'w', encoding='utf-8', errors='surrogateescape', newline='') as f:
                    f.write(out)
                os.replace(b + '.tmp', b)
            self.blobs_used.add(out_sha)
        self.used[key] = (out_sha, delta)
        return out, out_sha, [k for k, v in delta if v]

    def close(self):
        files = dict(self.ctx_files)
        for p in self.read:
            if p not in files:
                files[p] = self._fh(p)
        dirs = dict(self.ctx_dirs)
        for p in self.listed:
            if p not in dirs:
                dirs[p] = self._names(p)
        files = {p: h for p, h in files.items() if h is not None}
        dirs = {p: h for p, h in dirs.items() if h is not None}
        for name, obj in (('map.marshal', self.used), ('ctx.marshal', {'env': self.env, 'files': files, 'dirs': dirs})):
            with open(os.path.join(self.d, name + '.tmp'), 'wb') as f:
                self.marshal.dump(obj, f)
            os.replace(os.path.join(self.d, name + '.tmp'), os.path.join(self.d, name))
        bd = os.path.join(self.d, 'blobs')
        for sub in os.listdir(bd):
            for n in os.listdir(os.path.join(bd, sub)):
                if n not in self.blobs_used:
                    os.remove(os.path.join(bd, sub, n))
        imp = sorted({n for (m, n), r in self.rule_ids.items() if r is None})
        print('prepare memo: %s; %d rule results reused, %d ran, %d impure runs (%s)%s' % (
            'used' if self.valid else 'not used (the context changed or no memo yet)', self.hits, self.runs,
            self.impure_runs, ', '.join(imp),
            '; VERIFY: %d differ' % len(self.mismatch) if self.verify else ''), file=sys.stderr)
        for m in self.mismatch[:20]:
            print('prepare memo: VERIFY MISMATCH %s' % m, file=sys.stderr)


def main():
    src_root, out_root = sys.argv[1], sys.argv[2]
    rules = RULES + load_stream_rules()
    md = os.environ.get('FNVNX_PREPARE_MEMO', '')
    memo = Memo(md, src_root, out_root) if md and md not in ('0', 'none', 'off') else None
    # FNVNX_PREPARE_SKIP="r_a,r_b": leave those rule functions out (to measure what a rule changes)
    skip = set(filter(None, os.environ.get('FNVNX_PREPARE_SKIP', '').split(',')))
    rules = [r for r in rules if r.__name__ not in skip]
    stats, files = Counter(), Counter()
    for top in ('src', 'include'):
        for dirpath, _, names in os.walk(os.path.join(src_root, top)):
            rel = os.path.relpath(dirpath, src_root)
            os.makedirs(os.path.join(out_root, rel), exist_ok=True)
            for name in names:
                s, d = os.path.join(dirpath, name), os.path.join(out_root, rel, name)
                if not name.endswith(('.cpp', '.h', '.hpp', '.inl', '.c')):
                    shutil.copyfile(s, d); continue
                with open(s, encoding='utf-8', errors='surrogateescape') as f:
                    text = orig = f.read()
                if memo is not None:
                    sha = memo.tsha(text)
                    for rule in rules:
                        text, sha, changed = memo.apply(rule, s, text, sha, stats)
                        for k in changed: files[k] += 1
                else:
                    for rule in rules:
                        before = Counter(stats)
                        text = rule(s, text, stats)
                        for k in stats:
                            if stats[k] != before[k]: files[k] += 1
                with open(d, 'w', encoding='utf-8', errors='surrogateescape') as f:
                    f.write(text)
    shutil.copytree(os.path.join(src_root, 'build'), os.path.join(out_root, 'build'), dirs_exist_ok=True)
    # third_party/ (zlib 1.2.1: C units of units.json, compiled as C) is copied as it is: no rule edits it
    if os.path.isdir(os.path.join(src_root, 'third_party')):
        shutil.copytree(os.path.join(src_root, 'third_party'), os.path.join(out_root, 'third_party'), dirs_exist_ok=True)
    drop_native_units(out_root, stats, files)
    for hook in STREAM_POST:
        hook(out_root, stats, files)
    with open(os.path.join(out_root, 'prepare_stats.tsv'), 'w') as f:
        for k in sorted(stats):
            f.write('%s\t%d files\t%d edits\n' % (k, files[k], stats[k]))
    print(open(os.path.join(out_root, 'prepare_stats.tsv')).read(), end='')
    if memo is not None:
        memo.close()
        if memo.mismatch:
            sys.exit(3)

if __name__ == '__main__':
    main()
