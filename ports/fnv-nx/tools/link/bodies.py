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
   relaxed key pairs, as for msvc-inline). A host that does not emit it leaves the name `missing`.
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
   placement deletes `C::operator delete(void*, void*)` (a `ret`: the standard placement delete does nothing).
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
import sys

import sigs

TRAPPED = ('unresolved', 'unresolved-ctor-dtor', 'ctor-dtor-trap')
ADDR_SYM = '__fnv_%08x'
SHELL = re.compile(r'(?:_shell|_Shell|_SHELL)$')
UNIT_NS = re.compile(r'^fnv_[0-9a-f]{8}$')            # the decomp's per-function namespaces (fnvlink.UNIT_NS)
UNIT_NS_ANY = re.compile(r'\bfnv_[0-9a-f]{8}::')
IMAGE_SCN_CNT_CODE, IMAGE_SCN_LNK_COMDAT = 0x20, 0x1000
VARIANT_KIND = {'C1': 'C', 'C2': 'C', 'D1': 'X', 'D2': 'X'}   # addrmap kind an Itanium ctor/dtor variant may bind to


# ---------------------------------------------------------------------------------------------------- inputs

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
    Parameters at sigs level 2 (cv and long/int spellings ignored)."""
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
    return keys


def def_keys(sig):
    """The relaxed keys a definition (or a name standing for one) is indexed under: not a unit-local copy's
    global key (a global reference never binds to one unit's copy)."""
    return [(t, k) for t, k in relaxed_keys(sig) if t != 'unit-ns']


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
            c = vslots.Coff(p)
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

    def __init__(self, units, native, msvc, itanium, bodies=None, unit_names=()):
        self.native, self.itanium, self.bodies, self.unit_names = native, itanium, bodies, list(unit_names)
        self.idx = collections.defaultdict(lambda: collections.defaultdict(set))   # key -> {addr: {msvc names}}
        for u in units.values():
            for a, n in list(u.defs) + [(a, n) for n, a in u.refs.items()]:
                if a in native and n.startswith('?'):
                    m = msvc(n)
                    for _, k in def_keys(m):
                        self.idx[k][a].add(n)
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

    def by_address(self, isig):
        want = VARIANT_KIND.get(isig.variant, 'F') if isig.variant else 'F'
        if isig.variant == 'D0':
            return None
        for tier, k in relaxed_keys(isig):
            cands = {a: ns for a, ns in self.idx.get(k, {}).items() if self.native.get(a) == want}
            if len(cands) == 1:
                (a, ns), = cands.items()
                return tier, a, sorted(ns)[0]
            if len(cands) > 1:
                return 'ambiguous', None, ','.join('%08x' % a for a in sorted(cands)[:6])
        return None

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
        hit = self.by_address(isig)
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


def crt_rule(isig):
    """('throw', libstdc++ thrower, message) / ('ret', None, None) for a name crt-rule defines, else None."""
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
    return None


def crt_pass(rows, itanium):
    """missing rows a runtime rule defines -> crt-rule."""
    out = []
    for r in rows:
        k = crt_rule(itanium(r.name)) if r.cls == 'missing' else None
        if k:
            r = r._replace(cls='crt-rule', note='throws %s("%s")' % (k[1], k[2]) if k[0] == 'throw'
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


def gdel_line(addr, msig):
    """A scalar deleting destructor `??_GC@@...(unsigned int)` the decomp places but GCC has no counterpart of:
    `__fnv_<addr>(C *, unsigned)` destroys, and deletes on bit 0, as MSVC's does. None for a class it cannot name."""
    if msig is None or msig.kind != 'func' or len(msig.qual) < 2:
        return None
    scope = msig.qual[:-1]
    if any('<' in p or '{' in p or '`' in p or "'" in p or '(' in p for p in scope):
        return None
    c = '::' + '::'.join(scope)
    return ('extern "C" void *%s(%s *p, unsigned int f) { if (f & 1) delete p; else p->%s::~%s(); return p; }'
            % (ADDR_SYM % addr, c, c, scope[-1]))


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
        its = [(k, 'gd', ln) for k, (a, ln) in enumerate(lines)]
        obj, alive = vtables.compile_probe(tree, work, host, its, 'bdg', ('-ffunction-sections', '-fdata-sections'),
                                           prelude='', game=True)
        ok = {lines[k][0] for k, _, _ in alive} if obj else set()
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


def synth_host(tree, work, host, rows, itanium, inline=False):
    """Compile the host unit again with the `= default` lines (special-default) or with -fkeep-inline-functions
    (msvc-inline); keep only the wanted symbols. -> (object path or None, {names defined}, {name: why failed})."""
    import vtables
    want = {r.name for r in rows}
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
        for w in want - defined:
            for _, k in relaxed_keys(itanium(w)):
                if len(byk.get(k, ())) == 1:
                    renames[next(iter(byk[k]))] = w
                    break
        if renames:
            ren = base + 'r.o'
            args = ['--redefine-sym=%s=%s' % kv for kv in sorted(renames.items())]
            if subprocess.run([vtables.OBJCOPY, *args, clean, ren], capture_output=True).returncode == 0:
                clean = ren
                defined = (defined - set(renames)) | set(renames.values())
    got = vtables.gc_weaken(clean, want & defined, base + '.x')
    if not got:
        return None, set(), {n: 'synth-failed (not emitted)' for n in want}
    path, roots = got
    for n in want - roots:
        failed[n] = 'synth-failed (%s)' % ('not emitted by the host' if inline else 'GCC refused the = default')
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
    found = bodycause.tree_bodies(tree, set(keys.values()))
    out = {}
    for r in rows:
        k = keys.get(r.name)
        if not k:
            continue
        hosts = sorted({f for f, where in found.get(k, ()) if where.startswith('live') and f in unit_sources})
        if layouts:   # the class must have the referencing units' size in the host where the probe knows both
            ref = set().union(*(layouts.get((u, k[0]), set()) for u in r.units.split(',')))
            hosts = [h for h in hosts if not (ref and layouts.get((h, k[0])) and layouts[(h, k[0])] != ref)]
        if hosts:
            refs = [h for h in hosts if h in r.units.split(',')]
            out[r.name] = (refs or hosts)[0]
    return out


# ---------------------------------------------------------------------------------------------------- vcalls

VCALL_SYM = '__fnvnx_bdvc_%08x'
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
    if not os.path.exists(head):
        with open(head + '.tmp', 'w') as f:
            f.write('#pragma once\n' + VCALL_HEAD + '\n')
        os.replace(head + '.tmp', head)
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
    c = vslots.Coff(p)
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

    def find_unique(self, body, masked):
        """The one address in a code section whose bytes equal body outside the offsets in masked, else None.
        Candidates come from a literal search of body's longest unmasked run, then each is compared in full."""
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
                    if len(hits) > 1:
                        return None
                at = self.data.find(lit, at + 1, end)
        return hits[0] if hits else None

    def read(self, va, n):
        for start, size, roff, rsize in self.secs:
            if start <= va and va + n <= start + min(size, rsize):
                return self.data[roff + va - start:roff + va - start + n]
        return None


SEARCH_MIN = 16   # bytes: shorter bodies (a jmp thunk, `mov eax, [ecx]; ret`) occur too often to place by content


def exe_call_targets(objdir, i, unit, wanted, exe, search=False):
    """Original addresses the pristine object of unit i calls the MSVC names in wanted at. Functions are placed
    by units.json, then by following REL32 calls from placed functions (a COMDAT units.json does not list, such as
    `NiPointer<T>::operator=`, at the address the exe's call reaches); a function counts as placed only where its
    x86 bytes equal the exe's outside relocations. At each REL32 relocation naming a wanted name in a placed
    function, the exe's displacement gives the target; at a DIR32 one (the address taken, `push offset f`), the
    exe's word does. search: a function that references a wanted name and was not placed (inlined into its callers
    in the exe, or reached at an address holding other code) is placed where its bytes, relocations masked, occur
    exactly once in the exe's code (at least SEARCH_MIN bytes: a standalone copy units.json does not list).
    -> {msvc name: {target address: number of sites}}."""
    import struct
    import vslots
    p = os.path.join(objdir, 'u%d.obj' % i)
    if not os.path.exists(p) or not wanted:
        return {}
    c = vslots.Coff(p)
    funcs = {}   # defined code symbol -> (section, start, body)
    for name, si in c.defs.items():
        sy = c.syms[si]
        if sy.secnum > 0 and c.secs[sy.secnum].flags & IMAGE_SCN_CNT_CODE and sy.storage in (2, 3):
            ends = [v for v, _ in c.symbols_in(sy.secnum) if v > sy.value]
            data = c.section_bytes(sy.secnum)
            funcs[name] = (sy.secnum, sy.value, data[sy.value:min(ends) if ends else len(data)])
    todo = [(f['symbol'], int(f['address'], 16)) for f in unit['functions'] if f['symbol'] in funcs]
    placed = {}
    out = collections.defaultdict(collections.Counter)
    searched = set()
    while True:
        if not todo and search:   # standalone copies of the unplaced functions that reference a wanted name
            for fname, (sec, start, body) in sorted(funcs.items()):
                if fname in placed or fname in searched or len(body) < SEARCH_MIN:
                    continue
                rel = {off - start: v for off, v in c.relocs(sec).items() if 0 <= off - start < len(body)}
                if not any(sn in wanted and typ in (0x06, 0x14) for sn, typ in rel.values()):
                    continue
                searched.add(fname)
                a = exe.find_unique(body, {j for k in rel for j in range(k, min(k + 4, len(body)))})
                if a is not None:
                    todo.append((fname, a))
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
        if bytes(mask) != bytes(fixed):
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
    c = vslots.Coff(p)
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


def exe_pass(rows, exe_path, msvc_objs, units_json, unit_names, native, itanium, msvc, ms, providers=None):
    """`exe-addr`: a name still missing (or about to get a synthesized special member) whose MSVC counterpart the
    referencing unit's pristine object calls: bound to the address the exe's call at that site reaches, when every
    site agrees and the native build defines it with the right kind."""
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


def run(out, tree, msvc_objs=None, names_txt=None, units_json=None, jobs=4, synth=True, exe_path=None):
    import fnvlink
    units_json = units_json or os.path.join(tree, 'build', 'units.json')
    bdir = os.path.join(out, 'bodies')
    os.makedirs(bdir, exist_ok=True)
    os.makedirs(os.path.join(out, 'cache'), exist_ok=True)
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
    cl = Classifier(units, native, msvc, itanium, bodies, unit_names)
    rows = [cl.classify(n, us) for n, us in sorted(trapped.items())]

    if msvc_objs and exe_path:
        rows = exe_pass(rows, exe_path, msvc_objs, units_json, unit_names, native, itanium, msvc, ms,
                        load_providers(os.path.join(out, 'addrmap.tsv')))
    if synth:   # its stubs are emitted with the synthesized objects
        rows = gdel_pass(rows, units, native, itanium, msvc, ms, known=set(trapped) | allms_itanium(out))
        rows = crt_pass(rows, itanium)   # its stubs are emitted with the synthesized objects
        # variables declared and defined nowhere, without an exe address: a typed zero definition (decl-data)
        rows = [r._replace(cls='decl-data', host=sorted(r.units.split(','))[0],
                           note='zero-initialized definition of the declared variable (no exe address)')
                if r.cls == 'missing' and r.note.startswith('not a function') and r.units and
                data_line(itanium(r.name).qual) else r for r in rows]
        have_ = {r.name for r in rows}
        for n, us in sorted(load_cdata(os.path.join(out, 'refs.tsv')).items()):
            if n not in have_ and data_line([n]):
                rows.append(Row(n, 'decl-data', None, None, ','.join(sorted(us)),
                                'zero-initialized definition of the declared variable (no exe address)', sorted(us)[0]))

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
                        failed[n] = 'MSVC calls it directly (pristine reference)'
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
        vdone = set(vtargets) | set(failed) | set(vnotes)
        rest = [r for r in rows if r.cls == 'missing' and r.name not in vdone and r.units]
        tin = tree_inline_hosts(tree, rest, itanium, set(unit_names), load_layouts(out))
        tby = collections.defaultdict(list)
        for r in rest:
            if r.name in tin:
                tby[tin[r.name]].append(r)

        def tone(job):
            h, rs = job
            try:
                return h, synth_host(tree, work, h, rs, itanium, True)
            except Exception as e:
                return h, (None, set(), {r.name: 'synth-failed (%r)' % e for r in rs})
        tdone = {}
        with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
            for h, (path, roots, fail) in ex.map(tone, sorted(tby.items())):
                if path:
                    dst = os.path.join(bdir, 'zz_bd_%s_t.o' % h.replace('/', '_'))
                    os.replace(path, dst)
                    synth_rows.append((h, dst, len(roots)))
                    tdone.update({n: h for n in roots})
                for n, why in fail.items():
                    vnotes[n] = 'tree-inline in %s: %s' % (h, why)
        rows = [r._replace(cls='tree-inline', host=tdone[r.name], note='inline body in ' + tdone[r.name])
                if r.name in tdone else r for r in rows]
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
            if r.cls in ('addr', 'exe-addr', 'vcall', 'gdel', 'template-fold'):
                f.write('%s\t%s\t%s\n' % (r.name, r.cls, r.target))
    with open(os.path.join(bdir, 'synth.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#host_unit\tobject\tnames\n')
        for h, p, n in synth_rows:
            f.write('%s\t%s\t%d\n' % (h, p, n))
    st = collections.Counter(r.cls for r in rows)
    special = collections.Counter(r.cls for r in rows if itanium(r.name).variant)
    lines = ['names still undefined after resolve: %d (ctors/dtors %d)' % (len(rows), sum(special.values()))]
    lines += ['%-16s %6d  (ctors/dtors %d)' % (k, st[k], special[k])
              for k in ('addr', 'template-fold', 'exe-addr', 'gdel', 'vcall', 'msvc-empty', 'msvc-inline', 'tree-inline',
                        'exe-sret', 'thunk', 'addr-host', 'decl-data', 'special-default', 'crt-rule', 'missing')]
    lines.append('pristine MSVC objects: %s' % ('%d code COMDATs matched by token' % len(bodies) if bodies is not None
                                                 else 'not given (--msvc-objs)'))
    lines.append('synthesized objects: %d' % len(synth_rows))
    open(os.path.join(bdir, 'stats.txt'), 'w').write('\n'.join(lines) + '\n')
    print('\n'.join(lines), file=sys.stderr)
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
