"""engine.py - build a runner.Target for one original address; per-unit caches.

Per unit: the oracle Coff + UnitImage (x86 code, recovered literals) and the linked AArch64 Image. Pinned world bytes
are the unit's literals (strings, `__real@` constants, read-only tables at mapped addresses). The named-stub map gives
the original address behind a stub symbol when the name carries it (`__fnv_<addr>_C2`, ...). callee_info(addr) gives
an intercepted callee's x86 stack cleanup and result register from its definition's decorated name (any unit), else
from the calling unit's own declaration of that address.
"""
import collections
import json
import os
import re

import config
import coffobj
import a64image
import conv as convmod
import runner
import libcalls

STUB_ADDR = re.compile(r'^__fnv(?:nx_br)?_([0-9a-f]{8})')
MSVC_VFT = re.compile(r'^\?\?_7((?:[A-Za-z_]\w*@)+)@6B@$')


def itanium_class(m):
    """'12FUN_00404920' -> 'FUN_00404920', 'N3Foo3BarE' -> 'Foo::Bar' (plain names only, else None)."""
    parts, i = [], 0
    nested = m.startswith('N')
    if nested:
        i = 1
    while i < len(m) and m[i].isdigit():
        j = i
        while m[j].isdigit():
            j += 1
        n = int(m[i:j])
        parts.append(m[j:j + n])
        i = j + n
        if not nested:
            break
    rest = m[i:]
    if not parts or (nested and rest != 'E') or (not nested and rest):
        return None
    return '::'.join(parts)


LOCAL_STATIC = re.compile(r'^\?(?:[^@?]+@\?\d+\?\?|\$S\d+@|\$TSS\d+@)')   # ?x@?1??f@@..., ?$S1@..., ?$TSS0@...
# ?<var>@?<n>?<decorated function>@4<type>: a function-local static (value); ?$S<n>@ / ?$TSS<n>@: MSVC's guard word
LOCAL_VALUE = re.compile(r'^\?([^@?$][^@?]*)@\?\d+\?(\?.+)@4[^@]*@?A$')
LOCAL_GUARD = re.compile(r'^\?\$(?:S|TSS)\d+@\?\d+\?(\?.+)@4IA$')


def dyninit_conv(symbol, source):
    """`??__E...` / `??__F...` (dynamic initializer / atexit destructor; XC1's __fnvnx_dyninit_/_dynexit_):
    void __cdecl (void)."""
    if symbol.startswith(('??__E', '??__F', '__fnvnx_dyn')):
        return convmod.Conv('?fnvnx_s13_init@@YAXXZ', source)
    return None


def apply_renames(tree_def, rows):
    """S1b's units_renames rows (J-0141) on top of the tree's definitions {address: MSVC name}, for a tree whose
    units.json prepare did not rewrite (a tree prepared before the POST hook, a local FNVNX_TREE): a `def` row gives
    the patched definition its new name (0x00eacd00 `bool __stdcall f(void*)` -> a member: ecx = this on the oracle
    too), unless the tree names another one. Returns {(source, address): new name} of the `ref` rows: the patched
    caller's own declaration of a callee (caller_conv). Idempotent on a rewritten units.json."""
    tree_ref = {}
    for src, kind, a, old, new in rows:
        a = int(a, 16)
        if kind == 'def' and tree_def.get(a, old) in (old, new):
            tree_def[a] = new
        elif kind == 'ref':
            tree_ref.setdefault((src, a), new)
    return tree_ref


