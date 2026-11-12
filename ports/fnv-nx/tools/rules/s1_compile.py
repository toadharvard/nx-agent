"""s1_compile.py - prepare rules of stream S1-compile (make every unit parse for AArch64 ILP32 with GCC).

Each rule is fn(path, text, stats) -> text (see tools/rules/README.md); ids are cited in the journal.
"""
import bisect
import collections
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
    root, pos, sse = _roots(path), 0, False
    while True:
        m = IFX_HEAD.search(text, pos)
        if not m: break
        end = prepare.match_brace(text, m.end() - 1)
        parts, sys_headers = [], set()
        _visible(COMMENT.sub(' ', text[:m.start()]), path, root, {os.path.abspath(path)}, parts, sys_headers)
        class_view = '\n'.join(p[0] for p in parts)
        name_view = '\n'.join(p[1] for p in parts)
        keep = _exists(m.group(2), class_view, name_view, sys_headers) != bool(m.group(1))
        # IFX1-SSE: a declaration dropped because the target's sse2neon has the name: say where it comes from
        sse = sse or (bool(m.group(1)) and not keep and re.match(r'\s*(?:__m128[id]?|__m64|_mm_\w+|_MM_\w+)\s*$',
                                                                  m.group(2)) is not None)
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
        text, n = SAL_CLASS_ATTR.subn(lambda m: '\n' * m.group(0).count('\n'), text)
        stats['SAL1'] += n
    return text

# ---------------------------------------------------------------------------------------------------------- DECL2

SDK_TAGS = ('tagRECT tagPOINT tagSIZE _FILETIME _SYSTEMTIME _GUID _RTL_CRITICAL_SECTION _LARGE_INTEGER _ULARGE_INTEGER '
            '_OVERLAPPED _WIN32_FIND_DATAA _SYSTEM_INFO tagMSG tagPALETTEENTRY _LUID _RGNDATA tagBITMAPFILEHEADER '
            'tagBITMAPINFOHEADER _iobuf _MEMORYSTATUS CRITICAL_SECTION _RTL_CRITICAL_SECTION_DEBUG').split()
# CRITICAL_SECTION: a struct copy of the PC type. _RTL_CRITICAL_SECTION_DEBUG: the decomp's SDK copy of
# hkpcpuworldgetclosestpointsjob merges winnt.h's two records into `typedef struct _RTL_CRITICAL_SECTION_DEBUG {...}
# RTL_CRITICAL_SECTION, *PRTL_CRITICAL_SECTION;` (so its CRITICAL_SECTION was the 32-byte debug record under MSVC);
# dropped, the unit's CRITICAL_SECTION is the platform's 24-byte PC section (D15; no assert of the unit sizes it, so
# neither CSX1 nor CSX2 applies, and no code of the unit reads the holder's layout)
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

# ---------------------------------------------------------------------------------------------------------- COM1
#
# A unit that defines its own `struct IUnknown {...}` (bsshader.cpp, for its D3DX buffer / constant-table
# stand-ins) declares the COM base with its own parameter types (`int QueryInterface(void*, void**)`, not pure), and
# its interfaces redeclare those methods with the same types. DECL2 cannot give way to the platform's IUnknown
# (fnvnx_win32.h, `HRESULT QueryInterface(REFIID, void**) = 0`): the redeclarations would no longer override it,
# QueryInterface would get a new slot behind GetBufferSize, and every slot of ID3DXConstantTable would move by one.
# So the unit's copy keeps its slots under its own name: every IUnknown of that file is renamed (types only, the
# unit has no function with IUnknown in its signature, so no decoration the recipe binds changes).

COM_OWN = re.compile(r'^[ \t]*(?:typedef\s+)?(?:struct|class)\s+IUnknown\s*\{', re.M)
COM_NAME = 'fnvnx_unit_IUnknown'

def r_own_iunknown(path, text, stats):
    """COM1: rename a unit's own copy of the COM base IUnknown (defined in that file) and its uses there."""
    if 'IUnknown' not in text: return text
    m = COM_OWN.search(text)
    if not m or prepare.scope_chain(prepare.scope_spans(text), m.start()): return text
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

TAIL1_PACK = re.compile(r'^[ \t]*#[ \t]*pragma[ \t]+pack[ \t]*\(([^)]*)\)', re.M)

def _tail1_packs(text):
    """[(position, active #pragma pack value or None)] in order: the file's pack stack after each directive
    (push / pop, with or without an identifier, and a bare value; `pack()` resets to the default)"""
    out, stack, cur = [], [], None
    for m in TAIL1_PACK.finditer(text):
        args = [a.strip() for a in m.group(1).split(',') if a.strip()]
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
    hkReferencedObject.h's pack(1) classes were align 4 natively, 1 under MSVC)."""
    if ':' not in text: return text
    out, pos = [], 0
    packs = _tail1_packs(text) if 'pack' in text else []
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
# aligned to 8 (double, __int64) or 16 (__m128, hkVector4, ...), the first member sits at 8 or 16, while GCC puts it
# right after the 4-byte vfptr (the LAYOUT gate's vfpad rows: hkaabbtree.cpp FUN_00d9c110_cls, whose members are
# named by their exe offsets m10 / m14 / ... / m40, had m10 at 4 natively and at 16 under MSVC). VFPAD1 puts
# FNVNX_VFPAD(<the member types>) first in such a body (FNVNX_VFPAD_PACK(N, ...) under an active `#pragma pack(N)`,
# nothing for N <= 4): a zero-width bit-field aligned like the most aligned member type (fnvnx_compat.h). Pointers,
# references, function pointers and the 4-byte-or-smaller builtins are left out of the list; a class whose members
# use a type nested in it (declared after the filler), an anonymous union / struct or a nested class member is held
# (VFPAD-HELD). VFPAD1_SRC holds the classes whose member offset names say the exe has no padding there (the gate's
# vfpad-src rows: the source's own vfptr stands for a 4-aligned base's), counted as VFPAD-SRC.

VFPAD1_SRC = {
    # hkpspheremotion.cpp: hkReferencedObject's members flattened in (m_usMemSizeAndFlags at 4, ..., unk10 at 0x10)
    'hkpSphereMotionD1E4',
}
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
        if not m or not m.group(1).strip(): continue
        t = m.group(1).strip()
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
    for o, c, k, h in prepare.scope_spans(text):
        if k != 'class': continue
        bare = prepare.HEAD_DECOR.sub(' ', prepare.TEMPLATE_PREFIX.sub('', h)).strip()
        if not re.match(r'(?:class|struct)\b', bare) or BASE_CLAUSE.match(bare): continue
        nm = re.match(r'(?:class|struct)\s+(?:__declspec\s*\((?:[^()]|\([^()]*\))*\)\s*)*([A-Za-z_]\w*)', bare)
        if not nm: continue
        own = nm.group(1)
        flat = _flat_body(text, o, c + 1)
        if not re.search(r'\bvirtual\b', flat): continue
        types, held = _vfpad_types(flat, own)
        if not types: continue
        if held:
            stats['VFPAD-HELD'] += 1; continue
        if own in VFPAD1_SRC:
            stats['VFPAD-SRC'] += 1; continue
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
# (sizeof 19). The game's code allocates and constructs MSVC's size: 0x00c61510 `new FUN_00c612b0_Class` asks
# operator new for 0x14 and its constructor 0x00c612b0 writes up to +0x14; 0x00c652c0 `new FUN_00c64de0` (six ints)
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
    cands = [m for m in EBO_CLASS.finditer(text) if m.group(2) and ',' in m.group(2)]
    if not cands: return text
    spans = prepare.scope_spans(text)
    heads = lambda pos: tuple(h for _, h in prepare.scope_chain(spans, pos))
    defs = {}
    for m in EBO_CLASS.finditer(text):
        defs.setdefault(m.group(1), []).append((m.end() - 1, m.group(2), heads(m.start())))
    def visible(name, scope):
        """the definitions of `name` in the innermost enclosing scope that has one"""
        ds = [d for d in defs.get(name, ()) if d[2] == scope[:len(d[2])]]
        best = max((len(d[2]) for d in ds), default=-1)
        return [d for d in ds if len(d[2]) == best]
    edits = []
    for m in cands:
        bases = [re.sub(r'^(?:(?:public|protected|private)\s+)+', '', b).strip() for b in _split_bases(m.group(2))]
        if len(bases) != 2 or any(re.search(r'\bvirtual\b|[<:]', b) for b in bases): continue
        ok, scope = True, heads(m.start())
        for b in bases:
            d = visible(b, scope)
            if len(d) != 1 or d[0][1]: ok = False; break               # one definition, no bases of its own
            if not _ebo_empty_class(text, d[0][0], prepare.match_brace(text, d[0][0])): ok = False; break
        o = m.end() - 1
        if not ok or _ebo_empty_class(text, o, prepare.match_brace(text, o)): continue
        edits.append(o + 1)
    if not edits: return text
    for at in reversed(edits):
        text = text[:at] + ' char fnvnx_msvc_ebo; /* fnv-nx EBO1 */' + text[at:]
    stats['EBO1'] += len(edits)
    return text

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
    labels = {m.group(1) for m in re.finditer(r'(?<![:\w])(\w+)\s*:(?!:)', text[pos:end])
              if m.group(1) not in ('default', 'public', 'private', 'protected')}
    return any(m.group(1) in labels for m in re.finditer(r'\bgoto\s+(\w+)\s*;', text[o:pos]))

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
    spans = [sp for sp in prepare.scope_spans(text) if sp[2] == 'function']
    edits = []
    for o, c, k, h in spans:
        body = text[o:c]
        if not re.search(r'\bgoto\s+\w+\s*;', body): continue
        for m in SCALAR_INIT.finditer(text, o, c):
            t = m.group(2)
            if re.match(r'(?:return|else|case|goto|delete|throw|typedef|using)\b', t): continue
            if (t.startswith('const') and '*' not in t and not re.search(r'\b(?:float|double|__m|hk)', t)
                    and ',' not in m.group(4) and INT_LITERAL_EXPR.match(m.group(4))):
                if _goto_crosses(text, o, c, m.start()):
                    edits.append((m.start(), m.end(), '%sstatic %s %s = %s;' % (m.group(1), m.group(2), m.group(3),
                                                                               m.group(4).strip())))
                continue                                    # not crossed: as written (MSVC's code stays the same)
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
    # (a call statement `return CallNextHookEx(...);` is not a prototype: CALL1-RET)
    names = [n for n in names if not re.search(r'[\w\*&>](?<!\breturn)(?<!\belse)(?<!\bcase)(?<!\bthrow)(?<!\bdo)'
                                               r'\s+%s\s*\([^;{}]*\)\s*;' % n, text)]
    if not names: return text
    # N::name( where namespace N only has DECL1's `using ::name;` is the platform function too (CALL1-NS:
    # blk_00860000's InstalledPathDetail::RegOpenKeyExA)
    aliased = {}
    for am in re.finditer(r'\bnamespace\s+(\w+)\s*\{', text):
        body = text[am.end():prepare.match_brace(text, am.end() - 1)]
        for n in re.findall(r'\busing\s+::\s*(\w+)\s*;', body):
            aliased.setdefault(n, set()).add(am.group(1))
    call = re.compile(r'(?<![\w.>:~])((?:\w+\s*::\s*)?)(%s)\s*\(' % '|'.join(sorted(names, key=len, reverse=True)))
    bodies = []                                      # outermost function bodies, in order
    for o, c, k, _ in prepare.scope_spans(text):
        if k == 'function' and not (bodies and o < bodies[-1][1]): bodies.append((o, c))
    if not bodies: return text
    out, pos, bi = [], 0, 0
    for m in call.finditer(text):
        if m.start() < pos: continue
        q = re.sub(r'\s*::\s*$', '', m.group(1))
        if q and q not in aliased.get(m.group(2), ()): continue
        while bi < len(bodies) and bodies[bi][1] < m.start(): bi += 1
        if bi == len(bodies) or not bodies[bi][0] < m.start(): continue
        before = text[max(0, m.start() - 40):m.start()].rstrip()
        prev = re.search(r'(\w+)$', before)
        if prev and prev.group(1) not in ('return', 'case', 'else', 'do'): continue   # a declaration
        close = _call_end(text, m.end() - 1)
        args = _split_args(text[m.end():close - 1])
        types = sigs[m.group(2)]
        if len(args) != len(types): continue
        refs = getattr(prepare, 'DECL1_REFS', {}).get(path, {}).get(m.group(2), ())
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
    comments = [c.span() for c in _CODE_LEXEME.finditer(text) if c.group().startswith('/')]
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
# typedef names of the template arguments that are declared later in the file expanded. A member defined through a
# typedef name of the specialization (`typedef T<A> N;` ... `void N::m(...) { ... }`, T a class template of the
# file) is the same member T<A>::m and gets the same declaration.

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

class _SpecVia:
    """a member definition through a typedef name of a specialization, read like a SPEC_DEF match"""
    def __init__(self, m, tmpl, args):
        self._m, self._g = m, {5: tmpl, 6: args}
    def start(self): return self._m.start()
    def group(self, i): return self._g[i] if i in self._g else self._m.group({7: 5, 8: 6, 9: 7}.get(i, i))

def _typedef_spec_defs(text):
    """member definitions `N::m(...)` where the file declares `typedef T<A> N;` at the start of a line, T being a
    class template the file defines: they define the members of the specialization T<A>, like SPEC_DEF's"""
    out = []
    for t in re.finditer(r'^[ \t]*typedef\s+([A-Za-z_]\w*)\s*<', text, re.M):
        depth, i = 1, t.end()
        while i < len(text) and depth:
            if text[i] == '<': depth += 1
            elif text[i] == '>': depth -= 1
            elif text[i] in ';{}()': break
            i += 1
        if depth: continue
        n = re.match(r'\s*([A-Za-z_]\w*)\s*;', text[i:])
        if not n or not re.search(r'\btemplate\s*<[^{};]*>\s*(?:class|struct)\s+%s\s*(?::[^;{}]*)?\{'
                                  % re.escape(t.group(1)), text[:t.start()]):
            continue
        args = re.sub(r'\s+', ' ', text[t.end():i - 1]).strip()
        for m in re.finditer(r'^([ \t]*)(template\s*<\s*>\s*)?((?:FNVNX_KEEP\s+)?(?:(?:inline|static|virtual)\s+)*)'
                             r'((?:[\w:<>,\*& \t]+?[\s\*&])?)\b%s\s*::\s*(~?\w+)\s*\(([^;{}]*)\)\s*(const\s*)?(?=[{;:])'
                             % re.escape(n.group(1)), text[i:], re.M):
            out.append(_SpecVia(_Shifted(m, i), t.group(1), args))
    return out

class _Shifted:
    """a match of a slice text[off:], with positions in text"""
    def __init__(self, m, off): self._m, self._off = m, off
    def start(self): return self._m.start() + self._off
    def group(self, i): return self._m.group(i)

def r_specialization_decls(path, text, stats):
    """SPEC1: declare explicit specializations of class template members before their first use (see above)."""
    if '::' not in text or not path.replace(os.sep, '/').endswith(tuple('/src/' + f for f in SPEC1_FILES)):
        return text
    spans, edits = None, []
    for m in sorted(list(SPEC_DEF.finditer(text)) + _typedef_spec_defs(text), key=lambda m: m.start()):
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
        # not into a preprocessor line: after the directives that follow p0 (`#include <memory>` under an include)
        while re.match(r'[ \t]*#', text[nl + 1:nl + 80]):
            nxt = text.find('\n', nl + 1)
            if nxt < 0 or nxt >= m.start(): break
            nl = nxt
        if re.match(r'[ \t]*#', text[nl + 1:nl + 80]): continue
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

# ---------------------------------------------------------------------------------------------------------- VFTBL1
#
# The decomp sometimes stands a table of its own in for an exe vftable: `static const VTable g_vtblAgent = {{(void*)
# &AgentRelease, ...}};` for the vftable at 0x010ce838 (hkpBvTreeStreamAgent, RTTI `.?AVhkpBvTreeStreamAgent@@`), with
# declared-only functions as the slots, and units.json binds the name to that address. The link renames the definition
# to `__fnv_<address>` and aliases.ld assigns that symbol the native vtable's address point (hand_vptrs / vmap, else
# the D26 (c) minus-one copy), which overrides the definition (tools/link/test_vtables.py); but the invented
# initializer stays in the object, and the functions it names (AgentRelease, AgentFlush) are undefined names nothing
# else references. VFTBL1 turns such a definition into a declaration, `extern const VTable g_vtblAgent;` (a namespace
# one under C linkage like STATIC1's, where the name is bound as `_g_vtblAgent`), so that `&g_vtblAgent` is an
# undefined reference bound by address like any other, and no initializer remains.
# Only a name that units.json binds, at file scope, to an address in VFTBL1_SITES (exe vftables with an RTTI locator;
# tools/rules/check_vftbl1.py checks each against the exe, and lists the units.json-bound initialised tables at other
# exe vftables, which a new site must come from), defined with a brace initializer, and that the file
# uses only through `&name`: a reader indexing `name.entries[k]` expects x86 slot k, and under D26 native slot k+1 is
# exe slot k, so it is left as written. (Uninitialised `int DAT_<address>;` definitions need no rule: no initializer,
# the link renames them the same way.)

VFT_INIT = re.compile(r'^([ \t]*)(?:static\s+)?((?:const\s+|volatile\s+)*[A-Za-z_][\w:<>,\s\*&]*?[\s\*&](\w+)'
                      r'(?:\s*\[[^\]]*\])*)\s*=\s*\{', re.M)
# The exe vftables (RTTI class) whose units.json-bound name the decomp defines with an initialised stand-in table
VFTBL1_SITES = {
    0x010ce838,   # .?AVhkpBvTreeStreamAgent@@ - g_vtblAgent, hkpbvtreestreamagent.cpp
    0x0102e388,   # .?AVhkBaseObject@@ - g_vtblBase, hkpbvtreestreamagent.cpp
    0x01030bf4,   # g_vtblShape, bstreenode_o2_variant.cpp
    0x01030b0c,   # g_vtblSphereRep, bstreenode_o2_variant.cpp
    0x01066c5c,   # g_vtblMultiSphere, bstreenode_o2_variant.cpp
}
_VFT_NAMES = None        # file -> {name: is it bound as a C name (`_name`)}

def _exe_vftable_addrs():
    return VFTBL1_SITES

def _vftable_bound_names(path):
    """{absolute file: {name: bound as a C name}} of data names a unit including the file binds to an exe vftable."""
    global _VFT_NAMES
    if _VFT_NAMES is not None: return _VFT_NAMES
    import json
    _VFT_NAMES = {}
    root, uj = prepare._units_json(path)
    if not uj: return _VFT_NAMES
    vft = _exe_vftable_addrs()
    inc = re.compile(r'^[ \t]*#[ \t]*include[ \t]*"([^"]+)"', re.M)
    for u in json.load(open(uj))['units']:
        names = {}
        for n, a in u.get('symbols', {}).items():
            if int(a, 16) not in vft or n.startswith('??_7'): continue
            m = DATA_NAME.match(n)
            if m: names.setdefault(m.group(1), False)
            elif n.startswith('_') and n[1:].isidentifier(): names[n[1:]] = True
        if not names: continue
        todo, seen = [os.path.normpath(os.path.join(root, u['source']))], set()
        while todo:
            f = todo.pop()
            if f in seen or not os.path.isfile(f): continue
            seen.add(f)
            with open(f, encoding='utf-8', errors='surrogateescape') as h: t = h.read()
            e = _VFT_NAMES.setdefault(f, {})
            for n, c in names.items(): e[n] = e.get(n, False) or c
            for i in inc.findall(t):
                for base in (os.path.dirname(f), os.path.join(root, 'include')):
                    g = os.path.normpath(os.path.join(base, i))
                    if os.path.isfile(g): todo.append(g); break
    return _VFT_NAMES

