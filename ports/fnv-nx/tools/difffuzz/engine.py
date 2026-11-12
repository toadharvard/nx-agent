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
import world
import libcalls

STUB_ADDR = re.compile(r'^__fnv(?:nx_br)?_([0-9a-f]{8})')
_C_DECORATED = re.compile(r'^__imp__([A-Za-z_]\w*)@(\d+)$')


def _indirect_thunk(cs, code, addr, limit=8):
    """True when the function's first instructions are straight-line register / memory moves that end in an indirect
    `jmp reg` / `jmp [mem]` with no `ret`, `call` or branch before it: a virtual-call or vtable thunk, whose stack
    bytes are the jump target's."""
    for n, ins in enumerate(cs.disasm(code, addr)):
        if n >= limit:
            return False
        if ins.mnemonic == 'jmp':
            return not ins.op_str.startswith('0x') and n > 0
        if ins.mnemonic not in ('mov', 'lea', 'movzx', 'movsx', 'xchg'):
            return False
    return False


def import_identity(counts, spellings):
    """(name, stdcall bytes) of one IAT slot from every `__imp_` spelling units.json gives it (F1-callflow).

    The decomp declares many imports itself as C++ functions (`__imp_?FreeLibrary@@YG...`, or a placeholder
    `__imp_?IMP_fdf0a8@fnv_004da670@@YG...`) beside the import library's C spelling (`__imp__FreeLibrary@4`). The
    most common spelling used to win, and a C++ spelling carries no `@N`: the stub popped 0 bytes for a stdcall
    import, esp stayed off by its arguments, and the oracle's `ret` went to an argument word (wild calls after the
    import, read as native-early-exit). Now: the name is the most common C spelling (the export), else the C++
    identifier without `?`; the stack bytes come from a C spelling's `@N` (the chosen name's first), else from a
    `__stdcall` C++ spelling's parameters (conv.py), else 0 (cdecl, or nothing known)."""
    c = [(nm, n) for nm, n in counts.items() if not nm[0].startswith('?')]
    if c:
        (name, pop), _ = max(c, key=lambda x: (x[1], x[0][1] > 0, x[0][0]))
    else:
        (name, pop), _ = max(counts.items(), key=lambda x: (x[1], x[0][0]))
        name = name.lstrip('?')
    if pop:
        return name, pop
    dec = [(m.group(1), int(m.group(2))) for m in (_C_DECORATED.match(s) for s, _ in spellings) if m]
    for nm, n in sorted(dec, key=lambda x: x[0] != name):
        return name, n                         # an explicit `@N`, the chosen name's first (`@0` included)
    for s, src in spellings:
        if s.startswith('__imp_?') and '@@YG' in s:
            try:
                return name, convmod.Conv(s[len('__imp_'):], src).pop
            except Exception:  # noqa: BLE001 - a parameter type conv.py cannot place: nothing known
                continue
    return name, 0
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
LOCAL_VALUE = re.compile(r'^\?([^@?$][^@?]*)@\?\d+\?(\?.+?)@4.*A$')
LOCAL_GUARD = re.compile(r'^\?\$(?:S|TSS)\d+@\?\d+\?(\?.+)@4IA$')


