#!/usr/bin/env python3
"""fnvlink.py - link the decomp's units by original address (ARCHITECTURE.md D4; docs/LINK.md).

    fnvlink.py resolve <units.json> <objects.tsv> <out dir> [--override <tsv>] [--override-lib <archive>]
                       [--extern-text <list>]
                       [--crt-symbols <tsv|none>] [--imports <tsv|none>]
                                                              # mapping, address map, ABI report, bridges
                                                              # (S4's provider tables: platform/ of this tree)
                       [--units-renames <tsv|none>]           # S1b's patches/units_renames.tsv (default: this
                                                              # tree's; patched units only)
                       [--msvc-objs <dir>]                    # vtable slots via the MSVC vftables (vslots.py)
                       [--msvc-code <dir>]                    # only the definitions' register reads (ecx-not-
                                                              # passed verdicts) from those objects
    fnvlink.py probe   <out dir> <prepared tree> [jobs]        # layouts of the aggregates resolve asked for, and
                                                              # the synthesized vtables/typeinfo (vtables.py;
                                                              # FNVNX_NO_VTABLES=1 skips them); then resolve again
    fnvlink.py vtables <out dir> <prepared tree> [jobs]        # only the vtable/typeinfo synthesis
    fnvlink.py bodies  <out dir> <prepared tree> [--msvc-objs <dir>] [--exe <exe>] [--names <undefined.txt>] [--no-synth]
                                                              # declared-only functions (bodies.py): bound by
                                                              # address or synthesized; then resolve again
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
import bodies   # noqa: E402  (S2d: declared-only functions)
import thisadj  # noqa: E402  (S2c-abi: MSVC entries of secondary-base overrides)

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
MSVC_ENTRY_SYM = '__fnvnx_msvc_%08x'   # MSVC's entry of an override that takes `this` at a secondary base: the thunk
TEXTADDR_SYM = '__fnvnx_textaddr_%08x'   # a variable at a .text address nothing defines: absolute, the x86 value
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
# the decomp's unit-local namespaces (`namespace fnv_00650ea0 { struct NiFixedString ... }`): a copy of a class
UNIT_NS = re.compile(r'^fnv_[0-9a-f]{8}$')
UNIT_NS_ANY = re.compile(r'\bfnv_[0-9a-f]{8}::')
# a class's own operator new / new[] / delete / delete[] (sized or not): the global form it stands for, read from
# the demangled name (the mangled operator codes nw/na/dl/da cannot be told from a name ending in those letters)
MEMBER_NEWDEL_GLOBAL = {('operator new', 1): '_Znwj', ('operator new[]', 1): '_Znaj',
                        ('operator delete', 1): '_ZdlPv', ('operator delete', 2): '_ZdlPvj',
                        ('operator delete[]', 1): '_ZdaPv', ('operator delete[]', 2): '_ZdaPvj'}
SIZE_PARAM = ('unsigned int', 'unsigned long')


def member_newdel_global(isig):
    """The global allocation function a class's own `operator new/delete` (no placement form) stands for, or None."""
    if isig.kind != 'func' or not isig.qual or len(isig.qual) < 2 or isig.params is None:
        return None
    g = MEMBER_NEWDEL_GLOBAL.get((isig.qual[-1], len(isig.params)))
    if g is None:
        return None
    p = [' '.join(x.split()) for x in isig.params]
    if isig.qual[-1].startswith('operator new'):
        return g if p[0] in SIZE_PARAM else None
    return g if p[0] == 'void*' and (len(p) == 1 or p[1] in SIZE_PARAM) else None
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


