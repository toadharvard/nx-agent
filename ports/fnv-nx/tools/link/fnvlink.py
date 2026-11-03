#!/usr/bin/env python3
"""fnvlink.py - link the decomp's units by original address (ARCHITECTURE.md D4; docs/LINK.md).

    fnvlink.py resolve <units.json> <objects.tsv> <out dir> [--override <tsv>] [--extern-text <list>]
                                                              # mapping, address map, ABI report, bridges
    fnvlink.py probe   <out dir> <prepared tree> [jobs]        # layouts of the aggregates resolve asked for;
                                                              # then resolve again to use them
    fnvlink.py apply   <out dir> <fixed dir>                   # objcopy every object with its generated args
    fnvlink.py check   <out dir> <fixed dir>                   # trial link: what is still undefined / duplicated

objects.tsv: source<TAB>object path<TAB>ok|fail (tools/link/compile.sh writes it); only 'ok' rows are read.
Everything written to <out dir> is derived from the decomp: local output only, never committed.
"""
import collections
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import abi      # noqa: E402
import abi2     # noqa: E402
import elfsyms  # noqa: E402
import layout   # noqa: E402
import sigs     # noqa: E402

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
ADDR_IN_NAME = re.compile(r'^(?:DAT|FUN|g|UNK|PTR)_([0-9a-f]{8})$')


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
    __slots__ = ('source', 'defs', 'refs', 'obj')

    def __init__(self, source):
        self.source, self.defs, self.refs, self.obj = source, [], {}, None


def load_units(path):
    """units.json -> {source: Unit}; sources listed twice (built with two flag sets) are merged."""
    units = {}
    for u in json.load(open(path))['units']:
        unit = units.setdefault(u['source'], Unit(u['source']))
        for fn in u['functions']:
            a = int(fn['address'], 16)
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


