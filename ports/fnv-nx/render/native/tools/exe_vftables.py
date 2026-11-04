#!/usr/bin/env python3
"""Read the image vftables of renderer classes from the user's own FalloutNV.exe (local agent work only).

    python3 -I render/native/tools/exe_vftables.py <FalloutNV.exe> --units ext/fnv_decomp/build/units.json \
        [--out render/native/vftables.tsv] [--anchor Class=0xADDR ...] Class ...

Writes `<vftable symbol> <slot> <function address> <decomp symbol at that address or ->` (REQUESTS.md R2). The output
is derived from the game executable: it is gitignored and must never be committed.

A class with MSVC RTTI is found from its type descriptor (.?AV<Class>@@) -> complete object locator (offset 0) ->
the dword before the vftable. A class without RTTI (Bethesda's BSShader classes) needs an anchor: the address of one
function known to be in its vftable (an override row's address); the vftable is the latest start at or before the
slot holding it that code stores as an immediate (`mov dword [reg], imm32`, the constructor's vptr store), and
it extends while the dwords are .text addresses (stopping at the next class's RTTI locator pointer)."""
import argparse, json, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', '..', 'loader', 'tools'))
import fnvnx_pe  # noqa: E402


class Exe:
    def __init__(self, path):
        self.pe = fnvnx_pe.PE(open(path, 'rb').read())
        self.base = self.pe.image_base
        self.secs = [(self.base + s.rva, s.vsize_eff, s.raw_off, min(s.raw_size, s.vsize_eff), s.is_code)
                     for s in self.pe.sections]
        self.lo = self.base + self.pe.text_lo_rva
        self.hi = self.base + self.pe.text_hi_rva

    def u32(self, va):
        for v, size, off, nfile, _ in self.secs:
            if v <= va < v + nfile - 3:
                return struct.unpack_from('<I', self.pe.data, off + va - v)[0]
        return None

    def is_code(self, a):
        return a is not None and self.lo <= a < self.hi

    def data_dwords(self, value):
        """VAs of aligned dwords == value in the non-code sections."""
        pat = struct.pack('<I', value)
        out = []
        for v, size, off, nfile, code in self.secs:
            if code:
                continue
            blob = self.pe.data[off:off + nfile]
            i = blob.find(pat)
            while i >= 0:
                if i % 4 == 0:
                    out.append(v + i)
                i = blob.find(pat, i + 1)
        return out

    def find_string(self, s):
        b = s.encode() + b'\0'
        for v, size, off, nfile, code in self.secs:
            i = self.pe.data[off:off + nfile].find(b)
            if i >= 0:
                return v + i
        return None

    def code_imm_stores(self):
        """Immediates of `mov dword [reg(+disp8)], imm32` (C7 /0 with modrm mod 00/01) in .text."""
        out = set()
        for v, size, off, nfile, code in self.secs:
            if not code:
                continue
            d = self.pe.data[off:off + nfile]
            i = d.find(b'\xc7')
            while 0 <= i < len(d) - 6:
                m = d[i + 1]
                mod, reg, rm = m >> 6, (m >> 3) & 7, m & 7
                if reg == 0 and rm not in (4, 5) and mod in (0, 1):
                    k = i + 2 + (1 if mod == 1 else 0)
                    if k + 4 <= len(d):
                        out.add(struct.unpack_from('<I', d, k)[0])
                i = d.find(b'\xc7', i + 1)
        return out

    def slots(self, vft, cap=1024):
        out = []
        while len(out) < cap:
            a = self.u32(vft + 4 * len(out))
            if not self.is_code(a):
                break
            out.append(a)
        return out

    def rtti_vftable(self, cls):
        s = self.find_string('.?AV%s@@' % cls)
        if s is None:
            return None, 'no RTTI type descriptor'
        td = s - 8
        for ref in self.data_dwords(td):
            col = ref - 12
            if self.u32(col) == 0 and self.u32(col + 4) == 0:   # signature 0, offset 0: the primary vftable
                vp = [p for p in self.data_dwords(col)]
                if len(vp) == 1:
                    return vp[0] + 4, 'rtti'
                if vp:
                    return None, 'locator referenced %d times' % len(vp)
        return None, 'no offset-0 locator'

    def anchored_vftable(self, anchor, stores):
        refs = self.data_dwords(anchor)
        best = []
        for r in refs:
            # walk back over code pointers to the run start; candidates are constructor-stored starts in the run
            start = r
            while self.is_code(self.u32(start - 4)):
                start -= 4
            cands = [a for a in range(start, r + 4, 4) if a in stores]
            if cands:
                best.append((max(cands), r))
        best = sorted(set(best))
        if len(best) == 1:
            return best[0][0], 'anchor %08x at slot %d' % (anchor, (best[0][1] - best[0][0]) // 4)
        return None, 'anchor %08x: %d candidate vftables %s' % (anchor, len(best), ['%08x' % b[0] for b in best])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('exe')
    ap.add_argument('classes', nargs='+')
    ap.add_argument('--units', required=True)
    ap.add_argument('--out', default=os.path.join(HERE, '..', 'vftables.tsv'))
    ap.add_argument('--anchor', action='append', default=[], help='Class=0xADDR of a function in its vftable')
    o = ap.parse_args()
    x = Exe(o.exe)
    defs = {}
    for u in json.load(open(o.units))['units']:
        for f in u['functions']:
            defs.setdefault(int(f['address'], 16), f['symbol'])
    anchors = {}
    for a in o.anchor:
        c, v = a.split('=')
        anchors[c] = int(v, 16)
    stores = x.code_imm_stores() if anchors else set()
    rows, bad = [], 0
    for c in o.classes:
        vft, why = x.rtti_vftable(c)
        if vft is None and c in anchors:
            vft, why = x.anchored_vftable(anchors[c], stores)
        if vft is None:
            print('%s: %s' % (c, why), file=sys.stderr)
            bad += 1
            continue
        sl = x.slots(vft)
        print('%s: vftable %08x, %d slots (%s)' % (c, vft, len(sl), why), file=sys.stderr)
        for i, a in enumerate(sl):
            rows.append('??_7%s@@6B@\t%d\t%08x\t%s\t%08x' % (c, i, a, defs.get(a, '-'), vft))
    with open(o.out, 'w') as f:
        f.write('# image vftables read from the user\'s FalloutNV.exe (render/native/tools/exe_vftables.py): game-derived, '
                'gitignored, never commit\n')
        f.write('# vftable symbol\tslot\tfunction\tdecomp symbol\tvftable address\n')
        f.write('\n'.join(rows) + '\n')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