def resolve(units_json, objects_tsv, out, override=None, extern_list=None, crt_symbols=None, imports=None, override_lib=None,
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
    ident = collections.defaultdict(set)   # data identifier -> image data addresses any unit binds it to
    for u in units.values():
        for n, a in u.refs.items():
            m_ = msvc(n)
            gref.add(m_, a)
            if m_.kind in ('data', 'cname') and m_.qual and m_.special is None and DATA_START <= a < DATA_END:
                ident[m_.qual[-1]].add(a)

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
    # ---- S2d (bodies.py, `fnvlink.py bodies`): synthesized bodies of declared-only functions, extra objects of
    # their host unit like the vtables above; body_bind: names bound to an address under another name
    n_vt = len(have)
    for host, path in bodies.load_synth(out):
        if host in units:
            su = Unit('%s#bodies%d' % (host, len(have)))
            su.host, su.obj = host, path
            su.refs = dict(units[host].refs)
            for a, n in units[host].defs:
                su.refs.setdefault(n, a)
            units[su.source] = su
            have.append(su)
    if len(have) > n_vt:
        log('synthesized body objects: %d' % (len(have) - n_vt))
    body_bind = bodies.load_bind(out)
    # ---- end S2d

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
    # GCC's local aliases (`f.localalias`, same address as f): under -fPIC GCC makes them for some functions (virtual
    # ones defined with their vtable) without calling them; with -fno-semantic-interposition a unit's own calls go to
    # them, past every by-address binding, so compile.sh does not use that flag. Never a definition candidate.
    stats_localalias = sum(1 for u in have for s in objsyms[u.source] if s.name.endswith('.localalias'))
    for u in have:
        idx = KeyIndex()
        funcs = [s for s in objsyms[u.source] if s.shndx != 0 and s.type == 'func' and
                 not s.name.endswith('.localalias')]   # GCC's local alias of the same definition
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
    # S2b-vslots: slots of GCC vtables bound through the MSVC 2008 vftables (vslots.py; --msvc-objs / FNVNX_MSVC_OBJS);
    # also the image vftables a vslot override row names
    vsb = msvc_vslots.from_env(units_json, itanium, msvc, lambda ns: (ms.need(ns), {n: ms[n] for n in ns})[1])
    shadowed = {}
    osigs = load_override_sigs(override)   # optional 4th column: the override's own MSVC-decorated signature
    ms.need(osigs.values())
    pending = override_pending(override)
    stats_ov = collections.Counter()
    lib_syms = elfsyms.archive_symbols(override_lib) if override_lib else None
    ov_rows = []
    for key, osym, oobj in load_overrides(override, override_lib):
        if key in pending:
            stats_ov['pending'] += 1
            continue
        if lib_syms is not None and oobj == override_lib and osym not in lib_syms:
            stats_ov['not in --override-lib'] += 1
            log('override %s: %s is not defined in %s' % (key, osym, override_lib))
            continue
        if isinstance(key, tuple):
            a, why = vslot_address(vsb, key)
            if a is None:
                stats_ov['vslot ' + why] += 1
                log('override %s:%s:%d: %s' % (key + (why,)))
                continue
            stats_ov['vslot rows'] += 1
            if key in osigs:
                osigs[a] = osigs[key]
        else:
            a = key
        ov_rows.append((a, osym, oobj))
    for a, osym, oobj in ov_rows:
        old = native.get(a)
        if old is not None:
            shadowed[a] = old
        msv = osigs.get(a) or (old['msvc'] if old else (def_at[a][1] if a in def_at else ''))
        native[a] = dict(unit=None, sym=elfsyms.Sym(osym, 0, 0, 'func', 'global', 1, '', False), variants={},
                         tier='override', msvc=msv, override=oobj)

    # ---- S2c-abi: overrides of a secondary base's virtual, which MSVC enters with `this` at that base (thisadj.py):
    # image code pointers to the address reach GCC's _ZThn<d>_ thunk, the units' own references the primary entry
    sec_entries = thisadj.from_env()
    syms_by_obj = {}
    for a, d in sorted(sec_entries.items()):
        nd = native.get(a)
        if nd is None or nd['unit'] is None:
            continue
        tn = thisadj.thunk_name(nd['sym'].name, d)
        if nd['unit'] not in syms_by_obj:
            syms_by_obj[nd['unit']] = {x.name: x for x in objsyms.get(nd['unit'], ()) if x.shndx != 0}
        th = syms_by_obj[nd['unit']].get(tn) if tn else None
        if th is not None and th.bind != 'local':
            nd['msvc_entry'] = (th.name, d)
            pre_stats['msvc_entry_thunks'] += 1
        else:
            pre_stats['msvc_entry_no_thunk'] += 1
    pre_stats['msvc_entry_exe'] = len(sec_entries)

    # ---- references
    layouts = {k: v for k, v in layout.load(os.path.join(out, 'layouts.tsv'), tree).items() if v is not None}
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
    native_text = None   # demangled text -> a native definition's mangled name (built when first needed)
    bridges, abi_rows, abi_classes = {}, [], collections.Counter()
    traps = {}       # trap stub -> (slot symbol defined nowhere, (vtable, slot) where first seen)
    pair_classes = collections.Counter()
    extern_text = collections.defaultdict(set)
    per_obj = {}
    used_addr = set()
    data_defs, tls_defs, tls_addrs = [], [], set()
    text_addrs = set()   # .text addresses of variables nothing defines (`text-addr`)
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
    cast_cache = {}
    text_cache = {}

    def source_text(src_):
        if src_ not in text_cache:
            p_ = os.path.join(tree, src_.split('#')[0])
            text_cache[src_] = open(p_, encoding='utf-8', errors='surrogateescape').read() if os.path.exists(p_) else ''
        return text_cache[src_]
    decl_cache = {}

    def c_decls(src_):
        return unit_c_decls(tree, src_, decl_cache)

    def casts_of(u_):
        if u_.source not in cast_cache:
            path_ = os.path.join(tree, u_.source)
            cast_cache[u_.source] = unit_casts(open(path_, encoding='utf-8', errors='surrogateescape').read()) \
                if u_.host is None and os.path.exists(path_) else {}
        return cast_cache[u_.source]
    called_cache = {}

    def called_syms(u_):
        """Undefined symbols the object calls or branches to (CALL26 / JUMP26)."""
        if u_.source not in called_cache:
            o_ = vtables.Obj(u_.obj)
            called_cache[u_.source] = {o_.syms[si][0] for i_ in range(len(o_.secs)) if o_.secs[i_][2] & 4
                                       for _, (si, typ, _) in o_.rel(i_).items() if typ in (21, 20)}   # P32
        return called_cache[u_.source]
    # S2b-vslots: slots of GCC vtables bound through the MSVC 2008 vftables (vslots.py; --msvc-objs / FNVNX_MSVC_OBJS)
    ecx_use = msvc_code_use(units_json, ms, msvc, renames)
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
                cls, tier, addr, target, note = classify_ref(un, isig, idx, gdef, gref, anydef, msvc, ident)
                if tier == 'gref/ident':
                    stats['ident_' + cls] += 1
                if cls == 'unresolved' and isig.qual and UNIT_NS.match(isig.qual[0]) and it[un]:
                    # a unit-local namespaced copy of a class (`fnv_00650ea0::NiFixedString`, S13 J-0114): bound like
                    # the global class - by its names in units.json without the namespace, else to the native
                    # definition of the global name
                    st_ = UNIT_NS_ANY.sub('', it[un])
                    ts_ = sigs.parse_itanium(un, st_)
                    r_ = classify_ref('', ts_, idx, gdef, gref, set(), msvc, ident) if ts_.qual else None
                    if r_ and r_[0] not in ('unresolved', 'runtime'):
                        cls, tier, addr, target, note = r_[0], 'ns/' + (r_[1] or ''), r_[2], r_[3], r_[4]
                        stats['unit_ns_bound'] += 1
                    else:
                        if native_text is None:
                            native_text = {}
                            for n_ in sorted(anydef):
                                if it[n_]:
                                    native_text.setdefault(it[n_], n_)
                        g_ = native_text.get(st_)
                        if g_:
                            cls, tier, target = 'ns-alias', 'ns/native', g_
                            stats['unit_ns_aliased'] += 1
                gname_ = member_newdel_global(isig) if cls == 'unresolved' else None
                if gname_:
                    # a class's own operator new/delete that nothing defines or binds (a decomp class declares it,
                    # GCC's deleting destructor and new expressions call it): the game's allocator, like the
                    # global forms (J-0108: 509 undefined names)
                    na2, nm2 = newdel_addr(u, gname_)
                    if na2 is not None:
                        cls, tier, addr, target, note = 'addr', 'game/newdel-member', na2, None, nm2
                        stats['newdel_member_refs'] += 1
            if cls != 'unresolved' and isig.qual and un.find(GAME_PREFIX) >= 0:
                stats['game1_resolved'] += 1
            if vsb is not None and (un in vslots or (cls == 'unresolved' and isig.variant in ('D1', 'D2'))):
                cls, tier, addr, target, note = vsb.classify(u.host or u.source, u.obj, un, isig, native, def_at,
                                                             (cls, tier, addr, target, note))
            if addr is not None and cls == 'addr':
                ak = kind_of_addr(addr)
                if ak == 'data' and un in tls_undef:   # thread-local: native TLS in the image's .tls layout
                    cls = 'tls'; target = TLS_SYM % addr; tls_addrs.add(addr)
                elif ak == 'data' and addr in iat and un in called_syms(u):
                    # called at an IAT slot's address (a decomp `__declspec(dllimport)` function GCC calls directly):
                    # the call goes to the slot's api, never into the image's IAT data
                    cls = 'import'; target = import_syms.get(addr) or iat[addr][1]
                    stats['iat_calls'] += 1
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
                    cast = None
                    if refdecl and isig.qual and msvc(refdecl).kind == 'func' and not msvc(refdecl).member:
                        cast = casts_of(u).get(isig.qual[-1])
                    if refdecl and (refdecl != defdecl or cast):
                        rs, ds = msvc(refdecl), msvc(defdecl)
                        # an undecorated C name carries no parameter types: read them from the declaring unit's
                        # source and headers; undeclared, a stdcall/fastcall stand-in for the parameters only
                        rc_ = rs.kind == 'cname' and rs.special is None
                        dc_ = ds.kind == 'cname' and ds.special is None and nd['unit']
                        if rc_:
                            rs = c_name_sig(rs, c_decls(u.host or u.source)) or rs
                        if dc_:
                            ds = c_name_sig(ds, c_decls(nd['unit'])) or ds
                        for side_, on_, sg_, other_ in (('ref', rc_, rs, ds), ('def', dc_, ds, rs)):
                            if not on_:
                                continue
                            if sg_.kind == 'func':
                                stats['cname_%s_compared' % side_] += 1
                                continue
                            st_ = c_name_standin(sg_, other_)
                            stats['cname_%s_%s' % (side_, 'standin' if st_ else 'unknown')] += 1
                            if st_ is not None and side_ == 'ref':
                                rs = st_
                            elif st_ is not None:
                                ds = st_
                        cast_note = None
                        if cast and ds.kind == 'func':
                            # the unit calls the name through a cast to another function-pointer type: the cast's
                            # convention is how x86 passed the arguments
                            casts_, direct = cast
                            vs = {}
                            for cc_, ret_, params_ in sorted(casts_):
                                cv = abi.compare(cast_sig(cc_, ret_, params_, isig.qual[-1]), ds)
                                vs[(cv.kind, tuple(cv.moves))] = (cc_, ret_, params_)
                            dv = abi.compare(rs, ds)
                            if len(vs) > 1 or (direct and (dv.kind, tuple(dv.moves)) not in vs):
                                cast_note = ('the unit casts the name %s and calls it %s' % (
                                    'to %d conventions' % len(casts_) if len(vs) > 1 else 'to another convention',
                                    'directly too' if direct else 'only through casts'))
                                stats['cast_conflicts'] += 1
                            else:
                                cc_, ret_, params_ = next(iter(vs.values()))
                                rs = cast_sig(cc_, ret_, params_, isig.qual[-1])
                                refdecl_key = '%s|cast %s(__%s*)(%s)' % (refdecl, ret_, cc_, params_)
                                stats['cast_refs'] += 1
                        if rs.kind == 'func' and ds.kind == 'func':
                            v = abi.compare(rs, ds)
                            ecx_dead = False
                            if v.kind == 'x86-inconsistent' and ecx_use is not None and (
                                    'ecx-not-passed' in v.classes or 'this not passed by the reference' in v.notes):
                                # does the definition's x86 code use the ecx the reference leaves behind? (V1 c3:
                                # 0x00a75ec0, 0x00bbbe30 and ten this-not-passed pairs never read it)
                                use_ = ecx_use(addr)
                                ecx_dead = use_ is not None and use_['ecx'] not in ('read', 'pass')
                                stats['ecx_code_' + ('unknown' if use_ is None else 'unused' if ecx_dead
                                                     else 'used')] += 1
                                if ecx_dead:
                                    v = abi.compare(rs, ds, ecx_unused=True)
                            if cast_note:
                                v = abi.Verdict()
                                v.kind, v.classes, v.notes = 'x86-inconsistent', ['cast-conflict'], [cast_note]
                            elif cast:
                                v.notes.append('called through a cast: %s' % refdecl_key.split('|cast ', 1)[1])
                                if 'cast-call' not in v.classes:
                                    v.classes.append('cast-call')
                            for c in v.classes:
                                pair_classes[c] += 1
                            if v.kind != 'same':
                                # aggregates by value: ask the layout probe, then decide on bytes (abi2)
                                # synthesized vtable / body objects (`<host>#vtables`) have the host unit's types
                                for sg, unit in ((rs, u.host or u.source), (ds, nd['unit'])):
                                    for c in [c for _, c in abi.params_of(sg)] + [abi.ret_class(sg)]:
                                        if c.startswith('AGG:') and '{anon}' not in c:
                                            probe_req.add((unit, abi2.agg_name(c)))
                                p2 = None if nd['unit'] is None else abi2.compare(rs, ds, lambda t, x=u.host or u.source: layouts.get((x, t)),
                                                  lambda t, x=nd['unit']: layouts.get((x, t)), ecx_unused=ecx_dead)
                                if p2 is not None and (v.kind != 'bridge' or p2.kind != 'bridge') and \
                                        not (v.kind == 'bridge' and 'narrow-extend' in v.classes) and \
                                        'cast-conflict' not in v.classes:
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
                                bkey = refdecl_key if cast and not cast_note else refdecl
                                # an x86-inconsistent ecx-not-passed or pop-mismatch pair still gets the moves of the
                                # arguments the reference does pass (the definition's ecx parameter is often unused:
                                # 0x00bbbe30, 0x00a75ec0; a callee popping other stack bytes than the caller pushed
                                # still reads the arguments both declare, unless something else is wrong on x86:
                                # this not passed, sizes, casts, the result pointer); the pair stays listed
                                ekind = 'bridge' if v.kind == 'x86-inconsistent' and (
                                    ('ecx-not-passed' in v.classes and (v.moves or v.plan is not None)) or (
                                        'pop-mismatch' in v.classes and (v.moves or (v.plan is not None and
                                                                                     v.plan.moves)) and not {
                                            'this-not-passed', 'size-differs', 'cast-conflict',
                                            'result-pointer-not-passed'} & set(v.classes))) else v.kind
                                if ekind == 'bridge' and v.plan is not None:
                                    bn = abi.bridge_name(addr, bkey)
                                    code = abi2.emit(bn, target, v.plan, 'ref: %s | def: %s'
                                                     % (ms[refdecl] or refdecl, ms[defdecl] or defdecl))
                                    if code is None:
                                        cls = 'code-manual'
                                    else:
                                        bridges.setdefault(bn, code)
                                        target = bn
                                        cls = 'code-bridge'
                                elif ekind == 'bridge':
                                    bn = abi.bridge_name(addr, bkey)
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
                elif ak == 'text' and note and re.search(r'@@3[QD]', note) and msvc(note).kind == 'data' and \
                        un not in called_syms(u):
                    # a variable the decomp places at a .text address nothing defines (the generated initializers'
                    # `extern const char g_011c5a4c_arg1[]` at 0x00a00000: an immediate the generator took for an
                    # address): the code uses its address, which on x86 is that number - an absolute symbol there
                    # gives the native code the same value (S2c-abi, S13b native-stub-data). Only arrays (`@@3Q`)
                    # and char labels (`@@3D`, `&FUN_00a00000`), whose address is all the code uses; an `int` at
                    # 0x00000065 stays extern-text
                    cls = 'text-addr'; target = TEXTADDR_SYM % addr
                    text_addrs.add(addr)
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
            if cls in bodies.TRAPPED and un in body_bind:   # S2d: same function at an address, another name
                cls, target = body_bind[un]
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
    alias_lines += ['%s = 0x%08x;' % (TEXTADDR_SYM % a, a) for a in sorted(text_addrs)]
    for name, srcs in strong.items():
        if len(srcs) > 1:
            for s in sorted(srcs)[1:]:
                localize[s].append(name)
    stats['duplicate_strong_names'] = sum(1 for s in strong.values() if len(s) > 1)
    data_def_names = collections.defaultdict(dict)   # source -> {native name: address symbol it becomes}
    for a, name, src in data_defs:
        if name in data_def_names[src]:
            continue
        if ADDR_SYM % a not in data_def_names[src].values():  # objcopy allows one old name per new name
            data_def_names[src][name] = ADDR_SYM % a
            used_addr.add(a)
        else:
            # a second native definition of the same image variable in this object (tesform.cpp: `DAT_011c54cc`
            # and `fnv_00485a70::DAT_011c54cc`, S13b split-global): it becomes its own weak symbol, which aliases.ld
            # defines as the address symbol, so neither copy stays private (S2c-abi)
            k = sum(1 for v in data_def_names[src].values() if v.startswith(ADDR_SYM % a + '_d')) + 1
            dup = '%s_d%d' % (ADDR_SYM % a, k)
            data_def_names[src][name] = dup
            alias_lines.append('%s = %s;' % (dup, ADDR_SYM % a))
            stats['data_defs_same_address'] += 1
    for a, name, src in tls_defs:   # thread-local definitions at an image .tls address: the native block's slot
        if TLS_SYM % a not in data_def_names[src].values():
            data_def_names[src][name] = TLS_SYM % a
            tls_addrs.add(a)
    defs_by_unit = collections.defaultdict(list)
    for a, nd in native.items():
        if nd.get('msvc_entry'):
            alias_lines.append('%s = %s;' % (MSVC_ENTRY_SYM % a, nd['msvc_entry'][0]))
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
    stats['overrides'] = len(ov_rows)
    with open(os.path.join(out, 'overrides.list'), 'w') as f:
        for obj in sorted({o for _, _, o in ov_rows if o}):
            f.write(obj + '\n')
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
    hand = hand_vptr_stores(os.path.dirname(os.path.dirname(os.path.abspath(units_json))), real, vnames, msvc,
                            canon_msvc)
    # the class constructed (or destroyed) at a storing function's address, by every name units.json gives it
    # (`??0X`, `??1X`, `??_GX`, `??_EX` in any unit): a decomp shell storing an image vtable is often the
    # constructor another unit names properly (S5 J-0102: 750 hand-stored vptrs had no native vtable on f13)
    names_at, units_of = collections.defaultdict(set), collections.defaultdict(set)
    for u_ in units.values():
        for a_, n_ in u_.defs:
            names_at[a_].add(n_)
            units_of[n_].add(u_.source)
        for n_, a_ in u_.refs.items():
            names_at[a_].add(n_)
            units_of[n_].add(u_.source)
    compiled_ = {u_.source for u_ in units.values() if u_.obj and not u_.host}
    ctor_of = {}

    def ctor_classes(fa):
        if fa not in ctor_of:
            got = {}
            for n_ in sorted(names_at.get(fa, ())):
                if not n_.startswith(('??0', '??1', '??_G', '??_E')):
                    continue
                sg = msvc(n_)
                if sg.kind == 'func' and sg.qual and len(sg.qual) >= 2 and sg.qual[-1] in ('{ctor}', '{dtor}', '{sdd}',
                                                                                           '{vdd}'):
                    text_ = '::'.join(sg.qual[:-1])
                    got.setdefault(canon_msvc(text_), text_)
            ctor_of[fa] = got
        return ctor_of[fa]
    hand_req = {}   # class -> (spelling, host units) for classes constructed at a storing address, no vtable yet
    for a, sites in hand.items():
        if a in vmap:
            continue
        for unit, _, fa, _ in sites:
            for cc, text_ in ctor_classes(fa).items() if fa is not None else ():
                hosts = sorted(set().union(*(units_of[n_] for n_ in names_at[fa])) & compiled_)
                e = hand_req.setdefault(cc, (text_, []))
                for h_ in [unit] + hosts:
                    if h_ not in e[1]:
                        e[1].append(h_)
    write_vtable_requests(out, real, objsyms, vnames, it, itanium, hand_req)
    # hand-written vptr stores of image vftable addresses (`obj.vtbl = &DAT_010176f8;`): the address stands for the
    # native vtable of the class constructed there - the one of the same name when some unit names that vftable,
    # else the class whose member function stores it, else the class constructed or destroyed at the storing
    # function's address; its data symbol is bound to that address point (D7, point 2)
    by_cls = collections.defaultdict(list)
    for n in rtti.where:
        if n.startswith('_ZTV'):
            by_cls[canon_it(n)].append(n)
    rebind, hand_rows = {}, []
    for a, sites in sorted(hand.items()):
        how = 'named vftable' if a in vmap else None
        if a not in vmap:
            for unit, cls, _, off in sites:
                z = sorted(by_cls.get(cls or '', []))
                pts = [p for p, ott in (vtables.address_points(rtti.ents(z[0]) or []) if z else []) if ott == -off]
                if pts:
                    obj = rtti.where[z[0]][0]
                    vmap[a] = (z[0], pts[0], obj.path, '%s, hand-written vptr store in %s (%s)'
                               % ('primary' if not off else 'secondary at +%d' % off, unit, cls))
                    how = 'class of the storing function'
                    vstats['mapped-hand-written'] += 1
                    break
        want_ = set()
        if a not in vmap:
            # the class constructed / destroyed at the storing function's address; of several, the native vtable
            # with the most slots (a call through the rewritten vptr must find its slot), then the name
            best = None
            for unit, _, fa, off in sites:
                for cc in (ctor_classes(fa) if fa is not None else {}):
                    want_.add(cc)
                    for z in sorted(by_cls.get(cc, [])):
                        ents = rtti.ents(z) or []
                        pts = [p for p, ott in vtables.address_points(ents) if ott == -off]
                        if pts:
                            k_ = (-len(ents), z)
                            if best is None or k_ < best[0]:
                                best = (k_, z, pts[0], rtti.where[z][0].path, unit, fa, cc)
            if best is not None:
                _, z, pt, path, unit, fa, cc = best
                vmap[a] = (z, pt, path, 'primary, hand-written vptr store in %s at %08x, which constructs %s'
                           % (unit, fa, cc))
                how = 'class constructed at the storing address'
                vstats['mapped-hand-written-by-address'] += 1
        if a in vmap:
            rebind[a] = vmap[a][:2]
        elif want_:
            how = 'requested for synthesis: ' + ' '.join(sorted(want_))
        hand_rows.append(('%08x' % a, how or 'no native vtable', vmap[a][0] if a in vmap else '',
                          ' '.join(sorted({u for u, _, _, _ in sites})),
                          ' '.join(sorted({c or '?' for _, c, _, _ in sites})),
                          ' '.join(sorted({'%08x' % fa for _, _, fa, _ in sites if fa is not None})),
                          no_candidate_why(sites, ctor_classes) if a not in vmap and not want_ else ''))
    stats['hand_vptr_by_address'] = sum(1 for r in hand_rows if r[1] == 'class constructed at the storing address')
    stats['hand_vptr_requested'] = sum(1 for r in hand_rows if r[1].startswith('requested for synthesis'))
    with open(os.path.join(out, 'hand_vptrs.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#address\tmapped by\tnative vtable\tunits\tclasses of the storing functions\t'
                'storing function addresses\twhy no candidate\n')
        for r in hand_rows:
            f.write('\t'.join(r) + '\n')
    stats['hand_vptr_addrs'], stats['hand_vptr_rebound'] = len(hand), len(rebind)
    # primary image vftables several classes name (MSVC folds identical vftables: 0x01012120 is FUN_00404920's,
    # FUN_00404950's, ...): the map can give one native vtable; every class's own is listed, so a native vptr store
    # can be traced back to its image vftable (S13's vtable-store comparison)
    named = collections.defaultdict(set)
    for (n_, a_), (cls_, base_, _) in vnames.items():
        if base_ is None:
            named[a_].add(cls_)
    with open(os.path.join(out, 'vftable_aliases.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#address\tclass\tnative vtable\taddress point\tin the address map\n')
        for a_ in sorted(x for x in named if len(named[x]) > 1 and x in vmap):
            for cls_ in sorted(named[a_]):
                z = sorted(by_cls.get(cls_, []))
                pts = vtables.address_points(rtti.ents(z[0]) or []) if z else []
                f.write('%08x\t%s\t%s\t%s\t%s\n' % (a_, cls_, z[0] if z else '', pts[0][0] if pts else '',
                                                    'yes' if z and vmap[a_][0] == z[0] else 'no'))
                stats['vftable_aliases'] += 1

    # ---- data addresses: absolute symbols at the original address (the data image is mapped there, D3)
    with open(os.path.join(out, 'aliases.ld'), 'w') as f:
        f.write('/* fnv-nx link-by-address aliases (generated by tools/link/fnvlink.py; local output) */\n')
        for a in sorted(x for x in used_addr if kind_of_addr(x) == 'data'):
            # an image vftable (a V entry, whether or not a vptr store was recognised: S5 J-0109 S2:mapped): every
            # data reference to it is the native address point
            rb_ = rebind.get(a) or (vmap[a][:2] if a in vmap else None)
            if rb_:
                f.write('%s = %s + %d;\n' % (ADDR_SYM % a, rb_[0], rb_[1]))
                stats['vtable_data_refs_rebound'] += 1
                stats['vtable_data_refs_rebound_unrecognised'] += a not in rebind
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
                                         'code-defmissing', 'extern-text', 'text-addr', 'import', 'provider', 'tls'))
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
    if stats_ov:
        lines += ['override rows: ' + ', '.join('%s %d' % kv for kv in sorted(stats_ov.items()))]
    lines += ['', 'MSVC entries at a secondary base (exe vftables, thisadj.py): %d addresses; the map names the '
              '_ZThn thunk for %d, %d without a thunk' % (stats['msvc_entry_exe'], stats['msvc_entry_thunks'],
                                                         stats['msvc_entry_no_thunk'])]
    lines += ['', 'variables defined natively at a data-image address: %d (%d copies in objects whose unit does not '
              'name the address: STATIC1 header variables; %d second definitions of an address in one object)'
              % (len(data_defs), stats['rebound_copies'], stats['data_defs_same_address']),
              'address map entries by kind: %s' % ' '.join('%s=%d' % kv for kv in sorted(amap_kinds.items())),
              'deleting destructors (G) with both D1 and D0: %d of %d' % (n_deldtor, amap_kinds.get('G', 0)),
              'synthesized vtable objects linked: %d' % (len(have) - len(real)),
              'image vftable names (%d (name, address) pairs, %d secondary) -> native address points, by outcome:'
              % (len(vnames), sum(1 for v in vnames.values() if v[1]))]
    lines += ['  %-40s %d' % kv for kv in sorted(vstats.items())]
    lines += ['', 'definitions matched to native symbols (GCC .localalias symbols skipped: %d):' % stats_localalias]
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
              'calls of an IAT slot address bound to its import: %d' % stats['iat_calls'],
              'data references bound by identifier only (another scope or decorated type): %d; identifiers at several '
              'addresses (left ambiguous): %d' % (stats['ident_addr'], stats['ident_ambiguous']),
              'ecx-not-passed / this-not-passed pairs judged on the definition\'s MSVC code (--msvc-code/-objs): ecx '
              'unused, bridged: %d; used, flagged: %d; no code: %d' % (stats['ecx_code_unused'],
                                                                      stats['ecx_code_used'], stats['ecx_code_unknown']),
              'a class\'s own operator new/delete bound to the game allocator: %d; unit-local namespaced copies '
              '(fnv_XXXXXXXX::) bound like the global class: %d by address, %d to its native definition'
              % (stats['newdel_member_refs'], stats['unit_ns_bound'], stats['unit_ns_aliased']),
              'C-named declarations compared from their unit\'s source and headers: %d references, %d definitions; '
              'undeclared, compared through an integer stand-in (the other side\'s result): %d / %d; not compared: '
              '%d / %d' % (stats['cname_ref_compared'], stats['cname_def_compared'], stats['cname_ref_standin'],
                           stats['cname_def_standin'], stats['cname_ref_unknown'], stats['cname_def_unknown']),
              'references called through a cast to another function type (the cast decides): %d, conflicting casts '
              'or direct calls (x86-inconsistent): %d' % (stats['cast_refs'], stats['cast_conflicts']),
              'GAME1 names (fnvnx_game_*) looked up by the unit\'s own name: %d references, %d resolved'
              % (stats['game1_refs'], stats['game1_resolved']),
              'operator new/delete references bound to the game\'s allocator: %d' % stats['newdel_refs'],
              'S1b units_renames.tsv applied: %d definitions renamed, %d reference names added'
              % (stats['units_renames_def'], stats['units_renames_ref']),
              'data references to an image vftable bound to its native address point: %d (%d whose vptr store was '
              'not recognised)' % (stats['vtable_data_refs_rebound'], stats['vtable_data_refs_rebound_unrecognised']),
              'image vftable addresses stored into vptrs by hand: %d, bound to a native address point: %d (by the '
              'class constructed at the storing address: %d); classes requested for synthesis: %d (hand_vptrs.tsv)'
              % (stats['hand_vptr_addrs'], stats['hand_vptr_rebound'], stats['hand_vptr_by_address'],
                 stats['hand_vptr_requested']),
              'classes sharing a primary image vftable (MSVC folded; vftable_aliases.tsv): %d rows'
              % stats['vftable_aliases'],
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
# `*(int *)this = (int)&DAT_x;` / `((unsigned *)p)[0] = ...`: a word store at offset 0 through a cast to a pointer to a
# 4-byte integer or pointer (S5 J-0102: bgsragdoll.cpp's 0x010472d0); only for addresses near the named vftables
DEREF1_LHS = re.compile(r'^\s*\*\s*\(\s*(?:const\s+|volatile\s+|unsigned\s+|signed\s+)*(?:int|long|uint|uint32_t|UInt32|DWORD|'
                        r'undefined4|uintptr_t|size_t|unsigned|void\s*\*|char\s*\*)\s*\*\s*\)\s*\(?\s*\w+\s*\)?\s*$'
                        r'|^\s*\(\s*\(\s*(?:const\s+|unsigned\s+)*(?:int|long|uint|uint32_t|UInt32|DWORD|undefined4|uintptr_t|'
                        r'unsigned)\s*\*\s*\)\s*\w+\s*\)\s*\[\s*0\s*\]\s*$')
FNV_ANNOT = re.compile(r'@fnv\s+(0x[0-9a-fA-F]+)')
# a word store at a nonzero offset of an object: a secondary vptr (S5 J-0109: creature.cpp's
# `*(void **)((char *)this + 0xa8) = &DAT_01086f9c`) -> the offset
DEREFN_LHS = re.compile(r'^\s*\*\s*(?:\(\s*[\w\s:]+\*\s*(?:\*\s*)?\)|reinterpret_cast\s*<[^<>]*\*\s*>)\s*\(\s*\(\s*(?:const\s+)?'
                        r'(?:char|unsigned\s+char|uint8_t|BYTE|undefined)\s*\*\s*\)\s*\w+\s*\+\s*(0x[0-9a-fA-F]+|\d+)\s*\)\s*$')
DEREFK_LHS = re.compile(r'^\s*\(\s*\(\s*(?:const\s+|unsigned\s+)*(?:int|long|uint|uint32_t|UInt32|DWORD|undefined4|uintptr_t|'
                        r'unsigned|void\s*\*)\s*\*\s*\)\s*\w+\s*\)\s*\[\s*(0x[0-9a-fA-F]+|\d+)\s*\]\s*$')
DAT_NAME = re.compile(r'^(?:DAT|PTR|PTR_DAT|vftable|vtbl|vtable)_([0-9a-fA-F]{8})$')
FUNC_HDR = re.compile(r'^[^\n;{}#]*?\b((?:\w+\s*::\s*)*\w+)\s*::\s*~?\w+\s*\([^;{}]*\)\s*(?:const\s*)?\{', re.M)
# a free function defined at column 0: a function head without a class (S13: a vptr store in the free function
# InitLargePrimitiveArray after FUN_00404920's constructor was credited to FUN_00404920)
FREE_HDR = re.compile(r'^(?![ \t]|//)(?![^\n;{}#(]*::)[^\n;{}#(]*?\b(?!(?:if|for|while|switch|catch|return)\b)\w+\s*'
                      r'\([^;{}]*\)\s*(?:const\s*)?\{', re.M)


def no_candidate_why(sites, ctor_classes):
    """Why a hand-stored image vtable has no candidate class (S5 J-0108 item 3): what a candidate would need."""
    if all(fa is None for _, _, fa, _ in sites):
        return 'no storing address: the function stores several vtables at one offset, or has no @fnv annotation'
    names = [n for _, _, fa, _ in sites if fa is not None for n in ctor_classes(fa)]
    if not names:
        return 'no constructor/destructor name for the storing address in any unit (a shell only)'
    return 'candidates without a vtable'


def hand_vptr_stores(tree, real, vnames, msvc, canon_msvc):
    """Hand-written stores of image vftable addresses into vptrs in the unit sources: `x.vtbl = &DAT_010176f8;`,
    `*(void **)this = kVtbl_X;`, ... -> {address: [(unit, canonical class of the storing member function or
    None)]}. A store counts when the right side is (after casts and &) a name the unit binds to a data address and
    the left side looks like a vptr (vtbl/vtable/vptr/vft... member, *(T **)p, ((T **)p)[0]), or the address is a
    vftable some unit names (??_7) whatever the left side is called."""
    known = {a for (_, a) in vnames}
    near = (min(known) - 0x10000, max(known) + 0x10000) if known else (1, 0)
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
        heads = sorted([(m.start(), m.group(1)) for m in FUNC_HDR.finditer(text)] +
                       [(m.start(), None) for m in FREE_HDR.finditer(text)], key=lambda h: h[0])
        fnvs = [(m.start(), int(m.group(1), 16)) for m in FNV_ANNOT.finditer(text)]
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
            if a is None and DAT_NAME.match(rhs):   # a name units.json does not bind for this unit (S5 J-0109)
                a = int(DAT_NAME.match(rhs).group(1), 16)
                if not (kind_of_addr(a) == 'data' and near[0] <= a <= near[1]):
                    a = None
            if a is None:
                continue
            lhs = m.group('lhs').strip()
            off = 0
            mo = DEREFN_LHS.match(lhs) or DEREFK_LHS.match(lhs)
            if mo:
                off = int(mo.group(1), 0) * (4 if mo.re is DEREFK_LHS else 1)
            if not (a in known or VPTR_LHS.search(lhs) or DEREF_LHS.match(lhs) or
                    ((DEREF1_LHS.match(lhs) or (mo and off)) and near[0] <= a <= near[1])):
                continue
            cls, fn, hpos_ = None, -1, 0
            for k, (hpos, hcls) in enumerate(heads):
                if hpos > spos:
                    break
                cls, fn, hpos_ = hcls, k, hpos
            # the storing function's original address: its `// @fnv 0x...` annotation, the last one before the
            # store (prepare keeps them above each definition)
            fa = max(((p_, x) for p_, x in fnvs if p_ < spos), default=(None, None))[1]
            sites.append((a, u.source, canon_msvc(cls) if cls else None, (u.source, fn), fa, off))
    per_fn = collections.defaultdict(set)
    for a, _, _, fn, _, off in sites:
        per_fn[(fn, off)].add(a)
    for a, unit, cls, fn, fa, off in sites:
        # the storing function's class and address only count for a function that stores one vftable at that
        # offset (a destructor resetting its own and then its base's vftable stores two)
        one = len(per_fn[(fn, off)]) == 1
        out[a].append((unit, cls if one else None, fa if one else None, off))
    return out


NOT_SYNTH = ('std::', '__gnu_cxx::', '__cxxabiv1::')   # the C++ runtime defines its own


def write_vtable_requests(out, real, objsyms, vnames, it, itanium, hand_req=None):
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
    for cls, (text, hosts) in sorted((hand_req or {}).items()):   # constructed where an image vtable is stored
        if cls not in have_ztv:
            for h in hosts:
                want(cls, text, 'hand-vptr', h)
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


def override_key(text):
    """An override row's first column: a hex address, or `vslot:<vftable>:<slot>` (the MSVC vftable symbol or its
    hex address, the slot index) for the function an image vftable holds in that slot -> int or ('vslot', vftable,
    slot)."""
    if text.startswith('vslot:'):
        vft, _, slot = text[6:].rpartition(':')
        return ('vslot', vft, int(slot, 0))
    return int(text, 16)


def vslot_address(vsb, key):
    """(address, '') of the function an image vftable holds in a slot, from the MSVC vftables (vslots.MsvcIndex:
    resolve --msvc-objs), or (None, why)."""
    _, vft, slot = key
    if vsb is None:
        return None, 'needs --msvc-objs (the vftable contents)'
    if re.match(r'^(0x)?[0-9a-fA-F]+$', vft):
        a = int(vft, 16)
    else:
        addrs = vsb.ix.gref.get(vft, set())
        if len(addrs) != 1:
            return None, 'vftable %s' % ('ambiguous' if addrs else 'not named by units.json')
        a = next(iter(addrs))
    tab = vsb.ix.table(a)
    if not tab:
        return None, 'no MSVC object emits the vftable at %08x' % a
    if slot >= len(tab):
        return None, 'slot beyond the vftable (%d slots)' % len(tab)
    if tab[slot][0] is None:
        return None, 'slot target undecided (views disagree)'
    return tab[slot][0], ''


def is_object_path(text):
    return text.endswith(('.o', '.a', '.obj')) or (os.sep in text and os.path.isfile(text))


def load_overrides(path, lib=None):
    """Override table (tab-separated, '#' comments): address or vslot:<vftable>:<slot>, native symbol, then either
    the object or archive that defines it or free text (the native signature, notes: render/native/overrides.tsv),
    [an MSVC-decorated signature for the references]. -> [(address or ('vslot', vftable, slot), symbol, object)]:
    the object is column 3 when it names one, else `lib` (--override-lib), else None."""
    out = []
    if path:
        for line in open(path, encoding='utf-8', errors='surrogateescape'):
            f = line.rstrip('\n').split('\t')
            if line.startswith('#') or len(f) < 2 or not f[0].strip():
                continue
            obj = f[2] if len(f) > 2 and is_object_path(f[2]) else lib
            out.append((override_key(f[0]), f[1], obj))
    return out


def override_pending(path):
    """Keys of the rows whose notes say N1-pending (S6n: the symbol is planned, not built yet)."""
    out = set()
    if path:
        for line in open(path, encoding='utf-8', errors='surrogateescape'):
            f = line.rstrip('\n').split('\t')
            if not line.startswith('#') and len(f) > 2 and 'N1-pending' in '\t'.join(f[2:]):
                out.add(override_key(f[0]))
    return out


def msvc_code_use(units_json, ms, msvc, renames=()):
    """f(original address) -> how the pristine MSVC code there uses the incoming ecx / edx (vslots.Code.entry_use),
    from FNVNX_MSVC_CODE or FNVNX_MSVC_OBJS (`--msvc-code` / `--msvc-objs`: S11's u<index>.obj); None without them.
    A callee takes the registers its own pristine code uses, else those its declaration puts there
    (abi.x86_locations); a call through a function-pointer variable takes any. S1b's renamed references
    (units_renames rows) are found under their old, pristine names; a renamed definition uses the registers of its
    patched declaration."""
    d = os.environ.get('FNVNX_MSVC_CODE') or os.environ.get('FNVNX_MSVC_OBJS')
    if not d or not os.path.isdir(d):
        return None
    try:
        import types
        uu = json.load(open(units_json))['units']
        code = msvc_vslots.Code(types.SimpleNamespace(units=uu), d)
    except ImportError as e:
        log('--msvc-code: %s (capstone): register reads not checked' % e)
        return None
    by_src = {u['source']: i for i, u in enumerate(uu)}
    code.aliases = [(int(r[2], 16), r[3], by_src.get(r[0])) for r in renames if len(r) >= 5]
    code.renamed = {int(r[2], 16) for r in renames if len(r) >= 5 and r[1] == 'def'}

    def arg_bytes(n, slot=False):
        """(stack argument bytes, bytes the callee pops) of a callee by its declaration, or None."""
        if slot:
            if not n.startswith('__imp_'):
                return None
            n = n[6:]
        if n.startswith('?'):
            s = msvc(n)
            if not s or s.kind != 'func':
                return None
            nb = abi.x86_stack_bytes(s)
            return None if nb is None else (nb, nb if (s.cc or 'cdecl') in abi.CALLEE_POP else 0)
        m = re.match(r'^(@|_)[^@]+@(\d+)$', n)
        if m:
            nb = int(m.group(2))
            nb = max(0, nb - 8) if m.group(1) == '@' else nb
            return nb, nb
        return None

    def takes(n, slot=False):
        if slot:
            if not n.startswith('__imp_'):
                return None
            n = n[6:]
        if n.startswith('?'):
            s = msvc(n)
            return {loc for _, _, loc in abi.x86_locations(s) if loc in ('ecx', 'edx')} if s and s.kind == 'func' \
                else None
        m = re.match(r'^@[^@]+@(\d+)$', n)
        if m:
            k = int(m.group(1)) // 4
            return {'ecx', 'edx'} if k >= 2 else {'ecx'} if k else set()
        return set()

    def need(names):
        names = [n[6:] if n.startswith('__imp_?') else n for n in names]
        missing = sorted({n for n in names if n.startswith('?') and n not in ms.map})
        if missing:
            ms.map.update(batch_demangle(UNDNAME, missing))
    cache = {}

    def use(a):
        if a not in cache:
            cache[a] = code.entry_use(a, takes, need, arg_bytes)
        return cache[a]
    return use


def load_override_sigs(path):
    """Override table rows with a 4th column: {address: MSVC-decorated signature of the override}. References are
    compared with it instead of the decomp definition's (an override whose signature differs on purpose, e.g. a
    variadic replacement of a fixed-argument definition)."""
    out = {}
    if path:
        for line in open(path, encoding='utf-8', errors='surrogateescape'):
            f = line.rstrip('\n').split('\t')
            if not line.startswith('#') and len(f) >= 4 and re.match(r'^[?@_][^\s]*$', f[3]):
                out[override_key(f[0])] = f[3]
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


def classify_ref(un, isig, idx, gdef, gref, anydef, msvc, ident=None):
    """-> (class, tier, address, target, note). class 'addr' = an address was found (refined by the caller).
    ident: {data identifier: image addresses} - the last resort for a data reference (S5): the same identifier in
    another scope (the decomp's per-function namespaces) or with another decorated type (an array declared where
    MSVC saw a pointer, cv), bound when exactly one address carries it, 'ambiguous' otherwise."""
    if RUNTIME.match(un) and not (NEWDEL_ANY.match(un) and not NEWDEL_STD.match(un)):
        return 'runtime', None, None, None, None
    if isig.kind == 'special':
        return ('native' if un in anydef else 'special-unresolved'), None, None, None, isig.special
    tier, hit = idx.lookup(isig)
    scope = 'unit'
    if not hit:
        for g, name in ((gdef, 'gdef'), (gref, 'gref')):
            t, h = g.lookup(isig)
            # a looser parameter match (l2: typedef / cv spellings, arity) only when one address carries it anywhere
            # (J-0108: FindKeyInterval, DecodeBlock, FinishScriptRunLock ...); the ABI check then compares them
            if h and t in ('exact', 'l1', 'cname', 'name', 'l2', 'arity') and len(h) == 1:
                tier, hit, scope = t, h, name
                break
    if not hit:
        if un in anydef:
            return 'native', None, None, None, None
        m = ADDR_IN_NAME.match(isig.qual[-1]) if isig.qual else None
        if m:  # Ghidra-style name carrying its address: right for 99.6 % of the names units.json binds (LINK.md)
            return 'addr', 'name-addr', int(m.group(1), 16), None, None
        if ident is not None and isig.kind in ('data', 'cname') and isig.qual and ident.get(isig.qual[-1]):
            a_ = ident[isig.qual[-1]]
            if len(a_) == 1:
                return 'addr', 'gref/ident', next(iter(a_)), None, None
            return 'ambiguous', 'gref/ident', None, None, ' '.join('%08x' % x for x in sorted(a_))
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


# ---------------------------------------------------------------------------------------------------- casts

# a named function reached through a cast to a function-pointer type: the cast's convention and parameters decide
# how the x86 code called it, not the unit's declaration of the name (S13: GetOrLoadXML calls the fastcall-declared
# FUN_00a0c900 through a __thiscall cast)
_CAST_TYPE = r'(?P<ret>[A-Za-z_][\w:<>,\s\*&]*?)\s*\(\s*(?:__(?P<cc>thiscall|fastcall|stdcall|cdecl)\s*)?\*\s*\)\s*' \
             r'\((?P<params>[^()]*)\)'
CAST_C = re.compile(r'\(\s*' + _CAST_TYPE + r'\s*\)\s*&?\s*(?P<name>[A-Za-z_]\w*)(?=\s*[),;])')
CAST_R = re.compile(r'reinterpret_cast\s*<\s*' + _CAST_TYPE + r'\s*>\s*\(\s*&?\s*(?P<name>[A-Za-z_]\w*)\s*\)')
CAST_TYPEDEFS = {'UInt8': 'unsigned char', 'UInt16': 'unsigned short', 'UInt32': 'unsigned int', 'SInt8': 'char',
                 'SInt16': 'short', 'SInt32': 'int', 'BYTE': 'unsigned char', 'WORD': 'unsigned short',
                 'DWORD': 'unsigned long', 'BOOL': 'int', 'UINT': 'unsigned int', 'INT': 'int', 'LONG': 'long',
                 'ULONG': 'unsigned long', 'HRESULT': 'long', 'size_t': 'unsigned int', 'uint32_t': 'unsigned int',
                 'int32_t': 'int', 'uint16_t': 'unsigned short', 'uint8_t': 'unsigned char', 'LPVOID': 'void *',
                 'HANDLE': 'void *', 'HWND': 'void *', 'LPCSTR': 'char const *', 'LPSTR': 'char *'}


def split_top(text):
    out, depth, cur = [], 0, ''
    for ch in text:
        if ch in '<([':
            depth += 1
        elif ch in '>)]':
            depth -= 1
        if ch == ',' and depth == 0:
            out.append(cur); cur = ''
        else:
            cur += ch
    out.append(cur)
    return [x.strip() for x in out if x.strip()]


def cast_sig(cc, ret, params, name):
    """MSVC-model Sig of a call through `ret (__cc *)(params)` (a __thiscall pointer passes its first parameter in
    ecx, like a member function's this)."""
    def norm(t):
        return re.sub(r'\b(%s)\b' % '|'.join(CAST_TYPEDEFS), lambda m: CAST_TYPEDEFS[m.group(1)], ' '.join(t.split()))
    ps = split_top(params)
    if ps == ['void']:
        ps = []
    sig = sigs.Sig('func', '%s __%s %s(%s)' % (ret, cc or 'cdecl', name, params))
    sig.cc, sig.qual, sig.raw_ret, sig.static = cc or 'cdecl', [name], norm(ret), False
    if (cc or 'cdecl') == 'thiscall' and ps:
        sig.member, ps = True, ps[1:]
    sig.raw_params = [norm(x) for x in ps]
    sig.params = list(sig.raw_params)
    return sig


TYPE_WORDS = {'int', 'char', 'short', 'long', 'float', 'double', 'void', 'bool', 'unsigned', 'signed', 'const',
              'volatile', 'struct', 'class', 'union', 'enum', '__int64', 'wchar_t'}
CODE_NOISE = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|^[ \t]*#(?:[^\n]*\\\n)*[^\n]*',
                        re.S | re.M)
