"""fnkeys.py - a key per function that changes when what difffuzz would compare for it changes (incremental farms).

    fnkeys.py <targets.jsonl> <catalog.jsonl> <out keys.json>     {address: key} for the targets' functions

key = sha1(HARNESS, oracle hash, native hash):
  oracle   the function's relocated MSVC code (catalog `ohash`; fixed per decomp commit)
  native   the rewritten AArch64 function (link `fixed/`): its bytes and relocations (type, offset, addend, target),
           plus the same for every function of its object it reaches through relocations (difffuzz runs the whole
           unit for real, so a changed callee or inlined helper changes the caller's verdict) and for the S2 bridges
           it calls (zz_bridges.o). A target is named by what it resolves to: an absolute address from aliases.ld,
           `__fnv_<addr>`, a symbol name, or for a local data reference the referenced bytes (a literal's content).
  renames  S1b's units_renames rows of the function's unit that apply to the link's tree (config.units_renames:
           they set the definition's and the callers' conventions, engine.UnitCache); left out when there are
           none, so the keys of every other unit stay what they were (J-0141 renames: no HARNESS bump, only those units refresh).
  changes  the tags of the scoped harness changes (CHANGES) that select the function; left out when none does
HARNESS is bumped when the harness changes every verdict (a deliberate full refresh); a function without a native
definition gets no key (not fuzzed).

Scoped harness changes (difffuzz-14 on, item "a harness version bump says what it invalidates"): a change that
alters the verdicts of an identifiable set of functions is a CHANGES row instead of a HARNESS bump. It says in
words what it invalidates, and decides it with
  selects(fn)   static facts of the function (Facts: its MSVC name; the original addresses its oracle code calls,
                through the same-unit functions it runs for real; the names and absolute addresses its native code
                and the functions of its object it reaches refer to): its key gets the change's tag;
  recheck(div)  a saved divergence (div/<addr>-<n>.json) that the change explains differently: the cached verdict
                is fuzzed again once, whatever its key (fuzzcache.py todo; rows record the CHANGES they saw).
`fnkeys.py explain <targets> <catalog> [<cache>...]` counts what each change invalidates.
SCOPED (the newest tag) is what the farms compare with the integration head besides HARNESS.
"""
import hashlib
import json
import os
import re
import struct
import sys

import config

# difffuzz-19: a full refresh (orchestrator-approved, S1-fuzzB-002): S1-fuzzB's untyped float results
# (x86emu.x87_result_use), CRT_MATH builtins and engine's decorated-spelling preference change verdicts no key
# reflects, and so do the untyped canned results, the secondary-base entry and the vtpad-before-unplanted order of
# this bump. Every key changes; the scoped CHANGES rows below stay for later partial refreshes.
HARNESS = 'difffuzz-19'


class Facts:
    __slots__ = ('symbol', 'ocalls', 'nrefs', 'oindirect', 'ostatic')

    def __init__(self, symbol='', ocalls=(), nrefs=(), oindirect=False, ostatic=False):
        self.symbol, self.ocalls, self.nrefs = symbol or '', set(ocalls), set(nrefs)
        self.oindirect = oindirect      # the oracle code (or a same-unit function it runs) makes indirect calls
        self.ostatic = ostatic          # ... or refers to a function-local static or its guard (units.json names)


class Change:
    def __init__(self, tag, invalidates, selects=None, recheck=None):
        self.tag, self.invalidates, self.selects, self.recheck = tag, invalidates, selects, recheck


def _noreturn(f):
    import libcalls
    return bool(f.ocalls & set(libcalls.NORETURN_X86)) or any(
        isinstance(n, str) and (libcalls.noreturn_a64(n) or n == '__cxa_allocate_exception') for n in f.nrefs)


def _math(f):
    import libcalls
    crt = {a for a, n in libcalls.X86.items() if n in libcalls.CRT_MATH or n in libcalls.CI_FORMS}
    return bool(f.ocalls & crt) or bool(f.nrefs & crt) or bool(
        {n for n in f.nrefs if isinstance(n, str)} & libcalls.FLOAT_NAMES)


