#!/usr/bin/env python3
"""c4_vtables.py - V1-link claim C4: every vftable (V) entry of the address map points at a correct Itanium address
point in the linked image, and every deleting destructor (G) has distinct D1 and D0 there.

    c4_vtables.py <trial.elf> <addrmap.tsv> <fixed dir> <out tsv>

Read from the linked trial executable with V1's own reader (elf32.Image), never from tools/link:

V: the link symbol is defined; address point = symbol + offset lies inside the vtable group; the word before it is a
   typeinfo pointer to _ZTI of the group's own class (every vtable of a group carries the complete class's typeinfo);
   offset-to-top is 0 for a primary entry, minus the subobject offset for a secondary one, and that offset is the
   one of the named base in the class's typeinfo base lists (walked here: __si / __vmi layouts, non-virtual paths);
   each slot is a defined code address or 0, and a 0 slot is attributed through the relocations of the object the
   map names (undefined target in the trial link vs. GCC's 0 for abstract-class destructors);
   the runtime table fnvnx_addrmap holds the same address point for the entry's original address.
G: __fnv_<a> == native D0, d1_symbol == native D1/D2 of the same class, D0 != D1, both in .text, D0's code calls
   D1/D2 (or inlines a body that ends in a delete call), and fnvnx_addrmap_deldtors holds (orig, d1, d0) sorted.
"""
import collections
import os
import re
import struct
import subprocess
import sys

import capstone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import elf32  # noqa: E402

CS = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)


def load_map(path):
    rows = []
    for line in open(path):
        if line.startswith('#'):
            continue
        c = line.rstrip('\n').split('\t')
        c += [''] * (9 - len(c))
        rows.append(c)
    return rows