# specifiers before a declaration's result type; __declspec(...) / __attribute__((...)) end in a parenthesis, so no
# word boundary after them (V1 R4-2: `__declspec(noreturn) void f();` was not read)
DECL_SPEC = re.compile(r'\b(?:extern|static|inline|__inline|__forceinline|virtual|friend|FNVNX_KEEP|WINAPI|CALLBACK|'
                       r'__(?:cdecl|stdcall|fastcall|thiscall))\b|\b__declspec\s*\([^()]*\)'
                       r'|\b__attribute__\s*\(\([^()]*(?:\([^()]*\)[^()]*)*\)\)')
NOT_A_TYPE = {'return', 'else', 'case', 'goto', 'throw', 'new', 'delete', 'sizeof', 'do', 'typedef', 'if', 'while',
              'for', 'switch', 'using', 'operator', 'co_return', 'default'}


def blank_noise(text):
    """Comments, string and character literals and preprocessor lines replaced by blanks (newlines kept): what is
    left is code a declaration can be read from (R3-2: a Ghidra-style signature note is not a declaration)."""
    return CODE_NOISE.sub(lambda m: re.sub(r'[^\n]', ' ', m.group(0)), text or '')


def int_words(t):
    """Canonical MSVC spelling of the integer words of a C type: 'unsigned' -> 'unsigned int', 'long int' -> 'long',
    '__int16' -> 'short', 'signed int' -> 'int'."""
    def canon(m):
        w = m.group(0).split()
        u = 'unsigned ' if 'unsigned' in w else ''
        if 'char' in w or '__int8' in w:
            base = 'char' if u else ('signed char' if 'signed' in w else 'char')
        elif 'short' in w or '__int16' in w:
            base = 'short'
        elif '__int64' in w or w.count('long') >= 2:
            base = '__int64'
        elif 'long' in w:
            base = 'long'
        else:
            base = 'int'
        return u + base + ' '
    return ' '.join(re.sub(r'\b(?:(?:unsigned|signed|short|long|int|char|__int8|__int16|__int32|__int64)\b\s*)+',
                           canon, t).split())


