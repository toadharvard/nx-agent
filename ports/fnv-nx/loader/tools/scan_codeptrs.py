#!/usr/bin/env python3
"""Find the code pointers stored in the data sections of FalloutNV.exe and write the fixup list.

usage: scan_codeptrs.py EXE [--addrmap addrmap.tsv] [--s2-addrmap out/addrmap.tsv] [--units build/units.json]
                        [--hints FILE | --no-hints] [--image-check build/image_check.json]
                        [--data-probes dataprobes.txt] [--load-base VA] [--no-relocs] [--headers]
                        [--out fixups.bin] [--report report.json] [--list fixups.tsv]
                        [--emit-addrmap addrmap.tsv] [--emit-addrmap-bin addrmap.bin] [--emit-hints hints.txt]
                        [--vtables vftables.tsv] [--vtable-slots slots.tsv]

Known code addresses: --addrmap (this stream's format), --s2-addrmap (S2's addrmap.tsv, kinds F/C/X/G), --units
(the decomp's build/units.json); several are merged, in that order of precedence.

Acceptance, as loader/host/loadcheck: the data sections must look plain, and either the code probes
(--image-check) or the data probes (--data-probes, loader/tools/mkdataprobes.py) must match. The Steam exe has an
encrypted .text and a .bind section: its code probes fail, its data probes pass, and that is all we need.

--vtable-slots lists vftable, slot index, original target for every vftable found (what S2 needs to fill native
vtables from image ones); in Python, Image.vtable_slots(va) reads one vftable.
--vtables lists every vftable found (original VA, slot count, how it was found, the MSVC class name from its RTTI
TypeDescriptor): the input S2 needs to bind image vftables to native vtables (docs/DATAIMAGE.md "Native vtables").

Same algorithm as the runtime scanner (loader/src/codeptr.c, docs/DATAIMAGE.md "Finding code pointers"); the two
must produce byte-identical lists (tests/s3). The known code addresses come from the S2 address map, or, until S2
delivers it, straight from the decomp's build/units.json (--units: defined functions, referenced undefined .text
addresses, and the decomp's vftable names as hints).

Everything this tool writes is derived from the player's executable and the decomp: keep it local (build/), never
commit it. The tool prints addresses and counts only, never bytes of the image.
"""
import argparse
import bisect
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fnvnx_pe import DIR_TLS, HEADERS, M32, PE, Image, PEError, u32  # noqa: E402
from fnvnx_formats import (CLASSES, CONF_HIGH, CONF_LOW, CONF_MED, CONF_NONE, KINDS,  # noqa: E402
                           AddrMap, fixups_bytes)

COL, HINT, TLS, RELOC, RUN, ISOLATED, VPTR = 1, 2, 3, 4, 5, 6, 7
REJ_STRING, REJ_ROUND, UNKNOWN, MID = 0x81, 0x82, 0x83, 0x84
MID_KIND, VTABLE_KIND = KINDS['MID'], KINDS['VTABLE']


def _pr(c):
    return 0x20 <= c < 0x7f


