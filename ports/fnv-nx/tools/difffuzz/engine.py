"""engine.py - build a runner.Target for one original address; per-unit caches.

Per unit: the oracle Coff + UnitImage (x86 code, recovered literals) and the linked AArch64 Image. Pinned world bytes
are the unit's literals (strings, `__real@` constants, read-only tables at mapped addresses). The named-stub map gives
the original address behind a stub symbol when the name carries it (`__fnv_<addr>_C2`, ...). callee_info(addr) gives
an intercepted callee's x86 stack cleanup and result register from its definition's decorated name (any unit), else
from the calling unit's own declaration of that address.
"""
import collections
import json
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


class UnitCache:
    def __init__(self, linkdir, workdir, unit_closed=True):
        self.linkdir, self.workdir, self.unit_closed = linkdir, workdir, unit_closed
        self.units = json.load(open(config.DECOMP / 'build/units.json'))['units']
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
        self._x86, self._img, self._pin, self._callee = {}, {}, {}, {}
        self._refmap = None
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
        return out

    def callee_info_for(self, source):
        refs = {}
        for _, u in self.by_source[source]:
            for n, a in u['symbols'].items():
                refs.setdefault(int(a, 16), n)

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
            if cv is None:
                return None
            kind = 'int'
            if cv.x86_ret == 'st0':
                kind = 'f32' if cv.ret.size == 4 else 'f64'
            return (cv.pop, kind)
        info.conv = conv_of
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
            cv = convmod.Conv(fn['symbol'], source)
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
            for a, f in xu.funcs.items():
                if not f['unresolved']:
                    code[a] = f['code']
        t = runner.Target(cv, orig_addr, code, img, self.stub_addrmap(img, source), self.callee_info_for(source),
                          statics)
        t.source = source
        t.custom_cc = list(getattr(cv, 'custom', []) or [])
        t.vtables = self.vtable_map(img)
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
        # literals of every unit that contributed preloaded code
        t.extra_pinned = {}
        for xu2 in list(self._x86.values()):
            if xu2 is not xu and any(a in code for a in xu2.funcs):
                t.extra_pinned.update(xu2.literal_bytes())
        return t

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

    def pair(self, source, orig_addr):
        t = self.target(source, orig_addr)
        pin = dict(getattr(t, 'extra_pinned', {}))
        pin.update(self.pinned(source, orig_addr))
        return runner.Pair(t, pin), t