def unit_c_decls(tree, src_, cache):
    """CDecls of a unit and the local headers it includes (\"...\" from the including file's directory, then
    <tree>/include): the unit's own declarations first."""
    src_ = src_.split('#')[0]
    if src_ not in cache:
        texts, seen, todo = [], set(), [os.path.join(tree, src_)]
        while todo and len(seen) < 200:
            f_ = os.path.normpath(todo.pop(0))
            if f_ in seen or not os.path.isfile(f_):
                continue
            seen.add(f_)
            t_ = open(f_, encoding='utf-8', errors='surrogateescape').read()
            texts.append(t_)
            for inc in re.findall(r'^[ \t]*#[ \t]*include[ \t]*"([^"]+)"', t_, re.M):
                todo += [os.path.join(os.path.dirname(f_), inc), os.path.join(tree, 'include', inc)]
        cache[src_] = CDecls('\n;\n'.join(texts))
    return cache[src_]


class CDecls:
    """Free-function declarations and definitions of one source text (a unit and its local headers), for the C names
    the decoration leaves untyped. Typedefs (plain ones, and function-pointer typedefs as `void *`) are expanded,
    class/struct/union/enum names get their tag (classify needs it), integer spellings are canonical."""

    def __init__(self, text):
        self.s = blank_noise(text)
        self.typedefs, self.tags = {}, {}
        self.class_body = []   # (open, close, is a class/struct/union body) of every brace pair
        stack = []
        for m in re.finditer(r'[{}]', self.s):
            if m.group(0) == '{':
                stack.append((m.start(), bool(re.search(r'\b(?:struct|class|union)\s+[A-Za-z_][^;{}()]*$|'
                                                        r'\b(?:struct|class|union)\s*$', self.s[max(0, m.start() - 300):m.start()]))))
            elif stack:
                o, cls = stack.pop()
                self.class_body.append((o, m.start(), cls))
        for m in re.finditer(r'\btypedef\s+[^;{}]*?\(\s*(?:__\w+\s+)?\*\s*([A-Za-z_]\w*)\s*\)\s*\([^;{}]*\)\s*;', self.s):
            self.typedefs[m.group(1)] = 'void *'
        for m in re.finditer(r'\btypedef\s+([^;{}()]+?)\s*\b([A-Za-z_]\w*)\s*;', self.s):
            if m.group(2) not in self.typedefs and m.group(2) not in m.group(1).split():
                self.typedefs[m.group(2)] = ' '.join(m.group(1).split())
        for m in re.finditer(r'\b(struct|class|union|enum)\s+([A-Za-z_]\w*)\s*(?=[{;:])', self.s):
            self.tags.setdefault(m.group(2), m.group(1) if m.group(1) != 'class' else 'struct')

    def ctype(self, t):
        t = ' '.join(t.split())
        for _ in range(6):
            n = re.sub(r'(?<!struct )(?<!class )(?<!union )(?<!enum )\b([A-Za-z_]\w*)\b',
                       lambda m: self.typedefs.get(m.group(1), m.group(1)), t)
            if n == t:
                break
            t = n
        t = re.sub(r'(?<!struct )(?<!class )(?<!union )(?<!enum )(?<!:)\b([A-Za-z_]\w*)\b(?!\s*::)',
                   lambda m: ('%s %s' % (self.tags[m.group(1)], m.group(1))) if m.group(1) in self.tags
                   else m.group(1), t)
        return int_words(t)

    def param(self, p):
        """'T *self' -> 'struct T *', 'void (*cb)(int)' -> 'void (*)(int)', 'char buf[8]' -> 'char *', 'int a = 0' ->
        'int', 'unsigned n' -> 'unsigned int'."""
        p = ' '.join(p.split('=', 1)[0].split())   # a default argument
        p = re.sub(r'\(\s*((?:__\w+\s+)?[*&]+)\s*(?:const\s+)?[A-Za-z_]\w*\s*\)', r'(\1)', p)   # function pointer
        m = re.match(r'^(.*?[\s\*&])([A-Za-z_]\w*)\s*(\[[^\]]*\])?$', p)
        if m and m.group(2) not in TYPE_WORDS and m.group(1).strip() and m.group(1).strip() not in ('const',
                                                                                                    'volatile'):
            p = m.group(1).strip() + (' *' if m.group(3) else '')
        return self.ctype(p)

    def find(self, name):
        """[(result type, [parameter types])] of the declarations of the free function `name`, in text order."""
        out, s = [], self.s
        for m in re.finditer(r'(?<![\w.>:])' + re.escape(name) + r'\s*\(', s):
            depth, j = 0, m.end() - 1
            while j < len(s):
                depth += {'(': 1, ')': -1}.get(s[j], 0)
                if depth == 0:
                    break
                j += 1
            if j >= len(s):
                continue
            tail = re.match(r'\s*(?:const\b\s*)?(?:throw\s*\([^()]*\)\s*)?([;{])', s[j + 1:])
            if not tail:
                continue
            inner = max((b for b in self.class_body if b[0] < m.start() < b[1]), default=None)
            if inner is not None and inner[2]:
                continue                      # declared in a class body: a member, not the C function
            k = max(s.rfind(c, 0, m.start()) for c in ';{}')
            pre = re.sub(r'^\s*(?:(?:public|private|protected)\s*:(?!:))?', '', s[k + 1:m.start()])
            pre = ' '.join(DECL_SPEC.sub(' ', pre).split())
            words = set(re.findall(r'[A-Za-z_]\w*', pre))
            if not pre or not re.match(r'^[A-Za-z_][\w\s\*&:<>,]*$', pre) or words & NOT_A_TYPE or \
                    pre.endswith((':', ',')) or pre.startswith('template'):
                continue                      # a call (`return f(x);`, `a = f(x);`, `case 1: f(x);`), a template
            params = [self.param(x) for x in split_top(s[m.end():j])]
            out.append((self.ctype(pre), [x for x in params if x != 'void']))
        return out


