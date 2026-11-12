#!/usr/bin/env python3
"""equiv.py - S11: do the prepare rules keep program semantics? Build with MSVC 2008 x86 and compare per function.

The decomp is byte-matching MSVC 2008 x86-32 code. A prepare rule that claims to be x86-neutral (it only rewrites
spelling, never meaning) must leave every function compiling to the same code under that same compiler: same bytes,
same padding, every relocation resolving to the same original address. This tool builds the pristine decomp and a
prepared tree with a chosen set of rules under MSVC 2008 (through the decomp's own tools/msvc.py, Wine on Linux) and
compares them with the decomp's compare_builds.py fingerprint (its code is imported from the decomp checkout at run
time, nothing of it is copied here). See docs/EQUIVALENCE.md.

  equiv.py rules                                   every prepare rule: id, function, class (neutral/layout/platform)
  equiv.py prepare DECOMP OUT --rules SPEC         prepare.py with an allow-list (SPEC: ids and class names,
                                                   comma-separated, '-ID' removes: 'neutral', 'neutral,-EXC1', 'TAIL1')
  equiv.py plan DECOMP TREE --out units.json       units whose include closure changed (compare_builds.plan)
  equiv.py build DECOMP TREE --out FP.jsonl [--units all|FILE|i,j] [--jobs N] [--layout]
                                                   compile under MSVC, fingerprint every function (resumable JSONL)
  equiv.py compare A.jsonl B.jsonl [--out R.json]  per-unit / per-function differences

Builds use the same flags as the decomp's recipe plus `/I tools/equiv/msvc` (MSVC stand-ins for the fnv-nx shim
headers the rules include: the real SDK / CRT) and `/FI fnvnx_equiv_fi.h` (MSVC meaning of the macros rules insert),
on both sides. Object files go to a temporary folder and are deleted; results hold addresses and digests only.
"""
import argparse
import contextlib
import gzip
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading

HERE = Path(__file__).resolve().parent
PORT = HERE.parent.parent
SHIM = HERE / 'msvc'
FORCE_INCLUDE = SHIM / 'fnvnx_equiv_fi.h'          # both sides
COMPAT_INCLUDE = SHIM / 'fnvnx_equiv_compat.h'  # prepared trees only (stands in for fnvnx_compat.h)
CMATH = PORT / 'shim/include/fnvnx_cmath'           # DECL1's one-function C math headers (an _MSC_VER branch each)

# ---------------------------------------------------------------------------------------------------------- rules

# neutral   x86-neutral: claims to change spelling only; under MSVC x86 every function must stay identical.
# layout    changes a class / struct layout on purpose (or swaps in another definition of an SDK type); MSVC layouts
#           and code are compared before/after, a difference is expected where the layout really changes.
# platform  replaces x86/MSVC-only constructs by target ones (sse2neon, portable asm helpers, native renderer); no
#           x86 meaning to compare against, or the edited code is not compiled by MSVC at all.
CLASSES = {
    'SSE1': 'platform', 'SSE2': 'platform', 'SSE3': 'platform', 'SSE4': 'platform',
    'DECL1': 'neutral', 'NEW1': 'neutral',
    'UNIT1': 'neutral', 'KEEP1': 'neutral', 'IFX1': 'neutral', 'SAL1': 'neutral', 'CSX1': 'neutral',
    'DECL2': 'neutral', 'EXC1': 'neutral', 'NEW2': 'neutral', 'CTOR1': 'neutral', 'LINK1': 'neutral',
    'GOTO1': 'neutral', 'SDK1': 'layout', 'STR1': 'neutral', 'INTL1': 'platform', 'CALL1': 'neutral',
    'RCAST1': 'neutral', 'NEW3': 'neutral', 'TYPEID1': 'neutral', 'TAIL1': 'layout', 'EBO1': 'layout',
    'ASM1': 'platform', 'ASM2': 'platform', 'ASM4': 'platform',
    'NATIVE1': 'platform',      # drop_native_units in prepare.py (not a text rule)
    # classified by the orchestrator (J-0051) from each rule's docstring; the S11 gate confirms or refutes:
    'GAME1': 'neutral',         # renames a unit's own declaration + call sites; binding by address unchanged
    'COM1': 'neutral',          # renames a unit's own IUnknown copy (types only; the decomp's vtable slots kept)
    'SPEC1': 'neutral',         # early template<> declarations
    'STATIC1': 'neutral',       # internal -> external linkage of image-bound variables (MSVC may inline less)
    'VFTBL1': 'neutral',        # the decomp's stand-in table for an exe vftable becomes a declaration (`&name` is the same symbol)
    'XINC1': 'neutral',         # corrected caller declarations (S1b: 62/66 identical, 4 ecx reloads)
    'RET1': 'neutral',          # return <trailing stmt> where MSVC code is identical (S1b)
    'DBUG1': 'neutral',         # decomp-bug fixes per site (S1-crt 0x00b01510: identical)
    'MT1': 'platform',          # gated Switch behaviour changes (S15-threads mt_cull), behind FNVNX_MT1_ON; no MSVC parity
    'VA1': 'platform',          # MSVC char* va_list -> real va_list
    'FALL1': 'platform',        # __builtin_trap() at fall-off ends (GCC only)
    'DUPINT1': 'neutral',       # one of two integer-only-different prototypes of one address (same callee)
    'RREF1': 'neutral',         # fnvnx_lvalue around temporaries passed to non-const reference parameters
    'TPL1': 'neutral',          # this-> for members of dependent bases used by plain name (MSVC had no two-phase lookup)
    'TPL2': 'neutral',          # forward declarations of a namespace's function templates (MSVC's lookup)
    'TPL3': 'neutral',          # forward declarations of templates called qualified / with template args before definition
    'TYPN1': 'neutral',         # `typename` before a dependent library type member (MSVC took it as a type)
    'INST1': 'neutral',         # `unsigned int` -> C<..>::size_type in an explicit member instantiation (same member)
    'COPY1': 'neutral',         # B(o) -> B(static_cast<const B&>(o)) in a copy ctor: the copy ctor MSVC chose
    'DECL3': 'neutral',         # a unit's own `typedef _iobuf FILE` / `typedef __int64 fpos_t`: the MSVC types themselves
    'MATH1': 'neutral',         # drops the decomp's copies of MSVC <math.h> C++ overloads (the SDK header has them)
    'DTOR1': 'neutral',         # declares NiPointer<T>::~NiPointer() specializations for pointees the unit never completes
    'VTBL1': 'platform',        # full MSVC-order virtual interface for listed stand-ins (BSNiAllocator), slots forward
    'ALIGN1': 'neutral',       # __declspec(align(n)) moved behind the class key: the type's alignment on both compilers
    'ENUM2': 'neutral',        # enum with enumerators >= 2^31: MSVC's int base written out, (int) on those literals
    'VA2': 'neutral',          # va_arg(ap, Enum) -> (Enum)va_arg(ap, int): the int MSVC read
    'VLIT1': 'platform',       # a vptr store printed as a string literal -> the vftable address the recipe binds it to
    'VLIT2': 'platform',       # VLIT1 by the exe's vftables (private vlit.json): slot 0 at the store's COL offset, the class of the function's other vptrs
    'CSX2': 'neutral',          # the decomp's 28-byte critical sections keep their 28 bytes (D15 reopened, J-0067)
    'STAND1': 'platform',       # drops untagged stand-ins bound to a tagged definition's address (one function per address)
    'STAND2': 'platform',       # STAND1 for an address another unit defines, in units MSVC compiled without auto-inlining
    'STAND3': 'platform',       # STAND2 for in-class / inline stand-ins, in /Od units without /Ob1 (MSVC inlines nothing)
    'STAND5': 'platform',       # STAND2 for an in-class member body whose MSVC name the unit binds to another unit's address
    'STAND4': 'platform',       # STAND1/2 for a static free stand-in: the definition becomes an extern declaration of the bound name
    'PHSLOT1': 'platform',      # an empty `virtual void vNNN() {}` placeholder becomes declared-only (the exe slot fill binds it)
    'STAND6': 'platform',       # STAND2 for a __declspec(noinline) stand-in, in any unit (MSVC never inlined it)
    'ADDRFN1': 'platform',     # a code address cast to void* takes the address symbol __fnv_<a> (a GCC asm label)
    'POP2': 'neutral',          # POST hook: pop-mismatch chains, the words a getter leaves move to the call that pops them
    'THIS2': 'neutral',         # POST hook: a caller that hands its ecx to a thiscall callee calls it on its object
    'HARN1': 'platform',        # an untagged free harness definition no unit references gets internal linkage (static);
                               # not neutral: MSVC then drops the static recipe functions only a harness referenced
    'PTR1': 'neutral',          # ordered compare of a function / pointer with 0 -> the equality MSVC computed
    'INC1': 'neutral',          # quoted include spelled as the including directory's file (MSVC: no case)
    'ANON2': 'platform',        # raw storage for members with special functions in an anonymous struct of a union
    'ELAB1': 'neutral',         # `struct X` for type uses of a class named like a platform function
    'ENUM1': 'neutral',         # enum X; -> enum X : int; (+ its definitions): MSVC's underlying type spelled out
    'ANON1': 'platform',        # names an unnamed struct defined in a return type (MSVC decorates the name)
    'STL1': 'platform',        # fnvnx_msvcstl.h: MSVC <xutility> internals (_SCL_SECURE_VALIDATE, _Ptr_cat) for libstdc++
    'STL2': 'platform',        # fnvnx_vc9.h: VC9's containers in their x86 layout for units using real <string>/<vector>/<map>
    'STLB1': 'platform',      # VC9 helper template bodies (_Allocate, _Destroy, fill, swap, vector::_Xlen) an in-unit std copy declares only
    'STLB2': 'platform',      # VC9 bodies of an in-unit ifstream / vector<T> model's declared members (setstate, init, UseFacet, widen, open, vector ctor/dtor)
    'SPEC3': 'neutral',         # namespace qualification of names in specializations written inside a namespace
    'LEX1': 'neutral',          # MSVC token spellings (i64 suffixes, 0xae-0x34, "C"enum) -> standard ones
    'STD1': 'platform',         # a decomp `struct std { static float abs(float); }` -> <cmath>'s std::abs
    'SPEC2': 'neutral',         # template<> on member definitions of class template specializations
    'NEW4': 'neutral',          # adds <new> where placement new is used
    'ALLOC1': 'platform',       # drops untagged TU-local replacements of the global operator new/delete
    'FTOL1': 'platform',        # fnvnx_ftol2(expr): neutral with #define fnvnx_ftol2(x) ((__int64)(x)) under MSVC
    'BIND1': 'neutral',         # POST hook: units.json binds a stand-in member the unit uses to the exe's address
    'FRIEND1': 'neutral',       # `class X;` in front of a class whose `friend class X;` MSVC made visible
    'INIT1': 'platform',        # a static object whose initializer the exe has and the decomp lacks (NiMainSDM)
    'GOTO2': 'neutral',         # forward gotos across class / reference initializations: scope closed, copy or tail clone
    'QUAD1': 'neutral',         # `return q;` -> `return (__m128)q;`: the first of MSVC's two chained conversions spelled out
    'ANON3': 'platform',        # a function-local anonymous union with an anonymous struct -> local struct + member references
    'MSCV1': 'neutral',         # _MSC_FULL_VER / _MSC_VER in code -> the value of the unit's compiler (units.json)
    'RREF2': 'neutral',         # fnvnx_lvalue around T(args) passed to a member operator taking a non-const T&
    'ADDR1': 'neutral',         # &_mm_f(args) -> &fnvnx_lvalue(_mm_f(args)): the temporary MSVC took the address of
    'LANE1': 'platform',        # &((T*)&(v)) (SSE1 lane array, address taken) -> ((T(*)[n])&(v))
    'STD2': 'neutral',          # file-scope `R std::f(..)` definition -> fnvnx_std_f (+ its recipe symbol, POST hook)
    'COND1': 'neutral',         # `c ? false : f()` with f returning a bool gate class -> `c ? G(false) : f()`
    'RSTR1': 'neutral',         # restrict local passed to a const X& parameter -> fnvnx_unrestrict(v) ((v) under MSVC)
    'NEW5': 'neutral',          # FNVNX_GLOBAL_DELETE in a class whose only scalar delete is the placement form (MSVC: empty)
    'NSCALL1': 'neutral',       # unqualified call of N's own CRT-named prototype (bound only in N) -> N::name(
    'FINL1': 'neutral',         # __forceinline dropped from a body-less declaration the unit never defines (an ordinary call)
    'VFPAD1': 'layout',        # FNVNX_VFPAD(types) (empty under MSVC): MSVC pads a class's own vfptr to the class's alignment
    'EBO2': 'layout',          # FNVNX_EBO2 (empty under MSVC): an empty base of a class introducing a vfptr goes after the vptr
    'REC1': 'neutral',         # D20: reconstructed bodies from fnv-nx-build recon/ appended (new functions); REC1-REPL: a recon's
                               # exact-text replace pairs, applied before the append (part of REC1; each pair is the recon's
                               # accepted edit, gated with it)
    'HUNIT1': 'neutral',       # explicit instantiations of the template members a header unit's recipe lists (MSVC emitted them)
    'EBO3': 'layout',           # FNVNX_EBO2 (empty under MSVC) in an empty base after a base with data: after it, as MSVC
    'VORDER1': 'layout',        # R2: same-name virtual overloads in exe slot order (GCC lays them in declaration order, MSVC reversed)
    'IDTOR1': 'neutral',        # FNVNX_IDTOR1(C) (empty under MSVC): `~C();` declared where the recipe binds C's implicit dtor
    'D22': 'neutral',          # FNVNX_D22 / FNVNX_D22_DROP: GCC declares the slot-0 virtual destructor, MSVC keeps the original declaration
    'HKRO1': 'neutral',        # FNVNX_HKRO1 (empty under MSVC): hkReferencedObject's getClassType / calcContentStatistics under GCC
    'VSPLIT1': 'neutral',      # one declaration per virtual declarator (`virtual T a(), b();` -> two): the same declarations
    'EMPTY1': 'neutral',       # FNVNX_EMPTY1(T, name) (name under MSVC): a declared-only empty-string object -> T("") under GCC
    'RAWVT1': 'neutral',       # FNVNX_D26_SLOT(k) (k under MSVC): a raw exe slot index on `this`, k + 1 for GCC's minus-one vtables
    'DTORB1': 'neutral',       # FNVNX_DTORB_BEGIN/END (empty under MSVC): a destructor stand-in calls the address its ??1 is bound to
    'STAND7': 'neutral',       # #ifdef _MSC_VER around an inline out-of-class template member stand-in bound elsewhere (MSVC keeps it)
}
# rules that are not rule functions: prepare's NATIVE1 step and the stream modules' POST hooks that have a rule id
PSEUDO = {'NATIVE1': 'drop_native_units', 'BIND1': 'bind_stand_in_names', 'POP2': 'pop_chains',
          'THIS2': 'this_members'}