def r_vftable_standins(path, text, stats):
    """VFTBL1: a table the decomp defines for a name bound to an exe vftable becomes a declaration (see above)."""
    if '=' not in text: return text
    names = _vftable_bound_names(path).get(os.path.normpath(os.path.abspath(path)))
    if not names: return text
    bare = COMMENT.sub(lambda m: re.sub(r'[^\n]', ' ', m.group(0)), text)
    spans, edits = None, []
    for m in VFT_INIT.finditer(bare):
        name = m.group(3)
        if name not in names: continue
        if re.search(r'\bextern\b', bare[bare.rfind('\n', 0, m.start()) + 1:m.start(2)]): continue
        if spans is None: spans = prepare.scope_spans(bare)
        chain = prepare.scope_chain(spans, m.start())
        if any(k not in ('namespace', 'extern') for k, _ in chain): continue
        namespaced = any(k == 'namespace' for k, _ in chain)
        if namespaced and not names[name]: continue     # not bound by its plain name: C linkage would name another symbol
        end = _decl_end(bare, m.end() - 1)
        if end is None or '#' in bare[m.end():end]: continue
        # every other use of the name takes its address: `vtbl = &name;`, `(void*)&name`
        if any(not re.search(r'&\s*$', bare[:u.start()]) for u in re.finditer(r'(?<![\w:.>])%s\b' % re.escape(name), bare)
               if not m.start(3) <= u.start() < m.end(3)):
            continue
        decl = bare[m.start(2):m.end(2)].rstrip()
        new = m.group(1) + ('extern "C" { extern %s; }' if namespaced else 'extern %s;') % decl
        edits.append((m.start(), end + 1, new))
    if not edits: return text
    out, pos = [], 0
    for a, b, r in edits:
        out.append(text[pos:a]); out.append(r); pos = b
        stats['VFTBL1'] += 1
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
FALL_DECLARATOR = re.compile(r'((?:::)?(?:[A-Za-z_]\w*(?:\s*<(?:[^<>;{}()]|<(?:[^<>;{}()]|<[^<>;{}()]*>)*>)*>)?\s*::\s*)*'
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
        # (S1b found FALL1 trapping every `extern "C" void` function without this)
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
# The two may also differ in the return type (`extern "C" int NiSprintf(char*, int, const char*, ...)` and
# `char* NiSprintf(char*, unsigned int, const char*, ...)`, both bound to 0x00a305b0) when no call of that name in
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

_UNIT_PLACED = {}   # absolute unit source -> the decorated names its units.json `functions` place (_unit_symbols)

def _unit_symbols(path):
    """{absolute unit source: {decorated name: address}}"""
    global _UNIT_SYMBOLS
    if _UNIT_SYMBOLS is None:
        import json
        _UNIT_SYMBOLS = {}
        root, uj = prepare._units_json(path)
        if uj:
            _UNIT_PLACED.clear()
            for u in json.load(open(uj))['units']:
                _UNIT_SYMBOLS[os.path.normpath(os.path.join(root, u['source']))] = dict(u.get('symbols', {}))
                _UNIT_PLACED[os.path.normpath(os.path.join(root, u['source']))] = {
                    f['symbol'] for f in u.get('functions') or () if f.get('symbol')}
            # BIND1's rows (a POST hook on units.json): the source rules see the bindings the link will have
            for src, name, addr in BIND1_REFS + sorted(VLIT1_BINDS):
                k = os.path.normpath(os.path.join(root, src))
                if k in _UNIT_SYMBOLS:
                    _UNIT_SYMBOLS[k].setdefault(name, addr)
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
    return DUPINT_MASK.sub(lambda x: re.sub(r'[^\n]', ' ', x.group()), text)

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
    protos = [(m.start(), m.end()) for m in prepare.PROTO.finditer(masked) if m.group(1) == name]
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
            if not m.group(2).strip(): continue        # `template <>`: an explicit specialization needs its primary first
            name = m.group(5)
            if not re.search(r'(?<![\w.>:~])%s\s*\(' % re.escape(name), block[:m.start()]): continue
            if not m.group(2).strip(): continue          # `template <>`: an explicit specialization needs its primary first
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
        path.append(m.group(1))
    return path

def _strip_defaults(params):
    """`class A = B, int n = 3` -> `class A, int n` (top-level `= ...` of each comma-separated item)."""
    return ', '.join(re.sub(r'\s*=.*$', '', p, flags=re.S).strip() for p in _split_args(params) if p.strip())

def r_forward_template_calls(path, text, stats):
    """TPL3: declare a namespace's function templates in front of the first qualified / explicit-argument call that
    comes before their definition (see above)."""
    if 'template' not in text or 'namespace' not in text: return text
    masked = FRIEND_MASK.sub(lambda m: re.sub(r'[^\n]', ' ', m.group()), text)
    spans = prepare.scope_spans(masked)
    inserts = {}
    for m in TPL3_DEF.finditer(masked):
        tparams, name = m.group(1), m.group(6)
        if not tparams.strip(): continue
        nspath = _ns_path(prepare.scope_chain(spans, m.start()))
        if not nspath: continue
        before = masked[:m.start()]
        home = [sp for sp in spans if sp[0] < m.start() < sp[1]][-1]       # the innermost namespace block of the def
        tnames = set(re.findall(r'(?:class|typename)\s+(\w+)', tparams))
        params = _strip_defaults(m.group(7))
        pnames = {re.findall(r'[A-Za-z_]\w*', p)[-1] for p in _split_args(params)
                  if len(re.findall(r'[A-Za-z_]\w*', p)) > 1 and not p.rstrip().endswith(('*', '&'))}
        words = set(re.findall(r'(?<![\w:])[A-Za-z_]\w*', tparams + ' ' + m.group(4) + ' ' + params))
        words -= TPL3_WORDS | tnames | pnames | CXX_WORDS
        decl = re.sub(r'\s+', ' ', 'template <%s> %s%s%s%s(%s);' % (_strip_defaults(tparams), m.group(2),
                                                                    m.group(4).strip() + ' ', m.group(5), name, params))
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
    masked = FRIEND_MASK.sub(lambda m: re.sub(r'[^\n]', ' ', m.group()), text)
    at = set()
    for h in TYPN1_HEAD.finditer(masked):
        names = re.findall(r'(?:class|typename)\s+([A-Za-z_]\w*)', h.group(1))
        if not names: continue
        semi, brace = masked.find(';', h.end()), masked.find('{', h.end())
        if brace < 0 and semi < 0: continue
        end = semi + 1 if brace < 0 or 0 <= semi < brace else prepare.match_brace(masked, brace)
        word = re.compile(r'\b(?:%s)\b' % '|'.join(map(re.escape, names)))
        for m in TYPN1_DEP.finditer(masked, h.end(), end):
            if m.group(4) in TYPN1_MEMBERS and word.search(m.group(3)): at.add(m.start(2))
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
    return [n.group(1) for n in re.compile(r'\bnamespace\s+([A-Za-z_]\w*)\s*\{').finditer(text, 0, at)
            if prepare.match_brace(text, n.end() - 1) > at]

def r_instantiation_size_types(path, text, stats):
    """INST1: `unsigned int` -> `C<..>::size_type` in an explicit member instantiation (see above)."""
    if 'template' not in text or 'unsigned' not in text: return text
    out, pos = [], 0
    for m in INST1_DIR.finditer(text):
        ps = _split_args(m.group(5))
        if not any(re.sub(r'\s+', ' ', p.strip()) == 'unsigned int' for p in ps): continue
        # the class template the directive names: in the qualifier's namespace, the nearest definition before it
        # that declares the member with as many parameters (the decomp has several `vector`s in other namespaces)
        quals = [q for q in re.split(r'\s*::\s*', m.group(2).split('<', 1)[0].strip()) if q][:-1]
        dps = None
        for cls in reversed(list(re.compile(r'\btemplate\s*<[^{};]*>\s*(?:class|struct)\s+%s\b[^;{]*\{'
                                                % re.escape(m.group(3))).finditer(text, 0, m.start()))):
            if quals and _inst1_namespaces(text, cls.start())[-len(quals):] != quals: continue
            body = text[cls.end():prepare.match_brace(text, cls.end() - 1)]
            decl = re.search(r'\b%s\s*\(([^;{}()]*)\)' % re.escape(m.group(4)), body)
            if decl and len(_split_args(decl.group(1))) == len(ps):
                dps = _split_args(decl.group(1)); break
        if dps is None: continue
        new = []
        for p, d in zip(ps, dps):
            if re.sub(r'\s+', ' ', p.strip()) == 'unsigned int' and re.match(r'\s*size_type\b', d):
                new.append('%s::size_type' % re.sub(r'\s+', ' ', m.group(2).strip())); stats['INST1'] += 1
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
    masked = FRIEND_MASK.sub(lambda m: re.sub(r'[^\n]', ' ', m.group()), text)
    edits = []
    for m in COPY1_CTOR.finditer(masked):
        cls, arg = m.group(1), m.group(2)
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
    text = ''.join(out)
    return math_own_statics(text, stats) if have else text

# MATH1-OWN: where MATH1 puts <math.h> in place of a copy, a `static` definition the file keeps of a C math function
# (`static __forceinline float sqrtf(float x) { return (float)sqrt(x); }`, hkpboxboxcollisiondetection_rtm_variant)
# now meets <math.h>'s extern "C" declaration of the same name: MSVC refuses it (C2375), GCC takes it only under
# -fpermissive and then calls the C library function instead of the unit's own body. The definition and the file's
# calls of it are renamed fnvnx_own_<name>: the unit's own function, on both compilers. One file in the tree today.

MATH_OWN_STATIC = re.compile(r'^[ \t]*static\s+(?:(?:inline|__inline|__forceinline)\s+)*(?:float|double|long\s+double)\s+'
                             r'(?:__cdecl\s+)?(%s)\s*\([^;{}()]*\)\s*\{' % '|'.join(sorted(
                                 n for n in prepare.CRT_HEADERS['<math.h>'].split() if re.match(r'^[a-z]\w*$', n))), re.M)

def math_own_statics(text, stats):
    names = sorted(set(MATH_OWN_STATIC.findall(text)))
    for n in names:
        text = re.sub(r'(?<![\w.>:~])%s(?=\s*\()' % n, 'fnvnx_own_' + n, text)
        stats['MATH1-OWN'] += 1
    return text

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
# includes (at the end of a unit that is only includes, a header unit such as middlehighprocess_h_unit: GCC
# instantiates the destructor at the end of the unit, so a declaration there comes before it): nothing in the unit
# instantiates the destructor then, and a use of it is a call of the game's destructor
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
    if lead_end is None: lead_end = len(text)              # a unit of includes only (a header unit): after them
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
    if lead_end == len(text) and not text.endswith('\n'): text += '\n'; lead_end += 1
    return text[:lead_end] + ' '.join(out) + ' /* fnv-nx DTOR1 */ ' + text[lead_end:]

# ---------------------------------------------------------------------------------------------------------- STL2
#
# Seven units use the real MSVC 2008 <string> / <vector> / <map> (the `_std` variants, tesobject_ob1_nogf,
# the speedtree units, bsfacegenmanager_ob1, blk_00b20000_od_ob1_rtm). Their game structs hold those containers
# (speedtree's leaf: a string at +0x14 and the next member at +0x30; tesobject's El: string, int, vector = 0x38, the
# element size 0x00abfa90 divides by), and four of them program against the library's internals (_Mybase,
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
        if m.group(0).startswith('namespace'):
            new = 'namespace fnvnx_vc9'
        else:
            new = (m.group(1) or '') + ('fnvnx_vc9' if m.group(2) == 'std' else 'fnvnx_vc9::stdext')
        out.append(text[pos:m.start()]); out.append(new); pos = m.end(); n += 1
    out.append(text[pos:])
    text = ''.join(out)
    # the unit's own code in namespace std (allocator<El>) meant MSVC's size_t, unsigned int: VC9's _Sizet
    for m in reversed(list(re.finditer(r'\bnamespace fnvnx_vc9\s*\{', text))):
        end = prepare.match_brace(text, m.end() - 1)
        body, k = re.subn(r'(?<![\w:])size_t\b', '_Sizet', text[m.end():end])
        text = text[:m.end()] + body + text[end:]; n += k
    first = [True]
    def header(m):
        if first[0]:
            first[0] = False
            return '#include "fnvnx_vc9.h" /* fnv-nx STL2 */'
        return '/* fnv-nx STL2: %s */' % m.group(0).strip()
    text = STL2_HEADERS.sub(header, text)
    stats['STL2'] += 1 + n
    return text

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
# enumerators and macros are left alone, and so are static member functions and friends. An out-of-class member
# `template <class A, class T> R C<A, T>::f(...) { ... }` of a class template C that only an included header defines
# (bsaudiodefines.h's NiTPointerListBase<A, T>::RemoveHead) gets the same, from that header's single definition of C
# and its dependent bases; explicit and partial specializations (arguments other than the parameters) are left.
# TPL1-NESTED: the same for a class nested in a class template (no template head of its own) whose base is another
# member class of that template (speedtree's hand-written vector: `struct Iterator : ConstIterator` using `aux`,
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
    if 'template' not in text or not (TPL1_CLASS.search(text) or TPL1_OUT_DEF.search(text)): return text
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
                if m.group(2) != name: continue
                if f not in _TPL1_SPANS: _TPL1_SPANS[f] = prepare.scope_spans(t)
                if prepare.scope_chain(_TPL1_SPANS[f], m.start()): continue
                o = m.end() - 1
                ps = set(re.findall(r'(?:class|typename|int|unsigned|bool|short|long|char)\s+([A-Za-z_]\w*)', m.group(1)))
                dep, dep_types = set(), set()
                for bname, args in _base_specs(m.group(3) or ''):
                    if args is None or not set(re.findall(r'[A-Za-z_]\w*', args)) & ps: continue
                    v, ty = included_members(bname)
                    dep |= v; dep_types |= ty
                own_v, own_t = _members(_flat_body(t, o, prepare.match_brace(t, o)), name)
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
        if m.group(2) in classes:
            if len(by_name.get(m.group(2), ())) != 1 or len(classes[m.group(2)]) != 1: continue
            names = by_name[m.group(2)][0]
        else:                                              # the class template lives in an included header
            # only the member of the primary template, its own parameters as the arguments (not a specialization)
            own = re.findall(r'(?:class|typename)\s+([A-Za-z_]\w*)\s*(?=[,>]|$)', m.group(1))
            targs = re.search(r'<([^<>;{}]*)>\s*::\s*~?[A-Za-z_]\w*\s*\($', m.group())
            if not own or not targs or [a.strip() for a in targs.group(1).split(',')] != own: continue
            if prepare.scope_chain(spans, m.start()): continue
            names = (included_template(m.group(2)) or set()) - set(own)
            if not names: continue
        brace = re.match(r'[^;{}]*\{', text[m.end():])
        if not brace: continue
        fo = m.end() + brace.end() - 1
        if re.search(r'\b(?:static|friend)\b', text[m.start():fo]): continue
        bodies.append((fo, prepare.match_brace(text, fo) - 1, text[m.start():fo], names))
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

_RREF_CLASS_HEAD = re.compile(r'\b(?:class|struct|union)\s+(?:__declspec\s*\((?:[^()]|\([^()]*\))*\)\s*)*([A-Za-z_]\w*)')
_RREF_MEMBER_HEAD = re.compile(r'([A-Za-z_]\w*)\s*(?:<[^<>()]*>\s*)?::\s*~?[A-Za-z_]\w*\s*\(')

def _rref_member_class(text, spans, pos):
    """The class whose member function body holds pos (`T C::f(...) {` or a function defined in C's body), or None."""
    cls = None
    for k, h in prepare.scope_chain(spans, pos):
        if k == 'class':
            m = _RREF_CLASS_HEAD.search(h)
            cls = m.group(1) if m else None
        elif k == 'function':
            m = list(_RREF_MEMBER_HEAD.finditer(h.split('(')[0] + '('))
            if m: cls = m[-1].group(1)
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
                    if d.group(2) != name or d.group(1) in RREF_NOT_TYPE: continue
                    params = d.group(3).strip()
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
    edits, bi, class_cache = [], 0, {}
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
        cls = _rref_member_class(text, spans, m.start())
        if cls and not re.search(r'(?:\.|->|::)\s*$', before):
            # an unqualified call in a member function of cls: class scope is searched first, so the class's own
            # declarations of the name (or the nearest base's) are the candidates where it has any (RREF1-CLASS)
            own = _rref_class_shapes(unit, cls, m.group(1), class_cache)
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
    """ENUM1: `enum X;` -> `enum X FNVNX_ENUM_INT;`, and `enum X {` -> `enum X : int {` for those names (see above).
    FNVNX_ENUM_INT is `: int` (fnvnx_compat.h); in the S11 gate it is empty: MSVC 2008 has no opaque declaration,
    and its own forward declaration `enum X;` is the same type, underlying int (ENUM1-FWD)."""
    if 'enum' not in text: return text
    names = _opaque_enum_names(path)
    if not names: return text
    def decl(m):
        if m.group(2) not in names: return m.group(0)
        stats['ENUM1'] += 1
        return m.group(1) + ' FNVNX_ENUM_INT' + m.group(3)
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
        # `*p > 0` with `T* p` compares the pointee, a scalar (signed for `int* p`: `<= 0` is not `== 0`); the
        # compare is the pointer's only through `**` (PTR1-DEREF)
        if is_ptr and re.search(r'(?<![\w)\]])\*\s*$', text[max(0, m.start() - 4):m.start()]) \
                and is_ptr.group(0).count('*') == 1:
            continue
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
#
# STAND2 widens this to an address tagged in ANOTHER unit (units.json `functions` of some other unit): blk_00640000_od
# defines `GlobalObject_00869f00::Clear` (delete[] p) untagged, and its symbols bind ?Clear@GlobalObject_00869f00@@QAEX_N@Z
# to 0x008454f0, the tagged FUN_008454f0::Clear of bssimplearray.cpp. MSVC's /Od code calls the name and the exe runs
# 0x008454f0; GCC inlines the local body into the tagged ResetPointer 0x006442e0 (fuzz: the native calls delete, the
# oracle the function). Only where MSVC can never have inlined the stand-in, so the oracle never ran its body: a
# non-inline, non-static out-of-class or free definition in a unit compiled without /O1, /O2, /Ox or /Ob2 (/Ob1
# inlines only `inline` functions), and only when every spelling of the name in the unit's symbols binds an address
# that another unit defines and this unit does not. A name the unit declares virtual is left alone (the native
# vtable and its kind-V pairing stay as they are).

STAND_DEF = re.compile(r'^[ \t]*((?:[\w:<>,\*&]+[ \t\*&]+)*?)((?:[A-Za-z_]\w*\s*::\s*)*~?[A-Za-z_]\w*)\s*'
                       r'\(([^;{}()]*(?:\([^;{}()]*\)[^;{}()]*)*)\)\s*(?:const\s*)?(?:throw\s*\([^)]*\)\s*)?(?::[^;{}]*)?\{', re.M)
FNV_TAG_ADDR = re.compile(r'//[ \t]*@fnv[ \t]+0x([0-9a-fA-F]+)')
NOT_FUNCTIONS = set('if for while switch return sizeof catch do else'.split())

STAND2_INLINING_FLAGS = {'/O1', '/O2', '/Ox', '/Ob2'}
_UNIT_INFO = None

def _unit_info(path):
    """(flags, {function addresses}) of the unit `path` from build/units.json, or None."""
    global _UNIT_INFO
    if _UNIT_INFO is None:
        import json
        _UNIT_INFO = {}
        root, uj = prepare._units_json(path)
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

# The tag of a definition: `// @fnv 0x<a>` above its head, with only blank, comment and preprocessor lines (the
# unit's `#pragma optimize`) and type declarations in between: `template <class T> struct L;`, or a whole
# `template <> struct M<void*> { static void __cdecl F(void**); };` (one level of nested braces). The head may span
# lines (a return type over two lines, `static void __declspec(noinline)` above the name): STAND_DEF matches from its
# last line. Seen as untagged, a tagged definition whose own name the unit binds to its address was dropped as its own
# stand-in (R6: 0x004ab230 BipedAnim::GetBoneNode, 0x0087ce70, 0x00660400, 0x00a8a200; R1: 0x0046ffd0).
STAND_TAG = re.compile(r'//[ \t]*@fnv[ \t]+0x[0-9a-fA-F]+[^\n]*\n')
STAND_GAP_ITEM = re.compile(r'\s+|//[^\n]*|/\*.*?\*/|#[^\n]*|(?:template\s*<[^;{}]*>\s*)?(?:struct|class|union|enum)\s+'
                            r'[\w:<>, \t*]+?\s*(?::[^;{}]*)?(?:;|\{(?:[^{}]|\{[^{}]*\})*\}\s*;)', re.S)

def _stand_head(text, at, lines=3):
    """Start of the line where the head of the definition matched at `at` begins: up to `lines` lines above, while
    the line above is part of a declaration (not blank, a comment, a preprocessor line or the end of a statement)."""
    start = text.rfind('\n', 0, at) + 1
    for _ in range(lines):
        if start == 0: break
        ls = text.rfind('\n', 0, start - 1) + 1
        prev = text[ls:start].strip()
        if not prev or prev.startswith(('#', '//', '/*')) or prev.endswith((';', '}', '{', '*/')): break
        start = ls
    return start

def _stand_gap_only(gap):
    """gap holds only what may sit between a tag and its definition (STAND_GAP_ITEM)."""
    i = 0
    while i < len(gap):
        m = STAND_GAP_ITEM.match(gap, i)
        if not m or m.end() == i: return False
        i = m.end()
    return True

def _stand_tagged(text, at):
    """The definition whose head STAND_DEF matched at `at` carries its own `@fnv` tag (see above)."""
    for h in sorted({_stand_head(text, at), text.rfind('\n', 0, at) + 1, at}):
        window = text[max(0, h - 2000):h]
        tags = list(STAND_TAG.finditer(window))
        if tags and _stand_gap_only(window[tags[-1].end():]): return True
    return False

def r_stand_in_definitions(path, text, stats):
    """STAND1: drop untagged definitions bound to the address of a tagged definition in the same unit (see above)."""
    return _stand_ins(path, text, stats, 'STAND1')

def r_stand_in_definitions_elsewhere(path, text, stats):
    """STAND2: drop untagged definitions bound to an address another unit defines, where MSVC cannot have inlined
    them: non-inline, non-static, non-virtual, in a unit without /O1, /O2, /Ox, /Ob2 (see above)."""
    return _stand_ins(path, text, stats, 'STAND2')

def _stand_ins(path, text, stats, which):
    if not path.endswith('.cpp') or '@fnv' not in text: return text
    syms = _unit_symbols(path)
    if not syms: return text
    tagged = {int(a, 16) for a in FNV_TAG_ADDR.findall(text)}
    elsewhere = set()
    if which == 'STAND2':
        info = _unit_info(path)
        if info is None or set(info[0]) & STAND2_INLINING_FLAGS: return text
        elsewhere = _all_unit_functions(path) - info[1]
    spans, edits, cands, tagged_prefixes = None, [], [], set()
    for m in STAND_DEF.finditer(text):
        q = re.sub(r'\s+', '', m.group(2))
        parts = q.split('::')
        if parts[-1] in NOT_FUNCTIONS or parts[-1] in CXX_KEYWORDS: continue
        if re.match(r'\s*(?:return|else|case|new|delete|throw)\b', m.group(1) or ''): continue
        if spans is None: spans = prepare.scope_spans(text)
        chain = prepare.scope_chain(spans, m.start())
        if any(k != 'namespace' for k, _ in chain): continue
        ns = [h.split()[-1] for _, h in chain if h.split()[-1] != 'namespace']
        scope = list(reversed(parts[:-1])) + list(reversed(ns))
        name = parts[-1]
        if len(parts) >= 2 and name == parts[-2]: prefix = '??0' + '@'.join(scope) + '@@'
        elif name.startswith('~'): prefix = '??1' + '@'.join(scope) + '@@'
        else: prefix = '?' + '@'.join([name] + scope) + '@@'
        if _stand_tagged(text, m.start()):
            tagged_prefixes.add(prefix); continue
        cands.append((m, q, parts, name, prefix))
    placed = _UNIT_PLACED.get(os.path.normpath(os.path.abspath(path)), ())
    for m, q, parts, name, prefix in cands:
        if which == 'STAND1':
            if not any(k.startswith(prefix) and int(a, 16) in tagged for k, a in syms.items()): continue
            # the unit's own recipe places this name and no tagged definition of the unit has it: it is the function,
            # the tag sits on another one (R2: caravanmenu 0x0073b730, the tag is on TESCaravanCard::
            # FUN_0073b730_card, units.json places NPCHandArrayInfo's). A tagged overload of the name keeps STAND1.
            if prefix not in tagged_prefixes and any(k.startswith(prefix) for k in placed):
                continue
        else:
            bound = [int(a, 16) for k, a in syms.items() if k.startswith(prefix)]
            if not (bound and all(a in elsewhere and a not in tagged for a in bound)): continue
            if re.search(r'\b(?:inline|__inline|__forceinline|static)\b', m.group(1) or ''): continue
            if re.search(r'\b(?:inline|__inline|__forceinline|static)\s+$', text[max(0, m.start() - 200):m.start()]):
                continue
            if re.search(r'\bvirtual\b[^;{}]*[\s\*&]%s\s*\(' % re.escape(name), text): continue   # vtable: S2c
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
            edits.append((start, c, head + '; /* fnv-nx %s */' % which + nl[head.count('\n'):]))
        else:
            edits.append((start, c, '/* fnv-nx %s: %s is the function at the tagged address */%s' % (which, q, nl)))
    if not edits: return text
    out, pos = [], 0
    for a, b, r in sorted(edits):
        if a < pos: continue
        out.append(text[pos:a]); out.append(r); pos = b
        stats[which] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- STAND4
#
# STAND1 / STAND2 for `static` free stand-ins (S1-layout, S13b native-empty-body). tesconditionfunctions.cpp defines
# `static bool __cdecl InFactionConditionForPlayerCallee(...) { return false; }` untagged, and the tagged 0x0059f790
# returns its result; the recipe binds `?InFactionConditionForPlayerCallee@@YA_NPAVTESObjectREFR@@PAX1AAN@Z` to
# 0x0059f630 (GetInFaction, tagged in the same unit). MSVC decorates a static free function like an extern one and
# its /Od code calls it by that name, so the exe runs 0x0059f630; GCC inlines `return false` (fuzz: the native makes
# no call). STAND1/2 leave static ones alone because an internal name cannot be rebound. STAND4 replaces the
# definition by an extern declaration and drops `static` from the unit's other declarations of that name, so the
# call is a call of the decorated name that the link binds. Same gates as STAND1 (an address tagged in the unit) or
# STAND2 (an address only other units define, in a unit without /O1 /O2 /Ox /Ob2), for an untagged free function at
# namespace scope; not inline, not a template, not overloaded in the unit (one definition of the name).
#
# An overload is converted too when every spelling of the name binds one address that the unit tags and the unit
# has no other `static` declaration of the name (R6 class 5): hkpworldoperationutil.cpp tags its static
# FindItemForThreshold at 0x00cb3d10 and defines a second static FindItemForThreshold(S00cb5100_World*, ...) that
# calls `pick`, which MSVC called as 0x00cb3d10 too (both spellings bound there). Such an overload's head may carry
# `__declspec(noinline)` (STAND4_DEF); a single static noinline definition stays (S5's register-allocation
# stand-ins).

STAND4_DEF = re.compile(r'^[ \t]*((?:(?:__declspec\s*\(\s*\w+\s*\)|[\w:<>,\*&]+)[ \t\*&]+)*?)'
                        r'((?:[A-Za-z_]\w*\s*::\s*)*~?[A-Za-z_]\w*)\s*'
                        r'\(([^;{}()]*(?:\([^;{}()]*\)[^;{}()]*)*)\)\s*(?:const\s*)?(?:throw\s*\([^)]*\)\s*)?(?::[^;{}]*)?\{', re.M)

def r_static_stand_ins(path, text, stats):
    """STAND4: a static free stand-in bound to a decompiled address becomes an extern declaration (above)."""
    if not path.endswith('.cpp') or '@fnv' not in text or 'static' not in text: return text
    syms = _unit_symbols(path)
    if not syms: return text
    info = _unit_info(path)
    tagged = {int(a, 16) for a in FNV_TAG_ADDR.findall(text)}
    elsewhere = set() if info is None or set(info[0]) & STAND2_INLINING_FLAGS else _all_unit_functions(path) - info[1]
    spans, edits, names = None, [], collections.Counter()
    defs = []
    names0 = collections.Counter()   # the definitions STAND_DEF sees (heads without __declspec)
    for m in STAND4_DEF.finditer(text):
        q = re.sub(r'\s+', '', m.group(2))
        if '::' in q or q in NOT_FUNCTIONS or q in CXX_KEYWORDS: continue
        names[q] += 1
        names0[q] += '__declspec' not in (m.group(1) or '')
        defs.append((m, q))
    for m, name in defs:
        lead = m.group(1) or ''
        if not re.search(r'\bstatic\b', lead): continue
        single = names0[name] == 1 and '__declspec' not in lead   # the rule as it was
        overload = not single and names[name] != 1
        # A lone `static __declspec(noinline)` stand-in (W6-EQ, 0x00d57aa0 / 0x00d57b20: `static __declspec(noinline)
        # float FUN_00d571d0() { return DAT_01030ff0; }`, whose name the unit binds to 0x00d571d0, a function the exe
        # calls there): MSVC never inlines it, so every caller called the bound name, in any optimization flags. The
        # same address gates apply, with `elsewhere` taken over all units for it (STAND6's argument). Other
        # __declspec heads (align, naked, ...) and names with no address are still S5's.
        noinline = (not single and re.search(r'\bnoinline\b', lead)
                    and not re.search(r'__declspec\s*\(\s*(?!noinline)\w+', lead))
        if not single and not overload and not noinline: continue
        if overload and re.search(r'(?m)^[ \t]*static\s+[^;{}()]*\b%s\s*\([^;{}]*\)\s*;' % re.escape(name), text): continue
        if re.search(r'\b(?:inline|__inline|__forceinline|constexpr|template)\b', lead): continue
        if re.match(r'\s*(?:return|else|case|new|delete|throw)\b', lead): continue
        before = text[max(0, m.start() - 400):m.start()]
        if _stand_tagged(text, m.start()): continue
        if re.search(r'\btemplate\s*<[^;{}]*$', before): continue
        if spans is None: spans = prepare.scope_spans(text)
        chain = prepare.scope_chain(spans, m.start())
        if any(k != 'namespace' for k, _ in chain): continue
        ns = [h.split()[-1] for _, h in chain if h.split()[-1] != 'namespace']
        if len(ns) != sum(1 for _ in chain): continue                     # an anonymous namespace
        prefix = '?' + '@'.join([name] + list(reversed(ns))) + '@@'
        bound = {int(a, 16) for k, a in syms.items() if k.startswith(prefix)}
        if not bound or len(bound) != 1: continue
        if noinline:
            if not (bound <= tagged or (info is not None and bound <= _all_unit_functions(path) - info[1] and not bound & tagged)):
                continue
        elif not (bound <= tagged or (bound <= elsewhere and not bound & tagged and single)): continue
        o = m.end() - 1
        c = prepare.match_brace(text, o)
        start = m.start() + len(text[m.start():m.end()]) - len(text[m.start():m.end()].lstrip())
        nl = '\n' * text.count('\n', start, c)
        head = re.sub(r'\bstatic\s+', '', text[start:o].rstrip(), count=1)
        edits.append((start, c, head + '; /* fnv-nx STAND4 0x%08x */' % next(iter(bound)) + nl[head.count('\n'):]))
        # the other declarations of the name lose `static` too (a first static declaration keeps the name internal)
        for d in re.finditer(r'(?m)^([ \t]*)static\s+(?=(?:__declspec\s*\(\s*\w+\s*\)|[^;{}()])*\b%s\s*\([^;{}]*\)\s*;)' % re.escape(name), text):
            if d.start() < start or d.start() >= c:
                edits.append((d.start(), d.end(), d.group(1)))
    if not edits: return text
    out, pos = [], 0
    keep = os.environ.get('FNVNX_RECON') and 'STAND4' in ''.join(e[2] for e in edits)
    spans4 = prepare.__dict__.setdefault('_fnvnx_stand4_spans', {}).setdefault(os.path.abspath(path), []) if keep else None
    for a, b, r in sorted(edits):
        if a < pos: continue
        if spans4 is not None and 'STAND4' in r: spans4.append((r, text[a:b]))   # REC1 matches on the text before this
        out.append(text[pos:a]); out.append(r); pos = b
    out.append(text[pos:])
    stats['STAND4'] += sum(1 for e in edits if 'STAND4' in e[2])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- STAND3
#
# STAND2 for inline stand-ins (S1-fuzzA). The decomp writes small helpers as in-class bodies (modelloader.cpp
# `struct _Mem_00441d20 { int a; ~_Mem_00441d20() { FUN_0044b160(this); } };`), and the recipe binds their MSVC names
# to the exe function the call went to (`??1_Mem_00441d20@@QAE@XZ` -> 0x0040c110, FUN_0040c110 of magicitem.cpp).
# STAND2 leaves in-class and `inline` definitions alone, because /Ob1 inlines exactly those. In an /Od unit without
# /Ob1, /Ob2, /O1, /O2 or /Ox MSVC inlines nothing, so the exe calls that address at every site and the local body
# never runs; GCC inlines it (fuzz: 9 native-null-read functions in modelloader, the native running FUN_0044b160's
# lock loop where the exe calls 0x0040c110). STAND3 drops such a body and keeps the declaration (a constructor's
# without its initializer list), so each call stays a call of that name and the link binds it to the exe's address
# (units.json symbols, tier unit/exact). Only: a .cpp of such a unit; an untagged in-class definition, or an
# untagged out-of-class or free one marked inline, at namespace or class scope (no local class, no template, not
# virtual, static, constexpr or forceinline, no operator); every units.json spelling of the name in the unit bound
# to one address that some unit decompiles; not a destructor of a class with a base or virtual members (any class of
# that name in the unit), not a name the unit declares virtual (an implicit override's vtable slot stays as it is).

STAND3_INLINING_FLAGS = ('/Ob1', '/Ob2', '/O1', '/O2', '/Ox')

STAND3_CLASS = re.compile(r'^(?:typedef\s+)?(?:class|struct|union)\s+(?:__declspec\s*\([^()]*\)\s*)?([A-Za-z_]\w*)\s*(?::|$)')

def _stand3_maybe_virtual(text, cls, name):
    """True when member `name` of class `cls` may be virtual without saying so: a destructor of a class that has a
    base or virtual members (any class of that name in the unit), or a name the unit declares virtual anywhere."""
    if name.startswith('~'):
        for m in re.finditer(r'\b(?:class|struct)\s+(?:__declspec\s*\([^()]*\)\s*)?%s\b\s*([^;{()]*)\{' % re.escape(cls), text):
            if ':' in m.group(1).replace('::', ''): return True
            c = prepare.match_brace(text, m.end() - 1)
            if re.search(r'\bvirtual\b', text[m.end():c]): return True
        return False
    return bool(re.search(r'\bvirtual\b[^;{}()]*\b%s\s*\(' % re.escape(name), text))

STAND3_BUILTIN = {'char': 'D', 'signed char': 'C', 'unsigned char': 'E', 'short': 'F', 'unsigned short': 'G',
                  'int': 'H', 'unsigned int': 'I', 'unsigned': 'I', 'long': 'J', 'unsigned long': 'K', 'float': 'M',
                  'double': 'N', 'bool': '_N', '__int64': '_J', 'unsigned __int64': '_K', 'void': 'X',
                  'wchar_t': '_W'}

def _stand3_param_codes(params):
    """MSVC's encoding of a parameter list of builtin types and pointers / references to them (`int, const char*` ->
    `HPBD`), None when a parameter is anything else (a class type, a typedef, a function pointer, an array, `...`).
    MSVC abbreviates a repeated multi-character type to a back-reference digit, so lists with a repeated pointer
    or reference type are not encoded either."""
    if params in ('', 'void'): return ''
    out, seen = [], set()
    for p in _split_args(params):
        p = re.sub(r'=.*$', '', p).strip()
        m = re.fullmatch(r'((?:const\s+|volatile\s+)*)((?:unsigned\s+|signed\s+)?(?:__int64|char|short|int|long|float|'
                         r'double|bool|void|wchar_t)|unsigned)((?:\s*const)?)\s*([*&]?)\s*(?:const\s*)?(?:[A-Za-z_]\w*)?', p)
        if not m: return None
        base = re.sub(r'\s+', ' ', m.group(2)).strip()
        code = STAND3_BUILTIN.get(base)
        if code is None or (code == 'X' and not m.group(4)): return None
        cq = bool(m.group(1).strip()) or bool(m.group(3).strip())
        if m.group(4):
            code = {'*': 'PA', '&': 'AA'}[m.group(4)][0] + ('B' if cq else 'A') + code
            if code in seen: return None
            seen.add(code)
        out.append(code)
    return ''.join(out)

def _stand3_overloads(text, spans, pos, name, in_class, cls):
    """Declarations and definitions of `name` in the scope that holds the definition at `pos`: the class body for an
    in-class member, the body of class `cls` for an out-of-class one (0 when the unit does not define it), the
    enclosing namespace scope for a free function."""
    pat = re.compile(r'(?<![\w.>:~])%s\s*\(' % re.escape(name))
    if in_class:
        body = next(((o, c) for o, c, k, _ in reversed([sp for sp in spans if sp[0] < pos < sp[1]]) if k == 'class'), None)
    elif cls:
        cm = re.search(r'\b(?:class|struct)\s+%s\b[^;{()]*\{' % re.escape(cls), text)
        body = (cm.end() - 1, prepare.match_brace(text, cm.end() - 1) - 1) if cm else None
        if body is None: return 0
    else:
        body = None
    n = 0
    for mm in pat.finditer(text):
        at = mm.start()
        if body is not None:
            if not body[0] < at < body[1]: continue
            inner = [sp for sp in spans if sp[0] < at < sp[1] and body[0] <= sp[0] and sp[0] != body[0]]
            if inner: continue                            # inside a member's body or a nested class
        else:
            if any(k != 'namespace' for k, _ in prepare.scope_chain(spans, at)): continue
        if re.search(r'[=(,!&|+\-*/?:<>]\s*$|\breturn\s*$|\.\s*$|->\s*$', text[max(0, at - 40):at]): continue   # a call
        n += 1
    return n

def r_inline_stand_ins(path, text, stats):
    """STAND3: drop untagged inline bodies of an /Od unit without inlining flags whose MSVC name binds the address of
    a decompiled function (see above): in-class definitions become declarations, out-of-class ones go, free ones keep
    their declaration."""
    if not path.endswith('.cpp') or '@fnv' not in text: return text
    info = _unit_info(path)
    if info is None or '/Od' not in info[0] or set(info[0]) & set(STAND3_INLINING_FLAGS): return text
    decompiled = _all_unit_functions(path)
    syms = _unit_symbols(path)
    if not syms: return text
    spans, edits = None, []
    for m in STAND_DEF.finditer(text):
        q = re.sub(r'\s+', '', m.group(2))
        parts = q.split('::')
        if parts[-1] in NOT_FUNCTIONS or parts[-1] in CXX_KEYWORDS or 'operator' in parts[-1]: continue
        lead = m.group(1) or ''
        if re.match(r'\s*(?:return|else|case|new|delete|throw|goto)\b', lead): continue
        if re.search(r'\b(?:virtual|static|template|__forceinline|FNV_FORCEINLINE|constexpr)\b', lead): continue
        before = text[max(0, m.start() - 400):m.start()]
        if _stand_tagged(text, m.start()): continue
        if re.search(r'\btemplate\s*<[^;{}]*$', before): continue
        if spans is None: spans = prepare.scope_spans(text)
        chain = prepare.scope_chain(spans, m.start())
        if any(k not in ('namespace', 'class') for k, _ in chain): continue
        scope_names, ok = [], True
        for k, h in chain:
            if k == 'namespace':
                n = h.split()[-1]
                if n != 'namespace': scope_names.append(n)
            else:
                if 'template' in h: ok = False; break
                cm = STAND3_CLASS.match(h.strip())
                if not cm: ok = False; break
                scope_names.append(cm.group(1))
        if not ok: continue
        in_class = bool(chain) and chain[-1][0] == 'class'
        if in_class and len(parts) > 1: continue
        if not in_class and not (re.search(r'\b(?:inline|__inline)\b', lead) or
                                 re.search(r'\b(?:inline|__inline)\s+$', text[max(0, m.start() - 200):m.start()])):
            continue                                       # a non-inline definition is STAND2's
        if not in_class and any(k == 'class' for k, _ in chain): continue
        scope = list(reversed(parts[:-1])) + list(reversed(scope_names))
        name = parts[-1]
        cls = parts[-2] if len(parts) >= 2 else (scope_names[-1] if in_class and scope_names else None)
        if cls and name == cls: prefix = '??0' + '@'.join(scope) + '@@'
        elif name.startswith('~'):
            if not cls or name[1:] != cls: continue
            prefix = '??1' + '@'.join(scope) + '@@'
        else: prefix = '?' + '@'.join([name] + scope) + '@@'
        # the definition's own spelling, not an overload's (V2 O1/O2: tesworldspace `NiPoint2() {}` took the address of
        # the bound NiPoint2(float, float)). A spelling agrees with the definition on an empty parameter list (`XZ`),
        # on const (`QBE` / `QAE`) and, when every parameter is a builtin type or a pointer / reference to one, on the
        # encoded parameters; exactly one spelling must agree, and with a class-type parameter the name must have no
        # other declaration in its scope
        params = re.sub(r'\s+', ' ', m.group(3) or '').strip()
        tail = text[m.end(3) + 1:m.end() - 1]
        is_const = bool(re.match(r'\s*const\b', tail))
        enc = _stand3_param_codes(params)
        def agrees(k):
            if (params in ('', 'void')) != k.endswith('XZ'): return False
            if cls and not name.startswith('~') and name != cls and \
                    k[len(prefix) + 1:len(prefix) + 2] != ('B' if is_const else 'A'): return False
            return enc is None or params in ('', 'void') or k.endswith(enc + '@Z')
        spell = [k for k in syms if k.startswith(prefix) and agrees(k)]
        if len(spell) != 1: continue
        if enc is None and _stand3_overloads(text, spans, m.start(), name, in_class, cls) != 1: continue
        addrs = {int(syms[spell[0]], 16)}
        if not addrs <= decompiled: continue
        if cls and _stand3_maybe_virtual(text, cls, name): continue
        o = m.end() - 1
        c = prepare.match_brace(text, o)
        start = m.start() + len(text[m.start():m.end()]) - len(text[m.start():m.end()].lstrip())
        for _ in range(3):                                 # the head may begin on the lines above
            ls = text.rfind('\n', 0, start - 1) + 1 if start > 0 else 0
            prev = text[ls:start].strip()
            if not prev or prev.startswith(('#', '//')) or prev.endswith((';', '}', '{', '*/', ':', ')')): break
            if in_class and re.match(r'(?:public|private|protected)\s*:$', prev): break
            start = ls
        nl = '\n' * text.count('\n', start, c)
        x = '%08x' % next(iter(addrs))
        if in_class or len(parts) == 1:                    # keep the declaration (without an initializer list)
            after = text[m.end(3) + 1:o]
            cv = re.match(r'\s*(?:const\b\s*)?(?:throw\s*\([^)]*\)\s*)?', after).group(0).rstrip()
            head = (text[start:m.end(3) + 1] + cv).rstrip()
            head = re.sub(r'\b(?:inline|__inline)\s+', '', head)
            edits.append((start, c, head + '; /* fnv-nx STAND3 0x%s */' % x + nl[head.count('\n'):]))
        else:
            edits.append((start, c, '/* fnv-nx STAND3: %s is the function at 0x%s */%s' % (q, x, nl)))
    if not edits: return text
    out, pos = [], 0
    for a, b, r in sorted(edits):
        if a < pos: continue
        out.append(text[pos:a]); out.append(r); pos = b
        stats['STAND3'] += 1
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
    # Placeholder variables decl-data would give a zero copy (S2-bodies), each with the exe's bytes at the site the
    # unit's pristine object references it:
    # * RefSet6b3c0::Insert (the unit binds it to 0x00c68b50; Node6b3c0::operator new inlined): the exe has
    #   `push 8; mov ecx, 0x011f6238; call 0x00aa3e40` at 0x00c68b63, where the pristine object relocates
    #   g_memoryManager6b3c0 (DIR32) and MemoryManager6b3c0::Allocate (REL32): the memory manager and its Allocate.
    ('src/fallout_shared/globalfunc_o2_variant.cpp', '?g_memoryManager6b3c0@@3UMemoryManager6b3c0@@A', '0x011f6238'),
    ('src/fallout_shared/globalfunc_o2_variant.cpp', '?Allocate@MemoryManager6b3c0@@QAEPAXI@Z', '0x00aa3e40'),
    # * ??_G of FUN_00ae8d10 (slot 0 of the vftable the unit binds, 0x010a3328 -> 0x00ae9060; MapBase8D10's
    #   operator delete inlined): `if (flags & 1) { push this; mov ecx, 0x011f6238; call 0x00aa4060 }` at 0x00ae90cf.
    ('src/staging/blk_00ae0000_od_ob1_variant.cpp', '?g_kMemoryManager8D10@@3UMemoryManager8D10@@A', '0x011f6238'),
    ('src/staging/blk_00ae0000_od_ob1_variant.cpp', '?Deallocate@MemoryManager8D10@@QAEXPAX@Z', '0x00aa4060'),
    # * OutlinedEntry's constructor 0x00aaec10 (`assign(rhs, 0, entryNpos)`): `mov edx, [0x010173f0]; push edx` at
    #   0x00aaec6f, the .rdata word 0xffffffff (npos) other units bind as DAT_010173f0.
    ('src/staging/blk_00aa0000_od_ob1_variant.cpp', '?entryNpos@@3IA', '0x010173f0'),
    # * Vector12::length_error (blk_00ac0000_od_ob1_variant.cpp, a static in-class stand-in throwing with the
    #   placeholder vector_error_message): the exe's ReserveItems 0x0065cfd0 calls 0x0065f640 (ThrowVectorTooLong,
    #   which pushes the exe's own message) at 0x0065d00e, where the pristine object calls this name
    ('src/staging/blk_00ac0000_od_ob1_variant.cpp', '?length_error@Vector12@@SAXXZ', '0x0065f640'),
    # * W28 (top-down 44): MMAlloc::Allocate (blk_00aa0000_od_ob1_variant.cpp, exe 0x00aa3e40) reads the small-pool
    #   table `if (n <= 0x3fc) pool = smallPools[n >> 2]`: the exe has `cmp [ebp+8], 0x3fc; shr edx, 2;
    #   mov eax, [edx*4 + 0x011f67b8]` at 0x00aa3ee8 (and 0x00aa498b). The unit's pristine object relocates it (DIR32) in
    #   a COMDAT function the exe compiled with GetHeapIndex inlined, so exe-data's byte match never finds the site.
    ('src/staging/blk_00aa0000_od_ob1_variant.cpp', '?smallPools@fnv_00aafc80@@3PAPAUDeallocPool@1@A', '0x011f67b8'),
    # * W28 (top-down 109): LNode_af5480::AddTail (src/bssystem/archive.cpp; exe 0x00afcc10): `push 8;
    #   mov ecx, 0x011f6238; call 0x00aa3e40` at 0x00afcc5c: the memory manager, as for g_memoryManager6b3c0.
    ('src/bssystem/archive.cpp', '?g_memoryManager_af5480@fnv_00af5480@@3UMemoryManager_af5480@1@A', '0x011f6238'),
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

# VLIT2: VLIT1 by the exe's vftables (S1-rules, from S2-bodies: TESObjectARMA's constructor 0x00511f60 stores its
# secondary vptr at +0x68 as `" rH"`, the bytes of 0x00487220, slot 0 of 0x0102a28c; the unit has many literals, so
# VLIT1 left it to VLIT1-LIST and the store could not reach the minus-one copy). The table is exe-derived and
# private: tools/rules/_vlit_vftables.py writes vlit.json (fnvnx-vlit/1: slot 0 -> [(vftable, COL offset)], vftable ->
# RTTI class) to fnv-nx-build recon/vlit_vftables, read from FNVNX_RECON (or the file FNVNX_VLIT). For each 1-3
# character literal stored with `=` (also through a `(void*)` / `(const char*)` cast) whose bytes read as a decompiled
# function's address: the store offset comes from the left side (`(char*)this + N`, else 0 when it stores through
# `this`). The literal becomes `(char*)&DAT_<V>` when exactly one vftable V has that slot 0 at that offset. When
# several have it, V is the one whose class is the class of the other vftables (`DAT_<hex>`) the same function stores.
# Without the table VLIT1 is unchanged; the stores VLIT2 cannot decide are VLIT2-LIST.
VLIT_STORE2 = re.compile(r'(=\s*)((?:\((?:const\s+)?(?:void|char)\s*\*\s*\)\s*)?)"([^"\\\n]{1,3})"(\s*;)')
VLIT_THIS_OFF = re.compile(r'\bthis\s*\)?\s*\+\s*(0[xX][0-9a-fA-F]+|\d+)')

def _vlit_table():
    st = prepare.__dict__.get('_fnvnx_vlit')
    if st is not None: return st
    import json
    files = []
    if os.environ.get('FNVNX_VLIT') and os.path.isfile(os.environ['FNVNX_VLIT']): files.append(os.environ['FNVNX_VLIT'])
    top = os.environ.get('FNVNX_RECON')
    if top and os.path.isdir(top):
        files += sorted(os.path.join(dp, 'vlit.json') for dp, _, ns in os.walk(top) if 'vlit.json' in ns)
    st = {}
    for f in files:
        try: d = json.load(open(f))
        except (OSError, ValueError): continue
        if d.get('format') == 'fnvnx-vlit/1':
            st = d; break
    prepare.__dict__['_fnvnx_vlit'] = st
    return st

def _vlit_exe_stores(path, text, stats, defs):
    """VLIT2 (above): [(match, vftable address)] for the stores the exe's vftables decide"""
    tab = _vlit_table()
    if not tab: return []
    out, spans = [], None
    for m in VLIT_STORE2.finditer(text):
        a = int.from_bytes((m.group(3).encode('latin-1', 'replace') + b'\0' * 4)[:4], 'little')
        if not 0x00401000 <= a < 0x00fde578 or a not in defs: continue
        ls = max(text.rfind(';', 0, m.start()), text.rfind('{', 0, m.start()), text.rfind('}', 0, m.start())) + 1
        lv = text[ls:m.start()]
        om = VLIT_THIS_OFF.search(lv)
        off = int(om.group(1), 0) if om else (0 if re.search(r'\bthis\b', lv) else None)
        cands = [c for c in tab.get('slot0', {}).get('%08x' % a, ()) if off is None or c[1] == off]
        if len(cands) > 1:
            if spans is None: spans = prepare.scope_map(text)
            outer = [sp for sp in spans if sp[0] < m.start() < sp[1] and sp[2] not in ('namespace', 'extern')]
            body = text[outer[0][0]:outer[0][1]] if outer else ''
            cls = {tab.get('class', {}).get(h.lower()) for h in re.findall(r'\bDAT_([0-9a-fA-F]{8})\b', body)} - {None}
            cands = [c for c in cands if tab.get('class', {}).get(c[0]) in cls]
        if len(cands) != 1:
            stats['VLIT2-LIST'] += 1; continue
        out.append((m, int(cands[0][0], 16)))
    return out

def _vlit_apply(path, text, stats, hits):
    """VLIT2's edits: each literal -> `(char*)&DAT_<V>` (its cast kept), `extern "C" char DAT_<V>;` in front of the
    outermost function or class around its first store, a binding row for the link (VLIT1_BINDS). All positions are
    in the original text; the edits apply from the end."""
    root, _ = prepare._units_json(path)
    masked = FRIEND_MASK.sub(lambda x: re.sub(r'[^\n]', ' ', x.group()), text)
    spans = prepare.scope_map(masked)
    edits, first = [], {}
    for m, v in hits:
        name = 'DAT_%08x' % v
        edits.append((m.start(), m.end(), '%s%s(char*)&fnvnx_vlit_%08x%s /* fnv-nx VLIT2: "%s" */' % (
            m.group(1), m.group(2), v, m.group(4), m.group(3).replace('*/', '* /'))))
        first[name] = min(first.get(name, m.start()), m.start())
        stats['VLIT2'] += 1
        if root: VLIT1_BINDS.add((os.path.relpath(os.path.abspath(path), root).replace(os.sep, '/'), '_' + name,
                                  '0x%08x' % v))
    for name, pos in first.items():
        # its own identifier for the symbol DAT_<V> (asm label): the file may declare DAT_<V> itself, with another
        # type or in another scope (blk_004c0000_od.cpp `int DAT_01020944`)
        outer = [sp for sp in spans if sp[0] < pos < sp[1] and sp[2] not in ('namespace', 'extern')]
        at = _decl_start(masked, outer[0][0]) if outer else 0
        edits.append((at, at, 'extern "C" char fnvnx_vlit_%s __asm__("%s"); /* fnv-nx VLIT2 */ ' % (name[4:], name)))
    for a, b, r in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        text = text[:a] + r + text[b:]
    return text

def r_vptr_literals_exe(path, text, stats):
    """VLIT2: a 1-3 character string literal stored as a vptr -> the exe vftable with that slot 0 at the store's
    offset (vlit.json; see above). Runs before VLIT1, which sees only what is left."""
    if '"' not in text or not _vlit_table(): return text
    defs, _ = _vlit_index(path)
    hits = _vlit_exe_stores(path, text, stats, defs)
    return _vlit_apply(path, text, stats, hits) if hits else text

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

RULES = [r_unit_renames, r_include_case, r_stand_in_definitions, r_stand_in_definitions_elsewhere, r_inline_stand_ins, r_static_stand_ins, r_stand_in_interfaces, r_static_initializers, r_msvc_tokens, r_std_struct, r_vc9_containers, r_msvc_library_internals, r_anonymous_return_types, r_opaque_enums, r_enum_int_values, r_function_named_classes, r_anonymous_aggregate_members, r_local_anonymous_unions, r_pointer_zero_compares, r_global_allocators, r_critical_section_tail, r_xbox_cs_asserts, r_keep_definitions, r_if_exists, r_sal_attributes, r_sdk_types, r_own_iunknown, r_exception_specs, r_placement_new, r_ctor_calls, r_c_data_linkage, r_goto_scalar_inits, r_goto_crossed_scopes, r_quad_returns, r_msc_version_values, r_sdk_guarded_copies, r_string_array_address, r_interlocked_fallbacks, r_win32_call_args, r_ref_cast_temporaries, r_temporaries_to_references, r_global_delete_noexcept, r_typeid_include, r_specialization_decls, r_specialization_prefix, r_namespace_specializations, r_va_list, r_va_enum_args, r_vptr_literals_exe, r_vptr_literals, r_vftable_standins, r_image_statics, r_duplicate_int_overloads, r_placement_include, r_namespace_template_decls, r_dependent_typenames, r_forward_template_calls, r_instantiation_size_types, r_copy_ctor_base_casts, r_dependent_base_members, r_math_overloads, r_incomplete_pointee_dtors, r_falloff_traps,
         r_msvc_base_layout, r_vfptr_pad, r_msvc_empty_bases, r_class_alignment, r_friend_class_visibility]

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
        p = re.sub(r'\s+', ' ', m.group(2)).strip()
        t = re.sub(r'\b(?:const|volatile)\b|[&*]', ' ', p).split()
        if not t: continue
        kinds.setdefault((m.group(1), t[0]), set()).add(p.endswith('&') and 'const' not in p.split())
    want = sorted(k for k, v in kinds.items() if v == {True})
    if not want: return text
    spans = prepare.scope_spans(text)
    edits = []
    for op, t in want:
        for m in re.finditer(r'(?<![=!<>+\-*/&|^%%])(%s)\s*(%s)\s*\(' % (re.escape(op), re.escape(t)), text):
            if 'function' not in [k for k, _ in prepare.scope_chain(spans, m.start())]: continue
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
        if m.group(0).lstrip().startswith('&&'): continue
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
        t, v = m.group(1), text[m.end() - 1:inner_end]
        out.append(text[pos:m.start()])
        out.append('((%s(*)[sizeof%s / sizeof(%s)])&%s)' % (t, v, t, v)); pos = inner_end + 1
        stats['LANE1'] += 1
    if not out: return text
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- STD2
#
# A unit defines a function of namespace std at file scope, qualified: guardpackage.cpp's `double std::abs(float)`
# (0x00408840, `?abs@std@@YANM@Z`, a float -> double helper the game linked in). DECL1 drops the decomp's
# `namespace std { double abs(float); }` (libstdc++ declares `float std::abs(float)`), so GCC refuses the definition
# ("should have been declared inside 'std'", "ambiguating new declaration"). STD2 renames the definition to the file
# scope name fnvnx_std_<name> and, in a POST hook, the unit's recipe symbol `?<name>@std@@<sig>` to
# `?fnvnx_std_<name>@@<sig>` (the same signature, MSVC's decoration of the new name). Callers bind the address
# (their own declarations, e.g. `float FUN_00408840(float)`, keep their names); equiv's Fingerprinter takes the
# renamed definition from the prepared recipe. Only where the unit's recipe defines `?<name>@std@@...` at the
# address of the definition's `// @fnv` tag.

STD2_DEF = re.compile(r'//\s*@fnv\s+(0x[0-9a-fA-F]{8})[^\n]*\n((?:[A-Z_]+\s+)*[^\n;{}()]*?)\bstd::([A-Za-z_]\w*)\s*\(')
# (unit source, address, old symbol, new symbol), filled while the tree is prepared. Kept on the prepare module: equiv.py
# loads the stream modules twice and keeps the first copy's POST hook, which must see the second copy's renames.
STD2_RENAMES = prepare.__dict__.setdefault('_fnvnx_std2_renames', set())
_STD2_FNS = None

def _std2_functions(path):
    """{absolute unit source: {address: symbol}} of the recipe's definitions in namespace std."""
    global _STD2_FNS
    if _STD2_FNS is not None: return _STD2_FNS
    import json
    _STD2_FNS = {}
    root, uj = prepare._units_json(path)
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
        addr, name = m.group(1).lower(), m.group(3)
        sym = fns.get(addr)
        if not sym or not sym.startswith('?%s@std@@' % name): continue
        if spans is None: spans = prepare.scope_spans(text)
        if prepare.scope_chain(spans, m.start(3)): continue              # not at file scope
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
# code tests the result byte directly (`cmp byte [eax], 0; je`, hkgpmesh 0x00e11e10; the bool conversion of the G
# operand instead adds `setne al; test al, al`, measured under MSVC 2008). COND1 writes that conversion:
# `c ? G(false) : x.f(a)`. Only where the other operand is the literal `true` / `false`, the G operand ends in a
# call of a name the unit declares returning G with as many parameters as the call has arguments, and every
# declaration of that name with that many parameters returns a class with an `operator bool()`, exactly one of them
# with a non-explicit bool constructor (hkgpmesh.cpp: GateE10, and S_e1ce10_Bool without one; with an explicit one
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
        g = m.group(1)
        body = unit[m.end() - 1:prepare.match_brace(unit, m.end() - 1)]
        if re.search(r'\boperator\s+bool\s*\(\s*\)', body):
            booly.add(g)
            if re.search(r'(?<!explicit )(?<!explicit)\b%s\s*\(\s*bool\b' % g, re.sub(r'\bexplicit\s+', 'explicit ', body)):
                gates.add(g)                       # an explicit G(bool) makes the conditional bool (V1 C2)
    if not gates: return text
    rets = {}                                      # (name, arity) -> {return type}
    for m in re.finditer(r'(?:^|[;{}\s])((?:const\s+)?[A-Za-z_]\w*[\s*&]*?)\s*\b([A-Za-z_]\w*)\s*\(([^()]*)\)\s*'
                         r'(?:const\s*)?[;{]', unit):
        rets.setdefault((m.group(2), _cond1_arity(m.group(3))), set()).add(m.group(1).strip())
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
        r = rets.get((call.group(1), _cond1_arity(args)), set())
        if len(r & gates) != 1 or not r <= booly: continue
        g = next(iter(r & gates))
        out.append(text[pos:m.start(1)]); out.append('%s(%s)' % (g, m.group(1))); pos = m.end(1)
        stats['COND1'] += 1
    if not out: return text
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- RSTR1
#
# A function-local `T* __restrict v` passed by name to a parameter `const X&` (hkpsavecontactpointsutil.cpp:
# `hkArray<Struct0x20*>::AddBack(const T& val)` called with a restrict-qualified local). MSVC ignores __restrict for
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
                return (m.group(1), idx) if m else None
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
        for j, prm in enumerate(_rstr1_params(m.group(2))):
            if re.match(r'const\b[^&*]*&(?!&)', prm): cref.setdefault(m.group(1), set()).add(j)
    spans = prepare.scope_spans(text)
    funcs = [(o, c) for o, c, k, _ in spans if k == 'function']
    edits = set()
    for d in decls:
        body = [(o, c) for o, c in funcs if o < d.start() < c]
        if not body: continue
        end = min(c for o, c in body)
        v = d.group(1)
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
# of Havok's placement allocator and of Gamebryo's NiMemObject placement pair, without the usual forms) and that has a
# virtual destructor. The deleting destructor needs a usual deallocation function: class-scope lookup finds only the
# placement one, so GCC rejects the class (and every class derived from it: "no suitable 'operator delete'"). MSVC 2008
# accepted it and called the global ::operator delete(void*) from the scalar deleting destructor (??_G of the
# kd-tree raycaster helper at 0x00d979a0 is 31 bytes: vfptr store, `test [esp+8],1`, `push esi; call; add esp,4` -
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
        if m.group(1): continue
        if spans is None: spans = prepare.scope_spans(text)
        inner = None
        for sp in spans:
            if sp[0] > m.start(): break
            if sp[0] < m.start() < sp[1]: inner = sp
        if not inner or inner[2] != 'class': continue
        dels.setdefault(inner, []).append(m.group(2))
    inserts = []
    for (o, c, k, h), params in dels.items():
        if not all(NEW5_PLACEMENT.match(p) for p in params): continue
        head = prepare.HEAD_DECOR.sub(' ', prepare.TEMPLATE_PREFIX.sub('', h)).strip()
        hm = NEW5_HEAD.match(head)
        if not hm or hm.group(3): continue                                   # no base classes
        if not re.search(r'\bvirtual\s+~\s*%s\s*\(' % re.escape(hm.group(2)), text[o:c]): continue
        # the class's own non-placement operator new (TexMap's from g_memoryManager): MSVC found no matching delete
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
        inserts.append((o + 1, ' public: FNVNX_GLOBAL_DELETE private:' if hm.group(1) == 'class'
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
# A namespace N of a unit declares its own C++ prototype of a CRT name (`namespace fnv_005f3a20 { int strcpy_s(char*,
# unsigned int, const char*); int _stricmp(const char*, const char*); }`), bound by the recipe to the game's wrapper
# (`?strcpy_s@fnv_005f3a20@@YAHPADIPBD@Z` -> 0x00406d30). DECL1 keeps it (N::strcpy_s is not ::strcpy_s). The unit
# saw no other declaration of the name on MSVC, so every unqualified call in N went to N::name. On the target the
# shim (force-included fnvnx_crt.h) and the headers DECL1 adds declare ::name as well; ordinary lookup inside N still
# stops at N::name, but a class-type argument (`strcpy_s(buf, 260, keyText)` with a NiFixedStringEx that converts to
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
    if prepare.PLATFORM is None: prepare.PLATFORM = prepare.platform_names()
    platform = prepare.PLATFORM
    if not any(n in text for n in platform): return text
    syms = _unit_symbols(path)
    if not syms: return text
    masked = _mask_code(text)
    spans = None
    want = {}                                           # namespace head -> {name}
    for m in prepare.PROTO.finditer(masked):
        name = m.group(1)
        if name not in platform or name in prepare.MACRO_NAMES or name in prepare.MATH_NAMES: continue
        if spans is None: spans = prepare.scope_spans(text)
        chain = prepare.scope_chain(spans, m.start())
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
        chain = prepare.scope_chain(spans, o)
        nss = [re.sub(r'^(?:inline\s+)?namespace\s+', '', hh).strip() for kk, hh in chain if kk == 'namespace']
        names = want.get('::'.join(nss))
        if not names or any(kk == 'function' for kk, _ in chain): continue   # only outermost function bodies
        for m in NSCALL_CALL.finditer(masked, o + 1, c):
            if m.group(2) not in names: continue
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
# A `__forceinline` declaration without a body (`__forceinline ~ReleaseValue();`, `__forceinline hkVector4&
# getColumn(int i);`) of a function the unit never defines: its body is in another unit. MSVC compiled the call as an
# ordinary call (it cannot inline what it has not seen). The shim's `__forceinline` is `inline
# __attribute__((always_inline))`, and GCC refuses to compile such a call when it optimizes ("inlining failed in call
# to 'always_inline' ...: function body not available": tile_ob1_variant, three Havok _rtm_variant agents,
# blk_00ae0000_od_ob1; only the real compile fails, the -fsyntax-only census does not see it). FINL1 drops the
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
        if not tail or tail.group(1): continue                       # a definition, or a pure virtual
        name = m.group(2)
        sig = _finl1_sig(text[m.end():close - 1]) + (('const',) if re.match(r'\s*const\b', text[close:]) else ())
        if unit is None:
            unit = '\n'.join(t for _, t in _closure(path, text)) if '#include' in text else COMMENT.sub(' ', text)
            spans = prepare.scope_spans(text)
        inner = None
        for sp in spans:
            if sp[0] > m.start(): break
            if sp[0] < m.start() < sp[1]: inner = sp
        cls, body = None, ''
        if inner is not None:
            if inner[2] != 'class': continue                         # a declaration inside a function or namespace
            head = re.sub(r'\btemplate\s*<[^{};]*?>\s*(?=(?:class|struct|union)\b)', ' ', inner[3] or '')
            # the class's own name, not a template parameter's (`template <class KEYD3, ...> class DispatchUtilD3`:
            # hkpbvtreeagent, hkgpmesh lost the keyword on members their units define out of class)
            # `struct __declspec(align(16)) BigSolver`: the declspec's own parentheses nest (hkgpmesh 0x00e1f4e0)
            hm = re.search(r'\b(?:class|struct|union)\s+(?:__declspec\s*\((?:[^()]|\([^()]*\))*\)\s*)*([A-Za-z_]\w*)', head)
            if not hm: continue
            cls, body = hm.group(1), text[inner[0]:inner[1]]
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
# for the decomp's method-only stand-ins (effectitemlist.cpp: `struct EffectItemList : BSSimpleList_Standin { virtual
# ~EffectItemList(); int m_iEffectCount; }` whose base methods are bound to BSSimpleList's code in the exe): the exe
# passes this+4 to them (0x00405fe0 -> IsEmpty 0x008256d0; the pristine unit matches the exe byte for byte), the build
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
        defs.setdefault(m.group(1), []).append(m)
        if m.group(2):
            for b in _split_bases(m.group(2)):
                b = re.sub(r'^(?:(?:public|protected|private)\s+)+', '', b).strip()
                derived[b].append(m)
    edits = []
    for b, ms in derived.items():
        if not re.match(r'^[A-Za-z_]\w*$', b) or len(defs.get(b, ())) != 1: continue
        bd = defs[b][0]
        if bd.group(2): continue                                          # B has bases of its own
        bo = bd.end() - 1
        bc = prepare.match_brace(text, bo)
        if not _ebo_empty_class(text, bo, bc): continue
        intro = False
        for m in ms:
            if len(_split_bases(m.group(2))) != 1 or re.search(r'\bvirtual\b', m.group(2)): break
            o = m.end() - 1
            if re.search(r'\bvirtual\b', _flat_body(text, o, prepare.match_brace(text, o))): intro = True
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

# ---------------------------------------------------------------------------------------------------------- REC1
#
# Bodies the decomp lacks (D20): a function of the exe that no unit defines (MemoryHeap's constructor 0x00aa78a0 and
# destructor 0x00aa7930: the default heaps stayed raw memory, S5-boot) is reconstructed against the exe and accepted
# only when tools/build_and_match.py gives all bytes equal. The reconstruction is exe-derived, so it lives only in the
# private artifacts repository (fnv-nx-build branch recon/<unit>), never here. REC1 applies what a prepare run is
# given: FNVNX_RECON names a directory holding recon.json files (one per recon, in subdirectories or at its top):
#   {"format": "fnvnx-recon/1", "unit": <source>, "decomp_commit": <sha>, "append": <file next to recon.json>,
#    "functions": [{address, symbol, size}], "symbols": {decorated name: address},
#    "replace": [{"old": <exact text>, "new": <text>}]}
# The text rule appends the body to its unit; the POST hook add_recon_definitions adds the functions and symbols to
# the prepared build/units.json. A recon whose decomp_commit is not the checkout's, or whose addresses a unit already
# defines, is skipped (REC1-STALE / REC1-DUP). Without FNVNX_RECON (or with no recon in it) REC1 does nothing.
# REC1-REPL (orchestrator, R1): `replace` edits code the unit already has, where an appended body cannot help (a
# filler the unit's own calls bind to, a declaration whose arguments sit on the wrong call, an in-class body that is
# not the exe's). Each pair is applied in order, before the append, to the text the rule receives; every `old` must
# occur exactly once there. A recon written for other text must never half-apply: when any pair does not match exactly
# once (or is malformed), the whole recon is rejected for that unit (no replace, no append, no symbols), so its rows
# stay visible trap stubs, and the prepare goes on (orchestrator, R2: one stale recon must not block every build).
# Each rejection is counted (REC1-REJECT), printed to stderr and listed in <out>/build/recon_rejected.tsv (recon dir,
# unit, pair index, count, the start of `old`) by the POST hook.
# REC1-DUP-DEF (orchestrator, R2): an append that would define a function the unit already defines (same namespace
# path, qualified name and parameter types, at namespace scope) is rejected the same way (pair index -2), instead of
# failing the compile with a redefinition. A recon that restored a body a rule had stripped (STAND1 with a split head)
# then stops applying by itself once the rule keeps the body.

def _recon_entries(path):
    """[(recon dict, directory)] valid for the decomp checkout that holds path (cached on the prepare module:
    equiv.py loads the stream modules twice and keeps the first copy's POST hook)."""
    got = prepare.__dict__.get('_fnvnx_recon')
    if got is not None: return got
    import json, subprocess
    _RECON = prepare.__dict__['_fnvnx_recon'] = []
    top = os.environ.get('FNVNX_RECON')
    if not top or not os.path.isdir(top): return _RECON
    root, uj = prepare._units_json(path)
    head = None
    if root:
        try:
            head = subprocess.run(['git', '-C', root, 'rev-parse', 'HEAD'], capture_output=True, text=True,
                                  timeout=30).stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            head = None
    defined = set()
    if uj:
        for u in json.load(open(uj))['units']:
            defined.update(f['address'] for f in u.get('functions', []))
    files = sorted(os.path.join(dp, 'recon.json') for dp, _, ns in os.walk(top) if 'recon.json' in ns)
    for f in files:
        try:
            r = json.load(open(f))
        except (OSError, ValueError):
            continue
        if r.get('format') != 'fnvnx-recon/1' or not r.get('unit') or not (r.get('append') or r.get('symbols')
                                                                             or r.get('replace')):
            continue   # a recon with only `symbols` binds names to the exe's functions and adds no body
        if head and r.get('decomp_commit') and r['decomp_commit'] != head:
            r['_skip'] = 'REC1-STALE'
        elif any(fn.get('address') in defined for fn in r.get('functions', [])):
            r['_skip'] = 'REC1-DUP'
            # reported like a rejection (recon_rejected.tsv, count -3): a recon written before another unit placed one of
            # its functions loses its body and symbols silently otherwise (R2: the readbuf and parser839 recons of
            # blk_00ac0000_od_ob1_variant.cpp, 7 + 2 traps back open on link 97deacd38aec)
            dup = sorted(fn['address'] for fn in r.get('functions', []) if fn.get('address') in defined)
            prepare.__dict__.setdefault('_fnvnx_recon_rejected', []).append(
                (os.path.dirname(f), r['unit'], -1, -3, 'functions placed by another unit now: ' + ' '.join(dup)))
        _RECON.append((r, os.path.dirname(f)))
    return _RECON

# ---- REC1-DUP-DEF: function definitions at namespace scope, by a light scan (no preprocessor, no templates)
_REC1_KW = {'if', 'while', 'for', 'switch', 'catch', 'return', 'sizeof', 'else', 'do', 'defined', 'decltype', 'alignof', 'case', 'new', 'delete'}
def _rec1_strip(s):
    s = re.sub(r'/\*.*?\*/', lambda m: re.sub(r'[^\n]', ' ', m.group(0)), s, flags=re.S)
    s = re.sub(r'//[^\n]*', '', s)
    s = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', s)
    s = re.sub(r"'(?:\\.|[^'\\\n])*'", "''", s)
    s = re.sub(r'^\s*#[^\n]*', '', s, flags=re.M)   # preprocessor lines
    return s
_REC1_HEAD = re.compile(r'((?:[A-Za-z_]\w*\s*::\s*)*(?:~?[A-Za-z_]\w*|operator\s*(?:\(\)|[^\s(]+)))\s*\(((?:[^;{}()]|\([^;{}()]*\))*)\)\s*(?:const\b\s*)?(?:throw\s*\(\s*\)\s*|noexcept\s*)?(?::[^;{}]*)?$')
def _rec1_params(p):
    p = re.sub(r'=[^,]*', '', p)                      # default arguments
    out = []
    for a in [x.strip() for x in p.split(',') if x.strip()]:
        a = re.sub(r'\b(const|volatile|struct|class|enum|typename|register)\b', ' ', a)
        a = re.sub(r'\s+', ' ', a).strip()
        a = re.sub(r'\s*([*&])\s*', r'\1', a)
        m = re.match(r'^(.*?[*&\s>])\s*([A-Za-z_]\w*)(\[[^\]]*\])?$', a)   # drop the parameter name
        if m and m.group(1).strip() and not m.group(1).strip().endswith('::'): a = m.group(1).strip() + (m.group(3) or '')
        out.append(a.replace(' ', ''))
    return ','.join(x for x in out if x != 'void')
def _rec1_defs(text):
    """{(namespace path, qualified name, normalized params)} of function definitions at namespace scope (template
    heads ignored: a template and its instantiation are one definition only when the text is the same)"""
    s = _rec1_strip(text); out = set(); scope = []; last = 0
    for m in re.finditer(r'[{};]', s):
        p, ch = m.start(), m.group(0)
        if ch == ';': last = p + 1; continue
        if ch == '{':
            head = s[last:p]
            kind = 'blk'
            mn = re.search(r'\bnamespace\s+([A-Za-z_]\w*)\s*$', head)
            if mn: kind = ('ns', mn.group(1))
            elif re.search(r'\bnamespace\s*$', head): kind = ('ns', '(anon)')
            elif re.search(r'\bextern\s*""\s*$', head): kind = ('ns', '')
            elif all(k != 'blk' for k in scope):
                hm = _REC1_HEAD.search(head.rstrip())
                if hm and not re.search(r'\b(struct|class|union|enum)\b[^()]*$', head[:hm.start()].split('\n')[-1] if hm else ''):
                    name = re.sub(r'\s+', '', hm.group(1))
                    if name.split('::')[-1] not in _REC1_KW and not re.search(r'=\s*$', head[:hm.start()]):
                        ns = '::'.join(k[1] for k in scope if k[1])
                        out.add((ns, name, _rec1_params(hm.group(2))))
            scope.append(kind); last = p + 1
        else:
            if scope: scope.pop()
            last = p + 1
    return out
def _rec1_full(d):
    ns, name, params = d
    return ((ns + '::') if ns else '') + name + '(' + params + ')'
def _rec1_duplicates(text, body):
    t = {_rec1_full(d) for d in _rec1_defs(text)}
    return sorted(_rec1_full(d) for d in _rec1_defs(body) if _rec1_full(d) in t)


def _recon_reject(r, d, src, bad, stats):
    """REC1-REPL: record a recon rejected for unit src (bad = (pair index, count or -1, `old` start or reason)); the
    POST hook writes the list to build/recon_rejected.tsv."""
    import sys
    k, c, what = bad
    rej = prepare.__dict__.setdefault('_fnvnx_recon_rejected', [])
    rej.append((d, src, k, c, what))
    stats['REC1-REJECT'] += 1
    if c == -2:
        msg = 'the append defines what the unit already defines: %s' % what
    elif c < 0:
        msg = 'replace[%d] %s' % (k, what)
    else:
        msg = 'replace[%d] "old" occurs %d times, not once: %r' % (k, c, what)
    sys.stderr.write('REC1-REPL: recon rejected for %s: %s (%s); its rows stay trap stubs\n' % (src, d, msg))

def _rec1_unstand4(path, text):
    """text with every STAND4 rewrite put back to the definition it replaced (None when STAND4 rewrote nothing here)."""
    spans = prepare.__dict__.get('_fnvnx_stand4_spans', {}).get(os.path.abspath(path))
    if not spans: return None
    out = text
    for marker, orig in spans:
        if out.count(marker) == 1: out = out.replace(marker, orig)
    return out if out != text else None

def r_recon_bodies(path, text, stats):
    """REC1: append the reconstructed bodies FNVNX_RECON holds for this unit (D20; see above)."""
    if not os.environ.get('FNVNX_RECON') or not path.endswith(('.cpp', '.c')): return text
    entries = _recon_entries(path)
    if not entries: return text
    root, _ = prepare._units_json(path)
    if not root: return text
    src = os.path.relpath(os.path.abspath(path), root).replace(os.sep, '/')
    for r, d in entries:
        if r['unit'] != src: continue
        if r.get('_skip'):
            stats[r['_skip']] += 1; continue
        edited, bad = text, None
        for k, e in enumerate(r.get('replace') or []):
            old, new = (e.get('old'), e.get('new')) if isinstance(e, dict) else (None, None)
            if not isinstance(old, str) or not old or not isinstance(new, str):
                bad = (k, -1, 'needs a non-empty "old" and a "new" string'); break
            c = edited.count(old)
            if c == 0:
                # REC1 x STAND4: a recon is written against the decomp text, and STAND4 (which runs before REC1) turned a
                # lone static stand-in into an extern declaration. Match on the text before STAND4 (its marker put back
                # to the original definition), apply the pair there, and let STAND4 convert what is left of it.
                un = _rec1_unstand4(path, edited)
                if un is not None and un.count(old) == 1:
                    edited = un.replace(old, new)
                    edited = r_static_stand_ins(path, edited, stats)
                    continue
            if c != 1:
                bad = (k, c, old[:120]); break
            edited = edited.replace(old, new)
        if bad is not None:
            _recon_reject(r, d, src, bad, stats); continue
        body = None
        if r.get('append'):
            body = open(os.path.join(d, r['append']), encoding='utf-8', errors='surrogateescape').read()
            dup = _rec1_duplicates(edited, body)
            if dup:
                _recon_reject(r, d, src, (-1, -2, ', '.join(dup)[:300]), stats); continue
        stats['REC1-REPL'] += len(r.get('replace') or [])
        text = edited
        if body is not None:
            text = text.rstrip('\n') + '\n\n/* fnv-nx REC1: reconstructed bodies (D20, fnv-nx-build recon/) */\n' + body
            if not text.endswith('\n'): text += '\n'
            stats['REC1'] += len(r.get('functions', []))
            # one per appended recon body: REC1 counts placed functions only, so the inline (D23 c) bodies, the
            # GCC-only stand-ins and the re-issued recons (functions: []) were not counted at all (orchestrator, S2)
            stats['REC1-APPEND'] += 1
        r['_applied'] = True
    return text

def recon_retarget(u, rt):
    """REC1-RETARGET: a recon whose replace pairs change the decoration of a function the unit's recipe already places
    (a return type fixed from the exe: EQ/R2 0x008d1e50 GetNextObjectFloat, float -> void*, QAEMXZ -> QAEPAXXZ) moves
    that recipe entry to the new decoration: {address, old, new}. Only when the unit places exactly `old` at `address`
    (anything else is a miss, counted, and changes nothing); the unit's symbols binding of `old` follows. -> 1 or 0."""
    a = rt.get('address', '').lower()
    for f in u.get('functions', []):
        if f['address'].lower() == a and f['symbol'] == rt.get('old') and rt.get('new'):
            f['symbol'] = rt['new']
            sy = u.setdefault('symbols', {})
            if rt['old'] in sy:
                sy.setdefault(rt['new'], sy.pop(rt['old']))
            return 1
    return 0


def add_recon_definitions(out_root, stats, files=None):
    """POST hook: REC1's recipe entries (functions and symbols) in <out_root>/build/units.json."""
    import json
    _RECON = prepare.__dict__.get('_fnvnx_recon')
    rej = prepare.__dict__.get('_fnvnx_recon_rejected') or []
    if rej and os.path.isdir(os.path.join(out_root, 'build')):
        with open(os.path.join(out_root, 'build', 'recon_rejected.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
            f.write('#recon dir\tunit\treplace index\tcount (-1: malformed, -2: append redefines, -3: placed elsewhere)\told (start) or reason\n')
            for row in rej:
                f.write('\t'.join(str(x).replace('\t', ' ').replace('\n', '\\n') for x in row) + '\n')
        stats['REC1-REJECTED-RECONS'] = len(rej)
    if not _RECON or not any(r.get('_applied') for r, _ in _RECON): return
    p = os.path.join(out_root, 'build', 'units.json')
    if not os.path.exists(p): return
    data = json.load(open(p))
    n = m = 0
    for r, _ in _RECON:
        if not r.get('_applied'): continue
        for u in data['units']:
            if u['source'] != r['unit']: continue
            have = {f['address'] for f in u['functions']}
            for fn in r.get('functions', []):
                if fn['address'] not in have:
                    u['functions'].append(dict(fn)); n += 1
            for k, v in r.get('symbols', {}).items():
                if k not in u.setdefault('symbols', {}):
                    u['symbols'][k] = v; m += 1
            for rt in r.get('retarget_functions', []):     # REC1-RETARGET (see recon_retarget)
                t = recon_retarget(u, rt)
                stats['REC1-RETARGET' if t else 'REC1-RETARGET-MISS'] += 1
                n += t
    if n or m:   # symbols persist on their own: a recon may only bind names to the exe's functions (R1: the Havok
        # agents DCD6A0Agent1..9, compared by address in FUN_00dcd4d0, must bind to the exe and get no body)
        stats['REC1-DEF'] += n
        stats['REC1-SYM'] += m
        json.dump(data, open(p, 'w'))
        if files is not None: files['REC1-DEF'] += 1

POST.append(add_recon_definitions)

# ---------------------------------------------------------------------------------------------------------- HUNIT1
#
# A header unit (nitarray_h_unit.cpp) exists only to make MSVC emit the COMDATs of class-template members its recipe
# lists (NiTArray<ActorCombatGroup*, NiTMallocInterface<ActorCombatGroup*>>::SetAt 0x0096ae90, SetAtGrow 0x00470000,
# SetSize 0x0096ad30): it calls them through a null pointer in NiTArray_UnitReference. MSVC /Od emitted each one. GCC
# at -O2 inlines the calls, so SetAt and SetAtGrow were emitted nowhere and their addresses had no native definition
# (S5-boot collect-all: 0x0096ae90, 1,238 trap hits from FUN_0096a850's constructor). An `used` attribute cannot
# help: the unit instantiates the class (a sizeof check) before the members' definitions, and GCC does not attach a
# later definition's attributes to it. HUNIT1 appends explicit instantiation definitions of exactly those members,
# which emits them (weak) as MSVC did. Neutral: under MSVC an explicit instantiation of members the unit already
# emits changes nothing (S11 gate). Sites: HUNIT1_SITES.

HUNIT1_SITES = [
    ('gamebryo2.2/corelibs/nimain/nitarray_h_unit.cpp',
     'template void NiTArray<ActorCombatGroup*, NiTMallocInterface<ActorCombatGroup*> >::SetAt(unsigned int, '
     'ActorCombatGroup*&);\n'
     'template unsigned int NiTArray<ActorCombatGroup*, NiTMallocInterface<ActorCombatGroup*> >::SetAtGrow(unsigned int, '
     'ActorCombatGroup*&);\n'
     'template void NiTArray<ActorCombatGroup*, NiTMallocInterface<ActorCombatGroup*> >::SetSize(unsigned int);\n'),
]

def r_header_unit_instantiations(path, text, stats):
    """HUNIT1: explicit instantiations of the class-template members a header unit's recipe lists (see above)."""
    rel = path.replace(os.sep, '/')
    for unit, inst in HUNIT1_SITES:
        if rel.endswith('/' + unit) and 'fnv-nx HUNIT1' not in text:
            text = text.rstrip('\n') + '\n\n/* fnv-nx HUNIT1: the members the recipe lists, emitted as MSVC did */\n' + inst
            stats['HUNIT1'] += inst.count('template ')
    return text

# ---------------------------------------------------------------------------------------------------------- EBO3
#
# An empty base that is not the first base (S1-layout, S13b layout-offset). In `struct Node : NodeBase0, NodeBase16
# {}` with NodeBase0 four ints and NodeBase16 empty, MSVC 2008 puts NodeBase16 after NodeBase0, at +0x10
# (/d1reportSingleClassLayout: sizeof(Node) 16, the base at 16), the Itanium ABI at 0. bgszonetargetlistener.cpp
# 0x0061ff30 calls NodeBase16's method bound to the exe (0x006207a0) with node+0x10 on x86 and node+0 natively; the
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
        defs.setdefault(m.group(1), []).append(m)
        if m.group(2):
            bs = [re.sub(r'^(?:(?:public|protected|private|virtual)\s+)+', '', b).strip() for b in _split_bases(m.group(2))]
            for i, b in enumerate(bs): uses[b].append((m, i, bs))
    def empty(b):
        if not re.match(r'^[A-Za-z_]\w*$', b) or len(defs.get(b, ())) != 1: return None
        bd = defs[b][0]
        if bd.group(2): return None
        bo = bd.end() - 1
        bc = prepare.match_brace(text, bo)
        if 'FNVNX_EBO2' in text[bo:bc]: return None
        return (bd, bo, bc) if _ebo_empty_class(text, bo, bc) else None
    edits = []
    for b, us in sorted(uses.items()):
        e = empty(b)
        if e is None: continue
        bd, bo, bc = e
        if any(re.search(r'\bvirtual\b', m.group(2)) for m, _, _ in us): continue
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

# ---------------------------------------------------------------------------------------------------------- IDTOR1
#
# An implicit destructor the recipe binds (S1-layout, S13b layout-offset). A class C that declares no destructor but
# has a member with one gets an implicit, non-trivial `C::~C`. MSVC 2008 emits it as a COMDAT and calls it by name;
# the recipe binds that name to the exe function the call went to (extradataobjects.cpp `struct List_009e03e0 { int
# dummy; void RemoveAll(); ListX m_uiX; }`: `??1List_009e03e0@fnv_00434dc0@@QAE@XZ` -> 0x0046ffb0, BSSimpleList's
# destructor, which receives the list at this+0xc; build_and_match: 293 / 293 functions). GCC defines the implicit
# destructor itself, inline, from the decomp's placeholder members: it destroys `m_uiX` at this+0x10 (the exe passes
# this+0xc to 0x0046ffb0, the native this+0x10 to `ListX::~ListX`), so the exe's destructor never runs. IDTOR1
# writes FNVNX_IDTOR1(C) at the end of C's body: under GCC `public: ~C();` (body-less), so GCC calls C's D1 by name
# and the link binds it to the exe's address, as for MSVC; empty under MSVC, whose implicit destructor already is that
# call (a real declaration there would renumber the unit's $SG string literals the recipe binds). Only: a .cpp; C defined once at namespace or class scope (no template, no local class, no
# anonymous namespace) that declares no `~C` in its body; the unit binds `??1C@<scopes>@@QAE@XZ` (the
# non-virtual form: a virtual one would be an implicit override's vtable slot) to one address some unit decompiles.

def r_implicit_bound_destructors(path, text, stats):
    """IDTOR1: declare `~C();` in a class whose implicit destructor the recipe binds to a decompiled address (above)."""
    if not path.endswith('.cpp') or 'struct' not in text and 'class' not in text: return text
    syms = _unit_symbols(path)
    if not syms or not any(k.startswith('??1') and k.endswith('@@QAE@XZ') for k in syms): return text
    decompiled = _all_unit_functions(path)
    spans = prepare.scope_spans(text)
    cands, seen = [], collections.Counter()
    for o, c, k, h in spans:
        if k != 'class': continue
        bare = prepare.HEAD_DECOR.sub(' ', h).strip()
        if re.match(r'\s*template\b', bare) or re.search(r'\btemplate\s*<', bare): continue
        cm = re.match(r'(?:typedef\s+)?(?:class|struct)\s+([A-Za-z_]\w*)\s*(?::(?!:)|$)', bare)
        if not cm: continue
        chain = prepare.scope_chain(spans, o)
        scope, ok = [], True
        for kk, hh in chain:
            if kk == 'namespace':
                n = hh.split()[-1]
                if n == 'namespace': ok = False; break                   # anonymous namespace
                scope.append(n)
            elif kk == 'class':
                hb = prepare.HEAD_DECOR.sub(' ', hh).strip()
                if re.search(r'\btemplate\b', hb): ok = False; break
                hm = re.match(r'(?:typedef\s+)?(?:class|struct|union)\s+([A-Za-z_]\w*)', hb)
                if not hm: ok = False; break
                scope.append(hm.group(1))
            else:
                ok = False; break
        if not ok: continue
        name = cm.group(1)
        key = '??1' + '@'.join([name] + list(reversed(scope))) + '@@QAE@XZ'
        seen[key] += 1
        cands.append((key, name, o, c, bare.startswith('class') or bare.startswith('typedef class')))
    edits = []
    for key, name, o, c, is_class in cands:
        if seen[key] != 1 or key not in syms: continue
        try: a = int(syms[key], 16)
        except ValueError: continue
        if a not in decompiled: continue
        if re.search(r'~\s*%s\s*\(' % re.escape(name), text[o:c]): continue   # a destructor of its own
        edits.append((c, ' FNVNX_IDTOR1(%s) /* fnv-nx IDTOR1 0x%08x */\n' % (name, a)))
    if not edits: return text
    for at, ins in sorted(edits, reverse=True):
        text = text[:at] + ins + text[at:]
    stats['IDTOR1'] += len(edits)
    return text

# ---------------------------------------------------------------------------------------------------------- D22
#
# D22 (docs/ARCHITECTURE.md): MSVC gives a virtual destructor one vftable slot (??_G(flags)); GCC gives it two (D1, D0),
# and the link maps them by the minus-one rule. The decomp often declares a class's first virtual as a placeholder
# (`virtual void v0();`, `virtual void Unk_00();`) where the exe's vftable has the deleting destructor in slot 0: under
# GCC every later slot of that class (and of every caller declaring the same hierarchy) is then one off. D22 declares
# `virtual ~C();` instead of the placeholder, under GCC only: FNVNX_D22(C, <the original declaration>) expands to the
# original declaration under MSVC (tools/equiv/msvc/fnvnx_equiv_fi.h), so MSVC's objects are unchanged.
# Which classes: the exe's vftables are needed (slot 0 is read from the image), so the list is exe-derived and
# private: tools/rules/_d22_classes.py writes d22.json (fnvnx-d22/1) into fnv-nx-build branch recon/d22_vtable_dtor,
# which tools/farm/recon.sh fetches into FNVNX_RECON with the recon bodies. Without it D22 does nothing.
# A class is listed by its qualified name with the placeholder's member name; the rule rewrites, in every file, a
# class of that qualified name whose first declared virtual is that member, has no base with virtual functions
# (no base at all; so its first declared virtual is slot 0), declares no destructor, and whose placeholder is not
# defined outside the class nor called by that name in the file (D22-CALLED / D22-DEFINED are counted, not
# rewritten).

D22_MASK = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|//[^\n]*|/\*.*?(?:\*/|\Z)', re.S)
D22_CLASS_HEAD = re.compile(r'\b(class|struct)\s+(?:__declspec\s*\((?:[^()]|\([^()]*\))*\)\s*)*([A-Za-z_]\w*)'
                            r'\s*(?:final\s*)?(:[^;{}]*)?$', re.S)
D22_VIRTUAL = re.compile(r'\bvirtual\b')
D22_TEMPLATE = re.compile(r'\btemplate\s*<[^;{}]*>\s*$', re.S)

def d22_mask(text):
    """text with comments and string / character literals blanked (same length, line breaks kept)"""
    return D22_MASK.sub(lambda m: re.sub(r'[^\n]', ' ', m.group()), text)

def d22_classes(text, masked=None):
    """Every class / struct definition of text: dict(qual=[outer..., name], open, close, bases, template, dtor,
    first=(start, end, member, decl) of the first declared virtual at the class's own depth, or None)."""
    masked = masked if masked is not None else d22_mask(text)
    spans = prepare.scope_spans(masked)
    out = []
    for i, (o, c, kind, head) in enumerate(spans):
        if kind != 'class': continue
        m = D22_CLASS_HEAD.search(masked[max(0, o - 600):o].rstrip())
        if not m: continue
        hs = max(0, o - 600) + m.start()
        qual, own = [], []
        for o2, c2, k2, h2 in spans:
            if o2 >= o: break
            if o2 < o < c2:
                if k2 == 'namespace':
                    nm = re.match(r'(?:inline\s+)?namespace\s+(\w+)', h2 or '')
                    qual.append(nm.group(1) if nm else '{anon}')
                elif k2 == 'class':
                    hm = D22_CLASS_HEAD.search(masked[max(0, o2 - 600):o2].rstrip())
                    qual.append(hm.group(2) if hm else '{?}')
                elif k2 != 'extern':
                    qual.append('{local}')
        # the class's own depth: positions inside the body not inside a nested brace pair
        nested = [(o2, c2) for o2, c2, _, _ in spans if o < o2 < c]
        def own_depth(pos):
            return not any(o2 < pos < c2 for o2, c2 in nested)
        virtuals, dtor, dtor_decl = [], False, None
        name = m.group(2)
        for dm in re.finditer(r'~\s*%s\s*\(' % re.escape(name), masked[o:c]):
            if own_depth(o + dm.start()):
                if dtor: dtor_decl = None; break
                dtor = True
                # a body-less, non-virtual `~C();` alone on its line (D26 (b): it gives way to `virtual ~C()`)
                ls = masked.rfind('\n', 0, o + dm.start()) + 1
                le = masked.find('\n', o + dm.start())
                le = c if le < 0 or le > c else le
                if re.match(r'^[ \t]*~\s*%s\s*\(\s*(?:void\s*)?\)\s*;[ \t]*$' % re.escape(name), masked[ls:le]):
                    dtor_decl = (ls + len(masked[ls:le]) - len(masked[ls:le].lstrip()), le)
        skip_to = o + 1
        for vm in D22_VIRTUAL.finditer(masked, o + 1, c):
            if vm.start() < skip_to or not own_depth(vm.start()): continue
            # the declaration runs to `;` at the class's depth, or to the end of its inline body
            j, end = vm.end(), None
            while j < c:
                ch = masked[j]
                if ch == ';': end = j + 1; break
                if ch == '{':
                    end = prepare.match_brace(masked, j); break
                j += 1
            if end is None: break
            decl = masked[vm.start():end]
            mm = re.search(r'(~?\s*[A-Za-z_]\w*|operator\s*[^\s(]+)\s*\(', decl)
            ls = masked.rfind('\n', 0, vm.start()) + 1
            pre = masked[ls:vm.start()]
            start = vm.start()
            if re.match(r'^\s*[A-Z_][A-Z0-9_]*(?:\s+[A-Z_][A-Z0-9_]*)*\s*$', pre) and pre.strip():
                start = ls + len(pre) - len(pre.lstrip())                    # FNVNX_KEEP virtual ...
            virtuals.append((start, end, re.sub(r'\s+', '', mm.group(1)) if mm else None, text[start:end]))
            skip_to = end
        out.append(dict(qual=qual + [name], open=o, close=c, head=hs, bases=(m.group(3) or '')[1:].strip(),
                        template=bool(D22_TEMPLATE.search(masked[max(0, hs - 300):hs])), dtor=dtor, dtor_decl=dtor_decl,
                        first=virtuals[0] if virtuals else None, virtuals=virtuals, kind=m.group(1)))
    return out

# ---- T1: d22_placeholder / _d22_callonly read a file through an index of its words (prepare speed) -----------------
# A cold prepare spent 363 of 1,328 s in r_vtable_dtor_slot, nearly all in these two functions scanning whole files
# with one regex per class and member name. Every match of their patterns starts at a maximal \w+ run equal to that
# name (the lookbehinds forbid a word character before it; what follows the name cannot be one), so the patterns run
# only in small windows at those runs. A window ends at the first ( ; { } ) after the name: no match of these
# patterns crosses one before its final '('. d22_placeholder's text has spans blanked (_D22Rest): the runs a blank cuts
# are candidates too, and its windows are read from the blanked text. A name that is not a plain identifier keeps the
# whole-text scan. Same matches, in the same order (checked against re.finditer on the decomp's texts).
_D22_WORD = re.compile(r'\w+')
_D22_STOP = re.compile(r'[(;{})]')
_D22_IDX = {}


def _d22_index(text):
    """(word -> run starts, stop-char positions, run starts, run ends) of one text object (a few kept)"""
    e = _D22_IDX.get(id(text))
    if e is not None and e[0] is text:
        return e[1]
    words, starts, ends = collections.defaultdict(list), [], []
    for m in _D22_WORD.finditer(text):
        words[m.group()].append(m.start())
        starts.append(m.start())
        ends.append(m.end())
    if len(_D22_IDX) > 6:
        _D22_IDX.clear()
    _D22_IDX[id(text)] = (text, (words, [m.start() for m in _D22_STOP.finditer(text)], starts, ends))
    return _D22_IDX[id(text)][1]


class _D22Rest:
    """text with the (merged) spans blanked, as d22_placeholder's `rest` was, read in pieces"""
    def __init__(self, text, spans):
        sp = []
        for a, b in sorted(s for s in spans if s[1] > s[0]):
            if sp and a <= sp[-1][1]:
                sp[-1][1] = max(sp[-1][1], b)
            else:
                sp.append([a, b])
        self.t, self.sp, self.sa = text, sp, [a for a, _ in sp]

    def blanked(self, i):
        k = bisect.bisect_right(self.sa, i) - 1
        return k >= 0 and self.sp[k][0] <= i < self.sp[k][1]

    def slice(self, lo, hi):
        t, out, pos = self.t, [], lo
        k = max(0, bisect.bisect_right(self.sa, lo) - 1)
        while k < len(self.sp) and self.sp[k][0] < hi:
            a, b = self.sp[k]
            if b > pos:
                a = max(a, pos)
                if a > pos:
                    out.append(t[pos:min(a, hi)])
                if a < hi:
                    out.append(' ' * (min(b, hi) - a))
                pos = min(b, hi)
            k += 1
        if pos < hi:
            out.append(t[pos:hi])
        return ''.join(out)


class _D22Match:
    __slots__ = ('_s', '_e', '_g')

    def __init__(self, s, e, g):
        self._s, self._e, self._g = s, e, g

    def start(self):
        return self._s

    def end(self):
        return self._e

    def group(self, i=0):
        return self._g[i]


def _d22_finditer(pattern, name, text, spans=()):
    """re.finditer(pattern, text with spans blanked), where the pattern's first token after its one-character
    lookbehind is the literal name"""
    pat = re.compile(pattern)
    if not _D22_WORD.fullmatch(name):
        return list(pat.finditer(_D22Rest(text, spans).slice(0, len(text)) if spans else text))
    words, stops, rs, re_ = _d22_index(text)
    rest = _D22Rest(text, spans) if spans else None
    cands = set()
    if rest is None:
        cands.update(words.get(name, ()))
    else:
        hit = set()     # runs a span touches: their pieces outside the spans
        for a, b in rest.sp:
            k = max(0, bisect.bisect_right(rs, a) - 1)
            while k < len(rs) and rs[k] < b:
                if re_[k] > a:
                    hit.add(k)
                k += 1
        hit_starts = {rs[k] for k in hit}
        cands.update(s0 for s0 in words.get(name, ()) if s0 not in hit_starts)
        for k in hit:
            i, r1 = rs[k], re_[k]
            while i < r1:
                while i < r1 and rest.blanked(i):
                    i += 1
                j = i
                while j < r1 and not rest.blanked(j):
                    j += 1
                if j - i == len(name) and text[i:j] == name:
                    cands.add(i)
                i = j
    out, last_end, n = [], -1, len(text)
    for s0 in sorted(cands):
        if s0 < last_end:
            continue
        k = bisect.bisect_left(stops, s0 + len(name))
        while k < len(stops) and rest is not None and rest.blanked(stops[k]):
            k += 1
        hi = stops[k] + 1 if k < len(stops) else n
        lo = max(0, s0 - 1)
        w = rest.slice(lo, hi) if rest is not None else text[lo:hi]
        m = pat.match(w, s0 - lo)
        if m:
            out.append(_D22Match(lo + m.start(), lo + m.end(), [m.group(i) for i in range(pat.groups + 1)]))
            last_end = lo + m.end()
    return out


def _d22_search(pattern, name, text, spans=()):
    """re.search on the same terms (the first match, or None)"""
    ms = _d22_finditer(pattern, name, text, spans)
    return ms[0] if ms else None


def d22_placeholder(masked, cls, decl, ignore=(), reach=None):
    """the virtual declaration decl = (start, end, member, text) of class cls is a placeholder in this file: no
    definition outside the class, an inline body of one statement at most, and no other use of its name followed by
    `(` (a call or another declaration)"""
    start, end, member, src = decl
    if not member or member.startswith(('~', 'operator')) or '"' in src or "'" in src: return False
    body = masked[start:end]
    if '{' in body:
        inner = body[body.find('{') + 1:body.rfind('}')]
        if len([s for s in inner.split(';') if s.strip()]) > 1 or '{' in inner: return False
    # the text without this declaration and other classes' virtual declarations of the same name, as spans blanked
    # (T1: read through an index of the file's words, not rebuilt and scanned per name; _d22_finditer)
    spans = [(start, end)] + [(a, b) for a, b in ignore if (a, b) != (start, end)]
    # an empty out-of-class definition of the member is a placeholder as well (D22 drops it under GCC)
    spans += [(d.start(), d.end()) for d in _d22_out_of_class_defs(masked, cls)
              if d.group('mem') == member and D22_EMPTY_BODY.search(d.group(0))]
    rest = _D22Rest(masked, spans)
    if _d22_search(r'(?<![\w~])%s\s*::\s*%s\s*\(' % (re.escape(cls), re.escape(member)), cls, masked, spans): return False
    for u in _d22_finditer(r'(?<![\w~])%s\s*\(' % re.escape(member), member, masked, spans):
        # an unqualified call reaches cls's member only from a member of cls or of a class deriving from it (S5-boot:
        # actor.cpp's CVShell::FUN_008805f0 calls its own base CVB's `v0(p)`; NiTMap_S's `v0` stayed unconverted)
        qualified = re.search(r'(?:->|\.|::)\s*$', rest.slice(max(0, u.start() - 8), u.start()))
        if reach is not None and not qualified and not reach(u.start()):
            continue
        # a qualified call whose receiver's type resolves to a class outside the family (extradatalist.cpp:
        # `((Ref_0041b2a0*)ref)->sub.v0()`, sub a Sub_0041b2a0, held back AX's v0)
        if reach is not None and qualified and getattr(reach, 'recv', None) and reach.recv(u.start()) is False:
            continue
        return False
    return True

D22_MEMBER_DEF = re.compile(r'(?<![\w~:])([A-Za-z_]\w*)\s*(?:<[^;{}()]*>\s*)?::\s*~?\s*[A-Za-z_]\w*\s*\([^;{}]*\)[^;{}]*\{')

def d22_reach(masked, classes):
    """reach(cls) -> callable(pos): could an unqualified call at pos name a member of class cls? Only inside a
    member of cls or of a class of the file deriving from it (an inline body in the class, or an out-of-class
    `Y::f(...) { }` definition); not in a free function or another class's member."""
    owners = [(c['open'], c['close'], c['qual'][-1]) for c in classes]
    for m in D22_MEMBER_DEF.finditer(masked):
        b = m.end() - 1
        try: e = prepare.match_brace(masked, b)
        except Exception: e = None
        if e: owners.append((b, e, m.group(1)))
    owners.sort(key=lambda t: t[0] - t[1])          # innermost (shortest) span first
    derived = collections.defaultdict(set)
    for c in classes:
        for b in _d22_bases(c['bases']): derived[b.replace('<>', '')].add(c['qual'][-1])
    def reach(cls):
        fam, todo = {cls}, [cls]
        while todo:
            for d in derived.get(todo.pop(), ()):
                if d not in fam: fam.add(d); todo.append(d)
        def f(pos):
            for a, z, nm in owners:
                if a < pos < z: return nm in fam
            return False
        def recv(pos):
            # the receiver of `...(->|.)ph(` at pos: True / False when its type resolves, None when unknown
            t = _d22_receiver_type(masked, pos, by_name)
            return None if t is None else t in fam
        f.recv = recv
        return f
    by_name = collections.defaultdict(list)
    for c in classes: by_name[c['qual'][-1]].append(c)
    return reach

D22_CAST_RECV = re.compile(r'\(\s*\(\s*(?:const\s+)?([A-Za-z_]\w*)\s*\*\s*\)\s*[A-Za-z_]\w*\s*\)\s*->\s*'
                           r'(?:([A-Za-z_]\w*)\s*(->|\.)\s*)?$')

def _d22_receiver_type(masked, pos, by_name):
    """type name of the receiver of the call at pos for `((T*)x)->ph(` (T) and `((T*)x)->m.ph(` / `->m->ph(` (the
    declared type of T's member m, when T is defined in the file once); None otherwise"""
    m = D22_CAST_RECV.search(masked[max(0, pos - 200):pos])
    if not m: return None
    t, mem, op = m.group(1), m.group(2), m.group(3)
    if not mem: return t
    cs = by_name.get(t, ())
    if len(cs) != 1: return None
    body = masked[cs[0]['open'] + 1:cs[0]['close']]
    d = re.search(r'(?:^|[;{}\n])\s*(?:struct\s+|class\s+)?([A-Za-z_]\w*)\s*(\*?)\s*%s\s*(?:\[[^\]]*\])?\s*;' % re.escape(mem),
                  body)
    if not d or (op == '->') != bool(d.group(2)): return None
    return d.group(1)

def _d22_files():
    files = []
    if os.environ.get('FNVNX_D22') and os.path.isfile(os.environ['FNVNX_D22']): files.append(os.environ['FNVNX_D22'])
    top = os.environ.get('FNVNX_RECON')
    if top and os.path.isdir(top):
        files += sorted(os.path.join(dp, 'd22.json') for dp, _, ns in os.walk(top) if 'd22.json' in ns)
    return files

def _d22_list():
    """{class key: set(members)} from every d22.json under FNVNX_RECON (or the file FNVNX_D22); cached on prepare
    (equiv.py loads the stream modules twice)"""
    st = prepare.__dict__.get('_fnvnx_d22')
    if st is not None: return st
    files = _d22_files()
    st = collections.defaultdict(set)
    import json
    for f in files:
        try: d = json.load(open(f))
        except (OSError, ValueError): continue
        if d.get('format') != 'fnvnx-d22/1': continue
        for c in d.get('classes', ()):
            st[c['class']].update(c.get('members', ()))
    prepare.__dict__['_fnvnx_d22'] = st
    return st

def _d22_dtor_slots():
    """{class simple name: k} - classes whose exe vftable holds the deleting destructor at slot k > 0 (slot 0 is a
    trivial function: the TESForm family, k = 4, S2-bodies vtmetric 3f7a6c35). From the `dtor_slots` rows of every
    d22.json (FNVNX_D22 / FNVNX_RECON; tools/rules/_d22_classes.py --dtor-slots). Cached on prepare."""
    st = prepare.__dict__.get('_fnvnx_d22_slots')
    if st is not None: return st
    import json
    st = {}
    for f in _d22_files():
        try: d = json.load(open(f))
        except (OSError, ValueError): continue
        if d.get('format') != 'fnvnx-d22/1': continue
        for r in d.get('dtor_slots', ()):
            if not (isinstance(r.get('slot'), int) and r['slot'] > 0): continue
            # a row with `file` (a tree path, e.g. src/fallout/ai/actor.cpp: a call-only shell named after its function,
            # whose receivers seamcheck knows) applies to that file only, and wins over a class-wide row
            st[(r['class'].split('::')[-1], r.get('file') or '')] = r['slot']
    prepare.__dict__['_fnvnx_d22_slots'] = st
    return st

def _classlayout():
    """{(simple name, file or ''): (kind, dtor_slot)} from S2's classlayout.tsv (tools/link/classlayout.py: one row per
    RTTI class of the exe, `# fnvnx-classlayout/1`; columns class, vftable, kind minus-one / exe-order / com, dtor_slot,
    reason, file, qualified), from FNVNX_CLASSLAYOUT or any classlayout.tsv under FNVNX_RECON. Rows of one key that
    disagree make that key undecided (left to the heuristics). Cached on prepare."""
    got = prepare.__dict__.get('_fnvnx_classlayout')
    if got is not None: return got
    files = [os.environ['FNVNX_CLASSLAYOUT']] if os.environ.get('FNVNX_CLASSLAYOUT') else []
    top = os.environ.get('FNVNX_RECON')
    if top and os.path.isdir(top):
        files += sorted(os.path.join(dp, 'classlayout.tsv') for dp, _, ns in os.walk(top) if 'classlayout.tsv' in ns)
    rows = collections.defaultdict(set)
    for f in files:
        try: lines = open(f).read().splitlines()
        except OSError: continue
        if not lines or not lines[0].startswith('# fnvnx-classlayout/1'): continue
        for line in lines[1:]:
            if not line.strip() or line.startswith('#'): continue
            fs = line.split('\t') + [''] * 9
            if fs[0] == 'class': continue
            # a secondary vftable's row (COL offset > 0: reason or evidence `secondary...`) does not decide the class:
            # the primary does (S2-bodies)
            if fs[4].strip().startswith('secondary') or fs[7].strip().startswith('secondary'): continue
            try: k = int(fs[3])
            except ValueError: continue
            if fs[2] not in ('minus-one', 'exe-order', 'com'): continue
            rows[(fs[0].strip().split('::')[-1], fs[5].strip().lstrip('/'))].add((fs[2], k))
    got = {key: next(iter(v)) for key, v in rows.items() if len(v) == 1}
    prepare.__dict__['_fnvnx_classlayout'] = got
    return got

def _classlayout_row(path, name):
    """the classlayout decision (kind, dtor_slot) for class `name` in file `path`: a file row first, or None"""
    cl = _classlayout()
    if not cl: return None
    p = path.replace('\\', '/')
    for (n, f), v in cl.items():
        if n == name and f and p.endswith('/' + f): return v
    return cl.get((name, ''))

def _d22_dtor_slot(path, name):
    """the dtor_slots slot of class `name` in the file `path` (a per-file row first), or None"""
    st = _d22_dtor_slots()
    if not st: return None
    p = path.replace('\\', '/')
    for (n, f), k in st.items():
        if n == name and f and p.endswith('/' + f.lstrip('/')): return k
    return st.get((name, ''))

def _d22_bases(bases):
    out = []
    for b in _split_bases(bases) if bases else ():
        b = re.sub(r'^(?:(?:public|protected|private|virtual)\s+)+', '', b.strip())
        out.append(re.sub(r'\s*<.*', '<>', b).split('::')[-1].strip())
    return out

# D26 (b): a call-only placeholder declaration (S5 tests/s5/callonly_vt.py) takes the same FNVNX_D22 without a list:
# a class with no base and no destructor that declares at least two virtuals, defines none of them in the file (no
# inline body, no `C::member(` definition), and whose first virtual's name is a placeholder (v0, vf0, Unk_00,
# ...Unknown0, Reserved0, ...). Code uses it only to dispatch on an object of a class it does not name, by the exe's
# slot numbers (a class of the file that derives from it defines none of its own virtuals either: otherwise its
# vtable and implicit destructor would need the undefined `~C`); under D26 every native receiver has D1/D0 at slots 0/1 (D22, the image vftables' minus-one copies),
# so the declaration needs `virtual ~C()` where its slot-0 placeholder is. A placeholder called by name stays
# (D22-USED: slot 0 is the deleting destructor, whose call needs another form).
# An unqualified call `ph(` counts as a use only inside a member of the class or of a class of the file deriving from
# it (actor.cpp's CVShell::FUN_008805f0 calls its own CVB's `v0(p)`, which held NiTMap_S back). A class that declares
# only a body-less, non-virtual `~C();` (no definition in the file, none called or defined by a header's includers)
# is call-only as well; its `~C();` goes under GCC, FNVNX_D22_DROP (D22-DTOR: modelloader_activator_declarations.h's
# BGSOpenCloseForm, whose tesobjectdoor_decls.h copy D26 already converts).
D22_CALLONLY_PLACEHOLDER = re.compile(
    r"^(?:v|vf|vfn|vt|vfunc|virt|virtual|slot|func|fn|unk|unknown|Unk_?|Unknown|Func|Virtual|Reserved|reserved|pad|Pad|"
    r"Slot|V|F)_?0+$|^\w*?Unknown_?0*$|^\w*_v0$|^v0_\w*$|^vfunc0\w*$|^Unk(?:nown)?0+\w*$", re.IGNORECASE)

D22_EMPTY_BODY = re.compile(r'\{\s*(?:return\s+(?:[-+]?(?:0[xX][0-9a-fA-F]+|\d+)[uUlL]*|true|false|0\.0f?|nullptr|NULL)\s*;\s*)?\}\s*$')

# COM interfaces (S5-boot, link 3f7a6c35e923: the adapter-mode list ctor 0x00e7bc90 dispatched on the IDirect3D9 at
# [vptr+108] / [vptr+72] where the exe reads +104 / +68, so EnumAdapterModes ran GetAdapterDisplayMode): an
# IUnknown-shaped vtable has QueryInterface / AddRef / Release at slots 0..2 and no destructor slot, whatever its
# receiver, and the native D3D / DInput / DSound objects keep that ABI. D22 / D26 leave alone a class with a
# `__stdcall` virtual (MSVC's C++ virtuals are __thiscall; COM methods are __stdcall: STDMETHOD, WINAPI, ...) or one
# returning HRESULT / taking REFIID, a
# virtual QueryInterface / AddRef / Release, or an SDK interface name (IUnknown, IDirect*, IDXGI*, IXAudio*, ...).
# Counted D22-COM.
D22_COM_CONV = re.compile(r'\b(?:__stdcall|_stdcall|STDMETHOD\w*|STDMETHODCALLTYPE|WINAPI|CALLBACK|APIENTRY|PASCAL|'
                          r'__RPC_\w+|NTAPI|HRESULT|REFIID)\b')
D22_COM_MEMBER = {'QueryInterface', 'AddRef', 'Release'}
D22_COM_NAME = re.compile(r'^(?:I(?:Unknown|Direct\w*|DXGI\w*|XAudio\w*|D3D\w*|DInput\w*|DSound\w*|Dispatch|ClassFactory|'
                          r'Stream|Storage|Persist\w*|Moniker|EnumVARIANT|MMDevice\w*|Audio\w*|KsPropertySet|'
                          r'Wbem\w*|XACT\w*|Xact\w*)|LPDIRECT\w*|LPUNKNOWN)$')

def _d22_com(masked, c):
    """class c is (or stands for) a COM interface: see above"""
    if D22_COM_NAME.match(c['qual'][-1]): return True
    for v in c['virtuals']:
        if v[2] in D22_COM_MEMBER or D22_COM_CONV.search(masked[v[0]:v[1]]): return True
    return False

def _d22_numbered(c):
    """the class's first virtuals are a numbered sequence of one prefix: `P_00, P_01` (InitHolder's FUN_00aa2020_Impl),
    `_v01, _v02` (bstexturepalette.cpp's NiTexture_VF), `d000, d001` (RefV), or byte offsets `v000, v004, v008`;
    step 1 from 0 or 1, or step 4 from 0, and a third virtual continues it when there is one (EQ G1)"""
    ns = [v[2] or '' for v in c['virtuals'][:3]]
    ms = [re.match(r'^([A-Za-z_]\w*?)(\d+)$', n) for n in ns]
    if len(ms) < 2 or not (ms[0] and ms[1]): return False
    if len(ms) > 2 and not (ms[2] and ms[2].group(1) == ms[0].group(1)): ms = ms[:2]   # a named third virtual
    if len({m.group(1) for m in ms}) != 1: return False
    xs = [int(m.group(2)) for m in ms]
    step = xs[1] - xs[0]
    if not ((step == 1 and xs[0] in (0, 1)) or (step == 4 and xs[0] == 0)): return False
    return len(xs) < 3 or xs[2] - xs[1] == step

def _d22_callonly(masked, c, unit=True, named_ok=False):
    """D26 (b): class c of d22_classes is a call-only placeholder declaration in this file (above); named_ok: the
    slot is known from the exe (D22-SLOTK), so its member's name is not tested"""
    if c['bases'] or not c['first'] or len(c['virtuals']) < 2: return False
    if c['dtor'] and not c.get('dtor_decl'): return False
    if c['dtor'] and _d22_search(r'(?<![\w~:])%s\s*(?:<[^;{}()]*>\s*)?::\s*~\s*%s\s*\(' % ((re.escape(c['qual'][-1]),) * 2),
                                 c['qual'][-1], masked):
        return False                                     # `~C();` defined in the file: a real destructor
    if not c['first'][2]: return False
    if not named_ok and not D22_CALLONLY_PLACEHOLDER.match(c['first'][2]) and not _d22_numbered(c):
        return False
    # an empty inline body (`virtual void f00() {}`) or one that only returns a constant (`{ return 0; }`) is a
    # placeholder too, not a definition (EQ, link 0540aff0: tesobjectrefr.cpp's FUN_00567f40_class, 19 such slots;
    # S2-bodies' PHSLOT1 makes the empty ones declarations as well)
    if any('{' in masked[v[0]:v[1]] and not (unit and D22_EMPTY_BODY.search(masked[v[0]:v[1]])) for v in c['virtuals']):
        return False                                     # (a header's includers could build the class: .cpp only)
    name = c['qual'][-1]
    members = {v[2] for v in c['virtuals'] if v[2]}
    # S5-boot (link af44165ab2d8, NiAlloc corruption): an out-of-class definition with an empty body
    # (`inline void S_00aa1420_Object::S_00aa1420_v1(...) {}`) is a placeholder too, as an empty inline body is
    outside = []
    for m in _d22_out_of_class_defs(masked, name):
        if m.group('mem') not in members: continue
        if not (unit and D22_EMPTY_BODY.search(m.group(0))): return False
        outside.append(m)
    if outside or any('{' in masked[v[0]:v[1]] for v in c['virtuals']):
        # with inline bodies GCC can build the class (its vtable from those bodies); once `virtual ~C()` is the key
        # function nothing defines, a construction would need it. Only a class the file never uses as an object:
        # its name only as `C*`, `C&`, `C::`, in its own body or after `struct` / `class`
        nm = c['qual'][-1]
        for u in _d22_finditer(r'(?<![\w~:])%s\b' % re.escape(nm), nm, masked):
            if c['open'] < u.start() < c['close']: continue
            if re.match(r'\s*(?:\*|&|::)', masked[u.end():u.end() + 4]): continue
            if re.search(r'(?:struct|class)\s+$', masked[max(0, u.start() - 12):u.start()]): continue
            return False
    spans = [(m.start(), m.end()) for m in outside]
    for m in _d22_finditer(r'(?<![\w~])%s\s*(?:<[^;{}()]*>\s*)?::\s*(~?\s*[A-Za-z_]\w*)\s*\(' % re.escape(name), name, masked):
        if any(a <= m.start() < b for a, b in spans): continue
        if re.sub(r'\s+', '', m.group(1)) in members: return False
    return True


_D22_OOC = {}


def _d22_out_of_class_defs(masked, name):
    r"""the out-of-class definitions `[inline] T C::member(...) [const] { ... }` of class `name` whose body has no
    nested brace (the whole definition in group 0, the member in 'mem').
    T1 (prepare 16 -> 52 min on bl1: r_vtable_dtor_slot 75 -> 1,840 s): the pattern is anchored at a line start and
    was tried at every line of the file for every class. A match's `name` is a whole word on the line it starts (the
    prefix `[\w:<>*& \t]*?` crosses no newline, `(?<![\w~])` and `[ \t]*::` bound the word), so only the starts of
    the lines holding the word `name` (the file's word index, _d22_index) are tried, in order, a start inside the
    previous match skipped: the matches re.finditer gives, found without the scan. A name that is not a plain
    identifier keeps the scan."""
    pat = _D22_OOC.get(name)
    if pat is None:
        if len(_D22_OOC) > 4096:
            _D22_OOC.clear()
        pat = _D22_OOC[name] = re.compile(
            r'(?m)^[ \t]*(?:inline[ \t]+|__forceinline[ \t]+)?[\w:<>*& \t]*?(?<![\w~])%s[ \t]*::[ \t]*'
            r'(?P<mem>[A-Za-z_]\w*)[ \t]*\((?:[^()]|\([^()]*\))*\)[ \t]*(?:const[ \t]*)?\s*\{[^{}]*\}'
            % re.escape(name))
    if not re.fullmatch(r'[A-Za-z_]\w*', name):
        return pat.finditer(masked)
    return _d22_ooc_matches(pat, masked, _d22_index(masked)[0].get(name, ()))


def _d22_ooc_matches(pat, masked, pos):
    last, done = 0, -1
    for p in pos:
        ls = masked.rfind('\n', 0, p) + 1
        if ls < last or ls == done:
            continue
        done = ls
        m = pat.match(masked, ls)
        if m:
            yield m
            last = m.end()

def _d22_defines_none(masked, c):
    """class c defines no member function in the file (no body in the class, no `C::member(` definition) and
    declares no destructor: GCC emits neither its vtable nor its destructor here"""
    if c['dtor'] or '{' in masked[c['open'] + 1:c['close']]: return False
    return not re.search(r'(?<![\w~:])%s\s*(?:<[^;{}()]*>\s*)?::\s*~?\s*[A-Za-z_]\w*\s*\([^;{}]*\)[^;{}]*\{'
                         % re.escape(c['qual'][-1]), masked)

D22_REF = re.compile(r'(?:::|->|\.)\s*(~?[A-Za-z_]\w*)\s*\(')
D22_SRC_EXT = ('.cpp', '.c', '.h', '.inl', '.hpp', '.cxx', '.cc')

D22_INCLUDE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*[<"]([^>"]+)[>"]', re.M)

def _d22_tree_refs(path):
    """The tree index for header edits, cached per tree root: (refs, includers) where refs = {member name: {file}}
    of the files that define (`::m(`) or call (`->m(`, `.m(`) a member of that name, and includers(h) = the files
    that include the header h, directly or through other headers (by lower-case basename, as the decomp's includes
    resolve case-insensitively). A header's declaration is seen only by the units that include it."""
    root, _ = prepare._units_json(path)
    top = root or os.path.dirname(os.path.abspath(path))
    cache = prepare.__dict__.setdefault('_fnvnx_d22_refs', {})
    if top in cache: return cache[top]
    refs = collections.defaultdict(set)
    inc_by = collections.defaultdict(set)              # included basename -> files including it
    base_of = collections.defaultdict(set)             # basename -> files of that name
    for dp, _, fs in os.walk(os.path.join(top, 'src') if os.path.isdir(os.path.join(top, 'src')) else top):
        for f in fs:
            if not f.endswith(D22_SRC_EXT): continue
            fp = os.path.abspath(os.path.join(dp, f))
            try: s = open(fp, encoding='utf-8', errors='surrogateescape').read()
            except OSError: continue
            base_of[f.lower()].add(fp)
            for m in D22_REF.finditer(s): refs[m.group(1)].add(fp)
            for m in D22_INCLUDE.finditer(s): inc_by[os.path.basename(m.group(1).replace('\\', '/')).lower()].add(fp)
    memo = {}
    def includers(h):
        h = os.path.abspath(h)
        if h in memo: return memo[h]
        seen, todo = set(), [os.path.basename(h).lower()]
        done_b = set()
        while todo:
            b = todo.pop()
            if b in done_b: continue
            done_b.add(b)
            for fp in inc_by.get(b, ()):
                if fp not in seen:
                    seen.add(fp)
                    if not fp.endswith(('.cpp', '.c', '.cxx', '.cc')): todo.append(os.path.basename(fp).lower())
        memo[h] = seen
        return seen
    cache[top] = (refs, includers)
    return cache[top]

def _d22_spec_only(path, member, cls, users):
    """every use of member in users is a member of an explicit specialization `template <> class cls<A>` that the
    header itself declares (nitmapbase_decls.h's NiTMapBase<DFALL<...>, ...>::Destructor(unsigned) in
    nitmapbase.inl is that class's own member, not the primary template's slot-0 placeholder: S5, 8deb903cbe85,
    GetAt's Hash call read native slot 1 = D0)"""
    try: hdr = d22_mask(open(path, encoding='utf-8', errors='surrogateescape').read())
    except OSError: return False
    norm = lambda a: re.sub(r'\s+', '', a)
    specs = set()
    for m in re.finditer(r'\btemplate\s*<\s*>\s*(?:class|struct)\s+%s\s*<' % re.escape(cls), hdr):
        a, d, j = m.end(), 1, m.end()
        while j < len(hdr) and d:
            d += {'<': 1, '>': -1}.get(hdr[j], 0); j += 1
        specs.add(norm(hdr[a:j - 1]))
    if not specs: return False
    for f in users:
        try: t = d22_mask(open(f, encoding='utf-8', errors='surrogateescape').read())
        except OSError: return False
        for m in re.finditer(r'(?<![\w~])%s\s*\(' % re.escape(member), t):
            w = t[max(0, m.start() - 400):m.start()]
            qs = list(re.finditer(r'\b%s\s*<' % re.escape(cls), w))
            if not qs: return False
            a, d, j = qs[-1].end(), 1, qs[-1].end()
            while j < len(w) and d:
                d += {'<': 1, '>': -1}.get(w[j], 0); j += 1
            if d or not re.fullmatch(r'\s*::\s*', w[j:]) or norm(w[a:j - 1]) not in specs: return False
    return True


def _d22_extern_use(path, member, cls=None):
    """a header's member name that a file including the header (directly or not) defines or calls (try1of1
    4a15e91c: `NiPosData::LoadBinary` defined in niposdata.cpp lost its declaration in niposdata_local.h;
    bgsprojectile.cpp called TESModelVTable::Slot0); .cpp declarations are the unit's own. A file that does not
    include the header cannot see the edit (EQ vslot-shift: teswater_work.h's TESObjectREFR was held back because
    unrelated files call some other class's vfn_0)"""
    if path.endswith(('.cpp', '.c', '.cxx', '.cc')) or not member: return False
    me = os.path.abspath(path)
    refs, includers = _d22_tree_refs(path)
    inc = includers(me)
    users = [f for f in refs.get(member, ()) if f != me and f in inc]
    if users and not (cls and _d22_spec_only(path, member, cls, users)): return True
    # an unqualified call in an including file (magiccaster.cpp's members call `SpellCast(...)` of the
    # magiccaster_match.h MagicCaster that D22-CL converted at the exe's dtor slot 9: the unit stopped compiling,
    # full3 74a1c281). The files' texts are read once per tree (cached with the index).
    uq = prepare.__dict__.setdefault('_fnvnx_d22_uq', {})
    rx = re.compile(r'(?<![\w.>:~])%s\s*\(' % re.escape(member))
    for f in inc:
        if f == me: continue
        t = uq.get(f)
        if t is None:
            try: t = uq[f] = d22_mask(open(f, encoding='utf-8', errors='surrogateescape').read())
            except OSError: t = uq[f] = ''
        if rx.search(t): return True
    return False

def r_vtable_dtor_slot(path, text, stats):
    """D22: `virtual ~C();` for a listed class's slot-0 placeholder under GCC (above); a derived class's
    re-declaration of that placeholder is dropped under GCC (it would append a slot)."""
    lst = _d22_list()
    if 'virtual' not in text: return text
    masked = d22_mask(text)
    classes = d22_classes(text, masked)
    edits, done = [], {}                        # done: simple class name (or T<>) -> placeholder member
    vdecls = collections.defaultdict(list)      # member -> spans of every virtual declaration of that name
    for c in classes:
        for v in c['virtuals']:
            if v[2]: vdecls[v[2]].append((v[0], v[1]))
    calls, masked_nc, needs = {}, {}, collections.defaultdict(list)
    reach = d22_reach(masked, classes)
    dtor_drops = {}                             # first-virtual start -> span of the class's `~C();` (dropped)
    applied = {}                                # edit start -> (class key, member, kind): build/d22_applied.tsv
    def statement_calls(ph):
        # `EXPR->ph(flags);` as a statement: the deleting-destructor call D26 turns into FNVNX_D22_CALL
        if ph not in calls:
            calls[ph] = [m for m in re.finditer(r'(?<=[;{}\n])([ \t]*)((?:this|[A-Za-z_]\w*)(?:\s*(?:->|\.)\s*[A-Za-z_]\w*)*)'
                                                r'\s*->\s*%s\s*\(\s*((?:0x)?[0-9]+[uU]?)\s*\)\s*;' % re.escape(ph), masked)]
            nc = masked
            for m in calls[ph]: nc = nc[:m.start(2)] + ' ' * (m.end() - m.start(2)) + nc[m.end():]
            masked_nc[ph] = nc
        return calls[ph]
    for c in classes:
        key = c['qual'][-1] + '<>' if c['template'] else '::'.join(c['qual'])
        if c['first'] and _d22_com(masked, c):
            if key in lst or (not c['bases'] and len(c['virtuals']) >= 2): stats['D22-COM'] += 1
            continue
        cl = _classlayout_row(path, c['qual'][-1]) if c['first'] and not c['bases'] else None
        if cl is not None:
            # D22-CL: the exe decides (S2's classlayout.tsv, orchestrator 2026-11-08): exe-order / com keep the
            # declaration as it is; minus-one puts `virtual ~C()` at the exe's deleting-destructor slot k
            kind, k = cl
            if kind != 'minus-one' or k < 0:
                stats['D22-CL-KEEP'] += 1; continue
            if len(c['virtuals']) <= k:
                # the declaration ends before the exe's destructor slot: its slots are the exe's already (k' < k
                # stays in a minus-one table at k)
                stats['D22-CL-BELOW'] += 1; continue
            if c['dtor'] and not c.get('dtor_decl'):
                vd = next((i for i, v in enumerate(c['virtuals']) if (v[2] or '').startswith('~')), None)
                # a declared virtual destructor: right where the exe has its deleting destructor, else a layout
                # difference of the class definition (S2-bodies' DBUG1 side), counted
                stats['D22-CL-ASIS' if vd == k else 'D22-CL-DTORPOS'] += 1; continue
            t = c['virtuals'][k]
            ck = dict(c, first=t)
            blockers = [o for o in classes if o['bases'] and c['qual'][-1] in _d22_bases(o['bases'])
                        and not _d22_defines_none(masked, o)]
            if any(v[2] == t[2] for o in blockers for v in o['virtuals']) or \
                    _d22_extern_use(path, t[2], c['qual'][-1] if c['template'] else None) or \
                    not _d22_callonly(masked, ck, path.endswith(('.cpp', '.c', '.cxx', '.cc')), named_ok=True) or \
                    not d22_placeholder(masked, c['qual'][-1], t, vdecls[t[2]], reach(c['qual'][-1])):
                stats['D22-CL-HELD'] += 1; continue
            edits.append((t[0], t[1], '%s(%s, %s)' % ('FNVNX_D22I' if blockers else 'FNVNX_D22', c['qual'][-1], t[3])))
            applied[t[0]] = (key, t[2], 'classlayout-slot%d' % k)
            if c['dtor'] and c.get('dtor_decl'): dtor_drops[t[0]] = c['dtor_decl']
            done[c['qual'][-1] + ('<>' if c['template'] else '')] = t[2]
            stats['D22-CL'] += 1
            continue
        k = _d22_dtor_slot(path, c['qual'][-1])
        if not k and c['first'] and _rawvt_exe_order().get(c['qual'][-1], set()) & D22_EXE_ORDER_REASONS:
            # D22-EXEORDER (S2-bodies seamcheck V3 / V4): the link keeps this class's table in exe order, so its
            # callers keep the exe's numbering
            stats['D22-EXEORDER'] += 1; continue
        if k and c['first'] and not c['bases']:
            # D22-SLOTK: the exe's deleting destructor is at slot k (slot 0 a trivial function: the TESForm family,
            # k = 4): the k-th virtual, not the first, becomes `virtual ~C()`; nothing at slot 0
            if len(c['virtuals']) <= k or c['dtor']:
                stats['D22-SLOTK-HELD'] += 1; continue
            t = c['virtuals'][k]
            ck = dict(c, first=t)
            if not _d22_callonly(masked, ck, path.endswith(('.cpp', '.c', '.cxx', '.cc')), named_ok=True) or \
                    any(c['qual'][-1] in _d22_bases(o['bases']) and not _d22_defines_none(masked, o)
                        for o in classes if o['bases']) or _d22_extern_use(path, t[2]) or \
                    not d22_placeholder(masked, c['qual'][-1], t, vdecls[t[2]], reach(c['qual'][-1])):
                stats['D22-SLOTK-HELD'] += 1; continue
            edits.append((t[0], t[1], 'FNVNX_D22(%s, %s)' % (c['qual'][-1], t[3])))
            applied[t[0]] = (key, t[2], 'slot%d' % k)
            done[c['qual'][-1] + ('<>' if c['template'] else '')] = t[2]
            stats['D22-SLOTK'] += 1
            continue
        callonly = key not in lst and _d22_callonly(masked, c, path.endswith(('.cpp', '.c', '.cxx', '.cc')))
        inline_dtor = False
        if callonly:
            blockers = [o for o in classes if o['bases'] and c['qual'][-1] in _d22_bases(o['bases'])
                        and not _d22_defines_none(masked, o)]
            if blockers:
                # D22-INLINE (S2-bodies seamcheck V4: TerrainBase in hkpphysicssystem.cpp): a derived class of the file
                # defines members, so its destructor would call the base's undefined `~C`. When no such class declares
                # the placeholder member itself, the base takes an inline empty `virtual ~C() {}` under GCC
                # (FNVNX_D22I): the derived destructors call it, the slots stay minus-one.
                ph0 = c['first'][2]
                if any(v[2] == ph0 for o in blockers for v in o['virtuals']) or \
                        re.search(r'(?<![\w~])%s\s*\(' % re.escape(ph0),
                                  ''.join(masked[o['open']:o['close']] for o in blockers)):
                    callonly = False
                else:
                    inline_dtor = True
        if not callonly:
            if key not in lst or not c['first']: continue
            if c['bases'] or c['dtor'] or c['first'][2] not in lst[key]: continue
        if _d22_extern_use(path, c['first'][2]) or \
                (callonly and c['dtor'] and _d22_extern_use(path, '~' + c['qual'][-1])):
            stats['D22-EXTERN'] += 1; continue
        rc = reach(c['qual'][-1])
        if not d22_placeholder(masked, c['qual'][-1], c['first'], vdecls[c['first'][2]], rc):
            ph = c['first'][2]
            if not (callonly and statement_calls(ph) and
                    d22_placeholder(masked_nc[ph], c['qual'][-1], c['first'], vdecls[ph], rc)):
                stats['D22-USED'] += 1; continue
            needs[ph].append(len(edits))
        edits.append((c['first'][0], c['first'][1], '%s(%s, %s)' % ('FNVNX_D22I' if inline_dtor else 'FNVNX_D22',
                                                                       c['qual'][-1], c['first'][3])))
        applied[c['first'][0]] = (key, c['first'][2], 'callonly' if callonly else 'listed')
        if inline_dtor: stats['D22-INLINE'] += 1
        if callonly and c['dtor']: dtor_drops[c['first'][0]] = c['dtor_decl']
        done[c['qual'][-1] + ('<>' if c['template'] else '')] = c['first'][2]
        stats['D22-CALLONLY' if callonly else 'D22'] += 1
    drop = set()
    for ph, idx in needs.items():
        # the calls are rewritten only when every class of the file that declares a virtual `ph` has it converted
        conv = {edits[i][0] for i in range(len(edits)) if edits[i][2].startswith(('FNVNX_D22(', 'FNVNX_D22I('))}
        if all(v[0] in conv for cc in classes for v in cc['virtuals'] if v[2] == ph):
            for m in calls[ph]:
                edits.append((m.start(2), m.end(), 'FNVNX_D22_CALL(%s, %s, %s);' % (text[m.start(2):m.end(2)], ph,
                                                                                  text[m.start(3):m.end(3)].strip())))
                stats['D22-CALL'] += 1
        else:
            drop.update(idx)
            stats['D22-USED'] += len(idx)
    if drop:
        for i in drop:
            nm = re.match(r'FNVNX_D22I?\((\w+),', edits[i][2]).group(1)
            done.pop(nm, None); done.pop(nm + '<>', None)
            stats['D22-CALLONLY'] -= 1
        edits = [e for i, e in enumerate(edits) if i not in drop]
    for e in list(edits):
        # D22-DEFDROP: a converted placeholder is `virtual ~C()` under GCC now (every path: call-only, listed,
        # classlayout); its empty out-of-class definition goes too (S5 link 17f5b96e33fe: tesobjectcell.cpp's
        # hkpEntityListener::FUN_004f0000 and blk_00b00000_od.cpp's BSWin32TaskletManager::vf00 stayed defined)
        cm = re.match(r'FNVNX_D22I?\((\w+), (.*)\)$', e[2], re.S)
        if not cm: continue
        mm = re.search(r'([A-Za-z_]\w*)\s*\(', cm.group(2))
        if not mm: continue
        for d in _d22_out_of_class_defs(masked, cm.group(1)):
            if d.group('mem') == mm.group(1) and D22_EMPTY_BODY.search(d.group(0)) and \
                    not any(x[0] == d.start() for x in edits):
                edits.append((d.start(), d.end(), 'FNVNX_D22_DROP(%s)' % text[d.start():d.end()].strip('\n')))
                stats['D22-DEFDROP'] += 1
    for e in list(edits):
        # D22-DTOR: the class's own body-less `~C();` goes under GCC (`virtual ~C()` declares it now)
        if e[0] in dtor_drops and e[2].startswith(('FNVNX_D22(', 'FNVNX_D22I(')):
            a, b = dtor_drops[e[0]]
            edits.append((a, b, 'FNVNX_D22_DROP(%s)' % text[a:b]))
            stats['D22-DTOR'] += 1
    here = {c['qual'][-1] + ('<>' if c['template'] else '') for c in classes}
    for k, ms in lst.items():                   # listed classes defined in another file (a header)
        sk = k.split('::')[-1]
        if sk not in done and len(ms) == 1 and sk not in here: done.setdefault(sk, next(iter(ms)))
    changed = True
    while changed:                              # derived classes, through chains of derivation in the file
        changed = False
        for c in classes:
            if not c['bases'] or not c['virtuals'] or _d22_com(masked, c): continue
            mine = c['qual'][-1] + ('<>' if c['template'] else '')
            for b in _d22_bases(c['bases']):
                mem = done.get(b)
                if not mem: continue
                for v in c['virtuals']:
                    if v[2] != mem or any(e[0] == v[0] for e in edits): continue
                    if _d22_extern_use(path, v[2]):
                        stats['D22-EXTERN'] += 1; continue
                    if not d22_placeholder(masked, c['qual'][-1], v, vdecls[v[2]], reach(c['qual'][-1])):
                        stats['D22-USED'] += 1; continue
                    edits.append((v[0], v[1], 'FNVNX_D22_DROP(%s)' % v[3]))
                    applied[v[0]] = ('::'.join(c['qual']) + ('<>' if c['template'] else ''), v[2], 'derived:' + b)
                    stats['D22-DERIVED'] += 1
                    if mine not in done: done[mine] = mem; changed = True
    if not edits: return text
    out, pos = [], 0
    rec = prepare.__dict__.setdefault('_fnvnx_d22_applied', [])
    i = path.replace('\\', '/').rfind('/src/')
    rel = path.replace('\\', '/')[i + 1:] if i >= 0 else os.path.basename(path)
    for a, b, rep in sorted(edits):
        if a < pos: continue
        if a in applied and rep.startswith(('FNVNX_D22(', 'FNVNX_D22I(', 'FNVNX_D22_DROP(')):
            rec.append((rel,) + applied[a])
        out.append(text[pos:a]); out.append(rep + ' /* fnv-nx D22 */'); pos = b
    out.append(text[pos:])
    return ''.join(out)

def d22_write_applied(out_root, stats, files):
    """<out>/build/d22_applied.tsv: every class the D22 / D26 rule converted, as prepare decided (S2-bodies: the link
    rebuilds minus-one tables from it instead of re-deriving the decisions from the objects). Columns: file (tree
    path), class (qualified; T<> for a template), member (the slot-0 placeholder, or the dropped re-declaration),
    kind (listed = d22.json, callonly = D26 (b), derived:<base> = a dropped re-declaration), vftables (d22.json's,
    for a listed class; empty otherwise: a call-only declaration names no exe vftable). Names and addresses only."""
    rows = prepare.__dict__.get('_fnvnx_d22_applied') or []
    lst = {}
    import json
    for f in _d22_files():
        try: d = json.load(open(f))
        except (OSError, ValueError): continue
        for c in d.get('classes', ()): lst.setdefault(c['class'], set()).update(c.get('vftables', ()))
    os.makedirs(os.path.join(out_root, 'build'), exist_ok=True)
    with open(os.path.join(out_root, 'build', 'd22_applied.tsv'), 'w') as f:
        f.write('# fnvnx-d22-applied/1\tfile\tclass\tmember\tkind\tvftables\n')
        for r in sorted(set(rows)):
            vt = ','.join(sorted(lst.get(r[1], ()))) if r[3] == 'listed' else ''
            if _classlayout() and not r[3].startswith(('classlayout', 'derived')):
                r = r[:3] + ('fallback:' + r[3],)              # decided without an exe row (orchestrator: listed)
            f.write('%s\t%s\t%s\t%s\t%s\n' % (r + (vt,)))
    stats['D22-APPLIED'] += 0

POST.append(d22_write_applied)

# ---------------------------------------------------------------------------------------------------------- VSPLIT1
#
# A virtual declaration with several declarators, `virtual void v000(), v004(), v008();` (EQ, farm baseline 588863:
# lowprocess.cpp's ActorX v000..v228, nilookatinterpolator.cpp's NiAVObjectPalette v0..v34, bsbatchrenderer,
# testerrainlodmanager: 52 sites in 23 files). D22 / D26 took the whole list for one virtual: FNVNX_D22 dropped every
# placeholder of it under GCC (0x0090f670 read [x2, #8] where the exe reads [edx + 0x22c]), and the virtual counts that
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
        spec = re.sub(r'[\s\*&]+$', '', text[m.start():m.start() + len(dm.group(1))]) + ' '
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

# ---------------------------------------------------------------------------------------------------------- HKRO1
#
# Havok's hkReferencedObject declares, after its destructor, `getClassType()` and `calcContentStatistics(collector,
# cls)`: every hkReferencedObject-derived exe vftable has 0x00c90350 / 0x00c90360 at slots 1 / 2 (R4 vtgate abi_check:
# hkpWeldingViewer 010d1634 slot 3 is 0x00c90360; S2-bodies W32: 11 Havok tables one slot off from slot 2 on). 17 units
# declare hkReferencedObject with the destructor only (9 have the full form, 12 the two without the destructor), so
# their derived classes number every own virtual two slots early. HKRO1 adds the two declarations after the destructor
# under GCC (FNVNX_HKRO1; empty under MSVC, whose objects stay as they are), with `void` parameter types (the unit may
# not declare hkClass): declared-only slots, which the link fills from the exe's. Only a class named
# hkReferencedObject whose virtuals are exactly its destructor.
def r_hk_referenced_object_slots(path, text, stats):
    """HKRO1: hkReferencedObject's two missing base virtuals after its destructor (see above)."""
    if 'hkReferencedObject' not in text: return text
    masked = d22_mask(text)
    edits = []
    for c in d22_classes(text, masked):
        if c['qual'][-1] != 'hkReferencedObject': continue
        if [v[2] for v in c['virtuals']] != ['~hkReferencedObject']: continue
        edits.append(c['virtuals'][0][1])
    for at in sorted(edits, reverse=True):
        text = text[:at] + ' FNVNX_HKRO1 /* fnv-nx HKRO1 */' + text[at:]
    stats['HKRO1'] += len(edits)
    return text

# ---------------------------------------------------------------------------------------------------------- EMPTY1
#
# A placeholder object for an empty string (R2's trap list, rank 32: fnv_00b2d2e0::TagEmpty). The decomp writes the
# `sys == false` arm of the runtime-error constructor FUN_00b09480 (treeengine units) as `extern const TagText TagEmpty;`,
# defined nowhere: the native link has no data at that name (a trap). The exe's 0x00b09480 builds that arm from the
# empty literal at 0x01011584 through 0x0044a4c0 (TagText's constructor), a temporary like the other arm. EMPTY1
# writes a use of such a name as `FNVNX_EMPTY1(T, Name)`: `T("")` under GCC, the original name under MSVC
# (fnvnx_equiv_fi.h), only where the file declares `extern const T Name;` (Name ends with `Empty`), never defines
# it, and declares `T(const char*)` in T.
EMPTY1_DECL = re.compile(r'^[ \t]*extern[ \t]+const[ \t]+([A-Za-z_]\w*)[ \t]+([A-Za-z_]\w*Empty)[ \t]*;', re.M)

def r_empty_string_objects(path, text, stats):
    """EMPTY1: a declared-only empty-string object -> FNVNX_EMPTY1(T, Name) (see above)."""
    if 'Empty' not in text: return text
    masked = d22_mask(text)
    for m in list(EMPTY1_DECL.finditer(masked)):
        t, nm = m.group(1), m.group(2)
        if re.search(r'(?<![\w:])(?:const\s+)?%s\s+(?:\w+::)*%s\s*(?:\(|=|\{)' % (re.escape(t), re.escape(nm)), masked):
            continue                                            # defined in the file
        cls = [c for c in d22_classes(text, masked) if c['qual'][-1] == t]
        if len(cls) != 1 or not re.search(r'(?<![\w~])%s\s*\(\s*const\s+char\s*\*\s*\w*\s*\)' % re.escape(t),
                                          masked[cls[0]['open']:cls[0]['close']]):
            continue
        uses = [u for u in re.finditer(r'(?<![\w:.>])%s\b' % re.escape(nm), masked) if not (m.start() <= u.start() < m.end())]
        for u in reversed(uses):
            text = text[:u.start()] + 'FNVNX_EMPTY1(%s, %s)' % (t, nm) + text[u.end():]
        stats['EMPTY1'] += len(uses)
        masked = d22_mask(text)
    return text

# ---------------------------------------------------------------------------------------------------------- RAWVT1
#
# D26 (S5-boot, link 3f7a6c35e923): every native C++ vtable is minus-one (GCC's D1 / D0 at slots 0 / 1 where MSVC
# has ??_G at 0; the link rebuilds pair-less tables as minus-one copies, orchestrator decision 2026-11-08). The
# decomp also dispatches by a raw exe slot number, `((F)(*reinterpret_cast<void***>(this))[k])(this, ...)`, which no
# declaration carries: FUN_005e0830::Open (0x005e0830) called the INI collection's exe slot 6 (Open) at native
# offset 24, ran slot 5 with the bool as the setting pointer, and fallout.ini was never read. RAWVT1 writes such an
# index on `this` as FNVNX_D26_SLOT(k): k + 1 under GCC, k under MSVC (tools/equiv/msvc/fnvnx_equiv_fi.h).
# Only where:
# - the receiver is `this` (the object of the enclosing member function: a C++ object of the game);
# - the enclosing class is not COM-shaped (D22-COM: a __stdcall / HRESULT virtual, QueryInterface / AddRef /
#   Release, an SDK interface name; COM vtables have no destructor slot), as far as the file defines it;
# - k > 0 (slot 0 is the deleting destructor: a call of it needs FNVNX_D22_CALL's form; counted RAWVT1-SLOT0);
# - a class whose exe deleting destructor is at slot K > 0 (D22-SLOTK, the TESForm family) shifts only k > K
#   (k < K: RAWVT1-BELOWK, k = K: RAWVT1-SLOT0);
# - the class is not one whose table the link keeps in exe order (S2-bodies' exe_order_classes.tsv, read from
#   FNVNX_EXE_ORDER or under FNVNX_RECON; RAWVT1-EXEORDER).
RAWVT1_KNUM = r'(?:0[xX][0-9a-fA-F]+|[0-9]+)(?:\s*/\s*(?:4|sizeof\s*\(\s*void\s*\*\s*\)))?'   # a slot, or a byte offset / 4 (`0x128 / 4`)


def _kval(t):
    """the slot number of a RAWVT1_KNUM text"""
    m = re.fullmatch(r'\s*(0[xX][0-9a-fA-F]+|[0-9]+)\s*(?:/\s*(?:4|sizeof\s*\(\s*void\s*\*\s*\)))?\s*', t)
    v = int(m.group(1), 0)
    return v // 4 if '/' in t else v


RAWVT1_RX = re.compile(r'(\(\s*\*\s*(?:reinterpret_cast\s*<\s*void\s*\*\s*\*\s*\*\s*>\s*\(\s*this\s*\)|'
                       r'\(\s*void\s*\*\s*\*\s*\*\s*\)\s*this)\s*\)\s*\[\s*)((?:0[xX][0-9a-fA-F]+|[0-9]+)(?:\s*/\s*4)?)(\s*\])')

def _rawvt_exe_order():
    """{class simple name} whose native table the link keeps in exe order (S2-bodies' out/exe_order_classes.tsv:
    class, vftable, reason), from FNVNX_EXE_ORDER or any exe_order_classes.tsv under FNVNX_RECON; cached on prepare"""
    got = prepare.__dict__.get('_fnvnx_exe_order')
    if got is not None: return got
    files = [os.environ['FNVNX_EXE_ORDER']] if os.environ.get('FNVNX_EXE_ORDER') else []
    top = os.environ.get('FNVNX_RECON')
    if top and os.path.isdir(top):
        files += sorted(os.path.join(dp, 'exe_order_classes.tsv') for dp, _, ns in os.walk(top) if 'exe_order_classes.tsv' in ns)
    got = {}
    for f in files:
        try:
            for line in open(f):
                if line.startswith('#') or not line.strip(): continue
                fs = line.rstrip('\n').split('\t')
                if fs[0] in ('class',): continue                      # a header row
                got.setdefault(fs[0].strip().split('::')[-1], set()).add(fs[2].strip() if len(fs) > 2 else '')
        except OSError: continue
    prepare.__dict__['_fnvnx_exe_order'] = got
    return got

# reasons of exe_order_classes.tsv for which a declaration of that class must keep the exe's slot numbers (D22 / D26
# do not convert it): the link keeps the table in exe order because slot 0 is a defined function (BSNiAllocator::
# Destroy, TESLeveledList, ListBox<>, Tile3D...) or is not a destructor at all
D22_EXE_ORDER_REASONS = {'defined-slot0', 'd26-slot0-not-a-destructor', 'd22-used', 'com'}

# RAWVT1-MEM (S5-boot, link af44165ab2d8: the heap ctor dyninit 00fb66a0 overflowed the stack): the decomp also models
# the vptr as the class's first data member, `void** m_ppVirtualMethodTable;` (or vtable / vtbl / m_vtbl / m_pVtbl /
# vptr), and indexes it on `this`: BSDiscBootSystemUtility::Initialize (0x00af3aa0) calls m_ppVirtualMethodTable[2],
# exe slot 2 = BSSystemUtility::Destroy 0x00af3250 (exe [edx+8] at 0x00af3aaf); natively [vptr+8] is slot 2 of the
# minus-one table, Initialize itself. The same index on such a member, bare or `this->`, inside a member function of
# its class, or on a local initialised from `*(void***)this` / `*reinterpret_cast<void***>(this)`, gets
# FNVNX_D26_SLOT(k) under RAWVT1's conditions. Not when the class in the file declares a virtual function (its first
# data member is then not the vptr), the name is a local or parameter of another kind (RAWVT1-MEM-LOCAL), or the slot
# read is not called (RAWVT1-MEM-DATA: a cast to a builtin type, no call of the variable it is stored in).
RAWVT1_MEMBERS = r'(?:m_ppVirtualMethodTable|vtable|vtbl|m_vtbl|m_pVtbl|vptr|_vtable|vt|pVtbl|pVtable|m_pVtable|vfptr|__vfptr)'
RAWVT1_MEM_RX = re.compile(r'(?<![\w.>:])((?:this\s*->\s*)?(%s)\s*\[\s*)(%s)(\s*\])' % (RAWVT1_MEMBERS, RAWVT1_KNUM))
RAWVT1_THIS_VPTR = re.compile(r'^\s*\*\s*(?:reinterpret_cast\s*<\s*void\s*\*\s*\*\s*\*\s*>\s*\(\s*this\s*\)|'
                              r'\(\s*void\s*\*\s*\*\s*\*\s*\)\s*\(?\s*this\s*\)?)\s*;')


def _rawvt_mem_ok(masked, body_open, pos, name):
    """True when `name` at pos is the object's vptr: not declared in the function before pos, or declared as a local
    initialised from *(void***)this"""
    seg = masked[body_open:pos]
    sig_start = masked.rfind('\n', 0, body_open)
    head = masked[max(0, sig_start - 400):body_open]
    if re.search(r'[\w*&>\s]\b%s\s*[,)]' % re.escape(name), head[head.rfind('('):] if '(' in head else ''):
        return False                                                       # a parameter of that name
    d = None
    for d in re.finditer(r'\b(?:void\s*\*\s*\*|void\s*\*\s*const\s*\*|auto|[A-Za-z_]\w*\s*\*+)\s*%s\s*(=|;|\[)'
                         % re.escape(name), seg):
        pass
    if d is None:
        return True
    if d.group(1) != '=':
        return False
    return bool(RAWVT1_THIS_VPTR.match(seg[d.end():d.end() + 120]))


_RAWVT_BUILTIN = re.compile(r'^\s*(?:(?:const|unsigned|signed|int|long|short|char|bool|float|double|void|'
                            r'size_t|DWORD|UINT|INT|BOOL)\b\s*\**\s*)+$')


def _rawvt_mem_called(masked, m):
    """True when the slot read at m is called: under a cast to a function pointer or a typedef'd type (not a builtin:
    blk_00cc0000's FUN_00cd9f10 reads `(int)m_ppVirtualMethodTable[7]`, a count behind a data pointer, exe
    [[esi]+0x1c] * 48), or stored in a variable that the function then calls"""
    if re.match(r'\s*(?:\)\s*)*\(', masked[m.end():m.end() + 12]):
        return True                                       # `vtbl[26](object, ...)`: the slot is called directly
    before = masked[max(0, m.start() - 200):m.start()]
    before = before[max(before.rfind(';'), before.rfind('{'), before.rfind('}')) + 1:]
    c = re.search(r'(?:reinterpret_cast\s*<((?:[^<>]|<[^<>]*>)*)>|\(((?:[^()]|\([^()]*\))*)\))\s*\(?\s*$', before)
    if c:
        t = c.group(1) or c.group(2)
        return '(' in t or not _RAWVT_BUILTIN.match(t)
    v = re.search(r'([A-Za-z_]\w*)\s*=\s*$', before)
    if v:
        after = masked[m.end():m.end() + 600]
        return bool(re.search(r'(?:\b%s\s*\)?\s*\(|\(\s*\([^()]*\)\s*%s\s*\)\s*\()' % (re.escape(v.group(1)), re.escape(v.group(1))), after))
    return False


# RAWVT1-RECV (S5-boot, orchestrator: close the raw-slot class over the tree): the same index on another receiver X
# (`X->vtbl[k]`, `X.m_ppVirtualMethodTable[k]`, `(*(void***)X)[k]`) gets FNVNX_D26_SLOT(k) when X is declared in the
# function before it as `T* X` / `T& X` and T has a minus-one classlayout row whose destructor slot K is below k, and
# the slot read is called. A receiver without a row (void*, the D3D / COM-like shells) is RAWVT1-RECV-AMBIG, a COM
# name RAWVT1-RECV-COM, k <= K RAWVT1-RECV-K, an exe-order row RAWVT1-RECV-KEEP.
RAWVT1_RECV_MEM = re.compile(r'(?<![\w.>])([A-Za-z_]\w*(?:\s*(?:->|\.)\s*[A-Za-z_]\w*)*?)\s*(?:->|\.)\s*%s\s*\[\s*(%s)\s*\]'
                             % (RAWVT1_MEMBERS, RAWVT1_KNUM))
RAWVT1_RECV_CAST = re.compile(r'\(\s*\*\s*(?:reinterpret_cast\s*<\s*void\s*\*\s*\*\s*\*\s*>\s*\(\s*([A-Za-z_]\w*)\s*\)|'
                              r'\(\s*void\s*\*\s*\*\s*\*\s*\)\s*\(?\s*([A-Za-z_]\w*)\s*\)?)\s*\)\s*\[\s*'
                              r'(' + RAWVT1_KNUM + r')\s*\]')


# The receivers RAWVT1-RECV cannot type from the source are decided against the exe (S1-rules, private recon branch
# recon/s1_rawvt_sites: rawvt_sites.tsv, `# fnvnx-rawvt-sites/1`, unit / exe function / receiver / slot / decision /
# evidence): at the exe's call site a thiscall (the receiver in ecx) is a C++ object, whose native table is minus-one
# (decision shift); a stdcall with the receiver pushed is a COM interface (ok-com, left); later rows come from S5's
# runtime vptr log. FNVNX_RAWVT_SITES or any rawvt_sites.tsv under FNVNX_RECON; cached on prepare.
def _rawvt_sites():
    got = prepare.__dict__.get('_fnvnx_rawvt_sites')
    if got is not None: return got
    files = [os.environ['FNVNX_RAWVT_SITES']] if os.environ.get('FNVNX_RAWVT_SITES') else []
    top = os.environ.get('FNVNX_RECON')
    if top and os.path.isdir(top):
        files += sorted(os.path.join(dp, 'rawvt_sites.tsv') for dp, _, ns in os.walk(top) if 'rawvt_sites.tsv' in ns)
    got = {}
    for f in files:
        try: lines = open(f).read().splitlines()
        except OSError: continue
        if not lines or not lines[0].startswith('# fnvnx-rawvt-sites/1'): continue
        for line in lines[1:]:
            fs = line.split('\t')
            if len(fs) < 5 or line.startswith('#'): continue
            try: got[(fs[0].strip(), int(fs[1], 16), fs[2].strip(), int(fs[3], 0))] = fs[4].strip()
            except ValueError: continue
    prepare.__dict__['_fnvnx_rawvt_sites'] = got
    return got


def _rawvt_site_decision(path, masked, pos, var, k):
    sites = _rawvt_sites()
    if not sites: return None
    tags = re.findall(r'@fnv\s+0x([0-9a-fA-F]{8})', masked[:pos])
    if not tags: return None
    p = path.replace('\\', '/')
    i = p.rfind('/src/')
    unit = p[i + 1:] if i >= 0 else p
    return sites.get((unit, int(tags[-1], 16), var, k))


def _rawvt_recv(path, masked, owners, stats, text=None):
    """[(start, end) of k] for RAWVT1-RECV (above)"""
    out = []
    for rx, kind in ((RAWVT1_RECV_MEM, 'mem'), (RAWVT1_RECV_CAST, 'cast')):
        for m in rx.finditer(masked):
            var = m.group(1) if kind == 'mem' else (m.group(1) or m.group(2))
            if var == 'this': continue
            ki = 2 if kind == 'mem' else 3
            body = next((a for a, z, nm in owners if a < m.start() < z), None)
            seg = masked[body if body is not None else max(0, m.start() - 3000):m.start()]
            if body is not None:
                seg = masked[max(0, masked.rfind('\n', 0, max(0, body - 300))):m.start()]   # with the parameters
            d = None
            for d in re.finditer(r'\b([A-Za-z_][\w:]*)\s*(?:<[^;{}()]*>)?\s*[*&]+\s*(?:const\s*)?%s\b' % re.escape(var), seg):
                pass
            T = d.group(1).split('::')[-1] if d else None
            row = _classlayout_row(path, T) if T and T not in ('void', 'char', 'int', 'unsigned', 'const') else None
            if T and D22_COM_NAME.match(T): stats['RAWVT1-RECV-COM'] += 1; out.append((m.end(), m.end(), ' /* fnv-nx RAWVT1-KEEP com */')); continue
            if row is None:
                dec = _rawvt_site_decision(path, text or masked, m.start(), var, _kval(m.group(ki)))
                if dec == 'shift' and _rawvt_mem_called(masked, m):
                    out.append((m.start(ki), m.end(ki))); stats['RAWVT1-RECV-EXE'] += 1
                elif dec and dec.startswith('ok'):
                    stats['RAWVT1-RECV-OK'] += 1; out.append((m.end(), m.end(), ' /* fnv-nx RAWVT1-KEEP %s */' % dec))
                elif _rawvt_mem_called(masked, m):
                    # RAWVT1-DYN: decided at run time by where the vptr points (fnvnx_rawvt.h)
                    vp = (text or masked)[m.start():m.end()].rsplit('[', 1)[0].rstrip()
                    out.append((m.start(), m.end(), 'FNVNX_RAWVT_DYN(%s, %s)' % (vp, m.group(ki))))
                    stats['RAWVT1-DYN'] += 1
                else: stats['RAWVT1-RECV-AMBIG'] += 1
                continue
            if row[0] != 'minus-one' or row[1] < 0: stats['RAWVT1-RECV-KEEP'] += 1; out.append((m.end(), m.end(), ' /* fnv-nx RAWVT1-KEEP keep */')); continue
            if _kval(m.group(ki)) <= row[1]: stats['RAWVT1-RECV-K'] += 1; out.append((m.end(), m.end(), ' /* fnv-nx RAWVT1-KEEP k */')); continue
            if T in _rawvt_exe_order(): stats['RAWVT1-RECV-KEEP'] += 1; out.append((m.end(), m.end(), ' /* fnv-nx RAWVT1-KEEP keep */')); continue
            if not _rawvt_mem_called(masked, m): stats['RAWVT1-RECV-DATA'] += 1; continue
            out.append((m.start(ki), m.end(ki)))
            stats['RAWVT1-RECV'] += 1
    return out


# ---------------------------------------------------------------------------------------------------------- VTM1
#
# S5-boot (link 0829588572d1, an endless loop in the heap ctor dyninit): bsgamedatasystemutility.cpp's
# FUN_00af39e0_shell::CallVirtualMethod2 (0x00af39e0) calls `reinterpret_cast<Obj*>(this)->vt->f2(this)`, exe
# [edx+8] = slot 2 (0x00af3a00), through `struct VTable { void* f0; void* f1; void(__thiscall* f2)(void*); }`; the
# object's native table (_ZTV12FUN_00af3980) is minus-one, so native +8 is CallVirtualMethod2 itself. A struct that
# models an exe vftable (every member a pointer, at least two, the first named f0 / fn0 / vf0 / vfunc00 / v0 /
# slot0 / ..., or the struct named *vt* / *vtable* / *vtbl* with numbered members) gets FNVNX_VTM1_PAD (fnvnx_vtm.h:
# one pointer under GCC) after the entry of the destructor slot K: K from a classlayout class named in the struct's
# name (the longest), else 0; none when that class is not minus-one, or when the struct is COM-shaped (__stdcall,
# HRESULT, QueryInterface / AddRef / Release) (VTM1-COM / VTM1-KEEP).
VTM1_STRUCT = re.compile(r'\bstruct\s+([A-Za-z_]\w*)\s*\{([^{}]*)\}')
VTM1_FIRST = re.compile(r'^(?:f|fn|vf|vfn|vfunc|v|slot|func|pfn|m_pfn)_?0+$', re.I)


def _vtm1_members(body):
    """[(end offset of the declaration's `;` in body, member name)] when every declaration is a pointer, else None"""
    out = []
    for m in re.finditer(r'[^;]*;', body):
        d = m.group(0).strip().rstrip(';').strip()
        if not d or d.startswith('//'): continue
        pa = re.fullmatch(r'(?:unsigned\s+)?(?:char|int|void\s*\*)\s+((?:pad|unk|_pad|gap)\w*)\s*\[[^\]]+\]', d)
        if pa: out.append((m.start(), '#pad')); continue          # a gap of slots (`char pad0[0x13c];`)
        fp = re.search(r'\(\s*(?:__\w+\s*)?\*\s*(?:const\s*)?([A-Za-z_]\w*)\s*\)\s*\(', d)
        if fp: out.append((m.end(), fp.group(1))); continue
        vp = re.fullmatch(r'(?:const\s+)?[\w:<>]+\s*\*+\s*(?:const\s*)?([A-Za-z_]\w*)', d, re.S)
        if vp: out.append((m.end(), vp.group(1))); continue
        return None
    return out


def r_vtable_model_pad(path, text, stats):
    """VTM1: a struct modelling an exe vftable gets FNVNX_VTM1_PAD after its destructor slot's entry (see above)."""
    if 'struct' not in text: return text
    edits = []
    cl = _classlayout()
    names = sorted({n for (n, f) in cl if len(n) >= 4}, key=len, reverse=True) if cl else []
    for m in VTM1_STRUCT.finditer(text):
        name, body = m.group(1), m.group(2)
        if 'FNVNX_VTM1_PAD' in body: continue
        mem = _vtm1_members(body)
        if not mem or len(mem) < 2: continue
        # the struct's name says it is a vftable (VTable / Vtbl / _vt / vftable); a D3D / device model stays (COM)
        if not re.search(r'(?:v(?:table|tbl|ftable)|_vt$|^vt$|(?<=[a-z0-9])Vt$|_VT$)', name, re.I): continue
        if re.search(r'D3D|Device|Direct|Dx', name): stats['VTM1-COM'] += 1; continue
        if mem[0][1] != '#pad' and not (VTM1_FIRST.match(mem[0][1]) or re.search(r'\d', mem[0][1])): continue
        if mem[0][1] == '#pad' and not any(n != '#pad' for _, n in mem): continue
        if D22_COM_CONV.search(body) or any(n in D22_COM_MEMBER for _, n in mem):
            stats['VTM1-COM'] += 1; continue
        if re.search(r'\b%s\b[^;(){}]*=\s*\{' % re.escape(name), text):
            stats['VTM1-INIT'] += 1; continue           # a table defined with a positional initializer: data, not a model
        K = 0
        owner = next((n for n in names if n in name), None)
        if owner:
            row = _classlayout_row(path, owner)
            if row and (row[0] != 'minus-one' or row[1] < 0): stats['VTM1-KEEP'] += 1; continue
            if row: K = row[1]
        if mem[0][1] == '#pad':
            if K: stats['VTM1-HELD'] += 1; continue
            at = m.start(2) + len(body) - len(body.lstrip())                # before the leading gap: every slot +4
        else:
            if K >= len(mem) - 1: continue
            at = m.start(2) + mem[K][0]
        edits.append(at)
        stats['VTM1'] += 1
    if not edits: return text
    for at in sorted(edits, reverse=True):
        text = text[:at] + ' FNVNX_VTM1_PAD /* fnv-nx VTM1 */' + text[at:]
    if 'fnvnx_vtm.h' not in text:
        text = '#include "fnvnx_vtm.h" /* fnv-nx VTM1 */\n' + text
    return text

RULES.insert(RULES.index(r_vfptr_pad) + 1, r_vtable_model_pad)


# RAWVT1-ANY (S2 / S5 boot a9d9f9103325: niavobject.cpp 0x00a59c90 `(*(int**)this)[42]` and
# `(*(int**)(*(int**)((char*)this + 0x18)))[63](parent)`, 0x00a59d00 SetParent `(char*)*(void**)pkOldParent + 0xe8`, all
# unshifted on minus-one NiNode tables): the other spellings of a raw slot read - the vptr through any pointer-to-pointer
# cast `(*(T**)X)[k]` on any receiver expression, and the byte offset `(char*)*(T**)X + off` - get the run-time choice
# of fnvnx_rawvt.h (FNVNX_RAWVT_DYN / FNVNX_RAWVT_DYNOFF), right for a minus-one table, a COM object and an exe table
# alike. Only a slot that is called (a cast to a function pointer or typedef'd type, or a variable then called).
_RAWVT_ANY_TYPE = r'(?:const\s+)?(?:void\s*\*|int(?:\s*\*)?|unsigned(?:\s+int)?(?:\s*\*)?|unsigned\s+long|long|DWORD(?:\s*\*)?|UINT|uintptr_t|char\s*\*|\w+_Fn|FARPROC|PVOID)'
_RAWVT_ANY_IDX = re.compile(r'\(\s*\*\s*(?:\(\s*' + _RAWVT_ANY_TYPE + r'\s*\*\s*\*\s*\)|reinterpret_cast\s*<\s*' +
                            _RAWVT_ANY_TYPE + r'\s*\*\s*\*\s*>)\s*|\(\s*\(\s*void\s*\*\s*\*\s*\)\s*\*\s*\(\s*void\s*\*\s*\*\s*\)\s*')
# a cast to a pointer to a function pointer, `*(void(__thiscall**)(void*))(` or `*(Fn*)(` / `*(Fn**)(` (Fn a typedef)
_RAWVT_ANY_FNPP = re.compile(r'\*\s*\(\s*(?:[^()]*\(\s*(?:__\w+\s*)?\*\s*\*\s*\)\s*\((?:[^()]|\([^()]*\))*\)|'
                             r'[A-Za-z_]\w*(?:Fn|Func|Proc|Callback|Method|Slot|Handler|Thunk|_t)\w*\s*\*{1,2})\s*\)\s*\(')
# the vptr as an integer: *(int*)(*(int*)X + off) - a byte offset on the first word read as int
_RAWVT_ANY_INT = re.compile(r'(\*\s*\(\s*(?:int|unsigned(?:\s+int)?|DWORD|uintptr_t)\s*\*\s*\)\s*\(\s*)\*\s*\(\s*'
                            r'(?:int|unsigned(?:\s+int)?|DWORD|uintptr_t)\s*\*\s*\)\s*')
_RAWVT_ANY_OFF = re.compile(r'\(\s*(?:const\s+)?(?:unsigned\s+)?char\s*\*\s*\)\s*\*\s*\(\s*' + _RAWVT_ANY_TYPE +
                            r'\s*(?:\*\s*){1,2}\)\s*')


def _rawvt_operand(t, i):
    """the end of the receiver operand starting at t[i]: an identifier (this, a name, x->y chains) or a balanced (...),
    after any `*` and C casts (`*(void**)((char*)this + 0x5c)`)"""
    while i < len(t) and t[i] in ' \t': i += 1
    while i < len(t) and t[i] == '*':
        i += 1
        while i < len(t) and t[i] in ' \t': i += 1
        c = re.match(r'\(\s*(?:const\s+)?[A-Za-z_][\w\s]*\**\s*\)', t[i:])
        if c: i += c.end()
        while i < len(t) and t[i] in ' \t': i += 1
    if i < len(t) and t[i] == '(':
        d = 0
        for j in range(i, len(t)):
            if t[j] == '(': d += 1
            elif t[j] == ')':
                d -= 1
                if d == 0: return j + 1
        return None
    m = re.match(r'[A-Za-z_]\w*(?:\s*(?:->|\.)\s*[A-Za-z_]\w*)*', t[i:])
    return i + m.end() if m else None


class _M:
    def __init__(self, a, b): self._a, self._b = a, b
    def start(self, *g): return self._a
    def end(self, *g): return self._b


def _rawvt_any(masked, text, stats):
    out = []
    for m in _RAWVT_ANY_IDX.finditer(masked):
        e = _rawvt_operand(masked, m.end())
        if e is None: continue
        r = re.match(r'\s*\)\s*\[\s*(' + RAWVT1_KNUM + r')\s*\]', masked[e:])
        if not r: continue
        recv = text[m.end():e].strip()
        if re.match(r'\(\s*\*\s*(?:\(|reinterpret_cast\s*<)\s*void\s*\*\s*\*\s*\*', masked[m.start():]) and \
                re.fullmatch(r'\(?\s*[A-Za-z_]\w*\s*\)?', recv):
            continue                                     # (*(void***)x)[k] on a name: RAWVT1 / RAWVT1-RECV
        a, b = m.start(), e + r.end()
        if 'FNVNX_' in text[a:b]: continue
        if not _rawvt_mem_called(masked, _M(a, b)): stats['RAWVT1-ANY-DATA'] += 1; continue
        vp = text[a:e + r.start(1)].rsplit('[', 1)[0].rstrip()
        out.append((a, b, 'FNVNX_RAWVT_DYN(%s, %s)' % (vp, r.group(1))))
        stats['RAWVT1-ANY'] += 1
    for m in _RAWVT_ANY_INT.finditer(masked):
        e = _rawvt_operand(masked, m.end())
        if e is None: continue
        r = re.match(r'\s*\+\s*(0[xX][0-9a-fA-F]+|[0-9]+)\s*\)', masked[e:])
        if not r: continue
        off = int(r.group(1), 0)
        if off % 4 or off == 0: continue
        if not _rawvt_mem_called(masked, _M(m.start(), e + r.end())): stats['RAWVT1-ANY-INT-DATA'] += 1; continue
        recv = text[m.end():e].strip()
        out.append((e + r.start(1), e + r.end(1), 'FNVNX_RAWVT_DYNOFF(*(void**)(%s), %s)' % (recv, r.group(1))))
        stats['RAWVT1-ANY-INT'] += 1
    # the slot address as integer arithmetic under a cast to a function-pointer pointer (ninode.cpp 0x00a5db30:
    # `(*(void(__thiscall**)(void*))(*(int*)this + 0xbc))(this)`, `(*(...)(*pParent + 0xfc))(pParent)`, pParent an int*)
    for m in _RAWVT_ANY_FNPP.finditer(masked):
        i = m.end()
        n = re.match(r'\s*\*\s*(?:\(\s*(?:const\s+)?(?:int|unsigned(?:\s+int)?|DWORD|uintptr_t|long)\s*\*\s*\)\s*)?', masked[i:])
        if not n or '*' not in n.group(0): continue
        e = _rawvt_operand(masked, i + n.end())
        if e is None: continue
        r = re.match(r'\s*\+\s*(0[xX][0-9a-fA-F]+|[0-9]+)\s*\)', masked[e:])
        if not r: continue
        off = int(r.group(1), 0)
        if off % 4 or off == 0: continue
        recv = text[i + n.end():e].strip()
        out.append((e + r.start(1), e + r.end(1), 'FNVNX_RAWVT_DYNOFF(*(void**)(%s), %s)' % (recv, r.group(1))))
        stats['RAWVT1-ANY-FNPP'] += 1
    # (a) any local initialised from a vptr read, `void** v = *(void***)this;` / `= (void**)*(void**)x;` / `= *(T***)x;`
    for d in re.finditer(r'\b([A-Za-z_]\w*)\s*=\s*(?:\(\s*void\s*\*\s*\*\s*\)\s*)?\*\s*(?:\(\s*' + _RAWVT_ANY_TYPE +
                         r'\s*\*\s*\*?\s*\)|reinterpret_cast\s*<\s*' + _RAWVT_ANY_TYPE + r'\s*\*\s*\*?\s*>)\s*', masked):
        name = d.group(1)
        if re.fullmatch(RAWVT1_MEMBERS, name): continue                     # RAWVT1-MEM's
        e = _rawvt_operand(masked, d.end())
        if e is None or not re.match(r'\s*\)?\s*;', masked[e:]): continue
        end = masked.find('}', e)
        body = masked[e:end if end > 0 else len(masked)]
        for u in re.finditer(r'(?<![\w.>])(%s)\s*\[\s*(%s)\s*\]' % (re.escape(name), RAWVT1_KNUM), body):
            a, b = e + u.start(), e + u.end()
            if 'FNVNX_' in text[a:b]: continue
            if not _rawvt_mem_called(masked, _M(a, b)): stats['RAWVT1-ANY-LOCAL-DATA'] += 1; continue
            out.append((a, b, 'FNVNX_RAWVT_DYN(%s, %s)' % (name, u.group(2))))
            stats['RAWVT1-ANY-LOCAL'] += 1
    # (b) the vptr read cast to an array of function pointers: ((Fn*)*(void***)this)[81], reinterpret_cast<Fn*>(*self)[k]
    for m in re.finditer(r'(\(\s*\(\s*[A-Za-z_]\w*\s*\*\s*\)\s*\*\s*\(\s*' + _RAWVT_ANY_TYPE + r'\s*\*\s*\*?\s*\)\s*)|'
                         r'(reinterpret_cast\s*<\s*[A-Za-z_]\w*\s*\*\s*>\s*\(\s*\*\s*)', masked):
        e = _rawvt_operand(masked, m.end())
        if e is None: continue
        r = re.match(r'\s*\)\s*\[\s*(%s)\s*\]' % RAWVT1_KNUM, masked[e:])
        if not r: continue
        a, b = m.start(), e + r.end()
        if 'FNVNX_' in text[a:b]: continue
        vp = text[a:e + r.start()].rstrip() + ')'
        out.append((a, b, 'FNVNX_RAWVT_DYN(%s, %s)' % (vp, r.group(1))))
        stats['RAWVT1-ANY-CASTIDX'] += 1
    # (d) the slot address as integer arithmetic under any pointer cast that is called:
    # ((F) *(int**)(*(int*)this + 0x48))(...), *(void**)(*(int*)((char*)this + 0xe0) + 12), (*(VFn*)((*(int*)this) + 0xc8))
    for m in re.finditer(r'\*\s*\(\s*(?:[A-Za-z_][\w\s]*?\*{1,2}|[^()]*\(\s*(?:__\w+\s*)?\*\s*\*\s*\)\s*\((?:[^()]|\([^()]*\))*\))\s*\)\s*\(\s*\(?\s*', masked):
        i = m.end()
        n = re.match(r'\s*\*\s*(?:\(\s*(?:const\s+)?(?:int|unsigned(?:\s+int)?|DWORD|uintptr_t|long)\s*\*\s*\)\s*)?', masked[i:])
        if not n or '*' not in n.group(0): continue
        e = _rawvt_operand(masked, i + n.end())
        if e is None: continue
        r = re.match(r'\s*\)?\s*\+\s*(0[xX][0-9a-fA-F]+|[0-9]+)\s*\)', masked[e:])
        if not r: continue
        off = int(r.group(1), 0)
        if off % 4 or off == 0: continue
        a = e + r.start(1)
        if 'FNVNX_' in text[m.start():e + r.end()]: continue
        if not _rawvt_mem_called(masked, _M(m.start(), e + r.end())): stats['RAWVT1-ANY-ARITH-DATA'] += 1; continue
        recv = text[i + n.end():e].strip()
        out.append((a, e + r.end(1), 'FNVNX_RAWVT_DYNOFF(*(void**)(%s), %s)' % (recv, r.group(1))))
        stats['RAWVT1-ANY-ARITH'] += 1
    # (e) a cast around the vptr before the index: ((Fn*)vt)[49], ((T**)(*(void***)this))[49], ((T**) * (void***)p)[108],
    # ((Fn*)m_ppVirtualMethodTable)[83]
    for m in re.finditer(r'\(\s*\(\s*(?:[A-Za-z_][\w\s]*?\*{1,2}|[^()]*\(\s*(?:__\w+\s*)?\*\s*\*\s*\)\s*\((?:[^()]|\([^()]*\))*\))\s*\)\s*', masked):
        i = m.end()
        e = _rawvt_operand(masked, i)
        if e is None: continue
        vexp = masked[i:e]
        if not (re.search(r'\*\s*\(\s*' + _RAWVT_ANY_TYPE + r'\s*\*\s*\*|reinterpret_cast\s*<[^>]*\*\s*\*\s*\*?\s*>', vexp) or
                re.fullmatch(r'\s*(?:this\s*->\s*)?' + RAWVT1_MEMBERS + r'\s*', vexp)):
            continue
        r = re.match(r'\s*\)\s*\[\s*(%s)\s*\]' % RAWVT1_KNUM, masked[e:])
        if not r: continue
        a, b = m.start(), e + r.end()
        if 'FNVNX_' in text[a:b]: continue
        out.append((a, b, 'FNVNX_RAWVT_DYN(%s, %s)' % (text[a:e + r.start()].rstrip() + ')', r.group(1))))
        stats['RAWVT1-ANY-CASTVP'] += 1
    # (f) pointer arithmetic on the vptr: *(*(long**)p + 7), *((void***)*(void**)&s + 12), (*(int**)r + 0x44 / 4)
    for m in re.finditer(r'\*\s*\(\s*(?:\*\s*\(\s*' + _RAWVT_ANY_TYPE + r'\s*\*\s*\*\s*\)|\(\s*' + _RAWVT_ANY_TYPE +
                         r'\s*\*\s*\*\s*\*?\s*\)\s*\*\s*\(\s*void\s*\*\s*\*\s*\))\s*', masked):
        e = _rawvt_operand(masked, m.end())
        if e is None: continue
        r = re.match(r'\s*\+\s*(%s)\s*\)' % RAWVT1_KNUM, masked[e:])
        if not r: continue
        if _kval(r.group(1)) == 0: continue
        if not _rawvt_mem_called(masked, _M(m.start(), e + r.end())): stats['RAWVT1-ANY-PTR-DATA'] += 1; continue
        recv = text[m.end():e].strip()
        out.append((e + r.start(1), e + r.end(1), 'FNVNX_RAWVT_DYNK(*(void**)(%s), %s)' % (recv, r.group(1))))
        stats['RAWVT1-ANY-PTR'] += 1
    for m in _RAWVT_ANY_OFF.finditer(masked):
        e = _rawvt_operand(masked, m.end())
        if e is None: continue
        r = re.match(r'\s*\+\s*(0[xX][0-9a-fA-F]+|[0-9]+)\b', masked[e:])
        if not r: continue
        off = int(r.group(1), 0)
        if off % 4 or off == 0: continue
        # only a called slot: `(*(Fn*)(... + off))(...)` or a cast to a function-pointer type name (a field read
        # through a pointer member, `*(float*)((char*)*(void**)((char*)this + 0x6c) + 0x10)`, is data)
        tail = masked[e + r.end():e + r.end() + 12]
        head = masked[max(0, m.start() - 80):m.start()]
        cast = re.search(r'\(\s*([A-Za-z_]\w*)\s*\*\s*\)\s*\(?\s*$', head)
        called = re.match(r'\s*(?:\)\s*)+\(', tail) or (cast and re.search(r'Fn|Func|Proc|Callback|Method|Slot|Handler|Thunk', cast.group(1)))
        if not called: stats['RAWVT1-ANY-OFF-DATA'] += 1; continue
        recv = text[m.end():e].strip()
        out.append((e + r.start(1), e + r.end(1), 'FNVNX_RAWVT_DYNOFF(*(void**)(%s), %s)' % (recv, r.group(1))))
        stats['RAWVT1-ANY-OFF'] += 1
    keep = []                                              # one edit per span: the forms overlap (FNPP / ARITH / OFF)
    for e in sorted(set(out), key=lambda x: (x[0], -x[1])):
        if keep and e[0] < keep[-1][1]: continue
        keep.append(e)
    return keep


# RAWVT1-DEL0 (orchestrator: the exe's slot-0 calls ??_G(flags) must reach D0 for flag 1): a statement
# `CALLEE(obj, flag);` whose callee reads slot 0 of obj's vtable - any spelling: (*(void***)obj)[0], *(void**)*(void**)obj,
# **(void***)obj, vt[0] / *vt with vt a vptr of obj, ... - becomes FNVNX_RAWVT_DEL0(obj, flag) (fnvnx_rawvt.h). Only an
# integer flag (a literal or a name); `(cover, buffer)` is no destructor call (combatstate.cpp, left).
_DEL0_STMT = re.compile(r'(?m)^([ \t]*)((?:reinterpret_cast\s*<[^<>;]*>\s*)?\((?:[^;()]|\((?:[^;()]|\((?:[^;()]|\([^;()]*\))*\))*\))*\)|'
                        r'[A-Za-z_]\w*\s*\[\s*0\s*\])\s*\(\s*([^,();]+?)\s*,\s*([A-Za-z_]\w*|[0-9]+[uU]?|true|false)\s*\)\s*;')


def _del0_callee(callee, obj, local_vptrs):
    """True when the callee text reads slot 0 of obj's vtable (no other slot)"""
    c = re.sub(r'/\*.*?\*/', ' ', callee)
    if re.search(r'\[\s*(?!0\s*\])[^\]]+\]|\+\s*(?:0[xX][0-9a-fA-F]*[1-9a-fA-F]|[1-9])', c): return False
    base = re.sub(r'^\(\s*(?:void\s*\*|[A-Za-z_]\w*\s*\*)\s*\)\s*', '', obj.strip())
    names = {base} | {v for v, o in local_vptrs.items() if o == base}
    hit = any(re.search(r'(?<![\w.>])%s(?![\w])' % re.escape(n), c) for n in names if n)
    derefs = c.count('*') - len(re.findall(r'\*\s*\)', c))
    vn = [v for v, o in local_vptrs.items() if o == base]
    return hit and (derefs >= 2 or '[0]' in c.replace(' ', '') or any(re.search(r'\*\s*%s\b|%s\s*\[' % (v, v), c) for v in vn))


def _rawvt_del0(masked, text, stats):
    local = {m.group(1): re.sub(r'\s+', '', m.group(2)).strip('()')
             for m in re.finditer(r'\b([A-Za-z_]\w*)\s*=\s*\*\s*(?:\([^()]*\*\s*\*\s*\*?\s*\)|reinterpret_cast\s*<[^<>]*>)\s*\(?\s*([A-Za-z_]\w*)\s*\)?\s*;', masked)}
    out = []
    for m in _DEL0_STMT.finditer(masked):
        callee, obj, flag = m.group(2), m.group(3), m.group(4)
        if re.fullmatch(r'[A-Za-z_]\w*', flag) and flag not in ('true', 'false') and not re.search(
                r'\b(?:int|unsigned|bool|char|short|long|BOOL|UINT|DWORD|uint32_t|int32_t|uint8_t)\s+(?:\w+\s*,\s*)*%s\b'
                % re.escape(flag), masked[max(0, m.start() - 4000):m.start()]):
            continue                                         # a flag of another type: no destructor call
        if 'FNVNX_RAWVT_DEL0' in callee or re.search(r'FNVNX_RAWVT_DYN\w*\([^)]*,\s*(?!0\b)', text[m.start(2):m.end(2)]): continue
        if not _del0_callee(callee, obj, local): continue
        if re.search(r'\b(?:QueryInterface|AddRef|Release)\b|__stdcall', callee): continue
        out.append((m.start(2), m.end(), 'FNVNX_RAWVT_DEL0(%s, %s);' % (text[m.start(3):m.end(3)].strip(), flag)))
        stats['RAWVT1-DEL0'] += 1
    return out


def r_raw_vtable_slots(path, text, stats):
    """RAWVT1: a raw exe slot index on `this` -> FNVNX_D26_SLOT(k) (see above); RAWVT1-MEM: the same on the vptr
    modelled as a data member."""
    pass
    if '*' not in text: return text
    masked = d22_mask(text)
    hits = [m for m in RAWVT1_RX.finditer(masked)]
    mem = [m for m in RAWVT1_MEM_RX.finditer(masked)]
    recv_any = RAWVT1_RECV_MEM.search(masked) or RAWVT1_RECV_CAST.search(masked)
    if not hits and not mem and not recv_any and not _RAWVT_ANY_IDX.search(masked) and not _RAWVT_ANY_OFF.search(masked) \
            and not _RAWVT_ANY_INT.search(masked) and not _RAWVT_ANY_FNPP.search(masked):
        return text
    classes = d22_classes(text, masked)
    owners = [(c['open'], c['close'], c['qual'][-1]) for c in classes]
    for m in D22_MEMBER_DEF.finditer(masked):
        b = m.end() - 1
        try: e = prepare.match_brace(masked, b)
        except Exception: e = None
        if e: owners.append((b, e, m.group(1)))
    owners.sort(key=lambda t: t[0] - t[1])
    by_name = collections.defaultdict(list)
    for c in classes: by_name[c['qual'][-1]].append(c)
    edits, keeps, dyn = [], [], []
    cands = [(m, m.start(2), m.end(2), 'RAWVT1') for m in hits]
    for m in mem:
        own = next(((a, nm) for a, z, nm in owners if a < m.start() < z), None)
        if own is None:
            seg = masked[max(0, m.start() - 3000):m.start()]
            if re.search(r'\b%s\s*=\s*\*\s*(?:\(\s*[A-Za-z_][\w\s]*\*\s*\*\s*\*?\s*\)|reinterpret_cast\s*<\s*[A-Za-z_][\w\s]*\*\s*\*\s*\*?\s*>)'
                         % re.escape(m.group(2)), seg) and _rawvt_mem_called(masked, m):
                dyn.append((m.start(), m.end(), 'FNVNX_RAWVT_DYN(%s, %s)' % (m.group(2), m.group(3))))
                stats['RAWVT1-MEM-DYN'] += 1; continue
            stats['RAWVT1-MEM-FREE'] += 1; continue
        # the classes of that name that declare the member (a file can hold several: namespaces, middlehighprocess.cpp's
        # three Proc); held only when every one of them declares a virtual (the member is then not the vptr)
        decl = [c for c in by_name.get(own[1], ()) if re.search(r'\b%s\s*(?:\[[^\]]*\])?\s*;' % re.escape(m.group(2)),
                                                          masked[c['open']:c['close']])]
        if decl and all(re.search(r'\bvirtual\b', masked[c['open']:c['close']]) for c in decl):
            stats['RAWVT1-MEM-VIRT'] += 1; continue
        if not _rawvt_mem_ok(masked, own[0], m.start(), m.group(2)):
            # a local vptr of another object, `void** vtbl = *(void***)obj;` (hkpworld.cpp, tileshader.cpp): the
            # run-time choice (FNVNX_RAWVT_DYN)
            seg = masked[own[0]:m.start()]
            loc = None
            for loc in re.finditer(r'\b%s\s*=\s*\*\s*(?:\(\s*[A-Za-z_][\w\s]*\*\s*\*\s*\*?\s*\)|reinterpret_cast\s*<\s*[A-Za-z_][\w\s]*\*\s*\*\s*\*?\s*>)'
                                   % re.escape(m.group(2)), seg):
                pass
            if loc and _rawvt_mem_called(masked, m):
                dyn.append((m.start(), m.end(), 'FNVNX_RAWVT_DYN(%s, %s)' % (m.group(2), m.group(3))))
                stats['RAWVT1-MEM-DYN'] += 1; continue
            stats['RAWVT1-MEM-LOCAL'] += 1; continue
        if not _rawvt_mem_called(masked, m):
            stats['RAWVT1-MEM-DATA'] += 1; continue
        cands.append((m, m.start(3), m.end(3), 'RAWVT1-MEM'))
    for m, ka, kz, tag in cands:
        k = _kval(masked[ka:kz])
        owner = next((nm for a, z, nm in owners if a < m.start() < z), None)
        if owner is None: stats['RAWVT1-FREE'] += 1; continue
        if D22_COM_NAME.match(owner) or any(_d22_com(masked, c) for c in by_name.get(owner, ()) if c['first']):
            stats['RAWVT1-COM'] += 1; keeps.append((m.end(), 'RAWVT1-COM')); continue
        cl = _classlayout_row(path, owner)
        if cl is not None and (cl[0] != 'minus-one' or cl[1] < 0):
            stats['RAWVT1-CL-KEEP'] += 1; keeps.append((m.end(), 'RAWVT1-CL-KEEP')); continue
        K = cl[1] if cl is not None else (_d22_dtor_slot(path, owner) or 0)   # D22-SLOTK: the pair sits at K
        if k == K: stats['RAWVT1-SLOT0'] += 1; keeps.append((m.end(), 'RAWVT1-SLOT0')); continue
        if k < K: stats['RAWVT1-BELOWK'] += 1; keeps.append((m.end(), 'RAWVT1-BELOWK')); continue
        if owner in _rawvt_exe_order(): stats['RAWVT1-EXEORDER'] += 1; keeps.append((m.end(), 'RAWVT1-EXEORDER')); continue   # the link kept its table in exe order
        edits.append((ka, kz, 'FNVNX_D26_SLOT(%s)' % text[ka:kz]))
        if tag != 'RAWVT1': stats[tag] += 1
    if dyn:
        edits += dyn
        if 'fnvnx_rawvt.h' not in text:
            edits.append((0, 0, '#include "fnvnx_rawvt.h" /* fnv-nx RAWVT1-DYN */\n'))
    if recv_any:
        rv = _rawvt_recv(path, masked, owners, stats, text)
        edits += [(e[0], e[1], e[2]) if len(e) == 3 else (e[0], e[1], 'FNVNX_D26_SLOT(%s)' % text[e[0]:e[1]]) for e in rv]
        if any(len(e) == 3 for e in rv) and 'fnvnx_rawvt.h' not in text:
            edits.append((0, 0, '#include "fnvnx_rawvt.h" /* fnv-nx RAWVT1-DYN */\n'))
    anyv = _rawvt_any(masked, text, stats) if '*' in masked else []
    if anyv:
        taken = [(a, b) for a, b, _ in edits if b > a]
        keep = [e for e in anyv if not any(a < e[1] and e[0] < b for a, b in taken)]
        stats['RAWVT1-ANY-OVERLAP'] += len(anyv) - len(keep)
        edits += keep
        if keep and 'fnvnx_rawvt.h' not in text and not any(e[2].startswith('#include "fnvnx_rawvt.h"') for e in edits):
            edits.append((0, 0, '#include "fnvnx_rawvt.h" /* fnv-nx RAWVT1-DYN */\n'))
    del0 = _rawvt_del0(masked, text, stats)
    if del0:
        # the whole call statement: drop the slot edits inside it (the KEEP / DYN ones of its slot-0 read)
        edits = [e for e in edits if not any(a <= e[0] and e[1] <= b for a, b, _ in del0)]
        keeps = [k for k in keeps if not any(a <= k[0] <= b for a, b, _ in del0)]
        edits += del0
        if 'fnvnx_rawvt.h' not in text:
            edits.append((0, 0, '#include "fnvnx_rawvt.h" /* fnv-nx RAWVT1-DYN */\n'))
    edits += [(a, a, ' /* fnv-nx RAWVT1-KEEP %s */' % r[7:].lower()) for a, r in keeps]
    inc = [e for e in edits if e[2].startswith('#include "fnvnx_rawvt.h"')]
    edits = [e for e in edits if not e[2].startswith('#include "fnvnx_rawvt.h"')] + inc[:1]
    for a, b, rep in sorted(set(edits), key=lambda e: (e[0], e[1]), reverse=True):
        text = text[:a] + rep + text[b:]
    stats['RAWVT1'] += sum(1 for e in edits if e[1] > e[0])
    return text

# ---------------------------------------------------------------------------------------------------------- RAWVT1-SSA
#
# The raw vcalls that the text forms above miss (S1-rules, orchestrator 2026-11-09: rawvcall must report 0 over the
# tree). rawvcall.py finds them by meaning, in GCC's SSA dump: an indirect call through *(*(X) + off) with X the
# first argument. Its raw rows go into a site list, rawvt_ssa.tsv (`# fnvnx-rawvt-ssa/1`; unit / source line with
# whitespace collapsed / byte offset), under FNVNX_RECON (recon/s1_rawvt_sites) or at FNVNX_RAWVT_SSA. On each
# listed line:
# - slot 0 called with (X, an integer flag) is the exe's ??_G(flags): the call statement becomes
#   FNVNX_RAWVT_DEL0(X, flag), which sends flag 1 to D0 on a minus-one table;
# - slot 0 called with anything else is no deleting destructor: slot 0 does not shift (RAWVT1-KEEP slot0-not-dtor);
# - any other slot: the literal whose value is off / 4 (a slot index) or off (a byte offset) on the line, or on the
#   three lines before it, gets the m1 test on X's first word: FNVNX_RAWVT_DYNK(*(void**)(X), k) or
#   FNVNX_RAWVT_DYNOFF(*(void**)(X), off). X is the call's first argument, copied only when it has no side effect.
# - a field of a vftable-model struct (offset `field`) is VTM1's: counted as RAWVT1-SSA-FIELD.
def _rawvt_ssa_sites():
    got = prepare.__dict__.get('_fnvnx_rawvt_ssa')
    if got is not None: return got
    files = [os.environ['FNVNX_RAWVT_SSA']] if os.environ.get('FNVNX_RAWVT_SSA') else []
    top = os.environ.get('FNVNX_RECON')
    if top and os.path.isdir(top):
        files += sorted(os.path.join(dp, 'rawvt_ssa.tsv') for dp, _, ns in os.walk(top) if 'rawvt_ssa.tsv' in ns)
    got = collections.defaultdict(dict)
    for f in files:
        try: lines = open(f).read().splitlines()
        except OSError: continue
        if not lines or not lines[0].startswith('# fnvnx-rawvt-ssa/1'): continue
        for line in lines[1:]:
            fs = line.split('\t')
            if len(fs) < 3 or line.startswith('#'): continue
            got[fs[0].strip()][' '.join(fs[1].split())] = fs[2].strip()
    prepare.__dict__['_fnvnx_rawvt_ssa'] = got
    return got


_SSA_LIT = re.compile(r'(?<![\w.])((?:0x[0-9A-Fa-f]+|\d+)(?:\s*[/*]\s*(?:0x[0-9A-Fa-f]+|\d+|sizeof\s*\(\s*void\s*\*\s*\)))?)(?![\w.])')


def _ssa_val(e):
    e = re.sub(r'sizeof\s*\(\s*void\s*\*\s*\)', '4', e)
    try: return int(eval(re.sub(r'/', '//', e), {'__builtins__': {}}))
    except Exception: return None


def _ssa_call_after(t, i):
    """(open, close or None) of the argument list of the call whose callee holds position i: the first `(` right
    after a `)` that closes a group opened before i"""
    depth = 0
    for j in range(i, len(t)):
        c = t[j]
        if c in '([': depth += 1
        elif c in ')]':
            depth -= 1
            if depth < 0:
                k = j + 1
                while k < len(t) and t[k] in ' \t': k += 1
                if k < len(t) and t[k] == '(': return k, _ssa_close(t, k)
                if c == ']': depth = 0
        elif c == ';': return None
    return None


def _ssa_close(t, k):
    d = 0
    for j in range(k, len(t)):
        if t[j] == '(': d += 1
        elif t[j] == ')':
            d -= 1
            if d == 0: return j
    return None


def _ssa_args(t, a, z):
    """the top-level arguments of the list t[a] .. t[z] (the parens)"""
    out, d, s = [], 0, a + 1
    for j in range(a + 1, z):
        c = t[j]
        if c in '([{': d += 1
        elif c in ')]}': d -= 1
        elif c == ',' and d == 0: out.append(t[s:j].strip()); s = j + 1
    out.append(t[s:z].strip())
    return out


def _ssa_callee_start(t, a):
    """the start of the postfix expression that ends just before position a (the call's `(`)"""
    j = a - 1
    while j >= 0 and t[j] in ' \t': j -= 1
    while j >= 0:
        if t[j] in ')]':
            op, cl, d = '(' if t[j] == ')' else '[', t[j], 0
            while j >= 0:
                if t[j] == cl: d += 1
                elif t[j] == op:
                    d -= 1
                    if d == 0: break
                j -= 1
            j -= 1
        elif re.match(r'\w', t[j]):
            while j >= 0 and re.match(r'\w', t[j]): j -= 1
        else:
            break
        k = j
        while k >= 0 and t[k] in ' \t': k -= 1
        cm = re.search(r'\b(?:reinterpret_cast|static_cast|const_cast)\s*<[^<>;]*>\s*$', t[:k + 1])
        if cm: return cm.start()
        if k >= 1 and t[k - 1:k + 1] in ('->', '::'): j = k - 2; continue
        if k >= 0 and t[k] == '.': j = k - 1; continue
        if k >= 0 and (t[k] in ')]' or re.match(r'\w', t[k])) and t[j + 1] in '[': j = k; continue
        return j + 1
    return j + 1


def r_rawvt_ssa(path, text, stats):
    """RAWVT1-SSA: rawvcall's raw sites by meaning (see above)."""
    sites = _rawvt_ssa_sites()
    if not sites: return text
    p = path.replace('\\', '/')
    i = p.rfind('/src/')
    unit = p[i + 1:] if i >= 0 else p
    want = sites.get(unit)
    if not want: return text
    lines = text.split('\n')
    starts, n = [], 0
    for ln in lines: starts.append(n); n += len(ln) + 1
    edits = []
    for li, ln in enumerate(lines):
        key = ' '.join(ln.split())
        off = want.get(key) if len(key) > 8 else None      # a blank or tiny line is no call site
        if off is None or 'FNVNX_' in ln or 'RAWVT1-KEEP' in ln: continue
        base = starts[li]
        ext = '\n'.join(lines[li:li + 4])
        if off == 'field':
            # `tbl->f(` with tbl a side-effect-free path: FNVNX_RAWVT_FIELD(tbl, f)( - a __stdcall field is COM's
            fm = None
            for fm in re.finditer(r'((?:\bthis|\b[A-Za-z_]\w*)(?:\s*(?:->|\.)\s*[A-Za-z_]\w*)*)\s*->\s*([A-Za-z_]\w*)\s*\(', ln):
                pass
            if fm is None: stats['RAWVT1-SSA-HELD'] += 1; continue
            f = fm.group(2)
            if re.search(r'__stdcall\s*\*\s*%s\s*\)' % re.escape(f), text) or f in ('QueryInterface', 'AddRef', 'Release'):
                edits.append((base + len(ln.rstrip()), base + len(ln.rstrip()), ' /* fnv-nx RAWVT1-KEEP com-stdcall */'))
                stats['RAWVT1-SSA-COM'] += 1; continue
            edits.append((base + fm.start(), base + fm.end() - 1, 'FNVNX_RAWVT_FIELD(%s, %s)' % (fm.group(1), f)))
            stats['RAWVT1-SSA-FIELD'] += 1
            continue
        try: off = int(off)
        except ValueError: continue
        base = starts[li]
        if '__stdcall' in ln or 'STDMETHOD' in ln:
            # a stdcall with the receiver pushed: a COM interface, whose table keeps the exe's order
            edits.append((base + len(ln.rstrip()), base + len(ln.rstrip()), ' /* fnv-nx RAWVT1-KEEP com-stdcall */'))
            stats['RAWVT1-SSA-COM'] += 1
            continue
        if off == 0:
            # the call of this line: its last argument list at statement level (the arguments may go on over
            # the next lines: ext)
            cand = None
            for m in re.finditer(r'\(', ln):
                z = _ssa_close(ext, m.start())
                if z is None: continue
                cs = _ssa_callee_start(ln, m.start())
                if cs < m.start() and re.match(r'\s*;?\s*(?:\n|$)', ext[z + 1:]) and re.fullmatch(r'\s*(?:return\s+)?', ln[:cs]):
                    cand = (cs, m.start(), z); break
            if cand is None: stats['RAWVT1-SSA-HELD'] += 1; continue
            cs, a, z = cand
            args = _ssa_args(ext, a, z)
            if len(args) == 2 and _ssa_val(args[1]) is not None and not re.match(r'\s*return\b', ln):
                edits.append((base + cs, base + z + 1, 'FNVNX_RAWVT_DEL0(%s, %s)' % (args[0], args[1])))
                stats['RAWVT1-SSA-DEL0'] += 1
            else:
                edits.append((base + len(ln.rstrip()), base + len(ln.rstrip()), ' /* fnv-nx RAWVT1-KEEP slot0-not-dtor */'))
                stats['RAWVT1-SSA-SLOT0'] += 1
            continue
        done = False
        # a slot pointer set on an earlier line (`void** applySlot = pass->vtable + 30;`): a name of the callee
        am = next((mm.start() for mm in re.finditer(r'\(', ln) if _ssa_close(ln, mm.start()) is not None and
                   re.fullmatch(r'\s*;?\s*', ln[_ssa_close(ln, mm.start()) + 1:])), len(ln))
        callee_names = set(re.findall(r'[A-Za-z_]\w*', ln[:am]))
        for lj in range(li, max(-1, li - 7), -1):
            src = lines[lj]
            if lj != li:
                am = re.match(r'\s*(?:[\w:<>]+[\s*&]+)?([A-Za-z_]\w*)\s*=[^=]', src)
                if not am or am.group(1) not in callee_names: continue
            for m in _SSA_LIT.finditer(src):
                v = _ssa_val(m.group(1))
                if v is None or v not in (off // 4, off) or (v == off // 4 and off % 4): continue
                pre = src[:m.start()].rstrip()
                if not (pre.endswith('[') or pre.endswith('+')): continue
                if pre.endswith('+') and re.search(r'\(\s*char\s*\*\s*\)\s*\w+\s*\+$', pre) and v == off // 4: continue
                # the call: its first argument
                if lj == li:
                    ca = _ssa_call_after(src, m.end())
                else:
                    ca = None
                    for mm in re.finditer(r'\(', ln):
                        z = _ssa_close(ln, mm.start())
                        if z is not None and re.fullmatch(r'\s*;?\s*', ln[z + 1:]) and _ssa_callee_start(ln, mm.start()) < mm.start():
                            ca = (mm.start(), z); break
                if not ca or ca[1] is None:
                    # the arguments continue on the next lines
                    if lj == li and ca and ca[1] is None:
                        z2 = _ssa_close(ext, ca[0])
                        x = _ssa_args(ext, ca[0], z2)[0] if z2 is not None else src[ca[0] + 1:].split(',')[0].strip()
                    else:
                        continue
                else:
                    x = _ssa_args(ln if lj != li else src, ca[0], ca[1])[0]
                if not x or re.search(r'\+\+|--|[^=!<>]=[^=]|\w\s*\(', re.sub(r'\b(?:reinterpret_cast|static_cast|const_cast)\s*<[^<>]*>\s*\(', '(', x)):
                    continue
                mac = 'FNVNX_RAWVT_DYNK' if v == off // 4 else 'FNVNX_RAWVT_DYNOFF'
                b = starts[lj]
                edits.append((b + m.start(1), b + m.end(1), '%s(*(void **)(%s), %s)' % (mac, x, m.group(1))))
                stats['RAWVT1-SSA'] += 1
                done = True
                break
            if done: break
        if not done: stats['RAWVT1-SSA-HELD'] += 1
    if not edits: return text
    for a, b, rep in sorted(set(edits), reverse=True):
        text = text[:a] + rep + text[b:]
    if 'fnvnx_rawvt.h' not in text:
        text = '#include "fnvnx_rawvt.h" /* fnv-nx RAWVT1-DYN */\n' + text
    return text


# ---------------------------------------------------------------------------------------------------------- PHSLOT1
#
# The decomp's reconstructed classes (a shell standing for a class of the exe) list the exe vftable's slots as
# placeholders `virtual void vNNN()`. Most only declare them; some write `{}` after them, an empty body that is no
# function of the game: NiAVObject (magicprojectile.cpp) defines v000..v057 that way, so its native vtable names
# `_ZN10NiAVObject4v003Ev`, a `ret`, where the exe's slot 3 is 0x00acbb70 (`xor eax, eax; ret`, a native function
# of the address map) and virtual callers get whatever x0 held (EQ difffuzz records 0x00477560, 0x009abe40). A
# declared-only slot is a trap stub, which exe_slot_fill (fnvlink.py) binds to the exe slot's function; an empty
# stand-in is not a trap, so the fill never looked at it. The body is dropped: `virtual void vNNN();`. Only the
# placeholder names (v + three hex digits) with an empty void body; a named virtual with `{}` is the decomp's own
# knowledge and stays.
PHSLOT1_RX = re.compile(r'^([ \t]*virtual[ \t]+void[ \t]+v[0-9A-Fa-f]{3}\(\))[ \t]*\r?\n[ \t]*\{\}[ \t]*$', re.M)

def r_placeholder_slots(path, text, stats):
    """PHSLOT1: `virtual void vNNN()` followed by an empty body -> declared only (see above)."""
    if 'virtual void v' not in text: return text
    text, n = PHSLOT1_RX.subn(r'\1;', text)
    if n: stats['PHSLOT1'] += n
    return text

# ---------------------------------------------------------------------------------------------------------- DTORB1
#
# A destructor stand-in whose MSVC name the recipe binds to another function (S1-rules, fuzz oracle-fault). The decomp
# tags `X::~X() {}` with the address of X's scalar deleting destructor ??_G, and the unit binds `??1X` to another
# address A: blk_004a0000_od.cpp's `FUN_004aa1e0::~FUN_004aa1e0() {}` at 0x004aa1e0 (??_GFUN_004aa1e0), where
# `??1FUN_004aa1e0@@UAE@XZ` is bound to 0x004aa280, `FUN_004aa280::~FUN_004aa280`, which stores the vptrs and calls
# 0x00438af0. MSVC's ??_G calls ??1X, so the exe runs A. GCC's D0 calls X's own D1, the empty stand-in, so the native
# skipped A (the fuzz oracle faulted inside A on the planted object; the native returned). 65 tagged destructors in
# the decomp have such a binding, 50 of them to a function of the same unit.
# DTORB1 gives the stand-in's body, under GCC only, a call of A through the link's address symbol `__fnv_<A>` (the
# native definition at A, `this` in x0). The original body follows, unreachable: FNVNX_DTORB_BEGIN(A) ...
# FNVNX_DTORB_END expands to `{ __fnv_<A>(this); } if (0) {` ... `}`. MSVC expands both to nothing, so its objects
# are unchanged. Only where:
# - the tag is the unit's ??_GX (or ??_EX);
# - X is defined in the file without bases and its data members are of built-in or pointer type (GCC's D1 destroys
#   nothing after the body);
# - A is a function of some unit whose MSVC name takes only `this` (`...@@[QUA]AE@XZ` / `...@@[QUA]AEXXZ`).
_DTORB_FUNCS = None

def _dtorb_functions(path):
    """{address: MSVC symbol} of every function of build/units.json (cached on prepare)."""
    got = prepare.__dict__.get('_fnvnx_dtorb_funcs')
    if got is not None: return got
    import json
    out = {}
    root, uj = prepare._units_json(path)
    if uj:
        for u in json.load(open(uj))['units']:
            for f in u.get('functions') or ():
                out.setdefault(int(f['address'], 16), f.get('symbol') or '')
    prepare.__dict__['_fnvnx_dtorb_funcs'] = out
    return out

DTORB_DEF = re.compile(r'//[ \t]*@fnv[ \t]+0x([0-9a-fA-F]+)[^\n]*\n(?:[ \t]*//[^\n]*\n)*[ \t]*'
                       r'(?:(?:FNVNX_[A-Z0-9_]+|inline|__thiscall)\s+)*((?:[A-Za-z_]\w*::)*)([A-Za-z_]\w*)::~\3\s*\(\s*(?:void)?\s*\)\s*\{')
DTORB_THIS_ONLY = re.compile(r'@@[QUA][AE]E(?:@|X)XZ$')
DTORB_BUILTIN = re.compile(r'^\s*(?:(?:unsigned|signed|const|volatile|static)\s+)*(?:char|short|int|long|float|double|bool|'
                           r'void|__int64|wchar_t|DWORD|WORD|BYTE|UINT|ULONG|LONG|BOOL)\b|\*')

def _dtorb_plain_class(text, name):
    """X is defined in text once, without bases, and every data member is of built-in or pointer type"""
    ms = list(re.finditer(r'\b(?:struct|class)\s+%s\s*(:[^{;]*)?\{' % re.escape(name), text))
    if len(ms) != 1 or ms[0].group(1): return False
    o = ms[0].end() - 1
    c = prepare.match_brace(text, o) - 1                                     # the closing brace
    body = text[o + 1:c]
    if '{' in body: return False
    for decl in body.split(';'):
        d = re.sub(r'^\s*(?:public|private|protected)\s*:', '', decl.strip()).strip()
        if not d or '(' in d or d.startswith(('typedef', 'friend', 'using', 'enum')): continue
        if not DTORB_BUILTIN.search(d): return False
    return True

def r_bound_dtor_stand_ins(path, text, stats):
    """DTORB1: a tagged destructor stand-in whose ??1 the unit binds elsewhere calls the bound address under GCC."""
    if '~' not in text or '@fnv' not in text or not path.endswith('.cpp'): return text
    syms = _unit_symbols(path)
    if not syms: return text
    funcs = None
    edits = []
    for m in DTORB_DEF.finditer(text):
        tag, name = int(m.group(1), 16), m.group(3)
        bound = {int(v, 16) for k, v in syms.items() if k.startswith('??1%s@' % name)}
        if len(bound) != 1: continue
        a = next(iter(bound))
        if a == tag: continue
        if funcs is None: funcs = _dtorb_functions(path)
        if not funcs.get(tag, '').startswith(('??_G%s@' % name, '??_E%s@' % name)):
            stats['DTORB1-NOTAG'] += 1; continue
        if not DTORB_THIS_ONLY.search(funcs.get(a, '')):
            stats['DTORB1-TARGET'] += 1; continue
        if not _dtorb_plain_class(text, name):
            stats['DTORB1-CLASS'] += 1; continue
        o = m.end() - 1
        c = prepare.match_brace(text, o) - 1                                 # the closing brace
        edits.append((o + 1, c, a))
    if not edits: return text
    for o, c, a in sorted(edits, reverse=True):
        text = (text[:o] + ' FNVNX_DTORB_BEGIN(%08x) /* fnv-nx DTORB1 */' % a + text[o:c] + ' FNVNX_DTORB_END '
                + text[c:])
    stats['DTORB1'] += len(edits)
    return text

# ---------------------------------------------------------------------------------------------------------- STAND7
#
# An out-of-class template member body that is a stand-in (S1-rules, from EQ: link 0540aff0d154 call-flow 00b2b910).
# treeengine_ob1_variant.cpp defines `template <...> inline basic_string<...>& basic_string<...>::assign(const
# basic_string&, unsigned, unsigned) { ... if (_Roff == 0x7f3f3f3f) {...; _Tidy(...)} ... }` only to reproduce the
# frame MSVC /Ob1 reserves before it rolls the expansion back to a call. The unit binds
# `?assign@?$basic_string@...` to 0x0044a580, the exe's assign, which another unit decompiles. GCC inlined the stand-in
# and called _Tidy (0x0044a730) where the exe calls 0x0044a580 with the real arguments. STAND5 covers in-class bodies;
# this one is out of class. STAND7 puts such a definition under `#ifdef _MSC_VER`: MSVC keeps its byte-match body, and
# GCC sees only the class's declaration and calls the bound name, which the link binds to the exe's function. Only
# where:
# - the definition is `inline`, in an /Od /Ob1 unit (MSVC expanded it, then rolled the expansion back to the call the
#   recipe records), and the unit binds exactly one decorated name of the member (`?m@?$C@`), to an address that is
#   not tagged in the file and that another unit defines;
# - no `@fnv` tag sits right above the definition.
STAND7_DEF = re.compile(r'(?m)^[ \t]*template\s*<[^;{}]*>\s*(?:inline\s+)?[^;{}()]*?\b([A-Za-z_]\w*)\s*<[^;{}()]*>\s*::\s*'
                        r'([A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:const\s*)?\{')

def r_template_member_stand_ins(path, text, stats):
    """STAND7: an out-of-class template member stand-in whose MSVC name the unit binds elsewhere -> MSVC-only (above)."""
    if not path.endswith('.cpp') or 'template' not in text: return text
    syms = _unit_symbols(path)
    info = _unit_info(path)
    if not syms or info is None: return text
    tagged = {int(a, 16) for a in FNV_TAG_ADDR.findall(text)}
    elsewhere = None
    edits = []
    for m in STAND7_DEF.finditer(text):
        cls, mem = m.group(1), m.group(2)
        names = [k for k in syms if k.startswith('?%s@?$%s@' % (mem, cls))]
        bound = {int(syms[k], 16) for k in names}
        if not bound or bound & tagged: continue
        # the evidence of a stand-in: an `inline` body in an /Od /Ob1 unit (/Ob1 expands inline functions), which MSVC
        # still called out of line (the recipe binds the name); one instantiation (another one the unit uses would
        # lose its definition under GCC)
        if not re.search(r'\binline\b', m.group(0)) or not {'/Od', '/Ob1'} <= set(info[0]) or len(names) != 1:
            stats['STAND7-GATE'] += 1; continue
        if elsewhere is None: elsewhere = _all_unit_functions(path) - info[1]
        if not bound <= elsewhere:
            stats['STAND7-ADDR'] += 1; continue
        if re.search(r'//[ \t]*@fnv[ \t]+0x[0-9a-fA-F]+[^\n]*\n(?:[ \t]*//[^\n]*\n)*\s*$', text[max(0, m.start() - 300):m.start()]):
            continue
        c = prepare.match_brace(text, m.end() - 1)
        edits.append((m.start(), c))
    if not edits: return text
    for a, c in sorted(edits, reverse=True):
        text = text[:a] + '#ifdef _MSC_VER /* fnv-nx STAND7 */\n' + text[a:c] + '\n#endif\n' + text[c:]
    stats['STAND7'] += len(edits)
    return text

REFS_RULES = [r_operator_temporaries, r_address_of_temporaries, r_lane_array_address, r_std_definitions,
              r_conditional_bool_gates, r_restrict_reference_args, r_placement_only_delete,
              r_namespace_crt_calls, r_forceinline_declarations, r_vfptr_empty_base, r_recon_bodies,
              r_header_unit_instantiations, r_later_empty_base,
              r_implicit_bound_destructors, r_split_virtual_declarators, r_hk_referenced_object_slots, r_vtable_dtor_slot, r_placeholder_slots, r_bound_dtor_stand_ins,
              r_template_member_stand_ins, r_raw_vtable_slots, r_rawvt_ssa, r_empty_string_objects]
RULES += REFS_RULES
