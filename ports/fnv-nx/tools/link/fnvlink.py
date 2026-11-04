#!/usr/bin/env python3
"""fnvlink.py - link the decomp's units by original address (ARCHITECTURE.md D4; docs/LINK.md).

    fnvlink.py resolve <units.json> <objects.tsv> <out dir> [--override <tsv>] [--extern-text <list>]
                       [--crt-symbols <tsv|none>] [--imports <tsv|none>]
                                                              # mapping, address map, ABI report, bridges
                                                              # (S4's provider tables: platform/ of this tree)
                       [--units-renames <tsv|none>]           # S1b's patches/units_renames.tsv (default: this
                                                              # tree's; patched units only)
                       [--msvc-objs <dir>]                    # vtable slots via the MSVC vftables (vslots.py)
    fnvlink.py probe   <out dir> <prepared tree> [jobs]        # layouts of the aggregates resolve asked for, and
                                                              # the synthesized vtables/typeinfo (vtables.py;
                                                              # FNVNX_NO_VTABLES=1 skips them); then resolve again
    fnvlink.py vtables <out dir> <prepared tree> [jobs]        # only the vtable/typeinfo synthesis
    fnvlink.py apply   <out dir> <fixed dir>                   # objcopy every object with its generated args
    fnvlink.py check   <out dir> <fixed dir>                   # trial link: what is still undefined / duplicated

objects.tsv: source<TAB>object path<TAB>ok|fail (tools/link/compile.sh writes it); only 'ok' rows are read.
Everything written to <out dir> is derived from the decomp: local output only, never committed.
"""
import collections
import copy
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import abi      # noqa: E402
import abi2     # noqa: E402
import elfsyms  # noqa: E402
import layout   # noqa: E402
import sigs     # noqa: E402
import vtables  # noqa: E402
import vslots as msvc_vslots  # noqa: E402  (`vslots` is a local name in resolve)

DATA_START = 0x00fdf000          # first address of the image's .rdata/.data/.bss (J-0002)
DATA_END = 0x0200c612
UNDNAME = os.environ.get('UNDNAME') or next((p for p in ('llvm-undname-18', 'llvm-undname')
                                             if any(os.access(os.path.join(d, p), os.X_OK)
                                                    for d in os.environ.get('PATH', '').split(':'))), 'llvm-undname')
CXXFILT = os.environ.get('CXXFILT', 'c++filt')
OBJCOPY = os.environ.get('OBJCOPY', 'aarch64-linux-gnu-objcopy')
AS = os.environ.get('AS', 'aarch64-linux-gnu-as')
LD = os.environ.get('LD', 'aarch64-linux-gnu-ld')
CC = os.environ.get('CC', 'aarch64-linux-gnu-gcc')

# names the toolchain / C++ runtime provides; never matched against the decomp
RUNTIME = re.compile(r'^(?:__cxa_\w+|_Unwind_\w+|__gxx_personality_v0|__stack_chk_\w+|__aarch64_\w+|__dso_handle|'
                     r'_GLOBAL_OFFSET_TABLE_|__tls_get_addr|_Zn[wa]\w*|_Zd[la]\w*|_ZSt\w+|_ZNSt\w+|_ZTVN10__cxxabiv\w+|'
                     r'_ZTI[PKa-z]\w*|_ZTS[PKa-z]\w*|__cxa_pure_virtual|__fnvnx_\w+|__sse2neon\w*|__dynamic_cast|'
                     r'__\w+_chk|__isoc\d+_\w+|__errno_location|__assert_fail)$')
ADDR_SYM = '__fnv_%08x'
TRAP_SYM = '__fnvnx_vtrap_%08x'   # slot of a virtual function defined nowhere -> fnvnx_vslot_trap(this, text)
TLS_SYM = '__fnvnx_tls_%08x'      # thread-local variable at an image .tls address: slot of the native TLS block
XTRAP_SYM = '__fnvnx_xtrap_%08x'  # ctor/dtor defined nowhere, reached only by unwinding / emitted code -> trap
DYN_SYM = re.compile(r'^__fnvnx_(dyninit|dynexit)_([0-9a-f]{8})$')   # startup natives (docs/STARTUP.md)
# renamed in game objects so their static destructors share the game's onexit table (STARTUP.md, "Termination")
GAME_RENAMES = {'__cxa_atexit': 'fnvnx_xc_cxa_atexit'}
ADDR_IN_NAME = re.compile(r'^(?:DAT|FUN|g|UNK|PTR)_([0-9a-f]{8})$')
# prepare rule GAME1 (D13): a Win32-looking name a unit binds to game code is spelled fnvnx_game_<name>; the unit's
# recipe still names it <name>, so the lookup strips the prefix (the unit's binding decides: game code or an IAT slot)
GAME_PREFIX = 'fnvnx_game_'
# the global operator new/delete GCC calls -> the game's, by the MSVC names the units bind (S13: the game allocator,
# never the platform's): Itanium name -> MSVC names to try, in order (sized forms: the game's sized delete)
NEWDEL = {'_Znwm': ('??2@YAPAXI@Z',), '_Znwj': ('??2@YAPAXI@Z',),
          '_Znam': ('??_U@YAPAXI@Z', '??2@YAPAXI@Z'), '_Znaj': ('??_U@YAPAXI@Z', '??2@YAPAXI@Z'),
          '_ZdlPv': ('??3@YAXPAX@Z',), '_ZdaPv': ('??_V@YAXPAX@Z', '??3@YAXPAX@Z'),
          '_ZdlPvm': ('??3@YAXPAXI@Z', '??3@YAXPAX@Z'), '_ZdlPvj': ('??3@YAXPAXI@Z', '??3@YAXPAX@Z'),
          '_ZdaPvm': ('??3@YAXPAXI@Z', '??_V@YAXPAX@Z', '??3@YAXPAX@Z'),
          '_ZdaPvj': ('??3@YAXPAXI@Z', '??_V@YAXPAX@Z', '??3@YAXPAX@Z')}
# other operator new/delete forms (placement, class-like extra parameters) are decomp declarations: resolved like any
# other name, not as runtime; the nothrow / aligned forms stay runtime
NEWDEL_ANY = re.compile(r'^_Z(?:n[wa]|d[la])')
NEWDEL_STD = re.compile(r'^_Z(?:n[wa][jm]|d[la]Pv[jm]?)(?:RKSt9nothrow_t|St11align_val_t)*$')
# S1b (patches/units_renames.tsv): MSVC names of the definitions and references a patch changes; applied by resolve to
# the units whose prepared source carries the patch, when prepare has not rewritten units.json yet
UNITS_RENAMES = os.path.normpath(os.path.join(HERE, '..', '..', 'patches', 'units_renames.tsv'))
S1B_MARK = re.compile(r'fnv-nx (?:XINC1|FTOL1|DBUG1)\b')


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------------------------------- demangling

def batch_demangle(tool, names, args=()):
    """Run a demangler over names (one per line) -> {name: demangled or None}."""
    if not names:
        return {}
    names = list(names)
    p = subprocess.run([tool, *args], input='\n'.join(names) + '\n', capture_output=True, text=True,
                       errors='surrogateescape')
    out = p.stdout.split('\n')
    res = {}
    if tool.endswith(('undname', 'undname-18')) or 'undname' in os.path.basename(tool):
        # llvm-undname echoes each input line, then the result (or 'error: ...'), then a blank line; a result can
        # itself span lines (string literals), so resynchronize on the echo of each input name
        pos = 0
        for m in names:
            j = next((k for k in range(pos, min(pos + 64, len(out) - 1)) if out[k] == m), None)
            if j is None:
                res[m] = None
                continue
            d = out[j + 1]
            res[m] = None if d.startswith('error: Invalid mangled name') else d
            pos = j + 2
    else:
        for m, d in zip(names, out):
            res[m] = d
    return res


class DemangleCache:
    def __init__(self, path, tool, args=()):
        self.path, self.tool, self.args, self.map = path, tool, args, {}
        if os.path.exists(path):
            with open(path, encoding='utf-8', errors='surrogateescape') as f:
                for line in f:
                    m, _, d = line.rstrip('\n').partition('\t')
                    self.map[m] = d if d != '\x00' else None

    def need(self, names):
        missing = sorted(n for n in set(names) if n not in self.map)
        for i in range(0, len(missing), 50000):
            chunk = missing[i:i + 50000]
            got = batch_demangle(self.tool, chunk, self.args)
            for n in chunk:
                self.map[n] = got.get(n)
        if missing:
            with open(self.path, 'w', encoding='utf-8', errors='surrogateescape') as f:
                for m, d in sorted(self.map.items()):
                    f.write('%s\t%s\n' % (m, d if d is not None else '\x00'))

    def __getitem__(self, n):
        return self.map.get(n)


# ---------------------------------------------------------------------------------------------------- units

class Unit:
    """host: for the synthesized-vtable object of a unit (vtables.py), the unit whose names it is resolved with."""
    __slots__ = ('source', 'defs', 'refs', 'obj', 'host', 'sizes')

    def __init__(self, source):
        self.source, self.defs, self.refs, self.obj, self.host, self.sizes = source, [], {}, None, None, {}


def load_renames(path, tree):
    """patches/units_renames.tsv rows (source, kind, address, old, new) of the units whose prepared source under
    `tree` carries an S1b patch marker (a unit compiled without the patch keeps its old names)."""
    if not path or path == 'none' or not os.path.exists(path):
        return []
    rows, patched = [], {}
    for line in open(path, encoding='utf-8'):
        if line.startswith('#') or not line.strip():
            continue
        f = line.rstrip('\n').split('\t')
        if len(f) < 5:
            continue
        if f[0] not in patched:
            p = os.path.join(tree, f[0])
            patched[f[0]] = os.path.exists(p) and bool(
                S1B_MARK.search(open(p, encoding='utf-8', errors='surrogateescape').read()))
        if patched[f[0]]:
            rows.append(tuple(f[:5]))
    return rows


def apply_renames(data, rows, stats):
    """S1b's units.json fix (tools/rules/s1b_patches.py fix_units_data), in memory: 'def' rows rename a recipe
    function's symbol, 'ref' rows add a reference name. Idempotent: a units.json prepare already fixed is unchanged."""
    by_src = collections.defaultdict(list)
    for r in rows:
        by_src[r[0]].append(r)
    for u in data['units']:
        for src, kind, addr, old, new in by_src.get(u['source'], []):
            if kind == 'def':
                for fn in u['functions']:
                    if int(fn['address'], 16) == int(addr, 16) and fn['symbol'] == old:
                        fn['symbol'] = new
                        stats['units_renames_def'] += 1
            elif kind == 'ref' and new not in u['symbols']:
                u['symbols'][new] = addr
                stats['units_renames_ref'] += 1
    return data


