"""bodies.py - C++ functions GCC references that no unit defines (stream S2d-bodies; docs/LINK.md, "Declared-only
functions").

The decomp's headers declare many functions without a body: in the MSVC build they were inlined bodies, compiler-
generated special members, or definitions in another unit under another decorated name or class shell. GCC calls
them out of line, so after `resolve` they stay `unresolved` / `unresolved-ctor-dtor` (or `ctor-dtor-trap`), and the
guest link binds them to trap stubs. `fnvlink.py bodies out/ tree/ [--msvc-objs DIR] [--exe EXE]` classifies every such name,
in this order (first that applies):

1. `addr` - the same function at an original address under another name: the Itanium name, with class shells
   (`X_shell`) and then namespaces relaxed, matches MSVC names of exactly one address the native build
   defines (`addrmap.tsv` F/C/X; never a G: a deleting destructor is not a D1). Bound to `__fnv_<addr>`.
1a. `template-fold` - a member of a class template over pointer arguments (`BSSimpleList<TESPackage*>::~BSSimpleList`)
   whose other pointer instantiations units.json places at one address the native build defines, at least two of
   them there: MSVC's linker folded their identical code (the body does not depend on the pointee), so this
   instantiation's code is that body too. Bound to `__fnv_<addr>`; C2/D2 to the complete-object definition.
1c. `exe-sret` (after exe-addr's checks) - the exe calls one address whose definition takes one more pointer
   parameter first (after `this`) where the reference returns a class by value: MSVC's hidden return pointer, which
   the decomp writes out (`FUN_0045bb20_cls::ApplyScaled(target*, float)` for 53 vector `operator*(float)`).
   An adapter in the referencing unit calls the address with a local's address and returns it
   (`sret_line`: the type from the declaration by decltype, trivially copyable and destructible types only).
1b. `exe-addr` (`--exe`, the player's FalloutNV.exe) - the referencing unit's pristine object calls the MSVC name:
   every pristine object's calls of it (REL32 in functions placed by units.json, or at the address a placed
   function's call reaches, bytes checked against the exe; a DIR32 there, the address taken, counts the same)
   give one native address of the right kind and shape
   (`exe_pass`). Checked before the synthesized classes: an address beats `= default`.
1d. `exe-data` (`--exe`) - a decomp placeholder variable (an unmangled name refs.tsv leaves `unresolved`:
   `extern A11MM g_a11mm;`, a function pointer standing for an import `extern int (__stdcall* LocalIncrement)(void*)`)
   whose MSVC name the pristine objects' placed code addresses (DIR32): the exe's word at every site gives one image
   data address (exe_data_pass; variant units' other stack layouts and vftable-slot ??_G functions are placed by
   shape, `same_shape`). resolve binds it there like any image variable (an IAT slot for an import's pointer).
   Before decl-data: a typed zero copy of the game's memory manager is wrong where the image's is meant.
2. `msvc-empty` - a pristine MSVC object (`<dir>/u<index>.obj`, S11) defines the function (COMDAT code) and its x86
   body is only `ret` / `ret N` / `mov eax, ecx; ret[ N]` (a constructor returning this): an AArch64 `ret` stub
   under the Itanium name.
3. `msvc-inline` - a pristine object defines a real body: that unit's source holds it (the objects are the decomp
   compiled byte for byte), so the unit is compiled again with `-fkeep-inline-functions` and the wanted symbols are
   kept (`vtables.gc_weaken`: everything else of the host is undefined first, then dropped). A body emitted under
   a shell's name is renamed to the wanted one when the relaxed key pairs exactly one emitted symbol.
4. `vcall` - a virtual member function GCC calls directly where MSVC 2008 called through the vptr (`a->m.f()`
   with `m` a member object: GCC's front end resolves the call statically, and the decomp defines the function
   nowhere because the image's vftable slot holds it). A probe in the referencing unit reads GCC's own
   pointer-to-member constant `&C::f` (ARM-style Itanium ABI: vtable byte offset, 2 * this adjustment + 1) and
   a stub `__fnvnx_bdvc_<hash>` adjusts `this`, loads the slot from the object's vptr and tail-calls it. Not
   when the unit's pristine object references the MSVC name itself: a qualified call `B::f()`.
4b. `gdel` - a D1 (or a D2 of a class without virtual bases) that would be missing or `= default`, whose class has
   one scalar deleting destructor ??_G at an address the native build defines as a member function taking
   (unsigned int flags): MSVC folded the destructor's body into ??_G and calls that. A stub calls it with flags 0
   (destroy, do not free). After `exe-addr`: an address of the destructor itself wins.
5. `special-default` - a constructor without parameters, a copy constructor, a destructor or a copy assignment of
   a class (not a template) that no pristine object defines: MSVC inlined it everywhere, and the decomp declares it
   without a body. Synthesized as the out-of-line `= default` definition in the referencing unit (the implicit
   member's semantics: bases and members constructed / copied / destroyed, D0/D1/D2 all emitted). Not a decomp
   body: listed as such, and the MSVC code is the reference where a site misbehaves.
4c. `tree-inline` - (after vcall) the tree has the body inline in a unit's own source, out of line (`inline
   C::f() {...}` in a .cpp) or in the class body of a copy of C: that unit is compiled again with
   `-fkeep-inline-functions` like msvc-inline and only the wanted symbol is kept (its own name, or the one the
   relaxed key pairs, as for msvc-inline). A host that does not emit it leaves the name `missing`. Across copies of
   a class (s2d not-compiled): the emitted member of another copy (copy_key: unit namespaces removed, parameters and
   cv exact) is renamed to the wanted name when the layout probe (LayoutGate) gives both classes and their
   class-like parameters one size; names MSVC calls out of line (the vcall check's pristine reference) and
   constructors / destructors are taken too, always through the gate; up to three hosts are tried. The decomp's
   own copy namespaces of `std` (COPY_NS_STD: `ac8parse::char_traits<T>`, declared-only, against the `std` copy of
   the same file that has the bodies) count as copies of one class.
   A library member a unit declares as an explicit specialization and defines nowhere (`template <> R C<A>::m(P);`,
   the SPEC1 shape: `fnvnx_vc9::string::assign`) is instantiated explicitly (member_inst_line) in a unit that reaches
   the body, the port's shim headers included, and declares no specialization of it (member_inst_hosts): GCC alone
   leaves only a local `.isra` clone of such a member in the other units.
1e. `copy-addr` (after exe-addr) - a member of a class copy whose same member of another copy (copy_key) units.json
   places at one address the native build defines, with one layout on both sides (LayoutGate): `__fnv_<addr>`.
5c. `addr-host` - an exe address refs.tsv binds references to (`code-defmissing`) whose placing unit's object
   defines nothing there: a `??_G` scalar deleting destructor (no GCC counterpart: `gdel_line` defines
   `__fnv_<addr>(C *, unsigned)` in the placing unit), or an inline body GCC did not emit (the unit compiled with
   -fkeep-inline-functions; the one emitted symbol the MSVC name's relaxed key pairs; `__fnv_<addr>: b` to it).
5d. `decl-data` - a variable the units declare and nothing defines, with no exe address (a static member
   `not a function`, or an unmangled name refs.tsv leaves `unresolved`: a decomp placeholder global): its
   zero-initialized definition with the declared type in the first referencing unit (data_line). What the guest
   link's 4 KiB data stub stood for, now sized and typed; a function or an untyped name falls back to missing.
5a. `thunk` - a this-adjusting thunk (`_ZTh<off>_f`, `_ZTv<off>_n<vcall>_f`) refs.tsv leaves undefined
   (`special-unresolved`): GCC emits thunks only with f's definition, and f is bound by address or synthesized.
   A weak AArch64 thunk adjusts w0 (and adds the vcall offset from the vtable) and tail-calls f by name.
5b. `crt-rule` - a runtime member the C++ library defines by its contract, not by the decomp: the MSVC STL's
   throwers `C::_Xlen()` / `C::_Xran()` / `C::_Xinvalid_argument()` of the decomp's string and vector copies (a stub
   that throws std::length_error / out_of_range / invalid_argument through libstdc++, as MSVC's did), and class
   placement deletes `C::operator delete(void*, void*)` (a `ret`: the standard placement delete does nothing), and
   the decomp's `basic_streambuf` copies' `_Lock()` / `_Unlock()` (CRT_MUTEX: `this + 4` is the std::_Mutex, S4's
   fnvnx_mutex_lock / unlock), and the facet copies' `_Register()` / static `_Register(facet*)` (CRT_FACET: S4's
   fnvnx_facet_register, MSVC's _Facet_Register at 0x00ec3599), and NiPointer<T>'s destructor for a T no unit
   defines (CRT_NIPTR: a tail call to NiPointer<NiRefObject>'s; the same code on the NiRefObject base at 0).
   After the address classes: a decomp body at an exe address wins.
6. `missing` - the rest: real decomp gaps, for the real-exe path (S3), with the referencing units.

A synthesized item GCC refuses (member without default constructor, private base, ...) falls back to `missing`
with the reason `synth-failed`.

Output (`out/bodies/`, local: derived from the decomp, never committed): `bodies.tsv` (name, class, address,
target, referencing units, note), `bind.tsv` (name -> address symbol, read by the next `resolve`), `stats.txt`,
`synth.tsv` (host unit, object: linked by the next `resolve` as extra objects of the host, like
`vtables/synth.tsv`), `<unit>.bd.o`, `zz_bd_empty.o`, `zz_bd_crt.o`.
"""
import collections
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import time
import sys

import sigs

TRAPPED = ('unresolved', 'unresolved-ctor-dtor', 'ctor-dtor-trap')
SHIM_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'shim'))
ADDR_SYM = '__fnv_%08x'
SHELL = re.compile(r'(?:_shell|_Shell|_SHELL)$')
UNIT_NS = re.compile(r'^fnv_[0-9a-f]{8}$')            # the decomp's per-function namespaces (fnvlink.UNIT_NS)
UNIT_NS_ANY = re.compile(r'\bfnv_[0-9a-f]{8}::')
IMAGE_SCN_CNT_CODE, IMAGE_SCN_LNK_COMDAT = 0x20, 0x1000
VARIANT_KIND = {'C1': 'C', 'C2': 'C', 'D1': 'X', 'D2': 'X'}   # addrmap kind an Itanium ctor/dtor variant may bind to


# ---------------------------------------------------------------------------------------------------- inputs