def dyninit_conv(symbol, source):
    """`??__E...` / `??__F...` (dynamic initializer / atexit destructor):
    void __cdecl (void)."""
    if symbol.startswith(('??__E', '??__F')):
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
                if _indirect_thunk(cs, f['code'], addr):
                    # (difffuzz-20, args class) a virtual-call thunk `mov ecx, [ecx+x]; mov eax, [ecx]; jmp [eax+y]`
                    # (0x00c91270): the stack bytes are those of whichever function the slot holds. The `ret N` the
                    # linear scan finds past its padding belongs to the next function; the pop is learned from the run
                    cache[addr] = None
                    self.__dict__.setdefault('_thunk_unknown', set()).add(addr)
                    return None
                for ins in cs.disasm(f['code'], addr):
                    if ins.mnemonic == 'ret':
                        pops.add(int(ins.op_str, 0) if ins.op_str else 0)
                if len(pops) == 1:
                    got = pops.pop()
                elif not pops and f['code'][:1] == b'\xe9' and len(f['code']) == 5 and f.get('relocs'):
                    # (difffuzz-20, flow2) an incremental-link thunk `jmp rel32` (the exe's ILT: 0x00aa0ad0 -> 0x00a81fe0
                    # -> the real function): the decomp defines it as a `void F(void)` placeholder whose declaration
                    # popped 0; the stack bytes are those of the function it jumps to, through the chain. Unknown
                    # when the chain ends in no single `ret N`: the pop is then learned from the run
                    cache[addr] = None
                    tgt = [tg for _r, _k, tg, _x in f['relocs'] if tg is not None]
                    got = self.oracle_ret_pop(tgt[0]) if tgt else None
                    if got is None:
                        self.__dict__.setdefault('_thunk_unknown', set()).add(addr)
                elif not pops and f['code'][:2] == b'\xff\x25':
                    # an import thunk `jmp dword ptr [__imp_X]` (no ret of its own; F1-callflow: the decomp
                    # defines the exe's D3DX thunks as `void FUN_00ee6df2(void)` placeholders, whose cdecl
                    # declaration popped 0 for a stdcall import): the import's own stack bytes
                    self.import_stubs()
                    for _rel, _kind, tg, _x in f.get('relocs', ()):
                        if tg in self._iat:
                            got = self._imports[(self._iat[tg] - libcalls.IMPORT_BASE) // 16][1]
                            break
                    else:
                        # a slot units.json names only as data (`?g_D3DXVec3TransformNormal@@3P6AXXZA`): no import
                        # stub, no stack bytes; the declaration's are not evidence either. callee_info answers
                        # None: the pop is unknown and runner.Pair._learn_pop finds it from the run
                        self.__dict__.setdefault('_thunk_unknown', set()).add(addr)
        cache[addr] = got
        return got

    def exe_ret_pop(self, addr):
        """(difffuzz-20, flow2) The `ret N` of a function the tree does not define (a CRT function, a function no
        unit covers), read from the player's exe: `addr` must be a function start (the byte before it is padding
        `int3`/`nop` or a `ret`) in a code section, and its `ret N` is the first `ret` found. A
        `jmp rel32` start is followed (an ILT thunk). None without the exe or when it does not say; the intercepted
        call then pops what the learner finds (runner.Pair._learn_pop) or 0."""
        cache = self.__dict__.setdefault('_exepop', {})
        if addr in cache:
            return cache[addr]
        cache[addr] = None
        pe = coffobj.exe_image()
        if pe is None:
            return None
        import capstone
        cs = self.__dict__.setdefault('_cs', capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32))
        a = addr
        for _ in range(4):
            sec = pe.section_of(a)
            if sec is None or not sec[2] & 0x20 or a - sec[0] < 4:      # not code (SCN_CODE), or no byte before
                return None
            prev = pe.read(a - 1, 1)
            code = pe.read(a, 0x400)
            if not code or prev[0] not in (0xcc, 0x90, 0xc3) and pe.read(a - 3, 1) != b'\xc2':
                return None
            if code[0] == 0xe9:
                a = (a + 5 + int.from_bytes(code[1:5], 'little', signed=True)) & 0xffffffff
                continue
            # every `ret` of one function agrees, so the first one in the linear scan is the function's own (a
            # later one may already belong to the next function, which MSVC pads with `mov edi, edi`, not int3)
            for ins in cs.disasm(code, a):
                if ins.mnemonic == 'int3':
                    break
                if ins.mnemonic == 'ret':
                    cache[addr] = int(ins.op_str, 0) if ins.op_str else 0
                    break
            return cache[addr]
        return None

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

    def sret_bridges(self):
        """{(object, address)} the link bridged as sret-explicit only (out/abi.tsv, verdict `bridge`): the unit's
        reference passes the callee's result buffer as an explicit pointer, the definition takes it hidden."""
        if getattr(self, '_sretb', None) is None:
            self._sretb = set()
            p = config.LINK / 'out/abi.tsv'
            if p.exists():
                for line in open(p, encoding='utf-8', errors='replace'):
                    f = line.rstrip('\n').split('\t')
                    if len(f) > 6 and f[5] == 'bridge' and f[6] == 'sret-explicit':
                        self._sretb.add((f[0], int(f[2], 16)))
        return self._sretb

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
                    if name == refs.get(addr) and (source, addr) in self.sret_bridges():
                        # the unit's reference declaration passes the result buffer explicitly and the link put an
                        # sret-explicit bridge before the definition: the stub is called without that parameter
                        drop = next((lab for lab, sp, off, _ in got.x86 if sp == 'stk' and off == 0), None)
                        got.native_view = got.bridged_view(drop) if drop else None
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
            if addr in getattr(self, '_thunk_unknown', ()):
                return None
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
                    if got is None:
                        got = self._c_bool_decl(source, refs[addr])
                self._callee[key] = got
            return self._callee[key]
        info.exe_pop = self.exe_ret_pop
        info.conv = conv_of
        info.caller_conv = caller_conv_of
        return info

    def _c_bool_decl(self, source, name):
        """(R4 flow) A C-linkage name (`_FUN_00401500`) carries no types, so the calling unit's declaration of the callee
        is unknown and its bool result was not 0/1 on the oracle (MSVC tests al, GCC bit 0): read the declaration from
        the unit's prepared source, `extern "C" bool __cdecl FUN_00401500();`. Returns a conv for `bool f(void)`
        (only its result is used for the caller's side of a canned result), else None."""
        if not name.startswith('_') or name.startswith('?'):
            return None
        ident = name[1:].split('@')[0]
        cache = self.__dict__.setdefault('_cbool', {})
        if source not in cache:
            try:
                cache[source] = open(str(config.TREE / source), encoding='utf-8', errors='replace').read()
            except OSError:
                cache[source] = ''
        if not re.search(r'extern\s+"C"\s+bool\s+(?:__\w+\s+)?%s\s*\(' % re.escape(ident), cache[source]):
            return None
        try:
            return convmod.Conv('?%s@@YA_NXZ' % ident, source)
        except Exception:  # noqa: BLE001
            return None

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
        t.static_nguards = self._native_guards
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
        t.vtables_defined = self.vtable_map_defined(img)
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
            spell = collections.defaultdict(list)          # slot -> [(decorated spelling, unit source)]
            for u in self.units:
                for n, a in u['symbols'].items():
                    if n.startswith('__imp_'):
                        ad = int(a, 16)
                        if 0x00fdf000 <= ad < 0x00fe0000:
                            names[ad][libcalls.import_name(n)] += 1
                            spell[ad].append((n, u['source']))
            self._iat = {}
            self._imports = {}
            for k, ad in enumerate(sorted(names)):
                if k >= libcalls.IMPORT_MAX:
                    break
                nm = import_identity(names[ad], spell[ad])
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
        # (difffuzz-20, static-backref) inside a local static's name the function is decorated in its own scope, so
        # its back-references can count differently (`SAAAV2@XZ` there, `SAAAV1@XZ` in the function's own symbol):
        # when the exact spelling is unknown, the function whose symbol agrees with it up to the digits, if one does
        norm = {}
        for fs, fa_ in faddr.items():
            norm.setdefault(re.sub(r'\d', '#', fs), set()).add(fa_)

        def func_addr(fs):
            if fs in faddr:
                return faddr[fs]
            c = norm.get(re.sub(r'\d', '#', fs), ())
            return next(iter(c)) if len(c) == 1 else None
        pairs, guards, nguards = [], set(), []
        for n, a in rows.items():
            if True:
                g = LOCAL_GUARD.match(n)
                if g and func_addr(g.group(1)) in fn_names:
                    guards.update(range(int(a, 16), int(a, 16) + 4))
                    continue
                m = LOCAL_VALUE.match(n)
                if not m:
                    continue
                var, func = m.group(1), m.group(2)
                fa = func_addr(func)
                for nn in fn_names.get(fa, ()):
                    base = '_ZZ' + nn[2:] + 'E' + str(len(var)) + var
                    cand = [x for x in (base, base + '_0') if x in img.syms]
                    if cand and img.sizes.get(cand[0]):
                        pairs.append((img.syms[cand[0]], int(a, 16), img.sizes[cand[0]]))
                        gn = '_ZGV' + cand[0][2:]
                        if gn in img.syms:      # the native's own guard of that static (its init protocol)
                            nguards.append((img.syms[gn], img.sizes.get(gn) or 8))
                        break
        self._native_guards = nguards
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

    def vtable_map_defined(self, image):
        """{native address point: image vftable} of the kind-V address map rows whose vtable symbol the unit image
        DEFINES (no stub, no class-name pairing): the vtable the link itself binds to that exe vftable. A returned
        class identity is the same only by this pairing (R4, farm baseline 588863: vftable getters)."""
        self.vtable_map(image)                     # loads self._vrows
        return {(image.syms[sym] + off) & 0xffffffff: orig for orig, sym, off in self._vrows if sym in image.syms}

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
                # (difffuzz-20, flow) the `f` names too: the exe's ceilf / floorf / cosf / acosf wrappers
                # (0x004e4430, 0x00406cc0, 0x004e4470, 0x004f6e40) are float functions GCC inlines (frintp), the
                # oracle traced them as calls (11 native-inlined rows); x86emu._builtin reads their float argument
                if (b in int_names or b in libcalls.CRT_MATH) and addr not in libcalls.X86:
                    libcalls.X86[addr] = b

    def pair(self, source, orig_addr):
        self.crt_builtins()
        t = self.target(source, orig_addr)
        pin = dict(self.iat_pins())
        pin.update(getattr(t, 'extra_pinned', {}))
        pin.update(self.pinned(source, orig_addr))
        for v, b in getattr(t, 'vt_pins', {}).items():
            pin[v] = b               # the exe's slot words win over a unit's recovered copy of the vftable
        p = runner.Pair(t, pin)
        if t.custom_cc:
            self.calibrate_custom(p, t)
        return p, t

    def calibrate_custom(self, pair, t):
        """calibrate_custom (module function) with the data-window globals the oracle function refers to."""
        fn = self.x86_unit(t.source, t.orig_addr).funcs[t.orig_addr]
        pinned = set(pair.pinned)
        globals_ = sorted({tg for _, k, tg, _ in fn['relocs'] if tg is not None and
                           world.WINDOW[0] <= tg < world.WINDOW[1] and tg not in pinned})
        calibrate_custom(pair, t, globals_)