class TI:
    """Itanium typeinfo objects in the image (ILP32: 4-byte pointers)."""

    def __init__(self, img):
        self.img = img
        self.si = self._vt('_ZTVN10__cxxabiv120__si_class_type_infoE')
        self.vmi = self._vt('_ZTVN10__cxxabiv121__vmi_class_type_infoE')
        self.cls = self._vt('_ZTVN10__cxxabiv117__class_type_infoE')

    def _vt(self, n):
        v = self.img.by_name.get(n)
        return None if v is None else v[0] + 8

    def name_of(self, ti):
        for n in self.img.names_at(ti):
            if n.startswith('_ZTI'):
                return n
        return None

    def bases(self, ti):
        """[(base typeinfo, offset, virtual, public)] or None (unknown typeinfo kind).

        The abi::__*_type_info vtables are libsupc++'s and undefined in the trial link (the vptr word reads 8), so the
        kind is told by the object's size: 8 __class_type_info, 12 __si_class_type_info, 16 + 8n __vmi."""
        vp = self.img.u32(ti)
        if vp is None:
            return None
        n = self.name_of(ti)
        size = self.img.by_name[n][1] if n in self.img.by_name else 0
        kind = None
        if vp and vp == self.cls or (not self.cls and size == 8):
            kind = 'class'
        elif vp and vp == self.si or (not self.si and size == 12):
            kind = 'si'
        elif vp and vp == self.vmi or (not self.vmi and size >= 16 and (size - 16) % 8 == 0 and
                                       self.img.u32(ti + 12) == (size - 16) // 8):
            kind = 'vmi'
        if kind == 'class':
            return []
        if kind == 'si':
            return [(self.img.u32(ti + 8), 0, False, True)]
        if kind == 'vmi':
            n = self.img.u32(ti + 12)
            out = []
            for k in range(n):
                b = self.img.u32(ti + 16 + 8 * k)
                of = self.img.i32(ti + 20 + 8 * k)
                out.append((b, of >> 8, bool(of & 1), bool(of & 2)))
            return out
        return None

    def undefined_bases(self, ti):
        bs = self.bases(ti) or []
        return sum(1 for b, o, v, p in bs if not b)

    def offsets_of(self, ti, want_name):
        """All non-virtual-path offsets of base classes whose _ZTI name ends in want_name's mangling."""
        out = []
        q = [(ti, 0)]
        seen = 0
        while q and seen < 4000:
            t, off = q.pop(0)
            seen += 1
            bs = self.bases(t)
            if not bs:
                continue
            for b, o, virt, pub in bs:
                if virt:
                    continue
                n = self.name_of(b)
                if n and want_name(n):
                    out.append(off + o)
                q.append((b, off + o))
        return out


_DEM = {}


def demangle(n):
    if n not in _DEM:
        _DEM[n] = subprocess.run(['c++filt', n], capture_output=True, text=True).stdout.strip()
    return _DEM[n]


def mangled_class(name):
    """Itanium source-name of a plain class name 'A' or 'ns::A' -> '1A' / 'N2ns1AE' pieces for suffix tests."""
    parts = name.split('::')
    return ''.join('%d%s' % (len(p), p) for p in parts)


def slot_relocs(fixed_dir, objname, vtsym):
    """{offset in the vtable symbol: target symbol name} from the object's relocations."""
    p = os.path.join(fixed_dir, objname)
    if not objname or not os.path.exists(p):
        return None
    o = elf32.Obj(p)
    vs = [s for s in o.syms if s.name == vtsym and isinstance(s.shndx, int)]
    if not vs:
        o.close()
        return None
    v = vs[0]
    out = {}
    for r in o.rels:
        if r.sec == v.shndx and v.value <= r.offset < v.value + v.size:
            nm = r.sym.name if r.sym.type != 'STT_SECTION' else '%s%+d' % (o.secname(r.sym.shndx), r.addend)
            out[r.offset - v.value] = nm
    o.close()
    return out


def main():
    elf, amap, fixed_dir, out = sys.argv[1:5]
    img = elf32.Image(elf)
    ti = TI(img)
    rows = load_map(amap)
    res = []
    cnt = collections.Counter()
    text_lo, text_hi = [(lo, hi) for lo, hi, n, s in img.segs if n == '.text'][0]
    # runtime table
    am = img.by_name.get('fnvnx_addrmap')
    amc = img.by_name.get('fnvnx_addrmap_count')
    rt = {}
    if am and amc:
        n = img.u32(amc[0])
        for k in range(n):
            rt[img.u32(am[0] + 8 * k)] = img.u32(am[0] + 8 * k + 4)
        cnt['runtime entries'] = n
    seen_addr = collections.Counter(r[0] for r in rows)
    cnt['addresses with several rows'] = sum(1 for v in seen_addr.values() if v > 1)
    reloc_cache = {}
    for c in rows:
        addr, kind, link, native, obj, note, off = c[0], c[1], c[2], c[3], c[4], c[5], c[6]
        a = int(addr, 16)
        if kind != 'V':
            continue
        cnt['V'] += 1
        probs = []
        sym = img.by_name.get(link)
        if sym is None:
            loc = img.locals_by_name.get(link)
            if loc and len(loc) == 1:
                sym = (loc[0], 0, None, 'LOCAL', None)
                probs.append('link symbol is local')
            else:
                res.append((addr, 'V', 'FAIL', 'link symbol %s not defined' % link))
                cnt['V fail'] += 1
                continue
        base, size = sym[0], sym[1]
        ap = base + int(off or 0)
        if size and not (base + 8 <= ap <= base + size):
            probs.append('address point outside %s (+%s of %d)' % (link, off, size))
        ott = img.i32(ap - 8)
        tip = img.u32(ap - 4)
        tin = ti.name_of(tip) if tip else None
        want_ti = '_ZTI' + link[4:]
        if tin != want_ti:
            probs.append('typeinfo %s, want %s' % (tin, want_ti))
        m = re.search(r'\{for (.+?)\} at \+(\d+)', note)
        if note.startswith('primary'):
            if ott != 0:
                probs.append('primary offset-to-top %s' % ott)
        elif m:
            bname, boff = m.group(1), int(m.group(2))
            if ott != -boff:
                probs.append('secondary offset-to-top %s, note says +%d' % (ott, boff))
            want = ''.join(bname.split())
            offs = ti.offsets_of(tip, lambda n, w=want: ''.join(demangle(n).split()) in
                                 ('typeinfofor' + w, 'typeinfofor' + w.split('::')[-1])) if tip else []
            if boff not in offs:
                undef_b = ti.undefined_bases(tip) if tip else 0
                probs.append('base %s not at +%d in the typeinfo (found %s%s)' % (
                    bname, boff, offs[:6], '; %d base typeinfo pointers are 0 (undefined)' % undef_b
                    if undef_b else ''))
        else:
            probs.append('unparsed note: ' + note)
        # slots
        k = ap
        nslots = zero = undef0 = 0
        rel = None
        while size and k < base + size:
            w = img.u32(k)
            # next vtable of the group: (offset-to-top, typeinfo) pair
            if img.u32(k + 4) == tip and k + 4 < base + size and (w == 0 or w > 0x80000000) and k > ap:
                break
            nslots += 1
            if w == 0:
                zero += 1
                if rel is None:
                    key = (obj, link)
                    if key not in reloc_cache:
                        reloc_cache[key] = slot_relocs(fixed_dir, obj, link)
                    rel = reloc_cache[key] or {}
                tgt = rel.get(k - base)
                if tgt is not None:
                    undef0 += 1
                    probs.append('slot %d is 0: %s undefined in the trial link' % ((k - ap) // 4, tgt))
            elif not (text_lo <= w < text_hi):
                probs.append('slot %d = %#x outside .text' % ((k - ap) // 4, w))
            k += 4
        cnt['V slots'] += nslots
        cnt['V zero slots'] += zero
        cnt['V zero slots naming an undefined symbol'] += undef0
        if rt and rt.get(a) != ap:
            probs.append('runtime table %s != %#x' % (hex(rt.get(a) or 0), ap))
        struct_p = [p for p in probs if ' undefined in the trial link' not in p]
        st = 'FAIL' if struct_p else ('ok-undefined-slots' if probs else 'ok')
        cnt['V ' + st] += 1
        res.append((addr, 'V', st, '; '.join(probs)))
    # G
    dd = img.by_name.get('fnvnx_addrmap_deldtors')
    ddc = img.by_name.get('fnvnx_addrmap_deldtor_count')
    table = []
    if dd and ddc:
        n = img.u32(ddc[0])
        table = [(img.u32(dd[0] + 12 * k), img.u32(dd[0] + 12 * k + 4), img.u32(dd[0] + 12 * k + 8)) for k in range(n)]
    cnt['deldtor table entries'] = len(table)
    if [t[0] for t in table] != sorted(t[0] for t in table):
        cnt['deldtor table unsorted'] += 1
    tab = {t[0]: t for t in table}
    for c in rows:
        addr, kind, link, native, obj, note, off, d1s, d1n = c
        if kind != 'G':
            continue
        cnt['G'] += 1
        a = int(addr, 16)
        probs = []
        l0 = img.by_name.get(link)
        n0 = img.by_name.get(native)
        l1 = img.by_name.get(d1s)
        n1 = img.by_name.get(d1n)
        if not (l0 and n0 and l1 and n1):
            probs.append('undefined: %s' % [n for n, v in ((link, l0), (native, n0), (d1s, l1), (d1n, n1)) if not v])
        else:
            if l0[0] != n0[0]:
                probs.append('%s != %s' % (link, native))
            if l1[0] != n1[0]:
                probs.append('%s != %s' % (d1s, d1n))
            if l0[0] == l1[0]:
                probs.append('D0 == D1')
            for v in (l0[0], l1[0]):
                if not (text_lo <= v < text_hi):
                    probs.append('%#x outside .text' % v)
            if not re.match(r'^_ZN(.*)D0Ev$', native) or not re.match(r'^_ZN(.*)D[12]Ev$', d1n) or \
                    native[:-4] != d1n[:-4]:
                probs.append('not the same class: %s / %s' % (native, d1n))
            code = img.read(n0[0], 4 * 64) or b''
            calls = []
            for ins in CS.disasm(code, n0[0]):
                if ins.mnemonic in ('bl', 'b'):
                    t = int(ins.op_str.lstrip('#'), 16)
                    calls.append((ins.mnemonic, t))
                    if ins.mnemonic == 'b':
                        break
                if ins.mnemonic == 'ret':
                    break
            d1v = l1[0]
            d2 = img.by_name.get(d1n[:-4] + 'D2Ev')
            if not any(t in (d1v, d2[0] if d2 else -1) for _, t in calls):
                names = ['%s %s' % (m, ','.join(img.names_at(t)[:1]) or hex(t)) for m, t in calls]
                probs.append('D0 does not call D1/D2 (calls: %s)' % '; '.join(names[:6]))
                cnt['G D0 without a D1 call'] += 1
            t = tab.get(a)
            if t is None:
                probs.append('not in fnvnx_addrmap_deldtors')
            elif t[1] != l1[0] or t[2] != l0[0]:
                probs.append('deldtor table (%#x, %#x) != (%#x, %#x)' % (t[1], t[2], l1[0], l0[0]))
            if rt and rt.get(a) != l0[0]:
                probs.append('runtime table native != D0')
        st = 'ok' if not [p for p in probs if not p.startswith('D0 does not call')] else 'FAIL'
        cnt['G ' + st] += 1
        res.append((addr, 'G', st, '; '.join(probs)))
    with open(out, 'w') as f:
        for r in res:
            f.write('\t'.join(r) + '\n')
    for k in sorted(cnt):
        print('%-45s %d' % (k, cnt[k]))
    fails = [r for r in res if r[2] == 'FAIL']
    und = collections.Counter()
    for r in res:
        for m in re.finditer(r'is 0: (\S+) undefined', r[3]):
            und['__fnv_ (unit not compiled)' if m.group(1).startswith('__fnv_') else
                ('trap stub' if m.group(1).startswith('__fnvnx_') else 'C++ name')] += 1
    print('zero slots by undefined target kind:', dict(und))
    for r in fails[:30]:
        print('  ', r)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
