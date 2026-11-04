"""slotfill2.py - S2c-vt2's additions to the exe slot fill (fnvlink.exe_slot_fill; docs/LINK.md "Vftables from the
player's exe"). fnvlink.py keeps a few hook lines; the rules live here so S2c-vt's and S2c-vt2's edits stay apart.

Rules:
* **Secondary-base entries** (`secondary_entry`): the exe slot of a secondary vftable (COL offset d > 0) holds an
  override that MSVC compiled with `this` at that base subobject (thisadj.py; the address map names its entry
  `__fnvnx_msvc_<a>`, an alias of GCC's `_ZThn<d>_` thunk). The native slot of a secondary vtable passes the
  subobject too, so binding it to `__fnv_<a>` (the full-object entry) hands the definition a `this` that is d bytes
  too high. When every vtable naming the slot has offset-to-top -d, the slot binds to `__fnvnx_msvc_<a>` instead.
* **Fastcall deleting destructors** (`fastcall_dtor`): GCC's D0 / D1 slot where the exe's ??_G is the decomp's
  `__fastcall f(this, int, flags)` goes through `__fnvnx_vflag3_<a>_<f>` (w2 = flags).
* **Enum parameters** (`enum_params`): the slot's Itanium parameter types that the MSVC side spells `enum X` are
  ints, not unknown types, for slot_abi and the x86 model.
* **Decomp constructors at an exe slot** (`slot_ctor`): an MSVC vftable never holds a constructor; a C1 there is a
  plain member the decomp named after its shell class (`FUN_00c66450::FUN_00c66450(char)`, an exe ??_G).
"""
import copy
import os
import re


def secondary_entry(target_for, native, addr_sym, entry_sym, stats=None):
    """Wrap exe_slot_fill's target_for(un, a, otts, depth): a direct binding `__fnv_<a>` of an address MSVC enters
    at a secondary base d becomes `__fnvnx_msvc_<a>` when the native vtables naming the slot sit at that base
    (every offset-to-top is -d). Other results are returned unchanged."""

    def wrapped(un, a, otts=None, depth=0):
        tgt, why = target_for(un, a, otts, depth)
        nd = native.get(a) if tgt == addr_sym % a else None
        me = nd.get('msvc_entry') if nd else None
        if me and otts and all(-o == me[1] for o in otts):
            if stats is not None:
                stats['secondary_entry'] += 1
            return entry_sym % a, '%s; msvc this = base subobject +%d: the _ZThn thunk' % (why, me[1])
        return tgt, why
    return wrapped


FLAG3_SYM = '__fnvnx_vflag3_%08x_%d'   # GCC destructor slot -> a decomp `__fastcall f(this, int edx, flags)` ??_G


def fastcall_flags_param(ds, classify):
    """The MSVC declaration is the decomp's fastcall spelling of a thiscall deleting destructor: a free `__fastcall`
    function (pointer, int, integer): ecx = this, edx unused, the flags on the stack (the third argument)."""
    if ds is None or ds.kind != 'func' or ds.raw_params is None or ds.member or ds.cc != 'fastcall':
        return False
    ps = [classify(p) for p in ds.raw_params if p != 'void']
    return len(ps) == 3 and ps[0] == 'P' and ps[1] in ('I8', 'I16', 'I32') and ps[2] in ('I8', 'I16', 'I32')


def emit_flag3_stub(name, target, flags):
    return '\n'.join(['    .section .text.%s,"ax",%%progbits' % name, '    .p2align 2', '    .globl %s' % name,
                      '    .hidden %s' % name, '    .type %s, %%function' % name, '%s:' % name,
                      '    mov w2, #%d' % flags, '    b %s' % target, '    .size %s, .-%s' % (name, name),
                      '    .text']) + '\n'


def fastcall_dtor(target_for, native, itanium, msvc, classify, stubs, addr_sym, stats=None):
    """Wrap target_for: GCC's D0 / D1 / D2 slot where the exe slot's function is the decomp's fastcall deleting
    destructor `f(this, int, flags)` (DestroyNiTriShape, NiMaterialProperty_Destructor) goes through a stub that
    passes the flags in the third argument register: 1 for D0 (delete), 0 for D1 / D2."""

    def wrapped(un, a, otts=None, depth=0):
        tgt, why = target_for(un, a, otts, depth)
        if tgt is not None or not why.startswith('kind differs (function at the exe slot, D'):
            return tgt, why
        nd = native.get(a)
        isig = itanium(un)
        ds = msvc(nd['msvc']) if nd and nd.get('msvc') else None
        if not (isig.variant or '').startswith('D') or not fastcall_flags_param(ds, classify):
            return tgt, why
        fl = 1 if isig.variant == 'D0' else 0
        name = FLAG3_SYM % (a, fl)
        stubs.setdefault(name, emit_flag3_stub(name, addr_sym % a, fl))
        if stats is not None:
            stats['fastcall_dtor'] += 1
        return name, 'fastcall flags stub (%d)' % fl
    return wrapped