def _table(name):
    """a decomp-derived table of fnv-nx-build (tools/tables.py: FNVNX_TABLES, sha256 against tools/tables.pin.json)"""
    import importlib.util
    s = importlib.util.spec_from_file_location('fnvnx_tables', os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tables.py'))
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m.path(name)


def load_trapped(refs_tsv, names_txt=None):
    """refs.tsv rows still undefined after resolve -> {Itanium name: set(referencing units)}; names_txt (the guest
    link's undefined.txt) adds names with no unit."""
    out = collections.defaultdict(set)
    if refs_tsv and os.path.exists(refs_tsv):
        for line in open(refs_tsv, encoding='utf-8', errors='surrogateescape'):
            f = line.rstrip('\n').split('\t')
            if len(f) >= 3 and f[2] in TRAPPED and f[1].startswith('_Z'):
                out[f[1]].add(f[0].split('#')[0])
    if names_txt and os.path.exists(names_txt):
        for line in open(names_txt, encoding='utf-8', errors='surrogateescape'):
            n = line.strip()
            if n.startswith('_Z') and not n.startswith(('_ZTV', '_ZTI', '_ZTS', '_ZTh', '_ZTv', '_ZTc', '_ZTT')):
                out.setdefault(n, set())
    return dict(out)


def load_native(addrmap_tsv):
    """addrmap.tsv -> {address: kind} of the code the native build defines (F, C, X, G)."""
    nat = {}
    if os.path.exists(addrmap_tsv):
        for line in open(addrmap_tsv, encoding='utf-8', errors='surrogateescape'):
            if line.startswith('#'):
                continue
            f = line.rstrip('\n').split('\t')
            if len(f) >= 2 and f[1] in ('F', 'C', 'X', 'G'):
                nat[int(f[0], 16)] = f[1]
    return nat


def load_native_names(addrmap_tsv):
    """addrmap.tsv -> {address: native Itanium symbol} of the F addresses (Classifier.dtor_as_fun)."""
    res = {}
    if os.path.exists(addrmap_tsv):
        for line in open(addrmap_tsv, encoding='utf-8', errors='surrogateescape'):
            f = line.rstrip('\n').split('\t')
            if not line.startswith('#') and len(f) >= 4 and f[1] == 'F' and f[3].startswith('_Z'):
                res[int(f[0], 16)] = f[3]
    return res




def load_provider_params(crt_symbols_tsv=None):
    """platform/crt/crt_symbols.tsv -> {address: parameter count} of the rows whose identification (S4's reading of the
    exe function) spells a C++ parameter list: `std::_Lockit::_Lockit(int)` -> 1 (the object is not counted),
    `std::_Locinfo::_Locinfo_ctor(_Locinfo*, const std::string&)` -> 2, `std::locale::_Getgloballocale() [note]` -> 0.
    A trailing `[note]` is dropped first; a row with no parameter list is not listed."""
    res = {}
    path = crt_symbols_tsv or _table('crt_symbols.tsv')
    if not os.path.exists(path):
        return res
    for line in open(path, encoding='utf-8', errors='surrogateescape'):
        f = line.rstrip('\n').split('\t')
        if line.startswith('#') or len(f) < 6 or not f[0].startswith('0x'):
            continue
        m = re.search(r'\((.*)\)\s*(?:const)?\s*$', re.sub(r'\s*\[.*\]\s*$', '', f[2]))
        if not m:
            continue
        s, depth, n = m.group(1).strip(), 0, 0
        if s and s != 'void':
            n = 1
            for ch in s:
                depth += ch in '<('
                depth -= ch in '>)'
                n += ch == ',' and depth == 0
        res[int(f[0], 16)] = n
    return res


def load_providers(addrmap_tsv):
    """addrmap.tsv -> {address: provider} of the F addresses S4's runtime implements (note `provider: <name>`):
    the platform's own definition of the exe function at that address (platform/crt/crt_symbols.tsv)."""
    res = {}
    if os.path.exists(addrmap_tsv):
        for line in open(addrmap_tsv, encoding='utf-8', errors='surrogateescape'):
            f = line.rstrip('\n').split('\t')
            if not line.startswith('#') and len(f) >= 6 and f[1] == 'F' and f[5].startswith('provider: '):
                res[int(f[0], 16)] = f[5][len('provider: '):].strip()
    return res


# ---------------------------------------------------------------------------------------------------- keys

GHIDRA = re.compile(r'^(?:[A-Z]{1,4}|thunk_FUN|switchD|caseD|Unk|unk|fnv|vf|v|s|f)$')   # FUN_<addr8>, DAT_, S_, ...


def norm(part):
    """A class shell's name -> the class it stands for (`NiNode_shell` -> `NiNode`). An address suffix is not a
    shell: `Entry_004d4b00` and `Entry_0075d6b0` are different classes (link fa883870e756), like `FUN_00402b40`."""
    m = SHELL.search(part)
    if not m or GHIDRA.match(part[:m.start()]) or len(part[:m.start()]) < 3:
        return part
    return part[:m.start()]


def relaxed_keys(sig):
    """(tier, key) strict -> loose for a parsed function name: class shells stripped (`X_shell`); then
    a unit-local copy of a class (`fnv_00650ea0::NiFixedString`, resolve's ns binding) keyed as the global class
    (key 'g'), which a name without unit namespaces also gives, so two units' copies never pair with each other.
    Parameters at sigs level 2 (cv and long/int spellings ignored).
    Last tier, `unit-targ`: a class template instantiated over a unit-local copy (`NiPointer<fnv_009dfa00::
    ActorPathingMessageQueue>`) keyed as the instantiation over the global class, the same ns binding one level
    down. Only for lookups: Classifier.by_address takes it only where the layout probe gives the class and its
    template arguments one size in the referencing and the defining units (targ_layout_ok)."""
    if sig.kind != 'func' or sig.params is None or not sig.qual:
        return []
    p2 = tuple(sigs.canon_param(p, 2) for p in sig.params)
    last = sig.qual[-1]
    scope = tuple(norm(q) for q in sig.qual[:-1])
    keys = [('shell', ('s', scope, last, p2))]
    pu = tuple(UNIT_NS_ANY.sub('', p) for p in p2)
    su = tuple(q for q in scope if not UNIT_NS.match(q))
    if (pu, su) == (p2, scope):
        keys.append(('global', ('g', scope, last, p2)))      # what a unit-local copy of this class looks for
    elif su:
        keys.append(('unit-ns', ('g', su, UNIT_NS_ANY.sub('', last), pu)))
    st = tuple(UNIT_NS_ANY.sub('', q) for q in su)
    if st and st != su:
        keys.append(('unit-targ', ('g', st, UNIT_NS_ANY.sub('', last), pu)))
    return keys


def def_keys(sig):
    """The relaxed keys a definition (or a name standing for one) is indexed under: not a unit-local copy's
    global key (a global reference never binds to one unit's copy)."""
    return [(t, k) for t, k in relaxed_keys(sig) if t not in ('unit-ns', 'unit-targ')]


def targ_types(qual):
    """Class-like type names inside the template arguments of a qualified name's scope, outermost first
    (`NiPointer<fnv_009dfa00::A>` -> ['NiPointer<fnv_009dfa00::A>', 'fnv_009dfa00::A'])."""
    out = []
    for q in qual[:-1]:
        if '<' not in q:
            continue
        out.append(q)
        i = q.find('<')
        for a in sigs.split_top(q[i + 1:q.rfind('>')]):
            a = a.strip().rstrip('*&').strip()
            a = re.sub(r'^(?:const|volatile)\s+|\s+(?:const|volatile)$', '', a)
            if a and not re.match(r'^-?\d+$', a) and not SCALARS.match(a):
                out.append(a)
    return out


def targ_layout_ok(layouts, ref_units, ref_qual, def_units):
    """The class of a `unit-targ` pair and each class-like template argument: where the layout probe sizes the
    referencing unit's spelling and the defining unit's global spelling, they agree. A type the probe does not
    size in either unit is no evidence against the pair (NiPointer<T> holds a T* either way); the class itself
    must be sized in both."""
    if not layouts or not def_units:
        return False
    for k, t in enumerate(targ_types(ref_qual)):
        t = sigs.canon_type(t)
        g = UNIT_NS_ANY.sub('', t)
        ref = set().union(*(layouts.get((u, t), set()) for u in ref_units))
        dfn = set().union(*(layouts.get((u, g), set()) for u in def_units))
        if k == 0 and not (ref and dfn):
            return False
        if ref and dfn and ref != dfn:
            return False
    return True


def special_of(isig):
    """'ctor0' / 'copy' / 'dtor' / 'assign' / 'd0' for an Itanium special member we may synthesize, else None."""
    if isig.kind != 'func' or len(isig.qual) < 2 or isig.params is None:
        return None
    cls = re.sub(r'<.*$', '', isig.qual[-2])
    self_ref = [sigs.canon_param(p, 0) for p in isig.params] == ['%s const&' % isig.qual[-2]] or \
        [sigs.canon_param(p, 0) for p in isig.params] == ['const %s&' % isig.qual[-2]]
    last = isig.qual[-1]
    if last == '{ctor}' and isig.variant in ('C1', 'C2'):
        return 'ctor0' if not isig.params else ('copy' if self_ref else None)
    if last == '{dtor}' and isig.variant in ('D1', 'D2'):
        return 'dtor'
    if last == '{dtor}' and isig.variant == 'D0':
        return 'd0'
    if last == 'operator=' and self_ref and cls:
        return 'assign'
    return None


def class_spelling(isig):
    """C++ spelling of the class of a member, None if it cannot be named out of line here (template, anonymous)."""
    scope = isig.qual[:-1]
    if not scope or any('<' in p or '{' in p or '`' in p or "'" in p or '(' in p for p in scope):
        return None
    return '::' + '::'.join(scope)


def default_line(kind, isig):
    sp = class_spelling(isig)
    if sp is None:
        return None
    base = isig.qual[-2]
    return {'ctor0': '%s::%s() = default;' % (sp, base),
            'copy': '%s::%s(const %s &) = default;' % (sp, base, sp),
            'dtor': '%s::~%s() = default;' % (sp, base),
            'd0': '%s::~%s() = default;' % (sp, base),
            'assign': '%s &%s::operator=(const %s &) = default;' % (sp, sp, sp)}[kind]


def data_line(qual):
    """`decl-data`: a variable the units declare and nothing defines, with no exe address: its definition,
    zero-initialized, with the declared type (`decltype(N::x) N::x {};`; a global unqualified). None for a name
    it cannot spell (template, anonymous); GCC refuses a function, an array of unknown bound, a type without a
    default value, and the line falls back to `missing`."""
    if not qual or any('<' in p or '{' in p or '`' in p or "'" in p or '(' in p or ' ' in p for p in qual):
        return None
    q = '::'.join(qual)
    return 'decltype(%s) %s {};' % (q, q)


def load_cdata(refs_tsv):
    """Unmangled names refs.tsv leaves `unresolved` (C or namespace-scope variables, decomp placeholders) that are
    not named after an address -> {name: set(referencing units)}."""
    out = collections.defaultdict(set)
    if refs_tsv and os.path.exists(refs_tsv):
        for line in open(refs_tsv, encoding='utf-8', errors='surrogateescape'):
            f = line.rstrip('\n').split('\t')
            if len(f) >= 3 and f[2] == 'unresolved' and not f[1].startswith(('_Z', '__', '.', '$')) and \
                    re.match(r'^[A-Za-z_]\w*$', f[1]) and not re.search(r'[0-9a-fA-F]{8}$', f[1]):
                out[f[1]].add(f[0].split('#')[0])
    return dict(out)


SCALARS = re.compile(r'^(?:(?:un)?signed |const |volatile )*(?:void|bool|char|short|int|long|long long|__int64|'
                     r'float|double|long double|wchar_t|unsigned|signed)$')


def sret_line(isig, addr, this):
    """Adapter for `exe-sret`: the exe's function at addr takes the result's address as its first parameter
    after `this` (MSVC's hidden return pointer, written out by the decomp); GCC's caller wants the result by value.
    The result type comes from the declaration itself (decltype of a call); the exe builds it in raw storage, so it
    must be trivially copyable and destructible (a constructor of its own is fine: the exe's code runs it)."""
    if isig.kind != 'func' or isig.params is None or isig.raw_params is None or len(isig.qual) < 1:
        return None
    if any('<' in p or '{' in p or '`' in p or "'" in p or '(' in p for p in isig.qual):
        return None
    if this and len(isig.qual) < 2:
        return None
    q = '::' + '::'.join(isig.qual)
    ps = list(isig.raw_params)
    args = ', '.join('a%d' % k for k in range(len(ps)))
    decl = ', '.join('%s a%d' % (p, k) for k, p in enumerate(ps))
    call = ('this->%s(%s)' % (isig.qual[-1], args)) if this else '%s(%s)' % (q, args)
    xs = (['const volatile void*'] if this else []) + ['void*'] + ps
    sym = '__fnvnx_sr_%08x_%s' % (addr, hashlib.sha1(isig.text.encode('utf-8', 'surrogateescape')).hexdigest()[:8])
    return ('auto %s(%s)%s -> decltype(%s) { typedef decltype(%s) R; '
            'static_assert(__is_trivially_copyable(R) && __has_trivial_destructor(R), "sret"); '
            'extern void %s(%s) __asm__("%s"); alignas(R) unsigned char r[sizeof(R)]; %s(%sr%s); '
            'return *reinterpret_cast<R *>(r); }'
            % (q, decl, (' ' + isig.cv) if isig.cv else '', call, call, sym, ', '.join(xs), ADDR_SYM % addr,
               sym, 'this, ' if this else '', (', ' + args) if args else ''))


# ---------------------------------------------------------------------------------------------------- x86 bodies

def x86_empty(code):
    """True for an x86 body that does nothing: `ret`, `ret N`, `mov eax, ecx` then one of them (this returned)."""
    if code[:2] in (b'\x8b\xc1', b'\x89\xc8'):
        code = code[2:]
    return code == b'\xc3' or (len(code) == 3 and code[0] == 0xc2)


def pristine_bodies(objdir, nunits, wanted_tokens):
    """{MSVC name: (unit index, x86 bytes)} of the code COMDATs of every pristine object whose name holds one of
    wanted_tokens (`?<name>@` / `??0<class>@` prefixes; a cheap filter before demangling)."""
    import vslots
    out = {}
    for i in range(nunits):
        p = os.path.join(objdir, 'u%d.obj' % i)
        if not os.path.exists(p):
            continue
        try:
            c = vslots.coff(p)
        except Exception:   # a damaged object must not stop the step
            continue
        for name, si in c.defs.items():
            if not name.startswith('?') or name in out:
                continue
            if not (decoration_tokens(name) & wanted_tokens):
                continue
            s = c.syms[si]
            sec = c.secs[s.secnum]
            if not (sec.flags & IMAGE_SCN_CNT_CODE and sec.flags & IMAGE_SCN_LNK_COMDAT):
                continue
            ends = [v for v, n in c.symbols_in(s.secnum) if v > s.value]
            data = c.section_bytes(s.secnum)
            out[name] = (i, data[s.value:min(ends) if ends else len(data)])
    return out


def decoration_tokens(name):
    """('n', name) and ('c', special code, class) tokens of an MSVC decorated name (pristine_bodies' filter)."""
    m = re.match(r'^\?(\?[0-9A-Z_]|[^@?]+)@?([^@]*)', name)
    if not m:
        return set()
    if m.group(1).startswith('?'):
        cls = norm(m.group(2)[2:] if m.group(2).startswith('?$') else m.group(2))
        return {('c', m.group(1), cls), ('o', cls)}
    return {('n', m.group(1))}


def tokens_for(isig):
    """The decoration_tokens an MSVC name of the same function may have."""
    if not isig.qual:
        return set()
    last = isig.qual[-1]
    cls = norm(re.sub(r'<.*$', '', isig.qual[-2])) if len(isig.qual) > 1 else ''
    code = {'{ctor}': '?0', '{dtor}': '?1', 'operator=': '?4'}.get(last)
    if code:
        return {('c', code, cls)}
    if last.startswith('operator'):
        return {('o', cls)}
    return {('n', re.sub(r'<.*$', '', last))}


# ---------------------------------------------------------------------------------------------------- classify

def fold_key(sig):
    """A member of a class template whose template arguments are all pointers -> (scope, template, member,
    arity, kind) with unit namespaces and shells relaxed; else None. The key of template-fold."""
    if sig is None or sig.kind != 'func' or sig.params is None or len(sig.qual) < 2:
        return None
    cls = sig.qual[-2]
    i = cls.find('<')
    if i <= 0 or not cls.endswith('>'):
        return None
    args = [a.strip() for a in sigs.split_top(cls[i + 1:-1])]
    if not args or not all(a.endswith('*') for a in args):
        return None
    scope = tuple(norm(q) for q in sig.qual[:-2] if not UNIT_NS.match(q))
    last = re.sub(r'<.*$', '', sig.qual[-1]) if not sig.qual[-1].startswith('operator') else sig.qual[-1]
    return (scope, norm(cls[:i]), last, len(sig.params), len(args))


Row = collections.namedtuple('Row', 'name cls addr target units note host')


class Classifier:
    """units: fnvlink.load_units(); native: load_native(); msvc(name) / itanium(name) -> sigs.Sig;
    bodies: {MSVC name: (unit index, x86 bytes)} or None (no pristine objects); unit_names: [source] by index."""

    def __init__(self, units, native, msvc, itanium, bodies=None, unit_names=(), layouts=None, native_names=None):
        self.native, self.itanium, self.bodies, self.unit_names = native, itanium, bodies, list(unit_names)
        self.layouts = layouts   # load_type_sizes(): the unit-targ tier is taken only with it
        self.native_names = native_names or {}   # load_native_names(): F definitions standing for a destructor
        self.msvc = msvc
        self.idx = collections.defaultdict(lambda: collections.defaultdict(set))   # key -> {addr: {msvc names}}
        self.addr_units = collections.defaultdict(set)   # addr -> units naming an indexed MSVC name there
        for u in units.values():
            for a, n in list(u.defs) + [(a, n) for n, a in u.refs.items()]:
                if a in native and n.startswith('?'):
                    m = msvc(n)
                    for _, k in def_keys(m):
                        self.idx[k][a].add(n)
                    self.addr_units[a].add(u.source)
        # copy key -> {addr: {MSVC names of a unit-local class copy}}, and the units naming each (by_copy)
        self.cidx = collections.defaultdict(lambda: collections.defaultdict(set))
        self.cunits = collections.defaultdict(set)
        for u in units.values():
            for a, n in list(u.defs) + [(a, n) for n, a in u.refs.items()]:
                if a in native and n.startswith('?') and 'fnv_' in n:
                    m = msvc(n)
                    if m.kind == 'func' and any(UNIT_NS.match(q) for q in m.qual):
                        k = copy_key(m, variant=False)
                        if k:
                            self.cidx[k][a].add(n)
                            self.cunits[n].add(u.source)
        self.fidx = collections.defaultdict(lambda: collections.defaultdict(set))  # fold key -> {addr: {names}}
        for u in units.values():
            for a, n in list(u.defs) + [(a, n) for n, a in u.refs.items()]:
                if a in native and n.startswith('?') and '?$' in n:
                    k = fold_key(msvc(n))
                    if k:
                        self.fidx[k][a].add(n)
        self.bidx = collections.defaultdict(set)   # key -> {msvc names with a pristine body}
        for n in bodies or {}:
            m = msvc(n)
            for _, k in [('exact', kk) for t, kk in m.keys() if t in ('exact', 'l1', 'l2')] + def_keys(m):
                self.bidx[k].add(n)

    def by_address(self, isig, units_=()):
        want = VARIANT_KIND.get(isig.variant, 'F') if isig.variant else 'F'
        if isig.variant == 'D0':
            return None
        for tier, k in relaxed_keys(isig):
            cands = {a: ns for a, ns in self.idx.get(k, {}).items()
                     if self.native.get(a) == want or self.dtor_as_fun(isig, a, ns)}
            if tier == 'unit-targ':   # the global instantiation, only with one layout on both sides
                cands = {a: ns for a, ns in cands.items()
                         if targ_layout_ok(self.layouts, units_, isig.qual, self.addr_units.get(a))}
            if len(cands) == 1:
                (a, ns), = cands.items()
                return tier, a, sorted(ns)[0]
            if len(cands) > 1:
                return 'ambiguous', None, ','.join('%08x' % a for a in sorted(cands)[:6])
        return None

    def by_copy(self, isig):
        """(address, MSVC name, units naming it) when another unit-local copy of the member's class has the same
        member (copy_key) at exactly one address the native build defines with the right kind, else None. The
        layout gate is the caller's (copy_addr_pass)."""
        k = copy_key(isig, variant=False)
        if not k or isig.variant == 'D0':
            return None
        want = VARIANT_KIND.get(isig.variant, 'F') if isig.variant else 'F'
        cands = {a: ns for a, ns in self.cidx.get(k, {}).items() if self.native.get(a) == want}
        if len(cands) != 1:
            return None
        (a, ns), = cands.items()
        n = sorted(ns)[0]
        return a, n, self.cunits[n]

    def dtor_as_fun(self, isig, a, ns):
        """A D1/D2 whose address units.json names with this destructor (an MSVC `??1` there) while the native build
        defines a function at it, the decomp's spelling `FUN_<a>(T*)` or a member `C::FUN_<a>()`: natively both
        take x0 = the object, as exe_pass's this-only rule (shape_ok) accepts for exe-addr."""
        if isig.variant not in ('D1', 'D2') or isig.params or self.native.get(a) != 'F' or a not in self.native_names:
            return False
        if not any(self.msvc(n).variant == 'dtor' for n in ns):
            return False
        d = self.itanium(self.native_names[a])
        if d.kind != 'func' or d.params is None or d.variant:
            return False
        dp = [p for p in d.params if p != '...']
        member = len(d.qual) >= 2 and not UNIT_NS.match(d.qual[-2])
        return (not dp and member) or (len(dp) == 1 and dp[0].rstrip().endswith('*'))

    def by_fold(self, isig):
        """(address, folded MSVC names) when the instantiations of this template member over pointer arguments
        that units.json places all share one address the native build defines, with at least two of them there
        (MSVC folded identical code: the body does not depend on the pointee), else None."""
        k = fold_key(isig)
        if not k or isig.variant == 'D0':
            return None
        want = VARIANT_KIND.get(isig.variant, 'F') if isig.variant else 'F'
        cands = {a: ns for a, ns in self.fidx.get(k, {}).items() if self.native.get(a) == want}
        if len(cands) == 1:
            (a, ns), = cands.items()
            if len(ns) >= 2:
                return a, sorted(ns)
        return None

    def msvc_body(self, isig):
        if not self.bodies:
            return None
        keys = [kk for t, kk in isig.keys() if t in ('exact', 'l1', 'l2')] + [k for _, k in relaxed_keys(isig)]
        for k in keys:
            got = self.bidx.get(k)
            if got:
                n = sorted(got)[0]
                return n, self.bodies[n]
        return None

    def classify(self, name, units_):
        isig = self.itanium(name)
        us = ','.join(sorted(units_))
        if isig.kind != 'func':
            return Row(name, 'missing', None, None, us, 'not a function (%s)' % isig.kind, None)
        hit = self.by_address(isig, units_)
        amb = None
        if hit and hit[0] != 'ambiguous':
            tier, a, mn = hit
            note = 'via %s %s' % (tier, mn) + (' (%s bound to the complete-object definition)' % isig.variant
                                                if isig.variant in ('C2', 'D2') else '')
            return Row(name, 'addr', a, ADDR_SYM % a, us, note, None)
        if hit:
            amb = 'addresses ' + hit[2]
        mb = self.msvc_body(isig)
        if mb:
            mn, (ui, code) = mb
            host = self.unit_names[ui] if ui < len(self.unit_names) else None
            if x86_empty(code) and isig.variant != 'D0':
                return Row(name, 'msvc-empty', None, None, us, '%s in u%d (%d bytes)' % (mn, ui, len(code)), host)
            return Row(name, 'msvc-inline', None, None, us, '%s in u%d (%d bytes)' % (mn, ui, len(code)), host)
        fold = self.by_fold(isig)
        if fold:
            a, ns = fold
            return Row(name, 'template-fold', a, ADDR_SYM % a, us, 'folded with %d instantiations, e.g. %s' %
                       (len(ns), ns[0]), None)
        sk = special_of(isig)
        if sk and default_line(sk, isig):
            host = sorted(units_)[0] if units_ else None
            if host:
                return Row(name, 'special-default', None, None, us, sk + (' (no MSVC body to compare)'
                                                                        if self.bodies is None else ''), host)
        why = [amb] if amb else []
        if sk and not default_line(sk, isig):
            why.append('special member of a template or unnamed class')
        elif sk:
            why.append('special member without a referencing unit')
        return Row(name, 'missing', None, None, us, '; '.join(why) or 'no definition, address or body', None)


# ---------------------------------------------------------------------------------------------------- runtime rules

CRT_THROW = {'_Xlen': ('_ZSt20__throw_length_errorPKc', '%s too long'),
             '_Xran': ('_ZSt20__throw_out_of_rangePKc', 'invalid %s position'),
             '_Xinvalid_argument': ('_ZSt24__throw_invalid_argumentPKc', 'invalid %s argument')}


# MSVC's basic_streambuf::_Lock() / _Unlock() are inline: `_Mylock._Lock()`, the std::_Mutex member at +4 (after the
# vptr) whose first word is the critical section pointer. The exe's _Sentry_base constructor 0x00ad2fa0 and its
# destructor do `add ecx, 4; call 0x00ec3277` / `0x00ec3280` (crt_symbols.tsv: std::_Mutex::_Lock / _Unlock, S4's
# fnvnx_mutex_lock / fnvnx_mutex_unlock over `void *self` = the _Mutex). The decomp's streambuf copies declare the
# two members and never define them (their `void* streamLock` is that first word).
CRT_MUTEX = {'_Lock': 'fnvnx_mutex_lock', '_Unlock': 'fnvnx_mutex_unlock'}

# MSVC's locale::facet::_Register() is `_Facet_Register(this)`: the facet goes on the list the CRT frees at exit
# (0x00ec3599, crt_symbols.tsv: std::locale::facet::_Facet_Register, S4's fnvnx_facet_register(void *facet)). The
# decomp's facet copies declare it as a static `_Register(facet*)` (blk_00ac0000_od_ob1_variant.cpp's
# `fnv_00acfab0::facet`, called as `facet::_Register(_Pfmod)` in use_facet) or as the member `_Register()`, and define
# neither. Either way the facet is the first argument (x0): a tail call.
CRT_FACET = 'fnvnx_facet_register'

# NiPointer<T>::~NiPointer() of a T the decomp only forward-declares (NiPointer<ProjectileAuxiliaryData>, inlined
# everywhere in the exe): no unit can instantiate it. Every NiPointer<T> destructor is the same code, `if (m_pObject)
# m_pObject->DecRefCount()` on the NiRefObject base, which Gamebryo classes hold at offset 0 (single inheritance): a
# tail call to the NiPointer<NiRefObject> instantiation the units emit (W COMDAT). FNVNX_NIPTR=0 turns it off.
CRT_NIPTR = '_ZN9NiPointerI11NiRefObjectED1Ev'


def crt_rule(isig):
    """('throw', libstdc++ thrower, message) / ('ret', None, None) / ('mutex', platform function, 'this + 4') for a
    name crt-rule defines, else None."""
    if isig.kind != 'func' or len(isig.qual) < 2 or isig.params is None:
        return None
    last = isig.qual[-1]
    if last in CRT_THROW and not isig.params:
        fn, msg = CRT_THROW[last]
        what = 'vector<T>' if re.search(r'(?i)vector', isig.qual[-2]) else 'string'
        return ('throw', fn, msg % what)
    if last == 'operator delete' and len(isig.params) == 2 and \
            all(sigs.canon_param(p, 2) == 'void*' for p in isig.params):
        return ('ret', None, None)
    if last in CRT_MUTEX and not isig.params and re.match(r'basic_streambuf\b', isig.qual[-2]):
        return ('mutex', CRT_MUTEX[last], 'this + 4')
    if last == '_Register' and isig.qual[-2] == 'facet' and (
            not isig.params or (len(isig.params) == 1 and re.fullmatch(r'(?:\w+::)*facet\s*\*', isig.params[0].strip()))):
        return ('facet', CRT_FACET, 'the facet')
    if isig.qual[-1] == '{dtor}' and isig.variant in ('D1', 'D2') and not isig.params and \
            re.fullmatch(r'NiPointer<.+>', isig.qual[-2]) and isig.qual[-2] != 'NiPointer<NiRefObject>' and \
            len(isig.qual) == 2 and os.environ.get('FNVNX_NIPTR', '1') != '0':
        return ('niptr', CRT_NIPTR, 'NiPointer<NiRefObject>')
    return None


def crt_pass(rows, itanium):
    """missing rows a runtime rule defines -> crt-rule."""
    out = []
    for r in rows:
        k = crt_rule(itanium(r.name)) if r.cls == 'missing' else None
        if k:
            r = r._replace(cls='crt-rule', note='throws %s("%s")' % (k[1], k[2]) if k[0] == 'throw'
                           else '%s(%s): the _Mutex member at +4' % (k[1], k[2]) if k[0] == 'mutex'
                           else '%s(%s): MSVC\'s _Facet_Register' % (k[1], k[2]) if k[0] == 'facet'
                           else 'the %s destructor (%s)' % (k[2], k[1]) if k[0] == 'niptr'
                           else 'placement delete: ret')
        out.append(r)
    return out


def emit_crt(rows, itanium):
    """AArch64 stubs (weak) for the crt-rule names: x0 = the message, tail call the libstdc++ thrower; or ret."""
    out = ['// GENERATED by tools/link/bodies.py: runtime members defined by the C++ library contract', '    .text']
    for i, r in enumerate(sorted(rows, key=lambda r: r.name)):
        q = '"%s"' % r.name
        kind, fn, msg = crt_rule(itanium(r.name))
        out += ['    .section .text.fnvnx_bd_crt.%d,"ax",%%progbits' % i, '    .p2align 2', '    .weak ' + q,
                '    .type %s, %%function' % q, q + ':']
        if kind == 'ret':
            out += ['    ret']
        elif kind == 'mutex':
            out += ['    add x0, x0, #4', '    b ' + fn]
        elif kind in ('facet', 'niptr'):
            out += ['    b ' + fn]
        else:
            out += ['    adrp x0, .Lcrtmsg%d' % i, '    add x0, x0, :lo12:.Lcrtmsg%d' % i, '    b ' + fn]
        out += ['    .size %s, .-%s' % (q, q)]
        if kind == 'throw':
            out += ['    .section .rodata.fnvnx_bd_crt,"aMS",%progbits,1', '.Lcrtmsg%d:' % i,
                    '    .asciz "%s"' % msg]
    return '\n'.join(out) + '\n'


# ---------------------------------------------------------------------------------------------------- addr-host

def load_defmissing(refs_tsv):
    """Addresses refs.tsv binds references to (`code-defmissing`: units.json places a function there, but its unit's
    object defines nothing at it) -> {address: set(referencing units)}."""
    out = collections.defaultdict(set)
    if refs_tsv and os.path.exists(refs_tsv):
        for line in open(refs_tsv, encoding='utf-8', errors='surrogateescape'):
            f = line.rstrip('\n').split('\t')
            if len(f) >= 5 and f[2] == 'code-defmissing' and f[4]:
                out[int(f[4], 16)].add(f[0].split('#')[0])
    return dict(out)


# the deallocation function MSVC's ??_G calls, chosen as its lookup does: the class's (or a base's) one-parameter
# operator delete, else its sized usual one with sizeof(C), else the global one. Never a delete-expression: with a
# virtual destructor that dispatches to D0, which natively is this very ??_G stub's slot (or emitted nowhere: a
# class whose destructor has no body, R6's Havok viewers at 0x00ddeff0)
GDEL_HELPER = ('namespace __fnvnx_gd { '
               'template <class T> auto del(T *p, int) -> decltype(T::operator delete((void *)p), void()) '
               '{ T::operator delete((void *)p); } '
               'template <class T> auto del(T *p, long) -> decltype(T::operator delete((void *)p, sizeof(T)), void()) '
               '{ T::operator delete((void *)p, sizeof(T)); } '
               'template <class T> void del(T *p, ...) { ::operator delete((void *)p); } }')


def gdel_line(addr, msig):
    """A scalar deleting destructor `??_GC@@...(unsigned int)` the decomp places but GCC has no counterpart of:
    `__fnv_<addr>(C *, unsigned)` destroys (C's own destructor, non-virtually), then on bit 0 frees with the
    deallocation function MSVC's ??_G calls (GDEL_HELPER, compiled before the lines). None for a class it cannot
    name."""
    if msig is None or msig.kind != 'func' or len(msig.qual) < 2:
        return None
    scope = msig.qual[:-1]
    if any('<' in p or '{' in p or '`' in p or "'" in p or '(' in p for p in scope):
        return None
    c = '::' + '::'.join(scope)
    return ('extern "C" void *%s(%s *p, unsigned int f) { p->%s::~%s(); if (f & 1) __fnvnx_gd::del(p, 0); '
            'return p; }' % (ADDR_SYM % addr, c, c, scope[-1]))


def addr_host(tree, work, host, items, msvc, itanium_of_defs):
    """items: [(address, MSVC symbol units.json places there)] of one placing unit. ??_G: gdel_line; others: the
    unit compiled with -fkeep-inline-functions, the emitted symbol whose relaxed key pairs the MSVC name (exactly
    one) kept, and `__fnv_<addr>: b <symbol>`. -> (object or None, {address: target}, {address: why failed})."""
    import vtables
    lines, plain = [], []
    for a, m in items:
        if m.startswith('??_G'):
            ln = gdel_line(a, msvc(m))
            if ln:
                lines.append((a, ln))
                continue
        plain.append((a, m))
    got, failed = {}, {}
    objs = []
    if lines:
        its = [(len(lines), 'gdhelper', GDEL_HELPER)] + [(k, 'gd', ln) for k, (a, ln) in enumerate(lines)]
        obj, alive = vtables.compile_probe(tree, work, host, its, 'bdg', ('-ffunction-sections', '-fdata-sections'),
                                           prelude='', game=True)
        ok = {lines[k][0] for k, _, _ in alive if k < len(lines)} if obj else set()
        if obj:
            clean = os.path.join(work, host.replace('/', '_') + '.bdgs.o')
            if vtables.strip_init(obj.path, clean):
                r = vtables.gc_weaken(clean, {ADDR_SYM % a for a in ok}, os.path.join(work, host.replace('/', '_') + '.bdg.x'))
                if r:
                    objs.append(r[0])
                    got.update({a: ADDR_SYM % a for a in ok if ADDR_SYM % a in r[1]})
        for a, _ in lines:
            if a not in got:
                failed[a] = 'deleting destructor line refused'
    if plain:
        its = [(0, 'anchor', 'extern "C" void __fnvnx_bd_anchor() {}')]
        obj, _ = vtables.compile_probe(tree, work, host, its, 'bda',
                                       ('-ffunction-sections', '-fdata-sections', '-fkeep-inline-functions'),
                                       prelude='', game=True)
        clean = os.path.join(work, host.replace('/', '_') + '.bdas.o')
        if obj is None or not vtables.strip_init(obj.path, clean):
            failed.update({a: 'host does not compile' for a, _ in plain})
        else:
            o = vtables.Obj(clean)
            defined = sorted(n for n in o.defs if o.bind(n) != 'local' and n.startswith('_Z'))
            byk = collections.defaultdict(set)
            for n, isg in zip(defined, itanium_of_defs(defined)):
                for _, k in def_keys(isg):
                    byk[k].add(n)
            want = {}
            for a, m in plain:
                cands = set()
                for _, k in def_keys(msvc(m)):
                    cands |= byk.get(k, set())
                if len(cands) == 1:
                    want[a] = next(iter(cands))
                else:
                    failed[a] = 'emitted %d symbols for %s' % (len(cands), m)
            r = vtables.gc_weaken(clean, set(want.values()), os.path.join(work, host.replace('/', '_') + '.bda.x')) \
                if want else None
            if r:
                objs.append(r[0])
                for a, n in want.items():
                    if n in r[1]:
                        got[a] = n
                    else:
                        failed[a] = 'not kept'
            else:
                failed.update({a: 'not emitted' for a in want})
    return objs, got, failed


def emit_addr_stubs(targets):
    """`__fnv_<addr>: b <symbol>` (weak) for the addr-host bodies that are not themselves named __fnv_<addr>."""
    out = ['// GENERATED by tools/link/bodies.py: exe addresses bound to the body their placing unit emits', '    .text']
    for i, (a, n) in enumerate(sorted(targets.items())):
        if n == ADDR_SYM % a:
            continue
        q = '"%s"' % (ADDR_SYM % a)
        out += ['    .section .text.fnvnx_bd_addr.%d,"ax",%%progbits' % i, '    .p2align 2', '    .weak ' + q,
                '    .type %s, %%function' % q, q + ':', '    b "%s"' % n, '    .size %s, .-%s' % (q, q)]
    return '\n'.join(out) + '\n'


# ---------------------------------------------------------------------------------------------------- thunks

THUNK = re.compile(r'^_ZT(?:h(n?)(\d+)|v(n?)(\d+)_n(\d+))_(.+)$')


def thunk_parts(name):
    """`_ZTh[n]<off>_<enc>` / `_ZTv[n]<off>_n<vcall>_<enc>` -> (this adjustment, vcall offset or None, target
    `_Z<enc>`), else None. Virtual thunks: `this += off`, then `this += *(*this - vcall)` (ILP32: 4-byte entries)."""
    m = THUNK.match(name)
    if not m:
        return None
    if m.group(2) is not None:
        return (-int(m.group(2)) if m.group(1) else int(m.group(2))), None, '_Z' + m.group(6)
    return (-int(m.group(4)) if m.group(3) else int(m.group(4))), int(m.group(5)), '_Z' + m.group(6)


def load_thunks(refs_tsv):
    """Thunk names refs.tsv leaves undefined (`special-unresolved:*thunk*`) -> {name: set(referencing units)}."""
    out = collections.defaultdict(set)
    if refs_tsv and os.path.exists(refs_tsv):
        for line in open(refs_tsv, encoding='utf-8', errors='surrogateescape'):
            f = line.rstrip('\n').split('\t')
            if len(f) >= 3 and f[2].startswith('special-unresolved') and 'thunk' in f[2] and thunk_parts(f[1]):
                out[f[1]].add(f[0].split('#')[0])
    return dict(out)


def emit_thunks(names):
    """AArch64 (ILP32) thunks, weak: adjust w0, then tail-call the target, which resolve binds like any name."""
    out = ['// GENERATED by tools/link/bodies.py: this-adjusting thunks of functions no unit emits them with',
           '    .text']
    for i, n in enumerate(sorted(names)):
        off, vcall, target = thunk_parts(n)
        q = '"%s"' % n
        out += ['    .section .text.fnvnx_bd_thunk.%d,"ax",%%progbits' % i, '    .p2align 2', '    .weak ' + q,
                '    .type %s, %%function' % q, q + ':']
        if off:
            out += ['    %s w0, w0, #%d' % ('sub' if off < 0 else 'add', abs(off))]
        if vcall is not None:
            out += ['    ldr w16, [x0]', '    sub w16, w16, #%d' % vcall, '    ldr w16, [x16]',
                    '    add w0, w0, w16']
        out += ['    b "%s"' % target, '    .size %s, .-%s' % (q, q)]
    return '\n'.join(out) + '\n'


# ---------------------------------------------------------------------------------------------------- synthesis

def emit_empty(rows):
    """AArch64 `ret` stubs (weak) for the msvc-empty names."""
    out = ['// GENERATED by tools/link/bodies.py: functions whose MSVC body does nothing', '    .text']
    for i, r in enumerate(sorted(rows, key=lambda r: r.name)):
        q = '"%s"' % r.name
        out += ['    .section .text.fnvnx_bd_empty.%d,"ax",%%progbits' % i, '    .p2align 2', '    .weak ' + q,
                '    .type %s, %%function' % q, q + ':', '    ret', '    .size %s, .-%s' % (q, q)]
    return '\n'.join(out) + '\n'


def synth_host(tree, work, host, rows, itanium, inline=False, gate=None, strict=(), minst=()):
    """Compile the host unit again with the `= default` lines (special-default) or with -fkeep-inline-functions
    (msvc-inline); keep only the wanted symbols. -> (object path or None, {names defined}, {name: why failed}).
    gate (inline): a LayoutGate. With it, an emitted member of another copy of the wanted name's class (copy_key:
    the same member, parameters and cv once unit namespaces are removed) is renamed to the wanted name when it is the
    only such symbol and the gate gives both classes (and their class-like parameters) one size. Names in strict
    (tree-inline's own pairings across units: copies, constructors and destructors, names MSVC calls out of line)
    pass the gate for every pairing, also a body the host emits under the wanted name itself when the host is not a
    referencing unit. minst (inline): the names member_inst_hosts chose a host for, instantiated explicitly
    (member_inst_line) there."""
    import vtables
    want = {r.name for r in rows}
    ref_units = {r.name: [u for u in r.units.split(',') if u] for r in rows}
    gnotes = {}

    def gate_ok(w, emitted):
        if gate is None:
            return False
        ok, why = gate.same(ref_units.get(w) or [host], copy_types(itanium(w)), host, copy_types(itanium(emitted)))
        if not ok:
            gnotes[w] = why
        return ok
    items, failed = [], {}
    if not inline:
        for k, r in enumerate(sorted(rows, key=lambda r: r.name)):
            if r.cls == 'exe-sret':
                line = sret_line(itanium(r.name), r.addr, r.note.startswith('sret this'))
            elif r.cls == 'decl-data':
                line = data_line(itanium(r.name).qual)
            else:
                line = default_line(special_of(itanium(r.name)), itanium(r.name))
            if line in {it[2] for it in items}:
                continue
            items.append((k, 'bd', line))
    else:
        items.append((0, 'anchor', 'extern "C" void __fnvnx_bd_anchor() {}'))
        for k, r in enumerate(sorted(rows, key=lambda r: r.name)):   # a class template's destructor: instantiated here
            line = inst_line(itanium(r.name)) or (member_inst_line(itanium(r.name)) if r.name in minst else None)
            if line and line not in {it[2] for it in items}:
                if INST_DECLVAL not in {it[2] for it in items} and 'fnvnx_declval_' in line:
                    items.append((-1 - len(items), 'fwd', INST_DECLVAL))
                for j, fw in enumerate(inst_fwd(itanium(r.name))):   # a pointee the host does not define
                    if fw not in {it[2] for it in items}:
                        items.append((-1 - len(items), 'fwd', fw))
                items.append((k + 1, 'inst', line))
    # emitted bodies are game code: compile.sh's own flags (layout.game_flags: code generation, FTOI2, S12 order)
    flags = ('-ffunction-sections', '-fdata-sections') + (('-fkeep-inline-functions',) if inline else ())
    obj, alive = vtables.compile_probe(tree, work, host, items, 'bdi' if inline else 'bd', flags, prelude='',
                                       game=True)
    if obj is None:
        return None, set(), {n: 'synth-failed (host does not compile)' for n in want}
    base = os.path.join(work, host.replace('/', '_') + ('.bdi' if inline else '.bd'))
    clean = base + 's.o'
    if not vtables.strip_init(obj.path, clean):
        return None, set(), {n: 'synth-failed (objcopy)' for n in want}
    o = vtables.Obj(clean)
    defined = {n for n in o.defs if o.bind(n) != 'local'}
    renames = {}
    if inline:   # a body emitted under a shell's name: the relaxed key pairs it with the wanted name
        byk = collections.defaultdict(set)
        for n in defined:
            if n.startswith('_Z') and n not in want:
                for _, k in def_keys(itanium(n)):
                    byk[k].add(n)
        cbyk = collections.defaultdict(set)   # copy keys of the emitted names (gate given)
        if gate is not None:
            for n in defined:
                if n.startswith('_Z') and n not in want:
                    k = copy_key(itanium(n))
                    if k:
                        cbyk[k].add(n)
        for w in sorted(want & defined & set(strict)):
            if host not in ref_units.get(w, ()) and not gate_ok(w, w):
                want.discard(w)
                failed[w] = 'synth-failed (copy pair gate: %s)' % gnotes.get(w, '?')
        for w in sorted(want - defined):
            done = False
            for _, k in relaxed_keys(itanium(w)):
                if len(byk.get(k, ())) == 1:
                    e = next(iter(byk[k]))
                    if e not in renames and (w not in strict or gate_ok(w, e)):
                        renames[e] = w
                        done = True
                    break
            if not done and gate is not None:
                k = copy_key(itanium(w))
                e = next(iter(cbyk[k])) if k and len(cbyk.get(k, ())) == 1 else None
                if e and e not in renames and gate_ok(w, e):
                    renames[e] = w
        if renames:
            ren = base + 'r.o'
            args = ['--redefine-sym=%s=%s' % kv for kv in sorted(renames.items())]
            if subprocess.run([vtables.OBJCOPY, *args, clean, ren], capture_output=True).returncode == 0:
                clean = ren
                defined = (defined - set(renames)) | set(renames.values())
    got = vtables.gc_weaken(clean, want & defined, base + '.x') if want & defined else None
    if not got:
        failed.update({n: 'synth-failed (not emitted%s)' % ('; copy pair gate: ' + gnotes[n] if n in gnotes else '')
                       for n in want})
        return None, set(), failed
    path, roots = got
    for n in want - roots:
        failed[n] = 'synth-failed (%s)' % ('not emitted by the host' if inline else 'GCC refused the = default') + \
            ('; copy pair gate: ' + gnotes[n] if n in gnotes else '')
    return path, roots, failed


# ---------------------------------------------------------------------------------------------------- tree-inline

def load_layouts(out):
    """out/layouts.tsv -> {(unit, last type component): {sizes}} (the layout probe's sizes per unit)."""
    res = collections.defaultdict(set)
    path = os.path.join(out, 'layouts.tsv')
    if os.path.exists(path):
        for line in open(path, encoding='utf-8', errors='surrogateescape'):
            f = line.rstrip('\n').split('\t')
            if not line.startswith('#') and len(f) >= 3 and f[2].isdigit():
                res[(f[0], sigs.split_qual(f[1])[-1] if f[1] else f[1])].add(int(f[2]))
    return res


def load_type_sizes(out):
    """out/layouts.tsv -> {(unit, canonical full type spelling): {sizes}} (targ_layout_ok)."""
    res = collections.defaultdict(set)
    path = os.path.join(out, 'layouts.tsv')
    if os.path.exists(path):
        for line in open(path, encoding='utf-8', errors='surrogateescape'):
            f = line.rstrip('\n').split('\t')
            if not line.startswith('#') and len(f) >= 3 and f[2].isdigit():
                res[(f[0], sigs.canon_type(f[1]))].add(int(f[2]))
    return res


def inst_line(isig):
    """`template C<args>::~C();` - the explicit instantiation of the destructor of a class template instantiation
    over global types (`NiPointer<NiLight>`), None for any other name. The body is the template's own, from the
    host's headers: what a unit sees where the pointee is complete."""
    if isig is None or isig.kind != 'func' or isig.params or isig.variant not in ('D1', 'D2') or len(isig.qual) < 2:
        return None
    if isig.qual[-1] != '{dtor}' or '<' not in isig.qual[-2] or not isig.qual[-2].endswith('>'):
        return None
    if any(UNIT_NS.match(q) or UNIT_NS_ANY.search(q) for q in isig.qual):
        return None   # a unit-local copy of a class: its own unit hosts it (exe-addr / copy pairs)
    if any(c in q for q in isig.qual[:-1] for c in '`\'({'):
        return None
    base = isig.qual[-2][:isig.qual[-2].index('<')]
    return 'template %s::~%s();' % ('::'.join(isig.qual[:-1]), base)


INST_DECLVAL = 'template <class T> T&& fnvnx_declval_();'   # std::declval without the header (units define their own std)


def member_inst_line(isig):
    """`template decltype(...) C<args>::m(P) cv;` - the explicit instantiation of a member function (not a constructor
    or destructor) of a class template instantiation, None for any other name. The return type is not in the Itanium
    name; `decltype` of the call with the name's own parameter types gives it (the overload the call picks must be
    the member itself, else the line does not compile and the host fails). The body is the template's own, from the
    host's headers: what a unit sees where the unit declares no explicit specialization of the member."""
    if isig is None or isig.kind != 'func' or isig.params is None or isig.variant or len(isig.qual) < 2:
        return None
    cls, name = isig.qual[-2], isig.qual[-1]
    if '<' not in cls or not cls.endswith('>') or not re.fullmatch(r'[A-Za-z_]\w*', name):
        return None
    if any(UNIT_NS.match(q) or UNIT_NS_ANY.search(q) for q in isig.qual):
        return None
    if any(c in q for q in isig.qual[:-1] for c in '`\'({') or any('...' in p or '(' in p for p in isig.params):
        return None
    scope = '::'.join(isig.qual[:-1])
    cv = (isig.cv + ' ') if isig.cv else ''
    args = ', '.join(isig.params)
    call = ', '.join('fnvnx_declval_<%s>()' % p for p in isig.params)
    return 'template decltype(fnvnx_declval_<%s%s&>().%s(%s)) %s::%s(%s)%s;' % (
        cv, scope, name, call, scope, name, args, (' ' + isig.cv) if isig.cv else '')


SPEC_DECL = re.compile(r'template\s*<\s*>[^;{}]*?::\s*(\w+)\s*\(([^;{}]*)\)\s*(?:const\s*)?;')


def top_commas(args):
    """The number of parameters of a declaration's parameter text (top-level commas; `()` and `(void)` are none)."""
    s = args.strip()
    if not s or s == 'void':
        return 0
    depth = n = 0
    for ch in s:
        depth += ch in '<(['
        depth -= ch in '>)]'
        n += ch == ',' and depth == 0
    return n + 1


def member_inst_hosts(tree, rows, itanium, unit_sources, ref_units, shim=None, limit=5):
    """{name: [host units]} for member_inst_line names. Evidence: a referencing unit declares the member as an explicit
    specialization (`template <> R C<A>::m(P);`, MSVC's out-of-line instance of a library member, never defined in
    the unit): GCC then instantiates it nowhere there, and every other unit that uses the member inlines it or keeps
    only a local GCC clone (.isra), so no object defines the plain name. A body of the member in the headers (the
    tree's, or `shim` = the port's shim directory: fnvnx_vc9.h) that a host reaches by its includes, a host that is no
    referencing unit and declares no such specialization itself, the smallest include closure first."""
    import bodycause
    want = {}
    for r in rows:
        isg = itanium(r.name)
        if member_inst_line(isg) is None:
            continue
        base = isg.qual[-2][:isg.qual[-2].index('<')]
        base = base.split('::')[-1]
        spec = False
        for u in ref_units.get(r.name, ()):
            try:
                with open(os.path.join(tree, u), encoding='utf-8', errors='replace') as fh:
                    raw = fh.read()
            except OSError:
                continue
            spec = spec or any(m.group(1) == isg.qual[-1] and top_commas(m.group(2)) == len(isg.params)
                               for m in SPEC_DECL.finditer(raw))
        if spec:
            want[r.name] = (base, isg.qual[-1])
    if not want:
        return {}
    pairs = set(want.values())
    found = bodycause.tree_bodies(tree, pairs)
    shim_found = bodycause.tree_bodies(shim, pairs) if shim else {}
    bodyfiles = {}
    for n, k in want.items():
        fl = {f for f, w in found.get(k, ()) if w.startswith('live')}
        fl |= {os.path.join(shim, f) for f, w in shim_found.get(k, ()) if w.startswith('live')}
        if fl:
            bodyfiles[n] = fl
    if not bodyfiles:
        return {}
    clo = include_closure(tree, sorted(unit_sources), extra=[os.path.join(shim, 'include')] if shim else ())
    texts = {}

    def declares(u, k):
        if u not in texts:
            try:
                with open(os.path.join(tree, u), encoding='utf-8', errors='replace') as fh:
                    texts[u] = fh.read()
            except OSError:
                texts[u] = ''
        return any(m.group(1) == k[1] for m in SPEC_DECL.finditer(texts[u]))
    out = {}
    for n, fl in bodyfiles.items():
        rus = set(ref_units.get(n, ()))
        cand = [u for u in sorted(unit_sources) if u not in rus and fl & clo.get(u, {u}) and not declares(u, want[n])]
        if cand:
            out[n] = sorted(cand, key=lambda u: (len(clo[u]), u))[:limit]
    return out

def msvc_inline_retry(rows, failed):
    """msvc-inline names whose host compiled but emitted nothing under the wanted name: tree-inline tries them next,
    across the copies of the class (copy_key, through the layout gate). W44: the pristine u99 object
    (tileimage_decl_variant.cpp) defines MSVC's `basic_string<char>::erase(unsigned, unsigned)`, and that unit emits
    it only for its own copy `fnv_00b0e730::std::basic_string`; blk_00b00000_od_ob1_variant.cpp references the
    global `std::basic_string` copy's and `fnv_00b0f410::std::basic_string`'s, declared there and defined nowhere."""
    return {r.name for r in rows if r.cls == 'msvc-inline' and
            failed.get(r.name, '').startswith('synth-failed (not emitted')}


def inst_pointees(isig):
    """Class names the template arguments of an inst_line name use only as pointees (`NiTArray<ImageSpaceEffect*,
    NiTMallocInterface<ImageSpaceEffect*>>`): never as a value, a template argument of its own or a scope. The
    destructor body does not look inside them, so the host needs a declaration (`struct X;`), not a definition."""
    if inst_line(isig) is None:
        return []
    cls = isig.qual[-2]
    args = cls[cls.index('<'):]
    out = []
    for w in dict.fromkeys(ARG_WORDS.findall(args)):
        if w in BUILTIN_WORDS or w == cls[:cls.index('<')]:
            continue
        occ = list(re.finditer(r'(?<![\w:])%s(?![\w:])' % re.escape(w), args))
        if occ and len(occ) == len(re.findall(r'\b%s\b' % re.escape(w), args)) and \
                all(re.match(r'\s*\*', args[m.end():]) and not args[m.end():].lstrip().startswith('::') for m in occ):
            out.append(w)
    return out


ARG_WORDS = re.compile(r'[A-Za-z_]\w*')
BUILTIN_WORDS = frozenset('const volatile unsigned signed int long short char bool float double void wchar_t '
                          'struct class enum'.split())


INCLUDE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*"([^"]+)"', re.M)