# POST hooks (prepare.STREAM_POST, by function name) -> the rule (or 's1b': S1b's units.json renames, which belong to
# its patches XINC1 / FTOL1 / RET1 / DBUG1) that must be allowed for the hook to run; other hooks always run (their
# stats then fail the allow-list check until they are classified here)
POST_RULES = {'bind_stand_in_names': 'BIND1', 'fix_units_json': 's1b', 'rename_std_definitions': 'STD2', 'add_recon_definitions': 'REC1',
              'pop_chains': 'POP2', 'this_members': 'THIS2', 'd22_write_applied': 'D22',
              'vorder_post_check': 'VORDER1'}
S1B = {'XINC1', 'FTOL1', 'RET1', 'DBUG1'}

# Leave-out groups for `attribute --hier --default-groups` (the gate's attribution): rules that interact sit in one
# group; a group that explains a difference is split into its rules. KEEP1 is inert under MSVC (FNVNX_KEEP is empty)
# and is not tried. A rule of the gated set in no group is tried as a group of its own.
DEFAULT_GROUPS = {
    'sdk': 'DECL1 DECL2 CALL1 GAME1 DUPINT1 MATH1 ELAB1 STD2 NSCALL1',
    'newdel': 'NEW1 NEW2 NEW3 NEW4 NEW5 CTOR1',
    'EXC1': 'EXC1',
    'link': 'LINK1 STATIC1 VFTBL1 BIND1',
    's1b': 'XINC1 RET1 FTOL1 DBUG1',
    'tpl': 'TPL1 TPL2 SPEC1 SPEC2 SPEC3 DTOR1',
    'lex': 'LEX1 PTR1 INC1 ENUM1 RREF1 RREF2 ADDR1 COND1 RSTR1 FINL1 RCAST1 GOTO1 GOTO2 QUAD1 MSCV1 STR1 IFX1 SAL1 TYPEID1 UNIT1 CSX1',
    'plat': 'STL1 STD1 ANON1 ANON2 ANON3',
}
RULE_ID = re.compile(r'\s*([A-Z]+[0-9]+)\b')


def load_prepare():
    sys.path.insert(0, str(PORT / 'tools'))
    import prepare
    return prepare


def rule_table():
    """[(id, function name, module, class, first docstring line)] in prepare's order, plus NATIVE1."""
    prepare = load_prepare()
    out = []
    for fn in prepare.RULES + prepare.load_stream_rules():
        doc = (fn.__doc__ or '').strip()
        m = RULE_ID.match(doc)
        rid = m.group(1) if m else '?' + fn.__name__
        out.append((rid, fn.__name__, fn.__module__.replace('fnvnx_rules_', 'rules/'), CLASSES.get(rid, '?'),
                    doc.split('\n')[0]))
    out.append(('NATIVE1', PSEUDO['NATIVE1'], 'prepare', CLASSES['NATIVE1'], 'units in render/native/drop_units.txt '
                'leave build/units.json'))
    hooks = {h.__name__: h for h in prepare.STREAM_POST}
    for rid, name in PSEUDO.items():
        if name in hooks:
            doc = (hooks[name].__doc__ or '').strip()
            out.append((rid, name, hooks[name].__module__.replace('fnvnx_rules_', 'rules/'), CLASSES.get(rid, '?'),
                        doc.split('\n')[0]))
    return out


def resolve_spec(spec):
    """'neutral,-EXC1,TAIL1' -> set of rule ids."""
    ids = {r[0] for r in rule_table()}
    allow = set()
    for part in filter(None, (p.strip() for p in spec.split(','))):
        neg = part.startswith('-')
        part = part.lstrip('-')
        if part in ('neutral', 'layout', 'platform', 'all'):
            sel = {i for i in ids if part == 'all' or CLASSES.get(i) == part}
        elif part in ids or part == 'none':
            sel = {part} if part != 'none' else set()
        else:
            raise SystemExit('equiv.py: unknown rule or class %r (known: %s)' % (part, ', '.join(sorted(ids))))
        allow = allow - sel if neg else allow | sel
    return allow


def cmd_rules(a):
    for rid, name, mod, cls, doc in rule_table():
        print('%-8s %-9s %-28s %-16s %s' % (rid, cls, name, mod, doc[:90]))


def cmd_prepare(a):
    """prepare.py with only the allowed rules (FNVNX_PREPARE_SKIP = every other rule function)."""
    allow = resolve_spec(a.rules)
    table = rule_table()
    skip = [name for rid, name, *_ in table if rid not in allow and rid not in PSEUDO]
    prepare = load_prepare()
    if 'NATIVE1' not in allow:
        prepare.drop_native_units = lambda *args, **kw: None
    load_stream = prepare.load_stream_rules

    def allowed_post():     # POST hooks of allowed rules only, each once (every load appends the modules' hooks)
        rules = load_stream()
        keep, seen = [], set()
        for h in prepare.STREAM_POST:
            rid, key = POST_RULES.get(h.__name__), (h.__module__, h.__name__)
            if key in seen or rid == 's1b' and not allow & S1B or rid not in (None, 's1b') and rid not in allow:
                continue
            seen.add(key)
            keep.append(h)
        prepare.STREAM_POST[:] = keep
        return rules
    prepare.load_stream_rules = allowed_post
    os.environ['FNVNX_PREPARE_SKIP'] = ','.join(skip)
    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    sys.argv = ['prepare.py', a.decomp, str(out)]
    prepare.main()
    # prepare copies src/, include/, build/ only; units also build from third_party/ (never rewritten)
    for d in sorted(Path(a.decomp).iterdir()):
        if d.is_dir() and not (out / d.name).exists() and not d.name.startswith('.'):
            shutil.copytree(d, out / d.name, symlinks=True)
    (out / 'equiv_rules.txt').write_text(','.join(sorted(allow)) + '\n')
    stats = (out / 'prepare_stats.tsv').read_text().split('\n')
    edited = {l.split('\t')[0] for l in stats if l.strip() and not l.split('\t')[2].startswith('0 ')}
    # counters that belong to a rule (ASM3 to ASM2/ASM4, <RULE>-MISS / -LEFT, S1b's units.json renames)
    own = {k for k in edited if k.split('-')[0] in allow or k == 'ASM3' and allow & {'ASM2', 'ASM4'}
           or k.startswith('UNITS-') and allow & S1B}
    stray = sorted(edited - allow - own)
    if stray:
        raise SystemExit('equiv.py: rules outside the allow-list edited the tree: %s' % ', '.join(stray))


# ---------------------------------------------------------------------------------------------------------- decomp tools

def decomp_tools(decomp):
    """compare_builds (fingerprint, plan), compare.Coff and build_and_match.recipe_names, from the decomp checkout."""
    sys.dont_write_bytecode = True          # the decomp checkout stays untouched (no __pycache__)
    sys.path.insert(0, str(Path(decomp, 'tools').resolve()))
    import compare_builds
    return compare_builds


def cmd_plan(a):
    cb = decomp_tools(a.decomp)
    data = cb.plan(a.decomp, a.tree, 10 ** 6, 1)
    out = dict(units=data['units'], code_changed=data['code_changed'], comment_only=data['comment_only'])
    Path(a.out).write_text(json.dumps(out, indent=0) + '\n')
    print('%d files with code changes, %d comment-only, %d units affected' % (
        len(data['code_changed']), len(data['comment_only']), len(data['units'])))