CALIB_CASES = 8
CALIB_MAX = 120
CALIB_ROUNDS = 3


def calib_float_noise(rx, ra, d):
    """A calibration case that only differs in float representation agrees on the pairing (difffuzz-20, calib-float).
    A vector routine (SSE normalize / cross: 0x00dacf10, 0x00d5b440, 0x00cd6120, 0x00da9210) differs on almost every
    fuzzed input by a NaN payload or sign (x87 / SSE indefinite 0xffc00000 against the AArch64 default NaN) or by an
    ulp of an rsqrt / rcp, whichever pairing runs, so the 7-of-8 bar of `calibrate_custom` was never reached and the
    declared order stayed. Only the pairing choice looks at this: the verdicts of the real runs are untouched.
    True when no run faulted, every differing byte is a store both machines made, and every differing aligned word
    is two NaNs or two normal floats within causes._float_close's tolerance (the pairs the float causes accept); a
    soft record agrees as well."""
    import causes
    if d.get('soft'):
        return True
    if rx.error or ra.error or d.get('kind') not in ('mem-write', 'return'):
        return False
    if d['kind'] == 'return' and not (d.get('float') or {}).get('tolerant'):
        return False
    wx, wa = rx.writes, ra.writes
    words = set()
    for k in set(wx) | set(wa):
        if wx.get(k) != wa.get(k):
            if k not in wx or k not in wa:
                return False
            words.add(k & ~3)
    for w in words:
        if not all(k in wx and k in wa for k in range(w, w + 4)):
            return False
        bx = bytes(wx[k] for k in range(w, w + 4))
        ba = bytes(wa[k] for k in range(w, w + 4))
        if not causes._float_close(bx, ba)[0] and not _cancellation_residue(bx, ba):
            return False
    return True