def include_closure(tree, files, extra=()):
    """{file: set of the files it includes, itself included} for tree-relative `files`: quoted includes resolved
    against the file's directory, include/, src/ and the tree root, else by basename; read once per file. extra:
    further directories (absolute) the quoted includes are looked up in last (the shim headers), their files named
    by absolute path."""
    by_base = collections.defaultdict(list)
    for base in ('src', 'include'):
        for dp, _, fs in os.walk(os.path.join(tree, base)):
            for f in fs:
                if f.endswith(('.h', '.inl', '.hpp')):
                    by_base[f].append(os.path.relpath(os.path.join(dp, f), tree))
    direct = {}

    def inc(rel):
        if rel not in direct:
            try:
                raw = open(os.path.join(tree, rel), encoding='utf-8', errors='replace').read()
            except OSError:
                raw = ''
            res = set()
            for m in INCLUDE.findall(raw):
                for cand in (os.path.normpath(os.path.join(os.path.dirname(rel), m)), os.path.join('include', m),
                             os.path.join('src', m), m):
                    if os.path.exists(os.path.join(tree, cand)):
                        res.add(cand)
                        break
                else:
                    hit = by_base.get(os.path.basename(m), ())[:1]
                    if not hit:
                        hit = [os.path.join(x, m) for x in extra if os.path.exists(os.path.join(x, m))][:1]
                    res.update(hit)
            direct[rel] = res
        return direct[rel]
    out = {}
    for f in files:
        seen, todo = {f}, [f]
        while todo:
            for g in inc(todo.pop()):
                if g not in seen:
                    seen.add(g)
                    todo.append(g)
        out[f] = seen
    return out