def load_units(path, renames=None, stats=None):
    """units.json -> {source: Unit}; sources listed twice (built with two flag sets) are merged. renames: rows of
    load_renames, applied first."""
    units = {}
    data = json.load(open(path))
    if renames:
        apply_renames(data, renames, stats if stats is not None else collections.Counter())
    for u in data['units']:
        unit = units.setdefault(u['source'], Unit(u['source']))
        for fn in u['functions']:
            a = int(fn['address'], 16)
            unit.sizes[a] = fn.get('size')
            for name in [fn['symbol']] + list(fn.get('fold_aliases', [])):
                unit.defs.append((a, name))
        for name, a in u['symbols'].items():
            unit.refs[name] = int(a, 16)
    return units


class KeyIndex:
    """key -> {address: [msvc names]} for one set of (address, MSVC name) pairs."""

    def __init__(self):
        self.map = collections.defaultdict(lambda: collections.defaultdict(list))

    def add(self, sig, addr):
        for _, key in sig.keys():
            self.map[key][addr].append(sig)
        if sig.kind == 'cname' or (sig.kind in ('data', 'func') and len(sig.qual) == 1):
            self.map[('c', sig.qual[0] if sig.qual else '')][addr].append(sig)

    def lookup(self, isig):
        """Itanium Sig -> (tier, {addr: [msvc sigs]}) at the first tier that has candidates, or (None, {})."""
        keys = list(isig.keys())
        if isig.kind == 'cname':
            keys = [('cname', ('c', isig.qual[0]))]
        for tier, key in keys:
            hit = self.map.get(key)
            if hit:
                return tier, hit
        return None, {}


# ---------------------------------------------------------------------------------------------------- resolve

def kind_of_addr(a):
    return 'data' if DATA_START <= a < DATA_END + 0x1000 else ('text' if a < DATA_START else 'other')


