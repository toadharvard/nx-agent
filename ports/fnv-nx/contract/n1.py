#!/usr/bin/env python3
"""n1 - identity with the exe (MASTER_PLAN4 R1/N1) and what follows from it: the address-map certificate and
the vftable identity R6.

    n1.py --exe FalloutNV.exe --units units.json --msvc <dir with obj/u<i>.obj> --out n1.json

N1(f): the function as MSVC compiled it from the pristine decomp (COFF object of its unit) equals the exe's
bytes at address A outside its relocation words, and every relocation word of the exe at the same offset is
explained by one map  symbol -> address  that is a function over the whole accepted set.  The accepted set is
the greatest fixed point: a symbol that would need two addresses removes every function that references it,
until nothing changes.  units.json only proposes A; the certificate is checked as a whole and does not depend
on how A was found.

A relocation at offset r of f naming symbol S with in-place addend a pins S:
    DIR32:  S = exe_word(A + r) - a            REL32:  S = exe_word(A + r) + A + r + 4 - a
    SECREL (TLS): S = tls_template_start + exe_word(A + r) - a
A symbol defined inside a function's own section is pinned by that function's address as well (labels, jump
tables), and a function symbol is pinned by the address it is certified at.

Scope of a symbol: the unit.  An EXTERNAL symbol is (unit, name); a STATIC or LABEL symbol is its symbol-table
entry.  The port links by address per unit, so one unit's name for an address binds nothing in another unit;
names bound to different addresses in different units are counted and reported, they are not an error.
A DIR32 word the exe does not relocate pins the symbol to an absolute value.

Gate `n1` ($GATE_OUT/n1.json, gatefile.py): universe = all decomp functions, certified = those with N1 or a non-empty
reason (LIFTED); uncertified = neither (expected 0).

R6: in an N1 function a relocation naming `??_7C@@6B...@` carries the exe vftable address.  The map
(`??_7` symbol -> address) is a function by the fixed point; no learning, no order, no offsets.

Output (store.py kind `n1`, canonical JSON; exe- and decomp-derived: container and fnv-nx-build only):
  fn    {address: dict(unit, sym, size, n1, why, relocs=[(off, type, symbol, target)])}
  sym   {(unit, symbol) or (unit, symbol index, symbol): address}
  vt    {(unit, `??_7` symbol): address}
"""
import argparse, collections, json, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gatefile, store

DIR32, DIR32NB, REL32, SECTION, SECREL = 6, 7, 20, 10, 11
ABSOLUTE = 1 << 32


class Coff:
    def __init__(self, path):
        self.d = d = open(path, 'rb').read()
        _, ns, _, sp, nsym, _, _ = struct.unpack_from('<HHIIIHH', d, 0)
        self.strtab = sp + 18 * nsym
        self.secs = []
        for i in range(ns):
            o = 20 + 40 * i
            vs, va, rs, rp, rl, ll, nr, nl, ch = struct.unpack_from('<IIIIIIHHI', d, o + 8)
            self.secs.append((rp, rs, rl, nr, ch))
        self.syms = []          # index -> (name, value, section, storage class) or None for aux records
        i = 0
        while i < nsym:
            n, val, sec, typ, sc, na = struct.unpack_from('<8sIhHBB', d, sp + 18 * i)
            self.syms.append((self._name(n), val, sec, sc))
            self.syms.extend([None] * na)
            i += 1 + na
        self.by_name = {}
        for s in self.syms:
            if s and s[2] > 0 and s[3] == 2:
                self.by_name.setdefault(s[0], s)
        for s in self.syms:                      # static functions (dynamic initializers `??__E`, file statics)
            if s and s[2] > 0 and s[3] == 3 and s[0][:1] == '?':
                self.by_name.setdefault(s[0], s)

    def _name(self, b):
        if b[:4] == b'\0\0\0\0':
            o = self.strtab + struct.unpack_from('<I', b, 4)[0]
            return self.d[o:self.d.index(b'\0', o)].decode('latin1')
        return b.rstrip(b'\0').decode('latin1')

    def relocs(self, sec):
        rp, rs, rl, nr, ch = self.secs[sec - 1]
        n, o = nr, rl
        if ch & 0x01000000 and nr == 0xffff:
            n = struct.unpack_from('<I', self.d, rl)[0] - 1
            o += 10
        return [struct.unpack_from('<IIH', self.d, o + 10 * i) for i in range(n)]

    def data(self, sec):
        rp, rs, _, _, _ = self.secs[sec - 1]
        return self.d[rp:rp + rs]