def inst_hosts(tree, rows, itanium, unit_sources, found, ref_units, limit=5):
    """{name: [host units]} for destructors of template instantiations (inst_line) whose template has a body of its
    own in the tree (`found`: bodycause.tree_bodies for (class, ~class), live only; specializations of other
    arguments count too, the host's compile decides). A host is a unit whose includes reach that body and a
    definition of every class the arguments name (the pointee is complete there); the referencing units are left
    out (they declare the destructor without a body: the pointee is incomplete there)."""
    import bodycause
    want, bodyfiles, ptrs = {}, {}, {}
    for r in rows:
        isg = itanium(r.name)
        c = bodycause.key_of(isg)
        if not inst_line(isg) or not c:
            continue
        fl = {f for f, w in found.get((c[0], '~' + c[0]), ()) if w.startswith('live')}
        if not fl:
            continue
        cls = isg.qual[-2]
        words = [w for w in dict.fromkeys(ARG_WORDS.findall(cls[cls.index('<'):])) if w not in BUILTIN_WORDS and
                 w != c[0]]
        want[r.name], bodyfiles[r.name] = words, fl
        ptrs[r.name] = set(inst_pointees(isg))
    if not want:
        return {}
    alltok = sorted({w for ws in want.values() for w in ws}, key=len, reverse=True)
    rdef = re.compile(r'\b(?:class|struct)\s+(%s)\b[^;{(]*\{' % '|'.join(map(re.escape, alltok))) if alltok else None
    files = []
    for base in ('src', 'include'):
        for dp, _, fs in os.walk(os.path.join(tree, base)):
            files += [os.path.relpath(os.path.join(dp, f), tree) for f in fs if f.endswith(('.h', '.inl', '.hpp', '.cpp'))]
    defs = collections.defaultdict(set)     # word -> files defining a class of that name
    for f in files:
        try:
            raw = open(os.path.join(tree, f), encoding='utf-8', errors='replace').read()
        except OSError:
            continue
        if rdef:
            for w in set(rdef.findall(raw)):
                defs[w].add(f)
    clo = include_closure(tree, sorted(unit_sources))
    out = {}
    for n, ws in want.items():
        rus = set(ref_units.get(n, ()))
        cand = []
        for u in sorted(unit_sources):
            if u in rus:
                continue
            reach = clo.get(u, {u})
            if bodyfiles[n] & reach and all(not defs.get(w) or defs[w] & reach for w in ws if w not in ptrs[n]) and \
                    any(defs.get(w) for w in ws):   # a pointee needs a declaration only (inst_fwd)
                cand.append(u)
        if cand:
            out[n] = sorted(cand, key=lambda u: (len(clo[u]), u))[:limit]
    return out


def inst_fwd(isig):
    """`struct X;` for each class inst_pointees names: what the host needs where it does not define X."""
    return ['struct %s;' % w for w in inst_pointees(isig)]


def tree_inline_hosts(tree, rows, itanium, unit_sources, layouts=None):
    """{name: host unit} for missing names whose body the tree has inline (out of line `inline C::f(`, or in a
    class body of C) in a unit's own source (.cpp in units.json). A referencing unit is preferred, then the first
    in order. Only the host's emitted symbol under the wanted name (or the one its relaxed key pairs) is kept."""
    import bodycause
    keys = {}
    for r in rows:
        k = bodycause.key_of(itanium(r.name))
        if k and k[0]:
            keys[r.name] = (k[0], k[1])
    if not keys:
        return {}
    search = {}   # tree_bodies spells a constructor / destructor by the class's own name
    for n, (c, m) in keys.items():
        search[n] = (c, c if m == '{ctor}' else '~' + c if m == '{dtor}' else m)
    found = bodycause.tree_bodies(tree, set(search.values()))
    out = {}
    for r in rows:
        k = keys.get(r.name)
        if not k:
            continue
        hosts = sorted({f for f, where in found.get(search[r.name], ()) if where.startswith('live') and
                        f in unit_sources})
        if layouts:   # the class must have the referencing units' size in the host where the probe knows both
            ref = set().union(*(layouts.get((u, k[0]), set()) for u in r.units.split(',')))
            hosts = [h for h in hosts if not (ref and layouts.get((h, k[0])) and layouts[(h, k[0])] != ref)]
        if hosts:
            refs = [h for h in hosts if h in r.units.split(',')]
            out[r.name] = refs + [h for h in hosts if h not in refs]
    return out


# ---------------------------------------------------------------------------------------------------- copy pairs