def _cancellation_residue(bx, ba):
    """Two float words that are both zero or normal and differ by at most 1e-6 in absolute value: the residue of a
    cancellation (0.0 against 1.6e-18 after a normalize of nearly equal vectors) that a relative tolerance does not
    accept. An integer word is a denormal float (0 against 5), so it never qualifies."""
    import math
    import struct
    fx, fa = struct.unpack('<f', bx)[0], struct.unpack('<f', ba)[0]
    if math.isnan(fx) or math.isnan(fa) or math.isinf(fx) or math.isinf(fa):
        return False

    def plain(f):
        return f == 0 or abs(f) >= 1.1754943508222875e-38
    return plain(fx) and plain(fa) and abs(fx - fa) <= 1e-6


def calib_vptr_noise(pair, case, d):
    """A calibration case whose only difference is a byte or part of a planted vptr word that the code copies as
    data agrees on the pairing (difffuzz-20, calib-vptr): the oracle holds the image vftable there, the native the
    Itanium address point, and a byte copied from the word differs although the same object is read (0x00d409b0
    copies `src->val` at offset 2 of the word). The same probe as classify's `vptr-bytes` (world.VPTR_NATIVE), only
    for the pairing choice."""
    if d.get('kind') != 'mem-write' or d.get('soft') or not case.vptrs:
        return False
    import classify
    world.VPTR_NATIVE = True
    try:
        return classify._benign(pair.run(case)[2])
    finally:
        world.VPTR_NATIVE = False


