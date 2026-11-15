#!/usr/bin/env python3
"""Test of proof/x87spec.py against the SDM rows of tests/x87_sdm_rows.txt (values read from the Intel SDM, each row cited; the rows file does not import the spec).
Checks: the status/control word layout, the tag encoding, the FINIT state (also what FNSAVE leaves), the FCOM/FTST/FCOMI/FUCOMI condition codes and EFLAGS, FXAM classes and C1, the m80 constants
(to_m80 of exactly representable values equals the SDM encoding, from_m80 inverts it), the BCD and integer indefinites, and that the registry covers the instruction list of the pack
(journal/inbox/l3b-w9-x87-list.md) up to the two encodings that have no SDM page.
python3 -I tests/test_x87spec.py"""
import os, re, struct, sys, unittest
import z3
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import x87spec as X
K = X.K

def rows(kind):
    out = []
    with open(os.path.join(HERE, 'x87_sdm_rows.txt'), encoding='utf-8') as fh: lines = fh.readlines()
    for ln in lines:
        if ln.startswith('#') or not ln.strip(): continue
        f = [x.strip() for x in ln.split('|')]
        if f[0] == kind: out.append([f[1]] + f[2].split() + [f[3]] if len(f[2].split()) == 3 else f[1:])
    return out

def val(e):
    r = z3.simplify(e)
    assert z3.is_bv_value(r) or z3.is_true(r) or z3.is_false(r), r
    return r.as_long() if z3.is_bv_value(r) else bool(z3.is_true(r))

def f2b(f): return struct.unpack('<Q', struct.pack('<d', f))[0]

def state(vals, fcw=0x037F):
    """TOP = 8 - len(vals), ST(0) first; tags from the SDM 1:8.1.7 classes of the binary64 bit patterns"""
    R = z3.K(z3.BitVecSort(3), K(0, 64)); top = (8 - len(vals)) & 7; tag = 0xFFFF
    for i, v in enumerate(vals):
        b = f2b(v) if isinstance(v, float) else v; p = (top + i) & 7
        R = z3.Store(R, K(p, 3), K(b, 64)); e = (b >> 52) & 0x7ff
        c = 1 if b & (2**63 - 1) == 0 else (2 if e in (0, 0x7ff) else 0)
        tag = (tag & ~(3 << 2 * p)) | (c << 2 * p)
    return X.St(R=R, tag=K(tag, 16), fsw=K(top << 11, 16), fcw=K(fcw, 16), fip=K(0, 32), fcs=K(0, 16), fdp=K(0, 32), fds=K(0, 16), fop=K(0, 11))

def run(key, s, i=None): return X.execute(key, s, None, i, None)
def ccs(fsw):
    v = val(fsw); return [(v >> X.C3) & 1, (v >> X.C2) & 1, (v >> X.C0) & 1]

NAN = 0x7FF8000000000000
class Layout(unittest.TestCase):
    def test_fsw_fcw_bits(self):
        pos = {'IE': X.IE, 'DE': X.DE, 'ZE': X.ZE, 'OE': X.OE, 'UE': X.UE, 'PE': X.PE, 'SF': X.SF, 'ES': X.ES, 'C0': X.C0, 'C1': X.C1, 'C2': X.C2, 'TOP': X.TOP_LO, 'C3': X.C3, 'B': X.BBIT}
        for name, bit, _ in rows('fsw'): self.assertEqual(pos[name], int(bit), name)
        self.assertEqual(X.TOP_HI, 13)
        pos = {'IM': X.IM, 'PM': X.PM, 'PC': X.PC_LO, 'RC': X.RC_LO}
        for name, bit, _ in rows('fcw'): self.assertEqual(pos[name], int(bit), name)
    def test_tags(self):
        enc = {'valid': X.TAG_VALID, 'zero': X.TAG_ZERO, 'special': X.TAG_SPECIAL, 'empty': X.TAG_EMPTY}
        for name, bits, _ in rows('tag'): self.assertEqual(enc[name], int(bits, 2), name)
    def test_tag_of_value(self):
        for v, name in ((1.0, 'valid'), (0.0, 'zero'), (float('inf'), 'special'), (NAN, 'special'), (1, 'special')):   # 1 = smallest binary64 subnormal (OWNER 8: the denormal class)
            b = f2b(v) if isinstance(v, float) else v
            self.assertEqual(val(X.tag_of_value(K(b, 64))), {'valid': 0, 'zero': 1, 'special': 2}[name])
    def test_finit_state(self):
        want = {r[0]: int(r[1], 16) for r in rows('init')}
        s = X.St.after_finit(z3.K(z3.BitVecSort(3), K(0, 64)))
        self.assertEqual((val(s.fcw), val(s.fsw), val(s.tag), val(s.fip)), (want['fcw'], want['fsw'], want['ftw'], want['fip']))
        o = run(('dd', 'mem', 6, None), state([1.0]))                     # FNSAVE initialises the FPU (SDM 1:8.1.5, 8.1.7)
        self.assertEqual((val(o.s.fcw), val(o.s.fsw) & ~(7 << 11) & 0xffff, val(o.s.tag)), (want['fcw'], want['fsw'], want['ftw']))

