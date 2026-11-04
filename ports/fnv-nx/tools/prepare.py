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

def r_sse_local_types(path, text, stats):
    """SSE1: drop local copies of the MSVC <xmmintrin.h> vector unions (or structs); the global sse2neon type is used
    instead."""
    out, pos, lanes = [], 0, {}
    for m in INTRIN_UNION.finditer(text):
        if m.start() < pos: continue
        end = match_brace(text, m.end() - 1)
        for fields, name in NAMED_LANES.findall(text[m.end():end - 1]):     # `struct { float x, y, z, w; } lanes;`
            decl = [(ty, f) for ty, fs in LANE_DECL.findall(fields) for f in re.split(r'\s*,\s*', fs.strip())]
            if len(decl) == 4 and all(LANE_ELEM.get(re.sub(r'\s+', ' ', ty)) for ty, _ in decl):
                lanes[name] = {f: (LANE_ELEM[re.sub(r'\s+', ' ', ty)], i) for i, (ty, f) in enumerate(decl)}
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
    '': '_alloca _ReadWriteBarrier _ReadBarrier _WriteBarrier _CxxThrowException operator_delete_sized '
          '_operator_delete_sized',   # in the force-included fnvnx_compat.h
}
CRT_NAMES = [n for v in CRT_HEADERS.values() for n in v.split()]
# stdio functions S4b implements on MSVC's struct _iobuf (platform/crt/fnvnx_iobuf.h): a decomp prototype of one of
# these that takes or returns _iobuf* is replaced by that header (its C++ overloads take _iobuf*)
IOBUF_NAMES = set('''_fclose_nolock _fdopen _fileno _fseeki64 _fsopen _ftelli64 clearerr fclose feof ferror fflush fgetc
    fgetpos fgets fopen_s fprintf fputc fputs fread fseek fsetpos ftell fwrite getc putc rewind setvbuf ungetc vfprintf
    __iob_func'''.split())
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
    spans, out, pos, have, top = None, [], 0, set(), []
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
                and not cxx_copy_is_crt(path, name)): continue
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
            hdr = '"fnvnx_iobuf.h"'
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
    return ''.join('#include %s /* fnv-nx DECL1 */\n' % h for h in top) + ''.join(out)

NEWDEL_PARAMS = re.compile(r'(\boperator\s+(?:new|delete)(?:\s*\[\s*\])?\s*\()([^;{}()]*)(\))')

def r_newdel_size(path, text, stats):
    """NEW1: operator new/delete size parameters spelled 'unsigned int' (MSVC x86 size_t) -> size_t. A parameter
    passed by reference or pointer (`unsigned int& total`, an out parameter of a class operator new) is the caller's
    unsigned int and keeps its type."""
    def sub(m):
        params = re.sub(r'\bunsigned\s+int\b(?!\s*[&*])', 'size_t', m.group(2))
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
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'platform', 'crt', 'crt_symbols.tsv')
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
         r_platform_protos, r_newdel_size]

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

def main():
    src_root, out_root = sys.argv[1], sys.argv[2]
    rules = RULES + load_stream_rules()
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
                for rule in rules:
                    before = Counter(stats)
                    text = rule(s, text, stats)
                    for k in stats:
                        if stats[k] != before[k]: files[k] += 1
                with open(d, 'w', encoding='utf-8', errors='surrogateescape') as f:
                    f.write(text)
    shutil.copytree(os.path.join(src_root, 'build'), os.path.join(out_root, 'build'), dirs_exist_ok=True)
    drop_native_units(out_root, stats, files)
    for hook in STREAM_POST:
        hook(out_root, stats, files)
    with open(os.path.join(out_root, 'prepare_stats.tsv'), 'w') as f:
        for k in sorted(stats):
            f.write('%s\t%d files\t%d edits\n' % (k, files[k], stats[k]))
    print(open(os.path.join(out_root, 'prepare_stats.tsv')).read(), end='')

if __name__ == '__main__':
    main()