class Scanner:
    def __init__(self, img, amap, hints=(), no_relocs=False):
        self.img = img
        self.info = {k: v[0] for k, v in amap.entries.items()}
        self.hints = list(hints)
        self.relmode = bool(img.relocs) and not no_relocs
        relset = set(img.relocs)
        pe = img.pe
        self.text_lo = (img.load_base + pe.text_lo_rva) & M32
        self.text_hi = (img.load_base + pe.text_hi_rva) & M32
        self.excl = img.exclusions()
        self.dmaps = [m for m in img.maps if m.sec != HEADERS]
        self.dstarts = [m.va for m in self.dmaps]
        for m in self.dmaps:
            n = m.size // 4
            m.words = struct.unpack('<%dI' % n, bytes(m.data[:4 * n]))
            el = bytearray(b'\1') * n
            for lo, hi in self.excl:
                a = max(0, (lo - 4 - m.va) // 4 + 1)
                b = min(n, -((m.va - hi) // 4))
                if a < b:
                    el[a:b] = bytes(b - a)
            if self.relmode:
                for i in range(n):
                    if el[i] and (m.va + 4 * i) not in relset:
                        el[i] = 0
            m.elig = el

    # -- helpers mirroring codeptr.c --
    def is_text(self, v):
        return self.text_lo <= v < self.text_hi

    def mapping_of(self, w):
        i = bisect.bisect_right(self.dstarts, w) - 1
        if i >= 0 and w - self.dmaps[i].va < self.dmaps[i].size:
            return self.dmaps[i]
        return None

    @staticmethod
    def eligible_in(m, w):
        if w < m.va or w - m.va > m.size - 4:
            return False
        return bool(m.elig[(w - m.va) >> 2])

    @staticmethod
    def word(m, w):
        return m.words[(w - m.va) >> 2]

    def valid_col(self, c):
        img = self.img
        p = img.read(c, 20)
        if p is None or c & 3:
            return False
        sig, off, cdoff, td, chd = struct.unpack('<IIIII', p)
        if sig != 0 or off & 3 or off >= 0x100000 or cdoff >= 0x10000:
            return False
        q = img.read(td, 12)
        if q is None or td & 3 or img.ptr(u32(q, 0), 4) is None:
            return False
        s = img.string(td + 8, 512)
        if s is None or s[1] < 4 or s[0][:3] != b'.?A' or any(ch < 0x21 or ch > 0x7e for ch in s[0]):
            return False
        q = img.read(chd, 16)
        if q is None or chd & 3 or u32(q, 0) != 0 or u32(q, 4) >= 16:
            return False
        nb = u32(q, 8)
        if nb < 1 or nb > 4096 or img.ptr(u32(q, 12), 4 * nb) is None:
            return False
        return True

    def vt_find(self, w):
        k = bisect.bisect_right(self.vt_ends, w)
        return k if k < len(self.vt) and self.vt[k][0] <= w else -1

    def known_entry(self, v):
        if not self.is_text(v):
            return None
        info = self.info.get((v - self.img.delta) & M32)
        return None if info is None or info & 0xff == MID_KIND else info

    def known_at(self, m, x):
        if not self.eligible_in(m, x) or self.vt_find(x) >= 0 or x in self.tls:
            return False
        return self.known_entry(self.word(m, x)) is not None

    @staticmethod
    def string_tail(m, w):
        o = w - m.va
        b = m.data
        prev = b[o - 1] if o > 0 else -1
        prev2 = b[o - 2] if o >= 2 else -1
        if _pr(b[o]) and _pr(b[o + 1]) and _pr(b[o + 2]) and b[o + 3] == 0 and prev >= 0 and _pr(prev):
            return True
        if _pr(b[o]) and b[o + 1] == 0 and _pr(b[o + 2]) and b[o + 3] == 0 and prev == 0 and prev2 >= 0 and _pr(prev2):
            return True
        return False

    # -- passes --
    def find_vtables(self):
        col = []
        for m in self.dmaps:
            words, el, n = m.words, m.elig, len(m.words)
            for i in range(n - 1):
                if not el[i] or not el[i + 1] or not self.is_text(words[i + 1]):
                    continue
                c = words[i]
                if self.img.ptr(c, 20) is None or not self.valid_col(c):
                    continue
                col.append((m.va + 4 * i + 4, COL))
        vt = col
        if self.hints:
            nh, a, out = len(self.hints), 0, []
            delta = self.img.delta
            for h in range(nh + 1):
                have, v = h < nh, 0
                if have:
                    v = (self.hints[h] + delta) & M32
                    m = self.mapping_of(v)
                    if h and self.hints[h] <= self.hints[h - 1]:
                        have = False
                    elif m is None or v & 3 or not self.eligible_in(m, v) or not self.is_text(self.word(m, v)):
                        have = False
                while a < len(col) and (h == nh or col[a][0] <= v):
                    if have and col[a][0] == v:
                        have = False
                    out.append(col[a])
                    a += 1
                if have:
                    out.append((v, HINT))
            vt = out
        self.vt = []
        for k, (start, klass) in enumerate(vt):
            m = self.mapping_of(start)
            stop = vt[k + 1][0] if k + 1 < len(vt) else M32
            w = start
            while w < stop and self.eligible_in(m, w) and self.is_text(self.word(m, w)):
                w += 4
            self.vt.append((start, w, klass))
        self.vt_ends = [e for _, e, _ in self.vt]
        self.vt_starts = set(s for s, _, _ in self.vt)

    def find_tls(self):
        self.tls = {}
        img, pe = self.img, self.img.pe
        rva, size = pe.dir(DIR_TLS)
        if pe.ndirs <= DIR_TLS or size < 24:
            return
        t = img.read(img.load_base + rva, 24)
        if t is None or not u32(t, 12):
            return
        cb = u32(t, 12)
        for i in range(64):
            va = (cb + 4 * i) & M32
            v = img.read32(va)
            if self.mapping_of(va) is None or v is None or not v:
                break
            self.tls[va] = i

    def vtables(self):
        """[(orig start, slots, 'col'|'hint', class name or '')] after scan()"""
        out = []
        for start, end, klass in self.vt:
            name = ''
            if klass == COL:
                td = u32(self.img.read(u32(self.img.read(start - 4, 4), 0) + 12, 4), 0)
                name = self.img.string(td + 8, 512)[0].decode('latin-1')
            out.append(((start - self.img.delta) & M32, (end - start) // 4, 'col' if klass == COL else 'hint', name))
        return out

    def scan(self):
        self.find_vtables()
        self.find_tls()
        st = {'scan_words': 0, 'scan_textptrs': 0, 'vptr_refs': 0,
              'vtables_col': sum(1 for v in self.vt if v[2] == COL),
              'vtables_hint': sum(1 for v in self.vt if v[2] == HINT),
              'vtable_slots': sum((e - s) // 4 for s, e, _ in self.vt)}
        by_class = dict.fromkeys(CLASSES, 0)
        out = []
        delta = self.img.delta
        for m in self.dmaps:
            words, el = m.words, m.elig
            for i, v in enumerate(words):
                if not el[i]:
                    continue
                w = m.va + 4 * i
                st['scan_words'] += 1
                vi = self.vt_find(w)
                if not self.is_text(v):
                    if vi < 0 and v and v in self.vt_starts:
                        st['vptr_refs'] += 1
                    info = self.info.get((v - delta) & M32) if vi < 0 else None
                    if info is not None and info & 0xff == VTABLE_KIND:
                        by_class[VPTR] += 1
                        out.append(((w - delta) & M32, (v - delta) & M32, VPTR, CONF_MED, 0))
                    continue
                st['scan_textptrs'] += 1
                o = (v - delta) & M32
                aux = 0
                if vi >= 0:
                    klass, conf, aux = self.vt[vi][2], CONF_HIGH, (w - self.vt[vi][0]) // 4
                elif w in self.tls:
                    klass, conf, aux = TLS, CONF_HIGH, self.tls[w]
                else:
                    info = self.info.get(o)
                    if info is None:
                        klass, conf = UNKNOWN, CONF_NONE
                    elif info & 0xff == MID_KIND:
                        klass, conf = MID, CONF_NONE
                    elif self.relmode:
                        klass, conf = RELOC, CONF_HIGH
                    elif self.known_at(m, w - 4) or self.known_at(m, w + 4):
                        b = 0
                        while b < 0xffff and self.known_at(m, w - 4 * (b + 1)):
                            b += 1
                        klass, conf, aux = RUN, CONF_MED, b
                    elif self.string_tail(m, w):
                        klass, conf = REJ_STRING, CONF_NONE
                    elif not o & 0xfff:
                        klass, conf = REJ_ROUND, CONF_NONE
                    else:
                        klass, conf = ISOLATED, CONF_LOW
                by_class[klass] += 1
                out.append(((w - delta) & M32, o, klass, conf, aux))
        for k, name in CLASSES.items():
            st['fx_' + name] = by_class[k]
        st['fx_total'] = len(out)
        return out, st


def addrmap_from_units(path):
    """Known code addresses and vftable hints straight from the decomp's build/units.json (local use only)."""
    U = json.load(open(path))
    funcs = {}
    for u in U['units']:
        for f in u['functions']:
            a = int(f['address'], 16)
            info = KINDS['FUNC']
            names = [f['symbol']] + f.get('fold_aliases', [])
            if any(n.startswith('??__E') for n in names):
                info |= 0x100
            if any(n.startswith('??__F') for n in names):
                info |= 0x200
            funcs[a] = (info, f['size'])
    starts = sorted(funcs)
    entries = {a: (info, None) for a, (info, _) in funcs.items()}
    hints = set()
    for u in U['units']:
        for name, v in u.get('symbols', {}).items():
            a = int(v, 16)
            if 0x401000 <= a < 0xfdf000 and a not in funcs:
                i = bisect.bisect_right(starts, a) - 1
                inside = i >= 0 and starts[i] < a < starts[i] + funcs[starts[i]][1]
                entries.setdefault(a, (KINDS['MID'] if inside else KINDS['TEXT'], None))
            elif a >= 0xfdf000 and name.startswith('??_7'):
                hints.add(a)
    return AddrMap(entries), sorted(hints)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('exe')
    ap.add_argument('--addrmap')
    ap.add_argument('--s2-addrmap')
    ap.add_argument('--units')
    ap.add_argument('--data-probes')
    ap.add_argument('--vtable-slots')
    ap.add_argument('--hints')
    ap.add_argument('--no-hints', action='store_true')
    ap.add_argument('--image-check')
    ap.add_argument('--load-base', type=lambda s: int(s, 0))
    ap.add_argument('--no-relocs', action='store_true')
    ap.add_argument('--headers', action='store_true')
    ap.add_argument('--out')
    ap.add_argument('--report')
    ap.add_argument('--list')
    ap.add_argument('--emit-addrmap')
    ap.add_argument('--emit-addrmap-bin')
    ap.add_argument('--emit-hints')
    ap.add_argument('--vtables')
    a = ap.parse_args()
    if not (a.addrmap or a.s2_addrmap or a.units):
        ap.error('need --addrmap, --s2-addrmap or --units')

    try:
        pe = PE(open(a.exe, 'rb').read())
    except PEError as e:
        print('pe: %s' % e, file=sys.stderr)
        return 3
    code_ok = None
    if a.image_check:
        spec = json.load(open(a.image_check))
        code_ok, matched = pe.check_probes([(int(va, 16), n, d) for va, n, d in spec['probes']])
        print('probes=%d matched=%d wrapped=%d' % (len(spec['probes']), matched, pe.wrapped))
    amap, hints = AddrMap(), []
    if a.addrmap:
        amap.merge(AddrMap.read_tsv(a.addrmap))
    if a.s2_addrmap:
        amap.merge(AddrMap.read_s2_tsv(a.s2_addrmap))
    if a.units:
        umap, hints = addrmap_from_units(a.units)
        amap.merge(umap)
    if a.hints:
        hints = sorted({int(x, 0) for x in open(a.hints) if x.strip() and not x.startswith('#')})
    if a.no_hints:
        hints = []
    if a.emit_addrmap:
        amap.write_tsv(a.emit_addrmap)
    if a.emit_addrmap_bin:
        open(a.emit_addrmap_bin, 'wb').write(amap.to_bin())
    if a.emit_hints:
        with open(a.emit_hints, 'w') as f:
            f.writelines('0x%08x\n' % h for h in hints)

    img = Image(pe, a.load_base, headers=a.headers)
    plain = True
    for m in img.maps:
        if m.sec != HEADERS and pe.sections[m.sec].name != '.rsrc':
            n, zeros, chi2, ok = pe.section_stats(m.sec)
            print('section %-8s raw=0x%x zeros=%d chi2=%d plain=%d' % (pe.sections[m.sec].name, n, zeros, chi2, ok))
            plain &= ok
    data_ok = None
    if a.data_probes:
        probes = [(int(l.split()[0], 16), bytes.fromhex(l.split()[1])) for l in open(a.data_probes)
                  if len(l.split()) == 2]
        data_ok, matched = img.check_bytes(probes)
        print('data_probes=%d matched=%d' % (len(probes), matched))
    if not plain:
        print('the data sections are not plain (encrypted or packed); nothing is decrypted', file=sys.stderr)
        return 4
    if code_ok is False and not data_ok:
        print('image check failed: not FalloutNV.exe 1.4.0.525%s' % (
            ' (wrapped exe: pass --data-probes to verify its data instead)' if pe.wrapped else ''), file=sys.stderr)
        return 4
    img.load_relocs()
    img.relocate()
    scanner = Scanner(img, amap, hints, a.no_relocs)
    fixups, st = scanner.scan()
    if a.vtable_slots:
        with open(a.vtable_slots, 'w') as f:
            f.write('# vftable\tslot\ttarget\n')
            for va, nslots, _, _ in scanner.vtables():
                for k, t in enumerate(img.vtable_slots(va, nslots)):
                    f.write('0x%08x\t%d\t0x%08x\n' % (va, k, t))
    if a.vtables:
        with open(a.vtables, 'w') as f:
            f.write('# orig\tslots\tfound\tclass\n')
            for va, n, how, name in scanner.vtables():
                f.write('0x%08x\t%d\t%s\t%s\n' % (va, n, how, name))
    st['addrmap_entries'] = len(amap)
    st['sections_mapped'] = len(img.maps)
    st['relocs'] = len(img.relocs)
    if a.out:
        open(a.out, 'wb').write(fixups_bytes(pe, fixups))
    if a.list:
        with open(a.list, 'w') as f:
            f.write('# loc\ttarget\tclass\tconf\taux\n')
            for loc, tgt, k, c, aux in fixups:
                f.write('0x%08x\t0x%08x\t%s\t%d\t%d\n' % (loc, tgt, CLASSES[k], c, aux))
    if a.report:
        json.dump(st, open(a.report, 'w'), indent=1, sort_keys=True)
    applied = sum(1 for f in fixups if f[2] < 0x80)
    print(' '.join('%s=%d' % kv for kv in sorted(st.items())))
    print('to_apply=%d (high=%d med=%d low=%d) not_applied=%d' % (
        applied, sum(1 for f in fixups if f[3] == CONF_HIGH), sum(1 for f in fixups if f[3] == CONF_MED),
        sum(1 for f in fixups if f[3] == CONF_LOW), len(fixups) - applied))
    return 0


if __name__ == '__main__':
    sys.exit(main())
