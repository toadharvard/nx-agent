"""s1_compile.py - prepare rules of stream S1-compile (make every unit parse for AArch64 ILP32 with GCC).

Each rule is fn(path, text, stats) -> text (see tools/rules/README.md); ids are cited in the journal.
"""
import os
import re

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

def r_sal_attributes(path, text, stats):
    """SAL1: drop MSVC attribute-syntax SAL annotations ([SA_Pre(Null=SA_No)] in pasted CRT prototypes); they
    carry no semantics for the compiler."""
    text, n = SAL_ATTR.subn('', text)
    stats['SAL1'] += n
    return text

# ---------------------------------------------------------------------------------------------------------- DECL2

SDK_TAGS = ('tagRECT tagPOINT tagSIZE _FILETIME _SYSTEMTIME _GUID _RTL_CRITICAL_SECTION _LARGE_INTEGER _ULARGE_INTEGER '
            '_OVERLAPPED _WIN32_FIND_DATAA _SYSTEM_INFO tagMSG tagPALETTEENTRY _LUID _RGNDATA tagBITMAPFILEHEADER '
            'tagBITMAPINFOHEADER').split()
SDK_TYPEDEFS = {n: '"fnvnx_win32.h"' for n in 'DWORD BOOL BYTE WORD LONG UINT HANDLE HRESULT LPVOID ULONG_PTR DWORD_PTR'.split()}
SDK_TYPEDEFS.update({'size_t': '<stddef.h>', 'ptrdiff_t': '<stddef.h>', 'va_list': '<stdarg.h>'})
SDK_STRUCT = re.compile(r'^[ \t]*(typedef\s+)?(?:struct|class|union)\s+(%s)\s*\{' % '|'.join(SDK_TAGS), re.M)
SDK_TYPEDEF = re.compile(r'^[ \t]*typedef\s+[\w \t\*]+?[\s\*](%s)\s*;[ \t]*$' % '|'.join(SDK_TYPEDEFS), re.M)
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
        found.append((m.start(), end + tail.end(), '"fnvnx_win32.h"'))
    for m in SDK_TYPEDEF.finditer(text):
        found.append((m.start(), m.end(), SDK_TYPEDEFS[m.group(1)]))
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
    r'^[ \t]*(?:static\s+)?(?:inline\s+)?(?:void\s*\*|void)\s*(?:__cdecl\s+)?operator\s+(?:new|delete)\s*(?:\[\s*\])?\s*'
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

CTOR_CALL = re.compile(r'(\.|->)\s*((?:::)?(?:\w+::)*?)(\w+)::(\3)\s*\(')

