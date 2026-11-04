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
1b. `exe-addr` (`--exe`, the player's FalloutNV.exe) - the referencing unit's pristine object calls the MSVC name:
   every pristine object's calls of it (REL32 in functions placed by units.json, or at the address a placed
   function's call reaches, bytes checked against the exe) give one native address of the right kind and shape
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
5. `special-default` - a constructor without parameters, a copy constructor, a destructor or a copy assignment of
   a class (not a template) that no pristine object defines: MSVC inlined it everywhere, and the decomp declares it
   without a body. Synthesized as the out-of-line `= default` definition in the referencing unit (the implicit
   member's semantics: bases and members constructed / copied / destroyed, D0/D1/D2 all emitted). Not a decomp
   body: listed as such, and the MSVC code is the reference where a site misbehaves.
6. `missing` - the rest: real decomp gaps, for the real-exe path (S3), with the referencing units.

A synthesized item GCC refuses (member without default constructor, private base, ...) falls back to `missing`
with the reason `synth-failed`.

Output (`out/bodies/`, local: derived from the decomp, never committed): `bodies.tsv` (name, class, address,
target, referencing units, note), `bind.tsv` (name -> address symbol, read by the next `resolve`), `stats.txt`,
`synth.tsv` (host unit, object: linked by the next `resolve` as extra objects of the host, like
`vtables/synth.tsv`), `<unit>.bd.o`, `zz_bd_empty.o`.
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


# ---------------------------------------------------------------------------------------------------- synthesis

def emit_empty(rows):
    """AArch64 `ret` stubs (weak) for the msvc-empty names."""
    out = ['// GENERATED by tools/link/bodies.py: functions whose MSVC body does nothing', '    .text']
    for i, r in enumerate(sorted(rows, key=lambda r: r.name)):
        q = '"%s"' % r.name
        out += ['    .section .text.fnvnx_bd_empty.%d,"ax",%%progbits' % i, '    .p2align 2', '    .weak ' + q,
                '    .type %s, %%function' % q, q + ':', '    ret', '    .size %s, .-%s' % (q, q)]
    return '\n'.join(out) + '\n'


# compile.sh's code-generation flags (layout.flags has the semantics flags only): emitted bodies are game code
CODEGEN = ('-march=armv8-a+crc+crypto', '-mtune=cortex-a57', '-fno-stack-protector', '-fno-stack-clash-protection',
           '-mno-outline-atomics', '-U_FORTIFY_SOURCE')


def ftoi_plugin(bdir):
    """compile.sh's FTOI2 plugin (MSVC's float -> integer conversions): built like compile.sh builds it, no silent
    fallback (objects with and without it must never mix)."""
    port = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    r = subprocess.run(['bash', os.path.join(port, 'toolchain', 'ftoi', 'build.sh'), os.path.join(bdir, '.ftoi')],
                       capture_output=True, text=True)
    so = r.stdout.strip().splitlines()[-1] if r.returncode == 0 and r.stdout.strip() else ''
    if not so or not os.path.exists(so):
        raise SystemExit('bodies: the fnvnx_ftoi plugin does not build (%s); synthesis needs it, or --no-synth'
                         % (r.stderr.strip().splitlines() or ['?'])[-1])
    return so


def synth_host(tree, work, host, rows, itanium, inline=False, codegen=CODEGEN):
    """Compile the host unit again with the `= default` lines (special-default) or with -fkeep-inline-functions
    (msvc-inline); keep only the wanted symbols. -> (object path or None, {names defined}, {name: why failed})."""
    import vtables
    import layout
    want = {r.name for r in rows}
    items, failed = [], {}
    if not inline:
        for k, r in enumerate(sorted(rows, key=lambda r: r.name)):
            line = default_line(special_of(itanium(r.name)), itanium(r.name))
            if line in {it[2] for it in items}:
                continue
            items.append((k, 'bd', line))
    else:
        items.append((0, 'anchor', 'extern "C" void __fnvnx_bd_anchor() {}'))
    flags = ('-O2', '-ffunction-sections', '-fdata-sections', *layout.PIC, *codegen) + (
        ('-fkeep-inline-functions',) if inline else ())
    obj, alive = vtables.compile_probe(tree, work, host, items, 'bdi' if inline else 'bd', flags)
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
    obj, alive = vtables.compile_probe(tree, work, host, items, 'bdvc', ('-include', head))
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
        self.secs = []
        for k in range(nsec):
            o = pe + 24 + opt + 40 * k
            vsize, va, rsize, roff = struct.unpack_from('<IIII', d, o + 8)
            self.secs.append((self.base + va, max(vsize, rsize), roff, rsize))

    def read(self, va, n):
        for start, size, roff, rsize in self.secs:
            if start <= va and va + n <= start + min(size, rsize):
                return self.data[roff + va - start:roff + va - start + n]
        return None


def exe_call_targets(objdir, i, unit, wanted, exe):
    """Original addresses the pristine object of unit i calls the MSVC names in wanted at. Functions are placed
    by units.json, then by following REL32 calls from placed functions (a COMDAT units.json does not list, such as
    `NiPointer<T>::operator=`, at the address the exe's call reaches); a function counts as placed only where its
    x86 bytes equal the exe's outside relocations. At each REL32 relocation naming a wanted name in a placed
    function, the exe's displacement gives the target. -> {msvc name: {target address: number of sites}}."""
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
    while todo:
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
            if typ != 0x14 or k > len(body) - 4:   # IMAGE_REL_I386_REL32
                continue
            disp, = struct.unpack_from('<i', img, k)
            addend, = struct.unpack_from('<i', body, k)
            t = (a + k + 4 + disp - addend) & 0xffffffff
            if sn in wanted:
                out[sn][t] += 1
            elif sn in funcs and sn not in placed:
                todo.append((sn, t))
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


def exe_pass(rows, exe_path, msvc_objs, units_json, unit_names, native, itanium, msvc, ms):
    """`exe-addr`: a name still missing (or about to get a synthesized special member) whose MSVC counterpart the
    referencing unit's pristine object calls: bound to the address the exe's call at that site reaches, when every
    site agrees and the native build defines it with the right kind."""
    exe = Pe(exe_path)
    udata = json.load(open(units_json))['units']
    idx_of = collections.defaultdict(list)
    for i_, n_ in enumerate(unit_names):
        idx_of[n_].append(i_)
    cand = [r for r in rows if r.cls in ('missing', 'special-default') and r.units]
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

    def shape_ok(ref, a):
        """The MSVC definition at a and the pristine reference ref: as many parameters, both member functions or
        both not, the same calling convention (no ABI bridge is made for these bindings; resolve compares only the
        names it binds itself)."""
        rs = msvc(ref)
        if rs.kind != 'func' or rs.params is None:
            return False
        member = bool(rs.member)
        rparams = [p_ for p_ in rs.params if p_ != '...']
        for n_ in def_names.get(a, ()):
            d_ = msvc(n_) if n_.startswith('?') else None
            if d_ is None or d_.kind != 'func' or d_.params is None:
                continue
            dparams = [p_ for p_ in d_.params if p_ != '...']
            if len(dparams) == len(rparams) and bool(d_.member) == member and d_.cc == rs.cc:
                return True
        return False
    seen = collections.defaultdict(collections.Counter)   # Itanium name -> {address: sites}
    via = {}
    by_m = collections.defaultdict(set)   # MSVC name -> Itanium names it stands for
    for mp in pairs.values():
        for m_, ns_ in mp.items():
            by_m[m_] |= ns_
    # every pristine object: the referencing unit's own call is often in a COMDAT the exe dropped (MSVC inlined it
    # into callers, maybe in other units, whose calls of the same name the exe keeps)
    for i_ in range(len(udata)):
        for m_, tg in exe_call_targets(msvc_objs, i_, udata[i_], set(by_m), exe).items():
            for n in by_m[m_]:
                seen[n].update(tg)
                via.setdefault(n, m_)
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
        if isg.variant in ('C1', 'C2', 'D1', 'D2') and native.get(a) == 'F' and shape_ok(via[r.name], a):
            want = 'F'   # the decomp defines the constructor/destructor's address as a member function (FUN_...)
        if native.get(a) == want and want == 'F' and not shape_ok(via[r.name], a):
            out.append(r._replace(note=r.note + '; exe calls %08x (signature shape differs: %s)' % (
                a, ' / '.join(sorted(def_names.get(a, ()))[:2]))))
            continue
        if native.get(a) != want or isg.variant == 'D0':
            out.append(r._replace(note=r.note + '; exe calls %08x (%s)' % (
                a, 'native kind %s' % native[a] if a in native else 'not compiled natively')))
            continue
        out.append(r._replace(cls='exe-addr', addr=a, target=ADDR_SYM % a, host=None,
                              note='%s called at %d site(s) in the exe' % (via[r.name], nsite) +
                              (' (%s bound to the complete-object definition)' % isg.variant
                               if isg.variant in ('C2', 'D2') else '')))
    return out


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
        rows = exe_pass(rows, exe_path, msvc_objs, units_json, unit_names, native, itanium, msvc, ms)

    synth_rows, failed, vnotes = [], {}, {}
    if synth:
        work = os.path.join(bdir, 'work')
        os.makedirs(work, exist_ok=True)
        codegen = CODEGEN + ('-fplugin=' + ftoi_plugin(bdir),)
        by_host = collections.defaultdict(lambda: collections.defaultdict(list))
        for r in rows:
            if r.cls in ('special-default', 'msvc-inline') and r.host:
                by_host[r.host][r.cls == 'msvc-inline'].append(r)
        jobs_ = [(h, inl, rs) for h, d in sorted(by_host.items()) for inl, rs in sorted(d.items())]

        def one(job):
            h, inl, rs = job
            try:
                return h, inl, synth_host(tree, work, h, rs, itanium, inl, codegen)
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
            if r.cls in ('addr', 'exe-addr', 'vcall'):
                f.write('%s\t%s\t%s\n' % (r.name, r.cls, r.target))
    with open(os.path.join(bdir, 'synth.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#host_unit\tobject\tnames\n')
        for h, p, n in synth_rows:
            f.write('%s\t%s\t%d\n' % (h, p, n))
    st = collections.Counter(r.cls for r in rows)
    special = collections.Counter(r.cls for r in rows if itanium(r.name).variant)
    lines = ['names still undefined after resolve: %d (ctors/dtors %d)' % (len(rows), sum(special.values()))]
    lines += ['%-16s %6d  (ctors/dtors %d)' % (k, st[k], special[k])
              for k in ('addr', 'exe-addr', 'vcall', 'msvc-empty', 'msvc-inline', 'special-default', 'missing')]
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
