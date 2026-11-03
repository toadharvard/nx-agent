"""engine.py - build a runner.Target for one original address; per-unit caches.

Per unit: the oracle Coff + UnitImage (x86 code, recovered literals) and the linked AArch64 Image. Pinned world bytes
are the unit's literals (strings, `__real@` constants, read-only tables at mapped addresses). The named-stub map gives
the original address behind a stub symbol when the name carries it (`__fnv_<addr>_C2`, ...). callee_info(addr) gives
an intercepted callee's x86 stack cleanup and result register from its definition's decorated name (any unit), else
from the calling unit's own declaration of that address.
"""
import json
import re

import config
import coffobj
import a64image
import conv as convmod
import runner
import libcalls

STUB_ADDR = re.compile(r'^__fnv(?:nx_br)?_([0-9a-f]{8})')
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
        self._x86, self._img, self._pin, self._callee = {}, {}, {}, {}
        self._refmap = None

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
            path = config.WORK.parent / 'link/out/refs.tsv'
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
                for src, name in ((self.def_at.get(addr) or (None, None)), (source, refs.get(addr))):
                    if not name:
                        continue
                    try:
                        got = convmod.Conv(name, src)
                        break
                    except Exception:  # noqa: BLE001
                        continue
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
        cv = convmod.Conv(fn['symbol'], source)
        img = self.arm_image(source)
        if not any(o == orig_addr for o in img.entries.values()):
            raise KeyError('no native entry for %08x' % orig_addr)
        statics = set()
        for _, u in self.by_source[source]:
            for n, a in u['symbols'].items():
                if LOCAL_STATIC.match(n):
                    statics.update(range(int(a, 16), int(a, 16) + 16))
        code = {orig_addr: fn['code']}
        if self.unit_closed:
            for a, f in xu.funcs.items():
                if not f['unresolved']:
                    code[a] = f['code']
        return runner.Target(cv, orig_addr, code, img, self.stub_addrmap(img, source), self.callee_info_for(source),
                             statics)

    def pinned(self, source, orig_addr):
        xu = self.x86_unit(source, orig_addr)
        return next(self._pin[i] for i, x in self._x86.items() if x is xu)

    def pair(self, source, orig_addr):
        t = self.target(source, orig_addr)
        return runner.Pair(t, dict(self.pinned(source, orig_addr))), t