def _deleting(f):
    return f.symbol.startswith(('??_G', '??_E'))


def _old_exc7(rec):
    return str((rec.get('divergence') or {}).get('arm') or '').startswith('exc:7@')


def _old_uninit_both(rec):
    return (rec.get('hint') or {}).get('cause') == 'uninit-both'


def _old_bool_repr(rec):
    return (rec.get('hint') or {}).get('cause') == 'bool-repr'


def _indirect(f):
    return f.oindirect


def _statics(f):
    return f.ostatic or any(isinstance(n, str) and n.startswith(('_ZZ', '_ZGVZ')) for n in f.nrefs)


CHANGES = [
    Change('d14-dtor', 'targets that are MSVC deleting destructors (??_G / ??_E): fuzzed with flags = 1 only (the '
           'Itanium D0 they stand for always deletes), their return value (this vs void) no longer compared',
           selects=_deleting),
    Change('d14-noreturn', 'functions whose oracle code calls _CxxThrowException / std::_Xlen / exit or whose native '
           'code calls __cxa_throw*, __cxa_rethrow, fnvnx_cxx_throw_exception, std::__throw_*, abort ... (directly '
           'or through a same-unit function): the run ends at that call on both machines; __cxa_allocate_exception '
           'is a builtin', selects=_noreturn),
    Change('d14-math', 'functions that reach the CRT math (pow, log, log10, exp, atan[2], fmod, floor, ceil, tan, '
           'asin, acos, sin, cos, the _CI* forms) or the libm names: run in Python with IEEE results on both '
           'machines (log10 no longer through Unicorn\'s fyl2x)', selects=_math),
    Change('d14-vslot', 'cached divergences that ended in a native brk (exc:7): a call of the link\'s vslot / special '
           'trap is now named (trap:<stub>:<text>) and classified native-undefined', recheck=_old_exc7),
    Change('d15-vcall', 'functions whose oracle code (or a same-unit function it runs) makes indirect calls: an '
           'oracle call through memory the case did not plant no longer ends the comparison (oracle-vcall), and an '
           'intercepted callee of unknown convention pops what the caller pushed when the oracle otherwise '
           'returns to garbage (learned per call site)', selects=_indirect),
    Change('d17-statics', 'functions whose oracle code (or a same-unit function it runs) refers to a function-local '
           'static or its guard (units.json ?x@?1??f / ?$S1@), or whose native code names one: the static pairs with the oracle\'s image copy by function and name, '
           'both start zero (the MSVC value and guard word pinned), the native stores are compared at the oracle '
           'address, the guards are not compared (local-static)', selects=_statics),
    Change('d18-uninit', 'cached divergences classified uninit-both (both machines read garbage): the case runs once '
           'more with the same (zero) garbage on both machines, and when they then agree there is no record',
           recheck=_old_uninit_both),
    Change('d19-bool', 'cached divergences classified bool-repr: the probe now counts an input byte only when the '
           'oracle reads exactly that byte with a 1-byte access (a byte of a planted pointer or integer that removes '
           'the divergence when set to 1 is not a bool); the record gets the cause of the next probe',
           recheck=_old_bool_repr),
]
SCOPED = 'd19'


def harness_tag():
    """HARNESS plus the vtable model's input: verdicts with the player's exe (vptrs planted, vtmodel.py) and without
    it differ, so a farm that gains or loses the exe fuzzes again."""
    import vtmodel
    return HARNESS + ('+exe' if vtmodel.exe_path() else '')


ADDR = re.compile(r'^__fnv_([0-9a-f]{8})$')
CALLS = {282, 283}               # R_AARCH64_JUMP26 / CALL26 (ELF32 ILP32 uses the same numbers)
SHF_EXEC = 4