# ---------------------------------------------------------------------------------------------------------- build

# A data symbol with C linkage (_DAT_x) and the same variable with C++ linkage at global scope (?DAT_x@@3...) are one
# symbol under the Itanium ABI (LINK1) and resolve to the same address in the link-by-address step (D4).
CXX_GLOBAL_VAR = re.compile(r'^\?([A-Za-z_$][\w$]*)@@3')
C_NAME = re.compile(r'^_([A-Za-z_$][\w$]*)$')


def var_key(name):
    m = CXX_GLOBAL_VAR.match(name) or C_NAME.match(name)
    return m.group(1) if m else None


# An import slot by its function name: __imp__CreateFileA@28 (the SDK's C declaration) and __imp_?CreateFileA@@YG...
# (a C++ overload of the same import, tools/equiv/msvc/fnvnx_win32.h) are the same IAT entry.
IMP_C = re.compile(r'^__imp__([A-Za-z_]\w*?)(?:@\d+)?$')
IMP_CXX = re.compile(r'^__imp_\?([A-Za-z_]\w*)@@Y')


def import_key(name):
    m = IMP_CXX.match(name) or IMP_C.match(name)
    return ('imp', m.group(1)) if m else None


# MSVC decorates a class type with the key of its definition (U struct, V class, T union); the Itanium ABI does not
# (D1 target). The decomp's own `class _FILETIME` and the SDK's `struct _FILETIME` are the same type for the target.
CLASS_KEY = re.compile(r'(?<=[@A-DP-S0-9$])[UVT](?=[\w?$]+@)')


def key_free(name):
    return CLASS_KEY.sub('V', name)


SG_LITERAL = re.compile(r'^\$SG\d+$')


def literal_bytes(b):
    """A string literal's bytes up to the next symbol of its section, without the zero padding in front of that
    symbol: one terminator kept (`""` is b'\\0' whether 1 or 3 padding bytes follow it; MSVC places the next
    literal at an offset that depends on the unit's other literals)."""
    return b.rstrip(b'\0') + b'\0'
GAME_PREFIX = 'fnvnx_game_'


class Resolver:
    """Binds the undefined names an object refers to: the unit's own recipe names first (compare_builds), then any
    name the whole recipe binds to one address, then the other linkage of a global variable (var_key), another
    decoration of the same import (import_key), the same name up to class keys (key_free)."""

    def __init__(self, units, names):
        self.names = names
        self.by_key = {}
        for n, addr in names.items():
            for k in (var_key(n), import_key(n), ('key', key_free(n))):
                if k:
                    self.by_key.setdefault(k, set()).add(addr)

    def extend(self, coff, unit):
        """(unit with extra symbol bindings and function symbols found up to class keys, Counter of how). String
        literals ($SGnnnn) lose their recipe binding: MSVC numbers them with a counter that every literal parsed
        before them advances, including those of headers a rule adds, so they are compared by content (section
        bytes and offset, compare_builds' fallback) instead."""
        syms = {k: v for k, v in unit.get('symbols', {}).items() if not SG_LITERAL.match(k)}
        how = Counter()
        # a literal is its bytes: bind $SGnnnn to a digest of them (up to the next symbol of its section), so a
        # reference to the same literal matches wherever the literal sits in the unit's data section
        starts = {}
        for x in coff.symbols.values():
            if x['section'] > 0:
                starts.setdefault(x['section'], set()).add(x['value'])
        for x in coff.symbols.values():
            if x['section'] > 0 and SG_LITERAL.match(x['name']):
                data = coff.sections[x['section']]['data']
                end = min([v for v in starts[x['section']] if v > x['value']] or [len(data)])
                syms[x['name']] = hex(0x100000000 | int(hashlib.sha1(literal_bytes(data[x['value']:end])).hexdigest()[:8], 16))
                how['literal'] += 1
        defined = {}
        for s in coff.symbols.values():
            if s['section'] > 0 and s['name'].startswith('?'):
                defined.setdefault(key_free(s['name']), set()).add(s['name'])
        fns = []
        for fn in unit['functions']:
            if fn['symbol'] not in coff.by_name or not any(x['section'] > 0 for x in coff.by_name[fn['symbol']]):
                alt = defined.get(key_free(fn['symbol']), set())
                if len(alt) == 1:
                    fn = dict(fn, symbol=next(iter(alt))); how['rekeyed'] += 1
            fns.append(fn)
        own = set(syms)
        for fn in fns:
            own.update([fn['symbol']] + fn.get('fold_aliases', []))
        for sec in coff.sections[1:]:
            for _, ix, _ in sec['relocs']:
                s = coff.symbols[ix]
                n = s['name']
                if s['section'] != 0 or n in own or n in syms:
                    continue
                if n in self.names and not SG_LITERAL.match(n):
                    syms[n] = hex(self.names[n]); how['recipe'] += 1
                    continue
                if GAME_PREFIX in n:        # GAME1: fnvnx_game_X is the decomp's X (S2 binds it by the unit's address)
                    orig = n.replace(GAME_PREFIX, '')
                    if orig in syms or orig in self.names:
                        syms[n] = syms.get(orig) or hex(self.names[orig]); how['game'] += 1
                        continue
                    alts = {v for k, v in syms.items() if key_free(k) == key_free(orig)}   # the type's key changed
                    if len({int(v, 16) for v in alts}) == 1:
                        syms[n] = next(iter(alts)); how['game'] += 1
                        continue
                vk = var_key(n)
                if vk:
                    # the other linkage of a variable the unit itself binds: LINK1 names it by variable name inside
                    # this unit's link, so the unit's own address decides even where another unit binds the same
                    # name elsewhere (extradatalist.cpp: DAT_011977d8 at 0x011977d0)
                    mine = {v for k2, v in syms.items() if var_key(k2) == vk}
                    if len({int(v, 16) for v in mine}) == 1:
                        syms[n] = next(iter(mine)); how['linkage-unit'] += 1
                        continue
                for k, what in ((vk, 'linkage'), (import_key(n), 'import'), (('key', key_free(n)), 'key')):
                    if k and len(self.by_key.get(k, ())) == 1:
                        syms[n] = hex(next(iter(self.by_key[k]))); how[what] += 1
                        break
        # a variable this object defines under another linkage than the recipe's name of it (STATIC1 makes a static
        # `_s_x` an external `?s_x@@3...`): the recipe binds the image address, both spellings are that variable
        local = {}
        for k, v in syms.items():
            vk = var_key(k)
            if vk:
                local.setdefault(vk, set()).add(v)
        for x in coff.symbols.values():
            n = x['name']
            if x['section'] <= 0 or n in own or n in syms or SG_LITERAL.match(n):
                continue
            vk = var_key(n)
            if not vk:
                continue
            cand = local.get(vk) or {hex(a) for a in self.by_key.get(vk, ())}
            if len({int(c, 16) for c in cand}) == 1:
                syms[n] = next(iter(cand)); how['defined-linkage'] += 1
        ext = dict(unit)
        ext['symbols'] = syms
        ext['functions'] = fns
        return ext, how


CLASS_HEAD = re.compile(r'^class (.+?)\tsize\((\d+)\):$')


# cl writes its diagnostics into the same stream as the layout report, sometimes in the middle of a report line
DIAGNOSTIC = re.compile(r'[A-Za-z]:\\[^\n]*?\(\d+\) ?: (?:warning|error|fatal error|note)[^\n]*\n|'
                        r'[ \t]+[A-Za-z]:\\[^\n]*\n|'                    # "see declaration of ..." lines
                        r'[ \t]+(?:with|\[|\]|[A-Za-z_]\w*=[^\n]*)\n')    # template argument context


def layout_blocks(text):
    """/d1reportAllClassLayout output -> {class name: (size, layout text)} (vftable listings left out)."""
    out, cur, lines = {}, None, []
    text = DIAGNOSTIC.sub('', text.replace('\r', ''))
    for line in text.splitlines():
        line = line.rstrip()
        m = CLASS_HEAD.match(line)
        if not m:   # whitespace is not layout (and diagnostics removed above may have taken some of it)
            line = re.sub(r'\s*\|\s*', '|', re.sub(r'[ \t]+', ' ', line)).strip()
        if m:
            cur, lines = (m.group(1), int(m.group(2))), []
            continue
        if cur is None:
            continue
        if '|' not in line and '+---' not in line:
            continue        # not a layout line: what is left of a diagnostic
        lines.append(line)
        if line == '+---' and len(lines) > 1:
            out[cur[0]] = (cur[1], '\n'.join(lines))
            cur = None
    return out


def parse_layouts(text):
    """{class name: [size, digest of its layout]}"""
    return {k: [v[0], hashlib.sha256(v[1].encode()).hexdigest()[:16]] for k, v in layout_blocks(text).items()}


SDK_AW_MACRO = re.compile(r'^\s*#\s*define\s+([A-Za-z_]\w*)\s+\1[AW]\b', re.M)


def gen_dir():
    """Headers generated from the SDK in use (never committed: they list SDK names or paths): fnvnx_equiv_unaw.h,
    fnvnx_equiv_winerror.h, fnvnx_equiv_sdk_windows.h."""
    root = os.environ.get('FNV_MSVC9_ROOT')
    d = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'fnvnx-equiv'
    d.mkdir(parents=True, exist_ok=True)
    f = d / 'fnvnx_equiv_unaw.h'
    if root and not f.exists():
        names = set()
        for h in sorted(Path(root, 'SDK/Include').iterdir()):
            if h.suffix.lower() == '.h':
                names.update(SDK_AW_MACRO.findall(h.read_text(errors='replace')))
        body = ''.join('#undef %s\n' % n for n in sorted(names))
        f.write_text('/* generated by tools/equiv/equiv.py from %s/SDK/Include: the SDK\'s A/W function-name macros */\n'
                     % root + body)
    w = d / 'fnvnx_equiv_winerror.h'
    if root and not w.exists():
        src = next((h for h in Path(root, 'SDK/Include').iterdir() if h.name.lower() == 'winerror.h'), None)
        names = sorted(set(re.findall(r'^\s*#\s*define\s+(ERROR_\w+)\b(?!\()', src.read_text(errors='replace'), re.M))) \
            if src else []
        w.write_text('/* generated by tools/equiv/equiv.py from %s/SDK/Include/winerror.h: its ERROR_* codes, which the '
                     'target shim does not define */\n' % root + ''.join('#undef %s\n' % n for n in names))
    hide, unhide = d / 'fnvnx_equiv_sdkhide.h', d / 'fnvnx_equiv_sdkunhide.h'
    if root and not (hide.exists() and unhide.exists()):
        names = sorted(sdk_only_names(Path(root)))
        note = '/* generated by tools/equiv/equiv.py from %s: SDK-HIDE (sdk_only_names) */\n' % root
        tmp = hide.with_suffix('.tmp%d' % os.getpid())
        # a name already a macro is left alone; the marker lets unhide undefine only what hide defined
        tmp.write_text(note + ''.join('#ifndef %s\n#define %s fnvnx_sdk_%s\n#define FNVNX_HID_%s\n#endif\n' % (n, n, n, n)
                                      for n in names)); os.replace(tmp, hide)
        tmp = unhide.with_suffix('.tmp%d' % os.getpid())
        tmp.write_text(note + ''.join('#ifdef FNVNX_HID_%s\n#undef %s\n#undef FNVNX_HID_%s\n#endif\n' % (n, n, n)
                                      for n in names)); os.replace(tmp, unhide)
    k = d / 'fnvnx_equiv_sdk_windows.h'       # SDK-WINDOWS: tools/equiv/msvc/windows.h stands in for <windows.h>
    if root:
        inc = Path(root, 'SDK/Include')
        src = next((h for h in inc.iterdir() if h.name.lower() == 'windows.h'), None) if inc.is_dir() else None
        body = ('/* generated by tools/equiv/equiv.py: the SDK\'s own <windows.h>, by path (tools/equiv/msvc/windows.h '
                'takes its name on the include path) */\n#include "Z:%s"\n' % src) if src else '#error no SDK windows.h\n'
        if not k.exists() or k.read_text() != body:
            tmp = k.with_suffix('.tmp%d' % os.getpid()); tmp.write_text(body); os.replace(tmp, k)
    return d