def c_name_sig(csig, decls):
    """MSVC-model Sig for an undecorated C name (@f@N fastcall, _f@N stdcall, _f cdecl) from its declaration or
    definition (CDecls of the unit and its local headers): the parameter types and result the decoration does not
    carry. A declaration whose x86 argument bytes disagree with N is skipped. None when nothing is declared (see
    c_name_standin)."""
    name, cc, nbytes = csig.qual[-1], csig.cc or 'cdecl', csig.stack_bytes
    if isinstance(decls, str):
        decls = CDecls(decls)
    for ret, ps in decls.find(name):
        sig = cast_sig(cc, ret, ', '.join(ps) or 'void', name)
        if nbytes is not None:
            got = sum((abi.X86_SIZE.get(c, 4) + 3) // 4 * 4 for _, c in abi.params_of(sig))
            if got != nbytes:
                continue
        return sig
    return None


def c_name_standin(csig, other):
    """An undeclared stdcall/fastcall C name compared through N/4 four-byte integer parameters - only when the other
    side passes nothing but 32-bit integers and pointers (an edx dummy, a missing ecx still show) - and the other
    side's own result type: no result kind is assumed (R3-1: an int stand-in result put a float callee's s0 through
    `fmov s0, w0`). None otherwise: the pair is not compared."""
    if csig.stack_bytes is None or other is None or other.kind != 'func' or \
            any(c not in ('I8', 'I16', 'I32', 'P') for _, c in abi.params_of(other)):
        return None
    sig = cast_sig(csig.cc or 'cdecl', other.raw_ret or 'void', ', '.join(['int'] * (csig.stack_bytes // 4)) or 'void',
                   csig.qual[-1])
    sig.raw_ret = other.raw_ret
    return sig


# a function-pointer type named by a typedef or alias (S1b J-0112: `reinterpret_cast<AlphaFlag>(f)`,
# `((FUN_00653180_type)f)`)
FPTR_TYPEDEF = re.compile(r'\btypedef\s+(?P<ret>[A-Za-z_][\w:<>,\s\*&]*?)\s*\(\s*(?:__(?P<cc>thiscall|fastcall|stdcall|cdecl)\s*)?'
                          r'\*\s*(?P<tname>[A-Za-z_]\w*)\s*\)\s*\((?P<params>[^()]*)\)\s*;'
                          r'|\busing\s+(?P<uname>[A-Za-z_]\w*)\s*=\s*(?P<uret>[A-Za-z_][\w:<>,\s\*&]*?)\s*\(\s*'
                          r'(?:__(?P<ucc>thiscall|fastcall|stdcall|cdecl)\s*)?\*\s*\)\s*\((?P<uparams>[^()]*)\)\s*;')


def unit_casts(text):
    """{name: (set of (cc, ret, params) casts, direct calls of the name outside casts)} for one source text."""
    hits, spans = collections.defaultdict(set), []
    for rx in (CAST_C, CAST_R):
        for m in rx.finditer(text):
            hits[m.group('name')].add((m.group('cc') or 'cdecl', ' '.join(m.group('ret').split()),
                                       ' '.join(m.group('params').split())))
            spans.append((m.start(), m.end()))
    tdefs = {}
    for m in FPTR_TYPEDEF.finditer(text):
        if m.group('tname'):
            tdefs[m.group('tname')] = (m.group('cc') or 'cdecl', ' '.join(m.group('ret').split()),
                                       ' '.join(m.group('params').split()))
        else:
            tdefs[m.group('uname')] = (m.group('ucc') or 'cdecl', ' '.join(m.group('uret').split()),
                                       ' '.join(m.group('uparams').split()))
    if tdefs:
        alt = '|'.join(re.escape(t) for t in sorted(tdefs, key=len, reverse=True))
        for rx in (re.compile(r'\(\s*(?P<t>' + alt + r')\s*\)\s*&?\s*(?P<name>[A-Za-z_]\w*)(?=\s*[),;])'),
                   re.compile(r'reinterpret_cast\s*<\s*(?P<t>' + alt + r')\s*>\s*\(\s*&?\s*(?P<name>[A-Za-z_]\w*)\s*\)')):
            for m in rx.finditer(text):
                if m.group('name') in tdefs:
                    continue
                hits[m.group('name')].add(tdefs[m.group('t')])
                spans.append((m.start(), m.end()))
    out = {}
    for name, casts in hits.items():
        direct = 0
        for m in re.finditer(r'\b%s\s*\(' % re.escape(name), text):
            if any(lo <= m.start() < hi for lo, hi in spans):
                continue
            pre = text[max(0, m.start() - 400):m.start()].rstrip()
            if pre.endswith(('->', '.', '::')) or not pre:
                continue                                  # a member of that name / start of file
            if pre[-1] in '=(,;{}?:!|+-/<>%^~[' or re.search(r'\b(?:return|else|case|do)$', pre):
                direct += 1                               # a call (a declaration follows a type or __conv)
        out[name] = (casts, direct)
    return out


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
        if nd.get('msvc_entry'):
            # MSVC enters it with this at the base subobject d (thisadj.py): the map names the thunk's alias
            th, d = nd['msvc_entry']
            rows.append((a, kind, MSVC_ENTRY_SYM % a, th, obj, (note + '; ' if note else '') +
                         'msvc this = base subobject +%d: the _ZThn thunk' % d, str(d), d1l, d1n))
            continue
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
    retargeted, retarget_objs, left, left_objs = 0, 0, 0, 0
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
            k, k_left = elfsyms.retarget_local_relocs(obj, dst + '.rb', rebind)
            retargeted += k
            retarget_objs += bool(k)
            left += k_left
            left_objs += bool(k_left)
            if k_left:
                log('%s: %d code references reach a rebound variable through a section anchor / local symbol (compiled '
                    'without -fPIC?): they keep a private copy' % (base, k_left))
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
    print('own data pointers to rebound variables retargeted: %d relocations in %d objects; code references '
          'through a local symbol left (want 0, compile.sh -fPIC): %d in %d objects' % (retargeted, retarget_objs,
                                                                                      left, left_objs))
    rows, cnt = pair_typeinfo(fixed)
    with open(os.path.join(out, 'typeinfo_pairs.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#class\tkept typeinfo object (the vtable\'s)\tthe copy the linker would keep first\tcopies of '
                'another view turned into references\n')
        for r in rows:
            f.write('\t'.join(r) + '\n')
    print('typeinfo kept with its vtable (V1 C4): %d classes with copies of another view, %d typeinfo definitions '
          'turned into references (typeinfo_pairs.tsv); private base views %d in %d objects, %d base pointers '
          'retargeted; vtable object defines no typeinfo: %d' % (
              len(rows), cnt['undefined'], cnt['private base views'], cnt['objects with private base views'],
              cnt['base pointers retargeted'], cnt['vtable object defines no typeinfo']))


def kept_def(defs):
    """GNU ld's pick among [(object, strong)] in link order: the first strong definition, else the first copy."""
    return next((d for d in defs if d[1]), defs[0])


def pair_typeinfo(fixed):
    """Make each class's kept typeinfo describe the layout its kept vtable was built for (V1 defect C4, round 3).
    Units reconstruct one class with different bases; vtable and typeinfo are separate weak COMDAT groups (_ZTV<C>,
    _ZTI<C>) and the linker keeps the first copy of each in link order independently, so the kept vtable's
    offset-to-top words could name a base the kept typeinfo lacks (__dynamic_cast to it fails).

    * The class's typeinfo is the vtable object's own copy (when it has one), else the first in `check`'s link order.
      Every other copy whose *view* differs - its words and base names, and the same for its bases as that object
      sees them - becomes a reference (elfsyms.undefine_symbols, its group no longer COMDAT), so any link order
      keeps that copy (t2b); copies of the same view stay (they are interchangeable).
    * Base pointers: a kept typeinfo names its bases by global symbol, and the kept base typeinfo can come from
      another unit's view (t4: C : B in both, B's second base differs). Where the object holding the kept typeinfo
      has its own, different view of a base, that copy stays as a local `_ZTI<B>.fnvnx_view` (its global definition
      a reference like above) and the base pointers inside the object's kept typeinfos (and inside those local
      views, recursively) are retargeted to it. typeid(B) and B's own vtable keep the global one; type_info
      equality compares names, so casts between the views still match.
    -> ([(class, kept object, the copy the linker would have kept, objects changed)], Counter)."""
    objs = sorted(n for n in os.listdir(fixed) if n.endswith('.o'))
    vt, ti = collections.defaultdict(list), collections.defaultdict(list)
    for n in objs:
        for s in elfsyms.symbols(os.path.join(fixed, n)):
            if s.shndx and s.bind in ('global', 'weak') and s.name.startswith(('_ZTV', '_ZTI')):
                (vt if s.name[3] == 'V' else ti)[s.name[4:]].append((n, s.bind == 'global'))
    cnt = collections.Counter()
    kept = {}
    for cls, tdefs in ti.items():
        kt = kept_def(tdefs)
        kept[cls] = kt[0]
        vdefs = vt.get(cls)
        if not vdefs:
            continue
        kv = kept_def(vdefs)[0]
        own = dict(tdefs)
        if kv not in own:
            cnt['vtable object defines no typeinfo'] += 1
        elif kt[1] and not own[kv] and kt[0] != kv:
            cnt['a strong typeinfo elsewhere'] += 1   # a key function's unit: leave it to the linker
        else:
            kept[cls] = kv
    defs_in = collections.defaultdict(set)
    for cls, tdefs in ti.items():
        for o, _ in tdefs:
            defs_in[o].add(cls)
    # words and base names of every typeinfo in the objects that hold a copy with a rival, read once per object
    multi = {c for c, ds in ti.items() if len(ds) > 1}
    shapes, bases_of = {}, {}
    for o in objs:
        if not defs_in[o] & multi:
            continue
        ob = vtables.Obj(os.path.join(fixed, o))
        for c in defs_in[o]:
            ents = ob.ents('_ZTI' + c)
            shapes[(o, c)] = tuple((e.word if e.sym is None else 0, e.sym, e.addend) for e in ents)
            bases_of[(o, c)] = [e.sym[4:] for e in ents if e.sym and e.sym.startswith('_ZTI') and e.sym[4:] != c]
    memo = {}

    def view(o, c, depth=0):
        """The view of class c's typeinfo in object o: its words, and the views of its bases (o's own copy where o
        has one, else the kept one). Classes with one copy have no rival: their view is their name."""
        k = (o, c)
        if k in memo:
            return memo[k]
        if k not in shapes or depth > 40:
            r = ('=', c)
        else:
            memo[k] = ('...', c)   # guard
            r = (shapes[k], tuple(view(o if b in defs_in[o] else kept.get(b, o), b, depth + 1)
                                  for b in bases_of[k]))
        memo[k] = r
        return r

    undef, views, rows = collections.defaultdict(set), collections.defaultdict(set), []
    for cls, tdefs in sorted(ti.items()):
        if len(tdefs) < 2:
            continue
        k = kept[cls]
        want = view(k, cls)
        diff = sorted(o for o, _ in tdefs if o != k and view(o, cls) != want)
        for o in diff:
            undef[o].add(cls)
        first = kept_def(tdefs)[0]
        if diff:
            rows.append((cls, k, first, ' '.join(diff)))
    # private views of bases (t4), from the typeinfos each object keeps
    for o in objs:
        todo = [c for c in defs_in.get(o, ()) if kept.get(c) == o]
        seen = set(todo)
        while todo:
            c = todo.pop()
            for b in bases_of.get((o, c), ()):
                if b in seen or b not in defs_in[o] or kept.get(b) in (None, o):
                    continue
                seen.add(b)
                if view(o, b) != view(kept[b], b):
                    views[o].add(b)
                    undef[o].add(b)
                    todo.append(b)
        # a kept typeinfo that reaches a private view is the only copy left: another copy of the same view would
        # point at the global bases in another link order
        for c in [c for c in defs_in.get(o, ()) if kept.get(c) == o and views.get(o)]:
            reach, stack = set(), [c]
            while stack:
                x = stack.pop()
                for b in bases_of.get((o, x), ()):
                    if b in defs_in[o] and b not in reach:
                        reach.add(b)
                        stack.append(b)
            if reach & views[o]:
                for d, _ in ti[c]:
                    if d != o and c not in undef[d]:
                        undef[d].add(c)
                        cnt['copies of a class with private base views'] += 1
    cnt['private base views'] = sum(len(v) for v in views.values())
    cnt['objects with private base views'] = sum(1 for v in views.values() if v)
    for o, names in sorted(undef.items()):
        p = os.path.join(fixed, o)
        if views.get(o):
            # local symbols at the object's own copies, then the global definitions become references
            secs = {s.name: s for s in elfsyms.symbols(p)}
            args = ['--add-symbol=_ZTI%s.fnvnx_view=%s:0x%x,local,object' % (b, secs['_ZTI' + b].section,
                                                                            secs['_ZTI' + b].value)
                    for b in sorted(views[o])]
            r = subprocess.run([OBJCOPY, *args, p, p + '.v'], capture_output=True, text=True)
            if r.returncode:
                log('pair_typeinfo: objcopy failed for %s: %s' % (o, r.stderr[:300]))
                continue
            elfsyms.set_sizes(p + '.v', {'_ZTI%s.fnvnx_view' % b: secs['_ZTI' + b].size for b in views[o]})
            syms = elfsyms.symbols(p + '.v')
            index = {}
            for k_, s in enumerate(syms, 1):
                if s.name.startswith('_ZTI') and s.shndx and (s.bind != 'local' or s.name.endswith('.fnvnx_view')):
                    index[s.name] = (k_, s)
            ranges = [(s.shndx, s.value, s.value + s.size) for nm, (k_, s) in index.items()
                      if nm.endswith('.fnvnx_view') or (nm[4:] in defs_in[o] and kept.get(nm[4:]) == o)]
            for b in views[o]:   # a local view's range: the size of the global copy it stands at
                g = index.get('_ZTI' + b)
                if g:
                    ranges.append((g[1].shndx, g[1].value, g[1].value + g[1].size))
            mapping = {index['_ZTI' + b][0]: index['_ZTI%s.fnvnx_view' % b][0] for b in views[o]
                       if '_ZTI' + b in index and '_ZTI%s.fnvnx_view' % b in index}
            cnt['undefined'] += elfsyms.undefine_symbols(p + '.v', p + '.u', {'_ZTI' + c for c in names})
            cnt['base pointers retargeted'] += elfsyms.retarget_relocs(p + '.u', p + '.ti', ranges, mapping)
            os.remove(p + '.v'); os.remove(p + '.u')
        else:
            cnt['undefined'] += elfsyms.undefine_symbols(p, p + '.ti', {'_ZTI' + c for c in names})
        os.replace(p + '.ti', p)
    return rows, cnt


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
    if cmd == 'resolve' and '--msvc-code' in sys.argv:   # the same objects, for the register reads only
        k = sys.argv.index('--msvc-code')
        os.environ['FNVNX_MSVC_CODE'] = sys.argv[k + 1]
        del sys.argv[k:k + 2]
    if cmd == 'resolve':
        argv, opts = [], {}
        it_ = iter(sys.argv[2:])
        for x in it_:
            if x in ('--override', '--override-lib', '--extern-text', '--crt-symbols', '--imports', '--units-renames'):
                opts[x[2:].replace('-', '_')] = next(it_)
            else:
                argv.append(x)
        resolve(*argv[:3], override=opts.get('override'), extern_list=opts.get('extern_text'),
                crt_symbols=opts.get('crt_symbols'), imports=opts.get('imports'),
                units_renames=opts.get('units_renames'), override_lib=opts.get('override_lib'))
    elif cmd in ('probe', 'vtables'):
        jobs = int(sys.argv[4]) if len(sys.argv) > 4 else os.cpu_count()
        if cmd == 'probe':
            layout.run(sys.argv[2], sys.argv[3], jobs)
        if os.path.exists(os.path.join(sys.argv[2], 'vtable_requests.tsv')) and \
                (cmd == 'vtables' or not os.environ.get('FNVNX_NO_VTABLES')):
            vtables.run(sys.argv[2], sys.argv[3], jobs)
    elif cmd == 'bodies':
        bodies.main(sys.argv[2:])
    elif cmd == 'apply':
        apply(*sys.argv[2:4])
    elif cmd == 'check':
        check(*sys.argv[2:4])
    else:
        print(__doc__); sys.exit(2)


if __name__ == '__main__':
    main()
