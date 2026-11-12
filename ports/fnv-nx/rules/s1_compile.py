"""s1_compile.py - prepare rules of stream S1-compile (make every unit parse for AArch64 ILP32 with GCC).

Each rule is fn(path, text, stats) -> text (see tools/rules/README.md); ids are cited in the journal.
"""
import bisect
import collections
import os
import re
import sys

import core

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
    for o, c, k, h in core.scope_spans(text):
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
        inc = m[2]
        if m[1] == '<' or os.path.basename(inc) in WIN32_HEADERS:
            sys_headers.add(os.path.basename(inc))
            if m[1] == '<': continue
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
        end = core.match_brace(text, m.end() - 1)
        bases = [re.split(r'[\s:]+', b.strip())[-1] for b in (m[1] or ':')[1:].split(',') if b.strip()]
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
        if core.PLATFORM is None: core.PLATFORM = core.platform_names()
        if core.PLATFORM.get(n) == '"fnvnx_win32.h"' and sys_headers & WIN32_HEADERS: return True
        return re.search(r'(?<![\w.>:~])%s\b' % re.escape(n), name_view) is not None
    return _has_member(class_view, parts[-2], parts[-1])

def r_if_exists(path, text, stats):
    """IFX1: resolve __if_exists / __if_not_exists statically (see above); the kept body replaces the construct,
    line breaks are preserved."""
    if '__if_' not in text: return text
    root, pos, sse = _roots(path), 0, False
    while True:
        m = IFX_HEAD.search(text, pos)
        if not m: break
        end = core.match_brace(text, m.end() - 1)
        parts, sys_headers = [], set()
        _visible(COMMENT.sub(' ', text[:m.start()]), path, root, {os.path.abspath(path)}, parts, sys_headers)
        class_view = '\n'.join(p[0] for p in parts)
        name_view = '\n'.join(p[1] for p in parts)
        keep = _exists(m[2], class_view, name_view, sys_headers) != bool(m[1])
        # IFX1-SSE: a declaration dropped because the target's sse2neon has the name: say where it comes from
        sse = sse or (bool(m[1]) and not keep and re.match(r'\s*(?:__m128[id]?|__m64|_mm_\w+|_MM_\w+)\s*$',
                                                                  m[2]) is not None)
        head, body = text[m.start():m.end()], text[m.end():end - 1]
        nl = lambda s: '\n' * s.count('\n')
        if keep:
            repl = '/* fnv-nx IFX1 */' + nl(head) + body + ' '
        else:
            repl = '/* fnv-nx IFX1 */' + nl(head) + nl(body)
        text = text[:m.start()] + repl + text[end:]
        pos = m.start() + 1
        stats['IFX1'] += 1
    if sse:
        stats['IFX1-SSE'] += 1
        text = '#include "fnvnx_target_sse.h" /* fnv-nx IFX1 */\n' + text
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
        text, n = SAL_CLASS_ATTR.subn(lambda m: '\n' * m[0].count('\n'), text)
        stats['SAL1'] += n
    return text

# ---------------------------------------------------------------------------------------------------------- DECL2

SDK_TAGS = ('tagRECT tagPOINT tagSIZE _FILETIME _SYSTEMTIME _GUID _RTL_CRITICAL_SECTION _LARGE_INTEGER _ULARGE_INTEGER '
            '_OVERLAPPED _WIN32_FIND_DATAA _SYSTEM_INFO tagMSG tagPALETTEENTRY _LUID _RGNDATA tagBITMAPFILEHEADER '
            'tagBITMAPINFOHEADER _MEMORYSTATUS CRITICAL_SECTION _RTL_CRITICAL_SECTION_DEBUG').split()
# CRITICAL_SECTION: a struct copy of the PC type. _RTL_CRITICAL_SECTION_DEBUG: the decomp's SDK copy of
# unit merges winnt.h's two records into `typedef struct _RTL_CRITICAL_SECTION_DEBUG {...}
# RTL_CRITICAL_SECTION, *PRTL_CRITICAL_SECTION;` (so its CRITICAL_SECTION was the 32-byte debug record under MSVC);
# dropped, the unit's CRITICAL_SECTION is the platform's 24-byte PC section (D15)
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
        end = core.match_brace(text, m.end() - 1)
        tail = re.match(r'([^;{}]*);[ \t]*', text[end:])
        if not tail or not SDK_DECLARATORS.match(tail[1]): continue
        found.append((m.start(), end + tail.end(), '"fnvnx_win32.h"'))
    for m in SDK_TYPEDEF.finditer(text):
        found.append((m.start(), m.end(), SDK_TYPEDEFS[m[1]]))
    for m in SDK_FNTYPEDEF.finditer(text):
        found.append((m.start(), m.end(), '"fnvnx_win32.h"'))
    if not found: return text
    spans, out, pos, have, top = core.scope_spans(text), [], 0, set(), []
    for s, e, hdr in sorted(found):
        if s < pos: continue
        chain = core.scope_chain(spans, s)
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

# ---------------------------------------------------------------------------------------------------------- COM1
#
# A unit that defines its own `struct IUnknown {...}` (bsshader.cpp, for its D3DX buffer / constant-table
# stand-ins) declares the COM base with its own parameter types (`int QueryInterface(void*, void**)`, not pure), and
# its interfaces redeclare those methods with the same types. DECL2 cannot give way to the platform's IUnknown
# (fnvnx_win32.h, `HRESULT QueryInterface(REFIID, void**) = 0`): the redeclarations would no longer override it,
# QueryInterface would get a new slot behind Ent, and every slot of ID3DXConstantTable would move by one.
# So the unit's copy keeps its slots under its own name: every IUnknown of that file is renamed (types only, the
# unit has no function with IUnknown in its signature, so no decoration the recipe binds changes).

COM_OWN = re.compile(r'^[ \t]*(?:typedef\s+)?(?:struct|class)\s+IUnknown\s*\{', re.M)
COM_NAME = 'fnvnx_unit_IUnknown'

