#!/usr/bin/env python3
"""fnvlink.py - link the decomp's units by original address (ARCHITECTURE.md D4; docs/LINK.md).

    fnvlink.py resolve <units.json> <objects.tsv> <out dir> [--override <tsv>] [--override-lib <archive>]
                       [--extern-text <list>]
                       [--imports <tsv|none>]
                                                              # mapping, address map, ABI report, bridges
                                                              # (S4's provider tables: platform/ of this tree)
                       [--units-renames <tsv|none>]           # S1b's patches/units_renames.tsv (default: this
                                                              # tree's; patched units only)
                       [--msvc-objs <dir>]                    # vtable slots via the MSVC vftables (vslots.py)
                       [--msvc-code <dir>]                    # only the definitions' register reads (ecx-not-
                                                              # passed verdicts) from those objects
                       [--exe <FalloutNV.exe|none>]           # the player's exe (default FNVNX_EXE, PLAYER_EXE):
                                                              # image vftables paired by its RTTI class
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
import exevft   # noqa: E402  (S2c: the player's exe vftables)
import defshapes  # noqa: E402  (W22: the exe shape of the Havok sized-free ??_G)
import thisadj  # noqa: E402  (S2c-abi: MSVC entries of secondary-base overrides)
import vacall   # noqa: E402  (S2c-abi: call-site argument types of variadic references)
import xexit    # noqa: E402  (S2c-abi: native stand-ins of the exe's exit thunks)
import slotfill2  # noqa: E402  (S2c-vt2: slot fill rules)
import membervt  # noqa: E402  (S2-bodies class 4: member vtables)

DATA_START = 0x00fdf000          # first address of the image's .rdata/.data/.bss (J-0002)
DATA_END = 0x014089e4            # end of the image: .reloc 0x01340000 + 0xc89e4 (FalloutNV.exe 1.4.0.525)
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
XEXIT_SYM = '__fnvnx_xexit_%08x'   # native stand-in of an exe exit thunk a unit registers by address (xexit.py)
ANCHOR_SYM = '__fnvnx_anchor_%08x'     # a value anchor: absolute symbol whose value is the constant (W28)
ANCHOR_NAME = re.compile(r'(?:_value_anchor$|^g_target_[0-9a-f]{8}$)')
TEXTADDR_SYM = '__fnvnx_textaddr_%08x'   # a variable at a .text address nothing defines: absolute, the x86 value
XTRAP_SYM = '__fnvnx_xtrap_%08x'  # ctor/dtor defined nowhere, reached only by unwinding / emitted code -> trap
DYN_SYM = re.compile(r'^__fnvnx_(dyninit|dynexit)_([0-9a-f]{8})$')   # startup natives (docs/STARTUP.md)
# renamed in game objects so their static destructors share the game's onexit table (STARTUP.md, "Termination")
# __errno_location: GCC's errno in a game unit; the guest's libc has none, the CRT's _errno (0x00ec85e3,
# platform/crt/heap.c) is the same int *(void) (R2 trap loop, rank 1192)
GAME_RENAMES = {'__cxa_atexit': 'fnvnx_xc_cxa_atexit', '__errno_location': '_errno'}
ADDR_IN_NAME = re.compile(r'^(?:DAT|FUN|g|UNK|PTR)_([0-9a-f]{8})$')
# `str_<addr>`: the decomp's name for a C string of the image (`extern const char str_0109dca0[];`). Unlike the Ghidra
# names above it binds only where the exe holds a string at that address (exevft.string_at): units.json names the
# strings the pristine object emitted, and the two that only an inline function uses (MSVC inlined it away, so no
# symbol) stayed unresolved (top-down 10 / 11: a zero-filled data stub stood for the format string)
STR_IN_NAME = re.compile(r'^str_([0-9a-f]{8})$')
FUNC_SPANS = ([], [])   # sorted starts and their ends of units.json functions with a size (set by resolve)


def _table(name):
    """a decomp-derived table of fnv-nx-build (tools/tables.py: FNVNX_TABLES, sha256 against tools/tables.pin.json)"""
    import importlib.util
    s = importlib.util.spec_from_file_location('fnvnx_tables', os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tables.py'))
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m.path(name)


def inside_function(a):
    """True when a lies strictly inside a function units.json places (start < a < start + size): not a function
    start, so a FUN_<a> name cannot name one (R1: FnvString::FUN_0044aa30 / FUN_0044ab60 inside ResizeBuffer
    0x0044aa10)."""
    import bisect
    starts, ends = FUNC_SPANS
    j = bisect.bisect_right(starts, a) - 1
    return j >= 0 and starts[j] < a < ends[j]


def colliding_fun_defs(objsyms, native, itanium, shadowed=None):
    """Global free functions named FUN_<a> whose definition is not the native at a: the decomp keeps an unrelated
    helper under that tag (S5 s5-003 class 2: FUN_00e7acb0, bits per pixel, defined by a DXT block-size helper).
    A by-name reference would reach the helper, so every reference binds by address (__fnv_<a>), never by name:
    the defining objects' own definitions become references (apply's .undefine), the other objects' references
    are bound by address in classify. -> ({name: a}, {source: {name}}). Never a name that is the native of some
    address (its body is placed: a weak alias may name it), nor a decomp definition an override shadows (its name
    already follows the override)."""
    natnames = set()
    for nd in list(native.values()) + list((shadowed or {}).values()):
        natnames.add(nd['sym'].name)
        natnames.update(v.name for v in nd['variants'].values())
    addr_of, srcs = {}, collections.defaultdict(set)
    for src, syms in objsyms.items():
        for s in syms:
            if not s.shndx or s.bind not in ('global', 'weak') or s.type != 'func' or s.name in natnames:
                continue
            q = itanium(s.name).qual if s.name.startswith('_Z') else [s.name]
            m = ADDR_IN_NAME.match(q[-1]) if q and len(q) == 1 else None
            if not m or not m.group(0).startswith('FUN_'):
                continue
            a = int(m.group(1), 16)
            if a in native:
                addr_of[s.name] = a
                srcs[src].add(s.name)
    return addr_of, srcs
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
PTR_TARG = re.compile(r'\bfnv_[0-9a-f]{8}::[\w:]+\s*\*')


def ptr_targ_copy(isig, dem):
    """True for a member of a global class template whose only unit-local types are pointee types of pointer
    arguments (`BSSimpleList<fnv_009bc470::InventoryStackRecord*>::~BSSimpleList`): the member's code does not depend
    on the pointee, so the global instantiation's definition (`BSSimpleList<InventoryStackRecord*>`) is the same
    function. A copy held by value (`NiPointer<fnv_a::T>`) is not: its code reads the pointee."""
    return bool(isig.variant) and not isig.params and UNIT_NS_ANY.search(dem) is not None and \
        UNIT_NS_ANY.search(PTR_TARG.sub('', dem)) is None
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


def is_data_addr(a):
    return kind_of_addr(a) == 'data'


IMAGE_FIRST = 0x00401000   # the exe's first section (.text); nothing the decomp names lives below it


def in_image(a):
    """An address the exe maps (first section .. DATA_END + a page, kind_of_addr's range): a units.json value
    outside it (0x0, 0x14, 0x65 for three `g_target_*` anchors) is not where anything lives."""
    return IMAGE_FIRST <= a < DATA_END + 0x1000


def body_data_addr(body_bind, un):
    """S2d `exe-data` (bodies.py): the image address bodies/bind.tsv gives a placeholder variable, else None."""
    cls, target = body_bind.get(un, ('', ''))
    m = re.match(r'^__fnv_([0-9a-f]{8})$', target or '')
    return int(m.group(1), 16) if cls == 'body-exe-data' and m else None


ADDR_NAME = re.compile(r'^__fnv_[0-9a-f]{8}$')


def provider_or_addr(a, native, providers):
    """The symbol generated code (exit thunks, stubs) calls for exe address a: `__fnv_<a>` where the native build
    defines a, else the import thunk's binding, else `__fnv_<a>` (a visible trap) (R4 recon4-014)."""
    if a not in native and a in (providers or {}):
        return providers[a][0]
    return ADDR_SYM % a


def reset_synth(out):
    """Before the first resolve of a link run: drop the previous run's synthesized objects from what resolve loads -
    out/bodies/ (bodies.py's objects, synth.tsv and bind.tsv; nothing there is a cache) and vtables/synth.tsv (the
    probe cache, vtables/cache.json, stays). build/link/out persists between runs, so resolve 1 used to load them:
    names only those stale objects defined counted as native, so this run's bodies / vtables step skipped them and
    rewrote its lists without them, and resolve 2 left them undefined. Consecutive runs alternated (links 0540aff0
    and d1ed15e7: 497 constructors / destructors native -> unresolved, 85 zz_vt and 37 zz_bd objects). Returns what
    was removed."""
    gone = []
    b = os.path.join(out, 'bodies')
    if os.path.isdir(b):
        shutil.rmtree(b)
        gone.append('bodies/')
    v = os.path.join(out, 'vtables', 'synth.tsv')
    if os.path.exists(v):
        os.remove(v)
        gone.append('vtables/synth.tsv')
    return gone


def resolve(units_json, objects_tsv, out, override=None, extern_list=None, imports=None, override_lib=None,
            units_renames=None, exe=None):
    """imports: S4's imports.tsv (None: the table of the FNVNX_TABLES directory, tools/tables.py; the file of
    this tree, 'none': not used). exe: the player's FalloutNV.exe (None: FNVNX_EXE / PLAYER_EXE, 'none': not used):
    its RTTI pairs each image vftable with its own class's native vtable (exevft.py)."""
    import time
    _pt, _phases = [time.time()], []

    def _phase(name):   # seconds per step (T1: what the incremental link has left to do)
        now = time.time()
        _phases.append('%s %.1f s' % (name, now - _pt[0]))
        _pt[0] = now
    os.makedirs(os.path.join(out, 'cache'), exist_ok=True)
    os.makedirs(os.path.join(out, 'objcopy'), exist_ok=True)
    sigs.persist(os.path.join(out, 'cache', 'sigs.marshal'), prune=True)   # the last link's parses (T1)
    find_persist(os.path.join(out, 'cache', 'cdecls.marshal'))
    pre_stats = collections.Counter()
    tree = os.path.dirname(os.path.dirname(os.path.abspath(units_json)))   # <tree>/build/units.json
    renames = load_renames(UNITS_RENAMES if units_renames is None else units_renames, tree)
    units = load_units(units_json, renames, pre_stats)
    spans = sorted({(a, a + sz) for u in units.values() for a, sz in u.sizes.items() if sz})
    FUNC_SPANS[0][:] = [a for a, _ in spans]
    FUNC_SPANS[1][:] = [e for _, e in spans]
    for line in open(objects_tsv):
        f = line.rstrip('\n').split('\t')
        if len(f) >= 3 and f[2] == 'ok' and f[0] in units:
            units[f[0]].obj = f[1]
    have = [u for u in units.values() if u.obj]
    log('units: %d, with objects: %d' % (len(units), len(have)))
    # ODR stand-ins (odr.py): a plain global class several units define differently (SpawnedSub, SpawnedObject, ...)
    # gets per-unit names for its COMDAT groups before anything reads the objects; real exe RTTI classes are left
    if exe != 'none' and os.environ.get('FNVNX_ODR', '1') != '0':
        import odr
        _ep = exevft.exe_path(None if exe in (None, '') else exe)
        if _ep:
            _ev = exevft.load(_ep, os.path.join(out, 'cache', 'exe_vftables.json'))
            for k_, n_ in odr.rename(have, out, odr.exe_class_names(_ev), log).items():
                pre_stats['odr-' + k_] += n_

    _phase('load units+objects')
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

    _phase('msvc side')
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

    _phase('itanium side')
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

    # ---- W48 (R2, vtgate 4264bfd7): a `??_G` / `??_E` recipe name no object defines, whose unit defines the
    # function at that address under the decomp's own name (`FUN_00e22500_cls::FUN_00e22500(unsigned char)`,
    # `FUN_00431980(void *, unsigned)`): bound through the unit's `// @fnv 0x<a>` tag (tag_flags_defs)
    bound = {nd['sym'].name for nd in native.values()}
    for a, (u, n, pick) in sorted(tag_flags_defs(real, native, objsyms, msvc, itanium, tree, def_stats).items()):
        if pick.name in bound:
            def_stats['tag-flags-bound-elsewhere'] += 1; continue
        bound.add(pick.name)
        native[a] = dict(unit=u.source, sym=pick, variants={}, tier='tag-flags', msvc=n)
        def_stats['matched-tag-flags'] += 1

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
    ova = load_override_va(override)       # optional 5th column: va=none|escape of a variadic override
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
    # resolve's own --exe (the full build passes it without FNVNX_EXE: full1 0540aff0 had no MSVC entries at all, so
    # base-class views of 0x00d089f0 entered the primary entry with the subobject's this; EQ difffuzz-20)
    sec_entries = thisadj.from_env(None if exe in (None, '', 'none') else exe) if exe != 'none' else {}
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

    _phase('definitions/overrides')
    # ---- references
    layouts = {k: v for k, v in layout.load(os.path.join(out, 'layouts.tsv'), tree).items() if v is not None}
    lay_of = abi2.lookup(layouts)   # unit -> type -> Layout, with the unit's unique `ns::type` for a bare name
    probe_req = set()
    vacall_req = set()   # (unit, undefined symbol) of variadic references whose call sites decide (vacall.py)
    refs_out = open(os.path.join(out, 'refs.tsv'), 'w', encoding='utf-8', errors='surrogateescape')
    refs_out.write('#object\tundefined\tclass\ttier\taddress\ttarget\tnote\n')
    stats = collections.Counter(pre_stats)
    stats['libstdcxx_identifiers'] = len(std_identifiers(os.path.join(out, 'cache')))   # decomp_std's view
    stats['libstdcxx_symbols'] = len(std_exports(os.path.join(out, 'cache')))
    if not stats['libstdcxx_identifiers'] and not stats['libstdcxx_symbols']:
        # neither view of libstdc++ is known (no $CXX / nm here): every std:: name would be left to the runtime,
        # the decomp's own std classes included (std::StandardLibrarySingletonObject, S5 round 6): say so loudly
        stats['libstdcxx_unknown'] = 1
        print('fnvlink: WARNING: libstdc++ identifiers and symbols unknown ($CXX=%s); decomp std:: names stay '
              'runtime traps' % os.environ.get('CXX', 'aarch64-linux-gnu-g++'), file=sys.stderr)
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
    exe_path_ = exevft.exe_path(None if exe in (None, '') else exe) if exe != 'none' else None
    exe_slots = exevft.slot_index(exevft.load(exe_path_, os.path.join(out, 'cache', 'exe_vftables.json'))) \
        if exe_path_ else {}
    unbound = collections.defaultdict(set)   # source -> names left undefined (extern-text, import without a symbol)
    hints = collections.defaultdict(dict)    # source -> {vtable slot name: address} another unit's reference gave it
    chints = collections.defaultdict(dict)   # W20 (a): source -> {slot name: address} the unit's recipe gave, and its code calls
    used_addr = set()
    anchor_vals = set()   # W28: units.json values of the value anchors (absolute symbols in aliases.ld)
    data_defs, tls_defs, tls_addrs = [], [], set()
    text_addrs = set()   # .text addresses of variables nothing defines (`text-addr`)
    label_cache = {}
    xexit_left = set()   # callback labels whose exe thunk has no native stand-in (they trap)
    exports = load_exports(EXPORTS if imports is None else (os.path.join(os.path.dirname(imports), 'exports.cpp')
                                                             if imports != 'none' else None))
    providers = {}   # {address: (native symbol, text)}: the import thunks below (an import fact), no name-based table
    import_syms = load_imports(_table('imports.tsv') if imports is None else (None if imports == 'none' else imports), exports)
    # import thunks (`jmp [IAT]`, 6 bytes) the decomp spells as functions calling the import: compiled, they lose or
    # retype the caller's arguments (S6c); their address binds to the import itself, like a provider
    iat = load_iat(_table('imports.tsv') if imports is None else (None if imports == 'none' else imports))
    thunks = find_import_thunks(native, units, iat, import_syms, gdef, gref, anydef, msvc, itanium)
    thunk_undef = collections.defaultdict(dict)   # source -> {thunk definition: import symbol}: the object's own
    for a, (sym, text) in thunks.items():         # calls follow (apply undefines, then renames them)
        providers[a] = (sym, text)
        name = native[a]['sym'].name
        for src in set(strong.get(name, [])) | {native[a]['unit']}:
            thunk_undef[src][name] = sym
        del native[a]
    stats['import_thunks'] = len(thunks)
    # S5 s5-003 class 2: a global FUN_<a> defined by an unrelated body binds by address everywhere, the defining
    # objects' own references too (undefined there, then renamed, like an import thunk's)
    colliding, colliding_srcs = colliding_fun_defs(objsyms, native, itanium, shadowed)
    for src, names_ in colliding_srcs.items():
        for name in names_:
            thunk_undef[src].setdefault(name, ADDR_SYM % colliding[name])
    stats['colliding_fun_names'] = len(colliding)
    stats['colliding_fun_defs'] = sum(len(v) for v in colliding_srcs.values())
    # R6 class 1: the MSVC adjustor thunks units.json places (`?f@C@@W<n>AE..`), which GCC never emits: a stub at
    # the address does what the exe's `sub ecx, n; jmp f` does (this -= n, then f's native entry)
    exe_p_ = exevft.exe_path(None if exe in (None, '') else exe) if exe != 'none' else None
    # W22: the Havok sized-free ??_G (defshapes `sdd-sized`) get a native stand-in when the link defines the free
    # function (the hkostream recon); the adjustor thunks into them then bind below
    hk_rows = hk_sdd_rows(def_at, native, exe_p_, anydef, providers, stats)
    HK_SDD.clear()
    HK_SDD.update(hk_rows)
    adj_rows = adjustor_thunks(def_at, native, exe_p_, providers)
    # An object that already defines `__fnv_<a>` itself (a recon's GCC-only spelling of the thunk, REC1) keeps it:
    # a stub too was a multiple definition and failed the guest link (link 4a15e91c967a, 11 names; R2).
    own_addr_defs = {a_ for a_ in list(adj_rows) if ADDR_SYM % a_ in anydef}
    for a_ in own_addr_defs:
        del adj_rows[a_]
    stats['adjustor_thunk_unit_defined'] = len(own_addr_defs)
    for a_, (n_, t_) in sorted(adj_rows.items()):
        bridges.setdefault(ADDR_SYM % a_, emit_adj_stub(ADDR_SYM % a_, adj_target(native, t_), n_))
    stats['adjustor_thunk_stubs'] = len(adj_rows)
    # R6 class 4: a non-virtual class's ??_G (scalar deleting destructor) units.json places: GCC emits no D0 for a
    # non-virtual destructor, so callers bound there trapped. A stub does what MSVC's ??_G does: D1 (the unit's own,
    # else the address its ??1 is bound to), then the global operator delete when flags bit 0 is set; returns this
    gdel_rows = nonvirtual_deldtors(def_at, native, units, objsyms, msvc, itanium, newdel_votes, providers)
    for a_ in [a_ for a_ in gdel_rows if ADDR_SYM % a_ in anydef]:   # unit-defined, as for adjustor thunks
        del gdel_rows[a_]
    for a_, (d1_, del_) in sorted(gdel_rows.items()):
        bridges.setdefault(ADDR_SYM % a_, emit_sdd_stub(ADDR_SYM % a_, d1_, del_))
    stats['nonvirtual_deldtor_stubs'] = len(gdel_rows)
    for a_, d_ in sorted(hk_rows.items()):
        bridges.setdefault(ADDR_SYM % a_, emit_hk_sdd_stub(ADDR_SYM % a_, d_, HK_FREE))
    stats['hk_sdd_stubs'] = len(hk_rows)
    with open(os.path.join(out, 'colliding.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#name\taddress\tnative\tdefining objects (definitions turned into references to __fnv_<address>)\n')
        for name, a in sorted(colliding.items()):
            f.write('%s\t%08x\t%s\t%s\n' % (name, a, native[a]['sym'].name,
                                            ' '.join(sorted(s for s, n_ in colliding_srcs.items() if name in n_))))
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

    deldtor_cache = {}

    def unit_gdel_addrs(u_, isig_):
        """Addresses of the `??_G` the unit names for the class of the D1/D2 isig_."""
        if u_.source not in deldtor_cache:
            m_ = {}
            for n_, a_ in list(u_.refs.items()) + [(n2, a2) for a2, n2 in u_.defs]:
                if n_.startswith('??_G'):
                    sg_ = msvc(n_)
                    if sg_.kind == 'func' and sg_.qual and len(sg_.qual) >= 2:
                        m_.setdefault('::'.join(sigs.canon_qual(sg_.qual[:-1])), set()).add(a_)
            deldtor_cache[u_.source] = m_
        cq = '::'.join(sigs.canon_qual(isig_.qual[:-1])) if isig_.qual and len(isig_.qual) >= 2 else None
        return deldtor_cache[u_.source].get(cq) or set()

    def unit_deldtor(u_, isig_):
        """(address, target) for an unbound D1/D2 of class C whose unit names C's scalar deleting destructor
        (`??_GC@@...`): the native D1 at that address (a G row), or the decomp's (this, flags) function through
        the flag stub with flags 0; else None."""
        gs = unit_gdel_addrs(u_, isig_)
        if len(gs) != 1:
            return None
        g_ = next(iter(gs))
        nd_ = native.get(g_)
        if nd_ is None:
            return None
        for var_ in ('D1', 'D2'):
            if nd_['variants'].get(var_) is not None and sigs.itanium_variant(nd_['sym'].name) == 'D0':
                return g_, (ADDR_SYM % g_) + ('' if nd_['variants'][var_] is nd_['sym'] else '_' + var_)
        if sigs.itanium_variant(nd_['sym'].name) is None and (
                nd_.get('tier') == 'tag-flags' or flags_param(msvc(nd_['msvc']) if nd_['msvc'] else None)):
            name_ = FLAG_SYM % (g_, 0)
            bridges.setdefault(name_, emit_flag_stub(name_, ADDR_SYM % g_, 0))
            return g_, name_
        return None

    gdel_exe = []

    def exe_deldtor_body(u_, isig_):
        """(address, symbol) of the native destroy body the exe's `??_G` of the unbound D1/D2's class calls: the
        unit names the class's ??_G at an address g that has no native definition (the decomp has no body there), and
        exevft.deleting_dtor_callee(g) is X, an address with a native plain destructor body (not itself a deleting
        destructor). None otherwise (no exe, no ??_G, several ??_G addresses, another shape)."""
        if not gdel_exe:
            gdel_exe.append(thisadj.exe_path(None if exe in (None, '', 'none') else exe) if exe != 'none' else None)
        if not gdel_exe[0]:
            return None
        gs = unit_gdel_addrs(u_, isig_)
        if len(gs) != 1:
            return None
        g_ = next(iter(gs))
        if g_ in native:
            return None
        x_ = exevft.deleting_dtor_callee(gdel_exe[0], g_)
        nd_ = native.get(x_) if x_ is not None else None
        if nd_ is None or deleting_native(nd_, msvc) or sigs.itanium_variant(nd_['sym'].name) not in (None, 'D1', 'D2'):
            return None
        return x_, ADDR_SYM % x_

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
    _phase('references (1)')
    # S2b-vslots: slots of GCC vtables bound through the MSVC 2008 vftables (vslots.py; --msvc-objs / FNVNX_MSVC_OBJS)
    ecx_use = msvc_code_use(units_json, ms, msvc, renames)
    for u in have:
        idx = KeyIndex()
        uident = collections.defaultdict(set)   # ident over this unit's own symbols (classify_ref)
        for n, a in u.refs.items():
            m_ = msvc(n)
            idx.add(m_, a)
            if m_.kind in ('data', 'cname') and m_.qual and m_.special is None and DATA_START <= a < DATA_END:
                uident[m_.qual[-1]].add(a)
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
                cls, tier, addr, target, note = classify_ref(un, isig, idx, gdef, gref, anydef, msvc, ident, uident)
                if cls == 'native' and un in colliding:   # S5 class 2: never the unrelated body by name
                    cls, tier, addr = 'addr', 'name-addr/colliding', colliding[un]
                    stats['colliding_refs_by_address'] += 1
                if tier in ('gref/ident', 'unit/ident'):
                    stats['ident_' + cls] += 1
                if tier == 'unit/ident':
                    stats['ident_unit'] += 1
                if cls == 'unresolved' and isig.qual and it[un] and (UNIT_NS.match(isig.qual[0]) or
                                                                     ptr_targ_copy(isig, it[un])):
                    # a unit-local namespaced copy of a class (`fnv_00650ea0::NiFixedString`, S13 J-0114): bound like
                    # the global class - by its names in units.json without the namespace, else to the native
                    # definition of the global name
                    st_ = UNIT_NS_ANY.sub('', it[un])
                    ts_ = sigs.parse_itanium(un, st_)
                    r_ = classify_ref('', ts_, idx, gdef, gref, set(), msvc, ident, uident) if ts_.qual else None
                    if r_ and r_[0] not in ('unresolved', 'runtime'):
                        cls, tier, addr, target, note = r_[0], 'ns/' + (r_[1] or ''), r_[2], r_[3], r_[4]
                        stats['unit_ns_bound'] += 1
                    else:
                        if native_text is None:
                            native_text = {}
                            native_variant = {}
                            for n_ in sorted(anydef):
                                if it[n_]:
                                    native_text.setdefault(it[n_], n_)
                                    v_ = itanium(n_).variant
                                    if v_:
                                        native_variant.setdefault((it[n_], v_), n_)
                        g_ = native_text.get(st_)
                        if g_ and isig.variant and not UNIT_NS.match(isig.qual[0]):
                            g_ = native_variant.get((st_, isig.variant))   # a constructor / destructor of that variant
                        if g_ in colliding:   # S5 class 2: the unrelated body's name is undefined after apply
                            cls, tier, addr = 'addr', 'ns/colliding', colliding[g_]
                            stats['colliding_refs_by_address'] += 1
                        elif g_:
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
            if cls == 'unresolved' and isig.kind == 'data' and isig.qual and exe_path_ and \
                    STR_IN_NAME.match(isig.qual[-1]) and \
                    exevft.string_at(exe_path_, int(isig.qual[-1][4:], 16)) is not None:
                # S2d (top-down 10 / 11): a string name whose address holds a C string in the exe
                cls, tier, addr, target = 'addr', 'name-addr/str', int(isig.qual[-1][4:], 16), None
                stats['str_name_addr_refs'] += 1
            if cls == 'unresolved' and body_data_addr(body_bind, un) is not None:
                # S2d exe-data: a placeholder variable at the image address the exe's code uses for it; bound like
                # any address (data, an IAT slot GCC calls, TLS)
                cls, tier, addr, target = 'addr', 'body/exe-data', body_data_addr(body_bind, un), None
            if addr is not None and cls == 'addr' and not in_image(addr):
                # S2d (R6 recon6-007): a units.json "address" outside the image (`?g_target_00d53c90@@3HA` at
                # 0x00000065: a value the recipe recorded, not where the variable lives) is no evidence: the name
                # stays unresolved, a visible trap, never `__fnv_00000065`
                if isig.kind in ('data', 'cname') and isig.qual and ANCHOR_NAME.search(isig.qual[-1]):
                    # W28 (top-down 26 / 90): a value anchor. The decomp spells a constant leaf (`mov eax, 4; ret`)
                    # as `(int)&g_..._value_anchor + 4` so VC9 keeps the 6-byte shape; the units.json "address" IS the
                    # constant (exe 0x00e9ecc0: b8 04.. -> anchor 0; 0x00d53c90: b8 65.. -> g_target at 0x65). An
                    # absolute symbol of that value (aliases.ld) makes the native body return the exe's constant.
                    cls, tier, target = 'data', 'anchor/' + (tier or ''), ANCHOR_SYM % addr
                    note = 'absolute %#x (value anchor)' % addr
                    anchor_vals.add(addr)
                    stats['value_anchor_refs'] += 1
                else:
                    cls, tier, note = 'unresolved', 'not-in-image/' + (tier or ''), '%08x is outside the image' % addr
                addr = None
                stats['refs_not_in_image'] += 1
            if cls == 'import' and addr is not None and addr not in import_syms and addr not in iat and \
                    exe != 'none':
                # an `__imp_?...` name the recipe placed on a slot of an image vftable (BoneRecordArray::Free at
                # 0x010c4cec, slot 2 of 0x010c4ce4): the reference is a call through that slot, so it binds to the
                # function the exe's word holds (R2 recon2-006), by the same rules as a vslot
                cls, tier, addr, target, note = vftable_import(
                    u, un, isig, addr, native, providers, bridges, used_addr, stats, msvc, exe_slots, exe_path_,
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
                    if note and not isig.variant and (nd['msvc'] or '').startswith('??0') and cthis_ok(
                            msvc(nd['msvc'])) and msvc(note).kind == 'func' and abi.ret_class(msvc(note)) == 'P':
                        # S5 (s5-002 item 4): the reference uses an MSVC constructor's result (eax = this:
                        # `value = p->FUN_00aa2200()`), the Itanium C1 returns void: a stub returns this
                        target = CTHIS_SYM % addr
                        bridges.setdefault(target, emit_cthis_stub(target, ADDR_SYM % addr))
                        stats['ctor_returns_this_refs'] += 1
                    stats['code_same_name' if natname == un else 'code_other_name'] += 1
                    # ABI: reference declaration vs definition
                    refdecl = note  # msvc name of the matched reference declaration
                    if nd.get('msvc_entry') and refdecl and not isig.variant:
                        # MSVC enters it with this at a secondary base (thisadj.py). A reference through another class
                        # (a decomp shell declared at that subobject: 0x008d4870 calls 0x008805f0 with this at +0xa4)
                        # passes the subobject, as the x86 caller did: it binds to the thunk (V2 T1)
                        rq_, dq_ = msvc(refdecl), msvc(nd['msvc'] or '')
                        if rq_.kind == 'func' and dq_.kind == 'func' and rq_.member and \
                                rq_.qual[:-1] != dq_.qual[:-1]:
                            target = MSVC_ENTRY_SYM % addr
                            stats['msvc_entry_refs'] += 1
                    if nd.get('msvc_entry') and target == ADDR_SYM % addr and \
                            slotfill2.other_class_entry(isig, itanium(nd['sym'].name)):   # S2c-vt2
                        target = MSVC_ENTRY_SYM % addr
                        stats['msvc_entry_refs_itanium'] += 1
                    if isig.variant == 'D0' and target == ADDR_SYM % addr and \
                            sigs.itanium_variant(nd['sym'].name) is None and not deleting_native(nd, msvc):
                        # GCC's deleting destructor D0 bound to a plain (destroy-only) body: the decomp's `~C` at
                        # an address its unit binds `??1C` to (S5 round 5: _ZTV17SettingPointerMap's D0 ran
                        # FUN_00404da0 and freed nothing). D0 = that body, then the global operator delete
                        dl_ = global_delete(newdel_votes, native, providers)
                        if dl_:
                            target = D0DEL_SYM % addr
                            bridges.setdefault(target, emit_d0_stub(target, ADDR_SYM % addr, dl_))
                            stats['d0_on_plain_dtor'] += 1
                    defdecl = nd['msvc']
                    cast = None
                    if refdecl and isig.qual and msvc(refdecl).kind == 'func' and not msvc(refdecl).member:
                        cast = casts_of(u).get(isig.qual[-1])
                    if not refdecl and (tier or '').startswith('name-addr') and not isig.variant and \
                            nd['sym'].name.startswith('_Z') and un.startswith('_Z'):
                        # S2 s2d-010 (2a): bound by the address in a Ghidra-style name, so no MSVC declaration of the
                        # reference was found and nothing compared the pair. Both sides from their Itanium names
                        # (name_addr_sigs: the same convention rules for both), then the ordinary verdict: a bridge
                        # where the shapes differ and abi can map them, else flagged code-manual
                        rs_, ds_ = name_addr_sigs(isig, itanium(nd['sym'].name))
                        dm_ = msvc(defdecl) if defdecl else None
                        if rs_ is not None and dm_ is not None and dm_.kind == 'func' and dm_.raw_params is not None:
                            # the definition's own MSVC declaration carries its real convention (a __fastcall with an
                            # unused edx: 0x00b5ade0); only the reference side is guessed
                            ds_ = dm_
                        if rs_ is not None:
                            v_ = abi.compare(rs_, ds_)
                            # the conventions are guessed (Itanium has none): equal native argument shapes are the
                            # same call whatever x86 says (B::f(int) vs f(B *, int)); a bridge is taken only when abi
                            # derives one, any other difference is flagged for a decision, never guessed into moves
                            if [c for _, c, _ in abi.a64_locations(rs_)] == [c for _, c, _ in abi.a64_locations(ds_)]:
                                v_ = abi.Verdict()
                            stats['name_addr_abi_' + v_.kind] += 1
                            if v_.kind != 'same':
                                abi_rows.append((u.source, un, '%08x' % addr, '(itanium) ' + un,
                                                 '(itanium) ' + nd['sym'].name, v_.kind, ','.join(v_.classes),
                                                 '; '.join('%s<-%s' % (d_, s_) for d_, s_, _, _ in v_.moves),
                                                 '; '.join(v_.notes), it[un] or '', it[nd['sym'].name] or ''))
                                abi_classes[(v_.kind, ','.join(v_.classes) or '-')] += 1
                            if v_.kind == 'bridge':
                                bn_ = abi.bridge_name(addr, un)
                                bridges.setdefault(bn_, (target, v_, it[un] or un, it[nd['sym'].name] or nd['sym'].name))
                                target, cls = bn_, 'code-bridge'
                            elif v_.kind in ('manual', 'x86-inconsistent'):
                                cls = 'code-manual'
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
                            va_r = None   # what a variadic definition's body reads with va_arg (vacall VADEF, V2)
                            if nd['unit'] is None and addr in ova:
                                va_r = ova[addr]
                                vacall_req.add((u.host or u.source, un))   # format literals of its callers
                            elif nd['unit'] is not None and any(p_.strip() == '...' for p_ in ds.raw_params or ()):
                                vacall_req.add((nd['unit'], nd['sym'].name))
                                vacall_req.add((u.host or u.source, un))   # its format literals, in the same probe
                                defs_ = vacall.load_defs(out, nd['unit'])
                                va_r = defs_.get(nd['sym'].name) if defs_ else None
                            v = abi.compare(rs, ds, va_reads=va_r)
                            va_key = None
                            if va_r == ('escape',) and v.kind == 'manual' and 'varargs' in v.classes:
                                # the definition hands its va_list to vsnprintf: the call sites' format literals
                                # decide what it reads (vacall.decide_format)
                                fhost = u.host or u.source
                                vacall_req.add((fhost, un))
                                fm_ = vacall.load_fmts(out, fhost)
                                v3_, suf_, st_ = vacall.decide_format(rs, ds, fm_.get(un) if fm_ else None)
                                stats[st_] += 1
                                if v3_ is not None:
                                    v = v3_
                                    if suf_:
                                        va_key = refdecl + suf_
                            if vacall.needs(v):
                                # the definition reads the reference's anonymous arguments: the call sites' own
                                # argument types decide (vacall.py, V1 VA-U1..U4)
                                vhost = u.host or u.source
                                vacall_req.add((vhost, un))
                                calls_ = vacall.load(out, vhost)
                                v2_, vs_, seq_, st_ = vacall.decide(rs, ds, calls_.get(un) if calls_ else None, va_r)
                                stats[st_] += 1
                                if v2_ is not None:
                                    v, rs = v2_, vs_
                                    va_key = '%s|va %s' % (refdecl, ','.join(seq_))
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
                                # (and untagged named parameter types: typedefs, nested enums - abi2.probe_name)
                                for sg, unit in ((rs, u.host or u.source), (ds, nd['unit'])):
                                    for c in [c for _, c in abi.params_of(sg)] + [abi.ret_class(sg)]:
                                        pn_ = abi2.probe_name(c)
                                        if pn_ and '{anon}' not in c:
                                            probe_req.add((unit, pn_))
                                why2 = []
                                p2 = None if nd['unit'] is None else abi2.compare(
                                    rs, ds, lay_of(u.host or u.source), lay_of(nd['unit']), ecx_unused=ecx_dead,
                                    why=why2)
                                if nd['unit'] is None:
                                    why2.append('definition outside the units (no layouts)')
                                if p2 is None and v.kind == 'manual':
                                    v.notes = v.notes + ['abi2: ' + w for w in why2]
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
                                if va_key:   # one bridge per call-site signature (vacall)
                                    bkey = va_key
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
                                        cls = 'code-manual' if not abi_manual_trap(v) else 'abi-manual'
                                        target = None if cls == 'abi-manual' else target
                                        if cls == 'abi-manual':
                                            stats['abi_manual_unbound'] += 1
                                            stats['abi_manual_why:bridge frame over 4 KiB'] += 1
                                    else:
                                        bridges.setdefault(bn, code)
                                        target = bn
                                        cls = 'code-bridge'
                                elif ekind == 'bridge':
                                    bn = abi.bridge_name(addr, bkey)
                                    bridges.setdefault(bn, (target, v, ms[refdecl] or refdecl, ms[defdecl] or defdecl))
                                    target = bn
                                    cls = 'code-bridge'
                                elif v.kind == 'manual' and abi_manual_trap(v) and \
                                        ('%08x' % addr, un) in abi_layout_equal():
                                    # proved layout-equal (tools/link/abi_layout_equal.tsv): the direct binding stays
                                    stats['abi_layout_equal'] += 1
                                elif v.kind == 'manual' and abi_manual_trap(v):
                                    # D24: nothing binds across an aggregate / result-pointer difference without a
                                    # bridge; the reference stays a trap stub (cause abi-manual in the D23 list)
                                    cls, target = 'abi-manual', None
                                    stats['abi_manual_unbound'] += 1
                                    stats['abi_manual_why:' + abi_manual_why(why2)] += 1
                                elif v.kind == 'manual':
                                    cls = 'code-manual'
                            elif v.notes:
                                pass
                elif ak == 'text' and addr in providers:   # CRT/library code S4 implements, import thunks
                    cls = 'provider'; target = providers[addr][0]
                elif ak == 'text' and addr in adj_rows and not thisadj.thunk_layout_ok(
                        adj_rows[addr][0], thisadj.slot_otts(u.obj, un)):
                    # W15: a vtable slot whose native layout puts the subobject elsewhere than the thunk's x86
                    # immediate (the decomp's small shell classes): the stub would hand the target a `this` that is
                    # off by the difference. Stays a visible trap; x86-convention callers keep the stub
                    cls = 'code-adjthunk-layout'
                    target = TRAP_SYM % int(hashlib.sha1(un.encode('utf-8', 'surrogateescape')).hexdigest()[:8], 16)
                    if target not in traps:
                        traps[target] = (un, vtables.slot_of(u.obj, un))
                    stats['adjustor_thunk_layout_trap'] += 1
                elif ak == 'text' and addr in adj_rows:   # an MSVC adjustor thunk: its stub (adjustor_thunks)
                    cls = 'code-adjthunk'; target = ADDR_SYM % addr
                    stats['adjustor_thunk_refs'] += 1
                elif ak == 'text' and addr in gdel_rows:   # a non-virtual ??_G: its stub (nonvirtual_deldtors)
                    cls = 'code-sddstub'; target = ADDR_SYM % addr
                    stats['nonvirtual_deldtor_refs'] += 1
                elif ak == 'text' and addr in hk_rows:   # a Havok sized-free ??_G: its stub (hk_sdd_rows)
                    cls = 'code-sddstub'; target = ADDR_SYM % addr
                    stats['hk_sdd_refs'] += 1
                elif ak == 'text' and addr in def_at:
                    du = def_at[addr][0]
                    cls = 'code-pending' if not du.obj else 'code-defmissing'
                    target = ADDR_SYM % addr
                    used_addr.add(addr)
                elif ak == 'text' and note and re.search(r'@@3D', note) and msvc(note).kind == 'data' and \
                        un not in called_syms(u) and label_is_argument(
                            tree, u.host or u.source, msvc(note).qual[-1] if msvc(note).qual else un, label_cache):
                    # a char label whose address the unit passes to a call: a callback (atexit with the exe's exit
                    # thunk, V2 X2). A thunk of a known shape becomes a native stand-in (xexit.py); else the trap
                    exe_ = thisadj.exe_path(None if exe in (None, '', 'none') else exe) if exe != 'none' else None
                    sh_ = xexit.decode(exe_, addr) if exe_ else None
                    if sh_ is not None and (sh_[0] != 'thiscall' or sh_[2] in native):
                        cls, target = 'text-callback', XEXIT_SYM % addr
                        if target not in bridges:
                            bridges[target] = xexit.emit(target, sh_, lambda a_: provider_or_addr(a_, native, providers))
                            used_addr.update([sh_[1], sh_[2]] if sh_[0] == 'thiscall' else [sh_[1]])
                        stats['xexit_' + sh_[0]] += 1
                    else:
                        cls = 'extern-text'; target = None
                        extern_text[addr].add(un)
                        xexit_left.add(addr)
                elif ak == 'text' and note and re.search(r'@@3[QD]', note) and msvc(note).kind == 'data' and \
                        un not in called_syms(u) and not (re.search(r'@@3D', note) and label_is_argument(
                            tree, u.host or u.source, msvc(note).qual[-1] if msvc(note).qual else un, label_cache)):
                    # a variable the decomp places at a .text address nothing defines (the generated initializers'
                    # `extern const char g_011c5a4c_arg1[]` at 0x00a00000: an immediate the generator took for an
                    # address): the code uses its address, which on x86 is that number - an absolute symbol there
                    # gives the native code the same value (S2c-abi, S13b native-stub-data). Only arrays (`@@3Q`)
                    # and char labels (`@@3D`, `&FUN_00a00000`), whose address is all the code uses; an `int` at
                    # 0x00000065 stays extern-text. A char label whose address the unit passes to a call may be a
                    # callback (0x00c1d8b0: `FUN_00ec658f(&UNK_00fdd3b0)`, _atexit with the static's x86 exit thunk,
                    # V2 X2): it stays extern-text (a trap), never the raw x86 address
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
            elif cls == 'import' and addr in iat and iat[addr][1] != un and IDENT.match(iat[addr][1]):
                # no export-table symbol for the slot (an api a platform module defines under its C name, e.g.
                # d3dx9_38's D3DXCreateCubeTextureFromFileInMemory), but the unit spells it otherwise (declared
                # without extern "C": _Z37D3DXCreate...): the reference goes to the api's C name, as a C-named
                # reference to the same slot links by itself
                target = iat[addr][1]
                stats['imports_bound_by_api_name'] += 1
            if cls == 'unresolved' and ADDR_NAME.match(un) and int(un[6:], 16) in providers and \
                    int(un[6:], 16) not in native:
                # a reference spelled `__fnv_<a>` (recon / decomp text naming the address) where S4 provides a: the
                # provider, as for every other reference to a (R4 recon4-014)
                addr = int(un[6:], 16)
                cls, target = 'provider', providers[addr][0]
                stats['addr_name_provider'] += 1
            if cls == 'unresolved' and un in vslots:
                cls = 'vslot-unresolved'
            elif cls == 'unresolved' and isig.variant:
                cls = 'unresolved-ctor-dtor'
                exegd = exe_deldtor_body(u, isig) if isig.variant in ('D1', 'D2') else None
                alias = dtor_alias(isig) if not exegd and isig.variant in ('D1', 'D2') else None
                gd = unit_deldtor(u, isig) if not exegd and not alias and isig.variant in ('D1', 'D2') else None
                if exegd:
                    # W26: the unit names C's ??_G at an address no native body defines, and the exe's ??_G there is
                    # `call X; test flags, 1; delete` with X a native plain destructor body: D1 = X. Identical-code
                    # folding gives every class sharing the ??_G the same X, which is C's complete destructor.
                    cls, addr, target = 'dtor-by-exe-deldtor', exegd[0], exegd[1]
                    stats['dtor_by_exe_deldtor'] += 1
                elif alias:
                    cls, target = 'ctor-dtor-alias', alias
                elif gd:
                    # S1-refs (S1-refs-004): MSVC folds `~C` into its scalar deleting destructor; the unit binds ??_GC,
                    # GCC's `delete p` calls D1 + operator delete: D1(this) = ??_G(this, 0), destroy without freeing
                    cls, addr, target = 'dtor-by-deldtor', gd[0], gd[1]
                    stats['dtor_by_deldtor'] += 1
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
            if cls == 'code' and addr is not None and un in vslots and (tier or '').startswith('unit/') \
                    and un in called_syms(u):
                # W20 (a): the unit's own recipe binds the name and its code calls it directly (a qualified call of a
                # base's virtual, `Base::f()`): the address is right for that call, so the symbol keeps it. The
                # vtable slot that names the symbol is the exe vftable's business: exe_slot_fill gives that slot the
                # exe's function (a per-slot override) while the symbol stays where it is.
                chints[u.source][un] = addr
            elif cls == 'code' and addr is not None and un in vslots and (tier or '').startswith(
                    ('gref/', 'gdef/', 'unit/')) and un not in called_syms(u):
                # S2d-vslot: only a vtable of this object names the symbol, and the address is another unit's idea of
                # the name (units.json lists the call there, perhaps of a different class's function the decomp
                # called by this name: TESForm::IsQuestItem is 0x0046f070 in hudmainmenu.cpp, the exe's TESForm slot 37
                # holds 0x00401190). The exe vftable decides in exe_slot_fill; the name stays where it has none.
                hints[u.source][un] = addr
            if target and target != un:
                renames[un] = target
            if cls in ('extern-text', 'import') and not target:
                unbound[u.source].add(un)
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

    _phase('references')
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

    _phase('objcopy args')
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
                            canon_msvc, os.path.join(out, 'cache', 'hvs.marshal'))
    hand_vptr_confirm(hand, exevft.exe_path(None if exe in (None, '') else exe) if exe != 'none' else None, vstats)
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
    _phase('vftables')
    # ---- S2c: the player's exe (RTTI): every image vftable with its class and offset (exevft.py)
    exe_path = exevft.exe_path(None if exe in (None, '') else exe) if exe != 'none' else None
    exev = exevft.load(exe_path, os.path.join(out, 'cache', 'exe_vftables.json')) if exe_path else {}
    exe_cls = exe_vftable_classes(exev, ms, canon_msvc) if exe_path else {}
    have_cls = {canon_it(n) for n in rtti.where if n.startswith('_ZTV')}
    exe_req = exe_requests(exe_cls, have_cls, units, msvc, canon_msvc, compiled_) if exe_cls else {}
    write_vtable_requests(out, real, objsyms, vnames, it, itanium, hand_req, exe_req)
    # hand-written vptr stores of image vftable addresses (`obj.vtbl = &DAT_010176f8;`): the address stands for the
    # native vtable of the class constructed there - the one of the same name when some unit names that vftable,
    # else the class whose member function stores it, else the class constructed or destroyed at the storing
    # function's address; its data symbol is bound to that address point (D7, point 2)
    by_cls = collections.defaultdict(list)
    for n in rtti.where:
        if n.startswith('_ZTV'):
            by_cls[canon_it(n)].append(n)
    rebind, hand_rows = {}, []
    vmo = {}   # D26 (c): image vftable -> its minus-one native copy (minus_one_copies)
    exev_all = {}   # exe vftables, RTTI and plain (stays empty without the exe: no copies, no V rows)
    # a stand-in class name several units declare on their own (`struct StandIn`, `struct S`) whose hand stores at one
    # offset write different image vftables: the name says nothing about which native vtable that is (the one
    # _ZTV7StandIn is some other unit's class); such a store keeps its image vftable (D26 (c) copy, transform(exe))
    # unless the exe's own RTTI class has that name (FNVNX_HANDAMB=0: the name decides as before)
    amb_hand = hand_ambiguous_names(hand, exev)
    # the same rule for the class constructed at the storing address (R4 15:33: SpawnedSub's stand-in ctors at
    # 004385f0 / 00438660 / 00631750 store three different image vftables, none of them a SpawnedSub)
    amb_cc = hand_ambiguous_names({a: [(unit, cc, fa, off) for unit, _, fa, off in sites if fa is not None
                                       for cc in ctor_classes(fa)] for a, sites in hand.items()}, exev)
    for a, sites in sorted(hand.items()):
        how = 'named vftable' if a in vmap else None
        if a not in vmap:
            for unit, cls, _, off in sites:
                if (a, cls, off) in amb_hand:
                    vstats['hand-written-ambiguous-name'] += 1
                    continue
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
                    if (a, cc, off) in amb_cc:
                        vstats['hand-written-ambiguous-ctor-class'] += 1
                        continue
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
    if exe_cls:
        # the exe's RTTI decides the class of each image vftable: its own class's native vtable at the COL offset
        # replaces a pairing by decomp names or hand-stored vptrs with another class (shells with fewer slots,
        # another real class), and maps vftables no unit names (exe_vftables.tsv)
        exe_alt = collections.defaultdict(list)   # exe vftable -> the native vtables the pairing displaced
        # S5 class 3: constructor-named vtables (_ZTV<n>FUN_<ctor>) no unit pairs, by the vftable their exe
        # constructor stores; a vftable another native vtable already stands for takes them as displaced ones
        ctor_pairs = ctor_store_pairs(exev, vmap, rtti, native, exe_path, exe_alt, vstats, out)
        # S2-bodies: stand-in vtables by the vptr stores of the function bound to the exe address (store_seq_pairs)
        def seq_ctor(name):
            m_ = BOUND_CALLEE.fullmatch(name)
            if m_:
                return (native.get(int(m_.group(1), 16), {}).get('msvc') or '').startswith('??0')
            return bool(membervt.CTOR_NAME.fullmatch(name))
        for k_, vs_ in store_seq_pairs(exev, vmap, rtti, native, exe_path, exe_alt, vstats, out,
                                       {u.source: u.obj for u in have}, seq_kind, seq_ctor).items():
            ctor_pairs.setdefault(k_, set()).update(vs_)
        exe_pair(exe_cls, vmap, rtti, by_cls, vstats, out, exe_alt)
        # S2-bodies class 4: the vtables a constructor stores in its members, by the exe constructor's [reg+d] stores
        for k_, vs_ in member_store_pairs(exev, vmap, rtti, native, exe_path, exe_alt, vstats, out,
                                          {u.source: u.obj for u in have}).items():
            ctor_pairs.setdefault(k_, set()).update(vs_)
        # W47: the shells no pass paired, by the vptr stores of any function bound to an exe address (factories,
        # stand-in constructors without a `??0` name, inlined decomp constructors): bound_store_pairs
        for k_, vs_ in bound_store_pairs(exev, vmap, rtti, native, exe_path, exe_alt, vstats, out,
                                         {u.source: u.obj for u in have}, ctor_pairs).items():
            ctor_pairs.setdefault(k_, set()).update(vs_)
        for a in rebind:
            rebind[a] = vmap[a][:2]
        # the slots of virtual functions defined nowhere: the native definition at the exe slot's address
        slot_over = {}   # (object, vtable group, word index) -> target, where one symbol's vtables disagree
        # the image vftables the units name that have no RTTI in the exe (no COL): their slots, bounded by the next
        # known vftable
        exev_all = dict(exev)
        exev_all.update(exevft.plain_vftables(exe_path, [v for v in set(vmap) | set(hand) if v not in exev],
                                              set(exev) | set(vmap) | {a_ for (_, a_) in vnames}))
        vstats['exe-plain-vftables'] = len(exev_all) - len(exev)
        global VTFILL_ITANIUM
        VTFILL_ITANIUM = itanium   # vtfill_hold_abi: slot_abi on the held slots
        fill = exe_slot_fill(exev_all, exe_cls, vmap, by_cls, rtti, have, per_obj, traps, native, itanium, msvc,
                             canon_it, out, bridges, slot_over, exe_path, providers, ctor_pairs, unbound, hints, chints,
                             lay_of=lay_of, probe_req=probe_req)
        for k_, n_ in fill.items():
            stats['slot_fill_' + k_] = n_
        byclass['vslot-unresolved'] -= fill.get('bound_refs', 0)
        byclass['vslot-exe'] += fill.get('bound_refs', 0)
        # primary vtables shorter than the exe's: an extended strong copy (the exe's remaining slots)
        d22_listed, d22_files = load_d22_list(os.environ.get('FNVNX_RECON'))
        d22_applied = load_d22_applied(os.path.dirname(os.path.dirname(os.path.abspath(units_json)))) \
            if units_json else set()
        vstats['d22-applied-vftables'] = len(d22_applied)
        d22_listed = set(d22_listed) | d22_applied
        vstats['alias-displaced-primaries'] = alias_alt(vnames, vmap, by_cls, rtti, exe_alt)
        ext = exe_vtable_copies(exev_all, vmap, rtti, have, per_obj, traps, native, bridges, out, slot_over,
                                exe_path, providers, exe_alt, msvc, d22_listed,
                                lambda c_: code_refs([u.obj for u in have], c_), unbound=unbound,
                                defined_fn=lambda c_: code_defs([u.obj for u in have], c_))
        vstats['d22-list-files'] = d22_files
        vstats['d22-list-vftables'] = len(d22_listed)
        for k_, n_ in ext.items():
            vstats['exe-copy-' + k_] = n_
        vstats['m1-compiled-sections'] = m1_compiled_renames(have, objsyms, out, collections.Counter())
        vstats['exe-order-shell-names'] = add_unit_shell_names(out, units_json, exe_path)
        for a in rebind:   # S2c-vt2: shifted secondary groups moved their address points (slotfill2.shifted)
            rebind[a] = vmap[a][:2]
        if os.environ.get('FNVNX_D26C', '1') != '0':
            # D26 (c): the image vftables that would stay at their image address - hand-stored (hand_vptr_stores)
            # or an RTTI vftable referenced as data - get a minus-one native copy (minus_one_copies)
            vmo_c, vst_c = vmo_candidates(exev, vmap, hand, used_addr,
                                          os.environ.get('FNVNX_D26C_ALL', '1') != '0')
            for k_, n_ in vst_c.items():
                vstats[k_] += n_
            miss = [a for a in vmo_c if a not in exev_all]
            if miss:
                exev_all.update(exevft.plain_vftables(exe_path, miss, set(exev_all) | set(vmap) | set(vmo_c) |
                                                      {a_ for (_, a_) in vnames}))
            lay_ = None
            if os.environ.get('FNVNX_CLASSLAYOUT', '1') != '0' and os.path.isfile(os.path.join(out, 'classlayout.tsv')):
                import classlayout as _cl
                lay_ = _cl.load_layout(os.path.join(out, 'classlayout.tsv'))
            vmo, vst = minus_one_copies(vmo_c, exev_all, native, providers, exe_path, msvc, bridges, traps, out, lay_)
            for k_, n_ in vst.items():
                vstats['vmo-' + k_] = n_
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

    _phase('exe rtti')
    # ---- data addresses: absolute symbols at the original address (the data image is mapped there, D3)
    with open(os.path.join(out, 'aliases.ld'), 'w') as f:
        f.write('/* fnv-nx link-by-address aliases (generated by tools/link/fnvlink.py; local output) */\n')
        # S5 (s5-002 item 3): without SANE_EXPR, GNU ld makes `__fnv_<vftable> = _ZTV... + 8;` an ABSOLUTE symbol,
        # so a relocated guest's GOT keeps its link address (1,330 entries; INISettingCollection's vptr 0x3191f7b8);
        # with it, `sym + 8` is section-relative (RELATIVE relocations) and `= 0x...` stays absolute
        f.write('LD_FEATURE("SANE_EXPR")\n')
        # R4: vftable getters return their stand-in's vtable; the stand-in's symbol is an alias of the paired native one
        vtget_lines, _ = vtget_aliases(exev, vmap, rebind, rtti, native, exe_path, {u.source: u.obj for u in have},
                                       stats, out)
        alias_lines += vtget_lines
        for a in sorted(x for x in used_addr if kind_of_addr(x) == 'data'):
            # an image vftable (a V entry, whether or not a vptr store was recognised: S5 J-0109 S2:mapped): every
            # data reference to it is the native address point
            rb_ = rebind.get(a) or (vmap[a][:2] if a in vmap else None)
            if rb_:
                f.write('%s = %s + %d;\n' % (ADDR_SYM % a, rb_[0], rb_[1]))
                stats['vtable_data_refs_rebound'] += 1
                stats['vtable_data_refs_rebound_unrecognised'] += a not in rebind
            elif a in vmo:
                f.write('%s = %s + 8;\n' % (ADDR_SYM % a, vmo[a]))
                stats['vtable_data_refs_minus_one_copy'] += 1
            else:
                f.write('%s = 0x%08x;\n' % (ADDR_SYM % a, a))
        for a in sorted(anchor_vals):
            f.write('%s = 0x%08x;\n' % (ANCHOR_SYM % a, a))
        for line in sorted(set(alias_lines)):
            f.write(line + '\n')

    _phase('data addresses')
    # ---- bridges
    # S5 (s5-002 item 4): an address map C row stands for an MSVC constructor, which returns this in eax; the
    # Itanium C1 returns void, so the row names a stub that returns this (all arguments in registers)
    cthis_rows = set()
    for a_, nd_ in native.items():
        if nd_['unit'] is not None and not nd_.get('msvc_entry') and (nd_['msvc'] or '').startswith('??0') and \
                cthis_ok(msvc(nd_['msvc'])):
            cthis_rows.add(a_)
            bridges.setdefault(CTHIS_SYM % a_, emit_cthis_stub(CTHIS_SYM % a_, ADDR_SYM % a_))
    stats['ctor_returns_this_rows'] = len(cthis_rows)
    with open(os.path.join(out, 'bridges.S'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('// fnv-nx ABI bridges (generated by tools/link/fnvlink.py; local output)\n    .text\n')
        for bn in sorted(bridges):
            if isinstance(bridges[bn], str):
                f.write(bridges[bn]); continue
            target, v, rtext, dtext = bridges[bn]
            f.write(abi.emit_bridge(bn, target, v, 'ref: %s | def: %s' % (rtext, dtext)))
        f.write(emit_m1k_ranges(bridges, stats))
        f.write(emit_traps(traps))
        f.write(emit_xtraps(xtraps))
        f.write(emit_tls(tls_addrs))
    with open(os.path.join(out, 'abi.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#object\tundefined\taddress\tref_msvc\tdef_msvc\tverdict\tclasses\tmoves\tnotes\tref\tdef\n')
        for r in abi_rows:
            f.write('\t'.join(r) + '\n')

    with open(os.path.join(out, 'vacall_requests.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        for unit_, un_ in sorted(vacall_req):
            f.write('%s\t%s\n' % (unit_, un_))
    with open(os.path.join(out, 'probe_requests.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        for unit, t in sorted(probe_req):
            f.write('%s\t%s\n' % (unit, t))

    _phase('bridges')
    # ---- address map
    for a, names in load_extern_list(extern_list).items():
        extern_text.setdefault(a, set()).update(names)
    amap_kinds, n_deldtor = write_addrmap(out, native, def_at, units, {**vmap, **vmo_vrows(vmo, vmap, exev_all)},
                                          extern_text, providers, cthis_rows, adj_rows, gdel_rows)

    _phase('address map')
    # ---- stats
    resolved = sum(byclass[c] for c in ('code', 'code-bridge', 'code-manual', 'data', 'code-pending',
                                         'code-defmissing', 'code-adjthunk', 'code-sddstub', 'extern-text', 'text-addr',
                                         'text-callback',
                                         'import', 'provider', 'tls'))
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
    lines += ['', 'variadic references decided by their call sites (vacall.py): %d decided, %d not bridgeable, %d call '
              'sites disagree, %d units not recorded yet (vacall_requests.tsv: %d)' % (
                  stats['vacall_decided'], stats['vacall_not_bridgeable'], stats['vacall_sites_disagree'],
                  stats['vacall_unrecorded'], len(vacall_req))]
    lines += ['', 'calls into vsnprintf-forwarding definitions decided by their format literals: %d exact, %d bridged, '
              '%d non-literal or not modelled, %d sites disagree, %d not bridgeable' % (
                  stats['format_exact'], stats['format_bridged'], stats['format_unknown'],
                  stats['format_sites_disagree'], stats['format_not_bridgeable'])]
    lines += ['', 'exit callbacks registered by address (xexit.py): niptr %d, thiscall %d; left to trap: %s' % (
        stats['xexit_niptr'], stats['xexit_thiscall'], ' '.join('%08x' % a for a in sorted(xexit_left)) or '-')]
    lines += ['', 'MSVC entries at a secondary base (exe vftables, thisadj.py): %d addresses; the map names the '
              '_ZThn thunk for %d, %d without a thunk; references through another class bound to the thunk: %d'
              % (stats['msvc_entry_exe'], stats['msvc_entry_thunks'], stats['msvc_entry_no_thunk'],
                 stats['msvc_entry_refs'])]
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
              'addresses with a provider (import thunks): %d, references bound to it: %d'
              % (len(providers), byclass['provider']),
              'import references bound by IAT slot (imports.tsv + exports.cpp): %d of %d'
              % (stats['imports_bound'], byclass['import']),
              'import thunks bound to their import instead of the compiled decomp body: %d' % stats['import_thunks'],
              'global FUN_<addr> names defined by an unrelated body (colliding.tsv): %d names, %d definitions made '
              'references, %d other objects\' references bound by address' % (
                  stats['colliding_fun_names'], stats['colliding_fun_defs'], stats['colliding_refs_by_address']),
              'calls of an IAT slot address bound to its import: %d' % stats['iat_calls'],
              'data references bound by identifier only (another scope or decorated type): %d; identifiers at several '
              'addresses (left ambiguous): %d, of the bound ones by the unit\'s own name: %d'
              % (stats['ident_addr'], stats['ident_ambiguous'], stats['ident_unit']),
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
    _phase('stats')
    log('resolve phases: ' + ', '.join(_phases))


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


TAG_HEAD = re.compile(r'//\s*@fnv\s+0x([0-9a-fA-F]+)[^\n]*\n((?:[ \t]*(?://[^\n]*)?\n)*)([^\n(;{}]*)\(')
TAG_NAME = re.compile(r'((?:\w+\s*::\s*)*~?\w+)\s*$')


def tag_names(text):
    """`// @fnv 0x<a>` tags of a unit text -> {address: [qualified name of the function defined after the tag]}:
    the head before the first `(` of the next non-blank, non-comment line (`FNVNX_KEEP T *C::f(` -> `C::f`)."""
    out = collections.defaultdict(list)
    for m in TAG_HEAD.finditer(text):
        n = TAG_NAME.search(m.group(3))
        out[int(m.group(1), 16)].append(re.sub(r'\s+', '', n.group(1)) if n else None)
    return out


def flags_like(isig, cls):
    """An Itanium definition shaped like a deleting destructor of class qual `cls`: a member of cls with one integer
    parameter (the flags), or a free function (pointer, integer). cls is empty when undname gives no qualified name
    (a class local to a function: `??_GFUN_0048ef40_shell@?1??FUN_0048ef60@@...`): the free shape only."""
    ps = [abi.classify(p) for p in (isig.raw_params or []) if p != 'void']
    ints = ('I8', 'I16', 'I32')
    if cls and isig.qual[:-1] == cls:
        return len(ps) == 1 and ps[0] in ints
    return len(isig.qual) == 1 and len(ps) == 2 and ps[0] == 'P' and ps[1] in ints


def tag_flags_defs(real, native, objsyms, msvc, itanium, tree, stats):
    """W48 (R2, vtgate 4264bfd7, exe 0x00e22500 / 00431980 / 0048ef60 / 00e00910): units.json binds address a to
    the MSVC deleting destructor `??_GC@@UAEPAXI@Z` (or `??_E`), no object defines C's D0, and the unit's source
    defines the code at a as a plain function: `// @fnv 0x00e22500` before `C *C::FUN_00e22500(unsigned char)`, or
    a free `void FUN_00431980(void *, unsigned)`. The definition the tag names, when the unit tags a exactly once
    and defines exactly one function of that name with the flags shape (flags_like), is the native definition at a
    (its msvc stays the ??_G name: vmo_dtor_slots / exe_slot_fill make D1 / D0 from it with the flags stubs).
    Returns {a: (unit, msvc name, elf sym)}. stats: tag-flags-* (no-tag: no body at all, a D0 to synthesize)."""
    want = collections.defaultdict(list)
    for u in real:
        for a, n in u.defs:
            if a not in native and n.startswith(('??_G', '??_E')):   # undname: a local class's ??_G has no variant
                want[u].append((a, n))
    out = {}
    for u in sorted(want, key=lambda x: x.source):
        path = os.path.join(tree or '', u.source)
        if not tree or not os.path.exists(path):
            stats['tag-flags-no-source'] += len(want[u]); continue
        tags = tag_names(open(path, encoding='utf-8', errors='surrogateescape').read())
        funcs = collections.defaultdict(list)
        for s in objsyms.get(u.source, ()):
            if s.shndx != 0 and s.type == 'func' and s.bind in ('global', 'weak') and \
                    not s.name.endswith('.localalias') and s.name.startswith('_Z'):
                funcs[tuple(itanium(s.name).qual)].append(s)
        for a, n in want[u]:
            if a in out:
                continue
            names = tags.get(a, [])
            if not names:
                stats['tag-flags-no-tag'] += 1; log('tag-flags: no @fnv tag for %08x (%s)' % (a, n)); continue
            if len(names) != 1 or names[0] is None:
                stats['tag-flags-several-tags'] += 1; continue
            cands = {s.name: s for s in funcs.get(tuple(names[0].split('::')), ())
                     if not itanium(s.name).variant}
            cls = msvc(n).qual[:-1]
            shaped = [s for nm, s in sorted(cands.items()) if flags_like(itanium(nm), cls)]
            if not cands:   # a constructor's spelling at the ??_G (`C::C()`, R2's 00cc7e50 / 0041c660): no body
                q_ = names[0].split('::')
                stats['tag-flags-not-defined' + ('-ctor' if len(q_) > 1 and q_[-1] == q_[-2] else '')] += 1
                continue
            if len(shaped) != 1:
                stats['tag-flags-not-flags-shape' if not shaped else 'tag-flags-ambiguous'] += 1
                log('tag-flags: %08x %s: %s' % (a, names[0], 'not flags-shaped' if not shaped else 'ambiguous'))
                continue
            out[a] = (u, n, shaped[0])
    return out


def no_candidate_why(sites, ctor_classes):
    """Why a hand-stored image vtable has no candidate class (S5 J-0108 item 3): what a candidate would need."""
    if all(fa is None for _, _, fa, _ in sites):
        return 'no storing address: the function stores several vtables at one offset, or has no @fnv annotation'
    names = [n for _, _, fa, _ in sites if fa is not None for n in ctor_classes(fa)]
    if not names:
        return 'no constructor/destructor name for the storing address in any unit (a shell only)'
    return 'candidates without a vtable'


HV_BRACE = re.compile(r'[{}]')
HV_RCAST = re.compile(r'\b(?:reinterpret|static|const)_cast\s*<[^<>]*(?:<[^<>]*>[^<>]*)*>')
HV_FIELD_LHS = re.compile(r'^[\w\s\[\]().*+/<>:-]+$')   # W53: an lvalue spelled with names, members, indexes, casts
HV_ASSIGN = re.compile(r'^(?P<lhs>(?:[^=]|==)*?[^=!<>+\-*/%&|^])=(?!=)(?P<rhs>.*)$', re.S)
HV_CAST = re.compile(r'\(\s*(?:const\s+|unsigned\s+|signed\s+|struct\s+|class\s+)*[\w:<>]+(?:\s*\*)*\s*\)')
HV_NOISE = re.compile(r'[\s&()]')


def _hv_unit(text, names, known, near):
    """hand_vptr_stores for one unit text: [(address, class as written or None, function index, @fnv address, offset)]
    (a pure function of its arguments: kept per unit between link processes, _hv_cache)."""
    got = []
    heads = sorted([(m.start(), m.group(1)) for m in FUNC_HDR.finditer(text)] +
                   [(m.start(), None) for m in FREE_HDR.finditer(text)], key=lambda h: h[0])
    fnvs = [(m.start(), int(m.group(1), 16)) for m in FNV_ANNOT.finditer(text)]
    pos = 0
    for stmt in text.split(';'):
        start, pos = pos, pos + len(stmt) + 1
        if '=' not in stmt:   # HV_ASSIGN needs one (T1: most statements; the same sites)
            continue
        st = HV_BRACE.split(stmt)[-1]
        spos = start + len(stmt) - len(st)   # where the statement itself begins (after the last brace)
        m = HV_ASSIGN.match(st)
        if not m:
            continue
        rhs = m.group('rhs')
        if os.environ.get('FNVNX_HVFIELD', '1') != '0':
            rhs = HV_RCAST.sub('', rhs)   # W53: `reinterpret_cast<int>(&DAT_010bcdc8)`
        while True:
            r2 = HV_CAST.sub('', rhs)
            if r2 == rhs:
                break
            rhs = r2
        rhs = HV_NOISE.sub('', rhs)
        a = names.get(rhs)
        far = False   # W53: a DAT_ name outside the window: only a tentative (exe-confirmed) site may use it
        if a is None and DAT_NAME.match(rhs):   # a name units.json does not bind for this unit (S5 J-0109)
            a = int(DAT_NAME.match(rhs).group(1), 16)
            if not kind_of_addr(a) == 'data':
                a = None
            elif not near[0] <= a <= near[1]:
                far = True
        if a is None:
            continue
        lhs = m.group('lhs').strip()
        off = 0
        mo = DEREFN_LHS.match(lhs) or DEREFK_LHS.match(lhs)
        if mo:
            off = int(mo.group(1), 0) * (4 if mo.re is DEREFK_LHS else 1)
        if far or not (a in known or VPTR_LHS.search(lhs) or DEREF_LHS.match(lhs) or
                       ((DEREF1_LHS.match(lhs) or (mo and off)) and near[0] <= a <= near[1])):
            # W53 (cc_census, a9d9f910): a field store of an image address next to the named vftables
            # (`m_6c = &DAT_010b52a0`, `p[0x6c / 4] = (int)&DAT_…`, `*(int**)((char*)this + 0x6c) = &DAT_…`) is a
            # tentative site (offset None): hand_vptr_confirm keeps it when the exe function stores that address
            # at an object offset itself
            # (any data-image address: the exe's own store and the table head decide, hand_vptr_confirm; the window
            # around the named vftables left out 010b0e9c / 010b511c / 010b5140 on link 7f0f8526)
            if kind_of_addr(a) == 'data' and HV_FIELD_LHS.match(lhs) and os.environ.get('FNVNX_HVFIELD', '1') != '0':
                off = None
            else:
                continue
        cls, fn, hpos_ = None, -1, 0
        for k, (hpos, hcls) in enumerate(heads):
            if hpos > spos:
                break
            cls, fn, hpos_ = hcls, k, hpos
        # the storing function's original address: its `// @fnv 0x...` annotation, the last one before the
        # store (prepare keeps them above each definition)
        fa = max(((p_, x) for p_, x in fnvs if p_ < spos), default=(None, None))[1]
        got.append((a, cls, fn, fa, off))
    return got


def _hv_cache(path):
    """{key: [(address, class, function index, @fnv address, offset)]} of out/cache/hvs.marshal, under this file's
    sha256 (T1: resolve 1 and 2 and every later head scanned every unit's statements again: 11 s of resolve)."""
    import marshal
    code = hashlib.sha256(open(os.path.abspath(__file__), 'rb').read()).hexdigest()
    try:
        d = marshal.load(open(path, 'rb'))
        return code, (d['units'] if d.get('code') == code else {})
    except (OSError, EOFError, ValueError, TypeError, KeyError):
        return code, {}


M1_SECTION = 'fnvnx_m1'   # S1 / orchestrator 2026-11-08: the minus-one tables, between __fnvnx_m1_start / _end (guest.ld)


def m1_section(minus_one, other='fnvnx_vtext', k=0):
    """The .data.rel.ro.<name> prefix of a bridges.S table: minus-one tables with the destructor pair at slot k = 0
    go into the m1 range S1's runtime slot rule tests a vptr against, k = 4 (the TESForm family) into the m1k4 range
    (S1 shifts those slots past 4 only), any other k stays out of both (toolchain/guest/guest.ld). FNVNX_M1=0: every
    table where it was."""
    if minus_one and os.environ.get('FNVNX_M1', '1') != '0' and k in (0, 4):
        return (M1_SECTION if k == 0 else M1_SECTION + 'k4') + ('.vmo' if other == 'fnvnx_vmo' else '')
    return other


M1K = {}   # W51: minus-one tables whose destructor pair sits at k not in {0, 4}: symbol -> (k, bridges key)


def m1k_note(sym, key, minus_one, k):
    """W51 (S1's RAWVT1-ANY, fnvnx_rawvt.h): a minus-one table written with its destructor pair at k outside {0, 4}
    lies in neither fast range; it is listed in __fnvnx_m1k_ranges (emit_m1k_ranges) so FNVNX_RAWVT_DYN shifts only
    its slots past k. FNVNX_M1K=0: none listed."""
    if minus_one and k not in (0, 4) and os.environ.get('FNVNX_M1K', '1') != '0' and \
            os.environ.get('FNVNX_M1', '1') != '0':
        M1K[sym] = (k, key)


def emit_m1k_ranges(bridges, st=None):
    """W51: `__fnvnx_m1k_ranges[] = {start, end, k}` (address point to the table's last word) for the tables m1k_note
    listed, and `__fnvnx_m1k_count`; every table's words are counted in its bridges.S text."""
    rows = []
    for sym, (k, key) in sorted(M1K.items()):
        text = bridges.get(key)
        if not isinstance(text, str) or ('\n%s:\n' % sym) not in text:
            continue
        body = text.split('\n%s:\n' % sym, 1)[1].split('    .size', 1)[0]
        n = sum(1 for l in body.splitlines() if l.strip().startswith('.word'))
        if n <= 2:
            continue
        rows.append('    .word %s + 8, %s + %d, %d' % (sym, sym, 4 * n, k))
    if st is not None:
        st['m1k-ranges'] = len(rows)
    out = ['    .section .data.rel.ro.fnvnx_m1k_ranges,"aw"', '    .p2align 2',
           '    .globl __fnvnx_m1k_ranges', '    .hidden __fnvnx_m1k_ranges', '    .type __fnvnx_m1k_ranges, %object',
           '__fnvnx_m1k_ranges:'] + (rows or ['    .word 0, 0, 0']) + \
          ['    .size __fnvnx_m1k_ranges, .-__fnvnx_m1k_ranges',
           '    .globl __fnvnx_m1k_count', '    .hidden __fnvnx_m1k_count', '    .type __fnvnx_m1k_count, %object',
           '__fnvnx_m1k_count:', '    .word %d' % len(rows), '    .size __fnvnx_m1k_count, 4', '    .text']
    return '\n'.join(out) + '\n'


def m1_compiled_renames(have, objsyms, out, st):
    """The compiled vtable groups whose primary has GCC's destructor pair (D1 / D0, or the flags pair a binding
    makes of it: exevft.dtor_pos): their section goes into the m1 range too, by an objcopy --rename-section appended
    to the object's .args (every object that defines the group, so whichever COMDAT copy the linker keeps is in the
    range). -> renamed sections."""
    if os.environ.get('FNVNX_M1', '1') == '0':
        return 0
    n = 0
    for u in have:
        if not any(s_.name.startswith('_ZTV') and s_.shndx and s_.bind != 'local' for s_ in objsyms.get(u.source, ())):
            continue
        try:
            o = vtables.Obj(u.obj)
        except (OSError, ValueError):
            continue
        ren = []
        for z in sorted(x for x in o.defs if x.startswith('_ZTV') and o.bind(x) != 'local'):
            shndx = o.syms[o.defs[z]][5]
            if shndx >= len(o.secnames):
                continue
            sec = o.secnames[shndx]
            if not sec.startswith('.data.rel.ro') or M1_SECTION in sec:
                continue
            ents = o.ents(z) or []
            pts = vtables.address_points(ents)
            if not pts:
                continue
            ap = pts[0][0] // 4
            nslots = vtables.slots_at(ents, pts[0][0])
            prim = ents[ap:ap + nslots]
            k_ = exevft.dtor_pos([e.sym for e in prim], [e.word for e in prim])
            if k_ not in (0, 4):
                continue
            new = '.data.rel.ro.%s.%s' % (m1_section(True, k=k_), sec[len('.data.rel.ro.'):] if sec.startswith('.data.rel.ro.')
                                          else z)
            ren.append('--rename-section=%s=%s' % (sec, new))
        if ren:
            with open(os.path.join(out, 'objcopy', os.path.basename(u.obj) + '.args'), 'a', encoding='utf-8',
                      errors='surrogateescape') as f:
                f.write('\n'.join(sorted(set(ren))) + '\n')
            n += len(set(ren))
    st['m1-compiled-sections'] = n
    return n


def named_placeholder(sym, called):
    """D22-USED: the destructor slot's symbol is a placeholder (`v00`, `s_000`) that code calls by name, so prepare
    keeps the declaration and the table stays in exe order. A real destructor (D0 / D1 / D2: HighProcess's
    `FUN_008d98e0::~FUN_008d98e0()`, called by name from the derived destructors) is no placeholder: a minus-one
    table already, whoever calls it (link 0e8b2eb8: 4 process tables, 1,940 shifted slots). FNVNX_D22DTOR=0 counts
    it as before."""
    if not sym or sym not in (called or ()):
        return False
    if os.environ.get('FNVNX_D22DTOR', '1') != '0' and (sigs.itanium_variant(sym) or '').startswith('D'):
        return False
    return True


def hand_ambiguous_names(hand, exev):
    """hand_vptr_stores' sites {a: [(unit, cls, fa, off)]} -> {(a, cls, off)} whose class name cannot pick the native
    vtable: the name's stores at that offset write two or more image vftables (one class has one vftable per offset;
    a stand-in name several units declare on their own, `struct StandIn`, `struct S`, does not), and the exe's RTTI
    class of a is not that name. FNVNX_HANDAMB=0: none."""
    if os.environ.get('FNVNX_HANDAMB', '1') == '0':
        return set()
    import classlayout as _cl
    cls_addrs = collections.defaultdict(set)
    for a, sites in hand.items():
        for _, cls, _, off in sites:
            if cls:
                cls_addrs[(cls, off)].add(a)
    out = set()
    for a, sites in hand.items():
        for _, cls, _, off in sites:
            if not cls or len(cls_addrs[(cls, off)]) < 2:
                continue
            e = exev.get(a)
            if e is not None and e.td and cls in _cl.simple_name(e.td):
                continue
            out.add((a, cls, off))
    return out


def vftable_head(exe_path, a, d):
    """W53: the image words at a look like the vftable of a subobject at offset d: slot 0 is a deleting destructor
    (d = 0), or an adjustor thunk of exactly d in front of one (`sub ecx, d; jmp ??_G`). A callback table stored in
    a field has neither."""
    import struct as _st
    try:
        pe, data = exevft._text(exe_path)
        off = pe.rva_to_off(a - pe.image_base, 4)
    except Exception:
        return False
    if off is None:
        return False
    w0 = _st.unpack_from('<I', data, off)[0]
    if d == 0:
        return bool(exevft.deleting_dtor(exe_path, w0))
    t = exevft.thunk(exe_path, w0)
    return bool(t and t[0] == d and exevft.deleting_dtor(exe_path, t[1]))


def hand_vptr_confirm(hand, exe_path, st=None):
    """W53: hand_vptr_stores' tentative sites (offset None) stay only where the exe function at the site's @fnv
    address stores that image address into an object field itself (`mov dword ptr [reg+d], imm32`, a base register
    that is not esp / ebp); d becomes the site's offset. Without the exe or the function's address they are dropped.
    -> hand, changed in place."""
    for a in list(hand):
        keep = []
        for unit, cls, fa, off in hand[a]:
            if off is not None:
                keep.append((unit, cls, fa, off))
                continue
            ds = sorted({d for d, b, v in (exevft._ctor_stores(exe_path, fa) or ()) if v == a and
                         b not in ('esp', 'ebp')}) if exe_path and fa is not None else []
            ds = [d for d in ds if vftable_head(exe_path, a, d)]
            if ds:
                keep.append((unit, cls, fa, ds[0]))
                if st is not None:
                    st['hand-written-field-confirmed'] += 1
            elif st is not None:
                st['hand-written-field-dropped'] += 1
        if keep:
            hand[a] = keep
        else:
            del hand[a]
    return hand


def hand_vptr_stores(tree, real, vnames, msvc, canon_msvc, cache_path=None):
    """Hand-written stores of image vftable addresses into vptrs in the unit sources: `x.vtbl = &DAT_010176f8;`,
    `*(void **)this = kVtbl_X;`, ... -> {address: [(unit, canonical class of the storing member function or
    None)]}. A store counts when the right side is (after casts and &) a name the unit binds to a data address and
    the left side looks like a vptr (vtbl/vtable/vptr/vft... member, *(T **)p, ((T **)p)[0]), or the address is a
    vftable some unit names (??_7) whatever the left side is called."""
    known = {a for (_, a) in vnames}
    near = (min(known) - 0x10000, max(known) + 0x10000) if known else (1, 0)
    sites = []
    out = collections.defaultdict(list)
    code, cache = _hv_cache(cache_path) if cache_path else (None, None)
    used = {}
    ksig = hashlib.sha1(('%r|%r' % (sorted(known), near)).encode()).hexdigest()
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
        if cache is not None:
            key = hashlib.sha1(('%s\0%s\0%r' % (ksig, text, sorted(names.items()))).encode('utf-8', 'surrogateescape')).hexdigest()
            got = cache.get(key)
            if got is None:
                got = _hv_unit(text, names, known, near)
            used[key] = got
        else:
            got = _hv_unit(text, names, known, near)
        for a, cls, fn, fa, off in got:
            sites.append((a, u.source, canon_msvc(cls) if cls else None, (u.source, fn), fa, off))
    if cache_path and cache is not None:
        import marshal
        try:
            tmp = '%s.%d.tmp' % (cache_path, os.getpid())
            with open(tmp, 'wb') as f:
                marshal.dump({'code': code, 'units': used}, f)
            os.replace(tmp, cache_path)
        except (OSError, ValueError):
            pass
    per_fn = collections.defaultdict(set)
    for a, _, _, fn, _, off in sites:
        per_fn[(fn, off)].add(a)
    for a, unit, cls, fn, fa, off in sites:
        # the storing function's class and address only count for a function that stores one vftable at that
        # offset (a destructor resetting its own and then its base's vftable stores two)
        one = len(per_fn[(fn, off)]) == 1
        # W53: a tentative site (offset None) keeps its function's address however many it shares: the exe store
        # at that address decides its offset (hand_vptr_confirm); FUN_00b91a40 stores 010b511c and 010b5140
        out[a].append((unit, cls if one else None, fa if (one or off is None) else None, off))
    return out


NOT_SYNTH = ('std::', '__gnu_cxx::', '__cxxabiv1::')   # the C++ runtime defines its own


# ---- S2c: image vftables by the player's exe (RTTI; exevft.py, docs/LINK.md "Vftables from the player's exe")

def exe_vftable_classes(exev, ms, canon_msvc):
    """{vftable address: (canonical class, class text, COL offset, MSVC slot count)} of the exe's vftables whose
    TypeDescriptor names a class (llvm-undname spells templates too)."""
    names = {v: exevft.msvc_name(e.td) for v, e in exev.items()}
    ms.need(set(names.values()))
    out = {}
    for v, e in exev.items():
        m = vtables.VFT.match(ms[names[v]] or '')
        if m and not m.group(2):
            out[v] = (canon_msvc(m.group(1)), m.group(1), e.off, len(e.slots))
    return out


def exe_requests(exe_cls, have_cls, units, msvc, canon_msvc, compiled):
    """Classes the exe gives an image vftable that have no native vtable: {class: (text, host units)} for the
    synthesis step (why `exe-rtti`). Hosts: compiled units naming the vftable's address, then those naming a member
    of the class (they declare it), at most 8."""
    want = {}
    for v, (cls, text, _, _) in exe_cls.items():
        if cls not in have_cls and not cls.startswith(NOT_SYNTH) and '{anon}' not in cls:
            want.setdefault(cls, (text, set()))[1].add(v)
    if not want:
        return {}
    by_addr, by_cls = collections.defaultdict(set), collections.defaultdict(set)
    addrs = {v for _, vs in want.values() for v in vs}
    for u in units.values():
        if u.source not in compiled:
            continue
        for n, a in u.refs.items():
            if a in addrs:
                by_addr[a].add(u.source)
        for n in list(u.refs) + [n for _, n in u.defs]:
            if not n.startswith('?') or n.startswith('??_7'):
                continue
            sg = msvc(n)
            if sg.kind == 'func' and sg.qual and len(sg.qual) >= 2:
                c = canon_msvc('::'.join(sg.qual[:-1]))
                if c in want:
                    by_cls[c].add(u.source)
    out = {}
    for cls, (text, vs) in want.items():
        hosts = sorted(set().union(*(by_addr[v] for v in vs)))
        hosts += sorted(by_cls[cls] - set(hosts))
        if hosts:
            out[cls] = (text, hosts[:8])
    return out


CTOR_VT = re.compile(r'_ZTV\d+FUN_([0-9a-f]{8})')


def ctor_store_pairs(exev, vmap, rtti, native, exe_path, alt, vstats, out):
    """S5 class 3 (s5-003): a placeholder class named after its constructor (`_ZTV<n>FUN_<ctor>`) that no unit pairs
    with an image vftable keeps the slots the decomp declared (NiDX9Renderer's FUN_00e754a0: one slot, called at
    slot 54). The exe's constructor at <ctor> - an address the address map defines natively under an MSVC
    constructor name (`??0`) - names its vftable: the last `mov dword ptr [reg], imm32` of an RTTI primary
    vftable start (exevft.ctor_vftable). A vftable no native vtable stands for yet is paired in vmap; one that
    already has its vtable takes the shell as a displaced one (alt, as exe_pair does), so exe_vtable_copies extends
    it with the exe's slots either way. ctor_vftables.tsv lists each candidate with the outcome.
    -> {(group, address point): {exe vftable}} for exe_slot_fill."""
    bound = {e[0] for e in vmap.values()}
    starts = {w for w, e in exev.items() if e.td and e.off == 0}
    rows, got = [], collections.defaultdict(set)
    for z in sorted(rtti.where):
        m = CTOR_VT.fullmatch(z)
        if not m:
            continue
        a = int(m.group(1), 16)
        nd = native.get(a)
        v = None
        pts = [p for p, ott in vtables.address_points(rtti.ents(z) or []) if ott == 0]
        if z in bound:
            how = 'bound'
        elif nd is None or not (nd.get('msvc') or '').startswith('??0'):
            how = 'no native constructor at the address'
        elif not pts:
            how = 'no primary address point'
        else:
            v = exevft.ctor_vftable(exe_path, a, starts)
            if v is None:
                how = 'no vftable store'
            elif v not in vmap:
                vmap[v] = (z, pts[0], rtti.where[z][0].path, 'primary, the vftable its exe constructor %08x stores'
                           % a)
                how = 'paired'
            else:
                if all(e[:2] != (z, pts[0]) for e in alt.get(v, ())):
                    alt[v].append((z, pts[0], rtti.where[z][0].path, 'primary'))
                how = 'paired as displaced (%s)' % vmap[v][0]
            if v is not None:
                got[(z, pts[0])].add(v)
        vstats['ctor-vt-' + how.split(' (')[0].replace(' ', '-')] += 1
        rows.append((z, '%08x' % a, '' if v is None else '%08x' % v, how))
    with open(os.path.join(out, 'ctor_vftables.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#native vtable\tconstructor\texe vftable it stores\toutcome\n')
        for r in rows:
            f.write('\t'.join(r) + '\n')
    return got


def seq_kind(a, nd):
    """'ctor' / 'dtor' for a native definition the address map places at an exe constructor / destructor (its MSVC
    name), else None."""
    m = nd.get('msvc') or ''
    return 'ctor' if m.startswith('??0') else 'dtor' if m.startswith(('??1', '??_G', '??_E')) else None


def store_seq_pairs(exev, vmap, rtti, native, exe_path, alt, vstats, out, obj_of, kind_of, is_ctor):
    """S2-bodies stand-in pairing (the vtables of decomp stand-in classes named after an address, or a shell:
    `fnv_0093ad60::ActorStandIn`, `FUN_00508ee0`, `FUN_00483630_Base`, `Shell_8803a0`). A stand-in's vtable has
    no exe vftable when no unit names one and its name is not `_ZTV<n>FUN_<ctor>`. Its object is still built by a
    decomp function bound to an exe address, and both store vptrs: the native function (a constructor, destructor
    or scalar deleting destructor the address map places at <a>) stores the stand-in's vtable at this+0
    (membervt.vptr_stores, in code order), the exe function at <a> stores its RTTI vftables (exevft.vptr_store_seq).
    Pairing by position is the evidence:
      * constructor (`??0`): the last store of each (the most derived class; inlined bases come first), and the
        stores before it too when both sides have as many;
      * destructor (`??1`, `??_G`, `??_E`): the first store of each (its own class), and the rest when the counts
        are equal.
    A vtable another pass paired (vmap) is left; a vftable that already has a vtable takes the stand-in as a
    displaced one (alt), as ctor_store_pairs does. A stand-in two functions pair with different vftables is not
    paired (conflict). seq_vftables.tsv: every candidate with its outcome.
    -> {(group, address point): {exe vftable}} for exe_slot_fill."""
    bound = {e[0] for e in vmap.values()}
    starts = {w for w, e in exev.items() if e.td and e.off == 0}
    rows, got, claim = [], collections.defaultdict(set), {}
    objs = {}
    cand = []
    for a in sorted(native):
        nd = native[a]
        kind = kind_of(a, nd)
        path = obj_of.get(nd.get('unit')) if kind else None
        if not path:
            continue
        if path not in objs:
            objs.clear()
            try:
                objs[path] = vtables.Obj(path)
            except (OSError, ValueError):
                objs[path] = None
        o = objs[path]
        order = []
        if o is not None:
            membervt.vptr_stores(o, nd['sym'].name, is_ctor, order)
        ns, seen = [], set()
        for off, ev in order:
            if off == 0 and ev[0] == 'vt' and ev[1] in rtti.where:
                pts = [p for p, ott in vtables.address_points(rtti.ents(ev[1]) or []) if ott == 0]
                if ev[2] in pts and (ns[-1:] != [(ev[1], ev[2])]):
                    ns.append((ev[1], ev[2]))
        if not ns:
            continue
        exs = exevft.vptr_store_seq(exe_path, a, starts)
        if not exs:
            vstats['seq-vt-no-exe-store'] += 1
            continue
        ctor = kind == 'ctor'
        pairs = []
        if len(ns) == len(exs):
            pairs = list(zip(ns, exs))
        elif ctor:
            pairs = [(ns[-1], exs[-1])]
        else:
            pairs = [(ns[0], exs[0])]
        cand.append((a, kind, ns, exs, pairs))
    for a, kind, ns, exs, pairs in cand:
        for (z, ap), v in pairs:
            claim.setdefault((z, ap), {}).setdefault(v, []).append(a)
    for a, kind, ns, exs, pairs in cand:
        for (z, ap), v in pairs:
            if len(claim[(z, ap)]) > 1:
                how = 'conflict: %s' % ' '.join('%08x' % x for x in sorted(claim[(z, ap)]))
            elif z in bound:
                how = 'bound'
            elif v not in vmap:
                vmap[v] = (z, ap, rtti.where[z][0].path, 'primary, the vftable the exe %s %08x stores in the same '
                           'position as the native one' % (kind, a))
                bound.add(z)
                how = 'paired'
                got[(z, ap)].add(v)
            else:
                if all(e[:2] != (z, ap) for e in alt.get(v, ())):
                    alt[v].append((z, ap, rtti.where[z][0].path, 'primary'))
                bound.add(z)
                how = 'paired as displaced (%s)' % vmap[v][0]
                got[(z, ap)].add(v)
            vstats['seq-vt-' + how.split(' (')[0].split(':')[0].replace(' ', '-')] += 1
            rows.append((z, '%08x' % a, kind, '%d/%d' % (len(ns), len(exs)), '%08x' % v, how))
    with open(os.path.join(out, 'seq_vftables.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#native vtable\tfunction\tkind\tnative/exe stores\texe vftable\toutcome\n')
        for r in rows:
            f.write('\t'.join(r) + '\n')
    return got


def _prefix_ok(exev, vs):
    """The exe vftables vs agree slot by slot (each one's slots a prefix of the others')."""
    vs = sorted(vs)
    return all(exev[a].slots[:len(exev[b].slots)] == exev[b].slots[:len(exev[a].slots)]
               for i, a in enumerate(vs) for b in vs[i + 1:])


def bound_store_pairs(exev, vmap, rtti, native, exe_path, alt, vstats, out, obj_of, taken=()):
    """W47: shell vtables by the stores of any bound function. A decomp stand-in's vtable that no pass paired (no
    `??_7` name, not `_ZTV<n>FUN_<ctor>`, no `??0` / `??1` at the function storing it: `_ZTV12FUN_0050adf0`, the
    `CreateObject` / `Clone` factories of the staging blocks) is still stored into objects by natively compiled code:
    a function F that the address map binds to exe address X materialises `_ZTV<z>+8` and stores it at [xN] (this,
    the result of `operator new`, or a copy; membervt.vt_store_seq, which also takes in the stores of the decomp's
    own out-of-line constructors F calls that no exe address binds - the exe inlined them). The exe function at X
    stores its vftables at [reg] (exevft.vptr_store_seq). The two lists are paired by position:
      * as many stores on both sides: one pair per position;
      * one native vtable and one exe vftable, each stored one or more times: that pair;
      * else a constructor (`??0`) pairs its last stores, a destructor (`??1`, `??_G`, `??_E`) its first; any other
        function pairs nothing (`counts differ`).
    A vtable is paired only when every function agrees on its vftable, or on vftables that agree slot by slot (one a
    prefix of the other); else it is a conflict and stays unpaired. A vtable another pass paired (vmap, alt, taken)
    is left. A free vftable takes the vtable in vmap; one that has a vtable takes it as a displaced one (alt), so
    exe_vtable_copies (VTFILL) writes it as transform(exe table) either way, and never over a strong definition.
    bound_vftables.tsv: one row per (function, pair) with the outcome. FNVNX_BSTORE=0 turns the pass off.
    -> {(group, address point): {exe vftable}} for exe_slot_fill."""
    got = collections.defaultdict(set)
    if os.environ.get('FNVNX_BSTORE', '1') == '0':
        return got
    bound = {e[0] for e in vmap.values()} | {x[0] for zs in alt.values() for x in zs} | {k[0] for k in taken}
    starts = {w for w, e in exev.items() if e.td and e.off == 0}
    names = set()
    for nd in native.values():
        names.add(nd['sym'].name)
        names.update(s.name for s in (nd.get('variants') or {}).values())
    prim = {}

    def primary(z):
        if z not in prim:
            prim[z] = {p for p, ott in vtables.address_points(rtti.ents(z) or []) if ott == 0} if z in rtti.where \
                else set()
        return prim[z]
    objs, cand = {}, []
    for a in sorted(native):
        nd = native[a]
        path = obj_of.get(nd.get('unit'))
        if not path:
            continue
        if path not in objs:
            objs.clear()   # natives are sorted by address, mostly unit by unit: keep one object
            try:
                objs[path] = vtables.Obj(path)
            except (OSError, ValueError):
                objs[path] = None
        o = objs[path]
        sym = nd['sym'].name
        if o is None or sym not in o.defs:
            continue
        _, value, size, typ, _, shndx = o.syms[o.defs[sym]]
        if typ != 2 or shndx >= len(o.secs):
            continue
        def local_ctor(c):   # the decomp's own out-of-line constructor, in this object, bound to no exe address
            return c not in names and c in o.defs and bool(membervt.CTOR_NAME.fullmatch(c))
        if not any(value <= off < value + size and (o.syms[r[0]][0].startswith('_ZTV') or local_ctor(o.syms[r[0]][0]))
                   for off, r in o.rel(shndx).items()):
            continue   # names no vtable and calls no such constructor (most functions): the cheap test first
        memo, depth = {}, [0]

        def expand(c):
            if not local_ctor(c) or depth[0] > 2:
                return None
            if c not in memo:
                memo[c] = []
                depth[0] += 1
                memo[c] = membervt.vt_store_seq(o, c, expand)
                depth[0] -= 1
            return memo[c]
        ns = [(z, ap) for z, ap in membervt.vt_store_seq(o, sym, expand) if ap in primary(z)]
        if not any(z not in bound for z, _ in ns):
            continue
        exs = exevft.vptr_store_seq(exe_path, a, starts)
        kind = seq_kind(a, nd)
        if not exs:
            vstats['bstore-vt-no-exe-store'] += 1
            rows_ = [(z, '%08x' % a, kind or '-', '%d/0' % len(ns), '', 'no exe store') for z, _ in ns if z not in bound]
            cand.append((a, kind, ns, exs, [], rows_))
            continue
        if len(ns) == len(exs):
            pairs = list(zip(ns, exs))
        elif len(set(ns)) == 1 and len(set(exs)) == 1:
            pairs = [(ns[0], exs[0])]
        elif kind == 'ctor':
            pairs = [(ns[-1], exs[-1])]
        elif kind == 'dtor':
            pairs = [(ns[0], exs[0])]
        else:
            pairs = []
            vstats['bstore-vt-counts-differ'] += 1
        cand.append((a, kind, ns, exs, pairs, [] if pairs else [
            (z, '%08x' % a, kind or '-', '%d/%d' % (len(ns), len(exs)), '', 'counts differ')
            for z, _ in ns if z not in bound]))
    claim = collections.defaultdict(lambda: collections.defaultdict(list))
    for a, kind, ns, exs, pairs, _ in cand:
        for (z, ap), v in pairs:
            if z not in bound:
                claim[(z, ap)][v].append(a)
    rows = []
    for a, kind, ns, exs, pairs, extra in cand:
        rows += extra
        for (z, ap), v in pairs:
            if z in bound and (z, ap) not in claim:
                how = 'bound'
            elif len(claim[(z, ap)]) > 1 and not _prefix_ok(exev, claim[(z, ap)]):
                how = 'conflict: %s' % ' '.join('%08x' % x for x in sorted(claim[(z, ap)]))
            elif v in vmap and vmap[v][:2] == (z, ap) or any(x[:2] == (z, ap) for x in alt.get(v, ())):
                how = 'same'
            elif v not in vmap:
                vmap[v] = (z, ap, rtti.where[z][0].path, 'primary, the vftable the exe function %08x stores in the '
                           'same position as the native one (bound stores)' % a)
                how = 'paired'
                got[(z, ap)].add(v)
            else:
                alt[v].append((z, ap, rtti.where[z][0].path, 'primary'))
                how = 'paired as displaced (%s)' % vmap[v][0]
                got[(z, ap)].add(v)
            vstats['bstore-vt-' + how.split(' (')[0].split(':')[0].replace(' ', '-')] += 1
            rows.append((z, '%08x' % a, kind or '-', '%d/%d' % (len(ns), len(exs)), '%08x' % v, how))
    vstats['bstore-vt-tables'] = len(got)
    with open(os.path.join(out, 'bound_vftables.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#native vtable\tfunction\tkind\tnative/exe stores\texe vftable\toutcome\n')
        for r in rows:
            f.write('\t'.join(r) + '\n')
    return got


BOUND_CALLEE = re.compile(r'__fnv(?:nx_cthis)?_([0-9a-f]{8})(?:_C[12])?')


def vtget_pick(v, n, refs, ap_of, users):
    """R4 vftable getters: which stand-in vtable Z of a getter becomes an alias of the paired native vtable N.
    v: the image vftable the getter returns; n: (native vtable, address point) vmap/rebind gives v; refs: the `_ZTV`
    symbols the native function references; ap_of(z) -> [(address point, offset-to-top)] of z's group (None when z has
    no words); users: {vtable: {image vftables paired to it}}; refs in code order (the last one is the returned vptr).
    -> (outcome, z, delta) where delta = ap(N) - ap(Z) when outcome is 'alias', else z / delta are None."""
    zs = [r for r in refs if not r.startswith('_ZTVN10__cxxabiv')]
    if not zs:
        return 'native references no vtable', None, None
    if n[0] in zs:
        return 'already the paired vtable', None, None
    # a vtable another image vftable is paired with is a real class's (0x00dfd800 builds a second local
    # hkReferencedObject): not the stand-in. Of the rest, in code order, an inlined constructor sequence stores its
    # bases first, so the returned vptr is the last one (0x00dff380: S00dfbe20_Mid, then S00dff380_Object)
    free = [r for r in zs if not (users.get(r, set()) - {v})]
    if not free:
        return 'stand-in is paired to another vftable', None, None
    z = free[-1]
    pts = ap_of(z)
    if pts is None:
        return 'stand-in has no words', None, None
    if len(pts) != 1 or pts[0][1] != 0:
        return 'stand-in has %d address points' % len(pts), None, None
    return 'alias', z, n[1] - pts[0][0]


def vtget_aliases(exev, vmap, rebind, rtti, native, exe_path, obj_of, vstats, out):
    """R4: an MSVC vftable getter (Havok class registry: exevft.vtable_getter) returns the image vftable V; its native
    version builds a stand-in object and returns its own `_ZTV<stand-in>+8`, while the link pairs V with another native
    vtable N, so the getter hands back a vptr no object of the class carries. The stand-in's vtable symbol becomes an
    alias of N in aliases.ld (`Z = N + (apN - apZ);`; the script assignment overrides the weak COMDAT definition).
    -> ([alias line], vtget.tsv rows). FNVNX_VTGET=0 turns it off."""
    lines, rows = [], []
    if os.environ.get('FNVNX_VTGET', '1') == '0' or not exe_path:
        return lines, rows
    vfts = set(exev)
    users = collections.defaultdict(set)
    pairing = {}
    for v_ in vfts:
        e_ = rebind.get(v_) or (vmap[v_][:2] if v_ in vmap else None)
        if e_:
            pairing[v_] = e_
            users[e_[0]].add(v_)
    ap_cache = {}

    def ap_of(z):
        if z not in ap_cache:
            ents = rtti.ents(z) if z in rtti.where else None
            ap_cache[z] = vtables.address_points(ents) if ents else None
        return ap_cache[z]
    objs, claimed = {}, {}
    for a in sorted(native):
        nd = native[a]
        path = obj_of.get(nd.get('unit'))
        if not path:
            continue
        v = exevft.vtable_getter(exe_path, a, vfts)
        if v is None:
            continue
        vstats['vtget-candidates'] += 1
        n = pairing.get(v)
        row = ['%08x' % a, '%08x' % v, '', n[0] if n else '', '']
        rows.append(row)
        if n is None:
            row[4] = 'vftable is paired to no native vtable'
            continue
        if path not in objs:
            objs.clear()
            try:
                objs[path] = vtables.Obj(path)
            except (OSError, ValueError):
                objs[path] = None
        o = objs[path]
        sym = nd['sym'].name
        if o is None or sym not in o.defs:
            row[4] = 'no object / symbol'
            continue
        _, value, size, typ, _, shndx = o.syms[o.defs[sym]]
        if typ != 2 or shndx >= len(o.secs):
            row[4] = 'not a function'
            continue
        refs = []
        for off, r in sorted(o.rel(shndx).items()):
            nm_ = o.syms[r[0]][0]
            if value <= off < value + size and nm_.startswith('_ZTV'):
                if nm_ in refs:
                    refs.remove(nm_)
                refs.append(nm_)
        outcome, z, delta = vtget_pick(v, n, refs, ap_of, users)
        row[2] = z or ' '.join(sorted(refs))
        row[4] = outcome
        if outcome != 'alias':
            continue
        if claimed.get(z, (n, delta)) != (n, delta):
            row[4] = 'stand-in aliased to another vtable by another getter'
            continue
        claimed[z] = (n, delta)
        lines.append('%s = %s + %d;' % (z, n[0], delta))
        vstats['vtget_alias'] += 1
        row[4] = 'aliased'
    with open(os.path.join(out, 'vtget.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#address\tvftable\tstand-in vtable\tnative vtable\toutcome\n')
        for r in rows:
            f.write('\t'.join(r) + '\n')
    return lines, rows


def member_store_pairs(exev, vmap, rtti, native, exe_path, alt, vstats, out, obj_of, rules=None):
    """S2-bodies class 4 (s5-003 round 2): member vtables. A constructor stores one vptr per polymorphic member
    subobject and secondary base, and so does the exe's: `mov dword ptr [reg+d], imm32` of an RTTI vftable
    (exevft.ctor_member_vftables; S5's emulator stand-in counts 313 constructors, 888 offsets). The decomp's members
    are often placeholder classes (the renderer's RendererMap<N> at +0x610..: one slot, bound to nothing, called at
    slot 1). For every native `??0` definition at an exe constructor's address, the native vptr at the same offset
    d (membervt.vptr_stores: the last vtable stored at this+d, or the class of the constructor called with this+d)
    is paired with the exe vftable stored there:
      * a primary vtable (offset-to-top 0) takes a free vftable in vmap, else joins it as a displaced vtable (alt),
        so exe_vtable_copies extends it with the exe's slots either way (as ctor_store_pairs does);
      * a secondary vtable (a member's or the constructor's own secondary base) only stands for the vftable in the
        slot fill.
    The decomp's layouts are the exe's (D11's static_asserts), so the offsets are compared as they are. A pairing a
    named rule excludes (membervt.RULES: NiD3DShaderFactory's NiTFixedStringMap member) is listed and not made.
    member_vftables.tsv: one row per (constructor, offset) with the outcome.
    -> {(group, address point): {exe vftable}} for exe_slot_fill."""
    rules = membervt.RULES if rules is None else rules
    vfts = set(exev)
    starts = {w for w, e in exev.items() if e.td and e.off == 0}
    rows, got = [], collections.defaultdict(set)
    objs, pts_cache, short = {}, {}, set()

    def pts_of(z):
        if z not in pts_cache:
            ents = rtti.ents(z) or []
            pts_cache[z] = (ents, dict(vtables.address_points(ents)))
        return pts_cache[z]

    def callee_vtable(name):
        m = BOUND_CALLEE.fullmatch(name)
        if m:
            nd_ = native.get(int(m.group(1), 16))
            name = nd_['sym'].name if nd_ else ''
        z = membervt.ctor_class_vtable(name, rtti.where)
        if z is None:
            return None
        p = [p_ for p_, ott in vtables.address_points(rtti.ents(z) or []) if ott == 0]
        return (z, p[0]) if p else None
    def is_ctor(name):   # a constructor by its Itanium name, or bound to the address of a native `??0`
        m = BOUND_CALLEE.fullmatch(name)
        if m:
            return (native.get(int(m.group(1), 16), {}).get('msvc') or '').startswith('??0')
        return bool(membervt.CTOR_NAME.fullmatch(name))
    for a in sorted(native):
        nd = native[a]
        if not (nd.get('msvc') or '').startswith('??0') or not nd.get('unit'):
            continue
        m = exevft.ctor_member_vftables(exe_path, a, vfts)
        if not m:
            continue
        vstats['member-vt-constructors'] += 1
        cv = exevft.ctor_vftable(exe_path, a, starts)
        ctd = exev[cv].td if cv in exev else None
        path = obj_of.get(nd['unit'])
        nat = {}
        if path:
            if path not in objs:
                objs.clear()   # natives are sorted by address, mostly unit by unit: keep one object
                try:
                    objs[path] = vtables.Obj(path)
                except (OSError, ValueError):
                    objs[path] = None
            if objs[path] is not None:
                nat = membervt.vptr_stores(objs[path], nd['sym'].name, is_ctor)
        for d in sorted(m):
            v = m[d]
            e = exev[v]
            z = ap = None
            nslots = nm = None
            ev = nat.get(d)
            rule = membervt.excluded(ctd, e.td, rules)
            if ev and ev[0] == 'vt':
                z, ap = ev[1], ev[2]
            elif ev:
                zp = callee_vtable(ev[1])
                if zp:
                    z, ap = zp
            if rule:
                how = 'excluded: rule ' + rule.name
            elif path is None:
                how = 'no object'
            elif ev is None:
                how = 'no native vptr store at the offset'
            elif z is None:
                how = 'callee constructs no class with a vtable (%s)' % ev[1]
            elif z not in rtti.where:
                b = BOUND_CALLEE.fullmatch(z)   # a hand-written vptr bound to its image vftable (D26 c)
                how = 'native vtable not defined' if not b else 'stores the image vftable itself' if \
                    int(b.group(1), 16) == v else 'stores another image vftable'
            elif ap not in pts_of(z)[1]:
                how = 'not an address point'
            else:
                ents, pts = pts_of(z)
                nslots = vtables.slots_at(ents, ap)
                dp = exevft.dtor_pos([x.sym for x in ents[ap // 4:ap // 4 + nslots]],
                                     [x.word for x in ents[ap // 4:ap // 4 + nslots]])
                nm = nslots - 1 if dp is not None else nslots
                if vmap.get(v, (None, None))[:2] == (z, ap):
                    how = 'same'
                elif pts[ap]:
                    how = 'paired as secondary (slot fill only)'
                elif v not in vmap:
                    vmap[v] = (z, ap, rtti.where[z][0].path, 'primary, the member vftable constructor %08x stores at '
                               '+%#x' % (a, d))
                    how = 'paired'
                else:
                    if all(x[:2] != (z, ap) for x in alt.get(v, ())):
                        alt[v].append((z, ap, rtti.where[z][0].path, 'primary'))
                    how = 'paired as displaced (%s)' % vmap[v][0]
                got[(z, ap)].add(v)
                if not pts[ap] and len(e.slots) > nm:
                    short.add((z, ap, v))
            # the constructor's own secondary base (its class's vftable at that COL offset) or a member object
            kind = 'base' if ctd and e.td == ctd and e.off == d else 'member'
            vstats['member-vt-%s-offsets' % kind] += 1
            vstats['member-vt-' + how.split(' (')[0].split(':')[0].replace(' ', '-')] += 1
            if how.startswith('excluded'):
                vstats['member-vt-excluded-' + rule.name] += 1
            rows.append(('%08x' % a, ctd or '', '%#x' % d, kind, '%08x' % v, e.td or '', z or '',
                         '' if ap is None else str(ap), '' if nm is None else str(nm), str(len(e.slots)), how))
    vstats['member-vt-short'] = len(short)   # primary pairs whose native vtable has fewer slots than the exe's
    with open(os.path.join(out, 'member_vftables.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#constructor\tits exe class\toffset\tbase or member\texe vftable\texe class\tnative vtable\t'
                'address point\tnative slots (D1/D0 once)\texe slots\toutcome\n')
        for r in rows:
            f.write('\t'.join(r) + '\n')
    return got


def exe_pair(exe_cls, vmap, rtti, by_cls, vstats, out, alt=None):
    """Pair each image vftable of the exe with its own class's native vtable (the address point whose
    offset-to-top is minus the COL offset). It replaces the pairing by decomp names or hand-stored vptrs when that
    names another class, unless the own vtable is shorter than both the current one and the exe's slot count (a
    unit's partial declaration of the class: the longer one keeps the slots). Adds the vftables no unit names.
    exe_vftables.tsv: per exe vftable the class, its native vtable, what was paired before and the outcome."""
    rows = []
    for v in sorted(exe_cls):
        cls, text, off, ne = exe_cls[v]
        cur = vmap.get(v)
        curn = vtables.slots_at(rtti.ents(cur[0]) or [], cur[1]) if cur else None
        zs = sorted(by_cls.get(cls, []))
        own = None
        if zs:
            ents = rtti.ents(zs[0]) or []
            hit = [p for p, ott in vtables.address_points(ents) if ott == -off]
            if hit:
                own = (zs[0], hit[0], vtables.slots_at(ents, hit[0]))
        kind = 'primary' if not off else 'secondary'
        if own is None:
            how = 'no native vtable of the class' if not zs else 'no native vtable at offset %d' % off
        elif cur and (cur[0], cur[1]) == own[:2]:
            how = 'same'
        elif cur and own[2] < curn and own[2] < ne:
            how = 'kept: the class\'s own vtable is shorter'
            if alt is not None:
                alt[v].append((own[0], own[1], rtti.where[own[0]][0].path, kind))
        else:
            how = 'added' if cur is None else 'replaced'
            if alt is not None and cur is not None:   # the shell the units construct keeps being used natively
                alt[v].append((cur[0], cur[1], cur[2], 'primary' if cur[3].startswith('primary') else kind))
            vmap[v] = (own[0], own[1], rtti.where[own[0]][0].path, '%s, the exe\'s RTTI class %s%s%s' % (
                kind, text, ' at +%d' % off if off else '', ', synthesized' if rtti.where[own[0]][1] else ''))
        vstats['exe-%s-%s' % (kind, how.split(':')[0].replace(' ', '-').replace('\'', ''))] += 1
        nat = vmap.get(v)
        natn = vtables.slots_at(rtti.ents(nat[0]) or [], nat[1]) if nat else None
        if nat is not None and natn < ne:
            vstats['exe-%s-native-shorter-than-exe' % kind] += 1
        rows.append(('%08x' % v, text, str(off), str(ne), own[0] if own else '', str(own[2]) if own else '',
                     cur[0] if cur else '', '' if curn is None else str(curn), how,
                     '' if natn is None else str(natn)))
    with open(os.path.join(out, 'exe_vftables.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#vftable\texe class\tCOL offset\texe slots\town native vtable\tits slots\tpaired before\tits '
                'slots\toutcome\tnative slots now\n')
        for r in rows:
            f.write('\t'.join(r) + '\n')


ABI_MANUAL_TRAP = ('sret-explicit', 'ret-aggregate', 'aggregate-param')


_ABI_LAYOUT_EQUAL = None


def abi_layout_equal(path=None):
    """{(address %08x, reference symbol)} of tools/link/abi_layout_equal.tsv: manual pairs proved layout-equal"""
    global _ABI_LAYOUT_EQUAL
    if _ABI_LAYOUT_EQUAL is None or path:
        p = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'abi_layout_equal.tsv')
        s = set()
        if os.path.isfile(p):
            for line in open(p):
                f = line.rstrip('\n').split('\t')
                if len(f) >= 2 and not line.startswith('#') and re.fullmatch(r'[0-9a-f]{8}', f[0]):
                    s.add((f[0], f[1]))
        _ABI_LAYOUT_EQUAL = s
    return _ABI_LAYOUT_EQUAL


def abi_manual_trap(v):
    """A `manual` pair that differs in how an aggregate or the result pointer is passed (sret-explicit, ret-aggregate,
    aggregate-param): with no bridge (abi2 has no layout plan) the reference is left unbound, so it stays a trap stub
    and is listed (cause abi-manual) instead of binding across the difference (orchestrator, D24). Varargs-only
    manual pairs keep their direct binding: AArch64 passes variadic arguments where it passes named ones."""
    return any(c in ABI_MANUAL_TRAP for c in v.classes)


def abi_manual_why(why):
    """Reason bucket of an abi-manual pair (abi2.compare's why list) for the stats."""
    w = why[0] if why else 'unknown'
    if w.startswith('no layout: '):
        kinds = {c.split(':', 1)[0] + (' result' if c.endswith(' result') else '')
                 for c in w[len('no layout: '):].split('; ')}
        return 'no layout (%s)' % ','.join(sorted(kinds))
    return w.split(':', 1)[0] if w.startswith('variadic') else w


def slot_abi(isig, ds):
    """How a call through a vtable slot declared isig (Itanium, a virtual member: this in x0) reaches the definition
    ds (MSVC): 'same' or 'the slot passes more' (the definition reads only registers the caller set) when every
    argument the definition reads arrives where it reads it, in the same register file; else what differs. A free
    definition whose first parameter is a pointer takes the object there (decomp stand-ins of thiscall code). The
    slot's return type is not in its Itanium name, so results are not compared."""
    sl = copy.copy(isig)
    sl.member = True
    sloc = abi.a64_locations(sl)
    dd = ds
    if not ds.member or ds.static:
        if not ds.raw_params or ds.raw_params == ['void']:
            return 'the slot passes more'   # reads no argument at all (a static getter: GetRTTI)
        if abi.classify(ds.raw_params[0]) != 'P':
            return 'the definition is not a member'
        dd = copy.copy(ds)
        dd.member = False
    dloc = abi.a64_locations(dd)
    for i, (_, c, loc) in enumerate(dloc):
        if i >= len(sloc):
            # the slot's own declaration is a placeholder or partial (`virtual void v2();`, the AbstractHeap slots
            # of FUN_00aa7400): callers reach the slot through their own class's declaration, which passes them
            return 'the definition reads more arguments (%d of %d)' % (len(dloc) - 1, len(sloc) - 1)
        _, sc, sl_ = sloc[i]
        if loc[0] in ('agg', 'sym', 'varargs') or sl_[0] in ('agg', 'sym', 'varargs'):
            if c != sc or loc[0] != sl_[0]:
                return 'aggregate or varargs argument %d differs (%s / %s)' % (i, sc, c)
        elif loc != sl_ or abi._bucket(c) != abi._bucket(sc):
            return 'argument %d differs (%s / %s)' % (i, sc, c)
    return 'same' if len(dloc) == len(sloc) else 'the slot passes more'


def slot_msvc_sig(isig, ds):
    """The MSVC declaration a call through a vtable slot (Itanium isig, a virtual member) stands for: a __thiscall
    member with the slot's parameters; its result as the definition's (the Itanium name has none)."""
    r = sigs.Sig('func', isig.text)
    r.qual, r.params, r.raw_params = list(isig.qual), list(isig.params or []), list(isig.raw_params or [])
    r.cv, r.cc, r.member, r.static, r.virtual = '', 'thiscall', True, False, True
    r.ret, r.raw_ret = ds.ret, ds.raw_ret
    return r


THIS_SYM = '__fnvnx_vthis_%08x'   # an exe slot `mov ecx, imm32; jmp T`: x0 = imm32, then T (exevft.this_thunk)


def emit_this_stub(name, imm, target):
    return '\n'.join(['    .section .text.%s,"ax",%%progbits' % name, '    .p2align 2', '    .globl %s' % name,
                      '    .hidden %s' % name, '    .type %s, %%function' % name, '%s:' % name,
                      '    movz x0, #0x%x' % (imm & 0xffff), '    movk x0, #0x%x, lsl #16' % (imm >> 16),
                      '    b %s' % target, '    .size %s, .-%s' % (name, name), '    .text']) + '\n'


def vftable_import(u, un, isig, addr, native, providers, bridges, used_addr, stats, msvc, exe_slots, exe_path,
                   keep):
    """keep unless addr is a slot of an exe vftable (exe_slots {slot address: word}); then the word decides:
    S4's provider (a `_purecall`); a native definition with as many parameters as the reference declares (a
    declaration of another arity is no evidence: the decomp lost the argument); the thunk `mov ecx, imm32; jmp T`
    to such a native T, as a stub that sets x0 = imm32 and branches to it."""
    w = exe_slots.get(addr)
    if w is None:
        return keep
    want = None if isig.params is None else len([p for p in isig.params if p != 'void'])

    def arity_ok(a):
        nd = native.get(a)
        ds = msvc(nd['msvc']) if nd and nd.get('msvc') else None
        if ds is None or ds.kind != 'func' or ds.raw_params is None or want is None:
            return False
        return len([p for p in ds.raw_params if p != 'void']) == want
    if w not in native and w in providers:
        stats['import_vftable_provider'] += 1
        return 'provider', 'import/vftable-slot', w, providers[w][0], 'vftable slot %08x' % addr
    if w in native and arity_ok(w):
        stats['import_vftable_slot'] += 1
        return 'addr', 'import/vftable-slot', w, None, None
    th = exevft.this_thunk(exe_path, w) if exe_path else None   # also over a native of another arity
    if th is not None and th[1] in native and arity_ok(th[1]):
        name = THIS_SYM % w
        bridges.setdefault(name, emit_this_stub(name, th[0], ADDR_SYM % th[1]))
        used_addr.add(th[1])
        stats['import_vftable_this_thunk'] += 1
        return 'code', 'import/vftable-slot', th[1], name, 'vftable slot %08x, this = %08x' % (addr, th[0])
    stats['import_vftable_left'] += 1
    return keep


CTHIS_SYM = '__fnvnx_cthis_%08x'   # an MSVC constructor called for its result: the C1, then return this


def cthis_ok(ds):
    """The constructor's arguments are all in registers (the stub moves sp: stack arguments would shift)."""
    return ds is not None and ds.kind == 'func' and all(
        loc[0] in ('x', 'v', 'x2') for _, _, loc in abi.a64_locations(ds))


def emit_cthis_stub(name, target):
    return '\n'.join(['    .section .text.%s,"ax",%%progbits' % name, '    .p2align 2', '    .globl %s' % name,
                      '    .hidden %s' % name, '    .type %s, %%function' % name, '%s:' % name,
                      '    stp x29, x30, [sp, #-32]!', '    mov x29, sp', '    str x0, [sp, #16]', '    bl %s' % target,
                      '    ldr x0, [sp, #16]', '    ldp x29, x30, [sp], #32', '    ret',
                      '    .size %s, .-%s' % (name, name), '    .text']) + '\n'


CONST_SYM = '__fnvnx_vconst_%08x'   # a trivial x86 leaf (exevft.leaf_constant) no unit compiles
ADJ_SYM = '__fnvnx_vadj_%08x'       # an MSVC adjustor thunk no unit compiles: this -= n, then its target


def load_vtbind(top=None, path=None):
    """R4's VTFILL binding file (fnvnx-vtbind/1, fnv-nx-build recon/vtbind bind.tsv): {exe address: (kind, symbol,
    target, n)}; kind `fn` (the native of the exe function) or `adj` (an MSVC adjustor thunk: this -= n, then target).
    From FNVNX_VTBIND, else any bind.tsv with that header under FNVNX_RECON; {} without one."""
    files = []
    path = path or os.environ.get('FNVNX_VTBIND')
    top = top or os.environ.get('FNVNX_RECON')
    if path and os.path.isfile(path):
        files = [path]
    elif top and os.path.isdir(top):
        files = sorted(os.path.join(dp, f) for dp, _, fs in os.walk(top) for f in fs if f in ('bind.tsv', 'bindings.tsv'))
    out = {}
    for f in files:
        try:
            lines = open(f).read().split('\n')
        except OSError:
            continue
        if not lines or not lines[0].startswith(('# fnvnx-vtbind/1', '# fnvnx-vtgate-r4-bindings/1')):
            continue
        r4 = lines[0].startswith('# fnvnx-vtgate-r4-bindings/1')   # exe_addr, native_symbol, kind, target, n
        for line in lines[1:]:
            if not line or line.startswith('#'):
                continue
            p = line.split('\t')
            if len(p) < 5:
                continue
            try:
                if r4:
                    out[int(p[0], 16)] = (p[2], p[1], p[3], int(p[4] or 0))
                else:
                    out[int(p[0], 16)] = (p[1], p[2], p[3], int(p[4] or 0))
            except ValueError:
                continue
    return out


def emit_adj_stub(name, target, n):
    op = ('    sub w0, w0, #%d' % n) if 0 < n < 4096 else ('    add w0, w0, #%d' % -n) if -4096 < n < 0 else None
    if op is None:   # a larger adjustment: through w16
        op = '    movz w16, #0x%x\n    movk w16, #0x%x, lsl #16\n    sub w0, w0, w16' % (n & 0xffff, (n >> 16) & 0xffff)
    return '\n'.join(['    .section .text.%s,"ax",%%progbits' % name, '    .p2align 2', '    .globl %s' % name,
                      '    .hidden %s' % name, '    .type %s, %%function' % name, '%s:' % name, op,
                      '    b %s' % target, '    .size %s, .-%s' % (name, name), '    .text']) + '\n'


def adjustor_thunks(def_at, native, exe_path, providers=None):
    """{address: (n, target)} of the .text addresses units.json places with no native definition whose exe code is
    an MSVC adjustor thunk (exevft.thunk: `sub ecx, n; jmp`, `add ecx, n; jmp`, through at most two more plain or
    adjusting thunks) into an address the native build defines; n != 0. GCC has no counterpart of these (its
    `_ZThn` thunks are vtable-only), so callers bound to the address got a trap stub (code-defmissing)."""
    out = {}
    if not exe_path:
        return out
    for a in sorted(def_at):
        if a in native or a in (providers or {}) or kind_of_addr(a) != 'text':
            continue
        n, t = 0, a
        for _ in range(3):
            th = exevft.thunk(exe_path, t)
            if th is None:
                break
            n, t = n + th[0], th[1]
            if t in native or t in HK_SDD:
                if n:
                    out[a] = (n, t)
                break
    return out


def nonvirtual_deldtors(def_at, native, units, objsyms, msvc, itanium, newdel_votes, providers=None):
    """{address: (D1 symbol, operator delete symbol)} of the scalar deleting destructors (`??_GC@@QAE...`, C's
    destructor not virtual) units.json places with no native definition: GCC emits no D0 for them. D1 is the placing
    unit's own native D1/D2 of C (a global or COMDAT definition), else the native at the address the unit binds
    `??1C@@...` to; the delete is the address most units bind the global `??3@YAXPAX@Z` to (native or a provider).
    A class some unit names a member operator delete of (`??3C@@`), or without a D1 or delete, is left out."""
    out = {}
    dv = newdel_votes.get('??3@YAXPAX@Z')
    da = dv.most_common(1)[0][0] if dv else None
    dl = ADDR_SYM % da if da in native else ((providers or {}).get(da) or (None,))[0]
    if dl is None:
        return out
    member_del = set()
    for u in units.values():
        for n in list(u.refs) + [n for _, n in u.defs]:
            if n.startswith('??3') and not n.startswith('??3@'):
                q = msvc(n).qual or []
                member_del.add(tuple(sigs.canon_qual(q[:-1])))
    for a, (u, n) in sorted(def_at.items()):
        if a in native or a in (providers or {}) or not n.startswith('??_G') or kind_of_addr(a) != 'text':
            continue
        m = msvc(n)
        if m.kind != 'func' or m.variant != 'sdd' or m.virtual or not m.qual or len(m.qual) < 2:
            continue
        cq = tuple(sigs.canon_qual(m.qual[:-1]))
        if cq in member_del:
            continue
        d1 = None
        for s in sorted(objsyms.get(u.source, ()), key=lambda x: x.name):
            if s.shndx and s.type == 'func' and s.bind in ('global', 'weak') and s.name.startswith('_Z'):
                g = itanium(s.name)
                if g.variant in ('D1', 'D2') and tuple(sigs.canon_qual(g.qual[:-1])) == cq:
                    d1 = s.name
                    break
        if d1 is None:
            for rn, ra in sorted(u.refs.items()):
                rm = msvc(rn) if rn.startswith('??1') else None
                if rm is not None and rm.variant == 'dtor' and tuple(sigs.canon_qual(rm.qual[:-1])) == cq and \
                        ra in native and sigs.itanium_variant(native[ra]['sym'].name) != 'D0':
                    d1 = ADDR_SYM % ra
                    break
        if d1 is not None:
            out[a] = (d1, dl)
    return out


HK_FREE = 'fnvnx_hk_heap_block_free'   # extern "C" void (void *, int): the recon's blockFree through the router heap
HK_SDD = {}   # W22: {??_G address: its destructor's native symbol} of the Havok sized-free ??_G with a stand-in


def hk_d_sym(native, d):
    """The native symbol destroying without freeing at address d: its D1 when the definition there is a D0."""
    nd = native.get(d)
    if nd is None:
        return None
    if sigs.itanium_variant(nd['sym'].name) == 'D0':
        vs = nd['variants'].get('D1')
        return None if vs is None else ADDR_SYM % d + ('' if vs is nd['sym'] else '_D1')
    return ADDR_SYM % d


def hk_sdd_rows(def_at, native, exe_path, anydef, providers, stats):
    """{address G: destructor symbol} of the addresses units.json places with no native definition whose exe code is
    exactly the Havok sized-free `??_G` (defshapes.sdd_sized_exact, read from the exe here) and whose destructor
    has a native body. Gate: the link must define `fnvnx_hk_heap_block_free` (the hkostream recon); without it
    nothing is emitted and each such address counts as hk_sdd_no_free, so the names stay traps."""
    out = {}
    if not exe_path:
        return out
    for a in sorted(def_at):
        if a in native or a in (providers or {}) or kind_of_addr(a) != 'text' or ADDR_SYM % a in anydef:
            continue
        d = defshapes.sdd_sized_exact(exe_path, a)
        if d is None:
            continue
        ds = hk_d_sym(native, d)
        if ds is None:
            stats['hk_sdd_no_destructor'] += 1
        elif HK_FREE not in anydef:
            stats['hk_sdd_no_free'] += 1
        else:
            out[a] = ds
    return out


def emit_hk_sdd_stub(name, d, free):
    """MSVC's Havok `void *C::??_G(unsigned flags)`: d(this); if (flags & 1) free(this, *(uint16_t *)(this + 4));
    return this. The size is the object's own word at +4, as the exe reads it."""
    body = ['    stp x29, x30, [sp, #-32]!', '    mov x29, sp', '    stp x19, x20, [sp, #16]', '    mov w19, w0',
            '    mov w20, w1', '    bl %s' % d, '    tbz w20, #0, 1f', '    mov w0, w19', '    ldrh w1, [x19, #4]',
            '    bl %s' % free, '1:  mov w0, w19', '    ldp x19, x20, [sp, #16]', '    ldp x29, x30, [sp], #32',
            '    ret']
    return '\n'.join(['    .section .text.%s,"ax",%%progbits' % name, '    .p2align 2', '    .globl %s' % name,
                      '    .hidden %s' % name, '    .type %s, %%function' % name, '%s:' % name] + body +
                     ['    .size %s, .-%s' % (name, name), '    .text']) + '\n'


D0DEL_SYM = '__fnvnx_d0del_%08x'   # D0 = the destroy-only body at the address, then operator delete


def deleting_native(nd, msvc):
    """The native definition at an address is itself a deleting destructor: Itanium D0, an MSVC `??_G` / `??_E`, or a
    decomp (this, flags) function."""
    if sigs.itanium_variant(nd['sym'].name) == 'D0':
        return True
    n = nd.get('msvc') or ''
    return n.startswith(('??_G', '??_E')) or nd.get('tier') == 'tag-flags' or flags_param(msvc(n) if n else None)


def global_delete(newdel_votes, native, providers):
    """The global operator delete most units bind `??3@YAXPAX@Z` to (native or S4's provider), else None."""
    dv = newdel_votes.get('??3@YAXPAX@Z')
    da = dv.most_common(1)[0][0] if dv else None
    if da is None:
        return None
    return ADDR_SYM % da if da in native else ((providers or {}).get(da) or (None,))[0]


def emit_d0_stub(name, d1, dl):
    """GCC's D0(this): d1(this); dl(this) (the deleting destructor of a class whose only native body destroys)."""
    body = ['    stp x29, x30, [sp, #-32]!', '    mov x29, sp', '    str x19, [sp, #16]', '    mov w19, w0',
            '    bl %s' % d1, '    mov w0, w19', '    ldr x19, [sp, #16]', '    ldp x29, x30, [sp], #32',
            '    b %s' % dl]
    return '\n'.join(['    .section .text.%s,"ax",%%progbits' % name, '    .p2align 2', '    .globl %s' % name,
                      '    .hidden %s' % name, '    .type %s, %%function' % name, '%s:' % name] + body +
                     ['    .size %s, .-%s' % (name, name), '    .text']) + '\n'


def emit_sdd_stub(name, d1, dl):
    """MSVC's scalar deleting destructor `void *C::??_G(unsigned flags)`: d1(this); if (flags & 1) dl(this);
    return this."""
    body = ['    stp x29, x30, [sp, #-32]!', '    mov x29, sp', '    stp x19, x20, [sp, #16]', '    mov w19, w0',
            '    mov w20, w1', '    bl %s' % d1, '    tbz w20, #0, 1f', '    mov w0, w19', '    bl %s' % dl,
            '1:  mov w0, w19', '    ldp x19, x20, [sp, #16]', '    ldp x29, x30, [sp], #32', '    ret']
    return '\n'.join(['    .section .text.%s,"ax",%%progbits' % name, '    .p2align 2', '    .globl %s' % name,
                      '    .hidden %s' % name, '    .type %s, %%function' % name, '%s:' % name] + body +
                     ['    .size %s, .-%s' % (name, name), '    .text']) + '\n'


def adj_target(native, t):
    """What an adjustor thunk into t branches to: t's MSVC entry when MSVC enters t with this at a secondary base
    (thisadj.py: the thunk hands over the MSVC `this`), else t's native definition."""
    return MSVC_ENTRY_SYM % t if native.get(t, {}).get('msvc_entry') else ADDR_SYM % t


def emit_const_stub(name, value):
    """`return value;` (or `return;` for 'void'): the exact native form of the exe's constant leaf."""
    body = ['    ret'] if value == 'void' else (
        ['    mov w0, #%d' % value, '    ret'] if value < 0x10000 else
        ['    movz w0, #0x%x' % (value & 0xffff), '    movk w0, #0x%x, lsl #16' % (value >> 16), '    ret'])
    return '\n'.join(['    .section .text.%s,"ax",%%progbits' % name, '    .p2align 2', '    .globl %s' % name,
                      '    .hidden %s' % name, '    .type %s, %%function' % name, '%s:' % name] + body +
                     ['    .size %s, .-%s' % (name, name), '    .text']) + '\n'


FLAG_SYM = '__fnvnx_vflag_%08x_%d'   # GCC destructor slot -> a decomp (this, flags) function at the exe slot
GDEL_SYM = '__fnvnx_vgdel_%08x'      # placeholder slot -> the exe's ??_G (native D0 / D1 by the caller's flags)


def flags_param(ds):
    """The MSVC declaration takes the object and one integer (the deleting destructor's flags): a member with one
    integer parameter, or a free function (pointer, integer)."""
    if ds is None or ds.kind != 'func' or ds.raw_params is None:
        return False
    ps = [abi.classify(p) for p in ds.raw_params if p != 'void']
    ints = ('I8', 'I16', 'I32')
    if ds.member and not ds.static:
        return len(ps) == 1 and ps[0] in ints
    return len(ps) == 2 and ps[0] == 'P' and ps[1] in ints


def emit_flag_stub(name, target, flags):
    return '\n'.join(['    .section .text.%s,"ax",%%progbits' % name, '    .p2align 2', '    .globl %s' % name,
                      '    .hidden %s' % name, '    .type %s, %%function' % name, '%s:' % name,
                      '    mov w1, #%d' % flags, '    b %s' % target, '    .size %s, .-%s' % (name, name),
                      '    .text']) + '\n'   # the bridges after it assume .text


FREE_SYM = '__fnvnx_vfree_%08x'   # exe slot -> a decomp free function: `this` dropped / edx inserted
FREE_SLOT = re.compile(r'^\?FUN_[0-9a-f]{8}@@Y([AGI])')   # a global FUN_<a> and its MSVC convention (cdecl, stdcall, fastcall)


NAMED_FREE = re.compile(r'^\?[A-Za-z_]\w*@@YG')   # a named global __stdcall function (`?AllocateZeroedMemory@@YG...`)
# W50: a named global (or namespace-scope) __fastcall function (`?SetChildAt@fnv_00a93ea0@@YIX...`)
NAMED_FAST = re.compile(r'^\?[A-Za-z_]\w*(?:@[A-Za-z_]\w*)*@@YI')
_FREE_ARITY = {}


def named_free_drop(a, nd, exe_path):
    """S2d (link af44165a: BSNiAllocator slot 1 0x00aa2240): the decomp declares the exe's member as a named global
    __stdcall function, `AllocateZeroedMemory(unsigned&, unsigned&, ...)`; the exe's is a thiscall member whose `this`
    is unused. A virtual call passes `this` in x0, so the free function read the allocator's vptr as its size. True
    when the exe function's first return pops exactly 4 x the native's parameter count (`ret 0x1c`, 7 parameters:
    `this` is not among them; a definition that takes the object as its first parameter pops one fewer).
    W52 (cc_census, a9d9f910: 22 slots): also a namespace-scope or static-member __stdcall (`?F@ns@@YG`, `?F@C@@SG`)
    whose native symbol keeps the MSVC nesting (not wrapped into a shell class), a by-value struct return (its hidden
    pointer is popped too: 4 x (n + 1)), and more than 7 parameters (the bridge moves the stack ones). Not for a COM
    table (its __stdcall callers push the object; exe_slot_target passes com). FNVNX_FREENAMED=0 turns it off."""
    if not exe_path or os.environ.get('FNVNX_FREENAMED', '1') == '0':
        return False
    name = nd['sym'].name
    ms = nd.get('msvc') or ''
    if NAMED_FREE.match(ms) and re.match(r'_Z\d+[A-Za-z_]', name):
        pass
    elif os.environ.get('FNVNX_FREENS', '1') != '0' and NAMED_STD.match(ms) and name.startswith('_Z') and \
            msvc_depth(ms) is not None and msvc_depth(ms) == itanium_depth(name):
        pass
    else:
        return False
    n = _native_arity(name)
    if n is None or n > 15:
        return False
    return _first_ret_pop(a, exe_path) == 4 * (n + (1 if msvc_sret(ms) else 0))


NAMED_STD = re.compile(r'^\?[A-Za-z_]\w*(?:@[A-Za-z_]\w*)*@@(?:YG|[CDKLST]G)')   # free / namespace / static __stdcall


def msvc_depth(msvc):
    """Name components of an MSVC function name (`?F@ns@C@@...` -> 3); None for template / special names."""
    if not msvc or not msvc.startswith('?') or msvc.startswith('??') or '?$' in msvc.split('@@', 1)[0]:
        return None
    return len(msvc[1:].split('@@', 1)[0].split('@'))


def itanium_depth(sym):
    """Name components of an Itanium function symbol (`_Z3fooi` -> 1, `_ZN2ns1C1fEv` -> 3); None for templates and
    special names."""
    if not sym.startswith('_Z'):
        return None
    i = 2
    if i < len(sym) and sym[i] == 'N':
        i += 1
        while i < len(sym) and sym[i] in 'KVRO':
            i += 1
        n = 0
        while i < len(sym) and sym[i] != 'E':
            m = re.match(r'\d+', sym[i:])
            if not m:
                return None
            i += len(m.group()) + int(m.group())
            n += 1
        return n or None
    return 1 if re.match(r'\d+', sym[i:]) else None


def msvc_sret(msvc):
    """A by-value class / struct return in an MSVC function name (the convention letter is followed by U / V)."""
    i = (msvc or '').find('@@')
    if i < 0:
        return False
    t = msvc[i + 2:]
    t = t[2:] if t[:1] == 'Y' or t[:1] in 'CDKLST' else t[3:]
    return t.startswith(('U', 'V', '?AU', '?AV', '?BU', '?BV'))


def named_free_edx(a, nd, exe_path):
    """W50 (link beaa825a, S5 null-device boot: SetChildAt 00a93ea0 entered with a code address as the index and a
    pointer into the node as the child): the decomp declares the exe's thiscall member as a named __fastcall free
    function, `fnv_00a93ea0::SetChildAt(NiNode*, int, unsigned, NiAVObject*)` (ecx = the object, edx a placeholder,
    then the stack). Code calls it through an ABI bridge, but VTFILL put the bare definition in NiNode's slot: a
    virtual call passes (this, index, child), one register early from the second argument on. True when the MSVC
    name is a global / namespace-scope __fastcall (`@@YI`), the native takes 2..8 parameters, and the exe function's
    first return pops exactly the stack ones, 4 x (n - 2). The slot then gets the edx bridge. FNVNX_FREEEDX=0 turns
    it off."""
    if not exe_path or os.environ.get('FNVNX_FREEEDX', '1') == '0':
        return False
    name = nd['sym'].name
    if not NAMED_FAST.match(nd.get('msvc') or '') or not name.startswith('_Z'):
        return False
    n = _native_arity(name)
    if n is None or n < 2 or n > 8:
        return False
    return _first_ret_pop(a, exe_path) == 4 * (n - 2)


def _native_arity(name):
    if name not in _FREE_ARITY:
        n = None
        try:
            dem = subprocess.run([CXXFILT, name], capture_output=True, text=True).stdout.strip()
            isg = sigs.parse_itanium(name, dem)
            if isg is not None and isg.raw_params is not None:
                n = len([p_ for p_ in isg.raw_params if p_ != 'void'])
        except OSError:
            pass
        _FREE_ARITY[name] = n
    return _FREE_ARITY[name]


_RET_POP = {}


def _first_ret_pop(a, exe_path, depth=0):
    """The bytes the x86 function at a pops at its first `ret` (0 for a plain `ret`), scanning up to 0x4000 bytes.
    W52b (cc_census): a frameless tail jump (no push / sub esp / mov ebp before it) decides it instead. `jmp <address>`
    takes that function's count; an indirect `jmp` (an import, a vtable slot) passes through the stack arguments it
    touches, 4 x the highest `[esp + 4k]`. None without capstone, outside the image, or at an int3 first
    (FNVNX_POPTAIL=0: the first `ret` within 0x400 bytes, as before)."""
    key = (a, exe_path, depth)
    if key in _RET_POP:
        return _RET_POP[key]
    try:
        import capstone
    except ImportError:
        return None
    pe, data = exevft._text(exe_path)
    try:
        off = pe.rva_to_off(a - pe.image_base, 16)
    except Exception:
        return None
    if off is None:
        return None
    tail = os.environ.get('FNVNX_POPTAIL', '1') != '0'
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    res, frame, top = None, False, 0
    for ins in md.disasm(data[off:off + (0x4000 if tail else 0x400)], a):
        m, op = ins.mnemonic, ins.op_str
        if m == 'int3':
            break
        if m == 'ret':
            res = int(op, 0) if op else 0
            break
        if not tail:
            continue
        if m == 'push' or (m == 'sub' and op.startswith('esp')) or (m == 'mov' and op.startswith('ebp, esp')):
            frame = True
        if not frame:
            for k in re.findall(r'\[esp \+ (0x[0-9a-f]+|\d+)\]', op):
                top = max(top, int(k, 0) // 4)
        if m == 'jmp' and not frame:
            if re.fullmatch(r'0x[0-9a-f]+', op) and depth < 3:
                res = _first_ret_pop(int(op, 16), exe_path, depth + 1)
            else:
                res = 4 * top
            break
    _RET_POP[key] = res
    return res


def no_return(a, exe_path):
    """True when the x86 function at a reaches an int3 before any ret, its last call / jmp a call (a reporter that
    never returns: its arguments' layout cannot matter to a caller)."""
    try:
        import capstone
    except ImportError:
        return False
    pe, data = exevft._text(exe_path)
    try:
        off = pe.rva_to_off(a - pe.image_base, 16)
    except Exception:
        return False
    if off is None:
        return False
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    last = None   # the last call / jmp: an int3 after a call (not a tail jmp) is the end of a non-returning body
    for ins in md.disasm(data[off:off + 0x4000], a):
        if ins.mnemonic == 'ret':
            return False
        if ins.mnemonic == 'int3':
            return last == 'call'
        if ins.mnemonic in ('call', 'jmp'):
            last = ins.mnemonic
    return False


def free_slot(a, nd, bridges, st, exe_path=None, com=False):
    """S5's class 1 (s5-003): the decomp declares the exe's slot function as a global non-member (`__stdcall
    FUN_00e8b490(int *, int, int)`: its MSVC body ignores ecx), so a virtual call, which passes `this` in x0, hands
    it every argument one register early. Such a slot gets a bridge by the definition's MSVC convention: cdecl /
    stdcall drop `this` (x1..x7 -> x0..x6), fastcall (ecx = this, edx undefined on the x86 call, then the stack)
    inserts edx as 0 (x1..x6 -> x2..x7). Returns the bridge symbol, or None for any other definition."""
    if nd is None or sigs.itanium_variant(nd['sym'].name):
        return None
    named = None
    if not re.match(r'(?:_Z\d+)?FUN_[0-9a-f]{8}', nd['sym'].name):
        if not com and named_free_drop(a, nd, exe_path):
            named = 'drop'
        elif named_free_edx(a, nd, exe_path):
            named = 'edx'
        else:
            return None
    m = FREE_SLOT.match(nd.get('msvc') or '')
    if named:
        mode = named
        st['free-slot-named' + ('' if named == 'drop' else '-edx')] += 1
    elif m:
        mode = 'edx' if m.group(1) == 'I' else 'drop'
    else:
        # R2 (vtgate 4264bfd7): an extern "C" stand-in, `@FUN_<a>@N` (fastcall: edx inserted) or `_FUN_<a>@N` /
        # `_FUN_<a>` (stdcall / cdecl: this dropped), whose native name is the plain C name (00c73170, 00d2dd50)
        mc = re.match(r'^([@_])(FUN_[0-9a-f]{8})(?:@\d+)?$', nd.get('msvc') or '')
        if not mc or nd['sym'].name != mc.group(2):
            return None
        mode = 'edx' if mc.group(1) == '@' else 'drop'
    if com and mode == 'drop':
        st['free-slot-com-kept'] += 1   # a COM caller pushes the object: the free function's first parameter is it
        return None
    name = FREE_SYM % a
    if name not in bridges:
        if mode == 'drop':
            body = ['    mov x%d, x%d' % (r, r + 1) for r in range(7)]
            n_ = _native_arity(nd['sym'].name)
            if n_ is not None and n_ > 7:
                # W52: the caller's 8th native argument is on the stack: x7 from [sp], then each stack argument one
                # slot down (AAPCS64 ILP32: 8-byte slots; the incoming argument area is the callee's)
                body.append('    ldr x7, [sp]')
                body += ['    ldr x16, [sp, #%d]\n    str x16, [sp, #%d]' % (8 * (i + 1), 8 * i) for i in range(n_ - 8)]
                st['free-slot-drop-stack'] += 1
        else:
            body = ['    mov x%d, x%d' % (r, r - 1) for r in range(7, 1, -1)] + ['    mov w1, #0']
        bridges[name] = '\n'.join(['    .section .text.%s,"ax",%%progbits' % name, '    .p2align 2',
                                   '    .globl %s' % name, '    .hidden %s' % name, '    .type %s, %%function' % name,
                                   '%s:' % name] + body + ['    b %s' % (ADDR_SYM % a),
                                   '    .size %s, .-%s' % (name, name), '    .text']) + '\n'
    st['free-slot-' + mode] += 1
    return name


def flags0_slot(isig, nd, ds, exe_path, a):
    """A placeholder slot (`virtual void v00();`, no parameters, not a destructor variant) filled with the exe's
    deleting destructor whose native definition reads the flags (`??_G` / `??_E` or a decomp `FUN_<a>(unsigned)`
    with the exe's ??_G code shape, exevft.deleting_dtor; R1's adjustor recon bodies): a native call through the
    slot leaves w1 undefined, and bit 0 set frees the object. The slot gets a stub passing flags 0 (destroy only).
    Temporary: D26 (b) declares `virtual ~C()` for the placeholder (s2d-008/009)."""
    if isig.variant or sigs.itanium_variant(nd['sym'].name) or isig.raw_params is None:
        return False
    if [p for p in isig.raw_params if p != 'void']:
        return False
    if (nd.get('msvc') or '').startswith(('??_G', '??_E')):   # adjustor thunks too (`[thunk]:` ... `adjustor{4}'`)
        return True
    return flags_param(ds) and bool(exe_path and exevft.deleting_dtor(exe_path, a))


GSYN_SYM = '__fnvnx_vgsyn_%08x'   # a synthesised D0 for an exe ??_G no unit compiles: its destructor, then delete


def emit_gsyn_stub(name, dtor):
    """D0 = `dtor(this); operator delete(this); return this` (ILP32: pointers in w registers)."""
    return '\n'.join(['    .section .text.%s,"ax",%%progbits' % name, '    .p2align 2', '    .globl %s' % name,
                      '    .hidden %s' % name, '    .type %s, %%function' % name, '%s:' % name,
                      '    stp x29, x30, [sp, #-32]!', '    mov x29, sp', '    str x19, [sp, #16]', '    mov w19, w0',
                      '    bl %s' % dtor, '    mov w0, w19', '    bl _ZdlPv', '    mov w0, w19', '    ldr x19, [sp, #16]',
                      '    ldp x29, x30, [sp], #32', '    ret', '    .size %s, .-%s' % (name, name), '    .text']) + '\n'


def emit_gdel_stub(name, d0, d1):
    return '\n'.join(['    .section .text.%s,"ax",%%progbits' % name, '    .p2align 2', '    .globl %s' % name,
                      '    .hidden %s' % name, '    .type %s, %%function' % name, '%s:' % name,
                      '    tbz w1, #0, 1f', '    b %s' % d0, '1:  b %s' % d1, '    .size %s, .-%s' % (name, name),
                      '    .text']) + '\n'


def rewrite_refs(path, by_src, addr_of, rebound=()):
    """exe_slot_fill's refs.tsv rewrite: the rows (vslot-unresolved, extern-text, import) of the symbols a unit's slot
    fill bound become `vslot-exe` with the target and the exe address. by_src: {source: {symbol: target}};
    addr_of: {(source, symbol): exe address}. refs.tsv names a synthesized vtable / body object by its own source
    (`<unit>#vtables`, `<unit>#bodies<n>`), so its rows are matched under that name, never under its host unit's (the
    compiled object's). rebound: {(source, symbol)} whose `code` rows (a name another unit's reference bound,
    exe_slot_fill's hints) are rewritten too. Returns (rows rewritten, of them not vslot-unresolved)."""
    rows = {s_: {un_: (t_, addr_of[(s_, un_)]) for un_, t_ in m.items()} for s_, m in by_src.items()}
    n = nx = 0
    with open(path, encoding='utf-8', errors='surrogateescape') as fi, \
            open(path + '.tmp', 'w', encoding='utf-8', errors='surrogateescape') as fo:
        for line in fi:
            f = line.split('\t')
            if len(f) >= 7 and (f[2] in ('vslot-unresolved', 'extern-text', 'import') or
                                (f[2] in ('code', 'unresolved') and (f[0], f[1]) in rebound)) and \
                    f[1] in rows.get(f[0], {}):
                t, a_ = rows[f[0]][f[1]]
                nx += f[2] != 'vslot-unresolved'
                f[2], f[3], f[4], f[5] = 'vslot-exe', 'exe-slot', '%08x' % a_, t
                line = '\t'.join(f)
                n += 1
            fo.write(line)
    os.replace(path + '.tmp', path)
    return n, nx


_CTOR_DTOR = re.compile(r'^(C[123]|D[012])')


def caller_class(sym):
    """The vtable symbol of a member function's class (`_ZN3Foo3barEv` -> _ZTV3Foo, `_ZN2ns3FooC1Ev` ->
    _ZTVN2ns3FooE), None for anything else (free functions, templates, local names)."""
    if not sym.startswith('_ZN'):
        return None
    i, parts = 3, []
    while i < len(sym) and sym[i] in 'KVR':
        i += 1
    while i < len(sym):
        if _CTOR_DTOR.match(sym[i:]):
            return _vt_name(parts)
        m = re.match(r'\d+', sym[i:])
        if not m:
            return None
        n = int(m.group())
        j = i + len(m.group())
        parts.append(sym[i:j + n])
        i = j + n
        if i < len(sym) and sym[i] == 'E':
            return _vt_name(parts[:-1])
    return None


def _vt_name(parts):
    if not parts:
        return None
    return '_ZTV' + parts[0] if len(parts) == 1 else '_ZTVN' + ''.join(parts) + 'E'


def direct_call_slot_binds(have, changed, addr_of, per_obj, rebound, traps, rows):
    """W48 (link b7b866ef, raw boot: `virtual function defined nowhere: _ZN6BSFile7GetSizeEv`): GCC devirtualizes a call
    in a constructor (or a method of a final class) into a direct `bl` to the slot's name. When that name is a trap in
    the object (the object's own vtables disagree on it, or it has none) but the calling function's class has a
    vtable the slot fill bound the name in (any object: a unit's `#vtables` object holds the synthesized ones), the
    call is that class's slot: the symbol gets that target. Callers of several classes must agree. FNVNX_W48=0: off.
    -> names bound."""
    if os.environ.get('FNVNX_W48', '1') == '0':
        return 0
    by_vt = collections.defaultdict(set)   # (symbol, vtable) -> {(target, exe address)}
    for r in rows:
        for use in r[7].split():
            by_vt[(r[2], use.split('+')[0])].add((r[4], int(r[3], 16)))
    n = 0
    for u in have:
        ren = per_obj.get(u.source) or {}
        trapped = {un for un, t in ren.items() if t in traps and un not in changed.get(u, {})}
        if not trapped:
            continue
        try:
            o = vtables.Obj(u.obj)
        except (OSError, ValueError):
            continue
        callers = collections.defaultdict(set)   # name -> {caller class}
        for i_ in range(len(o.secs)):
            if not o.secs[i_][2] & 4:
                continue
            sec = o.secnames[i_]
            fn = sec[len('.text.'):] if sec.startswith('.text.') else None
            # a unit built without -ffunction-sections (archive.cpp's FUN_00af7eb0 constructor sits in `.text`):
            # the call's function is the FUNC symbol whose range holds the offset
            funcs = None
            for off_, (si, typ, _) in o.rel(i_).items():
                nm = o.syms[si][0]
                if typ not in (21, 20) or nm not in trapped:
                    continue
                f_ = fn
                if f_ is None or caller_class(f_) is None:
                    if funcs is None:
                        funcs = sorted((s_[1], s_[2], s_[0]) for s_ in o.syms
                                       if s_[5] == i_ and s_[3] == 2 and s_[0])
                    f_ = next((n_ for v_, z_, n_ in funcs if v_ <= off_ < v_ + max(z_, 1)), None)
                callers[nm].add(caller_class(f_) if f_ else None)
        for un, cls in callers.items():
            if None in cls:
                continue
            got = set()
            for c in cls:
                got |= by_vt.get((un, c), set())
            if len(got) != 1:
                continue
            tgt, a = next(iter(got))
            changed.setdefault(u, {})[un] = tgt
            addr_of[(u.source, un)] = a
            rebound.add((u.source, un))
            n += 1
    return n


def bodies_slot_renames(have, changed, addr_of, per_obj, rebound, undefined):
    """W28: for each synthesized body object (`<host>#bodies<n>`) the names it leaves undefined that the slot fill bound in
    the host's objects (`changed`: {unit: {symbol: target}}, addr_of {(source, symbol): exe address}) get the same
    target. Adds to changed / addr_of / rebound; returns the number of names. undefined(unit) -> its undefined names."""
    by_host = collections.defaultdict(dict)   # host source -> {symbol: (target, exe address)}
    for u, m in changed.items():
        for un_, t_ in m.items():
            by_host[u.host or u.source.split('#')[0]].setdefault(un_, (t_, addr_of[(u.source, un_)]))
    n = 0
    for u in have:
        if '#bodies' not in u.source:
            continue
        got_ = by_host.get(u.host or '')
        if not got_:
            continue
        ren_ = per_obj.get(u.source) or {}
        for name in undefined(u):
            if name in got_ and name not in ren_:
                changed.setdefault(u, {})[name] = got_[name][0]
                addr_of[(u.source, name)] = got_[name][1]
                rebound.add((u.source, name))
                n += 1
    return n


def exe_slot_fill(exev, exe_cls, vmap, by_cls, rtti, have, per_obj, traps, native, itanium, msvc, canon_it, out,
                  stubs=None, overrides=None, exe_path=None, providers=None, also=None, unbound=None, hints=None,
                  call_hints=None, lay_of=None, probe_req=None):
    """S2c-vt slot fill (docs/LINK.md "Vftables from the player's exe"): a slot of a native vtable that calls a trap
    stub (its virtual function is defined nowhere) gets the native definition at the address the exe's vftable
    holds in that slot, `__fnv_<addr>`, when the address map has one. The native vtable (group z, address point p)
    stands for the exe vftables the address map pairs with it (vmap) and those of its own RTTI class at that offset;
    GCC slot k is exe slot exevft.exe_slot_index(k, D1's index). One undefined symbol is renamed once per object, so
    every vtable of the object naming it must agree on the address (else the declaring class's vtable decides, else it
    stays a trap). Every argument the slot's declaration passes and the definition reads must arrive where the
    definition reads it (slot_abi; a free definition takes the object as its first pointer parameter). A definition
    reading more arguments than the slot declares is bound too: callers reach a slot through their own class's
    declaration (the decomp's shells declare placeholders, `virtual void v2();`), and slot_fill.tsv lists them.
    Rewrites the objects' .redefine files and refs.tsv rows it changes, drops trap stubs no object uses any more, and
    writes slot_fill.tsv (one row per (object, symbol)) and slot_gaps.tsv (trap slots left, with the reason).
    also: {(group, address point): {exe vftable}} the native vtable stands for besides vmap (ctor_store_pairs).
    unbound: {source: names} the unit leaves undefined without a trap (extern-text addresses nothing defines,
    imports no symbol answers): when such a name is a slot of a vtable of the object whose exe vftable holds a
    function at that slot (a `_purecall` entry is S4's provider), it is bound like a trap slot (BaseFormComponent's
    four pure virtuals, whose units.json addresses lie inside other functions). Not in any vtable: left as it was.
    hints: {source: {name: address}} (S2d-vslot) vtable-slot names that resolve bound by another unit's reference to
    the same name (gref / gdef tier) and that no code of the object calls: the exe vftable's function at the slot
    replaces the address when the two differ and the fill can bind it (`hint_rebound`); where the exe has none, the
    vtables of the object disagree or the exe's function cannot be bound, the name keeps its address (`hint_kept_*`).
    The hint rule also holds for a name the unit's own recipe binds (refs.tsv tier unit/*) that the object never calls
    (W20 (b): the name is only a slot).
    A hint symbol serves the vtables of a class and of the classes derived from it (the decomp does not declare their
    overrides), and ends at one address: each vtable whose exe vftables hold another single function in that slot gets
    its own word in the copy (`hint_slot_over`; W32), whether the symbol was rebound, agreed or kept.
    call_hints: {source: {name: address}} (W20 (a)) vtable-slot names the unit's recipe binds (tier unit/*) that the
    object also calls directly: the address is right for those calls and the symbol keeps it. Each vtable slot that
    names the symbol where the exe vftables of that use agree on another function gets that function as a per-slot
    override (`overrides`, the strong copy exe_vtable_copies writes), when target_for binds it with the ABI check
    (`chint_slot_over`); a use whose exe vftables disagree, past the exe's slots or with a function that cannot be
    bound keeps the symbol (`chint_kept_*`).
    Returns the counts."""
    st = collections.Counter()
    zmap = collections.defaultdict(set)   # (native vtable group, address point) -> exe vftable addresses
    for v, e in vmap.items():
        if v in exev:
            zmap[(e[0], e[1])].add(v)
    for k_, vs_ in (also or {}).items():
        zmap[k_] |= {v for v in vs_ if v in exev}
    own = collections.defaultdict(set)    # the same from the exe's RTTI: the class's own vftable at that offset
    for v, (cls, _, off, _) in exe_cls.items():
        for z in by_cls.get(cls, []):
            for p, ott in vtables.address_points(rtti.ents(z) or []):
                if ott == -off:
                    own[(z, p)].add(v)
    for k_, vs_ in own.items():   # a vtable the units also pair with another vftable: its own class's decides
        zmap[k_] = set(vs_)
    trap_set = set(traps)
    ott_cache = {}

    def ott_of(z, p):
        if z not in ott_cache:
            ott_cache[z] = dict(vtables.address_points(rtti.ents(z) or []))
        return ott_cache[z].get(p, 0)
    prim_by_td = {}
    for v_, e_ in exev.items():
        if e_.td and e_.off == 0 and v_ in exe_cls:
            prim_by_td.setdefault(e_.td, exe_cls[v_][0])

    def base_dtor_pos(vs, n):
        """Position of GCC's D1/D0 pair in the native vtable of the RTTI primary base of the exe vftables vs, when
        the native vtable at hand has exactly one slot more than each of them; else None."""
        got = set()
        for v_ in vs:
            e_ = exev[v_]
            if not e_.td or len(e_.bases) < 2 or n != len(e_.slots) + 1:
                return None
            bcls = prim_by_td.get(e_.bases[1][0]) if e_.bases[1][1] == 0 else None
            zb = sorted(by_cls.get(bcls, [])) if bcls else []
            if not zb:
                return None
            ents_ = rtti.ents(zb[0]) or []
            pts_ = vtables.address_points(ents_)
            if not pts_:
                return None
            nb = vtables.slots_at(ents_, pts_[0][0])
            sb = ents_[pts_[0][0] // 4:pts_[0][0] // 4 + nb]
            got.add(exevft.dtor_pos([x.sym for x in sb], [x.word for x in sb]))
        return got.pop() if len(got) == 1 else None
    rows, gaps, changed = [], [], {}
    rebound = set()   # (source, symbol) of the hints the exe vftable overrode
    addr_of = {}   # (source, symbol) -> the exe address it was bound to (refs.tsv)
    stubs = {} if stubs is None else stubs   # flag / deleting-destructor stubs -> their asm (bridges.S)
    itanium = slotfill2.enum_params(itanium, slotfill2.msvc_enums(out), st)   # S2c-vt2

    def target_for(un, a, otts=None, depth=0, var=None):
        nd = native.get(a)
        isig = itanium(un)
        if var:   # a placeholder slot at the base's destructor pair: bound as that variant (base_dtor_pos)
            isig = copy.copy(isig)
            isig.variant = var
        th = exevft.thunk(exe_path, a) if nd is None and exe_path and depth < 2 and a not in (providers or {}) else None
        if th is not None:
            # an MSVC thunk no unit compiles: its target's binding, with `this -= n` when the native vtable's
            # offset-to-top says the same adjustment (otts: those of the vtables naming the slot)
            n, t = th
            if n and (not otts or any(-o != n for o in otts)):
                return None, 'thunk: adjustment %d, the native vtable\'s offsets %s' % (n, sorted(set(otts or [])))
            inner, why_ = target_for(un, t, None, depth + 1, var)
            if inner is None or not n:
                return inner, 'thunk -> ' + why_
            name = ADJ_SYM % a
            stubs.setdefault(name, emit_adj_stub(name, inner, n))
            return name, 'adjustor thunk (%d) -> %s' % (n, why_)
        if nd is None and a in HK_SDD:   # W22: the Havok sized-free ??_G's stand-in takes (this, flags)
            if isig.variant and isig.variant.startswith('D'):
                fl = 1 if isig.variant == 'D0' else 0
                name = FLAG_SYM % (a, fl)
                stubs.setdefault(name, emit_flag_stub(name, ADDR_SYM % a, fl))
                return name, 'flags stub (%d): Havok sized-free ??_G' % fl
            if not isig.variant:
                return ADDR_SYM % a, 'Havok sized-free ??_G'
        if nd is None and a in (providers or {}) and not isig.variant:
            return providers[a][0], 'provider (%s)' % providers[a][1]   # S4's CRT entry: _purecall's slots
        if nd is None:
            c = exevft.leaf_constant(exe_path, a) if exe_path and not isig.variant else None
            if c is not None:   # a trivial x86 leaf no unit compiles: its exact native form
                name = CONST_SYM % a
                stubs.setdefault(name, emit_const_stub(name, c))
                return name, 'leaf constant (%s)' % c
            return None, 'not compiled'
        dsym = nd['sym'].name
        dvar = sigs.itanium_variant(dsym)
        ds = msvc(nd['msvc']) if nd.get('msvc') else None
        if isig.variant and isig.variant.startswith('D') and dvar is None and (
                flags_param(ds) or nd.get('tier') == 'tag-flags'):   # W48: tag_flags_defs checked the shape
            # GCC's D1/D0 where the decomp defines the exe's ??_G as a plain function taking the flags
            # (`FUN_00c2a090(unsigned int)`): a stub passes them, 1 for D0 (delete), 0 for D1/D2
            fl = 1 if isig.variant == 'D0' else 0
            name = FLAG_SYM % (a, fl)
            stubs.setdefault(name, emit_flag_stub(name, ADDR_SYM % a, fl))
            return name, 'flags stub (%d)' % fl
        if isig.variant is None and dvar == 'D0' and nd['variants'].get('D1') is not None:
            # a placeholder slot (`virtual void v00();`) where the exe has the deleting destructor: callers pass the
            # MSVC flags; bit 0 picks D0 (delete) or D1, as the kind-G adapter does
            name = GDEL_SYM % a
            d1 = ADDR_SYM % a + ('' if nd['variants']['D1'] is nd['sym'] else '_D1')
            stubs.setdefault(name, emit_gdel_stub(name, ADDR_SYM % a, d1))
            return name, 'deleting-destructor stub'
        if isig.variant or dvar:
            if not isig.variant or not dvar or not dvar.startswith('D') or not isig.variant.startswith('D'):
                return None, 'kind differs (%s at the exe slot, %s in the vtable)' % (dvar or 'function',
                                                                                   isig.variant or 'function')
            vs = nd['variants'].get(isig.variant)
            if vs is None:
                return None, 'no %s at the address' % isig.variant
            return ADDR_SYM % a + ('' if vs is nd['sym'] else '_' + isig.variant), 'dtor'
        fr = free_slot(a, nd, stubs, st, exe_path) if not isig.variant else None
        if fr is not None:
            return fr, 'free function: %s' % fr
        if flags0_slot(isig, nd, ds, exe_path, a):
            name = FLAG_SYM % (a, 0)
            stubs.setdefault(name, emit_flag_stub(name, ADDR_SYM % a, 0))
            st['flags0-placeholder-slots'] += 1
            return name, 'flags stub (0): deleting destructor in a placeholder slot'
        if ds is None or ds.kind != 'func' or ds.raw_params is None or isig.raw_params is None:
            return None, 'no declaration to compare'
        how = slot_abi(isig, ds)
        if how not in ('same', 'the slot passes more') and not how.startswith('the definition reads more'):
            # the x86 model: the slot is a thiscall member with the slot's parameters; a decomp stand-in that MSVC
            # reached the same way (`FUN_00a89af0(int)` for a LinkObject(NiStream &) slot: the int is the stack
            # argument) gets a bridge with the register moves
            ss = slot_msvc_sig(isig, ds)
            dps = [p_ for p_ in ds.raw_params if p_ != 'void']
            if len(ss.raw_params) < len(dps):
                # a placeholder slot (`virtual void v33();`): its callers pass the definition's arguments through
                # their own declarations, as the x86 code reads them (a free cdecl `FUN_00a89af0(int)` reads the
                # first stack argument, which a thiscall caller passes after this)
                ss.raw_params = ss.raw_params + dps[len(ss.raw_params):]
                ss.params = list(ss.raw_params)
            v = abi.compare(ss, ds)
            if v.kind == 'same':   # the link's ABI model passes them alike (as for every other reference)
                return ADDR_SYM % a, 'abi model: same (%s)' % ','.join(v.classes or ['-'])
            # as resolve does for every reference: a pop mismatch alone (the decomp's cdecl / stdcall stand-in for
            # MSVC's thiscall code: the callee pops what the caller pushed) still reads the arguments both declare
            pop_only = v.kind == 'x86-inconsistent' and 'pop-mismatch' in v.classes and not {
                'this-not-passed', 'size-differs', 'cast-conflict', 'result-pointer-not-passed', 'ecx-not-passed',
                'aggregate-param'} & set(v.classes)
            if (v.kind == 'bridge' or pop_only) and v.moves and all(
                    d_[0] in ('x', 'v') and s_[0] in ('x', 'v') for d_, s_, _, _ in v.moves):
                bn = abi.bridge_name(a, 'vslot ' + un)
                stubs.setdefault(bn, (ADDR_SYM % a, v, 'vtable slot %s' % un, nd['msvc']))
                return bn, 'bridge (%s)' % ','.join(v.classes or ['moves'])
            why2 = []
            if lay_of is not None and nd['unit'] is not None:
                # aggregates by value: decide on bytes (abi2), as for code references. target_for does not know the
                # unit that compiles the vtable, so both sides use the definition's unit nd['unit']. The slot as
                # declared first; the placeholder padding above (the definition's extra arguments) only after that
                s0 = slot_msvc_sig(isig, ds)
                cands = [s0] + ([ss] if ss.raw_params != s0.raw_params else [])
                for sg in cands + [ds]:
                    for c in [c for _, c in abi.params_of(sg)] + [abi.ret_class(sg)]:
                        pn_ = abi2.probe_name(c)
                        if pn_ and '{anon}' not in c and probe_req is not None:
                            probe_req.add((nd['unit'], pn_))
                for sg in cands:
                    w_ = []
                    p2 = abi2.compare(sg, ds, lay_of(nd['unit']), lay_of(nd['unit']), why=w_)
                    if p2 is not None and p2.kind == 'same':
                        return ADDR_SYM % a, 'abi2: same'
                    if p2 is not None and p2.kind == 'bridge':
                        bn = abi.bridge_name(a, 'vslot ' + un)
                        code = abi2.emit(bn, ADDR_SYM % a, p2, 'vtable slot %s | def: %s' % (un, nd['msvc']))
                        if code is not None:
                            stubs.setdefault(bn, code)
                            st['vslot-abi2-bridge'] += 1
                            return bn, 'abi2 bridge (%s)' % ','.join(p2.classes or ['moves'])
                        w_.append('bridge frame over 4 KiB')
                    elif p2 is not None:
                        w_.append(p2.kind + (' (' + ','.join(p2.classes + p2.notes) + ')' if p2.classes or p2.notes
                                             else ''))
                    why2.extend(w for w in w_ if w not in why2)
            return None, 'abi: ' + how + ('; abi2: ' + '; '.join(why2) if why2 else '')
        return ADDR_SYM % a, how

    target_for = slotfill2.secondary_entry(target_for, native, ADDR_SYM, MSVC_ENTRY_SYM, st)   # S2c-vt2
    target_for = slotfill2.fastcall_dtor(target_for, native, itanium, msvc, abi.classify, stubs, ADDR_SYM, st)
    target_for = slotfill2.slot_ctor(target_for, native, itanium, msvc, abi.classify, slot_abi, stubs, ADDR_SYM, st)

    for u in have:
        ren = per_obj.get(u.source) or {}
        tr = {un for un, t in ren.items() if t in trap_set}
        xt = {un for un in (unbound or {}).get(u.source, ()) if un not in ren and un.startswith('_ZN')}
        hs = (hints or {}).get(u.source, {})
        hn = {un for un in hs if str(ren.get(un, '')).startswith('__fnv_')} - tr
        cs = (call_hints or {}).get(u.source, {}) if overrides is not None else {}
        cn = {un for un in cs if str(ren.get(un, '')).startswith('__fnv_')} - tr - hn
        if not tr and not xt and not hn and not cn:
            continue
        o = vtables.Obj(u.obj)
        uses = collections.defaultdict(list)   # un -> [(z, p, k, j, {exe addresses})]
        for z in sorted(n for n in o.defs if n.startswith('_ZTV')):
            ents = o.ents(z)
            for p, _ in vtables.address_points(ents):
                n = vtables.slots_at(ents, p)
                se = ents[p // 4:p // 4 + n]
                names = [e.sym for e in se]
                d = exevft.dtor_pos(names, [e.word for e in se])
                vs = zmap.get((z, p), set())
                pd = None
                if d is None and vs:
                    # a shell declaring placeholders where its RTTI base has a virtual destructor (FUN_00aa8410 v0-v2
                    # for OEI::IO::IO_Thread : BSThread): one slot more than the exe, so callers reach it through
                    # the base's layout, GCC's D1/D0 pair at the base's position
                    pd = base_dtor_pos(vs, len(se))
                    d = pd
                for k, e in enumerate(se):
                    if e.sym in tr or e.sym in xt or e.sym in hn or e.sym in cn:
                        j = exevft.exe_slot_index(k, d)
                        pv = ('D1' if k == pd else 'D0' if k == pd + 1 else None) if pd is not None else None
                        uses[e.sym].append((z, p, k, j, {exev[v].slots[j] for v in vs if j < len(exev[v].slots)},
                                            bool(vs), pv))
        for un in sorted(tr | {x for x in xt | hn | cn if x in uses}):
            us = uses.get(un, [])
            if un in cn:
                # W20 (a): the symbol stays at the address the unit's recipe gave it; each use whose exe vftables
                # agree on another function gets that function in its own word
                st['chint_symbols'] += 1
                for x in us:
                    b_ = next(iter(x[4])) if len(x[4]) == 1 else None
                    if b_ is None:
                        st['chint_kept_' + ('vtables_disagree' if x[4] else 'no_exe_function')] += 1
                        continue
                    if b_ == cs[un]:
                        st['chint_agrees'] += 1
                        continue
                    t2, why2 = target_for(un, b_, [ott_of(x[0], x[1])], 0, x[6])
                    if not t2:
                        st['chint_kept_exe_function_not_bindable'] += 1
                        continue
                    overrides[(u.obj, x[0], x[1] // 4 + x[2])] = t2
                    st['chint_slot_over'] += 1
                    rows.append((os.path.basename(u.obj), u.source, un, '%08x' % b_, t2,
                                 'per slot (exe vftable; the name stays at %08x, its code calls it)' % cs[un], why2,
                                 '%s+%d:%d' % (x[0], x[1], x[2])))
                continue
            st['symbols'] += 1
            addrs = set().union(*(x[4] for x in us)) if us else set()
            how = None
            if not us:
                why = 'in no vtable of the object'
            elif not addrs:
                why = 'no exe vftable' if not any(x[5] for x in us) else 'past the exe vftable\'s slots'
            elif len(addrs) == 1:
                how = 'agree'
            else:
                isig = itanium(un)
                own = '::'.join(isig.qual[:-1]) if isig.qual and len(isig.qual) > 1 else None
                mine = set().union(*(x[4] for x in us if own and canon_it(x[0]) == own))
                if len(mine) == 1:
                    addrs, how = mine, 'declaring class'
                else:
                    why = 'vtables disagree (%s)' % ' '.join('%08x' % a for a in sorted(addrs))
            pvs = {x[6] for x in us}
            uvar = pvs.pop() if len(pvs) == 1 else None
            if un in hn:
                st['hint_symbols'] += 1
                a = next(iter(addrs)) if how else None
                final = hs[un]   # the address the symbol ends at: the exe's when it is rebound, else the name's own
                if a is None:
                    st['hint_kept_' + why.split(' (')[0].replace(' ', '_').replace('\'', '')] += 1
                elif a == hs[un]:
                    st['hint_agrees'] += 1
                else:
                    tgt, why = target_for(un, a, [ott_of(x[0], x[1]) for x in us if a in x[4]], 0, uvar)
                    if not tgt:
                        st['hint_kept_exe_function_not_bindable'] += 1
                    else:
                        changed.setdefault(u, {})[un] = tgt
                        addr_of[(u.source, un)] = a
                        rebound.add((u.source, un))
                        final = a
                        st['hint_rebound'] += 1
                        rows.append((os.path.basename(u.obj), u.source, un, '%08x' % a, tgt, 'exe vftable over the '
                                     'name\'s address %08x' % hs[un], why, ' '.join('%s+%d:%d' % (x[0], x[1], x[2])
                                                                                  for x in us)))
                for x in us if overrides is not None else ():
                    # W32: one symbol serves the vtables of a class and of the classes derived from it, whose
                    # overrides the decomp does not declare (NiTexture::Clone is NiObject's name in the shell's
                    # vtable); the symbol ends at one address, a vtable whose exe vftables hold another function in
                    # that slot gets its own word in the copy, as for a chint use or a trap-filled symbol
                    b_ = next(iter(x[4])) if len(x[4]) == 1 else None
                    if b_ is None or b_ == final:
                        continue
                    t2, why2 = target_for(un, b_, [ott_of(x[0], x[1])], 0, x[6])
                    if not t2:
                        st['hint_slot_kept_exe_function_not_bindable'] += 1
                        continue
                    overrides[(u.obj, x[0], x[1] // 4 + x[2])] = t2
                    st['hint_slot_over'] += 1
                    rows.append((os.path.basename(u.obj), u.source, un, '%08x' % b_, t2,
                                 'per slot (exe vftable; the name ends at %08x)' % final, why2,
                                 '%s+%d:%d' % (x[0], x[1], x[2])))
                continue
            if how:
                a = next(iter(addrs))
                tgt, why = target_for(un, a, [ott_of(x[0], x[1]) for x in us if a in x[4]], 0, uvar)
                if tgt:
                    changed.setdefault(u, {})[un] = tgt
                    addr_of[(u.source, un)] = a
                    st['bound_symbols'] += 1
                    st['bound_' + how.replace(' ', '_')] += 1
                    rows.append((os.path.basename(u.obj), u.source, un, '%08x' % a, tgt, how, why,
                                 ' '.join('%s+%d:%d' % (x[0], x[1], x[2]) for x in us)))
                    for x in us if overrides is not None else ():
                        # another vtable of the object holds another address there (a derived class's override
                        # the decomp does not declare): that slot gets its own word in the copy
                        b_ = next(iter(x[4])) if len(x[4]) == 1 else None
                        t2 = target_for(un, b_, [ott_of(x[0], x[1])], 0, x[6])[0] if b_ is not None and b_ != a else None
                        if t2:
                            overrides[(u.obj, x[0], x[1] // 4 + x[2])] = t2
                            st['per_slot_uses'] += 1
                            rows.append((os.path.basename(u.obj), u.source, un, '%08x' % b_, t2,
                                         'per slot (another address than the declaring class\'s)', '',
                                         '%s+%d:%d' % (x[0], x[1], x[2])))
                    continue
            else:
                a = None
            if why.startswith('vtables disagree') and overrides is not None:
                # one target per slot: exe_vtable_copies writes a strong copy of the kept vtable with these words
                left = []
                for x in us:
                    tgt_ = target_for(un, next(iter(x[4])), [ott_of(x[0], x[1])], 0, x[6])[0] if len(x[4]) == 1 else None
                    if tgt_:
                        overrides[(u.obj, x[0], x[1] // 4 + x[2])] = tgt_
                        st['per_slot_uses'] += 1
                        rows.append((os.path.basename(u.obj), u.source, un, '%08x' % next(iter(x[4])), tgt_,
                                     'per slot (vtables disagree)', '', '%s+%d:%d' % (x[0], x[1], x[2])))
                    else:
                        left.append(x)
                us = left
                if not us:
                    continue
            if un in xt and un not in tr:   # not a trap slot: it was undefined before, it stays so (D23's list)
                continue
            st['left_' + why.split(' (')[0].replace(' ', '_').replace(':', '').replace('\'', '')] += 1
            for x in us or [(None, 0, -1, -1, set(), False, None)]:
                gaps.append((os.path.basename(u.obj), u.source, un, x[0] or '', str(x[2]), str(x[3]),
                             ' '.join('%08x' % b for b in sorted(x[4])), why))
    # W28 (top-down 38, 42-43, 49-50, 98-99, 107): a synthesized body object of the host (`<host>#bodies<n>`: the
    # `_ZThn` adjustor thunks bodies.py emits) calls the slot function by name, and no vtable of that object names it, so
    # the fill above never saw it: the name stayed undefined (a trap) while the host's vtables got the exe's function.
    # The thunk adjusts `this` and calls the same function the slot holds: it takes the host's binding.
    st['bodies_slot_bound'] += bodies_slot_renames(have, changed, addr_of, per_obj, rebound,
                                                   lambda u: [x.name for x in elfsyms.symbols(u.obj) if x.shndx == 0])
    st['direct_call_slot_bound'] += direct_call_slot_binds(have, changed, addr_of, per_obj, rebound, traps, rows)
    # apply the renames: the .redefine files and refs.tsv rows of the objects that changed
    bound_refs = bound_refs_x = 0
    by_src = {u.source: m for u, m in changed.items()}
    for u, m in changed.items():
        per_obj[u.source].update(m)
        red = os.path.join(out, 'objcopy', os.path.basename(u.obj) + '.redefine')
        with open(red, 'w', encoding='utf-8', errors='surrogateescape') as f:
            for old, new in sorted(per_obj[u.source].items()):
                f.write('%s %s\n' % (old, new))
    if by_src:
        bound_refs, bound_refs_x = rewrite_refs(os.path.join(out, 'refs.tsv'), by_src, addr_of, rebound)
    used = {t for m in per_obj.values() for t in m.values()}
    for t in [t for t in traps if t not in used]:
        del traps[t]
        st['trap_stubs_dropped'] += 1
    st['bound_refs'] = bound_refs - bound_refs_x
    st['bound_refs_extern_text'] = bound_refs_x
    with open(os.path.join(out, 'slot_fill.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#object\tunit\tslot symbol\texe address\ttarget\tchosen by\tabi\tuses (vtable+address point:slot)\n')
        for r in rows:
            f.write('\t'.join(r) + '\n')
    with open(os.path.join(out, 'slot_gaps.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#object\tunit\tslot symbol\tvtable\tslot\texe slot\texe addresses\twhy still a trap\n')
        for r in gaps:
            f.write('\t'.join(r) + '\n')
    return st


VTEXT_OBJ = 'zz_bridges.o'   # the extended vtables live in bridges.S (apply assembles it into this object)
XTRAP = 'exe 0x%08x'


VADJ_SYM = '__fnvnx_vadj_%08x'   # W19: a secondary vftable's adjustor-thunk slot, the same this-adjustment natively
VADJD_SYM = '__fnvnx_vadjd_%08x_%d'   # ... for a destructor slot (0: D1, 1: D0)


def vmo_adjustor(a, off, native, exe_path, plain=True):
    """(n, f) when the exe code at a is an MSVC adjustor thunk (exevft.thunk, through at most two more plain or
    adjusting thunks) that subtracts exactly `off`, the COL offset of the secondary vftable it sits in, and lands on
    an address f the native build defines: the thunk's x86 immediate then is the native offset-to-top the copy
    carries, so the stub's adjustment agrees with what a GCC `_ZThn` thunk in that slot would do. A different
    immediate (virtual bases, a deeper chain) or an undefined target: None, and the slot is a trap. plain: the
    target must be no Itanium constructor / destructor variant (a destructor slot passes plain=False)."""
    n, t = 0, a
    for _ in range(3):
        th = exevft.thunk(exe_path, t)
        if th is None:
            return None
        n, t = n + th[0], th[1]
        if t in native or t in HK_SDD:
            if plain and t in native and sigs.itanium_variant(native[t]['sym'].name) is not None:
                return None   # a constructor / destructor variant: the slot is no plain member function
            return (n, t) if n and n == off else None
    return None


COM_TABLES = set()   # W52: the vtables VTFILL writes in COM order (exe_slot_target: no drop bridge there)


def exe_slot_target(a, native, providers, exe_path, bridges, traps, st, where, off=None):
    """The native word for exe vftable slot address a in a copy made from the exe (exe_vtable_copies' extra slots,
    minus_one_copies' slots): `__fnv_<a>` where the address map has a plain native definition, the
    deleting-destructor stub for a `??_G`, S4's provider, the target of a plain `jmp f` thunk, the exact form of a
    constant leaf, else a trap stub reporting the exe address (traps[t] = (text, where)). Counts go to st.
    off: the COL offset of the exe vftable the slot sits in (a secondary vftable's copy): an MSVC adjustor thunk
    (`sub ecx, n; jmp f`, vmo_adjustor) whose n is exactly that offset gets a stub doing the same this-adjustment
    in front of f's native entry; any other adjustor thunk stays a trap (never a guessed adjustment)."""
    nd = native.get(a)
    var = sigs.itanium_variant(nd['sym'].name) if nd else None
    com = bool(where) and where[0] in COM_TABLES
    if nd is not None and var is None and free_slot(a, nd, bridges, st, exe_path, com):
        t = FREE_SYM % a   # a decomp free function (S5 class 1): this dropped / edx inserted
    elif nd is not None and var is None:
        t = ADDR_SYM % a
        st['slots-native'] += 1
    elif nd is not None and var == 'D0' and nd['variants'].get('D1') is not None:
        t = GDEL_SYM % a
        bridges.setdefault(t, emit_gdel_stub(t, ADDR_SYM % a, ADDR_SYM % a + (
            '' if nd['variants']['D1'] is nd['sym'] else '_D1')))
        st['slots-deleting-destructor'] += 1
    elif nd is None and a in HK_SDD:   # W22: a Havok sized-free ??_G in a later slot: (this, flags), as MSVC calls it
        t = ADDR_SYM % a
        st['slots-hk-sdd'] += 1
    elif nd is None and a in (providers or {}):
        t = providers[a][0]
        st['slots-provider'] += 1
    elif nd is None and exe_path and (exevft.thunk(exe_path, a) or (1, 0))[0] == 0 and \
            native.get(exevft.thunk(exe_path, a)[1]) is not None and \
            sigs.itanium_variant(native[exevft.thunk(exe_path, a)[1]]['sym'].name) is None:
        t = ADDR_SYM % exevft.thunk(exe_path, a)[1]   # a primary vtable: only `jmp f` (no adjustment)
        st['slots-jump-thunk'] += 1
    elif nd is None and off and exe_path and vmo_adjustor(a, off, native, exe_path) is not None:
        n_, f_ = vmo_adjustor(a, off, native, exe_path)
        t = VADJ_SYM % a
        bridges.setdefault(t, emit_adj_stub(t, adj_target(native, f_), n_))
        st['slots-adjustor-thunk'] += 1
    elif nd is None and exe_path and exevft.leaf_constant(exe_path, a) is not None:
        t = CONST_SYM % a
        bridges.setdefault(t, emit_const_stub(t, exevft.leaf_constant(exe_path, a)))
        st['slots-leaf-constant'] += 1
    else:
        t = TRAP_SYM % int(hashlib.sha1((XTRAP % a).encode()).hexdigest()[:8], 16)
        traps.setdefault(t, (XTRAP % a + (' (not compiled)' if nd is None else ' (%s)' % var), where))
        st['slots-trap'] += 1
    return t


def d0_slot_fix(z, n, d, names, slots, native, msvc, bridges, traps, st, exe_path):
    """D26 and `delete`: the D0 word for native slot 1 of a vtable that stands for an exe vftable in the exe's slot
    order, else None. The native table has no destructor pair (d is None: the decomp declares the class with a
    placeholder where the exe has its scalar deleting destructor, `virtual void BaseVirtual();`, or no virtual
    destructor at all), the exe's slot 0 is a deleting destructor (vmo_dtor_slots) and exe slot 1 is
    `DeleteThis() { delete this; }` (exevft.delete_this). A native `delete p` through any declaration with a virtual
    destructor (GraphicsRenderer::Create's failure path; NiRefObject::DeleteThis itself) calls Itanium slot 1; there
    the table held DeleteThis, which does `delete this` through slot 1 again, forever (S5 round 6). Slot 1 becomes the
    exe slot 0's D0 (flags 1 where the definition takes flags), which is what DeleteThis does in the exe. Nothing
    else moves, so every caller that numbers the slots as the exe does stays right. names: the group's slot symbols
    from word 2; slots: the exe vftable's slots."""
    if d is not None or not exe_path or len(slots) < 2 or os.environ.get('FNVNX_D0SLOT', '1') == '0':
        return None
    if not exevft.delete_this(exe_path, slots[1]):
        return None
    if n >= 2 and exevft.slot_one_is_d0(names[0], names[1]):
        st['d0slot-already'] += 1
        return None
    pair = vmo_dtor_slots(slots[0], native, msvc, bridges, traps, st, (z, 3), exe_path)
    if pair is None:
        st['d0slot-slot0-not-a-destructor'] += 1
        return None
    if pair[1].startswith(TRAP_SYM.split('%')[0]):
        st['d0slot-no-d0'] += 1
        return None
    return pair[1]


# W30 (S5 boot, link 3f7a6c35): the evidence that a table without a D1/D0 pair is nevertheless a minus-one table
# (D26 layout: D1, D0, then exe slot k at native k + 1). S1's D26 (b) call-only placeholder: a first virtual named like
# these (the same pattern as tools/rules/s1_compile.py D22_CALLONLY_PLACEHOLDER) becomes `virtual ~C()`, unless a
# unit calls it by name (D22-USED) or defines it (D22-DEFINED): then the callers number the slots as the exe does.
D26B_PLACEHOLDER = re.compile(
    r"^(?:v|vf|vfn|vt|vfunc|virt|virtual|slot|func|fn|unk|unknown|Unk_?|Unknown|Func|Virtual|Reserved|reserved|pad|Pad|"
    r"Slot|V|F)_?0+$|^\w*?Unknown_?0*$|^\w*_v0$|^v0_\w*$|^vfunc0\w*$|^Unk(?:nown)?0+\w*$", re.IGNORECASE)
ITANIUM_MEMBER = re.compile(r'^_ZN(\d+)')


def itanium_member(sym):
    """The unqualified member name of a plain `_ZN[K]<len><Class>...<len><member>...` symbol (its last name component of
    the nested name, template arguments skipped), else None."""
    m = re.match(r'_ZNK?', sym or '')
    if not m:
        return None
    i, last, depth = m.end(), None, 0
    while i < len(sym):
        c = sym[i]
        if c.isdigit():
            j = i
            while j < len(sym) and sym[j].isdigit():
                j += 1
            n = int(sym[i:j])
            if j + n > len(sym):
                return last
            if depth == 0:
                last = sym[j:j + n]
            i = j + n
        elif c == 'I':
            depth += 1
            i += 1
        elif c == 'E':
            if depth == 0:
                break
            depth -= 1
            i += 1
        elif depth == 0:
            break   # the parameter encoding (or a ctor / dtor code) after the nested name
        else:
            i += 1
    return last


def load_d22_list(top):
    """Explicit minus-one evidence: the exe vftable addresses of the classes in S1's D22 list (d22.json, format
    fnvnx-d22/1, under `top` = FNVNX_RECON, tools/farm/recon.sh). Prepare's D22 rule converts exactly those classes to
    `virtual ~C()` (the compiled callers are minus-one). Returns ({vftable address}, number of files); empty with no
    recon. Names and addresses only."""
    got, n = set(), 0
    if not top or not os.path.isdir(top):
        return got, 0
    for dp, _, fs in os.walk(top):
        if 'd22.json' not in fs:
            continue
        try:
            d = json.load(open(os.path.join(dp, 'd22.json')))
        except (OSError, ValueError):
            continue
        if d.get('format') != 'fnvnx-d22/1':
            continue
        n += 1
        for c in d.get('classes', ()):
            got.update(int(v, 16) for v in c.get('vftables', ()))
    return got, n


def load_d22_applied(tree):
    """The exe vftables of the classes prepare's D22 actually converted: build/d22_applied.tsv of the prepared tree
    (S1-rules' POST hook; columns file, class, member, kind, vftables). Rows of kind `listed` carry the vftables; a
    call-only row names none. Empty without the file."""
    got = set()
    p = os.path.join(tree or '', 'build', 'd22_applied.tsv')
    if not tree or not os.path.exists(p):
        return got
    for line in open(p, encoding='utf-8', errors='surrogateescape'):
        f = line.rstrip('\n').split('\t')
        if len(f) < 5 or line.startswith('#') or f[0] == 'file':
            continue
        for w in re.split(r'[ ,]+', f[4].strip()):
            if re.fullmatch(r'(?:0x)?[0-9a-fA-F]{6,8}', w):
                got.add(int(w, 16))
    return got


# the reasons a pair-less table stays in the exe's slot order (out/exe_order_classes.tsv); every other reason is minus-one
EXE_ORDER_REASONS = ('com', 'd22-used', 'defined-slot0', 'exe-order-no-evidence', 'd26-slot0-not-a-destructor',
                     'multi-vtable')
COM_SLOT = re.compile(r'(?:QueryInterface|AddRef|Release)')
COM_CLASS = re.compile(r'^_ZTV(?:\d+)?I(?:Unknown|D3D\w*|Direct\w*|DXGI\w*|XAudio\w*|MM\w*|Dispatch)')


def is_com(z, raw_names):
    """A COM / IUnknown interface: an `IUnknown`-family class name, or QueryInterface / AddRef / Release among the first
    three slots (their callers number the slots as the exe does: slot 0 is not a destructor)."""
    if COM_CLASS.match(z or ''):
        return True
    for nm in (raw_names or ())[:3]:
        mem = itanium_member(nm)
        if mem and COM_SLOT.fullmatch(mem):
            return True
    return False


def d26_classify(z, v, raw_names, d, listed, called, defined=(), slot0_deldtor=False):
    """Why the table z (exe vftable v, slot symbols raw_names, destructor pair position d) is minus-one or stays in the
    exe's order, though it holds no D1/D0 pair, as (reason). D26 (docs/ARCHITECTURE.md): natively every polymorphic object
    is dispatched minus-one; D26 (b) gives every call-only declaration's slot-0 placeholder a `virtual ~X()`, so its
    callers number the slots minus-one whichever vftable they reach (S1 / S2 contract of 2026-11-08: every pair-less table
    is minus-one but those whose slot 0 code names or defines). Minus-one reasons:
      'd22-list'      v is a vftable of a class in S1's D22 list (d22.json) or of one prepare converted
                      (build/d22_applied.tsv), whatever slot 0 is;
      'd26-callonly'  slot 0 is a placeholder name (D26B_PLACEHOLDER) no body defines;
      'd26-undefined' slot 0 has no body in any object (undefined, a trap or placeholder under any name);
      'd26-defined-deldtor' slot 0 is a defined native function and exe slot 0 is a deleting destructor
                      (slot0_deldtor: exevft.deleting_dtor, the exe-verified rule of D26; orchestrator 2026-11-08 11:49):
                      D1 / D0 become the flags stubs into the named function (vmo_dtor_slots). FNVNX_D26DS=0 opts out.
    Exe-order reasons (EXE_ORDER_REASONS):
      'com'           a COM interface;
      'd22-used'      some code names slot 0 (the callers number the slots as the exe does);
      'defined-slot0' slot 0 is a defined native function (BSNiAllocator::Destroy) that is no deleting destructor in the
                      exe, and v has no list evidence.
    None: the table has a pair (d). called / defined: slot-0 symbols code references / a .text body defines."""
    if d is not None:
        return None
    s0 = raw_names[0] if raw_names else None
    if is_com(z, raw_names):
        return 'com'
    if named_placeholder(s0, called):
        return 'd22-used'
    if v in listed:
        return 'd22-list'
    if s0 and s0 in (defined or ()):
        if slot0_deldtor and os.environ.get('FNVNX_D26DS', '1') != '0':
            return 'd26-defined-deldtor'
        return 'defined-slot0'
    mem = itanium_member(s0)
    return 'd26-callonly' if mem and D26B_PLACEHOLDER.match(mem) else 'd26-undefined'


def d26_minus_one_reason(z, v, raw_names, d, listed, called, defined=(), slot0_deldtor=False):
    """d26_classify's reason when the table is a minus-one one, else None (a pair, or an exe-order reason)."""
    r = d26_classify(z, v, raw_names, d, listed, called, defined, slot0_deldtor)
    return None if r is None or r in EXE_ORDER_REASONS else r


def code_refs(objpaths, cands):
    """Symbols of cands that some object's code (.text sections) references: the calls by name D22-USED looks for."""
    hit = set()
    for p in objpaths:
        try:
            o = vtables.Obj(p)
        except (OSError, ValueError):
            continue
        for i, s in enumerate(o.secnames):
            if not s.startswith('.text'):
                continue
            for off, (si, ty, add) in o.rel(i).items():
                nm = o.syms[si][0]
                if nm in cands:
                    hit.add(nm)
    return hit



A64_RET = bytes.fromhex('c0035fd6')   # `ret`


def code_defs(objpaths, cands):
    """Symbols of cands that some object defines with a body (a symbol in a .text section): the slot-0 functions code
    defines directly. A placeholder no object defines, a trap, or an empty body (a bare `ret`) is not one."""
    hit = set()
    for p in objpaths:
        try:
            o = vtables.Obj(p)
        except (OSError, ValueError):
            continue
        for nm in cands:
            i = o.defs.get(nm)
            if i is not None and o.secnames[o.syms[i][5]].startswith('.text'):
                _, value, size, _, _, shndx = o.syms[i]
                sec = o.secs[shndx]
                if size == len(A64_RET) and o.data[sec[4] + value: sec[4] + value + size] == A64_RET:
                    continue   # `virtual void v00() {}` of a stand-in class (NiAVObject in tes.cpp): a placeholder, not a body
                hit.add(nm)
    return hit


def class_names(z):
    """Simple class name of a `_ZTV<enc>` symbol (the last name component), else None."""
    return itanium_member('_ZN' + z[5:] if z.startswith('_ZTVN') else '_ZN' + z[4:] + 'X')


VTFILL_REAL = ('__fnvnx_vtrap_', '.reloc', '.section:')
GCC_THUNK_DTOR = re.compile(r'^_ZThn\d+_.*D([01])Ev$')


VTFILL_HOLD = set()   # {(vtable symbol, slot index)}: load_vtfill_holds
VTFILL_ITANIUM = None   # the link's Itanium signature parser (main sets it); vtfill_hold_abi demangles alone else


def vtfill_hold_abi(raw, x, native, msvc):
    """R4/S1: how a call through a held slot (raw: the object's own Itanium slot symbol) reaches the exe override x's
    native definition (its MSVC declaration): fnvlink.slot_abi's answer, or None when either signature is missing."""
    nd = native.get(x)
    if not raw or not raw.startswith('_Z') or not nd or not nd.get('msvc'):
        return None
    ds = msvc(nd['msvc'])
    if ds is None or ds.kind != 'func' or ds.raw_params is None:
        return None
    if VTFILL_ITANIUM is not None:
        isig = VTFILL_ITANIUM(raw)
    else:
        try:
            dem = subprocess.run([CXXFILT, raw], capture_output=True, text=True).stdout.strip()
        except OSError:
            return None
        isig = sigs.parse_itanium(raw, dem)
    if isig is None or isig.raw_params is None:
        return None
    return slot_abi(isig, ds)


def load_vtfill_holds(top=None):
    """R4's overrides.tsv (fnvnx-vtbind-override/1, fnv-nx-build recon/vtbind) rows whose abi column starts with
    `abi-check`: {(table, slot)} VTFILL keeps as compiled unless slot_abi finds the call reaches the override
    unchanged (vtfill_hold_abi). The other rows need nothing: VTFILL writes the exe
    override's native in every slot it fills."""
    top = top or os.environ.get('FNVNX_RECON')
    out = set()
    if not top or not os.path.isdir(top):
        return out
    for dp, _, fs in os.walk(top):
        if 'overrides.tsv' not in fs:
            continue
        try:
            lines = open(os.path.join(dp, 'overrides.tsv')).read().split('\n')
        except OSError:
            continue
        if not lines or not lines[0].startswith('# fnvnx-vtbind-override/1'):
            continue
        for line in lines[1:]:
            p = line.split('\t')
            if len(p) >= 7 and not line.startswith('#') and p[6].startswith('abi-check'):
                try:
                    out.add((p[0], int(p[2])))
                except ValueError:
                    pass
    return out


def vtfill_block(expv, cur, z, base, off, native, providers, exe_path, msvc, bridges, traps, st, vtbind=None,
                 raw=None):
    """VTFILL's words of one vtable of a group (classlayout.expected's list expv) -> [native symbol]. base: the word
    index of the vtable's address point in the group (2 for the primary), for the trap reports. cur: the vtable's
    words as the object has them (renamed); raw: the same words before resolve's renames (the Itanium slot symbols
    vtfill_hold_abi compares). off (VTFILL2): the COL offset of a secondary exe vftable, else None.
    D1 / D0 at a ('dtor', a) pair: the primary's vmo_dtor_slots (uncompiled ??_G included, W45); in a secondary
    vtable a GCC this-adjusting `_ZThn<off>_...D1Ev` / `D0Ev` pair the object already has there stays, else the exe's
    adjustor (`sub ecx, off; jmp ??_G`, vmo_adjustor) gets `__fnvnx_vadjd_<a>_<0|1>` stubs, the same this-adjustment
    in front of the ??_G's pair (as minus_one_copies). Every other slot: exe_slot_target (off: an adjustor thunk of
    exactly off becomes `__fnvnx_vadj_<a>`), and in a secondary vtable an address MSVC compiled with `this` at that
    base (the address map's msvc_entry, d = off) binds to `__fnvnx_msvc_<a>`, the alias of GCC's `_ZThn` thunk
    (slotfill2.secondary_entry's rule). Where that is only a trap, R4's binding (vtbind: {exe address: (kind, symbol,
    target, n)}, load_vtbind) names the slot's native or adjustor; else a real word cur has at the index stays."""
    msvc = msvc or (lambda n_: None)
    sfx = '2' if off else ''
    fill, i = [], 0
    while i < len(expv):
        x = expv[i]
        if isinstance(x, tuple):
            pair = None
            if off:
                c0, c1 = (cur[i] if i < len(cur) else None), (cur[i + 1] if i + 1 < len(cur) else None)
                m0, m1 = GCC_THUNK_DTOR.match(c0 or ''), GCC_THUNK_DTOR.match(c1 or '')
                if m0 and m1 and m0.group(1) == '1' and m1.group(1) == '0':
                    pair = (c0, c1)
                    st['vtfill2-dtor-gcc-thunks'] += 1
                if pair is None and exe_path:
                    adj = vmo_adjustor(x[1], off, native, exe_path, plain=False)
                    inner = vmo_dtor_slots(adj[1], native, msvc, bridges, traps, st, (z, base + i), exe_path,
                                           uncompiled=True, providers=providers) if adj else None
                    if inner:
                        for fl, w_ in enumerate(inner):
                            sn = VADJD_SYM % (x[1], fl)
                            bridges.setdefault(sn, emit_adj_stub(sn, w_, adj[0]))
                        pair = (VADJD_SYM % (x[1], 0), VADJD_SYM % (x[1], 1))
                        st['vtfill2-dtor-adjustor'] += 1
                if pair is None and x[1] in (providers or {}):   # _purecall: S4's provider, no this to adjust
                    pair = vmo_dtor_slots(x[1], native, msvc, bridges, traps, st, (z, base + i), exe_path,
                                          uncompiled=True, providers=providers)
            else:
                pair = vmo_dtor_slots(x[1], native, msvc, bridges, traps, st, (z, base + i), exe_path, uncompiled=True,
                                      providers=providers)
            if pair:
                fill += list(pair)
            else:
                fill += [exe_slot_target(x[1], native, providers, exe_path, bridges, traps, st, (z, base + i),
                                         off=off)] * 2
                st['vtfill%s-dtor-no-stub' % sfx] += 1
            i += 2
            continue
        t = exe_slot_target(x, native, providers, exe_path, bridges, traps, st, (z, base + i), off=off)
        nd = native.get(x) if off else None
        if nd and t == ADDR_SYM % x and nd.get('msvc_entry') and nd['msvc_entry'][1] == off:
            t = MSVC_ENTRY_SYM % x
            st['vtfill2-msvc-entry'] += 1
        if t.startswith('__fnvnx_vtrap_') and x in (vtbind or {}):
            kb, sb, tb, nb = vtbind[x]
            kb = {'function': 'fn', 'adjthunk': 'adj', 'deldtor': 'fn'}.get(kb, kb)   # R4's two files
            if kb == 'adj' and tb:
                t = ADJ_SYM % x
                bridges.setdefault(t, emit_adj_stub(t, tb, nb))
                st['vtfill%s-vtbind-adj' % sfx] += 1
            elif kb == 'fn' and sb:
                t = sb
                st['vtfill%s-vtbind-fn' % sfx] += 1
        how = None
        if (z, i) in VTFILL_HOLD and i < len(cur) and cur[i] and t != cur[i] and raw and i < len(raw) and \
                os.environ.get('FNVNX_ABIREL', '1') != '0':
            how = vtfill_hold_abi(raw[i], x, native, msvc)
        if how in ('same', 'the slot passes more'):
            # S1's rule (slot_abi): every argument the definition reads arrives where it reads it, in the same
            # register file, so the exe's override is written (vtfill-abi-released)
            st['vtfill%s-abi-released' % sfx] += 1
        elif (z, i) in VTFILL_HOLD and i < len(cur) and cur[i] and t != cur[i]:
            # R4 (recon/vtbind overrides.tsv, abi-check): the exe's derived override and the slot's declaration take
            # other parameters; writing the override would pass the callers' arguments to the wrong shape, so the
            # compiled word stays until the class is declared right (listed: vtfill-abi-hold)
            t = cur[i]
            st['vtfill%s-abi-hold' % sfx] += 1
        elif t.startswith('__fnvnx_vtrap_') and i < len(cur) and cur[i] and not cur[i].startswith(VTFILL_REAL):
            t = cur[i]
            st['vtfill%s-kept' % sfx] += 1
        elif t.startswith('__fnvnx_vtrap_'):
            st['vtfill%s-trap' % sfx] += 1
        fill.append(t)
        i += 1
    return fill


def vtfill2_group(z, path, vs, ents, names, pts, n, fill, ov, xs, o, vmap, exev, lay, native, providers, exe_path,
                  msvc, bridges, traps, st, out, have, vtbind=None):
    """VTFILL2 (W46): the words of a group with secondary bases, every vtable transform(its exe vftable). fill: the
    primary's VTFILL words (vtfill_block). Each secondary vtable (address point ap, offset-to-top -d) whose V rows
    (vmap: this group's object, ap) name agreeing RTTI exe vftables at COL offset d with a classlayout row gets
    vtfill_block of that row (off = d); without a V row (a displaced copy), the one exe vftable of the primary's RTTI
    class at COL offset d stands for it (vtfill2-secondary-by-offset); a vtable without either keeps the object's
    words (with exe_vtable_copies' per-use words ov / xs). Offset-to-top and typeinfo words stay. Lengths: a vtable
    whose exe list is shorter keeps its remaining native words; a longer last vtable grows at the group's end; a
    longer primary or secondary before the last grows in place, every later address point moving down
    (slotfill2.shift_steps: one (from, delta) step per vtable that grows, vtshift.tsv for apply's patches), or, when
    an object referencing the group cannot be patched, every such vtable is cut at its native length
    (vtfill2-primary-short / vtfill2-secondary-short). -> (word lines, shift steps or None, secondary vtables
    filled), or None to leave the group to the builder (a kept word that is a relocation or a local symbol)."""
    wn = list(names)
    for i, t in ov.items():
        wn[i] = t
    for i, a_ in xs.items():
        wn[i] = exe_slot_target(a_, native, providers, exe_path, bridges, traps, st, (z, i), off=None)
    keep = set(range(len(ents)))
    for k in range(min(n, len(fill))):
        wn[2 + k] = fill[k]
        keep.discard(2 + k)
    inserts = [(8 + 4 * n, fill[n:], 'primary')] if len(fill) > n else []   # (group byte offset, words, which)
    tail_extra = []
    nsec = 0
    last_ap = pts[-1][0]
    for ap, ott in pts[1:]:
        ns = vtables.slots_at(ents, ap)
        vs2 = sorted(v for v, (z_, ap_, path_, note_) in vmap.items()
                     if z_ == z and ap_ == ap and path_ == path and v in exev and exev[v].td and v in lay)
        if not vs2:   # no V row (a displaced copy): the class's one exe vftable at this COL offset
            tds = {exev[v].td for v in vs if v in exev}
            vs2 = [v for v, e in exev.items() if e.td and e.td in tds and e.off == -ott and v in lay] \
                if len(tds) == 1 and ott else []
            if len(vs2) != 1:
                st['vtfill2-secondary-no-exe'] += 1
                continue
            st['vtfill2-secondary-by-offset'] += 1
        vv = max(vs2, key=lambda v: len(exev[v].slots))
        sl = exev[vv].slots
        dec = {(lay[v]['kind'], lay[v]['dtor_slot']) for v in vs2}
        offs = {exev[v].off for v in vs2}
        if any(exev[v].slots != sl[:len(exev[v].slots)] for v in vs2) or len(dec) != 1:
            st['vtfill2-secondary-disagree'] += 1
            continue
        d_ = offs.pop() if len(offs) == 1 else None
        if not d_ or -ott != d_:
            st['vtfill2-secondary-offset-differs'] += 1
            continue
        import classlayout as _cl
        kind_, k_ = next(iter(dec))
        expv = _cl.expected(dict(kind=kind_, dtor_slot=k_), _cl.real_slots(exe_path, vv, sl) if exe_path else sl)
        b = ap // 4
        f2 = vtfill_block(expv, wn[b:b + ns], z, b, d_, native, providers, exe_path, msvc, bridges, traps, st,
                          vtbind, raw=[e.sym for e in ents[b:b + ns]])
        for k in range(min(ns, len(f2))):
            wn[b + k] = f2[k]
            keep.discard(b + k)
        if len(f2) > ns and ap == last_ap:
            tail_extra = f2[ns:]
            st['vtfill2-last-extended'] += 1
        elif len(f2) > ns:   # before the next vtable's (offset-to-top, typeinfo) pair
            inserts.append((ap + 4 * ns, f2[ns:], 'secondary'))
        nsec += 1
        st['vtfill2-secondary-' + kind_] += 1
        st['vtfill2-secondary-slots'] += min(ns, len(f2)) + (len(tail_extra) if ap == last_ap else 0)
    for i in sorted(keep):
        e = ents[i]
        if e.sym is None:
            continue
        if e.sym.startswith(('.reloc', '.section:')):
            st['vtfill2-skipped-relocation'] += 1
            return None
        j = o.defs.get(e.sym)
        if wn[i] == e.sym and j is not None and o.syms[j][4] == 0:
            st['vtfill2-skipped-local-symbol'] += 1
            return None
    words = ['    .word %s' % ('%s%+d' % (w, e.addend) if (e.addend and i in keep) else w) if e.sym or i not in keep
             else '    .word 0x%x' % e.word for i, (e, w) in enumerate(zip(ents, wn))]
    words += ['    .word %s' % w for w in tail_extra]
    shift = None
    if inserts:
        plan = [(at, 4 * len(ws)) for at, ws, _ in inserts]
        shift = slotfill2.shift_steps(z, plan, out, have, st)
        for _, ws, which in inserts:
            st['vtfill2-%s-%s' % (which, 'shifted' if shift else 'short')] += 1
            st['vtfill2-%s-%s-slots' % (which, 'shifted' if shift else 'short')] += len(ws)
        if shift:
            slotfill2.record_shift(z, plan, out)
            for at, ws, _ in sorted(inserts, key=lambda x: x[0], reverse=True):
                words[at // 4:at // 4] = ['    .word %s' % w for w in ws]
    return words, shift, nsec


PLAIN_MAX = 64


def plain_vtfill(exev, vmap, alt, exe_path, vt_on):
    """W49 (link b7b866ef, S5 null-device boot: SetAt 00a642a0 reads exe slots 2 / 3 of ShaderMap's 4-slot table, a trap
    and a 0): a primary V row (or a displaced primary, alt) whose exe vftable has no RTTI (exevft.plain_vftables: the
    shader maps' 0x010af6cc) is extended like an RTTI one, its end the slot before the next deleting destructor (a
    vftable holds one, at its destructor slot; with no COL between them the scan runs into the neighbours: 0x010ade18
    reads 35 slots, five tables of 7), at most PLAIN_MAX. -> (exev with those vftables' slots cut and an RTTI-less
    marker kept, count). FNVNX_VTPLAIN=0: exev unchanged."""
    if not vt_on or os.environ.get('FNVNX_VTPLAIN', '1') == '0':
        return exev, 0
    cand = {v for v, (z, ap, path, note) in vmap.items()
            if v in exev and not exev[v].td and note.startswith('primary') and ap == 8}
    cand |= {v for v, zs in (alt or {}).items() if v in exev and not exev[v].td and
             any(kind == 'primary' and ap == 8 for _, ap, _, kind in zs)}
    if not cand:
        return exev, 0
    out = dict(exev)
    n = 0
    for v in sorted(cand):
        slots = list(exev[v].slots[:PLAIN_MAX])
        for i in range(1, len(slots)):
            if exevft.deleting_dtor(exe_path, slots[i]):
                slots = slots[:i]
                break
        if not slots:
            continue
        out[v] = exevft.Vft(PLAIN_TD % v, 0, [], slots)
        n += 1
    return out, n


PLAIN_TD = '.fnvnx-plain-%08x'   # a stand-in type descriptor: the vftable is extended, it has no RTTI class


def alias_alt(vnames, vmap, by_cls, rtti, alt):
    """W49: the other classes the units name for one primary image vftable (vftable_aliases.tsv: MSVC folded them, or
    a decomp shell of the same class - ShaderMap and NiTStringPointerMap<ShaderBufferRecord*> on 0x010af6cc) join the
    V row's as displaced primaries (alt), so exe_vtable_copies gives their native vtables the exe's slots too. ->
    count added. FNVNX_VTPLAIN=0: none."""
    if os.environ.get('FNVNX_VTPLAIN', '1') == '0':
        return 0
    named = collections.defaultdict(set)
    for (n_, a_), (cls_, base_, _) in vnames.items():
        if base_ is None:
            named[a_].add(cls_)
    n = 0
    for a_, clss in named.items():
        if len(clss) < 2 or a_ not in vmap:
            continue
        for cls_ in sorted(clss):
            zs = sorted(by_cls.get(cls_, []))
            if not zs or zs[0] == vmap[a_][0] or zs[0] not in rtti.where:
                continue
            pts = [p for p, ott in vtables.address_points(rtti.ents(zs[0]) or []) if ott == 0]
            if pts[:1] != [8]:
                continue
            if all(x[0] != zs[0] for x in alt.get(a_, ())):
                alt[a_].append((zs[0], 8, rtti.where[zs[0]][0].path, 'primary'))
                n += 1
    return n


def exe_vtable_copies(exev, vmap, rtti, have, per_obj, traps, native, bridges, out, slot_over=None, exe_path=None,
                      providers=None, alt=None, msvc=None, listed=None, called_fn=None, unbound=None, defined_fn=None):
    """S2c-vt: a primary native vtable shorter than the exe's vftable gets an extended copy: the group's words as the
    kept object has them (its slot symbols renamed as resolve renames them in that object), then exe slot j for every
    j past the native slots (the minus-one rule): `__fnv_<a>` where the address map has a plain native definition,
    the deleting-destructor stub for a `??_G`, else a trap stub reporting the exe address. The copy is a strong
    global definition of the same `_ZTV<C>` in bridges.S, so it wins over the GCC copies (weak COMDAT, every one of
    the 1,389 on link 31971038913d); out/vtext.tsv names the object the words came from, which apply's typeinfo
    pairing takes as the vtable's object. Only single-vtable groups (no secondary vtable whose address point would
    move) whose words are plain or ABS32 relocations against global symbols. Callers reach the extra slots through
    their own class's declaration, as the image vftable word would take them. The same copy carries the per-slot
    targets of exe_slot_fill (slot_over: one symbol whose vtables in an object disagree on the exe address), for the
    kept copy of the group (the first in link order), secondary vtables included. A word that still names a symbol
    the unit leaves undefined (unbound: extern-text addresses nothing defines, the exe slot fill left it: BaseFormComponent's
    Init / CopyFromBase / CompareWithBase, whose recipe addresses lie inside FUN_00402d30) takes the exe slot's word
    (exe_slot_target: S4's `_purecall` where the exe slot is the CRT's pure virtual, `__fnv_<a>` of a native
    definition, else a trap stub reporting the exe address) when the exe vftables the group stands for agree on it
    (`xt-slots`). Returns the counts."""
    st = collections.Counter()
    slotfill2.shift_reset(out)   # S2c-vt2: no shifted secondary groups yet (vtshift.tsv)
    by_obj = {u.obj: u for u in have}
    want = collections.defaultdict(list)    # (z, path) -> [exe vftable]
    vt_on = bool(exe_path and exev and os.environ.get('FNVNX_CLASSLAYOUT', '1') != '0' and
                 os.environ.get('FNVNX_VTFILL', '1') != '0' and os.environ.get('FNVNX_VTFILL2', '1') != '0')
    vt_only, vt_main = set(), set()   # (z, path) of a `secondary ... at +0` V row / of a primary one
    exev_rtti = exev
    exev, n_plain = plain_vtfill(exev, vmap, alt, exe_path, vt_on)
    st['plain-vftables-filled'] = n_plain
    for v, (z, ap, path, note) in vmap.items():
        # only vftables with RTTI: their end is the next COL pointer; a plain one (exevft.plain_vftables) may run on
        # into an unnamed neighbour, so it only fills slots the native vtable has (exe_slot_fill)
        if v in exev and exev[v].td and note.startswith('primary') and ap == 8:
            want[(z, path)].append(v)
            vt_main.add((z, path))
        elif v in exev and exev[v].td and exev[v].off == 0 and ap == 8 and vt_on:
            # W46: the exe's primary vftable on the native group's primary vtable, which the V row calls `secondary
            # {for X} at +0` (X is the native primary base: TESWorldSpace's _ZTV12FUN_00582e30, 0x0103195c). VTFILL
            # only: the builder's other rules keep their tables (vt_only)
            want[(z, path)].append(v)
            vt_only.add((z, path))
    # the native vtables exe_pair displaced from a V row (the shell a unit's constructor stores: FUN_004de530 for
    # INIPrefSettingCollection, 0x01022744): objects built natively still carry them, so they get the exe's slots too
    for v, zs in (alt or {}).items():
        for z, ap, path, kind in zs:
            if v in exev and exev[v].td and kind == 'primary' and ap == 8 and v not in want.get((z, path), []):
                # only when it agrees with the exe vftables the group already stands for (one a prefix of the
                # other): a disagreeing displaced one must not stop the group's own extension
                have_ = want.get((z, path), [])
                if any(not (exev[w].slots[:len(exev[v].slots)] == exev[v].slots[:len(exev[w].slots)]) for w in have_):
                    st['displaced-vtables-disagree'] += 1
                    continue
                want[(z, path)].append(v)
                vt_main.add((z, path))
                st['displaced-vtables'] += 1
    # a group whose V rows are all secondary vtables of one RTTI class C (TESActorBase's FUN_005f75e0, AlchemyItem's
    # FUN_00402d50: the primary's own V row names another native table): its primary stands for C's primary exe
    # vftable too, so VTFILL decides and fills it (W46's 837 undecided slots on 4264bfd7). FNVNX_VTPRIM=0 turns it off.
    if vt_on and os.environ.get('FNVNX_VTPRIM', '1') != '0':
        sec_td = collections.defaultdict(set)
        for v, (z, ap, path, note) in vmap.items():
            if v in exev and exev[v].td and exev[v].off and ap != 8:
                sec_td[(z, path)].add(exev[v].td)
        prim_of = collections.defaultdict(list)
        for v, e in exev.items():
            if e.td and e.off == 0:
                prim_of[e.td].append(v)
        for key, tds in sec_td.items():
            if key in want or len(tds) != 1:
                continue
            ps = prim_of.get(next(iter(tds)), [])
            if len(ps) == 1:
                want[key].append(ps[0])
                vt_only.add(key)
                st['vtfill-primary-by-class'] += 1
    called, defined = set(), set()
    exe_order = []   # (table, exe vftables, reason): the pair-less tables that stay in the exe's slot order
    if called_fn is not None:   # W30: slot 0 placeholders some code names (D22-USED): one pass over the objects
        c0 = set()
        for (z, path) in want:
            o_ = by_obj.get(path) and vtables.Obj(path)
            if o_ and z in o_.defs:
                e_ = o_.ents(z)
                if len(e_) > 2 and e_[2].sym:
                    c0.add(e_[2].sym)
        called = called_fn(c0)
        defined = defined_fn(c0) if defined_fn is not None else set()
    fam = family_prefixes(want, by_obj, exev, exe_path) if exe_path else {}
    # classlayout (orchestrator 2026-11-08 12:10): the exe's own decision per RTTI vftable, the one prepare reads too;
    # the heuristics below (d26_classify, family_dtor_pos) remain only for tables it does not decide
    vtbind = load_vtbind()   # R4's address -> native / adjustor binding for VTFILL's otherwise-trap slots
    st['vtbind-rows'] = len(vtbind)
    VTFILL_HOLD.clear()
    VTFILL_HOLD.update(load_vtfill_holds())
    if VTFILL_HOLD:
        st['vtbind-hold-rows'] = len(VTFILL_HOLD)
    lay = {}
    if exe_path and exev and os.environ.get('FNVNX_CLASSLAYOUT', '1') != '0':
        import classlayout
        lrows = classlayout.build(exe_path, exev_rtti)
        lay = {r['vftable']: r for r in lrows}
        # W49: the plain vftables plain_vtfill extended get the same decision for VTFILL (classlayout.tsv, which
        # prepare and S1 read, stays the RTTI vftables')
        for v_ in (v_ for v_ in exev if v_ not in exev_rtti or exev[v_] is not exev_rtti[v_]):
            if (exev[v_].td or '').startswith('.fnvnx-plain-'):
                kind_, k_, why_, _ = classlayout.decide(exe_path, exev[v_])
                lay[v_] = dict(cls='', vftable=v_, kind=kind_, dtor_slot=k_, reason='plain: ' + why_)
                st['classlayout-plain-rows'] += 1
        if out and os.path.isdir(out):
            classlayout.write_layout(lrows, os.path.join(out, 'classlayout.tsv'))
        st['classlayout-rows'] = len(lrows)
    over = collections.defaultdict(dict)    # (z, kept path) -> {word index: target}
    for (path, z, i), t in (slot_over or {}).items():
        if rtti.where.get(z) and rtti.where[z][0].path == path:
            over[(z, path)][i] = t
        else:
            st['per-slot-not-the-kept-copy'] += 1
    for key in over:
        want.setdefault(key, [])
    vt_only -= vt_main | set(over)
    if vt_only:
        st['vtfill2-primary-at-0-rows'] = len(vt_only)
    rows = []
    objs = {}
    for (z, path), vs in sorted(want.items()):
        u = by_obj.get(path)
        if u is None:
            continue
        if path not in objs:
            objs[path] = vtables.Obj(path)
        o = objs[path]
        if z not in o.defs:
            continue
        ents = o.ents(z)
        pts = vtables.address_points(ents)
        n = vtables.slots_at(ents, 8)
        ren = per_obj.get(u.source) or {}
        names = [ren.get(e.sym, e.sym) if e.sym else None for e in ents]
        ov = dict(over.get((z, path), {}))
        se = names[2:2 + n]
        # the pair is looked for on the object's own symbols: after resolve's renames D1 and D0 can both be one
        # native body (FUN_00404da0 for ~SettingPointerMap), which hid the pair and lost exe slot 1 (S5 round 5)
        d = exevft.dtor_pos([e.sym for e in ents[2:2 + n]], [e.word for e in ents[2:2 + n]])
        nm = n - 1 if d is not None else n
        slots = max((exev[v].slots for v in vs), key=len) if vs else []
        if any(exev[v].slots != slots[:len(exev[v].slots)] for v in vs):
            st['skipped-exe-vftables-disagree'] += 1
            slots = []
        only_ = (z, path) in vt_only
        extend = len(slots) > nm and not only_
        if extend:
            st['short'] += 1
        # `delete p` reaches Itanium slot 1: a table in the exe's slot order holds DeleteThis there (d0_slot_fix)
        d0w = d0_slot_fix(z, n, d, names[2:2 + n], slots, native, msvc or (lambda n_: None), bridges, traps, st,
                          exe_path) if slots and not only_ else None
        if d0w:
            st['d0slot-candidates'] += 1
        # W30: a table without a pair that its callers number minus-one (d26_minus_one_reason) is rebuilt whole in D26's
        # layout: offset-to-top, typeinfo, D1, D0 (exe slot 0), then exe slot k at native k + 1
        m1 = None
        dp = 0
        cl_done = False
        if slots and not only_ and len(pts) == 1 and d is None and lay and os.environ.get('FNVNX_D26T', '1') != '0':
            dec = {(lay[v]['kind'], lay[v]['dtor_slot']) for v in vs if v in lay}
            if len(dec) == 1:
                kind_, k_ = dec.pop()
                cl_done = True
                raw_ = [e.sym for e in ents[2:2 + n]]
                if kind_ == 'minus-one' and is_com(z, raw_):
                    kind_ = 'com'   # IUnknown's slots by name (the hierarchy descriptor may not name IUnknown)
                elif kind_ == 'minus-one' and 0 <= k_ < len(raw_) and named_placeholder(raw_[k_], called):
                    kind_ = 'd22-used'   # code names the destructor slot: prepare keeps the declaration (D22-USED)
                if kind_ == 'minus-one' and 0 <= k_ < len(slots):
                    pair1 = vmo_dtor_slots(slots[k_], native, msvc or (lambda n_: None), bridges, traps, st, (z, 2 + k_),
                                           exe_path, uncompiled=True, providers=providers)
                    if pair1:
                        m1, dp = 'classlayout', k_
                        st['classlayout-minus-one'] += 1
                        st['classlayout-minus-one-at-%d' % min(k_, 5)] += 1
                    else:
                        st['classlayout-no-dtor-stub'] += 1
                        exe_order.append((z, vs, 'classlayout-no-dtor-stub', path))
                else:
                    st['classlayout-' + kind_] += 1
                    exe_order.append((z, vs, 'classlayout-' + kind_, path))
            elif dec:
                st['classlayout-disagree'] += 1
            else:
                st['classlayout-fallback'] += 1
        if cl_done:
            pass
        elif slots and not only_ and len(pts) == 1 and d is None and os.environ.get('FNVNX_D26T', '1') != '0':
            s0n = ents[2].sym if n else None
            dd = bool(exe_path and s0n and s0n in (defined or ()) and exevft.deleting_dtor(exe_path, slots[0]))
            why = d26_classify(z, max(vs), [e.sym for e in ents[2:2 + n]], d, listed or (), called, defined, dd)
            m1 = None if why in EXE_ORDER_REASONS else why
            if not m1:
                st['exe-order-' + why] += 1
                st['exe-order-no-evidence'] += 1
                st['exe-order-no-evidence-BSNiAllocator'] += 'BSNiAllocator' in z
                exe_order.append((z, vs, why, path))
            if m1:
                pair1 = vmo_dtor_slots(slots[0], native, msvc or (lambda n_: None), bridges, traps, st, (z, 2), exe_path,
                                       uncompiled=True, providers=providers)
                if pair1 is None:
                    st['d26-slot0-not-a-destructor'] += 1
                    exe_order.append((z, vs, 'd26-slot0-not-a-destructor', path))
                    m1 = None
        elif slots and not only_ and len(pts) != 1 and d is None:
            exe_order.append((z, vs, 'multi-vtable', path))
            st['exe-order-multi-vtable'] += 1
        # V2 (seams triage, link 5fd996a4): the Actor / Projectile / TESForm family has four placeholder slots before the
        # deleting destructor (exe slot 4), so a pair-less table of it is minus-one from slot 4 (family_dtor_pos)
        if not cl_done and slots and not only_ and len(pts) == 1 and d is None and not m1 and fam and os.environ.get('FNVNX_D26F', '1') != '0' \
                and os.environ.get('FNVNX_D26T', '1') != '0' and not is_com(z, [e.sym for e in ents[2:2 + n]]):
            dp_ = family_dtor_pos(slots, fam)
            pair1 = vmo_dtor_slots(slots[dp_], native, msvc or (lambda n_: None), bridges, traps, st, (z, 2 + dp_),
                                   exe_path, uncompiled=True, providers=providers) if dp_ else None
            if pair1:
                m1, dp = 'd26-family-dtor', dp_
                gone = [x for x in exe_order if x[0] == z]
                for x in gone:
                    st['exe-order-' + x[2]] -= 1
                    if x[2] in EXE_ORDER_REASONS[:-1]:
                        st['exe-order-no-evidence'] -= 1
                        st['exe-order-no-evidence-BSNiAllocator'] -= 'BSNiAllocator' in z
                exe_order[:] = [x for x in exe_order if x[0] != z]
        xs = {}   # word index -> exe slot word, for the symbols the unit leaves undefined (see the docstring)
        xt_ = (unbound or {}).get(u.source) or ()
        for k_ in range(n if slots and xt_ else 0):
            e_ = ents[2 + k_]
            if e_.sym and e_.sym in xt_ and e_.sym not in ren and e_.sym.startswith('_ZN') and (2 + k_) not in ov \
                    and sigs.itanium_variant(e_.sym) is None:
                j_ = exevft.exe_slot_index(k_, d)
                if j_ < len(slots):
                    xs[2 + k_] = slots[j_]
        shift = slotfill2.shift_plan(z, pts, n, nm, slots, out, have, st) if extend and len(pts) != 1 else None
        if extend and len(pts) != 1 and not shift:   # S2c-vt2: shifted secondary groups (slotfill2.shift_plan)
            st['skipped-secondary-vtables'] += 1
            extend = False
        # VTFILL (orchestrator 2026-11-08 12:22): write the table as the gate expects it, transform(exe table) from
        # classlayout's decision, slot by slot: D1 / D0 at the exe's deleting-destructor slot, exe_slot_target of every
        # other exe slot (its native body, S4's _purecall, a flags / deleting stub); where that is only a trap and the
        # native table has a real word at the same index, the native word stays (listed: vtfill-kept). Never over a
        # strong (key function) definition. FNVNX_VTFILL=0 turns it off. A group with secondary bases is VTFILL2
        # (vtfill2_group; FNVNX_VTFILL2=0 keeps the builder's result for it: vtfill-skipped-secondary).
        if slots and lay and os.environ.get('FNVNX_VTFILL', '1') != '0':
            dec_ = {(lay[v]['kind'], lay[v]['dtor_slot']) for v in vs if v in lay}
            if len(dec_) == 1 and len(pts) != 1 and os.environ.get('FNVNX_VTFILL2', '1') == '0':
                st['vtfill-skipped-secondary'] += 1
            elif len(dec_) == 1:
                kind_, k_ = next(iter(dec_))
                raw_ = [e.sym for e in ents[2:2 + n]]
                if kind_ == 'minus-one' and (is_com(z, raw_) or (0 <= k_ < len(raw_) and
                                                                 named_placeholder(raw_[k_], called))):
                    kind_ = 'exe-order'   # as prepare: COM by its slot names, a destructor slot named by code
                if kind_ == 'com' or is_com(z, raw_):
                    COM_TABLES.add(z)   # W52: its __stdcall callers push the object (free_slot: no drop bridge)
                if o.bind(z) != 'weak':
                    st['vtfill-skipped-strong'] += 1
                else:
                    import classlayout as _cl
                    vv_ = max(vs, key=lambda v: len(exev[v].slots))
                    sl_ = _cl.real_slots(exe_path, vv_, slots) if exe_path else slots   # no words past the table
                    expv = _cl.expected(dict(kind=kind_, dtor_slot=k_), sl_)
                    fill = vtfill_block(expv, names[2:2 + n], z, 2, None, native, providers, exe_path, msvc, bridges,
                                        traps, st, vtbind, raw=[e.sym for e in ents[2:2 + n]])
                    head_ = ['    .word 0x%x' % ents[0].word if not ents[0].sym else '    .word %s' % ents[0].sym,
                             '    .word %s' % ents[1].sym if ents[1].sym else '    .word 0x%x' % ents[1].word]
                    g2 = None
                    if len(pts) != 1:
                        g2 = vtfill2_group(z, path, vs, ents, names, pts, n, fill, ov, xs, o, vmap, exev, lay,
                                           native, providers, exe_path, msvc, bridges, traps, st, out, have, vtbind)
                    if len(pts) == 1 or g2 is not None:
                        words_, shift2, nsec = (head_ + ['    .word %s' % w_ for w_ in fill], None, 0) if g2 is None \
                            else g2
                        lines = ['// %s from the exe vftable(s) %s: VTFILL%s, %s%s, %d slots (classlayout)%s' % (
                                     z, ' '.join('%08x' % v for v in sorted(vs)), '2 (%d secondary)' % nsec if g2 else '',
                                     kind_, ' at %d' % k_ if kind_ == 'minus-one' else '', len(fill),
                                     ' COM' if z in COM_TABLES else ''),
                                 '    .section .data.rel.ro.%s.%s,"aw"' % (m1_section(kind_ == 'minus-one', k=k_), z),
                                 '    .p2align 3',
                                 '    .globl %s' % z, '    .type %s, %%object' % z, '%s:' % z] + words_ + \
                                ['    .size %s, .-%s' % (z, z), '    .text']
                        bridges['zzvtext_' + z] = '\n'.join(lines) + '\n'
                        m1k_note(z, 'zzvtext_' + z, kind_ == 'minus-one', k_)
                        st['vtfill-tables'] += 1
                        st['vtfill-tables-' + kind_] += 1
                        st['vtfill-slots'] += len(fill)
                        if g2 is not None:
                            st['vtfill2-tables'] += 1
                        for v, (z_, ap_, path_, note_) in list(vmap.items()):
                            if z_ == z:
                                vmap[v] = (z_, slotfill2.shifted(ap_, shift2), VTEXT_OBJ,
                                           note_ + ', VTFILL%s from the exe (%s)' % ('2' if g2 else '', kind_))
                        rows.append((z, os.path.basename(path), str(n), str(max(0, len(fill) - n)),
                                     ' '.join('%08x' % v for v in sorted(vs)), '0'))
                        continue
        if only_:   # W46: a table want holds for VTFILL only, which did not write it
            st['vtfill2-only-not-written'] += 1
            continue
        if not extend and not ov and not d0w and not m1 and not xs:
            continue
        if o.bind(z) != 'weak':   # a key function's unit: its strong copy would be a second definition
            st['skipped-strong'] += 1
            if m1:
                exe_order.append((z, vs, 'skipped-strong', path))
            st['d0slot-skipped-strong'] += bool(d0w)
            continue
        for i, t in ov.items():
            names[i] = t
        for i, a_ in xs.items():
            names[i] = exe_slot_target(a_, native, providers, exe_path, bridges, traps, st, (z, i), off=None)
            st['xt-slots'] += 1
        bad = None
        for e, nmz in zip(ents, names):
            if e.sym is None:
                continue
            if e.sym.startswith(('.reloc', '.section:')):
                bad = 'relocation %s' % e.sym.split(':')[0]
                break
            i = o.defs.get(e.sym)
            if nmz == e.sym and i is not None and o.syms[i][4] == 0:   # a local definition: not reachable from here
                bad = 'local symbol'
                break
        if bad:
            st['skipped-' + bad.replace(' ', '-')] += 1
            if m1:
                exe_order.append((z, vs, 'skipped-' + bad.replace(' ', '-'), path))
            st['d0slot-skipped'] += bool(d0w)
            continue
        words = ['    .word %s' % ('%s%+d' % (nmz, e.addend) if e.addend else nmz) if e.sym else
                 '    .word 0x%x' % e.word for e, nmz in zip(ents, names)]
        extra = []
        if m1:
            words = words[:2] + \
                    ['    .word %s' % exe_slot_target(a, native, providers, exe_path, bridges, traps, st, (z, 2 + k))
                     for k, a in enumerate(slots[:dp])] + \
                    ['    .word %s' % w_ for w_ in pair1] + \
                    ['    .word %s' % exe_slot_target(a, native, providers, exe_path, bridges, traps, st, (z, 3 + k))
                     for k, a in enumerate(slots[dp + 1:], dp + 1)]
            extend, d0w, ov, shift = False, None, {}, None
            st['d26-minus-one'] += 1
            st['d26-minus-one-BSNiAllocator'] += 'BSNiAllocator' in z
            st['d26-minus-one-' + m1] += 1
            st['d26-minus-one-slots'] += len(words) - 2
        for j in (range(nm, len(slots)) if extend else ()):
            a = slots[j]
            t = exe_slot_target(a, native, providers, exe_path, bridges, traps, st, (z, 2 + j))
            extra.append('    .word %s' % t)
        if d0w and n >= 2:   # native slot 1 is a native word
            words[3] = '    .word %s' % d0w
            st['d0slot-fixed'] += 1
        elif d0w and extra:  # one native slot: slot 1 is exe slot 1, the first extra word
            extra[0] = '    .word %s' % d0w
            st['d0slot-fixed'] += 1
        elif d0w:
            st['d0slot-not-extended'] += 1
        # the primary vtable is the whole group: the extra slots go at its end
        lines = ['// %s from the exe vftable(s) %s: %d native slots + %d, %d slots per use (S2c-vt)' % (
                     z, ' '.join('%08x' % v for v in sorted(vs)), n, len(extra), len(ov)),
                 '    .section .data.rel.ro.%s.%s,"aw"' % (m1_section(bool(m1), k=dp), z), '    .p2align 3',
                 '    .globl %s' % z,
                 '    .type %s, %%object' % z, '%s:' % z] + slotfill2.place_extra(words, extra, shift, z, out) + ['    .size %s, .-%s' % (z, z), '    .text']
        bridges['zzvtext_' + z] = '\n'.join(lines) + '\n'
        m1k_note(z, 'zzvtext_' + z, bool(m1), dp)
        st['d26-minus-one-tables' if m1 else ('extended' if extra else 'copied-per-slot')] += 1
        st['slots-added'] += len(extra)
        st['slots-per-use'] += len(ov)
        for v, (z_, ap_, path_, note_) in list(vmap.items()):
            if z_ == z:
                vmap[v] = (z_, slotfill2.shifted(ap_, shift), VTEXT_OBJ, note_ + (', extended from the exe (+%d slots)' % len(extra) if extra
                                                        else ', slots per use from the exe'))
        rows.append((z, os.path.basename(path), str(n), str(len(extra)), ' '.join('%08x' % v for v in sorted(vs)),
                     str(len(ov) + len(xs))))
    with open(os.path.join(out, 'vtext.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#vtable\tobject the words came from\tnative slots\tslots added\texe vftables\tslots per use\n')
        for r in rows:
            f.write('\t'.join(r) + '\n')
    write_exe_order(out, exe_order, exev, alt)
    st['exe-order-tables'] = len({z for z, _, _, _ in exe_order})
    return st


def write_exe_order(out, exe_order, exev, alt=None):
    """out/exe_order_classes.tsv: the tables the link keeps in the exe's slot order (class, vftable, reason, native vtable
    symbol), for S1's RAWVT1 (FNVNX_EXE_ORDER): its callers number the slots as the exe does. One row per simple class
    name a native object uses for the table: the native vtable's class, the exe's RTTI class, and the classes of the
    other native vtables paired with the same exe vftable (shells such as FUN_005e0830 for an INI collection)."""
    rows = set()
    for z, vs, why, _ in exe_order:
        for v in vs:
            names = [(class_names(z), z)]
            e = exev.get(v)
            if e is not None and e.td:
                names.append((e.td[4:].rstrip('@').split('@')[0], z))
            for z2, _ap, _p, _k in (alt or {}).get(v, ()):
                names.append((class_names(z2), z2))
            for cn, zz in names:
                if cn:
                    rows.add((cn, '%08x' % v, why, zz))
    with open(os.path.join(out, 'exe_order_classes.tsv'), 'w', encoding='utf-8') as f:
        f.write('#class\tvftable\treason\tnative vtable symbol\n')
        for r in sorted(rows):
            f.write('\t'.join(r) + '\n')


def add_unit_shell_names(out, units_json, exe_path=None):
    """Add to out/exe_order_classes.tsv the class names units.json gives the same exe vftables (S1-rules' RAWVT1
    matches the class that owns the member doing `(*(void***)this)[k]`, often a decomp shell): a `??_7<C>...@@6B@`
    symbol at a listed vftable, and a constructor `??0<C>@...` whose x86 code stores a listed vftable last
    (exevft.ctor_vftable). Each row keeps the vftable's reason; the native vtable column says `units.json`.
    Returns the number of rows added."""
    p = os.path.join(out, 'exe_order_classes.tsv')
    if not units_json or not os.path.exists(p) or not os.path.exists(units_json):
        return 0
    rows, reason = set(), {}
    for line in open(p, encoding='utf-8'):
        if line.startswith('#'):
            continue
        f = line.rstrip('\n').split('\t')
        if len(f) >= 4:
            rows.add(tuple(f[:4]))
            reason.setdefault(int(f[1], 16), f[2])
    if not reason:
        return 0
    data = json.load(open(units_json))
    add = set()
    for u in data.get('units', ()):
        for n, a in (u.get('symbols') or {}).items():
            m = re.match(r'\?\?_7([A-Za-z_]\w*)@', n)
            v = int(a, 16) if isinstance(a, str) else None
            if m and v in reason:
                add.add((m.group(1), '%08x' % v, reason[v], 'units.json'))
        for fn in (u.get('functions') or ()):
            m = re.match(r'\?\?0([A-Za-z_]\w*)@', fn.get('symbol', ''))
            if not m or not exe_path:
                continue
            try:
                v = exevft.ctor_vftable(exe_path, int(fn['address'], 16), set(reason))
            except Exception:
                v = None
            if v in reason:
                add.add((m.group(1), '%08x' % v, reason[v], 'units.json'))
    new = add - rows
    if new:
        with open(p, 'w', encoding='utf-8') as f:
            f.write('#class\tvftable\treason\tnative vtable symbol\n')
            for r in sorted(rows | new):
                f.write('\t'.join(r) + '\n')
    return len(new)


VMO_SYM = '__fnvnx_vmo_%08x'   # D26 (c): minus-one native copy of an image vftable that stays at its image address


def family_prefixes(want, by_obj, exev, exe_path):
    """{exe slots before the destructor: its slot} of the tables that already have a D1/D0 pair at a slot past 0 whose
    exe slot is a deleting destructor: the evidence that a family's placeholders come before its destructor (TESForm,
    Actor, Projectile: exe slots 0..3 = nullsub, nullsub, ..., destructor at 4). Other tables of the same exe prefix
    are minus-one from that slot (family_dtor_pos)."""
    fam = {}
    objs = {}
    for (z, path), vs in want.items():
        if path not in by_obj:
            continue
        try:
            if path not in objs:
                if len(objs) > 64:
                    objs.clear()
                objs[path] = vtables.Obj(path)
            o = objs[path]
            if z not in o.defs:
                continue
            ents = o.ents(z)
            n = vtables.slots_at(ents, 8)
            d = exevft.dtor_pos([e.sym for e in ents[2:2 + n]], [e.word for e in ents[2:2 + n]])
        except (OSError, ValueError):
            continue
        if not d:
            continue
        for v in vs:
            sl = exev[v].slots if v in exev else None
            if sl is not None and len(sl) > d and exevft.deleting_dtor(exe_path, sl[d]):
                fam[tuple(sl[:d])] = d
    return fam


def family_dtor_pos(slots, fam):
    """The destructor slot (> 0) of a pair-less table whose exe slots before it are a family's placeholders, else None."""
    for pre, d in fam.items():
        if len(slots) > d and tuple(slots[:d]) == pre:
            return d
    return None


def vmo_dtor_slots(a0, native, msvc, bridges, traps, st, where, exe_path=None, uncompiled=False, providers=None):
    """D1 and D0 (native slots 0 and 1) for exe slot 0 at a0 when it is the deleting destructor: the address map's
    Itanium pair (kind G), or flags stubs into a decomp definition of the `??_G` (its declaration takes the flags, its
    MSVC name is `??_G`/`??_E`, or the exe's code has the shape: exevft.deleting_dtor); None when exe slot 0 is not
    a destructor. uncompiled (W45, the W30 / family rebuilds): a `??_G` no unit compiles (no native definition) whose
    exe code has the exact `??_G` shape (exevft.deleting_dtor_callee: call ~C, flags test, call delete, ret 4; the
    looser exevft.deleting_dtor can match a later function's `ret 4`) still gives the pair, as two traps reporting
    the exe address, so the
    table is rebuilt minus-one and exe slot k lands at k + 1 for its minus-one callers (FUN_00aa7400's 0x00aa77e0:
    the exe-order table ran Deallocate where MemoryManager FUN_00aa3e40 calls Allocate). FNVNX_D26U=0 turns it off."""
    if native.get(a0) is None and providers and a0 in providers:
        # an abstract class's destructor slot holds the CRT's pure virtual (_purecall, S4's provider): D1 and D0 are
        # that provider, not traps (R2, vtgate 4264bfd7: AGListItem 0/1, AA74C0Base 1, the vmo copies 0106c934 ...)
        st['slot0-dtor-provider'] += 1
        return providers[a0][0], providers[a0][0]
    nd = native.get(a0)
    if nd is None and a0 in HK_SDD:   # W22: the Havok sized-free ??_G's stand-in, through the flags stubs
        for fl in (0, 1):
            bridges.setdefault(FLAG_SYM % (a0, fl), emit_flag_stub(FLAG_SYM % (a0, fl), ADDR_SYM % a0, fl))
        st['slot0-hk-sdd'] += 1
        return FLAG_SYM % (a0, 0), FLAG_SYM % (a0, 1)
    if nd is None and uncompiled and exe_path and os.environ.get('FNVNX_D26U', '1') != '0' and \
            exevft.deleting_dtor_callee(exe_path, a0) is not None:
        x_ = exevft.deleting_dtor_callee(exe_path, a0)
        if native.get(x_) is not None and os.environ.get('FNVNX_GSYN', '1') != '0':
            # W48's 32 bodiless ??_G (bhkFixedConstraint 00cc7e50, ExtraEditorID 0041c660): the ??_G is MSVC's plain
            # `call X; test flags; delete`, and X (the destructor) has a native: D1 = X, D0 = X then operator delete
            t = GSYN_SYM % a0
            bridges.setdefault(t, emit_gsyn_stub(t, ADDR_SYM % x_))
            st['slot0-dtor-synth'] += 1
            return ADDR_SYM % x_, t
        pair = []
        for v_ in ('D1', 'D0'):
            t = TRAP_SYM % int(hashlib.sha1((XTRAP % a0 + ' ' + v_).encode()).hexdigest()[:8], 16)
            traps.setdefault(t, (XTRAP % a0 + ' (%s of a deleting destructor not compiled)' % v_, where))
            pair.append(t)
        st['slot0-dtor-not-compiled'] += 1
        return tuple(pair)
    if nd is None:
        return None
    var = sigs.itanium_variant(nd['sym'].name)
    if var == 'D0' and nd['variants'].get('D1') is not None:
        st['slot0-dtor-pair'] += 1
        return ADDR_SYM % a0 + ('' if nd['variants']['D1'] is nd['sym'] else '_D1'), ADDR_SYM % a0
    if var == 'D0' and os.environ.get('FNVNX_D0DESTROY', '1') != '0' and exe_path:
        # a native D0 alone (R2's Havok ??_G recons: 0x00d68740 hkpToiContactPointViewer, cb17b0, dfd8d0, d10730, ...):
        # the exe ??_G's destroy call before the flags test is the D1 (exevft.deleting_dtor_destroy; its free goes
        # through the Havok router, not operator delete), when that destructor has a native
        x_ = exevft.deleting_dtor_destroy(exe_path, a0)
        if x_ is not None and native.get(x_) is not None and sigs.itanium_variant(native[x_]['sym'].name) != 'D0':
            st['slot0-d0-destroy'] += 1
            return ADDR_SYM % x_, ADDR_SYM % a0
    if var == 'D1':   # destroys only: no deleting variant compiled
        t = TRAP_SYM % int(hashlib.sha1((XTRAP % a0 + ' D0').encode()).hexdigest()[:8], 16)
        traps.setdefault(t, (XTRAP % a0 + ' (D0 not compiled)', where))
        st['slot0-d1-only'] += 1
        return ADDR_SYM % a0, t
    ds = msvc(nd['msvc']) if nd.get('msvc') else None
    if var is None and (flags_param(ds) or (nd.get('msvc') or '').startswith(('??_G', '??_E')) or
                        (exe_path and exevft.deleting_dtor(exe_path, a0))):
        for fl in (0, 1):
            bridges.setdefault(FLAG_SYM % (a0, fl), emit_flag_stub(FLAG_SYM % (a0, fl), ADDR_SYM % a0, fl))
        st['slot0-flags-stubs'] += 1
        return FLAG_SYM % (a0, 0), FLAG_SYM % (a0, 1)
    return None


def vmo_vrows(vmo, vmap, exev):
    """D26 (c) V rows: {image vftable: (copy symbol, address point 8, bridge object, note)} for every minus-one copy of
    an exe vftable no native vtable stands for (the class is declared without virtual members, or has no secondary
    base: vtmetric's `no-polymorphic-view` and `no-secondary-base`). The address map then pairs the vftable with the
    copy (EQ's vtable pairing, vtmetric's slot comparison). The copy is the Itanium layout of the exe vftable
    (minus-one order), so the address point is word 2."""
    rows = {}
    for v, name in vmo.items():
        if v in vmap:
            continue
        e = exev.get(v)
        rows[v] = (name, 8, VTEXT_OBJ, '%s, minus-one copy of the exe vftable (D26 c)%s' % (
            'secondary' if e is not None and e.off else 'primary',
            ", the exe's RTTI class %s" % e.td[4:].rstrip('@') if e is not None and e.td else ''))
    return rows


def vmo_candidates(exev, vmap, hand, used_addr, every=True):
    """D26 (c): {image vftable: offset-to-top} of the vftables that get a minus-one copy, and counts. Those a unit
    stores by hand (hand: {address: [(unit, class, _, offset)]}) while no V row covers them, when referenced as data;
    and (W19) every RTTI exe vftable no V row stands for, referenced as data or not (every=False: only the
    referenced ones, as before): a vftable no unit mentions is still the vptr an exe constructor stores into an
    object native code then calls through, and an unpaired vftable is a hole in vtmetric. The offset-to-top is
    the exe's COL offset negated, else the store's offset."""
    vmo_c, st = {}, collections.Counter()
    for a, sites in hand.items():
        if a not in vmap and a in used_addr:
            off = collections.Counter(o_ for _, _, _, o_ in sites).most_common(1)[0][0]
            vmo_c[a] = -exev[a].off if a in exev and exev[a].td else -off
            st['vmo-hand-stored'] += 1
    for a, e_ in exev.items():
        if e_.td and a not in vmap and (every or a in used_addr) and a not in vmo_c:
            vmo_c[a] = -e_.off
            st['vmo-rtti-referenced' if a in used_addr else 'vmo-rtti-unreferenced'] += 1
    return vmo_c, st


def minus_one_copies(cands, exev, native, providers, exe_path, msvc, bridges, traps, out, lay=None):
    """D26 (c): every image vftable whose data symbol would stay at the image address (stored by hand into an
    object's vptr, no native vtable mapped: hand_vptrs 'image') gets a strong native copy `__fnvnx_vmo_<V>` in
    D22's minus-one order: offset-to-top (from the exe's COL, else the store's offset), typeinfo 0, D1/D0 for the
    exe's deleting destructor at slot 0 (vmo_dtor_slots; a trap for D1 and exe slot 0 at slot 1 when slot 0 is
    not a destructor), then exe slot k at native slot k + 1 (exe_slot_target). resolve binds `__fnv_<V>` to the
    copy's address point, so every hand store, comparison and read of V becomes native. cands: {V: offset-to-top}.
    Returns ({V: copy symbol}, counts); out/vmo.tsv lists every copy.
    lay (W46, classlayout rows {V: row}): a V whose row is exe-order / com, or minus-one at k > 0, is copied as
    transform(exe) instead (vtfill_block: exe slot i at native i, D1 / D0 at k; hkThreadMemory 0x010cacd0, exe-order:
    the minus-one copy moved its slot 3). FNVNX_VMOCL=0 turns it off."""
    st = collections.Counter()
    got, rows = {}, []
    vtb_ = None   # R4's binding file, read once when a copy follows classlayout
    for v in sorted(cands):
        e = exev.get(v)
        if e is None or not e.slots:
            st['no-exe-slots'] += 1
            continue
        name = VMO_SYM % v
        slots = list(e.slots)
        n_trap = st['slots-trap']
        row = (lay or {}).get(v) if os.environ.get('FNVNX_VMOCL', '1') != '0' else None
        if row is not None and (row['kind'] in ('exe-order', 'com') or
                                (row['kind'] == 'minus-one' and 0 < row['dtor_slot'] < len(slots))):
            import classlayout as _cl
            if vtb_ is None:
                vtb_ = load_vtbind()
            words = vtfill_block(_cl.expected(row, slots), [], name, 2, e.off if e.td and e.off else None, native,
                                 providers, exe_path, msvc, bridges, traps, st, vtb_)
            st['classlayout-' + row['kind']] += 1
        else:
            words = None
        pair = None if words is not None else \
            vmo_dtor_slots(slots[0], native, msvc, bridges, traps, st, (name, 2), exe_path, providers=providers)
        adj0 = None
        if words is None and pair is None and e.td and e.off and exe_path:
            adj0 = vmo_adjustor(slots[0], e.off, native, exe_path, plain=False)
        if adj0 is not None:   # a secondary vftable's destructor slot: `sub ecx, off; jmp ??_G` into the primary's
            inner = vmo_dtor_slots(adj0[1], native, msvc, bridges, traps, st, (name, 2), exe_path, providers=providers)
            if inner is not None:
                for fl, w_ in enumerate(inner):
                    sn = VADJD_SYM % (slots[0], fl)
                    bridges.setdefault(sn, emit_adj_stub(sn, w_, adj0[0]))
                pair = (VADJD_SYM % (slots[0], 0), VADJD_SYM % (slots[0], 1))
                st['slot0-adjustor-dtor'] += 1
        if words is None and pair is None:
            d1 = TRAP_SYM % int(hashlib.sha1((XTRAP % slots[0] + ' D1').encode()).hexdigest()[:8], 16)
            traps.setdefault(d1, (XTRAP % slots[0] + ' (D1 at an exe slot 0 that is not a destructor)', (name, 2)))
            st['slot0-not-a-destructor'] += 1
            pair = (d1, exe_slot_target(slots[0], native, providers, exe_path, bridges, traps, st, (name, 3),
                                        e.off if e.td else None))
        if words is None:
            words = list(pair) + [exe_slot_target(a, native, providers, exe_path, bridges, traps, st, (name, 3 + k),
                                                  e.off if e.td else None)
                                  for k, a in enumerate(slots[1:], 1)]
        ott = cands[v]
        lines = ['// %s: exe vftable %08x in the %s order, %d exe slots (D26 c)' % (
                     name, v, 'classlayout %s' % row['kind'] if row is not None and pair is None else 'minus-one',
                     len(slots)),
                 '    .section .data.rel.ro.%s.%08x,"aw"' % (
                     m1_section(not (row is not None and pair is None and row['kind'] != 'minus-one'), 'fnvnx_vmo',
                                k=row['dtor_slot'] if row is not None and pair is None and row['kind'] == 'minus-one'
                                else 0), v),
                 '    .p2align 3', '    .globl %s' % name,
                 '    .hidden %s' % name, '    .type %s, %%object' % name, '%s:' % name,
                 '    .word 0x%x' % (ott & 0xffffffff), '    .word 0'] + ['    .word %s' % w for w in words] + \
                ['    .size %s, .-%s' % (name, name), '    .text']
        bridges['zzvmo_%08x' % v] = '\n'.join(lines) + '\n'
        m1k_note(name, 'zzvmo_%08x' % v, not (row is not None and pair is None and row['kind'] != 'minus-one'),
                 row['dtor_slot'] if row is not None and pair is None and row['kind'] == 'minus-one' else 0)
        got[v] = name
        st['copies'] += 1
        st['slots'] += len(words)
        rows.append(('%08x' % v, str(ott), str(len(slots)), str(st['slots-trap'] - n_trap),
                     *(list(words[:2]) + ['', ''])[:2]))   # a classlayout exe-order table can have < 2 slots
    with open(os.path.join(out, 'vmo.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#image vftable\toffset-to-top\texe slots\ttrap slots\tnative slot 0 (D1)\tnative slot 1 (D0)\n')
        for r in rows:
            f.write('\t'.join(r) + '\n')
    return got, st


def write_vtable_requests(out, real, objsyms, vnames, it, itanium, hand_req=None, exe_req=None):
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
    for cls, (text, hosts) in sorted((exe_req or {}).items()):    # the class the exe's RTTI gives an image vftable
        if cls not in have_ztv:
            for h in hosts:
                want(cls, text, 'exe-rtti', h)
    vtables.write_requests(os.path.join(out, 'vtable_requests.tsv'), {c: tuple(e) for c, e in reqs.items()})
    with open(os.path.join(out, 'rtti_defined.txt'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('\n'.join(sorted(rdefs)) + '\n')


PORT = os.path.dirname(os.path.dirname(HERE))
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


def load_imports(imports_tsv, exports):
    """platform/win32/imports.tsv (S4: IAT slot, dll, api, ...) -> {slot address: native symbol} for the slots the
    platform implements (the slot's api, not the decomp's spelling of it, decides)."""
    out = {}
    if imports_tsv and os.path.exists(imports_tsv):
        for line in open(imports_tsv, encoding='utf-8', errors='surrogateescape'):
            f = line.rstrip('\n').split('\t')
            if line.startswith('#') or len(f) < 3 or not re.match(r'^0x[0-9a-fA-F]+$', f[0]) or not_iat_row(f):
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
            if not line.startswith('#') and len(f) >= 3 and re.match(r'^0x[0-9a-fA-F]+$', f[0]) and IDENT.match(f[2]) \
                    and not not_iat_row(f):
                out[int(f[0], 16)] = (f[1].lower(), f[2])
    return out


def not_iat_row(f):
    """An imports.tsv row S4 keeps only to record a decomp mislabel: dll `(not an IAT slot)` (0x010c4cec, the
    `__imp_` name the recipe put on slot 2 of the image vftable 0x010c4ce4). It is no IAT slot and no import, so the
    reference goes on to vftable_import (the exe word at the slot decides), never to an import named after it."""
    return f[1].strip().startswith('(')


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


def load_override_va(path):
    """Override table rows with a 5th column `va=<none|escape>`: {address: va_reads} of a variadic override (what
    its body does with the anonymous arguments, abi.va_reads_check; the plugin does not read the C sources)."""
    out = {}
    if path:
        for line in open(path, encoding='utf-8', errors='surrogateescape'):
            f = line.rstrip('\n').split('\t')
            if not line.startswith('#') and len(f) >= 5 and f[4].startswith('va='):
                out[override_key(f[0])] = (f[4][3:],)
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


STD_IDS = []   # [set] of every identifier libstdc++'s headers declare (std_identifiers); empty: not known


def std_identifiers(cache_dir=None):
    """The identifiers in GCC's libstdc++ headers (`#include <bits/stdc++.h>` preprocessed with $CXX, default
    aarch64-linux-gnu-g++), cached in <cache_dir>/libstdcxx_ids.txt. Empty when the compiler is not there: every
    std:: name then stays `runtime`, as before."""
    if STD_IDS:
        return STD_IDS[0]
    path = os.path.join(cache_dir, 'libstdcxx_ids.txt') if cache_dir else None
    ids = set()
    if path and os.path.exists(path):
        ids = set(open(path).read().split())
    else:
        try:
            r = subprocess.run([os.environ.get('CXX', 'aarch64-linux-gnu-g++'), '-std=gnu++17', '-E', '-x', 'c++', '-'],
                               input='#include <bits/stdc++.h>\n', capture_output=True, text=True, timeout=300)
            if r.returncode == 0:
                ids = set(re.findall(r'\b[A-Za-z_]\w*\b', r.stdout))
        except (OSError, subprocess.SubprocessError):
            ids = set()
        if ids and path:
            os.makedirs(cache_dir, exist_ok=True)
            open(path, 'w').write('\n'.join(sorted(ids)) + '\n')
    STD_IDS.append(ids)
    return ids


STD_EXPORTS = []   # [set] of every global symbol libstdc++ defines (std_exports); empty: not known
STD_PLATFORM = []  # [set] of the std:: names platform/crt defines under an asm label (platform_std_names)
CRT_DIR = os.path.normpath(os.path.join(HERE, '..', '..', 'platform', 'crt'))


def std_exports(cache_dir=None):
    """Every global symbol the toolchain's libstdc++ defines (`nm -g --defined-only` of its libstdc++.a and of the
    shared library next to it; $CXX -print-file-name finds both), cached in <cache_dir>/libstdcxx_syms.txt. A std::
    name referenced and undefined in an object is one the unit's own headers did not instantiate, so only a library
    symbol can satisfy it. Empty when the compiler or nm is not there (decomp_std then relies on the identifiers)."""
    if STD_EXPORTS:
        return STD_EXPORTS[0]
    path = os.path.join(cache_dir, 'libstdcxx_syms.txt') if cache_dir else None
    syms = set()
    if path and os.path.exists(path):
        syms = set(open(path).read().split())
    else:
        cxx = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
        nm = os.environ.get('NM', cxx[:-len('g++')] + 'nm' if cxx.endswith('g++') else 'nm')
        for lib, flags in (('libstdc++.a', ['-g']), ('libstdc++.so', ['-D'])):
            try:
                p = subprocess.run([cxx, '-print-file-name=' + lib], capture_output=True, text=True, timeout=60)
                f = p.stdout.strip()
                if p.returncode or not os.path.isabs(f) or not os.path.exists(f):
                    continue
                r = subprocess.run([nm] + flags + ['--defined-only', os.path.realpath(f)], capture_output=True,
                                   text=True, timeout=300, errors='replace')
            except (OSError, subprocess.SubprocessError):
                continue
            for line in r.stdout.splitlines():
                w = line.split()
                if len(w) == 3:
                    syms.add(w[2].split('@')[0])
        if syms and path:
            os.makedirs(cache_dir, exist_ok=True)
            open(path, 'w').write('\n'.join(sorted(syms)) + '\n')
    STD_EXPORTS.append(syms)
    return syms


def platform_std_names():
    """The std:: names platform/crt defines (S4's msvc_std_names.cpp: asm labels spelled as string literals) - they
    stay with the runtime link, whoever the libstdc++ is."""
    if not STD_PLATFORM:
        names = set()
        if os.path.isdir(CRT_DIR):
            for f in sorted(os.listdir(CRT_DIR)):
                if f.endswith(('.c', '.cpp', '.h')):
                    names |= set(re.findall(r'"(_ZN?K?St\w+)"', open(os.path.join(CRT_DIR, f), errors='replace').read()))
        STD_PLATFORM.append(names)
    return STD_PLATFORM[0]


def decomp_std(un, isig):
    """A `std::` name (_ZNSt / _ZSt) that libstdc++ cannot provide: the decomp's own class or function in namespace
    std (std::StandardLibrarySingletonObject, std::StringBase::_Xlen, std::_Lockit, std::FUN_00ad5770, ...), or an
    MSVC STL helper or member it declares without a body (std::_Destroy<T>(T*), std::swap<T>(T&, T&) with a
    `void` result, std::fill, std::vector<T>::_Xlen, std::locale::facet::_Incref, std::bad_cast(const char*), ...).
    Two tests, either one: libstdc++'s headers do not declare the first component after std:: (the identifiers,
    std_identifiers), or the name is not a symbol of the toolchain's libstdc++ (std_exports) and platform/crt does
    not define it (platform_std_names). They are classified like any other decomp name (units.json, bodies'
    exe-addr / crt-rule) instead of being left to the runtime (topdown rank 2/3 on d1ed15e7; the second test:
    rows 1047-1154 on 0540aff0d154)."""
    if not un.startswith(('_ZNSt', '_ZSt')):
        return False
    if STD_EXPORTS and STD_EXPORTS[0] and un not in STD_EXPORTS[0] and un not in platform_std_names():
        return True
    if not STD_IDS or not STD_IDS[0]:
        return False
    q = getattr(isig, 'qual', None) or []
    if len(q) < 2 or q[0] != 'std':
        return False
    first = re.match(r'[A-Za-z_]\w*', q[1])
    return bool(first) and first.group(0) not in STD_IDS[0]


def classify_ref(un, isig, idx, gdef, gref, anydef, msvc, ident=None, uident=None):
    """-> (class, tier, address, target, note). class 'addr' = an address was found (refined by the caller).
    ident: {data identifier: image addresses} - the last resort for a data reference (S5): the same identifier in
    another scope (the decomp's per-function namespaces) or with another decorated type (an array declared where
    MSVC saw a pointer, cv), bound when exactly one address carries it, 'ambiguous' otherwise. uident: the same map
    over the referencing unit's own symbols only: an identifier at several image addresses binds to the one of
    them the unit itself names (`unit/ident`; W44: font.cpp's FontTextManager::pInstance, whose unit names only
    `?pInstance@FontManager@@2PAV1@A` = 0x011f33f8 among 0x011ccb78 / 0x011d8ce8 / 0x011f33f8)."""
    if RUNTIME.match(un) and not (NEWDEL_ANY.match(un) and not NEWDEL_STD.match(un)) and not decomp_std(un, isig):
        return 'runtime', None, None, None, None
    if isig.kind == 'special':
        return ('native' if un in anydef else 'special-unresolved'), None, None, None, isig.special
    tier, hit = idx.lookup(isig)
    scope = 'unit'
    if not hit and isig.kind == 'func' and isig.qual and isig.qual[-1] == '__vecDelDtor' and len(isig.qual) > 1:
        # MSVC compiles a decomp member named `__vecDelDtor` as the class's vector deleting destructor (`??_E`,
        # thiscall (this, flags) -> void*): GCC's member of that name takes (this in x0, flags in w1) the same way
        # (R2 recon2-005: Date::__vecDelDtor and 9 more stayed trap stubs). The pair is then compared as usual.
        vd = copy.copy(isig)
        vd.qual = isig.qual[:-1] + ['{vdd}']
        tier, hit = idx.lookup(vd)
        if hit and tier not in ('exact', 'l1', 'l2'):
            tier, hit = None, {}
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
        if m and not (m.group(0).startswith('FUN_') and inside_function(int(m.group(1), 16))):
            # Ghidra-style name carrying its address: right for 99.6 % of the names units.json binds (LINK.md);
            # never an address inside another placed function (not a function start)
            return 'addr', 'name-addr', int(m.group(1), 16), None, None
        if ident is not None and isig.kind in ('data', 'cname') and isig.qual and ident.get(isig.qual[-1]):
            a_ = ident[isig.qual[-1]]
            if len(a_) == 1:
                return 'addr', 'gref/ident', next(iter(a_)), None, None
            own = (uident or {}).get(isig.qual[-1], set()) & a_
            if len(own) == 1:
                return 'addr', 'unit/ident', next(iter(own)), None, None
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



def name_addr_sigs(isig, dsig):
    """(reference, definition) Sigs for abi.compare from two Itanium names (S2 s2d-010 2a: a name-addr pair has no
    MSVC declaration on the reference side). Itanium carries neither the convention nor the return type of a
    non-template function, so both sides follow one rule: a function in a class scope is a thiscall member (a
    `fnv_<8 hex>` scope is the decomp's unit-local namespace, not a class), else cdecl; the result is taken as the
    same on both sides. (None, None) when either is not a function."""
    if isig is None or dsig is None or isig.kind != 'func' or dsig.kind != 'func':
        return None, None
    out = []
    for s_ in (isig, dsig):
        n = sigs.Sig('func', s_.name)
        n.qual = list(s_.qual)
        n.raw_params = list(s_.raw_params or [])
        n.params = n.raw_params
        scope = n.qual[:-1]
        n.member = bool(scope) and not re.fullmatch(r'fnv_[0-9a-f]{8}', scope[-1])
        n.cc = 'thiscall' if n.member else 'cdecl'
        n.static = False
        n.raw_ret = None
        n.variant = None
        out.append(n)
    return out[0], out[1]

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
           '    .type __fnvnx_tls_image, %tls_object', '    .size __fnvnx_tls_image, %d' % size, '__fnvnx_tls_image:',
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


def label_is_argument(tree, src_, name, cache):
    """Does the unit's prepared source pass `&name` (a char label) to a call: an argument position, `(` or `,`
    before it, a cast in between allowed? Then the label may be a callback (atexit with an x86 exit thunk, V2 X2)."""
    src_ = src_.split('#')[0]
    if src_ not in cache:
        p = os.path.join(tree, src_)
        cache[src_] = blank_noise(open(p, encoding='utf-8', errors='surrogateescape').read()) \
            if os.path.isfile(p) else ''
    text = cache[src_]
    return re.search(r'[(,]\s*(?:\([^()]*\)\s*)*&\s*%s\b' % re.escape(name), text) is not None


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

    _LAZY = ('s', 'typedefs', 'tags', 'class_body')

    def __init__(self, text):
        # T1: the parse is made on the first find() that the kept answers (find_persist) do not hold
        self._text = text
        self.key = hashlib.sha1(text.encode('utf-8', 'surrogateescape')).hexdigest()

    def __getattr__(self, name):
        if name in CDecls._LAZY:
            self._parse()
            return self.__dict__[name]
        raise AttributeError(name)

    def _parse(self):
        text = self._text
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

    _IDENT = re.compile(r'[A-Za-z_]\w*\Z')
    _CALLS = re.compile(r'(?<![\w.>:])([A-Za-z_]\w*)\s*\(')

    def _sites(self, name):
        """The matches of `(?<![\\w.>:])name\\s*\\(` in text order, as (start, end). For an identifier: one scan of the
        text for every `ident(` (T1: find ran one scan of the whole unit per name, 12 s of resolve under the
        profiler); the same matches, since an identifier match starts and ends at word boundaries."""
        if not self._IDENT.match(name):
            return [(m.start(), m.end()) for m in re.finditer(r'(?<![\w.>:])' + re.escape(name) + r'\s*\(', self.s)]
        idx = getattr(self, '_calls', None)
        if idx is None:
            idx = self._calls = {}
            for m in self._CALLS.finditer(self.s):
                idx.setdefault(m.group(1), []).append((m.start(), m.end()))
        return idx.get(name, [])

    def find(self, name):
        """[(result type, [parameter types])] of the declarations of the free function `name`, in text order."""
        m = _FIND['map']
        if m is None:
            return self._find(name)
        k = self.key + '\0' + name
        _FIND['used'].add(k)
        got = m.get(k)
        if got is None:
            got = m[k] = [(r, list(ps)) for r, ps in self._find(name)]
            _FIND['new'] += 1
        return [(r, list(ps)) for r, ps in got]

    def _find(self, name):
        out, s = [], self.s
        for m_start, m_end in self._sites(name):
            depth, j = 0, m_end - 1
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
            inner = max((b for b in self.class_body if b[0] < m_start < b[1]), default=None)
            if inner is not None and inner[2]:
                continue                      # declared in a class body: a member, not the C function
            k = max(s.rfind(c, 0, m_start) for c in ';{}')
            pre = re.sub(r'^\s*(?:(?:public|private|protected)\s*:(?!:))?', '', s[k + 1:m_start])
            pre = ' '.join(DECL_SPEC.sub(' ', pre).split())
            words = set(re.findall(r'[A-Za-z_]\w*', pre))
            if not pre or not re.match(r'^[A-Za-z_][\w\s\*&:<>,]*$', pre) or words & NOT_A_TYPE or \
                    pre.endswith((':', ',')) or pre.startswith('template'):
                continue                      # a call (`return f(x);`, `a = f(x);`, `case 1: f(x);`), a template
            params = [self.param(x) for x in split_top(s[m_end:j])]
            out.append((self.ctype(pre), [x for x in params if x != 'void']))
        return out


_FIND = {'map': None, 'used': None, 'new': 0, 'path': None}


def find_persist(path):
    """Keep CDecls.find's answers per (text sha1, name) in path for the next link process (resolve 1 and 2, later
    heads), under this file's sha256, pruned to the answers used (T1: CDecls parses and finds took ~15 s of resolve
    under the profiler)."""
    import atexit, marshal
    if _FIND['path']:
        return
    code = hashlib.sha256(open(os.path.abspath(__file__), 'rb').read()).hexdigest()
    try:
        d = marshal.load(open(path, 'rb'))
        m = d['finds'] if d.get('code') == code else {}
    except (OSError, EOFError, ValueError, TypeError, KeyError):
        m = {}
    _FIND.update(map=m, used=set(), new=0, path=path, code=code)

    def save():
        used = _FIND['used']
        if not _FIND['new'] and len(used) == len(_FIND['map']):
            return
        tmp = '%s.%d.tmp' % (path, os.getpid())
        try:
            with open(tmp, 'wb') as f:
                marshal.dump({'code': code, 'finds': {k: _FIND['map'][k] for k in used if k in _FIND['map']}}, f)
            os.replace(tmp, path)
        except (OSError, ValueError):
            pass
    atexit.register(save)


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

def write_addrmap(out, native, def_at, units, vtables=None, extern_text=None, providers=None, cthis=(), adj=None,
                  sdd=None):
    """addrmap.tsv / addrmap.c (docs/LINK.md, 'Address map'): code addresses the native build defines (F C X G),
    image vftables (V: the Itanium vtable address point of the same class, primary or secondary, compiled or
    synthesized), referenced .text addresses no unit defines (E: native NULL, the loader traps them), and per G
    entry its D1/D0 pair (fnvnx_addrmap_deldtors). vtables: {address: (vtable symbol, address-point offset, object
    path, note)}; providers: {address: (platform symbol, provider)} (F entries naming the platform symbol).
    adj: {address: (n, target)} of adjustor_thunks, sdd: {address: (D1, delete)} of nonvirtual_deldtors (F entries
    naming their stub in bridges.S).
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
        if kind == 'C' and a in cthis:   # returns this, as the MSVC constructor does (eax)
            rows.append((a, kind, CTHIS_SYM % a, nd['sym'].name, obj, 'returns this (MSVC constructor result)', '',
                         d1l, d1n))
            continue
        rows.append((a, kind, ADDR_SYM % a, nd['sym'].name, obj, note, '', d1l, d1n))
    for a, (zname, off, obj, note) in sorted((vtables or {}).items()):
        if a not in native:
            rows.append((a, 'V', zname, zname, os.path.basename(obj), note, str(off), '', ''))
    for a, (n, t) in sorted((adj or {}).items()):   # an MSVC adjustor thunk: its stub in bridges.S
        rows.append((a, 'F', ADDR_SYM % a, ADDR_SYM % a, VTEXT_OBJ, 'msvc adjustor thunk: this -= %d, then %08x'
                     % (n, t), '', '', ''))
    for a, (d1, dl) in sorted((sdd or {}).items()):   # a non-virtual ??_G: its stub (takes the flags)
        rows.append((a, 'F', ADDR_SYM % a, ADDR_SYM % a, VTEXT_OBJ, 'msvc ??_G of a non-virtual destructor: %s, '
                     'then %s if flags & 1' % (d1, dl), '', '', ''))
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
            if r.returncode and '--rename-section=' in open(argfile).read():
                # the m1 section renames (m1_compiled_renames) are only a placement: an objcopy they break (a second
                # rename of one section, an --add-symbol naming the old section) must not drop the whole unit
                log('objcopy failed for %s with the m1 renames, retried without: %s' % (base, r.stderr.strip()[:300]))
                kept = [a for a in open(argfile).read().splitlines() if not a.startswith('--rename-section=')]
                with open(argfile + '.nom1', 'w', encoding='utf-8', errors='surrogateescape') as f:
                    f.write('\n'.join(kept) + '\n')
                r = subprocess.run([OBJCOPY, '@' + argfile + '.nom1', obj, dst], capture_output=True, text=True)
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
    sk, sn, sb = slotfill2.shift_fixed(out, fixed)   # S2c-vt2: references into shifted secondary groups
    print('shifted secondary vtable groups: %d patches in %d objects, %d objects failed' % (sn, sk, sb))
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
    vtext = {}
    vt_path = os.path.join(out, 'vtext.tsv')
    if os.path.exists(vt_path):   # S2c-vt: extended vtables (bridges.S) stand for the object their words came from
        for line in open(vt_path, encoding='utf-8', errors='surrogateescape'):
            f_ = line.rstrip('\n').split('\t')
            if not line.startswith('#') and len(f_) >= 2:
                vtext[f_[0][4:]] = f_[1]
    rows, cnt = pair_typeinfo(fixed, vtext)
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


def pair_typeinfo(fixed, vtext=None):
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
        if vtext and cls in vtext and kv == VTEXT_OBJ:
            kv = vtext[cls]
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
            if x in ('--override', '--override-lib', '--extern-text', '--imports', '--units-renames',
                     '--exe'):
                opts[x[2:].replace('-', '_')] = next(it_)
            else:
                argv.append(x)
        resolve(*argv[:3], override=opts.get('override'), extern_list=opts.get('extern_text'),
                imports=opts.get('imports'),
                units_renames=opts.get('units_renames'), override_lib=opts.get('override_lib'), exe=opts.get('exe'))
    elif cmd in ('probe', 'vtables'):
        jobs = int(sys.argv[4]) if len(sys.argv) > 4 else os.cpu_count()
        if cmd == 'probe':
            layout.run(sys.argv[2], sys.argv[3], jobs)
            vacall.run(sys.argv[2], sys.argv[3], jobs)
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