# The decomp's own copy namespaces of `std`: a file that declares its traits class in a namespace of its own
# (`ac8parse::char_traits<T>`: `_Copy_s` and `assign` declared, no body) and defines the same class in `std` (the MSVC
# STL copy of the same file, `std::char_traits<E>` with both bodies). The namespace is the decomp's spelling of `std`,
# so the two are copies of one class (copy_key); the layout gate then confirms the pairing. A name is listed only
# for the shape found in the decomp tree (declared-only members in one namespace, the same members with bodies in
# `std` of the same file); the other namespaces that listing finds are no rows (s1c10's exception destructors are
# special-default, ImageReaderTypes::basic_string::_Tidy is no missing name), so they stay out.
COPY_NS_STD = frozenset({'ac8parse'})
COPY_NS_ANY = re.compile(r'(?<![\w:])(?:%s)::' % '|'.join(sorted(COPY_NS_STD)))


def copy_ns(text):
    """`ac8parse::char_traits<char>` -> `std::char_traits<char>` (COPY_NS_STD), in a qualified name or a type."""
    return 'std' if text in COPY_NS_STD else COPY_NS_ANY.sub('std::', text)


def copy_key(sig, variant=True):
    """The key the copies of one class share: the qualified name and the parameter types with every unit namespace
    (`fnv_<a>::`) removed and a COPY_NS_STD namespace spelled `std`, parameters at sigs level 0 (exact spellings),
    the member's cv and (variant) the Itanium ctor/dtor variant kept. The decomp's unit-local copies of a class
    (`fnv_00ad47f0::std::basic_ostream`, `fnv_00ac9790::std::basic_ostream`) and the global class get one key;
    another class, overload or cv does not."""
    if sig is None or sig.kind != 'func' or sig.params is None or len(sig.qual) < 2:
        return None
    qual = tuple(copy_ns(UNIT_NS_ANY.sub('', sigs.canon_type(q))) for q in sig.qual if not UNIT_NS.match(q))
    if len(qual) < 2:
        return None
    params = tuple(copy_ns(UNIT_NS_ANY.sub('', sigs.canon_param(p, 0))) for p in sig.params)
    return (qual, params, sig.cv or '', (sig.variant or '') if variant else '')


def copy_types(sig):
    """The spellings the copy-pair layout gate compares, in order: the member's class, then each class-like
    parameter type (cv, `*` and `&` stripped; scalars and function types left out)."""
    qual = [sigs.canon_type(q) for q in sig.qual[:-1]]
    out = ['::'.join(qual)]
    for p in sig.params or ():
        t = sigs.canon_param(p, 0)
        while True:
            t2 = re.sub(r'^(?:const|volatile)\s+|\s*(?:const|volatile)$|\s*[\*&]$', '', t).strip()
            if t2 == t:
                break
            t = t2
        if t and t != '...' and '(' not in t and not SCALARS.match(t) and not re.match(r'^-?\d+$', t):
            out.append(t)
    return out


class LayoutGate:
    """Sizes of types in units for the copy pairs (copy-addr, tree-inline across copies): out/layouts.tsv's rows,
    else the layout probe (layout.probe_unit in work, cached per (unit, type); thread-safe, one probe per unit at a
    time). same(): every type of the pair is sized in each referencing unit (at most max_units of them) and in the
    defining unit, with one size on both sides. A type the probe cannot name is no evidence: the pair fails."""

    def __init__(self, tree, work, known=None, max_units=8):
        import threading
        self.tree, self.work, self.known, self.max_units = tree, work, known or {}, max_units
        self.cache, self.lock = {}, threading.Lock()
        self.ulocks = collections.defaultdict(threading.Lock)

    def sizes(self, unit, types):
        """{type: size or None}."""
        res, need = {}, []
        for t in types:
            k = (unit, sigs.canon_type(t))
            kn = {s for s in self.known.get(k, ()) if s >= 0}
            if len(kn) == 1:
                res[t] = next(iter(kn))
            elif k in self.cache:
                res[t] = self.cache[k]
            elif t not in need:
                need.append(t)
        if need:
            import layout
            with self.lock:
                ul = self.ulocks[unit]
            with ul:
                got = layout.probe_unit(self.tree, self.work, unit, need)
            for t in need:
                lay = got.get(t)
                self.cache[(unit, sigs.canon_type(t))] = res[t] = lay.size if lay and lay.size >= 0 else None
        return res

    def same(self, ref_units, ref_types, def_unit, def_types):
        """-> (ok, note)."""
        if len(ref_types) != len(def_types) or not ref_units or not def_unit:
            return False, 'no layout pair'
        d = self.sizes(def_unit, def_types)
        refs = sorted(ref_units)[:self.max_units]
        for u in refs:
            s = self.sizes(u, ref_types)
            for rt, dt in zip(ref_types, def_types):
                if s.get(rt) is None or d.get(dt) is None:
                    return False, 'layout probe does not size %s' % (rt if s.get(rt) is None else dt)
                if s[rt] != d[dt]:
                    return False, 'sizes differ: %s %d in %s, %s %d in %s' % (rt, s[rt], u, dt, d[dt], def_unit)
        return True, 'sizes %s' % ','.join(str(d[t]) for t in def_types)


def copy_addr_pass(rows, cl, itanium, gate):
    """`copy-addr`: a missing member of a class copy (or of the global class) whose same member of another copy
    units.json places at one address the native build defines (Classifier.by_copy), with one layout on both sides
    (LayoutGate: the class and its class-like parameters, in the referencing units and in a unit naming the other
    copy's MSVC name). Bound to `__fnv_<addr>`."""
    out = []
    for r in rows:
        isig = itanium(r.name)
        hit = cl.by_copy(isig) if r.cls == 'missing' and r.units and isig.kind == 'func' else None
        if hit:
            a, mn, dunits = hit
            ok, why = gate.same(r.units.split(','), copy_types(isig), sorted(dunits)[0], copy_types(cl.msvc(mn)))
            if ok:
                r = Row(r.name, 'copy-addr', a, ADDR_SYM % a, r.units, 'via %s in %s (%s)' %
                        (mn, sorted(dunits)[0], why), None)
            else:
                r = r._replace(note=r.note + '; copy-addr %08x: %s' % (a, why))
        out.append(r)
    return out


def retry_special(rows, failed, itanium):
    """{host unit: rows}: `msvc-inline` names whose synthesis failed and that are a special member the implicit
    definition covers (special_of / default_line, a class that is not a template): tried again as `special-default`
    in the first referencing unit."""
    out = collections.defaultdict(list)
    for r in rows:
        if r.cls == 'msvc-inline' and r.name in failed and r.units:
            isig = itanium(r.name)
            sk = special_of(isig) if isig.kind == 'func' else None
            if sk and default_line(sk, isig):
                out[sorted(r.units.split(','))[0]].append(r)
    return out


def fold_fallback(rows, failed, cl, itanium):
    """A `msvc-inline` / `msvc-empty` name whose synthesis failed (`failed`: the host does not emit it) but which is a
    pointer-argument template member whose other instantiations units.json places at one native address
    (Classifier.by_fold): the folded exe body, `template-fold`. `BSSimpleList<fnv_<a>::Rec*>::~BSSimpleList` is
    classified msvc-inline first (the pristine object has the instantiation), and its host unit only uses the list,
    so the body is not emitted there. Removes the name from `failed`."""
    out = []
    for r in rows:
        if r.name in failed and r.cls in ('msvc-inline', 'msvc-empty'):
            isig = itanium(r.name)
            hit = cl.by_fold(isig) if isig.kind == 'func' else None
            if hit:
                a, ns = hit
                failed.pop(r.name)
                r = Row(r.name, 'template-fold', a, ADDR_SYM % a, r.units, 'folded with %d instantiations, e.g. %s;'
                        ' after %s: %s' % (len(ns), ns[0], r.cls, r.note), None)
        out.append(r)
    return out


# ---------------------------------------------------------------------------------------------------- vcalls

VCALL_SYM = '__fnvnx_bdvc_%08x'
VCALL_DIRECT = 'MSVC calls it directly (pristine reference)'   # not a vcall: a real non-virtual call
VCALL_HEAD = ('namespace __fnvnx_bd { template <class C, class... A> struct sel { '
              'template <class R> static constexpr auto m(R (C::*p)(A...)) { return p; } '
              'template <class R> static constexpr auto c(R (C::*p)(A...) const) { return p; } }; }')


def vcall_line(k, isig):
    """Probe line: GCC's pointer-to-member constant of the member function (ARM-style Itanium, as on AArch64: ptr =
    vtable byte offset and adj = 2 * this adjustment + 1 for a virtual function; ptr = the function otherwise)."""
    sp = class_spelling(isig)
    last = isig.qual[-1] if isig.qual else ''
    if sp is None or isig.variant or last.startswith('{') or last in ('operator new', 'operator delete',
                                                                      'operator new[]', 'operator delete[]'):
        return None
    if 'volatile' in (isig.cv or '') or any('...' in p for p in isig.raw_params or ()):
        return None
    expr = '__fnvnx_bd::sel<%s>::%s(&%s::%s)' % (', '.join([sp] + list(isig.raw_params or ())),
                                                  'c' if 'const' in (isig.cv or '') else 'm', sp, last)
    return 'extern const decltype(%s) __fnvnx_bdpm_%d __asm__("__fnvnx_bdpm_%d") = %s;' % (expr, k, k, expr)


def probe_vcalls(tree, work, host, names, itanium):
    """-> {name: (vtable byte offset, this adjustment)} of the names that are virtual member functions, seen from
    host; {name: why} for the others."""
    import vtables
    items, num = [], {}
    for k, n in enumerate(sorted(names)):
        line = vcall_line(k, itanium(n))
        if line:
            items.append((k, 'pm', line))
            num[k] = n
    if not items:
        return {}, {n: 'not a nameable member function' for n in names}
    head = os.path.join(work, 'fnvnx_bd_sel.h')   # by -include: GCC's notes naming it drop no probe line
    if not os.path.exists(head):   # probe threads race here: each writes its own temp file (one shared .tmp name
        import threading           # made the loser's os.replace raise FileNotFoundError: 4 vcall rows of link
        tmp = '%s.%d.%d.tmp' % (head, os.getpid(), threading.get_ident())   # 97deacd38aec, R2 trap loop)
        with open(tmp, 'w') as f:
            f.write('#pragma once\n' + VCALL_HEAD + '\n')
        os.replace(tmp, head)
    obj, alive = vtables.compile_probe(tree, work, host, items, 'bdvc', ('-include', head), prelude='')
    got, why = {}, {}
    alive_k = {k for k, _, _ in alive}
    for k, n in num.items():
        sym = '__fnvnx_bdpm_%d' % k
        if obj is None or k not in alive_k or sym not in obj.defs:
            why[n] = 'not a member function GCC can name here'
            continue
        if obj.is_bss(sym):
            why[n] = 'pointer to member not constant'
            continue
        e = obj.ents(sym)
        if len(e) < 2 or e[0].sym is not None or not e[1].word & 1:
            why[n] = 'not virtual'
            continue
        adj = e[1].word >> 1
        got[n] = (e[0].word, adj - (1 << 31) if adj & (1 << 30) else adj)
    for n in names:
        if n not in got and n not in why:
            why[n] = 'not a nameable member function'
    return got, why


def emit_vcalls(stubs):
    """stubs: {stub symbol: (name, vtable byte offset, this adjustment)} -> AArch64 ILP32 asm: this adjusted, the
    function loaded from the object's vtable, tail call (what MSVC's code did: a call through the vptr)."""
    out = ['// GENERATED by tools/link/bodies.py: direct calls of virtual functions defined nowhere, made virtual',
           '    .text']
    for i, (sym, (name, off, adj)) in enumerate(sorted(stubs.items())):
        out += ['    .section .text.fnvnx_bdvc.%d,"ax",%%progbits' % i, '    .p2align 2', '    .weak ' + sym,
                '    .type %s, %%function' % sym, '// %s' % name, sym + ':', '    mov w0, w0']
        if adj:
            out += ['    mov w17, #%d' % (adj & 0xffff)] + (['    movk w17, #%d, lsl #16' % ((adj >> 16) & 0xffff)]
                                                            if adj & ~0xffff else []) + ['    add w0, w0, w17']
        out.append('    ldr w16, [x0]')
        if off % 4 == 0 and 0 <= off < 16384:
            out.append('    ldr w16, [x16, #%d]' % off)
        else:
            out += ['    mov w17, #%d' % (off & 0xffff), '    movk w17, #%d, lsl #16' % ((off >> 16) & 0xffff),
                    '    ldr w16, [x16, x17]']
        out += ['    br x16', '    .size %s, .-%s' % (sym, sym)]
    return '\n'.join(out) + '\n'


def pristine_externs(objdir, i, code_only=False):
    """MSVC names the pristine object of unit i references (undefined externals); code_only: only those a code
    section's relocation names (a call or an address taken in code), not a vftable slot or another data word."""
    import vslots
    p = os.path.join(objdir, 'u%d.obj' % i)
    if not os.path.exists(p):
        return set()
    c = vslots.coff(p)
    ext = {s.name for s in c.syms.values() if s.secnum == 0 and s.storage == 2 and s.name.startswith('?')}
    if not code_only:
        return ext
    code = set()
    for k in range(1, len(c.secs)):
        if c.secs[k].flags & IMAGE_SCN_CNT_CODE:
            code |= {sn for sn, _ in c.relocs(k).values() if sn in ext}
    return code


# ---------------------------------------------------------------------------------------------------- exe calls

class Pe:
    """FalloutNV.exe: bytes at a virtual address (section table only)."""

    def __init__(self, path):
        import struct
        d = self.data = open(path, 'rb').read()
        pe, = struct.unpack_from('<I', d, 0x3c)
        nsec, = struct.unpack_from('<H', d, pe + 6)
        opt, = struct.unpack_from('<H', d, pe + 20)
        self.base, = struct.unpack_from('<I', d, pe + 24 + 28)
        self.secs, self.code = [], []
        for k in range(nsec):
            o = pe + 24 + opt + 40 * k
            vsize, va, rsize, roff = struct.unpack_from('<IIII', d, o + 8)
            flags, = struct.unpack_from('<I', d, o + 36)
            self.secs.append((self.base + va, max(vsize, rsize), roff, rsize))
            if flags & IMAGE_SCN_CNT_CODE:
                self.code.append((self.base + va, roff, rsize))

    def find_all(self, body, masked, limit=None):
        """Every address in a code section whose bytes equal body outside the offsets in masked (at most limit + 1 of
        them are looked for; None if the body has no unmasked run of 4 bytes to search by). Candidates come from a
        literal search of body's longest unmasked run, then each is compared in full."""
        runs, run = [], None   # the unmasked runs [a, b)
        for j in range(len(body) + 1):
            if j < len(body) and j not in masked:
                run = j if run is None else run
            elif run is not None:
                runs.append((run, j))
                run = None
        if not runs:
            return None
        lo, hi = max(runs, key=lambda r: r[1] - r[0])
        if hi - lo < 4:
            return None
        lit = body[lo:hi]
        hits = []
        for va, roff, rsize in self.code:
            end = roff + rsize
            at = self.data.find(lit, roff + lo, end)
            while at >= 0:
                start = at - lo
                if start + len(body) <= end and all(self.data[start + a:start + b] == body[a:b] for a, b in runs):
                    hits.append(va + start - roff)
                    if limit is not None and len(hits) > limit:
                        return hits
                at = self.data.find(lit, at + 1, end)
        return hits

    def find_unique(self, body, masked):
        """The one address in a code section whose bytes equal body outside the offsets in masked, else None."""
        hits = Pe.find_all(self, body, masked, 1)
        return hits[0] if hits and len(hits) == 1 else None

    def matches(self, va, body, masked):
        """True when the exe's bytes at va equal body outside the offsets in masked."""
        img = self.read(va, len(body))
        return img is not None and all(img[j] == body[j] for j in range(len(body)) if j not in masked)

    def vftable_words(self, unit, nmax=64):
        """The code addresses in the image vftables a unit's recipe names (`??_7C@@6B@` in units.json symbols): each
        vftable's words in order, up to the first word outside the code sections."""
        import struct
        out = []
        for n, a in sorted((unit.get('symbols') or {}).items()):
            if not n.startswith('??_7'):
                continue
            va = int(a, 16)
            for k in range(nmax):
                w = self.read(va + 4 * k, 4)
                w = struct.unpack('<I', w)[0] if w else None
                if w is None or not any(cv <= w < cv + rs for cv, _, rs in self.code):
                    break
                out.append(w)
        return sorted(set(out))

    def read(self, va, n):
        for start, size, roff, rsize in self.secs:
            if start <= va and va + n <= start + min(size, rsize):
                return self.data[roff + va - start:roff + va - start + n]
        return None


FUNCLET = ('__catch$', '__unwindfunclet$', '__ehhandler$')   # MSVC labels inside a function's own section
SEARCH_COPIES = 64  # most identical copies of a body in the exe whose call sites are compared (copy_sites)
SEARCH_MIN = 16   # bytes: shorter bodies (a jmp thunk, `mov eax, [ecx]; ret`) occur too often to place by content


def same_shape(body, img):
    """True when two x86 bodies are the same instruction stream up to constants: as many instructions, each with
    the same mnemonic, size and operands once every number is blanked (stack offsets and immediates may differ, the
    registers and addressing forms may not). A body the decoder cannot read through to its end is not."""
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)

    def shape(code):
        out, n = [], 0
        for ins in md.disasm(bytes(code), 0):
            out.append((ins.mnemonic, ins.size, re.sub(r'\b(?:0x[0-9a-f]+|\d+)\b', '#', ins.op_str)))
            n += ins.size
        return out if n == len(code) else None
    a, b = shape(body), shape(img)
    return a is not None and a == b


