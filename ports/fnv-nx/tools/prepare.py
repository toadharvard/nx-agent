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

INTRIN_UNION = re.compile(r'(?:typedef\s+)?union\s+(?:__declspec\([^)]*\(?[^)]*\)?\)\s*)*(__m128[id]?|__m64)\b[^;{]*\{')

def r_sse_local_types(path, text, stats):
    """SSE1: drop local copies of the MSVC <xmmintrin.h> vector unions; the global sse2neon type is used instead."""
    out, pos = [], 0
    for m in INTRIN_UNION.finditer(text):
        if m.start() < pos: continue
        end = match_brace(text, m.end() - 1)
        tail = re.match(r'\s*(?:__m128[id]?|__m64)?\s*;', text[end:])
        if tail: end += tail.end()
        out.append(text[pos:m.start()]); out.append('/* fnv-nx SSE1: local %s removed */' % m.group(1)); pos = end
        stats['SSE1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# one prototype; several may share a line (`extern"C"__m128 _mm_load_ss(float const*_A);extern"C"void ...`)
MM_PROTO = re.compile(r'(?:^|(?<=[;{}]))[ \t]*(?:extern\s*(?:"C"\s*)?)?(?:static\s+)?(?:inline\s+)?(?:const\s+)?(?:::)?(?:\w+::)*'
                      r'(?:__m128[id]?|__m64|float|double|int|unsigned\s+int|void)\s+(?:__cdecl\s+)?'
                      r'_mm_\w+\s*\([^;{)]*\)\s*;[ \t]*', re.M)
EXTERN_C_BLOCK = re.compile(r'extern\s+"C"\s*\{')

def r_sse_local_protos(path, text, stats):
    """SSE2: drop local prototypes of _mm_* intrinsics (and extern "C" blocks left empty by that)."""
    text, n = MM_PROTO.subn('', text)
    stats['SSE2'] += n
    out, pos = [], 0
    for m in EXTERN_C_BLOCK.finditer(text):
        if m.start() < pos: continue
        end = match_brace(text, m.end() - 1)
        if text[m.end():end - 1].strip() == '':
            out.append(text[pos:m.start()]); pos = end; stats['SSE2'] += 1
    out.append(text[pos:])
    return ''.join(out)

M128_MEMBER = re.compile(r'\s*(\.|->)\s*(m128i?)_(f32|f64|u64|u32|u16|u8|i64|i32|i16|i8)\b')
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
PAREN_SSE = re.compile(r'\(\s*(_mm_\w+)\s*\)\s*\(')
USING_SSE = re.compile(r'\busing\s+(?:::)?(?:\w+::)+(?:__m128[id]?|__m64|_mm_\w+)\s*;')

def r_sse_qualified(path, text, stats):
    """SSE4: N::__m128 / N::_mm_mul_ps (qualified names of the removed local copies) -> the global names;
    `using N::_mm_mul_ps;` declarations of them are dropped (the global names are visible everywhere)."""
    text, n = USING_SSE.subn('', text)
    stats['SSE4'] += n
    text, n = QUALIFIED_SSE.subn('', text)
    stats['SSE4'] += n
    text, n = GLOBAL_SSE.subn('', text)       # ::_mm_shuffle_ps(...) - sse2neon defines some as macros
    stats['SSE4'] += n
    text, n = PAREN_SSE.subn(r'\1(', text)    # (_mm_shuffle_ps)(a, b, i) suppresses the macro
    stats['SSE4'] += n
    return text

# ---- scopes: which kind of brace a position is inside (namespace / class / extern / function / other)

# comments and preprocessor lines before a '{' say nothing about it ('// end namespace X', '#pragma pack(push, 4)')
HEAD_NOISE = re.compile(r'//[^\n]*|/\*.*?\*/|^[ \t]*#[^\n]*', re.S | re.M)
# parenthesized decorations of a class head: __declspec(align(16)), __attribute__((x)), FNV_ALIGN(16), alignas(16)
HEAD_DECOR = re.compile(r'\b(?:__declspec|__attribute__|alignas|[A-Z][A-Z0-9_]*ALIGN[A-Z0-9_]*)\s*'
                        r'\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)')
TEMPLATE_PREFIX = re.compile(r'^\s*template\s*<(?:[^<>]|<(?:[^<>]|<[^<>]*>)*>)*>\s*')

def head_kind(head):
    """Kind of the brace that follows `head` (the text just before it) and the fragment that introduced it:
    namespace / extern / enum / class / init / function / other."""
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
            kind, h = head_kind(text[max(0, i - 300):i])
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
                     'strncpy_s sprintf_s vsprintf_s memcpy_s memmove_s strtok_s _splitpath_s _strlwr_s',
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

PLATFORM = None
PROTO = re.compile(r'^[ \t]*(?:extern\s+"C"\s+)?(?:__declspec\(\w+\)\s+)?(?:static\s+)?(?:inline\s+)?'
                   r'[A-Za-z_][\w \t\*&:<>,]*?[\s\*&](?:(?:__cdecl|__stdcall|__fastcall|WINAPI|__declspec\(\w+\))\s*)*'
                   r'(\w+|operator\s+(?:new|delete)(?:\s*\[\s*\])?)\s*\([^;{}]*\)\s*(?:throw\s*\([^)]*\)\s*)?;[ \t]*$',
                   re.M)

def r_platform_protos(path, text, stats):
    """DECL1: drop the decomp's own prototypes of CRT / Win32 functions (and namespace-scope operator new/delete)
    outside classes; the platform headers declare them with the target's types (size_t is unsigned long on
    AArch64 ILP32, unsigned int on MSVC x86). The header that declares the function on the target takes the place
    of the first such prototype at file scope (same line, so line numbers are kept), or goes on top of the file
    when the prototype was inside a namespace or extern block. A prototype that declares a different entity is
    kept: C++ linkage inside a namespace other than ::std (N::fmod is not ::fmod)."""
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
        if not is_op and spaces and spaces != ['namespace std'] and not c_linkage: continue
        if is_op:   # the usual forms are declared implicitly in every unit; placement forms come from <new>
            params = re.search(r'\(([^;{}]*)\)', m.group(0)[m.group(0).index('operator'):]).group(1)
            hdr = new_header(text) if PLACEMENT_PARAMS.match(params) else ''
        elif name in IOBUF_NAMES and '_iobuf' in m.group(0):
            hdr = '"fnvnx_iobuf.h"'
        else:
            hdr = PLATFORM[name]
        nl = '\n' * m.group(0).count('\n')
        out.append(text[pos:m.start()])
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
    """NEW1: operator new/delete size parameters spelled 'unsigned int' (MSVC x86 size_t) -> size_t."""
    def sub(m):
        params = re.sub(r'\bunsigned\s+int\b', 'size_t', m.group(2))
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
    names = game_bound_names(path).get(os.path.normpath(os.path.abspath(path)))
    if not names: return text
    spans = scope_spans(text)
    out, pos = [], 0
    for m in re.finditer(r'(?<![\w.>:~])(%s)\b' % '|'.join(sorted(names)), text):
        chain = scope_chain(spans, m.start())
        if chain and chain[-1][0] == 'class': continue               # a member of that name
        out.append(text[pos:m.start()]); out.append(GAME_PREFIX + m.group(1)); pos = m.end()
        stats['GAME1'] += 1
    out.append(text[pos:])
    return ''.join(out)

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
    return rules

def drop_native_units(out_root, stats, files):
    """NATIVE1: units listed in render/native/drop_units.txt (one source path per line; D6, the native renderer
    replaces them) leave the build list (<out>/build/units.json, which census.sh and the link step read)."""
    import json
    lst = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'render', 'native', 'drop_units.txt')
    drop = set()
    if os.path.exists(lst):
        drop = {l.strip() for l in open(lst) if l.strip() and not l.startswith('#')}
    stats['NATIVE1'] += 0
    if not drop: return
    p = os.path.join(out_root, 'build', 'units.json')
    data = json.load(open(p))
    keep = [u for u in data['units'] if u.get('source') not in drop]
    stats['NATIVE1'] += len(data['units']) - len(keep)
    files['NATIVE1'] += len({u.get('source') for u in data['units']} & drop)
    data['units'] = keep
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
    with open(os.path.join(out_root, 'prepare_stats.tsv'), 'w') as f:
        for k in sorted(stats):
            f.write('%s\t%d files\t%d edits\n' % (k, files[k], stats[k]))
    print(open(os.path.join(out_root, 'prepare_stats.tsv')).read(), end='')

if __name__ == '__main__':
    main()