def resolve(units_json, objects_tsv, out, override=None, extern_list=None):
    os.makedirs(os.path.join(out, 'cache'), exist_ok=True)
    os.makedirs(os.path.join(out, 'objcopy'), exist_ok=True)
    units = load_units(units_json)
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
    ms.need(n for n in allms if n.startswith('?'))
    msig = {}

    def msvc(n):
        s = msig.get(n)
        if s is None:
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
    stats = collections.Counter()
    byclass = collections.Counter()
    bridges, abi_rows, abi_classes = {}, [], collections.Counter()
    pair_classes = collections.Counter()
    extern_text = collections.defaultdict(set)
    per_obj = {}
    used_addr = set()
    data_defs = []
    bound = collections.defaultdict(set)  # unit -> addresses its object references by address
    for u in have:
        idx = KeyIndex()
        for n, a in u.refs.items():
            idx.add(msvc(n), a)
        for a, n in u.defs:
            idx.add(msvc(n), a)
        # variables this object defines at a data address: the data image holds the same object (docs/LINK.md)
        for sym in objsyms[u.source]:
            if sym.shndx != 0 and sym.type == 'object' and sym.bind != 'local' and not sym.name.startswith('_ZT'):
                t, hit = idx.lookup(itanium(sym.name))
                if len(hit) == 1 and kind_of_addr(next(iter(hit))) == 'data':
                    data_defs.append((next(iter(hit)), sym.name, u.source))
        renames = {}
        undefs = sorted({s.name for s in objsyms[u.source] if s.shndx == 0})
        vslots = elfsyms.vtable_slot_refs(u.obj)
        for un in undefs:
            stats['refs'] += 1
            isig = itanium(un)
            cls, tier, addr, target, note = classify_ref(un, isig, idx, gdef, gref, anydef, msvc)
            if addr is not None and cls == 'addr':
                ak = kind_of_addr(addr)
                if ak == 'data':
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
                                if p2 is not None and (v.kind != 'bridge' or p2.kind != 'bridge'):
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
            if cls == 'unresolved' and un in vslots:
                cls = 'vslot-unresolved'
            elif cls == 'unresolved' and isig.variant:
                cls = 'unresolved-ctor-dtor'
            elif cls == 'special-unresolved':
                cls = 'special-unresolved:' + (note or '?').replace(' ', '-')
            if un in vslots:
                stats['vslot_refs'] += 1
            if target and target != un:
                renames[un] = target
            byclass[cls] += 1
            refs_out.write('%s\t%s\t%s\t%s\t%s\t%s\t%s\n' % (u.source, un, cls, tier or '', '%08x' % addr if addr
                           is not None else '', target or '', note or ''))
        per_obj[u.source] = renames
    refs_out.close()

    # ---- per object objcopy args: renames, address symbols for the definitions it holds, localize duplicates
    alias_lines, localize = [], collections.defaultdict(list)
    for name, srcs in strong.items():
        if len(srcs) > 1:
            for s in sorted(srcs)[1:]:
                localize[s].append(name)
    stats['duplicate_strong_names'] = sum(1 for s in strong.values() if len(s) > 1)
    data_def_names = collections.defaultdict(dict)
    for a, name, src in data_defs:
        if a not in data_def_names[src].values():  # objcopy allows one old name per new name
            data_def_names[src][name] = a
            used_addr.add(a)
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
        inl = sorted(src for src, uu in units.items() if uu.obj and src != old['unit'] and a in set(uu.refs.values())
                     and a not in bound[src])
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
            if name not in dd:
                args.append('--localize-symbol=' + name)
        for name, a in sorted(dd.items()):
            # a variable this unit defines lives in the data image: the definition becomes weak and is renamed to
            # the address symbol, which aliases.ld defines at the original address (the native static
            # initializer then constructs the object in place, as the original CRT init did)
            args.append('--redefine-sym=%s=%s' % (name, ADDR_SYM % a))
            args.append('--weaken-symbol=' + ADDR_SYM % a)  # objcopy weakens after renaming
        with open(os.path.join(out, 'objcopy', base + '.args'), 'w', encoding='utf-8', errors='surrogateescape') as f:
            f.write('\n'.join(args) + '\n')
        with open(os.path.join(out, 'objcopy', base + '.src'), 'w') as f:
            f.write(u.obj + '\n')

    # ---- data addresses: absolute symbols at the original address (the data image is mapped there, D3)
    with open(os.path.join(out, 'aliases.ld'), 'w') as f:
        f.write('/* fnv-nx link-by-address aliases (generated by tools/link/fnvlink.py; local output) */\n')
        for a in sorted(x for x in used_addr if kind_of_addr(x) == 'data'):
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
    with open(os.path.join(out, 'abi.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#object\tundefined\taddress\tref_msvc\tdef_msvc\tverdict\tclasses\tmoves\tnotes\tref\tdef\n')
        for r in abi_rows:
            f.write('\t'.join(r) + '\n')

    with open(os.path.join(out, 'probe_requests.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        for unit, t in sorted(probe_req):
            f.write('%s\t%s\n' % (unit, t))

    # ---- address map
    # image vftables -> Itanium vtable address point of the class of the same name (single-inheritance
    # vftables only; '{for ...}' secondary vftables and classes without an emitted _ZTV are left out)
    ztv = {}
    for u in have:
        for sym in objsyms[u.source]:
            if sym.name.startswith('_ZTV') and sym.shndx and sym.bind != 'local':
                isg = itanium(sym.name)
                if isg.special == 'vtable for':
                    ztv.setdefault(isg.name, (sym.name, os.path.basename(u.obj)))
    vtables = {}
    for u in units.values():
        for n, a in u.refs.items():
            d = ms[n] if n.startswith('??_7') else None
            if d and d.endswith("::`vftable'") and '{for' not in d:
                cls = '::'.join(sigs.canon_qual(sigs.split_qual(d[:-len("::`vftable'")].replace('const ', '', 1))))
                if cls in ztv:
                    vtables.setdefault(a, ztv[cls])
    for a, names in load_extern_list(extern_list).items():
        extern_text.setdefault(a, set()).update(names)
    amap_kinds = write_addrmap(out, native, def_at, units, vtables, extern_text)

    # ---- stats
    resolved = sum(byclass[c] for c in ('code', 'code-bridge', 'code-manual', 'data', 'code-pending',
                                         'code-defmissing', 'extern-text', 'import'))
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
    lines += ['', 'variables defined natively at a data-image address: %d' % len(data_defs),
              'address map entries by kind: %s' % ' '.join('%s=%d' % kv for kv in sorted(amap_kinds.items()))]
    lines += ['', 'definitions matched to native symbols:']
    lines += ['  %-18s %d' % (c, n) for c, n in sorted(def_stats.items())]
    lines += ['', 'duplicate strong names localized: %d' % stats['duplicate_strong_names'],
              'bridges generated: %d' % len(bridges),
              'aggregate layouts known: %d (probe_requests.tsv: %d)' % (len(layouts), len(probe_req)), '',
              'byte-level re-decisions with layouts (class-level verdict -> byte-level verdict, refs):']
    lines += ['  %-6d %s' % (n, k[5:]) for k, n in sorted(stats.items()) if k.startswith('abi2_')]
    lines += ['', 'ABI verdicts (kind, classes) over references:']
    lines += ['  %-6d %s %s' % (n, k, c) for (k, c), n in abi_classes.most_common()]
    lines += ['', 'declaration-shape differences (all resolved code references):']
    lines += ['  %-6d %s' % (n, c) for c, n in pair_classes.most_common()]
    open(os.path.join(out, 'stats.txt'), 'w').write('\n'.join(lines) + '\n')
    print('\n'.join(lines))


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
    if RUNTIME.match(un):
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


# ---------------------------------------------------------------------------------------------------- address map

def write_addrmap(out, native, def_at, units, vtables=None, extern_text=None):
    """addrmap.tsv / addrmap.c (docs/LINK.md, 'Address map'): code addresses the native build defines (F C X G),
    image vftables (V: the Itanium vtable address point of the same class) and referenced .text addresses no unit
    defines (E: native NULL, the loader traps them)."""
    rows = []
    for a in sorted(native):
        nd = native[a]
        v = nd['msvc']
        kind, note = 'F', ''
        if '??_G' in v or '??_E' in v:
            kind, note = 'G', 'msvc deleting dtor (takes flags) -> Itanium D0 (always deletes)'
        elif v.startswith('??0'):
            kind = 'C'
        elif v.startswith('??1'):
            kind = 'X'
        elif nd['sym'].bind == 'local':
            note = 'static in its unit'
        if nd['unit'] is None:
            kind, note = 'F', 'override of ' + (nd['msvc'] or '?')
            obj = os.path.basename(nd['override'])
        else:
            obj = os.path.basename(units[nd['unit']].obj)
        rows.append((a, kind, ADDR_SYM % a, nd['sym'].name, obj, note))
    for a, (zname, obj) in sorted((vtables or {}).items()):
        if a not in native:
            rows.append((a, 'V', zname, zname, obj, 'address point = symbol + 8 (ILP32)'))
    for a in sorted(extern_text or {}):
        if a not in native:
            rows.append((a, 'E', '', '', '', 'no native definition (CRT/thunk, S4)'))
    rows.sort()
    with open(os.path.join(out, 'addrmap.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('# fnvnx-addrmap 1\n# address\tkind\tlink_symbol\tnative_symbol\tobject\tnote\n')
        for r in rows:
            f.write('%08x\t%s\t%s\t%s\t%s\t%s\n' % r)
    with open(os.path.join(out, 'addrmap.c'), 'w') as f:
        f.write('/* fnv-nx address map table (generated by tools/link/fnvlink.py; local output). */\n'
                '#include "fnvnx_addrmap.h"\n\n')
        for r in rows:
            if r[2]:
                f.write('extern char %s[];\n' % r[2])
        f.write('\nconst struct fnvnx_addrmap_entry fnvnx_addrmap[] = {\n')
        for a, kind, link, *_ in rows:
            ptr = '0' if not link else ('%s + 8' % link if kind == 'V' else link)
            f.write('    {0x%08xu, %s},\n' % (a, ptr))
        f.write('};\nconst char fnvnx_addrmap_kinds[] = "%s";\n' % ''.join(r[1] for r in rows))
        f.write('const unsigned fnvnx_addrmap_count = %d;\n' % len(rows))
    return collections.Counter(r[1] for r in rows)


# ---------------------------------------------------------------------------------------------------- apply / check

def apply(out, fixed):
    os.makedirs(fixed, exist_ok=True)
    n = 0
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
        elfsyms.rename_undefined(obj, dst, mapping)
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
    if cmd == 'resolve':
        argv, opts = [], {}
        it_ = iter(sys.argv[2:])
        for x in it_:
            if x in ('--override', '--extern-text'):
                opts[x[2:].replace('-', '_')] = next(it_)
            else:
                argv.append(x)
        resolve(*argv[:3], override=opts.get('override'), extern_list=opts.get('extern_text'))
        with open(os.path.join(argv[2], 'overrides.list'), 'w') as f:
            for _, _, obj in load_overrides(opts.get('override')):
                f.write(obj + '\n')
    elif cmd == 'probe':
        layout.run(sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else os.cpu_count())
    elif cmd == 'apply':
        apply(*sys.argv[2:4])
    elif cmd == 'check':
        check(*sys.argv[2:4])
    else:
        print(__doc__); sys.exit(2)


if __name__ == '__main__':
    main()