def calib_touch(pair, case):
    """(difffuzz-20, calib-touch) One case on both machines with every read and write of the argument buffers
    recorded: (rx, ra, divergence, oracle set, native set), each set holding (kind, dword address) of the shared
    buffers. The buffers sit at the same addresses on both machines, so the sets compare directly. Runs
    `Pair._run` (no adaptive follow) after one `Pair.run` has made the engines."""
    from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_WRITE
    lo, hi = world.BUF, world.BUF + world.NBUF * world.BUF_SIZE

    def hook(uc, access, address, size, value, acc):
        k = 'w' if access == UC_MEM_WRITE else 'r'
        for a in range(address >> 2, ((address + size - 1) >> 2) + 1):
            acc.add((k, a))
    sets = []
    for eng in (pair.xe, pair.ae):
        acc = set()
        sets.append((eng, eng.uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, hook, user_data=acc,
                                          begin=lo, end=hi - 1), acc))
    try:
        rx, ra, d = pair._run(case, 200000)
    finally:
        for eng, h, _ in sets:
            eng.uc.hook_del(h)
    return rx, ra, d, sets[0][2], sets[1][2]


def calib_touch_score(pair, cases):
    """(mean Jaccard of the oracle's and the native's buffer accesses over the cases where either touches a buffer,
    number of such cases) for the register assignment now set on the pair's conv."""
    js = []
    for c in cases:
        try:
            _, _, _, sx, sa = calib_touch(pair, c)
        except Exception:  # noqa: BLE001
            js.append(0.0)
            continue
        u = sx | sa
        if u:
            js.append(len(sx & sa) / len(u))
    return (sum(js) / len(js) if js else None), len(js)


def calib_agree_strict(rx, ra, d):
    """A case agrees for the pairing choice when there is no divergence and, if both machines faulted, they faulted
    the same way (access kind and address): a fault address comes from the register that carried the pointer, so
    two machines that read the same wild word agree on it and two that read different parameters do not."""
    if d:
        return False
    return not (rx.error and ra.error and rx.error != ra.error)


def calib_touch_pick(pair, cv, regs, cands, cases, declared, keep=0.75, margin=0.25):
    """Choose the pairing by what the oracle touches in the argument buffers (difffuzz-20, calib-touch). Pairing
    scores of `calibrate_custom` say nothing when every case ends in a fault on both machines (00dbcdd0: object in
    edi, count in eax; the declared eax = p0, edi = p1 faulted on both machines in 8 of 8 cases) or when a lasting
    difference spoils every pairing (00d6e280, four registers, 120 pairings). The native side is the ground truth:
    AAPCS passes the declared parameters in order, so the buffers the native reads and writes are the ones the
    declared parameters point to. For each pairing the oracle runs the same cases; its set of (read / write, dword)
    of the argument buffers is compared with the native's. Pairing score = strict agreement fraction + mean
    Jaccard of those sets. The declared order stays unless it touches the buffers differently (mean below `keep`)
    and another pairing scores `margin` more with a mean of at least `keep` and above the declared one's.
    Nothing is learned from a function whose cases touch no buffer."""
    cv.with_registers(regs, dict(zip(declared, regs)))
    pair.run(cases[0])
    dj, nev = calib_touch_score(pair, cases)
    if nev == 0 or dj is None or dj >= keep:
        return declared

    def total(cand):
        cv.with_registers(regs, dict(zip(cand, regs)))
        j, n = calib_touch_score(pair, cases)
        if n == 0 or j is None:
            return -1.0, None
        ok = 0
        for c in cases:
            try:
                rx_, ra_, d = pair.run(c)
                if d and (calib_float_noise(rx_, ra_, d) or calib_vptr_noise(pair, c, d)):
                    d = None
                ok += calib_agree_strict(rx_, ra_, d)
            except Exception:  # noqa: BLE001
                pass
        return ok / len(cases) + j, j
    base, _ = total(declared)
    best, bt, bj = declared, base, dj
    for cand in cands:
        if cand == declared:
            continue
        tot, j = total(cand)
        if j is not None and tot > bt:
            best, bt, bj = cand, tot, j
    if best != declared and bj >= keep and bj > dj and bt >= base + margin:
        return best
    return declared