CXX_WORDS = set('''alignas alignof and asm auto bool break case catch char char16_t char32_t class const const_cast
    constexpr continue decltype default delete do double dynamic_cast else enum explicit export extern false float for
    friend goto if inline int long mutable namespace new noexcept not nullptr operator or private protected public
    register reinterpret_cast return short signed sizeof static static_assert static_cast struct switch template this
    throw true try typedef typeid typename union unsigned using virtual void volatile wchar_t while xor'''.split())


# words of MSVC inline assembly (the SDK's own __asm blocks: winnt.h's Int64ShllMod32 `dword ptr`, `st`): never renamed
X86_ASM_WORDS = set('''eax ebx ecx edx esi edi ebp esp eip ax bx cx dx si di bp sp al ah bl bh cl ch dl dh cs ds es fs
    gs ss st byte word dword qword tbyte fword ptr offset short near far seg type length size mmword xmmword'''.split())


def sdk_only_names(root):
    """SDK-HIDE: the functions, typedef names, tags and enumerators the Windows SDK headers declare that the target shim never names
    (no word of shim/include), the VC headers (CRT, STL) do not use, and that are not C++ words. The gate's
    fnvnx_win32.h stand-in reads <windows.h> with each of them renamed to fnvnx_sdk_<name> and undefines the macros
    afterwards (a name that is already a macro is left alone; one the SDK #defines later replaces the rename, and
    the unhide list undefines it with the marker): the target has no such declaration, so a prepared unit cannot rely on one, and the decomp's own names
    (a struct Arc, DragObject; typedef PCH; gethostname) no longer collide with the full SDK's."""
    nocomment = lambda t: re.sub(r'/\*.*?\*/|//[^\n]*', ' ', t, flags=re.S)
    words = set()
    for f in (PORT / 'shim' / 'include').rglob('*.h'):    # code only: a name in a shim comment declares nothing
        words.update(re.findall(r'[A-Za-z_]\w*', nocomment(f.read_text(errors='replace'))))
    vc, sdk = Path(root, 'VC', 'include'), Path(root, 'SDK', 'Include')
    for f in vc.rglob('*.h') if vc.is_dir() else ():   # VC's C headers (CRT, sal, sourceannotations; the STL's
        t = f.read_text(errors='replace')               # names live in std), not the Windows headers VC ships beside
        if not re.search(r'#\s*include\s*<(?:windows|windef|ole2|winbase)\.h>', t, re.I):   # them (penwin.h: RECTL)
            words.update(re.findall(r'[A-Za-z_]\w*', nocomment(t)))
    fn = re.compile(r'\b(?:WINAPI|APIENTRY|WINAPIV|CALLBACK|STDAPI|WSAAPI|PASCAL|__stdcall|__cdecl|NTAPI)\s+'
                    r'(?:\w+\s+)*?([A-Za-z_]\w*)\s*\(')
    td = re.compile(r'\}\s*([A-Za-z_][\w\s,*]*);')    # every name between a closing brace and `;`
    td2 = re.compile(r'\btypedef\b[^;{}()]*?\b([A-Za-z_]\w*)\s*;')
    names = set()
    for h in sdk.iterdir() if sdk.is_dir() else ():
        if h.suffix.lower() != '.h':
            continue
        t = nocomment(h.read_text(errors='replace'))
        names.update(fn.findall(t))
        names.update(re.findall(r'\b(?:struct|union|enum)\s+(?:__declspec\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\{', t))   # tags
        for body in re.findall(r'\benum\b[^{};]*\{([^{}]*)\}', t):      # enumerators
            names.update(re.findall(r'(?:^|,)\s*([A-Za-z_]\w*)\s*(?==|,|$)', body.strip()))
        for m in td.findall(t):
            names.update(re.findall(r'[A-Za-z_]\w*', m))
        names.update(td2.findall(t))
    return {n for n in names if n not in words and n not in CXX_WORDS
            and n.lower() not in X86_ASM_WORDS}


_cmath_lock = threading.Lock()


def cmath_dir():
    """An include root that holds only the shim's fnvnx_cmath/ (DECL1 includes "fnvnx_cmath/floor.h" in place of a
    dropped C math prototype; each header has an _MSC_VER branch): the rest of shim/include must not shadow MSVC's
    CRT / SDK headers. A copy of the port's directory, kept equal to it (per port checkout, files replaced
    atomically)."""
    with _cmath_lock:
        d = gen_dir() / ('cmath-' + hashlib.sha1(str(PORT).encode()).hexdigest()[:12])
        dst = d / 'fnvnx_cmath'
        dst.mkdir(parents=True, exist_ok=True)
        want = {f.name: f.read_bytes() for f in CMATH.glob('*.h')} if CMATH.is_dir() else {}
        for f in dst.iterdir():
            if f.name not in want:
                f.unlink()
        for name, data in want.items():
            t = dst / name
            if not t.exists() or t.read_bytes() != data:
                tmp = dst / ('.%s.%d' % (name, os.getpid()))
                tmp.write_bytes(data)
                os.replace(tmp, t)
        return d


LAYOUT_LINE = re.compile(r'^\s*(\d*)\s*((?:\|\s*)+)(.*)$')   # on normalized lines: '20|name', '|+--- (base class X)'


def layout_shape(text):
    """A layout without member type names: offsets, nesting, member names, base-class markers. Two classes with the
    same shape put the same members at the same offsets (a member whose type was renamed keeps its shape)."""
    out = []
    for line in text.splitlines():
        m = LAYOUT_LINE.match(line)
        if not m:
            out.append(line.strip())
            continue
        rest = m.group(3).strip()
        if not rest.startswith(('+---', '<alignment', '{vfptr}', '{vbptr}')):
            rest = rest.split()[-1] if rest else rest
        out.append('%s %d %s' % (m.group(1), m.group(2).count('|'), rest))
    return '\n'.join(out)


def cmd_layouts(a):
    """MSVC class layouts of the same units in two layout builds (--layout --objdir): classes whose size, member
    offsets or member types differ, over all units (a class counts once, with the units it appears in)."""
    sel = select_units(a.units, json.loads(Path(a.decomp, 'build/units.json').read_text())['units'])
    seen, changed = {}, {}
    for i in sel:
        fa, fb = Path(a.a, 'u%d.layout.gz' % i), Path(a.b, 'u%d.layout.gz' % i)
        if not (fa.exists() and fb.exists()):
            continue
        la, lb = layout_blocks(gzip.open(fa, 'rt').read()), layout_blocks(gzip.open(fb, 'rt').read())
        for name in set(la) & set(lb):
            (sa, ta), (sb, tb) = la[name], lb[name]
            seen.setdefault(name, 0)
            seen[name] += 1
            if ta == tb:
                continue
            kind = 'size' if sa != sb else ('offsets' if layout_shape(ta) != layout_shape(tb) else 'types')
            c = changed.setdefault(name, dict(kind=kind, units=[], sizes=(sa, sb), a=ta, b=tb))
            c['units'].append(i)
    kinds = Counter(c['kind'] for c in changed.values())
    print('%d classes compared (%d class/unit pairs), %d differ: %s' % (len(seen), sum(seen.values()), len(changed),
                                                                         dict(kinds)))
    for name, c in sorted(changed.items(), key=lambda kv: (kv[1]['kind'], kv[0])):
        print('%-8s %-60s size %d -> %d, %d units' % (c['kind'], name[:60], c['sizes'][0], c['sizes'][1],
                                                     len(c['units'])))
    if a.show:
        import difflib
        for name in a.show.split(','):
            if name in changed:
                print('==== %s' % name)
                print('\n'.join(difflib.unified_diff(changed[name]['a'].splitlines(), changed[name]['b'].splitlines(),
                                                     'a', 'b', lineterm='', n=1)))
    if a.out:
        Path(a.out).write_text(json.dumps(dict(classes=len(seen), changed={k: dict(kind=v['kind'], sizes=v['sizes'],
                               units=v['units']) for k, v in changed.items()}), indent=0) + '\n')


def compile_unit(decomp, tree, unit, obj, layout, plain=False, eh_off=False, lean=False, variant=''):
    """variant: names a leave-out build (attribute) in the object cache, so it keeps its own manifest and does not
    replace the unit's head build there. SDK-LEAN: a prepared unit that the full <windows.h> breaks (names of
    winioctl / winsock / rpc / ole the target shim does not declare collide with the decomp's: RequestLocation,
    gethostname) is built again with WIN32_LEAN_AND_MEAN, nearer the target shim's subset; every caller (head,
    /EHs-c-, leave-out builds) makes the same choice, so their objects stay comparable."""
    ok, text = _compile_unit(decomp, tree, unit, obj, layout, plain, eh_off, lean, variant)
    if not ok and not plain and (tree / 'equiv_rules.txt').exists():
        # STANDIN-RETRY: stand-in variants for a unit that does not compile, in order (the same choice for every
        # caller): the variant its first error names, then SDK-HIDE, then lean, then both
        extra = [d for pat, d in STANDIN_RETRIES if re.search(pat, text)]
        hide = extra + ['/DFNVNX_EQUIV_SDKHIDE=1']
        tries = ([(lean, extra)] if extra else []) + [(lean, hide)] + ([(True, extra), (True, hide)] if not lean else [])
        for ln, ex in tries:
            ok2, text2 = _compile_unit(decomp, tree, unit, obj, layout, plain, eh_off, ln, variant, ex)
            if ok2:
                return ok2, text2
    return ok, text