def copy_sites(exe, body, rel, copies, wanted):
    """A body the exe holds several times over (identical code, relocations masked: COMDAT folding left no unique
    copy to place): the targets of its wanted relocation sites, for the sites where every copy reaches the same
    address. A site whose copies disagree says nothing about which copy the pristine function is, and is left out.
    -> [(msvc name, target)] one entry per agreeing site."""
    import struct
    out = []
    for k, (sn, typ) in sorted(rel.items()):
        if sn not in wanted or typ not in (0x06, 0x14) or k > len(body) - 4:
            continue
        addend, = struct.unpack_from('<i' if typ == 0x14 else '<I', body, k)
        tg = set()
        for h in copies:
            img = exe.read(h, len(body))
            if img is None:
                tg = None
                break
            if typ == 0x14:
                disp, = struct.unpack_from('<i', img, k)
                tg.add((h + k + 4 + disp - addend) & 0xffffffff)
            else:
                word, = struct.unpack_from('<I', img, k)
                tg.add((word - addend) & 0xffffffff)
        if tg and len(tg) == 1:
            out.append((sn, next(iter(tg))))
    return out


def exe_call_targets(objdir, i, unit, wanted, exe, search=False, loose=False):
    """Original addresses the pristine object of unit i calls the MSVC names in wanted at. Functions are placed
    by units.json, then by following REL32 calls from placed functions (a COMDAT units.json does not list, such as
    `NiPointer<T>::operator=`, at the address the exe's call reaches); a function counts as placed only where its
    x86 bytes equal the exe's outside relocations. At each REL32 relocation naming a wanted name in a placed
    function, the exe's displacement gives the target; at a DIR32 one (the address taken, `push offset f`), the
    exe's word does. search: a function that references a wanted name and was not placed (inlined into its callers
    in the exe, or reached at an address holding other code) is placed where its bytes, relocations masked, occur
    exactly once in the exe's code (at least SEARCH_MIN bytes: a standalone copy units.json does not list); a body
    that occurs up to SEARCH_COPIES times gives the sites on which all its copies reach one address (copy_sites).
    loose (exe-data only): a function whose bytes differ from the exe's counts as placed when it is the same
    instruction stream up to constants (same_shape: a variant unit's other stack layout); a word at a relocation
    sits in the same instruction there. A function's catch funclets (`__catch$f$0`, labels in its section) are then
    part of its body.
    -> {msvc name: {target address: number of sites}}."""
    import struct
    import vslots
    p = os.path.join(objdir, 'u%d.obj' % i)
    if not os.path.exists(p) or not wanted:
        return {}
    c = vslots.coff(p)
    funcs = {}   # defined code symbol -> (section, start, body)
    for name, si in c.defs.items():
        sy = c.syms[si]
        if sy.secnum > 0 and c.secs[sy.secnum].flags & IMAGE_SCN_CNT_CODE and sy.storage in (2, 3):
            ends = [v for v, n_ in c.symbols_in(sy.secnum) if v > sy.value and not (loose and n_.startswith(FUNCLET))]
            data = c.section_bytes(sy.secnum)
            funcs[name] = (sy.secnum, sy.value, data[sy.value:min(ends) if ends else len(data)])
    todo = [(f['symbol'], int(f['address'], 16)) for f in unit['functions'] if f['symbol'] in funcs]
    placed = {}
    out = collections.defaultdict(collections.Counter)
    searched = set()
    slot_words = None
    while True:
        if not todo and search:   # standalone copies of the unplaced functions that reference a wanted name
            for fname, (sec, start, body) in sorted(funcs.items()):
                if fname in placed or fname in searched or len(body) < SEARCH_MIN:
                    continue
                rel = {off - start: v for off, v in c.relocs(sec).items() if 0 <= off - start < len(body)}
                if not any(sn in wanted and typ in (0x06, 0x14) for sn, typ in rel.values()):
                    continue
                searched.add(fname)
                masked = {j for k in rel for j in range(k, min(k + 4, len(body)))}
                a = exe.find_unique(body, masked)
                if a is None and loose:   # a virtual function (??_G): the slot of a vftable the unit names
                    if slot_words is None:
                        slot_words = exe.vftable_words(unit)
                    at_ = [w for w in slot_words if exe.matches(w, body, masked)]
                    a = at_[0] if len(at_) == 1 else None
                if a is not None:
                    todo.append((fname, a))
                else:   # several identical copies: the sites all of them agree on
                    copies = exe.find_all(body, masked, SEARCH_COPIES)
                    if copies and 2 <= len(copies) <= SEARCH_COPIES:
                        for sn, t in copy_sites(exe, body, rel, copies, wanted):
                            out[sn][t] += 1
        if not todo:
            break
        fname, a = todo.pop()
        if fname in placed:
            continue
        sec, start, body = funcs[fname]
        img = exe.read(a, len(body))
        if img is None or not body:
            continue
        rel = {off - start: v for off, v in c.relocs(sec).items() if 0 <= off - start < len(body)}
        mask, fixed = bytearray(body), bytearray(img)
        for k in rel:
            for j in range(k, min(k + 4, len(body))):
                mask[j] = fixed[j] = 0
        if bytes(mask) != bytes(fixed) and not (loose and same_shape(body, img)):
            continue   # not the exe's code byte for byte here: none of its sites is trusted
        placed[fname] = a
        for k, (sn, typ) in rel.items():
            if k > len(body) - 4:
                continue
            if typ == 0x06:   # IMAGE_REL_I386_DIR32 in code: the function's address taken (push offset f)
                if sn in wanted:
                    word, = struct.unpack_from('<I', img, k)
                    addend, = struct.unpack_from('<I', body, k)
                    out[sn][(word - addend) & 0xffffffff] += 1
                continue
            if typ != 0x14:   # IMAGE_REL_I386_REL32
                continue
            disp, = struct.unpack_from('<i', img, k)
            addend, = struct.unpack_from('<i', body, k)
            t = (a + k + 4 + disp - addend) & 0xffffffff
            if sn in wanted:
                out[sn][t] += 1
            elif sn in funcs and sn not in placed:
                todo.append((sn, t))
    return out


# ---------------------------------------------------------------------------------------------------- call alignment

ALIGN_SKIP = ('__imp_', '__security', '__chkstk', '__alloca', '__EH', '__CxxFrame', '__ftol', '__alldiv',
              '__allmul', '__aulldiv', '__allshl', '__allshr', '__aullshr', '__allrem', '__aullrem', '__SEH',
              '_memcpy', '_memset', '__RTC')
ALIGN_MIN_RATIO = 0.5
ALIGN_MAX_BYTES = 0x4000


def object_call_seqs(objdir, i):
    """u<i>.obj -> {code symbol: [REL32 callee names in offset order]}."""
    import vslots
    p = os.path.join(objdir, 'u%d.obj' % i)
    if not os.path.exists(p):
        return {}
    c = vslots.coff(p)
    funcs = {}
    for name, si in c.defs.items():
        sy = c.syms[si]
        if sy.secnum > 0 and c.secs[sy.secnum].flags & IMAGE_SCN_CNT_CODE and sy.storage in (2, 3):
            ends = [v for v, _ in c.symbols_in(sy.secnum) if v > sy.value]
            end = min(ends) if ends else len(c.section_bytes(sy.secnum))
            funcs[name] = [sn for off, (sn, typ) in sorted(c.relocs(sy.secnum).items())
                           if sy.value <= off < end and typ == 0x14]
    return funcs


def align_targets(objdir, udata, exe, wanted):
    """`exe-addr` by call order, for names whose every caller the exe has only inlined. For each function P that
    units.json places, its pristine call sequence (REL32 callees in order; a callee defined in the same object and
    placed nowhere is expanded in place, as MSVC inlined it, up to 4 levels) is aligned with the exe's direct
    call/jmp targets in P's range (capstone; the range ends at the next placed function). Callees with an
    address are the anchors; in a replaced run of equal length on both sides, each callee without an address
    takes the exe target at its position. Held out on link 9f865b0a: 9,040 addresses right, 6 wrong.
    -> {msvc name in wanted: {address: votes}}."""
    import bisect
    import difflib
    import capstone
    addr_of, size_of = {}, {}
    for u in udata:
        for f in u['functions']:
            a = int(f['address'], 16)
            for n in [f['symbol']] + list(f.get('fold_aliases', [])):
                addr_of.setdefault(n, a)
                size_of.setdefault(n, f.get('size'))
        for n, a in (u.get('symbols') or {}).items():
            addr_of.setdefault(n, int(a, 16))
    starts = sorted({int(f['address'], 16) for u in udata for f in u['functions']})
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    out = collections.defaultdict(collections.Counter)

    def expand(funcs, n, depth=0):
        seq = []
        for x in funcs[n]:
            if x in funcs and x not in addr_of and depth < 4 and x != n:
                seq += expand(funcs, x, depth + 1)
            else:
                seq.append(x)
        return seq
    for i in range(len(udata)):
        funcs = object_call_seqs(objdir, i)
        for p_ in funcs:
            if p_ not in addr_of:
                continue
            seq = expand(funcs, p_)
            if not any(x in wanted for x in seq):
                continue
            a = addr_of[p_]
            size = size_of.get(p_)
            if not size:
                j = bisect.bisect_right(starts, a)
                size = min(starts[j] - a, ALIGN_MAX_BYTES) if j < len(starts) else None
            code = exe.read(a, size) if size else None
            if not code:
                continue
            es = [int(ins.op_str, 16) for ins in md.disasm(code, a)
                  if ins.mnemonic in ('call', 'jmp') and ins.size == 5 and ins.op_str.startswith('0x')
                  and not a <= int(ins.op_str, 16) < a + size]
            ptok = [addr_of.get(x, 'U:' + x) for x in seq]
            sm = difflib.SequenceMatcher(None, ptok, es, autojunk=False)
            if not es or sm.ratio() < ALIGN_MIN_RATIO:
                continue
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag == 'replace' and i2 - i1 == j2 - j1:
                    for t, e in zip(ptok[i1:i2], es[j1:j2]):
                        if isinstance(t, str) and t[2:] in wanted and not t[2:].startswith(ALIGN_SKIP):
                            out[t[2:]][e] += 1
    return out


# ---------------------------------------------------------------------------------------------------- run

def load_bind(out):
    """bodies/bind.tsv -> {Itanium name: (class, target)} for resolve (empty when the step never ran)."""
    path = os.path.join(out, 'bodies', 'bind.tsv')
    res = {}
    if os.path.exists(path):
        for line in open(path, encoding='utf-8', errors='surrogateescape'):
            f = line.rstrip('\n').split('\t')
            if not line.startswith('#') and len(f) >= 3:
                res[f[0]] = ('body-' + f[1], f[2])
    return res


def load_synth(out):
    """bodies/synth.tsv -> [(host unit, object path)] for the objects that exist."""
    path = os.path.join(out, 'bodies', 'synth.tsv')
    res = []
    if os.path.exists(path):
        for line in open(path, encoding='utf-8', errors='surrogateescape'):
            f = line.rstrip('\n').split('\t')
            if not line.startswith('#') and len(f) >= 2 and os.path.exists(f[1]):
                res.append((f[0], f[1]))
    return res


def exe_pass(rows, exe_path, msvc_objs, units_json, unit_names, native, itanium, msvc, ms, providers=None,
             provider_params=None):
    """`exe-addr`: a name still missing (or about to get a synthesized special member) whose MSVC counterpart the
    referencing unit's pristine object calls: bound to the address the exe's call at that site reaches, when every
    site agrees and the native build defines it with the right kind. provider_params: {address: parameter count} of
    S4's providers (load_provider_params): a provider's address no unit defines has no MSVC definition to compare, so
    its identification's parameter count stands for the shape (the provider takes the object first, then these)."""
    exe = Pe(exe_path)
    udata = json.load(open(units_json))['units']
    idx_of = collections.defaultdict(list)
    for i_, n_ in enumerate(unit_names):
        idx_of[n_].append(i_)
    cand = [r for r in rows if r.cls in ('missing', 'special-default', 'template-fold') and r.units]
    need = {i_ for r in cand for u_ in r.units.split(',') for i_ in idx_of.get(u_, ())}
    ext = {i_: pristine_externs(msvc_objs, i_) for i_ in need}
    ms.need({n_ for e_ in ext.values() for n_ in e_})
    mkeys = {}
    for e_ in ext.values():
        for m_ in e_:
            if m_ not in mkeys:
                sg = msvc(m_)
                mkeys[m_] = {k for t, k in sg.keys() if t in ('exact', 'l1', 'l2')} | {k for _, k in def_keys(sg)}
    pairs = collections.defaultdict(lambda: collections.defaultdict(set))   # unit index -> msvc name -> names
    for r in cand:
        isg = itanium(r.name)
        keys = {k for t, k in isg.keys() if t in ('exact', 'l1', 'l2')} | {k for _, k in relaxed_keys(isg)}
        for u_ in r.units.split(','):
            for i_ in idx_of.get(u_, ()):
                for m_ in ext[i_]:
                    if keys & mkeys[m_]:
                        pairs[i_][m_].add(r.name)
    def_names = collections.defaultdict(set)   # address -> MSVC names defined there
    for u_ in udata:
        for f_ in u_['functions']:
            def_names[int(f_['address'], 16)].add(f_['symbol'])
    ms.need({n_ for ns_ in def_names.values() for n_ in ns_ if n_.startswith('?')})

    def shape_ok(ref, a, this_only=False):
        """The MSVC definition at a and the pristine reference ref: as many parameters, both member functions or
        both not, the same calling convention (no ABI bridge is made for these bindings; resolve compares only the
        names it binds itself)."""
        rs = msvc(ref)
        if rs.kind != 'func' or rs.params is None:
            return False
        if this_only and a in (providers or {}):
            return True   # S4's runtime implements the exe's function there, taking the object (void *self)
        member = bool(rs.member)
        rparams = [p_ for p_ in rs.params if p_ != '...']
        if a in (providers or {}) and a in (provider_params or {}) and not def_names.get(a):
            return provider_params[a] == len(rparams)
        for n_ in def_names.get(a, ()):
            d_ = msvc(n_) if n_.startswith('?') else None
            if d_ is None or d_.kind != 'func' or d_.params is None:
                continue
            dparams = [p_ for p_ in d_.params if p_ != '...']
            if len(dparams) == len(rparams) and bool(d_.member) == member and d_.cc == rs.cc:
                return True
            if this_only and not d_.member and len(dparams) == 1 and dparams[0].rstrip().endswith('*'):
                return True   # the decomp spells the destructor as FUN_<addr>(T*): natively both take x0 = object
        return False
    seen = collections.defaultdict(collections.Counter)   # Itanium name -> {address: sites}
    aligned_names = set()

    def sret_ok(ref, a):
        """The reference returns a class by value and the definition at a takes one more parameter, a pointer,
        first (after this): MSVC's hidden return pointer as the decomp writes it."""
        rs = msvc(ref)
        if rs.kind != 'func' or rs.params is None or not rs.ret:
            return False
        ret = rs.ret.strip()
        if ret.endswith(('*', '&')) or SCALARS.match(ret) or ret.startswith('enum '):
            return False
        rparams = [p_ for p_ in rs.params if p_ != '...']
        for n_ in def_names.get(a, ()):
            d_ = msvc(n_) if n_.startswith('?') else None
            if d_ is None or d_.kind != 'func' or d_.params is None:
                continue
            dparams = [p_ for p_ in d_.params if p_ != '...']
            if len(dparams) == len(rparams) + 1 and dparams[0].rstrip().endswith('*') and \
                    bool(d_.member and not d_.static) == bool(rs.member and not rs.static):   # this or none
                return True
        return False
    via = {}
    by_m = collections.defaultdict(set)   # MSVC name -> Itanium names it stands for
    for mp in pairs.values():
        for m_, ns_ in mp.items():
            by_m[m_] |= ns_
    # every pristine object: the referencing unit's own call is often in a COMDAT the exe dropped (MSVC inlined it
    # into callers, maybe in other units, whose calls of the same name the exe keeps)
    for i_ in range(len(udata)):
        for m_, tg in exe_call_targets(msvc_objs, i_, udata[i_], set(by_m), exe, search=True).items():
            for n in by_m[m_]:
                seen[n].update(tg)
                via.setdefault(n, m_)
    try:   # names whose callers the exe has only inlined: the address at their position in the call order
        aligned = align_targets(msvc_objs, udata, exe, set(by_m))
    except ImportError:   # capstone missing: the call-order evidence is skipped
        aligned = {}
    for m_, tg in aligned.items():
        for n in by_m[m_]:
            if n not in seen:   # a byte-checked call site wins; the alignment fills only names without one
                seen[n].update(tg)
                via.setdefault(n, m_)
                aligned_names.add(n)
    out = []
    for r in rows:
        tg = seen.get(r.name)
        if not tg:
            out.append(r)
            continue
        isg = itanium(r.name)
        want = VARIANT_KIND.get(isg.variant, 'F') if isg.variant else 'F'
        if len(tg) != 1:
            out.append(r._replace(note=r.note + '; exe calls %s' % ','.join('%08x' % a for a in sorted(tg)[:4])))
            continue
        (a, nsite), = tg.items()
        this_only = bool(isg.variant) and not isg.params and bool(msvc(via[r.name]).member)
        if isg.variant in ('C1', 'C2', 'D1', 'D2') and native.get(a) == 'F' and \
                shape_ok(via[r.name], a, this_only=this_only):
            want = 'F'   # the decomp defines the constructor/destructor's address as a function (FUN_...)
        if native.get(a) == want and want == 'F' and not shape_ok(via[r.name], a, this_only=this_only) and \
                sret_ok(via[r.name], a) and r.units and \
                sret_line(isg, a, bool(msvc(via[r.name]).member) and not msvc(via[r.name]).static):
            this = bool(msvc(via[r.name]).member) and not msvc(via[r.name]).static
            out.append(r._replace(cls='exe-sret', addr=a, target=None, host=sorted(r.units.split(','))[0],
                                  note='sret %s: %s called at %d site(s) in the exe, %s' % (
                                      'this' if this else 'static', via[r.name], nsite,
                                      ' / '.join(sorted(def_names.get(a, ()))[:1]))))
            continue
        if native.get(a) == want and want == 'F' and not shape_ok(via[r.name], a, this_only=this_only):
            out.append(r._replace(note=r.note + '; exe calls %08x (signature shape differs: %s)' % (
                a, ' / '.join(sorted(def_names.get(a, ()))[:2]))))
            continue
        if native.get(a) != want or isg.variant == 'D0':
            out.append(r._replace(note=r.note + '; exe calls %08x (%s)' % (
                a, 'native kind %s' % native[a] if a in native else 'not compiled natively')))
            continue
        out.append(r._replace(cls='exe-addr', addr=a, target=ADDR_SYM % a, host=None,
                              note='%s %s %d site(s) in the exe' % (via[r.name], 'aligned by call order at'
                                                                    if r.name in aligned_names else 'called at', nsite) +
                              (' (%s bound to the complete-object definition)' % isg.variant
                               if isg.variant in ('C2', 'D2') else '')))
    return out


