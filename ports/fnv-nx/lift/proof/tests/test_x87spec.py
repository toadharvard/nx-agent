#!/usr/bin/env python3
"""Test of proof/x87spec.py against the SDM rows of tests/x87_sdm_rows.txt (values read from the Intel SDM, each row cited; the rows file does not import the spec).
Checks: the status/control word layout, the tag encoding, the FINIT state (also what FNSAVE leaves), the FCOM/FTST/FCOMI/FUCOMI condition codes and EFLAGS, FXAM classes and C1, the m80 constants
(to_m80 of exactly representable values equals the SDM encoding, from_m80 inverts it), the BCD and integer indefinites, and that the registry covers the instruction list of the pack
(journal/inbox/l3b-w9-x87-list.md) up to the two encodings that have no SDM page.
python3 -I tests/test_x87spec.py"""
import math, os, re, struct, sys, unittest
from fractions import Fraction
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
            if 'CLEARED' in ln: continue                                   # a cleared feature (SSE3 fisttp): #UD on the declared CPU, no spec row
            m = re.match(r'\|\s*([0-9a-f]{2})\s*\|\s*(mem|reg|-)\s*\|\s*(?:(\d)|-)/', ln)
            if m: keys.add((m.group(1), m.group(2), int(m.group(3)) if m.group(3) else None))
        have = {(k[0], k[1], k[2]) for k in X.REGISTRY} | {(k[0], k[1], k[2]) for k in X.CLEARED_UD}
        have |= {(k[0], k[1], None) for k in have if k[1] == '-'}
        self.assertGreater(len(keys), 100)
        self.assertEqual(sorted(keys - have), [])
class Citations(unittest.TestCase):
    def test_every_citation_has_a_page(self):
        with open(os.path.join(os.path.dirname(HERE), 'sdm_pages.tsv'), encoding='utf-8') as fh: tab = [l.rstrip('\n').split('\t') for l in fh][1:]
        with open(os.path.join(os.path.dirname(HERE), 'x87spec.py'), encoding='utf-8') as fh: src = fh.read()
        want = set('SDM %s:%s' % k for k in re.findall(r'SDM (1|2A|2B|2C|2D|3A):(T\d+-\d+|\d+(?:\.\d+)*|[A-Za-z][A-Za-z0-9]*)', src))
        self.assertEqual(want, set(r[0] for r in tab))
        self.assertEqual([r for r in tab if not r[1].isdigit()], [])                              # the key is the PDF page index
        self.assertEqual([r for r in tab if r[2] and not r[2].startswith('Vol. ')], [])            # a printed page is read text or empty
def snap(o, nd, n=2):
    """(TOP, FSW, ST(0..n-1) patterns) of the spec result with the nondeterministic variables set: nd = [(var, value)]"""
    s = o.s
    for v, x in nd: s = X.St(**{f: z3.substitute(getattr(s, f), (v, x)) for f in X.St.FIELDS})
    top = val(X.top(s)); out = [val(z3.Select(s.R, K((top + i) & 7, 3))) for i in range(n)]
    return top, val(s.fsw), out
INDEF = X.QNAN_INDEF
class Nondeterminism(unittest.TestCase):
    def test_trig_special_operands(self):
        keys = {'fptan': ('d9', 'reg', 6, 2), 'fsincos': ('d9', 'reg', 7, 3), 'fsin': ('d9', 'reg', 7, 6), 'fcos': ('d9', 'reg', 7, 7)}
        push, c2v = z3.Bool('nd.trig.push'), z3.Bool('nd.trig.c2')
        for name, key in keys.items():
            for cls, v in (('inf', float('inf')), ('nan', NAN)):
                o = run(key, state([v]))
                for pv in (True, False):
                    for cv in (True, False):
                        top, fsw, r = snap(o, [(push, z3.BoolVal(pv)), (c2v, z3.BoolVal(cv))])
                        pushed = name in ('fptan', 'fsincos') and pv
                        self.assertEqual(top, 6 if pushed else 7, (name, cls, pv))
                        self.assertEqual(fsw & 1, 1 if cls == 'inf' else 0, (name, cls, 'IE'))          # #IA for infinity only (a QNaN does not raise it)
                        res = INDEF if cls == 'inf' else NAN
                        if pushed: self.assertEqual(r, [0x3FF0000000000000 if name == 'fptan' else res, res], (name, cls))
                        else: self.assertEqual(r[0], res, (name, cls))
                        self.assertEqual((fsw >> X.C2) & 1, int(cv), (name, cls, 'C2 is the admissible choice'))
    def test_trig_out_of_range(self):
        big = 2.0 ** 63
        for name, key in (('fptan', ('d9', 'reg', 6, 2)), ('fsincos', ('d9', 'reg', 7, 3)), ('fsin', ('d9', 'reg', 7, 6)), ('fcos', ('d9', 'reg', 7, 7))):
            for v in (big, -big):
                o = run(key, state([v])); top, fsw, r = snap(o, [])
                self.assertEqual((top, r[0], (fsw >> X.C2) & 1, fsw & 0x3f), (7, f2b(v), 1, 0), (name, v))
    def test_fprem_every_N(self):
        N = z3.BitVec('nd.prem.N', 16)
        from fractions import Fraction
        a, b = 2.0 ** 100, 3.0                                   # D = 100 - 1 = 99 >= 64
        for key, nearest in ((('d9', 'reg', 7, 0), False), (('d9', 'reg', 6, 5), True)):
            o = run(key, state([a, b]))
            for n in (32, 40, 63):
                top, fsw, r = snap(o, [(N, K(n, 16))])
                self.assertTrue(val(z3.substitute(o.nd_pre, (N, K(n, 16)))))
                D = 99; qq = int(Fraction(int(a)) / int(b) / Fraction(2) ** (D - n))                       # SDM 2A:FPREM Op ELSE: QQ := trunc((ST(0)/ST(1)) / 2^(D-N))
                R = Fraction(int(a)) - 3 * qq * Fraction(2) ** (D - n)
                self.assertEqual(r[0], f2b(float(R)), (key, n)); self.assertEqual((fsw >> X.C2) & 1, 1)
        for n in (31, 64): self.assertFalse(val(z3.substitute(o.nd_pre, (N, K(n, 16)))))
    def test_fcmov_empty_source(self):
        uf = z3.Bool('nd.fcmov.uf_false')
        for key, cond in ((('db', 'reg', 0, None), True), (('db', 'reg', 0, None), False)):     # FCMOVNB: moves when CF = 0
            cf = z3.BoolVal(not cond)
            s = state([1.0]); o = X.execute(key, s, None, 1, (z3.BoolVal(False), z3.BoolVal(False), cf))     # ef = (ZF, PF, CF)           # ST(1) is empty
            for uv in (True, False):
                top, fsw, r = snap(o, [(uf, z3.BoolVal(uv))], 1)
                reported = cond or uv
                self.assertEqual(fsw & (1 | (1 << X.SF)), (1 | (1 << X.SF)) if reported else 0, (cond, uv))
                if not reported: self.assertEqual((top, r[0]), (7, f2b(1.0)))