# STANDIN-RETRY: (first-error pattern, /D of a gate stand-in variant). A unit that forward-declares the SDK's own
# `union _ULARGE_INTEGER;` meets the stand-in's class of that name (other units declare `class _ULARGE_INTEGER;`).
# A unit that includes <intrin.h> inside a namespace (hkadeltacompressedanimation's fnv_00e359f0) gets the barriers
# declared there too; MSVC 2008 takes them and the compat's global extern "C" ones for two functions (C2668). The
# retry includes <intrin.h> in the compat first (not for every unit: 140 of 382 intrinsic units break then).
STANDIN_RETRIES = [(r"'_ULARGE_INTEGER' : 'union' type redefinition", '/DFNVNX_EQUIV_ULARGE_UNION=1'),
                   (r"'_(?:ReadWrite|Read|Write)Barrier' : ambiguous call", '/DFNVNX_EQUIV_INTRIN=1')]


def _compile_unit(decomp, tree, unit, obj, layout, plain, eh_off, lean, variant, extra=()):
    args = [sys.executable, str(Path(decomp, 'tools/msvc.py').resolve()), '--compiler', unit['compiler'], '/c', '/nologo']
    flags = unit['flags']
    if eh_off:      # the same unit without C++ exception handling: separates EH-only differences from the rest
        flags = [f for f in flags if not f.upper().startswith('/EH')] + ['/EHs-c-']
    args += flags + ([] if plain else ['/I' + str(SHIM), '/I' + str(gen_dir()), '/FI', str(FORCE_INCLUDE)])
    if not plain and (tree / 'equiv_rules.txt').exists():      # a tree from `equiv.py prepare`
        args += ['/I' + str(cmath_dir()), '/FI', str(COMPAT_INCLUDE)] + (['/DFNVNX_EQUIV_LEAN=1'] if lean else []) \
            + list(extra)
    if layout:      # /w: no diagnostics interleaved with the report (no effect on code)
        args += ['/d1reportAllClassLayout', '/w']
    args += ['/I' + str(tree / 'include'), str(tree / unit['source']), '/Fo' + str(obj)]
    cache = obj_cache(decomp, tree)
    if cache:       # FNVNX_EQUIV_CACHE (tools/equiv/unitcache.py): the same inputs compiled before
        inc = [a[2:] for a in args if a.startswith('/I')] + [str((tree / unit['source']).parent)]
        key_args = [a for a in args if not a.startswith('/Fo')] + ['compiler=' + unit['compiler']]
        text = cache.lookup(unit['source'], key_args, inc, obj, variant)
        if text is not None:
            return True, text
        args = args[:-1] + ['/showIncludes', args[-1]]
    r = subprocess.run(args, cwd=tree, capture_output=True, text=True, errors='replace')
    ok = r.returncode == 0 and obj.exists()
    text = r.stdout + r.stderr
    if cache:
        if ok:
            text = cache.store(unit['source'], key_args, inc, tree / unit['source'], text, obj, variant)
        else:
            text = '\n'.join(l for l in text.splitlines() if not l.strip().startswith('Note: including file:'))
    return ok, text


_CACHES = {}


def obj_cache(decomp, tree):
    """unitcache.Cache for this tree when FNVNX_EQUIV_CACHE is set (one per tree and process), else None"""
    if not os.environ.get('FNVNX_EQUIV_CACHE'):
        return None
    k = str(tree)
    with _CACHE_LOCK:
        return _CACHES[k] if k in _CACHES else _new_cache(decomp, tree, k)


_CACHE_LOCK = threading.Lock()


def _new_cache(decomp, tree, k):
    import importlib.util
    import unitcache
    spec = importlib.util.spec_from_file_location('fnvnx_msvc_wrapper', str(Path(decomp, 'tools/msvc.py')))
    msvc = importlib.util.module_from_spec(spec); spec.loader.exec_module(msvc)
    _CACHES[k] = unitcache.Cache(os.environ['FNVNX_EQUIV_CACHE'], [('<tree>', str(Path(tree).resolve())),
                                 ('<decomp>', str(Path(decomp).resolve())), ('<port>', str(HERE.parent.parent)),
                                 ('<cmath>', str(cmath_dir())), ('<gen>', str(gen_dir()))],   # cmath: per port path
                                 [str(msvc.compiler_root(c) / 'VC/bin' / f) for c in ('sp1', 'rtm')
                                  if os.environ.get(msvc.ROOTS[c]) for f in ('cl.exe', 'c1.dll', 'c1xx.dll', 'c2.dll')])
    return _CACHES[k]


def shard_of(source, n):
    """1-based shard of a unit source among n (crc32 of the path, as tools/farm/compile_job.sh)."""
    import zlib
    return zlib.crc32(source.encode()) % n + 1


def select_units(spec, units, only=None):
    """'all', a JSON file ({"units": [...]} or a list), 'i,j,k', or 'shard:K/N' (stable hash split)."""
    if spec.startswith('shard:'):
        k, n = (int(x) for x in spec[6:].split('/'))
        sel = [i for i, u in enumerate(units) if shard_of(u['source'], n) == k]
    elif spec == 'all':
        sel = list(range(len(units)))
    elif os.path.exists(spec):
        data = json.loads(Path(spec).read_text())
        sel = data['units'] if isinstance(data, dict) else data
    else:
        sel = [int(x) for x in spec.split(',')]
    return [i for i in sel if not only or only in units[i]['source']]


class Fingerprinter:
    """compare_builds.fingerprint with the decomp's names (strict) and with Resolver's extra bindings."""

    def __init__(self, decomp, tree=None):
        self.cb = decomp_tools(decomp)
        from compare import Coff
        from build_and_match import recipe_names
        self.Coff = Coff
        self.units = json.loads(Path(decomp, 'build/units.json').read_text())['units']   # pristine recipe, both sides
        self.names = recipe_names(self.units)
        self.resolver = Resolver(self.units, self.names)
        self.names_nolit = {k: v for k, v in self.names.items() if not SG_LITERAL.match(k)}
        # a prepared tree's own recipe (S1b renames decorations of patched definitions and references): same units,
        # same addresses, other names - used where the pristine name is not in the object
        self.alt = None
        if tree is not None and Path(tree, 'equiv_rules.txt').exists() and Path(tree, 'build/units.json').exists():
            alt = json.loads(Path(tree, 'build/units.json').read_text())['units']
            if len(alt) == len(self.units) and alt != self.units:
                self.alt = alt

    def unit_for(self, i, coff):
        """The pristine recipe entry of unit i, with the prepared recipe's names where only those are in the object."""
        unit = self.units[i]
        if self.alt is None or self.alt[i] == unit:
            return unit
        alt = self.alt[i]
        by_addr = {f['address']: f for f in alt['functions']}
        fns = []
        for fn in unit['functions']:
            other = by_addr.get(fn['address'])
            if other and other['symbol'] != fn['symbol'] and fn['symbol'] not in coff.by_name \
                    and other['symbol'] in coff.by_name:
                fn = dict(fn, symbol=other['symbol'])
            fns.append(fn)
        syms = dict(alt.get('symbols', {}))
        syms.update(unit.get('symbols', {}))
        return dict(unit, functions=fns, symbols=syms)

    def row(self, i, obj):
        coff = self.Coff(obj)
        unit = self.unit_for(i, coff)
        ext, how = self.resolver.extend(coff, unit)
        strict = self.cb.fingerprint(obj, unit, self.names)
        fp = self.cb.fingerprint(obj, ext, self.names_nolit)
        fns = {}
        for k, v in fp.items():
            if k != 'eh':
                fns[k] = [v[0][:24], v[1][:24] if v[1] else None, v[2], strict[k][0][:24]]
        targets = self.targets(coff, ext)
        for k, t in targets.items():
            if k in fns:
                fns[k].append(t)
        return dict(status='ok', functions=fns, eh=fp.get('eh', '')[:24], eh_strict=strict.get('eh', '')[:24],
                    resolved=dict(how))

    @staticmethod
    def targets(coff, ext):
        """address -> [[offset, symbol name, bound address or None]] for every relocation to an undefined symbol in
        the function (what a call or data reference reaches; for reports, the digests decide), and [offset, name,
        LOCAL] for one to a C++ function the object defines itself (an inline overload of MSVC's headers)."""
        out = {}
        syms = ext.get('symbols', {})
        for fn in ext['functions']:
            try:
                s = coff.named(fn['symbol'])
            except ValueError:
                continue
            sec = coff.sections[s['section']]
            start = s['value']
            later = [x['value'] for x in coff.symbols.values() if x['section'] == s['section'] and x['type'] & 0x20
                     and x['value'] > start and not x['name'].startswith('__catch$')]
            end = min(later) if later else len(sec['data'])
            rows = []
            for o, ix, _ in sec['relocs']:
                x = coff.symbols[ix]
                if start <= o < end and x['section'] == 0:
                    rows.append([o - start, x['name'], syms.get(x['name'])])
                elif start <= o < end and x['type'] & 0x20 and x['name'].startswith('?'):
                    rows.append([o - start, x['name'], LOCAL])   # a C++ function the object defines (inline)
            out[fn['address']] = rows
        return out


def append_rows(out, rows_iter, total):
    lock, count = threading.Lock(), Counter()

    def put(row):
        with lock:
            count[row['status']] += 1
            with open(out, 'a') as f:
                f.write(json.dumps(row) + '\n')
            n = sum(count.values())
            if n % 50 == 0 or n == total:
                print('%d/%d %s' % (n, total, dict(count)), flush=True)
    return put


def cmd_build(a):
    fpr = Fingerprinter(a.decomp, a.tree)
    units = fpr.units
    tree = Path(a.tree).resolve()
    sel = select_units(a.units, units, a.only)
    out = Path(a.out)
    done = set(load_rows(out)) if out.exists() else set()
    todo = [i for i in sel if i not in done]
    print('%d units selected, %d already done, %d to build' % (len(sel), len(sel) - len(todo), len(todo)), flush=True)
    objdir = Path(a.objdir).resolve() if a.objdir else Path(tempfile.mkdtemp(prefix='fnvnx-equiv-'))
    objdir.mkdir(parents=True, exist_ok=True)
    put = append_rows(out, None, len(todo))

    def one(i):
        unit = units[i]
        obj = objdir / ('u%d.obj' % i)
        row = dict(unit=i, source=unit['source'], compiler=unit['compiler'])
        try:
            ok, text = compile_unit(a.decomp, tree, unit, obj, a.layout, a.plain, a.eh_off, a.lean)
            if not ok:
                row.update(status='compile-failed', error=text[-4000:])
                obj.unlink(missing_ok=True)
            else:
                row.update(fpr.row(i, obj))
                if a.layout:
                    row['layouts'] = parse_layouts(text)
                    if a.objdir:
                        with gzip.open(objdir / ('u%d.layout.gz' % i), 'wt') as f:
                            f.write(text)
        except Exception as e:
            row.update(status='tool-error', error='%s: %s' % (type(e).__name__, e))
        finally:
            if not a.objdir:
                obj.unlink(missing_ok=True)
        put(row)

    try:
        with ThreadPoolExecutor(a.jobs) as pool:
            list(pool.map(one, todo))
    finally:
        if not a.objdir:
            shutil.rmtree(objdir, ignore_errors=True)