def candidates(img, units, msvc, log):
    """Byte comparison: yields per function its relocation pins or the reason it is not byte-equal."""
    fn = {}
    t0, ts = img.text
    tls = img.u32(img.base + img.dd[9][0]) if img.dd[9][0] else 0     # TLS directory: start of the template
    for ui, un in enumerate(units['units']):
        path = os.path.join(msvc, 'obj', 'u%d.obj' % ui)
        if not os.path.exists(path):
            for f in un['functions']:
                fn[int(f['address'], 16)] = dict(unit=ui, sym=f['symbol'], size=f['size'], n1=False, why='no MSVC object', relocs=[])
            continue
        c = Coff(path)
        rcache, dcache = {}, {}
        placed = {}                   # section -> [(value, size, address)] of this unit's functions
        for f in un['functions']:
            s = c.by_name.get(f['symbol'])
            if s:
                placed.setdefault(s[2], []).append((s[1], f['size'], int(f['address'], 16)))
        for f in un['functions']:
            A, size, name = int(f['address'], 16), f['size'], f['symbol']
            F = fn[A] = dict(unit=ui, sym=name, size=size, n1=False, why='', relocs=[])
            s = c.by_name.get(name)
            if s is None:
                F['why'] = 'symbol not defined in the MSVC object'
                continue
            _, v, sec, _ = s
            if sec not in dcache:
                dcache[sec] = c.data(sec)
                rcache[sec] = sorted(c.relocs(sec))
            body = dcache[sec][v:v + size]
            o = img.off(A)
            if len(body) != size or o is None or not (t0 <= A and A + size <= t0 + ts):
                F['why'] = 'size or address out of range'
                continue
            exe = img.d[o:o + size]
            mask = bytearray(size)
            pins = []
            bad = ''
            for ra, si, typ in rcache[sec]:
                r = ra - v
                if not (0 <= r <= size - 4):
                    continue
                S = c.syms[si]
                a = struct.unpack_from('<I', body, r)[0]
                w = struct.unpack_from('<I', exe, r)[0]
                if typ == DIR32:
                    T = (w - a) & 0xffffffff
                    if (A + r) not in img.rset:
                        T |= ABSOLUTE            # the symbol is an absolute value (e.g. __tls_array), not an address
                elif typ == REL32:
                    T = (w + A + r + 4 - a) & 0xffffffff
                elif typ == SECREL and tls:      # offset inside the TLS template: not a base relocation
                    T = (tls + w - a) & 0xffffffff
                else:
                    bad = 'relocation type %d' % typ
                    T = 0
                mask[r:r + 4] = b'\xff\xff\xff\xff'
                key = (ui, S[0]) if S[3] == 2 else (ui, si, S[0])     # a static symbol is its symbol-table entry
                # a symbol defined in this object pins itself when it lies inside one of the unit's functions
                want = None
                if S[2] > 0:
                    for pv, ps, pa in placed.get(S[2], ()):
                        if pv <= S[1] < pv + ps:
                            want = pa + S[1] - pv
                pins.append((r, typ, key, T, want))
            if not bad:
                for k in range(size):
                    if not mask[k] and body[k] != exe[k]:
                        bad = 'bytes differ'
                        break
            if not bad:
                # an exe base relocation inside f that the object does not have
                k = img.rset
                for r in range(size - 3):
                    if (A + r) in k and not mask[r]:
                        bad = 'the exe has a relocation the object has not'
                        break
            if bad:
                F['why'] = bad
                continue
            F['pins'] = pins
            F['n1'] = True
        if ui % 500 == 0:
            log('unit %d' % ui)
    return fn


def fixed_point(fn):
    """Greatest set of byte-equal functions under one symbol -> address map."""
    rounds = 0
    while True:
        rounds += 1
        m = collections.defaultdict(set)
        for A, F in fn.items():
            if not F['n1']:
                continue
            m[(F['unit'], F['sym'])].add(A)          # the function's own symbol is at its address
            for r, typ, key, T, want in F['pins']:
                m[key].add(T)
                if want is not None:
                    m[key].add(want)
        bad = {k for k, v in m.items() if len(v) > 1}
        if not bad:
            break
        drop = 0
        for A, F in fn.items():
            if F['n1'] and ((F['unit'], F['sym']) in bad or any(p[2] in bad for p in F['pins'])):
                F['n1'] = False
                F['why'] = 'relocation target not a function of the symbol'
                drop += 1
        if not drop:
            break
    sym = {k: next(iter(v)) for k, v in m.items() if len(v) == 1}
    for F in fn.values():
        F['relocs'] = [(r, typ, key[-1], T) for r, typ, key, T, _ in F.pop('pins', [])] if F['n1'] else []
        F.pop('pins', None)
    return sym, rounds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', required=True)
    ap.add_argument('--units', required=True)
    ap.add_argument('--msvc', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    import exefacts
    img = exefacts.Image(a.exe)
    units = json.load(open(a.units))
    fn = candidates(img, units, a.msvc, lambda s: print(s, file=sys.stderr))
    before = collections.Counter(F['why'] or 'byte-equal outside relocations' for F in fn.values())
    sym, rounds = fixed_point(fn)
    vt = collections.defaultdict(set)            # R6, and across units: one decorated name, one vftable
    glob = collections.defaultdict(set)
    for k, v in sym.items():
        if len(k) == 2:
            glob[k[1]].add(v)
            if k[1].startswith('??_7'):
                vt[k[1]].add(v)
    print('external names bound to more than one address across units: %d (of %d); vftable names among them: %d' % (
        sum(1 for v in glob.values() if len(v) > 1), len(glob), sum(1 for v in vt.values() if len(v) > 1)))
    vt = {(k[0], k[1]): v for k, v in sym.items() if len(k) == 2 and k[1].startswith('??_7')}
    c = collections.Counter('N1' if F['n1'] else 'not N1: ' + F['why'] for F in fn.values())
    print('functions %d; fixed point in %d rounds' % (len(fn), rounds))
    for k, v in sorted(before.items(), key=lambda x: -x[1]):
        print('%7d  before the fixed point: %s' % (v, k))
    for k, v in sorted(c.items(), key=lambda x: -x[1]):
        print('%7d  %s' % (v, k))
    print('symbols pinned: %d; (unit, vftable symbol) pairs (R6): %d -> %d addresses' % (len(sym), len(vt), len(set(vt.values()))))
    store.save(a.out, 'n1', dict(fn=fn, sym=sym, vt=vt, exe_sha256=img.sha256))
    # gate `n1` (L4): every decomp function has a certificate (N1) or a LIFTED verdict with a reason (`why`)
    bad = ['%08x' % A for A, F in sorted(fn.items()) if not (F['n1'] or F['why'])]
    gatefile.write('n1', len(fn), len(fn) - len(bad), bad,
                   'N1 functions %d of %d; the others are LIFTED with a reason' % (sum(1 for F in fn.values() if F['n1']), len(fn)))


if __name__ == '__main__':
    main()