def r_ctor_calls(path, text, stats):
    """CTOR1: explicit constructor calls (MSVC extension) p->A::A(args) / o.A::A(args) -> placement new
    ::new ((void*)(A*)(p)) A(args): both construct an A in place at that address (vtable pointer included)."""
    if '::' not in text: return text
    out, pos, n = [], 0, 0
    for m in CTOR_CALL.finditer(text):
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
        out.append(text[pos:o + 1]); out.append(' FNVNX_MSVC_BASE_END(%s)' % last); pos = o + 1
        stats['TAIL1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- LINK1

C_DATA_DECL = re.compile(r'^([ \t]*extern)\s+"C"(\s+[\w \t\*&:<>,]*?[\s\*&]\w+(?:\s*\[[^\]]*\])*\s*;)', re.M)

def r_c_data_linkage(path, text, stats):
    """LINK1: `extern "C" T DAT_x;` at file scope -> `extern T DAT_x;`. The decomp declares the same image variable
    with both linkages; MSVC kept them apart, GCC rejects the pair. For a variable at global scope both linkages
    name the same symbol under the Itanium ABI (no mangling), so nothing changes for the link."""
    if 'extern "C"' not in text: return text
    spans, out, pos = None, [], 0
    for m in C_DATA_DECL.finditer(text):
        if '(' in m.group(2): continue
        if spans is None: spans = prepare.scope_spans(text)
        if prepare.scope_chain(spans, m.start()): continue
        out.append(text[pos:m.start()]); out.append(m.group(1) + m.group(2)); pos = m.end()
        stats['LINK1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- GOTO1

SCALAR_INIT = re.compile(r'^([ \t]*)((?:(?:unsigned|signed|volatile)\s+)*(?:bool|char|short|int|long(?:\s+long)?|float|'
                         r'double|unsigned|\w+\s*\*+))\s+(\**\s*\w+)\s*=\s*([^;{}=][^;{}]*);', re.M)

def r_goto_scalar_inits(path, text, stats):
    """GOTO1: in functions that use goto, `T x = e;` (scalar or pointer T) -> `T x; x = e;` on the same line. MSVC
    lets a goto jump past such an initialization; C++ (and GCC even with -fpermissive) does not, but jumping past a
    declaration without initializer is allowed, and for a scalar the split changes nothing."""
    if 'goto' not in text: return text
    spans = [sp for sp in prepare.scope_spans(text) if sp[2] == 'function']
    edits = []
    for o, c, k, h in spans:
        body = text[o:c]
        if not re.search(r'\bgoto\s+\w+\s*;', body): continue
        for m in SCALAR_INIT.finditer(text, o, c):
            t = m.group(2)
            if re.match(r'(?:return|else|case|goto|delete|throw|typedef|using)\b', t) or ',' in m.group(4) and '(' not in m.group(4):
                continue
            name = m.group(3).lstrip('* \t')
            edits.append((m.start(), m.end(), '%s%s %s; %s = %s;' % (m.group(1), t, m.group(3), name, m.group(4))))
    if not edits: return text
    out, pos = [], 0
    for s, e, r in sorted(set(edits)):
        if s < pos: continue
        out.append(text[pos:s]); out.append(r); pos = e
        stats['GOTO1'] += 1
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

def r_string_array_address(path, text, stats):
    """STR1: `&str_01082ab8` where the decomp declares `extern char str_01082ab8[];` -> `str_01082ab8`: the same
    address; MSVC converted char (*)[] to const char* silently, GCC does not."""
    if 'str_' not in text: return text
    arrays = set(re.findall(r'\b(str_[0-9a-fA-F]{8})\s*\[', text))
    if not arrays: return text
    def sub(m):
        if m.group(1) not in arrays: return m.group(0)
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
# prototype did implicitly (same bits; ILP32 pointers and ints are both 32-bit).

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
        new = []
        for a, t in zip(args, types):
            if t is None or not a.strip(): new.append(a); continue
            new.append(' (%s)(%s)' % (t, a.strip()) if new else '(%s)(%s)' % (t, a.strip()))
        out.append(text[pos:m.end()]); out.append(','.join(new)); out.append(')'); pos = close
        stats['CALL1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- RCAST1

REF_CAST_CALL = re.compile(r'\(\s*((?:const\s+)?(?:volatile\s+)?(?:::)?[A-Za-z_][\w:<>, \t\*]*?&)\s*\)\s*'
                           r'(?=(?:::)?[A-Za-z_][\w:]*\s*\()')

def r_ref_cast_temporaries(path, text, stats):
    """RCAST1: `(T&)f(args)` where f(args) is a temporary (a functional cast or a call returning by value) ->
    `(T&)fnvnx_lvalue(f(args))`. MSVC binds a C-style reference cast to a temporary, which lives to the end of the
    full expression; fnvnx_lvalue (fnvnx_compat.h) returns the same object as an lvalue for that same lifetime.
    A call that already returns an lvalue is returned unchanged."""
    if '&)' not in text: return text
    out, pos = [], 0
    for m in REF_CAST_CALL.finditer(text):
        if m.start() < pos: continue
        open_at = text.index('(', m.end())
        close = _call_end(text, open_at)
        if re.match(r'\s*(?:\.|->|\[|\()', text[close:]): continue   # the cast applies to a longer postfix
        out.append(text[pos:m.end()]); out.append('fnvnx_lvalue(' + text[m.end():close] + ')'); pos = close
        stats['RCAST1'] += 1
    out.append(text[pos:])
    return ''.join(out)

# ---------------------------------------------------------------------------------------------------------- CS28
#
# The decomp's generated layout headers spell the critical section of engine classes as `_RTL_CRITICAL_SECTION`
# with a 28-byte layout (include/_RTL_CRITICAL_SECTION.h: 16-byte Synchronization.RawEvent[4], LockCount,
# RecursionCount, OwningThread - the Xbox 360 type), and every offset after it is asserted on that basis
# (RecursiveLock::m_uiFirstValue == 0x1C, BSThread 0x40, AbstractHeap, IO_Thread, NiCriticalSection ...). The
# byte-matching code of those units was compiled against that layout, so the PC image has 28 bytes there too; the
# PC CRITICAL_SECTION is 24. FNVNX_CS28 (fnvnx_win32.h) is the PC CRITICAL_SECTION followed by 4 unidentified bytes:
# the API works on the first 24 bytes (CALL1 casts &x to LPCRITICAL_SECTION), every decomp offset stays true.

CS28_FILE = re.compile(r'#\s*include\s*"(?:[^"]*/)?_RTL_CRITICAL_SECTION\.h"|'
                       r'\b(?:class|struct)\s+_RTL_CRITICAL_SECTION\s*\{[^}]*\bSynchronization\b', re.S)
CS28_MEMBER = re.compile(r'^([ \t]*)_RTL_CRITICAL_SECTION([ \t]+\w+[ \t]*;)', re.M)

def r_cs28_members(path, text, stats):
    """CS28: `_RTL_CRITICAL_SECTION m;` class members in files that use the decomp's 28-byte layout of it ->
    `FNVNX_CS28 m;` (see above)."""
    if '_RTL_CRITICAL_SECTION' not in text or not CS28_FILE.search(text): return text
    spans, out, pos = prepare.scope_spans(text), [], 0
    for m in CS28_MEMBER.finditer(text):
        chain = prepare.scope_chain(spans, m.start())
        if not chain or chain[-1][0] != 'class': continue
        out.append(text[pos:m.start()]); out.append(m.group(1) + 'FNVNX_CS28' + m.group(2)); pos = m.end()
        stats['CS28'] += 1
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
# Targeted renames for single files (pragmatic fixes on the boot path; every entry says why).

UNIT_RENAMES = {
    # tesdatahandler: a local `struct FILETIME {low, high}` / `struct WIN32_FIND_DATAA` with its own member names,
    # in a header that also needs the SDK's file functions; the local types keep their layout under new names.
    'fallout_shared/tesdatahandler_files.h': [(r'\bFILETIME\b', 'TesFileTime'), (r'\bWIN32_FIND_DATAA\b', 'TesFindData')],
    'fallout_shared/tesdatahandler_decls.h': [(r'\bFILETIME\b', 'TesFileTime'), (r'\bWIN32_FIND_DATAA\b', 'TesFindData')],
    'fallout_shared/tesdatahandler.cpp': [(r'\bFILETIME\b', 'TesFileTime'), (r'\bWIN32_FIND_DATAA\b', 'TesFindData')],
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

# ---------------------------------------------------------------------------------------------------------- KEEP1

FNV_TAG = re.compile(r'^[ \t]*//[ \t]*@fnv[ \t]+0x[0-9a-fA-F]+[^\n]*\n((?:[ \t]*//[^\n]*\n)*)([ \t]*)(?=\S)', re.M)
KEEP_SKIP = re.compile(r'(?:#|//|/\*|template\b|extern\s*"C|namespace\b|typedef\b|using\b|struct\b|class\b|union\b|'
                       r'enum\b|static_assert\b|FNV_STATIC_ASSERT\b|[{}])')

def r_keep_definitions(path, text, stats):
    """KEEP1: FNVNX_KEEP (__attribute__((used))) in front of every declaration tagged `// @fnv 0x...`, so a definition
    the optimizer inlines everywhere is still emitted out of line at its address (the link step binds by address,
    tools/link). Same line, so line numbers are kept."""
    if '@fnv' not in text: return text
    out, pos = [], 0
    for m in FNV_TAG.finditer(text):
        if KEEP_SKIP.match(text, m.end()): continue
        out.append(text[pos:m.end()]); out.append('FNVNX_KEEP '); pos = m.end()
        stats['KEEP1'] += 1
    out.append(text[pos:])
    return ''.join(out)

RULES = [r_unit_renames, r_keep_definitions, r_if_exists, r_sal_attributes, r_cs28_members, r_sdk_types, r_exception_specs, r_placement_new, r_ctor_calls, r_c_data_linkage, r_goto_scalar_inits, r_sdk_guarded_copies, r_string_array_address, r_interlocked_fallbacks, r_win32_call_args, r_ref_cast_temporaries, r_global_delete_noexcept, r_typeid_include,
         r_msvc_base_layout]