class Obj:
    """ELF32 relocatable: sections, symbols, RELA entries per target section."""

    def __init__(self, path):
        d = self.data = open(path, 'rb').read()
        if d[:4] != b'\x7fELF' or d[4] != 1:
            raise ValueError('%s: not ELF32' % path)
        shoff, = struct.unpack_from('<I', d, 0x20)
        shentsize, shnum, shstrndx = struct.unpack_from('<HHH', d, 0x2e)
        self.secs = [struct.unpack_from('<IIIIIIIIII', d, shoff + i * shentsize) for i in range(shnum)]
        shstr = self.secs[shstrndx][4]
        self.names = [self._cstr(shstr + s[0]) for s in self.secs]
        self.syms, self.rela = [], {}
        for s in self.secs:
            if s[1] == 2:                                    # SHT_SYMTAB
                stroff = self.secs[s[6]][4]
                for k in range(s[5] // 16):
                    nm, value, size, info, _, shndx = struct.unpack_from('<IIIBBH', d, s[4] + k * 16)
                    self.syms.append((self._cstr(stroff + nm) if nm else '', value, size, info & 15, info >> 4,
                                      shndx))
        for s in self.secs:
            if s[1] == 4:                                    # SHT_RELA
                ents = []
                for o in range(s[4], s[4] + s[5] - 11, 12):
                    off, info, addend = struct.unpack_from('<IIi', d, o)
                    ents.append((off, info & 0xff, info >> 8, addend))
                self.rela.setdefault(s[7], []).extend(ents)

    def _cstr(self, off):
        return self.data[off:self.data.index(b'\0', off)].decode('utf-8', 'surrogateescape')

    def sec_bytes(self, i, lo, hi):
        s = self.secs[i]
        if s[1] == 8:                                        # NOBITS
            return b'\0' * (hi - lo)
        return self.data[s[4] + lo:s[4] + hi]

    def functions(self):
        """[(section, start, end, name)] of sized function symbols in executable sections."""
        out = []
        for name, value, size, typ, bind, shndx in self.syms:
            if typ == 2 and size and 0 < shndx < len(self.secs) and self.secs[shndx][2] & SHF_EXEC:
                out.append((shndx, value, value + size, name))
        return out


def load_aliases(path):
    absolute = {}
    if os.path.exists(path):
        for line in open(path, encoding='utf-8', errors='replace'):
            m = re.match(r'\s*([^\s=]+)\s*=\s*(0x[0-9a-fA-F]+)\s*;', line)
            if m:
                absolute[m.group(1)] = int(m.group(2), 16)
    return absolute


class Native:
    def __init__(self, linkdir):
        self.fixed = os.path.join(linkdir, 'fixed')
        self.absolute = load_aliases(os.path.join(linkdir, 'out', 'aliases.ld'))
        self.bridges = {}
        for bn in ('zz_bridges.o', 'zz_bd_vcall.o'):   # S2 bridges, S2d's vcall stubs: both linked into every image
            bp = os.path.join(self.fixed, bn)
            if os.path.exists(bp):
                o = Obj(bp)
                for sec, lo, hi, name in o.functions():
                    self.bridges[name] = self._own(o, sec, lo, hi, {})[0]

    def _target(self, o, k, addend):
        name, value, size, typ, bind, shndx = o.syms[k]
        if name in self.absolute:
            return 'abs:%x+%d' % (self.absolute[name], addend)
        if name and bind != 0:
            return 'sym:%s+%d' % (name, addend)
        if typ == 3 or not name:                            # section symbol: the bytes it points at
            if 0 < shndx < len(o.secs) and not o.secs[shndx][2] & SHF_EXEC:
                at = value + addend
                return 'data:%s:%s' % (o.names[shndx], hashlib.sha1(o.sec_bytes(shndx, at, at + 64)).hexdigest()[:12])
            return 'sec:%s+%d' % (o.names[shndx] if 0 < shndx < len(o.names) else '?', value + addend)
        return 'local:%s+%d' % (name, addend)

    def _own(self, o, sec, lo, hi, by_start, refs=None):
        h = hashlib.sha1(o.sec_bytes(sec, lo, hi))
        reach = set()
        for off, typ, k, addend in sorted(o.rela.get(sec, ())):
            if not lo <= off < hi:
                continue
            t = self._target(o, k, addend)
            h.update(('%d:%d:%s;' % (off - lo, typ, t)).encode())
            name, value, size, styp, bind, shndx = o.syms[k]
            if refs is not None and name:
                m = ADDR.match(name)
                refs.add(int(m.group(1), 16) if m else (self.absolute[name] if name in self.absolute else name))
            if shndx == sec or (0 < shndx < len(o.secs) and o.secs[shndx][2] & SHF_EXEC):
                at = value + (addend if styp == 3 else 0)
                f = by_start.get((shndx, at))
                if f is not None:
                    reach.add(f)
            elif name in self.bridges:
                reach.add(('bridge', name))
        return h.hexdigest(), reach

    def keys(self, source, refs_out=None):
        """{original address: native hash} for the functions the unit's rewritten object defines; refs_out gets
        {original address: names / absolute addresses referred to by the function and what it reaches}."""
        p = os.path.join(self.fixed, config.obj_name(source))
        if not os.path.exists(p):
            return {}
        o = Obj(p)
        funcs = o.functions()
        by_start = {}
        for f in funcs:
            by_start.setdefault((f[0], f[1]), f)
        own, edges, refs = {}, {}, {}
        for f in funcs:
            refs[f] = set()
            own[f], edges[f] = self._own(o, f[0], f[1], f[2], by_start, refs[f])
        out = {}
        for name, value, size, typ, bind, shndx in o.syms:
            m = ADDR.match(name)
            if not m or not shndx or shndx >= len(o.secs):
                continue
            f = by_start.get((shndx, value))
            if f is None:
                continue
            seen, todo = {f}, [f]
            while todo:
                g = todo.pop()
                for e in edges.get(g, ()):
                    if e in seen:
                        continue
                    seen.add(e)
                    if e[0] != 'bridge':
                        todo.append(e)
            parts = sorted(own[g] if g[0] != 'bridge' else 'bridge:' + self.bridges[g[1]] for g in seen if g != f)
            h = hashlib.sha1((own[f] + '|' + ','.join(parts)).encode()).hexdigest()
            out[m.group(1)] = h
            if refs_out is not None:
                rs = set()
                for g in seen:
                    if g[0] == 'bridge':
                        rs.add(g[1])
                    else:
                        rs |= refs[g]
                refs_out[m.group(1)] = rs
        return out


def oracle_closure(catalog_rows, indirect_out=None, static_addrs=None, static_out=None):
    """{address: original addresses called (or tail-jumped to) by the function and the same-unit catalogued
    functions it reaches} - difffuzz runs the oracle's unit for real, so a callee's call is the caller's.
    indirect_out gets the addresses whose closure makes an indirect call."""
    by = {r['address']: r for r in catalog_rows}
    out = {}
    for a, r in by.items():
        seen, todo, calls = {a}, [a], set()
        while todo:
            x = by.get(todo.pop())
            if indirect_out is not None and x.get('indirect'):
                indirect_out.add(a)
            if static_out is not None and static_addrs and any(int(t, 16) in static_addrs
                                                               for t in x.get('data_refs') or ()):
                static_out.add(a)
            for c in list(x.get('calls') or []) + list(x.get('tails') or []):
                calls.add(int(c, 16))
                y = by.get(c)
                if y is not None and y.get('source') == r.get('source') and c not in seen:
                    seen.add(c)
                    todo.append(c)
        out[a] = calls
    return out


def change_tags(facts):
    return [c.tag for c in CHANGES if c.selects is not None and c.selects(facts)]


def facts_of(t, ocl, nrefs, oind=(), ostat=()):
    return Facts(t.get('symbol'), ocl.get(t['address'], ()), nrefs.get(t['address'], ()), t['address'] in oind,
                 t['address'] in ostat)


def local_static_bytes():
    """Byte addresses of every function-local static and guard units.json names (16 bytes each, as engine)."""
    import engine
    out = set()
    p = config.DECOMP / 'build' / 'units.json'
    if not p.exists():
        return out
    for u in json.load(open(p))['units']:
        for n, a in u['symbols'].items():
            if engine.LOCAL_STATIC.match(n):
                out.update(range(int(a, 16), int(a, 16) + 16))
    return out


def compute(targets, catalog):
    """-> ({address: key}, {address: [change tags]}, number of units)"""
    cat = [json.loads(line) for line in open(catalog)]
    ohash = {r['address']: r['ohash'] for r in cat if 'ohash' in r}
    oind, ostat = set(), set()
    ocl = oracle_closure(cat, oind, local_static_bytes(), ostat)
    nat = Native(str(config.LINK))
    rn, defs = {}, {}
    for row in config.units_renames():
        rn.setdefault(row[0], []).append('\t'.join(row))
        if row[1] == 'def':
            try:
                defs.setdefault(int(str(row[2]), 16), []).append('\t'.join(row))
            except ValueError:
                pass
    per_unit, nrefs, keys, tags = {}, {}, {}, {}
    for line in open(targets):
        t = json.loads(line)
        if t['source'] not in per_unit:
            try:
                per_unit[t['source']] = nat.keys(t['source'], nrefs)
            except TypeError:                   # a Native without native references (tests' stubs)
                per_unit[t['source']] = nat.keys(t['source'])
        n = per_unit[t['source']].get(t['address'])
        o = ohash.get(t['address'])
        if n and o:
            k = '%s|%s|%s' % (harness_tag(), o, n)
            if t['source'] in rn:
                k += '|renames:' + hashlib.sha1('\n'.join(sorted(rn[t['source']])).encode()).hexdigest()
            # a def row of another unit gives a callee this function reaches its convention (call-args decoding,
            # canned kind): the caller's verdict depends on it too (V1 R1)
            cd = sorted(r for a in ocl.get(t['address'], ()) for r in defs.get(a, ()) if not r.startswith(
                t['source'] + '\t'))
            if cd:
                k += '|callee-renames:' + hashlib.sha1('\n'.join(cd).encode()).hexdigest()
            tg = change_tags(facts_of(t, ocl, nrefs, oind, ostat))
            if tg:
                k += '|changes:' + ','.join(tg)
                tags[t['address']] = tg
            keys[t['address']] = hashlib.sha1(k.encode()).hexdigest()[:20]
    return keys, tags, len(per_unit)


def explain(targets, catalog, caches=()):
    """What each scoped change invalidates on this link: functions whose key it changes, and cached verdicts it
    sends back for a recheck (fuzzcache.recheck_due)."""
    import fuzzcache
    keys, tags, nunits = compute(targets, catalog)
    out = {'harness': HARNESS, 'scoped': SCOPED, 'functions_with_key': len(keys), 'changes': []}
    for c in CHANGES:
        e = dict(tag=c.tag, invalidates=c.invalidates,
                 keys_changed=sum(1 for v in tags.values() if c.tag in v))
        if c.recheck is not None:
            e['rechecked'] = sum(len(fuzzcache.recheck_due(d, [c])) for d in caches)
        out['changes'].append(e)
    out['keys_changed_any'] = len(tags)
    return out


def main():
    if sys.argv[1] == 'explain':
        print(json.dumps(explain(sys.argv[2], sys.argv[3], sys.argv[4:]), indent=1))
        return
    targets, catalog, out = sys.argv[1:4]
    keys, tags, nunits = compute(targets, catalog)
    json.dump(keys, open(out, 'w'))
    print('%d keys (%d units); scoped changes %s: %s' % (
        len(keys), nunits, SCOPED, ', '.join('%s %d' % (c.tag, sum(1 for v in tags.values() if c.tag in v))
                                             for c in CHANGES if c.selects is not None)))


if __name__ == '__main__':
    main()