def resolve(units_json, objects_tsv, out, override=None, extern_list=None, crt_symbols=None, imports=None,
            units_renames=None):
    """crt_symbols / imports: S4's platform/crt/crt_symbols.tsv and platform/win32/imports.tsv (None: the files of
    this tree, 'none': not used)."""
    os.makedirs(os.path.join(out, 'cache'), exist_ok=True)
    os.makedirs(os.path.join(out, 'objcopy'), exist_ok=True)
    pre_stats = collections.Counter()
    tree = os.path.dirname(os.path.dirname(os.path.abspath(units_json)))   # <tree>/build/units.json
    renames = load_renames(UNITS_RENAMES if units_renames is None else units_renames, tree)
    units = load_units(units_json, renames, pre_stats)
    for line in open(objects_tsv):
        f = line.rstrip('\n').split('\t')
        if len(f) >= 3 and f[2] == 'ok' and f[0] in units:
            units[f[0]].obj = f[1]
    have = [u for u in units.values() if u.obj]
    log('units: %d, with objects: %d' % (len(units), len(have)))

    # MSVC side
    ms = DemangleCache(os.path.join(out, 'cache', 'msvc.tsv'), UNDNAME)
    allms = set()
    for u in units.values():
        allms.update(n for _, n in u.defs); allms.update(u.refs)
    ms.need(n[6:] if n.startswith('__imp_?') else n for n in allms if n.startswith(('?', '__imp_?')))
    msig = {}

    def msvc(n):
        s = msig.get(n)
        if s is None:
            if n.startswith('__imp_?'):   # C++-decorated import (undname leaves these alone): the inner declaration,
                s = copy.copy(sigs.parse_msvc(n[6:], ms[n[6:]]))   # reached through the slot
                s.special = 'import'
                msig[n] = s
            else:
                s = msig[n] = sigs.parse_msvc(n, ms[n])
        return s

    def_at = {}                    # address -> (unit, msvc name) (first definition wins)
    gdef = KeyIndex()              # all definitions, all units
    for u in units.values():
        for a, n in u.defs:
            def_at.setdefault(a, (u, n))
            gdef.add(msvc(n), a)
    gref = KeyIndex()
    for u in units.values():
        for n, a in u.refs.items():
            gref.add(msvc(n), a)

    # synthesized vtables/typeinfo (vtables.py, written by `probe`): one extra object per host unit, its references
    # resolved with the host unit's names
    real = list(have)
    for host, path in vtables.load_synth(out):
        if host in units:
            su = Unit(host + '#vtables')
            su.host, su.obj = host, path
            su.refs = dict(units[host].refs)
            for a, n in units[host].defs:
                su.refs.setdefault(n, a)
            units[su.source] = su
            have.append(su)
    if len(have) > len(real):
        log('synthesized vtable objects: %d' % (len(have) - len(real)))

    # Itanium side
    objsyms, itnames = {}, set()
    for u in have:
        syms = [s for s in elfsyms.symbols(u.obj) if s.name and s.type not in ('section', 'file')]
        objsyms[u.source] = syms
        itnames.update(s.name for s in syms if s.name.startswith('_Z'))
    it = DemangleCache(os.path.join(out, 'cache', 'itanium.tsv'), CXXFILT)
    it.need(itnames)
    isig_cache = {}

    def itanium(n):
        s = isig_cache.get(n)
        if s is None:
            s = isig_cache[n] = sigs.parse_itanium(n, it[n])
        return s

    strong = collections.defaultdict(list)   # Itanium name -> [unit] with a strong global definition
    anydef = set()
    comdat_defs = collections.defaultdict(set)  # unit -> names it defines as COMDAT/weak (inline copies)
    for u in have:
        for s in objsyms[u.source]:
            if s.shndx != 0 and s.bind in ('global', 'weak'):
                anydef.add(s.name)
                if s.comdat or s.bind == 'weak':
                    comdat_defs[u.source].add(s.name)
                if s.bind == 'global' and not s.comdat:
                    strong[s.name].append(u.source)

    # ---- definitions: original address -> native symbol in the defining object
    native = {}      # addr -> dict(unit, sym (elf Sym), variants {variant: Sym}, how)
    def_stats = collections.Counter()
    for u in have:
        idx = KeyIndex()
        funcs = [s for s in objsyms[u.source] if s.shndx != 0 and s.type == 'func']
        groups = collections.defaultdict(list)
        for s in funcs:
            groups[s.name].append(s)
        for name in groups:
            idx.add(itanium(name), name)
        for a, n in u.defs:
            if a in native:
                continue
            m = msvc(n)
            if m.kind == 'special':
                def_stats['special'] += 1; continue
            look = m
            if m.variant in ('sdd', 'vdd'):  # MSVC deleting destructors -> Itanium D0
                look = sigs.Sig('func'); look.qual = m.qual[:-1] + ['{dtor}']; look.params = []; look.cv = ''
            tier, hit = idx_lookup_msvc(idx, look)
            names = sorted(hit.keys()) if hit else []
            if not names:
                def_stats['missing'] += 1; continue
            if len(names) > 1:
                names = disambiguate_defs(names, m, itanium)
            if len(names) != 1 and m.variant in ('ctor', 'dtor', 'sdd', 'vdd'):
                names = names[:1] if len({it[x] for x in names}) == 1 else names
            if len(names) != 1:
                def_stats['ambiguous'] += 1; continue
            variants = {}
            want = {'ctor': ('C1', 'C2'), 'dtor': ('D1', 'D2'), 'sdd': ('D0',), 'vdd': ('D0',)}.get(m.variant)
            if want:  # collect the ctor/dtor variants that share the demangled text
                text = it[names[0]]
                for nm in groups:
                    if it[nm] == text and itanium(nm).variant:
                        variants[itanium(nm).variant] = groups[nm][0]
                pick = next((variants[v] for v in want if v in variants), None)
                if pick is None:
                    def_stats['missing-variant'] += 1; continue
            else:
                pick = groups[names[0]][0]
            native[a] = dict(unit=u.source, sym=pick, variants=variants, tier=tier, msvc=n)
            def_stats['matched-' + tier] += 1

    # ---- startup natives (docs/STARTUP.md, prepare rule XC1): extern "C" __fnvnx_dyninit_<addr> / _dynexit_<addr>
    # stand for the ??__E / ??__F at <addr>, which MSVC names only as specials
    for u in sorted(real, key=lambda x: x.source):
        for s in objsyms[u.source]:
            m = DYN_SYM.match(s.name)
            if m and s.shndx and s.bind != 'local' and s.type == 'func':
                a = int(m.group(2), 16)
                if a not in native:
                    native[a] = dict(unit=u.source, sym=s, variants={}, tier='startup',
                                     msvc=def_at[a][1] if a in def_at else '')
                    def_stats['startup-' + m.group(1)] += 1

    # ---- overrides: a native implementation replaces the decomp definition at an address (docs/LINK.md)
    shadowed = {}
    for a, osym, oobj in load_overrides(override):
        old = native.get(a)
        if old is not None:
            shadowed[a] = old
        msv = old['msvc'] if old else (def_at[a][1] if a in def_at else '')
        native[a] = dict(unit=None, sym=elfsyms.Sym(osym, 0, 0, 'func', 'global', 1, '', False), variants={},
                         tier='override', msvc=msv, override=oobj)

    # ---- references
    layouts = {k: v for k, v in layout.load(os.path.join(out, 'layouts.tsv')).items() if v is not None}
    probe_req = set()
    refs_out = open(os.path.join(out, 'refs.tsv'), 'w', encoding='utf-8', errors='surrogateescape')
    refs_out.write('#object\tundefined\tclass\ttier\taddress\ttarget\tnote\n')
    stats = collections.Counter(pre_stats)
    byclass = collections.Counter()
    # the game's operator new/delete for GCC's global forms: the unit's own binding of the MSVC name, else the one
    # most units use
    newdel_votes = collections.defaultdict(collections.Counter)
    for u_ in units.values():
        for n_, a_ in u_.refs.items():
            if n_.startswith(('??2@', '??3@', '??_U@', '??_V@')):
                newdel_votes[n_][a_] += 1

    def newdel_addr(u_, un_):
        for mn in NEWDEL.get(un_, ()):
            src_ = units.get(u_.host or u_.source)
            if src_ is not None and mn in src_.refs:
                return src_.refs[mn], mn
            if newdel_votes.get(mn):
                return newdel_votes[mn].most_common(1)[0][0], mn
        return None, None
    bridges, abi_rows, abi_classes = {}, [], collections.Counter()
    traps = {}       # trap stub -> (slot symbol defined nowhere, (vtable, slot) where first seen)
    pair_classes = collections.Counter()
    extern_text = collections.defaultdict(set)
    per_obj = {}
    used_addr = set()
    data_defs, tls_defs, tls_addrs = [], [], set()
    exports = load_exports(EXPORTS if imports is None else (os.path.join(os.path.dirname(imports), 'exports.cpp')
                                                             if imports != 'none' else None))
    providers, crt_classes = load_providers(CRT_SYMBOLS if crt_symbols is None else
                                            (None if crt_symbols == 'none' else crt_symbols), exports)
    import_syms = load_imports(IMPORTS if imports is None else (None if imports == 'none' else imports), exports)
    # import thunks (`jmp [IAT]`, 6 bytes) the decomp spells as functions calling the import: compiled, they lose or
    # retype the caller's arguments (S6c); their address binds to the import itself, like a provider
    iat = load_iat(IMPORTS if imports is None else (None if imports == 'none' else imports))
    thunks = find_import_thunks(native, units, iat, import_syms, gdef, gref, anydef, msvc, itanium)
    thunk_undef = collections.defaultdict(dict)   # source -> {thunk definition: import symbol}: the object's own
    for a, (sym, text) in thunks.items():         # calls follow (apply undefines, then renames them)
        providers[a] = (sym, text)
        name = native[a]['sym'].name
        for src in set(strong.get(name, [])) | {native[a]['unit']}:
            thunk_undef[src][name] = sym
        del native[a]
    stats['import_thunks'] = len(thunks)
    bound = collections.defaultdict(set)  # unit -> addresses its object references by address
    # destructors GCC calls where MSVC did not (MSVC destroys through a deleting destructor ??_G/??_E at an address;
    # its identical-code folding gives classes with the same destructor one ??_G): a class sharing a deleting
    # destructor address with a class whose destructor a compiled object defines uses that destructor
    nat_dtor = collections.defaultdict(dict)   # class qual -> {D1/D2: native name}
    for u_ in have:
        for sy in objsyms[u_.source]:
            if sy.shndx and sy.bind in ('global', 'weak') and sy.type == 'func' and re.search(r'D[12]Ev$', sy.name):
                g_ = itanium(sy.name)
                if g_.variant in ('D1', 'D2'):
                    nat_dtor[tuple(g_.qual[:-1])].setdefault(g_.variant, sy.name)
    ddtor_cls = collections.defaultdict(set)   # deleting destructor address -> class quals naming it
    for u_ in units.values():
        for n_, a_ in list(u_.refs.items()) + [(n_, a_) for a_, n_ in u_.defs]:
            if n_.startswith(('??_G', '??_E')):
                m_ = msvc(n_)
                if m_.variant in ('sdd', 'vdd'):
                    ddtor_cls[a_].add(tuple(m_.qual[:-1]))
    ddtor_of = collections.defaultdict(set)
    for a_, cs in ddtor_cls.items():
        for c_ in cs:
            ddtor_of[c_].add(a_)

    def dtor_alias(sig):
        q = tuple(sig.qual[:-1])
        got = {nat_dtor[c_][sig.variant] for a_ in ddtor_of.get(q, ()) for c_ in ddtor_cls[a_] - {q}
               if sig.variant in nat_dtor.get(c_, {})}
        return got.pop() if len(got) == 1 else None
    xtraps = {}      # trap stub -> (ctor/dtor defined nowhere, why)
    unwind_cache = {}
    # S2b-vslots: slots of GCC vtables bound through the MSVC 2008 vftables (vslots.py; --msvc-objs / FNVNX_MSVC_OBJS)
    vsb = msvc_vslots.from_env(units_json, itanium, msvc, lambda ns: (ms.need(ns), {n: ms[n] for n in ns})[1])
    for u in have:
        idx = KeyIndex()
        for n, a in u.refs.items():
            idx.add(msvc(n), a)
        for a, n in u.defs:
            idx.add(msvc(n), a)
        # variables this object defines at a data address: the data image holds the same object (docs/LINK.md)
        for sym in objsyms[u.source]:
            if sym.shndx != 0 and sym.type in ('object', 'tls') and sym.bind != 'local' and \
                    not sym.name.startswith('_ZT'):
                t, hit = idx.lookup(itanium(sym.name))
                if len(hit) == 1 and kind_of_addr(next(iter(hit))) == 'data':
                    (tls_defs if sym.type == 'tls' else data_defs).append((next(iter(hit)), sym.name, u.source))
        renames = {}
        undefs = sorted({s.name for s in objsyms[u.source] if s.shndx == 0})
        tls_undef = {s.name for s in objsyms[u.source] if s.shndx == 0 and s.type == 'tls'}
        vslots = elfsyms.vtable_slot_refs(u.obj)
        for un in undefs:
            stats['refs'] += 1
            isig = itanium(un)
            if isig.qual and isig.qual[-1].startswith(GAME_PREFIX):   # GAME1: the unit's own name for it
                g = copy.copy(isig)
                g.qual = isig.qual[:-1] + [isig.qual[-1][len(GAME_PREFIX):]]
                isig = g
                stats['game1_refs'] += 1
            na, nm = newdel_addr(u, un) if un in NEWDEL else (None, None)
            if na is not None:   # GCC's global operator new/delete -> the game's allocator
                cls, tier, addr, target, note = 'addr', 'game/newdel', na, None, nm
                stats['newdel_refs'] += 1
            else:
                cls, tier, addr, target, note = classify_ref(un, isig, idx, gdef, gref, anydef, msvc)
            if cls != 'unresolved' and isig.qual and un.find(GAME_PREFIX) >= 0:
                stats['game1_resolved'] += 1
            if vsb is not None and (un in vslots or (cls == 'unresolved' and isig.variant in ('D1', 'D2'))):
                cls, tier, addr, target, note = vsb.classify(u.host or u.source, u.obj, un, isig, native, def_at,
                                                             (cls, tier, addr, target, note))
            if addr is not None and cls == 'addr':
                ak = kind_of_addr(addr)
                if ak == 'data' and un in tls_undef:   # thread-local: native TLS in the image's .tls layout
                    cls = 'tls'; target = TLS_SYM % addr; tls_addrs.add(addr)
                elif ak == 'data':
                    cls = 'data'; target = ADDR_SYM % addr; used_addr.add(addr)
                elif addr in native:
                    cls = 'code'
                    bound[u.source].add(addr)
                    nd = native[addr]
                    target = ADDR_SYM % addr
                    if isig.variant and nd['variants'].get(isig.variant) is not None:
                        vs = nd['variants'][isig.variant]
                        if vs is not nd['sym']:
                            target = (ADDR_SYM % addr) + '_' + isig.variant
                    used_addr.add(addr)
                    natname = (nd['variants'].get(isig.variant) or nd['sym']).name if isig.variant else nd['sym'].name
                    stats['code_same_name' if natname == un else 'code_other_name'] += 1
                    # ABI: reference declaration vs definition
                    refdecl = note  # msvc name of the matched reference declaration
                    defdecl = nd['msvc']
                    if refdecl and refdecl != defdecl:
                        rs, ds = msvc(refdecl), msvc(defdecl)
                        if rs.kind == 'func' and ds.kind == 'func':
                            v = abi.compare(rs, ds)
                            for c in v.classes:
                                pair_classes[c] += 1
                            if v.kind != 'same':
                                # aggregates by value: ask the layout probe, then decide on bytes (abi2)
                                for sg, unit in ((rs, u.source), (ds, nd['unit'])):
                                    for c in [c for _, c in abi.params_of(sg)] + [abi.ret_class(sg)]:
                                        if c.startswith('AGG:') and '{anon}' not in c:
                                            probe_req.add((unit, abi2.agg_name(c)))
                                p2 = None if nd['unit'] is None else abi2.compare(rs, ds, lambda t, x=u.source: layouts.get((x, t)),
                                                  lambda t, x=nd['unit']: layouts.get((x, t)))
                                if p2 is not None and (v.kind != 'bridge' or p2.kind != 'bridge') and \
                                        not (v.kind == 'bridge' and 'narrow-extend' in v.classes):
                                    stats['abi2_' + v.kind + '->' + p2.kind] += 1
                                    v = abi.Verdict()
                                    v.kind, v.classes, v.notes, v.plan = p2.kind, p2.classes, p2.notes, p2
                            if v.kind != 'same':
                                key = (addr, refdecl)
                                abi_classes[(v.kind, ','.join(v.classes) or '-')] += 1
                                abi_rows.append((u.source, un, '%08x' % addr, refdecl, defdecl, v.kind,
                                                 ','.join(v.classes), '; '.join(v.moves and
                                                 ['%s<-%s' % (d, s) for d, s, _, _ in v.moves] or []),
                                                 '; '.join(v.notes), ms[refdecl] or '', ms[defdecl] or ''))
                                if v.kind == 'bridge' and v.plan is not None:
                                    bn = abi.bridge_name(addr, refdecl)
                                    code = abi2.emit(bn, target, v.plan, 'ref: %s | def: %s'
                                                     % (ms[refdecl] or refdecl, ms[defdecl] or defdecl))
                                    if code is None:
                                        cls = 'code-manual'
                                    else:
                                        bridges.setdefault(bn, code)
                                        target = bn
                                        cls = 'code-bridge'
                                elif v.kind == 'bridge':
                                    bn = abi.bridge_name(addr, refdecl)
                                    bridges.setdefault(bn, (target, v, ms[refdecl] or refdecl, ms[defdecl] or defdecl))
                                    target = bn
                                    cls = 'code-bridge'
                                elif v.kind == 'manual':
                                    cls = 'code-manual'
                            elif v.notes:
                                pass
                elif ak == 'text' and addr in providers:   # CRT/library code S4 implements, import thunks
                    cls = 'provider'; target = providers[addr][0]
                elif ak == 'text' and addr in def_at:
                    du = def_at[addr][0]
                    cls = 'code-pending' if not du.obj else 'code-defmissing'
                    target = ADDR_SYM % addr
                    used_addr.add(addr)
                elif ak == 'text':
                    cls = 'extern-text'; target = None
                    extern_text[addr].add(un)
                else:
                    cls = 'other-addr'; target = None
            elif cls == 'import' and addr in import_syms:   # IAT slot: the slot's api, not the decomp's spelling
                target = import_syms[addr]
                stats['imports_bound'] += 1
            if cls == 'unresolved' and un in vslots:
                cls = 'vslot-unresolved'
            elif cls == 'unresolved' and isig.variant:
                cls = 'unresolved-ctor-dtor'
                alias = dtor_alias(isig) if isig.variant in ('D1', 'D2') else None
                if alias:
                    cls, target = 'ctor-dtor-alias', alias
                else:
                    if u.host is None and u.source not in unwind_cache:
                        unwind_cache[u.source] = unwind_only(u.obj, {n_ for n_ in undefs if itanium(n_).variant})
                    why = 'called by an emitted slot function' if u.host is not None else (
                        'reached by exception unwinding' if unwind_cache[u.source].get(un) else None)
                    if why:
                        cls = 'ctor-dtor-trap'
                        target = XTRAP_SYM % int(hashlib.sha1(un.encode('utf-8', 'surrogateescape')).hexdigest()[:8], 16)
                        xtraps.setdefault(target, (un, why))
            elif cls == 'special-unresolved':
                cls = 'special-unresolved:' + (note or '?').replace(' ', '-')
            if un in vslots:
                stats['vslot_refs'] += 1
            if cls == 'runtime' and un in GAME_RENAMES:
                target = GAME_RENAMES[un]
            elif cls == 'vslot-unresolved':   # a virtual function defined nowhere: its slots call a trap stub
                target = TRAP_SYM % int(hashlib.sha1(un.encode('utf-8', 'surrogateescape')).hexdigest()[:8], 16)
                if target not in traps:
                    traps[target] = (un, vtables.slot_of(u.obj, un))
            if target and target != un:
                renames[un] = target
            byclass[cls] += 1
            refs_out.write('%s\t%s\t%s\t%s\t%s\t%s\t%s\n' % (u.source, un, cls, tier or '', '%08x' % addr if addr
                           is not None else '', target or '', note or ''))
        renames.update(thunk_undef.get(u.source, {}))   # the object's own import-thunk definition (undefined)
        per_obj[u.source] = renames
    refs_out.close()
    if vsb is not None:
        vsb.write(out)
        bridges.update(vsb.stubs)   # slots through image vftable words (vslots.emit_image_stub)
    # STATIC1 (S1): a variable a header defines gets one definition per including object, also in units whose recipe
    # never names it: every global copy of a name some unit binds to an image address is rebound like that unit's
    # (never a private copy: localizing the duplicates would split the variable)
    bound_var = {}
    for a, name, src in data_defs + tls_defs:
        bound_var.setdefault(name, a)
    have_def = {(name, src) for _, name, src in data_defs + tls_defs}
    for u in have:
        if u.host is not None:
            continue
        for sym in objsyms[u.source]:
            if sym.name in bound_var and (sym.name, u.source) not in have_def and sym.shndx != 0 and \
                    sym.type in ('object', 'tls') and sym.bind != 'local':
                (tls_defs if sym.type == 'tls' else data_defs).append((bound_var[sym.name], sym.name, u.source))
                have_def.add((sym.name, u.source))
                stats['rebound_copies'] += 1

    # ---- per object objcopy args: renames, address symbols for the definitions it holds, localize duplicates
    alias_lines, localize = [], collections.defaultdict(list)
    for name, srcs in strong.items():
        if len(srcs) > 1:
            for s in sorted(srcs)[1:]:
                localize[s].append(name)
    stats['duplicate_strong_names'] = sum(1 for s in strong.values() if len(s) > 1)
    data_def_names = collections.defaultdict(dict)   # source -> {native name: address symbol it becomes}
    for a, name, src in data_defs:
        if ADDR_SYM % a not in data_def_names[src].values():  # objcopy allows one old name per new name
            data_def_names[src][name] = ADDR_SYM % a
            used_addr.add(a)
    for a, name, src in tls_defs:   # thread-local definitions at an image .tls address: the native block's slot
        if TLS_SYM % a not in data_def_names[src].values():
            data_def_names[src][name] = TLS_SYM % a
            tls_addrs.add(a)
    defs_by_unit = collections.defaultdict(list)
    for a, nd in native.items():
        if nd['unit'] is None:
            alias_lines.append('%s = %s;' % (ADDR_SYM % a, nd['sym'].name))
        else:
            defs_by_unit[nd['unit']].append(a)
    # the shadowed decomp definitions: weak, and their names follow the override (so the defining object's own
    # calls and every GCC vtable naming them reach the override too)
    weaken = collections.defaultdict(list)
    shadow_rows = []
    native_names = collections.Counter(nd['sym'].name for nd in native.values())
    for a, old in sorted(shadowed.items()):
        osym = native[a]['sym'].name
        syms = [old['sym']] + [v for v in old['variants'].values() if v is not old['sym']]
        notes = []
        for sy in syms:
            if sy.bind == 'local':
                notes.append('%s is static in its unit: only by-address references follow' % sy.name)
                continue
            if len(strong.get(sy.name, [])) > 1 or native_names[sy.name] > 0:
                notes.append('%s also names other definitions: not aliased' % sy.name)
                continue
            if not (sy.comdat or sy.bind == 'weak'):
                weaken[old['unit']].append(sy.name)
            if sy.name != osym:
                alias_lines.append('%s = %s;' % (sy.name, osym))
        copies = sorted(src for src, names in comdat_defs.items() for sy in syms if sy.name in names
                        and src != old['unit'])
        inl = sorted(src for src, uu in units.items() if uu.obj and not uu.host and src != old['unit']
                     and a in set(uu.refs.values()) and a not in bound[src])
        shadow_rows.append(('%08x' % a, old['msvc'], old['sym'].name, old['unit'], osym, native[a]['override'],
                            ' '.join(copies), ' '.join(inl), '; '.join(notes)))
    with open(os.path.join(out, 'shadowed.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#address\tdecomp_msvc\tdecomp_native\tdecomp_unit\toverride_symbol\toverride_object\t'
                'inline_copies_in\tinlined_or_dropped_in\tnotes\n')
        for r in shadow_rows:
            f.write('\t'.join(r) + '\n')
    stats['overrides'] = len(load_overrides(override))
    stats['shadowed'] = len(shadow_rows)
    stats['shadow_inlined_units'] = sum(1 for r in shadow_rows if r[7])
    for u in have:
        base = os.path.basename(u.obj)
        args = []
        red = os.path.join(out, 'objcopy', base + '.redefine')
        with open(red, 'w', encoding='utf-8', errors='surrogateescape') as f:
            for old, new in sorted(per_obj[u.source].items()):
                f.write('%s %s\n' % (old, new))
        for a in sorted(defs_by_unit[u.source]):
            nd = native[a]
            entries = [(ADDR_SYM % a, nd['sym'])]
            for var, vs in nd['variants'].items():
                if vs is not nd['sym']:
                    entries.append(((ADDR_SYM % a) + '_' + var, vs))
            for alias, s in entries:
                if s.comdat or s.bind == 'weak':
                    alias_lines.append('%s = %s;' % (alias, s.name))   # whichever copy the linker keeps
                else:
                    args.append('--add-symbol=%s=%s:0x%x,global,function' % (alias, s.section, s.value))
        for name in weaken.get(u.source, []):
            args.append('--weaken-symbol=' + name)
        dd = data_def_names.get(u.source, {})
        for name in localize.get(u.source, []):
            if name not in dd and name not in thunk_undef.get(u.source, {}):
                args.append('--localize-symbol=' + name)
        for name, newsym in sorted(dd.items()):
            # a variable this unit defines lives in the data image: the definition becomes weak and is renamed to
            # the address symbol, which aliases.ld defines at the original address (the native static
            # initializer then constructs the object in place, as the original CRT init did); a thread-local one
            # becomes the slot of the native TLS block (bridges.S)
            args.append('--redefine-sym=%s=%s' % (name, newsym))
            args.append('--weaken-symbol=' + newsym)  # objcopy weakens after renaming
        with open(os.path.join(out, 'objcopy', base + '.args'), 'w', encoding='utf-8', errors='surrogateescape') as f:
            f.write('\n'.join(args) + '\n')
        with open(os.path.join(out, 'objcopy', base + '.src'), 'w') as f:
            f.write(u.obj + '\n')
        with open(os.path.join(out, 'objcopy', base + '.undefine'), 'w', encoding='utf-8',
                  errors='surrogateescape') as f:   # definitions apply turns into references (import thunks)
            f.write(''.join(n + '\n' for n in sorted(thunk_undef.get(u.source, {}))))
        with open(os.path.join(out, 'objcopy', base + '.rebind'), 'w', encoding='utf-8',
                  errors='surrogateescape') as f:   # rebound variables: apply retargets the object's own references
            f.write(''.join(n + '\n' for n in sorted(set(dd.values()))))

    # ---- vftables
    # image vftables (primary `vftable' and secondary `vftable'{for `B'}) -> address point of the Itanium vtable of
    # the class of the same name, emitted by a compiled object or synthesized (vtables.py, docs/LINK.md "Vtables")
    def canon_msvc(text):
        return '::'.join(sigs.canon_qual(sigs.split_qual(text)))

    def canon_it(name):
        return itanium(name).name
    vnames = vtables.vftable_names(units, ms, canon_msvc)
    rtti = vtables.RttiIndex()
    for u in sorted(have, key=lambda x: os.path.basename(x.obj)):   # link order (synthesized zz_vt_* last)
        if any(s.shndx and s.bind != 'local' and s.name.startswith(('_ZTV', '_ZTI')) for s in objsyms[u.source]):
            rtti.add(vtables.Obj(u.obj), u.host is not None)
    vmap, vstats = vtables.vftable_map(vnames, rtti, canon_it)
    write_vtable_requests(out, real, objsyms, vnames, it, itanium)
    # hand-written vptr stores of image vftable addresses (`obj.vtbl = &DAT_010176f8;`): the address stands for the
    # native vtable of the class constructed there - the one of the same name when some unit names that vftable,
    # else the class whose member function stores it; its data symbol is bound to that address point (D7, point 2)
    hand = hand_vptr_stores(os.path.dirname(os.path.dirname(os.path.abspath(units_json))), real, vnames, msvc,
                            canon_msvc)
    by_cls = collections.defaultdict(list)
    for n in rtti.where:
        if n.startswith('_ZTV'):
            by_cls[canon_it(n)].append(n)
    rebind, hand_rows = {}, []
    for a, sites in sorted(hand.items()):
        how = 'named vftable' if a in vmap else None
        if a not in vmap:
            for unit, cls in sites:
                z = sorted(by_cls.get(cls or '', []))
                pts = vtables.address_points(rtti.ents(z[0]) or []) if z else []
                if pts:
                    obj = rtti.where[z[0]][0]
                    vmap[a] = (z[0], pts[0][0], obj.path, 'primary, hand-written vptr store in %s (%s)' % (unit, cls))
                    how = 'class of the storing function'
                    vstats['mapped-hand-written'] += 1
                    break
        if a in vmap:
            rebind[a] = vmap[a][:2]
        hand_rows.append(('%08x' % a, how or 'no native vtable', vmap[a][0] if a in vmap else '',
                          ' '.join(sorted({u for u, _ in sites})), ' '.join(sorted({c or '?' for _, c in sites}))))
    with open(os.path.join(out, 'hand_vptrs.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#address\tmapped by\tnative vtable\tunits\tclasses of the storing functions\n')
        for r in hand_rows:
            f.write('\t'.join(r) + '\n')
    stats['hand_vptr_addrs'], stats['hand_vptr_rebound'] = len(hand), len(rebind)

    # ---- data addresses: absolute symbols at the original address (the data image is mapped there, D3)
    with open(os.path.join(out, 'aliases.ld'), 'w') as f:
        f.write('/* fnv-nx link-by-address aliases (generated by tools/link/fnvlink.py; local output) */\n')
        for a in sorted(x for x in used_addr if kind_of_addr(x) == 'data'):
            if a in rebind:   # an image vftable stored into vptrs by hand: the native address point
                f.write('%s = %s + %d;\n' % (ADDR_SYM % a, rebind[a][0], rebind[a][1]))
            else:
                f.write('%s = 0x%08x;\n' % (ADDR_SYM % a, a))
        for line in sorted(set(alias_lines)):
            f.write(line + '\n')

    # ---- bridges
    with open(os.path.join(out, 'bridges.S'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('// fnv-nx ABI bridges (generated by tools/link/fnvlink.py; local output)\n    .text\n')
        for bn in sorted(bridges):
            if isinstance(bridges[bn], str):
                f.write(bridges[bn]); continue
            target, v, rtext, dtext = bridges[bn]
            f.write(abi.emit_bridge(bn, target, v, 'ref: %s | def: %s' % (rtext, dtext)))
        f.write(emit_traps(traps))
        f.write(emit_xtraps(xtraps))
        f.write(emit_tls(tls_addrs))
    with open(os.path.join(out, 'abi.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#object\tundefined\taddress\tref_msvc\tdef_msvc\tverdict\tclasses\tmoves\tnotes\tref\tdef\n')
        for r in abi_rows:
            f.write('\t'.join(r) + '\n')

    with open(os.path.join(out, 'probe_requests.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        for unit, t in sorted(probe_req):
            f.write('%s\t%s\n' % (unit, t))

    # ---- address map
    for a, names in load_extern_list(extern_list).items():
        extern_text.setdefault(a, set()).update(names)
    for a, c in crt_classes.items():   # S4's table lists every referenced .text address without definition
        if c in ('data', 'mid_data'):  # data symbols with a bogus "address": not code
            extern_text.pop(a, None)
        else:
            extern_text.setdefault(a, set())
    amap_kinds, n_deldtor = write_addrmap(out, native, def_at, units, vmap, extern_text, providers)

    # ---- stats
    resolved = sum(byclass[c] for c in ('code', 'code-bridge', 'code-manual', 'data', 'code-pending',
                                         'code-defmissing', 'extern-text', 'import', 'provider', 'tls'))
    with open(os.path.join(out, 'extern_text.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        for a in sorted(extern_text):
            f.write('%08x\t%s\n' % (a, ' '.join(sorted(extern_text[a]))))
    lines = ['objects: %d (of %d units)' % (len(have), len(units)),
             'undefined references (object, symbol): %d' % stats['refs'],
             'resolved by address: %d' % resolved,
             'referenced from vtable slots: %d' % stats['vslot_refs'],
             'code refs bound to a native definition: same Itanium name %d, other name %d (the plain linker would '
             'miss or mis-bind these)' % (stats['code_same_name'], stats['code_other_name']), '']
    lines += ['%-18s %d' % (c, n) for c, n in byclass.most_common()]
    with open(os.path.join(out, 'data_defs.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        for a, name, src in sorted(data_defs):
            f.write('%08x\t%s\t%s\n' % (a, name, src))
    lines += ['', 'overrides: %d, shadowing a compiled decomp definition: %d (with possibly inlined callers in other '
              'units: %d; shadowed.tsv)' % (stats['overrides'], stats['shadowed'], stats['shadow_inlined_units'])]
    lines += ['', 'variables defined natively at a data-image address: %d (%d copies in objects whose unit does not '
              'name the address: STATIC1 header variables)' % (len(data_defs), stats['rebound_copies']),
              'address map entries by kind: %s' % ' '.join('%s=%d' % kv for kv in sorted(amap_kinds.items())),
              'deleting destructors (G) with both D1 and D0: %d of %d' % (n_deldtor, amap_kinds.get('G', 0)),
              'synthesized vtable objects linked: %d' % (len(have) - len(real)),
              'image vftable names (%d (name, address) pairs, %d secondary) -> native address points, by outcome:'
              % (len(vnames), sum(1 for v in vnames.values() if v[1]))]
    lines += ['  %-40s %d' % kv for kv in sorted(vstats.items())]
    lines += ['', 'definitions matched to native symbols:']
    lines += ['  %-18s %d' % (c, n) for c, n in sorted(def_stats.items())]
    lines += ['', 'duplicate strong names localized: %d' % stats['duplicate_strong_names'],
              'bridges generated: %d' % len(bridges),
              'trap stubs for virtual functions defined nowhere: %d (%d slot references)'
              % (len(traps), byclass['vslot-unresolved']),
              'ctors/dtors GCC needs where MSVC did not: %d references bound to the destructor of a class sharing '
              'the deleting destructor, %d to %d trap stubs (only exception cleanups or emitted slot functions reach '
              'them: %d / %d), %d left (normal code)'
              % (byclass['ctor-dtor-alias'], byclass['ctor-dtor-trap'], len(xtraps),
                 sum(1 for w in xtraps.values() if w[1].startswith('reached')),
                 sum(1 for w in xtraps.values() if w[1].startswith('called')), byclass['unresolved-ctor-dtor']),
              'CRT/library addresses with a provider (crt_symbols.tsv): %d, references bound to it: %d'
              % (len(providers), byclass['provider']),
              'import references bound by IAT slot (imports.tsv + exports.cpp): %d of %d'
              % (stats['imports_bound'], byclass['import']),
              'import thunks bound to their import instead of the compiled decomp body: %d' % stats['import_thunks'],
              'GAME1 names (fnvnx_game_*) looked up by the unit\'s own name: %d references, %d resolved'
              % (stats['game1_refs'], stats['game1_resolved']),
              'operator new/delete references bound to the game\'s allocator: %d' % stats['newdel_refs'],
              'S1b units_renames.tsv applied: %d definitions renamed, %d reference names added'
              % (stats['units_renames_def'], stats['units_renames_ref']),
              'image vftable addresses stored into vptrs by hand: %d, bound to a native address point: %d '
              '(hand_vptrs.tsv)' % (stats['hand_vptr_addrs'], stats['hand_vptr_rebound']),
              'thread-local references bound to the native TLS block: %d (%d image .tls addresses, %d native '
              'definitions folded in)' % (byclass['tls'], len(tls_addrs), len(tls_defs)),
              'aggregate layouts known: %d (probe_requests.tsv: %d)' % (len(layouts), len(probe_req)), '',
              'byte-level re-decisions with layouts (class-level verdict -> byte-level verdict, refs):']
    lines += ['  %-6d %s' % (n, k[5:]) for k, n in sorted(stats.items()) if k.startswith('abi2_')]
    lines += ['', 'ABI verdicts (kind, classes) over references:']
    lines += ['  %-6d %s %s' % (n, k, c) for (k, c), n in abi_classes.most_common()]
    lines += ['', 'declaration-shape differences (all resolved code references):']
    lines += ['  %-6d %s' % (n, c) for c, n in pair_classes.most_common()]
    open(os.path.join(out, 'stats.txt'), 'w').write('\n'.join(lines) + '\n')
    print('\n'.join(lines))


VPTR_LHS = re.compile(r'(?i)(?:^|[^\w])(?:\w*_)?(?:vptr|vtbl|vtable|vftable|vfptr|vft|vftbl|vtab|vt)\w*\s*$')
DEREF_LHS = re.compile(r'^\s*\*\s*(?:\(\s*[\w\s:]+\*\s*\*\s*\)|reinterpret_cast\s*<[^<>]*\*\s*\*\s*>)\s*\(?\s*\w+\s*\)?\s*$'
                       r'|^\s*\(\s*\(\s*[\w\s:]+\*\s*\*\s*\)\s*\w+\s*\)\s*\[\s*0\s*\]\s*$')
FUNC_HDR = re.compile(r'^[^\n;{}#]*?\b((?:\w+\s*::\s*)*\w+)\s*::\s*~?\w+\s*\([^;{}]*\)\s*(?:const\s*)?\{', re.M)


def hand_vptr_stores(tree, real, vnames, msvc, canon_msvc):
    """Hand-written stores of image vftable addresses into vptrs in the unit sources: `x.vtbl = &DAT_010176f8;`,
    `*(void **)this = kVtbl_X;`, ... -> {address: [(unit, canonical class of the storing member function or
    None)]}. A store counts when the right side is (after casts and &) a name the unit binds to a data address and
    the left side looks like a vptr (vtbl/vtable/vptr/vft... member, *(T **)p, ((T **)p)[0]), or the address is a
    vftable some unit names (??_7) whatever the left side is called."""
    known = {a for (_, a) in vnames}
    sites = []
    out = collections.defaultdict(list)
    for u in real:
        path = os.path.join(tree, u.source)
        if not os.path.exists(path):
            continue
        names = {}
        for n, a in u.refs.items():
            if kind_of_addr(a) == 'data':
                sg = msvc(n)
                if sg.kind in ('data', 'cname') and sg.qual:
                    names.setdefault(sg.qual[-1], a)
        if not names:
            continue
        text = open(path, encoding='utf-8', errors='surrogateescape').read()
        heads = [(m.start(), m.group(1)) for m in FUNC_HDR.finditer(text)]
        pos = 0
        for stmt in text.split(';'):
            start, pos = pos, pos + len(stmt) + 1
            st = re.split(r'[{}]', stmt)[-1]
            spos = start + len(stmt) - len(st)   # where the statement itself begins (after the last brace)
            m = re.match(r'^(?P<lhs>(?:[^=]|==)*?[^=!<>+\-*/%&|^])=(?!=)(?P<rhs>.*)$', st, re.S)
            if not m:
                continue
            rhs = m.group('rhs')
            while True:
                r2 = re.sub(r'\(\s*(?:const\s+|unsigned\s+|signed\s+|struct\s+|class\s+)*[\w:<>]+(?:\s*\*)*\s*\)', '', rhs)
                if r2 == rhs:
                    break
                rhs = r2
            rhs = re.sub(r'[\s&()]', '', rhs)
            a = names.get(rhs)
            if a is None:
                continue
            lhs = m.group('lhs').strip()
            if not (a in known or VPTR_LHS.search(lhs) or DEREF_LHS.match(lhs)):
                continue
            cls, fn = None, -1
            for k, (hpos, hcls) in enumerate(heads):
                if hpos > spos:
                    break
                cls, fn = hcls, k
            sites.append((a, u.source, canon_msvc(cls) if cls else None, (u.source, fn)))
    per_fn = collections.defaultdict(set)
    for a, _, _, fn in sites:
        per_fn[fn].add(a)
    for a, unit, cls, fn in sites:
        # the storing function's class only counts for a function that stores one vftable (a destructor resetting
        # its own and then its base's vftable stores two)
        out[a].append((unit, cls if len(per_fn[fn]) == 1 else None))
    return out


NOT_SYNTH = ('std::', '__gnu_cxx::', '__cxxabiv1::')   # the C++ runtime defines its own


def write_vtable_requests(out, real, objsyms, vnames, it, itanium):
    """What the synthesis step (vtables.run, `probe`) is asked for, from the compiled objects only (so a rerun after
    the second resolve asks for the same): vtable_requests.tsv - classes with an image vftable whose vtable no
    compiled object emits, and every _ZTV/_ZTI a compiled object references but none defines, each with the units
    that can name the class (referencing units first); rtti_defined.txt - the C++ names compiled objects define
    (vtables, typeinfo, thunks and functions: never synthesized or emitted again)."""
    rdefs = set()
    for u in real:
        for s in objsyms[u.source]:
            if s.shndx and s.bind != 'local' and s.name.startswith('_Z'):
                rdefs.add(s.name)
    reqs = {}

    def want(cls, text, why, host):
        if cls.startswith(NOT_SYNTH) or '{anon}' in cls:
            return
        e = reqs.setdefault(cls, [None, set(), []])
        e[0] = e[0] or vtables.spelling(text, sigs.canon_type, sigs.split_qual)
        e[1].add(why)
        if host not in e[2]:
            e[2].append(host)
    for u in sorted(real, key=lambda x: x.source):
        for s in objsyms[u.source]:
            if s.shndx == 0 and s.name.startswith(('_ZTV', '_ZTI')) and s.name not in rdefs and not RUNTIME.match(s.name):
                isg = itanium(s.name)
                if isg.special in ('vtable for', 'typeinfo for') and it[s.name]:
                    want(isg.name, it[s.name][len(isg.special) + 1:], isg.special.split()[0] + '-ref', u.source)
    have_ztv = {itanium(n).name for n in rdefs if n.startswith('_ZTV')}
    for u in sorted(real, key=lambda x: x.source):
        for n, a in sorted(u.refs.items()):
            v = vnames.get((n, a))
            if v and v[0] not in have_ztv:
                want(v[0], v[2], 'vftable', u.source)
    vtables.write_requests(os.path.join(out, 'vtable_requests.tsv'), {c: tuple(e) for c, e in reqs.items()})
    with open(os.path.join(out, 'rtti_defined.txt'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('\n'.join(sorted(rdefs)) + '\n')


PORT = os.path.dirname(os.path.dirname(HERE))
CRT_SYMBOLS = os.path.join(PORT, 'platform', 'crt', 'crt_symbols.tsv')
IMPORTS = os.path.join(PORT, 'platform', 'win32', 'imports.tsv')
EXPORTS = os.path.join(PORT, 'platform', 'win32', 'exports.cpp')
IDENT = re.compile(r'^[A-Za-z_]\w*$')


def load_exports(path):
    """platform/win32/exports.cpp -> {(dll, api): native symbol} (E/K/U macros and explicit rows)."""
    out = {}
    if path and os.path.exists(path):
        text = open(path, encoding='utf-8', errors='surrogateescape').read()
        for m in re.finditer(r'\bE\("(\w+)",\s*(\w+)\)', text):
            out[(m.group(1).lower(), m.group(2))] = m.group(2)
        for mac, dll in (('K', 'kernel32'), ('U', 'user32')):
            for m in re.finditer(r'\b%s\((\w+)\)' % mac, text):
                out[(dll, m.group(1))] = m.group(1)
        for m in re.finditer(r'\{\s*"(\w+)",\s*"(\w+)",\s*\(void \*\)&(\w+)\s*\}', text):
            out[(m.group(1).lower(), m.group(2))] = m.group(3)
    return {k: v for k, v in out.items() if k[1] != 'f'}   # the macro definitions themselves


def load_providers(crt_tsv, exports):
    """platform/crt/crt_symbols.tsv (S4) -> ({address: (native symbol, provider text)} for the referenced .text
    addresses a platform/library symbol implements, {address: class} of every row). Provider spellings: 'libc: f',
    'platform/crt: f', 'fnvnx_f ...', 'fnvnx_ws2_* (see imports.tsv)' (the thunk's api through exports.cpp);
    'implicit', 'needs body', 'none' and unidentified ones give no symbol."""
    prov, classes = {}, {}
    if not crt_tsv or not os.path.exists(crt_tsv):
        return prov, classes
    for line in open(crt_tsv, encoding='utf-8', errors='surrogateescape'):
        f = line.rstrip('\n').split('\t')
        if line.startswith('#') or len(f) < 7 or not re.match(r'^0x[0-9a-fA-F]+$', f[0]):
            continue
        a, cls, ident, p = int(f[0], 16), f[1], f[2], f[6].strip()
        classes[a] = cls
        sym = None
        m = re.match(r'^(?:libc|libm|platform/crt|platform/win32): (\S+)', p)
        if m and IDENT.match(m.group(1)):
            sym = m.group(1)
        elif p.startswith('fnvnx_ws2_'):
            w = ident.split()
            sym = exports.get(('ws2_32', w[1])) if len(w) == 2 and w[0] == 'ws2_32' else None
        else:
            m = re.match(r'^(fnvnx_\w+)(?:\s|$)', p)
            sym = m.group(1) if m else None
        if sym:
            prov[a] = (sym, p)
    return prov, classes


def load_imports(imports_tsv, exports):
    """platform/win32/imports.tsv (S4: IAT slot, dll, api, ...) -> {slot address: native symbol} for the slots the
    platform implements (the slot's api, not the decomp's spelling of it, decides)."""
    out = {}
    if imports_tsv and os.path.exists(imports_tsv):
        for line in open(imports_tsv, encoding='utf-8', errors='surrogateescape'):
            f = line.rstrip('\n').split('\t')
            if line.startswith('#') or len(f) < 3 or not re.match(r'^0x[0-9a-fA-F]+$', f[0]):
                continue
            sym = exports.get((f[1].lower(), f[2]))
            if sym:
                out[int(f[0], 16)] = sym
    return out


def load_iat(imports_tsv):
    """imports.tsv -> {IAT slot address: (dll, api)}."""
    out = {}
    if imports_tsv and os.path.exists(imports_tsv):
        for line in open(imports_tsv, encoding='utf-8', errors='surrogateescape'):
            f = line.rstrip('\n').split('\t')
            if not line.startswith('#') and len(f) >= 3 and re.match(r'^0x[0-9a-fA-F]+$', f[0]) and IDENT.match(f[2]):
                out[int(f[0], 16)] = (f[1].lower(), f[2])
    return out


def has_branch(obj, sym):
    """The AArch64 code of a defined function contains a branch (b, bl, br, blr): a thunk does, a getter does not."""
    sec = obj.secs[sym.shndx]
    for k in range(sym.size // 4):
        w = struct.unpack_from('<I', obj.data, sec[4] + sym.value + 4 * k)[0]
        if (w & 0x7c000000) == 0x14000000 or (w & 0xfffffc1f) in (0xd61f0000, 0xd63f0000):
            return True
    return False


def func_refs(obj, sym):
    """Names the relocations inside a defined function refer to (vtables.Obj, elfsyms.Sym)."""
    if not sym.size:
        return []
    names = []
    for off, (si, _, _) in obj.rel(sym.shndx).items():
        if sym.value <= off < sym.value + sym.size:
            t = obj.syms[si]
            names.append(t[0] if t[3] != 3 else '.section:' + obj.secnames[t[5]])
    return names


def find_import_thunks(native, units, iat, import_syms, gdef, gref, anydef, msvc, itanium):
    """{address: (native symbol, note)} for the decomp definitions that are import thunks: 6 bytes long (`jmp [slot]`)
    and compiled to a function that branches and whose only reference is one name the defining unit binds to an IAT
    slot (the dllimport declaration or an import-entry variable at the slot; a slot is any address of the IAT span,
    imports.tsv names the slots units call by name). The native symbol is S4's implementation of the slot's api
    (exports.cpp), else the api name (imports.tsv's, or the unit's name for the slot: g_X / __imp_X -> X; S6/S7
    provide those by name)."""
    out, objs, idxs = {}, {}, {}
    lo, hi = (min(iat), max(iat) + 4) if iat else (0, 0)
    for a, nd in native.items():
        u = units.get(nd['unit']) if nd['unit'] else None
        if u is None or u.sizes.get(a) != 6 or nd['sym'].shndx == 0:
            continue
        if u.obj not in objs:
            objs[u.obj] = vtables.Obj(u.obj)
        names = [n for n in func_refs(objs[u.obj], nd['sym']) if not RUNTIME.match(n)]
        if len(set(names)) != 1 or names[0].startswith('.') or not has_branch(objs[u.obj], nd['sym']):
            continue
        if u.source not in idxs:
            idx = KeyIndex()
            for n, x in u.refs.items():
                idx.add(msvc(n), x)
            idxs[u.source] = idx
        _, _, slot, _, _ = classify_ref(names[0], itanium(names[0]), idxs[u.source], gdef, gref, anydef, msvc)
        if slot is None or not lo <= slot < hi:
            continue
        dll, api = iat.get(slot, ('?', None))
        if api is None:
            q = itanium(names[0]).qual
            api = re.sub(r'^(?:__imp_|_imp_|g_)', '', q[-1] if q else names[0])
            if not IDENT.match(api):
                continue
        out[a] = (import_syms.get(slot) or api, 'import thunk %s!%s (slot 0x%08x)' % (dll, api, slot))
    return out


def load_overrides(path):
    """Override table: address (hex), native symbol, object or archive; '#' comments, tab-separated."""
    out = []
    if path:
        for line in open(path, encoding='utf-8', errors='surrogateescape'):
            f = line.rstrip('\n').split('\t')
            if line.startswith('#') or len(f) < 3:
                continue
            out.append((int(f[0], 16), f[1], f[2]))
    return out


def load_extern_list(path):
    """Extern-text list (S4 addr_ids.txt: '0x<addr> name...' per line) -> {address: names}."""
    out = {}
    if path:
        for line in open(path, encoding='utf-8', errors='surrogateescape'):
            f = line.split()
            if f and re.match(r'^(0x)?[0-9a-fA-F]+$', f[0]):
                out[int(f[0], 16)] = set(f[1:])
    return out


def idx_lookup_msvc(idx, m):
    """Look an MSVC definition up in an index of Itanium symbols (the KeyIndex keys are symmetric)."""
    if m.kind == 'cname':
        hit = idx.map.get(('c', m.qual[0]))
        return ('cname', hit) if hit else (None, {})
    for tier, key in m.keys():
        hit = idx.map.get(key)
        if hit:
            return tier, hit
    if len(m.qual) == 1:
        hit = idx.map.get(('c', m.qual[0]))
        if hit:
            return 'cname', hit
    return None, {}


def disambiguate_defs(names, m, itanium):
    """Several native symbols for one MSVC definition: prefer the one whose const-ness and arity agree."""
    want = len(m.params or [])
    c = [n for n in names if itanium(n).params is not None and len(itanium(n).params) == want]
    if len(c) > 1:
        c2 = [n for n in c if itanium(n).cv == m.cv]
        c = c2 or c
    return c or names


def classify_ref(un, isig, idx, gdef, gref, anydef, msvc):
    """-> (class, tier, address, target, note). class 'addr' = an address was found (refined by the caller)."""
    if RUNTIME.match(un) and not (NEWDEL_ANY.match(un) and not NEWDEL_STD.match(un)):
        return 'runtime', None, None, None, None
    if isig.kind == 'special':
        return ('native' if un in anydef else 'special-unresolved'), None, None, None, isig.special
    tier, hit = idx.lookup(isig)
    scope = 'unit'
    if not hit:
        for g, name in ((gdef, 'gdef'), (gref, 'gref')):
            t, h = g.lookup(isig)
            if h and t in ('exact', 'l1', 'cname', 'name') and len(h) == 1:
                tier, hit, scope = t, h, name
                break
    if not hit:
        if un in anydef:
            return 'native', None, None, None, None
        m = ADDR_IN_NAME.match(isig.qual[-1]) if isig.qual else None
        if m:  # Ghidra-style name carrying its address: right for 99.6 % of the names units.json binds (LINK.md)
            return 'addr', 'name-addr', int(m.group(1), 16), None, None
        return 'unresolved', None, None, None, None
    addrs = sorted(hit)
    if len(addrs) > 1 and isig.params is not None:  # disambiguate by arity, then by const
        want = len(isig.params)
        c = [a for a in addrs if any(s.params is not None and len(s.params) == want for s in hit[a])]
        if len(c) > 1:
            c2 = [a for a in c if any(s.cv == isig.cv for s in hit[a])]
            c = c2 or c
        addrs = c or addrs
    if len(addrs) > 1:
        imp = [a for a in addrs if all(s.special == 'import' for s in hit[a])]
        if len(imp) == len(addrs):
            return 'import', tier, None, None, None
        return 'ambiguous', '%s/%s' % (scope, tier), None, None, ' '.join('%08x' % a for a in addrs)
    a = addrs[0]
    decls = hit[a]
    if all(s.special == 'import' for s in decls):
        return 'import', tier, a, None, None
    # the reference declaration: prefer one with the same arity
    best = decls[0]
    if isig.params is not None:
        for s in decls:
            if s.params is not None and len(s.params) == len(isig.params):
                best = s; break
    return 'addr', '%s/%s' % (scope, tier), a, None, best.text


def emit_tls(addrs):
    """Native TLS block in the layout of the image's .tls template (bridges.S): the decomp's __declspec(thread)
    variables are thread-local in GCC's code too, so a reference by template address must not become the absolute
    image address (one copy for all threads, and no valid TLS relocation). __fnvnx_tls_<addr> is the slot of each
    referenced address in __fnvnx_tls_image (.tbss: zero in every new thread); __fnvnx_tls_image_orig / _size give
    the template range, so the platform can copy the image's template into the block of each thread (S4)."""
    if not addrs:
        return ''
    base = max(addrs) & ~0xfff   # the template's page (.tls is the last section before .rsrc)
    size = max(0x1000, (max(addrs) + 8 - base + 15) & ~15)
    out = []
    for a in sorted(x for x in addrs if x < base):   # thread-local in the source, but outside the template: own slot
        n = TLS_SYM % a
        out += ['// 0x%08x is not in the .tls template: a thread-local of its own' % a,
                '    .section .tbss.%s,"awT",%%nobits' % n, '    .p2align 3', '    .globl %s' % n,
                '    .type %s, %%tls_object' % n, '    .size %s, 8' % n, '%s:' % n, '    .zero 8']
    addrs = [x for x in addrs if x >= base]
    out += ['// native TLS block in the image .tls layout (template 0x%08x..0x%08x)' % (base, base + size),
           '    .section .tbss.__fnvnx_tls_image,"awT",%nobits', '    .p2align 4', '    .globl __fnvnx_tls_image',
           '    .type __fnvnx_tls_image, %object', '    .size __fnvnx_tls_image, %d' % size, '__fnvnx_tls_image:',
           '    .zero %d' % size, '    .globl __fnvnx_tls_image_orig', '    .set __fnvnx_tls_image_orig, 0x%08x' % base,
           '    .globl __fnvnx_tls_image_size', '    .set __fnvnx_tls_image_size, %d' % size]
    for a in sorted(addrs):
        n = TLS_SYM % a
        out += ['    .globl %s' % n, '    .type %s, %%tls_object' % n, '    .set %s, __fnvnx_tls_image + %d' % (n, a - base)]
    return '\n'.join(out) + '\n'


def unwind_only(path, names):
    """{name: True when every reference of the object to it is a call in an exception cleanup}: scanning forward from
    the call, `bl _Unwind_Resume` comes before any `ret` / `b` / `br` (GCC's landing pads destroy what a throwing
    constructor or scope had built, then resume unwinding; MSVC's code for the same functions has no such cleanup).
    References from data or other code count as normal."""
    o = vtables.Obj(path)
    out = {}
    resume = {i for i, sy in enumerate(o.syms) if sy[0] == '_Unwind_Resume' and not sy[5]}
    want = {i for i, sy in enumerate(o.syms) if sy[0] in names and not sy[5]}
    for i in range(len(o.secs)):
        if not o.secs[i][2] & 2:
            continue
        rel = o.rel(i)
        hits = [(off, si) for off, (si, _, _) in rel.items() if si in want]
        if not hits:
            continue
        stops = {off for off, (si, _, _) in rel.items() if si in resume}
        code = o.secs[i][2] & 4 and o.secs[i][1] != 8
        data = o.data[o.secs[i][4]:o.secs[i][4] + o.secs[i][5]] if code else b''
        for off, si in hits:
            eh = False
            pos = off + 4
            while code and pos + 4 <= len(data) and pos - off <= 1024:
                if pos in stops:
                    eh = True
                    break
                w = int.from_bytes(data[pos:pos + 4], 'little')
                if w == 0xd65f03c0 or w >> 26 == 0b000101 or (w & 0xfffffc1f) == 0xd61f0000:   # ret, b, br
                    break
                pos += 4
            name = o.syms[si][0]
            out[name] = out.get(name, True) and eh
    return out


def emit_xtraps(xtraps):
    """Trap stubs for constructors / destructors defined nowhere (bridges.S): x1 = what it was, then
    fnvnx_special_trap(this, text); weak default: brk."""
    if not xtraps:
        return ''
    out = ['// constructors / destructors defined nowhere, reached only by exception cleanups or emitted slot functions',
           '    .text', '    .p2align 2', '    .weak fnvnx_special_trap', '    .type fnvnx_special_trap, %function',
           'fnvnx_special_trap:', '    brk #0xf6', '    .size fnvnx_special_trap, .-fnvnx_special_trap']
    for name in sorted(xtraps):
        sym, why = xtraps[name]
        text = ('constructor/destructor defined nowhere: %s (%s)' % (sym, why)).replace('\\', '\\\\').replace('"', '\\"')
        out += ['    .section .text.%s,"ax",%%progbits' % name, '    .p2align 2', '    .globl %s' % name,
                '    .hidden %s' % name, '    .type %s, %%function' % name, '%s:' % name,
                '    adrp x1, .L%s' % name, '    add x1, x1, :lo12:.L%s' % name, '    b fnvnx_special_trap',
                '    .size %s, .-%s' % (name, name), '    .section .rodata.%s,"a"' % name, '.L%s:' % name,
                '    .asciz "%s"' % text]
    return '\n'.join(out) + '\n'


def emit_traps(traps):
    """Trap stubs (bridges.S): x1 = what the slot was, then fnvnx_vslot_trap(this, text); the weak default here
    stops at a brk (the platform overrides it with one that reports and aborts)."""
    if not traps:
        return ''
    out = ['// virtual functions defined nowhere: their vtable slots call these stubs',
           '    .text', '    .p2align 2', '    .weak fnvnx_vslot_trap', '    .type fnvnx_vslot_trap, %function',
           'fnvnx_vslot_trap:', '    brk #0xf5', '    .size fnvnx_vslot_trap, .-fnvnx_vslot_trap']
    for name in sorted(traps):
        sym, where = traps[name]
        text = 'virtual function defined nowhere: %s' % sym
        if where:
            text += ' (slot %d of %s)' % (where[1], where[0])
        text = text.replace('\\', '\\\\').replace('"', '\\"')
        out += ['    .section .text.%s,"ax",%%progbits' % name, '    .p2align 2', '    .globl %s' % name,
                '    .hidden %s' % name, '    .type %s, %%function' % name, '%s:' % name,
                '    adrp x1, .L%s' % name, '    add x1, x1, :lo12:.L%s' % name, '    b fnvnx_vslot_trap',
                '    .size %s, .-%s' % (name, name), '    .section .rodata.%s,"a"' % name, '.L%s:' % name,
                '    .asciz "%s"' % text]
    return '\n'.join(out) + '\n'


# ---------------------------------------------------------------------------------------------------- address map

def write_addrmap(out, native, def_at, units, vtables=None, extern_text=None, providers=None):
    """addrmap.tsv / addrmap.c (docs/LINK.md, 'Address map'): code addresses the native build defines (F C X G),
    image vftables (V: the Itanium vtable address point of the same class, primary or secondary, compiled or
    synthesized), referenced .text addresses no unit defines (E: native NULL, the loader traps them), and per G
    entry its D1/D0 pair (fnvnx_addrmap_deldtors). vtables: {address: (vtable symbol, address-point offset, object
    path, note)}; providers: {address: (platform symbol, provider)} (F entries naming the platform symbol).
    -> (Counter of kinds, number of G entries with a D1)."""
    rows = []   # address, kind, link_symbol, native_symbol, object, note, offset, d1_symbol, d1_native
    for a in sorted(native):
        nd = native[a]
        v = nd['msvc']
        kind, note, d1l, d1n = 'F', '', '', ''
        if '??_G' in v or '??_E' in v:
            kind, note = 'G', 'msvc deleting dtor (takes flags) -> Itanium D0 (always deletes); D1 (destroys only)'
            for var in ('D1', 'D2'):   # D2 == D1 without virtual bases
                if nd['variants'].get(var) is not None:
                    d1l, d1n = (ADDR_SYM % a) + '_' + var, nd['variants'][var].name
                    break
            else:
                note += ' not emitted'
        elif v.startswith('??0'):
            kind = 'C'
        elif v.startswith('??1'):
            kind = 'X'
        elif nd['sym'].bind == 'local':
            note = 'static in its unit'
        if nd['unit'] is None:
            kind, note, d1l, d1n = 'F', 'override of ' + (nd['msvc'] or '?'), '', ''
            obj = os.path.basename(nd['override'])
        else:
            obj = os.path.basename(units[nd['unit']].obj)
        rows.append((a, kind, ADDR_SYM % a, nd['sym'].name, obj, note, '', d1l, d1n))
    for a, (zname, off, obj, note) in sorted((vtables or {}).items()):
        if a not in native:
            rows.append((a, 'V', zname, zname, os.path.basename(obj), note, str(off), '', ''))
    for a, (sym, text) in sorted((providers or {}).items()):
        if a not in native:   # CRT/library code the platform implements: the provider symbol itself
            rows.append((a, 'F', sym, sym, '', 'provider: ' + text, '', '', ''))
    for a in sorted(extern_text or {}):
        if a not in native and a not in (providers or {}):
            rows.append((a, 'E', '', '', '', 'no native definition (CRT/thunk, S4)', '', '', ''))
    rows.sort()
    with open(os.path.join(out, 'addrmap.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('# fnvnx-addrmap 1\n# address\tkind\tlink_symbol\tnative_symbol\tobject\tnote\toffset\td1_symbol\t'
                'd1_native\n')
        for r in rows:
            f.write('%08x\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' % r)
    dd = [(r[0], r[7], r[2]) for r in rows if r[1] == 'G']
    with open(os.path.join(out, 'addrmap.c'), 'w') as f:
        f.write('/* fnv-nx address map table (generated by tools/link/fnvlink.py; local output). */\n'
                '#include "fnvnx_addrmap.h"\n\n')
        cname = {}
        for name in sorted({r[2] for r in rows if r[2]} | {r[7] for r in rows if r[7]}):
            if name.startswith(('__fnv', '_Z')):
                cname[name] = name
                f.write('extern char %s[];\n' % name)
            else:   # a platform/libc symbol: an asm label keeps it clear of the compiler's built-in declarations
                cname[name] = '__fnvnx_am_%d' % len(cname)
                f.write('extern char %s[] __asm__("%s");\n' % (cname[name], name))
        f.write('\nconst struct fnvnx_addrmap_entry fnvnx_addrmap[] = {\n')
        for a, kind, link, *rest in rows:
            ptr = '0' if not link else ('%s + %s' % (cname[link], rest[3]) if kind == 'V' else cname[link])
            f.write('    {0x%08xu, %s},\n' % (a, ptr))
        if not rows:
            f.write('    {0u, 0},\n')
        f.write('};\nconst char fnvnx_addrmap_kinds[] = "%s";\n' % ''.join(r[1] for r in rows))
        f.write('const unsigned fnvnx_addrmap_count = %d;\n' % len(rows))
        f.write('\nconst struct fnvnx_addrmap_deldtor fnvnx_addrmap_deldtors[] = {\n')
        for a, d1, d0 in dd:
            f.write('    {0x%08xu, %s, %s},\n' % (a, cname[d1] if d1 else '0', cname[d0]))
        if not dd:
            f.write('    {0u, 0, 0},\n')
        f.write('};\nconst unsigned fnvnx_addrmap_deldtor_count = %d;\n' % len(dd))
    return collections.Counter(r[1] for r in rows), sum(1 for _, d1, _ in dd if d1)


# ---------------------------------------------------------------------------------------------------- apply / check

def apply(out, fixed):
    os.makedirs(fixed, exist_ok=True)
    n = 0
    retargeted, retarget_objs = 0, 0
    for name in sorted(os.listdir(os.path.join(out, 'objcopy'))):
        if not name.endswith('.args'):
            continue
        base = name[:-5]
        src = os.path.join(out, 'objcopy', base + '.src')
        obj = open(src).read().strip() if os.path.exists(src) else None
        if obj is None:
            continue
        dst = os.path.join(fixed, base)
        argfile = os.path.join(out, 'objcopy', name)
        if open(argfile).read().strip():
            r = subprocess.run([OBJCOPY, '@' + argfile, obj, dst], capture_output=True, text=True)
            if r.returncode:
                log('objcopy failed for %s: %s' % (base, r.stderr.strip()[:500]))
                continue
            obj = dst
        mapping = {}
        with open(os.path.join(out, 'objcopy', base + '.redefine'), encoding='utf-8', errors='surrogateescape') as f:
            for line in f:
                old, new = line.split()
                mapping[old] = new
        temps = []
        # split globals (S13): GAS resolves the defining object's own references to a variable as `.bss + off`, so
        # renaming and weakening it would miss that object (and its static initializer would build a private
        # copy): those relocations are pointed at the rebound symbol first
        rb = os.path.join(out, 'objcopy', base + '.rebind')
        rebind = set(open(rb, encoding='utf-8', errors='surrogateescape').read().split()) if os.path.exists(rb) \
            else set()
        if rebind:
            k = elfsyms.retarget_local_relocs(obj, dst + '.rb', rebind)
            retargeted += k
            retarget_objs += bool(k)
            obj = dst + '.rb'
            temps.append(obj)
        und = os.path.join(out, 'objcopy', base + '.undefine')
        names = set(open(und, encoding='utf-8', errors='surrogateescape').read().split()) if os.path.exists(und) \
            else set()
        if names:
            elfsyms.undefine_symbols(obj, dst + '.und', names)
            obj = dst + '.und'
            temps.append(obj)
        elfsyms.rename_undefined(obj, dst, mapping)
        for t in temps:
            os.remove(t)
        n += 1
    ov = os.path.join(out, 'overrides.list')
    if os.path.exists(ov):
        for obj in sorted(set(open(ov).read().split('\n')) - {''}):
            shutil.copyfile(obj, os.path.join(fixed, 'zz_override_' + os.path.basename(obj)))
    br = os.path.join(out, 'bridges.S')
    r = subprocess.run([AS, '-mabi=ilp32', br, '-o', os.path.join(fixed, 'zz_bridges.o')], capture_output=True,
                       text=True)
    if r.returncode:
        log('as failed: ' + r.stderr[:2000])
    r = subprocess.run([CC, '-mabi=ilp32', '-O2', '-c', '-I', HERE, os.path.join(out, 'addrmap.c'), '-o',
                        os.path.join(fixed, 'zz_addrmap.o')], capture_output=True, text=True)
    if r.returncode:
        log('cc addrmap.c failed: ' + r.stderr[:2000])
    print('objects rewritten: %d' % n)
    print('own references to rebound variables retargeted: %d relocations in %d objects' % (retargeted,
                                                                                            retarget_objs))


def check(out, fixed):
    objs = sorted(os.path.join(fixed, n) for n in os.listdir(fixed) if n.endswith(('.o', '.a')))
    elf = os.path.join(out, 'trial.elf')
    r = subprocess.run([LD, '-m', 'aarch64linux32', '--no-demangle', '--noinhibit-exec', '--defsym=__dso_handle=0', '-e', '0', '-o', elf,
                        os.path.join(out, 'aliases.ld'), *objs], capture_output=True, text=True,
                       errors='surrogateescape')
    open(os.path.join(out, 'trial_link.log'), 'w').write(r.stderr)
    undef = collections.Counter(re.findall(r"undefined reference to `([^']+)'", r.stderr))
    multi = set(re.findall(r"multiple definition of `([^']+)'", r.stderr))
    cls = collections.Counter()
    mapped = set()
    for line in open(os.path.join(out, 'addrmap.tsv')):
        if not line.startswith('#') and line.split('\t')[1] in 'FCXG':
            mapped.add(ADDR_SYM % int(line.split('\t')[0], 16))
    for name in undef:
        if name.startswith('__fnv_') and name.split('_C')[0].split('_D')[0] in mapped:
            cls['__fnv_ of a mapped address (BUG)'] += 1
        elif name.startswith('__fnv_'):
            cls['__fnv_ (definition not compiled yet)'] += 1
        elif name.startswith('__fnvnx_br_'):
            cls['bridge'] += 1
        elif RUNTIME.match(name) or not name.startswith('_Z'):
            cls['runtime / C / platform'] += 1
        else:
            cls['C++ by name'] += 1
    print('trial link: %d objects; undefined names: %d; multiple definitions: %d' % (len(objs), len(undef),
                                                                                     len(multi)))
    for c, n in cls.most_common():
        print('  %-40s %d' % (c, n))


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    cmd = sys.argv[1]
    if cmd == 'resolve' and '--msvc-objs' in sys.argv:   # vslots.py: the pristine MSVC objects (u<index>.obj)
        k = sys.argv.index('--msvc-objs')
        os.environ['FNVNX_MSVC_OBJS'] = sys.argv[k + 1]
        del sys.argv[k:k + 2]
    if cmd == 'resolve':
        argv, opts = [], {}
        it_ = iter(sys.argv[2:])
        for x in it_:
            if x in ('--override', '--extern-text', '--crt-symbols', '--imports', '--units-renames'):
                opts[x[2:].replace('-', '_')] = next(it_)
            else:
                argv.append(x)
        resolve(*argv[:3], override=opts.get('override'), extern_list=opts.get('extern_text'),
                crt_symbols=opts.get('crt_symbols'), imports=opts.get('imports'),
                units_renames=opts.get('units_renames'))
        with open(os.path.join(argv[2], 'overrides.list'), 'w') as f:
            for _, _, obj in load_overrides(opts.get('override')):
                f.write(obj + '\n')
    elif cmd in ('probe', 'vtables'):
        jobs = int(sys.argv[4]) if len(sys.argv) > 4 else os.cpu_count()
        if cmd == 'probe':
            layout.run(sys.argv[2], sys.argv[3], jobs)
        if os.path.exists(os.path.join(sys.argv[2], 'vtable_requests.tsv')) and \
                (cmd == 'vtables' or not os.environ.get('FNVNX_NO_VTABLES')):
            vtables.run(sys.argv[2], sys.argv[3], jobs)
    elif cmd == 'apply':
        apply(*sys.argv[2:4])
    elif cmd == 'check':
        check(*sys.argv[2:4])
    else:
        print(__doc__); sys.exit(2)


if __name__ == '__main__':
    main()