def r_own_iunknown(path, text, stats):
    """COM1: rename a unit's own copy of the COM base IUnknown (defined in that file) and its uses there."""
    if 'IUnknown' not in text: return text
    m = COM_OWN.search(text)
    if not m or core.scope_chain(core.scope_spans(text), m.start()): return text
    out, pos = [], 0
    for u in re.finditer(r'(?<![\w.>:~])IUnknown\b', text):
        line = text[text.rfind('\n', 0, u.start()) + 1:u.start()]
        if '//' in line or line.lstrip().startswith('#'): continue
        out.append(text[pos:u.start()]); out.append(COM_NAME); pos = u.end()
        stats['COM1'] += 1
    out.append(text[pos:])
    return ''.join(out)

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
    engine's global operator new[] -> Ent, which the platform's global operator new provides). For a
    placement form <new> (core.new_header) takes the place of the first one at file scope, or goes on top."""
    if 'operator' not in text: return text
    spans, out, pos, have, top, hdr = None, [], 0, False, False, core.new_header(text)
    for m in NEWDEL_DEF.finditer(text):
        if m.start() < pos: continue
        if spans is None: spans = core.scope_spans(text)
        chain = core.scope_chain(spans, m.start())
        if any(k not in ('namespace', 'extern') for k, _ in chain): continue
        in_ns = any(k == 'namespace' for k, _ in chain)
        placement = core.PLACEMENT_PARAMS.match(m[1])
        if not in_ns and not placement: continue
        end = core.match_brace(text, m.end() - 1)
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
        if re.sub(r'<.*', '', m[3]) != m[4]: continue          # A::A / A<T>::A
        if '//' in text[text.rfind('\n', 0, m.start()) + 1:m.start()]: continue   # in a comment
        s = core.operand_start(text, m.start())
        if s >= m.start() or s < pos: continue
        expr = text[s:m.start()].strip()
        cls = m[2] + m[3]
        ptr = '(%s*)(%s)' % (cls, expr) if m[1] == '->' else '(%s*)&(%s)' % (cls, expr)
        # the call is an lvalue of the constructed object where the decomp uses its value (*(T**)&p->A::A())
        out.append(text[pos:s]); out.append('(*::new ((void*)%s) %s(' % (ptr, cls))
        close = _call_end(text, m.end() - 1)
        out.append(text[m.end():close]); out.append(')'); pos = close; n += 1
    out.append(text[pos:])
    if not n: return text
    stats['CTOR1'] += n
    return '#include %s /* fnv-nx CTOR1 */\n' % core.new_header(text) + ''.join(out)

# ---------------------------------------------------------------------------------------------------------- TAIL1
#
# Itanium C++ (GCC) places a derived class's first members in the tail padding of a base that is not POD for the
# purpose of layout (any class with virtual functions, constructors, private members, ...); MSVC never does and
# always starts them at sizeof(base). Example: Ent ends with a bool at 0x2C (sizeof 0x30); MSVC puts
# Ent::unit at 0x30, GCC at 0x2D. GCC has no switch to turn the reuse off. A zero-width unnamed bit-field
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

TAIL1_PACK = re.compile(r'^[ \t]*#[ \t]*pragma[ \t]+pack[ \t]*\(([^)]*)\)', re.M)

def _tail1_packs(text):
    """[(position, active #pragma pack value or None)] in order: the file's pack stack after each directive
    (push / pop, with or without an identifier, and a bare value; `pack()` resets to the default)"""
    out, stack, cur = [], [], None
    for m in TAIL1_PACK.finditer(text):
        args = [a.strip() for a in m[1].split(',') if a.strip()]
        num = next((int(a, 0) for a in args if re.fullmatch(r'\d+|0x[0-9a-fA-F]+', a)), None)
        if args and args[0] == 'push':
            stack.append(cur)
            if num is not None: cur = num
        elif args and args[0] == 'pop':
            cur = stack.pop() if stack else None
        elif num is not None:
            cur = num
        elif not args:
            cur = None
        out.append((m.end(), cur))
    return out

def r_msvc_base_layout(path, text, stats):
    """TAIL1: derived class members start at sizeof(last base), like MSVC (see above). Classes with a virtual base
    are left alone. Under an active `#pragma pack(N)` of the file (N below 8) the filler is
    FNVNX_MSVC_BASE_END_PACK(N, base): MSVC lays a base subobject out at min(alignof(base), N), while GCC's zero-width
    bit-field of alignof(base) raised the class's alignment past the pack (the LAYOUT gate's pack rows:
    unit.h's pack(1) classes were align 4 natively, 1 under MSVC)."""
    if ':' not in text: return text
    out, pos = [], 0
    packs = _tail1_packs(text) if 'pack' in text else []
    for o, c, k, h in core.scope_spans(text):
        if k != 'class': continue
        bare = core.HEAD_DECOR.sub(' ', core.TEMPLATE_PREFIX.sub('', h)).strip()
        m = BASE_CLAUSE.match(bare)
        if not m: continue
        bases = _split_bases(m[1])
        if not bases or any(re.search(r'\bvirtual\b', b) for b in bases): continue
        last = re.sub(r'^(?:(?:public|protected|private)\s+)+', '', bases[-1]).strip()
        if not last: continue
        # a qualified base (`List<VERTEX, BASE>::Item`) may be a dependent name: inside alignof it needs typename
        # (C++11 allows it before any qualified name, in a template or not); a `::` inside the template arguments
        # does not make the name qualified
        if _qualified_top(last) and not last.startswith('typename'): last = 'typename ' + last
        pk = None
        for at, v in packs:
            if at > o: break
            pk = v
        if pk is not None and pk < 8:
            out.append(text[pos:o + 1]); out.append(' FNVNX_MSVC_BASE_END_PACK(%d, %s)' % (pk, last)); pos = o + 1
            stats['TAIL1-PACK'] += 1
        else:
            out.append(text[pos:o + 1]); out.append(' FNVNX_MSVC_BASE_END(%s)' % last); pos = o + 1
        stats['TAIL1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# -------------------------------------------------------------------------------------------------------- VFPAD1
#
# MSVC pads a class's own vfptr to the class's alignment: in a class with no base, a virtual function and a member
# aligned to 8 (double, __int64) or 16 (__m128, unit, ...), the first member sits at 8 or 16, while GCC puts it
# right after the 4-byte vfptr (the LAYOUT gate's vfpad rows: unit.cpp FUN_ADDR_cls, whose members are
# named by their exe offsets m10 / m14 / ... / m40, had m10 at 4 natively and at 16 under MSVC). VFPAD1 puts
# FNVNX_VFPAD(<the member types>) first in such a body (FNVNX_VFPAD_PACK(N, ...) under an active `#pragma pack(N)`,
# nothing for N <= 4): a zero-width bit-field aligned like the most aligned member type (fnvnx_compat.h). Pointers,
# references, function pointers and the 4-byte-or-smaller builtins are left out of the list; a class whose members
# use a type nested in it (declared after the filler), an anonymous union / struct or a nested class member is held
# (VFPAD-HELD).
VFPAD1_SMALL = re.compile(r'^(?:(?:const|volatile|mutable|signed|unsigned|short|int|long|char|bool|float|wchar_t|'
                          r'__int8|__int16|__int32|BYTE|WORD|DWORD|UINT|INT|BOOL|LONG|ULONG|USHORT|UCHAR|FLOAT)\b\s*)+$')

def _vfpad_types(flat, own):
    """(member types for FNVNX_VFPAD, held) of a flattened class body: None when the class has no data member"""
    _, nested = _members(flat, own)
    types, held, any_data = [], False, False
    # nested function-pointer typedefs (`typedef unsigned (*READFN)(...);`, which _members does not see): 4 bytes
    fptrs = set(re.findall(r'\btypedef\b[^;]*?\(\s*(?:__\w+\s*)?\*\s*([A-Za-z_]\w*)\s*\)', flat))
    masked = flat
    for _ in range(4):
        masked = re.sub(r'\((?:[^()]|\([^()]*\))*\)', '()', masked)        # FNVNX_D22(C, virtual ... ;) and calls
    for stmt in re.split(r'[;}]', masked):
        st = re.sub(r'^\s*(?:(?:public|private|protected)\s*:\s*|[A-Z][A-Z0-9_]*\s*\(\)\s*)+', '',
                    stmt.replace('{', ' { ')).strip()                         # a `;`-less macro (FNVNX_D22) in front
        if not st or st.startswith('#'): continue
        if re.match(r'(?:struct|class|union)\b[^{]*\{\s*$', st) or re.match(r'(?:union|struct)\s*\{', st):
            if re.match(r'(?:union|struct)\s*\{', st): held = True                 # anonymous aggregate
            continue
        if re.match(r'(?:struct|class|union)\b[^{]*\{', st):
            held = True; continue                                                 # `struct X {...} m;`
        if re.fullmatch(r'[A-Za-z_]\w*\s*(?:\[[^\]]*\]\s*)*(?:,\s*[A-Za-z_]\w*\s*(?:\[[^\]]*\]\s*)*)*', st):
            held = True; continue                                                 # the declarators after `struct {...}`
        if '(' in st or '{' in st: continue
        if re.match(r'(?:typedef|using|friend|template|enum|static|static_assert|virtual|operator|explicit|inline|'
                    r'public|private|protected)\b', st): continue
        d0 = _split_args(st)[0] if ',' in st else st
        m = re.match(r'^(.*?[\w>\s*&])\s*\b([A-Za-z_]\w*)\s*((?:\[[^\]]*\]\s*)*)(?::\s*\w+\s*)?(?:=.*)?$', d0, re.S)
        if not m or not m[1].strip(): continue
        t = m[1].strip()
        any_data = True
        if t.endswith(('*', '&')) or VFPAD1_SMALL.match(t) or t in fptrs: continue
        if set(re.findall(r'[A-Za-z_]\w*', t)) & (nested | {own}):
            held = True; continue
        t = re.sub(r'^(?:(?:mutable|const|volatile)\s+)+', '', t)
        if not re.fullmatch(r'[\w\s:<>,*&]+', t) or t.count('<') != t.count('>'):
            held = True; continue
        t = re.sub(r'\s+', ' ', t)
        if t not in types: types.append(t)
    return (types if any_data else None), held

def r_vfptr_pad(path, text, stats):
    """VFPAD1: MSVC's vfptr padding for a class with no base, a virtual function and a member aligned to 8 / 16 (see
    above). Same line count."""
    if 'virtual' not in text: return text
    out, pos = [], 0
    packs = _tail1_packs(text) if 'pack' in text else []
    for o, c, k, h in core.scope_spans(text):
        if k != 'class': continue
        bare = core.HEAD_DECOR.sub(' ', core.TEMPLATE_PREFIX.sub('', h)).strip()
        if not re.match(r'(?:class|struct)\b', bare) or BASE_CLAUSE.match(bare): continue
        nm = re.match(r'(?:class|struct)\s+(?:__declspec\s*\((?:[^()]|\([^()]*\))*\)\s*)*([A-Za-z_]\w*)', bare)
        if not nm: continue
        own = nm[1]
        flat = _flat_body(text, o, c + 1)
        if not re.search(r'\bvirtual\b', flat): continue
        types, held = _vfpad_types(flat, own)
        if not types: continue
        if held:
            stats['VFPAD-HELD'] += 1; continue
        pk = None
        for at, v in packs:
            if at > o: break
            pk = v
        if pk is not None and pk <= 4: continue
        out.append(text[pos:o + 1])
        out.append(' FNVNX_VFPAD_PACK(%d, %s)' % (pk, ', '.join(types)) if pk is not None and pk < 16
                   else ' FNVNX_VFPAD(%s)' % ', '.join(types))
        pos = o + 1
        stats['VFPAD1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- EBO1
#
# MSVC gives the empty base class optimization only to the first of several empty bases: in `struct D : A, B
# { char m[19]; }` with A and B empty, D's members start at offset 1 and sizeof(D) is 20; GCC puts both bases at 0
# (sizeof 19). The game's code allocates and constructs MSVC's size: 0xADDR `new FUN_ADDR_Class` asks
# operator new for 0x14 and its constructor 0xADDR writes up to +0x14; 0xADDR `new FUN_ADDR` (six ints)
# asks for 0x1c (the ints at 4). EBO1 puts `char fnvnx_msvc_ebo;` first in the body of a class with exactly two
# direct non-virtual bases that are both empty in the unit (no data members, nothing virtual, no bases) and that
# has data members of its own: its members then start where MSVC's do. Other empty-base patterns are not rewritten
# (S12's static checks report them; benign unless a size or offset reaches a byte image or an allocation).

def _ebo_empty_class(text, o, c):
    """True when the class body text[o..c] declares no data member and nothing virtual."""
    flat = re.sub(r'\bFNVNX_MSVC_BASE_END(?:_PACK)?\s*\((?:[^()]|\([^()]*\))*\)', ' ', _flat_body(text, o, c))   # TAIL1's (no `;`)
    if re.search(r'\bvirtual\b', flat): return False
    flat = re.sub(r'\((?:[^()]|\([^()]*\))*\)', '()', flat)
    for st in re.split(r'[;{}]', flat):
        st = re.sub(r'^\s*(?:(?:public|private|protected)\s*:\s*)+', '', st).strip()
        if not st or '(' in st or re.match(r'(?:typedef|using|friend|template|enum|static|struct|class|union)\b', st):
            continue
        return False
    return True

EBO_CLASS = re.compile(r'(?<![<\w])(?:struct|class)\s+([A-Za-z_]\w*)\s*(?::\s*([^{};]*))?\{')

def r_msvc_empty_bases(path, text, stats):
    """EBO1: `char fnvnx_msvc_ebo;` first in a class whose two bases are both empty, as MSVC lays it out (above)."""
    if ':' not in text or ',' not in text: return text
    cands = [m for m in EBO_CLASS.finditer(text) if m[2] and ',' in m[2]]
    if not cands: return text
    spans = core.scope_spans(text)
    heads = lambda pos: tuple(h for _, h in core.scope_chain(spans, pos))
    defs = {}
    for m in EBO_CLASS.finditer(text):
        defs.setdefault(m[1], []).append((m.end() - 1, m[2], heads(m.start())))
    def visible(name, scope):
        """the definitions of `name` in the innermost enclosing scope that has one"""
        ds = [d for d in defs.get(name, ()) if d[2] == scope[:len(d[2])]]
        best = max((len(d[2]) for d in ds), default=-1)
        return [d for d in ds if len(d[2]) == best]
    edits = []
    for m in cands:
        bases = [re.sub(r'^(?:(?:public|protected|private)\s+)+', '', b).strip() for b in _split_bases(m[2])]
        if len(bases) != 2 or any(re.search(r'\bvirtual\b|[<:]', b) for b in bases): continue
        ok, scope = True, heads(m.start())
        for b in bases:
            d = visible(b, scope)
            if len(d) != 1 or d[0][1]: ok = False; break               # one definition, no bases of its own
            if not _ebo_empty_class(text, d[0][0], core.match_brace(text, d[0][0])): ok = False; break
        o = m.end() - 1
        if not ok or _ebo_empty_class(text, o, core.match_brace(text, o)): continue
        edits.append(o + 1)
    if not edits: return text
    for at in reversed(edits):
        text = text[:at] + ' char fnvnx_msvc_ebo; /* fnv-nx EBO1 */' + text[at:]
    stats['EBO1'] += len(edits)
    return text

# ---------------------------------------------------------------------------------------------------------- LINK1

C_DATA_DECL = re.compile(r'^([ \t]*extern)\s+"C"(\s+[\w \t\*&:<>,]*?[\s\*&]\w+(?:\s*\[[^\]]*\])*'
                         r'(?:\s*,\s*[\*&]*\s*\w+(?:\s*\[[^\]]*\])*)*\s*;)', re.M)   # `extern "C" char a[], b[];` too
C_FNPTR_DECL = re.compile(r'^([ \t]*extern)\s+"C"(\s*[\w \t\*&:<>,]*?\(\s*(?:__\w+\s*)?\*\s*\w+\s*\)\s*\([^;{}]*\)\s*;)',
                          re.M)

def r_c_data_linkage(path, text, stats):
    """LINK1: `extern "C" T x;` (also a function-pointer variable `T (*x)(..)`) at file scope -> `extern T x;`. The decomp
    declares the same image variable with both linkages; MSVC kept them apart, GCC rejects the pair. For a variable at global scope both linkages
    name the same symbol under the Itanium ABI (no mangling), so nothing changes for the link."""
    if 'extern "C"' not in text: return text
    spans, out, pos = None, [], 0
    for m in sorted(list(C_DATA_DECL.finditer(text)) + list(C_FNPTR_DECL.finditer(text)), key=lambda x: x.start()):
        if m.start() < pos: continue
        if '(' in m[2] and not C_FNPTR_DECL.match(m[0]): continue
        if spans is None: spans = core.scope_spans(text)
        if core.scope_chain(spans, m.start()): continue
        out.append(text[pos:m.start()]); out.append(m[1] + m[2]); pos = m.end()
        stats['LINK1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- GOTO1

SCALAR_BASE = r'(?:(?:unsigned|signed)\s+)?(?:bool|char|short|int|long(?:\s+long)?|float|double|unsigned)'
SCALAR_TYPEDEF = re.compile(r'\btypedef\s+(%s)\s+([A-Za-z_]\w*)\s*;' % SCALAR_BASE)
VECTOR_TYPEDEF = re.compile(r'\btypedef\s+(?:const\s+)?(?:::\s*)?__m128[id]?\s+([A-Za-z_]\w*)\s*;')
ANY_TYPEDEF = re.compile(r'\btypedef\b[^;{}]*?([A-Za-z_]\w*)\s*;')

def _typedef_aliases(text):
    """({name: scalar type}, {vector names}) of the typedefs the text itself declares (`typedef float unit;`,
    `typedef __m128 unit;`): the underlying type is the declaration's own. A name that the text also
    typedefs to anything else is left out (its meaning depends on the scope)."""
    code = COMMENT.sub(' ', text)
    sc = {m[2]: m[1] for m in SCALAR_TYPEDEF.finditer(code)}
    vec = {m[1] for m in VECTOR_TYPEDEF.finditer(code)}
    for m in ANY_TYPEDEF.finditer(code):
        n = m[1]
        if n in sc and SCALAR_TYPEDEF.fullmatch(m[0]) is None or n in vec and VECTOR_TYPEDEF.fullmatch(m[0]) is None:
            sc.pop(n, None); vec.discard(n)
    return sc, vec

_SCALAR_INIT = {}

def _scalar_init(aliases):
    """SCALAR_INIT for a text whose scalar / vector typedef names are `aliases`."""
    key = tuple(sorted(aliases))
    if key not in _SCALAR_INIT:
        extra = ''.join(r'|' + re.escape(a) for a in key)
        _SCALAR_INIT[key] = re.compile(r'^([ \t]*)((?:const\s+)?(?:(?:unsigned|signed|volatile)\s+)*(?:bool|char|short|int|'
                                       r'long(?:\s+long)?|float|double|unsigned|__m128[id]?|__m64%s|[\w:]+(?:\s+const)?\s*\*+))\s+'
                                       r'(\**\s*\w+)\s*=\s*([^;{}=][^;{}]*);' % extra, re.M)
    return _SCALAR_INIT[key]

INT_LITERAL_EXPR = re.compile(r'^[\s()+\-~!*/%<>&|^]*(?:(?:0[xX][0-9a-fA-F]+|\d+)[uUlL]*[\s()+\-~!*/%<>&|^]*)+$')

def _goto_crosses(text, o, c, pos):
    """A forward goto in the function body text[o:c] jumps past the declaration at pos into its scope: the goto is
    before pos, its label after pos and inside the innermost block that holds pos."""
    depth, start = 0, pos
    while start > o:                                   # the innermost '{' that holds pos
        start -= 1
        ch = text[start]
        if ch == '}': depth += 1
        elif ch == '{':
            if depth == 0: break
            depth -= 1
    end, depth = pos, 0
    while end < c:                                     # its matching '}'
        ch = text[end]
        if ch == '{': depth += 1
        elif ch == '}':
            if depth == 0: break
            depth -= 1
        end += 1
    labels = {m[1] for m in re.finditer(r'(?<![:\w])(\w+)\s*:(?!:)', text[pos:end])
              if m[1] not in ('default', 'public', 'private', 'protected')}
    return any(m[1] in labels for m in re.finditer(r'\bgoto\s+(\w+)\s*;', text[o:pos]))

def r_goto_scalar_inits(path, text, stats):
    """GOTO1: in functions that use goto, `T x = e;` (scalar, SSE vector or pointer T) -> `T x; x = e;` on the same
    line. MSVC lets a goto jump past such an initialization; C++ (and GCC even with -fpermissive) does not, but
    jumping past a declaration without initializer is allowed, and for a scalar the split changes nothing. A const
    scalar loses the const it cannot keep without its initializer (`const int n = e;` -> `int n; n = e;`; a pointer
    to const keeps it). A declarator list is split declarator by declarator, `T a = e1, b = e2;` ->
    `T a, b; a = e1; b = e2;` (the initializers keep their order). A const integer scalar whose initializer is an
    integer literal expression (`const int n = 4;`, perhaps an array bound) stays as written unless a forward goto
    jumps past it into its scope; then it becomes `static const int n = 4;`: still a constant expression, and no
    automatic variable for the goto to cross (GOTO1-CONST)."""
    if 'goto' not in text: return text
    spans = [sp for sp in core.scope_spans(text) if sp[2] == 'function']
    edits = []
    sc, vec = _typedef_aliases(text)
    nonint = vec | {a for a, b in sc.items() if re.search(r'float|double', b)}
    scalar_init = _scalar_init(set(sc) | vec)
    for o, c, k, h in spans:
        body = text[o:c]
        if not re.search(r'\bgoto\s+\w+\s*;', body): continue
        for m in scalar_init.finditer(text, o, c):
            t = m[2]
            if re.match(r'(?:return|else|case|goto|delete|throw|typedef|using)\b', t): continue
            if (t.startswith('const') and '*' not in t and not re.search(r'\b(?:float|double|__m)', t)
                    and not nonint & set(re.findall(r'\w+', t))
                    and ',' not in m[4] and INT_LITERAL_EXPR.match(m[4])):
                if _goto_crosses(text, o, c, m.start()):
                    edits.append((m.start(), m.end(), '%sstatic %s %s = %s;' % (m[1], m[2], m[3],
                                                                               m[4].strip())))
                continue                                    # not crossed: as written (MSVC's code stays the same)
            if t.startswith('const') and '*' not in t: t = t[5:].lstrip()          # the scalar's own const
            # a declarator list `float a = f(x), b = g(y);` -> `float a, b; a = f(x); b = g(y);` (same order)
            parts = [re.match(r'^\s*(\**\s*[A-Za-z_]\w*)\s*(?:=\s*(\S.*?))?\s*$', d, re.S)
                     for d in _split_args(m[3] + ' = ' + m[4])]
            if not all(parts) or len(parts) > 1 and '<' in m[4]: continue   # `f<a, b>(x)` may be split wrong
            decl = ', '.join(p[1] for p in parts)
            inits = ' '.join('%s = %s;' % (p[1].lstrip('* \t'), p[2]) for p in parts if p[2])
            edits.append((m.start(), m.end(), '%s%s %s; %s' % (m[1], t, decl, inits)))
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

def _goto_scalar(aliases):
    """The scalar type words, plus the scalar typedef names `aliases` of the text (_typedef_aliases)."""
    return '(?:%s%s)' % (SCALAR_BASE, ''.join('|' + re.escape(a) for a in sorted(aliases)))

def _goto_cref(scalar):
    return re.compile(r'^const\s+(%s)\s*&\s*([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*(\(.*\))\s*;$' % scalar, re.S)
CXX_WORDS = GOTO_KW | frozenset('int char short long float double bool void unsigned signed auto inline'.split())

def _goto_decls(masked, o, c):
    """Declarations of the function body masked[o:c]: [(start, type end, end after ';', [(name, has init, is ref)],
    is static)]; also for-init declarations (start -1, so they never count as crossed)."""
    starts = [o + 1] + [m.end() for m in GOTO_STARTS.finditer(masked, o + 1, c)]
    out = []
    for s in starts:
        s += len(masked[s:c]) - len(masked[s:c].lstrip())
        m = GOTO_DECL.match(masked, s)
        if not m or m[2] in GOTO_KW or m[1].split()[-1] in GOTO_KW - {'const'}: continue
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
            if not dm or dm[2] in CXX_WORDS: decls = None; break
            decls.append((dm[2], dm[3] in ('=', '(', '{'), '&' in dm[1]))
        if decls:
            for_init = re.search(r'\bfor\s*\(\s*$', masked[o:s])
            out.append((-1 if for_init else s, m.end(1), e + 1, decls,
                        bool(re.match(r'(?:\w+\s+)*static\b', m[1]))))
    return out

def _goto_fix(text, stats, skip):
    """One GOTO2 edit in `text` (the first crossing it can fix), or None."""
    masked = FRIEND_MASK.sub(lambda x: re.sub(r'[^\n]', ' ', x[0]), text)
    spans = core.scope_spans(masked)
    scalar = _goto_scalar(_typedef_aliases(text)[0])
    funcs, last = [], -1
    for sp in spans:
        if sp[2] == 'function' and sp[0] > last:
            funcs.append(sp); last = sp[1]
    for o, c, _, _ in funcs:
        if not GOTO_STMT.search(masked, o, c): continue
        blocks = [sp for sp in spans if o <= sp[0] and sp[1] <= c and sp[2] not in ('init', 'class', 'enum')]
        def block(p): return max((sp for sp in blocks if sp[0] < p < sp[1]), key=lambda sp: sp[0])
        gotos = [(m.start(), m.end(), m[1]) for m in GOTO_STMT.finditer(masked, o, c)]
        targets = {g[2] for g in gotos}
        labels = [(m.start(1), m.end(), m[1]) for m in GOTO_LABEL.finditer(masked, o + 1, c)
                  if m[1] in targets and m[1] not in GOTO_KW]
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
                cm = _goto_cref(scalar).match(text[s:e])
                if cm and len(dl) == 1:
                    rets = re.findall(r'\b(%s)\s*(&?)\s*(?:(?:__cdecl|__stdcall|__fastcall|__thiscall)\s+)?%s\s*\('
                                      % (scalar, re.escape(cm[3])), masked)
                    if rets and all(not r for _, r in rets) and \
                            not re.search(r'[\w>]\s*&\s*(?:\w+::)*%s\s*\(' % re.escape(cm[3]), masked):
                        stats['GOTO2'] += 1
                        return text[:s] + '%s %s; %s = %s%s;' % (cm[1], cm[2], cm[2], cm[3],
                                                               cm[4]) + text[e:]
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
    root, uj = core._units_json(path)
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
    masked = FRIEND_MASK.sub(lambda x: re.sub(r'[^\n]', ' ', x[0]), text)
    out, pos = [], 0
    for m in re.finditer(r'\b_MSC_(FULL_)?VER\b', masked):
        out.append(text[pos:m.start()]); out.append(ver[1] if m[1] else ver[0]); pos = m.end()
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
    masked = FRIEND_MASK.sub(lambda x: re.sub(r'[^\n]', ' ', x[0]), text)
    spans = core.scope_spans(masked)
    classes = {}
    for o, c, k, h in spans:
        hm = QUAD_HEAD.search(h) if k == 'class' else None
        if hm: classes.setdefault(hm[1], []).append(masked[o:c])
    conv = sorted(n for n, bodies in classes.items() if any(QUAD_CONV.search(b) for b in bodies))
    if not conv: return text
    names = set()
    for m in re.finditer(r'\b(?:%s)\s+([A-Za-z_]\w*)\s*;' % '|'.join(conv), masked):
        others = {t for t in re.findall(r'\b([A-Za-z_]\w*)[\s\*&]+%s\s*[;,=)\[]' % m[1], masked)
                  if t not in GOTO_KW}
        if others <= set(conv): names.add(m[1])
    rets = sorted(n for n, bodies in classes.items()
                  if any(re.search(r'\b%s\s*\(\s*(?:const\s+)?__m128\s*&?\s*\w*\s*\)' % n, b) for b in bodies)
                  and not any(re.search(r'\b%s\s*\(\s*(?:const\s+)?(?:%s)\b' % (n, '|'.join(conv)), b) for b in bodies))
    if not names or not rets: return text
    head = re.compile(r'(?:^|\s)(?:%s)\s+(?:\w+::)*\w+\s*\([^()]*\)\s*(?:const\s*)?$' % '|'.join(rets))
    ret = re.compile(r'\breturn\s+((?:this\s*->\s*)?(?:%s))\s*;' % '|'.join(sorted(names)))
    edits = []
    for o, c, k, h in spans:
        if k == 'function' and head.search(h):
            edits += [(m.start(1), m[1]) for m in ret.finditer(masked, o, c)]
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

STR_ADDR = re.compile(r'&\s*([A-Za-z_]\w*)\b(?!\s*\[)')

# a declaration statement (it may go on over lines: `extern "C" char a[], b[],\n    c[];`), up to its first
# initializer or parameter list: the names in it are the ones it declares
STR_DECL_LINE = re.compile(r'^[ \t]*(?:extern\b|static\b|const\b|char\b|unsigned\b|signed\b|wchar_t\b)[^;{}=(]*', re.M)
STR_DECL_NAME = re.compile(r'\b([A-Za-z_]\w*)\b\s*(\[\s*\])?')
STR_TYPE_WORDS = frozenset('extern static const volatile char unsigned signed wchar_t'.split())

def r_string_array_address(path, text, stats):
    """STR1: `&x` where the declaration in scope is `extern char x[];` (a char array of unknown bound) -> `x`: the
    same address; MSVC converted char (*)[] to const char* silently, GCC does not. An array of unknown bound has no
    size, so its address admits no arithmetic and the decayed pointer names the same object. A file may declare the
    same name as an array in one place and as a scalar `char` in another: the declaration in scope at the use
    decides (the latest one before it, in the use's namespace or an enclosing one). Only declarations of plain
    `char` (no pointer) count."""
    if not re.search(r'\b\w+\s*\[\s*\]', text): return text
    spans = core.scope_spans(text)
    heads = lambda pos: tuple(h for k, h in core.scope_chain(spans, pos) if k == 'namespace')
    decls = {}                                             # name -> [(pos, scope, is_array)]
    for line in STR_DECL_LINE.finditer(text):
        seg = line[0]
        if '*' in seg or not re.search(r'\bchar\b', seg): continue
        for d in STR_DECL_NAME.finditer(seg):
            if d[1] in STR_TYPE_WORDS or seg[max(0, d.start() - 1):d.start()] == '"' : continue
            pos = line.start() + d.start()
            decls.setdefault(d[1], []).append((pos, heads(pos), bool(d[2])))
    if not decls: return text
    def sub(m):
        scope = heads(m.start())
        seen = [d for d in decls.get(m[1], ()) if d[0] < m.start() and d[1] == scope[:len(d[1])]]
        if not seen: return m[0]
        best = max(seen, key=lambda d: (len(d[1]), d[0]))  # innermost namespace, then the latest
        if not best[2]: return m[0]
        stats['STR1'] += 1
        return m[1]
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
        if spans is None: spans = core.scope_spans(text)
        if core.scope_chain(spans, m.start()): continue
        end = core.match_brace(text, m.end() - 1)
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
# (Ent*, Ent*, int for a HANDLE, a local copy of _RTL_CRITICAL_SECTION...). Under MSVC each call passed
# that value to the same import; with the SDK prototypes GCC refuses the conversions. CALL1 casts every argument
# passed to a pointer parameter of a Win32 function to the SDK parameter type, which is what the decomp's own
# prototype did implicitly (same bits; ILP32 pointers and ints are both 32-bit). Where the dropped prototype took a
# reference (`CoCreateInstance(const Ent& clsid, ...)`, as the C++ SDK does) the address of the argument is
# passed (core.DECL1_REFS): what the reference passed.

_W32_SIGS = None

def _w32_signatures():
    """name -> list of parameter types (None for a non-pointer parameter), from shim/include/fnvnx_win32.h."""
    global _W32_SIGS
    if _W32_SIGS is not None: return _W32_SIGS
    src = COMMENT.sub(' ', core.CTX['win32_header'])
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
        name, params = m[1], m[2].strip()
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
    # (a call statement `return CallNextHookEx(...);` is not a prototype: CALL1-RET)
    names = [n for n in names if not re.search(r'[\w\*&>](?<!\breturn)(?<!\belse)(?<!\bcase)(?<!\bthrow)(?<!\bdo)'
                                               r'\s+%s\s*\([^;{}]*\)\s*;' % n, text)]
    if not names: return text
    # N::name( where namespace N only has DECL1's `using ::name;` is the platform function too (CALL1-NS:
    # blk_ADDR's Ent::RegOpenKeyExA)
    aliased = {}
    for am in re.finditer(r'\bnamespace\s+(\w+)\s*\{', text):
        body = text[am.end():core.match_brace(text, am.end() - 1)]
        for n in re.findall(r'\busing\s+::\s*(\w+)\s*;', body):
            aliased.setdefault(n, set()).add(am[1])
    call = re.compile(r'(?<![\w.>:~])((?:\w+\s*::\s*)?)(%s)\s*\(' % '|'.join(sorted(names, key=len, reverse=True)))
    bodies = []                                      # outermost function bodies, in order
    for o, c, k, _ in core.scope_spans(text):
        if k == 'function' and not (bodies and o < bodies[-1][1]): bodies.append((o, c))
    if not bodies: return text
    out, pos, bi = [], 0, 0
    for m in call.finditer(text):
        if m.start() < pos: continue
        q = re.sub(r'\s*::\s*$', '', m[1])
        if q and q not in aliased.get(m[2], ()): continue
        while bi < len(bodies) and bodies[bi][1] < m.start(): bi += 1
        if bi == len(bodies) or not bodies[bi][0] < m.start(): continue
        before = text[max(0, m.start() - 40):m.start()].rstrip()
        prev = re.search(r'(\w+)$', before)
        if prev and prev[1] not in ('return', 'case', 'else', 'do'): continue   # a declaration
        close = _call_end(text, m.end() - 1)
        args = _split_args(text[m.end():close - 1])
        types = sigs[m[2]]
        if len(args) != len(types): continue
        refs = core.DECL1_REFS.get(path, {}).get(m[2], ())
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

# comments, and the string / character literals that may hold `//` or `/*` (only the comments are used)
_CODE_LEXEME = re.compile(r'"(?:\\.|[^"\\\n])*"|' + r"'(?:\\.|[^'\\\n])*'" + r'|//[^\n]*|/\*.*?\*/', re.S)

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
    # (a cast spelled in a comment is not code: `// f(const T&)` above a declaration `T();` is no cast of it)
    comments = [c.span() for c in _CODE_LEXEME.finditer(text) if c[0].startswith('/')]
    starts = [a for a, _ in comments]
    def in_comment(i):
        k = bisect.bisect_right(starts, i) - 1
        return k >= 0 and i < comments[k][1]
    for m in REF_CAST_CALL.finditer(text):
        if in_comment(m.start()): continue
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
        if in_comment(m.start()): continue
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

# ---------------------------------------------------------------------------------------------------------- NEW3

GLOBAL_DELETE = re.compile(r'^([ \t]*(?:static\s+)?(?:inline\s+|__forceinline\s+)?void\s+(?:__cdecl\s+)?operator\s+delete\s*'
                           r'(?:\[\s*\])?\s*\([^;{}()]*\))(\s*)(?=[{;])', re.M)

def r_global_delete_noexcept(path, text, stats):
    """NEW3: the decomp's file-scope replacements of the global operator delete get `noexcept`, the specification
    every deallocation function has implicitly (libstdc++'s <new> spells it, GCC then rejects the unspelled one)."""
    if 'operator delete' not in text: return text
    spans, out, pos = None, [], 0
    for m in GLOBAL_DELETE.finditer(text):
        if spans is None: spans = core.scope_spans(text)
        if core.scope_chain(spans, m.start()): continue
        out.append(text[pos:m.end(1)]); out.append(' noexcept'); pos = m.end(1)
        stats['NEW3'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- TYPEID1

def r_typeid_include(path, text, stats):
    """TYPEID1: MSVC accepts typeid without <typeinfo>; GCC does not. <typeinfo> on top of files that use typeid
    (not in files that carry their own copy of MSVC's std::type_info / std::exception)."""
    if 'typeid' not in text or not re.search(r'\btypeid\s*\(', text) or core.OWN_STD.search(text): return text
    stats['TYPEID1'] += 1
    return '#include <typeinfo> /* fnv-nx TYPEID1 */\n' + text

# ---------------------------------------------------------------------------------------------------------- VA1
#
# MSVC's va_list is a char*, and the decomp spells it that way: parameters that pass a va_list on are declared
# `char* args` (or void*), and variadic functions build one by hand (`char* args = (char*)(&format + 1);`). On
# AArch64 va_list is a structure, and the same function is declared with char* in one file and va_list in another.
# VA1 makes every such char* hold the address of a real va_list, whatever each declaration says:
#   - a hand-built one becomes `FNVNX_VA_HAND(args, format);` (fnvnx_compat.h: `va_list fnvnx_va_args; va_start(...);
#     char* args = (char*)&fnvnx_va_args`; tools/equiv/msvc: the decomp's `char* args = (char*)(&format + 1)`);
#   - a va_list variable passed to a function is passed as fnvnx_va(x), and so is the argument at the va_list
#     position of a call to a function declared (in this file, or the platform's vprintf family) with a va_list
#     parameter. fnvnx_va (fnvnx_compat.h) gives the address of the va_list to a char* / void* parameter, the
#     va_list itself to a va_list parameter, and hands an ordinary char* back unchanged to a char* parameter.

VA_HANDMADE = re.compile(r'\bchar\s*\*\s*(\w+)\s*=\s*\(\s*char\s*\*\s*\)\s*\(\s*&\s*(\w+)\s*\+\s*1\s*\)\s*;')
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
        for i, p in enumerate(_split_args(m[2])):
            if re.match(r'^\s*(?:const\s+)?va_list\b', p) and len(p.split()) <= 3:
                pos.setdefault(m[1], (i, m.start()))
    return pos

def r_va_list(path, text, stats):
    """VA1: MSVC's char* va_list -> address of a real va_list (see above)."""
    if 'va_list' not in text and not VA_HANDMADE.search(text): return text
    def handmade(m):
        stats['VA1'] += 1
        return 'FNVNX_VA_HAND(%s, %s);' % (m[1], m[2])
    text = VA_HANDMADE.sub(handmade, text)
    va_vars = set(VA_VAR.findall(text))
    targets = _va_positions(text)
    out, pos = [], 0
    for m in re.finditer(r'(?<!\w)([A-Za-z_]\w*)\s*\(', text):
        name = m[1]
        if m.start() < pos or name in NOT_CALLS or name in VA_MACROS: continue
        before = text[max(0, m.start() - 60):m.start()]
        if not re.search(r'(?:\.|->)\s*$', before):                   # a declaration: return type before it
            word = re.search(r'([\w\*&>])\s*(?:::\s*)?(?:\w+\s*::\s*)*$', before)
            kw = re.search(r'(\w+)\s*$', before)
            if word and not (kw and kw[1] in ('return', 'case', 'else', 'do', 'throw', 'new')): continue
        close = _call_end(text, m.end() - 1)
        args = _split_args(text[m.end():close - 1])
        at = len(args) - 1 if name in VA_PLATFORM_LAST else (
            targets[name][0] if name in targets and targets[name][1] < m.start() else None)
        changed = False
        for i, a in enumerate(args):
            ident = re.match(r'^(\s*)([A-Za-z_]\w*)(\s*)$', a)
            if not ident or ident[2] in ('NULL', 'nullptr', '0'): continue
            if ident[2] in va_vars or i == at:
                args[i] = '%sfnvnx_va(%s)%s' % ident.groups()
                changed = True
        if not changed: continue
        out.append(text[pos:m.end()]); out.append(','.join(args)); out.append(')'); pos = close
        stats['VA1'] += 1
    out.append(text[pos:])
    return ''.join(out)

CXX_WORDS = set('''const volatile unsigned signed int char short long float double bool void virtual static inline
    struct class enum typename operator return this true false wchar_t __int64 __cdecl __stdcall __thiscall
    __fastcall FNVNX_KEEP'''.split())

# ---------------------------------------------------------------------------------------------------------- STATIC1
#
# MSVC placed a file-static variable (and a namespace-scope const, internal in C++) in the image like any other, and
# units.json binds its name to that address. GCC keeps such a variable local to the object, and the link step can
# only rebind global symbols, so the unit would use a private copy while everything else sees the image's (S13:
# unit's unit at 0xADDR). STATIC1 gives those variables external linkage: `static` is dropped, a
# namespace-scope `const` definition gets `extern`, and so does an array of unknown size declared without `extern`
# at file scope (`const void* x[];`, which MSVC took as a reference to the image's table; as a definition of unknown
# size it is ill-formed in C++, so `extern` is its only reading, whatever the name). Only names that a unit
# including the file binds to an image data address (`?name@@3...` / `_name` in its units.json symbols) at file
# scope are touched otherwise.
# A namespace-scope static (`namespace fnv_ADDR { static float unit; }`, S13: 77 with an image address,
# three of them uninitialised and written, so the private copy diverges for real: Ent's instance pointer
# 0xADDR, unit's unit / unit 0xADDR / 0xADDR) is bound under its plain name
# (`_s_fSpinTime`), the way the link binds a C name: STATIC1 gives it C linkage where it stands,
# `extern "C" { float unit; }` (a const one `extern "C" { extern const ... }`), so the definition is the
# image's variable under that name. Only a name the file declares once.

STATIC_DEF = re.compile(r'^([ \t]*)static\s+(?!inline\b)((?:const\s+|volatile\s+)*[A-Za-z_][\w:<>,\s\*&]*?[\s\*&](\w+)'
                        r'\s*(?:\[[^\]]*\]\s*)*(?:=|;|\(|\{))', re.M)
UNSIZED_DECL = re.compile(r'^([ \t]*)(?:static\s+)?((?!(?:extern|typedef|friend|return)\b)(?:const\s+|volatile\s+)*'
                          r'[A-Za-z_][\w:<>,\s\*&]*?[\s\*&](\w+)\s*\[\s*\]\s*;)', re.M)
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
    root, uj = core._units_json(path)
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
            if q: addrs.setdefault(('g', q[1]), set()).add(int(a, 16))
            m = DATA_NAME.match(n)
            if m: names.add(m[1])
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
        c = t[0]
        if c in '({[': depth += 1
        elif c in ')}]': depth -= 1
        elif c == ';' and depth == 0: return i + t.start()
        if depth < 0: return None
    return None

SCALAR_WORDS = set('volatile unsigned signed short long int char bool float double __int64 wchar_t BOOL BYTE WORD DWORD'.split())

def _declared_once(text, name, spans, addrs, image=False):
    """True when the namespace static `name` is the file's only declaration of that name that its C name could
    meet: class members do not count, nor an `extern` declaration where every decorated binding of the name
    (`?name@@3...`, `?name@ns@@3...`) is the C name's one address (the same variable)."""
    bare = COMMENT.sub(lambda m: re.sub(r'[^\n]', ' ', m[0]), text)
    n = 0
    for m in re.finditer(r'(\w+|[>\*&])\s+\**%s\s*(?:\[[^\]]*\]\s*)*[;=,(\[{]' % re.escape(name), bare):
        if m[1] in NOT_A_TYPE: continue
        chain = core.scope_chain(spans, m.start())
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
    if 'static' not in text and 'const' not in text and not re.search(r'\[\s*\]\s*;', text): return text
    key = os.path.normpath(os.path.abspath(path))
    names = _image_bound_names(path).get(key) or set()
    cnames = _IMAGE_CNAMES.get(key) or set()
    if not names and not UNSIZED_DECL.search(text): return text
    spans, edits = None, {}
    for rx, kind in ((UNSIZED_DECL, 'unsized'), (STATIC_DEF, 'static'), (CONST_DEF, 'const')):
        for m in rx.finditer(text):
            if m.start() in edits: continue
            if kind != 'unsized' and m[3] not in names: continue
            if kind == 'const' and re.search(r'\bextern\b', text[text.rfind('\n', 0, m.start()) + 1:m.start()]): continue
            if spans is None: spans = core.scope_spans(text)
            chain = core.scope_chain(spans, m.start())
            if any(k != 'extern' for k, _ in chain):
                # a namespace static bound by its plain name: C linkage where it stands
                if kind == 'unsized' or m[3] not in cnames or any(k not in ('namespace', 'extern') for k, _ in chain):
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
                if not _declared_once(text, m[3], spans, _IMAGE_ADDRS.get(key, {}), image): continue
                edits[m.start()] = (m.start(), end + 1, m[1] + 'extern "C" { ' + ('extern ' if const or image else '')
                                    + decl + ' }')
                continue
            new = m[1] + ('' if kind == 'static' else 'extern ') + m[2]
            edits[m.start()] = (m.start(), m.end(), new)
    if not edits: return text
    out, pos = [], 0
    for a, b, r in sorted(edits.values()):
        if a < pos: continue
        out.append(text[pos:a]); out.append(r); pos = b
        stats['STATIC1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- DUPINT1
#
# The decomp sometimes declares the same function twice in one unit, differing only in an integer parameter's type
# (`unit(void*, int, unsigned int)` and `(void*, int, int)`), and the recipe binds both decorations to
# one address. On MSVC size_t is unsigned int, so `sizeof` arguments matched one of them exactly; on AArch64 ILP32
# size_t is unsigned long and the call is ambiguous. DUPINT1 keeps the first declaration and drops the later ones of
# the same name, arity and scope when they differ only in integer types and every decorated spelling of that name
# in the unit's units.json symbols binds the same address: the calls reach the same function with the same bits.
# (With C linkage the two are a conflict outright: `extern "C" void _delete(void*, unsigned int)` and `(void*, size_t)`.)
# The two may also differ in the return type (`extern "C" int Ent(char*, int, const char*, ...)` and
# `char* Ent(char*, unsigned int, const char*, ...)`, both bound to 0xADDR) when no call of that name in
# the unit uses the returned value: every mention of the name outside its prototypes is a call that is a whole
# expression statement (after `;`, `{`, `}`, `else` or the `)` of an if / while / for), and the parameters that differ
# are 32-bit integers on both sides (int / unsigned int / size_t / long ...), and both return types are void, an
# integer or a pointer (a class returned by value passes a hidden result pointer, which would shift the arguments).
# The callee and the argument bits are the same, and the return register is ignored either way (DUPINT1-RET).

INT_TYPES = re.compile(r'^(?:(?:const|volatile|signed|unsigned|short|long|int|char|__int8|__int16|__int32|__int64|'
                       r'size_t|DWORD|UINT|INT|LONG|ULONG|WORD|BYTE|BOOL)\s*)+$')
# the 32-bit ones (on MSVC x86 and AArch64 ILP32 alike): DUPINT1-RET takes only these, so the value is passed as is
INT32_TYPES = re.compile(r'^(?:const\s+|volatile\s+)*(?:(?:signed|unsigned)(?:\s+(?:int|long(?:\s+int)?))?|int|long(?:\s+int)?|'
                         r'unsigned\s+long\s+int|__int32|size_t|DWORD|UINT|INT|LONG|ULONG|BOOL)(?:\s+const)?$')
_UNIT_SYMBOLS = None

def _unit_symbols(path):
    """{absolute unit source: {decorated name: address}}"""
    global _UNIT_SYMBOLS
    if _UNIT_SYMBOLS is None:
        import json
        _UNIT_SYMBOLS = {}
        root, uj = core._units_json(path)
        if uj:
            for u in json.load(open(uj))['units']:
                _UNIT_SYMBOLS[os.path.normpath(os.path.join(root, u['source']))] = dict(u.get('symbols', {}))
    return _UNIT_SYMBOLS.get(os.path.normpath(os.path.abspath(path)))

def _param_type(p):
    p = re.sub(r'=.*', '', p).strip()
    ids = re.findall(r'[A-Za-z_]\w*', p)
    if len(ids) > 1 and not re.search(r'[\*&]\s*$', p) and ids[-1] not in ('int', 'long', 'short', 'char', 'unsigned'):
        p = p[:p.rfind(ids[-1])]                          # drop the parameter name
    return re.sub(r'\s+', ' ', p).strip()

DUPINT_MASK = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|//[^\n]*|/\*.*?(?:\*/|\Z)', re.S)

def _mask_code(text):
    """text with string / character literals and comments blanked (same length, newlines kept)"""
    return DUPINT_MASK.sub(lambda x: re.sub(r'[^\n]', ' ', x[0]), text)

def _statement_start(masked, at):
    """True when masked[at] begins a statement: after `;`, `{`, `}`, `else`, or the `)` of if / while / for."""
    i = at - 1
    while i >= 0 and masked[i].isspace(): i -= 1
    if i < 0 or masked[i] in ';{}': return True
    if masked[i] == 'e' and re.search(r'(?<![\w.])else$', masked[max(0, i - 5):i + 1]): return True
    if masked[i] != ')': return False
    depth = 0
    for j in range(i, -1, -1):                          # the matching '('
        if masked[j] == ')': depth += 1
        elif masked[j] == '(':
            depth -= 1
            if depth == 0: break
    else: return False
    return bool(re.search(r'(?<![\w.>])(?:if|while|for)\s*$', masked[max(0, j - 12):j]))

def _results_unused(text, name):
    """True when every mention of `name` in text outside its prototypes is a call that is a whole expression
    statement `name(...);` (its value is discarded)."""
    masked = _mask_code(text)
    protos = [(m.start(), m.end()) for m in core.PROTO.finditer(masked) if m[1] == name]
    for m in re.finditer(r'(?<![\w.>:])%s\b' % re.escape(name), masked):
        if any(a <= m.start() < b for a, b in protos): continue
        k = m.end()
        while k < len(masked) and masked[k].isspace(): k += 1
        if k >= len(masked) or masked[k] != '(': return False
        e = _call_end(masked, k)
        if masked[e:].lstrip()[:1] != ';' or not _statement_start(masked, m.start()): return False
    for m in re.finditer(r'(?:\.|->|::)\s*%s\b' % re.escape(name), masked):
        return False                                    # a member or qualified name: not this simple case
    return True

def r_duplicate_int_overloads(path, text, stats):
    """DUPINT1: drop later integer-only-different duplicate prototypes bound to one address (see above). Only a pair
    of signatures is handled, with the same return type or (DUPINT1-RET) with results that are never used; anything
    else is left as it is."""
    syms = _unit_symbols(path)
    if not syms: return text
    groups, spans = {}, None
    for m in core.PROTO.finditer(text):
        name = m[1]
        if name.startswith('operator'): continue
        decl = re.sub(r'\s*//[^\n]*$', '', m[0])   # PROTO takes a trailing // comment along
        at = decl.index('(', decl.index(name) + len(name))
        params = _split_args(decl[at + 1:decl.rindex(')')])
        if spans is None: spans = core.scope_spans(text)
        chain = core.scope_chain(spans, m.start())
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
        if len(sigs) != 2: continue
        a, b = sigs[0][0], sigs[1][0]
        if not all(x == y or (INT_TYPES.match(x) and INT_TYPES.match(y)) for x, y in zip(a, b)): continue
        addrs = {v for k, v in syms.items()                # C++ decorations, or the C name (_name, _name@N)
                 if re.match(r'^\?%s@@|^_%s(?:@\d+)?$' % (re.escape(name), re.escape(name)), k)}
        if len(addrs) != 1: continue
        if sigs[0][1] != sigs[1][1]:
            if a == b or not _results_unused(text, name): continue
            # both results in a register (void, integer, pointer): a class returned by value has a hidden result
            # pointer, which would shift the arguments (V1 D2)
            if not all(r == 'void' or INT_TYPES.match(r) or re.search(r'[*]\s*(?:const\s*)?$', r) for r in (sigs[0][1], sigs[1][1])):
                continue
            if not all(x == y or (INT32_TYPES.match(x) and INT32_TYPES.match(y)) for x, y in zip(a, b)): continue
            stats['DUPINT1-RET'] += 1
        drops += [m for types, ret, m in decls if types == b]
    if not drops: return text
    out, pos = [], 0
    for m in sorted(drops, key=lambda x: x.start()):
        out.append(text[pos:m.start()]); out.append('/* fnv-nx DUPINT1 */' + '\n' * m[0].count('\n')); pos = m.end()
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
    return '#include %s /* fnv-nx NEW4 */\n' % core.new_header(text) + text

# ---------------------------------------------------------------------------------------------------------- ALLOC1
#
# The game's global allocation functions are its own (0xADDR operator new, 0xADDR operator delete, ...) and the
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
        if not REPLACEABLE_PARAMS.match(m[1]): continue  # only operator new(size), delete(void*[, size])
        if FNV_TAG_BEFORE.search(text[max(0, m.start() - 600):m.start()]): continue
        if spans is None: spans = core.scope_spans(text)
        if any(k != 'extern' for k, _ in core.scope_chain(spans, m.start())): continue
        end = core.match_brace(text, m.end() - 1)
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
    spans = core.scope_spans(text)
    inserts = []
    for o, c, k, h in spans:
        if k != 'namespace' or h == 'namespace': continue
        block = text[o + 1:c]
        decls = []
        for m in TPL_FN_DEF.finditer(block):
            # top level of this namespace only
            inner = [sp for sp in spans if o < sp[0] < o + 1 + m.start() < sp[1]]
            if inner: continue
            if not m[2].strip(): continue        # `template <>`: an explicit specialization needs its primary first
            name = m[5]
            if not re.search(r'(?<![\w.>:~])%s\s*\(' % re.escape(name), block[:m.start()]): continue
            if not m[2].strip(): continue          # `template <>`: an explicit specialization needs its primary first
            tparams = set(re.findall(r'(?:class|typename)\s+(\w+)', m[2]))
            words = set(re.findall(r'[A-Za-z_]\w*', m[4] + ' ' + m[6]))
            params = _split_args(m[6])
            pnames = set()
            for p in params:
                ids = re.findall(r'[A-Za-z_]\w*', p)
                if len(ids) > 1: pnames.add(ids[-1])
            if words - tparams - TPL_PLAIN - pnames: continue
            decls.append('%s%s%s%s(%s);' % (m[1], m[3], m[4], name, m[6]))
        if decls:
            inserts.append((o + 1, ' ' + ' '.join(decls) + ' /* fnv-nx TPL2 */ '))
            stats['TPL2'] += len(decls)
    if not inserts: return text
    out, pos = [], 0
    for at, s in sorted(inserts):
        out.append(text[pos:at]); out.append(s); pos = at
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- TPL3
#
# The same lookup difference for function templates that are called before their namespace defines them, in the two
# forms TPL2 does not cover: a qualified call (`::N::f(x)` - a qualified name is looked up only at the definition, ADL
# never adds to it) and an unqualified call with explicit template arguments (`f<T>(a, b, tag)` reaching an overload
# of f defined further down, whose arguments live in another namespace than f). MSVC 2008 looked both up at the
# instantiation and found every overload of the file. TPL3 declares each such overload, inside its own namespaces,
# just in front of the file-scope declaration that holds the first call: `namespace N { template <class A> R f(A); }`.
# Only declarations whose names are all seen before that point are moved forward; default arguments stay on the
# definition. A redeclaration changes nothing in the code.

TPL3_DEF = re.compile(r'^[ \t]*template\s*<((?:[^<>{};]|<[^<>{};]*>)+)>\s*((?:(?:inline|static|__forceinline)\s+)*)'
                      r'((?:__declspec\s*\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)\s*)*)'
                      r'([\w \t\*&:<>,]*?[\s\*&])((?:__cdecl\s+)?)([A-Za-z_]\w*)\s*'
                      r'\(((?:[^;{}()]|\([^;{}()]*\))*)\)\s*\{', re.M)
TPL3_WORDS = set('''template typename class const volatile unsigned signed int char short long float
    double bool void inline static __cdecl __forceinline size_t wchar_t struct enum'''.split())

def _ns_path(chain):
    """The namespace names of a scope chain, or None when it holds a non-namespace scope or an anonymous one."""
    path = []
    for k, h in chain:
        if k != 'namespace': return None
        m = re.match(r'namespace\s+([A-Za-z_]\w*)$', re.sub(r'\s+', ' ', h.strip()))
        if not m: return None
        path.append(m[1])
    return path

def _strip_defaults(params):
    """`class A = B, int n = 3` -> `class A, int n` (top-level `= ...` of each comma-separated item)."""
    return ', '.join(re.sub(r'\s*=.*$', '', p, flags=re.S).strip() for p in _split_args(params) if p.strip())

def r_forward_template_calls(path, text, stats):
    """TPL3: declare a namespace's function templates in front of the first qualified / explicit-argument call that
    comes before their definition (see above)."""
    if 'template' not in text or 'namespace' not in text: return text
    masked = FRIEND_MASK.sub(lambda m: re.sub(r'[^\n]', ' ', m[0]), text)
    spans = core.scope_spans(masked)
    inserts = {}
    for m in TPL3_DEF.finditer(masked):
        tparams, name = m[1], m[6]
        if not tparams.strip(): continue
        nspath = _ns_path(core.scope_chain(spans, m.start()))
        if not nspath: continue
        before = masked[:m.start()]
        home = [sp for sp in spans if sp[0] < m.start() < sp[1]][-1]       # the innermost namespace block of the def
        tnames = set(re.findall(r'(?:class|typename)\s+(\w+)', tparams))
        params = _strip_defaults(m[7])
        pnames = {re.findall(r'[A-Za-z_]\w*', p)[-1] for p in _split_args(params)
                  if len(re.findall(r'[A-Za-z_]\w*', p)) > 1 and not p.rstrip().endswith(('*', '&'))}
        words = set(re.findall(r'(?<![\w:])[A-Za-z_]\w*', tparams + ' ' + m[4] + ' ' + params))
        words -= TPL3_WORDS | tnames | pnames | CXX_WORDS
        decl = re.sub(r'\s+', ' ', 'template <%s> %s%s%s%s(%s);' % (_strip_defaults(tparams), m[2],
                                                                    m[4].strip() + ' ', m[5], name, params))
        at = None
        # A: f<..>(...) in a function of the same namespace block: declared in front of that function, in the block
        for u in re.finditer(r'(?<![\w.>:~])%s\s*<[^;{}()]*>\s*\(' % re.escape(name), before):
            if u.start() < home[0]: continue                                 # (u is before the def: inside home)
            below = [sp for sp in spans if home[0] < sp[0] < u.start() < sp[1]]
            if not below or below[0][2] == 'namespace': continue             # in a function of home itself
            pos = _decl_start(masked, below[0][0])
            if pos <= home[0] or any(not re.search(r'\b%s\b' % re.escape(w), masked[:pos]) for w in words): continue
            at = pos
            break
        if at is None:
            # B: ::N::f(...) spelled with the whole namespace path: declared at file scope, wrapped in its namespaces,
            # when it names nothing but its template parameters and built-in types
            q = r'(?<![\w:])(?:::\s*)?' + r'\s*::\s*'.join(map(re.escape, nspath + [name])) + r'\s*[<(]'
            u = re.search(q, before)
            if not u or words: continue
            chain_spans = [sp for sp in spans if sp[0] < u.start() < sp[1]]
            at = _decl_start(masked, chain_spans[0][0]) if chain_spans else u.start()
            if at > u.start(): continue
            for n in reversed(nspath): decl = 'namespace %s { %s }' % (n, decl)
        lst = inserts.setdefault(at, [])
        if decl not in lst: lst.append(decl); stats['TPL3'] += 1
    if not inserts: return text
    out, pos = [], 0
    for at in sorted(inserts):
        out.append(text[pos:at]); out.append(' '.join(inserts[at]) + ' /* fnv-nx TPL3 */ '); pos = at
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- TYPN1
#
# MSVC 2008 takes `iterator_traits<_Iter>::iterator_category` as a type in a template argument list without the
# `typename` that standard C++ requires for a name that depends on a template parameter. GCC reads it as a value
# ("type/value mismatch"). TYPN1 adds `typename` in front of such a qualified name in a template argument position
# (after `<` or `,`, before `,` or `>`), inside the declaration a `template <...>` head introduces, when its template
# arguments name one of that head's parameters and the member is one of the library's type members.

TYPN1_MEMBERS = set('''iterator_category value_type difference_type distance_type pointer reference size_type
    const_pointer const_reference char_type int_type pos_type off_type allocator_type key_type mapped_type key_compare
    value_compare iterator const_iterator other _Checked_iterator_category _Inner_type _Secure_char_traits _Ptr_cat
    _Iter_random_cat _Move_cat _Iter_cat'''.split())
TYPN1_HEAD = re.compile(r'\btemplate\s*<((?:[^<>{};]|<[^<>{};]*>)+)>')
TYPN1_DEP = re.compile(r'([<,]\s*)((?:::\s*)?(?:[A-Za-z_]\w*\s*::\s*)*[A-Za-z_]\w*\s*<((?:[^<>;{}()]|<[^<>;{}()]*>)*)>'
                       r'\s*::\s*([A-Za-z_]\w*))(?=\s*[,>])')

def r_dependent_typenames(path, text, stats):
    """TYPN1: `typename` in front of a dependent library type member in a template argument list (see above)."""
    if 'template' not in text or '>::' not in text: return text
    masked = FRIEND_MASK.sub(lambda m: re.sub(r'[^\n]', ' ', m[0]), text)
    at = set()
    for h in TYPN1_HEAD.finditer(masked):
        names = re.findall(r'(?:class|typename)\s+([A-Za-z_]\w*)', h[1])
        if not names: continue
        semi, brace = masked.find(';', h.end()), masked.find('{', h.end())
        if brace < 0 and semi < 0: continue
        end = semi + 1 if brace < 0 or 0 <= semi < brace else core.match_brace(masked, brace)
        word = re.compile(r'\b(?:%s)\b' % '|'.join(map(re.escape, names)))
        for m in TYPN1_DEP.finditer(masked, h.end(), end):
            if m[4] in TYPN1_MEMBERS and word.search(m[3]): at.add(m.start(2))
    if not at: return text
    out, pos = [], 0
    for a in sorted(at):
        out.append(text[pos:a]); out.append('typename '); pos = a
    out.append(text[pos:])
    stats['TYPN1'] += len(at)
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- INST1
#
# An explicit instantiation of a class template member spells a `size_type` parameter as `unsigned int`, the type
# size_type has under MSVC x86. On AArch64 ILP32 size_t is unsigned long, so the directive names no member. INST1
# writes `<class>::size_type` at those positions, where the member's declaration in the file's class template says
# size_type: the member MSVC instantiated.

INST1_DIR = re.compile(r'^([ \t]*template\s+(?!<)[\w \t\*&:<>,]*?[\s\*&])((?:::\s*)?(?:[A-Za-z_]\w*\s*::\s*)*'
                       r'([A-Za-z_]\w*)\s*<[^;{}()]*>)\s*::\s*([A-Za-z_]\w*)\s*\(([^;{}]*)\)\s*;', re.M)

def _inst1_namespaces(text, at):
    """Names of the namespaces open at offset `at`, outermost first."""
    return [n[1] for n in re.compile(r'\bnamespace\s+([A-Za-z_]\w*)\s*\{').finditer(text, 0, at)
            if core.match_brace(text, n.end() - 1) > at]

def r_instantiation_size_types(path, text, stats):
    """INST1: `unsigned int` -> `C<..>::size_type` in an explicit member instantiation (see above)."""
    if 'template' not in text or 'unsigned' not in text: return text
    out, pos = [], 0
    for m in INST1_DIR.finditer(text):
        ps = _split_args(m[5])
        if not any(re.sub(r'\s+', ' ', p.strip()) == 'unsigned int' for p in ps): continue
        # the class template the directive names: in the qualifier's namespace, the nearest definition before it
        # that declares the member with as many parameters (the decomp has several `vector`s in other namespaces)
        quals = [q for q in re.split(r'\s*::\s*', m[2].split('<', 1)[0].strip()) if q][:-1]
        dps = None
        for cls in reversed(list(re.compile(r'\btemplate\s*<[^{};]*>\s*(?:class|struct)\s+%s\b[^;{]*\{'
                                                % re.escape(m[3])).finditer(text, 0, m.start()))):
            if quals and _inst1_namespaces(text, cls.start())[-len(quals):] != quals: continue
            body = text[cls.end():core.match_brace(text, cls.end() - 1)]
            decl = re.search(r'\b%s\s*\(([^;{}()]*)\)' % re.escape(m[4]), body)
            if decl and len(_split_args(decl[1])) == len(ps):
                dps = _split_args(decl[1]); break
        if dps is None: continue
        new = []
        for p, d in zip(ps, dps):
            if re.sub(r'\s+', ' ', p.strip()) == 'unsigned int' and re.match(r'\s*size_type\b', d):
                new.append('%s::size_type' % re.sub(r'\s+', ' ', m[2].strip())); stats['INST1'] += 1
            else:
                new.append(p.strip())
        out.append(text[pos:m.start(5)]); out.append(', '.join(new)); pos = m.end(5)
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- COPY1
#
# `X::X(const X& o) : B(o)` where B, a direct base of X, has besides its copy constructor a constructor that takes
# one of B's own bases A by value (`B(A a)`): both are reached from `const X&` by a derived-to-base conversion, which
# standard C++ ranks the same, so GCC calls it ambiguous. MSVC 2008 took B's copy constructor (binding to the more
# derived base). COPY1 writes `B(static_cast<const B&>(o))`, which only that constructor matches exactly. Left alone
# when B declares anything that takes an X.

COPY1_CTOR = re.compile(r'\b([A-Za-z_]\w*)\s*::\s*\1\s*\(\s*const\s+\1\s*&\s*([A-Za-z_]\w*)\s*\)\s*:')

def r_copy_ctor_base_casts(path, text, stats):
    """COPY1: cast the copied object to the base in a copy constructor's base initializer (see above)."""
    if '::' not in text or 'const' not in text: return text
    masked = FRIEND_MASK.sub(lambda m: re.sub(r'[^\n]', ' ', m[0]), text)
    edits = []
    for m in COPY1_CTOR.finditer(masked):
        cls, arg = m[1], m[2]
        bodies = _class_bodies(masked, cls)
        if not bodies: continue
        for base in bodies[0][1]:
            bb = _class_bodies(masked, base)
            if not bb or re.search(r'\b%s\b' % re.escape(cls), bb[0][0]): continue
            if not any(re.search(r'\b%s\s*\(\s*%s\s+\w*\s*\)' % (re.escape(base), re.escape(a)), bb[0][0])
                       for a in bb[0][1] if a): continue
            brace = masked.find('{', m.end())
            init = re.search(r'\b%s\s*\(\s*%s\s*\)' % (re.escape(base), re.escape(arg)), masked[m.end():brace])
            if not init: continue
            a = m.end() + init.start()
            edits.append((a, m.end() + init.end(), '%s(static_cast<const %s&>(%s))' % (base, base, arg)))
    if not edits: return text
    out, pos = [], 0
    for a, b, r in sorted(edits):
        if a < pos: continue
        out.append(text[pos:a]); out.append(r); pos = b
        stats['COPY1'] += 1
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
        if spans is None: spans = core.scope_spans(text)
        chain = core.scope_chain(spans, m.start())
        in_ns = bool(chain) and all(k == 'namespace' for k, _ in chain)
        if in_ns:
            # in a namespace only where the namespace takes the global overloads (`using ::fabs;`): the copy
            # collides with them. A copy tagged as a function of the binary (`// @fnv`) is kept.
            o = max(sp[0] for sp in spans if sp[0] < m.start() < sp[1])
            before = text[o:m.start()]
            name = m[1]
            if not re.search(r'\busing\s+::\s*%s\s*;' % name, before): continue
            if re.search(r'//\s*@fnv\b[^\n]*\n(?:[ \t]*//[^\n]*\n)*[ \t]*$', text[max(0, m.start() - 300):m.start()]): continue
        elif any(k not in ('extern',) or '"C"' in h for k, h in chain): continue
        end = core.match_brace(text, m.end() - 1)
        nl = '\n' * text.count('\n', m.start(), end)
        out.append(text[pos:m.start()])
        out.append(('/* fnv-nx MATH1 */' if have or in_ns else '#include <math.h> /* fnv-nx MATH1 */') + nl)
        have, pos = have or not in_ns, end
        stats['MATH1'] += 1
    out.append(text[pos:])
    text = ''.join(out)
    return math_own_statics(text, stats) if have else text

# MATH1-OWN: where MATH1 puts <math.h> in place of a copy, a `static` definition the file keeps of a C math function
# (`static __forceinline float sqrtf(float x) { return (float)sqrt(x); }`, unit)
# now meets <math.h>'s extern "C" declaration of the same name: MSVC refuses it (C2375), GCC takes it only under
# -fpermissive and then calls the C library function instead of the unit's own body. The definition and the file's
# calls of it are renamed fnvnx_own_<name>: the unit's own function, on both compilers. One file in the tree today.

MATH_OWN_STATIC = re.compile(r'^[ \t]*static\s+(?:(?:inline|__inline|__forceinline)\s+)*(?:float|double|long\s+double)\s+'
                             r'(?:__cdecl\s+)?(%s)\s*\([^;{}()]*\)\s*\{' % '|'.join(sorted(
                                 n for n in core.CRT_HEADERS['<math.h>'].split() if re.match(r'^[a-z]\w*$', n))), re.M)

def math_own_statics(text, stats):
    names = sorted(set(MATH_OWN_STATIC.findall(text)))
    for n in names:
        text = re.sub(r'(?<![\w.>:~])%s(?=\s*\()' % n, 'fnvnx_own_' + n, text)
        stats['MATH1-OWN'] += 1
    return text

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
# what a declarator or a function type has in those parentheses: `unsigned int (__cdecl* unit)(...)`,
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
        out.append(text[pos:m.start()]); out.append('%s((%s)(%s))' % (m[1], re.sub(r'\s+', ' ', m[2]), arg))
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
        if m[1]:
            out.append(text[pos:m.end()]); out.append(' '); pos = m.end()
        elif m[2]:
            out.append(text[pos:m.end()]); out.append(' '); pos = m.end()
        elif m[3]:
            out.append(text[pos:m.start()]); out.append(m[3] + ('ULL' if m[4] else 'LL')); pos = m.end()
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
                          lambda m: m[1] + '__forceinline', text, flags=re.M)
        stats['LEX1'] += n
    return text

# ---------------------------------------------------------------------------------------------------------- STD1
#
# Three units declare the std::abs(float) they call as a static member of a file-scope `struct std` (units.json:
# ?abs@std@@SAMM@Z -> 0xADDR, the game's fabsf, which 23 other units bind as ?abs@std@@YAMM@Z). Here `std` is the
# namespace of the C++ headers, so the struct cannot be declared. STD1 replaces such a struct, when every member is
# a <cmath> function, by `#include <cmath>`: the calls `std::abs(x)` then reach the standard overload, fabsf.

STD_STRUCT = re.compile(r'^struct\s+std\s*\{((?:\s*static\s+[^;{}()]*\([^;{}()]*\)\s*;)+)\s*\}\s*;', re.M)
STD_MATH = set('abs fabs sqrt floor ceil sin cos tan asin acos atan atan2 exp log log10 pow fmod'.split())

def r_std_struct(path, text, stats):
    """STD1: a decomp `struct std { static float abs(float); };` at file scope -> `#include <cmath>`."""
    if 'struct std' not in text: return text
    spans, out, pos = None, [], 0
    for m in STD_STRUCT.finditer(text):
        if spans is None: spans = core.scope_spans(text)
        if core.scope_chain(spans, m.start()): continue
        if not set(re.findall(r'(\w+)\s*\(', m[1])) <= STD_MATH: continue
        out.append(text[pos:m.start()])
        out.append('#include <cmath> /* fnv-nx STD1 */' + '\n' * m[0].count('\n')); pos = m.end()
        stats['STD1'] += 1
    if not out: return text
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- SPEC3
#
# The decomp writes explicit specializations of global class templates inside its per-function namespaces:
#     namespace fnv_ADDR { class Ent {...}; template <> class Ent<Ent> { Ent* p; }; }
# MSVC looked the names in the body up from that namespace. GCC (-fpermissive) makes the specialization a member of
# the template's namespace and looks them up there, so it finds the global forward declaration of the same name, or
# nothing. SPEC3 qualifies the names in such a body that the enclosing namespace declares as types before it
# (`fnv_ADDR::Ent* p`): the declarations MSVC found.

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
    spans = core.scope_spans(text)
    heads = lambda chain: [h for _, h in chain]
    decls = {}                                           # namespace path -> [(pos, name)]
    for m in NS_TYPE_DECL.finditer(text):
        chain = core.scope_chain(spans, m.start())
        if not chain or any(k != 'namespace' for k, _ in chain): continue
        decls.setdefault(tuple(heads(chain)), []).append((m.start(), m[1] or m[2]))
    edits = []
    for m in SPEC3_HEAD.finditer(text):
        chain = core.scope_chain(spans, m.start())
        if not chain or any(k != 'namespace' for k, _ in chain): continue
        key = tuple(heads(chain))
        tmpl = m[1]
        if re.search(r'\btemplate\s*<[^<>]*\w[^<>]*>\s*(?:class|struct)\s+' + tmpl + r'\b', text) and any(
                tuple(heads(core.scope_chain(spans, p.start()))) == key
                for p in re.finditer(r'\btemplate\s*<[^<>]*\w[^<>]*>\s*(?:class|struct)\s+' + tmpl + r'\b', text)):
            continue                                     # the template is the namespace's own
        end = _angle_end(text, m.end() - 1)
        if end < 0: continue
        brace = re.match(r'[^;{}]*\{', text[end:])
        if not brace: continue
        o = end + brace.end() - 1
        c = core.match_brace(text, o)
        names = {n for p, n in decls.get(key, ()) if p < m.start() and n != tmpl}
        if not names: continue
        qual = '::'.join(h.split()[-1] for h in key) + '::'
        for t in BODY_TOKENS.finditer(text, end, c):     # the base clause too (`: public Ent`)
            w = t[0]
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

# -------------------------------------------------------------------------------------------------------- closure
#
# The text of a unit's local headers, transitively, for the rules that read declarations from them.

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

# ---------------------------------------------------------------------------------------------------------- STL2
#
# Seven units use the real MSVC 2008 <string> / <vector> / <map> (the `_std` variants, tesobject_ob1_nogf,
# the unit units, bsfacegenmanager_ob1, blk_ADDR_od_ob1_rtm). Their game structs hold those containers
# (unit's leaf: a string at +0x14 and the next member at +0x30; unit's El: string, int, vector = 0x38, the
# element size 0xADDR divides by), and four of them program against the library's internals (_Mybase,
# _Vector_val, _Buy, _Myptr, _Mylast, _Make_iter, _String_val, stdext::unchecked_copy, _Allocate). libstdc++'s
# containers have other sizes (string 24, vector 12) and none of those internals. STL2 spells such a unit's `std` as
# `fnvnx_vc9`, the namespace of shim/include/fnvnx_vc9.h (VC9's containers in their x86 release layout: string 28,
# vector 24, map 32; every other std name is libstdc++'s through a using-directive), `stdext` as
# `fnvnx_vc9::stdext`, and includes that header in place of the first container header. Code only: comments and
# strings keep their text. Units with their own copies of these classes do not include the real headers.

STL2_HEADERS = re.compile(r'^[ \t]*#[ \t]*include[ \t]*<(?:string|vector|map|xstring|xtree|xmemory|xutility)>[^\n]*$',
                          re.M)
STL2_CODE_MASK = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|//[^\n]*|/\*.*?(?:\*/|\Z)', re.S)
STL2_NAME = re.compile(r'(?<![\w:])(::\s*)?\b(std|stdext)\b(?=\s*::)|\bnamespace\s+std\b')

def r_vc9_containers(path, text, stats):
    """STL2: `std` -> `fnvnx_vc9` and fnvnx_vc9.h for the STL headers in a unit that uses MSVC's containers (above)."""
    if not path.endswith('.cpp') or '#' not in text or 'include' not in text: return text
    if not STL2_HEADERS.search(text): return text
    masked = set()
    for m in STL2_CODE_MASK.finditer(text):
        masked.update(range(m.start(), m.end()))
    out, pos, n = [], 0, 0
    for m in STL2_NAME.finditer(text):
        if m.start() in masked: continue
        if m[0].startswith('namespace'):
            new = 'namespace fnvnx_vc9'
        else:
            new = (m[1] or '') + ('fnvnx_vc9' if m[2] == 'std' else 'fnvnx_vc9::stdext')
        out.append(text[pos:m.start()]); out.append(new); pos = m.end(); n += 1
    out.append(text[pos:])
    text = ''.join(out)
    # the unit's own code in namespace std (allocator<El>) meant MSVC's size_t, unsigned int: VC9's _Sizet
    for m in reversed(list(re.finditer(r'\bnamespace fnvnx_vc9\s*\{', text))):
        end = core.match_brace(text, m.end() - 1)
        body, k = re.subn(r'(?<![\w:])size_t\b', '_Sizet', text[m.end():end])
        text = text[:m.end()] + body + text[end:]; n += k
    first = [True]
    def header(m):
        if first[0]:
            first[0] = False
            return '#include "fnvnx_vc9.h" /* fnv-nx STL2 */'
        return '/* fnv-nx STL2: %s */' % m[0].strip()
    text = STL2_HEADERS.sub(header, text)
    stats['STL2'] += 1 + n
    return text

# ---------------------------------------------------------------------------------------------------------- STL1
#
# A few units call internals of the MSVC 2008 C++ library by name (unit: _SCL_SECURE_VALIDATE,
# std::_Checked_base, std::_Ptr_cat, the checked-copy iterator tags of <xutility>). STL1 includes
# shim/include/fnvnx_msvcstl.h, which defines them as <xutility> does, in a unit that uses them without defining them.
#
# A unit that derives a container of its own from std::_Container_base_aux_alloc_real (blk_ADDR_od_ob1_rtm: MSVC
# vector<void*>'s copy constructor written against <vector>'s _SECURE_SCL internals) gets the header's AUX part too
# (`#define FNVNX_MSVCSTL_AUX` in front: the container base and its proxy, _Destroy_range,
# stdext::unchecked_uninitialized_copy, the checked vector iterators). Such a unit builds `std::vector<T>::
# const_iterator(ptr, container)`, which libstdc++'s iterators cannot take: STL1 spells `std::vector<T[, A]>::
# const_iterator` / `::iterator` as MSVC's own names of those types there, `std::_Vector_const_iterator<T, A>` /
# `std::_Vector_iterator<T, A>` (A = std::allocator<T> by default; the same types under MSVC). Units with their own
# copy of these classes (blk_ADDR and the unit units define them) are left alone.

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
        name = '_Vector_const_iterator' if it[1] == 'const_iterator' else '_Vector_iterator'
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
# enumerators and macros are left alone, and so are static member functions and friends. An out-of-class member
# `template <class A, class T> R C<A, T>::f(...) { ... }` of a class template C that only an included header defines
# (unit.h's Ent<A, T>::RemoveHead) gets the same, from that header's single definition of C
# and its dependent bases; explicit and partial specializations (arguments other than the parameters) are left.
# TPL1-NESTED: the same for a class nested in a class template (no template head of its own) whose base is another
# member class of that template (unit's hand-written vector: `struct Iterator : ConstIterator` using `aux`,
# `ptr`): that base is a member of the current instantiation, which GCC does not search either.

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
        types.add(m[1] or m[2] or m[3])
    for m in re.finditer(r'(?<![\w:~.>])([A-Za-z_]\w*)\s*\(', flat):        # member functions (and noise)
        values.add(m[1])
    for stmt in re.split(r'[;{}]', re.sub(r'\((?:[^()]|\([^()]*\))*\)', '()', flat)):
        s = re.sub(r'^\s*(?:(?:public|private|protected)\s*:\s*)+', '', stmt).strip()
        if not s or '(' in s or re.match(r'(?:typedef|using|friend|template|enum|struct|class|union)\b', s): continue
        for d in s.split(','):
            m = re.search(r'([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*(?::\s*\w+\s*)?(?:=.*)?$', d.strip(), re.S)
            if m: values.add(m[1])
    values = {v for v in values if v not in CXX_KEYWORDS and v != own_name and not v.startswith('__')
              and not re.match(r'^[A-Z][A-Z0-9_]*$', v)}
    return values, types

def _base_specs(bases):
    """[(name, template arguments or None)] of a base clause."""
    out = []
    for b in _split_bases(bases):
        b = re.sub(r'\b(?:public|protected|private|virtual)\b', '', b).strip()
        m = re.match(r'(?:::)?([A-Za-z_]\w*)\s*(?:<(.*)>)?\s*$', b, re.S)
        if m: out.append((m[1], m[2]))
    return out

_TPL1_SPANS = {}

def r_dependent_base_members(path, text, stats):
    """TPL1: `this->` in front of the members of a dependent base used by plain name in a class template (see above)."""
    if 'template' not in text or not (TPL1_CLASS.search(text) or TPL1_OUT_DEF.search(text)): return text
    spans = core.scope_spans(text)
    parent, stack = {}, []
    for sp in spans:                                       # innermost enclosing brace of every brace
        while stack and stack[-1][1] < sp[0]: stack.pop()
        parent[sp[0]] = stack[-1][0] if stack else None
        stack.append(sp)
    heads = lambda pos: tuple(h for _, h in core.scope_chain(spans, pos))
    classes = {}                                           # name -> [(open, close, bases, template params, scope)]
    for m in TPL1_CLASS.finditer(text):
        o = m.end() - 1
        ps = set(re.findall(r'(?:class|typename|int|unsigned|bool|short|long|char)\s+([A-Za-z_]\w*)', m[1]))
        classes.setdefault(m[2], []).append((o, core.match_brace(text, o), m[3] or '', ps, heads(m.start()),
                                                   m[2]))
    tdefs = {d[0] for v in classes.values() for d in v}
    for m in PLAIN_CLASS.finditer(text):
        o = m.end() - 1
        if o in tdefs: continue
        classes.setdefault(m[1], []).append((o, core.match_brace(text, o), m[2] or '', set(), heads(m.start()),
                                                   m[1]))
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
                if f not in _TPL1_SPANS: _TPL1_SPANS[f] = core.scope_spans(t)
                if core.scope_chain(_TPL1_SPANS[f], m.start()): continue
                o = m.end() - 1
                v, ty = _members(_flat_body(t, o, core.match_brace(t, o)), name)
                vals |= v; types |= ty
                for bname, _ in _base_specs(m[1] or ''):
                    bv, bt = included_members(bname, depth + 1)
                    vals |= bv; types |= bt
        inc[name] = (vals, types)
        return inc[name]
    fixes = {}                                             # class open -> names to qualify
    templates = [(d[0], d[1], d[3]) for v in classes.values() for d in v if d[3]]
    for name, defs in classes.items():
        for d in defs:
            o, c, bases, params, scope, _ = d
            if not bases: continue
            # a class nested in a class template: a base that is another member class of that template is a member
            # of the current instantiation, as dependent as a base naming the parameters (TPL1-NESTED)
            outer = [(to, tc, tp) for to, tc, tp in templates if to < o < tc]
            if not params and not outer: continue
            dep, dep_types = set(), set()
            for bname, args in _base_specs(bases):
                if not params:
                    nested = [bd for bd in visible(bname, scope) if any(to < bd[0] < tc for to, tc, _ in outer)]
                    for bd in nested:
                        v, ty = members_of(bd)
                        dep |= v; dep_types |= ty
                    continue
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
            if names and not params: stats['TPL1-NESTED'] += 1
    def included_template(name):
        """names to qualify in an out-of-class member of class template `name` that only included headers define: the
        members of its dependent bases, minus its own (None when not exactly one consistent such definition)"""
        if '#include' not in text: return None
        if not closure: closure.extend(_closure(path, text)[1:] or [None])
        found = set()
        for ft in closure:
            if ft is None or name not in ft[1]: continue
            f, t = ft
            for m in TPL1_CLASS.finditer(t):
                if m[2] != name: continue
                if f not in _TPL1_SPANS: _TPL1_SPANS[f] = core.scope_spans(t)
                if core.scope_chain(_TPL1_SPANS[f], m.start()): continue
                o = m.end() - 1
                ps = set(re.findall(r'(?:class|typename|int|unsigned|bool|short|long|char)\s+([A-Za-z_]\w*)', m[1]))
                dep, dep_types = set(), set()
                for bname, args in _base_specs(m[3] or ''):
                    if args is None or not set(re.findall(r'[A-Za-z_]\w*', args)) & ps: continue
                    v, ty = included_members(bname)
                    dep |= v; dep_types |= ty
                own_v, own_t = _members(_flat_body(t, o, core.match_brace(t, o)), name)
                found.add(frozenset(dep - dep_types - own_v - own_t - ps - {name}))
        return set(next(iter(found))) if len(found) == 1 and next(iter(found)) else None
    bodies = []                                            # (body open, body close, head, names)
    for fo, fc, k, h in spans:
        if k != 'function' or parent.get(fo) not in fixes: continue
        if re.search(r'\b(?:static|friend)\b', h): continue
        bodies.append((fo, fc, h, fixes[parent[fo]][1]))
    by_name = {}
    for o, (name, names) in fixes.items(): by_name.setdefault(name, []).append(names)
    for m in TPL1_OUT_DEF.finditer(text):
        if m[2] in classes:
            if len(by_name.get(m[2], ())) != 1 or len(classes[m[2]]) != 1: continue
            names = by_name[m[2]][0]
        else:                                              # the class template lives in an included header
            # only the member of the primary template, its own parameters as the arguments (not a specialization)
            own = re.findall(r'(?:class|typename)\s+([A-Za-z_]\w*)\s*(?=[,>]|$)', m[1])
            targs = re.search(r'<([^<>;{}]*)>\s*::\s*~?[A-Za-z_]\w*\s*\($', m[0])
            if not own or not targs or [a.strip() for a in targs[1].split(',')] != own: continue
            if core.scope_chain(spans, m.start()): continue
            names = (included_template(m[2]) or set()) - set(own)
            if not names: continue
        brace = re.match(r'[^;{}]*\{', text[m.end():])
        if not brace: continue
        fo = m.end() + brace.end() - 1
        if re.search(r'\b(?:static|friend)\b', text[m.start():fo]): continue
        bodies.append((fo, core.match_brace(text, fo) - 1, text[m.start():fo], names))
    if not bodies: return text
    edits = set()
    for fo, fc, head, names in bodies:
        body = COMMENT.sub(' ', text[fo:fc])
        params_text = head[head.find('('):] if '(' in head else ''
        declared = set(re.findall(r'([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)?(?:=[^,()]*)?\s*[,)]', params_text))
        declared |= {v for t, _, v in TPL1_LOCAL.findall(body) if t not in NOT_A_TYPE}
        use = names - declared
        if not use: continue
        for t in BODY_TOKENS.finditer(text, fo, fc):
            w = t[0]
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
# MSVC binds a temporary to a non-const reference parameter (`actor->Ent(a + b)` with Ent(Ent&),
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

_RREF_CLASS_HEAD = re.compile(r'\b(?:class|struct|union)\s+(?:__declspec\s*\((?:[^()]|\([^()]*\))*\)\s*)*([A-Za-z_]\w*)')
_RREF_MEMBER_HEAD = re.compile(r'([A-Za-z_]\w*)\s*(?:<[^<>()]*>\s*)?::\s*~?[A-Za-z_]\w*\s*\(')

def _rref_member_class(text, spans, pos):
    """The class whose member function body holds pos (`T C::f(...) {` or a function defined in C's body), or None."""
    cls = None
    for k, h in core.scope_chain(spans, pos):
        if k == 'class':
            m = _RREF_CLASS_HEAD.search(h)
            cls = m[1] if m else None
        elif k == 'function':
            m = list(_RREF_MEMBER_HEAD.finditer(h.split('(')[0] + '('))
            if m: cls = m[-1][1]
            return cls
    return None

def _rref_class_shapes(unit, cls, name, cache):
    """[[non-const reference per parameter]] of the declarations of member `name` in the nearest class, starting at
    cls and going through its bases, that declares it (class scope lookup); [] when none does."""
    key = (cls, name)
    if key in cache: return cache[key]
    found, level, seen = [], [cls], set()
    while level and not found and len(seen) < 16:
        nxt = []
        for c in level:
            if c in seen: continue
            seen.add(c)
            for body, bases in _class_bodies(unit, c):
                for d in RREF_DECL.finditer(body):
                    if d[2] != name or d[1] in RREF_NOT_TYPE: continue
                    params = d[3].strip()
                    ps = [] if params in ('', 'void') else _split_args(params)
                    found.append([_nonconst_ref(p) for p in ps])
                nxt += [b for b in bases if b]
        level = nxt
    cache[key] = found
    return found

def r_temporaries_to_references(path, text, stats):
    """RREF1: fnvnx_lvalue around a temporary passed to a non-const reference parameter (see above)."""
    if '&' not in text or not path.endswith('.cpp'): return text
    unit = '\n'.join(t for _, t in _closure(path, text)) if '#include' in text else COMMENT.sub(' ', text)
    decls = {}
    for m in RREF_DECL.finditer(unit):
        if m[1] in RREF_NOT_TYPE: continue
        params = m[3].strip()
        ps = [] if params in ('', 'void') else _split_args(params)
        decls.setdefault(m[2], []).append([_nonconst_ref(p) for p in ps])
    refs = {n: v for n, v in decls.items() if any(any(r) for r in v)}
    if not refs: return text
    call = re.compile(r'(?<![\w~])(%s)\s*\(' % '|'.join(sorted(refs, key=len, reverse=True)))
    spans = core.scope_spans(text)
    bodies = []
    for o, c, k, _ in spans:
        if k == 'function' and not (bodies and o < bodies[-1][1]): bodies.append((o, c))
    edits, bi, class_cache = [], 0, {}
    for m in call.finditer(text):
        while bi < len(bodies) and bodies[bi][1] < m.start(): bi += 1
        if bi == len(bodies) or not bodies[bi][0] < m.start(): continue
        before = text[max(0, m.start() - 40):m.start()]
        pw = re.search(r'([A-Za-z_]\w*)\s*$', before)
        if pw and pw[1] not in RREF_NOT_TYPE: continue                 # a declaration (`T name(...)`)
        close = _call_end(text, m.end() - 1)
        args = _split_args(text[m.end():close - 1])
        if args == ['']: args = []
        shapes = [r for r in refs[m[1]] if len(r) == len(args)]
        cls = _rref_member_class(text, spans, m.start())
        if cls and not re.search(r'(?:\.|->|::)\s*$', before):
            # an unqualified call in a member function of cls: class scope is searched first, so the class's own
            # declarations of the name (or the nearest base's) are the candidates where it has any (RREF1-CLASS)
            own = _rref_class_shapes(unit, cls, m[1], class_cache)
            if own:
                own = [r for r in own if len(r) == len(args)]
                if own != shapes and own: stats['RREF1-CLASS'] += 1
                shapes = own
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
        name = 'fnvnx_anon_' + m[3]
        nl = '\n' * text.count('\n', m.start(), m.end())
        out.append(text[pos:m.start()])
        out.append('struct %s {%s}; %s%s %s(' % (name, m[1].replace('\n', ' '), name, m[2], m[3]) + nl)
        pos = m.end()
        stats['ANON1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- ENUM1
#
# The decomp forward-declares enums (`enum DIALOGUE_TYPE;`, `enum ENUM_FORM_ID;`, Ent's `enum Index;`), which
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
        for dp, _, fs in core.walk_sorted(os.path.join(root, d)):
            for f in fs:
                if not f.endswith(('.h', '.cpp', '.inl', '.hpp', '.c')): continue
                with open(os.path.join(dp, f), encoding='utf-8', errors='surrogateescape') as h: t = h.read()
                if 'enum' in t: names.update(m[2] for m in OPAQUE_ENUM.finditer(t))
    _OPAQUE_NAMES = names
    return _OPAQUE_NAMES

def _fits_int(body):
    for v in re.findall(r'=\s*(-?\s*(?:0[xX][0-9a-fA-F]+|\d+))[uUlL]*', body):
        if not -2 ** 31 <= int(v.replace(' ', ''), 0) < 2 ** 31: return False
    return True

def r_opaque_enums(path, text, stats):
    """ENUM1: `enum X;` -> `enum X FNVNX_ENUM_INT;`, and `enum X {` -> `enum X : int {` for those names (see above).
    FNVNX_ENUM_INT is `: int` (fnvnx_compat.h); in the S11 gate it is empty: MSVC 2008 has no opaque declaration,
    and its own forward declaration `enum X;` is the same type, underlying int (ENUM1-FWD)."""
    if 'enum' not in text: return text
    names = _opaque_enum_names(path)
    if not names: return text
    def decl(m):
        if m[2] not in names: return m[0]
        stats['ENUM1'] += 1
        return m[1] + ' FNVNX_ENUM_INT' + m[3]
    def defn(m):
        if m[1] not in names: return m[0]
        if not _fits_int(text[m.end():text.find('}', m.end())]): return m[0]   # int cannot hold it: as it was
        stats['ENUM1'] += 1
        return 'enum %s : int%s{' % (m[1], m[2] or ' ')
    text = OPAQUE_ENUM.sub(decl, text)
    return ENUM_DEF.sub(defn, text)

# ---------------------------------------------------------------------------------------------------------- ENUM2
#
# An enum with an enumerator in [2^31, 2^32) (`CHANGE_SCRIPT_VARIABLES = 0xADDR` in Ent.h, havok's
# `MARKED_RO`, Ent 0xADDR, `Ent ... = 0xADDR`): MSVC's underlying type is int and the
# value is converted to int (0xADDR is INT_MIN, 0xADDR is -1), so it promotes, compares, divides, shifts and
# widens as a negative int. GCC picks unsigned int (long long when the enum also has a negative value). ENUM2 writes
# MSVC's type out, `enum E : int {`, with an (int) cast on each such literal (a fixed type does not take the value
# otherwise). Only enums whose initializers are all integer literals in [-2^31, 2^32) and which have no base yet
# (S12 f8 ENUM-UNSIGNED: 6 sites in the sample, 51 definitions in the tree). The enum bit-fields S12 lists as
# ENUM-BITFIELD (signed in MSVC) would take the same base; the tree has none.

ENUM_BODY = re.compile(r'\benum(\s+[A-Za-z_]\w*)?(\s*)\{([^{}]*)\}')
ENUM_INIT = re.compile(r'(//[^\n]*|/\*.*?\*/)|(=\s*)(-\s*)?(0[xX][0-9a-fA-F]+|\d+)([uUlL]*)\b', re.S)

def r_enum_int_values(path, text, stats):
    """ENUM2: `enum E { A = 0xADDR }` -> `enum E : int { A = (int)0xADDR }` (see above). Same line count."""
    if 'enum' not in text: return text
    def body(m):
        code = re.sub(r'//[^\n]*|/\*.*?\*/', ' ', m[3], flags=re.S)
        inits = re.findall(r'=([^,]*)', code)
        vals = []
        for e in inits:
            v = re.fullmatch(r'\s*(-\s*)?(0[xX][0-9a-fA-F]+|\d+)[uUlL]*\s*', e)
            if not v: return m[0]                   # an expression: left as it is
            vals.append(-int(v[2], 0) if v[1] else int(v[2], 0))
        if not any(v >= 2 ** 31 for v in vals) or not all(-2 ** 31 <= v < 2 ** 32 for v in vals): return m[0]
        def lit(n):
            if n[1] or n[3] or int(n[4], 0) < 2 ** 31: return n[0]
            return n[2] + '(int)' + n[4] + n[5]
        stats['ENUM2'] += 1
        return 'enum%s : int%s{%s}' % (m[1] or '', m[2] or ' ', ENUM_INIT.sub(lit, m[3]))
    return ENUM_BODY.sub(body, text)

# ---------------------------------------------------------------------------------------------------------- ELAB1
#
# Some units name a class after a Win32 / CRT function (`struct HeapAlloc { ... }` in blk_ADDR: the decomp's
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
    if core.PLATFORM is None: core.PLATFORM = core.platform_names()
    spans = None
    tds = set()
    for m in FNPTR_TYPEDEF.finditer(text):
        if m[1] not in core.PLATFORM: continue
        if spans is None: spans = core.scope_spans(text)
        if not core.scope_chain(spans, m.start()): tds.add(m[1])
    if tds:
        text, n = re.subn(r'(?<![\w:.>~])(%s)\b(?!\s*\()' % '|'.join(sorted(tds)), r'fnvnx_t_\1', text)
        stats['ELAB1'] += n
        spans = None
    found = {}
    for m in ELAB_DEF.finditer(text):
        if m[2] not in core.PLATFORM: continue
        if spans is None: spans = core.scope_spans(text)
        if core.scope_chain(spans, m.start()): continue
        found[m[2]] = m[1]
    if not found: return text
    use = re.compile(r'(?<![\w:.>~])(%s)\b(?=\s*(?:[\*&]|[A-Za-z_]))' % '|'.join(sorted(found, key=len, reverse=True)))
    out, pos = [], 0
    for m in use.finditer(text):
        before = text[max(0, m.start() - 12):m.start()]
        if re.search(r'\b(?:struct|class|union|enum|typename)\s+$', before): continue
        out.append(text[pos:m.start()]); out.append(found[m[1]] + ' '); pos = m.start()
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
            names.append(m[1])
    return names

def r_local_anonymous_unions(path, text, stats):
    """ANON3: `union { ... struct { ... }; ... };` in a function body -> `FNVNX_ANON3_BEGIN(N) union ... };
    FNVNX_ANON3_END(N) FNVNX_ANON3_REF(N, m) ...` (same line count). fnvnx_compat.h: `struct fnvnx_anon_uN { union ...
    }; } fnvnx_anon_vN; decltype(fnvnx_anon_vN.m)& m = fnvnx_anon_vN.m; ...`; tools/equiv/msvc: nothing, the decomp's own
    anonymous union (MSVC 2008 has no decltype)."""
    if 'union' not in text or 'struct' not in text: return text
    masked = FRIEND_MASK.sub(lambda x: re.sub(r'[^\n]', ' ', x[0]), text)
    spans = core.scope_spans(masked)
    edits, n = [], 0
    for o, c, k, h in spans:
        if k != 'class' or ANON3_DECOR.sub('', h).strip() != 'union': continue
        tail = re.match(r'\s*;', masked[c + 1:])
        chain = core.scope_chain(spans, o)
        if not tail or not chain or chain[-1][0] not in ('function', 'other') or \
                'function' not in [ck for ck, _ in chain]: continue
        if not any(sp[2] == 'class' and o < sp[0] and sp[1] < c for sp in spans): continue
        names = _anon3_members(masked, o, c, spans)
        if not names: continue
        u = masked.rfind('union', 0, o)
        v = 'fnvnx_anon_v%d' % n
        refs = ' '.join('FNVNX_ANON3_REF(%d, %s)' % (n, m) for m in names)
        edits.append((u, 'FNVNX_ANON3_BEGIN(%d) ' % n))
        edits.append((c + 1 + tail.end(), ' FNVNX_ANON3_END(%d) %s' % (n, refs)))
        n += 1
        stats['ANON3'] += 1
    for at, s in sorted(edits, reverse=True):
        text = text[:at] + s + text[at:]
    return text

# ---------------------------------------------------------------------------------------------------------- ANON2
#
# The decomp nests an anonymous struct in an anonymous union whose members have constructors, a destructor or a copy
# assignment (Ent::Extra's `String unit`, Ent's `Ent unit`). MSVC took that
# and, as for any union member, never ran those functions for it; GCC refuses such members in an anonymous aggregate.
# ANON2 declares the member as raw storage of the same size and alignment
# (`alignas(String) unsigned char unit[sizeof(String)];`) and every use of it in the file as the object
# that storage holds (`(*(String*)(slot->unit)).FUN_ADDR()`): same bytes, same calls, and, as before,
# no implicit construction or destruction. The uses are converted in the file that declares the member and in every
# file that includes it (Ent is declared in unit.h and used in the .cpp).
# Where another class of the file or its headers has a member of that name (the .cpp's Ent has
# its own unit) or the file declares the name otherwise, a use is converted only where it is the member's:
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
        uc = core.match_brace(text, uo)
        for s in re.finditer(r'\bstruct\s*\{', text[uo + 1:uc - 1]):
            so = uo + 1 + s.end() - 1
            sc = core.match_brace(text, so)
            if spans is None: spans = core.scope_spans(text)
            # the struct must be a direct member of this union
            if max((sp[0] for sp in spans if sp[0] < so < sp[1]), default=None) != uo: continue
            flat = _flat_body(text, so, sc)
            for m in ANON_MEMBER.finditer(flat):
                ty, name = m[2], m[3]
                base = ty.split('<')[0].strip()
                if base in CXX_KEYWORDS or base in ('unsigned', 'signed', 'const'): continue
                special = False
                for d in re.finditer(CLASS_DEF % re.escape(base), text):
                    body = text[d.end():core.match_brace(text, d.end() - 1)]
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
                        if hm: owner = hm[1]
                found.append((owner, ty, name, so + dm.start(), so + dm.end()))
    return found

def _anon2_names(path):
    """names of the members ANON2 handles anywhere in the tree (a file that names none needs no closure)"""
    global _ANON2_NAMES
    if _ANON2_NAMES is not None: return _ANON2_NAMES
    names = set()
    for d in ('src', 'include'):
        for dp, _, fs in core.walk_sorted(os.path.join(_roots(path), d)):
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
    bare = COMMENT.sub(lambda m: re.sub(r'[^\n]', ' ', m[0]), text)        # same offsets, no comments
    decl = re.compile(r'[\w>\*&]\s+\**%s\s*(?:\[[^\]]*\])?\s*[;=,)]' % re.escape(name))
    bases, own = {}, {}
    for _, t in files:
        for m in ANY_CLASS_DEF.finditer(t):
            bases.setdefault(m[1], set()).update(b for b, _ in _base_specs(m[2] or ''))
            o = m.end() - 1
            if decl.search(_flat_body(t, o, core.match_brace(t, o))): own.setdefault(m[1], True)
    def derives(cls, depth=0):
        if cls == owner: return True
        if depth > 8 or cls in own: return False           # a class with its own member of that name
        return any(derives(b, depth + 1) for b in bases.get(cls, ()))
    others = {c for c in own if c != owner}
    count = len(decl.findall(bare)) - (1 if skip else 0)
    simple = not others and count == 0
    spans = None if simple else core.scope_spans(text)
    def context(pos):
        """the class whose member function holds pos, and that function's (head, body) text"""
        cls, fn = None, None
        for o, c, k, h in spans:
            if o > pos: break
            if not o < pos < c: continue
            if k == 'class' and fn is None:
                hm = re.search(r'\b(?:class|struct)\s+(?:__declspec\s*\((?:[^()]|\([^()]*\))*\)\s*)*([A-Za-z_]\w*)', h)
                cls = hm[1] if hm else None
            elif k == 'function' and fn is None:
                fn = (h, o, c)
                q = re.search(r'([A-Za-z_]\w*)\s*::\s*~?[A-Za-z_]\w*\s*\([^()]*(?:\([^()]*\)[^()]*)*\)[^()]*$', h)
                if q: cls = q[1]
        return cls, fn
    def var_type(var, fn, pos):
        if fn is None: return None
        h, o, _ = fn
        hit = None
        for m in re.finditer(r'\b([A-Za-z_]\w*)\s*(?:const\s*)?[\*&]?\s*(?:const\s*)?\b%s\s*[,)=;\[]' % re.escape(var),
                             h + bare[o:pos]):
            if m[1] not in CXX_KEYWORDS: hit = m[1]
        return hit
    edits = []
    for use in re.finditer(r'(?<![\w~])%s\b' % re.escape(name), bare):
        if skip and skip[0] <= use.start() < skip[1]: continue
        before = bare[max(0, use.start() - 2):use.start()]
        if before.endswith('::'): continue
        if before.endswith('.') or before.endswith('->'):
            dot = use.start() - (1 if before.endswith('.') else 2)
            start = core.operand_start(text, dot)
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
                if (pm and pm[1] not in NOT_TYPES and not pm[1][0].isdigit()
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
# lacks the declarations the unit was written against (actor.cpp, unit.cpp, unit.cpp, the
# unit *_types headers: 41 includes). INC1 spells such an include as the file in the including directory is
# spelled: the header MSVC read.

INC_QUOTED = re.compile(r'^([ \t]*#[ \t]*include[ \t]*")([^"]+)(")', re.M)
_DIR_LISTING = {}

def r_include_case(path, text, stats):
    """INC1: a quoted #include that names, up to case, a file in the including directory -> that file (see above)."""
    if '#include' not in text: return text
    here = os.path.dirname(os.path.abspath(path))
    def sub(m):
        inc = m[2]
        full = os.path.normpath(os.path.join(here, inc))
        if os.path.isfile(full): return m[0]
        d, b = os.path.split(full)
        if d not in _DIR_LISTING:
            _DIR_LISTING[d] = core.list_ci(d)
        real = _DIR_LISTING[d].get(b.lower())
        if not real: return m[0]
        stats['INC1'] += 1
        return m[1] + os.path.join(os.path.dirname(inc), real).replace(os.sep, '/') + m[3]
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
        name = m[1]
        if name in CXX_KEYWORDS or re.match(r'\s*\(', text[m.end(1):m.end(1) + 3]): continue
        if spans is None: spans = [sp for sp in core.scope_spans(text) if sp[2] == 'function']
        body = next(((o, c, h) for o, c, _, h in spans if o < m.start() < c), None)
        if body is None: continue
        local = body[2] + '\n' + text[body[0]:m.start()]          # parameters, then the body up to the compare
        is_ptr = re.search(r'\b[A-Za-z_][\w:]*(?:\s*<[^<>;]*>)?\s*\*+\s*%s\s*[=;,)]' % re.escape(name), local)
        declared_other = re.search(r'\b(?:int|unsigned|short|char|long|float|double|bool|__int64|DWORD|UINT|size_t)\s+%s\b'
                                   % re.escape(name), local)
        if declared_other: continue
        # `*p > 0` with `T* p` compares the pointee, a scalar (signed for `int* p`: `<= 0` is not `== 0`); the
        # compare is the pointer's only through `**` (PTR1-DEREF)
        if is_ptr and re.search(r'(?<![\w)\]])\*\s*$', text[max(0, m.start() - 4):m.start()]) \
                and is_ptr[0].count('*') == 1:
            continue
        if not is_ptr and not (name in funcs and not re.search(r'[\w\*&>]\s+%s\s*[=;,\[]' % re.escape(name), local)):
            continue
        out.append(text[pos:m.start(2)]); out.append('!=' if m[2] == '>' else '=='); pos = m.end(2)
        stats['PTR1'] += 1
    if not out: return text
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- UNITS
#
# Helpers shared with s2_bodies.py: the unit's flags and function addresses from build/units.json, and the @fnv tag.

FNV_TAG_ADDR = re.compile(r'//[ \t]*@fnv[ \t]+0x([0-9a-fA-F]+)')
NOT_FUNCTIONS = set('if for while switch return sizeof catch do else'.split())

_UNIT_INFO = None

def _unit_info(path):
    """(flags, {function addresses}) of the unit `path` from build/units.json, or None."""
    global _UNIT_INFO
    if _UNIT_INFO is None:
        import json
        _UNIT_INFO = {}
        root, uj = core._units_json(path)
        if uj:
            for u in json.load(open(uj))['units']:
                _UNIT_INFO[os.path.normpath(os.path.join(root, u['source']))] = (
                    tuple(u.get('flags') or ()), {int(f['address'], 16) for f in u.get('functions') or ()})
    return _UNIT_INFO.get(os.path.normpath(os.path.abspath(path)))

_ALL_FUNCTIONS = None

def _all_unit_functions(path):
    """Every address some unit of build/units.json defines (its `functions`)."""
    global _ALL_FUNCTIONS
    if _ALL_FUNCTIONS is None:
        _unit_info(path)
        _ALL_FUNCTIONS = set().union(*(fs for _, fs in _UNIT_INFO.values())) if _UNIT_INFO else set()
    return _ALL_FUNCTIONS

# ---------------------------------------------------------------------------------------------------------- ALIGN1
#
# `__declspec(align(16)) struct S {...};`: MSVC aligns the type S (also with a declarator after the body, and on a
# forward declaration `__declspec(align(16)) struct S;`, which aligns S where it is defined: clang's MS mode, which
# follows MSVC, gives sizeof/alignof 64/16 for a 56-byte record in all three forms). GCC takes an attribute in front
# of the class key as the declaration's, not the type's: with no declarator it is dropped ("attribute ignored in
# declaration of", -Wattributes), with one it aligns only that variable, so the record is 4-aligned and the records
# that hold it shrink and move their members (S12 f8 ALIGN-IGNORED / LAYOUT-ALIGN-IGNORED: S_A 48 -> 36, Buf 64 ->
# 56, unit 240 -> 224). ALIGN1 moves the align specifier behind the class key, where both compilers apply
# it to the type. Only in front of a class definition or a forward declaration: in `__declspec(align(16)) struct
# Atoms unit;` the class key starts an elaborated type and the alignment is the member's, on both.

ALIGN_HEAD = re.compile(r'(__declspec\s*\(\s*align\s*\(\s*\d+\s*\)\s*\))(\s*)'
                        r'((?:__declspec\s*\((?:[^()]|\([^()]*\))*\)\s*)*)\b(struct|class|union)\b(?=([^;{}()]*)([;{]))')

def r_class_alignment(path, text, stats):
    """ALIGN1: `__declspec(align(n)) struct S` -> `struct __declspec(align(n)) S` in front of a class definition or a
    forward declaration (see above). Same line count."""
    if 'align' not in text: return text
    def sub(m):
        head, end = m[5], m[6]
        if end == ';' and not re.fullmatch(r'\s*[A-Za-z_]\w*\s*', head): return m[0]   # elaborated type use
        if end == '{' and re.search(r'[=,]|\boperator\b', head.split(':')[0]): return m[0]
        stats['ALIGN1'] += 1
        ws = m[2] if '\n' in m[2] else ''
        return m[3] + m[4] + ' ' + m[1] + ws
    return ALIGN_HEAD.sub(sub, text)

# POST hooks of this module (STD2, REC1)
POST = []

# ---------------------------------------------------------------------------------------------------------- VA2
#
# `va_arg(ap, Index)` with an enum type: an enum argument passed through `...` is promoted to its underlying type,
# so in C++ the va_arg of the enum itself is undefined and GCC compiles it to a trap ("is promoted to 'int' when
# passed through '...'; if this code is reached, the program will abort"; S1b: Ent 0xADDR).
# MSVC read the int the caller pushed. VA2 reads that int and converts it: `(Index)va_arg(ap, int)`.

VA_ARG = re.compile(r'\bva_arg\s*\(\s*([A-Za-z_]\w*)\s*,\s*((?:[A-Za-z_]\w*::)*([A-Za-z_]\w*))\s*\)')
ENUM_NAME = re.compile(r'\benum\s+(?:class\s+|struct\s+)?([A-Za-z_]\w*)\s*(?::[^;{]*)?[;{]')
_ENUM_NAMES = None

def _enum_names(path):
    global _ENUM_NAMES
    if _ENUM_NAMES is not None: return _ENUM_NAMES
    names, root = set(), _roots(path)
    for d in ('src', 'include'):
        for dp, _, fs in core.walk_sorted(os.path.join(root, d)):
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
        if m[3] not in local and m[3] not in _enum_names(path): return m[0]
        stats['VA2'] += 1
        return '(%s)va_arg(%s, int)' % (m[2], m[1])
    return VA_ARG.sub(sub, text)

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

# ---------------------------------------------------------------------------------------------------------- FRIEND1
#
# `friend class unit;` in a class body, with unit declared nowhere before it: MSVC 2008 makes the friend's
# name visible to ordinary lookup in the enclosing namespace from there on, so the class goes on to use it
# (`unit* unit;`, `virtual void WorldAddedCallback(unit* world);`) and the class itself is defined
# further down. C++ (GCC) does not: the friend declaration names the class but does not make it visible, so every
# later use is "'unit' does not name a type" (the Ent viewers, unit, unit's
# `friend class unit;`). FRIEND1 forward-declares the class in front of the outermost class around the
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
    masked = FRIEND_MASK.sub(lambda m: re.sub(r'[^\n]', ' ', m[0]), text)
    spans = core.scope_spans(masked)
    header_views, prefix_views, adds, done = None, {}, {}, set()
    for m in found:
        if masked[m.start():m.end()] != m[0]: continue          # in a comment or a string
        key, name = m[1], m[2]
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
        adds.setdefault(at, []).append('%s %s;' % (d[1] if d else key, name))
        done.add((at, name))
    if not adds: return text
    out, pos = [], 0
    for at in sorted(adds):
        out.append(text[pos:at]); out.append(' %s /* fnv-nx FRIEND1 */ ' % ' '.join(adds[at])); pos = at
        stats['FRIEND1'] += len(adds[at])
    out.append(text[pos:])
    return ''.join(out)

RULES = [r_include_case, r_msvc_tokens, r_std_struct, r_vc9_containers, r_msvc_library_internals, r_anonymous_return_types, r_opaque_enums, r_enum_int_values, r_function_named_classes, r_anonymous_aggregate_members, r_local_anonymous_unions, r_pointer_zero_compares, r_global_allocators, r_keep_definitions, r_if_exists, r_sal_attributes, r_sdk_types, r_own_iunknown, r_exception_specs, r_placement_new, r_ctor_calls, r_c_data_linkage, r_goto_scalar_inits, r_goto_crossed_scopes, r_quad_returns, r_msc_version_values, r_sdk_guarded_copies, r_string_array_address, r_interlocked_fallbacks, r_win32_call_args, r_ref_cast_temporaries, r_temporaries_to_references, r_global_delete_noexcept, r_typeid_include, r_namespace_specializations, r_va_list, r_va_enum_args, r_image_statics, r_duplicate_int_overloads, r_placement_include, r_namespace_template_decls, r_dependent_typenames, r_forward_template_calls, r_instantiation_size_types, r_copy_ctor_base_casts, r_dependent_base_members, r_math_overloads, r_msvc_base_layout, r_vfptr_pad, r_msvc_empty_bases, r_class_alignment, r_friend_class_visibility]

# ==================================================================================================== stream S1-refs
#
# Rules of the S1-refs stream (references to temporaries, overload ambiguities, single-unit errors). They run after
# every rule above, in the order of REFS_RULES (appended to RULES at the end of this section).

# ---------------------------------------------------------------------------------------------------------- RREF2
#
# RREF1 for operators: `a != T(0)` where class T's own `bool operator!=(T& other)` takes a non-const reference. MSVC
# binds the temporary T(0) to it (alive to the end of the full expression); GCC refuses. RREF2 wraps the functional
# cast in fnvnx_lvalue where every member operator of that symbol the unit declares with a parameter of type T takes
# it by non-const reference (so no other overload could have been chosen).

RREF2_OPS = r'==|!=|\+=|-=|\*=|/=|\+|-|\*|/'
RREF2_DECL = re.compile(r'\boperator\s*(%s)\s*\(\s*((?:const\s+)?(?:volatile\s+)?[A-Za-z_]\w*(?:\s*const)?\s*[&*]?)'
                        r'\s*[A-Za-z_]\w*\s*\)' % RREF2_OPS)

def r_operator_temporaries(path, text, stats):
    """RREF2: `x OP T(args)` -> `x OP fnvnx_lvalue(T(args))` where the unit's member operators OP with a T parameter
    all take a non-const T& (see above)."""
    if 'operator' not in text and '#include' not in text: return text
    if not path.endswith('.cpp'): return text
    unit = '\n'.join(t for _, t in _closure(path, text)) if '#include' in text else COMMENT.sub(' ', text)
    kinds = {}                                       # (op, T) -> {non-const reference?}
    for m in RREF2_DECL.finditer(unit):
        p = re.sub(r'\s+', ' ', m[2]).strip()
        t = re.sub(r'\b(?:const|volatile)\b|[&*]', ' ', p).split()
        if not t: continue
        kinds.setdefault((m[1], t[0]), set()).add(p.endswith('&') and 'const' not in p.split())
    want = sorted(k for k, v in kinds.items() if v == {True})
    if not want: return text
    spans = core.scope_spans(text)
    edits = []
    for op, t in want:
        for m in re.finditer(r'(?<![=!<>+\-*/&|^%%])(%s)\s*(%s)\s*\(' % (re.escape(op), re.escape(t)), text):
            if 'function' not in [k for k, _ in core.scope_chain(spans, m.start())]: continue
            edits.append((m.start(2), _call_end(text, m.end() - 1)))
    if not edits: return text
    out, pos = [], 0
    for a, b in sorted(edits):
        if a < pos: continue
        out.append(text[pos:a]); out.append('fnvnx_lvalue(' + text[a:b] + ')'); pos = b
        stats['RREF2'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- ADDR1
#
# MSVC takes the address of a class rvalue (an extension, warning C4238): `(const Vec*)&_mm_andnot_ps(m, v)` hands
# the callee the address of the returned __m128 temporary, alive to the end of the full expression. GCC refuses the
# address of an rvalue. ADDR1 spells the operand fnvnx_lvalue(...): the same temporary as an lvalue, for the same
# lifetime. Only intrinsic calls (`_mm_*`), only as the operand of a unary `&` (after `(`, `,`, `=`, `return` or a
# pointer cast).

ADDR1_SITE = re.compile(r'(?:(?<=[(,=])|(?<=\breturn)|(?<=\*\)))(\s*&\s*)(_mm_\w+\s*\()')

def r_address_of_temporaries(path, text, stats):
    """ADDR1: `&_mm_f(args)` (the address of a returned vector temporary) -> `&fnvnx_lvalue(_mm_f(args))`."""
    if '&' not in text or '_mm_' not in text: return text
    out, pos = [], 0
    for m in ADDR1_SITE.finditer(text):
        if m.start() < pos: continue
        if m[0].lstrip().startswith('&&'): continue
        close = _call_end(text, m.end() - 1)
        out.append(text[pos:m.start(2)]); out.append('fnvnx_lvalue(' + text[m.start(2):close] + ')'); pos = close
        stats['ADDR1'] += 1
    if not out: return text
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- LANE1
#
# SSE1 turns a lane array `v.m128_f32` into `((float*)&(v))`, a pointer prvalue. Where the decomp takes the address
# of the whole array (`(&v.m128_f32)[0]`, `(0)[&v.m128_f32]`: a float[4] lvalue that decays to the first lane's
# address) that gives `&((float*)&(v))`, the address of an rvalue. LANE1 writes the array pointer itself:
# `((float(*)[sizeof(v) / sizeof(float)])&(v))`, the same address with the same type as MSVC's `&v.m128_f32`.

LANE1_SITE = re.compile(r'&\s*\(\(\s*((?:unsigned\s+|signed\s+)?(?:float|double|int|char|short|long\s+long|__int64|'
                        r'__int32|__int16|__int8))\s*\*\s*\)\s*&\s*\(')

def r_lane_array_address(path, text, stats):
    """LANE1: `&((T*)&(v))` (SSE1's lane array, its address taken) -> `((T(*)[sizeof(v) / sizeof(T)])&(v))`."""
    if 'm128' not in text and '*)&(' not in text: return text
    out, pos = [], 0
    for m in LANE1_SITE.finditer(text):
        if m.start() < pos: continue
        inner_end = _call_end(text, m.end() - 1)                 # past `(v)`
        if text[inner_end:inner_end + 1] != ')': continue
        after = text[inner_end + 1:inner_end + 40].lstrip()
        if after.startswith(('[', '.', '->')): continue          # `&((T*)&(v))[i]`: a lane's address, fine
        t, v = m[1], text[m.end() - 1:inner_end]
        out.append(text[pos:m.start()])
        out.append('((%s(*)[sizeof%s / sizeof(%s)])&%s)' % (t, v, t, v)); pos = inner_end + 1
        stats['LANE1'] += 1
    if not out: return text
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- STD2
#
# A unit defines a function of namespace std at file scope, qualified: unit.cpp's `double std::abs(float)`
# (0xADDR, `?abs@std@@YANM@Z`, a float -> double helper the game linked in). DECL1 drops the decomp's
# `namespace std { double abs(float); }` (libstdc++ declares `float std::abs(float)`), so GCC refuses the definition
# ("should have been declared inside 'std'", "ambiguating new declaration"). STD2 renames the definition to the file
# scope name fnvnx_std_<name> and, in a POST hook, the unit's recipe symbol `?<name>@std@@<sig>` to
# `?fnvnx_std_<name>@@<sig>` (the same signature, MSVC's decoration of the new name). Callers bind the address
# (their own declarations, e.g. `float FUN_ADDR(float)`, keep their names); equiv's Fingerprinter takes the
# renamed definition from the prepared recipe. Only where the unit's recipe defines `?<name>@std@@...` at the
# address of the definition's `// @fnv` tag.

STD2_DEF = re.compile(r'//\s*@fnv\s+(0x[0-9a-fA-F]{8})[^\n]*\n((?:[A-Z_]+\s+)*[^\n;{}()]*?)\bstd::([A-Za-z_]\w*)\s*\(')
# (unit source, address, old symbol, new symbol), filled while the tree is prepared.
STD2_RENAMES = set()
_STD2_FNS = None

def _std2_functions(path):
    """{absolute unit source: {address: symbol}} of the recipe's definitions in namespace std."""
    global _STD2_FNS
    if _STD2_FNS is not None: return _STD2_FNS
    import json
    _STD2_FNS = {}
    root, uj = core._units_json(path)
    if uj:
        for u in json.load(open(uj))['units']:
            fns = {f['address']: f['symbol'] for f in u.get('functions', []) if '@std@@' in f.get('symbol', '')}
            if fns: _STD2_FNS[os.path.normpath(os.path.join(root, u['source']))] = (u['source'], fns)
    return _STD2_FNS

def r_std_definitions(path, text, stats):
    """STD2: a file-scope `R std::f(...)` definition the recipe decorates `?f@std@@...` -> `R fnvnx_std_f(...)`
    (and the recipe's symbol renamed by the POST hook rename_std_definitions; see above)."""
    if 'std::' not in text or '@fnv' not in text or not path.endswith('.cpp'): return text
    entry = _std2_functions(path).get(os.path.normpath(os.path.abspath(path)))
    if not entry: return text
    src, fns = entry
    spans = None
    out, pos = [], 0
    for m in STD2_DEF.finditer(text):
        addr, name = m[1].lower(), m[3]
        sym = fns.get(addr)
        if not sym or not sym.startswith('?%s@std@@' % name): continue
        if spans is None: spans = core.scope_spans(text)
        if core.scope_chain(spans, m.start(3)): continue              # not at file scope
        a = m.start(3) - len('std::')
        out.append(text[pos:a]); out.append('fnvnx_std_' + name); pos = m.end(3)
        STD2_RENAMES.add((src, addr, sym, '?fnvnx_std_%s@@%s' % (name, sym[len('?%s@std@@' % name):])))
        stats['STD2'] += 1
    if not out: return text
    out.append(text[pos:])
    return ''.join(out)

def rename_std_definitions(out_root, stats, files=None):
    """POST hook: STD2's recipe renames in <out_root>/build/units.json."""
    import json
    if not STD2_RENAMES: return
    p = os.path.join(out_root, 'build', 'units.json')
    if not os.path.exists(p): return
    data = json.load(open(p))
    by_src = {}
    for src, addr, old, new in STD2_RENAMES:
        by_src.setdefault(src, {})[(addr, old)] = new
    n = 0
    for u in data['units']:
        for fn in u.get('functions', []):
            new = by_src.get(u['source'], {}).get((fn['address'], fn['symbol']))
            if new: fn['symbol'] = new; n += 1
    if n:
        stats['STD2-DEF'] += n
        json.dump(data, open(p, 'w'))
        if files is not None: files['STD2-DEF'] += 1

POST.append(rename_std_definitions)

# ---------------------------------------------------------------------------------------------------------- COND1
#
# `c ? false : x.f(a)` where f returns a class G that converts both ways with bool (`G(bool)` and `operator bool()`),
# a stand-in for a byte-sized boolean result: GCC refuses the conditional ("operands to '?:' have different types
# 'bool' and 'G' ... each type can be converted to the other"); MSVC accepted it and converted the literal to G: its
# code tests the result byte directly (`cmp byte [eax], 0; je`, unit 0xADDR; the bool conversion of the G
# operand instead adds `setne al; test al, al`, measured under MSVC 2008). COND1 writes that conversion:
# `c ? G(false) : x.f(a)`. Only where the other operand is the literal `true` / `false`, the G operand ends in a
# call of a name the unit declares returning G with as many parameters as the call has arguments, and every
# declaration of that name with that many parameters returns a class with an `operator bool()`, exactly one of them
# with a non-explicit bool constructor (unit.cpp: Ent, and Ent without one; with an explicit one
# the conditional is bool), and the G operand is a postfix expression ending in that call (`!x.f(a)` is bool).

COND1_CLASS = re.compile(r'\b(?:struct|class)\s+([A-Za-z_]\w*)\s*(?::[^{;]*)?\{')
COND1_LIT = re.compile(r'\?\s*(false|true)\s*:(?!:)')

def _cond1_arity(args):
    args = args.strip()
    if not args or args == 'void': return 0
    depth, n = 0, 1
    for ch in args:
        if ch in '(<[{': depth += 1
        elif ch in ')>]}': depth -= 1
        elif ch == ',' and depth == 0: n += 1
    return n

def _cond1_operand_end(text, i):
    """End of the conditional's third operand starting at i (stops at `)`, `,`, `;` or `?`/`:` at depth 0)."""
    depth = 0
    for k in range(i, len(text)):
        ch = text[k]
        if ch in '([{': depth += 1
        elif ch in ')]}':
            if depth == 0: return k
            depth -= 1
        elif depth == 0 and ch in ',;?:': return k
    return len(text)

def r_conditional_bool_gates(path, text, stats):
    """COND1: `c ? false : x.f(a)` with f returning a class G convertible both ways with bool -> `c ? G(false) :
    x.f(a)`, the conversion MSVC chose (see above)."""
    if '?' not in text or 'operator bool' not in text and '#include' not in text: return text
    if not COND1_LIT.search(text): return text
    unit = '\n'.join(t for _, t in _closure(path, text)) if '#include' in text else COMMENT.sub(' ', text)
    gates, booly = set(), set()                    # classes G(bool) + operator bool; classes with operator bool
    for m in COND1_CLASS.finditer(unit):
        g = m[1]
        body = unit[m.end() - 1:core.match_brace(unit, m.end() - 1)]
        if re.search(r'\boperator\s+bool\s*\(\s*\)', body):
            booly.add(g)
            if re.search(r'(?<!explicit )(?<!explicit)\b%s\s*\(\s*bool\b' % g, re.sub(r'\bexplicit\s+', 'explicit ', body)):
                gates.add(g)                       # an explicit G(bool) makes the conditional bool (V1 C2)
    if not gates: return text
    rets = {}                                      # (name, arity) -> {return type}
    for m in re.finditer(r'(?:^|[;{}\s])((?:const\s+)?[A-Za-z_]\w*[\s*&]*?)\s*\b([A-Za-z_]\w*)\s*\(([^()]*)\)\s*'
                         r'(?:const\s*)?[;{]', unit):
        rets.setdefault((m[2], _cond1_arity(m[3])), set()).add(m[1].strip())
    out, pos = [], 0
    for m in COND1_LIT.finditer(text):
        if m.start() < pos: continue
        a = m.end()
        while a < len(text) and text[a] in ' \t\n': a += 1
        b = _cond1_operand_end(text, a)
        x = text[a:b].rstrip()
        call = None
        for cm in re.finditer(r'\b([A-Za-z_]\w*)\s*\(', x):
            if _call_end(x, cm.end() - 1) == len(x): call = cm; break
        if call is None: continue
        # the operand is a postfix expression ending in the call (`x.f(a)`, `p->q.f(a)`, `f(a)`), not `!x.f(a)` or
        # `a + x.f(a)`, whose type is not G (V1 C3)
        if not re.match(r'^(?:::)?(?:[A-Za-z_]\w*(?:<[^<>;{}()]*>)?\s*(?:\[[^\[\]]*\]\s*)*(?:\.|->|::)\s*)*$',
                        x[:call.start()]):
            continue
        args = x[call.end():len(x) - 1]
        r = rets.get((call[1], _cond1_arity(args)), set())
        if len(r & gates) != 1 or not r <= booly: continue
        g = next(iter(r & gates))
        out.append(text[pos:m.start(1)]); out.append('%s(%s)' % (g, m[1])); pos = m.end(1)
        stats['COND1'] += 1
    if not out: return text
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- RSTR1
#
# A function-local `T* __restrict v` passed by name to a parameter `const X&` (unit.cpp:
# `unit<Ent*>::AddBack(const T& val)` called with a restrict-qualified local). MSVC ignores __restrict for
# reference binding and binds the reference to the local itself; GCC keeps the qualifier on the lvalue and refuses
# ("binding reference of type 'T* const&' to 'T* __restrict__' discards qualifiers"). RSTR1 writes the argument as
# fnvnx_unrestrict(v): under GCC the same object as a T* lvalue (fnvnx_compat.h), under MSVC `(v)`
# (fnvnx_equiv_fi.h), so the reference still binds the local. Only arguments that are exactly the variable, of a
# call whose name the unit declares with a `const ...&` parameter at that position.

RSTR1_DECL = re.compile(r'\*\s*__restrict(?:__)?\s+([A-Za-z_]\w*)\s*(?=[=;,)])')
RSTR1_PROTO = re.compile(r'\b([A-Za-z_]\w*)\s*\(([^()]*)\)\s*(?:const\s*)?[;{]')

def _rstr1_params(params):
    out, depth, cur = [], 0, ''
    for ch in params:
        if ch in '(<[': depth += 1
        elif ch in ')>]': depth -= 1
        if ch == ',' and depth == 0: out.append(cur); cur = ''
        else: cur += ch
    out.append(cur)
    return [x.strip() for x in out]

def _rstr1_call(text, i):
    """(callee name, argument index) of the argument starting after text[i] (`(` or `,`), or None."""
    depth, k, idx = 0, i, 0
    while k >= 0:
        ch = text[k]
        if ch in ')]': depth += 1
        elif ch in '([':
            if depth == 0:
                if ch != '(': return None
                m = re.search(r'([A-Za-z_]\w*)\s*$', text[max(0, k - 200):k])
                return (m[1], idx) if m else None
            depth -= 1
        elif ch == ',' and depth == 0 and k != i: idx += 1
        elif ch in ';{}' and depth == 0: return None
        k -= 1
    return None

def r_restrict_reference_args(path, text, stats):
    """RSTR1: a restrict-qualified local passed to a `const X&` parameter -> fnvnx_unrestrict(v) (see above)."""
    if '__restrict' not in text or not path.endswith('.cpp'): return text
    decls = [m for m in RSTR1_DECL.finditer(text)]
    if not decls: return text
    unit = '\n'.join(t for _, t in _closure(path, text)) if '#include' in text else COMMENT.sub(' ', text)
    cref = {}                                      # name -> {parameter index taking a const reference}
    for m in RSTR1_PROTO.finditer(unit):
        for j, prm in enumerate(_rstr1_params(m[2])):
            if re.match(r'const\b[^&*]*&(?!&)', prm): cref.setdefault(m[1], set()).add(j)
    spans = core.scope_spans(text)
    funcs = [(o, c) for o, c, k, _ in spans if k == 'function']
    edits = set()
    for d in decls:
        body = [(o, c) for o, c in funcs if o < d.start() < c]
        if not body: continue
        end = min(c for o, c in body)
        v = d[1]
        for u in re.finditer(r'([(,])\s*(%s)\s*(?=[),])' % re.escape(v), text[:end]):
            if u.start() < d.end(): continue
            call = _rstr1_call(text, u.start(1))
            if call and call[1] in cref.get(call[0], ()): edits.add((u.start(2), u.end(2)))
    if not edits: return text
    out, pos = [], 0
    for a, b in sorted(edits):
        out.append(text[pos:a]); out.append('fnvnx_unrestrict(' + text[a:b] + ')'); pos = b
        stats['RSTR1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- NEW5
#
# A class whose only scalar operator delete is the placement form `operator delete(void*, void*)` (the decomp's copies
# of Ent's placement allocator and of Ent's Ent placement pair, without the usual forms) and that has a
# virtual destructor. The deleting destructor needs a usual deallocation function: class-scope lookup finds only the
# placement one, so GCC rejects the class (and every class derived from it: "no suitable 'operator delete'"). MSVC 2008
# accepted it and called the global ::operator delete(void*) from the scalar deleting destructor (??_G of the
# kd-tree raycaster helper at 0xADDR is 31 bytes: vfptr store, `test [esp+8],1`, `push esi; call; add esp,4` -
# a one-argument cdecl call, i.e. ??3@YAXPAX@Z, the class has no one-argument delete of its own). NEW5 puts
# FNVNX_GLOBAL_DELETE first in such a class (with `public:` / `private:` around it in a `class`, so no member changes
# its access): under GCC a static `operator delete(void*)` that forwards to ::operator delete, the function MSVC
# called; under MSVC (tools/equiv) it is empty. Only classes without base classes (class-scope lookup then sees
# nothing but the class's own placement form, so MSVC's choice is the global function, not a base's) and only the
# scalar form (GCC needs the array form for `delete[]` expressions only, and the classes NEW5 edits declare none, so
# `delete[]` on them already reaches the global one). Decomp-wide: 3 classes in 3 files (S1-refs).

NEW5_DELETE = re.compile(r'\boperator\s+delete\s*(\[\s*\])?\s*\(([^;{}()]*)\)')
NEW5_PLACEMENT = re.compile(r'^\s*void\s*\*\s*\w*\s*,\s*void\s*\*\s*\w*\s*$')
NEW5_NEW = re.compile(r'\boperator\s+new\s*\(\s*(?:std::)?(?:size_t|unsigned(?:\s+int)?)(?:\s+[A-Za-z_]\w*)?\s*\)')
NEW5_HEAD = re.compile(r'^(class|struct)\s+(?:\w+\s+)*?([A-Za-z_]\w*)\s*(:(?!:).*)?$', re.S)

def r_placement_only_delete(path, text, stats):
    """NEW5: FNVNX_GLOBAL_DELETE in a base-less class with a virtual destructor whose only scalar operator delete is
    the placement form (void*, void*): the global ::operator delete(void*) its MSVC deleting destructor called."""
    if 'operator delete' not in text or 'virtual' not in text: return text
    spans, dels = None, {}
    for m in NEW5_DELETE.finditer(text):
        if m[1]: continue
        if spans is None: spans = core.scope_spans(text)
        inner = None
        for sp in spans:
            if sp[0] > m.start(): break
            if sp[0] < m.start() < sp[1]: inner = sp
        if not inner or inner[2] != 'class': continue
        dels.setdefault(inner, []).append(m[2])
    inserts = []
    for (o, c, k, h), params in dels.items():
        if not all(NEW5_PLACEMENT.match(p) for p in params): continue
        head = core.HEAD_DECOR.sub(' ', core.TEMPLATE_PREFIX.sub('', h)).strip()
        hm = NEW5_HEAD.match(head)
        if not hm or hm[3]: continue                                   # no base classes
        if not re.search(r'\bvirtual\s+~\s*%s\s*\(' % re.escape(hm[2]), text[o:c]): continue
        # the class's own non-placement operator new (Ent's from unit): MSVC found no matching delete
        # for it (C4291), so a throwing constructor frees nothing; with FNVNX_GLOBAL_DELETE GCC would free through it
        # (V1 N2). FNVNX_NEW5_TAG makes that operator new a placement form under GCC (a defaulted tag parameter; empty
        # under MSVC), which has no matching delete: nothing is freed, as on MSVC. Only an inline one (its decorated
        # name is bound nowhere); a class whose operator new has no body here is left as it is.
        tags, ok = [], True
        for nm in NEW5_NEW.finditer(text, o, c):
            if not re.match(r'\s*\{', text[nm.end():]): ok = False; break
            tags.append((nm.end() - 1, ' FNVNX_NEW5_TAG'))
        if not ok: continue
        inserts += tags
        inserts.append((o + 1, ' public: FNVNX_GLOBAL_DELETE private:' if hm[1] == 'class'
                        else ' FNVNX_GLOBAL_DELETE'))
    if not inserts: return text
    out, pos = [], 0
    for at, s in sorted(inserts):
        out.append(text[pos:at]); out.append(s + ('' if s == ' FNVNX_NEW5_TAG' else ' /* fnv-nx NEW5 */')); pos = at
        stats['NEW5'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- NSCALL1
#
# A namespace N of a unit declares its own C++ prototype of a CRT name (`namespace fnv_ADDR { int strcpy_s(char*,
# unsigned int, const char*); int _stricmp(const char*, const char*); }`), bound by the recipe to the game's wrapper
# (`?strcpy_s@fnv_ADDR@@YAHPADIPBD@Z` -> 0xADDR). DECL1 keeps it (N::strcpy_s is not ::strcpy_s). The unit
# saw no other declaration of the name on MSVC, so every unqualified call in N went to N::name. On the target the
# shim (force-included fnvnx_crt.h) and the headers DECL1 adds declare ::name as well; ordinary lookup inside N still
# stops at N::name, but a class-type argument (`strcpy_s(buf, 260, unit)` with a Ent that converts to
# const char*) brings in ::name by argument-dependent lookup, and GCC finds the call ambiguous (size_t is unsigned
# long). NSCALL1 qualifies the unqualified calls of such a name inside N as `N::name(`: qualified lookup finds exactly
# N::name, the function MSVC called. Only where the unit's units.json symbols bind the name only in N (no global
# `?name@@...` / `_name`, no other scope): MSVC emitted no call to another function of that name from this unit; and
# only where no class in the unit declares a member of that name (a member call would not be N::name). <math.h> names
# are left to DECL1 (their arguments are arithmetic: no argument-dependent lookup).

NSCALL_CALL = re.compile(r'(?<![\w.>:~])(\s*)([A-Za-z_]\w*)(\s*\()')

def r_namespace_crt_calls(path, text, stats):
    """NSCALL1: unqualified calls of a namespace's own C++ prototype of a CRT / Win32 name, bound only in that
    namespace by the recipe -> `N::name(...)` (see above)."""
    if 'namespace' not in text: return text
    if core.PLATFORM is None: core.PLATFORM = core.platform_names()
    platform = core.PLATFORM
    if not any(n in text for n in platform): return text
    syms = _unit_symbols(path)
    if not syms: return text
    masked = _mask_code(text)
    spans = None
    want = {}                                           # namespace head -> {name}
    for m in core.PROTO.finditer(masked):
        name = m[1]
        if name not in platform or name in core.MACRO_NAMES or name in core.MATH_NAMES: continue
        if spans is None: spans = core.scope_spans(text)
        chain = core.scope_chain(spans, m.start())
        if not chain or any(k != 'namespace' for k, _ in chain): continue
        heads = [re.sub(r'^(?:inline\s+)?namespace\s+', '', h).strip() for _, h in chain]
        if any(not re.fullmatch(r'[A-Za-z_]\w*', h) or h == 'std' for h in heads): continue
        if re.search(r'\bextern\s*"C"', text[m.start():m.end()]): continue
        scope = ''.join('%s@' % h for h in reversed(heads))
        bound = [k for k in syms if re.match(r'^\?%s@|^_%s(?:@\d+)?$' % (re.escape(name), re.escape(name)), k)]
        if not bound or any(not k.startswith('?%s@%s@' % (name, scope)) for k in bound): continue
        want.setdefault('::'.join(heads), set()).add(name)
    if not want: return text
    # a class that declares a member of that name: leave the name alone
    for o, c, k, h in spans:
        if k != 'class': continue
        for nm in [n for names in want.values() for n in names]:
            if re.search(r'(?<![\w.>:])%s\s*\(' % re.escape(nm), masked[o:c]):
                for names in want.values(): names.discard(nm)
    edits = []
    for o, c, k, h in spans:
        if k != 'function': continue
        chain = core.scope_chain(spans, o)
        nss = [re.sub(r'^(?:inline\s+)?namespace\s+', '', hh).strip() for kk, hh in chain if kk == 'namespace']
        names = want.get('::'.join(nss))
        if not names or any(kk == 'function' for kk, _ in chain): continue   # only outermost function bodies
        for m in NSCALL_CALL.finditer(masked, o + 1, c):
            if m[2] not in names: continue
            before = masked[max(0, m.start() - 40):m.start()].rstrip()
            if re.search(r'(?:\boperator|::|\.|->)$', before): continue
            if re.search(r'(?:(?<!\w)[A-Za-z_]\w*|(?<=[\w\s])[*&]|>)$', before) and \
               not re.search(r'(?<![\w.>])(?:return|else|do|case|throw)$', before): continue   # a declaration
            edits.append((m.start(2), '::'.join(nss) + '::'))
    if not edits: return text
    out, pos = [], 0
    for at, q in sorted(set(edits)):
        out.append(text[pos:at]); out.append(q); pos = at
        stats['NSCALL1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- FINL1
#
# A `__forceinline` declaration without a body (`__forceinline ~Ent();`, `__forceinline unit&
# getColumn(int i);`) of a function the unit never defines: its body is in another unit. MSVC compiled the call as an
# ordinary call (it cannot inline what it has not seen). The shim's `__forceinline` is `inline
# __attribute__((always_inline))`, and GCC refuses to compile such a call when it optimizes ("inlining failed in call
# to 'always_inline' ...: function body not available": tile_ob1_variant, three Ent _rtm_variant agents,
# blk_ADDR_od_ob1; only the real compile fails, the -fsyntax-only census does not see it). FINL1 drops the
# keyword from that declaration: the same external call. Only in .cpp files, only declarations ending in `;` at file
# scope or in a class C, and only where the unit (the file and its headers) has no body of that name with the same
# parameter types and const qualification: `C::name(...) {` or a body inside C's braces for a member, any body for a free function (calls
# after `.`, `->`, `:` and `,` - member accesses, initializer lists - are not bodies).

FINL1_DECL = re.compile(r'\b__forceinline\s+((?:[\w:<>,*&~\s]|\[\])*?)(~?[A-Za-z_]\w*)\s*\(')
FINL1_TAIL = r'\s*(?:const\s*)?(?:throw\s*\([^)]*\)\s*)?(?:noexcept\s*)?'

def _finl1_sig(params):
    """Parameter types without names and defaults: `const T& a, int n = 0` -> ('const T&', 'int')."""
    params = params.strip()
    if not params or params == 'void': return ()
    out, depth, cur = [], 0, ''
    for ch in params + ',':
        if ch in '(<[': depth += 1
        elif ch in ')>]': depth -= 1
        if ch == ',' and depth == 0:
            p = re.sub(r'\s*=.*$', '', cur, flags=re.S).strip()
            q = re.sub(r'(?<=[*&>\s])[A-Za-z_]\w*\s*(\[[^\]]*\])?$', '', p).strip()
            if q and q not in ('const', 'unsigned', 'signed', 'struct', 'class') and not q.endswith('::'): p = q
            out.append(re.sub(r'\s+', '', p))
            cur = ''
        else: cur += ch
    return tuple(out)

def _finl1_defined(unit, name, sig, cls, cls_body):
    """A body of name(sig) in the unit: `cls::name(...) {` (or `: init {`), or inside cls_body; for a free function
    (cls None) any `name(...) {` that is not a member access or an initializer call."""
    spots = []
    if cls:
        spots += [(unit, m) for m in re.finditer(r'\b%s\s*(?:<[^<>;{}()]*>)?\s*::\s*%s\s*\(' % (re.escape(cls),
                  re.escape(name)), unit)]
        spots += [(cls_body, m) for m in re.finditer(r'(?<![\w~.:>,])%s\s*\(' % re.escape(name), cls_body)]
    else:
        spots += [(unit, m) for m in re.finditer(r'(?<![\w~.:>,])%s\s*\(' % re.escape(name), unit)]
    for t, m in spots:
        e = _call_end(t, m.end() - 1)
        if _finl1_sig(t[m.end():e - 1]) + (('const',) if re.match(r'\s*const\b', t[e:]) else ()) != sig: continue
        if re.match(FINL1_TAIL + r'(?:\{|:(?!:))', t[e:]): return True
    return False

def r_forceinline_declarations(path, text, stats):
    """FINL1: `__forceinline` on a body-less declaration of a function the unit does not define -> dropped (the
    ordinary call MSVC made; see above)."""
    if '__forceinline' not in text or not path.endswith('.cpp'): return text
    unit, spans = None, None
    edits = []
    for m in FINL1_DECL.finditer(text):
        close = _call_end(text, m.end() - 1)
        tail = re.match(FINL1_TAIL + r'(=\s*0\s*)?;', text[close:])
        if not tail or tail[1]: continue                       # a definition, or a pure virtual
        name = m[2]
        sig = _finl1_sig(text[m.end():close - 1]) + (('const',) if re.match(r'\s*const\b', text[close:]) else ())
        if unit is None:
            unit = '\n'.join(t for _, t in _closure(path, text)) if '#include' in text else COMMENT.sub(' ', text)
            spans = core.scope_spans(text)
        inner = None
        for sp in spans:
            if sp[0] > m.start(): break
            if sp[0] < m.start() < sp[1]: inner = sp
        cls, body = None, ''
        if inner is not None:
            if inner[2] != 'class': continue                         # a declaration inside a function or namespace
            head = re.sub(r'\btemplate\s*<[^{};]*?>\s*(?=(?:class|struct|union)\b)', ' ', inner[3] or '')
            # the class's own name, not a template parameter's (`template <class KEYD3, ...> class Ent`:
            # unit, unit lost the keyword on members their units define out of class)
            # `struct __declspec(align(16)) Ent`: the declspec's own parentheses nest (unit 0xADDR)
            hm = re.search(r'\b(?:class|struct|union)\s+(?:__declspec\s*\((?:[^()]|\([^()]*\))*\)\s*)*([A-Za-z_]\w*)', head)
            if not hm: continue
            cls, body = hm[1], text[inner[0]:inner[1]]
            if name.lstrip('~') != name and name[1:] != cls: continue
        if _finl1_defined(unit, name, sig, cls, body): continue
        edits.append((m.start(), m.start(1)))
    if not edits: return text
    out, pos = [], 0
    for a, b in edits:
        out.append(text[pos:a]); out.append('/* fnv-nx FINL1: __forceinline */ '); pos = b
        stats['FINL1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- EBO2
#
# A class C that introduces its own vfptr (it declares a virtual function; its only base is not polymorphic) with an
# empty base B: MSVC puts the vfptr at 0 and B after it, at +4; the Itanium ABI puts the empty B at offset 0, under the
# vptr. The members of C are at the same offsets either way (B takes no room), but B's address differs. That matters
# for the decomp's method-only stand-ins (unit.cpp: `struct Ent : Ent { virtual
# ~Ent(); int unit; }` whose base methods are bound to Ent's code in the exe): the exe
# passes this+4 to them (0xADDR -> IsEmpty 0xADDR; the pristine unit matches the exe byte for byte), the build
# passed this+0, the vptr, as the list (S13b layout-offset). EBO2 writes FNVNX_EBO2 first in B's body: under GCC a
# zero-length array (B is no longer empty, still 0 bytes, so it goes after the vptr and the members keep their
# offsets), empty under MSVC. Only where B is defined in the file without bases and without data or virtual
# functions, every class of the file that derives from B has it as its only base, at least one of them introduces a
# vfptr, and B is never used as an object (its name only as a base, in `B*` / `B&`, `B::`, or in its own body): the
# zero size changes nothing else. Only in .cpp files (a header's other includers are not seen).

FNVNX_EBO2_USE = re.compile(r'\s*(?:[*&]|::|\))')

def r_vfptr_empty_base(path, text, stats):
    """EBO2: FNVNX_EBO2 in an empty base B of a class introducing its own vfptr: B after the vptr, as MSVC (above)."""
    if 'virtual' not in text or ':' not in text or not path.endswith('.cpp'): return text
    defs, derived = {}, collections.defaultdict(list)
    for m in EBO_CLASS.finditer(text):
        defs.setdefault(m[1], []).append(m)
        if m[2]:
            for b in _split_bases(m[2]):
                b = re.sub(r'^(?:(?:public|protected|private)\s+)+', '', b).strip()
                derived[b].append(m)
    edits = []
    for b, ms in derived.items():
        if not re.match(r'^[A-Za-z_]\w*$', b) or len(defs.get(b, ())) != 1: continue
        bd = defs[b][0]
        if bd[2]: continue                                          # B has bases of its own
        bo = bd.end() - 1
        bc = core.match_brace(text, bo)
        if not _ebo_empty_class(text, bo, bc): continue
        intro = False
        for m in ms:
            if len(_split_bases(m[2])) != 1 or re.search(r'\bvirtual\b', m[2]): break
            o = m.end() - 1
            if re.search(r'\bvirtual\b', _flat_body(text, o, core.match_brace(text, o))): intro = True
        else:
            if not intro: continue
            heads = {mm.start() for mm in ms} | {bd.start()}
            bad = False
            for u in re.finditer(r'(?<![\w~:])%s\b' % re.escape(b), text):
                if bo < u.start() < bc: continue                          # B's own body (its constructors)
                pre = text[max(0, u.start() - 40):u.start()]
                if any(h <= u.start() < h + 400 and text.find('{', h) > u.start() for h in heads): continue
                if FNVNX_EBO2_USE.match(text, u.end()): continue           # B*, B&, B::, (B)
                if re.search(r'(?:class|struct)\s+$', pre): continue       # a forward declaration / elaborated use
                bad = True; break
            if bad: continue
            edits.append(bo + 1)
    if not edits: return text
    for at in sorted(edits, reverse=True):
        text = text[:at] + ' FNVNX_EBO2 /* fnv-nx EBO2 */' + text[at:]
    stats['EBO2'] += len(edits)
    return text

# ---------------------------------------------------------------------------------------------------------- EBO3
#
# An empty base that is not the first base (S1-layout, S13b layout-offset). In `struct Node : Ent, Ent
# {}` with Ent four ints and Ent empty, MSVC 2008 puts Ent after Ent, at +0x10
# (/d1reportSingleClassLayout: sizeof(Node) 16, the base at 16), the Itanium ABI at 0. unit.cpp
# 0xADDR calls Ent's method bound to the exe (0xADDR) with node+0x10 on x86 and node+0 natively; the
# pristine unit matches the exe. EBO3 writes FNVNX_EBO2 first in that base's body (GCC: a zero-length array, so the
# base is not empty, still 0 bytes, and goes after the bases before it; empty under MSVC). Only where the base E is
# defined in the .cpp without bases, data or virtual functions, it follows a base of the same class that is defined
# in the file and not empty (two empty bases are EBO1's), no class has E as a virtual base, and E is never used as an object (EBO2's
# test: its name only as a base, in `E*` / `E&`, `E::`, or in its own body), so the zero size changes nothing else.

def r_later_empty_base(path, text, stats):
    """EBO3: FNVNX_EBO2 in an empty base that follows a non-empty base: after the bases before it, as MSVC (above)."""
    if ',' not in text or ':' not in text or not path.endswith('.cpp'): return text
    defs, uses = {}, collections.defaultdict(list)
    for m in EBO_CLASS.finditer(text):
        defs.setdefault(m[1], []).append(m)
        if m[2]:
            bs = [re.sub(r'^(?:(?:public|protected|private|virtual)\s+)+', '', b).strip() for b in _split_bases(m[2])]
            for i, b in enumerate(bs): uses[b].append((m, i, bs))
    def empty(b):
        if not re.match(r'^[A-Za-z_]\w*$', b) or len(defs.get(b, ())) != 1: return None
        bd = defs[b][0]
        if bd[2]: return None
        bo = bd.end() - 1
        bc = core.match_brace(text, bo)
        if 'FNVNX_EBO2' in text[bo:bc]: return None
        return (bd, bo, bc) if _ebo_empty_class(text, bo, bc) else None
    edits = []
    for b, us in sorted(uses.items()):
        e = empty(b)
        if e is None: continue
        bd, bo, bc = e
        if any(re.search(r'\bvirtual\b', m[2]) for m, _, _ in us): continue
        if not any(i > 0 and any(len(defs.get(x, ())) == 1 and empty(x) is None for x in bs[:i]) for m, i, bs in us):
            continue                                                      # no earlier base of the file that has data
        heads = {mm.start() for mm, _, _ in us} | {bd.start()}
        bad = False
        for u in re.finditer(r'(?<![\w~:])%s\b' % re.escape(b), text):
            if bo < u.start() < bc: continue
            pre = text[max(0, u.start() - 40):u.start()]
            if any(h <= u.start() < h + 400 and text.find('{', h) > u.start() for h in heads): continue
            if FNVNX_EBO2_USE.match(text, u.end()): continue
            if re.search(r'(?:class|struct)\s+$', pre): continue
            bad = True; break
        if bad: continue
        edits.append(bo + 1)
    if not edits: return text
    for at in sorted(edits, reverse=True):
        text = text[:at] + ' FNVNX_EBO2 /* fnv-nx EBO3 */' + text[at:]
    stats['EBO3'] += len(edits)
    return text

# --------------------------------------------------------------------------------------------- class scanner
#
# d22_mask: a text with comments and literals blanked, for the rules below.

D22_MASK = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|//[^\n]*|/\*.*?(?:\*/|\Z)', re.S)

def d22_mask(text):
    """text with comments and string / character literals blanked (same length, line breaks kept)"""
    return D22_MASK.sub(lambda m: re.sub(r'[^\n]', ' ', m[0]), text)


# ---------------------------------------------------------------------------------------------------------- VSPLIT1
#
# A virtual declaration with several declarators, `virtual void v000(), v004(), v008();` (EQ, farm baseline 588863:
# unit.cpp's Ent v000..v228, unit.cpp's Ent v0..v34, unit,
# unit: 52 sites in 23 files). D22 / D26 took the whole list for one virtual: FNVNX_D22 dropped every
# placeholder of it under GCC (0xADDR read [x2, #8] where the exe reads [edx + 0x22c]), and the virtual counts that
# D22-SLOTK and the call-only gates use were off. VSPLIT1 writes one declaration per declarator, each with the same
# decl-specifiers (`virtual void v000(); virtual void v004(); ...`): the same declarations in C++, so the same objects
# under MSVC. Only declarators without an initializer or body (a declarator list ends with `;`), at any class depth.
VSPLIT1_HEAD = re.compile(r'\bvirtual\b')

def _vsplit_top(s, ch):
    """indices of ch at parenthesis / bracket / angle depth 0 of s"""
    out, d = [], 0
    for i, c in enumerate(s):
        if c in '([<': d += 1
        elif c in ')]>': d -= 1
        elif c == ch and d == 0: out.append(i)
    return out

def r_split_virtual_declarators(path, text, stats):
    """VSPLIT1: `virtual T a(), b();` -> `virtual T a(); virtual T b();` (see above)."""
    if 'virtual' not in text or '),' not in text.replace(' ', ''): return text
    masked = d22_mask(text)
    edits = []
    for m in VSPLIT1_HEAD.finditer(masked):
        end = masked.find(';', m.start())
        if end < 0: continue
        stmt = masked[m.start():end]
        if '{' in stmt or '}' in stmt or '=' in stmt: continue
        cuts = _vsplit_top(stmt, ',')
        if not cuts: continue
        first = stmt[:cuts[0]]
        # the decl-specifiers: everything before the first declarator's name (`virtual void `, `virtual int `); a
        # declarator keeps its own `*` / `&`
        dm = re.match(r'(virtual\s+(?:[\w:<>]+\s*(?:<[^;{}()]*>\s*)?[\s\*&]+)+?)(~?[A-Za-z_]\w*)\s*\(', first)
        if not dm: continue
        spec = re.sub(r'[\s\*&]+$', '', text[m.start():m.start() + len(dm[1])]) + ' '
        parts = [stmt[a:b] for a, b in zip([0] + [c + 1 for c in cuts], cuts + [len(stmt)])]
        if not all(re.match(r'\s*[\*&]*\s*~?[A-Za-z_]\w*\s*\(', q) for q in parts[1:]): continue
        src = text[m.start():end + 1]
        for c in reversed(cuts):                    # each top-level comma -> `; <decl-specifiers>`; line breaks stay
            src = src[:c] + '; ' + spec + src[c + 1:].lstrip(' \t')
        edits.append((m.start(), end + 1, src))
        stats['VSPLIT1'] += len(parts) - 1
    if not edits: return text
    for a, b, rep in sorted(edits, reverse=True):
        text = text[:a] + rep + text[b:]
    return text

REFS_RULES = [r_operator_temporaries, r_address_of_temporaries, r_lane_array_address, r_std_definitions,
              r_conditional_bool_gates, r_restrict_reference_args, r_placement_only_delete,
              r_namespace_crt_calls, r_forceinline_declarations, r_vfptr_empty_base,
              r_later_empty_base,
              r_split_virtual_declarators]
RULES += REFS_RULES
