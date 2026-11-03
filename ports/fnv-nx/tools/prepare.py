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

MM_PROTO = re.compile(r'^[ \t]*(?:extern\s+"C"\s+)?(?:static\s+)?(?:inline\s+)?(?:const\s+)?'
                      r'(?:__m128[id]?|__m64|float|double|int|unsigned\s+int|void)\s+_mm_\w+\s*\([^;{)]*\)\s*;[ \t]*$', re.M)
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

M128_MEMBER = re.compile(r'((?:\b\w+|\))(?:\s*(?:\.|->)\s*\w+|\[[^\]\[]*\])*)\s*\.\s*(m128i?)_(f32|f64|u64|u32|u16|u8|i64|i32|i16|i8)\s*\[')
ELEM = {'f32': 'float', 'f64': 'double', 'u64': 'unsigned long long', 'u32': 'unsigned int', 'u16': 'unsigned short',
        'u8': 'unsigned char', 'i64': 'long long', 'i32': 'int', 'i16': 'short', 'i8': 'signed char'}

def r_sse_members(path, text, stats):
    """SSE3: MSVC union members (v.m128_f32[i]) -> element access through a typed pointer (lvalue-safe)."""
    def sub(m):
        stats['SSE3'] += 1
        return '((%s*)&(%s))[' % (ELEM[m.group(3)], m.group(1))
    return M128_MEMBER.sub(sub, text)

QUALIFIED_SSE = re.compile(r'\b(?:\w+::)+(?=(?:__m128[id]?|__m64|_mm_\w+)\b)')

def r_sse_qualified(path, text, stats):
    """SSE4: N::__m128 / N::_mm_mul_ps (qualified names of the removed local copies) -> the global names."""
    text, n = QUALIFIED_SSE.subn('', text)
    stats['SSE4'] += n
    return text

# ---- scopes: which kind of brace a position is inside (namespace / class / extern / function / other)

SCOPE_HEAD = re.compile(r'(\bnamespace\b[^;{}()]*|\b(?:class|struct|union)\b[^;{}()]*|extern\s+"C\+?\+?"\s*'
                        r'|\)[^;{}]*|=\s*|\benum\b[^;{}]*)$')

def scope_map(text):
    """Sorted list of (open, close, kind) for every brace pair.
    kind: namespace / class / extern / function / init / enum / other."""
    spans, stack, i, n = [], [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"' or c == "'":
            q = c; i += 1
            while i < n and text[i] != q:
                i += 2 if text[i] == '\\' else 1
        elif text.startswith('//', i):
            j = text.find('\n', i); i = n if j < 0 else j
        elif text.startswith('/*', i):
            j = text.find('*/', i); i = n if j < 0 else j + 1
        elif c == '{':
            head = text[max(0, i - 300):i].rstrip()
            m = SCOPE_HEAD.search(head)
            kind = 'other'
            if m:
                h = m.group(1)
                kind = ('namespace' if h.startswith('namespace') else 'extern' if h.startswith('extern') else
                        'function' if h.startswith(')') else 'init' if h.startswith('=') else
                        'enum' if h.startswith('enum') else 'class')
            stack.append((i, kind))
        elif c == '}':
            if stack:
                o, k = stack.pop(); spans.append((o, i, k))
        i += 1
    spans.sort()
    return spans

def innermost(spans, pos):
    kind = 'file'
    for o, c, k in spans:
        if o > pos: break
        if o < pos < c: kind = k
    return kind

CRT_NAMES = '''memset memcpy memmove memcmp memchr strlen strcmp strncmp strcpy strncpy strcat strncat strchr strrchr strstr
    _stricmp _strnicmp stricmp strnicmp _strdup strtok sprintf _snprintf vsprintf _vsnprintf sscanf printf fprintf atoi
    atof atol strtol strtoul strtod malloc free calloc realloc _msize abs labs fabs fabsf sqrt sqrtf sin sinf cos cosf
    tan tanf asin asinf acos acosf atan atanf atan2 atan2f exp expf log logf log10 log10f pow powf floor floorf ceil
    ceilf fmod fmodf modf frexp ldexp qsort bsearch rand srand toupper tolower isspace isdigit isalpha isalnum isupper
    islower exit abort'''.split()

def platform_names():
    names = set(CRT_NAMES)
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shim', 'include', 'fnvnx_win32.h')
    for line in open(here):
        m = re.match(r'^[A-Za-z_][\w \*]*?\b([A-Z_][A-Za-z0-9_]+)\(', line)
        if m: names.add(m.group(1))
    return names

PLATFORM = None
PROTO = re.compile(r'^[ \t]*(?:extern\s+"C"\s+)?(?:__declspec\(\w+\)\s+)?(?:static\s+)?(?:inline\s+)?'
                   r'[A-Za-z_][\w \t\*&:<>,]*?[\s\*&](?:__cdecl\s+|__stdcall\s+|__fastcall\s+|WINAPI\s+)?'
                   r'(\w+|operator\s+(?:new|delete)(?:\s*\[\s*\])?)\s*\([^;{}]*\)\s*(?:throw\s*\([^)]*\)\s*)?;[ \t]*$',
                   re.M)

def r_platform_protos(path, text, stats):
    """DECL1: drop the decomp's own prototypes of CRT / Win32 functions (and namespace-scope operator new/delete)
    outside classes; the platform headers declare them with the target's types (size_t is unsigned long on
    AArch64 ILP32, unsigned int on MSVC x86)."""
    global PLATFORM
    if PLATFORM is None: PLATFORM = platform_names()
    spans, out, pos = None, [], 0
    for m in PROTO.finditer(text):
        name = re.sub(r'\s+', ' ', m.group(1))
        if not name.startswith('operator') and name not in PLATFORM: continue
        if spans is None: spans = scope_map(text)
        if innermost(spans, m.start()) not in ('file', 'namespace', 'extern'): continue
        out.append(text[pos:m.start()]); out.append('/* fnv-nx DECL1 */'); pos = m.end()
        stats['DECL1'] += 1
    out.append(text[pos:])
    return ''.join(out)

NEWDEL_PARAMS = re.compile(r'(\boperator\s+(?:new|delete)(?:\s*\[\s*\])?\s*\()([^;{}()]*)(\))')

def r_newdel_size(path, text, stats):
    """NEW1: operator new/delete size parameters spelled 'unsigned int' (MSVC x86 size_t) -> size_t."""
    def sub(m):
        params = re.sub(r'\bunsigned\s+int\b', 'size_t', m.group(2))
        if params != m.group(2): stats['NEW1'] += 1
        return m.group(1) + params + m.group(3)
    return NEWDEL_PARAMS.sub(sub, text)

RULES = [r_sse_local_types, r_sse_local_protos, r_sse_members, r_sse_qualified, r_platform_protos, r_newdel_size]

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

def main():
    src_root, out_root = sys.argv[1], sys.argv[2]
    rules = RULES + load_stream_rules()
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
    with open(os.path.join(out_root, 'prepare_stats.tsv'), 'w') as f:
        for k in sorted(stats):
            f.write('%s\t%d files\t%d edits\n' % (k, files[k], stats[k]))
    print(open(os.path.join(out_root, 'prepare_stats.tsv')).read(), end='')

if __name__ == '__main__':
    main()