class UnitCache:
    def __init__(self, linkdir, workdir, unit_closed=True):
        self.linkdir, self.workdir, self.unit_closed = linkdir, workdir, unit_closed
        self.units = json.load(open(config.DECOMP / 'build/units.json'))['units']
        if config.ORACLE == 'exe':      # the prepared tree's extents: REC1's recon functions are there, not in pristine
            self.units = json.load(open(config.TREE / 'build/units.json'))['units']
        self.by_source = {}             # source -> [(index, unit)] (a source can be listed with two flag sets)
        self.def_at = {}
        for i, u in enumerate(self.units):
            self.by_source.setdefault(u['source'], []).append((i, u))
            for f in u['functions']:
                self.def_at.setdefault(int(f['address'], 16), (u['source'], f['symbol']))
        # the candidate tree's declarations: per-site patches (S1b XINC1) correct a definition's signature to what
        # the binary really does (e.g. a `void f(void)` that reads ecx = this); that corrected declaration places the
        # arguments on BOTH machines
        self.tree_def = {}
        tj = config.TREE / 'build/units.json'
        if tj.exists():
            for u in json.load(open(tj))['units']:
                for f in u['functions']:
                    self.tree_def.setdefault(int(f['address'], 16), f['symbol'])
        self.tree_ref = apply_renames(self.tree_def, config.units_renames())
        self._x86, self._img, self._pin, self._callee = {}, {}, {}, {}
        self._refmap = None
        self._imports = self._iat = None
        self._abi = None
        self._statics = None
        self._vrows = None
        self._vft_by_class = None

    def x86_unit(self, source, addr=None):
        """The oracle unit of `source` that defines `addr` (the first listing when addr is None)."""
        entries = self.by_source[source]
        if addr is not None:
            entries = [e for e in entries if any(int(f['address'], 16) == addr for f in e[1]['functions'])] or entries
        i, u = entries[0]
        if i not in self._x86:
            if config.ORACLE == 'exe':
                import exeimage
                img = exeimage.ExeImage(exeimage.load_pe(config.EXE, not os.environ.get('FNVNX_EXE_ANY')), u)
            else:
                img = coffobj.UnitImage(coffobj.Coff(config.X86OBJ / ('u%d.obj' % i)), u)
            self._x86[i] = img
            self._pin[i] = img.literal_bytes()
        return self._x86[i]

    def arm_image(self, source):
        if source not in self._img:
            self._img[source] = a64image.build(source, self.linkdir, self.workdir)
        return self._img[source]

    def _refs(self, source):
        """{undefined name: original address} of the unit object (S2's refs.tsv), for names S2 kept (CRT addresses
        without a definition, imports)."""
        if self._refmap is None:
            self._refmap = {}
            path = config.LINK / 'out/refs.tsv'
            for line in open(path, encoding='utf-8', errors='replace'):
                if line.startswith('#'):
                    continue
                f = line.rstrip('\n').split('\t')
                if len(f) >= 5 and f[4] and f[2] in ('extern-text', 'import', 'code', 'code-bridge', 'code-manual',
                                                     'code-pending', 'code-defmissing'):
                    try:
                        self._refmap.setdefault(f[0], {})[f[1]] = int(f[4], 16)
                    except ValueError:
                        pass
        return self._refmap.get(source, {})

    def stub_addrmap(self, image, source):
        refs = self._refs(source)
        out = {}
        for addr, name in image.stubs.items():
            m = STUB_ADDR.match(name)
            if m:
                out[addr] = int(m.group(1), 16)
            elif name in refs:
                out[addr] = refs[name]
            elif name in libcalls.EQUIV:
                out[addr] = libcalls.EQUIV[name]
            elif self._import_names().get(libcalls.import_name(name)[0]):
                out[addr] = libcalls.import_id(libcalls.import_name(name)[0])
            elif name in self.providers():
                out[addr] = self.providers()[name]     # a CRT / library address the link gives a native provider
        return out

    def oracle_ret_pop(self, addr):
        """N of the `ret N` instructions in the oracle code of `addr` (0 for a plain `ret`), None when the code is
        unknown or its returns disagree."""
        cache = self.__dict__.setdefault('_retpop', {})
        if addr in cache:
            return cache[addr]
        got = None
        if addr in self.def_at and addr not in libcalls.X86:
            try:
                f = self.x86_unit(self.def_at[addr][0], addr).funcs.get(addr)
            except Exception:  # noqa: BLE001
                f = None
            if f:
                import capstone
                cs = self.__dict__.setdefault('_cs', capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32))
                pops = set()
                for ins in cs.disasm(f['code'], addr):
                    if ins.mnemonic == 'ret':
                        pops.add(int(ins.op_str, 0) if ins.op_str else 0)
                if len(pops) == 1:
                    got = pops.pop()
        cache[addr] = got
        return got

    def providers(self):
        """{native name: original address} of the link's provider rows (addrmap kind F, `provider: ...`): the CRT
        and library functions the port implements natively (free, _invalid_parameter_noinfo, fnvnx_rt_dynamic_cast)."""
        if getattr(self, '_prov', None) is None:
            self._prov, self._prov_rows = {}, []
            p = config.LINK / 'out/addrmap.tsv'
            if p.exists():
                for line in open(p, encoding='utf-8', errors='replace'):
                    f = line.rstrip('\n').split('\t')
                    if len(f) > 5 and f[1] == 'F' and f[5].startswith('provider:') and f[2]:
                        try:
                            self._prov.setdefault(f[2], int(f[0], 16))
                            self._prov_rows.append((f[2], int(f[0], 16)))
                        except ValueError:
                            pass
        return self._prov

    def iat_names(self):
        """{IAT slot address: import name}: the native code calls an import through its slot (S2's IAT binding)."""
        self.import_stubs()
        return {ad: self._imports[(stub - libcalls.IMPORT_BASE) // 16][0] for ad, stub in self._iat.items()}

    def oracle_callees(self, addr):
        """Call targets in the oracle code of `addr` (any unit): original addresses, and import identities for calls
        through IAT slots. Empty when the code is unknown."""
        if addr not in self.def_at:
            return set()
        try:
            f = self.x86_unit(self.def_at[addr][0], addr).funcs.get(addr)
        except Exception:  # noqa: BLE001
            return set()
        out, iat = set(), self.iat_names()
        for rel, kind, tg, _ in (f or {}).get('relocs', ()):
            if tg is None:
                continue
            if kind == 0x14:
                out.add(tg)
            elif tg in iat:
                out.add(libcalls.import_id(iat[tg]))
        return out

    def callee_info_for(self, source):
        refs = {}
        for _, u in self.by_source[source]:
            for n, a in u['symbols'].items():
                # a decorated spelling first: it carries the type (`_FUN_00eca920` next to ?FUN_00eca920@@YANN@Z
                # left the double result of CRT log unknown, so the oracle got no ST0)
                cur = refs.get(int(a, 16))
                if cur is None or (n.startswith('?') and not cur.startswith('?')):
                    refs[int(a, 16)] = n
        for (src, a), n in self.tree_ref.items():     # the patched unit's own declaration (S1b `ref` rows)
            if src == source:
                refs[a] = n

        def conv_of(addr):
            key = (source, addr)
            if key not in self._callee:
                got = None
                dsrc = (self.def_at.get(addr) or (source, None))[0]
                for src, name in ((dsrc, self.tree_def.get(addr)), (self.def_at.get(addr) or (None, None)),
                                  (source, refs.get(addr))):
                    if not name:
                        continue
                    try:
                        got = convmod.Conv(name, src)
                    except Exception:  # noqa: BLE001
                        continue
                    try:                       # the callee's own custom register convention, when its code is known
                        import classify
                        f = self.x86_unit(dsrc, addr).funcs.get(addr) if dsrc else None
                        regs = classify.entry_register_reads(f, got) if f else []
                        if regs:
                            got.with_registers(regs)
                    except Exception:  # noqa: BLE001
                        pass
                    break
                self._callee[key] = got
            return self._callee[key]

        def info(addr):
            cv = conv_of(addr)
            pop = self.oracle_ret_pop(addr)
            if cv is None:
                return (pop, 'int') if pop is not None else None
            kind = 'int'
            if cv.x86_ret == 'st0':
                kind = 'f32' if cv.ret.size == 4 else 'f64'
            # the stack bytes the callee really pops, read from its oracle code (`ret N`), win over the declaration:
            # a decomp declaration with a parameter too few or too many left esp off by 4 after the intercepted
            # call (the oracle then returned to garbage: fault:x:00000000)
            return (pop if pop is not None else cv.pop, kind)
        def caller_conv_of(addr):
            """The calling unit's own declaration of the callee (its MSVC symbol for that address)."""
            key = ('caller', source, addr)
            if key not in self._callee:
                got = None
                if refs.get(addr):
                    try:
                        got = convmod.Conv(refs[addr], source)
                    except Exception:  # noqa: BLE001
                        got = None
                self._callee[key] = got
            return self._callee[key]
        info.conv = conv_of
        info.caller_conv = caller_conv_of
        return info

    def target(self, source, orig_addr):
        xu = self.x86_unit(source, orig_addr)
        if orig_addr not in xu.funcs:
            raise KeyError('no x86 function at %08x in %s' % (orig_addr, source))
        fn = xu.funcs[orig_addr]
        if fn['unresolved']:
            raise ValueError('unresolved x86 relocations: %s' % ','.join(n for _, _, n in fn['unresolved'][:4]))
        name = self.tree_def.get(orig_addr, fn['symbol'])
        try:
            cv = convmod.Conv(name, source)
        except convmod.Unsupported:
            cv = dyninit_conv(fn['symbol'], source) or convmod.Conv(fn['symbol'], source)
        import classify
        custom = classify.entry_register_reads(fn, cv)
        if custom:
            cv.with_registers(custom)      # MSVC custom register convention (oracle placement only)
        img = self.arm_image(source)
        if not any(o == orig_addr for o in img.entries.values()):
            raise KeyError('no native entry for %08x' % orig_addr)
        statics = self.local_statics()
        code = {orig_addr: fn['code']}
        if self.unit_closed:
            # a function of the unit that the native object does not define and calls through its code stub
            # (`__fnv_<X>` bound to CODE_STUB + X: defined in another object, e.g. moved by S1b) runs out of line
            # natively, so the oracle calls it out of line too (an intercepted call on both machines)
            native_def = set(img.entries.values())
            external = {a for a in xu.funcs if a not in native_def and
                        img.syms.get('__fnv_%08x' % a) == a64image.CODE_STUB + a}
            for a, f in xu.funcs.items():
                if not f['unresolved'] and a not in external:
                    code[a] = f['code']
        pairs, guards = self.static_pairs(img)
        paired = {o + k for _, o, n in pairs for k in range(n)}
        t = runner.Target(cv, orig_addr, code, img, self.stub_addrmap(img, source), self.callee_info_for(source),
                          statics - paired - guards)
        t.static_pairs, t.static_guards = pairs, guards
        t.extra_pinned = dict(getattr(t, 'extra_pinned', {}) or {})
        for _, o, n in pairs:
            t.extra_pinned[o] = bytes(n)
        for g in guards:
            t.extra_pinned[g] = b'\0'
        t.source = source
        t.imports = self.import_stubs()
        t.iat_names = self.iat_names()
        t.oracle_callees = self.oracle_callees
        t.custom_cc = list(getattr(cv, 'custom', []) or [])
        t.vtables = self.vtable_map(img)
        self.plant_model(t, cv, img, orig_addr)
        t.followed = self.preload(code, img, source)
        t.x86_code = code

        def oracle_code(addr):
            """(code, literals of its unit) of an oracle function, or None."""
            if addr in libcalls.X86 or addr not in self.def_at:
                return None
            try:
                xu2 = self.x86_unit(self.def_at[addr][0], addr)
            except Exception:  # noqa: BLE001
                return None
            f = xu2.funcs.get(addr)
            return (f['code'], xu2.literal_bytes()) if f and not f['unresolved'] else None
        t.oracle_code = oracle_code
        dum = self.edx_dummies(source)
        t.dummies = lambda addr: dum.get(addr, ())
        # literals of every unit that contributed preloaded code (beside the zeroed local statics and guards)
        for xu2 in list(self._x86.values()):
            if xu2 is not xu and any(a in code for a in xu2.funcs):
                t.extra_pinned.update(xu2.literal_bytes())
        return t

    def vt_model(self):
        """The vtable model (vtmodel.Model) of this link, or None without the player's exe (FNVNX_EXE)."""
        if getattr(self, '_vtm', False) is False:
            import vtmodel
            self._vtm = vtmodel.Model(str(config.LINK), str(config.WORK / 'vt'), self.units) \
                if vtmodel.exe_path() else None
        return self._vtm

    def plant_model(self, t, cv, img, orig_addr):
        """What the fuzzer plants for the target (difffuzz-13): t.vt_cands {label: [image vftable]} for `this` and
        class pointer parameters, t.vt_ap {V: native address point in this image}, t.vt_mem (synthesized native
        vtable groups), t.vt_pins {V: the exe's slot words}, t.vt_slots (every planted slot's original address)."""
        t.vt_cands, t.vt_ap, t.vt_mem, t.vt_pins, t.vt_slots = {}, {}, {}, {}, set()
        m = self.vt_model()
        if m is None:
            return
        t.exe_vfts = m.exe.vft.keys()        # every RTTI vftable of the exe (runner: destructor vptr words)
        import vtmodel
        cands = {}
        for lab, cls in vtmodel.arg_classes(cv).items():
            vs = m.candidates(cls)
            if lab == 'this':
                vs += [v for v in m.for_function(orig_addr) if v not in vs]
            if vs:
                cands[lab] = vs
        if 'this' not in cands and any(v.label == 'this' for v in cv.vals):
            vs = m.for_function(orig_addr)
            if vs:
                cands['this'] = vs
        allv = sorted({v for vs in cands.values() for v in vs})
        if not allv:
            return
        ap, mem, stubs = m.native(allv, img, t.stub_orig)
        img.stubs.update(stubs)
        cands = {lab: [v for v in vs if v in ap] for lab, vs in cands.items()}
        t.vt_cands = {lab: vs for lab, vs in cands.items() if vs}
        used = sorted({v for vs in t.vt_cands.values() for v in vs})
        t.vt_ap = {v: ap[v] for v in used}
        t.vt_mem = mem
        t.vt_pins = m.x86_pins(used)
        t.vt_slots = {a for v in used for a in m.slots(v)}
        for v, a in t.vt_ap.items():
            t.vtables.setdefault(a & 0xffffffff, v)

    def import_stubs(self):
        """{stub index: (name, stdcall bytes)} for every IAT slot units.json names (one name per slot: the most
        common); the slots are pinned to x86 import stubs (iat_pins)."""
        if self._imports is None:
            names = collections.defaultdict(collections.Counter)
            for u in self.units:
                for n, a in u['symbols'].items():
                    if n.startswith('__imp_'):
                        ad = int(a, 16)
                        if 0x00fdf000 <= ad < 0x00fe0000:
                            names[ad][libcalls.import_name(n)] += 1
            self._iat = {}
            self._imports = {}
            for k, ad in enumerate(sorted(names)):
                if k >= libcalls.IMPORT_MAX:
                    break
                nm = names[ad].most_common(1)[0][0]
                self._imports[k] = nm
                self._iat[ad] = libcalls.IMPORT_BASE + 16 * k
                runner.IMPORTS[libcalls.import_id(nm[0])] = nm[0]
        return self._imports

    def _import_names(self):
        self.import_stubs()
        return {nm[0]: True for nm in self._imports.values()}

    def iat_pins(self):
        import struct
        self.import_stubs()
        return {ad: struct.pack('<I', stub) for ad, stub in self._iat.items()}

    def edx_dummies(self, source):
        """{callee address: definition labels the unit's references do not pass} from S2's abi.tsv notes
        (`pN: edx dummy not passed`): an fastcall edx dummy is junk on both machines, never compared."""
        if self._abi is None:
            self._abi = collections.defaultdict(lambda: collections.defaultdict(set))
            p = config.LINK / 'out/abi.tsv'
            if p.exists():
                for line in open(p, encoding='utf-8', errors='replace'):
                    f = line.rstrip('\n').split('\t')
                    if len(f) > 8 and f[2]:
                        for m in re.finditer(r'\b(p\d+): edx dummy not passed', f[8]):
                            try:
                                self._abi[f[0]][int(f[2], 16)].add(m.group(1))
                            except ValueError:
                                pass
        return self._abi.get(source, {})

    def static_pairs(self, img):
        """(pairs [(native address, oracle address, size)], oracle guard byte addresses) for the function-local
        statics of the functions this unit image defines (difffuzz-17). MSVC keeps a local static at an image
        address with a guard bit word, GCC keeps `_ZZ<function>E<len><name>` in the unit's .bss with its own guard
        byte: a value pairs by its function (the native names at the function's entry) and name; both start zero
        (the oracle's value and guard are pinned to 0, as GCC's .bss starts), the native stores are compared at the
        oracle address, and the guards (an implementation detail on both sides) are never compared."""
        key = img.elf
        if getattr(self, '_static_pairs_key', None) == key:
            return self._static_pairs_val
        rev = {}
        for n, a in img.syms.items():
            rev.setdefault(a, []).append(n)
        fn_names = {}
        for na, orig in img.entries.items():
            fn_names[orig] = [n for n in rev.get(na, ()) if n.startswith('_Z')]
        if getattr(self, '_static_names', None) is None:
            faddr, rows = {}, {}
            for u in self.units:
                for f in u.get('functions') or ():
                    faddr.setdefault(f['symbol'], int(f['address'], 16))
                for n, a in u['symbols'].items():
                    if n.startswith('?'):
                        faddr.setdefault(n, int(a, 16))
                    if LOCAL_GUARD.match(n) or LOCAL_VALUE.match(n):
                        rows.setdefault(n, a)
            self._static_names = (faddr, rows)
        faddr, rows = self._static_names
        pairs, guards = [], set()
        for n, a in rows.items():
            if True:
                g = LOCAL_GUARD.match(n)
                if g and faddr.get(g.group(1)) in fn_names:
                    guards.update(range(int(a, 16), int(a, 16) + 4))
                    continue
                m = LOCAL_VALUE.match(n)
                if not m:
                    continue
                var, func = m.group(1), m.group(2)
                fa = faddr.get(func)
                for nn in fn_names.get(fa, ()):
                    base = '_ZZ' + nn[2:] + 'E' + str(len(var)) + var
                    cand = [x for x in (base, base + '_0') if x in img.syms]
                    if cand and img.sizes.get(cand[0]):
                        pairs.append((img.syms[cand[0]], int(a, 16), img.sizes[cand[0]]))
                        break
        self._static_pairs_key, self._static_pairs_val = key, (pairs, guards)
        return pairs, guards

    def local_statics(self):
        """Byte addresses of every function-local static and guard any unit names (image-wide: followed callees of
        other units have them too). MSVC keeps them in the image, GCC in the unit's .bss."""
        if self._statics is None:
            st = set()
            for u in self.units:
                for n, a in u['symbols'].items():
                    if LOCAL_STATIC.match(n):
                        st.update(range(int(a, 16), int(a, 16) + 16))
            self._statics = st
        return self._statics

    def preload(self, code, img, source):
        """Add to `code` (transitively) every oracle callee outside it that the native image never names: GCC
        inlined it (typically a header inline function MSVC called out of line), so the oracle runs its copy."""
        called = self.arm_called(img, source)
        own = set(code)

        def follow(addr):
            if addr in own or addr in called or addr in libcalls.X86 or addr not in self.def_at:
                return None
            src, _ = self.def_at[addr]
            try:
                f = self.x86_unit(src, addr).funcs.get(addr)
            except Exception:  # noqa: BLE001
                return None
            return f if f and not f['unresolved'] else None

        todo = [f for xu in list(self._x86.values()) for a, f in xu.funcs.items() if a in code]
        added = 0
        while todo and added < 256:
            f = todo.pop()
            for rel, kind, tg, _ in f['relocs']:
                if kind != 0x14 or tg is None or tg in code or not (0x00401000 <= tg < 0x00fdf000):
                    continue
                g = follow(tg)
                if g is not None:
                    code[tg] = g['code']
                    own.add(tg)
                    todo.append(g)
                    added += 1
        return added

    def arm_called(self, image, source):
        """Original addresses the native unit can call out of the unit: code stubs it binds (undefined
        `__fnv_<addr>` and bridges' targets), named stubs with a known address, and its own definitions."""
        out = set(image.entries.values())
        for n in image.syms:
            m = STUB_ADDR.match(n)
            if m:
                out.add(int(m.group(1), 16))
        for addr, name in image.stubs.items():
            m = STUB_ADDR.match(name)
            if m:
                out.add(int(m.group(1), 16))
        out.update(self.stub_addrmap(image, source).values())
        return out

    def vtable_map(self, image):
        """{native address point: image vftable address} for S2's kind-V address map rows whose vtable symbol the
        unit image defines or stubs (a constructor stores the Itanium address point where MSVC stored the vftable)."""
        if self._vrows is None:
            self._vrows = []
            p = config.LINK / 'out/addrmap.tsv'
            if p.exists():
                for line in open(p, encoding='utf-8', errors='replace'):
                    f = line.rstrip('\n').split('\t')
                    if len(f) > 6 and f[1] == 'V' and f[2]:
                        self._vrows.append((int(f[0], 16), f[2], int(f[6] or 8)))
        by_name = {n: a for a, n in image.stubs.items()}
        out = {}
        for orig, sym, off in self._vrows:
            base = image.syms.get(sym, by_name.get(sym))
            if base is not None:
                out[(base + off) & 0xffffffff] = orig
        # classes without a V row (no vtable synthesis in this link): primary vftable ??_7<C>@@6B@ <-> _ZTV<C> + 8,
        # paired by the (non-template) class name
        if self._vft_by_class is None:
            self._vft_by_class = {}
            for u in self.units:
                for n, a in u['symbols'].items():
                    m = MSVC_VFT.match(n)
                    if m:
                        self._vft_by_class.setdefault('::'.join(reversed([p for p in m.group(1).split('@') if p])),
                                                      int(a, 16))
        for name, a in list(image.syms.items()) + [(n, a) for a, n in image.stubs.items()]:
            if not name.startswith('_ZTV'):
                continue
            cls = itanium_class(name[4:])
            orig = self._vft_by_class.get(cls) if cls else None
            if orig is not None:
                out.setdefault((a + 8) & 0xffffffff, orig)
        return out

    def pinned(self, source, orig_addr):
        xu = self.x86_unit(source, orig_addr)
        return next(self._pin[i] for i, x in self._x86.items() if x is xu)

    def crt_builtins(self):
        """CRT addresses the link gives a native provider whose name is a harness builtin (memmove at 0x00ec7230,
        the libm functions log, pow, exp, ... of libcalls.CRT_MATH): the oracle runs them as that builtin too,
        untraced, as the native code does. Added to libcalls.X86."""
        if not getattr(self, '_crt_done', False):
            self._crt_done = True
            int_names = {'memcpy', 'memmove', 'memset', 'memcmp', 'strlen', 'wcslen', 'strcmp', 'stricmp', 'strncmp',
                         'strnicmp', 'strcpy', 'strcat', 'strncpy', 'strchr', 'strrchr', 'strstr', 'tolower',
                         'toupper', 'bswap'}
            self.providers()
            for name, addr in self._prov_rows:       # every address of a name (memmove: 0x00ec44d0, 0x00ec7230)
                b = libcalls.A64.get(name)
                if (b in int_names or b in libcalls.CRT_MATH and not b.endswith('f')) and addr not in libcalls.X86:
                    libcalls.X86[addr] = b

    def pair(self, source, orig_addr):
        self.crt_builtins()
        t = self.target(source, orig_addr)
        pin = dict(self.iat_pins())
        pin.update(getattr(t, 'extra_pinned', {}))
        pin.update(self.pinned(source, orig_addr))
        for v, b in getattr(t, 'vt_pins', {}).items():
            pin[v] = b               # the exe's slot words win over a unit's recovered copy of the vftable
        return runner.Pair(t, pin), t