class Constants(unittest.TestCase):
    """the constants of FLDPI etc.: floor(v * 10^120); the RNE binary64 equals the Python float constant, and no rounding boundary lies within 10^-100"""
    FLOATS = {'pi': math.pi, 'ln2': math.log(2), 'log2e': math.log2(math.e), 'log2_10': math.log2(10), 'lg2': math.log10(2)}
    def test_constants(self):
        for name, ref in self.FLOATS.items():
            v = Fraction(X.CONST_INT[name], 10 ** 120)
            self.assertEqual(float(v), ref, name)                                                                 # float(Fraction) rounds to nearest even
            e = v.numerator.bit_length() - v.denominator.bit_length()                                             # floor(log2 v) within 1
            while Fraction(2) ** e > v: e -= 1
            while Fraction(2) ** (e + 1) <= v: e += 1
            half = Fraction(2) ** (e - 53)                                                                        # half the spacing of the 53-bit grid
            k = v / half; dist = abs(k - round(k)) * half
            self.assertGreater(dist, Fraction(1, 10 ** 100), name)
class DslView(unittest.TestCase):
    """the L3a DSL names and widths (fnvlift/dsl.py XW) and fault kinds (dsl.FAULTS 12..18, Wine 9.0 get_fpu_code order IE/SF, DE, ZE, OE, UE, PE)"""
    def test_widths(self):
        d = X.to_dsl(state([1.5, 2.0]))
        for k, w in X.DSL_WIDTH.items(): self.assertEqual(d[k].size(), w, k)
        self.assertEqual(sorted(d), sorted(X.DSL_WIDTH))
    def test_roundtrip(self):
        s = state([1.5, -2.0, 0.0]); d = X.to_dsl(s); s2 = X.from_dsl(d)
        for f in X.St.FIELDS:
            if f != 'R': self.assertEqual(val(getattr(s, f)), val(getattr(s2, f)), f)
        for i in range(8): self.assertEqual(val(z3.Select(s.R, K(i, 3))), val(z3.Select(s2.R, K(i, 3))), i)
        self.assertTrue(val(X.dsl_pre(d)))
        self.assertEqual(val(d['fsw'] & K(7 << 11, 16)), 0)                                      # fsw carries no TOP
        self.assertEqual(val(d['ftop']), 5)
    def test_pre_rejects_non_binary64(self):
        d = X.to_dsl(state([1.0])); d['fr%d' % 7] = K(0x3FFF8000000000000001, 80)                  # 1 + 2^-63: not a binary64 value, register 7 is ST(0)
        self.assertFalse(val(X.dsl_pre(d)))
        d['ftag'] = d['ftag'] | K(3 << 14, 16)                                                      # an empty register is unconstrained
        self.assertTrue(val(X.dsl_pre(d)))
    def test_fault_kinds(self):
        self.assertEqual((X.FLT_IE, X.FLT_DE, X.FLT_ZE, X.FLT_OE, X.FLT_UE, X.FLT_PE, X.FLT_SF), (12, 13, 14, 15, 16, 17, 18))
        fdiv_st0 = ('d8', 'reg', 6, None)                                                            # FDIV ST(0), ST(i)
        self.assertEqual(val(X.fault_kind(run(fdiv_st0, state([1.0, 0.0], fcw=0x037B), i=1))), X.FLT_ZE)    # 1/0, ZE unmasked (SDM 1:8.5.3)
        self.assertEqual(val(X.fault_kind(run(fdiv_st0, state([1.0, 0.0], fcw=0x037F), i=1))), 0)           # masked
        self.assertEqual(val(X.fault_kind(run(('d8', 'reg', 0, None), state([], fcw=0x037E), i=1))), X.FLT_SF)   # FADD on an empty stack: IE with SF
        self.assertEqual(val(X.fault_kind(run(fdiv_st0, state([0.0, 0.0], fcw=0x037A), i=1))), X.FLT_IE)       # 0/0: IE wins over ZE (Wine order), a pure IE
if __name__ == '__main__':
    unittest.main()