def calibrate_custom(pair, t, globals_=()):
    """Which declared parameter each custom register carries (difffuzz-20, F1b). MSVC's whole-program
    convention does not pass them in declared order: 0x00d58d40 (base, index) takes the index in eax, 0x00e5b130
    (object, flags) the object in ecx and the flags in eax, so the declared-order pairing gave the oracle the
    other argument and the records read as oracle-only near-null faults or other results. Every injective
    pairing of the custom registers with the register-eligible parameters runs the same CALIB_CASES fresh cases
    (fixed seed); a pairing other than the declared order is kept only when it leaves at most one case with a
    divergence and does better than the declared order (a weaker lead is no evidence: a register read after
    the entry path, or another difference, spoils every pairing). Nothing of the case or the native side
    changes, and what the runs learned (followed code, pops) is dropped. t.custom_calib records {pairing:
    score} (a pruned pairing scores below the bar)."""
    import itertools
    import fuzz
    cv = t.conv
    regs = list(getattr(cv, 'custom_regs', None) or t.custom_cc)
    elig = cv.register_eligible()
    if not regs or len(elig) < len(regs):
        return
    cands = list(itertools.permutations(elig, len(regs)))
    if len(cands) < 2 or len(cands) > CALIB_MAX:
        return
    gen = fuzz.Gen(t, globals_, t.orig_addr ^ 0x5ca1ab1e)
    cases = [gen.fresh() for _ in range(CALIB_CASES)]
    saved = (dict(t.x86_code), dict(pair.pinned), dict(pair.pops), set(pair.pops_tried), pair.adapted)
    declared = cands[0]                     # permutations() starts with the declared order
    scores = {}
    need = CALIB_CASES - 1
    for cand in cands:
        cv.with_registers(regs, dict(zip(cand, regs)))
        n = bad = 0
        for c in cases:
            try:
                rx_, ra_, d = pair.run(c)
                if d and (calib_float_noise(rx_, ra_, d) or calib_vptr_noise(pair, c, d)):
                    d = None
            except Exception:  # noqa: BLE001
                d = True
            n += not d
            bad += bool(d)
            if cand != declared and bad > CALIB_CASES - need:
                break                       # cannot reach the bar any more
        scores[cand] = n
        if cand == declared:
            if n == CALIB_CASES:
                break                       # the declared order agrees on every case
            need = max(need, n + 1)
    best = max(scores, key=lambda c: (scores[c], c == declared))
    if best != declared and scores[best] < need:
        best = declared
    else:
        # several pairings at the top score: the cases do not tell them apart (0x00d6c3c0: six pairings agree on
        # all 8 cases, the zero or random buffers of its pointer parameters look alike to the vector code), and
        # the first in permutation order was taken. Run fresh cases on the tied pairings only, up to
        # CALIB_ROUNDS more rounds, and keep the ones that agree on the most (difffuzz-20, calib-tie).
        tied = [c for c in cands if scores.get(c) == scores[best]]
        for _ in range(CALIB_ROUNDS if len(tied) > 1 else 0):
            extra = [gen.fresh() for _ in range(CALIB_CASES)]
            more = {}
            for cand in tied:
                cv.with_registers(regs, dict(zip(cand, regs)))
                more[cand] = 0
                for c in extra:
                    try:
                        rx_, ra_, d = pair.run(c)
                        if d and (calib_float_noise(rx_, ra_, d) or calib_vptr_noise(pair, c, d)):
                            d = None
                    except Exception:  # noqa: BLE001
                        d = True
                    more[cand] += not d
            top = max(more.values())
            tied = [c for c in tied if more[c] == top]
            if len(tied) == 1:
                break
        best = declared if declared in tied else tied[0]
    if best == declared and (scores[declared] == CALIB_CASES or scores[declared] < CALIB_CASES - 1):
        # the fresh cases cannot tell the pairings apart (both machines fault the same way, or nothing agrees):
        # what each pairing makes the oracle touch in the argument buffers is compared with what the native touches
        best = calib_touch_pick(pair, cv, regs, cands, cases, declared)
    cv.with_registers(regs, dict(zip(best, regs)))
    t.x86_code.clear(); t.x86_code.update(saved[0])
    pair.pinned.clear(); pair.pinned.update(saved[1])
    pair.pops.clear(); pair.pops.update(saved[2])
    pair.pops_tried = saved[3]; pair.adapted = saved[4]
    pair.xe = pair.ae = None
    t.custom_cc = list(cv.custom)
    t.custom_calib = {','.join('%s=%s' % (r, lab) for lab, r in zip(c, regs)): s for c, s in scores.items()}