ENUM_RE = re.compile(r'\benum ([A-Za-z_][\w:]*)')


def msvc_enums(out):
    """Names the MSVC side spells `enum X` (the demangle cache of resolve: cache/msvc.tsv)."""
    names = set()
    try:
        for line in open(os.path.join(out, 'cache', 'msvc.tsv'), encoding='utf-8', errors='surrogateescape'):
            names.update(ENUM_RE.findall(line))
    except OSError:
        pass
    return names


def enum_params(itanium, enums, stats=None):
    """Wrap the Itanium signature reader for the slot fill: a parameter whose type the MSVC side declares as an enum
    (`ActorValue::Index`, `ANIM_GROUP_ENUM`) is spelled `enum X`, so abi.classify gives it MSVC's and GCC's int
    (I32) instead of an unknown type. The Itanium name carries no class/enum keyword; without this the slot's
    enum arguments never match the definitions' `int` (slot_abi `aggregate or varargs argument n differs`)."""
    cache = {}

    def wrapped(n):
        if n in cache:
            return cache[n]
        s = itanium(n)
        ps = s.raw_params
        if ps and any(p in enums for p in ps):
            s = copy.copy(s)
            s.raw_params = ['enum ' + p if p in enums else p for p in ps]
            if s.params is not None:
                s.params = ['enum ' + p if p in enums else p for p in s.params]
            if stats is not None:
                stats['enum_param_sigs'] += 1
        cache[n] = s
        return s
    return wrapped


FLAGC_SYM = '__fnvnx_vflagc_%08x_%d'   # GCC destructor slot -> a decomp "constructor" (this, flags) at the exe slot


def emit_flagc_stub(name, target, flags):
    return '\n'.join(['    .section .text.%s,"ax",%%progbits' % name, '    .p2align 2', '    .globl %s' % name,
                      '    .hidden %s' % name, '    .type %s, %%function' % name, '%s:' % name,
                      '    mov w1, #%d' % flags, '    b %s' % target, '    .size %s, .-%s' % (name, name),
                      '    .text']) + '\n'


def slot_ctor(target_for, native, itanium, msvc, classify, slot_abi, stubs, addr_sym, stats=None):
    """Wrap target_for: an MSVC vftable slot never holds a constructor, so a C1 at the exe slot address is the
    decomp's naming of a plain member: a shell class named after the function (`FUN_00c66450::FUN_00c66450(char)`,
    the exe's ??_G taking the flags; `FUN_00d04300::FUN_00d04300(bool)`). GCC compiled it as a constructor, which
    takes this and the same parameters. A destructor slot (D0 / D1 / D2) binds through a stub passing the flags in
    w1 (1 for D0) when the "constructor" takes this and one integer; any other slot binds directly when slot_abi
    finds the arguments where the definition reads them."""
    ints = ('I8', 'I16', 'I32')

    def wrapped(un, a, otts=None, depth=0):
        tgt, why = target_for(un, a, otts, depth)
        if tgt is not None or not why.startswith('kind differs (C1 at the exe slot'):
            return tgt, why
        nd = native.get(a)
        ds = msvc(nd['msvc']) if nd and nd.get('msvc') else None
        if ds is None or ds.kind != 'func' or not ds.member or ds.static or ds.raw_params is None:
            return tgt, why
        isig = itanium(un)
        ps = [classify(p) for p in ds.raw_params if p != 'void']
        if (isig.variant or '').startswith('D'):
            if len(ps) != 1 or ps[0] not in ints:
                return tgt, why
            fl = 1 if isig.variant == 'D0' else 0
            name = FLAGC_SYM % (a, fl)
            stubs.setdefault(name, emit_flagc_stub(name, addr_sym % a, fl))
            if stats is not None:
                stats['slot_ctor_dtor'] += 1
            return name, 'decomp constructor at the exe slot: flags stub (%d)' % fl
        if isig.variant or isig.raw_params is None:
            return tgt, why
        how = slot_abi(isig, ds)
        if how in ('same', 'the slot passes more') or how.startswith('the definition reads more'):
            if stats is not None:
                stats['slot_ctor_plain'] += 1
            return addr_sym % a, 'decomp constructor at the exe slot: %s' % how
        return tgt, why
    return wrapped