def cmd_refp(a):
    """Fingerprint again from the objects a build kept (--objdir), e.g. after a change to Resolver: rows of units that
    did not compile are copied over. OLD '-' (or a missing file): the rows come from the objects alone, for --units
    (default all): a unit without an object did not compile."""
    fpr = Fingerprinter(a.decomp, a.tree)
    old = load_rows(a.old) if a.old != '-' and Path(a.old).exists() else {}
    spec = a.units or (None if old else 'all')
    if spec:
        keep = set(select_units(spec, fpr.units))
        old = {i: r for i, r in old.items() if i in keep}
        for i in sorted(keep - set(old)):
            u = fpr.units[i]
            ok = Path(a.objdir, 'u%d.obj' % i).exists()
            old[i] = dict(unit=i, source=u['source'], compiler=u['compiler'], status='ok' if ok else 'compile-failed',
                          **({} if ok else {'error': 'no object'}))
    out = Path(a.out)
    out.unlink(missing_ok=True)
    put = append_rows(out, None, len(old))
    for i, r in sorted(old.items()):
        obj = Path(a.objdir, 'u%d.obj' % i)
        if r['status'] == 'ok' and obj.exists():
            new = dict(unit=i, source=r['source'], compiler=r['compiler'])
            try:
                new.update(fpr.row(i, obj))
            except Exception as e:
                new.update(status='tool-error', error='%s: %s' % (type(e).__name__, e))
            if 'layouts' in r:
                new['layouts'] = r['layouts']
            r = new
        put(r)


# ---------------------------------------------------------------------------------------------------------- compare

def load_rows(path):
    rows = {}
    for line in Path(path).read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r['unit']] = r
    return rows


def compare_rows(ra, rb):
    """One unit: dict(status, differ=[addresses], same_bytes=[...], strict_only=[...], eh, layouts)."""
    if ra['status'] != 'ok':
        return dict(status='base-failed')
    if rb['status'] != 'ok':
        return dict(status='does-not-compile', error=rb.get('error', '')[-1500:])
    fa, fb = ra['functions'], rb['functions']
    keys = sorted(set(fa) | set(fb))
    differ = [k for k in keys if fa.get(k, [None])[0] != fb.get(k, [None])[0]]
    strict = [k for k in keys if fa.get(k, [0, 0, 0, None])[3] != fb.get(k, [0, 0, 0, None])[3]]
    out = dict(status='identical' if not differ and ra.get('eh') == rb.get('eh') else 'differs', differ=differ,
               functions=len(keys),
               missing=[k for k in differ if fb.get(k, [None])[0] == 'missing' or k not in fb],
               same_bytes=[k for k in differ if fa.get(k, [0, 1])[1] == fb.get(k, [0, 2])[1]],
               strict_only=[k for k in strict if k not in differ],
               eh_differs=ra.get('eh') != rb.get('eh'),
               unbound=sorted({n for k in differ for n in fb.get(k, [0, 0, []])[2]})[:20])
    # same code bytes, other callee / data: which relocations reach another address
    retargets = {}
    num = lambda v: int(v, 16) if isinstance(v, str) else v
    for k in out['same_bytes']:
        ta = {o: (n, num(v)) for o, n, v in (fa[k][4] if len(fa[k]) > 4 else [])}
        tb = {o: (n, num(v)) for o, n, v in (fb[k][4] if len(fb[k]) > 4 else [])}
        hexa = lambda v: '0x%08x' % v if isinstance(v, int) else v
        rt = []
        for o in sorted(set(ta) | set(tb)):
            (na, va), (nb, vb) = ta.get(o, (None, None)), tb.get(o, (None, None))
            if va != vb or (va is None and na != nb):
                rt.append([o, na, hexa(va), nb, hexa(vb)])
        if rt:
            retargets[k] = rt
    out['retargets'] = retargets
    la, lb = ra.get('layouts'), rb.get('layouts')
    if la is not None and lb is not None:
        changed = sorted(c for c in set(la) & set(lb) if la[c][1] != lb[c][1])
        out['layouts'] = dict(classes=len(set(la) & set(lb)), changed=changed,
                              size_changed=[c for c in changed if la[c][0] != lb[c][0]],
                              only_a=len(set(la) - set(lb)), only_b=len(set(lb) - set(la)))
    return out


def cmd_compare(a):
    A, B = load_rows(a.a), load_rows(a.b)
    common = sorted(set(A) & set(B))
    res = {}
    st = Counter()
    fcount = Counter()
    for i in common:
        c = compare_rows(A[i], B[i])
        c['source'] = A[i]['source']
        res[i] = c
        st[c['status']] += 1
        if c['status'] in ('identical', 'differs'):
            fcount['functions'] += c['functions']
            fcount['differ'] += len(c['differ'])
            fcount['same_bytes'] += len(c['same_bytes'])
            fcount['missing'] += len(c['missing'])
            fcount['strict_only'] += len(c['strict_only'])
            fcount['eh_differs'] += c['eh_differs']
            if 'layouts' in c:
                fcount['classes'] += c['layouts']['classes']
                fcount['classes_changed'] += len(c['layouts']['changed'])
    summary = dict(units=len(common), status=dict(st), **fcount)
    if a.out:
        Path(a.out).write_text(json.dumps(dict(summary=summary, units=res), indent=0) + '\n')
    print(json.dumps(summary))
    for i in common:
        c = res[i]
        if c['status'] == 'differs':
            print('differs  %-70s %d/%d fn%s%s' % (c['source'], len(c['differ']), c['functions'],
                  ' eh' if c['eh_differs'] else '', (' layouts %d' % len(c['layouts']['changed'])) if c.get('layouts')
                  and c['layouts']['changed'] else ''))
        elif c['status'] not in ('identical',):
            print('%-8s %s' % (c['status'], c['source']))


# ---------------------------------------------------------------------------------------------------------- attribute

_ALL_RULES = []


def rule_functions(allow):
    """prepare's rule functions in prepare's order, only the allowed ids (stream modules loaded once)."""
    if not _ALL_RULES:
        prepare = load_prepare()
        table = {name: rid for rid, name, *_ in rule_table()}
        _ALL_RULES.extend((table.get(fn.__name__), fn) for fn in prepare.RULES + prepare.load_stream_rules())
    return [fn for rid, fn in _ALL_RULES if rid in allow]


def prepared_text(decomp, rel, rules):
    """The file `rel` of the decomp as prepare.py writes it with `rules` (rules are per file: fn(path, text))."""
    src = Path(decomp, rel)
    with open(src, encoding='utf-8', errors='surrogateescape') as f:
        text = f.read()
    stats = Counter()
    for rule in rules:
        text = rule(str(src), text, stats)
    return text


def overlay_tree(base, out, decomp, files, rules):
    """A copy of tree `base` (hard links) where `files` are re-prepared from the decomp with `rules`."""
    if out.exists():
        shutil.rmtree(out)
    subprocess.run(['cp', '-al', str(base), str(out)], check=True)
    changed = 0
    for rel in files:
        if not rel.endswith(('.cpp', '.h', '.hpp', '.inl', '.c')) or not rel.startswith(('src/', 'include/')):
            continue
        text = prepared_text(decomp, rel, rules)
        dst = out / rel
        old = dst.read_text(encoding='utf-8', errors='surrogateescape')
        if old != text:
            dst.unlink()                          # never write through a hard link into `base`
            with open(dst, 'w', encoding='utf-8', errors='surrogateescape') as f:
                f.write(text)
            changed += 1
    return changed


class WorkerTrees:
    """Hard-linked copies of tree `base` under `work`, one per worker thread, made once and reused: a leave-out build
    swaps its files in (gzip files, as attribute's cache writes them) and links the originals back afterwards, so an
    attribution holds at most --jobs trees (b727d428: one tree per unit and candidate filled the farm's disk)."""

    def __init__(self, base, work):
        self.base, self.work = Path(base), Path(work)
        self.local, self.lock, self.n, self.stamp = threading.local(), threading.Lock(), 0, 10 ** 9

    def slot(self):
        if not hasattr(self.local, 'tree'):
            with self.lock:
                self.n += 1
                mine = self.work / ('w%d' % self.n)
            mine.mkdir(parents=True)
            subprocess.run(['cp', '-al', str(self.base), str(mine / 'tree')], check=True)
            self.local.mine, self.local.tree = mine, mine / 'tree'
        return self.local.mine, self.local.tree

    @contextlib.contextmanager
    def swapped(self, tree, touched):
        put = []
        try:
            for f, p in touched.items():
                dst = tree / f
                dst.unlink()                      # never write through a hard link into `base`
                put.append(f)
                with gzip.open(p, 'rb') as src, open(dst, 'wb') as h:
                    shutil.copyfileobj(src, h)
                with self.lock:                   # a unique mtime: unitcache memoizes hashes by (path, size, mtime)
                    self.stamp += 1
                    t = self.stamp
                os.utime(dst, ns=(t, t))
            yield
        finally:
            for f in put:
                dst = tree / f
                if dst.exists():
                    dst.unlink()
                os.link(self.base / f, dst)