def load_cnames(refs_tsv):
    """Every unmangled name refs.tsv leaves `unresolved` (a decomp placeholder: C function, variable or function
    pointer) -> {name: set(referencing units)}."""
    out = collections.defaultdict(set)
    if refs_tsv and os.path.exists(refs_tsv):
        for line in open(refs_tsv, encoding='utf-8', errors='surrogateescape'):
            f = line.rstrip('\n').split('\t')
            if len(f) >= 3 and f[2] == 'unresolved' and re.match(r'^[A-Za-z_]\w*$', f[1]) and \
                    not f[1].startswith(('_Z', '__')):
                out[f[1]].add(f[0].split('#')[0])
    return dict(out)


def exe_data_pass(cnames, exe_path, msvc_objs, udata, is_data, members=None, msvc=None, placed_refs=None):
    """`exe-data`: a placeholder variable the decomp declares and nothing defines or binds (`extern A11MM g_a11mm;`,
    `extern int (__stdcall* LocalIncrement)(void*);`) whose MSVC name (`?g_a11mm@@3UA11MM@@A`: a variable, or `_name`)
    the pristine objects' placed code takes the address of: the exe's word at every such site (exe_call_targets,
    loose: variant units' other stack layouts and the ??_G a vftable slot holds are placed too) gives one image
    data address (is_data). Bound there like any image variable; a function pointer standing for an import gets its
    IAT slot (the same binding as `g_pfnInterlockedIncrement`). Function placeholders (REL32 calls) never bind:
    a value source the decomp invented has no exe address.
    members: {Itanium name: (canonical qual, units)} of declared-only C++ variables (a static data member
    `Pt3::ms_kFirstPoint`, a unit namespace's `fnv_00ec15e0::g_movieHeap`), matched to the MSVC data names
    (`?ms_kFirstPoint@Pt3@@2U1@B`, storage class 2/3) whose parsed qualified name is the same (msvc(name) -> Sig);
    bound the same way.
    placed_refs: a set, filled with the names (of cnames and members) that a function units.json places, or a
    same-object function it reaches (MSVC inlined it there), references in its pristine code, bytes matching or
    not: the exe has that variable, at an address and with an initial value
    the pass may not have found. decl-data never gives such a variable zeros (no evidence of the exe's value).
    -> [Row]"""
    import vslots
    members = members or {}
    if not cnames and not members:
        return []
    pats = {n: re.compile(r'^(?:\?%s@@3|_%s$)' % (re.escape(n), re.escape(n))) for n in cnames}
    first = {}
    for n in cnames:
        first.setdefault(n[:1], []).append(n)
    mlast = collections.defaultdict(list)   # '?<last component>@' -> member names
    for n, (q, _) in members.items():
        mlast['?%s@' % q[-1]].append(n)
    cnames = dict(cnames)
    cnames.update({n: us for n, (_, us) in members.items()})
    by_obj = collections.defaultdict(set)
    of = {}
    for i in range(len(udata)):
        p = os.path.join(msvc_objs, 'u%d.obj' % i)
        if not os.path.exists(p):
            continue
        try:
            c = vslots.coff(p)
        except Exception:   # a damaged object must not stop the step
            continue
        for sy in c.syms.values():
            if sy.secnum != 0 or sy.storage != 2:
                continue
            nm = sy.name
            key = nm[1:2] if nm[:1] in '?_' else ''
            for n in first.get(key, ()):
                if pats[n].match(nm):
                    by_obj[i].add(nm)
                    of[nm] = n
            if msvc is not None and nm.startswith('?') and '@' in nm:
                for n in mlast.get(nm[:nm.index('@') + 1], ()):
                    sg = msvc(nm)
                    if sg.kind == 'data' and tuple(sg.qual) == tuple(members[n][0]):
                        by_obj[i].add(nm)
                        of[nm] = n
    if placed_refs is not None:   # referenced by code the exe has (a placed function's relocations)
        for i, ms_ in sorted(by_obj.items()):
            try:
                c = vslots.coff(os.path.join(msvc_objs, 'u%d.obj' % i))
            except Exception:
                continue
            body = {}   # defined function -> names its code references
            for fname, si in c.defs.items():
                sy = c.syms[si]
                if sy.secnum <= 0 or sy.storage not in (2, 3) or not c.secs[sy.secnum].flags & IMAGE_SCN_CNT_CODE:
                    continue
                ends = [v for v, _ in c.symbols_in(sy.secnum) if v > sy.value]
                end = min(ends) if ends else len(c.section_bytes(sy.secnum))
                body[fname] = {sn for off, (sn, _) in c.relocs(sy.secnum).items() if sy.value <= off < end}
            # placed functions and what they reach in the object (MSVC inlined those callees into exe code)
            todo = [f['symbol'] for f in udata[i].get('functions', ()) if f['symbol'] in body]
            reached = set()
            while todo:
                f_ = todo.pop()
                if f_ in reached:
                    continue
                reached.add(f_)
                todo += [x for x in body[f_] if x in body and x not in reached]
            for f_ in reached:
                placed_refs.update(of[x] for x in body[f_] & ms_)
    exe = Pe(exe_path)
    seen = collections.defaultdict(collections.Counter)
    via = {}
    for i, ms_ in sorted(by_obj.items()):
        for m_, tg in exe_call_targets(msvc_objs, i, udata[i], ms_, exe, search=True, loose=True).items():
            seen[of[m_]].update(tg)
            via.setdefault(of[m_], m_)
    rows = []
    for n, tg in sorted(seen.items()):
        if len(tg) != 1:
            continue
        (a, nsite), = tg.items()
        if not is_data(a):
            continue
        rows.append(Row(n, 'exe-data', a, ADDR_SYM % a, ','.join(sorted(cnames[n])),
                        '%s at %d site(s) in the exe' % (via[n], nsite), None))
    return rows


EXE_VAR_NOTE = 'placed exe code references it, its exe address was not found: no zeros without evidence'


def pristine_data_names(objdir, nunits, lasts):
    """Undefined external `?<last>@...` names in the pristine objects, for every <last> in lasts (the MSVC names
    exe_data_pass may match to a declared-only C++ variable; demangled once, before the pass)."""
    import vslots
    out = set()
    pre = {'?%s@' % x for x in lasts}
    for i in range(nunits):
        p = os.path.join(objdir, 'u%d.obj' % i)
        if not os.path.exists(p):
            continue
        try:
            c = vslots.coff(p)
        except Exception:
            continue
        for sy in c.syms.values():
            nm = sy.name
            if sy.secnum == 0 and sy.storage == 2 and nm.startswith('?') and '@' in nm and \
                    nm[:nm.index('@') + 1] in pre:
                out.add(nm)
    return out


GDEL_SYM = '__fnvnx_bdgd_%08x'


def vtt_name(name):
    """_ZTT of the class of an Itanium D1/D2 name (its VTT: the class has virtual bases), None if not simply nested."""
    m = re.match(r'^_ZN((?:\d+[A-Za-z_]\w*)+)D[12]Ev$', name)
    if not m:
        return None
    parts = re.findall(r'(\d+)', m.group(1))
    return '_ZTT' + (m.group(1) if len(parts) == 1 else 'N%sE' % m.group(1))


def gdel_pass(rows, units, native, itanium, msvc, ms, known=()):
    """`gdel`: a D1 (a D2 too when the class has no virtual bases: no _ZTT of it in known) still missing or about to
    get `= default`, whose class has one scalar deleting destructor ??_G at an address the native build defines as a
    member function (this, unsigned int flags): MSVC folded the destructor's body into ??_G, which is
    `this->~C(); if (flags & 1) delete this; return this`. Bound to a stub that calls it with flags 0 (destroy, do
    not free)."""
    gaddr = collections.defaultdict(set)   # ??_G name -> addresses
    names_at = collections.defaultdict(set)
    for u in units.values():
        for a, n in list(u.defs) + [(a, n) for n, a in u.refs.items()]:
            if a in native:
                names_at[a].add(n)
                if n.startswith('??_G'):
                    gaddr[n].add(a)
    ms.need(set(gaddr) | {n for ns in names_at.values() for n in ns if n.startswith('?')})
    bycls = collections.defaultdict(set)
    for n, a_ in gaddr.items():
        m = re.search(r"(?:__thiscall )?(\S+)::`scalar deleting dtor'", ms[n] or '')
        if m:
            bycls[sigs.canon_type(m.group(1))] |= a_

    def flags_member(a):
        for n in names_at.get(a, ()):
            if n.startswith('?') and not n.startswith('??_'):
                d = msvc(n)
                if d.kind == 'func' and d.member and d.params is not None and len(d.params) == 1 and \
                        re.match(r'^(unsigned )?int$', d.params[0]):
                    return n
        return None
    known = set(known)
    out = []
    for r in rows:
        isg = itanium(r.name)
        if r.cls not in ('missing', 'special-default') or isg.variant not in ('D1', 'D2'):
            out.append(r)
            continue
        if isg.variant == 'D2' and (vtt_name(r.name) is None or vtt_name(r.name) in known):
            out.append(r)
            continue
        cands = bycls.get('::'.join(isg.qual[:-1]), set())
        a = next(iter(cands)) if len(cands) == 1 else None
        fm = flags_member(a) if a is not None and native.get(a) == 'F' else None
        if fm is None:
            out.append(r)
            continue
        out.append(r._replace(cls='gdel', addr=a, target=GDEL_SYM % a, host=None,
                              note='??_G at %08x (%s) with flags 0; was %s' % (a, fm, r.cls)))
    return out


def allms_itanium(out):
    """The VTT names (_ZTT...) any unit references (refs.tsv): their classes have virtual bases."""
    path = os.path.join(out, 'refs.tsv')
    got = set()
    if os.path.exists(path):
        for line in open(path, encoding='utf-8', errors='surrogateescape'):
            f = line.split('\t', 2)
            if len(f) > 1 and f[1].startswith('_ZTT'):
                got.add(f[1])
    return got


def emit_gdel(addrs):
    """AArch64 ILP32 stubs: w1 = 0 (flags: do not free), tail call of the ??_G's native definition."""
    out = ['// GENERATED by tools/link/bodies.py: D1/D2 bound to the scalar deleting destructor with flags 0', '.text']
    for a in sorted(addrs):
        sym = GDEL_SYM % a
        out += ['.globl %s' % sym, '.type %s, %%function' % sym, '.p2align 2', '%s:' % sym,
                '    mov w1, #0', '    b %s' % (ADDR_SYM % a), '.size %s, .-%s' % (sym, sym)]
    return '\n'.join(out) + '\n'


class Phases:
    """Seconds per step of run(), printed at the end (T1: where the link's time goes, for the incremental link)."""
    def __init__(self):
        self.t, self.got = time.time(), []

    def mark(self, name):
        now = time.time()
        self.got.append((name, now - self.t))
        self.t = now

    def line(self):
        return 'bodies phases: ' + ', '.join('%s %.1f s' % g for g in self.got)


