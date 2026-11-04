"""fnkeys.py - a key per function that changes when what difffuzz would compare for it changes (incremental farms).

    fnkeys.py <targets.jsonl> <catalog.jsonl> <out keys.json>     {address: key} for the targets' functions

key = sha1(HARNESS, oracle hash, native hash):
  oracle   the function's relocated MSVC code (catalog `ohash`; fixed per decomp commit)
  native   the rewritten AArch64 function (link `fixed/`): its bytes and relocations (type, offset, addend, target),
           plus the same for every function of its object it reaches through relocations (difffuzz runs the whole
           unit for real, so a changed callee or inlined helper changes the caller's verdict) and for the S2 bridges
           it calls (zz_bridges.o). A target is named by what it resolves to: an absolute address from aliases.ld,
           `__fnv_<addr>`, a symbol name, or for a local data reference the referenced bytes (a literal's content).
HARNESS is bumped when the harness changes verdicts (a deliberate full refresh); a function without a native
definition gets no key (not fuzzed).
"""
import hashlib
import json
import os
import re
import struct
import sys

import config

HARNESS = 'difffuzz-4'
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
        bp = os.path.join(self.fixed, 'zz_bridges.o')
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

    def _own(self, o, sec, lo, hi, by_start):
        h = hashlib.sha1(o.sec_bytes(sec, lo, hi))
        reach = set()
        for off, typ, k, addend in sorted(o.rela.get(sec, ())):
            if not lo <= off < hi:
                continue
            t = self._target(o, k, addend)
            h.update(('%d:%d:%s;' % (off - lo, typ, t)).encode())
            name, value, size, styp, bind, shndx = o.syms[k]
            if shndx == sec or (0 < shndx < len(o.secs) and o.secs[shndx][2] & SHF_EXEC):
                at = value + (addend if styp == 3 else 0)
                f = by_start.get((shndx, at))
                if f is not None:
                    reach.add(f)
            elif name in self.bridges:
                reach.add(('bridge', name))
        return h.hexdigest(), reach

    def keys(self, source):
        """{original address: native hash} for the functions the unit's rewritten object defines."""
        p = os.path.join(self.fixed, config.obj_name(source))
        if not os.path.exists(p):
            return {}
        o = Obj(p)
        funcs = o.functions()
        by_start = {}
        for f in funcs:
            by_start.setdefault((f[0], f[1]), f)
        own, edges = {}, {}
        for f in funcs:
            own[f], edges[f] = self._own(o, f[0], f[1], f[2], by_start)
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
        return out


def main():
    targets, catalog, out = sys.argv[1:4]
    ohash = {}
    for line in open(catalog):
        r = json.loads(line)
        if 'ohash' in r:
            ohash[r['address']] = r['ohash']
    nat = Native(str(config.LINK))
    per_unit, keys = {}, {}
    for line in open(targets):
        t = json.loads(line)
        if t['source'] not in per_unit:
            per_unit[t['source']] = nat.keys(t['source'])
        n = per_unit[t['source']].get(t['address'])
        o = ohash.get(t['address'])
        if n and o:
            keys[t['address']] = hashlib.sha1(('%s|%s|%s' % (HARNESS, o, n)).encode()).hexdigest()[:20]
    json.dump(keys, open(out, 'w'))
    print('%d keys (%d units)' % (len(keys), len(per_unit)))


if __name__ == '__main__':
    main()