class Compare(unittest.TestCase):
    VALS = {'gt': (2.0, 1.0), 'lt': (1.0, 2.0), 'eq': (3.0, 3.0), 'unordered': (NAN, 1.0)}      # (ST(0), ST(1))
    def test_fcom_family(self):
        for rel, c3, c2, c0, _ in rows('cmp'):
            a, b = self.VALS[rel]
            for key in (('d8', 'reg', 2, None), ('dd', 'reg', 4, None)):        # FCOM / FUCOM
                o = run(key, state([a, b]), i=1)
                self.assertEqual(ccs(o.s.fsw), [int(c3), int(c2), int(c0)], (rel, key))
    def test_ftst(self):
        zero = {'gt': 1.0, 'lt': -1.0, 'eq': 0.0, 'unordered': NAN}                              # compared with +0.0: ST(0) > 0 is gt
        for rel, c3, c2, c0, _ in rows('cmp'):
            o = run(('d9', 'reg', 4, 4), state([zero[rel]]))
            self.assertEqual(ccs(o.s.fsw), [int(c3), int(c2), int(c0)], rel)
    def test_eflags(self):
        for rel, zf, pf, cf, _ in rows('eflags'):
            a, b = self.VALS[rel]
            for key in (('db', 'reg', 6, None), ('db', 'reg', 5, None)):        # FCOMI / FUCOMI
                o = run(key, state([a, b]), i=1)
                self.assertEqual([int(val(x)) for x in o.eflags[:3]], [int(zf), int(pf), int(cf)], (rel, key))
class Fxam(unittest.TestCase):
    ST = {'nan': NAN, 'normal': 1.5, 'infinity': float('inf'), 'zero': 0.0, 'denormal': 1}
    def test_classes(self):
        for cls, c3, c2, c0, _ in rows('fxam'):
            s = state([]) if cls == 'empty' else state([self.ST[cls]])
            self.assertEqual(ccs(run(('d9', 'reg', 4, 5), s).s.fsw), [int(c3), int(c2), int(c0)], cls)
    def test_c1_is_sign_even_when_empty(self):
        for v, sign in ((-1.5, 1), (1.5, 0), (-0.0, 1)):
            self.assertEqual((val(run(('d9', 'reg', 4, 5), state([v])).s.fsw) >> X.C1) & 1, sign)
        s = state([]); s = s.copy(R=z3.Store(s.R, K(0, 3), K(f2b(-1.0), 64)))                    # empty register that holds a negative pattern: C1 = sign (SDM 2A:FXAM)
        self.assertEqual((val(run(('d9', 'reg', 4, 5), s).s.fsw) >> X.C1) & 1, 1)
class Formats(unittest.TestCase):
    EXACT = {'+0': 0.0, '-0': -0.0, '+inf': float('inf'), '-inf': float('-inf'), '+1.0': 1.0, '-2.0': -2.0}
    def test_m80_constants(self):
        for name, hexv, _ in rows('m80'):
            if name in self.EXACT:
                b = K(f2b(self.EXACT[name]), 64)
                self.assertEqual(val(X.to_m80(b)), int(hexv, 16), name)
                self.assertEqual(val(X.from_m80(K(int(hexv, 16), 80))), f2b(self.EXACT[name]), name)
            if name == 'qnan_indefinite':
                self.assertEqual(val(X.to_m80(K(X.QNAN_INDEF, 64))), int(hexv, 16))
                self.assertEqual(val(X.from_m80(K(int(hexv, 16), 80))), X.QNAN_INDEF)
            if name == 'bcd_indefinite': self.assertEqual(X.BCD_INDEFINITE, int(hexv, 16))
    def test_int_indefinite(self):
        # FIST/FISTP masked, out of range: the integer indefinite (SDM 1:4.2.1.1); keys: DF /2 m16, DB /2 m32 (FIST), DF /7 m64 (FISTP)
        keys = {16: ('df', 'mem', 2, None), 32: ('db', 'mem', 2, None), 64: ('df', 'mem', 7, None)}
        for w, hexv, _ in rows('intindef'):
            o = run(keys[int(w)], state([1e30]))
            self.assertEqual(val(o.mem) & ((1 << int(w)) - 1), int(hexv, 16), w)
class Coverage(unittest.TestCase):
    def test_registry_vs_list(self):
        lst = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), 'journal', 'inbox', 'l3b-w9-x87-list.md')
        if not os.path.exists(lst): self.skipTest('list not present')
        keys = set()
        with open(lst, encoding='utf-8') as fh: lines = fh.readlines()
        for ln in lines:
            m = re.match(r'\|\s*([0-9a-f]{2})\s*\|\s*(mem|reg|-)\s*\|\s*(?:(\d)|-)/', ln)
            if m: keys.add((m.group(1), m.group(2), int(m.group(3)) if m.group(3) else None))
        have = {(k[0], k[1], k[2]) for k in X.REGISTRY} | {(k[0], k[1], k[2]) for k in X.NO_SDM_PAGE}
        have |= {(k[0], k[1], None) for k in have if k[1] == '-'}
        self.assertGreater(len(keys), 100)
        self.assertEqual(sorted(keys - have), [])
if __name__ == '__main__':
    unittest.main()