def cmd_attribute(a):
    """For every unit that differs between A (pristine) and B (prepared with SPEC), and with --failed every unit that
    B does not compile: rebuild it with SPEC minus one rule (and minus each --group), for each rule that edits a file
    in the unit's include closure. A differing function that becomes identical to A when rule R is left out is
    attributed to R; a unit that compiles without R did not compile because of R."""
    fpr = Fingerprinter(a.decomp, a.tree)
    cb, units = fpr.cb, fpr.units
    A, B = load_rows(a.a), load_rows(a.b)
    allow = resolve_spec(a.rules)
    full = rule_functions(allow)
    files = cb.tree_files(a.decomp)
    graph = cb.include_graph(a.decomp, files)
    todo = []
    for i in sorted(set(A) & set(B)):
        c = compare_rows(A[i], B[i])
        if (c['status'] == 'differs' or (a.failed and c['status'] == 'does-not-compile')) \
                and (not a.only or a.only in units[i]['source']):
            todo.append((i, c))
    if a.units:
        keep = set(select_units(a.units, units))
        todo = [(i, c) for i, c in todo if i in keep]
    if a.limit:
        todo = todo[:a.limit]
    cands = [] if a.hier else [(rid, {rid}) for rid in sorted(allow)]
    groups = list(a.group or [])
    if a.default_groups:
        grouped = set()
        for gname, ids in DEFAULT_GROUPS.items():
            groups.append('%s=%s' % (gname, ','.join(ids.split())))
            grouped.update(ids.split())
        groups += ['%s=%s' % (r, r) for r in sorted(allow - grouped - {'KEEP1', 'NATIVE1'})]
    for g in groups:
        gname, _, ids = g.partition('=')
        cands.append((gname, set(ids.split(',')) & allow))
    base = Path(a.tree).resolve()
    work = Path(tempfile.mkdtemp(prefix='fnvnx-equiv-attr-', dir=a.workdir))
    out = Path(a.out)
    done = set(load_rows(out)) if out.exists() else set()
    todo = [(i, c) for i, c in todo if i not in done]
    print('%d units to attribute' % len(todo), flush=True)
    lock = threading.Lock()
    edits = {}            # (file, candidate) -> path of the file prepared without the candidate, or None if the same

    # T1: FNVNX_EQUIV_REDUCED=<dir>: the prepared texts without a candidate, kept between runs. tools/farm/equiv_job.sh
    # names the directory after the sha256 of the traced record of the full prepare (every rule, equiv.py with its
    # groups, every decomp file it read): the same record, the same texts; another record, another directory.
    keep_dir = os.environ.get('FNVNX_EQUIV_REDUCED')

    def reduced(f, rid, ids):
        key = (f, rid)
        with lock:
            if key in edits:
                return edits[key]
        kp = Path(keep_dir) / rid / (f + '.gz') if keep_dir else None
        text = None
        if kp is not None and kp.is_file():
            try:
                with gzip.open(kp, 'rt', encoding='utf-8', errors='surrogateescape') as h:
                    text = h.read()
            except (OSError, EOFError):
                text = None
        if text is None:
            drop = {fn.__name__ for fn in rule_functions(ids)}
            rules = [fn for fn in full if fn.__name__ not in drop]
            text = prepared_text(a.decomp, f, rules)
            if kp is not None:
                kp.parent.mkdir(parents=True, exist_ok=True)
                tmp = kp.with_name(kp.name + '.tmp%d.%d' % (os.getpid(), threading.get_ident()))
                with gzip.open(tmp, 'wt', encoding='utf-8', errors='surrogateescape', compresslevel=3) as h:
                    h.write(text)
                os.replace(tmp, kp)
        path = None
        if text != (base / f).read_text(encoding='utf-8', errors='surrogateescape'):
            if kp is not None:
                path = kp                                  # the kept file is the same gzip text
            else:
                path = work / 'cache' / rid / (f + '.gz')  # compressed: the cache spans every rule x header of the shard
                path.parent.mkdir(parents=True, exist_ok=True)
                with gzip.open(path, 'wt', encoding='utf-8', errors='surrogateescape', compresslevel=3) as h:
                    h.write(text)
        with lock:
            edits[key] = path
        return path

    trees = WorkerTrees(base, work)

    def one(item):
        i, c = item
        unit = units[i]
        closure = [f for f in sorted(cb.closure(graph, unit['source']))
                   if f.endswith(('.cpp', '.h', '.hpp', '.inl', '.c')) and f.startswith(('src/', 'include/'))]
        row = dict(unit=i, source=unit['source'], status=c['status'], differ=c.get('differ', []),
                   eh_differs=c.get('eh_differs', False), by_rule={})
        mine, tree = trees.slot()
        try:
            queue = list(cands)
            while queue:
                rid, ids = queue.pop(0)
                if not ids:
                    continue
                touched = {f: reduced(f, rid, ids) for f in closure}
                touched = {f: p for f, p in touched.items() if p}
                if not touched:
                    continue
                obj = mine / 'u.obj'
                with trees.swapped(tree, touched):
                    ok, text = compile_unit(a.decomp, tree, unit, obj, False, variant='leave-out:' + rid)
                if not ok:
                    row['by_rule'][rid] = dict(compiles=False, files=len(touched),
                                               error='%s %s: %s' % first_error(text))
                    continue
                fp = fpr.row(i, obj)
                obj.unlink()
                fa = A[i]['functions']
                same = [k for k in fa if fp['functions'].get(k, [None])[0] == fa[k][0]]
                fixed = [k for k in row['differ'] if k in same]
                row['by_rule'][rid] = dict(compiles=True, files=len(touched), fixed=fixed,
                                           identical=len(same) == len(fa) and fp['eh'] == A[i].get('eh'),
                                           eh_fixed=bool(row['eh_differs'] and fp['eh'] == A[i].get('eh')))
                # --hier: a group that explains something is split into its rules
                if a.hier and len(ids) > 1 and (fixed or row['by_rule'][rid]['eh_fixed'] or c['status'] != 'differs'):
                    queue += [(r, {r}) for r in sorted(ids)]
        finally:
            for p in mine.glob('u.obj'):
                p.unlink()
        with lock:
            with open(out, 'a') as f:
                f.write(json.dumps(row) + '\n')
            print('%s %s: %s' % (c['status'], unit['source'], {r: (len(v['fixed']) if v['compiles'] else 'x')
                                                               for r, v in row['by_rule'].items()}), flush=True)

    try:
        with ThreadPoolExecutor(a.jobs) as pool:
            list(pool.map(one, todo))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def cmd_summarize(a):
    """Per-rule table from whole-tree runs: BASE (pristine) vs HEAD (prepared with a rule set) and, for each NAME,
    a tree prepared with that set minus NAME (--without NAME=FP.jsonl, a rule id or a group). A HEAD function that
    differs from BASE but is identical in the tree without NAME is attributed to NAME (single rules before groups);
    a unit HEAD does not compile that compiles without NAME failed because of NAME. Kinds: retarget (same code
    bytes), eh (identical when both sides build with /EHs-c-, --eh BASE_EHOFF HEAD_EHOFF), code."""
    A, B = load_rows(a.base), load_rows(a.head)
    without = []
    for w in a.without or []:
        name, _, path = w.partition('=')
        without.append((name, load_rows(path)))
    without.sort(key=lambda nw: (not RULE_ID.fullmatch(nw[0]), nw[0]))      # single rules first
    eha, ehb = (load_rows(a.eh[0]), load_rows(a.eh[1])) if a.eh else ({}, {})
    table, units_by, fails_by, examples, unexplained = {}, {}, {}, {}, []
    totals = Counter()
    for i in sorted(set(A) & set(B)):
        c = compare_rows(A[i], B[i])
        if c['status'] == 'differs':
            ehc = compare_rows(eha[i], ehb[i]) if i in eha and i in ehb else None
            for k in c['differ']:
                kind = 'retarget' if k in c['same_bytes'] else (
                    'eh' if ehc is not None and ehc['status'] in ('identical', 'differs') and k not in ehc['differ']
                    else 'code')
                totals[kind] += 1
                who = [n for n, rows in without if i in rows and rows[i]['status'] == 'ok'
                       and rows[i]['functions'].get(k, [None])[0] == A[i]['functions'][k][0]]
                name = who[0] if who else '(interaction)'
                table.setdefault(name, Counter())[kind] += 1
                units_by.setdefault(name, set()).add(i)
                examples.setdefault((name, kind), []).append((B[i]['source'], k))
                if not who:
                    unexplained.append((B[i]['source'], k))
        elif c['status'] == 'does-not-compile':
            totals['does-not-compile'] += 1
            who = [n for n, rows in without if i in rows and rows[i]['status'] == 'ok']
            name = who[0] if who else '(interaction)'
            fails_by.setdefault(name, []).append((B[i]['source'], '%s %s: %s' % first_error(B[i].get('error', ''))))
    print('differing functions: %s; units that do not compile: %d' % (
        ', '.join('%s %d' % kv for kv in sorted(totals.items()) if kv[0] != 'does-not-compile'),
        totals['does-not-compile']))
    print('%-14s %6s %8s %6s %6s %6s %8s' % ('rule', 'units', 'retarget', 'eh', 'code', 'total', 'no-build'))
    for name in sorted(set(table) | set(fails_by)):
        t = table.get(name, Counter())
        print('%-14s %6d %8d %6d %6d %6d %8d' % (name, len(units_by.get(name, ())), t['retarget'], t['eh'], t['code'],
                                                 sum(t.values()), len(fails_by.get(name, []))))
    if a.examples:
        for (name, kind), ex in sorted(examples.items()):
            print('\n%s / %s: %s' % (name, kind, '; '.join('%s %s' % e for e in ex[:a.examples])))
        for name, ex in sorted(fails_by.items()):
            print('\n%s / no-build: %s' % (name, '; '.join('%s (%s)' % e for e in ex[:a.examples])))
    if a.out:
        Path(a.out).write_text(json.dumps(dict(
            totals=dict(totals), table={k: dict(v) for k, v in table.items()},
            units={k: sorted(v) for k, v in units_by.items()},
            examples={'%s/%s' % k: v for k, v in examples.items()}, fails=fails_by), indent=0) + '\n')


def attributed(row, k, failed=False):
    """The rule (or group) an attribute row names for function k (or for the unit not compiling): the first single
    rule that explains it, else the first group, else '(interaction)'."""
    names = []
    for name, v in row.get('by_rule', {}).items():
        ok = v.get('compiles') and (v.get('identical') if failed else k in v.get('fixed', []))
        if ok:
            names.append(name)
    single = [n for n in names if RULE_ID.fullmatch(n)]
    return (single or names or ['(interaction)'])[0]


LOCAL = -1      # Fingerprinter.targets: the relocation reaches a function defined in the object
hexa_local = '0x%08x' % LOCAL


def retarget_pairs(rt):
    """compare_rows' retargets of one function -> 'A>B,...' (distinct pairs, sorted): A is the pristine side's
    bound address (or its symbol when unbound), B the prepared side's symbol name (its address when it has one)."""
    pairs = set()
    for _, na, va, nb, vb in rt:
        a = na if va in (None, LOCAL, hexa_local) else va
        pairs.add('%s>%s' % (a, nb if nb else vb))
    return ','.join(sorted(pairs)) or '-'


def cmd_report(a):
    """Markdown report of a gate: BASE (pristine) vs HEAD (prepared), with --attr (equiv.py attribute rows) and --eh
    (both sides built with --eh-off) when given. Numbers, unit names, addresses and short diagnostics only."""
    A, B = load_rows(a.base), load_rows(a.head)
    attr = load_rows(a.attr) if a.attr and Path(a.attr).exists() else {}
    eha, ehb = (load_rows(a.eh[0]), load_rows(a.eh[1])) if a.eh else ({}, {})
    st, kinds, table, lines, fails, rts = Counter(), Counter(), {}, [], [], []
    nfun = Counter()
    for i in sorted(set(A) & set(B)):
        c = compare_rows(A[i], B[i])
        st[c['status']] += 1
        if c['status'] not in ('identical', 'differs'):
            if c['status'] == 'does-not-compile':
                rule = attributed(attr.get(i, {}), None, failed=True) if i in attr else '-'
                table.setdefault(rule, Counter())['no-build'] += 1
                f, code, msg = first_error(B[i].get('error', ''))
                fails.append('%s  %s  %s %s' % (B[i]['source'], rule, code or '', (msg or '')[:100]))
            continue
        nfun['compared'] += c['functions']
        nfun['differ'] += len(c['differ'])
        ehc = compare_rows(eha[i], ehb[i]) if i in eha and i in ehb else None
        for k in c['differ']:
            kind = 'retarget' if k in c['same_bytes'] else (
                'eh' if ehc is not None and ehc['status'] in ('identical', 'differs') and k not in ehc['differ']
                else 'code')
            kinds[kind] += 1
            rule = attributed(attr[i], k) if i in attr else '-'
            table.setdefault(rule, Counter())[kind] += 1
            lines.append('%s  %s  %s  %s' % (B[i]['source'], k, kind, rule))
            if kind == 'retarget':
                rts.append('%s  %s  %s' % (B[i]['source'], k, retarget_pairs(c['retargets'].get(k, []))))
    out = ['# MSVC equivalence gate%s' % (' - ' + a.title if a.title else ''), '']
    out.append('- units: %d compared; %d identical, %d differ, %d prepared side does not compile, %d pristine '
               'does not compile' % (sum(st.values()), st['identical'], st['differs'], st['does-not-compile'],
                                     st['base-failed']))
    out.append('- functions: %d compared, %d differ (EH-only %d, retarget %d, code %d%s)' % (
        nfun['compared'], nfun['differ'], kinds['eh'], kinds['retarget'], kinds['code'],
        '' if a.eh else '; EH-only not measured'))
    out += ['', '| rule | eh | retarget | code | no-build |', '|---|---:|---:|---:|---:|']
    for rule in sorted(table, key=lambda r: (r.startswith('(') or r == '-', r)):
        t = table[rule]
        out.append('| %s | %d | %d | %d | %d |' % (rule, t['eh'], t['retarget'], t['code'], t['no-build']))
    out += ['', 'Kinds: eh = identical when both sides build with /EHs-c-; retarget = same code bytes, a relocation '
            'reaches another address; code = the rest. Rule: the single rule (or group) whose leave-out makes the '
            'function identical (the unit compile).', '']
    out += ['## Differing functions (unit, address, kind, rule)', '', '```'] + lines + ['```', '']
    out += ['## Units the prepared side does not compile (unit, rule, first error)', '', '```'] + fails + ['```', '']
    out += ['## Retargets (unit, address, pristine binding>prepared binding of each relocation that moved)', '',
            '```'] + rts + ['```', '']
    Path(a.out).write_text('\n'.join(out) + '\n')
    print('\n'.join(out[:4 + len(table) + 4]))


