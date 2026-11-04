"""s1_compile.py - prepare rules of stream S1-compile (make every unit parse for AArch64 ILP32 with GCC).

Each rule is fn(path, text, stats) -> text (see tools/rules/README.md); ids are cited in the journal.
"""
import os
import re
import sys

import prepare

# ---------------------------------------------------------------------------------------------------------- IFX1
#
# MSVC's __if_exists (name) { ... } / __if_not_exists (name) { ... } keep or drop their body depending on whether the
# name is declared at that point. GCC has no equivalent, so prepare decides each one statically from what the unit
# has seen up to there: the unit's own text before the site and the local headers it included before the site.
# In code MSVC accepted, every name that appears before a point is declared there, so "appears before the site"
# is the test for plain names; A::B needs a definition of class A with B in it or in one of its bases (A::A, the
# injected class name, only needs the definition). Names the target always declares (sse2neon) exist; Win32 names
# exist where a Win32 shim header was included.

IFX_HEAD = re.compile(r'\b__if_(not_)?exists\s*\(\s*([^()]*?)\s*\)\s*\{')
INCLUDE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*([<"])([^>"]+)[>"]', re.M)
COMMENT = re.compile(r'//[^\n]*|/\*.*?\*/', re.S)
TARGET_NAMES = re.compile(r'^(?:__m128[id]?|__m64|_mm_\w+|_MM_\w+|_ReadWriteBarrier|_ReadBarrier|_WriteBarrier)$')
WIN32_HEADERS = {'windows.h', 'intrin.h', 'commctrl.h', 'wingdi.h', 'd3d9.h', 'fnvnx_win32.h'}

_header_cache = {}

def _roots(path):
    p = os.path.abspath(path)
    for marker in ('/src/', '/include/'):
        i = p.rfind(marker)
        if i >= 0: return p[:i]
    return os.path.dirname(p)

def _resolve(inc, from_dir, root):
    for d in (from_dir, os.path.join(root, 'include'), os.path.join(root, 'src')):
        f = os.path.normpath(os.path.join(d, inc))
        if os.path.isfile(f): return f
    return None

def _strip_closed(text, keep):
    """`text` without the contents of its closed brace blocks, except blocks of the kinds in `keep`: what is
    still in scope after the text (names declared in a closed function or named namespace are not)."""
    out, pos = [], 0
    for o, c, k, h in prepare.scope_spans(text):
        if o < pos: continue
        if k in keep or (k == 'namespace' and h == 'namespace'): continue
        out.append(text[pos:o + 1]); pos = c
    out.append(text[pos:])
    return ''.join(out)

CLASS_KEEP = ('class', 'extern', 'enum')     # class lookups need class bodies
NAME_KEEP = ('extern', 'enum')               # plain names: members of closed classes are not in scope

def _visible(prefix, path, root, seen, parts, sys_headers):
    """(class view, name view) of `prefix` and of every local header it includes (transitively, each once)."""
    for m in INCLUDE.finditer(prefix):
        inc = m.group(2)
        if m.group(1) == '<' or os.path.basename(inc) in WIN32_HEADERS:
            sys_headers.add(os.path.basename(inc))
            if m.group(1) == '<': continue
        f = _resolve(inc, os.path.dirname(path), root)
        if not f or f in seen: continue
        seen.add(f)
        if f not in _header_cache:
            with open(f, encoding='utf-8', errors='surrogateescape') as h: t = COMMENT.sub(' ', h.read())
            sub, sys_sub = [], set()
            _visible(t, f, root, set(seen), sub, sys_sub)
            _header_cache[f] = (sub, sys_sub)
        parts += _header_cache[f][0]; sys_headers |= _header_cache[f][1]
    parts.append((_strip_closed(prefix, CLASS_KEEP), _strip_closed(prefix, NAME_KEEP)))

CLASS_DEF = r'\b(?:class|struct|union)\s+(?:__declspec\s*\((?:[^()]|\([^()]*\))*\)\s*)*%s\s*(?:final\s*)?(:[^;{}]*)?\{'

def _class_bodies(text, name):
    out = []
    for m in re.finditer(CLASS_DEF % re.escape(name), text):
        end = prepare.match_brace(text, m.end() - 1)
        bases = [re.split(r'[\s:]+', b.strip())[-1] for b in (m.group(1) or ':')[1:].split(',') if b.strip()]
        bases = [re.sub(r'<.*', '', b) for b in bases]
        out.append((text[m.end():end - 1], bases))
    return out

def _has_member(text, cls, member, depth=0):
    """Class `cls` is defined in `text` and has `member` (itself or through a base). A::A and A::~A exist for
    every defined class (injected class name, implicit destructor)."""
    if depth > 8: return False
    for body, bases in _class_bodies(text, cls):
        if member in (cls, '~' + cls): return True
        if re.search(r'(?<![\w~])%s\b' % re.escape(member), body): return True
        if any(_has_member(text, b, member, depth + 1) for b in bases if b): return True
    return False

def _exists(name, class_view, name_view, sys_headers):
    parts = [re.sub(r'\s+', '', p) for p in name.strip().lstrip(':').split('::')]
    if len(parts) == 1:
        n = parts[0]
        if TARGET_NAMES.match(n): return True
        if prepare.PLATFORM is None: prepare.PLATFORM = prepare.platform_names()
        if prepare.PLATFORM.get(n) == '"fnvnx_win32.h"' and sys_headers & WIN32_HEADERS: return True
        return re.search(r'(?<![\w.>:~])%s\b' % re.escape(n), name_view) is not None
    return _has_member(class_view, parts[-2], parts[-1])

def r_if_exists(path, text, stats):
    """IFX1: resolve __if_exists / __if_not_exists statically (see above); the kept body replaces the construct,
    line breaks are preserved."""
    if '__if_' not in text: return text
    root, pos = _roots(path), 0
    while True:
        m = IFX_HEAD.search(text, pos)
        if not m: break
        end = prepare.match_brace(text, m.end() - 1)
        parts, sys_headers = [], set()
        _visible(COMMENT.sub(' ', text[:m.start()]), path, root, {os.path.abspath(path)}, parts, sys_headers)
        class_view = '\n'.join(p[0] for p in parts)
        name_view = '\n'.join(p[1] for p in parts)
        keep = _exists(m.group(2), class_view, name_view, sys_headers) != bool(m.group(1))
        head, body = text[m.start():m.end()], text[m.end():end - 1]
        nl = lambda s: '\n' * s.count('\n')
        if keep:
            repl = '/* fnv-nx IFX1 */' + nl(head) + body + ' '
        else:
            repl = '/* fnv-nx IFX1 */' + nl(head) + nl(body)
        text = text[:m.start()] + repl + text[end:]
        pos = m.start() + 1
        stats['IFX1'] += 1
    return text

# ---------------------------------------------------------------------------------------------------------- SAL1

SAL_ATTR = re.compile(r'\[\s*(?:returnvalue\s*:\s*)?SA_(?:Pre|Post|FormatString)\s*\([^\]]*\)\s*\]')

# the attributes of sourceannotations.h's own attribute classes (`[repeatable][source_annotation_attribute(Parameter)]
# struct PreAttribute`), pasted with it
SAL_CLASS_ATTR = re.compile(r'^[ \t]*(?:\[\s*(?:repeatable|source_annotation_attribute\s*\([^\]\n]*\))\s*\][ \t]*\n?[ \t]*)+', re.M)

def r_sal_attributes(path, text, stats):
    """SAL1: drop MSVC attribute-syntax SAL annotations ([SA_Pre(Null=SA_No)] in pasted CRT prototypes, and the
    attributes of the annotation classes themselves); they carry no semantics for the compiler."""
    text, n = SAL_ATTR.subn('', text)
    stats['SAL1'] += n
    if '[repeatable]' in text or 'source_annotation_attribute' in text:
        text, n = SAL_CLASS_ATTR.subn(lambda m: '\n' * m.group(0).count('\n'), text)
        stats['SAL1'] += n
    return text

# ---------------------------------------------------------------------------------------------------------- DECL2

SDK_TAGS = ('tagRECT tagPOINT tagSIZE _FILETIME _SYSTEMTIME _GUID _RTL_CRITICAL_SECTION _LARGE_INTEGER _ULARGE_INTEGER '
            '_OVERLAPPED _WIN32_FIND_DATAA _SYSTEM_INFO tagMSG tagPALETTEENTRY _LUID _RGNDATA tagBITMAPFILEHEADER '
            'tagBITMAPINFOHEADER _iobuf _MEMORYSTATUS CRITICAL_SECTION').split()   # CRITICAL_SECTION: a struct copy of the PC type
SDK_TYPEDEFS = {n: '"fnvnx_win32.h"' for n in '''DWORD BOOL BYTE WORD LONG UINT HANDLE HRESULT LPVOID ULONG_PTR
    DWORD_PTR WPARAM LPARAM LRESULT HINSTANCE HMODULE HWND HHOOK HKEY HDC HICON HMENU HBRUSH HFONT HBITMAP HCURSOR
    UINT_PTR LONG_PTR INT_PTR ATOM COLORREF LPSTR LPCSTR'''.split()}
SDK_TYPEDEFS.update({'size_t': '<stddef.h>', 'ptrdiff_t': '<stddef.h>', 'va_list': '<stdarg.h>'})
SDK_STRUCT = re.compile(r'^[ \t]*(typedef\s+)?(?:struct|class|union)\s+(%s)\s*\{' % '|'.join(SDK_TAGS), re.M)
SDK_TYPEDEF = re.compile(r'^[ \t]*typedef\s+[\w \t\*]+?[\s\*](%s)\s*;[ \t]*$' % '|'.join(SDK_TYPEDEFS), re.M)
SDK_CALLBACKS = 'HOOKPROC WNDPROC WNDENUMPROC FARPROC LPTHREAD_START_ROUTINE'.split()
SDK_FNTYPEDEF = re.compile(r'^[ \t]*typedef\s+[\w \t\*]+?\(\s*(?:__stdcall\s*|CALLBACK\s*|WINAPI\s*)?\*\s*(%s)\s*\)'
                           r'\s*\([^;{}]*\)\s*;[ \t]*$' % '|'.join(SDK_CALLBACKS), re.M)
SDK_DECLARATORS = re.compile(r'^[\s\w\*,]*$')

def r_sdk_types(path, text, stats):
    """DECL2: drop the decomp's own copies of Windows SDK / CRT types at file scope (struct tagRECT {...};,
    typedef unsigned int size_t;, typedef unsigned int DWORD;); the platform header that defines them with the
    SDK's members and the target's types takes the place of the first one (or goes on top of the file)."""
    if not any(k in text for k in ('typedef', 'struct', 'class', 'union')): return text
    found = []
    for m in SDK_STRUCT.finditer(text):
        end = prepare.match_brace(text, m.end() - 1)
        tail = re.match(r'([^;{}]*);[ \t]*', text[end:])
        if not tail or not SDK_DECLARATORS.match(tail.group(1)): continue
        found.append((m.start(), end + tail.end(), '"fnvnx_iobuf.h"' if m.group(2) == '_iobuf' else '"fnvnx_win32.h"'))
    for m in SDK_TYPEDEF.finditer(text):
        found.append((m.start(), m.end(), SDK_TYPEDEFS[m.group(1)]))
    for m in SDK_FNTYPEDEF.finditer(text):
        found.append((m.start(), m.end(), '"fnvnx_win32.h"'))
    if not found: return text
    spans, out, pos, have, top = prepare.scope_spans(text), [], 0, set(), []
    for s, e, hdr in sorted(found):
        if s < pos: continue
        chain = prepare.scope_chain(spans, s)
        if any(k != 'extern' for k, _ in chain): continue
        nl = '\n' * text.count('\n', s, e)
        out.append(text[pos:s])
        if not chain and hdr not in have:
            out.append('#include %s /* fnv-nx DECL2 */%s' % (hdr, nl)); have.add(hdr)
        else:
            out.append('/* fnv-nx DECL2 */' + nl)
            if hdr not in have: have.add(hdr); top.append(hdr)
        pos = e
        stats['DECL2'] += 1
    out.append(text[pos:])
    return ''.join('#include %s /* fnv-nx DECL2 */\n' % h for h in top) + ''.join(out)

# ---------------------------------------------------------------------------------------------------------- EXC1

DYN_EXC_SPEC = re.compile(r'(\)\s*(?:const\s*)?(?:volatile\s*)?)throw\s*\(\s*(?:\.\.\.)?\s*\)')

def r_exception_specs(path, text, stats):
    """EXC1: drop dynamic exception specifications throw() / throw(...). MSVC 2008 never enforced them (throw() was
    only an optimizer hint, like __declspec(nothrow)); C++17 makes throw() mean noexcept and rejects the decomp's
    redeclarations that spell it on one declaration only."""
    text, n = DYN_EXC_SPEC.subn(r'\1', text)
    stats['EXC1'] += n
    return text

# ---------------------------------------------------------------------------------------------------------- NEW2

NEWDEL_DEF = re.compile(
    r'^[ \t]*(?:static\s+)?(?:inline\s+|__forceinline\s+|FNV_FORCEINLINE\s+)?(?:void\s*\*|void)\s*(?:__cdecl\s+)?'
    r'operator\s+(?:new|delete)\s*(?:\[\s*\])?\s*'
    r'\(([^;{}()]*)\)\s*\{', re.M)
def r_placement_new(path, text, stats):
    """NEW2: drop the decomp's own inline definitions of operator new / delete outside classes where they are not
    replacements of the global allocation functions: the standard placement forms (size_t or void*, void*), which
    <new> defines identically, and any form inside a namespace (ill-formed; the decomp's TU-local copies of the
    engine's global operator new[] -> MemoryManager, which the platform's global operator new provides). For a
    placement form <new> (prepare.new_header) takes the place of the first one at file scope, or goes on top."""
    if 'operator' not in text: return text
    spans, out, pos, have, top, hdr = None, [], 0, False, False, prepare.new_header(text)
    for m in NEWDEL_DEF.finditer(text):
        if m.start() < pos: continue
        if spans is None: spans = prepare.scope_spans(text)
        chain = prepare.scope_chain(spans, m.start())
        if any(k not in ('namespace', 'extern') for k, _ in chain): continue
        in_ns = any(k == 'namespace' for k, _ in chain)
        placement = prepare.PLACEMENT_PARAMS.match(m.group(1))
        if not in_ns and not placement: continue
        end = prepare.match_brace(text, m.end() - 1)
        nl = '\n' * text.count('\n', m.start(), end)
        out.append(text[pos:m.start()])
        if placement and not chain and not have:
            out.append('#include %s /* fnv-nx NEW2 */%s' % (hdr, nl)); have = True
        else:
            out.append('/* fnv-nx NEW2 */' + nl); top = top or (placement and not have)
        pos = end
        stats['NEW2'] += 1
    out.append(text[pos:])
    return ('#include %s /* fnv-nx NEW2 */\n' % hdr if top else '') + ''.join(out)

# ---------------------------------------------------------------------------------------------------------- CTOR1

def _call_end(text, open_at):
    """Index just past the ')' matching text[open_at] == '('."""
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == '(': depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0: return i + 1
    return len(text)

CTOR_CALL = re.compile(r'(\.|->)[ \t]*((?:::)?(?:\w+(?:<[^<>;{}()]*>)?::)*?)(\w+(?:<[^<>;{}()]*>)?)::(\w+)\s*\(')

def r_ctor_calls(path, text, stats):
    """CTOR1: explicit constructor calls (MSVC extension) p->A::A(args) / o.A::A(args) -> placement new
    ::new ((void*)(A*)(p)) A(args): both construct an A in place at that address (vtable pointer included)."""
    if '::' not in text: return text
    out, pos, n = [], 0, 0
    for m in CTOR_CALL.finditer(text):
        if re.sub(r'<.*', '', m.group(3)) != m.group(4): continue          # A::A / A<T>::A
        if '//' in text[text.rfind('\n', 0, m.start()) + 1:m.start()]: continue   # in a comment
        s = prepare.operand_start(text, m.start())
        if s >= m.start() or s < pos: continue
        expr = text[s:m.start()].strip()
        cls = m.group(2) + m.group(3)
        ptr = '(%s*)(%s)' % (cls, expr) if m.group(1) == '->' else '(%s*)&(%s)' % (cls, expr)
        # the call is an lvalue of the constructed object where the decomp uses its value (*(T**)&p->A::A())
        out.append(text[pos:s]); out.append('(*::new ((void*)%s) %s(' % (ptr, cls))
        close = _call_end(text, m.end() - 1)
        out.append(text[m.end():close]); out.append(')'); pos = close; n += 1
    out.append(text[pos:])
    if not n: return text
    stats['CTOR1'] += n
    return '#include %s /* fnv-nx CTOR1 */\n' % prepare.new_header(text) + ''.join(out)

# ---------------------------------------------------------------------------------------------------------- TAIL1
#
# Itanium C++ (GCC) places a derived class's first members in the tail padding of a base that is not POD for the
# purpose of layout (any class with virtual functions, constructors, private members, ...); MSVC never does and
# always starts them at sizeof(base). Example: NiFile ends with a bool at 0x2C (sizeof 0x30); MSVC puts
# BSFile::m_bIsOpen at 0x30, GCC at 0x2D. GCC has no switch to turn the reuse off. A zero-width unnamed bit-field
# whose type has the base's alignment, first in the derived class body, moves the next member to
# roundup(dsize(base), alignof(base)) == sizeof(base) - MSVC's offset - and changes nothing else: it is not a member
# (aggregate initialization, access and standard-layout-ness stay as they are) and it is a no-op when the base has
# no tail padding. FNVNX_MSVC_BASE_END is defined in fnvnx_compat.h.

def _split_bases(clause):
    out, depth, cur = [], 0, ''
    for c in clause:
        if c in '<(': depth += 1
        elif c in '>)': depth -= 1
        if c == ',' and depth == 0: out.append(cur); cur = ''
        else: cur += c
    out.append(cur)
    return [b.strip() for b in out if b.strip()]

BASE_CLAUSE = re.compile(r'^(?:typedef\s+)?(?:class|struct)\b(?:[^:]|::)*?(?<!:):(?!:)(.*)$', re.S)

def _qualified_top(name):
    """True when `name` has a `::` outside its template argument lists (`List<V, B>::Item`, `::X`, not
    `_Ranit<T, typename A::difference_type>`, whose argument list may span lines)."""
    depth = 0
    for i, c in enumerate(name):
        if c in '<(': depth += 1
        elif c in '>)': depth -= 1
        elif c == ':' and depth == 0 and name[i + 1:i + 2] == ':': return True
    return False

def r_msvc_base_layout(path, text, stats):
    """TAIL1: derived class members start at sizeof(last base), like MSVC (see above). Classes with a virtual base
    are left alone."""
    if ':' not in text: return text
    out, pos = [], 0
    for o, c, k, h in prepare.scope_spans(text):
        if k != 'class': continue
        bare = prepare.HEAD_DECOR.sub(' ', prepare.TEMPLATE_PREFIX.sub('', h)).strip()
        m = BASE_CLAUSE.match(bare)
        if not m: continue
        bases = _split_bases(m.group(1))
        if not bases or any(re.search(r'\bvirtual\b', b) for b in bases): continue
        last = re.sub(r'^(?:(?:public|protected|private)\s+)+', '', bases[-1]).strip()
        if not last: continue
        # a qualified base (`List<VERTEX, BASE>::Item`) may be a dependent name: inside alignof it needs typename
        # (C++11 allows it before any qualified name, in a template or not); a `::` inside the template arguments
        # does not make the name qualified
        if _qualified_top(last) and not last.startswith('typename'): last = 'typename ' + last
        out.append(text[pos:o + 1]); out.append(' FNVNX_MSVC_BASE_END(%s)' % last); pos = o + 1
        stats['TAIL1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- LINK1

C_DATA_DECL = re.compile(r'^([ \t]*extern)\s+"C"(\s+[\w \t\*&:<>,]*?[\s\*&]\w+(?:\s*\[[^\]]*\])*'
                         r'(?:\s*,\s*[\*&]*\s*\w+(?:\s*\[[^\]]*\])*)*\s*;)', re.M)   # `extern "C" char a[], b[];` too
C_FNPTR_DECL = re.compile(r'^([ \t]*extern)\s+"C"(\s*[\w \t\*&:<>,]*?\(\s*(?:__\w+\s*)?\*\s*(?:DAT|PTR|g)_\w+\s*\)\s*\([^;{}]*\)\s*;)',
                          re.M)

def r_c_data_linkage(path, text, stats):
    """LINK1: `extern "C" T DAT_x;` at file scope -> `extern T DAT_x;`. The decomp declares the same image variable
    with both linkages; MSVC kept them apart, GCC rejects the pair. For a variable at global scope both linkages
    name the same symbol under the Itanium ABI (no mangling), so nothing changes for the link."""
    if 'extern "C"' not in text: return text
    spans, out, pos = None, [], 0
    for m in sorted(list(C_DATA_DECL.finditer(text)) + list(C_FNPTR_DECL.finditer(text)), key=lambda x: x.start()):
        if m.start() < pos: continue
        if '(' in m.group(2) and not C_FNPTR_DECL.match(m.group(0)): continue
        if spans is None: spans = prepare.scope_spans(text)
        if prepare.scope_chain(spans, m.start()): continue
        out.append(text[pos:m.start()]); out.append(m.group(1) + m.group(2)); pos = m.end()
        stats['LINK1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- GOTO1

SCALAR_INIT = re.compile(r'^([ \t]*)((?:const\s+)?(?:(?:unsigned|signed|volatile)\s+)*(?:bool|char|short|int|long(?:\s+long)?|'
                         r'float|double|unsigned|__m128[id]?|__m64|hkQuadReal|hkReal|[\w:]+(?:\s+const)?\s*\*+))\s+'
                         r'(\**\s*\w+)\s*=\s*([^;{}=][^;{}]*);', re.M)

def r_goto_scalar_inits(path, text, stats):
    """GOTO1: in functions that use goto, `T x = e;` (scalar, SSE vector or pointer T) -> `T x; x = e;` on the same
    line. MSVC lets a goto jump past such an initialization; C++ (and GCC even with -fpermissive) does not, but
    jumping past a declaration without initializer is allowed, and for a scalar the split changes nothing. A const
    scalar loses the const it cannot keep without its initializer (`const int n = e;` -> `int n; n = e;`; a pointer
    to const keeps it). A declarator list is split declarator by declarator, `T a = e1, b = e2;` ->
    `T a, b; a = e1; b = e2;` (the initializers keep their order)."""
    if 'goto' not in text: return text
    spans = [sp for sp in prepare.scope_spans(text) if sp[2] == 'function']
    edits = []
    for o, c, k, h in spans:
        body = text[o:c]
        if not re.search(r'\bgoto\s+\w+\s*;', body): continue
        for m in SCALAR_INIT.finditer(text, o, c):
            t = m.group(2)
            if re.match(r'(?:return|else|case|goto|delete|throw|typedef|using)\b', t): continue
            if t.startswith('const') and '*' not in t: t = t[5:].lstrip()          # the scalar's own const
            # a declarator list `float a = f(x), b = g(y);` -> `float a, b; a = f(x); b = g(y);` (same order)
            parts = [re.match(r'^\s*(\**\s*[A-Za-z_]\w*)\s*(?:=\s*(\S.*?))?\s*$', d, re.S)
                     for d in _split_args(m.group(3) + ' = ' + m.group(4))]
            if not all(parts) or len(parts) > 1 and '<' in m.group(4): continue   # `f<a, b>(x)` may be split wrong
            decl = ', '.join(p.group(1) for p in parts)
            inits = ' '.join('%s = %s;' % (p.group(1).lstrip('* \t'), p.group(2)) for p in parts if p.group(2))
            edits.append((m.start(), m.end(), '%s%s %s; %s' % (m.group(1), t, decl, inits)))
    if not edits: return text
    out, pos = [], 0
    for s, e, r in sorted(set(edits)):
        if s < pos: continue
        out.append(text[pos:s]); out.append(r); pos = e
        stats['GOTO1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- GOTO2
#
# A forward goto that crosses a declaration with an initializer into its scope (a class object, a reference, an
# aggregate: what GOTO1's split cannot help). MSVC let it through - the object is just not constructed on that path,
# and the code after the label never uses it there - GCC refuses it even with -fpermissive. Three shapes, each kept
# to cases where the meaning provably stays (lines are kept):
#  (a) a const scalar reference to the value of a function the unit declares as returning that scalar by value,
#      `const float& a = f(x);` -> `float a; a = f(x);` (the reference named a const temporary of that value);
#  (b) the label is in the declaration's own block, and nothing declared in that block between the first crossed
#      declaration and the label is named after the label in the block: `{ ` before that declaration and `} ` before
#      the label end those scopes before the label, so the jump enters none of them;
#  (c) the label starts a straight tail `L: s1; ... return e;` (no braces, no other label) whose names are not
#      declared in the function (parameters, members, globals): each crossing `goto L;` -> `{ s1; ... return e; }`.

GOTO_KW = frozenset('return goto delete throw else case default new typedef using break continue do sizeof if while '
                    'for switch try catch operator public private protected static_cast reinterpret_cast const_cast '
                    'dynamic_cast struct class union enum const volatile static register this true false'.split())
GOTO_DECL = re.compile(r'((?:(?:const|volatile|static|register|struct|class|union|enum|unsigned|signed|short|long)\s+)*'
                       r'(?:::\s*)?([A-Za-z_]\w*)(?:\s*::\s*[A-Za-z_]\w*)*(?:\s*<[^;{}()]*>)?(?:\s+const)?)'
                       r'(?=\s*[\*&]+\s*(?:const\s+)?[A-Za-z_]|\s+(?:const\s+)?[A-Za-z_])')
GOTO_DECLARATOR = re.compile(r'^\s*([\*&\s]*)(?:const\s+)?([A-Za-z_]\w*)\s*(\S?)', re.S)
GOTO_LABEL = re.compile(r'(?:[;{}]|^)\s*([A-Za-z_]\w*)[ \t]*:(?!:)', re.M)
GOTO_STARTS = re.compile(r'[;{}]|(?<!:):(?!:)|\bfor\s*\(')
GOTO_STMT = re.compile(r'\bgoto\s+([A-Za-z_]\w*)\s*;')
GOTO_SCALAR = r'(?:(?:unsigned|signed)\s+)?(?:bool|char|short|int|long(?:\s+long)?|float|double|unsigned|hkReal)'
GOTO_CREF = re.compile(r'^const\s+(%s)\s*&\s*([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*(\(.*\))\s*;$' % GOTO_SCALAR, re.S)
CXX_WORDS = GOTO_KW | frozenset('int char short long float double bool void unsigned signed auto inline'.split())

def _goto_decls(masked, o, c):
    """Declarations of the function body masked[o:c]: [(start, type end, end after ';', [(name, has init, is ref)],
    is static)]; also for-init declarations (start -1, so they never count as crossed)."""
    starts = [o + 1] + [m.end() for m in GOTO_STARTS.finditer(masked, o + 1, c)]
    out = []
    for s in starts:
        s += len(masked[s:c]) - len(masked[s:c].lstrip())
        m = GOTO_DECL.match(masked, s)
        if not m or m.group(2) in GOTO_KW or m.group(1).split()[-1] in GOTO_KW - {'const'}: continue
        depth, e = 0, m.end(1)
        while e < c:
            ch = masked[e]
            if ch in '([{': depth += 1
            elif ch in ')]}':
                if depth == 0: break
                depth -= 1
            elif ch == ';' and depth == 0: break
            e += 1
        if e >= c or masked[e] != ';': continue
        decls = []
        for d in _split_args(masked[m.end(1):e]):
            dm = GOTO_DECLARATOR.match(d)
            if not dm or dm.group(2) in CXX_WORDS: decls = None; break
            decls.append((dm.group(2), dm.group(3) in ('=', '(', '{'), '&' in dm.group(1)))
        if decls:
            for_init = re.search(r'\bfor\s*\(\s*$', masked[o:s])
            out.append((-1 if for_init else s, m.end(1), e + 1, decls,
                        bool(re.match(r'(?:\w+\s+)*static\b', m.group(1)))))
    return out

def _goto_fix(text, stats, skip):
    """One GOTO2 edit in `text` (the first crossing it can fix), or None."""
    masked = FRIEND_MASK.sub(lambda x: re.sub(r'[^\n]', ' ', x.group()), text)
    spans = prepare.scope_spans(masked)
    funcs, last = [], -1
    for sp in spans:
        if sp[2] == 'function' and sp[0] > last:
            funcs.append(sp); last = sp[1]
    for o, c, _, _ in funcs:
        if not GOTO_STMT.search(masked, o, c): continue
        blocks = [sp for sp in spans if o <= sp[0] and sp[1] <= c and sp[2] not in ('init', 'class', 'enum')]
        def block(p): return max((sp for sp in blocks if sp[0] < p < sp[1]), key=lambda sp: sp[0])
        gotos = [(m.start(), m.end(), m.group(1)) for m in GOTO_STMT.finditer(masked, o, c)]
        targets = {g[2] for g in gotos}
        labels = [(m.start(1), m.end(), m.group(1)) for m in GOTO_LABEL.finditer(masked, o + 1, c)
                  if m.group(1) in targets and m.group(1) not in GOTO_KW]
        decls = _goto_decls(masked, o, c)
        local = {n for d in decls for n, _, _ in d[3]}
        for lp, le, name in labels:
            lb = block(lp)
            crossing = [d for d in decls if d[0] >= 0 and not d[4] and d[0] < lp and any(i or r for _, i, r in d[3])
                        and block(d[0])[0] < lp < block(d[0])[1]
                        and any(g[0] < d[0] and g[2] == name for g in gotos)]
            for d in crossing:
                key = (name, masked[d[0]:d[2]])
                if key in skip: continue
                s, te, e, dl, _ = d
                # (a) const scalar reference to a by-value scalar result
                cm = GOTO_CREF.match(text[s:e])
                if cm and len(dl) == 1:
                    rets = re.findall(r'\b(%s)\s*(&?)\s*(?:(?:__cdecl|__stdcall|__fastcall|__thiscall)\s+)?%s\s*\('
                                      % (GOTO_SCALAR, re.escape(cm.group(3))), masked)
                    if rets and all(not r for _, r in rets) and \
                            not re.search(r'[\w>]\s*&\s*(?:\w+::)*%s\s*\(' % re.escape(cm.group(3)), masked):
                        stats['GOTO2'] += 1
                        return text[:s] + '%s %s; %s = %s%s;' % (cm.group(1), cm.group(2), cm.group(2), cm.group(3),
                                                               cm.group(4)) + text[e:]
                db = block(s)
                # (b) close the crossed scopes before the label
                if lb == db:
                    names = {n for d2 in decls if d2[0] >= s and d2[2] <= lp and block(d2[0]) == db for n, _, _ in d2[3]}
                    if not re.search(r'\b(?:%s)\b' % '|'.join(sorted(names)), masked[le:db[1]]):
                        stats['GOTO2'] += 1
                        return text[:s] + '{ ' + text[s:lp] + '} ' + text[lp:]
                # (c) the label's straight tail cloned into each crossing goto
                tail = re.match(r'(?:[^;{}:]*;){0,3}?\s*return\b[^;{}:]*;', masked[le:db[1]])
                if tail and text[le:le + tail.end()] == masked[le:le + tail.end()]:
                    t = text[le:le + tail.end()]
                    words = re.findall(r'(?<![\w:])([A-Za-z_]\w*)\b(?!\s*::)', re.sub(r'(?:\.|->)\s*\w+', ' ', t))
                    if not re.search(r'\bgoto\b', t) and not (set(words) - CXX_WORDS) & local:
                        clone = '{ %s }' % re.sub(r'\s*\n\s*', ' ', t.strip())
                        for g0, g1, gn in reversed(gotos):
                            if gn == name and g0 < s: text = text[:g0] + clone + text[g1:]
                        stats['GOTO2'] += 1
                        return text
                skip.add(key)
    return None

def r_goto_crossed_scopes(path, text, stats):
    """GOTO2: forward gotos that cross a declaration with an initializer of a class, reference or aggregate (see
    above): (a) `const float& a = f(x);` -> `float a; a = f(x);` for a by-value scalar f, (b) `{ ` ... `} ` around
    the crossed declarations before a label of their block, when nothing they declare is named after the label,
    (c) the label's straight `...; return e;` tail cloned into each crossing goto."""
    if 'goto' not in text: return text
    skip = set()
    for _ in range(64):
        new = _goto_fix(text, stats, skip)
        if new is None: break
        text = new
    return text

# ---------------------------------------------------------------------------------------------------------- MSCV1
#
# The decomp tests the compiler's version in code, not only in #if: `if (_MSC_FULL_VER >= 150030729)`. The value is
# the one of the compiler build/units.json names for the unit (the decomp README: sp1 cl 15.00.30729.01, rtm
# 15.00.21022.08, vc8 14.00.50727.762), so the branch MSVC compiled is the one we compile.

MSC_VERSIONS = {'sp1': ('1500', '150030729'), 'rtm': ('1500', '150021022'), 'vc8': ('1400', '140050727')}
MSC_IN_CODE = re.compile(r'^(?![ \t]*#).*\b_MSC_(?:FULL_)?VER\b', re.M)
_unit_compilers = {}

def _unit_compiler(path):
    root, uj = prepare._units_json(path)
    if not uj: return None
    if uj not in _unit_compilers:
        import json
        with open(uj, encoding='utf-8') as f:
            _unit_compilers[uj] = {u.get('source'): u.get('compiler') for u in json.load(f).get('units', [])}
    return _unit_compilers[uj].get(os.path.relpath(os.path.abspath(path), root).replace(os.sep, '/'))

def r_msc_version_values(path, text, stats):
    """MSCV1: `_MSC_FULL_VER` / `_MSC_VER` used in code (not in a preprocessor line) -> the value of the compiler
    build/units.json gives the unit (sp1, rtm or vc8); left alone where the file is no unit or none is given."""
    if '_MSC_' not in text or not MSC_IN_CODE.search(text): return text
    ver = MSC_VERSIONS.get(_unit_compiler(path))
    if not ver: return text
    masked = FRIEND_MASK.sub(lambda x: re.sub(r'[^\n]', ' ', x.group()), text)
    out, pos = [], 0
    for m in re.finditer(r'\b_MSC_(FULL_)?VER\b', masked):
        out.append(text[pos:m.start()]); out.append(ver[1] if m.group(1) else ver[0]); pos = m.end()
        stats['MSCV1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- QUAD1
#
# A decomp class of four floats that converts to __m128 (`operator __m128() const`), returned by a function whose
# return type is constructible from __m128 only: `return m_q;`. MSVC chains the two user-defined conversions (the
# conversion to its __m128 union, then the constructor); C++ allows one, so the first is written out:
# `return (__m128)m_q;`.

QUAD_CONV = re.compile(r'\boperator\s+__m128\s*\(\s*\)\s*const\b')
QUAD_HEAD = re.compile(r'\b(?:struct|class)\s+(?:__declspec\s*\((?:[^()]|\([^()]*\))*\)\s*)*([A-Za-z_]\w*)')

def r_quad_returns(path, text, stats):
    """QUAD1: `return q;` -> `return (__m128)q;` where q is declared only with a class that converts to __m128 and
    the function returns a class constructible from __m128 but not from q's class."""
    if 'operator __m128' not in text and not re.search(r'operator\s+__m128', text): return text
    masked = FRIEND_MASK.sub(lambda x: re.sub(r'[^\n]', ' ', x.group()), text)
    spans = prepare.scope_spans(masked)
    classes = {}
    for o, c, k, h in spans:
        hm = QUAD_HEAD.search(h) if k == 'class' else None
        if hm: classes.setdefault(hm.group(1), []).append(masked[o:c])
    conv = sorted(n for n, bodies in classes.items() if any(QUAD_CONV.search(b) for b in bodies))
    if not conv: return text
    names = set()
    for m in re.finditer(r'\b(?:%s)\s+([A-Za-z_]\w*)\s*;' % '|'.join(conv), masked):
        others = {t for t in re.findall(r'\b([A-Za-z_]\w*)[\s\*&]+%s\s*[;,=)\[]' % m.group(1), masked)
                  if t not in GOTO_KW}
        if others <= set(conv): names.add(m.group(1))
    rets = sorted(n for n, bodies in classes.items()
                  if any(re.search(r'\b%s\s*\(\s*(?:const\s+)?__m128\s*&?\s*\w*\s*\)' % n, b) for b in bodies)
                  and not any(re.search(r'\b%s\s*\(\s*(?:const\s+)?(?:%s)\b' % (n, '|'.join(conv)), b) for b in bodies))
    if not names or not rets: return text
    head = re.compile(r'(?:^|\s)(?:%s)\s+(?:\w+::)*\w+\s*\([^()]*\)\s*(?:const\s*)?$' % '|'.join(rets))
    ret = re.compile(r'\breturn\s+((?:this\s*->\s*)?(?:%s))\s*;' % '|'.join(sorted(names)))
    edits = []
    for o, c, k, h in spans:
        if k == 'function' and head.search(h):
            edits += [(m.start(1), m.group(1)) for m in ret.finditer(masked, o, c)]
    out, pos = [], 0
    for s, name in sorted(set(edits)):
        out.append(text[pos:s]); out.append('(__m128)' + name); pos = s + len(name)
        stats['QUAD1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- SDK1

SDK_GUARD = re.compile(r'^([ \t]*#[ \t]*ifndef[ \t]+_WIN(?:DEF|NT|BASE)_\b)', re.M)

def r_sdk_guarded_copies(path, text, stats):
    """SDK1: the decomp's generated headers keep their own copy of an SDK type behind `#ifndef _WINNT_` (the SDK
    guard, set when the SDK came first). The shim is included right before that test, so the PC SDK type (and its
    guards) always win and the copy is skipped, whatever the include order of the unit."""
    if '#' not in text or '_WIN' not in text: return text
    text, n = SDK_GUARD.subn(r'#include "fnvnx_win32.h" /* fnv-nx SDK1 */\n\1', text)
    stats['SDK1'] += n
    return text

STR_ADDR = re.compile(r'&\s*(str_[0-9a-fA-F]{8})\b(?!\s*\[)')

# a declaration statement (it may go on over lines: `extern "C" char str_a[], str_b[],\n    str_c[];`), up to its
# first initializer or parameter list: the names in it are the ones it declares
STR_DECL_LINE = re.compile(r'^[ \t]*(?:extern\b|static\b|const\b|char\b|unsigned\b|signed\b|wchar_t\b)[^;{}=(]*', re.M)
STR_DECL_NAME = re.compile(r'\b(str_[0-9a-fA-F]{8})\b\s*(\[)?')

def r_string_array_address(path, text, stats):
    """STR1: `&str_01082ab8` where the declaration in scope is `extern char str_01082ab8[];` -> `str_01082ab8`: the
    same address; MSVC converted char (*)[] to const char* silently, GCC does not. A file may declare the same name
    as an array in one place and as a scalar `char` in another: the declaration in scope at the use decides (the
    latest one before it, in the use's namespace or an enclosing one)."""
    if 'str_' not in text or not re.search(r'\bstr_[0-9a-fA-F]{8}\s*\[', text): return text
    spans = prepare.scope_spans(text)
    heads = lambda pos: tuple(h for k, h in prepare.scope_chain(spans, pos) if k == 'namespace')
    decls = {}                                             # name -> [(pos, scope, is_array)]
    for line in STR_DECL_LINE.finditer(text):
        for d in STR_DECL_NAME.finditer(line.group(0)):
            pos = line.start() + d.start()
            decls.setdefault(d.group(1), []).append((pos, heads(pos), bool(d.group(2))))
    if not decls: return text
    def sub(m):
        scope = heads(m.start())
        seen = [d for d in decls.get(m.group(1), ()) if d[0] < m.start() and d[1] == scope[:len(d[1])]]
        if not seen: return m.group(0)
        best = max(seen, key=lambda d: (len(d[1]), d[0]))  # innermost namespace, then the latest
        if not best[2]: return m.group(0)
        stats['STR1'] += 1
        return m.group(1)
    return STR_ADDR.sub(sub, text)

# ---------------------------------------------------------------------------------------------------------- INTL1

INTERLOCKED_DEF = re.compile(r'^[ \t]*(?:FNV_FORCEINLINE|__forceinline|static\s+inline|inline)\s+[\w \t\*]*?\b'
                             r'(Interlocked\w+)\s*\([^;{}]*\)\s*\{', re.M)

def r_interlocked_fallbacks(path, text, stats):
    """INTL1: drop the decomp's own inline definitions of the Win32 Interlocked* functions at file scope (the
    non-MSVC branch of its layout_support headers); the shim declares them like the SDK, with C linkage, and the
    platform layer defines them (same semantics: full-barrier atomics). The shim takes the place of the first one."""
    if 'Interlocked' not in text: return text
    spans, out, pos, have = None, [], 0, False
    for m in INTERLOCKED_DEF.finditer(text):
        if m.start() < pos: continue
        if spans is None: spans = prepare.scope_spans(text)
        if prepare.scope_chain(spans, m.start()): continue
        end = prepare.match_brace(text, m.end() - 1)
        nl = '\n' * text.count('\n', m.start(), end)
        out.append(text[pos:m.start()])
        out.append(('/* fnv-nx INTL1 */' if have else '#include "fnvnx_win32.h" /* fnv-nx INTL1 */') + nl)
        have, pos = True, end
        stats['INTL1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- CALL1
#
# DECL1 drops the decomp's prototypes of Win32 functions, which spelled the parameters with the decomp's own types
# (SystemInfo86*, Rect86*, int for a HANDLE, a local copy of _RTL_CRITICAL_SECTION...). Under MSVC each call passed
# that value to the same import; with the SDK prototypes GCC refuses the conversions. CALL1 casts every argument
# passed to a pointer parameter of a Win32 function to the SDK parameter type, which is what the decomp's own
# prototype did implicitly (same bits; ILP32 pointers and ints are both 32-bit). Where the dropped prototype took a
# reference (`CoCreateInstance(const AudGuid& clsid, ...)`, as the C++ SDK does) the address of the argument is
# passed (prepare.DECL1_REFS): what the reference passed.

_W32_SIGS = None

def _w32_signatures():
    """name -> list of parameter types (None for a non-pointer parameter), from shim/include/fnvnx_win32.h."""
    global _W32_SIGS
    if _W32_SIGS is not None: return _W32_SIGS
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'shim', 'include', 'fnvnx_win32.h')
    src = COMMENT.sub(' ', open(here).read())
    ptr = set(re.findall(r'FNVNX_DECLARE_HANDLE\((\w+)\)', src))
    for td in re.findall(r'\btypedef\b[^;{}]*;', src):
        ptr.update(re.findall(r'\*\s*(\w+)\s*[,;)]', td))
    for td in re.findall(r'\}([^;{}]*);', src):      # } RECT, *PRECT, *LPRECT;
        ptr.update(re.findall(r'\*\s*(\w+)\s*(?:,|$)', td.strip()))
    changed = True
    while changed:                                   # typedef HINSTANCE HMODULE;
        changed = False
        for a, b in re.findall(r'\btypedef\s+(\w+)\s+(\w+)\s*;', src):
            if a in ptr and b not in ptr: ptr.add(b); changed = True
    sigs, seen = {}, {}
    for m in re.finditer(r'^[A-Za-z_][\w \t\*]*?\b([A-Za-z_]\w*)\(([^()]*)\)\s*(?:__attribute__\(\([^()]*\)\))?\s*;', src, re.M):
        name, params = m.group(1), m.group(2).strip()
        seen[name] = seen.get(name, 0) + 1
        if params in ('', 'void') or '...' in params: continue
        types = []
        for p in params.split(','):
            p = p.strip()
            words = re.findall(r'\w+', p)
            is_ptr = '*' in p or any(w in ptr for w in words)
            types.append(p if is_ptr else None)
        sigs[name] = types
    for n, c in seen.items():                        # overloaded in the shim: leave to overload resolution
        if c > 1: sigs.pop(n, None)
    for n in re.findall(r'^\s*inline\b[^(]*\b(\w+)\s*\(', src, re.M): sigs.pop(n, None)
    _W32_SIGS = sigs
    return sigs

def _split_args(s):
    out, depth, cur, q = [], 0, '', None
    for c in s:
        if q:
            cur += c
            if c == q and not cur.endswith('\\' + q): q = None
            continue
        if c in '"\'': q = c
        elif c in '([{': depth += 1
        elif c in ')]}': depth -= 1
        if c == ',' and depth == 0: out.append(cur); cur = ''
        else: cur += c
    out.append(cur)
    return out

W32_VISIBLE = re.compile(r'#\s*include\s*[<"](?:windows\.h|fnvnx_win32\.h|d3d9\.h|wingdi\.h|commctrl\.h|intrin\.h)[>"]')

def r_win32_call_args(path, text, stats):
    """CALL1: cast pointer arguments of Win32 calls to the SDK parameter types (see above)."""
    if not W32_VISIBLE.search(text): return text
    sigs = _w32_signatures()
    names = [n for n in sigs if n in text]
    if not names: return text
    # names the file still declares itself (C++ prototypes kept in a namespace) resolve to those: leave them
    names = [n for n in names if not re.search(r'[\w\*&>]\s+%s\s*\([^;{}]*\)\s*;' % n, text)]
    if not names: return text
    call = re.compile(r'(?<![\w.>:~])(%s)\s*\(' % '|'.join(sorted(names, key=len, reverse=True)))
    bodies = []                                      # outermost function bodies, in order
    for o, c, k, _ in prepare.scope_spans(text):
        if k == 'function' and not (bodies and o < bodies[-1][1]): bodies.append((o, c))
    if not bodies: return text
    out, pos, bi = [], 0, 0
    for m in call.finditer(text):
        if m.start() < pos: continue
        while bi < len(bodies) and bodies[bi][1] < m.start(): bi += 1
        if bi == len(bodies) or not bodies[bi][0] < m.start(): continue
        before = text[max(0, m.start() - 40):m.start()].rstrip()
        prev = re.search(r'(\w+)$', before)
        if prev and prev.group(1) not in ('return', 'case', 'else', 'do'): continue   # a declaration
        close = _call_end(text, m.end() - 1)
        args = _split_args(text[m.end():close - 1])
        types = sigs[m.group(1)]
        if len(args) != len(types): continue
        refs = getattr(prepare, 'DECL1_REFS', {}).get(path, {}).get(m.group(1), ())
        new = []
        for i, (a, t) in enumerate(zip(args, types)):
            if t is None or not a.strip(): new.append(a); continue
            v = '&(%s)' % a.strip() if i in refs else '(%s)' % a.strip()
            new.append(' (%s)%s' % (t, v) if new else '(%s)%s' % (t, v))
        out.append(text[pos:m.end()]); out.append(','.join(new)); out.append(')'); pos = close
        stats['CALL1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- RCAST1

REF_CAST_CALL = re.compile(r'\(\s*((?:const\s+)?(?:volatile\s+)?(?:::)?[A-Za-z_][\w:<>, \t\*]*?&)\s*\)\s*'
                           r'(?=(?:::)?[A-Za-z_]|\()')

def _postfix_end(text, i):
    """End of the postfix expression starting at text[i] (a name, then (...), [...], .name, ->name), and whether its
    last part is a call."""
    if text[i:i + 1] == '(':                                          # a parenthesized operand: (a - b)
        j, call = _call_end(text, i), True
    else:
        m = re.match(r'(?:::)?[A-Za-z_]\w*(?:\s*::\s*~?[A-Za-z_]\w*)*', text[i:])
        if not m: return i, False
        j, call = i + m.end(), False
    while True:
        k = j
        while k < len(text) and text[k] in ' \t': k += 1
        if k < len(text) and text[k] == '(':
            j, call = _call_end(text, k), True
        elif k < len(text) and text[k] == '[':
            depth, k2 = 0, k
            while k2 < len(text):
                depth += (text[k2] == '[') - (text[k2] == ']')
                k2 += 1
                if depth == 0: break
            j, call = k2, False
        else:
            mm = re.match(r'\s*(?:\.|->)\s*(?:operator\s*(?:\(\)|\[\]|[^\s\w(]+)(?=\s*\()|~?[A-Za-z_]\w*)', text[k:])
            if not mm: return j, call
            j, call = k + mm.end(), False

NAMED_REF_CAST = re.compile(r'\b(?:static_cast|reinterpret_cast)\s*<\s*((?:const\s+)?(?:volatile\s+)?(?:::)?'
                            r'[A-Za-z_][\w:<>, \t\*]*?&)\s*>\s*\(')
LVALUE_LIKE = re.compile(r'^\s*(?:\*\s*)*(?:::)?[A-Za-z_]\w*(?:\s*(?:::|\.|->)\s*~?[A-Za-z_]\w*|\s*\[[^\[\]]*\])*\s*$')

def r_ref_cast_temporaries(path, text, stats):
    """RCAST1: `(T&)f(args)` where f(args) is a temporary (a functional cast or a call returning by value) ->
    `(T&)fnvnx_lvalue(f(args))`, and the same for `static_cast<T&>(e)` / `reinterpret_cast<T&>(e)` of an operand
    that is not a name, member or element. MSVC binds a reference cast to a temporary, which lives to the end of the
    full expression; fnvnx_lvalue (fnvnx_compat.h) returns the same object as an lvalue for that same lifetime.
    An operand that already is an lvalue is returned unchanged."""
    if '&)' not in text and '&>' not in text and '& >' not in text: return text
    edits = []
    for m in REF_CAST_CALL.finditer(text):
        if re.search(r'[\w\)\]]\s*$', text[max(0, m.start() - 20):m.start()]) and not re.search(
                r'\b(?:return|case|throw)\s*$', text[max(0, m.start() - 20):m.start()]):
            continue                                                  # a declarator (`void(__cdecl*&)(...)`), a call
        if text[m.end()] == '(':                                      # parenthesized: an expression, not a type
            inner = text[m.end() + 1:_call_end(text, m.end()) - 1]
            if re.match(r'^[\w\s:<>,\*&]*$', inner): continue        # `(T&)(void*)p`, `(T&)(*p)`, `(T&)(x)`
        close, call = _postfix_end(text, m.end())
        if not call: continue                                         # an lvalue (a name, a member, an element)
        edits.append((m.end(), close))
    for m in NAMED_REF_CAST.finditer(text):
        close = _call_end(text, m.end() - 1)
        if LVALUE_LIKE.match(text[m.end():close - 1]): continue
        edits.append((m.end(), close - 1))
    out, pos = [], 0
    for a, b in sorted(edits):
        if a < pos: continue
        out.append(text[pos:a]); out.append('fnvnx_lvalue(' + text[a:b] + ')'); pos = b
        stats['RCAST1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- CSX1
#
# Asserts of the decomp's generated layouts that only hold on the Xbox 360: the member order of its big-endian
# _FILETIME (tesform's layout header). The critical-section asserts this rule dropped under D15 (J-0051) hold again
# since D15 was reopened (J-0067): CSX2 gives the generated sections their 28 bytes. Every other assert stays.

CSX1_ASSERTS = {
    # not a critical section: the Xbox 360 SDK's big-endian _FILETIME puts dwHighDateTime first (PC: dwLowDateTime)
    '_FILETIME': 'dwHighDateTime dwLowDateTime'.split(),
}

CSX1_LINE = re.compile(r'^[ \t]*\w*ASSERT\s*\(\s*(?:sizeof\s*\(\s*([\w:]+)\s*\)|offsetof\s*\(\s*([\w:]+)\s*,\s*([\w.\[\]]+)\s*\))'
                       r'[^\n]*\)\s*;[ \t]*$', re.M)

def r_xbox_cs_asserts(path, text, stats):
    """CSX1: drop the decomp's asserts that only hold on the Xbox 360 (see above)."""
    if 'ASSERT' not in text or not any(c.split('::')[-1] in text for c in CSX1_ASSERTS): return text
    def sub(m):
        cls, member = (m.group(1), 'sizeof') if m.group(1) else (m.group(2), m.group(3))
        if member not in CSX1_ASSERTS.get(cls, ()): return m.group(0)
        stats['CSX1'] += 1
        return '/* fnv-nx CSX1: Xbox 360 layout */'
    return CSX1_LINE.sub(sub, text)

# ---------------------------------------------------------------------------------------------------------- NEW3

GLOBAL_DELETE = re.compile(r'^([ \t]*(?:static\s+)?(?:inline\s+|__forceinline\s+)?void\s+(?:__cdecl\s+)?operator\s+delete\s*'
                           r'(?:\[\s*\])?\s*\([^;{}()]*\))(\s*)(?=[{;])', re.M)

def r_global_delete_noexcept(path, text, stats):
    """NEW3: the decomp's file-scope replacements of the global operator delete get `noexcept`, the specification
    every deallocation function has implicitly (libstdc++'s <new> spells it, GCC then rejects the unspelled one)."""
    if 'operator delete' not in text: return text
    spans, out, pos = None, [], 0
    for m in GLOBAL_DELETE.finditer(text):
        if spans is None: spans = prepare.scope_spans(text)
        if prepare.scope_chain(spans, m.start()): continue
        out.append(text[pos:m.end(1)]); out.append(' noexcept'); pos = m.end(1)
        stats['NEW3'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- TYPEID1

def r_typeid_include(path, text, stats):
    """TYPEID1: MSVC accepts typeid without <typeinfo>; GCC does not. <typeinfo> on top of files that use typeid
    (not in files that carry their own copy of MSVC's std::type_info / std::exception)."""
    if 'typeid' not in text or not re.search(r'\btypeid\s*\(', text) or prepare.OWN_STD.search(text): return text
    stats['TYPEID1'] += 1
    return '#include <typeinfo> /* fnv-nx TYPEID1 */\n' + text

# ---------------------------------------------------------------------------------------------------------- UNIT1
#
# Targeted substitutions for single files (pragmatic fixes; every entry says why).

UNIT_RENAMES = {
    # tesdatahandler: a local `struct FILETIME {low, high}` / `struct WIN32_FIND_DATAA` with its own member names,
    # in a header that also needs the SDK's file functions; the local types keep their layout under new names.
    'fallout_shared/tesdatahandler_files.h': [(r'\bFILETIME\b', 'TesFileTime'), (r'\bWIN32_FIND_DATAA\b', 'TesFindData')],
    'fallout_shared/tesdatahandler_decls.h': [(r'\bFILETIME\b', 'TesFileTime'), (r'\bWIN32_FIND_DATAA\b', 'TesFindData')],
    'fallout_shared/tesdatahandler.cpp': [(r'\bFILETIME\b', 'TesFileTime'), (r'\bWIN32_FIND_DATAA\b', 'TesFindData')],
    # SpeedTree: the stack object handed to the std::exception constructor (FUN_00ec78f1) is declared 8 bytes, but
    # that constructor writes 12 (vfptr, _m_what, _m_doFree; S4b): without the third member the write runs past
    # the object on the stack.
    # blk_00c20000: FindFirstNonCancelButton is declared twice (C3C2B0HWND, long) / (void*, int); the decomp's own
    # EnumChildWindows prototype (dropped by DECL1) took int (__stdcall*)(void*, int), which picked the second
    'staging/blk_00c20000_od.cpp': [(r'EnumChildWindows\(owner, FindFirstNonCancelButton,',
                                     r'EnumChildWindows(owner, (int(__stdcall*)(void*, int))FindFirstNonCancelButton,')],
    'speedtree/frondengine.cpp': [(r'(struct FUN_00ec78f1_t\s*\{\s*int m0;\s*int m4;)', r'\1 int m8; /* fnv-nx UNIT1 */')],
    'speedtree/frondengine_ob1_variant.cpp': [(r'(struct FUN_00ec78f1_t\s*\{\s*int m0;\s*int m4;)',
                                               r'\1 int m8; /* fnv-nx UNIT1 */')],
}

def r_unit_renames(path, text, stats):
    """UNIT1: targeted per-file renames (UNIT_RENAMES)."""
    p = path.replace(os.sep, '/')
    for key, subs in UNIT_RENAMES.items():
        if p.endswith('/src/' + key):
            for pat, rep in subs:
                text, n = re.subn(pat, rep, text)
                stats['UNIT1'] += n
    return text

# ---------------------------------------------------------------------------------------------------------- SPEC1
#
# The decomp defines members of class template specializations (`T<A>::m(...) { ... }`, often without the
# `template <>`) after the unit has already used T<A>::m. MSVC takes the later definition as the one; C++ calls it an
# explicit specialization after implicit instantiation. The primary template only declares those members, so an
# early declaration `template <> R T<A>::m(P);` gives exactly MSVC's meaning: SPEC1 puts one right after the primary
# template's definition (when it is in the same file) or after the file's last #include before the definition, with
# typedef names of the template arguments that are declared later in the file expanded.

SPEC_DEF = re.compile(r'^([ \t]*)(template\s*<\s*>\s*)?((?:FNVNX_KEEP\s+)?(?:(?:inline|static|virtual)\s+)*)'
                      r'((?:[\w:<>,\*& \t]+?[\s\*&])?)'                    # return type (none for constructors)
                      r'(\b(?:[A-Za-z_]\w*\s*::\s*)*[A-Za-z_]\w*)\s*<([^<>;{}()]*(?:<[^<>;{}()]*(?:<[^<>;{}()]*>[^<>;{}()]*)*>[^<>;{}()]*)*)>'
                      r'\s*::\s*(~?\w+)\s*'
                      r'\(([^;{}]*)\)\s*(const\s*)?(?=[{;:])', re.M)
TEMPLATE_PARAM_LIKE = re.compile(r'^\s*(?:class|typename)\b|^\s*[A-Z]\s*$')

def _primary_template_end(text, name):
    m = re.search(r'\btemplate\s*<[^{};]*>\s*(?:class|struct)\s+%s\s*(?::[^;{}]*)?\{' % re.escape(name), text)
    if not m: return None
    end = prepare.match_brace(text, m.end() - 1)
    semi = text.find(';', end - 1)
    return semi + 1 if semi >= 0 else end

# The files where GCC reports a member specialization defined after its implicit instantiation (census c8); the
# rule only adds declarations, so it is limited to them rather than run on every member definition of the decomp.
SPEC1_FILES = ('unmapped/threadsafestructures.cpp', 'bsmain/threadsafestructures.inl', 'fallout_shared/tesdatahandler.cpp',
               'gamebryo2.2/corelibs/nimain/nitarray.inl', 'gamebryo2.2/corelibs/nimain/nitset.inl',
               'bsshader/shadowscenelight.cpp', 'fallout/ai/movement/actormover.cpp', 'bscore/bssimplearray.h',
               'speedtree/projectedshadow_ob1_variant.cpp')

def r_specialization_decls(path, text, stats):
    """SPEC1: declare explicit specializations of class template members before their first use (see above)."""
    if '>::' not in text or not path.replace(os.sep, '/').endswith(tuple('/src/' + f for f in SPEC1_FILES)):
        return text
    spans, edits = None, []
    for m in SPEC_DEF.finditer(text):
        tmpl, args, member = m.group(5), m.group(6).strip(), m.group(7)
        if tmpl in ('static_cast', 'reinterpret_cast', 'const_cast', 'dynamic_cast'): continue
        if not args or any(TEMPLATE_PARAM_LIKE.match(a) for a in args.split(',')): continue
        squash = lambda x: re.sub(r'\s+', '', x)
        if any(squash(c) == squash(args) for c in re.findall(
                r'\btemplate\s*<\s*>\s*(?:class|struct)\s+%s\s*<([^{};]*)>\s*(?::[^{};]*)?\{' % re.escape(tmpl), text)):
            continue                                                 # this very T<A> is a class specialization
        if spans is None: spans = prepare.scope_spans(text)
        if any(k != 'namespace' for k, _ in prepare.scope_chain(spans, m.start())): continue
        if prepare.scope_chain(spans, m.start()): continue          # file scope only
        last = text[max(0, m.start() - 200):m.start()].rstrip()
        if last.endswith('>') and re.search(r'\btemplate\s*<[^\n]*>$', last): continue   # template <class U> ...
        p0 = _primary_template_end(text[:m.start()], tmpl)
        if p0 is None:
            # after the file's include preamble (the #includes before its first line of code): later #includes
            # (DECL1 puts some mid-file) may come after the first use
            pre = re.match(r'(?:[ \t]*(?:#[^\n]*|//[^\n]*)?\n|[ \t]*/\*.*?\*/[ \t]*\n)*', text[:m.start()], re.S)
            incs = [i.end() for i in re.finditer(r'^[ \t]*#[ \t]*include[^\n]*', text[:pre.end()], re.M)]
            if not incs:
                incs = [i.end() for i in re.finditer(r'^[ \t]*#[ \t]*include[^\n]*', text[:m.start()], re.M)][:1]
            if not incs: continue
            p0 = incs[-1]
        # typedef names in the arguments that the file declares after p0: use what they stand for
        for _ in range(4):
            changed = False
            for name in set(re.findall(r'\b[A-Za-z_]\w*\b', args)):
                td = re.search(r'^[ \t]*typedef\s+([^;{}()]+?)\s+%s\s*;' % re.escape(name), text[p0:m.start()], re.M)
                if td: args = re.sub(r'\b%s\b' % re.escape(name), td.group(1).strip(), args); changed = True
            if not changed: break
        ret = re.sub(r'\b(?:FNVNX_KEEP|inline|static|virtual)\b', '', m.group(4) or '').strip()
        params = re.sub(r'=\s*[^,]+', '', m.group(8))                # default arguments stay on the definition
        keep = 'FNVNX_KEEP' in (m.group(3) or '')
        # (GCC takes no attribute between `template <>` and the declarator: `used` goes after it)
        decl = 'template <> %s%s<%s>::%s(%s)%s%s;' % (ret + ' ' if ret else '', tmpl, args, member, params,
                                                     ' const' if m.group(9) else '', ' FNVNX_KEEP' if keep else '')
        nl = text.find('\n', p0)
        if nl < 0 or nl >= m.start(): continue
        # names of the declaration that are not declared yet at that point get a forward declaration when the file
        # declares them as a class / struct at file scope; otherwise the declaration is not moved
        fwd, unknown = [], False
        # + the local headers included before it, transitively
        seen_text, todo, done = text[:nl], [(path, text[:nl])], set()
        root = _roots(path)
        while todo:
            fpath, ftext = todo.pop()
            for inc in re.findall(r'^[ \t]*#[ \t]*include[ \t]*"([^"]+)"', ftext, re.M):
                f = _resolve(inc, os.path.dirname(fpath), root)
                if not f or f in done: continue
                done.add(f)
                with open(f, encoding='utf-8', errors='surrogateescape') as h: t2 = h.read()
                seen_text += '\n' + t2
                todo.append((f, t2))
        if any(squash(c) == squash(args) for c in re.findall(
                r'\btemplate\s*<\s*>\s*(?:class|struct)\s+%s\s*<([^{};]*)>\s*(?::[^{};]*)?\{' % re.escape(tmpl), seen_text)):
            continue                                                 # a class specialization in an included header
        ptypes = ','.join(re.sub(r'\b[A-Za-z_]\w*\s*$', '', p) if len(re.findall(r'[A-Za-z_]\w*', p)) > 1 else p
                          for p in _split_args(params))                # parameter names are not types
        for n in sorted(set(re.findall(r'(?<![\w:])([A-Za-z_]\w*)\b(?!\s*::)', args + ',' + ptypes + ',' + ret))):
            if n in TPL_PLAIN or n in CXX_WORDS or n == tmpl or re.search(r'\b%s\b' % re.escape(n), seen_text): continue
            if n in ('FNVNX_KEEP',): continue
            k = re.search(r'^[ \t]*(class|struct|union)\s+%s\s*[;:{]' % re.escape(n), text, re.M)
            if not k: unknown = True; break
            fwd.append('%s %s;' % (k.group(1), n))
        if unknown: continue
        for n in set(re.findall(r'(?<![\w:])([A-Za-z_]\w*)\s*::', args + ',' + params + ',' + ret)):
            if not re.search(r'\b%s\b' % re.escape(n), seen_text): unknown = True
        if unknown: continue
        edits.append((nl + 1, nl + 1, ' '.join(fwd + [decl]) + ' /* fnv-nx SPEC1 */ '))
        stats['SPEC1'] += 1
    if not edits: return text
    out, pos = [], 0
    for a, b, r in sorted(edits, key=lambda x: (x[0], x[1])):
        if a < pos: continue
        out.append(text[pos:a]); out.append(r); pos = b
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- VA1
#
# MSVC's va_list is a char*, and the decomp spells it that way: parameters that pass a va_list on are declared
# `char* args` (or void*), and variadic functions build one by hand (`char* args = (char*)(&format + 1);`). On
# AArch64 va_list is a structure, and the same function is declared with char* in one file and va_list in another.
# VA1 makes every such char* hold the address of a real va_list, whatever each declaration says:
#   - a hand-built one becomes `va_list fnvnx_va_args; va_start(fnvnx_va_args, format); char* args = (char*)&...;`;
#   - a va_list variable passed to a function is passed as fnvnx_va(x), and so is the argument at the va_list
#     position of a call to a function declared (in this file, or the platform's vprintf family) with a va_list
#     parameter. fnvnx_va (fnvnx_compat.h) gives the address of the va_list to a char* / void* parameter, the
#     va_list itself to a va_list parameter, and hands an ordinary char* back unchanged to a char* parameter.

VA_HANDMADE = re.compile(r'\bchar\s*\*\s*(\w+)\s*=\s*\(\s*char\s*\*\s*\)\s*\(?\s*&\s*(\w+)\s*\+\s*1\s*\)?\s*;')
VA_VAR = re.compile(r'\bva_list\s+(\w+)\s*(?=[;,)=\[])')
VA_MACROS = {'va_start', 'va_end', 'va_arg', 'va_copy', '__builtin_va_start', '__builtin_va_end', 'fnvnx_va'}
VA_PLATFORM_LAST = {'vsprintf', 'vsnprintf', 'vprintf', 'vfprintf', 'vsscanf', '_vsnprintf', 'vsprintf_s',
                    '_vscprintf', '_vsnprintf_s', 'vswprintf', '_vswprintf'}
NOT_CALLS = {'if', 'while', 'for', 'switch', 'return', 'sizeof', 'decltype', 'alignof', '__alignof__'}

def _va_positions(text):
    """{function: (index of its va_list parameter, where that declaration is)} for the functions this file
    declares with one (a call only counts declarations before it)."""
    pos = {}
    for m in re.finditer(r'(?<!\w)([A-Za-z_]\w*)\s*\(([^;{}()]*\bva_list\b[^;{}()]*)\)', text):
        for i, p in enumerate(_split_args(m.group(2))):
            if re.match(r'^\s*(?:const\s+)?va_list\b', p) and len(p.split()) <= 3:
                pos.setdefault(m.group(1), (i, m.start()))
    return pos

def r_va_list(path, text, stats):
    """VA1: MSVC's char* va_list -> address of a real va_list (see above)."""
    if 'va_list' not in text and not VA_HANDMADE.search(text): return text
    def handmade(m):
        stats['VA1'] += 1
        v = 'fnvnx_va_' + m.group(1)
        return 'va_list %s; va_start(%s, %s); char* %s = (char*)&%s;' % (v, v, m.group(2), m.group(1), v)
    text = VA_HANDMADE.sub(handmade, text)
    va_vars = set(VA_VAR.findall(text))
    targets = _va_positions(text)
    out, pos = [], 0
    for m in re.finditer(r'(?<!\w)([A-Za-z_]\w*)\s*\(', text):
        name = m.group(1)
        if m.start() < pos or name in NOT_CALLS or name in VA_MACROS: continue
        before = text[max(0, m.start() - 60):m.start()]
        if not re.search(r'(?:\.|->)\s*$', before):                   # a declaration: return type before it
            word = re.search(r'([\w\*&>])\s*(?:::\s*)?(?:\w+\s*::\s*)*$', before)
            kw = re.search(r'(\w+)\s*$', before)
            if word and not (kw and kw.group(1) in ('return', 'case', 'else', 'do', 'throw', 'new')): continue
        close = _call_end(text, m.end() - 1)
        args = _split_args(text[m.end():close - 1])
        at = len(args) - 1 if name in VA_PLATFORM_LAST else (
            targets[name][0] if name in targets and targets[name][1] < m.start() else None)
        changed = False
        for i, a in enumerate(args):
            ident = re.match(r'^(\s*)([A-Za-z_]\w*)(\s*)$', a)
            if not ident or ident.group(2) in ('NULL', 'nullptr', '0'): continue
            if ident.group(2) in va_vars or i == at:
                args[i] = '%sfnvnx_va(%s)%s' % ident.groups()
                changed = True
        if not changed: continue
        out.append(text[pos:m.end()]); out.append(','.join(args)); out.append(')'); pos = close
        stats['VA1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- VIEW1
#
# Some units carry a partial view of a class (only the members that unit's original code needed, as the decomp
# reconstructed it) and then call a member the view lacks. When another definition of the same class in the decomp
# declares that member as a non-virtual member function, VIEW1 adds that declaration (without its body) to the
# partial view. A non-virtual member function takes no storage and no vtable slot, so the class layout cannot change
# (checked on the affected units with GCC's class dumps: identical sizes and offsets). The call then binds to the
# fuller definition's function (same class name, same signature) at link time. VIEW_MEMBERS lists the
# (class, member) pairs GCC reported missing (census c9); only declarations whose types the unit already names are
# added.
# NOT IN RULES: measured on the 10 units with these errors it adds 55 declarations and makes none of them clean
# (12->3, 26->22, 16->15 errors; the rest unchanged), while touching every definition of Actor / TESBoundObject.
# Kept for when those units' other errors are gone.

VIEW_MEMBERS = {
    'NiD3DPass': ['GetRenderStateGroup', 'IncRefCount', 'DecRefCount'],
    'TESBoundObject': ['GetFormType'],
    'MemRouter': ['heap'],
    'RouterX': ['heap'],
    'FUN_008b70d0_shell': ['FUN_008b70d0'],
    'P09PC': ['GetAnimation'],
    'P09ActorH': ['GetAnimation'],
    'hkpCdBody': ['getRootCollidable'],
    'FUN_00b355d0': ['size', 'begin'],
    'Actor': ['GetActorStateFlags'],
    'TESObjectWEAP': ['GetWeaponType'],
}
_VIEW_DECLS = None
CXX_WORDS = set('''const volatile unsigned signed int char short long float double bool void virtual static inline
    struct class enum typename operator return this true false wchar_t __int64 __cdecl __stdcall __thiscall
    __fastcall FNVNX_KEEP'''.split())

def _class_body_spans(text, cls):
    for m in re.finditer(r'\b(?:class|struct)\s+(?:__declspec\s*\((?:[^()]|\([^()]*\))*\)\s*)*%s\b\s*(?:final\s*)?'
                         r'(?::[^;{}]*)?\{' % re.escape(cls), text):
        yield m.end(), prepare.match_brace(text, m.end() - 1) - 1

def _member_decl(body, member):
    """The declaration of member function `member` in a class body (without its body), or None if it is virtual,
    static data, or absent."""
    depth, start = 0, 0
    i = 0
    while i < len(body):
        c = body[i]
        if c == '{':
            if depth == 0:
                stmt = body[start:i]
                if re.search(r'(?<![\w~])%s\s*\(' % re.escape(member), stmt) and stmt.count('(') >= 1:
                    return stmt
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0: start = i + 1
        elif c == ';' and depth == 0:
            stmt = body[start:i]
            if re.search(r'(?<![\w~])%s\s*\(' % re.escape(member), stmt): return stmt
            start = i + 1
        elif c == ':' and depth == 0 and re.search(r'\b(?:public|private|protected)\s*$', body[start:i]):
            start = i + 1
        i += 1
    return None

def _view_decls(path):
    global _VIEW_DECLS
    if _VIEW_DECLS is not None: return _VIEW_DECLS
    root, found = _roots(path), {}
    for top in ('src', 'include'):
        for dp, _, fs in os.walk(os.path.join(root, top)):
            for f in sorted(fs):
                if not f.endswith(('.h', '.cpp', '.inl', '.hpp')): continue
                with open(os.path.join(dp, f), encoding='utf-8', errors='surrogateescape') as h: t = h.read()
                for cls, members in VIEW_MEMBERS.items():
                    if cls not in t: continue
                    for a, b in _class_body_spans(t, cls):
                        body = COMMENT.sub(' ', t[a:b])
                        for mem in members:
                            if (cls, mem) in found: continue
                            d = _member_decl(body, mem)
                            if not d or re.search(r'\bvirtual\b', d): continue
                            d = re.sub(r'\s+', ' ', d).strip()
                            d = re.sub(r'\)\s*:\s*[\w:]+\s*\(.*$', ')', d)       # constructor initializer list
                            d = re.sub(r'^(?:FNVNX_KEEP\s+|__forceinline\s+|inline\s+)+', '', d)
                            found[(cls, mem)] = d
    _VIEW_DECLS = found
    return found

def _decl_type_names(d, mem):
    """Identifiers naming types in a member function declaration (parameter names left out)."""
    i = re.search(r'(?<![\w~])%s\s*\(' % re.escape(mem), d)
    ret, rest = d[:i.start()], d[i.end():]
    names = set(re.findall(r'\b[A-Za-z_]\w*\b', ret))
    params = rest[:rest.rfind(')')] if ')' in rest else rest
    for p in _split_args(params):
        ids = [w for w in re.findall(r'\b[A-Za-z_]\w*\b', re.sub(r'=.*', '', p)) if w not in CXX_WORDS]
        names |= set(ids[:-1] if len(ids) > 1 else ids)
    return names - CXX_WORDS

def r_complete_views(path, text, stats):
    """VIEW1: add missing non-virtual member function declarations to partial class views (see above)."""
    if not any(c in text for c in VIEW_MEMBERS): return text
    decls = _view_decls(path)
    edits = []
    for cls, members in VIEW_MEMBERS.items():
        if cls not in text: continue
        for a, b in _class_body_spans(text, cls):
            body = text[a:b]
            add = []
            for mem in members:
                d = decls.get((cls, mem))
                if not d or re.search(r'(?<![\w~])%s\s*\(' % re.escape(mem), body): continue
                names = _decl_type_names(d, mem) - {cls}
                if any(not re.search(r'\b%s\b' % re.escape(n), text[:a]) for n in names): continue
                add.append(d + ';')
            if add:
                edits.append((b, ' public: ' + ' '.join(add) + ' /* fnv-nx VIEW1 */ '))
                stats['VIEW1'] += len(add)
    if not edits: return text
    out, pos = [], 0
    for at, s in sorted(edits):
        out.append(text[pos:at]); out.append(s); pos = at
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- STATIC1
#
# MSVC placed a file-static variable (and a namespace-scope const, internal in C++) in the image like any other, and
# units.json binds its name to that address. GCC keeps such a variable local to the object, and the link step can
# only rebind global symbols, so the unit would use a private copy while everything else sees the image's (S13:
# fadermanager's s_faders at 0x011d8828). STATIC1 gives those variables external linkage: `static` is dropped, a
# namespace-scope `const` definition gets `extern`, and so does an array of unknown size declared without `extern`
# (`const void* DAT_010d0fc4[];`, which MSVC took as a reference to the image's table and GCC rejects as a definition
# of unknown size). Only names that a unit including the file binds to an image data address (`?name@@3...` /
# `_name` in its units.json symbols) at file scope are touched, and for the unsized arrays also address names
# (`DAT_<address>`, which the link binds by the address in the name).
# A namespace-scope static (`namespace fnv_007bd2a0 { static float s_fSpinTime; }`, S13: 77 with an image address,
# three of them uninitialised and written, so the private copy diverges for real: HUDMainMenu's instance pointer
# 0x011d96c0, roulettemenu's s_fSpinTime / s_bValidBet 0x011da904 / 0x011da900) is bound under its plain name
# (`_s_fSpinTime`), the way the link binds a C name: STATIC1 gives it C linkage where it stands,
# `extern "C" { float s_fSpinTime; }` (a const one `extern "C" { extern const ... }`), so the definition is the
# image's variable under that name. Only a name the file declares once.

STATIC_DEF = re.compile(r'^([ \t]*)static\s+(?!inline\b)((?:const\s+|volatile\s+)*[A-Za-z_][\w:<>,\s\*&]*?[\s\*&](\w+)'
                        r'\s*(?:\[[^\]]*\]\s*)*(?:=|;|\(|\{))', re.M)
UNSIZED_DECL = re.compile(r'^([ \t]*)(?:static\s+)?((?!(?:extern|typedef|friend|return)\b)(?:const\s+|volatile\s+)*'
                          r'[A-Za-z_][\w:<>,\s\*&]*?[\s\*&](\w+)\s*\[\s*\]\s*;)', re.M)
# an address name (DAT_010cc0ec) is bound by its address whatever units.json lists (tools/link: ADDR_IN_NAME)
ADDR_NAME = re.compile(r'^(?:DAT|g|UNK|PTR)_[0-9a-f]{8}$')
UNSIZED_ADDR = re.compile(r'\b(?:DAT|g|UNK|PTR)_[0-9a-f]{8}\s*\[\s*\]\s*;')
CONST_DEF = re.compile(r'^([ \t]*)(const\s+[A-Za-z_][\w:<>,\s\*&]*?[\s\*&](\w+)\s*(?:\[[^\]]*\]\s*)*=)', re.M)
DATA_NAME = re.compile(r'^\?(\w+)@@3')
_IMAGE_NAMES = None
_IMAGE_CNAMES = {}       # file -> names a unit including it binds to an image data address as `_name` (a C name)
_IMAGE_ADDRS = {}        # file -> {('c' | 'g', name): {addresses}}: `_name` / `?name@...@3...` (decorated) bindings
NOT_A_TYPE = {'return', 'case', 'goto', 'else', 'delete', 'throw', 'new', 'sizeof', 'typedef', 'using'}

def _image_bound_names(path):
    """{absolute file: {name}} of global variables bound to image data addresses by a unit that includes it."""
    global _IMAGE_NAMES
    if _IMAGE_NAMES is not None: return _IMAGE_NAMES
    import json
    _IMAGE_NAMES = {}
    root, uj = prepare._units_json(path)
    if not uj: return _IMAGE_NAMES
    inc = re.compile(r'^[ \t]*#[ \t]*include[ \t]*"([^"]+)"', re.M)
    texts = {}
    def text(f):
        if f not in texts:
            with open(f, encoding='utf-8', errors='surrogateescape') as h: texts[f] = h.read()
        return texts[f]
    for u in json.load(open(uj))['units']:
        names, cnames, addrs = set(), set(), {}
        for n, a in u.get('symbols', {}).items():
            if not 0x00fdf000 <= int(a, 16) < 0x02100000: continue
            q = re.match(r'^\?(\w+)@[\w@]*?@3', n)                # any decorated variable, namespaced too
            if q: addrs.setdefault(('g', q.group(1)), set()).add(int(a, 16))
            m = DATA_NAME.match(n)
            if m: names.add(m.group(1))
            elif n.startswith('_') and n[1:].isidentifier():
                names.add(n[1:]); cnames.add(n[1:]); addrs.setdefault(('c', n[1:]), set()).add(int(a, 16))
        if not names: continue
        todo, seen = [os.path.normpath(os.path.join(root, u['source']))], set()
        while todo:
            f = todo.pop()
            if f in seen or not os.path.isfile(f): continue
            seen.add(f)
            t = text(f)
            if 'static' in t or 'const' in t:
                _IMAGE_NAMES.setdefault(f, set()).update(names)
                if cnames:
                    _IMAGE_CNAMES.setdefault(f, set()).update(cnames)
                    fa = _IMAGE_ADDRS.setdefault(f, {})
                    for k, v in addrs.items(): fa.setdefault(k, set()).update(v)
            for i in inc.findall(t):
                for base in (os.path.dirname(f), os.path.join(root, 'include')):
                    g = os.path.normpath(os.path.join(base, i))
                    if os.path.isfile(g): todo.append(g); break
    return _IMAGE_NAMES

def _decl_end(text, i):
    """Index of the ';' that ends the declaration whose declarator text[i] follows (brackets, strings skipped)."""
    depth = 0
    for t in re.finditer(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/|[(){}\[\];]', text[i:], re.S):
        c = t.group()
        if c in '({[': depth += 1
        elif c in ')}]': depth -= 1
        elif c == ';' and depth == 0: return i + t.start()
        if depth < 0: return None
    return None

SCALAR_WORDS = set('volatile unsigned signed short long int char bool float double __int64 wchar_t BOOL BYTE WORD DWORD '
                   'UInt8 UInt16 UInt32 SInt32 hkUint8 hkUint16 hkUint32 hkInt32 hkReal'.split())

def _declared_once(text, name, spans, addrs, image=False):
    """True when the namespace static `name` is the file's only declaration of that name that its C name could
    meet: class members do not count, nor an `extern` declaration where every decorated binding of the name
    (`?name@@3...`, `?name@ns@@3...`) is the C name's one address (the same variable)."""
    bare = COMMENT.sub(lambda m: re.sub(r'[^\n]', ' ', m.group(0)), text)
    n = 0
    for m in re.finditer(r'(\w+|[>\*&])\s+\**%s\s*(?:\[[^\]]*\]\s*)*[;=,(\[{]' % re.escape(name), bare):
        if m.group(1) in NOT_A_TYPE: continue
        chain = prepare.scope_chain(spans, m.start())
        if chain and chain[-1][0] == 'class': continue
        if chain and chain[-1][0] == 'function': return False      # a local of that name
        line = bare[bare.rfind('\n', 0, m.start()) + 1:m.end()]
        same = len(addrs.get(('c', name), ())) == 1 and addrs.get(('g', name), addrs[('c', name)]) == addrs[('c', name)]
        if same and all(k == 'namespace' for k, _ in chain):
            if re.match(r'\s*extern\b(?!\s*")', line) and '=' not in line: continue
            if image and not re.search(r'=\s*$', line): continue   # declared only: another definition is the same one
        n += 1
    return n == 1 or image and n == 0

def r_image_statics(path, text, stats):
    """STATIC1: file-static / internal const variables bound to image addresses get external linkage, a namespace
    static bound by its plain name C linkage (see above)."""
    if 'static' not in text and 'const' not in text: return text
    key = os.path.normpath(os.path.abspath(path))
    names = _image_bound_names(path).get(key) or set()
    cnames = _IMAGE_CNAMES.get(key) or set()
    if not names and not UNSIZED_ADDR.search(text): return text
    spans, edits = None, {}
    for rx, kind in ((UNSIZED_DECL, 'unsized'), (STATIC_DEF, 'static'), (CONST_DEF, 'const')):
        for m in rx.finditer(text):
            if m.start() in edits: continue
            if m.group(3) not in names and not (kind == 'unsized' and ADDR_NAME.match(m.group(3))): continue
            if kind == 'const' and re.search(r'\bextern\b', text[text.rfind('\n', 0, m.start()) + 1:m.start()]): continue
            if spans is None: spans = prepare.scope_spans(text)
            chain = prepare.scope_chain(spans, m.start())
            if any(k != 'extern' for k, _ in chain):
                # a namespace static bound by its plain name: C linkage where it stands
                if kind == 'unsized' or m.group(3) not in cnames or any(k not in ('namespace', 'extern') for k, _ in chain):
                    continue
                end = _decl_end(text, m.end() - 1) if text[m.end() - 1] != ';' else m.end() - 1
                if end is None or '#' in text[m.end():end]: continue
                decl = text[m.start(2):end + 1]
                const = kind == 'const' or decl.startswith('const')
                # an uninitialised scalar / pointer is the image's variable: declared, not defined (the image holds it,
                # and a definition elsewhere in the file of the same C name, a STATIC1 global, is the same one)
                ty = text[m.start(2):m.start(3)]
                image = (text[m.end() - 1] == ';' and not const and
                         ('*' in ty or set(re.findall(r'\w+', ty)) <= SCALAR_WORDS))
                if not _declared_once(text, m.group(3), spans, _IMAGE_ADDRS.get(key, {}), image): continue
                edits[m.start()] = (m.start(), end + 1, m.group(1) + 'extern "C" { ' + ('extern ' if const or image else '')
                                    + decl + ' }')
                continue
            new = m.group(1) + ('' if kind == 'static' else 'extern ') + m.group(2)
            edits[m.start()] = (m.start(), m.end(), new)
    if not edits: return text
    out, pos = [], 0
    for a, b, r in sorted(edits.values()):
        if a < pos: continue
        out.append(text[pos:a]); out.append(r); pos = b
        stats['STATIC1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- FALL1
#
# Some decomp functions return a value on every path but the last: the body was reconstructed without its final
# return. MSVC then returned whatever eax held; GCC treats flowing off the end as unreachable and emits no epilogue,
# so on AArch64 the code runs into the next function (S13: PathingLocation::HasValidTriangle 004ff7e0). FALL1 puts
# `__builtin_trap()` at the end of every non-void function whose last statement is not a return / throw: where the
# end is unreachable it is dead code, where it is reached the game stops there with a trap instead of executing the
# next function. (What MSVC returned there has to be read from the x86 code per site; S1b patches can replace a trap.)

FALL_SKIP_RET = re.compile(r'^(?:extern\s*"C\+?\+?"\s*|(?:FNVNX_KEEP|inline|static|virtual|explicit|extern|constexpr|'
                           r'__forceinline|__inline|friend)\s+|template\s*<[^{};]*>\s*)+')
FALL_DECLARATOR = re.compile(r'((?:::)?(?:[A-Za-z_]\w*(?:\s*<[^<>;{}()]*(?:<[^<>;{}()]*>)?[^<>;{}()]*>)?\s*::\s*)*'
                             r'(?:~?[A-Za-z_]\w*|operator\s*(?:\(\)|[^\s(]+)))\s*\(')
FALL_END = re.compile(r'(?:\breturn\b[^;]*;|\bthrow\b[^;]*;|__builtin_trap\s*\(\s*\)\s*;|__builtin_unreachable\s*\(\s*\)\s*;|'
                      r'\b(?:abort|exit|_exit|ExitProcess|ExitThread|terminate|_CxxThrowException|__CxxThrowException|'
                      r'FUN_00ec7894)\s*\([^;]*\)\s*;)\s*$')

def _last_statement(body):
    """The last statement at the top level of a function body (after the last `;` or `}` that closes one)."""
    depth, last, q, i = 0, 0, None, 0
    while i < len(body):
        c = body[i]
        if q:
            if c == '\\': i += 1
            elif c == q: q = None
        elif c in '"\'': q = c
        elif c in '({[': depth += 1
        elif c in ')]': depth -= 1
        elif c == '}':
            depth -= 1
            if depth == 0: last = i + 1
        elif c == ';' and depth == 0 and i + 1 < len(body.rstrip()):
            last = i + 1
        i += 1
    return body[last:].strip()

def r_falloff_traps(path, text, stats):
    """FALL1: `__builtin_trap()` at the end of non-void functions that do not end in a return (see above)."""
    if '{' not in text: return text
    spans = prepare.scope_spans(text)
    edits, func_end = [], -1
    for o, c, k, h in spans:
        if k != 'function' or o < func_end: continue        # only outermost function bodies
        func_end = c
        head = prepare.HEAD_DECOR.sub(' ', prepare.HEAD_NOISE.sub('', h)).strip()
        head = re.sub(r'^(?:\s*(?:public|private|protected)\s*:(?!:))+', '', head).strip()   # access label
        m = None
        for mm in FALL_DECLARATOR.finditer(head):
            m = mm                                          # the declarator is the first name followed by `(`
            break
        if not m: continue
        ret = FALL_SKIP_RET.sub('', head[:m.start()]).strip()
        ret = prepare.HEAD_DECOR.sub(' ', ret).strip()
        name = m.group(1)
        if not ret or re.search(r'(?:^|\W)(?:if|while|for|switch|catch|return|else|do|sizeof)$', ret): continue
        ret = re.sub(r'\b(?:__thiscall|__cdecl|__stdcall|__fastcall|__clrcall|WINAPI|CALLBACK|APIENTRY|NTAPI|WINAPIV|'
                     r'STDMETHODCALLTYPE|__declspec|FNV_\w+|FNVNX_\w+|FORCEINLINE|NOINLINE)\b', ' ', ret).strip()
        # linkage / storage / function specifiers are not part of the return type: `extern "C" void f(void)` is void
        # (S1b found FALL1 trapping all 10,957 XC1 dyninit/dynexit functions without this)
        ret = re.sub(r'(?:\bextern\s*)?"C\+?\+?"|\b(?:extern|static|inline|virtual|explicit|friend|constexpr|'
                     r'__inline|__forceinline)\b', ' ', ret)
        ret = re.sub(r'\s+', ' ', ret).strip()
        if re.fullmatch(r'(?:const\s+|volatile\s+)*void', ret) or 'operator' in name and not ret: continue
        # the head window can start inside a long parameter list: then the first "declarator" is a base or member in
        # a constructor's mem-initializer list, or a name inside the parameters, and `ret` is parameter text (S1b:
        # FUN_00a7b630::FUN_00a7b630, ImageSpaceModifierInstanceDRB ctor). Constructors return nothing; a real
        # return type has no `)` and no `,` outside template brackets.
        if re.search(r'\)\s*:(?!:)', head[:m.end()]): continue
        flat = ret
        while True:
            nflat = re.sub(r'<[^<>]*>', '', flat)
            if nflat == flat: break
            flat = nflat
        if re.search(r'[),]', flat): continue
        if re.search(r'[=,(]$', ret) or ret.endswith(('return', 'new', 'delete')): continue   # an expression
        if not re.search(r'[A-Za-z_]', ret): continue
        if re.search(r'\\[ \t]*\n', text[o:c]): continue          # inside a #define (continued lines)
        body = COMMENT.sub(' ', text[o + 1:c]).rstrip()
        # the last top-level statement must be the return (`if (x) return 1;` at the end still falls off);
        # an empty body falls off too (`virtual T* V0() {}`)
        if body and FALL_END.match(_last_statement(body)): continue
        edits.append((c, ' __builtin_trap(); /* fnv-nx FALL1 */ '))
        stats['FALL1'] += 1
    if not edits: return text
    out, pos = [], 0
    for at, s in edits:
        out.append(text[pos:at]); out.append(s); pos = at
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- DUPINT1
#
# The decomp sometimes declares the same function twice in one unit, differing only in an integer parameter's type
# (`_fnv_zlib_memset(void*, int, unsigned int)` and `(void*, int, int)`), and the recipe binds both decorations to
# one address. On MSVC size_t is unsigned int, so `sizeof` arguments matched one of them exactly; on AArch64 ILP32
# size_t is unsigned long and the call is ambiguous. DUPINT1 keeps the first declaration and drops the later ones of
# the same name, arity and scope when they differ only in integer types and every decorated spelling of that name
# in the unit's units.json symbols binds the same address: the calls reach the same function with the same bits.
# (With C linkage the two are a conflict outright: `extern "C" void _delete(void*, unsigned int)` and `(void*, size_t)`.)

INT_TYPES = re.compile(r'^(?:(?:const|volatile|signed|unsigned|short|long|int|char|__int8|__int16|__int32|__int64|'
                       r'size_t|DWORD|UINT|INT|LONG|ULONG|WORD|BYTE|BOOL)\s*)+$')
_UNIT_SYMBOLS = None

def _unit_symbols(path):
    """{absolute unit source: {decorated name: address}}"""
    global _UNIT_SYMBOLS
    if _UNIT_SYMBOLS is None:
        import json
        _UNIT_SYMBOLS = {}
        root, uj = prepare._units_json(path)
        if uj:
            for u in json.load(open(uj))['units']:
                _UNIT_SYMBOLS[os.path.normpath(os.path.join(root, u['source']))] = u.get('symbols', {})
    return _UNIT_SYMBOLS.get(os.path.normpath(os.path.abspath(path)))

def _param_type(p):
    p = re.sub(r'=.*', '', p).strip()
    ids = re.findall(r'[A-Za-z_]\w*', p)
    if len(ids) > 1 and not re.search(r'[\*&]\s*$', p) and ids[-1] not in ('int', 'long', 'short', 'char', 'unsigned'):
        p = p[:p.rfind(ids[-1])]                          # drop the parameter name
    return re.sub(r'\s+', ' ', p).strip()

def r_duplicate_int_overloads(path, text, stats):
    """DUPINT1: drop later integer-only-different duplicate prototypes bound to one address (see above). Only a pair
    of signatures with the same return type is handled; anything else is left as it is."""
    syms = _unit_symbols(path)
    if not syms: return text
    groups, spans = {}, None
    for m in prepare.PROTO.finditer(text):
        name = m.group(1)
        if name.startswith('operator'): continue
        decl = re.sub(r'\s*//[^\n]*$', '', m.group(0))   # PROTO takes a trailing // comment along
        at = decl.index('(', decl.index(name) + len(name))
        params = _split_args(decl[at + 1:decl.rindex(')')])
        if spans is None: spans = prepare.scope_spans(text)
        chain = prepare.scope_chain(spans, m.start())
        if any(k not in ('namespace', 'extern') for k, _ in chain): continue
        ret = re.sub(r'\s+', ' ', re.sub(r'\b(?:extern|static|inline|__cdecl|__stdcall|__fastcall|__thiscall)\b|"C"', ' ',
                                        decl[:decl.index(name)])).strip()
        key = (name, len(params), tuple(h for _, h in chain))
        groups.setdefault(key, []).append((tuple(_param_type(p) for p in params), ret, m))
    drops = []
    for (name, _, _), decls in groups.items():
        sigs = []
        for types, ret, m in decls:
            if (types, ret) not in [(t, r) for t, r, _ in sigs]: sigs.append((types, ret, m))
        if len(sigs) != 2 or sigs[0][1] != sigs[1][1]: continue
        a, b = sigs[0][0], sigs[1][0]
        if not all(x == y or (INT_TYPES.match(x) and INT_TYPES.match(y)) for x, y in zip(a, b)): continue
        addrs = {v for k, v in syms.items()                # C++ decorations, or the C name (_name, _name@N)
                 if re.match(r'^\?%s@@|^_%s(?:@\d+)?$' % (re.escape(name), re.escape(name)), k)}
        if len(addrs) != 1: continue
        drops += [m for types, ret, m in decls if types == b]
    if not drops: return text
    out, pos = [], 0
    for m in sorted(drops, key=lambda x: x.start()):
        out.append(text[pos:m.start()]); out.append('/* fnv-nx DUPINT1 */' + '\n' * m.group(0).count('\n')); pos = m.end()
        stats['DUPINT1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- NEW4

PLACEMENT_USE = re.compile(r'(?<![\w.>])(?:::\s*)?new\s*\((?!\s*\))')

def r_placement_include(path, text, stats):
    """NEW4: a file that uses placement new (`new (p) T`) without a declaration of it gets <new> (or fnvnx_new.h where
    it carries its own MSVC std::exception) on top. MSVC declared the placement form through its own headers."""
    if 'new' not in text: return text
    if not any(not re.search(r'\boperator\s*$', text[max(0, m.start() - 20):m.start()])
               for m in PLACEMENT_USE.finditer(text)): return text      # only operator new(n) calls
    if re.search(r'#\s*include\s*[<"](?:new|fnvnx_new\.h)[>"]', text) or re.search(r'operator\s+new\s*\([^;{})]*,\s*void\s*\*', text):
        return text
    stats['NEW4'] += 1
    return '#include %s /* fnv-nx NEW4 */\n' % prepare.new_header(text) + text

# ---------------------------------------------------------------------------------------------------------- ALLOC1
#
# The game's global allocation functions are its own (0x00401000 operator new, 0x00401030 operator delete, ...) and the
# link binds every call to them by address. A unit that defines a replaceable global allocation function without an
# `// @fnv` tag carries a stand-in the original never had in that object; under GCC it would replace the global
# operator for the whole program. ALLOC1 drops such definitions (all forms: new/new[]/delete/delete[], sized,
# nothrow); placement forms are NEW2's.

ALLOC_DEF = re.compile(r'^[ \t]*(?:FNVNX_KEEP\s+)?(?:static\s+)?(?:inline\s+|__forceinline\s+|FNV_FORCEINLINE\s+)?'
                       r'(?:void\s*\*|void)\s*(?:__cdecl\s+)?operator\s+(?:new|delete)\s*(?:\[\s*\])?\s*'
                       r'\(([^;{}()]*)\)\s*(?:noexcept|throw\s*\(\s*\))?\s*\{', re.M)
_SIZE_T = r'(?:(?:std::)?size_t|__SIZE_TYPE__|unsigned(?:\s+(?:int|long))?)\s*\w*'
REPLACEABLE_PARAMS = re.compile(r'^\s*(?:%s|void\s*\*\s*\w*(?:\s*,\s*%s)?)\s*(?:,\s*const\s+(?:std::)?nothrow_t\s*&\s*\w*)?\s*$'
                                % (_SIZE_T, _SIZE_T))
FNV_TAG_BEFORE = re.compile(r'//[ \t]*@fnv[ \t]+0x[0-9a-fA-F]+[^\n]*\n(?:[ \t]*//[^\n]*\n)*[ \t]*$')

def r_global_allocators(path, text, stats):
    """ALLOC1: drop untagged file-scope definitions of the replaceable global allocation functions (see above)."""
    if 'operator' not in text: return text
    spans, out, pos = None, [], 0
    for m in ALLOC_DEF.finditer(text):
        if m.start() < pos: continue
        if not REPLACEABLE_PARAMS.match(m.group(1)): continue  # only operator new(size), delete(void*[, size])
        if FNV_TAG_BEFORE.search(text[max(0, m.start() - 600):m.start()]): continue
        if spans is None: spans = prepare.scope_spans(text)
        if any(k != 'extern' for k, _ in prepare.scope_chain(spans, m.start())): continue
        end = prepare.match_brace(text, m.end() - 1)
        out.append(text[pos:m.start()]); out.append('/* fnv-nx ALLOC1 */' + '\n' * text.count('\n', m.start(), end))
        pos = end
        stats['ALLOC1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- TPL2
#
# MSVC 2008 looks names up when it instantiates a template; GCC binds the non-dependent part at the definition. In the
# decomp's copies of MSVC's STL, a class template inside namespace N calls `_DestroyS(p)` and N defines its own
# `_DestroyS` template only further down: GCC finds the global one at the definition and N's through ADL at the
# instantiation - ambiguous - while MSVC saw only N's (it hides the global one). TPL2 declares such a function template
# at the start of its namespace block, so ordinary lookup inside N finds N's, as MSVC did. Only templates whose
# declaration names nothing but their own template parameters and built-in types are moved forward.

TPL_FN_DEF = re.compile(r'^[ \t]*(template\s*<([^<>{};]*)>\s*)((?:inline\s+|static\s+)*)([\w \t\*&:<>,]*?[\s\*&])'
                        r'([A-Za-z_]\w*)\s*\(([^;{}()]*)\)\s*\{', re.M)
TPL_PLAIN = set('''const volatile unsigned signed int char short long float double bool void class typename inline static
    __cdecl __stdcall __thiscall __fastcall size_t'''.split())

def r_namespace_template_decls(path, text, stats):
    """TPL2: forward-declare a namespace's function templates that its earlier class templates call (see above)."""
    if 'template' not in text or 'namespace' not in text: return text
    spans = prepare.scope_spans(text)
    inserts = []
    for o, c, k, h in spans:
        if k != 'namespace' or h == 'namespace': continue
        block = text[o + 1:c]
        decls = []
        for m in TPL_FN_DEF.finditer(block):
            # top level of this namespace only
            inner = [sp for sp in spans if o < sp[0] < o + 1 + m.start() < sp[1]]
            if inner: continue
            name = m.group(5)
            if not re.search(r'(?<![\w.>:~])%s\s*\(' % re.escape(name), block[:m.start()]): continue
            tparams = set(re.findall(r'(?:class|typename)\s+(\w+)', m.group(2)))
            words = set(re.findall(r'[A-Za-z_]\w*', m.group(4) + ' ' + m.group(6)))
            params = _split_args(m.group(6))
            pnames = set()
            for p in params:
                ids = re.findall(r'[A-Za-z_]\w*', p)
                if len(ids) > 1: pnames.add(ids[-1])
            if words - tparams - TPL_PLAIN - pnames: continue
            decls.append('%s%s%s%s(%s);' % (m.group(1), m.group(3), m.group(4), name, m.group(6)))
        if decls:
            inserts.append((o + 1, ' ' + ' '.join(decls) + ' /* fnv-nx TPL2 */ '))
            stats['TPL2'] += len(decls)
    if not inserts: return text
    out, pos = [], 0
    for at, s in sorted(inserts):
        out.append(text[pos:at]); out.append(s); pos = at
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- MATH1

MATH_OVERLOAD_DEF = re.compile(
    r'^[ \t]*(?:extern\s*"C"\s*)?(?:inline|__inline|__forceinline)\s+(?:float|long\s+double|double)\s+(?:__cdecl\s+)?'
    r'(abs|(?:fabs|sqrt|sin|cos|tan|asin|acos|atan|atan2|exp|log|log10|pow|floor|ceil|fmod|modf|frexp|ldexp|sinh|cosh|'
    r'tanh)f?)\s*\(([^;{}()]*)\)\s*\{', re.M)

def r_math_overloads(path, text, stats):
    """MATH1: drop the decomp's copies of MSVC <math.h>'s C++ overloads (`inline float sqrt(float _X)
    { return sqrtf(_X); }`, also long double and the double (int) forms) at file scope; the target's <math.h> defines
    the same overloads (std::sqrt(float) brought to the global namespace), so the copy is a redefinition and its calls
    ambiguous. Also the x86 header's inline float functions themselves (`inline float fabsf(float _X) { return
    (float)fabs((double)_X); }`, also `extern "C" inline`): <math.h> declares fabsf as the C function. The header
    takes the place of the first one. In a namespace that takes the global overloads (`using ::fabs;`) an untagged
    copy goes too; next to the namespace's own `extern "C" double fabs(double);` (DECL1 keeps it, DECL1-MATHNS) the
    copy stays: MSVC's lookup in the namespace found the copy for a float argument, and GCC takes both."""
    if 'inline' not in text and '__forceinline' not in text: return text
    spans, out, pos, have = None, [], 0, False
    for m in MATH_OVERLOAD_DEF.finditer(text):
        if m.start() < pos: continue
        if spans is None: spans = prepare.scope_spans(text)
        chain = prepare.scope_chain(spans, m.start())
        in_ns = bool(chain) and all(k == 'namespace' for k, _ in chain)
        if in_ns:
            # in a namespace only where the namespace takes the global overloads (`using ::fabs;`): the copy
            # collides with them. A copy tagged as a function of the binary (`// @fnv`) is kept.
            o = max(sp[0] for sp in spans if sp[0] < m.start() < sp[1])
            before = text[o:m.start()]
            name = m.group(1)
            if not re.search(r'\busing\s+::\s*%s\s*;' % name, before): continue
            if re.search(r'//\s*@fnv\b[^\n]*\n(?:[ \t]*//[^\n]*\n)*[ \t]*$', text[max(0, m.start() - 300):m.start()]): continue
        elif any(k not in ('extern',) or '"C"' in h for k, h in chain): continue
        end = prepare.match_brace(text, m.end() - 1)
        nl = '\n' * text.count('\n', m.start(), end)
        out.append(text[pos:m.start()])
        out.append(('/* fnv-nx MATH1 */' if have or in_ns else '#include <math.h> /* fnv-nx MATH1 */') + nl)
        have, pos = have or not in_ns, end
        stats['MATH1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- SPEC2

# Files where GCC reports "specializing member ... requires 'template<>' syntax" (census c16): definitions of members
# of a class template specialization, which MSVC took without `template <>`.
SPEC2_FILES = ('staging/blk_00440000_od.cpp', 'staging/blk_006e0000_od.cpp', 'staging/blk_00b40000_od_ob1_variant.cpp',
               'staging/blk_00b40000_od_ob1_variant_rtm_variant.cpp', 'unmapped/bssimplearray.cpp')

def r_specialization_prefix(path, text, stats):
    """SPEC2: `template <>` in front of the decomp's definitions of members of class template specializations
    (`void BSTFreeList<QueuedMessage>::f(...) {`) at file scope in SPEC2_FILES, as C++ requires and MSVC did not.
    Members of an explicitly specialized class (`template <> class T<A> {...}`) take no prefix and are left alone."""
    if '>::' not in text or not path.replace(os.sep, '/').endswith(tuple('/src/' + f for f in SPEC2_FILES)):
        return text
    spans, edits = None, []
    squash = lambda x: re.sub(r'\s+', '', x)
    explicit = [squash(c) for c in re.findall(r'\btemplate\s*<\s*>\s*(?:class|struct)\s+\w+\s*<([^{};]*)>\s*(?::[^{};]*)?\{',
                                              text)]
    for m in SPEC_DEF.finditer(text):
        if m.group(2): continue
        if not text[m.end():].lstrip().startswith(('{', ':')): continue          # a definition
        tmpl, args = m.group(5), m.group(6).strip()
        if tmpl in ('static_cast', 'reinterpret_cast', 'const_cast', 'dynamic_cast'): continue
        if not args or any(TEMPLATE_PARAM_LIKE.match(a) for a in args.split(',')): continue
        if squash(args) in explicit: continue
        before = re.sub(r'(?://[^\n]*\n|/\*.*?\*/|\s)+$', '', text[max(0, m.start() - 400):m.start()], flags=re.S)
        if re.search(r'\btemplate\s*<[^\n]*>$', before): continue                  # already a template head
        if spans is None: spans = prepare.scope_spans(text)
        if any(k != 'namespace' for k, _ in prepare.scope_chain(spans, m.start())): continue
        edits.append(m.start(3))
        stats['SPEC2'] += 1
    if not edits: return text
    out, pos = [], 0
    for at in edits:
        out.append(text[pos:at]); out.append('template <> '); pos = at
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- LEX1
#
# Tokens MSVC 2008 lexed its own way: the `i64` / `ui64` integer suffixes, a hex literal whose last digit is e/E
# directly followed by + or - (`char pad2[0xae-0x34]`: one pp-number in standard C++, a subtraction to MSVC) and a
# string literal directly followed by an identifier (`extern"C"enum`: a C++11 user-defined literal). LEX1 writes the
# standard spelling of the same tokens: `LL` / `ULL`, a space before the sign, a space after the literal. It also
# drops the `inline` MSVC accepted next to `__forceinline` (an inline specifier itself on the target), and writes a
# functional cast to a multi-word type (`unsigned long(-1)`) as the C-style cast of that type.

LEX_TOKENS = re.compile(r'("(?:\\.|[^"\\\n])*")(?=[A-Za-z_])|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|//[^\n]*|'
                        r'/\*.*?(?:\*/|\Z)|(?<![\w.])(0[xX][0-9a-fA-F]*[eE])(?=[+-])|'
                        r'(?<![\w.])((?:0[xX][0-9a-fA-F]+|\d+))([uU]?)[iI]64\b', re.S)

# a functional cast to a multi-word type (`key = unsigned long(-1);`, `reinterpret_cast<void*>(unsigned int(id))`), which
# MSVC takes and C++ does not: written as the C-style cast of the same type, `((unsigned long)(-1))`
MULTIWORD_CAST = re.compile(r'(?:(?<=[=(,!?:>+\-*/%&|^~\[])|(?<=\breturn))(\s*)'
                            r'((?:unsigned|signed)\s+(?:long\s+long|long|int|short|char)(?:\s+int)?|long\s+(?:long|double|int)|'
                            r'short\s+int)\s*\((?!\s*[\*&])')
# what a declarator or a function type has in those parentheses: `unsigned int (__cdecl* m_pfnRead)(...)`,
# `Delegate<unsigned char(unsigned int)>`
CAST_NOT_EXPR = re.compile(r'^(?:__\w+|(?:const|volatile|unsigned|signed|int|char|short|long|float|double|void|bool|struct|'
                           r'class|enum|union)\b)')

def _multiword_casts(text, stats):
    out, pos = [], 0
    for m in MULTIWORD_CAST.finditer(text):
        if m.start() < pos: continue
        close = _call_end(text, m.end() - 1)
        inner = text[m.end():close - 1].strip()
        if CAST_NOT_EXPR.match(inner) or len(_split_args(inner)) > 1 or re.search(r'::\s*\*|[\*&]$', inner): continue
        lead = text[:m.start()].rstrip()
        if re.match(r'^[A-Za-z_]\w*$', inner) and (lead.endswith(',') or (lead.endswith('(') and not re.search(
                r'(?:>|\b(?:unsigned|signed|int|char|short|long|return))\s*\($', lead))):
            continue                                       # `f(int a, unsigned long (b))` may be a declarator
        arg = inner or '0'
        out.append(text[pos:m.start()]); out.append('%s((%s)(%s))' % (m.group(1), re.sub(r'\s+', ' ', m.group(2)), arg))
        pos = close
        stats['LEX1'] += 1
    if not out: return text
    out.append(text[pos:])
    return ''.join(out)

def r_msvc_tokens(path, text, stats):
    """LEX1: MSVC-only token spellings -> standard ones (see above)."""
    if not any(k in text for k in ('"', 'i64', 'I64', 'unsigned', 'signed', 'long', 'short', '__forceinline')) and \
            not re.search(r'0[xX][0-9a-fA-F]*[eE][+-]', text):
        return text
    out, pos = [], 0
    for m in LEX_TOKENS.finditer(text):
        if m.group(1):
            out.append(text[pos:m.end()]); out.append(' '); pos = m.end()
        elif m.group(2):
            out.append(text[pos:m.end()]); out.append(' '); pos = m.end()
        elif m.group(3):
            out.append(text[pos:m.start()]); out.append(m.group(3) + ('ULL' if m.group(4) else 'LL')); pos = m.end()
        else:
            continue
        stats['LEX1'] += 1
    if out:
        out.append(text[pos:])
        text = ''.join(out)
    if 'unsigned' in text or 'long' in text or 'signed' in text:
        text = _multiword_casts(text, stats)
    if '__forceinline' in text:
        text, n = re.subn(r'^([ \t]*(?:static\s+)?)(?:inline\s+__forceinline|__forceinline\s+inline)\b',
                          lambda m: m.group(1) + '__forceinline', text, flags=re.M)
        stats['LEX1'] += n
    return text

# ---------------------------------------------------------------------------------------------------------- STD1
#
# Three units declare the std::abs(float) they call as a static member of a file-scope `struct std` (units.json:
# ?abs@std@@SAMM@Z -> 0x00408840, the game's fabsf, which 23 other units bind as ?abs@std@@YAMM@Z). Here `std` is the
# namespace of the C++ headers, so the struct cannot be declared. STD1 replaces such a struct, when every member is
# a <cmath> function, by `#include <cmath>`: the calls `std::abs(x)` then reach the standard overload, fabsf.

STD_STRUCT = re.compile(r'^struct\s+std\s*\{((?:\s*static\s+[^;{}()]*\([^;{}()]*\)\s*;)+)\s*\}\s*;', re.M)
STD_MATH = set('abs fabs sqrt floor ceil sin cos tan asin acos atan atan2 exp log log10 pow fmod'.split())

def r_std_struct(path, text, stats):
    """STD1: a decomp `struct std { static float abs(float); };` at file scope -> `#include <cmath>`."""
    if 'struct std' not in text: return text
    spans, out, pos = None, [], 0
    for m in STD_STRUCT.finditer(text):
        if spans is None: spans = prepare.scope_spans(text)
        if prepare.scope_chain(spans, m.start()): continue
        if not set(re.findall(r'(\w+)\s*\(', m.group(1))) <= STD_MATH: continue
        out.append(text[pos:m.start()])
        out.append('#include <cmath> /* fnv-nx STD1 */' + '\n' * m.group(0).count('\n')); pos = m.end()
        stats['STD1'] += 1
    if not out: return text
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- SPEC3
#
# The decomp writes explicit specializations of global class templates inside its per-function namespaces:
#     namespace fnv_009dfa00 { class PathingRequest {...}; template <> class NiPointer<PathingRequest> { PathingRequest* p; }; }
# MSVC looked the names in the body up from that namespace. GCC (-fpermissive) makes the specialization a member of
# the template's namespace and looks them up there, so it finds the global forward declaration of the same name, or
# nothing. SPEC3 qualifies the names in such a body that the enclosing namespace declares as types before it
# (`fnv_009dfa00::PathingRequest* p`): the declarations MSVC found.

SPEC3_HEAD = re.compile(r'\btemplate\s*<\s*>\s*(?:class|struct)\s+([A-Za-z_]\w*)\s*<')
NS_TYPE_DECL = re.compile(r'\b(?:class|struct|union|enum)\s+(?:__declspec\s*\([^()]*(?:\([^()]*\))?[^()]*\)\s*)?'
                          r'([A-Za-z_]\w*)(?=\s*(?:;|\{|:(?!:)))|\btypedef\b[^;{}]*?\b([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*;')
BODY_TOKENS = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|//[^\n]*|/\*.*?(?:\*/|\Z)|\b[A-Za-z_]\w*\b', re.S)

def _angle_end(text, at):
    """Index just past the '>' closing the '<' at text[at] (parentheses nest, '->' is not a bracket)."""
    depth, i, n = 0, at, len(text)
    while i < n:
        c = text[i]
        if c in '<([': depth += 1
        elif c in ')]': depth -= 1
        elif c == '>' and text[i - 1] != '-':
            depth -= 1
            if depth == 0: return i + 1
        elif c in ';{}': return -1
        i += 1
    return -1

def r_namespace_specializations(path, text, stats):
    """SPEC3: names in the body of an explicit class specialization written inside a namespace, which that namespace
    declares as types, get the namespace's qualification (see above)."""
    if 'namespace' not in text or not SPEC3_HEAD.search(text): return text
    spans = prepare.scope_spans(text)
    heads = lambda chain: [h for _, h in chain]
    decls = {}                                           # namespace path -> [(pos, name)]
    for m in NS_TYPE_DECL.finditer(text):
        chain = prepare.scope_chain(spans, m.start())
        if not chain or any(k != 'namespace' for k, _ in chain): continue
        decls.setdefault(tuple(heads(chain)), []).append((m.start(), m.group(1) or m.group(2)))
    edits = []
    for m in SPEC3_HEAD.finditer(text):
        chain = prepare.scope_chain(spans, m.start())
        if not chain or any(k != 'namespace' for k, _ in chain): continue
        key = tuple(heads(chain))
        tmpl = m.group(1)
        if re.search(r'\btemplate\s*<[^<>]*\w[^<>]*>\s*(?:class|struct)\s+' + tmpl + r'\b', text) and any(
                tuple(heads(prepare.scope_chain(spans, p.start()))) == key
                for p in re.finditer(r'\btemplate\s*<[^<>]*\w[^<>]*>\s*(?:class|struct)\s+' + tmpl + r'\b', text)):
            continue                                     # the template is the namespace's own
        end = _angle_end(text, m.end() - 1)
        if end < 0: continue
        brace = re.match(r'[^;{}]*\{', text[end:])
        if not brace: continue
        o = end + brace.end() - 1
        c = prepare.match_brace(text, o)
        names = {n for p, n in decls.get(key, ()) if p < m.start() and n != tmpl}
        if not names: continue
        qual = '::'.join(h.split()[-1] for h in key) + '::'
        for t in BODY_TOKENS.finditer(text, end, c):     # the base clause too (`: public MoveNode454`)
            w = t.group()
            if w not in names: continue
            before = text[max(0, t.start() - 2):t.start()].rstrip()
            if before.endswith(('::', '->', '.', '~')): continue
            edits.append((t.start(), qual))
    if not edits: return text
    out, pos = [], 0
    for at, q in sorted(set(edits)):
        out.append(text[pos:at]); out.append(q); pos = at
        stats['SPEC3'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- DTOR1
#
# GCC emits a class's vtable where its key function (the first virtual member not defined in the class) is defined,
# and with it the implicit virtual destructor, which destroys every member: a `NiPointer<QueuedFile>` member
# instantiates NiPointer<QueuedFile>::~NiPointer(), whose body calls m_pObject->DecRefCount() and so needs a complete
# QueuedFile with that member. MSVC emits a vtable where an object is constructed and never needed that destructor in
# these units (middlehighprocess, tileimage, grenadeprojectile, skytexturemanager...), where the pointee is only
# forward-declared, or declared without DecRefCount, in the whole unit. DTOR1 declares the explicit specialization
# `template <> NiPointer<T>::~NiPointer();` for each such T in front of the unit's first line of code, after its
# includes: nothing in the unit instantiates the destructor then, and a use of it is a call of the game's destructor
# (the link binds it like any other declared function, e.g. ??1?$NiPointer@VQueuedFile@@@@QAE@XZ -> 0x0044cbf0).

NIPTR_MEMBER = re.compile(r'\bNiPointer\s*<\s*([A-Za-z_]\w*)\s*>\s*[A-Za-z_]\w*\s*(?:\[[^\]]*\]\s*)?;')
NIPTR_DTOR = re.compile(r'~NiPointer\s*\(\s*\)\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*DecRefCount')
_CLOSURE_CACHE = {}

def _closure(path, text):
    """[(file, text)] of the text and every local header it includes, transitively (comments removed)."""
    root, out, todo, done = _roots(path), [(path, COMMENT.sub(' ', text))], [(path, text)], set()
    while todo:
        fpath, ftext = todo.pop()
        for inc in re.findall(r'^[ \t]*#[ \t]*include[ \t]*"([^"]+)"', ftext, re.M):
            f = _resolve(inc, os.path.dirname(fpath), root)
            if not f or f in done: continue
            done.add(f)
            if f not in _CLOSURE_CACHE:
                with open(f, encoding='utf-8', errors='surrogateescape') as h: t2 = h.read()
                _CLOSURE_CACHE[f] = (t2, COMMENT.sub(' ', t2))
            t2, bare = _CLOSURE_CACHE[f]
            out.append((f, bare))
            todo.append((f, t2))
    return out

def r_incomplete_pointee_dtors(path, text, stats):
    """DTOR1: explicit specialization declarations of NiPointer<T>::~NiPointer() for member pointees T the unit never
    completes with a DecRefCount (see above)."""
    if not path.endswith('.cpp') or '#include' not in text: return text
    # the unit's first line of code at file scope, after its leading includes
    lines, pos, lead_end = text.split('\n'), 0, None
    for line in lines:
        s = line.strip()
        if s.startswith('#'):
            if not re.match(r'#\s*(?:include|pragma)\b', s): return text      # conditional prologue: leave it
        elif s and not s.startswith('//'):
            lead_end = pos; break
        pos += len(line) + 1
    if lead_end is None: return text
    whole = _closure(path, text)
    alltext = '\n'.join(t for _, t in whole)
    if 'NiPointer' not in alltext or not NIPTR_DTOR.search(alltext): return text
    lead = '\n'.join(t for _, t in _closure(path, text[:lead_end]))
    if not NIPTR_DTOR.search(lead): return text
    # every NiPointer template the unit defines must declare that destructor (main.h defines its own without one,
    # behind a macro that renames the canonical template)
    for d in re.finditer(r'\btemplate\s*<\s*(?:class|typename)\s+\w+\s*>\s*class\s+NiPointer\s*\{', alltext):
        if not NIPTR_DTOR.search(alltext[d.end() - 1:prepare.match_brace(alltext, d.end() - 1)]): return text
    spans = {}
    def defs(name):
        """[(base clause, body)] of the file-scope definitions of class `name` in the unit"""
        found = []
        for f, t in whole:
            if name not in t: continue
            for d in re.finditer(CLASS_DEF % re.escape(name), t):
                if f not in spans: spans[f] = prepare.scope_spans(t)
                if any(k != 'extern' for k, _ in prepare.scope_chain(spans[f], d.start())): continue
                found.append((d.group(1) or '', t[d.end():prepare.match_brace(t, d.end() - 1)]))
        return found
    def refcounted(name, depth=0):
        """True when a file-scope definition of `name` (or of a base) declares DecRefCount; None when unknown"""
        if depth > 8: return None
        for bases, body in defs(name):
            if re.search(r'\bDecRefCount\b', body): return True
            for b in re.findall(r'([A-Za-z_][\w:<>, ]*?)\s*(?:,|$)', re.sub(r'\b(?:public|protected|private|virtual)\b', '',
                                                                       bases.lstrip(':'))):
                b = b.strip()
                if not re.match(r'^[A-Za-z_]\w*$', b): return None      # a template or qualified base: unknown
                r = refcounted(b, depth + 1)
                if r is not False: return r
        return False
    out = []
    for t in sorted(set(NIPTR_MEMBER.findall(alltext))):
        if TEMPLATE_PARAM_LIKE.match(t) or re.search(r'\btemplate\s*<\s*>\s*(?:class|struct)\s+NiPointer\s*<\s*%s\s*>' % t,
                                                     alltext):
            continue
        if not re.search(r'^[ \t]*(?:class|struct)\s+%s\s*;' % t, lead, re.M): continue    # declared before the code
        if refcounted(t) is not False: continue
        out.append('template <> NiPointer<%s>::~NiPointer();' % t)
    if not out: return text
    stats['DTOR1'] += len(out)
    return text[:lead_end] + ' '.join(out) + ' /* fnv-nx DTOR1 */ ' + text[lead_end:]

# ---------------------------------------------------------------------------------------------------------- STL1
#
# A few units call internals of the MSVC 2008 C++ library by name (robertdavies_random: _SCL_SECURE_VALIDATE,
# std::_Checked_base, std::_Ptr_cat, the checked-copy iterator tags of <xutility>). STL1 includes
# shim/include/fnvnx_msvcstl.h, which defines them as <xutility> does, in a unit that uses them without defining them.
#
# A unit that derives a container of its own from std::_Container_base_aux_alloc_real (blk_00b20000_od_ob1_rtm: MSVC
# vector<void*>'s copy constructor written against <vector>'s _SECURE_SCL internals) gets the header's AUX part too
# (`#define FNVNX_MSVCSTL_AUX` in front: the container base and its proxy, _Destroy_range,
# stdext::unchecked_uninitialized_copy, the checked vector iterators). Such a unit builds `std::vector<T>::
# const_iterator(ptr, container)`, which libstdc++'s iterators cannot take: STL1 spells `std::vector<T[, A]>::
# const_iterator` / `::iterator` as MSVC's own names of those types there, `std::_Vector_const_iterator<T, A>` /
# `std::_Vector_iterator<T, A>` (A = std::allocator<T> by default; the same types under MSVC). Units with their own
# copy of these classes (blk_00ac0000 and the speedtree units define them) are left alone.

STL_USE = re.compile(r'\b_SCL_SECURE_VALIDATE(?:_RANGE)?\s*\(|\bstd\s*::\s*(?:_Checked_base|_Ptr_cat|'
                     r'_Range_checked_iterator_tag|_Nonscalar_ptr_iterator_tag|_Scalar_ptr_iterator_tag|'
                     r'_Unchecked_iterator_tag)\b')
STL_AUX_USE = re.compile(r'\bstd\s*::\s*_Container_base_aux_alloc_real\s*<')
STL_AUX_OWN = re.compile(r'\b(?:class|struct)\s+(?:_Container_base_aux(?:_alloc_real)?|_Aux_cont|_Vector_const_iterator|'
                         r'_Range_checked_iterator_tag)\b\s*[:{<]|\bnamespace\s+stdext\b')
STL_VECTOR = re.compile(r'(?:::\s*)?\bstd\s*::\s*vector\s*<')

def _template_args(s):
    """Top-level template arguments of the text between '<' and its '>'."""
    out, depth, cur = [], 0, ''
    for c in s:
        if c in '<([': depth += 1
        elif c in '>)]': depth -= 1
        if c == ',' and depth == 0: out.append(cur.strip()); cur = ''
        else: cur += c
    out.append(cur.strip())
    return out

def _stl_vector_iterators(text):
    """`std::vector<T[, A]>::const_iterator` / `::iterator` -> `::std::_Vector_const_iterator<T, A>` /
    `::std::_Vector_iterator<T, A>` (see above). Returns (text, edits)."""
    out, pos, n = [], 0, 0
    for m in STL_VECTOR.finditer(text):
        if m.start() < pos: continue
        lt = text.index('<', m.start())
        end = _angle_end(text, lt)
        if end < 0: continue
        it = re.match(r'\s*::\s*(const_iterator|iterator)\b', text[end:])
        if not it: continue
        args = _template_args(text[lt + 1:end - 1])
        if len(args) == 1: args.append('::std::allocator< %s >' % args[0])
        if len(args) != 2 or not all(args): continue
        name = '_Vector_const_iterator' if it.group(1) == 'const_iterator' else '_Vector_iterator'
        out.append(text[pos:m.start()] + '::std::%s< %s, %s >' % (name, args[0], args[1]))
        pos = end + it.end()
        n += 1
    return ''.join(out) + text[pos:], n

def r_msvc_library_internals(path, text, stats):
    """STL1: `#include "fnvnx_msvcstl.h"` on top of a unit that uses MSVC library internals; the AUX part and MSVC's
    vector iterator names in a unit that derives from std::_Container_base_aux_alloc_real (see above)."""
    if '_S' not in text and 'std' not in text and '_C' not in text: return text
    code = COMMENT.sub(' ', text)
    if STL_AUX_USE.search(code) and not STL_AUX_OWN.search(code):
        text, n = _stl_vector_iterators(text)
        stats['STL1'] += 1 + n
        return '#define FNVNX_MSVCSTL_AUX 1 /* fnv-nx STL1 */\n#include "fnvnx_msvcstl.h"\n' + text
    if not STL_USE.search(code): return text
    if re.search(r'#\s*define\s+_SCL_SECURE_VALIDATE\b|\bstruct\s+_Range_checked_iterator_tag\b', text): return text
    stats['STL1'] += 1
    return '#include "fnvnx_msvcstl.h" /* fnv-nx STL1 */\n' + text

# ---------------------------------------------------------------------------------------------------------- TPL1
#
# MSVC 2008 has no two-phase name lookup: in a class template derived from a base that depends on a template
# parameter (`template <class T> struct A : B<T> { void f() { size = 0; own(p, n); } }`), it found the base's members
# by their plain names when it instantiated the class. Standard C++ looks such a name up where the template is
# defined, where the dependent base is not searched, and GCC rejects the use (or binds it to a global of that name).
# TPL1 writes `this->name` for the uses, in the member function bodies of such a class template (in the class or
# out of it), of names the base declares as members (its bases' too, as far as the file defines them) and that the
# class itself, the function's parameters or its locals do not declare: the members MSVC found. Type names,
# enumerators and macros are left alone, and so are static member functions and friends.

TPL1_CLASS = re.compile(r'\btemplate\s*<((?:[^<>]|<[^<>]*>)*)>\s*(?:struct|class)\s+'
                        r'(?:__declspec\s*\((?:[^()]|\([^()]*\))*\)\s*)?([A-Za-z_]\w*)\s*(?::\s*([^{};]*))?\{')
PLAIN_CLASS = re.compile(r'(?<![<\w])(?:struct|class)\s+(?:__declspec\s*\((?:[^()]|\([^()]*\))*\)\s*)?([A-Za-z_]\w*)\s*'
                         r'(?::\s*([^{};]*))?\{')
TPL1_OUT_DEF = re.compile(r'\btemplate\s*<((?:[^<>]|<[^<>]*>)*)>\s*(?:[^;{}()]*?[\s\*&>])?([A-Za-z_]\w*)\s*<[^<>;{}]*>\s*::'
                          r'\s*~?[A-Za-z_]\w*\s*\(')
# a local declaration: `T name =`, `T* name;`, `A<B, C>* name = ...` (the type may end in a template argument list)
TPL1_LOCAL = re.compile(r'(?:\b([A-Za-z_]\w*)|(>))(?:\s*[\*&]+\s*|\s+)([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)?(?:=(?!=)|;|,|\[)')
NOT_A_TYPE = set('return else case goto delete new throw sizeof do typedef'.split())
CXX_KEYWORDS = set('''alignas alignof and asm auto bool break case catch char class const const_cast constexpr continue
decltype default delete do double dynamic_cast else enum explicit export extern false float for friend goto if inline int
long mutable namespace new noexcept not nullptr operator or private protected public register reinterpret_cast return
short signed sizeof static static_assert static_cast struct switch template this throw true try typedef typeid typename
union unsigned using virtual void volatile wchar_t while'''.split())

def _flat_body(text, o, c):
    """The class body text[o+1:c-1] with every nested brace block emptied (its top-level member declarations)."""
    body = COMMENT.sub(' ', text[o + 1:c - 1])
    res, depth = [], 0
    for ch in body:
        if ch == '{':
            if depth == 0: res.append('{')
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0: res.append('}')
        elif depth == 0:
            res.append(ch)
    return ''.join(res)

def _members(flat, own_name):
    """(value member names, type member names) declared at the top level of a flattened class body."""
    values, types = set(), set()
    for m in re.finditer(r'\btypedef\b[^;]*?\b([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*;|\b(?:struct|class|union|enum)\s+'
                         r'([A-Za-z_]\w*)|\busing\s+([A-Za-z_]\w*)\s*=', flat):
        types.add(m.group(1) or m.group(2) or m.group(3))
    for m in re.finditer(r'(?<![\w:~.>])([A-Za-z_]\w*)\s*\(', flat):        # member functions (and noise)
        values.add(m.group(1))
    for stmt in re.split(r'[;{}]', re.sub(r'\((?:[^()]|\([^()]*\))*\)', '()', flat)):
        s = re.sub(r'^\s*(?:(?:public|private|protected)\s*:\s*)+', '', stmt).strip()
        if not s or '(' in s or re.match(r'(?:typedef|using|friend|template|enum|struct|class|union)\b', s): continue
        for d in s.split(','):
            m = re.search(r'([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*(?::\s*\w+\s*)?(?:=.*)?$', d.strip(), re.S)
            if m: values.add(m.group(1))
    values = {v for v in values if v not in CXX_KEYWORDS and v != own_name and not v.startswith('__')
              and not re.match(r'^[A-Z][A-Z0-9_]*$', v)}
    return values, types

def _base_specs(bases):
    """[(name, template arguments or None)] of a base clause."""
    out = []
    for b in _split_bases(bases):
        b = re.sub(r'\b(?:public|protected|private|virtual)\b', '', b).strip()
        m = re.match(r'(?:::)?([A-Za-z_]\w*)\s*(?:<(.*)>)?\s*$', b, re.S)
        if m: out.append((m.group(1), m.group(2)))
    return out

_TPL1_SPANS = {}

def r_dependent_base_members(path, text, stats):
    """TPL1: `this->` in front of the members of a dependent base used by plain name in a class template (see above)."""
    if 'template' not in text or not TPL1_CLASS.search(text): return text
    spans = prepare.scope_spans(text)
    parent, stack = {}, []
    for sp in spans:                                       # innermost enclosing brace of every brace
        while stack and stack[-1][1] < sp[0]: stack.pop()
        parent[sp[0]] = stack[-1][0] if stack else None
        stack.append(sp)
    heads = lambda pos: tuple(h for _, h in prepare.scope_chain(spans, pos))
    classes = {}                                           # name -> [(open, close, bases, template params, scope)]
    for m in TPL1_CLASS.finditer(text):
        o = m.end() - 1
        ps = set(re.findall(r'(?:class|typename|int|unsigned|bool|short|long|char)\s+([A-Za-z_]\w*)', m.group(1)))
        classes.setdefault(m.group(2), []).append((o, prepare.match_brace(text, o), m.group(3) or '', ps, heads(m.start()),
                                                   m.group(2)))
    tdefs = {d[0] for v in classes.values() for d in v}
    for m in PLAIN_CLASS.finditer(text):
        o = m.end() - 1
        if o in tdefs: continue
        classes.setdefault(m.group(1), []).append((o, prepare.match_brace(text, o), m.group(2) or '', set(), heads(m.start()),
                                                   m.group(1)))
    def visible(name, scope):
        """the definitions of `name` visible from `scope`: those of the innermost enclosing scope that has one"""
        cands = [d for d in classes.get(name, ()) if d[4] == scope[:len(d[4])]]
        if not cands: return []
        best = max(len(d[4]) for d in cands)
        return [d for d in cands if len(d[4]) == best]
    cache = {}
    def members_of(d, depth=0):
        key = d[0]
        if key in cache: return cache[key]
        cache[key] = (set(), set())
        vals, types = _members(_flat_body(text, d[0], d[1]), d[5])
        if depth < 8:
            for bname, _ in _base_specs(d[2]):
                for bd in visible(bname, d[4]):
                    bv, bt = members_of(bd, depth + 1)
                    vals |= bv; types |= bt
        cache[key] = (vals, types)
        return cache[key]
    closure, inc = [], {}
    def included_members(name, depth=0):
        """(values, types) of the file-scope definitions of class `name` in the local headers the file includes"""
        if name in inc: return inc[name]
        inc[name] = (set(), set())
        if depth > 8 or '#include' not in text: return inc[name]
        if not closure: closure.extend(_closure(path, text)[1:] or [None])
        vals, types = set(), set()
        for ft in closure:
            if ft is None or name not in ft[1]: continue
            f, t = ft
            for m in re.finditer(r'\b(?:struct|class)\s+%s\s*(?::\s*([^{};]*))?\{' % re.escape(name), t):
                if f not in _TPL1_SPANS: _TPL1_SPANS[f] = prepare.scope_spans(t)
                if prepare.scope_chain(_TPL1_SPANS[f], m.start()): continue
                o = m.end() - 1
                v, ty = _members(_flat_body(t, o, prepare.match_brace(t, o)), name)
                vals |= v; types |= ty
                for bname, _ in _base_specs(m.group(1) or ''):
                    bv, bt = included_members(bname, depth + 1)
                    vals |= bv; types |= bt
        inc[name] = (vals, types)
        return inc[name]
    fixes = {}                                             # class open -> names to qualify
    for name, defs in classes.items():
        for d in defs:
            o, c, bases, params, scope, _ = d
            if not params or not bases: continue
            dep, dep_types = set(), set()
            for bname, args in _base_specs(bases):
                if args is None or not set(re.findall(r'[A-Za-z_]\w*', args)) & params: continue
                found = visible(bname, scope)
                for bd in found:
                    v, t = members_of(bd)
                    dep |= v; dep_types |= t
                if not found:                              # the base template lives in an included header
                    v, t = included_members(bname)
                    dep |= v; dep_types |= t
            own_v, own_t = _members(_flat_body(text, o, c), name)
            names = dep - dep_types - own_v - own_t - params - {name}
            if names: fixes[o] = (name, names)
    if not fixes: return text
    bodies = []                                            # (body open, body close, head, names)
    for fo, fc, k, h in spans:
        if k != 'function' or parent.get(fo) not in fixes: continue
        if re.search(r'\b(?:static|friend)\b', h): continue
        bodies.append((fo, fc, h, fixes[parent[fo]][1]))
    by_name = {}
    for o, (name, names) in fixes.items(): by_name.setdefault(name, []).append(names)
    for m in TPL1_OUT_DEF.finditer(text):
        if len(by_name.get(m.group(2), ())) != 1 or len(classes.get(m.group(2), ())) != 1: continue
        brace = re.match(r'[^;{}]*\{', text[m.end():])
        if not brace: continue
        fo = m.end() + brace.end() - 1
        if re.search(r'\b(?:static|friend)\b', text[m.start():fo]): continue
        bodies.append((fo, prepare.match_brace(text, fo) - 1, text[m.start():fo], by_name[m.group(2)][0]))
    edits = set()
    for fo, fc, head, names in bodies:
        body = COMMENT.sub(' ', text[fo:fc])
        params_text = head[head.find('('):] if '(' in head else ''
        declared = set(re.findall(r'([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)?(?:=[^,()]*)?\s*[,)]', params_text))
        declared |= {v for t, _, v in TPL1_LOCAL.findall(body) if t not in NOT_A_TYPE}
        use = names - declared
        if not use: continue
        for t in BODY_TOKENS.finditer(text, fo, fc):
            w = t.group()
            if w not in use: continue
            before = text[max(0, t.start() - 12):t.start()]
            if re.search(r'(?:::|->|\.|~)\s*$|\b(?:struct|class|union|enum|typename|goto)\s+$', before): continue
            after = text[t.end():t.end() + 4]
            if re.match(r'\s*::', after): continue
            if re.match(r'\s*:(?!:)', after) and re.search(r'(?:^|[;{}])\s*$', before): continue          # a label
            edits.add(t.start())
    if not edits: return text
    out, pos = [], 0
    for at in sorted(edits):
        out.append(text[pos:at]); out.append('this->'); pos = at
    out.append(text[pos:])
    stats['TPL1'] += len(edits)
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- RREF1
#
# MSVC binds a temporary to a non-const reference parameter (`actor->Unk_AA(a + b)` with Unk_AA(NiPoint3&),
# `Add(va_arg(args, T*))` with Add(T&)); the temporary lives to the end of the full expression. GCC refuses. RREF1
# wraps such an argument in fnvnx_lvalue (fnvnx_compat.h), which hands back the same object as an lvalue for that same
# lifetime, where every declaration of the function the unit sees (by name and number of parameters) takes a
# non-const reference at that position. An argument that already is an lvalue comes back unchanged.

RREF_DECL = re.compile(r'(?:\b([A-Za-z_]\w*)\s+|[\*&]\s*|(?<!-)>\s*)(?:[A-Za-z_]\w*\s*::\s*)*~?([A-Za-z_]\w*)\s*'
                       r'\(((?:[^;{}()]|\((?:[^;{}()]|\([^;{}()]*\))*\))*)\)\s*(?:const\s*)?(?:throw\s*\([^()]*\)\s*)?'
                       r'(?:=\s*0\s*)?[;{:]')
RREF_NOT_TYPE = set('return else case goto delete new throw sizeof do if while for switch'.split())

def _nonconst_ref(param):
    p = re.sub(r'=.*$', '', param, flags=re.S).strip()
    if '&&' in p or not re.search(r'&\s*(?:[A-Za-z_]\w*)?\s*(?:\[[^\]]*\])?$', p): return False
    return not re.search(r'\bconst\b', p[:p.rfind('&')])

def r_temporaries_to_references(path, text, stats):
    """RREF1: fnvnx_lvalue around a temporary passed to a non-const reference parameter (see above)."""
    if '&' not in text or not path.endswith('.cpp'): return text
    unit = '\n'.join(t for _, t in _closure(path, text)) if '#include' in text else COMMENT.sub(' ', text)
    decls = {}
    for m in RREF_DECL.finditer(unit):
        if m.group(1) in RREF_NOT_TYPE: continue
        params = m.group(3).strip()
        ps = [] if params in ('', 'void') else _split_args(params)
        decls.setdefault(m.group(2), []).append([_nonconst_ref(p) for p in ps])
    refs = {n: v for n, v in decls.items() if any(any(r) for r in v)}
    if not refs: return text
    call = re.compile(r'(?<![\w~])(%s)\s*\(' % '|'.join(sorted(refs, key=len, reverse=True)))
    spans = prepare.scope_spans(text)
    bodies = []
    for o, c, k, _ in spans:
        if k == 'function' and not (bodies and o < bodies[-1][1]): bodies.append((o, c))
    edits, bi = [], 0
    for m in call.finditer(text):
        while bi < len(bodies) and bodies[bi][1] < m.start(): bi += 1
        if bi == len(bodies) or not bodies[bi][0] < m.start(): continue
        before = text[max(0, m.start() - 40):m.start()]
        pw = re.search(r'([A-Za-z_]\w*)\s*$', before)
        if pw and pw.group(1) not in RREF_NOT_TYPE: continue                 # a declaration (`T name(...)`)
        close = _call_end(text, m.end() - 1)
        args = _split_args(text[m.end():close - 1])
        if args == ['']: args = []
        shapes = [r for r in refs[m.group(1)] if len(r) == len(args)]
        if not shapes: continue
        at = m.end()
        for i, a in enumerate(args):
            if all(r[i] for r in shapes) and a.strip() and not LVALUE_LIKE.match(a) and \
                    not a.strip().startswith('fnvnx_lvalue('):
                lead = len(a) - len(a.lstrip())
                edits.append((at + lead, at + len(a.rstrip())))
            at += len(a) + 1
    if not edits: return text
    out, pos = [], 0
    for a, b in sorted(edits):
        if a < pos: continue
        out.append(text[pos:a]); out.append('fnvnx_lvalue(' + text[a:b] + ')'); pos = b
        stats['RREF1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- ANON1
#
# MSVC takes an unnamed struct defined in a member function's return type (`struct { float f; }* Float();`, the
# decomp's view of a setting object); C++ forbids defining a type there. ANON1 names it and defines it just before:
# `struct fnvnx_anon_Float { float f; }; fnvnx_anon_Float* Float();`. Same layout; the return type is not part of the
# function's (Itanium) symbol, so the call binds as before.

ANON_RET = re.compile(r'\bstruct\s*\{([^{}]*)\}\s*(\*+|&)\s*([A-Za-z_]\w*)\s*\(')

def r_anonymous_return_types(path, text, stats):
    """ANON1: an unnamed struct defined in a return type gets a name (see above)."""
    if 'struct' not in text or not ANON_RET.search(text): return text
    out, pos = [], 0
    for m in ANON_RET.finditer(text):
        name = 'fnvnx_anon_' + m.group(3)
        nl = '\n' * text.count('\n', m.start(), m.end())
        out.append(text[pos:m.start()])
        out.append('struct %s {%s}; %s%s %s(' % (name, m.group(1).replace('\n', ' '), name, m.group(2), m.group(3)) + nl)
        pos = m.end()
        stats['ANON1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- ENUM1
#
# The decomp forward-declares enums (`enum DIALOGUE_TYPE;`, `enum ENUM_FORM_ID;`, ActorValue's `enum Index;`), which
# MSVC accepts because every unscoped enum has the underlying type int there. Standard C++ allows an opaque enum
# declaration only with a fixed underlying type. ENUM1 writes MSVC's type out, `enum X : int;`, and gives every
# definition of such an enum in the decomp the same base (`enum X : int {`), so declaration and definition agree.
# (GCC would otherwise pick unsigned int for an enum without negative values; int is what MSVC used.) A definition
# with an enumerator outside int's range keeps its form.

OPAQUE_ENUM = re.compile(r'^([ \t]*(?:typedef\s+)?enum\s+([A-Za-z_]\w*))(\s*;)', re.M)
ENUM_DEF = re.compile(r'\benum\s+([A-Za-z_]\w*)(\s*)\{')
_OPAQUE_NAMES = None

def _opaque_enum_names(path):
    """names the decomp declares as opaque enums anywhere"""
    global _OPAQUE_NAMES
    if _OPAQUE_NAMES is not None: return _OPAQUE_NAMES
    root, names = _roots(path), set()
    for d in ('src', 'include'):
        for dp, _, fs in os.walk(os.path.join(root, d)):
            for f in fs:
                if not f.endswith(('.h', '.cpp', '.inl', '.hpp', '.c')): continue
                with open(os.path.join(dp, f), encoding='utf-8', errors='surrogateescape') as h: t = h.read()
                if 'enum' in t: names.update(m.group(2) for m in OPAQUE_ENUM.finditer(t))
    _OPAQUE_NAMES = names
    return _OPAQUE_NAMES

def _fits_int(body):
    for v in re.findall(r'=\s*(-?\s*(?:0[xX][0-9a-fA-F]+|\d+))[uUlL]*', body):
        if not -2 ** 31 <= int(v.replace(' ', ''), 0) < 2 ** 31: return False
    return True

def r_opaque_enums(path, text, stats):
    """ENUM1: `enum X;` -> `enum X : int;`, and `enum X {` -> `enum X : int {` for those names (see above)."""
    if 'enum' not in text: return text
    names = _opaque_enum_names(path)
    if not names: return text
    def decl(m):
        if m.group(2) not in names: return m.group(0)
        stats['ENUM1'] += 1
        return m.group(1) + ' : int' + m.group(3)
    def defn(m):
        if m.group(1) not in names: return m.group(0)
        if not _fits_int(text[m.end():text.find('}', m.end())]): return m.group(0)   # int cannot hold it: as it was
        stats['ENUM1'] += 1
        return 'enum %s : int%s{' % (m.group(1), m.group(2) or ' ')
    text = OPAQUE_ENUM.sub(decl, text)
    return ENUM_DEF.sub(defn, text)

# ---------------------------------------------------------------------------------------------------------- ENUM2
#
# An enum with an enumerator in [2^31, 2^32) (`CHANGE_SCRIPT_VARIABLES = 0x80000000` in TESObjectREFR.h, havok's
# `MARKED_RO`, ExtendedColors 0xff800000, `ShaderParameter ... = 0xffffffff`): MSVC's underlying type is int and the
# value is converted to int (0x80000000 is INT_MIN, 0xffffffff is -1), so it promotes, compares, divides, shifts and
# widens as a negative int. GCC picks unsigned int (long long when the enum also has a negative value). ENUM2 writes
# MSVC's type out, `enum E : int {`, with an (int) cast on each such literal (a fixed type does not take the value
# otherwise). Only enums whose initializers are all integer literals in [-2^31, 2^32) and which have no base yet
# (S12 f8 ENUM-UNSIGNED: 6 sites in the sample, 51 definitions in the tree). The enum bit-fields S12 lists as
# ENUM-BITFIELD (signed in MSVC) would take the same base; the tree has none.

ENUM_BODY = re.compile(r'\benum(\s+[A-Za-z_]\w*)?(\s*)\{([^{}]*)\}')
ENUM_INIT = re.compile(r'(//[^\n]*|/\*.*?\*/)|(=\s*)(-\s*)?(0[xX][0-9a-fA-F]+|\d+)([uUlL]*)\b', re.S)

def r_enum_int_values(path, text, stats):
    """ENUM2: `enum E { A = 0x80000000 }` -> `enum E : int { A = (int)0x80000000 }` (see above). Same line count."""
    if 'enum' not in text: return text
    def body(m):
        code = re.sub(r'//[^\n]*|/\*.*?\*/', ' ', m.group(3), flags=re.S)
        inits = re.findall(r'=([^,]*)', code)
        vals = []
        for e in inits:
            v = re.fullmatch(r'\s*(-\s*)?(0[xX][0-9a-fA-F]+|\d+)[uUlL]*\s*', e)
            if not v: return m.group(0)                   # an expression: left as it is
            vals.append(-int(v.group(2), 0) if v.group(1) else int(v.group(2), 0))
        if not any(v >= 2 ** 31 for v in vals) or not all(-2 ** 31 <= v < 2 ** 32 for v in vals): return m.group(0)
        def lit(n):
            if n.group(1) or n.group(3) or int(n.group(4), 0) < 2 ** 31: return n.group(0)
            return n.group(2) + '(int)' + n.group(4) + n.group(5)
        stats['ENUM2'] += 1
        return 'enum%s : int%s{%s}' % (m.group(1) or '', m.group(2) or ' ', ENUM_INIT.sub(lit, m.group(3)))
    return ENUM_BODY.sub(body, text)

# ---------------------------------------------------------------------------------------------------------- ELAB1
#
# Some units name a class after a Win32 / CRT function (`struct HeapAlloc { ... }` in blk_00e60000: the decomp's
# heap view). Where the platform header declares the function in the same scope, the function hides the class name
# and `HeapAlloc* alloc` no longer names a type (MSVC saw no such function: the unit did not include the SDK).
# ELAB1 writes the uses of the class name as a type in elaborated form, `struct HeapAlloc* alloc`: the same class,
# the same symbols (an elaborated specifier changes no mangled name).

ELAB_DEF = re.compile(r'^[ \t]*(struct|class)\s+([A-Za-z_]\w*)\s*(?::[^;{}]*)?\{', re.M)

FNPTR_TYPEDEF = re.compile(r'^[ \t]*typedef\s+[^;{}()]*\(\s*(?:__\w+\s*)?\*\s*([A-Za-z_]\w*)\s*\)\s*\([^;{}]*\)\s*;', re.M)

def r_function_named_classes(path, text, stats):
    """ELAB1: `struct X` in front of the type uses of a file-scope class named like a platform function; a file-scope
    typedef of such a name (`typedef unsigned int(__fastcall* VirtualQuery)(void*)`, a slot type) is renamed
    fnvnx_t_<name> in the file (a typedef name is in no symbol)."""
    if 'struct' not in text and 'class' not in text and 'typedef' not in text: return text
    if prepare.PLATFORM is None: prepare.PLATFORM = prepare.platform_names()
    spans = None
    tds = set()
    for m in FNPTR_TYPEDEF.finditer(text):
        if m.group(1) not in prepare.PLATFORM: continue
        if spans is None: spans = prepare.scope_spans(text)
        if not prepare.scope_chain(spans, m.start()): tds.add(m.group(1))
    if tds:
        text, n = re.subn(r'(?<![\w:.>~])(%s)\b(?!\s*\()' % '|'.join(sorted(tds)), r'fnvnx_t_\1', text)
        stats['ELAB1'] += n
        spans = None
    found = {}
    for m in ELAB_DEF.finditer(text):
        if m.group(2) not in prepare.PLATFORM: continue
        if spans is None: spans = prepare.scope_spans(text)
        if prepare.scope_chain(spans, m.start()): continue
        found[m.group(2)] = m.group(1)
    if not found: return text
    use = re.compile(r'(?<![\w:.>~])(%s)\b(?=\s*(?:[\*&]|[A-Za-z_]))' % '|'.join(sorted(found, key=len, reverse=True)))
    out, pos = [], 0
    for m in use.finditer(text):
        before = text[max(0, m.start() - 12):m.start()]
        if re.search(r'\b(?:struct|class|union|enum|typename)\s+$', before): continue
        out.append(text[pos:m.start()]); out.append(found[m.group(1)] + ' '); pos = m.start()
        stats['ELAB1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- ANON3
#
# An anonymous union declared in a function body with an anonymous struct in it (`union { Stream s; struct { T* a;
# T* b; }; };`): GCC takes an anonymous struct only inside a named class. The union goes into a local struct
# of its own, one object of it, and a reference of each member's name to that member, so the code goes on using the
# plain names on the same storage and layout.

ANON3_DECOR = re.compile(r'__declspec\s*\((?:[^()]|\([^()]*\))*\)|alignas\s*\([^()]*\)')

def _anon3_members(masked, o, c, spans):
    """Member names of the union body masked[o+1:c]: its own declarations and those of its anonymous structs;
    None when a member is something else (a function, a named nested type, a bit-field)."""
    body, names, pos = [], [], o + 1
    inner = [sp for sp in spans if o < sp[0] and sp[1] < c]
    for so, sc, sk, sh in inner:
        if so < pos: continue
        if sk != 'class' or ANON3_DECOR.sub('', sh).strip() != 'struct' or not re.match(r'\s*;', masked[sc + 1:]):
            return None
        sub = _anon3_members(masked, so, sc, spans)
        if sub is None: return None
        names += sub
        body.append(masked[pos:masked.rfind('struct', 0, so)]); pos = masked.index(';', sc) + 1
    body.append(masked[pos:c])
    for stmt in ''.join(body).split(';'):
        if not stmt.strip(): continue
        if re.search(r'[(){}:]', stmt.replace('::', '')): return None
        for d in _split_args(re.sub(r'^\s*(?:[A-Za-z_][\w:]*(?:\s*<[^;{}]*>)?\s*)', '', stmt) if ',' in stmt else stmt):
            m = re.search(r'([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*$', d)
            if not m: return None
            names.append(m.group(1))
    return names

def r_local_anonymous_unions(path, text, stats):
    """ANON3: `union { ... struct { ... }; ... };` in a function body -> `struct fnvnx_anon_uN { union ... }; }
    fnvnx_anon_vN; decltype(fnvnx_anon_vN.m)& m = fnvnx_anon_vN.m; ...` (same line count)."""
    if 'union' not in text or 'struct' not in text: return text
    masked = FRIEND_MASK.sub(lambda x: re.sub(r'[^\n]', ' ', x.group()), text)
    spans = prepare.scope_spans(masked)
    edits, n = [], 0
    for o, c, k, h in spans:
        if k != 'class' or ANON3_DECOR.sub('', h).strip() != 'union': continue
        tail = re.match(r'\s*;', masked[c + 1:])
        chain = prepare.scope_chain(spans, o)
        if not tail or not chain or chain[-1][0] not in ('function', 'other') or \
                'function' not in [ck for ck, _ in chain]: continue
        if not any(sp[2] == 'class' and o < sp[0] and sp[1] < c for sp in spans): continue
        names = _anon3_members(masked, o, c, spans)
        if not names: continue
        u = masked.rfind('union', 0, o)
        v = 'fnvnx_anon_v%d' % n
        refs = ' '.join('decltype(%s.%s)& %s = %s.%s;' % (v, m, m, v, m) for m in names)
        edits.append((u, 'struct fnvnx_anon_u%d { ' % n))
        edits.append((c + 1 + tail.end(), ' } %s; %s' % (v, refs)))
        n += 1
        stats['ANON3'] += 1
    for at, s in sorted(edits, reverse=True):
        text = text[:at] + s + text[at:]
    return text

# ---------------------------------------------------------------------------------------------------------- ANON2
#
# The decomp nests an anonymous struct in an anonymous union whose members have constructors, a destructor or a copy
# assignment (ExtraSave::Extra's `String activationText`, NiPoint3Interpolator's `NiPoint3 m_kValue`). MSVC took that
# and, as for any union member, never ran those functions for it; GCC refuses such members in an anonymous aggregate.
# ANON2 declares the member as raw storage of the same size and alignment
# (`alignas(String) unsigned char activationText[sizeof(String)];`) and every use of it in the file as the object
# that storage holds (`(*(String*)(slot->activationText)).FUN_004048e0()`): same bytes, same calls, and, as before,
# no implicit construction or destruction. The uses are converted in the file that declares the member and in every
# file that includes it (NiPoint3Interpolator is declared in nipoint3interpolator_unit.h and used in the .cpp).
# Where another class of the file or its headers has a member of that name (the .cpp's A499A0PointInterpolator has
# its own m_kValue) or the file declares the name otherwise, a use is converted only where it is the member's:
# unqualified or this-> in a member function of the class or of a class derived from it, or x->name / x.name with x a
# parameter or local of such a class type.

ANON_UNION = re.compile(r'\bunion\s*\{')
ANON_MEMBER = re.compile(r'^([ \t]*)([A-Za-z_][\w:]*(?:\s*<[^<>;{}]*>)?)\s+([A-Za-z_]\w*)\s*;', re.M)
ANY_CLASS_DEF = re.compile(r'\b(?:class|struct)\s+(?:__declspec\s*\((?:[^()]|\([^()]*\))*\)\s*)*([A-Za-z_]\w*)\s*'
                           r'(?:final\s*)?(?::\s*([^;{}]*))?\{')
_ANON2_NAMES = None
NOT_TYPES = {'return', 'case', 'else', 'do', 'throw', 'delete', 'new', 'sizeof', 'goto', 'and', 'or', 'not'}

def _anon2_members(text):
    """[(owner, type, name, start, end)] of the members ANON2 handles in text: text[start:end] is the declaration,
    owner the class that holds the union ('' when there is none)."""
    found, spans = [], None
    for u in ANON_UNION.finditer(text):
        uo = u.end() - 1
        uc = prepare.match_brace(text, uo)
        for s in re.finditer(r'\bstruct\s*\{', text[uo + 1:uc - 1]):
            so = uo + 1 + s.end() - 1
            sc = prepare.match_brace(text, so)
            if spans is None: spans = prepare.scope_spans(text)
            # the struct must be a direct member of this union
            if max((sp[0] for sp in spans if sp[0] < so < sp[1]), default=None) != uo: continue
            flat = _flat_body(text, so, sc)
            for m in ANON_MEMBER.finditer(flat):
                ty, name = m.group(2), m.group(3)
                base = ty.split('<')[0].strip()
                if base in CXX_KEYWORDS or base in ('unsigned', 'signed', 'const'): continue
                special = False
                for d in re.finditer(CLASS_DEF % re.escape(base), text):
                    body = text[d.end():prepare.match_brace(text, d.end() - 1)]
                    if re.search(r'~\s*%s\s*\(|\boperator\s*=\s*\(|(?<![\w~:])%s\s*\(' % (re.escape(base), re.escape(base)), body):
                        special = True
                if not special: continue
                dm = re.search(r'\b%s\s+%s\s*;' % (re.escape(ty).replace(r'\ ', r'\s*'), re.escape(name)), text[so:sc])
                if not dm: continue
                owner = ''
                for o, c, k, h in spans:                   # the innermost class around the union
                    if o >= uo: break
                    if c > uo and k == 'class':
                        hm = re.search(r'\b(?:class|struct)\s+(?:__declspec\s*\((?:[^()]|\([^()]*\))*\)\s*)*'
                                       r'([A-Za-z_]\w*)', h)
                        if hm: owner = hm.group(1)
                found.append((owner, ty, name, so + dm.start(), so + dm.end()))
    return found

def _anon2_names(path):
    """names of the members ANON2 handles anywhere in the tree (a file that names none needs no closure)"""
    global _ANON2_NAMES
    if _ANON2_NAMES is not None: return _ANON2_NAMES
    names = set()
    for d in ('src', 'include'):
        for dp, _, fs in os.walk(os.path.join(_roots(path), d)):
            for f in fs:
                if not f.endswith(('.h', '.cpp', '.inl', '.hpp')): continue
                with open(os.path.join(dp, f), encoding='utf-8', errors='surrogateescape') as h: t = h.read()
                if 'union' in t and ANON_UNION.search(t): names.update(n for _, _, n, _, _ in _anon2_members(t))
    _ANON2_NAMES = names
    return names

def _anon2_uses(text, owner, ty, name, skip, files):
    """(start, end, replacement) edits that make each use of `owner`'s member `name` in text the object its storage
    holds. When no other class in `files` [(file, text)] has a member of that name and text declares it nowhere else,
    every use is one; otherwise a use counts when it is unqualified (or this->) in a member function of `owner` or of
    a class derived from it, or `x->name` / `x.name` with x a parameter or local of such a class type."""
    bare = COMMENT.sub(lambda m: re.sub(r'[^\n]', ' ', m.group(0)), text)        # same offsets, no comments
    decl = re.compile(r'[\w>\*&]\s+\**%s\s*(?:\[[^\]]*\])?\s*[;=,)]' % re.escape(name))
    bases, own = {}, {}
    for _, t in files:
        for m in ANY_CLASS_DEF.finditer(t):
            bases.setdefault(m.group(1), set()).update(b for b, _ in _base_specs(m.group(2) or ''))
            o = m.end() - 1
            if decl.search(_flat_body(t, o, prepare.match_brace(t, o))): own.setdefault(m.group(1), True)
    def derives(cls, depth=0):
        if cls == owner: return True
        if depth > 8 or cls in own: return False           # a class with its own member of that name
        return any(derives(b, depth + 1) for b in bases.get(cls, ()))
    others = {c for c in own if c != owner}
    count = len(decl.findall(bare)) - (1 if skip else 0)
    simple = not others and count == 0
    spans = None if simple else prepare.scope_spans(text)
    def context(pos):
        """the class whose member function holds pos, and that function's (head, body) text"""
        cls, fn = None, None
        for o, c, k, h in spans:
            if o > pos: break
            if not o < pos < c: continue
            if k == 'class' and fn is None:
                hm = re.search(r'\b(?:class|struct)\s+(?:__declspec\s*\((?:[^()]|\([^()]*\))*\)\s*)*([A-Za-z_]\w*)', h)
                cls = hm.group(1) if hm else None
            elif k == 'function' and fn is None:
                fn = (h, o, c)
                q = re.search(r'([A-Za-z_]\w*)\s*::\s*~?[A-Za-z_]\w*\s*\([^()]*(?:\([^()]*\)[^()]*)*\)[^()]*$', h)
                if q: cls = q.group(1)
        return cls, fn
    def var_type(var, fn, pos):
        if fn is None: return None
        h, o, _ = fn
        hit = None
        for m in re.finditer(r'\b([A-Za-z_]\w*)\s*(?:const\s*)?[\*&]?\s*(?:const\s*)?\b%s\s*[,)=;\[]' % re.escape(var),
                             h + bare[o:pos]):
            if m.group(1) not in CXX_KEYWORDS: hit = m.group(1)
        return hit
    edits = []
    for use in re.finditer(r'(?<![\w~])%s\b' % re.escape(name), bare):
        if skip and skip[0] <= use.start() < skip[1]: continue
        before = bare[max(0, use.start() - 2):use.start()]
        if before.endswith('::'): continue
        if before.endswith('.') or before.endswith('->'):
            dot = use.start() - (1 if before.endswith('.') else 2)
            start = prepare.operand_start(text, dot)
            if start >= dot: continue
            obj = text[start:dot].strip()
            if not simple:
                cls, fn = context(use.start())
                if obj == 'this':
                    if not (cls and derives(cls)): continue
                elif re.fullmatch(r'[A-Za-z_]\w*', obj):
                    vt = var_type(obj, fn, start)
                    if not (vt and derives(vt)): continue
                elif others:
                    continue
            edits.append((start, use.end(), '(*(%s*)(%s))' % (ty, text[start:use.end()])))
        else:
            if not simple:
                pm = re.search(r'(\w+|>)\s*[\*&]*\s*$', bare[max(0, use.start() - 80):use.start()])
                if (pm and pm.group(1) not in NOT_TYPES and not pm.group(1)[0].isdigit()
                        and re.match(r'\s*(?:\[[^\]]*\])?\s*[;=,)]', bare[use.end():])):
                    continue                                  # a declaration of that name
                cls, fn = context(use.start())
                if not (cls and derives(cls)): continue
                if fn is not None and decl.search(fn[0] + bare[fn[1]:use.start()]): continue   # a local of that name
            edits.append((use.start(), use.end(), '(*(%s*)(%s))' % (ty, name)))
    return edits

def r_anonymous_aggregate_members(path, text, stats):
    """ANON2: raw storage for class members with special functions in an anonymous struct of an anonymous union, and
    their uses as the object, in the file and in the files that include it (see above)."""
    found = _anon2_members(text) if 'union' in text and ANON_UNION.search(text) else []
    inherited = []
    names = _anon2_names(path)
    if '#include' in text and any(re.search(r'\b%s\b' % re.escape(n), text) for n in names):
        files = _closure(path, text)
        for f, t in files[1:]:
            if 'union' in t and ANON_UNION.search(t):
                inherited += [(o, ty, n) for o, ty, n, _, _ in _anon2_members(t) if n in text]
    else:
        files = None
    if not found and not inherited: return text
    if files is None: files = _closure(path, text)
    edits = []                                             # (start, end, replacement)
    for owner, ty, name, at, end in found:
        edits.append((at, end, 'alignas(%s) unsigned char %s[sizeof(%s)]; /* fnv-nx ANON2 */' % (ty, name, ty)))
        edits += _anon2_uses(text, owner, ty, name, (at, end), files)
        stats['ANON2'] += 1
    for owner, ty, name in inherited:
        uses = _anon2_uses(text, owner, ty, name, None, files)
        edits += uses
        if uses: stats['ANON2'] += 1
    if not edits: return text
    out, pos = [], 0
    for a, b, r in sorted(edits):
        if a < pos: continue
        out.append(text[pos:a]); out.append(r); pos = b
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- INC1
#
# MSVC looks a quoted #include up in the including file's directory first, and on Windows file names are not case
# sensitive: in src/fallout/ai/actor.cpp `#include "Actor.h"` is the unit header src/fallout/ai/actor.h. On the
# target the case-sensitive lookup misses it there and takes include/Actor.h, the canonical class header, which
# lacks the declarations the unit was written against (actor.cpp, playercharacter.cpp, bgsentrypoint.cpp, the
# nimain *_types headers: 41 includes). INC1 spells such an include as the file in the including directory is
# spelled: the header MSVC read.

INC_QUOTED = re.compile(r'^([ \t]*#[ \t]*include[ \t]*")([^"]+)(")', re.M)
_DIR_LISTING = {}

def r_include_case(path, text, stats):
    """INC1: a quoted #include that names, up to case, a file in the including directory -> that file (see above)."""
    if '#include' not in text: return text
    here = os.path.dirname(os.path.abspath(path))
    def sub(m):
        inc = m.group(2)
        full = os.path.normpath(os.path.join(here, inc))
        if os.path.isfile(full): return m.group(0)
        d, b = os.path.split(full)
        if d not in _DIR_LISTING:
            try: _DIR_LISTING[d] = {f.lower(): f for f in os.listdir(d)}
            except OSError: _DIR_LISTING[d] = {}
        real = _DIR_LISTING[d].get(b.lower())
        if not real: return m.group(0)
        stats['INC1'] += 1
        return m.group(1) + os.path.join(os.path.dirname(inc), real).replace(os.sep, '/') + m.group(3)
    return INC_QUOTED.sub(sub, text)

# ---------------------------------------------------------------------------------------------------------- PTR1
#
# The decomp compares a function's address or a pointer with 0 by order (`if (!(GetTime64 > 0))`,
# `component > 0`), which MSVC compiled as an unsigned compare of the address: `p > 0` is `p != 0`, `p <= 0` is
# `p == 0`. C++ rejects an ordered comparison of a pointer with the integer 0. PTR1 writes the equality the compare
# meant for names the file declares as functions, or as pointers in the function that compares them.

PTR_CMP = re.compile(r'(?<![\w.>:])([A-Za-z_]\w*)\s*(>|<=)\s*0(?![\w.])')

def r_pointer_zero_compares(path, text, stats):
    """PTR1: `f > 0` / `p > 0` -> `!= 0`, `<= 0` -> `== 0` for functions and local pointers (see above)."""
    if '> 0' not in text and '<= 0' not in text and '>0' not in text: return text
    funcs = set(re.findall(r'[\w\*&>]\s+(?:__\w+\s+)?([A-Za-z_]\w*)\s*\([^;{}()]*\)\s*;', COMMENT.sub(' ', text)))
    if not funcs and '*' not in text: return text
    spans = None
    out, pos = [], 0
    for m in PTR_CMP.finditer(text):
        name = m.group(1)
        if name in CXX_KEYWORDS or re.match(r'\s*\(', text[m.end(1):m.end(1) + 3]): continue
        if spans is None: spans = [sp for sp in prepare.scope_spans(text) if sp[2] == 'function']
        body = next(((o, c, h) for o, c, _, h in spans if o < m.start() < c), None)
        if body is None: continue
        local = body[2] + '\n' + text[body[0]:m.start()]          # parameters, then the body up to the compare
        is_ptr = re.search(r'\b[A-Za-z_][\w:]*(?:\s*<[^<>;]*>)?\s*\*+\s*%s\s*[=;,)]' % re.escape(name), local)
        declared_other = re.search(r'\b(?:int|unsigned|short|char|long|float|double|bool|__int64|DWORD|UINT|size_t)\s+%s\b'
                                   % re.escape(name), local)
        if declared_other: continue
        if not is_ptr and not (name in funcs and not re.search(r'[\w\*&>]\s+%s\s*[=;,\[]' % re.escape(name), local)):
            continue
        out.append(text[pos:m.start(2)]); out.append('!=' if m.group(2) == '>' else '=='); pos = m.end(2)
        stats['PTR1'] += 1
    if not out: return text
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- STAND1
#
# One function per address. Some units carry an untagged stand-in next to the tagged definition of the same address:
# blk_00620000_od.cpp has a global `struct FUN_00632ec0 { char m_data[0x114]; FUN_00632ec0(); }` with an empty
# constructor, while the tagged `fnv_00632ec0::FUN_00632ec0::FUN_00632ec0` (0x00632ec0) stores the vptr; the recipe
# binds the stand-in's name ??0FUN_00632ec0@@QAE@XZ to 0x00632ec0 too. MSVC's /Od code called the stand-in by name
# and the link took it to 0x00632ec0; GCC inlines the empty body and `new FUN_00632ec0` leaves the vptr 0 (S5 g4:
# 60 null reads). STAND1 removes an untagged out-of-class or free definition whose MSVC name the unit's units.json
# symbols bind to an address that a tagged definition in the same unit provides: the calls stay calls of that name,
# which the link binds to the tagged function's address. A free function keeps its declaration.

STAND_DEF = re.compile(r'^[ \t]*((?:[\w:<>,\*&]+[ \t\*&]+)*?)((?:[A-Za-z_]\w*\s*::\s*)*~?[A-Za-z_]\w*)\s*'
                       r'\(([^;{}()]*(?:\([^;{}()]*\)[^;{}()]*)*)\)\s*(?:const\s*)?(?:throw\s*\([^)]*\)\s*)?(?::[^;{}]*)?\{', re.M)
FNV_TAG_ADDR = re.compile(r'//[ \t]*@fnv[ \t]+0x([0-9a-fA-F]+)')
NOT_FUNCTIONS = set('if for while switch return sizeof catch do else'.split())

def r_stand_in_definitions(path, text, stats):
    """STAND1: drop untagged definitions bound to the address of a tagged definition in the same unit (see above)."""
    if not path.endswith('.cpp') or '@fnv' not in text: return text
    syms = _unit_symbols(path)
    if not syms: return text
    tagged = {int(a, 16) for a in FNV_TAG_ADDR.findall(text)}
    spans, edits = None, []
    for m in STAND_DEF.finditer(text):
        q = re.sub(r'\s+', '', m.group(2))
        parts = q.split('::')
        if parts[-1] in NOT_FUNCTIONS or parts[-1] in CXX_KEYWORDS: continue
        if re.match(r'\s*(?:return|else|case|new|delete|throw)\b', m.group(1) or ''): continue
        before = text[max(0, m.start() - 400):m.start()]
        if re.search(r'//[ \t]*@fnv[ \t]+0x[0-9a-fA-F]+[^\n]*\n(?:[ \t]*//[^\n]*\n)*[ \t]*$', before): continue
        if spans is None: spans = prepare.scope_spans(text)
        chain = prepare.scope_chain(spans, m.start())
        if any(k != 'namespace' for k, _ in chain): continue
        ns = [h.split()[-1] for _, h in chain if h.split()[-1] != 'namespace']
        scope = list(reversed(parts[:-1])) + list(reversed(ns))
        name = parts[-1]
        if len(parts) >= 2 and name == parts[-2]: prefix = '??0' + '@'.join(scope) + '@@'
        elif name.startswith('~'): prefix = '??1' + '@'.join(scope) + '@@'
        else: prefix = '?' + '@'.join([name] + scope) + '@@'
        if not any(k.startswith(prefix) and int(a, 16) in tagged for k, a in syms.items()): continue
        o = m.end() - 1
        c = prepare.match_brace(text, o)
        start = m.start() + len(text[m.start():m.end()]) - len(text[m.start():m.end()].lstrip())
        for _ in range(3):                                 # the head may begin on the lines above
            ls = text.rfind('\n', 0, start - 1) + 1 if start > 0 else 0
            prev = text[ls:start].strip()
            if not prev or prev.startswith(('#', '//')) or prev.endswith((';', '}', '{', '*/', ':', ')')): break
            start = ls
        nl = '\n' * text.count('\n', start, c)
        if len(parts) == 1:                                # a free function keeps its declaration
            if re.search(r'\bstatic\b', m.group(1) or ''): continue     # internal: the link cannot rebind it
            head = text[start:o].rstrip()
            head = re.sub(r'\b(?:inline|__inline|__forceinline)\s+', '', head)
            edits.append((start, c, head + '; /* fnv-nx STAND1 */' + nl[head.count('\n'):]))
        else:
            edits.append((start, c, '/* fnv-nx STAND1: %s is the function at the tagged address */%s' % (q, nl)))
    if not edits: return text
    out, pos = [], 0
    for a, b, r in sorted(edits):
        if a < pos: continue
        out.append(text[pos:a]); out.append(r); pos = b
        stats['STAND1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- CSX2
#
# D15 reopened (J-0067): the decomp's generated layouts were taken from the Xbox 360 build, whose
# RTL_CRITICAL_SECTION is 28 bytes, and the PC code keeps those 28 bytes. S11's MSVC gate shows the byte-matched
# GraphicsRenderer::BeginUsingRenderTargetGroup reading +0x200 / +0x208, which only holds when each of its three
# NiCriticalSections is 128 bytes with m_ulThreadOwner at 0x1C - 4 bytes behind the 24-byte PC section (the
# section itself starts at +0x0: NiCriticalSection hands `this` to EnterCriticalSection). CSX2 puts those 4 bytes
# back behind every member of the decomp's generated `_RTL_CRITICAL_SECTION` type whose class asserts the next
# member (or its size) 0x1C bytes after the section: NiCriticalSection (7 layout copies) and RecursiveLock, and with
# them every holder (GraphicsRenderer, BSShaderAccumulator, NiGlobalStringTable, NiRenderer, BSThread, AbstractHeap,
# OEI::IO::IO_Thread). The decomp's asserts then hold again; what the PC build keeps in those 4 bytes is not known
# (no code of the binary reads them).

CS_MEMBER = re.compile(r'^([ \t]*)_RTL_CRITICAL_SECTION\s+([A-Za-z_]\w*)\s*;', re.M)

def r_critical_section_tail(path, text, stats):
    """CSX2: 4 bytes behind a generated-layout _RTL_CRITICAL_SECTION member that the class asserts as 28 bytes."""
    if '_RTL_CRITICAL_SECTION' not in text or 'offsetof' not in text and 'sizeof' not in text: return text
    spans, edits = None, []
    for m in CS_MEMBER.finditer(text):
        if spans is None: spans = prepare.scope_spans(text)
        cls = [sp for sp in spans if sp[0] < m.start() < sp[1] and sp[2] == 'class']
        if not cls: continue
        o, c, _, head = max(cls)
        name_m = re.search(r'(?:class|struct)\s+([A-Za-z_]\w*)\b(?:(?!\b(?:class|struct)\b)[^{])*$', head)
        if not name_m: continue
        cname, member = name_m.group(1), m.group(2)
        asserts = text[c:c + 8000]
        at = re.search(r'offsetof\s*\(\s*%s\s*,\s*%s\s*\)\s*==\s*(0[xX][0-9a-fA-F]+|\d+)' % (cname, member), asserts)
        if not at: continue
        start = int(at.group(1), 0)
        eol = text.find('\n', m.end())
        nxt = re.match(r'\s*(?:[\w:<>\*& \t]+?[\s\*&])([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*;', text[eol:c])
        want = None
        if nxt:
            nm = re.search(r'offsetof\s*\(\s*%s\s*,\s*%s\s*\)\s*==\s*(0[xX][0-9a-fA-F]+|\d+)' % (cname, nxt.group(1)), asserts)
            if nm: want = int(nm.group(1), 0)
        else:
            sm = re.search(r'sizeof\s*\(\s*%s\s*\)\s*==\s*(0[xX][0-9a-fA-F]+|\d+)' % cname, asserts)
            if sm: want = int(sm.group(1), 0)
        if want != start + 0x1C: continue
        edits.append((m.end(), ' unsigned int %s_fnvnx_tail; /* fnv-nx CSX2: the 28 bytes of the layout */' % member))
    if not edits: return text
    out, pos = [], 0
    for at, s in edits:
        out.append(text[pos:at]); out.append(s); pos = at
        stats['CSX2'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- VTBL1
#
# A stand-in polymorphic class whose native vtable is too short for the slots the game calls through it. The
# decomp declares BSNiAllocator (constructor 0x00aa2230, vftable 0x010a2528) with one virtual, so the native vtable
# has one slot, while NiMemoryManager calls slot 1 (Allocate, FUN_00aa1070), slot 4 (FUN_00aa1130) and InitHolder
# slot 7 (S14 r2: the vftable has 10 slots, bounded by the next COL word at 0x010a2550). VTBL1 gives the stand-in
# the interface in MSVC slot order. The slot functions the decomp knows are its BSNiAllocator functions
# (gamebryo2.2/corelibs/nisystem/bsniallocator.cpp, called through these slots with these signatures in
# nimemorydefines.cpp): 0 scalar deleting destructor 0x00aa24a0, 1 Allocate 0x00aa2240, 2 Deallocate 0x00aa22b0
# (S13), 3 Reallocate 0x00aa22f0, 4 aligned Allocate 0x00aa2370, 5 aligned Deallocate 0x00aa23e0, 6 aligned
# Reallocate 0x00aa2420; each slot forwards to that function by its decomp name (the link binds it by address).
# Slots 7-9 (7 is InitHolder's no-argument call) have no function in the decomp: declared only, they stay
# unresolved until the exe gives their addresses.
#
# NiStandardAllocator (vftable 0x010a24fc, 10 slots; nigpuprogramcache.cpp's FUN_00aa2200 creates it with one
# virtual, and InitHolder's tail call goes through its slot 7, f13 J-0080). Its functions are the ones of
# gamebryo2.2/corelibs/nisystem/nistandardallocator.cpp (the 360 source path of 0x00aa3090..0x00aa3560), laid out in
# slot order like BSNiAllocator's and with the same signatures: 1 Allocate 0x00aa30c0 (prepends the size for the
# default alignment, small blocks <= 0x100 from the small-object allocator DAT_011f622c), 2 Deallocate 0x00aa3150,
# 3 Reallocate 0x00aa31b0 (StdAllocator::FUN_00aa31b0, which calls slots 1 and 2 itself), 4-6 the aligned set
# 0x00aa3300 / 0x00aa3370 / 0x00aa33a0 (event 0xd / 0xe aligned blocks from MemoryManager), 7 Initialize 0x00aa3430
# (creates the small-object allocator, Gamebryo's NiStandardAllocator::Initialize) and 8 Shutdown 0x00aa3460
# (deletes it, NiStandardAllocator::Shutdown); `this` is unused in all of them, the decomp writes them as free
# __stdcall / __cdecl functions. Slot 0 (scalar deleting destructor) and slot 9 have no function in the decomp and
# stay unresolved (slot 0 is declared inline so that Allocate is the key function and the vtable is emitted).
# StdAllocator::FUN_00aa31b0 reaches slots 1 and 2 through StdAllocator's own declaration, whose virtual destructor
# takes two Itanium slots: there it becomes the MSVC slot-0 plain virtual, so Allocate / Deallocate are slots 1 / 2.

VTBL1_NISTD = '''unsigned char* __stdcall FUN_00aa30c0(unsigned int* pCount, int* pType, int eType, unsigned char bFlag, int a4, int a5, int a6);
void __stdcall FUN_00aa3150(void* pvMemory, int aiAllocationType, unsigned int stSizeInBytes);
struct StdAllocator
{
    void* FUN_00aa31b0(void* pvMemory, unsigned int& stSizeInBytes, unsigned int& stAlignment, int aiAllocationType,
                       bool bProvideAccurateSizeOnDeallocate, unsigned int stSizeCurrent, const char* pcFile, int iLine,
                       const char* pcFunction);
};
void* __stdcall FUN_00aa3300(unsigned int puiSize, unsigned char* pucAlign, int iType, int iUnk, int iUnkCur, int iUnkValue, int iNewUnk);
void __stdcall FUN_00aa3370(void* apBlock, int e, unsigned int s);
void* __stdcall FUN_00aa33a0(void* pvMem, unsigned int* puiSize, unsigned char* pucAlign, int iType, int iUnk, int iUnkCur, int iUnkValue, int iNewUnk, int iTmpUnk);
namespace fnv_00aa3430 { void __cdecl AllocateStandardAllocator(); }
void __cdecl DestroySmallAllocPools();
struct NiStandardAllocator   /* fnv-nx VTBL1: the 10 slots of the vftable at 0x010a24fc in MSVC order */
{
    static void* __cdecl operator new(size_t);   /* NEW1's form */
    inline virtual void* Destroy(unsigned int flags);   /* slot 0: not in the decomp */
    virtual void* Allocate(unsigned int& size, unsigned int& alignment, int event, bool accurate, const char* file,
                           int line, const char* function);   /* the key function: defined below */
    virtual void Deallocate(void* p, int event, unsigned int size) { FUN_00aa3150(p, event, size); }
    virtual void* Reallocate(void* p, unsigned int& size, unsigned int& alignment, int event, bool accurate,
                             unsigned int current, const char* file, int line, const char* function)
    {
        return ((StdAllocator*)this)->FUN_00aa31b0(p, size, alignment, event, accurate, current, file, line, function);
    }
    virtual void* AlignedAllocate(unsigned int& size, unsigned int& alignment, int event, bool accurate,
                                  const char* file, int line, const char* function)
    {
        return FUN_00aa3300((unsigned int)&size, (unsigned char*)&alignment, event, accurate, (int)file, line,
                            (int)function);
    }
    virtual void AlignedDeallocate(void* p, int event, unsigned int size) { FUN_00aa3370(p, event, size); }
    virtual void* AlignedReallocate(void* p, unsigned int& size, unsigned int& alignment, int event, bool accurate,
                                    unsigned int current, const char* file, int line, const char* function)
    {
        return FUN_00aa33a0(p, &size, (unsigned char*)&alignment, event, accurate, (int)current, (int)file, line,
                            (int)function);
    }
    virtual void Initialize() { fnv_00aa3430::AllocateStandardAllocator(); }
    virtual void Shutdown() { DestroySmallAllocPools(); }
    virtual void Slot9();   /* not in the decomp */
};
void* NiStandardAllocator::Allocate(unsigned int& size, unsigned int& alignment, int event, bool accurate,
                                    const char* file, int line, const char* function)
{
    return FUN_00aa30c0(&size, (int*)&alignment, event, accurate, (int)file, line, (int)function);
}'''
VTBL1_NISTD_RX = re.compile(r'^struct NiStandardAllocator\s*\{\s*static void\* __cdecl operator new\((?:unsigned int|size_t)\);\s*'
                            r'virtual void func\(\);\s*\};', re.M)

# A660A0Map (gamebryo2.2/corelibs/nimain/nistream.cpp, namespace fnv_00a660a0) is the NiStream loader map
# DAT_011f4784 (Gamebryo's NiStream::ms_pkLoaders, a NiTStringPointerMap<CreateFunction>) that FUN_00a660a0 creates
# with `new A660A0Map`, vftable 0x0109bf4c (units.json `??_7A660A0Map@fnv_00a660a0@@6B@`). The decomp declares only its
# destructor, so the native vtable has D1/D0 and nothing else, while RegisterObjectLoader 0x00a64900 calls
# NiTStringTemplateMap<...>::SetAt 0x00a642a0, which calls KeyToHashIndex, IsKeysEqual and SetValue through it
# (Itanium slots 2-4 of nitstringmap_decls.h's NiTStringTemplateMap: D1, D0, then those three). The interface is
# Gamebryo's NiTMapBase in MSVC order: 0 destructor, 1 KeyToHashIndex, 2 IsKeysEqual, 3 SetValue, 4 ClearValue,
# 5 NewItem, 6 DeleteItem. No slot has a function in the decomp: 0x0109bf4c is bound by nistream.cpp only (and only
# with that destructor), its base chain 0x0109bcc4 / 0x0109bca4 / 0x0109bc84 is written only by the destructors
# 0x00a63850 / 0x00a63a20 of other maps, and the decomp's string-map slot definitions belong to other vftables
# (NiTStringTemplateMap<.., int>::SetValue 0x00a1e770, Tile__MenuStringMap's case-insensitive 0x00a0d180 / 0x00a0d1b0,
# NiTStringPointerMap<ShaderBufferRecord*> at 0x010af6cc). Folding by MSVC would make some of them the same code, but
# that is analogy, not evidence: all six are declared only (vslot traps, unresolved until the exe's vftable gives
# their addresses). The native vtable then has the slots SetAt calls, at the same Itanium positions as SetAt's
# declaration. A660A0MapBase (0x0109bc84) keeps its one virtual: only construction and destruction use its vptr.
A660A0_SLOTS = ('virtual unsigned int KeyToHashIndex(const char* key) const; '
                'virtual bool IsKeysEqual(const char* key1, const char* key2) const; '
                'virtual void SetValue(void* item, const char* key, void* value); virtual void ClearValue(void* item); '
                'virtual void* NewItem(); virtual void DeleteItem(void* item);')

VTBL1_SITES = [
    ('gamebryo2.2/corelibs/nimain/nigpuprogramcache.cpp', VTBL1_NISTD_RX, VTBL1_NISTD),
    ('gamebryo2.2/corelibs/nimain/nigpuprogramcache_rtm_variant.cpp', VTBL1_NISTD_RX, VTBL1_NISTD),
    ('gamebryo2.2/corelibs/nisystem/nistandardallocator.cpp',
     re.compile(r'^([ \t]*)virtual ~StdAllocator\(\);', re.M),
     r'\1virtual void* Destroy(unsigned int flags);   /* fnv-nx VTBL1: MSVC slot 0, the scalar deleting destructor */'),
    # the second heap MemoryManager::InitializeHeaps creates (constructor 0x00aa7400): the decomp declares one
    # virtual, MemoryManager::Allocate+0xa8 calls its slot 2 (S5 J-0102: 4,327 of 4,343 initializer skips); it is an
    # AbstractHeap, whose 14 slots the same unit declares (v0 .. v13): the native vtable gets them all
    ('staging/blk_00aa0000_od_ob1_variant.cpp',
     re.compile(r'(\n[ \t]*HeapBlockLink m_20;\s*FUN_00aa7400\(unsigned int a, unsigned int b\);\s*virtual void v00\(\);)(\s*\};)'),
     r'\1 /* fnv-nx VTBL1: the AbstractHeap slots 1-13 */ virtual void v1(); virtual void v2(); virtual void v3(); '
     r'virtual void v4(); virtual void v5(); virtual void v6(); virtual void v7(); virtual void v8(); virtual void v9(); '
     r'virtual void v10(); virtual void v11(); virtual void v12(); virtual void v13(void* value);\2'),
    # the NiStream loader map (DAT_011f4784, vftable 0x0109bf4c) FUN_00a660a0 creates: the decomp declares only the
    # destructor, NiTStringTemplateMap::SetAt 0x00a642a0 (nitstringmap.inl) calls KeyToHashIndex, IsKeysEqual and
    # SetValue through it (see A660A0_SLOTS above)
    ('gamebryo2.2/corelibs/nimain/nistream.cpp',
     re.compile(r'(\bclass A660A0Map : public A660A0MapBase\s*\{[^{}]*\{\s*\}\s*virtual ~A660A0Map\(\);)(\s*\};)'),
     r'\1 /* fnv-nx VTBL1: NiTMapBase slots 1-6 in MSVC order, none in the decomp (vslot traps) */ ' + A660A0_SLOTS
     + r'\2'),
    ('staging/blk_00aa0000_o2.cpp',
        re.compile(r'^struct FUN_00aa2230_BSNiAllocator\s*\{\s*virtual void m_virtual0\(\);\s*'
                   r'FUN_00aa2230_BSNiAllocator\(\);\s*\};', re.M),
        '''enum NiMemEventTypeAA2 { NI_MEM_EVENT_AA2_UNKNOWN };
void* __stdcall AllocateZeroedMemory(unsigned int&, unsigned int&, NiMemEventTypeAA2, bool, const char*, int, const char*);
struct BSNiAllocator { void FUN_00aa22b0(void* pvMemory, int aiAllocationType, unsigned int stSizeInBytes); };
char* __stdcall ResizeHeapBlock(char*, int*, unsigned char*, int, int, int, int, int, int);
unsigned int __stdcall AllocateAlignedBuffer(unsigned int*, unsigned char*, int, int, int, int, int);
namespace fnv_00aa23e0 { void __stdcall FUN_00aa23e0(void* pvMemory, int aiAllocationType, unsigned int stSizeInBytes); }
void* __stdcall FUN_00aa2420(void*, unsigned int&, unsigned char&, int, bool, unsigned int, const char*, int, const char*);
namespace fnv_00aa24a0 { struct BSNiAllocator { void* Destroy(unsigned int); }; }
struct FUN_00aa2230_BSNiAllocator   /* fnv-nx VTBL1: the 10 slots of the vftable at 0x010a2528 in MSVC order */
{
    virtual void* Destroy(unsigned int flags);   /* the key function: defined below, so the vtable is emitted here */
    virtual void* Allocate(unsigned int& size, unsigned int& alignment, int event, bool accurate, const char* file,
                           int line, const char* function)
    {
        return AllocateZeroedMemory(size, alignment, (NiMemEventTypeAA2)event, accurate, file, line, function);
    }
    virtual void Deallocate(void* p, int event, unsigned int size) { ((BSNiAllocator*)this)->FUN_00aa22b0(p, event, size); }
    virtual void* Reallocate(void* p, unsigned int& size, unsigned char& alignment, int event, bool accurate,
                             unsigned int current, const char* file, int line, const char* function)
    {
        return ResizeHeapBlock((char*)p, (int*)&size, &alignment, event, accurate, (int)current, (int)file, line,
                               (int)function);
    }
    virtual void* AlignedAllocate(unsigned int& size, unsigned int& alignment, int event, bool accurate,
                                  const char* file, int line, const char* function)
    {
        return (void*)AllocateAlignedBuffer(&size, (unsigned char*)&alignment, event, accurate, (int)file, line,
                                            (int)function);
    }
    virtual void AlignedDeallocate(void* p, int event, unsigned int size) { fnv_00aa23e0::FUN_00aa23e0(p, event, size); }
    virtual void* AlignedReallocate(void* p, unsigned int& size, unsigned char& alignment, int event, bool accurate,
                                    unsigned int current, const char* file, int line, const char* function)
    {
        return FUN_00aa2420(p, size, alignment, event, accurate, current, file, line, function);
    }
    virtual void Slot7();   /* InitHolder's call (Initialize?): not in the decomp */
    virtual void Slot8();   /* not in the decomp */
    virtual void Slot9();   /* not in the decomp */
    FUN_00aa2230_BSNiAllocator();
};
void* FUN_00aa2230_BSNiAllocator::Destroy(unsigned int flags)
{
    return ((fnv_00aa24a0::BSNiAllocator*)this)->Destroy(flags);
}'''),
]

def r_stand_in_interfaces(path, text, stats):
    """VTBL1: the full virtual interface, in MSVC slot order, for the stand-ins listed in VTBL1_SITES (see above)."""
    rel = path.replace(os.sep, '/')
    for unit, rx, body in VTBL1_SITES:
        if not rel.endswith('/src/' + unit): continue
        m = rx.search(text)
        if not m: continue
        # same line count: the replacement is longer, so it goes on the stand-in's lines joined up
        nl = m.group(0).count('\n')
        text = text[:m.start()] + m.expand(body).replace('\n', ' ') + '\n' * nl + text[m.end():]
        stats['VTBL1'] += 1
    return text

# ---------------------------------------------------------------------------------------------------------- ALIGN1
#
# `__declspec(align(16)) struct S {...};`: MSVC aligns the type S (also with a declarator after the body, and on a
# forward declaration `__declspec(align(16)) struct S;`, which aligns S where it is defined: clang's MS mode, which
# follows MSVC, gives sizeof/alignof 64/16 for a 56-byte record in all three forms). GCC takes an attribute in front
# of the class key as the declaration's, not the type's: with no declarator it is dropped ("attribute ignored in
# declaration of", -Wattributes), with one it aligns only that variable, so the record is 4-aligned and the records
# that hold it shrink and move their members (S12 f8 ALIGN-IGNORED / LAYOUT-ALIGN-IGNORED: S_A 48 -> 36, Buf 64 ->
# 56, hkpMotionStub 240 -> 224). ALIGN1 moves the align specifier behind the class key, where both compilers apply
# it to the type. Only in front of a class definition or a forward declaration: in `__declspec(align(16)) struct
# Atoms m_kBridgeAtoms;` the class key starts an elaborated type and the alignment is the member's, on both.

ALIGN_HEAD = re.compile(r'(__declspec\s*\(\s*align\s*\(\s*\d+\s*\)\s*\))(\s*)'
                        r'((?:__declspec\s*\((?:[^()]|\([^()]*\))*\)\s*)*)\b(struct|class|union)\b(?=([^;{}()]*)([;{]))')

def r_class_alignment(path, text, stats):
    """ALIGN1: `__declspec(align(n)) struct S` -> `struct __declspec(align(n)) S` in front of a class definition or a
    forward declaration (see above). Same line count."""
    if 'align' not in text: return text
    def sub(m):
        head, end = m.group(5), m.group(6)
        if end == ';' and not re.fullmatch(r'\s*[A-Za-z_]\w*\s*', head): return m.group(0)   # elaborated type use
        if end == '{' and re.search(r'[=,]|\boperator\b', head.split(':')[0]): return m.group(0)
        stats['ALIGN1'] += 1
        ws = m.group(2) if '\n' in m.group(2) else ''
        return m.group(3) + m.group(4) + ' ' + m.group(1) + ws
    return ALIGN_HEAD.sub(sub, text)

# ---------------------------------------------------------------------------------------------------------- BIND1
#
# A member or variable of a stand-in class that the unit uses but units.json does not bind: MSVC never emitted a
# reference to it as such (the use sits in an inline function MSVC compiled out of line, and the matcher bound that
# copy to the exe's function), while GCC inlines that function and references the name itself, which the link cannot
# resolve (S2 leaves it to a trap). BIND1 adds the binding to the unit's symbols in the prepared build/units.json
# (POST hook) where the decomp shows what the name is in the exe:
# * blk_00aa0000_od_ob1_variant.cpp, MemoryManager's constructor 0x00aa3880 (f13, J-0080): its m_kTable is a
#   TableAA3880 whose base constructor TableBaseAA3880(37) allocates through g_kMemoryAA3880.Allocate. The unit
#   binds that inline constructor (??0TableBaseAA3880@@QAE@I@Z) to 0x00aa47e0, and the decomp's 0x00aa47e0
#   (blk_00aa0000_od.cpp, FUN_00aa47e0_Class::FUN_00aa47e0) makes that allocation as g_memoryManager.Allocate, which
#   its unit binds to 0x00aa3e40 (MemoryManager::Allocate, defined in this unit as MemoryManagerStandIn::FUN_00aa3e40)
#   on 0x011f6238 (the memory manager, ?DAT_011f6238@@3UMemoryManager@@A here too).

# VLIT1's `DAT_<addr>` names, bound in their unit (the C name `_DAT_<addr>`), filled while the tree is prepared
VLIT1_BINDS = set()
BIND1_REFS = [
    # (unit source, MSVC decorated name, address)
    ('src/staging/blk_00aa0000_od_ob1_variant.cpp', '?Allocate@MemoryManagerAA3880@@QAEPAXI@Z', '0x00aa3e40'),
    ('src/staging/blk_00aa0000_od_ob1_variant.cpp', '?g_kMemoryAA3880@@3UMemoryManagerAA3880@@A', '0x011f6238'),
]

def bind_units_data(data, stats, refs=BIND1_REFS):
    """BIND1 on a units.json object (in place): add each listed name to its unit's symbols unless it is bound."""
    by_src = {}
    for src, name, addr in refs:
        by_src.setdefault(src, []).append((name, addr))
    for u in data['units']:
        for name, addr in by_src.get(u['source'], ()):
            if name not in u.setdefault('symbols', {}):
                u['symbols'][name] = addr
                stats['BIND1'] += 1
    return data

def bind_stand_in_names(out_root, stats, files=None):
    """POST hook: BIND1 on <out_root>/build/units.json."""
    import json
    p = os.path.join(out_root, 'build', 'units.json')
    if not os.path.exists(p): return
    data = json.load(open(p))
    before = stats['BIND1']
    bind_units_data(data, stats, BIND1_REFS + sorted(VLIT1_BINDS))
    if stats['BIND1'] != before:
        json.dump(data, open(p, 'w'))
        if files is not None: files['BIND1'] += 1

POST = [bind_stand_in_names]

# ---------------------------------------------------------------------------------------------------------- VA2
#
# `va_arg(ap, Index)` with an enum type: an enum argument passed through `...` is promoted to its underlying type,
# so in C++ the va_arg of the enum itself is undefined and GCC compiles it to a trap ("is promoted to 'int' when
# passed through '...'; if this code is reached, the program will abort"; S1b: RegisterActorValue 0x0066efe0).
# MSVC read the int the caller pushed. VA2 reads that int and converts it: `(Index)va_arg(ap, int)`.

VA_ARG = re.compile(r'\bva_arg\s*\(\s*([A-Za-z_]\w*)\s*,\s*((?:[A-Za-z_]\w*::)*([A-Za-z_]\w*))\s*\)')
ENUM_NAME = re.compile(r'\benum\s+(?:class\s+|struct\s+)?([A-Za-z_]\w*)\s*(?::[^;{]*)?[;{]')
_ENUM_NAMES = None

def _enum_names(path):
    global _ENUM_NAMES
    if _ENUM_NAMES is not None: return _ENUM_NAMES
    names, root = set(), _roots(path)
    for d in ('src', 'include'):
        for dp, _, fs in os.walk(os.path.join(root, d)):
            for f in fs:
                if not f.endswith(('.h', '.cpp', '.inl', '.hpp', '.c')): continue
                with open(os.path.join(dp, f), encoding='utf-8', errors='surrogateescape') as h: t = h.read()
                if 'enum' in t: names.update(ENUM_NAME.findall(t))
    _ENUM_NAMES = names
    return names

def r_va_enum_args(path, text, stats):
    """VA2: `va_arg(ap, E)` with an enum E -> `(E)va_arg(ap, int)` (see above)."""
    if 'va_arg' not in text: return text
    local = set(ENUM_NAME.findall(text))
    def sub(m):
        if m.group(3) not in local and m.group(3) not in _enum_names(path): return m.group(0)
        stats['VA2'] += 1
        return '(%s)va_arg(%s, int)' % (m.group(2), m.group(1))
    return VA_ARG.sub(sub, text)

# ---------------------------------------------------------------------------------------------------------- VLIT1
#
# A vptr store the decompiler printed as a string literal: `m_p = "@K@";` in nitmapbase.cpp's FUN_00404b10. The
# instruction stores an image address whose bytes it read as text: "@K@\0" is 0x00404b40, slot 0 (the scalar
# deleting destructor, S00404b40::DestroyIfFlagged) of the vftable at that address (S5 J-0102: the
# GameSettingCollection vptr, 3,454 setting initializers). The unit's recipe binds the literal (MSVC `$SG2541`)
# to that vftable address, 0x0101212c. VLIT1 stores the address through the image symbol the link maps image
# vftables by: `m_p = (char*)&DAT_0101212c;`, with `extern "C" char DAT_0101212c;` declared at namespace scope in
# front of the storing function (S2c 1aeaa9b: an unbound DAT_<hex> right side counts as that image address, so the
# store can be mapped to a native vtable; a raw constant could not be). The comment goes behind the `;`, outside the
# statement the link reads. Only where a literal
# of at most 3 characters stored with `=` reads as the address of a function the recipe defines, and the unit's
# `$SG` binding can only be that literal (the unit binds one `$SG` address and the file has one string literal);
# the other sites (45 in the tree) are counted as VLIT1-LIST and left as they are until the exe says which.

VLIT_STORE = re.compile(r'(=\s*)"([^"\\\n]{1,3})"(\s*;)')
STRING_LIT = re.compile(r'"(?:\\.|[^"\\\n])*"')
_VLIT = None

def _vlit_index(path):
    """(defined function addresses, {unit source: {$SG addresses}})"""
    global _VLIT
    if _VLIT is not None: return _VLIT
    import json
    root, uj = prepare._units_json(path)
    defs, sg = set(), {}
    if uj:
        for u in json.load(open(uj))['units']:
            defs.update(int(f['address'], 16) for f in u.get('functions', []))
            s = {int(v, 16) for k, v in u.get('symbols', {}).items() if k.startswith('$SG')}
            sg.setdefault(os.path.normpath(os.path.join(root, u['source'])), set()).update(s)
    _VLIT = (defs, sg)
    return _VLIT

def r_vptr_literals(path, text, stats):
    """VLIT1: a 1-3 character string literal stored as a vptr -> the vftable address the unit binds it to (see
    above)."""
    if '"' not in text: return text
    hits = []
    for m in VLIT_STORE.finditer(text):
        a = int.from_bytes((m.group(2).encode('latin-1', 'replace') + b'\0' * 4)[:4], 'little')
        if 0x00401000 <= a < 0x00fde578: hits.append((m, a))
    if not hits: return text
    defs, sg = _vlit_index(path)
    hits = [(m, a) for m, a in hits if a in defs]
    if not hits: return text
    addrs = sg.get(os.path.normpath(os.path.abspath(path)), set())
    code = re.sub(r'^[ \t]*#[^\n]*', '', COMMENT.sub(' ', text), flags=re.M)
    if len(addrs) != 1 or len(hits) != 1 or len(STRING_LIT.findall(code)) != 1:
        stats['VLIT1-LIST'] += len(hits)
        return text
    m, _ = hits[0]
    stats['VLIT1'] += 1
    name = 'DAT_%08x' % next(iter(addrs))
    root, _ = prepare._units_json(path)
    if root: VLIT1_BINDS.add((os.path.relpath(os.path.abspath(path), root).replace(os.sep, '/'), '_' + name,
                              '0x%08x' % next(iter(addrs))))
    text = (text[:m.start()] + '%s(char*)&%s%s /* fnv-nx VLIT1: "%s" */' % (m.group(1), name, m.group(3),
                                                                             m.group(2).replace('*/', '* /'))
            + text[m.end():])
    # the declaration, at namespace scope in front of the outermost function or class around the store (same line)
    masked = FRIEND_MASK.sub(lambda x: re.sub(r'[^\n]', ' ', x.group()), text)
    outer = [sp for sp in prepare.scope_map(masked) if sp[0] < m.start() < sp[1] and sp[2] not in ('namespace', 'extern')]
    at = _decl_start(masked, outer[0][0]) if outer else 0
    return text[:at] + 'extern "C" char %s; /* fnv-nx VLIT1 */ ' % name + text[at:]

# ---------------------------------------------------------------------------------------------------------- KEEP1

FNV_TAG = re.compile(r'^[ \t]*//[ \t]*@fnv[ \t]+0x[0-9a-fA-F]+[^\n]*\n((?:[ \t]*//[^\n]*\n)*)([ \t]*)(?=\S)', re.M)
KEEP_SKIP = re.compile(r'(?:#|//|/\*|template\b|extern\s*"C|namespace\b|typedef\b|using\b|struct\b|class\b|union\b|'
                       r'enum\b|static_assert\b|FNV_STATIC_ASSERT\b|[{}:,)]|'   # `: base(...)` of a constructor too
                       # a tag left in front of a statement inside a body
                       r'(?:if|for|while|do|switch|return|else|goto|break|continue|case|default|try)\b)')
ACCESS_LABEL = re.compile(r'(?:public|private|protected)\s*:(?!:)\s*')

def r_keep_definitions(path, text, stats):
    """KEEP1: FNVNX_KEEP (__attribute__((used))) in front of every declaration tagged `// @fnv 0x...`, so a definition
    the optimizer inlines everywhere is still emitted out of line at its address (the link step binds by address,
    tools/link). Same line, so line numbers are kept."""
    if '@fnv' not in text: return text
    out, pos = [], 0
    for m in FNV_TAG.finditer(text):
        lab = ACCESS_LABEL.match(text, m.end())          # a tag above an access label: the declaration after it
        at = lab.end() if lab else m.end()
        if KEEP_SKIP.match(text, at): continue
        out.append(text[pos:at]); out.append('FNVNX_KEEP '); pos = at
        stats['KEEP1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- INIT1
#
# A static object whose dynamic initializer the exe has and the decomp does not. Gamebryo gives every unit that
# includes NiMain.h a `static NiMainSDM` object; its constructor (0x00a8a5f0, `FUN_00a8a5f0_class::FUN_00a8a5f0` in
# staging/blk_00a80000_o2.cpp) counts the objects in DAT_011f5b84 and the first one registers NiMain's init and
# shutdown (InitializeGameSystems 0x00a89cc0, FUN_00a8a280) with NiStaticDataManager (FUN_00aa2060: 0x011f6110 /
# 0x011f6114), which NiInit 0x00aa20b0 runs. The exe calls it from many dynamic initializers; nothing in the decomp
# calls it, so on the target 0x011f6110 stays 0, NiInit skips NiMain's init and the NiStream loader map, its
# post-process array and the global string table stay NULL (S5 J-0106, known_faults `before-winmain 0x00a8a5f0`).
# INIT1 adds one such static to the constructor's own unit. S5 (J-0109, TOOLCHAIN.md section 19): a native static's
# .init_array entry runs after boot_banner (data image mapped, memory manager constructed), before every game
# initializer and before WinMain. Game statics run before the platform's constructors, so the constructor may not
# use the clock, the profile strings or exceptions: 0x00a8a5f0 and FUN_00aa2060 only write image variables. Same
# line count: the static joins the line of the constructor's closing brace.

INIT1_SITES = [
    ('staging/blk_00a80000_o2.cpp',
     re.compile(r'(// @fnv 0x00a8a5f0\s*\nvoid\* FUN_00a8a5f0_class::FUN_00a8a5f0\(\)\s*\{(?:[^{}]|\{[^{}]*\})*\})'),
     r"\1 /* fnv-nx INIT1: the exe's static NiMainSDM objects (none in the decomp) */ "
     r'static FUN_00a8a5f0_class fnvnx_init1_NiMainSDM; '
     r'static void* const fnvnx_init1_NiMainSDM_ctor = fnvnx_init1_NiMainSDM.FUN_00a8a5f0();'),
]

def r_static_initializers(path, text, stats):
    """INIT1: static objects whose dynamic initializers the exe has and the decomp lacks (INIT1_SITES, see above)."""
    rel = path.replace(os.sep, '/')
    for unit, rx, repl in INIT1_SITES:
        if not rel.endswith('/src/' + unit): continue
        text, n = rx.subn(repl, text, count=1)
        stats['INIT1'] += n
    return text

# ---------------------------------------------------------------------------------------------------------- FRIEND1
#
# `friend class hkpWorld;` in a class body, with hkpWorld declared nowhere before it: MSVC 2008 makes the friend's
# name visible to ordinary lookup in the enclosing namespace from there on, so the class goes on to use it
# (`hkpWorld* m_pOwningWorld;`, `virtual void WorldAddedCallback(hkpWorld* world);`) and the class itself is defined
# further down. C++ (GCC) does not: the friend declaration names the class but does not make it visible, so every
# later use is "'hkpWorld' does not name a type" (the Havok viewers, hkpContinuousSimulation, hkpCdBody's
# `friend class hkpCollidable;`). FRIEND1 forward-declares the class in front of the outermost class around the
# friend declaration, in the same namespace (where the friend declaration puts it), when the name is declared
# nowhere before: not in the unit's text in front of it, not in the local headers it includes. The class key is
# that of the class's definition in the unit (a later `union X {..}` takes `union X;`), else the friend's. Only at
# namespace scope (not in a local class), and not for a name the unit also uses as a typedef or a template, or a
# template parameter of the enclosing class. Same line count: the declaration joins the line where the class's
# declaration starts.

FRIEND_CLASS = re.compile(r'(?<![\w>])friend\s+(class|struct|union)\s+([A-Za-z_]\w*)\s*;')
FRIEND_MASK = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|//[^\n]*|/\*.*?(?:\*/|\Z)|^[ \t]*#[^\n]*', re.S | re.M)
DECOR = r'(?:__declspec\s*\((?:[^()]|\([^()]*\))*\)\s*|alignas\s*\([^()]*\)\s*)*'

def _decl_start(masked, brace):
    """Where the declaration whose body opens at `brace` begins: the first character after the previous `;`, `{` or
    `}` that is not blank in `masked` (comments, literals and preprocessor lines blanked), so text inserted there
    stays off a preprocessor line and out of a comment."""
    at = max(masked.rfind(c, 0, brace) for c in ';{}') + 1
    return at + len(masked[at:brace]) - len(masked[at:brace].lstrip())

def _friend_declared(view, name):
    """`name` is declared as a type (or anything else) in `view`, a name view of _visible."""
    n = re.escape(name)
    return re.search(r'(?<!friend )\b(?:class|struct|union|enum)\s+%s%s\b(?!\s*<)|\btypedef\b[^;]*\b%s\s*(?:\[[^\]]*\]\s*)?;|'
                     r'\busing\s+%s\s*=|\bnamespace\s+%s\b' % (DECOR, n, n, n, n), view) is not None

def r_friend_class_visibility(path, text, stats):
    """FRIEND1: forward-declare a class that a friend declaration names before it is declared (see above)."""
    if 'friend' not in text: return text
    found = list(FRIEND_CLASS.finditer(text))
    if not found: return text
    masked = FRIEND_MASK.sub(lambda m: re.sub(r'[^\n]', ' ', m.group()), text)
    spans = prepare.scope_spans(masked)
    header_views, prefix_views, adds, done = None, {}, {}, set()
    for m in found:
        if masked[m.start():m.end()] != m.group(): continue          # in a comment or a string
        key, name = m.group(1), m.group(2)
        enclosing = [sp for sp in spans if sp[0] < m.start() < sp[1]]
        if not enclosing or enclosing[-1][2] != 'class': continue
        if any(sp[2] not in ('class', 'namespace', 'extern') for sp in enclosing): continue
        outer = next(sp for sp in enclosing if sp[2] == 'class')
        at = _decl_start(masked, outer[0])                              # where the outer class's declaration starts
        if (at, name) in done: continue
        n = re.escape(name)
        if re.search(r'\btypedef\b[^;{}]*\b%s\s*;|\btemplate\s*<(?:[^<>]|<[^<>]*>)*>\s*(?:class|struct|union)\s+%s%s\b|'
                     r'\busing\s+%s\s*=' % (n, DECOR, n, n), masked):
            continue                                                  # a typedef or a template of that name
        if re.search(r'\b(?:class|typename)\s+%s\s*[,>=]' % n, masked[at:m.start()]): continue   # template parameter
        if at not in prefix_views: prefix_views[at] = _strip_closed(masked[:at], NAME_KEEP)
        if _friend_declared(prefix_views[at], name): continue
        if header_views is None:
            parts = []
            _visible('\n'.join(l for l in text.split('\n') if INCLUDE.match(l)), path, _roots(path), set(), parts, set())
            header_views = [nv for _, nv in parts[:-1]]
        if any(_friend_declared(v, name) for v in header_views): continue
        d = re.search(r'\b(class|struct|union)\s+%s%s\s*(?:final\s*)?(?::[^;{}]*)?\{' % (DECOR, n), masked)
        adds.setdefault(at, []).append('%s %s;' % (d.group(1) if d else key, name))
        done.add((at, name))
    if not adds: return text
    out, pos = [], 0
    for at in sorted(adds):
        out.append(text[pos:at]); out.append(' %s /* fnv-nx FRIEND1 */ ' % ' '.join(adds[at])); pos = at
        stats['FRIEND1'] += len(adds[at])
    out.append(text[pos:])
    return ''.join(out)

RULES = [r_unit_renames, r_include_case, r_stand_in_definitions, r_stand_in_interfaces, r_static_initializers, r_msvc_tokens, r_std_struct, r_msvc_library_internals, r_anonymous_return_types, r_opaque_enums, r_enum_int_values, r_function_named_classes, r_anonymous_aggregate_members, r_local_anonymous_unions, r_pointer_zero_compares, r_global_allocators, r_critical_section_tail, r_xbox_cs_asserts, r_keep_definitions, r_if_exists, r_sal_attributes, r_sdk_types, r_exception_specs, r_placement_new, r_ctor_calls, r_c_data_linkage, r_goto_scalar_inits, r_goto_crossed_scopes, r_quad_returns, r_msc_version_values, r_sdk_guarded_copies, r_string_array_address, r_interlocked_fallbacks, r_win32_call_args, r_ref_cast_temporaries, r_temporaries_to_references, r_global_delete_noexcept, r_typeid_include, r_specialization_decls, r_specialization_prefix, r_namespace_specializations, r_va_list, r_va_enum_args, r_vptr_literals, r_image_statics, r_duplicate_int_overloads, r_placement_include, r_namespace_template_decls, r_dependent_base_members, r_math_overloads, r_incomplete_pointee_dtors, r_falloff_traps,
         r_msvc_base_layout, r_class_alignment, r_friend_class_visibility]