def run(out, tree, msvc_objs=None, names_txt=None, units_json=None, jobs=4, synth=True, exe_path=None):
    import fnvlink
    import objcache
    T = Phases()
    units_json = units_json or os.path.join(tree, 'build', 'units.json')
    bdir = os.path.join(out, 'bodies')
    os.makedirs(bdir, exist_ok=True)
    os.makedirs(os.path.join(out, 'cache'), exist_ok=True)
    sigs.persist(os.path.join(out, 'cache', 'sigs.marshal'))   # the parses of the last link process (T1)
    trapped = load_trapped(os.path.join(out, 'refs.tsv'), names_txt)
    native = load_native(os.path.join(out, 'addrmap.tsv'))
    units = fnvlink.load_units(units_json)
    unit_names = [u['source'] for u in json.load(open(units_json))['units']]
    ms = fnvlink.DemangleCache(os.path.join(out, 'cache', 'msvc.tsv'), fnvlink.UNDNAME)
    it = fnvlink.DemangleCache(os.path.join(out, 'cache', 'itanium.tsv'), fnvlink.CXXFILT)
    it.need(trapped)
    isig = {}

    def itanium(n):
        if n not in isig:
            if n not in it.map:
                it.need([n])
            isig[n] = sigs.parse_itanium(n, it[n])
        return isig[n]
    allms = {n for u in units.values() for n in list(u.refs) + [n for _, n in u.defs] if n.startswith('?')}
    bodies = None
    if msvc_objs:
        toks = set()
        for n in trapped:
            toks |= tokens_for(itanium(n))
        bodies = pristine_bodies(msvc_objs, len(unit_names), toks)
        allms |= set(bodies)
    ms.need(allms)
    msig = {}

    def msvc(n):
        if n not in msig:
            msig[n] = sigs.parse_msvc(n, ms[n])
        return msig[n]
    cl = Classifier(units, native, msvc, itanium, bodies, unit_names, load_type_sizes(out),
                    load_native_names(os.path.join(out, 'addrmap.tsv')))
    rows = [cl.classify(n, us) for n, us in sorted(trapped.items())]
    T.mark('load+classify')

    exe_vars = set()   # variables placed exe code references whose address exe-data did not find
    if msvc_objs and exe_path:
        rows = exe_pass(rows, exe_path, msvc_objs, units_json, unit_names, native, itanium, msvc, ms,
                        load_providers(os.path.join(out, 'addrmap.tsv')), load_provider_params())
        # placeholder variables the exe's code addresses: bound to that image address (before decl-data)
        have_ = {r.name for r in rows}
        # declared-only C++ variables (static data members, unit namespaces' globals) the same way, before decl-data
        mem = {r.name: (tuple(itanium(r.name).qual), set(r.units.split(','))) for r in rows
               if r.cls == 'missing' and r.note.startswith('not a function') and r.units and itanium(r.name).qual}
        if mem:
            ms.need(pristine_data_names(msvc_objs, len(unit_names), {q[-1] for q, _ in mem.values()}))
        got_ = exe_data_pass({n: us for n, us in load_cnames(os.path.join(out, 'refs.tsv')).items()
                              if n not in have_}, exe_path, msvc_objs, json.load(open(units_json))['units'],
                             fnvlink.is_data_addr, mem, msvc, exe_vars)
        bym_ = {r.name: r for r in got_ if r.name in mem}
        rows = [bym_[r.name] if r.name in bym_ else r for r in rows]
        rows += [r for r in got_ if r.name not in have_]
        exe_vars -= {r.name for r in got_}
        T.mark('exe-pass')
    gate = None
    if synth:   # its stubs are emitted with the synthesized objects
        # a member of another copy of the class at an exe address, one layout on both sides (layout probe)
        os.makedirs(os.path.join(bdir, 'work'), exist_ok=True)
        gate = LayoutGate(tree, os.path.join(bdir, 'work'), load_type_sizes(out))
        rows = copy_addr_pass(rows, cl, itanium, gate)
        rows = gdel_pass(rows, units, native, itanium, msvc, ms, known=set(trapped) | allms_itanium(out))
        rows = crt_pass(rows, itanium)   # its stubs are emitted with the synthesized objects
        # variables declared and defined nowhere, without an exe address: a typed zero definition (decl-data)
        # never for a variable code the exe has references (exe_vars): its address and initial value are the exe's,
        # not found here, so zeros would hide a gap; it stays missing (a visible data trap)
        rows = [r._replace(note=r.note + '; %s' % EXE_VAR_NOTE) if r.name in exe_vars and r.cls == 'missing' else
                r._replace(cls='decl-data', host=sorted(r.units.split(','))[0],
                           note='zero-initialized definition of the declared variable (no exe address)')
                if r.cls == 'missing' and r.note.startswith('not a function') and r.units and
                data_line(itanium(r.name).qual) else r for r in rows]
        have_ = {r.name for r in rows}
        for n, us in sorted(load_cdata(os.path.join(out, 'refs.tsv')).items()):
            if n in exe_vars:   # stays unresolved (a data trap), as without decl-data
                continue
            if n not in have_ and data_line([n]):
                rows.append(Row(n, 'decl-data', None, None, ','.join(sorted(us)),
                                'zero-initialized definition of the declared variable (no exe address)', sorted(us)[0]))

    T.mark('copy/gdel/crt/decl passes')
    synth_rows, failed, vnotes = [], {}, {}
    if synth:
        work = os.path.join(bdir, 'work')
        os.makedirs(work, exist_ok=True)
        import layout
        layout.game_flags(tree, work)   # compile.sh --print-flags (plugins built) once, before the threads; no fallback
        by_host = collections.defaultdict(lambda: collections.defaultdict(list))
        for r in rows:
            if r.cls in ('special-default', 'msvc-inline', 'exe-sret', 'decl-data') and r.host:
                by_host[r.host][r.cls == 'msvc-inline'].append(r)
        jobs_ = [(h, inl, rs) for h, d in sorted(by_host.items()) for inl, rs in sorted(d.items())]

        def one(job):
            h, inl, rs = job
            try:
                return h, inl, synth_host(tree, work, h, rs, itanium, inl)
            except Exception as e:   # one host must not stop the step
                return h, inl, (None, set(), {r.name: 'synth-failed (%r)' % e for r in rs})
        with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
            for h, inl, (path, roots, fail) in ex.map(one, jobs_):
                failed.update(fail)
                if path:
                    dst = os.path.join(bdir, 'zz_bd_%s%s.o' % (h.replace('/', '_'), '_i' if inl else ''))
                    os.replace(path, dst)
                    synth_rows.append((h, dst, len(roots)))
        # an implicit special member the pristine object has a body for (msvc-inline) but whose host unit emits none
        # (the class declares no such member, so nothing odr-uses it there): the implicit member's own semantics
        retry = retry_special(rows, failed, itanium)
        if retry:
            def rone(job):
                h, rs = job
                try:
                    return h, rs, synth_host(tree, work, h, [r._replace(cls='special-default', host=h) for r in rs],
                                             itanium, False)
                except Exception as e:
                    return h, rs, (None, set(), {r.name: 'synth-failed (%r)' % e for r in rs})
            done_sd = {}
            with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
                for h, rs, (path, roots, fail) in ex.map(rone, sorted(retry.items())):
                    if path:
                        dst = os.path.join(bdir, 'zz_bd_%s_sd.o' % h.replace('/', '_'))
                        os.replace(path, dst)
                        synth_rows.append((h, dst, len(roots)))
                    done_sd.update({n: h for n in roots})
            for n in done_sd:
                failed.pop(n, None)
            rows = [Row(x.name, 'special-default', None, None, x.units, 'implicit member after msvc-inline (%s)' %
                        x.note, done_sd[x.name]) if x.name in done_sd else x for x in rows]
        T.mark('synth-host')
        # virtual functions GCC calls directly (an object member, `a->m.f()`) where MSVC called through the vptr
        cand = {r.name: r for r in rows if r.cls == 'missing' and r.note == 'no definition, address or body'
                and r.units and vcall_line(0, itanium(r.name))}
        if msvc_objs and cand:   # a direct reference in the pristine object's code is a real non-virtual call
            # (B::f()); a vftable slot of the same name is not (MSVC called it through the vptr)
            idx_of = collections.defaultdict(list)
            for i_, n_ in enumerate(unit_names):
                idx_of[n_].append(i_)
            need = {i_ for r in cand.values() for u_ in r.units.split(',') for i_ in idx_of.get(u_, ())}
            ext = {i_: pristine_externs(msvc_objs, i_, code_only=True) for i_ in need}
            ms.need({n_ for e_ in ext.values() for n_ in e_})
            for n, r in list(cand.items()):
                keys = {k for _, k in relaxed_keys(itanium(n))}
                for u_ in r.units.split(','):
                    if any(keys & {k for _, k in relaxed_keys(msvc(m_))} for i_ in idx_of.get(u_, ())
                           for m_ in ext[i_]):
                        failed[n] = VCALL_DIRECT
                        del cand[n]
                        break
        vby = collections.defaultdict(list)
        for n, r in cand.items():
            vby[sorted(r.units.split(','))[0]].append(n)

        def vone(job):
            h, ns = job
            try:
                return probe_vcalls(tree, work, h, ns, itanium)
            except Exception as e:
                return {}, {n: 'vcall probe failed (%r)' % e for n in ns}
        vstubs, vtargets = {}, {}
        with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
            for got, why in ex.map(vone, sorted(vby.items())):
                for n, (off, adj) in got.items():
                    st_ = VCALL_SYM % int(hashlib.sha1(n.encode('utf-8', 'surrogateescape')).hexdigest()[:8], 16)
                    vstubs[st_] = (n, off, adj)
                    vtargets[n] = (st_, off, adj)
                vnotes.update(why)
        if vstubs:
            s_path, o_path = os.path.join(bdir, 'vcall.S'), os.path.join(bdir, 'zz_bd_vcall.o')
            open(s_path, 'w', encoding='utf-8', errors='surrogateescape').write(emit_vcalls(vstubs))
            as_ = os.environ.get('AS', 'aarch64-linux-gnu-as')
            if subprocess.run([as_, '-mabi=ilp32', s_path, '-o', o_path], capture_output=True).returncode == 0:
                synth_rows.append((sorted(cand[next(iter(vtargets))].units.split(','))[0], o_path, len(vstubs)))
                rows = [r._replace(cls='vcall', target=vtargets[r.name][0],
                                   note='slot byte %d, this +%d' % vtargets[r.name][1:]) if r.name in vtargets
                        else r for r in rows]
        # bodies the decomp writes inline in another unit's source (`inline C::f() {...}`, an in-class body of a
        # copy of C): that unit hosts them, compiled with -fkeep-inline-functions like msvc-inline
        # a name MSVC calls out of line (the vcall check's pristine reference) is no vcall, but the decomp's inline
        # body is still its body: tree-inline takes it, gated like a copy pair (strict)
        T.mark('vcall')
        direct = {n for n, why in failed.items() if why == VCALL_DIRECT}
        inl_retry = msvc_inline_retry(rows, failed)
        vdone = set(vtargets) | (set(failed) - direct - inl_retry) | set(vnotes)
        rest = [r for r in rows if (r.cls == 'missing' or r.name in inl_retry) and r.name not in vdone and r.units]
        tin = tree_inline_hosts(tree, rest, itanium, set(unit_names), load_layouts(out))
        # a class template's destructor no object instantiates (the pointee is incomplete where it is referenced): the
        # template's own body, explicitly instantiated in a unit that names the arguments (inst_hosts)
        inst_rows = [r for r in rest if inst_line(itanium(r.name))]
        if inst_rows:
            import bodycause
            want_ = {(k[0], '~' + k[0]) for k in (bodycause.key_of(itanium(r.name)) for r in inst_rows) if k and k[0]}
            ih = inst_hosts(tree, inst_rows, itanium, set(unit_names), bodycause.tree_bodies(tree, want_),
                            {r.name: r.units.split(',') for r in inst_rows})
            for n_, hs_ in ih.items():
                tin[n_] = hs_ + [h_ for h_ in tin.get(n_, []) if h_ not in hs_]
        # a library member the unit declares as an explicit specialization and nothing instantiates (member_inst_hosts)
        mem_rows = [r for r in rest if member_inst_line(itanium(r.name))]
        mih = member_inst_hosts(tree, mem_rows, itanium, set(unit_names), {r.name: r.units.split(',') for r in mem_rows},
                                SHIM_DIR) if mem_rows else {}
        for n_, hs_ in mih.items():
            tin[n_] = hs_ + [h_ for h_ in tin.get(n_, []) if h_ not in hs_]
        # tree-inline's pairings across units pass the layout gate: names MSVC calls directly, constructors and
        # destructors (found since the search spells them by the class name), and every copy-key rename
        strict = direct | inl_retry | {n for n in tin if itanium(n).variant} | set(mih)

        def tone(job):
            h, rs, k = job
            try:
                return h, k, synth_host(tree, work, h, rs, itanium, True, gate=gate, strict=strict, minst=set(mih))
            except Exception as e:
                return h, k, (None, set(), {r.name: 'synth-failed (%r)' % e for r in rs})
        tdone = {}
        for round_ in range(5):   # a host that does not pair a name: the next host with a body of the class
            tby = collections.defaultdict(list)
            for r in rest:
                hs = tin.get(r.name, [])
                if r.name not in tdone and round_ < len(hs) and (round_ == 0 or r.name in strict):
                    tby[hs[round_]].append(r)
            if not tby:
                break
            with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
                for h, k, (path, roots, fail) in ex.map(tone, [(h, rs, round_) for h, rs in sorted(tby.items())]):
                    if path:
                        dst = os.path.join(bdir, 'zz_bd_%s_t%s.o' % (h.replace('/', '_'), str(k) if k else ''))
                        os.replace(path, dst)
                        synth_rows.append((h, dst, len(roots)))
                        tdone.update({n: h for n in roots})
                    for n, why in fail.items():
                        if n not in tdone:
                            vnotes[n] = (vnotes[n] + '; ' if n in vnotes and k else '') + \
                                'tree-inline in %s: %s' % (h, why)
        for n in tdone:
            failed.pop(n, None)
            vnotes.pop(n, None)
        rows = [r._replace(cls='tree-inline', host=tdone[r.name], note='inline body in ' + tdone[r.name])
                if r.name in tdone else r for r in rows]
        T.mark('tree-inline')
        gd = [r for r in rows if r.cls == 'gdel']
        if gd:
            s_path, o_path = os.path.join(bdir, 'gdel.S'), os.path.join(bdir, 'zz_bd_gdel.o')
            open(s_path, 'w').write(emit_gdel({r.addr for r in gd}))
            as_ = os.environ.get('AS', 'aarch64-linux-gnu-as')
            if subprocess.run([as_, '-mabi=ilp32', s_path, '-o', o_path], capture_output=True).returncode == 0:
                synth_rows.append((sorted(gd[0].units.split(','))[0] if gd[0].units else unit_names[0], o_path, len(gd)))
            else:
                failed.update({r.name: 'synth-failed (as)' for r in gd})
        # exe addresses callers are bound to whose placing unit emits nothing there (code-defmissing)
        dm = load_defmissing(os.path.join(out, 'refs.tsv'))
        placed_at = {}
        for u_ in json.load(open(units_json))['units']:
            for f_ in u_['functions']:
                a_ = int(f_['address'], 16)
                if a_ in dm and a_ not in placed_at:
                    placed_at[a_] = (u_['source'], f_['symbol'])
        ms.need({m_ for _, m_ in placed_at.values()})
        aby = collections.defaultdict(list)
        for a_, (h_, m_) in placed_at.items():
            aby[h_].append((a_, m_))

        def ita_defs(names):
            it.need(names)
            return [itanium(n) for n in names]

        def aone(job):
            h, items = job
            try:
                return h, items, addr_host(tree, work, h, sorted(items), msvc, ita_defs)
            except Exception as e:
                return h, items, ([], {}, {a: 'addr-host failed (%r)' % e for a, _ in items})
        T.mark('gdel+defmissing')
        atargets = {}
        with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
            for h, items, (objs, got, fail) in ex.map(aone, sorted(aby.items())):
                for k_, p_ in enumerate(objs):
                    dst = os.path.join(bdir, 'zz_bd_%s_a%d.o' % (h.replace('/', '_'), k_))
                    os.replace(p_, dst)
                    synth_rows.append((h, dst, len(got)))
                atargets.update(got)
                for a_, m_ in items:
                    rows.append(Row(ADDR_SYM % a_, 'addr-host' if a_ in got else 'missing', a_, got.get(a_),
                                    ','.join(sorted(dm[a_])), ('%s in %s' % (m_, h)) if a_ in got else
                                    'addr-host: %s (%s in %s)' % (fail.get(a_, '?'), m_, h), h))
        if atargets:
            s_path, o_path = os.path.join(bdir, 'addr.S'), os.path.join(bdir, 'zz_bd_addr.o')
            open(s_path, 'w', encoding='utf-8', errors='surrogateescape').write(emit_addr_stubs(atargets))
            as_ = os.environ.get('AS', 'aarch64-linux-gnu-as')
            if subprocess.run([as_, '-mabi=ilp32', s_path, '-o', o_path], capture_output=True).returncode == 0:
                synth_rows.append((unit_names[0], o_path, len(atargets)))
        T.mark('addr-host')
        thunks = load_thunks(os.path.join(out, 'refs.tsv'))
        if thunks:
            s_path, o_path = os.path.join(bdir, 'thunks.S'), os.path.join(bdir, 'zz_bd_thunks.o')
            open(s_path, 'w', encoding='utf-8', errors='surrogateescape').write(emit_thunks(thunks))
            as_ = os.environ.get('AS', 'aarch64-linux-gnu-as')
            if subprocess.run([as_, '-mabi=ilp32', s_path, '-o', o_path], capture_output=True).returncode == 0:
                host_ = sorted(next(iter(thunks.values())) or [unit_names[0]])[0]
                synth_rows.append((host_, o_path, len(thunks)))
                for n, us in sorted(thunks.items()):
                    off, vcall, target = thunk_parts(n)
                    rows.append(Row(n, 'thunk', None, target, ','.join(sorted(us)),
                                    'this %+d%s -> %s' % (off, ', vcall -%d' % vcall if vcall is not None else '',
                                                          target), None))
        crt = [r for r in rows if r.cls == 'crt-rule']
        if crt:
            s_path, o_path = os.path.join(bdir, 'crt.S'), os.path.join(bdir, 'zz_bd_crt.o')
            open(s_path, 'w', encoding='utf-8', errors='surrogateescape').write(emit_crt(crt, itanium))
            as_ = os.environ.get('AS', 'aarch64-linux-gnu-as')
            if subprocess.run([as_, '-mabi=ilp32', s_path, '-o', o_path], capture_output=True).returncode == 0:
                synth_rows.append((sorted(crt[0].units.split(','))[0] if crt[0].units else unit_names[0],
                                   o_path, len(crt)))
            else:
                failed.update({r.name: 'synth-failed (as)' for r in crt})
        empty = [r for r in rows if r.cls == 'msvc-empty']
        if empty:
            s_path, o_path = os.path.join(bdir, 'empty.S'), os.path.join(bdir, 'zz_bd_empty.o')
            open(s_path, 'w', encoding='utf-8', errors='surrogateescape').write(emit_empty(empty))
            as_ = os.environ.get('AS', 'aarch64-linux-gnu-as')
            if subprocess.run([as_, '-mabi=ilp32', s_path, '-o', o_path], capture_output=True).returncode == 0:
                synth_rows.append((sorted(empty[0].units.split(','))[0] if empty[0].units else unit_names[0],
                                   o_path, len(empty)))
            else:
                failed.update({r.name: 'synth-failed (as)' for r in empty})
    rows = fold_fallback(rows, failed, cl, itanium)
    rows = [r._replace(cls='missing', note=failed[r.name] + '; was ' + r.cls + ': ' + r.note)
            if r.name in failed else r for r in rows]
    rows = [r._replace(note=r.note + '; ' + vnotes[r.name]) if r.cls == 'missing' and r.name in vnotes else r
            for r in rows]

    with open(os.path.join(bdir, 'bodies.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#name\tclass\taddress\ttarget\tunits\tnote\thost\n')
        for r in rows:
            f.write('\t'.join([r.name, r.cls, '%08x' % r.addr if r.addr is not None else '', r.target or '',
                               r.units, r.note, r.host or '']) + '\n')
    with open(os.path.join(bdir, 'bind.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#name\tclass\ttarget\n')
        for r in rows:
            if r.cls in ('addr', 'exe-addr', 'vcall', 'gdel', 'template-fold', 'exe-data', 'copy-addr'):
                f.write('%s\t%s\t%s\n' % (r.name, r.cls, r.target))
    with open(os.path.join(bdir, 'synth.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#host_unit\tobject\tnames\n')
        for h, p, n in synth_rows:
            f.write('%s\t%s\t%d\n' % (h, p, n))
    st = collections.Counter(r.cls for r in rows)
    special = collections.Counter(r.cls for r in rows if itanium(r.name).variant)
    lines = ['names still undefined after resolve: %d (ctors/dtors %d)' % (len(rows), sum(special.values()))]
    lines += ['%-16s %6d  (ctors/dtors %d)' % (k, st[k], special[k])
              for k in ('addr', 'template-fold', 'exe-addr', 'copy-addr', 'exe-data', 'gdel', 'vcall', 'msvc-empty',
                        'msvc-inline', 'tree-inline', 'exe-sret', 'thunk', 'addr-host', 'decl-data', 'special-default', 'crt-rule',
                        'missing')]
    lines.append('pristine MSVC objects: %s' % ('%d code COMDATs matched by token' % len(bodies) if bodies is not None
                                                 else 'not given (--msvc-objs)'))
    lines.append('synthesized objects: %d' % len(synth_rows))
    open(os.path.join(bdir, 'stats.txt'), 'w').write('\n'.join(lines) + '\n')
    print('\n'.join(lines), file=sys.stderr)
    T.mark('emit+write')
    print(T.line(), file=sys.stderr)
    print(objcache.summary('bodies'), file=sys.stderr, flush=True)
    return rows


def main(argv):
    """fnvlink.py bodies out/ tree/ [--msvc-objs DIR] [--exe FalloutNV.exe | $FNVNX_EXE] [--names undefined.txt]
    [--units units.json] [--jobs N]
    [--no-synth]"""
    pos, opts = [], {}
    it_ = iter(argv)
    for x in it_:
        if x in ('--msvc-objs', '--names', '--units', '--jobs', '--exe'):
            opts[x[2:]] = next(it_)
        elif x == '--no-synth':
            opts['no-synth'] = True
        else:
            pos.append(x)
    run(pos[0], pos[1], msvc_objs=opts.get('msvc-objs'), names_txt=opts.get('names'), units_json=opts.get('units'),
        jobs=int(opts.get('jobs', os.cpu_count() or 4)), synth=not opts.get('no-synth'),
        exe_path=opts.get('exe') or os.environ.get('FNVNX_EXE'))
    # the vtables / typeinfo only the synthesized bodies name: requested, then the synthesis runs again (its probe
    # cache keeps the earlier classes), so resolve 2 links them like the others (s2d-010)
    import vtables
    n = vtables.bodies_requests(pos[0])
    print('bodies: %d vtable / typeinfo requests from the synthesized bodies' % n, file=sys.stderr)
    if n and not os.environ.get('FNVNX_NO_VTABLES'):
        vtables.run(pos[0], pos[1], int(opts.get('jobs', os.cpu_count() or 4)))