DECORATED_FN = [re.compile(r'^__imp__([A-Za-z_]\w*?)(?:@\d+)?$'), re.compile(r'^__imp_\?([A-Za-z_]\w*)@@Y'),
                re.compile(r'^\?([A-Za-z_]\w*)@@Y'), re.compile(r'^@([A-Za-z_]\w*)@\d+$'),
                re.compile(r'^_([A-Za-z_]\w*?)(?:@\d+)?$')]


def cmd_bindings(a):
    """Static check behind DECL1 (no compiler): the decomp declares CRT / Win32 functions itself, and the recipe binds
    each decorated name to an address. DECL1 replaces those declarations by the platform's, which binds the call by
    name. Where the recipe binds a decomp spelling of a platform function to another address than the function's
    own import slot / CRT entry, the decomp's name is wrong for that call site and DECL1 changes the callee."""
    prepare = load_prepare()
    platform = prepare.platform_names()
    units = json.loads(Path(a.decomp, 'build/units.json').read_text())['units']
    by_name = {}
    for u in units:
        for k, v in u.get('symbols', {}).items():
            for rx in DECORATED_FN:
                m = rx.match(k)
                if m:
                    if m.group(1) in platform:
                        by_name.setdefault(m.group(1), {}).setdefault(int(v, 16), Counter())[k] += 1
                    break
    bad = 0
    for name in sorted(by_name):
        addrs = by_name[name]
        canon = [ad for ad, ks in addrs.items() if any(IMP_C.match(k) or re.match(r'^_%s$' % name, k) for k in ks)]
        if len(addrs) < 2 and canon:
            continue
        if len(canon) == 1:
            others = {ad: ks for ad, ks in addrs.items() if ad != canon[0]}
        else:
            others = addrs
        if not others:
            continue
        bad += 1
        print('%s (%s): canonical %s' % (name, platform[name] or 'compat', ', '.join('0x%08x' % c for c in canon) or '-'))
        for ad, ks in sorted(others.items()):
            print('    0x%08x  %s' % (ad, ', '.join('%s x%d' % kv for kv in ks.most_common(3))))
    print('%d platform names bound to more than their own address' % bad)


MSVC_ERROR = re.compile(r'^(.*?)\((\d+)\) : (?:fatal )?error (C\d+): (.*)$')


def first_error(text):
    """(file, code, message with names kept and numbers dropped) of the first compiler error in `text`."""
    for line in text.splitlines():
        m = MSVC_ERROR.match(line.strip())
        if m:
            f = m.group(1).replace('\\', '/')
            f = re.sub(r'^.*?/(src|include|SDK/Include|VC/include|msvc)/', r'\1/', f)
            return f, m.group(3), re.sub(r'\d+', 'N', m.group(4))[:120]
    return None, None, text.strip().splitlines()[-1][:120] if text.strip() else ''


def cmd_errors(a):
    """Units that did not compile, grouped by their first error."""
    groups = {}
    for r in load_rows(a.fp).values():
        if r['status'] == 'ok':
            continue
        key = first_error(r.get('error', ''))
        groups.setdefault(key, []).append(r['source'])
    for key, srcs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        print('%5d  %s %s: %s   e.g. %s' % (len(srcs), key[1], key[0], key[2], srcs[0]))


def disassemble(obj, coff, symbol):
    """objdump -d -r of the COMDAT section that holds `symbol` (Intel syntax), offsets relative to the function."""
    s = coff.named(symbol)
    sec = coff.sections[s['section']]
    r = subprocess.run(['objdump', '-d', '-r', '-M', 'intel', '--no-show-raw-insn', '-j', sec['name'],
                        '--disassemble=' + symbol, str(obj)], capture_output=True, text=True, errors='replace')
    lines = [l for l in r.stdout.splitlines() if re.match(r'\s+[0-9a-f]+:', l)]
    return [re.sub(r'^\s*[0-9a-f]+:\s*', '', l).rstrip() for l in lines]


def cmd_show(a):
    """Compile one unit in two trees and print the disassembly diff of the given functions (never committed: the
    output is derived from the decomp)."""
    import difflib
    decomp_tools(a.decomp)
    from compare import Coff
    recipe = json.loads(Path(a.decomp, 'build/units.json').read_text())
    unit = recipe['units'][a.unit]
    want = set(a.fn.split(',')) if a.fn else None
    scratch = Path(tempfile.mkdtemp(prefix='fnvnx-equiv-show-'))
    try:
        objs = []
        for k, tree in enumerate((a.tree_a, a.tree_b)):
            obj = scratch / ('%d.obj' % k)
            ok, text = compile_unit(a.decomp, Path(tree).resolve(), unit, obj, False)
            if not ok:
                print(text[-3000:]); return
            objs.append((obj, Coff(obj)))
        names_b = {f['address']: f['symbol']
                   for f in Fingerprinter(a.decomp, a.tree_b).unit_for(a.unit, objs[1][1])['functions']}
        for fn in unit['functions']:
            if want and fn['address'] not in want:
                continue
            da = disassemble(objs[0][0], objs[0][1], fn['symbol'])
            db = disassemble(objs[1][0], objs[1][1], names_b.get(fn['address'], fn['symbol']))
            if da == db and want is None:
                continue
            print('==== %s %s' % (fn['address'], fn['symbol']))
            print('\n'.join(difflib.unified_diff(da, db, 'a', 'b', n=a.context, lineterm='')))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    sub.add_parser('rules')
    p = sub.add_parser('prepare'); p.add_argument('decomp'); p.add_argument('out'); p.add_argument('--rules', required=True)
    p = sub.add_parser('plan'); p.add_argument('decomp'); p.add_argument('tree'); p.add_argument('--out', required=True)
    p = sub.add_parser('build'); p.add_argument('decomp'); p.add_argument('tree'); p.add_argument('--out', required=True)
    p.add_argument('--units', default='all'); p.add_argument('--only'); p.add_argument('--jobs', type=int, default=os.cpu_count() or 2)
    p.add_argument('--layout', action='store_true', help='also record /d1reportAllClassLayout per unit')
    p.add_argument('--plain', action='store_true', help='the decomp recipe only: no /I tools/equiv/msvc, no /FI (control)')
    p.add_argument('--eh-off', action='store_true', help='/EHs-c- in place of the unit\'s /EH flags (both sides)')
    p.add_argument('--objdir', help='keep the objects (and layout reports) here, for refp / show')
    p.add_argument('--lean', action='store_true', help='prepared trees: the stand-in <windows.h> with '
                   'WIN32_LEAN_AND_MEAN (retry of units the full SDK breaks)')
    p = sub.add_parser('refp'); p.add_argument('decomp'); p.add_argument('old'); p.add_argument('objdir')
    p.add_argument('--out', required=True); p.add_argument('--tree', help='the prepared tree the objects come from')
    p.add_argument('--units', help='only these units (as build --units); units without a row get one from the objects')
    p = sub.add_parser('compare'); p.add_argument('a'); p.add_argument('b'); p.add_argument('--out')
    p = sub.add_parser('show'); p.add_argument('decomp'); p.add_argument('tree_a'); p.add_argument('tree_b')
    p.add_argument('--unit', type=int, required=True); p.add_argument('--fn', help='addresses, comma-separated')
    p.add_argument('--context', type=int, default=3)
    p = sub.add_parser('attribute'); p.add_argument('decomp'); p.add_argument('tree'); p.add_argument('a'); p.add_argument('b')
    p.add_argument('--rules', required=True, help='the rule set tree B was prepared with'); p.add_argument('--out', required=True)
    p.add_argument('--only'); p.add_argument('--limit', type=int); p.add_argument('--workdir')
    p.add_argument('--units', help='only these units (as build --units)')
    p.add_argument('--group', action='append', help='NAME=ID,ID: also leave these rules out together')
    p.add_argument('--failed', action='store_true', help='also units B does not compile')
    p.add_argument('--default-groups', action='store_true', help='the groups of DEFAULT_GROUPS (+ ungrouped rules)')
    p.add_argument('--hier', action='store_true', help='try only the --group sets first; split a group into its rules '
                   'when leaving it out fixes a function (or lets the unit compile)')
    p.add_argument('--jobs', type=int, default=os.cpu_count() or 2)
    p = sub.add_parser('errors'); p.add_argument('fp')
    p = sub.add_parser('bindings'); p.add_argument('decomp')
    p = sub.add_parser('report'); p.add_argument('base'); p.add_argument('head'); p.add_argument('--out', required=True)
    p.add_argument('--attr'); p.add_argument('--eh', nargs=2); p.add_argument('--title')
    p = sub.add_parser('layouts'); p.add_argument('decomp'); p.add_argument('a'); p.add_argument('b')
    p.add_argument('--units', default='all'); p.add_argument('--show'); p.add_argument('--out')
    p = sub.add_parser('summarize'); p.add_argument('base'); p.add_argument('head')
    p.add_argument('--without', action='append', help='NAME=FP.jsonl: HEAD\'s rule set minus NAME')
    p.add_argument('--eh', nargs=2, help='BASE and HEAD built with --eh-off'); p.add_argument('--out')
    p.add_argument('--examples', type=int, default=0)
    a = ap.parse_args()
    dict(rules=cmd_rules, prepare=cmd_prepare, plan=cmd_plan, build=cmd_build, compare=cmd_compare,
         show=cmd_show, attribute=cmd_attribute, errors=cmd_errors, refp=cmd_refp, bindings=cmd_bindings, summarize=cmd_summarize, layouts=cmd_layouts,
         report=cmd_report)[a.command](a)


if __name__ == '__main__':
    main()
